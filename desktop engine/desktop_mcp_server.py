#! python3
"""MCP server exposing the desktop agent and visible desktop host tools over stdio."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from desktop_agent import AgentAction, BrokerDesktopExecutor, DesktopAgent


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent


def mcp_text(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def tool_def(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": read_only},
    }


class DesktopMcpServer:
    def __init__(self) -> None:
        cwd = Path(os.getenv("CODEX_CWD", str(WORKSPACE_ROOT))).expanduser()
        broker_url = os.getenv("DESKTOP_BROKER_URL", "http://127.0.0.1:8765").strip() or None
        broker_token = os.getenv("DESKTOP_BROKER_TOKEN", "").strip() or None
        self.agent = DesktopAgent(cwd=cwd, broker_url=broker_url, broker_token=broker_token)
        self.executor = BrokerDesktopExecutor(broker_url, broker_token) if broker_url else None
        self.instructions = (
            "Use the specific desktop_* tools when possible. "
            "Use desktop_request only for higher-level natural-language tasks. "
            "Visible desktop actions require the Desktop Host app or desktop broker to be running."
        )
        self.tools = self._build_tools()

    def _build_tools(self) -> list[dict[str, Any]]:
        session_key_prop = {
            "session_key": {
                "type": "string",
                "description": "Persistent task/session key. Reuse this to keep task state together.",
                "default": "mcp",
            }
        }
        return [
            tool_def(
                "desktop_request",
                "Handle a natural-language desktop task with persistent state.",
                {
                    "request": {"type": "string", "description": "Natural-language desktop request."},
                    **session_key_prop,
                },
                ["request"],
            ),
            tool_def(
                "desktop_status",
                "Read the current desktop task status for a session.",
                session_key_prop,
                read_only=True,
            ),
            tool_def(
                "desktop_continue",
                "Continue the current desktop task observation cycle for a session.",
                session_key_prop,
            ),
            tool_def(
                "desktop_open_app",
                "Open or restore an application by name or alias.",
                {"app": {"type": "string", "description": "Application name or alias, for example appstudio or notepad."}},
                ["app"],
            ),
            tool_def(
                "desktop_open_url",
                "Open a URL in the default browser.",
                {"url": {"type": "string", "description": "Fully qualified URL."}},
                ["url"],
            ),
            tool_def(
                "desktop_focus_window",
                "Find, restore if minimized, and focus a desktop window by title or process name.",
                {"title": {"type": "string", "description": "Window title or process name fragment."}},
                ["title"],
            ),
            tool_def(
                "desktop_type_text",
                "Type text into the currently focused app.",
                {"text": {"type": "string", "description": "Text to type."}},
                ["text"],
            ),
            tool_def(
                "desktop_send_keys",
                "Send a key sequence to the currently focused app using Windows SendKeys syntax.",
                {"keys": {"type": "string", "description": "Key sequence, for example %{TAB} or ^a."}},
                ["keys"],
            ),
            tool_def(
                "desktop_click",
                "Click at screen coordinates.",
                {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                ["x", "y"],
            ),
            tool_def(
                "desktop_double_click",
                "Double-click at screen coordinates.",
                {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                ["x", "y"],
            ),
            tool_def(
                "desktop_right_click",
                "Right-click at screen coordinates.",
                {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                ["x", "y"],
            ),
            tool_def(
                "desktop_move_mouse",
                "Move the mouse pointer to screen coordinates.",
                {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                ["x", "y"],
            ),
            tool_def(
                "desktop_drag_mouse",
                "Drag the mouse from one point to another.",
                {
                    "start_x": {"type": "integer"},
                    "start_y": {"type": "integer"},
                    "end_x": {"type": "integer"},
                    "end_y": {"type": "integer"},
                    "duration_ms": {"type": "integer", "minimum": 100, "description": "Optional drag duration in milliseconds."},
                },
                ["start_x", "start_y", "end_x", "end_y"],
            ),
            tool_def(
                "desktop_take_screenshot",
                "Take a screenshot of the current desktop.",
                {},
                read_only=True,
            ),
            tool_def(
                "desktop_take_window_screenshot",
                "Take a screenshot of the active window or a matching window title.",
                {"title": {"type": "string", "description": "Optional window title or process name fragment."}},
                read_only=True,
            ),
            tool_def(
                "desktop_preview_status",
                "Return the current status of the fast active-window preview cache.",
                {},
                read_only=True,
            ),
            tool_def(
                "desktop_refresh_preview",
                "Refresh the active-window preview cache immediately.",
                {},
            ),
            tool_def(
                "desktop_list_windows",
                "List visible desktop windows. Minimized windows may be included and labeled.",
                {},
                read_only=True,
            ),
            tool_def(
                "desktop_active_window",
                "Return the currently active desktop window.",
                {},
                read_only=True,
            ),
            tool_def(
                "desktop_window_bounds",
                "Return the bounds of a matching window.",
                {"title": {"type": "string", "description": "Window title or process name fragment."}},
                ["title"],
                read_only=True,
            ),
            tool_def(
                "desktop_cursor_position",
                "Return the current mouse cursor position.",
                {},
                read_only=True,
            ),
            tool_def(
                "desktop_screen_size",
                "Return the primary screen size.",
                {},
                read_only=True,
            ),
            tool_def(
                "desktop_execute",
                "Compatibility escape hatch for direct structured desktop actions through the broker.",
                {
                    "kind": {"type": "string", "description": "Low-level action kind."},
                    "params": {"type": "object", "description": "Parameters for the low-level action."},
                },
                ["kind"],
            ),
        ]

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "desktop-agent-mcp", "version": "0.2.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": self.instructions,
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": self.tools}}

        if method == "tools/call":
            params = message.get("params", {}) or {}
            name = params.get("name")
            arguments = params.get("arguments", {}) or {}
            try:
                payload = self._call_tool(name, arguments)
            except Exception as exc:
                payload = {"content": [mcp_text(str(exc))], "isError": True}
            return {"jsonrpc": "2.0", "id": msg_id, "result": payload}

        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return None

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "desktop_request":
            session_key = arguments.get("session_key", "mcp")
            result = self.agent.handle(arguments["request"], session_key=session_key)
            return {"content": [mcp_text(result.message)], "isError": False}

        if name == "desktop_status":
            session_key = arguments.get("session_key", "mcp")
            result = self.agent.handle("status", session_key=session_key)
            return {"content": [mcp_text(result.message)], "isError": False}

        if name == "desktop_continue":
            session_key = arguments.get("session_key", "mcp")
            result = self.agent.handle("continue", session_key=session_key)
            return {"content": [mcp_text(result.message)], "isError": False}

        if name == "desktop_execute":
            return self._execute_action(arguments["kind"], arguments.get("params", {}))

        direct_map: dict[str, tuple[str, list[str]]] = {
            "desktop_open_app": ("open_app", ["app"]),
            "desktop_open_url": ("open_url", ["url"]),
            "desktop_focus_window": ("focus_window", ["title"]),
            "desktop_type_text": ("type_text", ["text"]),
            "desktop_send_keys": ("send_keys", ["keys"]),
            "desktop_click": ("click", ["x", "y"]),
            "desktop_double_click": ("double_click", ["x", "y"]),
            "desktop_right_click": ("right_click", ["x", "y"]),
            "desktop_move_mouse": ("move_mouse", ["x", "y"]),
            "desktop_drag_mouse": ("drag_mouse", ["start_x", "start_y", "end_x", "end_y"]),
            "desktop_take_screenshot": ("screenshot", []),
            "desktop_take_window_screenshot": ("window_screenshot", []),
            "desktop_preview_status": ("preview_status", []),
            "desktop_refresh_preview": ("refresh_preview", []),
            "desktop_list_windows": ("list_windows", []),
            "desktop_active_window": ("active_window", []),
            "desktop_window_bounds": ("window_bounds", ["title"]),
            "desktop_cursor_position": ("cursor_position", []),
            "desktop_screen_size": ("screen_size", []),
        }
        if name in direct_map:
            kind, required = direct_map[name]
            for field_name in required:
                if field_name not in arguments:
                    raise RuntimeError(f"Missing required argument '{field_name}' for tool {name}.")
            return self._execute_action(kind, arguments)

        raise RuntimeError(f"Unknown tool: {name}")

    def _execute_action(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.executor:
            raise RuntimeError("No desktop broker URL is configured.")
        action = AgentAction(kind, params)
        message_text = self.executor.execute(action)
        return {"content": [mcp_text(message_text)], "isError": False}


def main() -> int:
    server = DesktopMcpServer()
    for raw in sys.stdin:
        raw = raw.lstrip("\ufeff").strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
            response = server.handle(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(
                json.dumps({"jsonrpc": "2.0", "error": {"code": -32000, "message": str(exc)}}) + "\n"
            )
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
