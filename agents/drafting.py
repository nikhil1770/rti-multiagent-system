"""
Drafting Agent
--------------
Writes a grounded, plain-language answer from retrieved record chunk(s), with a
citation. Also handles officer-instructed revisions: if an officer instruction
is present, it rewrites using the SAME retrieved chunks + that instruction
instead of re-retrieving.

Uses Gemini when GOOGLE_API_KEY is set; otherwise falls back to a deterministic
extractive draft so the pipeline stays runnable offline.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import complete  # noqa: E402
from agents.retrieval import citation  # noqa: E402


def _format_sources(chunks: list[dict]) -> str:
    blocks = []
    for c in chunks:
        blocks.append(f"[{citation(c)}]\n{c['text']}")
    return "\n\n".join(blocks)


def _build_prompt(question: str, chunks: list[dict], officer_instruction: str | None) -> str:
    sources = _format_sources(chunks)
    instruction_block = ""
    if officer_instruction:
        instruction_block = (
            "\nAn officer reviewed a previous draft and gave this fix instruction. "
            "Apply it exactly, using the SAME source records (do not invent new facts):\n"
            f'  "{officer_instruction}"\n'
        )
    return (
        "You are drafting a reply to a Right to Information (RTI) request. "
        "Use ONLY the source records below and never invent facts — but use them "
        "FULLY. If a record contains a decision, order, letter, or fact that bears "
        "on the question, report it and explain its relevance, even when it is not "
        "an exact-word match to the question. Say information is unavailable only "
        "when nothing in the records addresses that specific point. Be clear and "
        "factual, and end with a citation to the record(s) you used.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"SOURCE RECORDS:\n{sources}\n"
        f"{instruction_block}\n"
        "DRAFT ANSWER:"
    )


def _fallback_draft(question: str, chunks: list[dict], officer_instruction: str | None) -> str:
    """Deterministic draft used when no LLM is configured."""
    if not chunks:
        return "The requested information could not be located in this department's records."
    top = chunks[0]
    note = f" (Revision applied per officer instruction: {officer_instruction})" if officer_instruction else ""
    return (
        f"Based on the department's records, the following information is available: "
        f"{top['text']}{note}\n\nSource: {citation(top)}"
    )


def draft_answer(question: str, retrieved: list[dict] | dict, officer_instruction: str | None = None) -> dict:
    """Return {answer, citations, used_llm}.

    `retrieved` is the list of chunks from the retrieval agent (a single dict is
    also accepted for convenience).
    """
    chunks = [retrieved] if isinstance(retrieved, dict) else list(retrieved)

    answer = complete(
        _build_prompt(question, chunks, officer_instruction),
        model=os.getenv("DRAFTING_MODEL", "gemini-1.5-pro"),
    )
    used_llm = answer is not None
    if not used_llm:
        answer = _fallback_draft(question, chunks, officer_instruction)

    return {
        "answer": answer.strip(),
        "citations": [citation(c) for c in chunks],
        "used_llm": used_llm,
    }
