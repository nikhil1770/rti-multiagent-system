"""Thin LLM helper around Google Gemini (google-genai SDK).

- Reads any of GOOGLE_API_KEY / GOOGLE_API_KEY1 / GOOGLE_API_KEY2 and rotates
  between them: if one key errors (e.g. free-tier quota exhausted), it retries
  with the next. This stretches the free quota across multiple accounts.
- complete() returns None when no key/library is available or all keys fail, so
  callers fall back to their deterministic path and the system stays runnable.
"""

import os

from dotenv import load_dotenv

load_dotenv()

_KEYS = None
_CLIENTS: dict[str, object] = {}


def _keys() -> list[str]:
    global _KEYS
    if _KEYS is None:
        found = []
        for name in ("GOOGLE_API_KEY", "GOOGLE_API_KEY1", "GOOGLE_API_KEY2"):
            val = os.getenv(name)
            if val and val not in found:
                found.append(val)
        _KEYS = found
    return _KEYS


def llm_available() -> bool:
    return bool(_keys())


def _client(key: str):
    if key not in _CLIENTS:
        from google import genai
        _CLIENTS[key] = genai.Client(api_key=key)
    return _CLIENTS[key]


def complete(prompt: str, model: str | None = None, temperature: float = 0.2) -> str | None:
    """Single-shot completion with key failover. Returns None to signal fallback."""
    keys = _keys()
    if not keys:
        return None
    try:
        from google.genai import types
    except ImportError:
        return None

    model = (model or os.getenv("LIGHT_MODEL", "gemini-2.5-flash")).replace("models/", "")
    for key in keys:
        try:
            resp = _client(key).models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature),
            )
            if resp.text:
                return resp.text
        except Exception:
            continue  # quota / rate / transient error -> try the next key
    return None
