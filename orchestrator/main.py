"""
LangGraph Orchestrator (Supervisor)
------------------------------------
Builds the state graph that drives one RTI request through the agents, tracks
the 30-day statutory deadline, and pauses on flagged questions for the human
officer via interrupt().

Per-question flow (routing is decided by state, not a fixed script):
  intake -> [for each question] retrieve -> draft -> exemption check
     -> auto-approved            : move on
     -> flagged                  : interrupt() for the officer, who can
            approve   -> move on
            instruct  -> re-draft with the SAME records + instruction (no re-search)
            reject    -> re-retrieve from the next department
  -> assemble the final reply once every question is resolved.

Checkpointer: MemorySaver by default (swap for the Postgres saver to survive
restarts — see build_graph()).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.graph import StateGraph, START, END  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import interrupt, Command  # noqa: E402

from agents.intake_classifier import classify_rti  # noqa: E402
from agents.retrieval import retrieve, citation  # noqa: E402
from agents.drafting import draft_answer  # noqa: E402
from agents.exemption_check import check_exemptions  # noqa: E402

STATUTORY_DAYS = 30


class RTIState(TypedDict, total=False):
    document_path: str
    reference: str | None
    applicant: str | None
    filed_date: str
    deadline: str
    questions: list[dict]
    cursor: int
    final_reply: str | None


# --- nodes -----------------------------------------------------------------

def intake_node(state: RTIState) -> RTIState:
    parsed = classify_rti(state["document_path"])
    # Use the document's own filing date when it is recent (within the statutory
    # window), so the deadline reflects when the RTI was actually filed. A far
    # past date (e.g. an archival sample) is treated as freshly filed instead.
    filed = datetime.now(timezone.utc)
    if parsed.get("filed_date"):
        try:
            d = datetime.fromisoformat(parsed["filed_date"])
            if 0 <= (filed - d).days <= STATUTORY_DAYS:
                filed = d
        except ValueError:
            pass
    questions = []
    for q in parsed["questions"]:
        questions.append({
            **q,
            "dept_idx": 0,
            "status": "pending",
            "draft": None,
            "citations": [],
            "exemption": None,
            "officer_history": [],
        })
    return {
        "reference": parsed["reference"],
        "applicant": parsed["applicant"],
        "filed_date": filed.isoformat(),
        "deadline": (filed + timedelta(days=STATUTORY_DAYS)).isoformat(),
        "questions": questions,
        "cursor": 0,
        "final_reply": None,
    }


def _current(state: RTIState) -> dict:
    return state["questions"][state["cursor"]]


def process_node(state: RTIState) -> RTIState:
    """Retrieve -> draft -> exemption check for the current question."""
    q = _current(state)
    depts = q["departments"]
    if not depts or q["dept_idx"] >= len(depts):
        q["status"] = "unanswerable"
        q["draft"] = "The requested information could not be located in the relevant department's records."
        return state

    dept = depts[q["dept_idx"]]
    hits = retrieve(q["question_text"], dept)
    drafted = draft_answer(q["question_text"], hits)
    ex = check_exemptions(drafted["answer"])

    q["retrieved_dept"] = dept
    q["draft"] = drafted["answer"]
    q["citations"] = drafted["citations"]
    q["exemption"] = ex
    # keep the top chunk (verbatim text + score) for the officer's "view source"
    if hits:
        q["top_chunk"] = {"text": hits[0]["text"], "score": hits[0]["score"], "citation": citation(hits[0])}
    q["status"] = "flagged" if ex["status"] == "flagged" else "auto_approved"
    return state


def review_node(state: RTIState) -> RTIState:
    """Pause for the officer. Resume value is the officer's decision dict."""
    q = _current(state)
    decision = interrupt({
        "reference": state.get("reference"),
        "question_n": q["n"],
        "question": q["question_text"],
        "draft": q["draft"],
        "citations": q["citations"],
        "exemption": q["exemption"],
        "actions": ["approve", "instruct", "reject"],
    })
    q["officer_history"].append(decision)
    action = decision.get("action", "approve")

    if action in ("approve", "partial"):
        q["status"] = "approved"  # partial = disclose under S.10 severability
    elif action == "instruct":
        q["status"] = "revising"
        q["_instruction"] = decision.get("instruction", "")
    elif action == "reject":
        q["status"] = "rejected"
        q["dept_idx"] += 1  # try the next department on re-retrieval
    return state


def revise_node(state: RTIState) -> RTIState:
    """Re-draft using the SAME retrieved records + the officer instruction."""
    q = _current(state)
    dept = q.get("retrieved_dept")
    hits = retrieve(q["question_text"], dept) if dept else []
    drafted = draft_answer(q["question_text"], hits, officer_instruction=q.get("_instruction"))
    ex = check_exemptions(drafted["answer"])
    q["draft"] = drafted["answer"]
    q["citations"] = drafted["citations"]
    q["exemption"] = ex
    # Officer already engaged; if their fix cleared the flag it is approved,
    # otherwise it goes back to them rather than looping automatically.
    q["status"] = "approved" if ex["status"] == "safe" else "flagged"
    return state


def advance_node(state: RTIState) -> RTIState:
    state["cursor"] += 1
    return state


def assemble_node(state: RTIState) -> RTIState:
    lines = [
        f"Reply to RTI request {state.get('reference') or '(no reference)'}",
        f"Applicant: {state.get('applicant') or '(unknown)'}",
        f"Statutory deadline: {state.get('deadline')}",
        "",
    ]
    for q in state["questions"]:
        lines.append(f"Q{q['n']}. {q['question_text']}")
        lines.append(f"Answer: {q['draft']}")
        if q["citations"]:
            lines.append(f"Source(s): {'; '.join(q['citations'])}")
        lines.append("")
    return {"final_reply": "\n".join(lines)}


# --- routing ---------------------------------------------------------------

def route_from_cursor(state: RTIState) -> str:
    return "process" if state["cursor"] < len(state["questions"]) else "assemble"


def route_after_process(state: RTIState) -> str:
    return "review" if _current(state)["status"] == "flagged" else "advance"


def route_after_review(state: RTIState) -> str:
    status = _current(state)["status"]
    if status == "revising":
        return "revise"
    if status == "rejected":
        return "process"  # re-retrieve from the next department
    return "advance"      # approved


# --- graph -----------------------------------------------------------------

_pg_cm = None  # keep the Postgres context manager alive for the process lifetime


def make_checkpointer():
    """Use the Postgres checkpointer when POSTGRES_URL is set and reachable, so
    in-flight (interrupted) requests survive a restart. Fall back to in-memory
    otherwise, so the system always runs even without a database."""
    global _pg_cm
    url = os.getenv("POSTGRES_URL")
    if not url:
        return MemorySaver()
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        _pg_cm = PostgresSaver.from_conn_string(url)
        saver = _pg_cm.__enter__()
        saver.setup()  # create checkpoint tables if missing
        print("[checkpointer] using Postgres — state is durable across restarts")
        return saver
    except Exception as exc:  # noqa: BLE001
        print(f"[checkpointer] Postgres unavailable ({str(exc)[:80]}); using in-memory")
        return MemorySaver()


def build_graph(checkpointer=None):
    g = StateGraph(RTIState)
    g.add_node("intake", intake_node)
    g.add_node("process", process_node)
    g.add_node("review", review_node)
    g.add_node("revise", revise_node)
    g.add_node("advance", advance_node)
    g.add_node("assemble", assemble_node)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_from_cursor, ["process", "assemble"])
    g.add_conditional_edges("process", route_after_process, ["review", "advance"])
    g.add_conditional_edges("review", route_after_review, ["revise", "process", "advance"])
    g.add_conditional_edges("revise", route_after_process, ["review", "advance"])
    g.add_conditional_edges("advance", route_from_cursor, ["process", "assemble"])
    g.add_edge("assemble", END)

    return g.compile(checkpointer=checkpointer or make_checkpointer())


def days_remaining(state: RTIState) -> int:
    deadline = datetime.fromisoformat(state["deadline"])
    return (deadline - datetime.now(timezone.utc)).days


if __name__ == "__main__":
    doc = sys.argv[1] if len(sys.argv) > 1 else "test_data/sample_rtis/rti_02_judges_asset_declaration.pdf"
    app = build_graph()
    config = {"configurable": {"thread_id": "demo-1"}}

    result = app.invoke({"document_path": doc}, config)

    # Drive the human-review gate. In the real app this is the officer in the
    # dashboard; here we auto-approve each flagged question to run end-to-end.
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print(f"\n[OFFICER REVIEW] Q{payload['question_n']} flagged: "
              f"{payload['exemption'].get('category')} — {payload['exemption'].get('reason', '')[:80]}")
        print(f"  draft: {payload['draft'][:120]}...")
        print("  -> auto-approving for demo")
        result = app.invoke(Command(resume={"action": "approve"}), config)

    print("\n" + "=" * 70)
    print(result["final_reply"])
    print("=" * 70)
    print(f"Days remaining until statutory deadline: {days_remaining(result)}")
