"""In-process manager that drives the LangGraph orchestrator for the web layer.

Each RTI request is one graph thread (isolated state via the checkpointer).
Runs happen on background threads so HTTP handlers return immediately; the UI
polls for status. The officer's Approve / Instruct / Reject action resumes the
paused graph via Command(resume=...).
"""

import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command  # noqa: E402
from orchestrator.main import build_graph, days_remaining  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "test_data" / "sample_rtis"


class Manager:
    def __init__(self):
        self.app = build_graph()
        self.requests: dict[str, dict] = {}
        self.intakes: dict[str, dict] = {}
        self._locks: dict[str, threading.Lock] = {}

    # --- helpers -----------------------------------------------------------

    def _config(self, rid: str) -> dict:
        return {"configurable": {"thread_id": self.requests[rid]["thread_id"]}}

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%H:%M:%S")

    def _trace(self, rid: str, node: str, message: str) -> None:
        self.requests[rid]["trace"].append({"t": self._now(), "node": node, "message": message})

    def _state(self, rid: str) -> dict:
        try:
            return self.app.get_state(self._config(rid)).values or {}
        except Exception:
            return {}

    def _summarize_node(self, rid: str, node: str, update) -> None:
        # Read from this step's own update payload; get_state() lags mid-stream.
        st = update if isinstance(update, dict) and "questions" in update else self._state(rid)
        qs = st.get("questions", [])
        cursor = st.get("cursor", 0)
        cur = qs[cursor] if qs and cursor < len(qs) else None
        if node == "intake":
            self._trace(rid, node, f"Parsed {len(qs)} question(s), deadline set")
        elif node == "process" and cur:
            if cur.get("status") == "unanswerable":
                self._trace(rid, node, f"Q{cur['n']}: no matching department record — marked unanswerable")
            else:
                ex = (cur.get("exemption") or {}).get("status", "?")
                self._trace(rid, node, f"Q{cur['n']}: searched {cur.get('retrieved_dept')} → drafted → exemption: {ex}")
        elif node == "revise" and cur:
            self._trace(rid, node, f"Q{cur['n']}: re-drafted per officer instruction")
        elif node == "assemble":
            self._trace(rid, node, "Assembled final reply")
        # advance and the post-resume re-entry of review carry no new info
        elif node not in ("advance", "review"):
            self._trace(rid, node, node)

    # --- run loop ----------------------------------------------------------

    def _run(self, rid: str, payload) -> None:
        with self._locks[rid]:
            self.requests[rid]["status"] = "processing"
            config = self._config(rid)
            try:
                for chunk in self.app.stream(payload, config, stream_mode="updates"):
                    if "__interrupt__" in chunk:
                        value = chunk["__interrupt__"][0].value
                        self.requests[rid]["pending"] = value
                        self.requests[rid]["status"] = "awaiting_review"
                        self._trace(rid, "review",
                                    f"Q{value['question_n']} flagged for officer: "
                                    f"{value['exemption'].get('category')}")
                        return
                    for node, update in chunk.items():
                        self._summarize_node(rid, node, update)
            except Exception as exc:  # noqa: BLE001
                self.requests[rid]["status"] = "error"
                self.requests[rid]["error"] = str(exc)
                self._trace(rid, "error", str(exc)[:200])
                return

            self.requests[rid]["pending"] = None
            st = self._state(rid)
            self.requests[rid]["status"] = "completed" if st.get("final_reply") else "processing"
            if self.requests[rid]["status"] == "completed":
                self._trace(rid, "done", "Request completed")

    # --- public API --------------------------------------------------------

    def list_samples(self) -> list[str]:
        return sorted(p.name for p in SAMPLE_DIR.glob("*.pdf"))

    def start_path(self, doc_path: str, title: str | None = None) -> str:
        """Start a request from any document path (sample or uploaded file)."""
        doc = Path(doc_path)
        rid = uuid.uuid4().hex[:8]
        self.requests[rid] = {
            "id": rid,
            "thread_id": rid,
            "doc": str(doc),
            "title": title or doc.stem,
            "status": "processing",
            "trace": [],
            "pending": None,
            "created": self._now(),
        }
        self._locks[rid] = threading.Lock()
        threading.Thread(target=self._run, args=(rid, {"document_path": str(doc)}), daemon=True).start()
        return rid

    def start(self, sample_name: str) -> str:
        return self.start_path(str(SAMPLE_DIR / sample_name), Path(sample_name).stem)

    def decide(self, rid: str, decision: dict) -> None:
        """Resume a paused request with the officer's decision."""
        self.requests[rid]["pending"] = None
        action = decision.get("action", "approve")
        self._trace(rid, "officer", f"Officer chose: {action}"
                    + (f" — '{decision.get('instruction')}'" if action == "instruct" else ""))
        threading.Thread(target=self._run, args=(rid, Command(resume=decision)), daemon=True).start()

    def queue(self) -> list[dict]:
        out = []
        for rid, meta in self.requests.items():
            st = self._state(rid)
            qs = st.get("questions", [])
            done = sum(1 for q in qs if q.get("status") in ("approved", "auto_approved", "unanswerable"))
            out.append({
                "id": rid,
                "title": meta["title"],
                "status": meta["status"],
                "questions_total": len(qs),
                "questions_done": done,
                "days_remaining": days_remaining(st) if st.get("deadline") else None,
                "applicant": st.get("applicant"),
            })
        return out

    def detail(self, rid: str) -> dict:
        meta = self.requests.get(rid)
        if not meta:
            return {}
        st = self._state(rid)
        return {
            **{k: meta[k] for k in ("id", "title", "status", "trace", "pending", "created")},
            "applicant": st.get("applicant"),
            "reference": st.get("reference"),
            "deadline": st.get("deadline"),
            "days_remaining": days_remaining(st) if st.get("deadline") else None,
            "final_reply": st.get("final_reply"),
            "questions": st.get("questions", []),
        }

    def sla(self) -> dict:
        reqs = list(self.requests)
        total_q = flagged_q = at_risk = 0
        rounds = 0
        for rid in reqs:
            st = self._state(rid)
            for q in st.get("questions", []):
                total_q += 1
                hist = q.get("officer_history", [])
                if hist or q.get("status") == "flagged":
                    flagged_q += 1
                rounds += len(hist)
            dr = days_remaining(st) if st.get("deadline") else None
            if dr is not None and dr < 7:
                at_risk += 1
        return {
            "requests_total": len(reqs),
            "completed": sum(1 for r in reqs if self.requests[r]["status"] == "completed"),
            "awaiting_review": sum(1 for r in reqs if self.requests[r]["status"] == "awaiting_review"),
            "at_risk": at_risk,
            "questions_total": total_q,
            "pct_needing_review": round(100 * flagged_q / total_q) if total_q else 0,
            "officer_rounds": rounds,
        }

    # --- workbench (React UI) serializers -----------------------------------

    _CLAUSE = {
        "privacy": "8(1)(j)", "national_security": "8(1)(a)", "ongoing_investigation": "8(1)(h)",
        "cabinet_papers": "8(1)(i)", "commercial_confidence": "8(1)(d)",
    }

    def _req_depts(self, qs: list[dict]) -> set:
        depts = set()
        for q in qs:
            if q.get("retrieved_dept"):
                depts.add(q["retrieved_dept"])
            for d in (q.get("departments") or []):
                depts.add(d)
        return depts

    def _wb_question(self, q: dict) -> dict:
        ex = q.get("exemption")
        exemption = None
        if ex:
            flagged = ex.get("status") == "flagged"
            exemption = {
                "status": ex.get("status"),
                "clause": self._CLAUSE.get(ex.get("category")) if flagged else None,
                "reason": ex.get("reason"),
            }
        top = q.get("top_chunk") or {}
        cites = q.get("citations") or []
        return {
            "n": q.get("n"),
            "status": q.get("status"),
            "text": q.get("question_text"),
            "dept": q.get("retrieved_dept") or (q.get("departments") or [""])[0],
            "draft": q.get("draft"),
            "citation": top.get("citation") or (cites[0] if cites else None),
            "chunkText": top.get("text"),
            "score": top.get("score"),
            "exemption": exemption,
            "history": [
                {"action": h.get("action"), "instruction": h.get("instruction"),
                 "timestamp": "", "actor": "Officer"}
                for h in q.get("officer_history", [])
            ],
        }

    def wb_queue(self, dept: str | None = None) -> list[dict]:
        out = []
        for rid, meta in self.requests.items():
            st = self._state(rid)
            qs = st.get("questions", [])
            depts = self._req_depts(qs)
            if dept and qs and dept not in depts:
                continue
            done = sum(1 for q in qs if q.get("status") in ("approved", "auto_approved", "unanswerable"))
            out.append({
                "id": rid,
                "ref": st.get("reference") or meta["title"],
                "dept": next((d for d in depts if d), ""),
                "daysLeft": days_remaining(st) if st.get("deadline") else 30,
                "done": done, "total": len(qs),
                "status": meta["status"],
            })
        return out

    def wb_detail(self, rid: str, dept: str | None = None) -> dict:
        meta = self.requests.get(rid)
        if not meta:
            return {}
        st = self._state(rid)
        qs = st.get("questions", [])
        return {
            "id": rid,
            "ref": st.get("reference") or meta["title"],
            "applicant": st.get("applicant"),
            "deadline": st.get("deadline"),
            "daysLeft": days_remaining(st) if st.get("deadline") else None,
            "status": meta["status"],
            "pending": meta.get("pending"),
            "trace": meta.get("trace", []),
            "final_reply": st.get("final_reply"),
            "questions": [self._wb_question(q) for q in qs],
        }

    # --- clerk intake flow (extract -> verify -> forward to PIO) -------------

    def _fmt_date(self, iso: str | None) -> str:
        if not iso:
            return ""
        try:
            return datetime.fromisoformat(iso).strftime("%d %b %Y")
        except Exception:
            return ""

    def intake_upload(self, doc_path: str, filename: str) -> dict:
        """Run intake extraction only (no drafting) so a clerk can verify."""
        from agents.intake_classifier import classify_rti
        iid = uuid.uuid4().hex[:8]
        parsed = classify_rti(doc_path)
        applicant = parsed.get("applicant")
        ref = parsed.get("reference")
        fields = [
            {"key": "applicant", "label": "Applicant name",
             "value": applicant or "", "confidence": "confirmed" if applicant else "failed"},
            {"key": "docdate", "label": "Date on application",
             "value": self._fmt_date(parsed.get("filed_date")),
             "confidence": "confirmed" if parsed.get("filed_date") else "failed"},
            {"key": "docref", "label": "Reference on document",
             "value": ref or "", "confidence": "confirmed" if ref else "low"},
        ]
        questions = [
            {"n": q["n"], "text": q["question_text"],
             "dept": (q.get("departments") or [""])[0], "departments": q.get("departments") or []}
            for q in parsed["questions"]
        ]
        self.intakes[iid] = {
            "id": iid, "doc": doc_path, "filename": filename,
            "fields": fields, "questions": questions,
            "suggested_ref": ref or f"RTI/{iid.upper()}",
            "status": "ready", "created": self._now(), "forwarded_rid": None,
        }
        return self.intakes[iid]

    def intake_queue(self, dept: str | None = None) -> list[dict]:
        out = []
        for iid, it in self.intakes.items():
            depts = set()
            for q in it["questions"]:
                for d in (q.get("departments") or []):
                    depts.add(d)
            if dept and it["questions"] and dept not in depts:
                continue
            out.append({
                "id": iid, "filename": it["filename"], "status": it["status"],
                "questions": len(it["questions"]), "created": it["created"],
            })
        return out

    def intake_detail(self, iid: str) -> dict:
        return self.intakes.get(iid, {})

    def intake_forward(self, iid: str, meta: dict) -> str | None:
        it = self.intakes.get(iid)
        if not it:
            return None
        title = (it["fields"][0]["value"] or it["filename"])
        rid = self.start_path(it["doc"], title)
        it["status"] = "forwarded"
        it["forwarded_rid"] = rid
        it["receipt"] = meta
        return rid

    def intake_resolve(self, iid: str, action: str) -> bool:
        it = self.intakes.get(iid)
        if not it:
            return False
        it["status"] = action  # "transferred" | "rejected"
        return True
