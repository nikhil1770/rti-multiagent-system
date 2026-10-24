# Automated RTI / Public Information Request Response System

A multi-agent system built with **LangGraph** and a grounded **RAG** pipeline that automates
searching, drafting, and legally-aware review of RTI (Right to Information) requests across
government departments — with a mandatory human review gate for anything sensitive.

## Problem
Citizens file RTI requests that must legally be answered within 30 days. Today this is manual:
officers search records by hand, decide what's disclosable vs. exempt, and draft replies under
time pressure. This project automates the research and drafting, while keeping a human officer
in control of every disclosure decision.

## Architecture
- **Orchestrator** (LangGraph supervisor) — routes each question through the pipeline, tracks the
  30-day deadline, pauses on flagged questions via `interrupt()`
- **Intake & Classification agent** — parses the RTI PDF, splits it into questions, tags department(s)
- **Retrieval agent** — semantic search over each department's Chroma collection, returns cited chunks
- **Drafting agent** — writes a grounded answer, also handles officer-instructed revisions
- **Exemption / Risk Check agent** — flags anything privacy/security sensitive, per question
- **Public Information Officer** (human) — reviews only flagged questions in the dashboard
- **Dashboard** — request queue, live agent trace, review workspace, SLA panel

The agents run on **Google Gemini** when an API key is set, and fall back to a deterministic
offline mode (extractive drafts + rule-based exemption scan) when no key is present, so the whole
system stays runnable without credentials.

---

## Prerequisites
- **Python 3.10+** (3.13 recommended — LangGraph requires ≥3.10)
- macOS / Linux / WSL

---

## Step-by-step: setup

### 1. Create and activate a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

### 2. Install dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Add your API key (optional but recommended)
```bash
cp .env.example .env
```
Open `.env` and paste a [Google AI Studio](https://aistudio.google.com/apikey) key:
```
GOOGLE_API_KEY=your-key-here
```
You can add up to three keys (`GOOGLE_API_KEY`, `GOOGLE_API_KEY1`, `GOOGLE_API_KEY2`) — the system
rotates to the next key automatically when one hits its free-tier quota. Model is set by
`DRAFTING_MODEL` / `LIGHT_MODEL` (default `gemini-3.5-flash-lite`).

> Skip this step and everything still runs in offline fallback mode.

---

## Step-by-step: build the search index

Department knowledge lives as source files under `data/departments/<dept>/source/`. Each section can
have several source files (provisions, a rules brief, etc.) in **`.docx`, `.md`, or `.txt`** — every
file becomes its own searchable record. Two scripts turn them into the vector index.

### 4. Build JSON records from the source files
```bash
python ingest/build_records.py
```
Chunks each source file (~900 chars/chunk) into `data/departments/<dept>/records/<name>.json`.
(Run `python ingest/build_records.py --init` first to create the empty `source/` folders and list
the expected filenames.)

### 5. Embed the records into the vector index
```bash
python ingest/embed.py
```
Builds one Chroma collection per department under `data/chroma/`. Uses Google's
`gemini-embedding-001` when a key is set (much stronger ranking on dense records), or a local model
as an offline fallback (downloads once, no key needed). The embedder **retries with backoff** on
free-tier rate limits, and only resets a collection after embedding succeeds — so a quota hit never
wipes the index.

Re-run steps 4–5 any time you edit or add a source file. **Note:** Gemini (3072-dim) and the local
model (384-dim) have different dimensions, so if you add or remove your API key you must rebuild the
index (step 5).

---

## Step-by-step: run it

### Option A — the dashboard (recommended)
```bash
uvicorn dashboard.app:app --reload --port 8000
```
Open **http://localhost:8000**, then:
1. Pick a sample RTI from the dropdown and click **New request**, or click **Upload RTI** to submit
   your own PDF/DOCX/TXT application.
2. Watch the **live trace** (right) as agents classify → retrieve → draft → check each question.
3. When a question is flagged, use the **officer review workspace**: **Approve**, **Reject
   (re-search)**, or type a **fix instruction** (quick buttons for common ones).
4. Once every question is resolved, the **assembled final reply** appears and the **SLA panel**
   updates.

### Option B — command line (single request, end to end)
```bash
python orchestrator/main.py test_data/sample_rtis/rti_04_ration_card_pds.pdf
```
Runs one RTI through the whole graph, auto-approving flagged questions, and prints the final reply
plus days remaining until the statutory deadline.

### Test individual pieces
```bash
# Retrieval only
python agents/retrieval.py mcd_building "fee and time limit for building plan sanction"
python agents/retrieval.py delhi_food  "documents required for a new ration card"

# Intake + classification only
python agents/intake_classifier.py test_data/sample_rtis/rti_04_ration_card_pds.pdf
```

---

## Testing
A hermetic test suite (runs offline — no API key or database needed) covers intake parsing,
exemption screening, drafting, retrieval ranking, and the full graph including the human-review
interrupt/resume gate:
```bash
python -m pytest tests/ -q
```

---

## Durable state (Postgres checkpointer)
By default the orchestrator keeps state in memory. To make in-flight (interrupted) requests
survive a restart, start Postgres and the app switches to the LangGraph Postgres checkpointer
automatically (it reads `POSTGRES_URL` from `.env`):
```bash
docker compose up -d          # starts Postgres on localhost:5432
uvicorn dashboard.app:app --port 8000
# console prints: [checkpointer] using Postgres — state is durable across restarts
```
If Postgres is unreachable it falls back to in-memory with a clear message, so the app always runs.

---

## Deployment (optional — get a shareable link)
The app is a single FastAPI service. On [Render](https://render.com) or [Fly.io](https://fly.io):
- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn dashboard.app:app --host 0.0.0.0 --port $PORT`
- **Env vars:** set `GOOGLE_API_KEY` (and optionally `POSTGRES_URL` pointing at a managed Postgres).
- Commit `data/departments/**/records/*.json` so the index can be built on deploy; run
  `python ingest/embed.py` as a post-deploy/release step to populate the vector store.

---

## Project structure
```
rti-multiagent-system/
├── agents/                 intake_classifier, retrieval, drafting, exemption_check
├── orchestrator/main.py    LangGraph state graph + human-review gate + deadline + checkpointer
├── ingest/                 build_records.py (source→JSON), embed.py (JSON→vector index), sections.py
├── dashboard/              app.py (FastAPI + upload), manager.py, index.html (web UI)
├── tests/                  hermetic pytest suite (offline, no key/DB required)
├── data/
│   ├── departments/<d>/    source/ (.docx/.md/.txt), records/ (.json), about.json
│   └── exemption_precedent/ CIC decisions corpus (feeds exemption screening, not retrieval)
├── test_data/sample_rtis/  sample RTI application PDFs
├── vectorstore.py          shared Chroma access + Gemini/local embeddings (with backoff)
├── llm.py                  Gemini wrapper with multi-key failover + offline fallback
├── docker-compose.yml      Postgres for durable checkpointing
└── requirements.txt
```

---

## Departments in this demo
Two NCT-Delhi departments, so a single RTI can plausibly route across both:

1. **Municipal Corporation of Delhi — Building Approvals** (`mcd_building`)
   — plan sanction, occupancy certificate, unauthorised construction, property tax
2. **Delhi Dept. of Food, Civil Supplies & Consumer Affairs** (`delhi_food`)
   — ration card, fair-price-shop licensing, PDS stock, grievance

Each subsection carries up to three searchable layers: **provisions** (the rules), a **rules brief**
(distilled fees/timelines/documents), and **synthetic case records** (example applications). A
separate `exemption_precedent` corpus of CIC decisions feeds exemption screening only.

## Design decisions worth knowing about
- The AI never autonomously decides to disclose or withhold — that decision always goes to a human
  Public Information Officer via the `interrupt()` review gate.
- Exemption flagging happens **per question**, not per document, so the officer only reviews what
  actually needs judgment.
- Every drafted fact is grounded in a retrieved record with a citation; when the records don't
  contain the answer, the drafting agent says so instead of inventing one.
- Source files are kept in the repo (diffable); the vector index under `data/chroma/` is derived and
  rebuildable, so it is git-ignored.

## Troubleshooting
- **Answers look like raw record text** — no API key set; add `GOOGLE_API_KEY` to `.env`.
- **`429 RESOURCE_EXHAUSTED`** — free-tier embedding/generation quota; the embedder auto-retries with
  backoff, and adding a second key raises headroom.
- **Retrieval returns nothing / dimension mismatch** — the index isn't built or was built with a
  different embedder; run step 5 to rebuild.

## Engineering notes worth mentioning in an interview
- **Grounding is enforced end-to-end.** The drafting agent answers only from retrieved chunks and
  says "not in the records" rather than fabricating.
- **A real retrieval-quality bug and fix.** A local embedding model ranked a fact-bearing chunk near
  the bottom of its department; switching to Gemini embeddings (with correct document/query task
  types) moved it into the top results and corrected the answer. Embedding choice is a first-class
  quality lever, not a default.
- **Human-in-the-loop is a graph primitive**, via LangGraph `interrupt()` + a checkpointer, not a
  bolted-on flag — so a paused request can genuinely be resumed (durably, with Postgres).

## Status
Portfolio/demo project, not affiliated with any government body. All department data is either
public-domain or synthetic — no real citizen data is used.
