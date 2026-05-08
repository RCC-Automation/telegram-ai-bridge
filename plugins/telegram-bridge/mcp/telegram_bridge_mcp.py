#! python3
"""Minimal MCP server for the local Telegram bridge plugin.

This intentionally delegates all operations to telegram_bridge_manager.py so
the CLI, skill, and MCP surfaces share the same behavior.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANAGER = PLUGIN_ROOT / "scripts" / "telegram_bridge_manager.py"


TOOLS: dict[str, dict[str, Any]] = {
    "status": {
        "description": "Show process, routing, and log status for the Telegram bridge.",
        "args": [],
    },
    "list_chats": {
        "description": "List known Telegram chats.",
        "args": ["chats"],
    },
    "list_threads": {
        "description": "List known Codex threads.",
        "args": ["threads"],
    },
    "use_thread": {
        "description": "Switch the active Codex thread used by Telegram.",
        "args": ["use-thread"],
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    "use_chat": {
        "description": "Switch the active Telegram chat used for proactive sends.",
        "args": ["use-chat"],
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    "voice_status": {
        "description": "Show local voice transcription configuration status.",
        "args": ["voice-status"],
    },
    "logs": {
        "description": "Show recent Telegram gateway log lines.",
        "args": ["logs"],
        "inputSchema": {
            "type": "object",
            "properties": {"lines": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
    },
    "restart": {
        "description": "Restart the Telegram bridge using the existing launcher.",
        "args": ["restart"],
    },
}


def manager_call(args: list[str]) -> str:
    result = subprocess.run(
        ["py", "-3", str(MANAGER), *args],
        cwd=str(PLUGIN_ROOT.parents[1]),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    output = result.stdout.strip()
    if result.stderr.strip():
        output = (output + "\n" if output else "") + result.stderr.strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"Manager returned {result.returncode}")
    return output


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def tool_schema(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": spec["description"],
        "inputSchema": spec.get("inputSchema", {"type": "object", "properties": {}}),
    }


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return rpc_result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "telegram-bridge", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return rpc_result(request_id, {"tools": [tool_schema(name, spec) for name, spec in TOOLS.items()]})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if not spec:
            return rpc_error(request_id, -32602, f"Unknown tool: {name}")
        args = list(spec["args"])
        if name in {"use_thread", "use_chat"}:
            args.append(str(arguments.get("ref") or ""))
        if name == "logs" and arguments.get("lines"):
            args.extend(["--lines", str(arguments["lines"])])
        try:
            text = manager_call(args)
        except Exception as exc:
            return rpc_error(request_id, -32000, str(exc))
        return rpc_result(request_id, {"content": [{"type": "text", "text": text}]})
    return rpc_error(request_id, -32601, f"Unsupported method: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.lstrip("\ufeff").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle(request)
        except Exception as exc:
            response = rpc_error(None, -32700, str(exc))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
