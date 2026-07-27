"""Turn per-section DOCX files into chunked JSON records.

Workflow:
  1. Run this once to create the empty source/ folders:
         python ingest/build_records.py --init
  2. Drop your section DOCX files into each department's source/ folder,
     named after the section keys (see ingest/sections.py). 10-15 pages each.
  3. Run again to build the JSON records the retrieval agent searches:
         python ingest/build_records.py

Output: data/departments/<dept>/records/<section>.json, each containing the
department/section metadata plus a list of paragraph-based chunks (so a
citation can point to an exact passage, not the whole file).
"""

import argparse
import json
import sys
from pathlib import Path

from sections import DEPARTMENTS

try:
    import docx  # python-docx
except ImportError:
    docx = None

ROOT = Path(__file__).resolve().parent.parent
DEPT_ROOT = ROOT / "data" / "departments"

# Merge paragraphs until a chunk reaches this many characters, then start a new
# chunk. Keeps chunks roughly paragraph-to-passage sized for clean citations.
CHUNK_TARGET_CHARS = 900


def read_docx_paragraphs(path: Path) -> list[str]:
    """Return non-empty paragraph texts from a .docx file."""
    document = docx.Document(str(path))
    paras = [p.text.strip() for p in document.paragraphs]
    return [p for p in paras if p]


def chunk_paragraphs(paragraphs: list[str], target: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Group consecutive paragraphs into chunks of ~target characters."""
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0
    for para in paragraphs:
        buffer.append(para)
        size += len(para)
        if size >= target:
            chunks.append("\n\n".join(buffer))
            buffer, size = [], 0
    if buffer:
        chunks.append("\n\n".join(buffer))
    return chunks


def init_source_folders() -> None:
    """Create source/ + records/ folders and list expected DOCX filenames."""
    for dept_key, dept in DEPARTMENTS.items():
        src = DEPT_ROOT / dept_key / "source"
        src.mkdir(parents=True, exist_ok=True)
        (DEPT_ROOT / dept_key / "records").mkdir(parents=True, exist_ok=True)
        print(f"\n{dept_key}  ({dept['title']})")
        print(f"  drop DOCX files into: {src.relative_to(ROOT)}")
        for section_key, title, _ in dept["sections"]:
            print(f"    - {section_key}.docx   ({title})")


def build() -> None:
    if docx is None:
        sys.exit("python-docx not installed. Activate your venv and run: pip install -r requirements.txt")

    built, missing = 0, []
    for dept_key, dept in DEPARTMENTS.items():
        src = DEPT_ROOT / dept_key / "source"
        records_dir = DEPT_ROOT / dept_key / "records"
        records_dir.mkdir(parents=True, exist_ok=True)

        # keep about.json title in sync with the canonical definition
        about_path = DEPT_ROOT / dept_key / "about.json"
        about = json.loads(about_path.read_text()) if about_path.exists() else {}
        about["department"] = dept["title"]
        about_path.write_text(json.dumps(about, indent=2, ensure_ascii=False))

        for section_key, title, topic in dept["sections"]:
            # Accept either "<section>.docx" or "<dept>_<section>.docx".
            candidates = [src / f"{section_key}.docx", src / f"{dept_key}_{section_key}.docx"]
            docx_path = next((c for c in candidates if c.exists()), None)
            if docx_path is None:
                missing.append(f"{dept_key}/{section_key}.docx")
                continue

            paragraphs = read_docx_paragraphs(docx_path)
            chunks = chunk_paragraphs(paragraphs)
            record = {
                "id": f"{dept_key}/{section_key}",
                "department": dept_key,
                "section": section_key,
                "title": title,
                "topic": topic,
                "source_file": docx_path.name,
                "chunks": [
                    {"chunk_id": f"{section_key}#{i}", "text": text}
                    for i, text in enumerate(chunks)
                ],
            }
            out_path = records_dir / f"{section_key}.json"
            out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
            built += 1
            print(f"  built {dept_key}/{section_key}.json  ({len(chunks)} chunks)")

    print(f"\nDone. {built} section record(s) built.")
    if missing:
        print(f"{len(missing)} section DOCX file(s) not found yet (that's fine — add them later):")
        for m in missing:
            print(f"    - {m}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build chunked JSON records from section DOCX files.")
    parser.add_argument("--init", action="store_true", help="Create source/records folders and list expected filenames.")
    args = parser.parse_args()
    if args.init:
        init_source_folders()
    else:
        build()
