#! python3
"""Local message hub for embedded Codex Telegram/WhatsApp tools.

This module talks to existing local transports only. It must not launch Codex,
resume Codex threads, or call the legacy Telegram gateway.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def resolve_bridge_root() -> Path:
    configured = os.getenv("MESSAGING_BRIDGE_ROOT", "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    for parent in [PLUGIN_ROOT, *PLUGIN_ROOT.parents]:
        if (parent / "telegram_notifier_service.py").exists():
            return parent
    default = Path(r"C:\Users\barru\Documents\New project\telegram-ai-bridge")
    if default.exists():
        return default
    return PLUGIN_ROOT.parents[1]


BRIDGE_ROOT = resolve_bridge_root()
WORKSPACE_ROOT = BRIDGE_ROOT.parent
TELEGRAM_MESSAGES_ROOT = WORKSPACE_ROOT / "telegram-messages"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(BRIDGE_ROOT / ".env")


TELEGRAM_TOKEN_PATH = BRIDGE_ROOT / "telegram_notifier_token.txt"
TELEGRAM_NOTIFIER_URL = os.getenv("TELEGRAM_NOTIFIER_URL", "http://127.0.0.1:8787")
WHATSAPP_ROOT = WORKSPACE_ROOT / "whatsapp-mcp"
WHATSAPP_DB_PATH = Path(
    os.getenv(
        "MESSAGING_BRIDGE_WHATSAPP_DB",
        str(WHATSAPP_ROOT / "whatsapp-bridge" / "store" / "messages.db"),
    )
)
WHATSAPP_API_BASE_URL = os.getenv("MESSAGING_BRIDGE_WHATSAPP_API_BASE_URL", "http://127.0.0.1:8080/api")
POLICY_PATH = Path(
    os.getenv(
        "MESSAGING_BRIDGE_POLICY_PATH",
        str(TELEGRAM_MESSAGES_ROOT / "messaging-bridge-policy.json"),
    )
)


if str(BRIDGE_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(BRIDGE_ROOT))


def _token() -> str:
    if not TELEGRAM_TOKEN_PATH.exists():
        raise RuntimeError(f"Missing Telegram local token file: {TELEGRAM_TOKEN_PATH}")
    return TELEGRAM_TOKEN_PATH.read_text(encoding="utf-8").strip()


def _http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "messaging-bridge/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"error": body or str(exc)}
        parsed.setdefault("ok", False)
        parsed.setdefault("http_status", exc.code)
        return parsed


def _tcp_status(host: str, port: int, timeout: float = 1.0) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"reachable": True, "host": host, "port": port}
    except Exception as exc:
        return {"reachable": False, "host": host, "port": port, "error": str(exc)}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _annotate_heartbeat(heartbeat: dict[str, Any] | None, stale_after_seconds: int = 10) -> dict[str, Any] | None:
    if not isinstance(heartbeat, dict):
        return heartbeat
    annotated = dict(heartbeat)
    timestamp = annotated.get("timestamp")
    try:
        age_seconds = max(0.0, time.time() - float(timestamp))
    except Exception:
        age_seconds = None
    annotated["age_seconds"] = age_seconds
    annotated["stale"] = age_seconds is None or age_seconds > stale_after_seconds
    if annotated["stale"] and annotated.get("status") == "running":
        annotated["effective_status"] = "stale"
    else:
        annotated["effective_status"] = annotated.get("status")
    return annotated


def _row_to_dict(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    return {columns[index]: row[index] for index in range(len(columns))}


def _limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(1, min(parsed, maximum))


def normalize_telegram_message(message: dict[str, Any]) -> dict[str, Any]:
    sender = message.get("from") or {}
    media = None
    if message.get("type") == "voice":
        media = {
            "type": "voice",
            "voice": message.get("voice") or {},
            "audio_path": message.get("audio_path"),
            "telegram_file_path": message.get("telegram_file_path"),
        }
    if message.get("type") == "image":
        media = {
            "type": "image",
            "image": message.get("image") or {},
            "image_path": message.get("image_path"),
            "telegram_file_path": message.get("telegram_file_path"),
        }
    return {
        "channel": "telegram",
        "chat_id": message.get("chat_id"),
        "chat_ref": str(message.get("chat_id") or ""),
        "sender": {
            "id": sender.get("id"),
            "username": sender.get("username"),
            "first_name": sender.get("first_name"),
        },
        "text": message.get("text") or "",
        "media": media,
        "timestamp": message.get("date") or message.get("received_at"),
        "raw": message,
    }


def normalize_whatsapp_message(message: dict[str, Any]) -> dict[str, Any]:
    media = None
    if message.get("media_type"):
        media = {
            "type": message.get("media_type"),
            "message_id": message.get("id"),
            "chat_jid": message.get("chat_jid"),
        }
    return {
        "channel": "whatsapp",
        "chat_id": message.get("chat_jid"),
        "chat_ref": message.get("chat_name") or message.get("chat_jid"),
        "sender": {
            "id": message.get("sender"),
            "is_from_me": bool(message.get("is_from_me")),
        },
        "text": message.get("content") or "",
        "media": media,
        "timestamp": str(message.get("timestamp") or ""),
        "raw": message,
    }


def telegram_health() -> dict[str, Any]:
    try:
        return _http_json(f"{TELEGRAM_NOTIFIER_URL.rstrip('/')}/health", timeout=3)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "action": "Start the Telegram notifier with run_telegram_notifier.ps1 or run_telegram_bridge.ps1.",
        }


def telegram_inbox(clear: bool = False, limit: int | None = None) -> dict[str, Any]:
    query = urllib.parse.urlencode({"token": _token(), "clear": "true" if clear else "false"})
    try:
        payload = _http_json(f"{TELEGRAM_NOTIFIER_URL.rstrip('/')}/inbox?{query}", timeout=10)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "action": "Start the Telegram notifier with run_telegram_notifier.ps1 or run_telegram_bridge.ps1.",
            "messages": [],
        }
    messages = payload.get("messages") or []
    normalized = [normalize_telegram_message(message) for message in messages]
    if limit is not None:
        normalized = normalized[-_limit(limit) :]
    return {
        "ok": bool(payload.get("ok")),
        "count": len(normalized),
        "cleared": clear,
        "messages": normalized,
        "raw": payload,
    }


def telegram_chats() -> dict[str, Any]:
    import telegram_chat_registry

    query = urllib.parse.urlencode({"token": _token()})
    try:
        payload = _http_json(f"{TELEGRAM_NOTIFIER_URL.rstrip('/')}/chats?{query}", timeout=10)
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
    if payload.get("ok"):
        return {
            "ok": True,
            "active_chat": telegram_chat_registry.active_chat(),
            "chats": payload.get("chats") or [],
            "text": payload.get("text") or telegram_chat_registry.format_chats(),
        }
    return {
        "ok": False,
        "active_chat": telegram_chat_registry.active_chat(),
        "chats": telegram_chat_registry.list_chats(),
        "text": telegram_chat_registry.format_chats(),
        "notifier_error": payload,
    }


def send_telegram(text: str, chat_ref: str | None = None) -> dict[str, Any]:
    import telegram_chat_registry

    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "Missing Telegram message text."}
    payload: dict[str, Any] = {"token": _token(), "text": text}
    if chat_ref:
        chat = telegram_chat_registry.resolve_chat(str(chat_ref))
        if chat:
            payload["chat_id"] = int(chat["chat_id"])
        elif str(chat_ref).lstrip("-").isdigit():
            payload["chat_id"] = int(chat_ref)
        else:
            return {"ok": False, "error": f"Unknown Telegram chat: {chat_ref}", "chats": telegram_chat_registry.list_chats()}
    try:
        response = _http_json(f"{TELEGRAM_NOTIFIER_URL.rstrip('/')}/send", payload=payload, timeout=10)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "action": "Start the Telegram notifier with run_telegram_notifier.ps1 or run_telegram_bridge.ps1.",
        }
    return {
        "ok": bool(response.get("ok")),
        "channel": "telegram",
        "chat_ref": chat_ref,
        "raw": response,
    }


def send_telegram_image(image_path: str, caption: str = "", chat_ref: str | None = None) -> dict[str, Any]:
    import telegram_chat_registry

    path = Path(str(image_path or "")).expanduser()
    if not path.exists():
        return {"ok": False, "error": f"Image file does not exist: {path}"}
    payload: dict[str, Any] = {"token": _token(), "photo_path": str(path.resolve()), "text": str(caption or "")}
    if chat_ref:
        chat = telegram_chat_registry.resolve_chat(str(chat_ref))
        if chat:
            payload["chat_id"] = int(chat["chat_id"])
        elif str(chat_ref).lstrip("-").isdigit():
            payload["chat_id"] = int(chat_ref)
        else:
            return {"ok": False, "error": f"Unknown Telegram chat: {chat_ref}", "chats": telegram_chat_registry.list_chats()}
    try:
        response = _http_json(f"{TELEGRAM_NOTIFIER_URL.rstrip('/')}/send", payload=payload, timeout=30)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "action": "Start the Telegram notifier with run_telegram_notifier.ps1 or run_telegram_bridge.ps1.",
        }
    return {
        "ok": bool(response.get("ok")),
        "channel": "telegram",
        "chat_ref": chat_ref,
        "image_path": str(path.resolve()),
        "raw": response,
    }


def _whatsapp_connection() -> sqlite3.Connection:
    if not WHATSAPP_DB_PATH.exists():
        raise RuntimeError(f"WhatsApp message database does not exist: {WHATSAPP_DB_PATH}")
    return sqlite3.connect(str(WHATSAPP_DB_PATH))


def _allowed_whatsapp_recipients() -> set[str]:
    values: set[str] = set()
    raw = os.getenv("MESSAGING_BRIDGE_WHATSAPP_ALLOWED_RECIPIENTS", "")
    for part in raw.split(","):
        stripped = part.strip()
        if stripped:
            values.add(stripped.lower())
    policy = _read_json(POLICY_PATH, {})
    if isinstance(policy, dict):
        for item in policy.get("whatsapp_allowed_recipients") or []:
            stripped = str(item).strip()
            if stripped:
                values.add(stripped.lower())
    return values


def search_whatsapp_contacts(query: str, limit: int = 20) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "error": "Missing WhatsApp contact search query."}
    limit = _limit(limit, default=20, maximum=50)
    pattern = f"%{query}%"
    try:
        with _whatsapp_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT jid, name
                FROM chats
                WHERE (LOWER(COALESCE(name, '')) LIKE LOWER(?) OR LOWER(jid) LIKE LOWER(?))
                  AND jid NOT LIKE '%@g.us'
                ORDER BY name, jid
                LIMIT ?
                """,
                (pattern, pattern, limit),
            )
            rows = cursor.fetchall()
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "action": "Start the WhatsApp bridge and scan the QR code if needed, then rerun the search.",
            "db_path": str(WHATSAPP_DB_PATH),
        }
    contacts = [
        {
            "phone_number": str(jid).split("@", 1)[0],
            "name": name,
            "jid": jid,
        }
        for jid, name in rows
    ]
    return {"ok": True, "count": len(contacts), "contacts": contacts}


def _resolve_whatsapp_recipient(recipient: str) -> dict[str, Any]:
    recipient = str(recipient or "").strip()
    if not recipient:
        return {"ok": False, "error": "Missing WhatsApp recipient."}
    lowered = recipient.lower()
    try:
        with _whatsapp_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT jid, name
                FROM chats
                WHERE LOWER(jid) = LOWER(?)
                   OR LOWER(REPLACE(jid, '@s.whatsapp.net', '')) = LOWER(?)
                   OR LOWER(COALESCE(name, '')) = LOWER(?)
                LIMIT 5
                """,
                (recipient, recipient, recipient),
            )
            rows = cursor.fetchall()
    except Exception:
        rows = []
    if len(rows) == 1:
        jid, name = rows[0]
        return {
            "ok": True,
            "recipient": jid,
            "aliases": {lowered, str(jid).lower(), str(jid).split("@", 1)[0].lower(), str(name or "").lower()},
            "contact": {"jid": jid, "name": name, "phone_number": str(jid).split("@", 1)[0]},
        }
    if len(rows) > 1:
        return {"ok": False, "error": f"WhatsApp recipient is ambiguous: {recipient}", "matches": rows}
    aliases = {lowered}
    if "@s.whatsapp.net" in lowered:
        aliases.add(lowered.split("@", 1)[0])
    elif recipient.replace("+", "").isdigit():
        aliases.add(f"{recipient.replace('+', '')}@s.whatsapp.net".lower())
    return {"ok": True, "recipient": recipient, "aliases": aliases, "contact": None}


def read_whatsapp_messages(
    query: str | None = None,
    chat_jid: str | None = None,
    sender_phone_number: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    page: int = 0,
) -> dict[str, Any]:
    limit = _limit(limit, default=20, maximum=100)
    page = max(0, int(page or 0))
    query_parts = [
        """
        SELECT messages.timestamp, messages.sender, chats.name AS chat_name,
               messages.content, messages.is_from_me, chats.jid AS chat_jid,
               messages.id, messages.media_type
        FROM messages
        JOIN chats ON messages.chat_jid = chats.jid
        """
    ]
    where: list[str] = []
    params: list[Any] = []
    if after:
        where.append("messages.timestamp > ?")
        params.append(after)
    if before:
        where.append("messages.timestamp < ?")
        params.append(before)
    if sender_phone_number:
        where.append("messages.sender = ?")
        params.append(sender_phone_number)
    if chat_jid:
        where.append("messages.chat_jid = ?")
        params.append(chat_jid)
    if query:
        where.append("LOWER(messages.content) LIKE LOWER(?)")
        params.append(f"%{query}%")
    if where:
        query_parts.append("WHERE " + " AND ".join(where))
    query_parts.append("ORDER BY messages.timestamp DESC LIMIT ? OFFSET ?")
    params.extend([limit, page * limit])
    try:
        with _whatsapp_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("\n".join(query_parts), params)
            columns = [description[0] for description in cursor.description]
            rows = [_row_to_dict(columns, row) for row in cursor.fetchall()]
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "action": "Start the WhatsApp bridge and wait for the local message database to be created.",
            "db_path": str(WHATSAPP_DB_PATH),
            "messages": [],
        }
    messages = [normalize_whatsapp_message(row) for row in rows]
    return {"ok": True, "count": len(messages), "messages": messages}


def send_whatsapp(recipient: str, message: str, confirm: bool = False) -> dict[str, Any]:
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "error": "Missing WhatsApp message text."}
    resolved = _resolve_whatsapp_recipient(recipient)
    if not resolved.get("ok"):
        return {"ok": False, **resolved}
    allowed = _allowed_whatsapp_recipients()
    aliases = {str(item).lower() for item in resolved.get("aliases") or set()}
    allowed_match = bool(allowed.intersection(aliases))
    if not allowed_match:
        return {
            "ok": False,
            "blocked": True,
            "reason": "Recipient is not allowlisted for WhatsApp sends.",
            "recipient": recipient,
            "resolved_recipient": resolved.get("recipient"),
            "policy_path": str(POLICY_PATH),
            "env_var": "MESSAGING_BRIDGE_WHATSAPP_ALLOWED_RECIPIENTS",
        }
    if not confirm:
        return {
            "ok": False,
            "requires_confirmation": True,
            "reason": "WhatsApp sends require confirm=true.",
            "recipient": resolved.get("recipient"),
            "contact": resolved.get("contact"),
        }
    response = _http_json(
        f"{WHATSAPP_API_BASE_URL.rstrip()}/send",
        payload={"recipient": resolved["recipient"], "message": message},
        timeout=15,
    )
    return {
        "ok": bool(response.get("success") or response.get("ok")),
        "channel": "whatsapp",
        "recipient": resolved["recipient"],
        "contact": resolved.get("contact"),
        "raw": response,
    }


def messaging_status() -> dict[str, Any]:
    telegram = telegram_health()
    whatsapp_api = _tcp_status("127.0.0.1", int(urllib.parse.urlparse(WHATSAPP_API_BASE_URL).port or 8080))
    gateway_heartbeat = _annotate_heartbeat(
        _read_json(TELEGRAM_MESSAGES_ROOT / "bridge-heartbeats" / "telegram_gateway.json", None)
    )
    return {
        "ok": True,
        "mode": "embedded-tools",
        "telegram": {
            "notifier_url": TELEGRAM_NOTIFIER_URL,
            "health": telegram,
            "token_file_exists": TELEGRAM_TOKEN_PATH.exists(),
        },
        "whatsapp": {
            "db_path": str(WHATSAPP_DB_PATH),
            "db_exists": WHATSAPP_DB_PATH.exists(),
            "api_base_url": WHATSAPP_API_BASE_URL,
            "api_tcp": whatsapp_api,
            "allowed_recipients_count": len(_allowed_whatsapp_recipients()),
        },
        "legacy_wake_adapter": {
            "script": str(BRIDGE_ROOT / "telegram_codex_gateway.py"),
            "used_by_this_plugin": False,
            "heartbeat": gateway_heartbeat,
        },
    }


def diagnostics() -> dict[str, Any]:
    return {
        "ok": True,
        "plugin_root": str(PLUGIN_ROOT),
        "bridge_root": str(BRIDGE_ROOT),
        "workspace_root": str(WORKSPACE_ROOT),
        "telegram_notifier_url": TELEGRAM_NOTIFIER_URL,
        "telegram_token_file_exists": TELEGRAM_TOKEN_PATH.exists(),
        "whatsapp_root": str(WHATSAPP_ROOT),
        "whatsapp_db_path": str(WHATSAPP_DB_PATH),
        "whatsapp_db_exists": WHATSAPP_DB_PATH.exists(),
        "whatsapp_api_base_url": WHATSAPP_API_BASE_URL,
        "policy_path": str(POLICY_PATH),
        "policy_exists": POLICY_PATH.exists(),
        "normal_workflow_uses_codex_exec_resume": False,
        "checked_at": time.time(),
    }


def telegram_service_manager(command: str) -> dict[str, Any]:
    allowed = {"status", "start", "stop", "restart", "ensure", "install-task"}
    if command not in allowed:
        return {"ok": False, "error": f"Unsupported Telegram service command: {command}"}
    script = BRIDGE_ROOT / "telegram_service_manager.py"
    if not script.exists():
        return {"ok": False, "error": f"Missing Telegram service manager: {script}"}
    completed = subprocess.run(
        ["py", "-3", str(script), command, "--json"],
        cwd=str(BRIDGE_ROOT),
        text=True,
        capture_output=True,
        timeout=90,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except Exception:
        payload = {}
    payload.setdefault("ok", completed.returncode == 0)
    payload.setdefault("returncode", completed.returncode)
    if completed.stderr.strip():
        payload["stderr"] = completed.stderr.strip()
    if completed.stdout.strip() and not payload:
        payload["stdout"] = completed.stdout.strip()
    return payload
