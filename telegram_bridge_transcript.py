#! python3
"""Append-only transcript for Telegram <-> Codex bridge events."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STORE_DIR = ROOT.parent / "telegram-messages"
TRANSCRIPT_PATH = STORE_DIR / "telegram-codex-transcript.jsonl"


def append_transcript(event: str, payload: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "event": event,
        "timestamp": time.time(),
        "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **payload,
    }
    with TRANSCRIPT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

