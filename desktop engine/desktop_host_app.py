#! python3
"""Interactive desktop host app with embedded localhost broker."""

from __future__ import annotations

import base64
import json
import os
import queue
import signal
import threading
import tkinter as tk
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import ttk

from desktop_agent import AgentAction, LocalDesktopExecutor


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent.parent
HOST_LOG_PATH = ROOT / "desktop_host.log"


class HostBrokerHandler(BaseHTTPRequestHandler):
    executor: LocalDesktopExecutor | None = None
    broker_token: str = ""
    event_queue: queue.Queue | None = None

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return
        preview = self.executor.preview_status_payload() if self.executor else {"enabled": False, "ready": False}
        self._send_json(200, {"ok": True, "host": "desktop-host-app", "preview": preview})

    def do_POST(self) -> None:
        if self.path not in {"/execute", "/execute_many"}:
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.broker_token and payload.get("token") != self.broker_token:
            self._log("Rejected request due to invalid broker token.")
            self._send_json(403, {"ok": False, "error": "Invalid broker token"})
            return

        try:
            if self.path == "/execute_many":
                actions = [AgentAction(kind=item["kind"], params=item.get("params", {})) for item in payload.get("actions", [])]
                self._log(f"Executing batch of {len(actions)} actions.")
                messages = self.executor.execute_many(actions) if self.executor else ["Host executor is not ready."]
                self._log("Completed action batch.")
                self._send_json(200, {"ok": True, "messages": messages})
            else:
                action = AgentAction(kind=payload["kind"], params=payload.get("params", {}))
                self._log(f"Executing {action.kind}: {action.params}")
                message = self.executor.execute(action) if self.executor else "Host executor is not ready."
                self._log(f"Completed {action.kind}: {message}")
                self._send_json(200, {"ok": True, "message": message})
        except Exception as exc:
            self._log(f"Failed action: {exc}")
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

    def _log(self, message: str) -> None:
        if self.event_queue:
            self.event_queue.put(message)


class DesktopHostApp:
    def __init__(self) -> None:
        self.cwd = Path(os.getenv("DESKTOP_BROKER_CWD", str(WORKSPACE_ROOT))).expanduser()
        self.artifacts_dir = ROOT / "artifacts"
        self.port = int(os.getenv("DESKTOP_BROKER_PORT", "8765"))
        self.token = os.getenv("DESKTOP_BROKER_TOKEN", "").strip()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self._closing = False

        self.root = tk.Tk()
        self.root.title("Desktop Host")
        self.root.geometry("760x460")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            signal.signal(signal.SIGINT, self._handle_sigint)
        except Exception:
            pass

        self.status_var = tk.StringVar(value="Starting desktop host...")
        self.port_var = tk.StringVar(value=str(self.port))
        self.cwd_var = tk.StringVar(value=str(self.cwd))
        self.preview_status_var = tk.StringVar(value="Preview: starting...")
        self.preview_photo = None
        self._last_preview_stamp: float | None = None

        self._build_ui()
        self.start_server()
        self.root.after(200, self._drain_logs)
        self.root.after(300, self._refresh_preview)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Desktop Host", font=("Segoe UI", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(top, textvariable=self.status_var).pack(anchor=tk.W, pady=(4, 10))

        info = ttk.Frame(frame)
        info.pack(fill=tk.X, pady=(0, 10))
        info.columnconfigure(1, weight=1)

        ttk.Label(info, text="Port:").grid(row=0, column=0, sticky="w")
        ttk.Entry(info, textvariable=self.port_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(info, text="Working Folder:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(info, textvariable=self.cwd_var, state="readonly").grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(actions, text="Copy URL", command=self.copy_url).pack(side=tk.LEFT)
        ttk.Button(actions, text="Clear Log", command=self.clear_log).pack(side=tk.LEFT, padx=(8, 0))

        body = ttk.Panedwindow(frame, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        log_frame = ttk.Frame(body)
        preview_frame = ttk.Frame(body)
        body.add(log_frame, weight=3)
        body.add(preview_frame, weight=2)

        self.log = tk.Text(log_frame, wrap="word", height=18)
        self.log.pack(fill=tk.BOTH, expand=True)
        self.log.insert("end", "Desktop host starting...\n")
        self.log.configure(state="disabled")

        ttk.Label(preview_frame, text="Live Preview", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(preview_frame, textvariable=self.preview_status_var, wraplength=260).pack(anchor=tk.W, pady=(4, 8))
        self.preview_label = ttk.Label(preview_frame, text="Preview unavailable.", anchor="center")
        self.preview_label.pack(fill=tk.BOTH, expand=True)

    def start_server(self) -> None:
        try:
            HostBrokerHandler.executor = LocalDesktopExecutor(cwd=self.cwd, artifacts_dir=self.artifacts_dir)
            HostBrokerHandler.broker_token = self.token
            HostBrokerHandler.event_queue = self.log_queue
            if HostBrokerHandler.executor.start_preview_cache():
                self._log_local("Fast preview cache is enabled.")
            else:
                self._log_local("Fast preview cache is unavailable; using fallback screenshot path.")

            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), HostBrokerHandler)
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            self.status_var.set(f"Listening on http://127.0.0.1:{self.port}")
            self._log_local(f"Listening on http://127.0.0.1:{self.port}")
        except Exception as exc:
            self.status_var.set(f"Desktop Host failed: {exc}")
            self._log_local(f"Startup failed: {exc}")
            raise

    def copy_url(self) -> None:
        url = f"http://127.0.0.1:{self.port}"
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._log_local(f"Copied broker URL: {url}")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _drain_logs(self) -> None:
        if self._closing:
            return
        drained = False
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            drained = True
            timestamp = datetime.now().strftime("%H:%M:%S")
            try:
                self.log.configure(state="normal")
                self.log.insert("end", f"[{timestamp}] {message}\n")
                self.log.see("end")
                self.log.configure(state="disabled")
            except tk.TclError:
                return
        try:
            self.root.after(200 if drained else 400, self._drain_logs)
        except tk.TclError:
            return

    def _refresh_preview(self) -> None:
        if self._closing:
            return
        try:
            executor = HostBrokerHandler.executor
            if not executor:
                self.preview_status_var.set("Preview: host not ready.")
                if not self._closing:
                    self.root.after(500, self._refresh_preview)
                return

            payload = executor.preview_status_payload()
            if not payload.get("enabled"):
                self.preview_status_var.set("Preview: fast preview cache unavailable.")
                self.preview_label.configure(text="Preview unavailable.", image="")
                self.preview_photo = None
                if not self._closing:
                    self.root.after(800, self._refresh_preview)
                return

            if not payload.get("ready"):
                self.preview_status_var.set("Preview: waiting for first cached frame...")
                self.preview_label.configure(text="Waiting for preview...", image="")
                self.preview_photo = None
                if not self._closing:
                    self.root.after(400, self._refresh_preview)
                return

            self.preview_status_var.set(
                f"Preview ({payload.get('source_mode', 'screen')}): {payload.get('title', 'Unknown')} | "
                f"source {payload.get('width', '?')}x{payload.get('height', '?')} | "
                f"cached {payload.get('preview_width', payload.get('width', '?'))}x{payload.get('preview_height', payload.get('height', '?'))} | "
                f"age {payload.get('age_ms', '?')} ms"
            )

            captured_at = float(payload.get("captured_at", 0) or 0)
            if self._last_preview_stamp != captured_at:
                try:
                    image_bytes = executor.latest_preview_png_bytes()
                    if not image_bytes:
                        self.preview_label.configure(text="Preview frame unavailable.", image="")
                        self.preview_photo = None
                    else:
                        image_b64 = base64.b64encode(image_bytes).decode("ascii")
                        self.preview_photo = tk.PhotoImage(data=image_b64)
                        self.preview_label.configure(image=self.preview_photo, text="")
                        self._last_preview_stamp = captured_at
                except Exception as exc:
                    self.preview_label.configure(text=f"Preview error: {exc}", image="")
                    self.preview_photo = None
                    self._log_local(f"Preview image error: {exc}")

            if not self._closing:
                self.root.after(500, self._refresh_preview)
        except tk.TclError:
            return
        except Exception as exc:
            if self._closing:
                return
            self.preview_status_var.set(f"Preview error: {exc}")
            self.preview_label.configure(text=f"Preview error: {exc}", image="")
            self.preview_photo = None
            self._log_local(f"Refresh preview failed: {exc}")
            if not self._closing:
                self.root.after(1000, self._refresh_preview)

    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.status_var.set("Stopping...")
        if HostBrokerHandler.executor:
            HostBrokerHandler.executor.stop_preview_cache()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        try:
            self.root.destroy()
        except tk.TclError:
            return

    def _handle_sigint(self, signum: int, frame: object) -> None:
        self._log_local("Received Ctrl+C. Shutting down Desktop Host.")
        try:
            self.root.after(0, self.on_close)
        except tk.TclError:
            self.on_close()

    def _log_local(self, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_queue.put(message)
        try:
            with HOST_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass

    def run(self) -> int:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.on_close()
        return 0


def main() -> int:
    app = DesktopHostApp()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
