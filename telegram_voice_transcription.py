#! python3
"""Voice transcription helpers for the localhost Telegram gateway."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
LOCAL_TRANSCRIBE_SCRIPT = ROOT / "telegram_voice_transcribe_local.py"


def load_dotenv(path: Path = ENV_PATH) -> None:
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


def voice_transcription_status() -> dict[str, object]:
    configured_command = env_value("TELEGRAM_VOICE_TRANSCRIBE_COMMAND")
    local_command = _default_local_transcribe_command()
    command = configured_command or local_command
    openai_key = bool(env_value("OPENAI_API_KEY"))
    openai_model = env_value("TELEGRAM_VOICE_TRANSCRIBE_MODEL")
    return {
        "ok": bool(command or (openai_key and openai_model)),
        "command_configured": bool(configured_command),
        "local_backend_available": bool(local_command),
        "openai_key_present": openai_key,
        "openai_model": openai_model,
        "active_backend": "command" if configured_command else "local" if local_command else "openai" if openai_key and openai_model else "",
        "message": _status_message(configured_command, local_command, openai_key, openai_model),
    }


def format_voice_transcription_status() -> str:
    status = voice_transcription_status()
    lines = ["Voice transcription status:"]
    lines.append(f"- ready: {'yes' if status['ok'] else 'no'}")
    lines.append(f"- backend: {status['active_backend'] or 'none'}")
    lines.append(f"- local command configured: {'yes' if status['command_configured'] else 'no'}")
    lines.append(f"- local backend available: {'yes' if status['local_backend_available'] else 'no'}")
    lines.append(f"- OpenAI key present: {'yes' if status['openai_key_present'] else 'no'}")
    if status["openai_model"]:
        lines.append(f"- OpenAI model: {status['openai_model']}")
    lines.append(f"- note: {status['message']}")
    return "\n".join(lines)


def transcribe_voice_file(audio_path: Path, file_id: str = "") -> str:
    command = env_value("TELEGRAM_VOICE_TRANSCRIBE_COMMAND") or _default_local_transcribe_command()
    if command:
        return _transcribe_with_command(command, audio_path, file_id)
    if env_value("OPENAI_API_KEY") and env_value("TELEGRAM_VOICE_TRANSCRIBE_MODEL"):
        return _transcribe_with_openai(audio_path)
    return ""


def _status_message(configured_command: str, local_command: str, openai_key: bool, openai_model: str) -> str:
    if configured_command:
        return "Using TELEGRAM_VOICE_TRANSCRIBE_COMMAND."
    if local_command:
        return "Using local project Whisper backend."
    if openai_key and openai_model:
        return "Using OpenAI transcription backend."
    if openai_key and not openai_model:
        return "OPENAI_API_KEY is present, but TELEGRAM_VOICE_TRANSCRIBE_MODEL is missing."
    return "No transcription backend configured."


def _default_local_transcribe_command() -> str:
    if not LOCAL_TRANSCRIBE_SCRIPT.exists() or not _local_whisper_backend_available():
        return ""
    return f'"{sys.executable}" "{LOCAL_TRANSCRIBE_SCRIPT}" "{{audio_path}}"'


def _local_whisper_backend_available() -> bool:
    vendor_py = ROOT / ".vendor_py"
    if (vendor_py / "faster_whisper").exists() or (vendor_py / "whisper").exists():
        return True
    return bool(importlib.util.find_spec("faster_whisper") or importlib.util.find_spec("whisper"))


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
    body = b"".join(
        [
            _multipart_field(boundary, "model", model),
            _multipart_file(boundary, "file", audio_path.name, "audio/ogg", audio_path.read_bytes()),
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
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
