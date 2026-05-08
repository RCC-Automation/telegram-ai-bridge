#! python3
"""CLI client for the localhost Telegram notifier service."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from telegram_chat_registry import active_chat, format_chats, resolve_chat, set_active_chat
from telegram_voice_transcription import format_voice_transcription_status, load_dotenv as load_voice_dotenv


ROOT = Path(__file__).resolve().parent
TOKEN_PATH = ROOT / "telegram_notifier_token.txt"


def local_token() -> str:
    if not TOKEN_PATH.exists():
        raise RuntimeError(f"Missing local notifier token file: {TOKEN_PATH}")
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in {"send", "send-image", "inbox", "chats", "use", "whoami", "voice-status", "-h", "--help"}:
        sys.argv.insert(1, "send")
    parser = argparse.ArgumentParser(description="Send a message through the local Telegram notifier service.")
    sub = parser.add_subparsers(dest="command")
    send_parser = sub.add_parser("send", help="Send a Telegram message.")
    send_parser.add_argument("text")
    send_parser.add_argument("--chat", help="Optional chat alias or id. Defaults to the active chat.")
    image_parser = sub.add_parser("send-image", help="Send a Telegram image.")
    image_parser.add_argument("path", help="Local image path.")
    image_parser.add_argument("--caption", default="", help="Optional image caption.")
    image_parser.add_argument("--chat", help="Optional chat alias or id. Defaults to the active chat.")
    inbox_parser = sub.add_parser("inbox", help="Read stored incoming Telegram messages.")
    inbox_parser.add_argument("--clear", action="store_true")
    sub.add_parser("chats", help="List known Telegram chats.")
    use_parser = sub.add_parser("use", help="Set the active Telegram chat.")
    use_parser.add_argument("chat")
    sub.add_parser("whoami", help="Show the active Telegram chat.")
    sub.add_parser("voice-status", help="Show Telegram voice transcription backend status.")
    parser.add_argument("--url", default=os.getenv("TELEGRAM_NOTIFIER_URL", "http://127.0.0.1:8787"))
    args = parser.parse_args()

    if args.command == "chats":
        print(format_chats())
        return 0

    if args.command == "use":
        selected = set_active_chat(args.chat)
        print(f"Active Telegram chat set to {selected.get('label') or selected.get('chat_id')} ({selected.get('chat_id')}).")
        return 0

    if args.command == "whoami":
        current = active_chat()
        print(json.dumps(current or {}, indent=2))
        return 0

    if args.command == "voice-status":
        load_voice_dotenv()
        print(format_voice_transcription_status())
        return 0

    if args.command == "inbox":
        clear = "true" if args.clear else "false"
        with urllib.request.urlopen(f"{args.url.rstrip('/')}/inbox?token={local_token()}&clear={clear}", timeout=10) as response:
            print(response.read().decode("utf-8"))
        return 0

    if args.command not in {"send", "send-image"}:
        raise RuntimeError("Use 'send <text>', 'send-image <path>', or 'inbox'.")
    payload = {"token": local_token()}
    if args.command == "send-image":
        image_path = Path(args.path).expanduser().resolve()
        payload["photo_path"] = str(image_path)
        payload["text"] = args.caption
    else:
        payload["text"] = args.text
    chat_ref = getattr(args, "chat", None)
    if chat_ref:
        chat = resolve_chat(chat_ref)
        if not chat and chat_ref.lstrip("-").isdigit():
            payload["chat_id"] = int(chat_ref)
        elif chat:
            payload["chat_id"] = int(chat["chat_id"])
        else:
            raise RuntimeError(f"Unknown chat: {chat_ref}")
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        args.url.rstrip("/") + "/send",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "telegram-notify-client/1.0"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.read().decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
