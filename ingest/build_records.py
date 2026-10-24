"""Turn per-section source files into chunked JSON records.

Workflow:
  1. Create the empty source/ folders:
         python ingest/build_records.py --init
  2. Drop one source file per section into each department's source/ folder,
     named after the section key (see ingest/sections.py). Accepted formats:
     .docx, .md, .txt  (a .docx that is actually plain text is handled too).
  3. Build the JSON records the retrieval agent searches:
         python ingest/build_records.py

Output: data/departments/<dept>/records/<section>.json — department/section
metadata plus paragraph-based chunks (so a citation points to an exact passage).
"""

import argparse
import json
import sys
from pathlib import Path

from sections import DEPARTMENTS

try:
    import docx  # python-docx (only needed for real .docx files)
except ImportError:
    docx = None

ROOT = Path(__file__).resolve().parent.parent
DEPT_ROOT = ROOT / "data" / "departments"

# Accepted source extensions, in priority order.
SOURCE_EXTS = (".pdf", ".docx", ".md", ".txt")

# Merge paragraphs until a chunk reaches this many characters, then start a new
# chunk. Keeps chunks roughly paragraph-to-passage sized for clean citations.
CHUNK_TARGET_CHARS = 900


def _read_pdf(path: Path) -> list[str]:
    """Extract text lines from a PDF (PyMuPDF). Empty if the PDF is scanned
    (image-only) — OCR is not applied here."""
    import fitz  # pymupdf
    doc = fitz.open(str(path))
    text = "\n".join(page.get_text() for page in doc)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        print(f"    ! {path.name}: no extractable text (looks scanned) — needs OCR, skipped")
    return lines


def read_source(path: Path) -> list[str]:
    """Return non-empty text blocks from a .pdf / .docx / .md / .txt source file.

    PDFs are read via PyMuPDF; a real .docx via python-docx; a .md/.txt (or a
    .docx that is actually plain text, i.e. mislabeled) is read as UTF-8 text.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx" and docx is not None:
        try:
            return [p.text.strip() for p in docx.Document(str(path)).paragraphs if p.text.strip()]
        except Exception:
            pass  # not a real .docx — fall through to plain-text reading
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [line.strip() for line in text.splitlines() if line.strip()]


def provenance_for(filename: str) -> str:
    """Tag content origin so real vs. synthetic is queryable in metadata."""
    name = filename.lower()
    if "synthetic" in name or "_cases" in name:
        return "synthetic_grounded"
    if "_rules_brief" in name:
        return "real_distilled"
    return "real_public"


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


def section_key_for(dept_key: str, stem: str) -> str:
    """Derive a section key from a source filename stem (strip an optional
    <dept>_ prefix). Any file in source/ becomes its own searchable record."""
    return stem[len(dept_key) + 1:] if stem.startswith(dept_key + "_") else stem


def init_source_folders() -> None:
    """Create source/ + records/ folders and list expected filenames."""
    for dept_key, dept in DEPARTMENTS.items():
        src = DEPT_ROOT / dept_key / "source"
        src.mkdir(parents=True, exist_ok=True)
        (DEPT_ROOT / dept_key / "records").mkdir(parents=True, exist_ok=True)
        print(f"\n{dept_key}  ({dept['title']})")
        print(f"  drop a .docx/.md/.txt file into: {src.relative_to(ROOT)}")
        for section_key, title, _ in dept["sections"]:
            print(f"    - {section_key}.(docx|md|txt)   ({title})")


def build() -> None:
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

        meta = {sk: (title, topic) for sk, title, topic in dept["sections"]}
        seen = set()

        # Ingest EVERY source file in the folder — a section may have several
        # (e.g. provisions + a rules brief). Each file becomes its own record.
        for source_path in sorted(src.glob("*")):
            if source_path.suffix.lower() not in SOURCE_EXTS or source_path.name.startswith("."):
                continue
            section_key = section_key_for(dept_key, source_path.stem)
            title, topic = meta.get(section_key, (section_key.replace("_", " ").title(), ""))
            seen.add(section_key)

            chunks = chunk_paragraphs(read_source(source_path))
            record = {
                "id": f"{dept_key}/{section_key}",
                "department": dept_key,
                "section": section_key,
                "title": title,
                "topic": topic,
                "source_file": source_path.name,
                "provenance": provenance_for(source_path.name),
                "chunks": [
                    {"chunk_id": f"{section_key}#{i}", "text": text}
                    for i, text in enumerate(chunks)
                ],
            }
            (records_dir / f"{section_key}.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False)
            )
            built += 1
            print(f"  built {dept_key}/{section_key}.json  ({len(chunks)} chunks, from {source_path.name})")

        for section_key, _, _ in dept["sections"]:
            if section_key not in seen:
                missing.append(f"{dept_key}/{section_key}")

    print(f"\nDone. {built} record(s) built.")
    if missing:
        print(f"{len(missing)} canonical section(s) still have no source file (fine — add later):")
        for m in missing:
            print(f"    - {m}.(docx|md|txt)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build chunked JSON records from section source files.")
    parser.add_argument("--init", action="store_true", help="Create source/records folders and list expected filenames.")
    args = parser.parse_args()
    if args.init:
        init_source_folders()
    else:
        build()
