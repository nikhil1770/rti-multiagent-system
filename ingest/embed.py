"""Build the vector index: embed every department's JSON records into Chroma.

Run after build_records.py (or against the existing placeholder records):
    python ingest/embed.py

Handles both record formats:
  - chunked (from build_records.py): {"department","section","title","topic",
    "source_file","chunks":[{"chunk_id","text"}]}
  - simple placeholder:              {"id","title","content"}

One Chroma collection per department. The index lives under CHROMA_PATH and is
rebuildable — it is NOT committed to git.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vectorstore import reset_collection, embed_documents, embedder_name  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEPT_ROOT = ROOT / "data" / "departments"


def clean_meta(meta: dict) -> dict:
    """Chroma metadata values must be str/int/float/bool (no None)."""
    return {k: ("" if v is None else v) for k, v in meta.items()}


def record_to_entries(record: dict, dept: str):
    """Yield (id, document_text, metadata) tuples for one record file."""
    if "chunks" in record:  # chunked format
        section = record.get("section", record.get("id", "section"))
        base_meta = {
            "department": dept,
            "section": section,
            "title": record.get("title", section),
            "topic": record.get("topic", ""),
            "source_file": record.get("source_file", ""),
        }
        for i, chunk in enumerate(record["chunks"]):
            chunk_id = chunk.get("chunk_id", f"{section}#{i}")
            yield (
                f"{dept}/{chunk_id}",
                chunk["text"],
                clean_meta({**base_meta, "chunk_id": chunk_id}),
            )
    else:  # simple placeholder format
        rid = record.get("id", "record")
        yield (
            f"{dept}/{rid}",
            record.get("content", ""),
            clean_meta({
                "department": dept,
                "section": rid,
                "title": record.get("title", rid),
                "topic": record.get("topic", ""),
                "source_file": "",
                "chunk_id": rid,
            }),
        )


def build_index() -> None:
    if not DEPT_ROOT.exists():
        sys.exit(f"No department data found at {DEPT_ROOT}")

    total_chunks = 0
    for dept_dir in sorted(p for p in DEPT_ROOT.iterdir() if p.is_dir()):
        dept = dept_dir.name
        records_dir = dept_dir / "records"
        if not records_dir.exists():
            continue

        ids, docs, metas = [], [], []
        for record_file in sorted(records_dir.glob("*.json")):
            record = json.loads(record_file.read_text())
            for cid, text, meta in record_to_entries(record, dept):
                if not text.strip():
                    continue
                ids.append(cid)
                docs.append(text)
                metas.append(meta)

        if not ids:
            print(f"  {dept}: no records, skipped")
            continue

        collection = reset_collection(dept)
        # add in batches to stay well under any single-call limits
        for start in range(0, len(ids), 50):
            batch_docs = docs[start:start + 50]
            collection.add(
                ids=ids[start:start + 50],
                documents=batch_docs,
                embeddings=embed_documents(batch_docs),
                metadatas=metas[start:start + 50],
            )
        total_chunks += len(ids)
        print(f"  {dept}: indexed {len(ids)} chunk(s)")

    print(f"\nDone. {total_chunks} chunk(s) indexed using {embedder_name()} embeddings.")


if __name__ == "__main__":
    build_index()
