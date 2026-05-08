#! python3
"""Local smoke tests for Telegram bridge queue and routing primitives."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from codex_thread_registry import resolve_codex_thread
from telegram_codex_gateway import acquire_lock, release_lock
from telegram_notifier_service import TelegramInbox


def test_inbox_dispatch_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        inbox = TelegramInbox(Path(tmp) / "inbox.json", allowed_ids={123})
        updates = [
            {
                "update_id": 10,
                "message": {
                    "message_id": 20,
                    "date": 1,
                    "chat": {"id": 123},
                    "from": {"id": 123, "first_name": "Test", "username": "tester"},
                    "text": "hello",
                },
            }
        ]
        added = inbox.add_updates(updates)
        assert len(added) == 1
        assert len(inbox.pending_messages()) == 1
        inbox.mark_dispatch(added[0], "processing")
        assert len(inbox.pending_messages(processing_timeout_seconds=9999)) == 0
        inbox.mark_dispatch(added[0], "done")
        assert len(inbox.pending_messages()) == 0


def test_stale_processing_resets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        inbox = TelegramInbox(Path(tmp) / "inbox.json", allowed_ids={123})
        added = inbox.add_updates(
            [
                {
                    "update_id": 11,
                    "message": {
                        "message_id": 21,
                        "date": 1,
                        "chat": {"id": 123},
                        "from": {"id": 123, "first_name": "Test"},
                        "text": "stale",
                    },
                }
            ]
        )
        inbox.mark_dispatch(added[0], "processing", started_at=time.time() - 1000)
        assert len(inbox.pending_messages(processing_timeout_seconds=1)) == 1


def test_thread_latest_resolution() -> None:
    threads = [
        {"id": "new", "name": "Newest", "updated_at": "2026-05-07T10:00:00Z"},
        {"id": "old", "name": "Oldest", "updated_at": "2026-05-06T10:00:00Z"},
    ]
    assert resolve_codex_thread("latest", threads)["id"] == "new"
    assert resolve_codex_thread("Newest", threads)["id"] == "new"


def test_lock_lifecycle() -> None:
    assert acquire_lock(max_age_seconds=30)
    assert not acquire_lock(max_age_seconds=30)
    release_lock()
    assert acquire_lock(max_age_seconds=30)
    release_lock()


def main() -> int:
    os.environ["TELEGRAM_BRIDGE_TRANSCRIPT_ENABLED"] = "false"
    test_inbox_dispatch_state()
    test_stale_processing_resets()
    test_thread_latest_resolution()
    test_lock_lifecycle()
    print("telegram bridge smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
