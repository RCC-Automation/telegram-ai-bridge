#! python3
"""Telegram side-channel for the current Codex session.

This does not start Codex. It lets the current agent send a Telegram message,
optionally wait for a human reply, and continue in the same chat turn.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "telegram_sidechannel_state.json"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def allowed_chat_ids() -> set[int]:
    raw = env_value("TELEGRAM_SIDECHANNEL_CHAT_IDS", "TELEGRAM_ALLOWED_CHAT_IDS")
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def default_chat_id() -> int:
    raw = env_value("TELEGRAM_SIDECHANNEL_DEFAULT_CHAT_ID", "TELEGRAM_ALLOWED_CHAT_IDS")
    if "," in raw:
        raw = raw.split(",", 1)[0]
    raw = raw.strip()
    if not raw:
        raise RuntimeError("Missing TELEGRAM_SIDECHANNEL_DEFAULT_CHAT_ID or TELEGRAM_ALLOWED_CHAT_IDS.")
    return int(raw)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"offset": 0}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


class TelegramSideChannel:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "telegram-sidechannel/1.0"},
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

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [""]
        for chunk in chunks:
            result = self.call("sendMessage", {"chat_id": chat_id, "text": chunk}, timeout=30)
        return result or {}

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict[str, Any]]:
        result = self.call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 10,
        )
        return list(result.get("result", []))


def extract_text_reply(
    updates: list[dict[str, Any]],
    allowed_ids: set[int],
    prompt_message_id: int | None,
    sent_at: float,
) -> tuple[int, str] | None:
    for update in updates:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id") or 0)
        if allowed_ids and chat_id not in allowed_ids:
            continue
        text = (message.get("text") or "").strip()
        if not text:
            continue
        message_date = int(message.get("date") or 0)
        if message_date and message_date < int(sent_at):
            continue
        reply_to = message.get("reply_to_message") or {}
        if prompt_message_id and reply_to.get("message_id") not in {None, prompt_message_id}:
            continue
        return int(update["update_id"]) + 1, text
    return None


def main() -> int:
    load_dotenv(ENV_PATH)
    token = env_value("TELEGRAM_SIDECHANNEL_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_SIDECHANNEL_BOT_TOKEN or TELEGRAM_BOT_TOKEN.")

    parser = argparse.ArgumentParser(description="Use Telegram as a side-channel for this Codex chat.")
    sub = parser.add_subparsers(dest="command", required=True)

    send_parser = sub.add_parser("send", help="Send a Telegram message.")
    send_parser.add_argument("text")
    send_parser.add_argument("--chat-id", type=int, default=None)

    ask_parser = sub.add_parser("ask", help="Send a Telegram question and wait for a reply.")
    ask_parser.add_argument("text")
    ask_parser.add_argument("--chat-id", type=int, default=None)
    ask_parser.add_argument("--timeout", type=int, default=300)

    sub.add_parser("updates", help="Poll and print new allowed text messages.")

    client = TelegramSideChannel(token)
    state = load_state()
    allowed_ids = allowed_chat_ids()

    if parser.parse_args().command == "updates":
        args = parser.parse_args()
    else:
        args = parser.parse_args()

    if args.command == "send":
        chat_id = args.chat_id or default_chat_id()
        if allowed_ids and chat_id not in allowed_ids:
            raise RuntimeError(f"Chat id {chat_id} is not allowed.")
        result = client.send_message(chat_id, args.text)
        print(json.dumps({"sent": True, "result": result.get("result", {})}, indent=2))
        return 0

    if args.command == "updates":
        offset = int(state.get("offset") or 0)
        updates = client.get_updates(offset, timeout=1)
        if updates:
            state["offset"] = max(int(update["update_id"]) + 1 for update in updates)
            save_state(state)
        messages = []
        for update in updates:
            message = update.get("message") or {}
            chat_id = int((message.get("chat") or {}).get("id") or 0)
            if allowed_ids and chat_id not in allowed_ids:
                continue
            text = (message.get("text") or "").strip()
            if text:
                messages.append({"chat_id": chat_id, "text": text, "update_id": update.get("update_id")})
        print(json.dumps(messages, indent=2))
        return 0

    if args.command == "ask":
        chat_id = args.chat_id or default_chat_id()
        if allowed_ids and chat_id not in allowed_ids:
            raise RuntimeError(f"Chat id {chat_id} is not allowed.")
        sent_at = time.time()
        sent = client.send_message(chat_id, args.text)
        prompt_message_id = ((sent.get("result") or {}).get("message_id"))
        offset = int(state.get("offset") or 0)
        deadline = time.time() + max(1, int(args.timeout))
        while time.time() < deadline:
            updates = client.get_updates(offset, timeout=min(30, max(1, int(deadline - time.time()))))
            if updates:
                offset = max(int(update["update_id"]) + 1 for update in updates)
                state["offset"] = offset
                save_state(state)
            reply = extract_text_reply(updates, allowed_ids, prompt_message_id, sent_at)
            if reply:
                state["offset"] = reply[0]
                save_state(state)
                print(reply[1])
                return 0
        raise TimeoutError("No Telegram reply received before timeout.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
