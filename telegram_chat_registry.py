#! python3
"""Persistent Telegram chat registry for the local Codex gateway."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STORE_DIR = ROOT.parent / "telegram-messages"
CHATS_PATH = STORE_DIR / "chats.json"
ACTIVE_CHAT_PATH = STORE_DIR / "active-chat.json"


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


def list_chats() -> list[dict[str, Any]]:
    chats = _read_json(CHATS_PATH, [])
    if not isinstance(chats, list):
        return []
    return sorted(chats, key=lambda item: item.get("last_received_at") or 0, reverse=True)


def active_chat() -> dict[str, Any] | None:
    data = _read_json(ACTIVE_CHAT_PATH, None)
    return data if isinstance(data, dict) and data.get("chat_id") is not None else None


def active_chat_id(default: int | None = None) -> int | None:
    active = active_chat()
    if active and active.get("chat_id") is not None:
        return int(active["chat_id"])
    return default


def register_message(message: dict[str, Any]) -> dict[str, Any]:
    chat_id = int(message.get("chat_id") or 0)
    sender = message.get("from") or {}
    label = _message_label(message)
    chats = list_chats()
    existing = next((chat for chat in chats if int(chat.get("chat_id") or 0) == chat_id), None)
    now = float(message.get("received_at") or time.time())
    record = {
        "chat_id": chat_id,
        "label": label,
        "alias": (existing or {}).get("alias", ""),
        "username": sender.get("username") or (existing or {}).get("username", ""),
        "first_name": sender.get("first_name") or (existing or {}).get("first_name", ""),
        "last_message_id": message.get("message_id"),
        "last_text": str(message.get("text") or "")[:400],
        "last_received_at": now,
    }
    updated = [chat for chat in chats if int(chat.get("chat_id") or 0) != chat_id]
    updated.append(record)
    _write_json(CHATS_PATH, sorted(updated, key=lambda item: item.get("last_received_at") or 0, reverse=True))
    if not active_chat():
        set_active_chat(chat_id)
    return record


def set_alias(chat_ref: str, alias: str) -> dict[str, Any]:
    chats = list_chats()
    chat = resolve_chat(chat_ref, chats)
    if not chat:
        raise ValueError(f"Unknown chat: {chat_ref}")
    alias = alias.strip()
    if not alias:
        raise ValueError("Alias cannot be empty.")
    for item in chats:
        if int(item.get("chat_id") or 0) == int(chat["chat_id"]):
            item["alias"] = alias
    _write_json(CHATS_PATH, chats)
    active = active_chat()
    if active and int(active["chat_id"]) == int(chat["chat_id"]):
        active["alias"] = alias
        _write_json(ACTIVE_CHAT_PATH, active)
    return next(item for item in chats if int(item.get("chat_id") or 0) == int(chat["chat_id"]))


def set_active_chat(chat_ref: str | int) -> dict[str, Any]:
    chats = list_chats()
    chat = resolve_chat(str(chat_ref), chats)
    if not chat:
        if str(chat_ref).lstrip("-").isdigit():
            chat = {"chat_id": int(chat_ref), "label": str(chat_ref), "alias": ""}
        else:
            raise ValueError(f"Unknown chat: {chat_ref}")
    active = {
        "chat_id": int(chat["chat_id"]),
        "label": chat.get("label") or str(chat["chat_id"]),
        "alias": chat.get("alias", ""),
        "selected_at": time.time(),
    }
    _write_json(ACTIVE_CHAT_PATH, active)
    return active


def resolve_chat(chat_ref: str, chats: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    ref = str(chat_ref).strip()
    if not ref:
        return None
    chats = chats if chats is not None else list_chats()
    if ref.lstrip("-").isdigit():
        wanted = int(ref)
        return next((chat for chat in chats if int(chat.get("chat_id") or 0) == wanted), None)
    lowered = ref.lower()
    return next(
        (
            chat
            for chat in chats
            if str(chat.get("alias") or "").lower() == lowered
            or str(chat.get("username") or "").lower() == lowered
            or str(chat.get("label") or "").lower() == lowered
        ),
        None,
    )


def format_chats() -> str:
    chats = list_chats()
    active = active_chat()
    active_id = int(active["chat_id"]) if active else None
    if not chats:
        return "No Telegram chats have been registered yet."
    lines = ["Known Telegram chats:"]
    for chat in chats:
        marker = "*" if int(chat.get("chat_id") or 0) == active_id else "-"
        alias = f" alias={chat['alias']}" if chat.get("alias") else ""
        username = f" @{chat['username']}" if chat.get("username") else ""
        lines.append(f"{marker} {chat.get('label') or chat.get('chat_id')} ({chat.get('chat_id')}){username}{alias}")
    return "\n".join(lines)


def _message_label(message: dict[str, Any]) -> str:
    sender = message.get("from") or {}
    name = " ".join(part for part in [sender.get("first_name"), sender.get("username")] if part).strip()
    return name or str(message.get("chat_id") or "")
