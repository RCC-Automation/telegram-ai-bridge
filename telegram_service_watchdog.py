#! python3
"""Keep the local Telegram notifier alive.

This watchdog is intentionally small: it checks the local notifier health
endpoint and starts the notifier through telegram_service_manager when needed.
It does not poll Telegram itself and it does not run Codex directly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from telegram_bridge_heartbeat import start_heartbeat, write_heartbeat


ROOT = Path(__file__).resolve().parent
PID_FILE = ROOT / "telegram_watchdog_pid.txt"
LOG_FILE = ROOT / "telegram_watchdog.log"
NOTIFIER_URL = "http://127.0.0.1:8787"


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def health(timeout: int = 3) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{NOTIFIER_URL}/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        payload.setdefault("ok", True)
        return payload
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def manager(command: str, timeout: int = 90) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "telegram_service_manager.py"), command, "--json"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except Exception:
        payload = {}
    payload.setdefault("ok", completed.returncode == 0)
    payload["returncode"] = completed.returncode
    if completed.stderr.strip():
        payload["stderr"] = completed.stderr.strip()
    return payload


def run(interval_seconds: float) -> int:
    PID_FILE.write_text(str(__import__("os").getpid()), encoding="utf-8")
    stop_heartbeat = start_heartbeat("telegram_watchdog", details={"interval_seconds": interval_seconds})
    log(f"Watchdog started with interval {interval_seconds:g}s.")
    try:
        while True:
            current = health()
            if not current.get("ok"):
                log(f"Notifier unhealthy: {current.get('error')}. Restarting.")
                started = manager("start")
                log(f"Start result ok={started.get('ok')} returncode={started.get('returncode')}.")
            time.sleep(max(5.0, interval_seconds))
    except KeyboardInterrupt:
        log("Watchdog stopped by KeyboardInterrupt.")
        return 0
    finally:
        write_heartbeat("telegram_watchdog", status="stopping")
        stop_heartbeat.set()


def main() -> int:
    parser = argparse.ArgumentParser(description="Keep the Telegram notifier running.")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    args = parser.parse_args()
    return run(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
