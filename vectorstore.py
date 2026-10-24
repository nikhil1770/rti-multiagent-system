"""Shared Chroma vector-store access for ingestion and retrieval.

One collection per department (keyed by the department folder name).

Embeddings are computed here and passed to Chroma explicitly (so we control the
model and the query/document task type):
  - Google `gemini-embedding-001` when a GOOGLE_API_KEY is set — much stronger
    ranking on dense records, with key failover.
  - Chroma's local all-MiniLM-L6-v2 as an offline fallback (no key, runs on CPU).

The two models have different dimensions, so switching between them requires a
full rebuild: python ingest/embed.py
"""

import os
import re
import time
from pathlib import Path

import chromadb
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
CHROMA_PATH = os.getenv("CHROMA_PATH", str(ROOT / "data" / "chroma"))
GEMINI_EMBED_MODEL = "gemini-embedding-001"

_client = None
_gemini_clients = None
_local_ef = None


def _gemini_keys() -> list[str]:
    found = []
    for name in ("GOOGLE_API_KEY", "GOOGLE_API_KEY1", "GOOGLE_API_KEY2"):
        val = os.getenv(name)
        if val and val not in found:
            found.append(val)
    return found


def _clients():
    global _gemini_clients
    if _gemini_clients is None:
        from google import genai
        _gemini_clients = [genai.Client(api_key=k) for k in _gemini_keys()]
    return _gemini_clients


def _local():
    global _local_ef
    if _local_ef is None:
        from chromadb.utils import embedding_functions
        _local_ef = embedding_functions.DefaultEmbeddingFunction()
    return _local_ef


def using_gemini() -> bool:
    return bool(_gemini_keys())


def embedder_name() -> str:
    return GEMINI_EMBED_MODEL if using_gemini() else "default-minilm"


def _retry_delay(msg: str, default: float = 30.0) -> float:
    """Pull the server-suggested retry delay (seconds) out of a 429 message."""
    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)", msg)
    return float(m.group(1)) + 2 if m else default


def _embed(texts: list[str], task_type: str, max_rounds: int = 5) -> list[list[float]]:
    """Embed texts, rotating keys and backing off on free-tier 429s."""
    if not using_gemini():
        return _local()(texts)
    from google.genai import types
    cfg = types.EmbedContentConfig(task_type=task_type)
    last_err = None
    for attempt in range(max_rounds):
        for client in _clients():
            try:
                resp = client.models.embed_content(model=GEMINI_EMBED_MODEL, contents=texts, config=cfg)
                return [e.values for e in resp.embeddings]
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        # every key failed this round — if it's a quota limit, wait it out
        if "RESOURCE_EXHAUSTED" in str(last_err) or "429" in str(last_err):
            delay = min(_retry_delay(str(last_err)), 65)
            print(f"  [embed] rate limit hit; waiting {delay:.0f}s then retrying "
                  f"(round {attempt + 1}/{max_rounds})...")
            time.sleep(delay)
        else:
            break  # non-quota error — don't spin
    raise RuntimeError(f"all Gemini keys failed for embedding after {max_rounds} rounds: {last_err}")


def embed_documents(texts: list[str]) -> list[list[float]]:
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float]:
    return _embed([text], "RETRIEVAL_QUERY")[0]


def get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def get_collection(department: str):
    """Get (or create) a department's collection. Embeddings are supplied
    explicitly by callers, so no embedding_function is attached."""
    return get_client().get_or_create_collection(name=department)


def reset_collection(department: str):
    """Drop and recreate a department's collection for a clean rebuild."""
    client = get_client()
    try:
        client.delete_collection(name=department)
    except Exception:
        pass  # didn't exist yet
    return client.get_or_create_collection(name=department)
