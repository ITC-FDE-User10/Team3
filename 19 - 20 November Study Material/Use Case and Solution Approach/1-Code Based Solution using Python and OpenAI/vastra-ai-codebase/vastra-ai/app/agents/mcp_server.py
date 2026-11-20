"""Minimal MCP (Model Context Protocol) server over stdio, JSON-RPC 2.0.

Exposes Vastra's tool registry so any MCP-capable client (Claude Desktop, IDE agents,
partner agents) can use check_stock, quote_price, reorder_suggestion, policy_lookup.
Write tools are only exposed when VASTRA_MCP_SCOPES includes ops.write.

Run:  python -m app.agents.mcp_server
For production use the official `mcp` Python SDK with OAuth; this file has no
dependencies so it is easy to read and test.
"""
from __future__ import annotations

import json
import os
import sys

from app.agents.tools import TOOLS, call_tool

SCOPES = set(os.getenv("VASTRA_MCP_SCOPES", "ops.read").split(","))
PROTOCOL_VERSION = "2025-06-18"


def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "vastra-ops", "version": "1.0.0"}}
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        result = {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.schema}
                            for t in TOOLS.values() if t.scope in SCOPES]}
    elif method == "tools/call":
        p = req.get("params", {})
        try:
            out = call_tool(p["name"], p.get("arguments", {}), SCOPES, actor="mcp-client")
            result = {"content": [{"type": "text", "text": json.dumps(out, default=str)}], "isError": False}
        except Exception as exc:  # noqa: BLE001
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        resp = handle(json.loads(line))
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
