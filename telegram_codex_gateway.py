#! python3
"""Instant-ish Telegram gateway for the current Codex thread.

This process watches the localhost Telegram notifier inbox and resumes the
configured Codex thread as soon as an allowed Telegram message arrives.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from codex_app_server_bridge import start_turn_in_thread
from codex_thread_registry import (
    active_codex_thread,
    active_codex_thread_id,
    format_codex_threads,
    set_active_codex_thread,
    set_codex_thread_alias,
)
from telegram_bridge_heartbeat import write_heartbeat
from telegram_bridge_transcript import append_transcript
from telegram_chat_registry import active_chat, format_chats, register_message, set_active_chat, set_alias
from telegram_voice_transcription import format_voice_transcription_status


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TOKEN_PATH = ROOT / "telegram_notifier_token.txt"
GATEWAY_LOG_PATH = ROOT / "telegram_codex_gateway.log"
GATEWAY_REPLY_PATH = ROOT / "last_gateway_codex_reply.txt"
DEFAULT_GATEWAY_CODEX_HOME = ROOT / ".gateway-codex-home"
GATEWAY_LOCK_PATH = ROOT / "telegram_codex_gateway.lock"


def log_line(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    GATEWAY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GATEWAY_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def env_value(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def candidate_codex_paths(configured_path: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        normalized = os.path.expandvars(value.strip().strip('"').strip("'"))
        if normalized and normalized not in seen:
            candidates.append(normalized)
            seen.add(normalized)

    add(configured_path)
    add(shutil.which("codex"))
    add(shutil.which("codex.exe"))
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if local_app_data:
        add(os.path.join(local_app_data, "OpenAI", "Codex", "bin", "codex.exe"))
        add(
            os.path.join(
                local_app_data,
                "Packages",
                "OpenAI.Codex_2p2nqsd0c76g0",
                "LocalCache",
                "Local",
                "OpenAI",
                "Codex",
                "bin",
                "codex.exe",
            )
        )
    return candidates


def resolve_codex_path(configured_path: str) -> str | None:
    for candidate in candidate_codex_paths(configured_path):
        if "WindowsApps" in candidate:
            continue
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
        else:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    return None


def copy_path_if_present(source: Path, target: Path) -> None:
    if not source.exists():
        return
    try:
        if source.resolve() == target.resolve():
            return
    except OSError:
        pass
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def write_gateway_codex_config(codex_home: Path) -> None:
    """Use a minimal config so the gateway avoids machine-wide Windows sandbox state."""
    config_path = codex_home / "config.toml"
    if config_path.exists():
        return
    config_path.write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                'model_reasoning_effort = "medium"',
                'sandbox_mode = "workspace-write"',
                'approval_policy = "on-failure"',
                "",
                "[windows]",
                'sandbox = "unelevated"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def prepare_gateway_codex_home(codex_home_value: str) -> Path:
    codex_home = Path(os.path.expandvars(codex_home_value)).expanduser()
    codex_home.mkdir(parents=True, exist_ok=True)

    source_home = Path(os.getenv("USERPROFILE", str(Path.home()))) / ".codex"
    try:
        if codex_home.resolve() == source_home.resolve():
            return codex_home
    except OSError:
        pass
    copy_path_if_present(source_home / "auth.json", codex_home / "auth.json")
    copy_path_if_present(source_home / "session_index.jsonl", codex_home / "session_index.jsonl")
    copy_path_if_present(source_home / "sessions", codex_home / "sessions")
    copy_path_if_present(source_home / "archived_sessions", codex_home / "archived_sessions")
    write_gateway_codex_config(codex_home)
    return codex_home


def local_token() -> str:
    if not TOKEN_PATH.exists():
        raise RuntimeError(f"Missing local Telegram notifier token file: {TOKEN_PATH}")
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def notifier_get(path: str, notifier_url: str, timeout: int = 10) -> dict[str, Any]:
    token = local_token()
    separator = "&" if "?" in path else "?"
    url = f"{notifier_url.rstrip('/')}{path}{separator}token={token}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def notifier_send(text: str, notifier_url: str, timeout: int = 10, chat_id: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"token": local_token(), "text": text}
    if chat_id is not None:
        payload["chat_id"] = int(chat_id)
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        notifier_url.rstrip("/") + "/send",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "telegram-codex-gateway/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    append_transcript("telegram_outbound", {"chat_id": chat_id, "text": text, "result": result})
    return result


def lock_max_age_seconds() -> int:
    raw = env_value("TELEGRAM_GATEWAY_LOCK_MAX_AGE_SECONDS", default="900")
    try:
        return max(30, int(raw))
    except ValueError:
        return 900


def lock_owner_pid() -> int | None:
    if not GATEWAY_LOCK_PATH.exists():
        return None
    try:
        raw = GATEWAY_LOCK_PATH.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:
        return None


def process_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                capture_output=True,
                timeout=5,
            )
        except Exception:
            return True
        output = (completed.stdout or "").strip()
        if completed.returncode != 0 or not output:
            return True
        return f'"{pid}"' in output
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(max_age_seconds: int | None = None) -> bool:
    max_age_seconds = max_age_seconds if max_age_seconds is not None else lock_max_age_seconds()
    if GATEWAY_LOCK_PATH.exists():
        age = time.time() - GATEWAY_LOCK_PATH.stat().st_mtime
        owner_pid = lock_owner_pid()
        owner_is_alive = process_exists(owner_pid)
        if age < max_age_seconds and owner_is_alive:
            return False
        reason = "stale by age" if age >= max_age_seconds else f"dead owner pid {owner_pid}"
        try:
            GATEWAY_LOCK_PATH.unlink()
            log_line(f"Removed gateway lock ({reason}).")
        except OSError:
            return False
    try:
        GATEWAY_LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def wait_for_lock(timeout_seconds: int | None = None) -> bool:
    raw_timeout = env_value("TELEGRAM_GATEWAY_LOCK_WAIT_SECONDS", default="1800")
    if timeout_seconds is None:
        try:
            timeout_seconds = max(5, int(raw_timeout))
        except ValueError:
            timeout_seconds = 1800
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if acquire_lock():
            return True
        time.sleep(1)
    return False


def release_lock() -> None:
    try:
        GATEWAY_LOCK_PATH.unlink()
    except OSError:
        pass


def delivery_mode() -> str:
    mode = env_value("TELEGRAM_GATEWAY_DELIVERY_MODE", default="active-app").lower().strip()
    if mode in {"active", "desktop", "app", "active-chat", "active_app"}:
        return "active-app"
    if mode in {"resume", "thread", "cli", "exec"}:
        return "resume"
    return "active-app"


def build_prompt(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "")
    sender = message.get("from") or {}
    message_type = message.get("type") or "text"
    audio_path = message.get("audio_path") or ""
    image_path = message.get("image_path") or ""
    media_note = f"Telegram message type: {message_type}\n"
    if audio_path:
        media_note += f"Voice audio path: {audio_path}\n"
    if image_path:
        media_note += f"Image file path: {image_path}\n"
        media_note += "If the user asks about the image, inspect that local file before answering.\n"
    return (
        "This request arrived via the local Telegram Codex gateway. "
        "Continue the existing Codex thread naturally. Complete the requested task when safe and feasible. "
        "Do not manually send the normal final answer to Telegram; the gateway will send your final response automatically. "
        "Use the localhost Telegram notifier helper only for extra proactive Telegram messages that are separate from the final answer. "
        "When finished, give a concise final answer suitable for both Telegram and this thread.\n\n"
        f"Telegram message id: {message.get('message_id')}\n"
        f"Telegram chat id: {message.get('chat_id')}\n"
        f"Sender: {sender.get('first_name') or ''} {sender.get('username') or ''}\n"
        f"{media_note}"
        f"User request:\n{text}"
    )


def build_active_app_prompt(message: dict[str, Any], notifier_url: str) -> str:
    text = str(message.get("text") or "")
    sender = message.get("from") or {}
    message_type = message.get("type") or "text"
    audio_path = message.get("audio_path") or ""
    image_path = message.get("image_path") or ""
    media_note = f"Telegram message type: {message_type}\n"
    if audio_path:
        media_note += f"Voice audio path: {audio_path}\n"
    if image_path:
        media_note += f"Image file path: {image_path}\n"
        media_note += "If the user asks about the image, inspect that local file before answering.\n"
    chat_id = int(message.get("chat_id") or 0)
    return (
        "This request arrived from Telegram and was routed into this Codex thread through the local bridge.\n"
        "Answer it naturally here so the conversation is recorded in the Codex thread.\n\n"
        "Required behavior:\n"
        "1. Produce the answer text only; the bridge will send your final answer back to Telegram.\n"
        "2. Keep the answer concise enough for Telegram unless the user explicitly asks for detail.\n"
        "3. Do not start the legacy Telegram gateway and do not launch a second polling bridge.\n\n"
        f"Telegram message id: {message.get('message_id')}\n"
        f"Telegram chat id: {chat_id}\n"
        f"Telegram sender: {sender.get('first_name') or ''} {sender.get('username') or ''}\n"
        f"{media_note}"
        f"User request:\n{text}"
    )


def handle_gateway_command(message: dict[str, Any]) -> str | None:
    text = str(message.get("text") or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    command = parts[0].split("@", 1)[0].lower()

    if command == "/chats":
        return format_chats()

    if command in {"/threads", "/codex-threads"}:
        return format_codex_threads()

    if command in {"/voice", "/voice-status"}:
        return format_voice_transcription_status()

    if command == "/whoami":
        current = active_chat()
        active_text = (
            f"Active chat: {current.get('label') or current.get('chat_id')} ({current.get('chat_id')})"
            if current
            else "No active chat selected."
        )
        current_thread = active_codex_thread()
        thread_text = (
            f"Active Codex thread: {current_thread.get('alias') or current_thread.get('name') or current_thread.get('id')} ({current_thread.get('id')})"
            if current_thread
            else f"Active Codex thread: default from .env ({env_value('TELEGRAM_GATEWAY_CODEX_THREAD_ID', 'CODEX_THREAD_ID')})"
        )
        return f"This Telegram chat: {message.get('chat_id')}\n{active_text}\n{thread_text}"

    if command == "/use":
        if len(parts) < 2:
            return "Usage: /use <alias-or-chat-id>"
        try:
            selected = set_active_chat(parts[1])
        except Exception as exc:
            return str(exc)
        return f"Active Telegram chat set to {selected.get('label') or selected.get('chat_id')} ({selected.get('chat_id')})."

    if command in {"/use-thread", "/use-codex"}:
        if len(parts) < 2:
            return "Usage: /use-thread <alias-or-thread-id-or-thread-name>"
        try:
            selected = set_active_codex_thread(" ".join(parts[1:]))
        except Exception as exc:
            return str(exc)
        return f"Active Codex thread set to {selected.get('alias') or selected.get('name') or selected.get('id')} ({selected.get('id')})."

    if command == "/alias":
        if len(parts) < 3:
            return "Usage: /alias <chat-id-or-current> <name>"
        chat_ref = str(message.get("chat_id")) if parts[1].lower() in {"current", "this"} else parts[1]
        alias = " ".join(parts[2:])
        try:
            updated = set_alias(chat_ref, alias)
        except Exception as exc:
            return str(exc)
        return f"Alias set: {updated.get('chat_id')} = {updated.get('alias')}"

    if command == "/alias-thread":
        if len(parts) < 3:
            return "Usage: /alias-thread <thread-id-or-name> <alias>"
        thread_ref = parts[1]
        alias = " ".join(parts[2:])
        try:
            updated = set_codex_thread_alias(thread_ref, alias)
        except Exception as exc:
            return str(exc)
        return f"Codex thread alias set: {updated.get('id')} = {updated.get('alias')}"

    return None


def run_active_app_for_message(message: dict[str, Any], notifier_url: str) -> dict[str, Any]:
    configured_codex_path = env_value("CODEX_PATH", default="codex")
    codex_path = resolve_codex_path(configured_codex_path)
    if not codex_path:
        attempted = "\n".join(candidate_codex_paths(configured_codex_path))
        return {"ok": False, "error": "Codex executable was not found.", "attempted": attempted}

    default_thread_id = env_value("TELEGRAM_GATEWAY_CODEX_THREAD_ID", "CODEX_THREAD_ID")
    thread_id = active_codex_thread_id(default_thread_id)
    if not thread_id:
        return {"ok": False, "error": "Missing TELEGRAM_GATEWAY_CODEX_THREAD_ID or CODEX_THREAD_ID."}
    cwd = Path(env_value("TELEGRAM_GATEWAY_CODEX_CWD", "CODEX_CWD", default=str(ROOT.parent))).expanduser()
    timeout_seconds = int(env_value("TELEGRAM_GATEWAY_APP_SERVER_TIMEOUT_SECONDS", "TELEGRAM_GATEWAY_CODEX_TIMEOUT_SECONDS", default="900"))
    codex_home_value = env_value("TELEGRAM_GATEWAY_CODEX_HOME", "CODEX_HOME", default=str(Path.home() / ".codex"))
    codex_env = os.environ.copy()
    codex_env["CODEX_HOME"] = str(Path(os.path.expandvars(codex_home_value)).expanduser())
    prompt = build_active_app_prompt(message, notifier_url)

    log_line(
        "Starting Codex app-server turn for Telegram message "
        f"{message.get('message_id')} from chat {message.get('chat_id')} in thread {thread_id} "
        f"using codex_path={codex_path!r} cwd={str(cwd)!r} CODEX_HOME={codex_env.get('CODEX_HOME')!r}"
    )
    try:
        payload = start_turn_in_thread(codex_path, thread_id, prompt, cwd, codex_env, timeout_seconds=timeout_seconds)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "codex_path": codex_path, "cwd": str(cwd), "thread_id": thread_id}
    payload.update({"codex_path": codex_path, "cwd": str(cwd)})
    append_transcript("codex_active_app_post", {"message": message, "result": payload})
    return payload


def run_codex_for_message(message: dict[str, Any]) -> str:
    configured_codex_path = env_value("CODEX_PATH", default="codex")
    codex_path = resolve_codex_path(configured_codex_path)
    if not codex_path:
        attempted = "\n".join(candidate_codex_paths(configured_codex_path))
        return "Codex executable was not found.\n\nTried:\n" + attempted

    default_thread_id = env_value("TELEGRAM_GATEWAY_CODEX_THREAD_ID", "CODEX_THREAD_ID")
    thread_id = active_codex_thread_id(default_thread_id)
    if not thread_id:
        return "Missing TELEGRAM_GATEWAY_CODEX_THREAD_ID or CODEX_THREAD_ID."
    cwd = Path(env_value("TELEGRAM_GATEWAY_CODEX_CWD", "CODEX_CWD", default=str(ROOT.parent))).expanduser()
    timeout_seconds = int(env_value("TELEGRAM_GATEWAY_CODEX_TIMEOUT_SECONDS", "CODEX_TASK_TIMEOUT_SECONDS", default="900"))
    full_auto = env_value("TELEGRAM_GATEWAY_CODEX_FULL_AUTO", "CODEX_FULL_AUTO", default="true").lower() in {"1", "true", "yes", "on"}
    codex_home_value = env_value("TELEGRAM_GATEWAY_CODEX_HOME", "CODEX_HOME", default=str(DEFAULT_GATEWAY_CODEX_HOME))
    codex_env = os.environ.copy()
    try:
        gateway_codex_home = prepare_gateway_codex_home(codex_home_value)
        codex_env["CODEX_HOME"] = str(gateway_codex_home)
    except Exception as exc:
        return f"Codex gateway could not prepare its local CODEX_HOME.\n\n{exc}"

    args = [codex_path, "exec", "resume"]
    if full_auto:
        args.append("--full-auto")
    args.extend(
        [
            "--skip-git-repo-check",
            "--output-last-message",
            str(GATEWAY_REPLY_PATH),
            thread_id,
            build_prompt(message),
        ]
    )

    log_line(
        "Starting Codex for Telegram message "
        f"{message.get('message_id')} from chat {message.get('chat_id')} "
        f"using codex_path={codex_path!r} cwd={str(cwd)!r} CODEX_HOME={codex_env.get('CODEX_HOME')!r}"
    )
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            env=codex_env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        return "Codex is still working or timed out while handling the Telegram request."
    except Exception as exc:
        attempted = "\n".join(candidate_codex_paths(configured_codex_path))
        return (
            "Codex could not be started from the Telegram gateway.\n\n"
            f"{exc}\n\n"
            f"Resolved Codex path: {codex_path}\n"
            f"Working directory: {cwd}\n"
            f"CODEX_HOME: {codex_env.get('CODEX_HOME')}\n"
            f"Configured CODEX_PATH: {configured_codex_path}\n"
            f"Candidate paths:\n{attempted}"
        )

    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        if len(details) > 1800:
            details = details[-1800:]
        return f"Codex could not complete the Telegram-triggered task.\n\n{details or 'No diagnostic output was returned.'}"

    if GATEWAY_REPLY_PATH.exists():
        reply = GATEWAY_REPLY_PATH.read_text(encoding="utf-8").strip()
        if reply:
            return reply
    return "Codex finished the Telegram-triggered task, but did not produce a final text reply."


def handle_message(message: dict[str, Any], notifier_url: str, send_ack: bool) -> None:
    register_message(message)
    append_transcript("gateway_received", {"message": message, "delivery_mode": delivery_mode()})
    reply_chat_id = int(message.get("chat_id") or 0) or None
    command_reply = handle_gateway_command(message)
    if command_reply is not None:
        notifier_send(command_reply, notifier_url, chat_id=reply_chat_id)
        return

    if not wait_for_lock():
        raise RuntimeError("Gateway lock stayed active until the configured wait timeout.")

    try:
        mode = delivery_mode()
        if mode == "active-app":
            result = run_active_app_for_message(message, notifier_url)
            if result.get("ok"):
                reply = str(result.get("reply") or "").strip()
                notifier_send(reply or "Codex finished the Telegram-triggered task, but did not produce a final text reply.", notifier_url, chat_id=reply_chat_id)
                return
            log_line(f"Active Codex Desktop delivery failed for Telegram message {message.get('message_id')}: {result}")
            if env_value("TELEGRAM_GATEWAY_FALLBACK_TO_RESUME", default="true").lower() not in {"1", "true", "yes", "on"}:
                notifier_send(
                    "I received your Telegram message, but could not post it into the active Codex Desktop chat.",
                    notifier_url,
                    chat_id=reply_chat_id,
                )
                return

        if send_ack:
            notifier_send("Received. I am waking this Codex thread now.", notifier_url, chat_id=reply_chat_id)
        reply = run_codex_for_message(message)
        notifier_send(reply, notifier_url, chat_id=reply_chat_id)
    finally:
        release_lock()


def main() -> int:
    load_dotenv(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run an instant-ish Telegram-to-Codex gateway.")
    parser.add_argument("--notifier-url", default=env_value("TELEGRAM_NOTIFIER_URL", default="http://127.0.0.1:8787"))
    parser.add_argument("--interval", type=float, default=float(env_value("TELEGRAM_GATEWAY_POLL_INTERVAL_SECONDS", default="1")))
    parser.add_argument("--once", action="store_true", help="Process current inbox once and exit.")
    parser.add_argument("--no-ack", action="store_true", help="Do not send a received/waking acknowledgment before Codex runs.")
    args = parser.parse_args()

    print(f"Telegram Codex gateway watching {args.notifier_url}")
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()
    last_issue = ""
    while True:
        try:
            write_heartbeat(
                "telegram_gateway",
                details={"notifier_url": args.notifier_url, "interval_seconds": args.interval},
            )
            payload = notifier_get("/inbox?clear=true", args.notifier_url)
            if last_issue:
                print("Connected to the Telegram notifier.")
                last_issue = ""
            messages = list(payload.get("messages") or [])
            for message in messages:
                write_heartbeat(
                    "telegram_gateway",
                    status="handling_message",
                    details={"message_id": message.get("message_id"), "chat_id": message.get("chat_id")},
                )
                handle_message(message, args.notifier_url, send_ack=not args.no_ack)
                write_heartbeat(
                    "telegram_gateway",
                    details={"notifier_url": args.notifier_url, "interval_seconds": args.interval},
                )
            if args.once:
                write_heartbeat("telegram_gateway", status="stopping", details={"reason": "once"})
                return 0
            time.sleep(max(0.2, args.interval))
        except KeyboardInterrupt:
            write_heartbeat("telegram_gateway", status="stopping", details={"reason": "keyboard_interrupt"})
            print("\nTelegram Codex gateway stopped.")
            return 0
        except Exception as exc:
            write_heartbeat(
                "telegram_gateway",
                status="waiting_for_notifier",
                details={"notifier_url": args.notifier_url, "error": str(exc)},
            )
            log_line(f"Gateway loop error: {exc!r}")
            issue = str(exc)
            if issue != last_issue:
                print(
                    "Waiting for the Telegram notifier at "
                    f"{args.notifier_url}. Start run_telegram_notifier.ps1 first. "
                    f"Last error: {issue}",
                    file=sys.stderr,
                )
                last_issue = issue
            if args.once:
                return 1
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
