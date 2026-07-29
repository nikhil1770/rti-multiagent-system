"""Shared MCP server factory — one server per department, same tools.

Each department server (e.g. election_commission_server.py) is a thin entry
point that calls build_server("<dept>"). This standardizes how the Retrieval
Agent asks any department for records (the point of using MCP).

Tools exposed per department:
  - search(question, k)  -> top-k matching record chunks with provenance
  - about()              -> the department's about.json metadata
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from agents.retrieval import retrieve  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEPT_ROOT = ROOT / "data" / "departments"


def build_server(department: str) -> FastMCP:
    mcp = FastMCP(department)

    @mcp.tool()
    def search(question: str, k: int = 3) -> list[dict]:
        """Search this department's records for the most relevant chunk(s)."""
        return retrieve(question, department, k)

    @mcp.tool()
    def about() -> dict:
        """Return this department's metadata (mandate, jurisdiction, contact)."""
        about_path = DEPT_ROOT / department / "about.json"
        if about_path.exists():
            return json.loads(about_path.read_text())
        return {"department": department}

    return mcp
