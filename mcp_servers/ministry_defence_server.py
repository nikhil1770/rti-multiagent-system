"""MCP server — ministry_defence
Thin entry point; all logic is in mcp_servers/_factory.build_server.
Run standalone:  python mcp_servers/ministry_defence_server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_servers._factory import build_server

mcp = build_server("ministry_defence")

if __name__ == "__main__":
    mcp.run()
