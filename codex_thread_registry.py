#! python3
"""Persistent registry for routing Telegram requests to Codex threads."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STORE_DIR = ROOT.parent / "telegram-messages"
THREADS_PATH = STORE_DIR / "codex-threads.json"
ACTIVE_THREAD_PATH = STORE_DIR / "active-codex-thread.json"
SESSION_INDEX_PATH = Path.home() / ".codex" / "session_index.jsonl"
SESSION_ROOT = Path.home() / ".codex" / "sessions"


def _ensure_store() -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    _ensure_store()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_codex_threads_from_session_index() -> list[dict[str, Any]]:
    existing = {str(item.get("id")): item for item in _read_json(THREADS_PATH, []) if item.get("id")}
    if SESSION_INDEX_PATH.exists():
        for line in SESSION_INDEX_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            thread_id = str(item.get("id") or "").strip()
            if not thread_id:
                continue
            previous = existing.get(thread_id, {})
            existing[thread_id] = {
                "id": thread_id,
                "name": item.get("thread_name") or previous.get("name") or thread_id,
                "alias": previous.get("alias", ""),
                "updated_at": item.get("updated_at") or previous.get("updated_at", ""),
                "last_synced_at": time.time(),
            }
    for item in _scan_session_files():
        thread_id = str(item.get("id") or "").strip()
        if not thread_id:
            continue
        previous = existing.get(thread_id, {})
        existing[thread_id] = {
            "id": thread_id,
            "name": previous.get("name") or item.get("name") or thread_id,
            "alias": previous.get("alias", ""),
            "updated_at": item.get("updated_at") or previous.get("updated_at", ""),
            "path": item.get("path") or previous.get("path", ""),
            "cwd": item.get("cwd") or previous.get("cwd", ""),
            "last_synced_at": time.time(),
        }
    threads = sorted(existing.values(), key=lambda item: item.get("updated_at") or "", reverse=True)
    _write_json(THREADS_PATH, threads)
    return threads


def _iso_from_epoch(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _scan_session_files(limit: int = 200) -> list[dict[str, Any]]:
    if not SESSION_ROOT.exists():
        return []
    files = sorted(
        SESSION_ROOT.rglob("rollout-*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    items: list[dict[str, Any]] = []
    for path in files:
        item = _thread_from_session_file(path)
        if item:
            items.append(item)
    return items


def _thread_from_session_file(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            first_line = handle.readline().strip()
    except OSError:
        return None
    if not first_line:
        return None
    try:
        first = json.loads(first_line)
    except Exception:
        return None
    payload = first.get("payload") if isinstance(first, dict) else {}
    if not isinstance(payload, dict):
        return None
    thread_id = str(payload.get("id") or "").strip()
    if not thread_id:
        return None
    return {
        "id": thread_id,
        "name": payload.get("thread_name") or path.stem,
        "updated_at": _iso_from_epoch(path.stat().st_mtime),
        "path": str(path),
        "cwd": payload.get("cwd") or "",
    }


def list_codex_threads() -> list[dict[str, Any]]:
    return sync_codex_threads_from_session_index()


def active_codex_thread() -> dict[str, Any] | None:
    data = _read_json(ACTIVE_THREAD_PATH, None)
    return data if isinstance(data, dict) and data.get("id") else None


def active_codex_thread_id(default: str = "") -> str:
    active = active_codex_thread()
    if active and active.get("id"):
        return str(active["id"])
    return default


def set_active_codex_thread(thread_ref: str) -> dict[str, Any]:
    threads = list_codex_threads()
    thread = resolve_codex_thread(thread_ref, threads)
    if not thread:
        raise ValueError(f"Unknown Codex thread: {thread_ref}")
    active = {
        "id": thread["id"],
        "name": thread.get("name") or thread["id"],
        "alias": thread.get("alias", ""),
        "selected_at": time.time(),
    }
    _write_json(ACTIVE_THREAD_PATH, active)
    return active


def set_codex_thread_alias(thread_ref: str, alias: str) -> dict[str, Any]:
    threads = list_codex_threads()
    thread = resolve_codex_thread(thread_ref, threads)
    if not thread:
        raise ValueError(f"Unknown Codex thread: {thread_ref}")
    alias = alias.strip()
    if not alias:
        raise ValueError("Alias cannot be empty.")
    for item in threads:
        if item.get("id") == thread["id"]:
            item["alias"] = alias
    _write_json(THREADS_PATH, threads)
    active = active_codex_thread()
    if active and active.get("id") == thread["id"]:
        active["alias"] = alias
        _write_json(ACTIVE_THREAD_PATH, active)
    return next(item for item in threads if item.get("id") == thread["id"])


def resolve_codex_thread(thread_ref: str, threads: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    ref = str(thread_ref).strip()
    if not ref:
        return None
    threads = threads if threads is not None else list_codex_threads()
    lowered = ref.lower()
    if lowered in {"latest", "newest", "recent"}:
        return threads[0] if threads else None
    if lowered in {"active", "current"}:
        active = active_codex_thread()
        if active:
            return next((thread for thread in threads if thread.get("id") == active.get("id")), active)
        return threads[0] if threads else None
    return next(
        (
            thread
            for thread in threads
            if str(thread.get("id") or "").lower() == lowered
            or str(thread.get("alias") or "").lower() == lowered
            or str(thread.get("name") or "").lower() == lowered
        ),
        None,
    )


def format_codex_threads() -> str:
    threads = list_codex_threads()
    active = active_codex_thread()
    active_id = active.get("id") if active else ""
    if not threads:
        return "No Codex threads found in the local session index."
    lines = ["Known Codex threads:"]
    for thread in threads:
        marker = "*" if thread.get("id") == active_id else "-"
        alias = f" alias={thread['alias']}" if thread.get("alias") else ""
        updated = f" updated={thread['updated_at']}" if thread.get("updated_at") else ""
        lines.append(f"{marker} {thread.get('name') or thread.get('id')} ({thread.get('id')}){alias}{updated}")
    return "\n".join(lines)
