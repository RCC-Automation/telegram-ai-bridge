#! python3
"""MCP server for the embedded local messaging bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HUB_ROOT = PLUGIN_ROOT / "hub"
if str(HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(HUB_ROOT))

import messaging_hub as hub


TOOLS: dict[str, dict[str, Any]] = {
    "messaging_status": {
        "description": "Show embedded messaging status for Telegram, WhatsApp, and the legacy wake adapter.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_send_telegram": {
        "description": "Send a Telegram message through the local notifier. Does not launch Codex.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "chat": {"type": "string", "description": "Optional Telegram chat alias or id."},
            },
            "required": ["text"],
        },
    },
    "messaging_send_telegram_image": {
        "description": "Send a local image file to Telegram through the local notifier. Does not launch Codex.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "caption": {"type": "string"},
                "chat": {"type": "string", "description": "Optional Telegram chat alias or id."},
            },
            "required": ["image_path"],
        },
    },
    "messaging_read_telegram_inbox": {
        "description": "Read pending Telegram inbox messages without clearing them.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        },
    },
    "messaging_clear_telegram_inbox": {
        "description": "Read and clear pending Telegram inbox messages.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_list_telegram_chats": {
        "description": "List known Telegram chats and the active Telegram chat.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_send_whatsapp": {
        "description": "Send a WhatsApp message through the local WhatsApp bridge. Requires confirm=true and an allowlisted recipient.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "message": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            "required": ["recipient", "message"],
        },
    },
    "messaging_search_whatsapp_contacts": {
        "description": "Search WhatsApp contacts from the local WhatsApp message database.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    "messaging_read_whatsapp_messages": {
        "description": "Read WhatsApp messages from the local WhatsApp message database.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "chat_jid": {"type": "string"},
                "sender_phone_number": {"type": "string"},
                "after": {"type": "string"},
                "before": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "page": {"type": "integer", "minimum": 0},
            },
        },
    },
    "messaging_diagnostics": {
        "description": "Show local paths, policy files, and transport diagnostics for the messaging bridge.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_telegram_service_status": {
        "description": "Show Windows task, health, heartbeat, and process status for the Telegram notifier service.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_telegram_service_start": {
        "description": "Start the Telegram notifier service through the managed Windows task.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_telegram_service_stop": {
        "description": "Stop the managed Telegram notifier service.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_telegram_service_restart": {
        "description": "Restart the managed Telegram notifier service.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "messaging_telegram_service_ensure": {
        "description": "Install the managed Windows task if needed and ensure the Telegram notifier service is running.",
        "inputSchema": {"type": "object", "properties": {}},
    },
}


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


def tool_content(payload: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, ensure_ascii=False),
            }
        ]
    }


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "messaging_status":
        return hub.messaging_status()
    if name == "messaging_send_telegram":
        return hub.send_telegram(str(arguments.get("text") or ""), arguments.get("chat"))
    if name == "messaging_send_telegram_image":
        return hub.send_telegram_image(
            image_path=str(arguments.get("image_path") or ""),
            caption=str(arguments.get("caption") or ""),
            chat_ref=arguments.get("chat"),
        )
    if name == "messaging_read_telegram_inbox":
        return hub.telegram_inbox(clear=False, limit=arguments.get("limit"))
    if name == "messaging_clear_telegram_inbox":
        return hub.telegram_inbox(clear=True)
    if name == "messaging_list_telegram_chats":
        return hub.telegram_chats()
    if name == "messaging_send_whatsapp":
        return hub.send_whatsapp(
            recipient=str(arguments.get("recipient") or ""),
            message=str(arguments.get("message") or ""),
            confirm=bool(arguments.get("confirm")),
        )
    if name == "messaging_search_whatsapp_contacts":
        return hub.search_whatsapp_contacts(
            query=str(arguments.get("query") or ""),
            limit=int(arguments.get("limit") or 20),
        )
    if name == "messaging_read_whatsapp_messages":
        return hub.read_whatsapp_messages(
            query=arguments.get("query"),
            chat_jid=arguments.get("chat_jid"),
            sender_phone_number=arguments.get("sender_phone_number"),
            after=arguments.get("after"),
            before=arguments.get("before"),
            limit=int(arguments.get("limit") or 20),
            page=int(arguments.get("page") or 0),
        )
    if name == "messaging_diagnostics":
        return hub.diagnostics()
    if name == "messaging_telegram_service_status":
        return hub.telegram_service_manager("status")
    if name == "messaging_telegram_service_start":
        return hub.telegram_service_manager("start")
    if name == "messaging_telegram_service_stop":
        return hub.telegram_service_manager("stop")
    if name == "messaging_telegram_service_restart":
        return hub.telegram_service_manager("restart")
    if name == "messaging_telegram_service_ensure":
        return hub.telegram_service_manager("ensure")
    raise ValueError(f"Unknown tool: {name}")


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return rpc_result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "messaging-bridge", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return rpc_result(request_id, {"tools": [tool_schema(name, spec) for name, spec in TOOLS.items()]})
    if method == "tools/call":
        params = request.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if name not in TOOLS:
            return rpc_error(request_id, -32602, f"Unknown tool: {name}")
        try:
            return rpc_result(request_id, tool_content(call_tool(name, arguments)))
        except Exception as exc:
            return rpc_error(request_id, -32000, str(exc))
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
