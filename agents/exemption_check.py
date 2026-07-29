"""
Exemption / Risk Check Agent
----------------------------
Checks ONE drafted answer against RTI Act Section 8 exemption categories
(privacy, national security, ongoing investigation, etc.). Runs per question.

Returns {"status": "safe"} or
        {"status": "flagged", "category": ..., "reason": ...}.

The AI never decides to withhold — it only flags for the human officer. Uses
Gemini when GOOGLE_API_KEY is set; otherwise falls back to a rule-based scan.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import complete  # noqa: E402

# RTI Act Section 8(1) exemption categories relevant to this demo.
EXEMPTION_CATEGORIES = {
    "privacy": "Section 8(1)(j) — personal information whose disclosure invades an individual's privacy (names, addresses, phone numbers, personal financial/asset details).",
    "national_security": "Section 8(1)(a) — information affecting national security, sovereignty, strategic or defence interests.",
    "ongoing_investigation": "Section 8(1)(h) — information that would impede an ongoing investigation, apprehension, or prosecution.",
    "cabinet_papers": "Section 8(1)(i) — cabinet papers including deliberations of council of ministers.",
    "commercial_confidence": "Section 8(1)(d) — commercial confidence, trade secrets, or intellectual property held by a third party.",
}

# Lightweight patterns for the offline fallback scan.
_RULES = [
    ("privacy", re.compile(r"\b(\d{10}|\+91[\-\s]?\d{10})\b")),                 # phone
    ("privacy", re.compile(r"\b[\w.]+@[\w.]+\.\w+\b")),                          # email
    ("privacy", re.compile(r"\b(address|residence|resides at|home of|asset|net worth|bank account)\b", re.I)),
    ("national_security", re.compile(r"\b(classified|top secret|national security|defence secret|strategic|intelligence)\b", re.I)),
    ("ongoing_investigation", re.compile(r"\b(ongoing investigation|under investigation|pending inquiry|prosecution|chargesheet)\b", re.I)),
    ("cabinet_papers", re.compile(r"\b(cabinet note|council of ministers|cabinet deliberation)\b", re.I)),
]


def _build_prompt(answer: str) -> str:
    categories = "\n".join(f"- {name}: {desc}" for name, desc in EXEMPTION_CATEGORIES.items())
    return (
        "You screen a drafted RTI answer for legal exemptions. Do NOT decide to "
        "withhold — only flag for a human officer. Consider these categories:\n"
        f"{categories}\n\n"
        f"DRAFTED ANSWER:\n{answer}\n\n"
        "Respond with ONLY a JSON object: "
        '{"status":"safe"} or '
        '{"status":"flagged","category":"<one category key>","reason":"<one short sentence>"}.'
    )


def _fallback_check(answer: str) -> dict:
    for category, pattern in _RULES:
        if pattern.search(answer):
            return {
                "status": "flagged",
                "category": category,
                "reason": f"Matched a {category.replace('_', ' ')} pattern; {EXEMPTION_CATEGORIES[category]}",
            }
    return {"status": "safe"}


def _parse_llm(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if data.get("status") == "flagged":
        cat = data.get("category", "")
        return {
            "status": "flagged",
            "category": cat,
            "reason": data.get("reason") or EXEMPTION_CATEGORIES.get(cat, "Flagged for officer review."),
        }
    return {"status": "safe"}


def check_exemptions(drafted_answer: str, source_record: dict | None = None) -> dict:
    raw = complete(_build_prompt(drafted_answer))
    if raw is not None:
        parsed = _parse_llm(raw)
        if parsed is not None:
            return parsed
    return _fallback_check(drafted_answer)
