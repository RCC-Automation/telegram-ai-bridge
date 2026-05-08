#! python3
"""Localhost Telegram notifier service.

Runs a tiny HTTP server on 127.0.0.1. Codex can post to this local service, and
the service sends the configured Telegram message to the configured chat.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from telegram_bridge_heartbeat import start_heartbeat, write_heartbeat
from telegram_bridge_transcript import append_transcript
from telegram_chat_registry import active_chat_id, format_chats, list_chats, register_message, set_active_chat
import telegram_voice_transcription as voice_transcription


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TOKEN_PATH = ROOT / "telegram_notifier_token.txt"
INBOX_PATH = ROOT / "telegram_notifier_inbox.json"
VOICE_DIR = ROOT.parent / "telegram-messages" / "voice"
IMAGE_DIR = ROOT.parent / "telegram-messages" / "images"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_value(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def notifier_token() -> str:
    configured = env_value("TELEGRAM_NOTIFIER_LOCAL_TOKEN")
    if configured:
        return configured
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    return token


def default_chat_id() -> int:
    raw = env_value(
        "TELEGRAM_NOTIFIER_CHAT_ID",
        "TELEGRAM_SIDECHANNEL_DEFAULT_CHAT_ID",
        "TELEGRAM_ALLOWED_CHAT_IDS",
    )
    if "," in raw:
        raw = raw.split(",", 1)[0]
    if not raw:
        raise RuntimeError("Missing TELEGRAM_NOTIFIER_CHAT_ID or side-channel/default Telegram chat id.")
    return int(raw)


def fallback_default_chat_id() -> int:
    try:
        return default_chat_id()
    except Exception:
        selected = active_chat_id()
        if selected is not None:
            return int(selected)
        raise


def allowed_chat_ids() -> set[int]:
    raw = env_value(
        "TELEGRAM_NOTIFIER_ALLOWED_CHAT_IDS",
        "TELEGRAM_SIDECHANNEL_CHAT_IDS",
        "TELEGRAM_ALLOWED_CHAT_IDS",
    )
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        for chunk in [text[i : i + 3900] for i in range(0, len(text), 3900)] or [""]:
            result = self._call("sendMessage", {"chat_id": chat_id, "text": chunk})
        return result or {}

    def send_photo(self, chat_id: int, photo_path: Path, caption: str = "") -> dict[str, Any]:
        fields: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption[:1024]
        return self._call_multipart("sendPhoto", fields, "photo", photo_path, _guess_content_type(photo_path))

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict[str, Any]]:
        result = self._call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]}, timeout=timeout + 10)
        return list(result.get("result", []))

    def get_file(self, file_id: str) -> dict[str, Any]:
        result = self._call("getFile", {"file_id": file_id})
        return dict(result.get("result") or {})

    def download_file(self, file_path: str, destination: Path, timeout: int = 60) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.base_url.replace('/bot', '/file/bot')}/{file_path}"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            destination.write_bytes(response.read())

    def _call(self, method: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "telegram-notifier-service/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram HTTP {exc.code}: {body}") from exc
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result}")
        return result

    def _call_multipart(
        self,
        method: str,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
        content_type: str,
        timeout: int = 60,
    ) -> dict[str, Any]:
        if not file_path.exists():
            raise RuntimeError(f"File does not exist: {file_path}")
        boundary = "----telegramBridgeBoundary" + uuid.uuid4().hex
        body_parts: list[bytes] = []
        for name, value in fields.items():
            body_parts.append(_multipart_field(boundary, name, value))
        body_parts.append(_multipart_file(boundary, file_field, file_path.name, content_type, file_path.read_bytes()))
        body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=b"".join(body_parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "telegram-notifier-service/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram HTTP {exc.code}: {body}") from exc
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result}")
        return result


class TelegramInbox:
    def __init__(self, path: Path, allowed_ids: set[int], telegram: TelegramClient | None = None) -> None:
        self.path = path
        self.allowed_ids = allowed_ids
        self.telegram = telegram
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"offset": 0, "messages": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"offset": 0, "messages": []}

    def save(self) -> None:
        with self.lock:
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def offset(self) -> int:
        with self.lock:
            return int(self.data.get("offset") or 0)

    def set_offset(self, offset: int) -> None:
        with self.lock:
            self.data["offset"] = offset

    def add_updates(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        changed = False
        added_messages: list[dict[str, Any]] = []
        with self.lock:
            messages = list(self.data.get("messages") or [])
            existing_update_ids = {int(message.get("update_id") or -1) for message in messages}
            for update in updates:
                self.data["offset"] = max(int(self.data.get("offset") or 0), int(update["update_id"]) + 1)
                update_id = int(update.get("update_id") or -1)
                if update_id in existing_update_ids:
                    continue
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = int(chat.get("id") or 0)
                inbox_message = self._message_to_inbox_entry(update, message, chat_id)
                if not inbox_message:
                    continue
                register_message(inbox_message)
                if self.allowed_ids and chat_id not in self.allowed_ids:
                    continue
                messages.append(inbox_message)
                existing_update_ids.add(update_id)
                added_messages.append(inbox_message)
                record_transcript("telegram_inbound", {"message": inbox_message})
                changed = True
            self.data["messages"] = messages[-200:]
        if changed or updates:
            self.save()
        return added_messages

    def list_messages(self, clear: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            messages = list(self.data.get("messages") or [])
            if clear:
                self.data["messages"] = []
        if clear:
            self.save()
        return messages

    def pending_messages(self, processing_timeout_seconds: int = 1800) -> list[dict[str, Any]]:
        now = time.time()
        changed = False
        pending: list[dict[str, Any]] = []
        with self.lock:
            messages = list(self.data.get("messages") or [])
            for message in messages:
                dispatch = message.setdefault("dispatch", {"status": "pending", "attempts": 0})
                status = str(dispatch.get("status") or "pending")
                if status == "done":
                    continue
                if status == "processing":
                    started_at = float(dispatch.get("started_at") or 0)
                    if now - started_at < processing_timeout_seconds:
                        continue
                    dispatch["status"] = "pending"
                    dispatch["stale_processing_reset_at"] = now
                    changed = True
                pending.append(dict(message))
        if changed:
            self.save()
        return pending

    def mark_dispatch(self, message: dict[str, Any], status: str, **fields: Any) -> None:
        key = _message_key(message)
        now = time.time()
        with self.lock:
            messages = list(self.data.get("messages") or [])
            for stored in messages:
                if _message_key(stored) != key:
                    continue
                previous = stored.get("dispatch") if isinstance(stored.get("dispatch"), dict) else {}
                attempts = int(previous.get("attempts") or 0)
                if status == "processing":
                    attempts += 1
                stored["dispatch"] = {
                    **previous,
                    "status": status,
                    "attempts": attempts,
                    "updated_at": now,
                    **fields,
                }
                if status == "processing" and "started_at" not in fields:
                    stored["dispatch"]["started_at"] = now
                if status in {"done", "error"}:
                    stored["dispatch"]["finished_at"] = now
                break
            self.data["messages"] = messages
        self.save()

    def _message_to_inbox_entry(self, update: dict[str, Any], message: dict[str, Any], chat_id: int) -> dict[str, Any] | None:
        base = {
            "update_id": update.get("update_id"),
            "message_id": message.get("message_id"),
            "chat_id": chat_id,
            "from": {
                "id": (message.get("from") or {}).get("id"),
                "username": (message.get("from") or {}).get("username"),
                "first_name": (message.get("from") or {}).get("first_name"),
            },
            "date": message.get("date"),
            "received_at": time.time(),
        }

        text = (message.get("text") or "").strip()
        if text:
            return {**base, "type": "text", "text": text, "dispatch": {"status": "pending", "attempts": 0}}

        voice = message.get("voice")
        if voice:
            voice_entry = self._voice_to_text_entry(base, voice)
            return {**voice_entry, "dispatch": {"status": "pending", "attempts": 0}}

        photos = message.get("photo") or []
        if photos:
            image_entry = self._photo_to_image_entry(base, photos, message.get("caption"))
            return {**image_entry, "dispatch": {"status": "pending", "attempts": 0}}

        document = message.get("document") or {}
        if str(document.get("mime_type") or "").lower().startswith("image/"):
            image_entry = self._document_to_image_entry(base, document, message.get("caption"))
            return {**image_entry, "dispatch": {"status": "pending", "attempts": 0}}

        return None

    def _voice_to_text_entry(self, base: dict[str, Any], voice: dict[str, Any]) -> dict[str, Any]:
        file_id = str(voice.get("file_id") or "")
        entry = {
            **base,
            "type": "voice",
            "voice": {
                "file_id": file_id,
                "duration": voice.get("duration"),
                "mime_type": voice.get("mime_type"),
                "file_size": voice.get("file_size"),
            },
        }
        if not self.telegram or not file_id:
            return {**entry, "text": "[Voice message received, but no Telegram file client was available for download.]"}

        try:
            file_info = self.telegram.get_file(file_id)
            file_path = str(file_info.get("file_path") or "")
            suffix = Path(file_path).suffix or ".ogg"
            local_path = VOICE_DIR / f"{int(time.time())}_{uuid.uuid4().hex}{suffix}"
            self.telegram.download_file(file_path, local_path)
            transcript = voice_transcription.transcribe_voice_file(local_path, file_id)
            text = transcript or f"[Voice message downloaded to {local_path}, but no transcription backend is configured.]"
            return {**entry, "text": text, "audio_path": str(local_path), "telegram_file_path": file_path}
        except Exception as exc:
            return {**entry, "text": f"[Voice message could not be transcribed: {exc}]"}

    def _photo_to_image_entry(self, base: dict[str, Any], photos: list[dict[str, Any]], caption: Any) -> dict[str, Any]:
        photo = max(photos, key=lambda item: int(item.get("file_size") or item.get("width") or 0))
        return self._image_file_to_entry(
            base=base,
            file_id=str(photo.get("file_id") or ""),
            caption=str(caption or "").strip(),
            source_type="photo",
            metadata={
                "width": photo.get("width"),
                "height": photo.get("height"),
                "file_size": photo.get("file_size"),
            },
        )

    def _document_to_image_entry(self, base: dict[str, Any], document: dict[str, Any], caption: Any) -> dict[str, Any]:
        return self._image_file_to_entry(
            base=base,
            file_id=str(document.get("file_id") or ""),
            caption=str(caption or "").strip(),
            source_type="image_document",
            metadata={
                "file_name": document.get("file_name"),
                "mime_type": document.get("mime_type"),
                "file_size": document.get("file_size"),
            },
        )

    def _image_file_to_entry(
        self,
        base: dict[str, Any],
        file_id: str,
        caption: str,
        source_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        entry = {
            **base,
            "type": "image",
            "text": caption or "[Image message received.]",
            "image": {
                "file_id": file_id,
                "source_type": source_type,
                **metadata,
            },
        }
        if not self.telegram or not file_id:
            return {**entry, "text": caption or "[Image message received, but no Telegram file client was available for download.]"}
        try:
            file_info = self.telegram.get_file(file_id)
            file_path = str(file_info.get("file_path") or "")
            suffix = Path(file_path).suffix or _image_suffix_from_metadata(metadata)
            local_path = IMAGE_DIR / f"{int(time.time())}_{uuid.uuid4().hex}{suffix}"
            self.telegram.download_file(file_path, local_path)
            return {**entry, "image_path": str(local_path), "telegram_file_path": file_path}
        except Exception as exc:
            return {**entry, "text": f"{caption}\n[Image message could not be downloaded: {exc}]".strip()}


class NotifierHandler(BaseHTTPRequestHandler):
    local_token: str = ""
    telegram: TelegramClient
    default_chat_id: int = 0
    allowed_chat_ids: set[int] = set()
    inbox: TelegramInbox

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "telegram-notifier",
                    "default_chat_id": self.default_chat_id,
                    "active_chat_id": active_chat_id(self.default_chat_id),
                    "inbox_count": len(self.inbox.list_messages()),
                },
            )
            return
        if parsed.path == "/inbox":
            query = urllib.parse.parse_qs(parsed.query)
            token = (query.get("token") or [""])[0]
            if token != self.local_token:
                self._send_json(403, {"ok": False, "error": "Invalid local token"})
                return
            clear = (query.get("clear") or ["false"])[0].lower() in {"1", "true", "yes"}
            self._send_json(200, {"ok": True, "messages": self.inbox.list_messages(clear=clear)})
            return
        if parsed.path == "/chats":
            query = urllib.parse.parse_qs(parsed.query)
            token = (query.get("token") or [""])[0]
            if token != self.local_token:
                self._send_json(403, {"ok": False, "error": "Invalid local token"})
                return
            self._send_json(200, {"ok": True, "chats": list_chats(), "text": format_chats()})
            return
        else:
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

    def do_POST(self) -> None:
        if self.path == "/shutdown":
            self._handle_shutdown()
            return
        if self.path != "/send":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if payload.get("token") != self.local_token:
                self._send_json(403, {"ok": False, "error": "Invalid local token"})
                return
            text = str(payload.get("text") or "").strip()
            photo_path = str(payload.get("photo_path") or payload.get("image_path") or "").strip()
            if not text and not photo_path:
                self._send_json(400, {"ok": False, "error": "Missing text or photo_path"})
                return
            chat_id = int(payload.get("chat_id") or active_chat_id(self.default_chat_id) or self.default_chat_id)
            if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
                self._send_json(403, {"ok": False, "error": "Chat id is not allowed"})
                return
            if photo_path:
                result = self.telegram.send_photo(chat_id, Path(photo_path), caption=text)
            else:
                result = self.telegram.send_message(chat_id, text)
            self._send_json(200, {"ok": True, "telegram_result": result.get("result", {})})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _handle_shutdown(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if payload.get("token") != self.local_token:
                self._send_json(403, {"ok": False, "error": "Invalid local token"})
                return
            self._send_json(200, {"ok": True, "status": "shutting_down"})
            threading.Thread(target=self.server.shutdown, name="telegram-notifier-shutdown", daemon=True).start()
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _enabled_env(*names: str) -> bool:
    return env_value(*names, default="").lower() in {"1", "true", "yes", "on"}


def transcript_enabled() -> bool:
    return env_value("TELEGRAM_BRIDGE_TRANSCRIPT_ENABLED", default="true").lower() not in {"0", "false", "no", "off"}


def record_transcript(event: str, payload: dict[str, Any]) -> None:
    if transcript_enabled():
        append_transcript(event, payload)


def _message_key(message: dict[str, Any]) -> str:
    return f"{message.get('chat_id')}:{message.get('message_id')}:{message.get('update_id')}"


class DispatchQueue:
    def __init__(self) -> None:
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.lock = threading.Lock()
        self.keys: set[str] = set()

    def put(self, message: dict[str, Any]) -> None:
        key = _message_key(message)
        with self.lock:
            if key in self.keys:
                return
            self.keys.add(key)
        self.queue.put(message)

    def get(self) -> dict[str, Any]:
        return self.queue.get()

    def done(self, message: dict[str, Any]) -> None:
        with self.lock:
            self.keys.discard(_message_key(message))
        self.queue.task_done()


def start_codex_dispatcher(notifier_url: str, inbox: TelegramInbox) -> DispatchQueue:
    work_queue = DispatchQueue()
    send_ack = not _enabled_env("TELEGRAM_NOTIFIER_INTERACTIVE_NO_ACK")

    def worker() -> None:
        import telegram_codex_gateway

        while True:
            message = work_queue.get()
            try:
                inbox.mark_dispatch(message, "processing")
                telegram_codex_gateway.handle_message(message, notifier_url, send_ack=send_ack)
                inbox.mark_dispatch(message, "done")
            except Exception as exc:
                inbox.mark_dispatch(message, "error", error=str(exc))
                print(f"Telegram interactive Codex dispatch error: {exc}", file=sys.stderr)
                sys.stderr.flush()
            finally:
                work_queue.done(message)

    thread = threading.Thread(target=worker, name="telegram-codex-dispatcher", daemon=True)
    thread.start()
    for message in inbox.pending_messages():
        work_queue.put(message)
    return work_queue


def start_polling(
    client: TelegramClient,
    inbox: TelegramInbox,
    long_poll_timeout_seconds: float,
    codex_dispatch_queue: DispatchQueue | None = None,
) -> threading.Event:
    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            try:
                updates = client.get_updates(inbox.offset(), timeout=max(1, int(long_poll_timeout_seconds)))
                if updates:
                    added_messages = inbox.add_updates(updates)
                    if codex_dispatch_queue is not None:
                        for message in added_messages:
                            codex_dispatch_queue.put(message)
            except Exception as exc:
                print(f"Telegram inbox poll error: {exc}", file=sys.stderr)
                sys.stderr.flush()
                stop.wait(min(10, max(1, long_poll_timeout_seconds)))

    thread = threading.Thread(target=worker, name="telegram-inbox-poller", daemon=True)
    thread.start()
    return stop


def transcribe_voice_file(audio_path: Path, file_id: str = "") -> str:
    command = env_value("TELEGRAM_VOICE_TRANSCRIBE_COMMAND")
    if command:
        return _transcribe_with_command(command, audio_path, file_id)
    if env_value("OPENAI_API_KEY") and env_value("TELEGRAM_VOICE_TRANSCRIBE_MODEL"):
        return _transcribe_with_openai(audio_path)
    return ""


def _transcribe_with_command(command: str, audio_path: Path, file_id: str) -> str:
    formatted = command.format(
        audio_path=str(audio_path),
        audio_dir=str(audio_path.parent),
        file_id=file_id,
    )
    completed = subprocess.run(formatted, shell=True, text=True, capture_output=True, timeout=180)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(details or f"Transcription command failed with exit code {completed.returncode}.")
    return (completed.stdout or "").strip()


def _transcribe_with_openai(audio_path: Path) -> str:
    api_key = env_value("OPENAI_API_KEY")
    model = env_value("TELEGRAM_VOICE_TRANSCRIBE_MODEL")
    boundary = "----telegramVoiceBoundary" + uuid.uuid4().hex
    file_bytes = audio_path.read_bytes()
    fields = [
        _multipart_field(boundary, "model", model),
        _multipart_file(boundary, "file", audio_path.name, "audio/ogg", file_bytes),
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    body = b"".join(fields)
    request = urllib.request.Request(
        env_value("TELEGRAM_VOICE_TRANSCRIBE_URL", default="https://api.openai.com/v1/audio/transcriptions"),
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "telegram-notifier-service/voice-transcribe",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    return str(result.get("text") or "").strip()


def _multipart_field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file(boundary: str, name: str, filename: str, content_type: str, content: bytes) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    return header + content + b"\r\n"


def _guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _image_suffix_from_metadata(metadata: dict[str, Any]) -> str:
    mime = str(metadata.get("mime_type") or "").lower()
    if mime == "image/png":
        return ".png"
    if mime == "image/gif":
        return ".gif"
    if mime == "image/webp":
        return ".webp"
    return ".jpg"


def main() -> int:
    load_dotenv(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run a localhost Telegram notifier service.")
    parser.add_argument("--host", default=env_value("TELEGRAM_NOTIFIER_HOST", default="127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(env_value("TELEGRAM_NOTIFIER_PORT", default="8787")))
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Refusing to bind Telegram notifier to a non-localhost address.")

    bot_token = env_value("TELEGRAM_NOTIFIER_BOT_TOKEN", "TELEGRAM_SIDECHANNEL_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Missing TELEGRAM_NOTIFIER_BOT_TOKEN or Telegram bot token.")

    NotifierHandler.local_token = notifier_token()
    NotifierHandler.telegram = TelegramClient(bot_token)
    NotifierHandler.default_chat_id = fallback_default_chat_id()
    NotifierHandler.allowed_chat_ids = allowed_chat_ids()
    NotifierHandler.inbox = TelegramInbox(INBOX_PATH, NotifierHandler.allowed_chat_ids, telegram=NotifierHandler.telegram)
    long_poll_timeout = float(
        env_value(
            "TELEGRAM_NOTIFIER_LONG_POLL_TIMEOUT_SECONDS",
            "TELEGRAM_NOTIFIER_POLL_INTERVAL_SECONDS",
            default="30",
        )
    )

    server = ThreadingHTTPServer((args.host, args.port), NotifierHandler)
    notifier_url = f"http://{args.host}:{args.port}"
    interactive_codex = _enabled_env("TELEGRAM_NOTIFIER_INTERACTIVE_CODEX", "TELEGRAM_INTERACTIVE_CODEX")
    codex_dispatch_queue = start_codex_dispatcher(notifier_url, NotifierHandler.inbox) if interactive_codex else None
    stop_polling = start_polling(
        NotifierHandler.telegram,
        NotifierHandler.inbox,
        long_poll_timeout,
        codex_dispatch_queue,
    )
    stop_heartbeat = start_heartbeat(
        "telegram_notifier",
        details={"host": args.host, "port": args.port, "long_poll_timeout_seconds": long_poll_timeout},
    )
    print(f"Telegram notifier listening on {notifier_url}")
    print(f"Default chat id: {NotifierHandler.default_chat_id}")
    print(f"Local token file: {TOKEN_PATH}")
    print(f"Inbox file: {INBOX_PATH}")
    print(f"Interactive Codex dispatch: {'enabled' if interactive_codex else 'disabled'}")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTelegram notifier stopped.")
    finally:
        write_heartbeat("telegram_notifier", status="stopping")
        stop_heartbeat.set()
        stop_polling.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
