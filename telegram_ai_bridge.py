#! python3
"""Private Telegram-to-Codex task bridge.

This uses Telegram long polling, so the computer does not need an inbound
network route or public webhook URL. Telegram messages are handed to the
local Codex CLI, which can work in this computer environment before replying.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DESKTOP_ENGINE_ROOT = ROOT.parent / "desktop engine"
if str(DESKTOP_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ENGINE_ROOT))

from desktop_agent import DesktopAgent

ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "state.json"
LOG_PATH = ROOT / "bridge.log"
DEFAULT_DESKTOP_BROKER_URL = "http://127.0.0.1:8765"


def log_line(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def candidate_codex_paths(configured_path: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        normalized = os.path.expandvars(value.strip().strip('"').strip("'"))
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

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


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}. Add it to {ENV_PATH}.")
    return value


def allowed_chat_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return set()
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"telegram_offset": 0}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def desktop_host_is_running(broker_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{broker_url.rstrip('/')}/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("ok"))
    except Exception:
        return False


def maybe_start_desktop_host(broker_url: str | None) -> None:
    if not broker_url:
        return
    if not env_flag("DESKTOP_HOST_AUTOSTART", True):
        return
    parsed = urllib.parse.urlparse(broker_url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return
    if desktop_host_is_running(broker_url):
        return

    host_script = DESKTOP_ENGINE_ROOT / "desktop_host_app.py"
    if not host_script.exists():
        log_line(f"Desktop host autostart skipped; missing {host_script}")
        return

    env = os.environ.copy()
    env.setdefault("DESKTOP_BROKER_CWD", str(Path(os.getenv("CODEX_CWD", str(ROOT.parent))).expanduser()))
    env.setdefault("DESKTOP_BROKER_PORT", str(parsed.port or 8765))
    creationflags = 0
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        creationflags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    stdout_path = DESKTOP_ENGINE_ROOT / "desktop_host_autostart.out.log"
    stderr_path = DESKTOP_ENGINE_ROOT / "desktop_host_autostart.err.log"
    try:
        stdout_handle = stdout_path.open("ab")
        stderr_handle = stderr_path.open("ab")
        subprocess.Popen(
            [sys.executable, str(host_script)],
            cwd=str(DESKTOP_ENGINE_ROOT),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        log_line("Desktop host autostart requested.")
        deadline = time.time() + 10
        while time.time() < deadline:
            if desktop_host_is_running(broker_url):
                log_line("Desktop host is running.")
                return
            time.sleep(0.5)
        log_line("Desktop host autostart did not become ready before timeout.")
    except Exception as exc:
        log_line(f"Desktop host autostart failed: {exc!r}")


def _network_error_message(url: str, exc: Exception) -> str:
    host = urllib.parse.urlparse(url).hostname or url
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return f"DNS lookup failed for {host}."
        if isinstance(reason, TimeoutError):
            return f"Connection to {host} timed out."
        return f"Network error while reaching {host}: {reason}"
    return f"Network error while reaching {host}: {exc}"


def http_json(
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 60,
    retries: int = 3,
    retry_delay_seconds: float = 3.0,
) -> dict:
    data = None
    req_headers = {"User-Agent": "telegram-ai-bridge/1.0"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, data=data, headers=req_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            message = _network_error_message(url, exc)
            log_line(f"{message} Attempt {attempt}/{retries}")
            if attempt == retries:
                raise RuntimeError(message) from exc
            time.sleep(retry_delay_seconds * attempt)
    raise RuntimeError(f"Network request failed for {url}: {last_exc}")


class Telegram:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    def get_updates(self, offset: int) -> list[dict]:
        payload = {
            "offset": offset,
            "timeout": 45,
            "allowed_updates": ["message"],
        }
        result = http_json(f"{self.base_url}/getUpdates", payload, timeout=60)
        if not result.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {result}")
        return result.get("result", [])

    def send_message(self, chat_id: int, text: str) -> None:
        max_len = 3900
        chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)] or [""]
        for chunk in chunks:
            payload = {"chat_id": chat_id, "text": chunk}
            result = http_json(f"{self.base_url}/sendMessage", payload, timeout=30)
            if not result.get("ok"):
                raise RuntimeError(f"Telegram sendMessage failed: {result}")


class CodexCli:
    def __init__(self, codex_path: str, thread_id: str, cwd: Path, full_auto: bool, timeout_seconds: int) -> None:
        self.codex_path = codex_path
        self.thread_id = thread_id
        self.cwd = cwd
        self.full_auto = full_auto
        self.timeout_seconds = timeout_seconds

    def resolve_codex_path(self) -> str | None:
        for candidate in candidate_codex_paths(self.codex_path):
            if os.path.isabs(candidate):
                if os.path.exists(candidate):
                    return candidate
            else:
                resolved = shutil.which(candidate)
                if resolved:
                    return resolved
        return None

    def run_task(self, text: str, chat_id: int) -> str:
        output_path = ROOT / "last_codex_reply.txt"
        prompt = (
            "This request arrived from the user's private Telegram bridge. "
            "Complete the user's requested task in this computer environment when it is feasible. "
            "After you have done the work, reply with a concise result summary suitable for Telegram. "
            "If a task needs permission or cannot be completed safely, explain the blocker clearly.\n\n"
            f"Telegram chat id: {chat_id}\n"
            f"User request:\n{text}"
        )

        resolved_codex_path = self.resolve_codex_path()
        if not resolved_codex_path:
            attempted = "\n".join(candidate_codex_paths(self.codex_path))
            log_line(f"Could not resolve Codex executable. Tried: {attempted}")
            return "Codex executable was not found.\n\nTried:\n" + attempted

        args = [resolved_codex_path, "exec", "resume"]
        if self.full_auto:
            args.append("--full-auto")
        args.extend(
            [
                "--skip-git-repo-check",
                "--output-last-message",
                str(output_path),
                self.thread_id,
                prompt,
            ]
        )

        log_line(f"Starting Codex task for chat {chat_id} in {self.cwd}")
        log_line(f"Resolved Codex path: {resolved_codex_path}")
        log_line(f"Codex command: {' '.join(args[:5])} ...")

        try:
            completed = subprocess.run(
                args,
                cwd=str(self.cwd),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError:
            return (
                f"Codex executable was not found: {resolved_codex_path}\n\n"
                "Set CODEX_PATH in .env to the full path of codex.exe."
            )
        except PermissionError as exc:
            return f"Codex could not start because Windows denied access.\n\n{exc}"
        except subprocess.TimeoutExpired:
            return (
                "Codex is still working or took too long for the bridge timeout. "
                "Check the computer, or increase CODEX_TASK_TIMEOUT_SECONDS in .env."
            )
        except Exception as exc:
            log_line(f"Unexpected launch error: {exc!r}")
            return f"Codex could not be started.\n\n{exc}"

        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "").strip()
            if len(details) > 1500:
                details = details[-1500:]
            log_line(f"Codex exit code {completed.returncode}: {details or 'no output'}")
            return f"Codex could not complete the task.\n\n{details or 'No diagnostic output was returned.'}"

        if output_path.exists():
            answer = output_path.read_text(encoding="utf-8").strip()
            if answer:
                log_line(f"Codex completed task for chat {chat_id}")
                return answer
        log_line("Codex finished without producing a final reply file")
        return "Codex finished, but the bridge did not receive a final text reply."


def main() -> int:
    load_dotenv(ENV_PATH)
    telegram = Telegram(require_env("TELEGRAM_BOT_TOKEN"))
    broker_url = os.getenv("DESKTOP_BROKER_URL", DEFAULT_DESKTOP_BROKER_URL).strip() or None
    maybe_start_desktop_host(broker_url)
    agent = DesktopAgent(
        cwd=Path(os.getenv("CODEX_CWD", str(ROOT.parent))).expanduser(),
        artifacts_dir=DESKTOP_ENGINE_ROOT / "artifacts",
        broker_url=broker_url,
        broker_token=os.getenv("DESKTOP_BROKER_TOKEN", "").strip() or None,
    )
    codex = CodexCli(
        codex_path=os.getenv("CODEX_PATH", r"C:\Users\barru\AppData\Local\OpenAI\Codex\bin\codex.exe").strip()
        or r"C:\Users\barru\AppData\Local\OpenAI\Codex\bin\codex.exe",
        thread_id=require_env("CODEX_THREAD_ID"),
        cwd=Path(os.getenv("CODEX_CWD", str(ROOT.parent))).expanduser(),
        full_auto=os.getenv("CODEX_FULL_AUTO", "true").strip().lower() in {"1", "true", "yes", "on"},
        timeout_seconds=int(os.getenv("CODEX_TASK_TIMEOUT_SECONDS", "900")),
    )
    allowed_ids = allowed_chat_ids()
    state = load_state()
    print("Telegram Codex task bridge is running. Press Ctrl+C to stop.")

    while True:
        try:
            updates = telegram.get_updates(int(state.get("telegram_offset", 0)))
            for update in updates:
                state["telegram_offset"] = update["update_id"] + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                text = (message.get("text") or "").strip()
                if not chat_id or not text:
                    continue

                if allowed_ids and int(chat_id) not in allowed_ids:
                    telegram.send_message(int(chat_id), "This chat is not authorized for this private bridge.")
                    continue
                if not allowed_ids:
                    telegram.send_message(
                        int(chat_id),
                        f"Your chat id is {chat_id}. Add it to TELEGRAM_ALLOWED_CHAT_IDS in .env, then restart me.",
                    )
                    continue
                if text.lower() in {"/start", "/help"}:
                    telegram.send_message(
                        int(chat_id),
                        "Send me a task. I will pass it to Codex on this computer, wait for the work, then reply here.",
                    )
                    continue
                if text.lower() == "/status":
                    telegram.send_message(int(chat_id), "The bridge is running and ready for a task.")
                    continue

                telegram.send_message(int(chat_id), "Received. I am working on it from this computer now.")
                maybe_start_desktop_host(broker_url)
                agent_result = agent.handle(text, session_key=f"telegram:{chat_id}")
                if agent_result.handled:
                    log_line(f"Desktop helper handled chat {chat_id}: {text}")
                    answer = agent_result.message
                else:
                    answer = codex.run_task(text, int(chat_id))
                save_state(state)
                telegram.send_message(int(chat_id), answer)
            save_state(state)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
        except Exception as exc:
            log_line(f"Bridge loop error: {exc!r}")
            print(f"Network or bridge issue: {exc}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
