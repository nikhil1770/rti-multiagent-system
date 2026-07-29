"""
Retrieval Agent
---------------
Given a question and its department, searches that department's vector collection
and returns the most relevant chunk(s) with source provenance for citation.

Run the index build first: python ingest/embed.py
Quick test:              python agents/retrieval.py election_commission "party donations above 20000"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vectorstore import get_collection, embed_query  # noqa: E402

# Records are long and dense: the chunk carrying a specific fact often ranks
# below generic policy/intro chunks, so retrieve enough to include it.
TOP_K = 6


def retrieve(question: str, department: str, k: int = TOP_K) -> list[dict]:
    """Return up to k matching chunks, best first.

    Each result: {text, score, department, section, title, chunk_id, source_file}
    `score` is a similarity in [0,1] (higher = closer); derived from Chroma's
    distance so drafting/exemption agents can threshold on confidence.
    """
    collection = get_collection(department)
    if collection.count() == 0:
        return []

    k = min(k, collection.count())
    res = collection.query(
        query_embeddings=[embed_query(question)],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]

    results = []
    for text, meta, dist in zip(docs, metas, dists):
        results.append({
            "text": text,
            "score": round(1.0 / (1.0 + dist), 4),  # distance -> (0,1] similarity
            "department": meta.get("department", department),
            "section": meta.get("section", ""),
            "title": meta.get("title", ""),
            "chunk_id": meta.get("chunk_id", ""),
            "source_file": meta.get("source_file", ""),
        })
    return results


def citation(result: dict) -> str:
    """Human-readable provenance string for a retrieved chunk."""
    parts = [result.get("title") or result.get("section")]
    if result.get("chunk_id"):
        parts.append(f"({result['chunk_id']})")
    return " ".join(p for p in parts if p)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print('Usage: python agents/retrieval.py <department> "<question>"')
        raise SystemExit(1)

    dept, question = sys.argv[1], sys.argv[2]
    hits = retrieve(question, dept)
    if not hits:
        print(f"No results (is the index built? run: python ingest/embed.py) — dept={dept}")
    for i, h in enumerate(hits, 1):
        print(f"\n[{i}] score={h['score']}  {citation(h)}")
        print(f"    {h['text'][:200]}{'...' if len(h['text']) > 200 else ''}")
