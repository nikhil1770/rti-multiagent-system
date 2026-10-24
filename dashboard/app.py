"""FastAPI backend for the RTI dashboard.

Serves the single-page UI at / and a small JSON API the page polls. Run:
    source .venv/bin/activate
    uvicorn dashboard.app:app --reload --port 8000
Then open http://localhost:8000
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from dashboard.manager import Manager  # noqa: E402

app = FastAPI(title="RTI Multi-Agent Dashboard")
manager = Manager()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UPLOAD_DIR = ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIR = ROOT / "frontend"


class StartBody(BaseModel):
    sample: str


class DecisionBody(BaseModel):
    action: str                 # approve | instruct | reject | partial
    instruction: str | None = None


class ForwardBody(BaseModel):
    receiptDate: str | None = None
    mode: str | None = None
    feePaid: str | None = None
    bpl: bool = False
    reference: str | None = None


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/samples")
def samples():
    return manager.list_samples()


@app.post("/api/requests")
def start_request(body: StartBody):
    if body.sample not in manager.list_samples():
        raise HTTPException(404, "unknown sample")
    return {"id": manager.start(body.sample)}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(400, "upload a .pdf, .docx, or .txt file")
    dest = UPLOAD_DIR / file.filename
    dest.write_bytes(await file.read())
    return {"id": manager.start_path(str(dest), Path(file.filename).stem)}


@app.get("/api/requests")
def queue():
    return manager.queue()


@app.get("/api/requests/{rid}")
def detail(rid: str):
    d = manager.detail(rid)
    if not d:
        raise HTTPException(404, "unknown request")
    return d


@app.post("/api/requests/{rid}/decide")
def decide(rid: str, body: DecisionBody):
    if rid not in manager.requests:
        raise HTTPException(404, "unknown request")
    if manager.requests[rid]["status"] != "awaiting_review":
        raise HTTPException(409, "request is not awaiting review")
    manager.decide(rid, body.model_dump())
    return {"ok": True}


@app.get("/api/sla")
def sla():
    return manager.sla()


# --- workbench (React UI) endpoints -----------------------------------------

@app.get("/api/wb/requests")
def wb_requests(dept: str | None = None):
    return manager.wb_queue(dept)


@app.get("/api/wb/requests/{rid}")
def wb_request(rid: str, dept: str | None = None):
    d = manager.wb_detail(rid, dept)
    if not d:
        raise HTTPException(404, "unknown request")
    return d


# --- clerk intake endpoints (extract -> verify -> forward to PIO) -----------

@app.post("/api/intake/upload")
async def intake_upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(400, "upload a .pdf, .docx, or .txt file")
    dest = UPLOAD_DIR / file.filename
    dest.write_bytes(await file.read())
    return manager.intake_upload(str(dest), file.filename)


@app.get("/api/intake/queue")
def intake_queue(dept: str | None = None):
    return manager.intake_queue(dept)


@app.get("/api/intake/{iid}")
def intake_detail(iid: str):
    d = manager.intake_detail(iid)
    if not d:
        raise HTTPException(404, "unknown intake")
    return d


@app.post("/api/intake/{iid}/forward")
def intake_forward(iid: str, body: ForwardBody):
    rid = manager.intake_forward(iid, body.model_dump())
    if not rid:
        raise HTTPException(404, "unknown intake")
    return {"ok": True, "request_id": rid}


@app.post("/api/intake/{iid}/resolve")
def intake_resolve(iid: str, action: str):
    if action not in ("transferred", "rejected"):
        raise HTTPException(400, "action must be transferred or rejected")
    if not manager.intake_resolve(iid, action):
        raise HTTPException(404, "unknown intake")
    return {"ok": True}


# --- serve the React pages (same-origin, so relative /api fetches work) ------

if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")


@app.get("/app")
def launcher():
    return RedirectResponse(url="/ui/rti_login.html")
