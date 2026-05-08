#! python3
"""Heartbeat helpers for the local Telegram bridge services."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STORE_DIR = ROOT.parent / "telegram-messages"
HEARTBEAT_DIR = STORE_DIR / "bridge-heartbeats"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        Path(temp_name).replace(path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def write_heartbeat(service: str, status: str = "running", details: dict[str, Any] | None = None) -> None:
    """Write one heartbeat record.

    Heartbeat failures are intentionally ignored because monitoring must not
    crash the service being monitored.
    """
    try:
        service_name = "".join(char for char in service if char.isalnum() or char in {"_", "-"}).strip()
        if not service_name:
            return
        now = time.time()
        record = {
            "service": service,
            "status": status,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "timestamp": now,
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "details": details or {},
        }
        _atomic_write_json(HEARTBEAT_DIR / f"{service_name}.json", record)
    except Exception:
        return


def start_heartbeat(service: str, interval_seconds: float = 5.0, details: dict[str, Any] | None = None) -> threading.Event:
    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            write_heartbeat(service, details=details)
            stop.wait(max(1.0, interval_seconds))

    thread = threading.Thread(target=worker, name=f"{service}-heartbeat", daemon=True)
    thread.start()
    write_heartbeat(service, details=details)
    return stop
