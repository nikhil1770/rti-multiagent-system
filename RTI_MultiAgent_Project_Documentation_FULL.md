# Automated RTI / Public Information Request Response System
### Complete Project Documentation

---

## 1. Problem Statement

Under the **Right to Information (RTI) Act** in India (and equivalent laws like the US **FOIA**), any citizen can file a request asking a government department for specific information or records. By law, the department must respond within a strict deadline — **30 days in India**.

Today, handling one of these requests is almost entirely manual:

- A **Public Information Officer (PIO)** reads the request and figures out which department(s) hold the relevant records.
- They manually search files, databases, and past responses across departments that often don't talk to each other.
- They must decide what can legally be disclosed vs. what falls under an **exemption** (personal privacy, national security, an ongoing investigation, etc.) — a decision with real legal consequences if done wrong.
- They draft a formal reply, get it reviewed, and send it — all before the statutory clock runs out.

The result: massive backlogs, inconsistent decisions between officers and departments, requests that quietly miss their legal deadline, and senior officers spending hours on searches a machine could do in seconds.

**The core problem:** there is no system that can search across departments, draft a grounded and legally-aware response, flag anything that needs a human legal judgment call, and track the statutory deadline — while giving oversight bodies visibility into how the whole process is performing.

---

## 2. Motivation

- **Unambiguous, non-controversial value.** Faster, more consistent government transparency is something almost everyone agrees is good.
- **Real legal stakes force real engineering discipline.** An AI system cannot be allowed to autonomously decide to withhold or release government records — that decision stays with a human officer, always.
- **A genuinely hard grounding problem.** Every disclosed fact must trace to an actual record, and every withheld fact must map to an actual legal exemption. Hallucination is unacceptable here, which is what makes the RAG design a real skill, not a checkbox.
- **Statutory deadlines make the dashboard meaningful.** "Requests at risk of breaching their 30-day legal deadline" is a number a government oversight body would genuinely care about.
- **Distinctive on a resume.** Most portfolios are business-automation bots. A civic-tech multi-agent system with a real ethical design constraint stands out precisely because almost nobody else builds it.

---

## 3. Agents & Tools

### 3.1 Agent Roster

Five AI agents, one orchestrator, and one human — each with exactly one job.

| # | Agent | What it does | Input | Output |
|---|---|---|---|---|
| 0 | **Orchestrator** (LangGraph supervisor) | Decides what happens next for each question. Tracks the 30-day deadline. | Current request status | A routing decision |
| 1 | **Intake & Classification Agent** | Parses the document, splits it into questions, decides which department(s) each belongs to | Raw RTI document | List of questions, each tagged with department(s) |
| 2 | **Retrieval Agent** | Searches the relevant department's vector collection and returns the best-matching record(s), with source provenance | One question + department | Retrieved record(s) |
| 3 | **Drafting Agent** | Writes a plain-language answer grounded in the retrieved record(s). Also handles **officer-instructed revisions** — rewrites using the officer's specific fix instead of re-searching | Record(s) (+ officer instruction, if any) | Written answer with citation |
| 4 | **Exemption / Risk Check Agent** | Checks one drafted answer for anything sensitive (privacy, security, ongoing investigation) — evaluated **per question**, not per document | One drafted answer | "Safe" or "flagged, with reason" |
| — | **Public Information Officer** (human) | Reviews only flagged questions. Approves, gives a fix instruction, or rejects. | Flagged answer + citation + reason | Approval / instruction / rejection |
| — | **Assembler** (plain code, no AI) | Stitches all approved answers into one final reply | All approved answers | One combined document |

### 3.2 Tools Mapped to Each Component

| Where it's needed | Tool | Why |
|---|---|---|
| Orchestrator — routing & state | **LangGraph** | Runs the graph, decides next agent, holds shared state |
| Orchestrator — pausing for the officer | LangGraph's **`interrupt()`** | Pauses execution on a flagged question, waits for the officer, resumes with their decision |
| Orchestrator — surviving restarts | **PostgreSQL** (LangGraph checkpointer) | Saves progress so a mid-review request doesn't lose its place on a restart |
| Intake & Classification | **PyMuPDF / python-docx** | Extracts text from scanned or typed applications |
| Retrieval — talking to departments | **MCP (Model Context Protocol)** — one server per department | Standardizes how the agent asks each department for records |
| Retrieval — finding relevant records | **Chroma** or **Qdrant** (vector database) | Stores each department's records so the agent can search by meaning |
| Exemption Check | Same vector DB, seeded with **RTI Act exemption categories** | Lets the agent compare a drafted answer against real exemption rules |
| Backend | **FastAPI** | Hosts the app, exposes it to the dashboard |
| Dashboard — live updates | **SSE / WebSockets** via **`astream_events`** | Streams agent activity to the dashboard in real time |
| Dashboard — the screens | **Next.js (React)** or **Streamlit** | Builds the queue, review workspace, progress bars, SLA panel |
| Debugging | **LangSmith** or **LangGraph Studio** | See agent reasoning traces during development |
| Running/demoing | **Docker Compose** locally; **Fly.io / Render** for a link | Portable, no manual setup needed |

### 3.3 Cost — What's Actually Free

The entire stack above is free/open-source. **The one real cost is the LLM API itself** (billed per token). For a small interview demo, a single Google account's Gemini API free tier is more than sufficient — no need for multiple accounts or paid tiers. Use Flash/Flash-Lite models for the lighter agents (classification, exemption-check) and reserve a stronger model only for drafting, to stretch the free quota further.

---

## 4. System Architecture & Flow

One LangGraph graph with a supervisor node that routes control based on the current state of each *question* — not a fixed linear script.

1. **Request intake.** The parser agent extracts questions and reference numbers from the uploaded RTI.
2. **Orchestrator takes control.** Tracks the 30-day deadline in Postgres.
3. **Multi-department retrieval.** The retrieval agent queries the relevant department's MCP server / vector collection, pulling back the specific record(s) with provenance.
4. **Classification + drafting.** The classifier tags department(s); the drafting agent writes a grounded, plain-language answer citing its source.
5. **Exemption check — per question, not per document.** A 10-question request might have 8 auto-approved and only 2 flagged — the officer never touches the parts that were already fine.
6. **Officer review gate — only sees flagged pieces.** Using `interrupt()`, execution pauses only on flagged questions. Three options:
   - **Approve** — moves forward as-is.
   - **Give a fix instruction** (e.g. "remove the address, keep the department name") — goes to the **drafting agent only**, which rewrites using the same record plus the instruction. No need to re-search; the record was fine, only the wording needed to change.
   - **Reject** — sends the question back to retrieval, for cases where the record itself was wrong.
7. **Dispatch & tracking.** Once every question is approved (auto or by the officer), the assembler stitches the final reply, sends it, and the dashboard logs resolution time against the deadline.

**Why the human gate is non-negotiable:** disclosure decisions carry real legal weight. The AI never makes that call alone — it only prepares the ground for a person to decide faster, with better information.

---

## 5. How Retrieval Actually Works (RAG Mechanics)

This is worth documenting explicitly, since it's the part of the system most likely to get technical follow-up questions in an interview.

**JSON is source data, not the search index.** You never hand-populate a vector database directly. The real pipeline is:

```
JSON/text files (readable, git-friendly source data)
        ↓  (an ingestion script chunks + embeds this text)
Vector database (Chroma/Qdrant) — a derived, rebuildable search index
```

JSON stays in the repo because it's diffable and editable; the vector index is regenerated from it by a script and isn't committed to git.

**Retrieval is a flat similarity search, not a hierarchical lookup.** All the records/segments within one department sit as equal siblings in a single collection — there's no "check this file's name, then decide whether to look inside." Every question is compared against **every** segment in that department's collection simultaneously, using embedding similarity (how close the *meaning* of the question is to the *meaning* of each segment, not keyword overlap). The highest-scoring segment(s) are returned.

**When an answer spans more than one segment**, the system retrieves **top-k** matches (e.g. top 3), not just the single best one — so a question needing both a "stock received" record and a "stock distributed" record gets both handed to the drafting agent together.

**Topic tagging (metadata) is an optional scaling aid, not a requirement at this size.** A chunk can carry a small label like `"topic": "export"` alongside its content. This doesn't replace similarity search — it just lets you optionally *filter* the pool before searching, useful once a department has huge volumes of data. With 3-4 records per department, plain flat search is already sufficient.

**Chunking matters once documents get longer than these demo records.** A short JSON record is already one chunk. A 10-page real document would be split into paragraph-sized chunks first, each embedded separately — this is also what gives you precise **citations** (pointing to the exact paragraph, not just "somewhere in this file").

---

## 6. Departments & Test Data

### 6.1 Departments (7 total)

| # | Department | Tier |
|---|---|---|
| 1 | Election Commission of India — Political Finance Compliance Cell | Central |
| 2 | Supreme Court of India — CPIO Office | Central / Judiciary |
| 3 | Ministry of Home Affairs / PMO — Records & Archives | Central |
| 4 | Delhi Dept. of Food, Civil Supplies & Consumer Affairs | State (NCT Delhi) |
| 5 | Ministry of Defence — Land Records | Central |
| 6 | Municipal Corporation — Building Approvals | Municipal |
| 7 | State Environment Department | State |

Each department folder contains an `about.json` (general department info — mandate, jurisdiction, contact; realistic filler, not tied to any specific test RTI) and 3-4 `records/*.json` files. None of the record files reference which test RTI they answer — the retrieval agent has to find the right one by actual relevance, the same way a real search index would.

### 6.2 Test RTIs (5 real, publicly documented cases)

| # | Case | Department(s) needed | What it demonstrates |
|---|---|---|---|
| 1 | Political party funding (2013 CIC ruling) | Election Commission | A contested case — good for testing the officer's "instructed revision" loop |
| 2 | Judges' asset declaration case | Supreme Court | Exemption agent flags for **privacy** |
| 3 | Netaji Bose files declassification | MHA / PMO | Exemption agent flags for a **different** reason — national security |
| 4 | Ration card / PDS campaign (Parivartan) | Food & Civil Supplies | The clean, routine path — auto-approved, no flag |
| 5 | Adarsh Housing Society scam | Defence + Municipal + Environment (3 depts) | Tests multi-department routing from a single request |

Reformatted as realistic application PDFs in `test_data/sample_rtis/`. Real historical figures/organizations are named where factually accurate, with placeholder contact details (not their real ones).

---

## 7. Dashboard — Detailed Breakdown

### 7.1 Request Queue View
All open requests: reference, department(s), days remaining until deadline, status. Deadline-approaching requests are visually flagged — the single most important view for accountability.

### 7.2 Live Execution Trace
Real-time view of which agent is running, what it retrieved, from which department — streamed via `astream_events`. Lets a reviewer watch the search-and-draft process happen instead of trusting a black box.

### 7.3 Citation & Exemption Viewer
Every drafted answer shown beside the exact record it's grounded in. Exemption flags show the specific legal category triggered.

### 7.4 Officer Review Queue — a workspace, not just an inbox
- Only individual **flagged questions** — never a whole request, never anything already auto-approved.
- Three actions: **Approve**, **Give a fix instruction**, **Reject (back to retrieval)**.
- Fix instructions show a live "revising..." status, then a **before/after view** of the change.
- **Quick-action buttons** for common fixes ("Remove name," "Remove address," "Add privacy disclaimer").
- **Instruction history** — every fix instruction stays visible per question, useful for audits.

### 7.5 SLA & Accountability Panel
- Requests at risk of breaching deadline
- Average resolution time vs. legal limit
- % requiring exemption review vs. straightforward disclosure
- Average officer rounds per flagged answer (shows whether drafting is actually improving)
- Backlog trend by department

### 7.6 Progress Tracking
- **Per-request progress bar** (e.g. "8/10 questions done") with each question's status
- **System-wide progress**: total requests today, completed, awaiting review, in processing, at risk
- **Pipeline bottleneck view**: shows where remaining work is stuck (officer review vs. retrieval vs. drafting) — tells whoever manages the system whether to add officers or fix the AI

---

## 8. Repository Structure

```
rti-multiagent-system/
├── README.md
├── .gitignore                    ← keeps .env out of version control
├── .env.example                  ← template; copy to .env locally, never commit .env
├── requirements.txt
├── docker-compose.yml            ← Postgres (+ Chroma, commented out until needed)
├── docs/
│   └── PROJECT_DOCUMENTATION.md  ← this document
├── test_data/sample_rtis/        ← the 5 test RTI PDFs
├── data/departments/<dept>/
│   ├── about.json
│   └── records/record_*.json
├── mcp_servers/                  ← one stub per department
├── agents/                       ← intake_classifier.py, retrieval.py, drafting.py, exemption_check.py
├── orchestrator/main.py          ← LangGraph graph gets built here
└── dashboard/                    ← Next.js/Streamlit frontend (not yet built)
```

**Getting it onto GitHub:**
```bash
cd rti-multiagent-system
cp .env.example .env      # then add your real API key — never commit this file
git init
git add .
git commit -m "Initial project scaffold"
git remote add origin https://github.com/YOUR_USERNAME/rti-multiagent-system.git
git branch -M main
git push -u origin main
```
Always run `git status` before your first push and confirm `.env` is *not* in the list of files about to be committed.

**Suggested GitHub "About" description:**
> Multi-agent RTI/FOIA response system built with LangGraph and MCP — supervisor-orchestrated agents for multi-department retrieval, drafting, and legal exemption screening, with a human-in-the-loop review gate and live dashboard.

**Suggested topics:** `langgraph` `multi-agent` `mcp` `rag` `llm` `ai-agents` `govtech` `civic-tech` `human-in-the-loop` `python` `fastapi` `vector-database`

---

## 9. Suggested Build Order (2-3 weeks)

| Phase | Days | Focus |
|---|---|---|
| 1 | 1-3 | LangGraph skeleton: supervisor + stub agents on one hardcoded request, deadline tracking |
| 2 | 4-7 | Real parsing + retrieval agent against the seeded department data |
| 3 | 8-11 | Drafting + exemption-check agents, MCP servers per department, officer review gate + instructed revision |
| 4 | 12-16 | Dashboard: queue and trace view first, then review workspace, then SLA/progress panel |
| 5 | 17-21 | Polish: demo video, README design-decisions section, final GitHub push |

---

## 10. Demo Video Plan

A 60-90 second clip, screen-recorded (OBS Studio or Loom, both free):
1. (5s) The problem, one sentence
2. (15s) Upload a sample RTI → queue picks it up, deadline starts tracking
3. (20s) Live trace — agents visibly classifying, retrieving, drafting, checking
4. (20s) Review queue — one question auto-approved, one flagged with a fix instruction applied live
5. (15s) Final answer assembled and sent, SLA panel updates
6. (5s) End card — name, GitHub link, tech stack

---

## 11. Future / Stretch Goals

- Public-facing, read-only status tracker for citizens
- Department-level performance comparison
- A learning loop where officer edits improve future exemption-flagging
- Multi-language intake support
- Metadata/topic filtering on top of similarity search, once department data volume grows

---

## 12. One-Line Resume Summary

*"Built a multi-agent RTI/FOIA-style public records response system using LangGraph and MCP — supervisor-orchestrated agents for multi-department retrieval, drafting, and legal exemption screening, with a mandatory human officer review gate and a live SLA-tracking dashboard for statutory deadline compliance."*
