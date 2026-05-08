#! python3
"""Interactive desktop broker for visible OS actions on the logged-in desktop."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desktop_agent import AgentAction, LocalDesktopExecutor


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent


class BrokerHandler(BaseHTTPRequestHandler):
    executor: LocalDesktopExecutor | None = None
    broker_token: str = ""

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return
        preview = self.executor.preview_status_payload() if self.executor else {"enabled": False, "ready": False}
        self._send_json(200, {"ok": True, "preview": preview})

    def do_POST(self) -> None:
        if self.path not in {"/execute", "/execute_many"}:
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.broker_token and payload.get("token") != self.broker_token:
            self._send_json(403, {"ok": False, "error": "Invalid broker token"})
            return

        try:
            if self.path == "/execute_many":
                actions = [AgentAction(kind=item["kind"], params=item.get("params", {})) for item in payload.get("actions", [])]
                messages = self.executor.execute_many(actions) if self.executor else ["Broker is not ready."]
                self._send_json(200, {"ok": True, "messages": messages})
            else:
                action = AgentAction(kind=payload["kind"], params=payload.get("params", {}))
                message = self.executor.execute(action) if self.executor else "Broker is not ready."
                self._send_json(200, {"ok": True, "message": message})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    cwd = Path(os.getenv("DESKTOP_BROKER_CWD", str(WORKSPACE_ROOT))).expanduser()
    artifacts_dir = ROOT / "artifacts"
    port = int(os.getenv("DESKTOP_BROKER_PORT", "8765"))
    token = os.getenv("DESKTOP_BROKER_TOKEN", "").strip()

    BrokerHandler.executor = LocalDesktopExecutor(cwd=cwd, artifacts_dir=artifacts_dir)
    BrokerHandler.broker_token = token
    BrokerHandler.executor.start_preview_cache()

    server = ThreadingHTTPServer(("127.0.0.1", port), BrokerHandler)
    print(f"Desktop broker listening on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDesktop broker stopped.")
    finally:
        if BrokerHandler.executor:
            BrokerHandler.executor.stop_preview_cache()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
