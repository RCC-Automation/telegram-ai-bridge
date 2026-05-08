#! python3
"""Small JSON-RPC client for the local Codex app-server control channel."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


def start_turn_in_thread(
    codex_path: str,
    thread_id: str,
    prompt: str,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Resume a Codex app-server thread, start a turn, and return final text.

    This talks to the running app-server control socket through
    `codex app-server proxy`. If the desktop app does not expose a healthy
    control socket, the caller should fall back to CLI `exec resume`.
    """
    client = _JsonRpcProcess(
        [codex_path, "app-server", "proxy"],
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    try:
        client.initialize()
        client.request(
            "thread/resume",
            {
                "threadId": thread_id,
                "excludeTurns": True,
                "cwd": str(cwd),
            },
        )
        turn = client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "cwd": str(cwd),
            },
        )
        turn_id = (((turn or {}).get("turn") or {}).get("id")) or ""
        final_text = client.wait_for_turn(thread_id, turn_id)
        return {"ok": True, "thread_id": thread_id, "turn_id": turn_id, "reply": final_text}
    finally:
        client.close()


class _JsonRpcProcess:
    def __init__(
        self,
        args: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.process = subprocess.Popen(
            args,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "telegram-codex-bridge",
                    "title": "Telegram Codex Bridge",
                    "version": "1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [
                        "command/exec/outputDelta",
                        "item/agentMessage/delta",
                        "item/reasoning/textDelta",
                        "item/reasoning/summaryTextDelta",
                    ],
                },
            },
        )
        self.notify("initialized", {})

    def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = uuid.uuid4().hex
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            message = self._read_message(deadline)
            if not message:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            return message.get("result")
        raise TimeoutError(f"Timed out waiting for {method} response.")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params:
            payload["params"] = params
        self._send(payload)

    def wait_for_turn(self, thread_id: str, turn_id: str) -> str:
        deadline = time.time() + self.timeout_seconds
        final_text = ""
        while time.time() < deadline:
            message = self._read_message(deadline)
            if not message:
                continue
            method = message.get("method")
            params = message.get("params") or {}
            if params.get("threadId") != thread_id:
                continue
            if method == "item/completed" and (not turn_id or params.get("turnId") == turn_id):
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    text = str(item.get("text") or "").strip()
                    if text:
                        final_text = text
            if method == "turn/completed" and (not turn_id or params.get("turnId") == turn_id):
                return final_text
        raise TimeoutError("Timed out waiting for turn completion.")

    def close(self) -> None:
        try:
            self.process.kill()
        except Exception:
            pass

    def _send(self, payload: dict[str, Any]) -> None:
        if self.process.poll() is not None:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"Codex app-server proxy exited early: {stderr.strip()}")
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _read_message(self, deadline: float) -> dict[str, Any] | None:
        if self.process.poll() is not None:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"Codex app-server proxy exited: {stderr.strip()}")
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            if time.time() >= deadline:
                return None
            time.sleep(0.1)
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
