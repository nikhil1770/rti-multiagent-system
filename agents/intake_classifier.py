"""
Intake & Classification Agent
------------------------------
Reads a raw RTI application (PDF/DOCX) and:
1. Extracts individual questions
2. Classifies each question to one or more departments (see data/departments/)

Output: {
  "reference": <str|None>, "applicant": <str|None>,
  "questions": [ {"n": 1, "question_text": ..., "departments": [<dept_key>, ...]}, ... ]
}

Classification uses Gemini when GOOGLE_API_KEY is set; otherwise it scores the
question against every department's vector index and picks the best match(es).
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import complete  # noqa: E402
from ingest.sections import DEPARTMENTS  # noqa: E402
from agents.retrieval import retrieve  # noqa: E402

# A secondary department is included only if its best score is at least this
# fraction of the top department's score (enables multi-department routing).
# Kept strict because the vector fallback is noisy on thin data; the LLM path
# handles multi-department routing far better once a key is set.
MULTI_DEPT_RATIO = 0.97
MAX_DEPTS = 3


def extract_text(document_path: str) -> str:
    path = Path(document_path)
    if path.suffix.lower() == ".pdf":
        import fitz  # pymupdf
        doc = fitz.open(str(path))
        return "\n".join(page.get_text() for page in doc)
    if path.suffix.lower() in (".docx", ".doc"):
        import docx
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text()


# Closing boilerplate that follows the last numbered question; trimmed off so
# it doesn't get absorbed into that question's text.
_CLOSING = re.compile(
    r"\b(I am enclosing|I request that|Thanking you|Yours faithfully|"
    r"Yours sincerely|I hereby declare|Place\s*:|Date\s*:)",
    re.I,
)


def extract_questions(text: str) -> list[str]:
    """Pull out enumerated questions (1. ... 2. ...)."""
    matches = re.findall(
        r"\n\s*\d+[\.\)]\s+(.*?)(?=\n\s*\d+[\.\)]\s|\Z)",
        text,
        re.DOTALL,
    )
    questions = []
    for m in matches:
        cut = _CLOSING.search(m)
        if cut:
            m = m[: cut.start()]
        questions.append(re.sub(r"\s+", " ", m).strip())
    return [q for q in questions if len(q) > 8]


def _extract_filed_date(text: str) -> str | None:
    """Extract the application's filing date (ISO, UTC) if present, else None.
    Used to compute the statutory deadline from when the RTI was actually filed."""
    from datetime import datetime, timezone
    m = re.search(r"Date(?:\s*of\s*Application)?\s*[:\-]\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", text, re.I)
    if not m:
        return None
    day, month, year = (int(x) for x in m.groups())
    try:
        return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _extract_reference(text: str) -> tuple[str | None, str | None]:
    # Require an explicit "No./Number" keyword AND a digit, so prose like
    # "Application under Section 6(1)" is not mistaken for a reference number.
    ref = re.search(
        r"(?:Reference|Ref\.?|Application)\s*(?:No\.?|Number)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]*)",
        text,
    )
    applicant = re.search(r"\bI,\s+([A-Z][A-Za-z .]+?),", text)
    return (ref.group(1) if ref else None,
            applicant.group(1).strip() if applicant else None)


def _classify_by_vectors(question: str) -> list[str]:
    scored = []
    for dept in DEPARTMENTS:
        hits = retrieve(question, dept, k=1)
        scored.append((dept, hits[0]["score"] if hits else 0.0))
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored or scored[0][1] == 0.0:
        return []
    top_score = scored[0][1]
    picked = [dept for dept, score in scored if score >= MULTI_DEPT_RATIO * top_score]
    return picked[:MAX_DEPTS]


def _classify_by_llm(question: str) -> list[str] | None:
    dept_list = "\n".join(f"- {k}: {v['title']}" for k, v in DEPARTMENTS.items())
    prompt = (
        "Assign this RTI question to every department that plausibly holds a "
        "relevant record. Many questions map to one department, but assign "
        "MULTIPLE when the question spans jurisdictions — e.g. a question about "
        "a land-use change touches both the land-owning ministry AND the "
        "municipal building-approval body; a construction clearance touches both "
        "the approving authority AND the environmental regulator. Prefer being "
        "inclusive over missing a department, but do not add clearly unrelated "
        "ones.\n\n"
        f"DEPARTMENTS:\n{dept_list}\n\n"
        f"QUESTION:\n{question}\n\n"
        'Respond with ONLY a JSON array of department keys, most relevant first, '
        'e.g. ["ministry_defence", "municipal_corporation"].'
    )
    raw = complete(prompt)
    if raw is None:
        return None
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return None
    try:
        keys = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    valid = [k for k in keys if k in DEPARTMENTS]
    return valid or None


def classify_question(question: str) -> list[str]:
    return _classify_by_llm(question) or _classify_by_vectors(question)


def classify_rti(document_path: str) -> dict:
    text = extract_text(document_path)
    reference, applicant = _extract_reference(text)
    questions = extract_questions(text)
    return {
        "reference": reference,
        "applicant": applicant,
        "filed_date": _extract_filed_date(text),
        "questions": [
            {"n": i + 1, "question_text": q, "departments": classify_question(q)}
            for i, q in enumerate(questions)
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agents/intake_classifier.py <rti_pdf_path>")
        raise SystemExit(1)
    result = classify_rti(sys.argv[1])
    print(f"reference={result['reference']} applicant={result['applicant']}")
    for q in result["questions"]:
        print(f"\nQ{q['n']}: {q['question_text'][:110]}")
        print(f"   -> {q['departments']}")
