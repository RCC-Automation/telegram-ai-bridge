#! python3
"""Manage the local Telegram notifier as a Windows scheduled task.

The notifier needs normal host networking. Starting it as a scheduled task keeps
it out of Codex's command sandbox and gives Codex a stable start/stop surface.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
HEARTBEAT_PATH = WORKSPACE_ROOT / "telegram-messages" / "bridge-heartbeats" / "telegram_notifier.json"
INBOX_PATH = ROOT / "telegram_notifier_inbox.json"
GATEWAY_LOCK_PATH = ROOT / "telegram_codex_gateway.lock"
TRANSCRIPT_PATH = WORKSPACE_ROOT / "telegram-messages" / "telegram-codex-transcript.jsonl"
TASK_NAME = os.getenv("TELEGRAM_NOTIFIER_TASK_NAME", "CodexTelegramNotifier")
RUN_KEY_NAME = os.getenv("TELEGRAM_NOTIFIER_RUN_KEY_NAME", "CodexTelegramNotifier")
RUN_SCRIPT = ROOT / "run_telegram_notifier.ps1"
BOOTSTRAP_SCRIPT = ROOT / "telegram_service_bootstrap.py"
NOTIFIER_SCRIPT = ROOT / "telegram_notifier_service.py"
STDOUT_LOG = ROOT / "telegram_notifier_service.out.log"
STDERR_LOG = ROOT / "telegram_notifier_service.err.log"
PID_FILE = ROOT / "telegram_notifier_restart_pid.txt"
NOTIFIER_URL = os.getenv("TELEGRAM_NOTIFIER_URL", "http://127.0.0.1:8787")
TOKEN_PATH = ROOT / "telegram_notifier_token.txt"


def run_command(args: list[str], timeout: int = 30) -> dict[str, Any]:
    completed = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
        "args": args,
    }


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def heartbeat_status() -> dict[str, Any] | None:
    heartbeat = read_json(HEARTBEAT_PATH)
    if not isinstance(heartbeat, dict):
        return None
    annotated = dict(heartbeat)
    try:
        age = max(0.0, time.time() - float(annotated.get("timestamp")))
    except Exception:
        age = None
    annotated["age_seconds"] = age
    annotated["stale"] = age is None or age > 20
    return annotated


def tcp_status(host: str = "127.0.0.1", port: int = 8787, timeout: float = 1.0) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"reachable": True, "host": host, "port": port}
    except Exception as exc:
        return {"reachable": False, "host": host, "port": port, "error": str(exc)}


def health(timeout: int = 5) -> dict[str, Any]:
    request = urllib.request.Request(NOTIFIER_URL.rstrip("/") + "/health")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        payload.setdefault("ok", True)
        return payload
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "url": NOTIFIER_URL,
        }


def local_token() -> str:
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    return load_env_file().get("TELEGRAM_NOTIFIER_LOCAL_TOKEN", "")


def shutdown_notifier(timeout: int = 5) -> dict[str, Any]:
    token = local_token()
    if not token:
        return {"ok": False, "error": f"Missing local notifier token: {TOKEN_PATH}"}
    payload = json.dumps({"token": token}).encode("utf-8")
    request = urllib.request.Request(
        NOTIFIER_URL.rstrip("/") + "/shutdown",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "telegram-service-manager/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": NOTIFIER_URL.rstrip("/") + "/shutdown"}


def task_query() -> dict[str, Any]:
    result = run_command(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"], timeout=20)
    result["exists"] = result["ok"]
    return result


def install_task() -> dict[str, Any]:
    if not RUN_SCRIPT.exists():
        return {"ok": False, "error": f"Missing run script: {RUN_SCRIPT}"}
    task_command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{RUN_SCRIPT}"'
    result = run_command(
        [
            "schtasks",
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            task_command,
            "/SC",
            "ONLOGON",
            "/F",
        ],
        timeout=30,
    )
    return {"ok": result["ok"], "task_name": TASK_NAME, "task_command": task_command, "result": result}


def pythonw_path() -> str:
    current = Path(sys.executable)
    if current.name.lower() == "python.exe":
        sibling = current.with_name("pythonw.exe")
        if sibling.exists():
            return str(sibling)
    if current.name.lower() == "pythonw.exe" and current.exists():
        return str(current)
    found = run_command(["where", "pythonw.exe"], timeout=10)
    if found["ok"] and found["stdout"]:
        return found["stdout"].splitlines()[0].strip()
    return str(current)


def direct_start() -> dict[str, Any]:
    if not BOOTSTRAP_SCRIPT.exists() or not NOTIFIER_SCRIPT.exists():
        return {
            "ok": False,
            "error": "Missing notifier bootstrap or service script.",
            "bootstrap": str(BOOTSTRAP_SCRIPT),
            "service": str(NOTIFIER_SCRIPT),
        }
    env = os.environ.copy()
    env["TELEGRAM_NOTIFIER_INTERACTIVE_CODEX"] = "true"
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    process = subprocess.Popen(
        [
            pythonw_path(),
            str(BOOTSTRAP_SCRIPT),
            str(NOTIFIER_SCRIPT),
            "--stdout",
            str(STDOUT_LOG),
            "--stderr",
            str(STDERR_LOG),
        ],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    return {"ok": True, "pid": process.pid, "backend": "direct-detached", "pid_file": str(PID_FILE)}


def task_run() -> dict[str, Any]:
    query = task_query()
    if not query["exists"]:
        installed = install_task()
        if not installed["ok"]:
            fallback = direct_start()
            return {"ok": fallback["ok"], "task_install": installed, "fallback": fallback}
    result = run_command(["schtasks", "/Run", "/TN", TASK_NAME], timeout=20)
    if not result["ok"]:
        fallback = direct_start()
        return {"ok": fallback["ok"], "task_run": result, "fallback": fallback}
    return {"ok": result["ok"], "task_name": TASK_NAME, "result": result}


def task_end() -> dict[str, Any]:
    result = run_command(["schtasks", "/End", "/TN", TASK_NAME], timeout=20)
    # schtasks returns non-zero when the task is not running. That is acceptable
    # for a stop operation, so keep the raw result and continue with PID cleanup.
    pid_cleanup = stop_pid_tree(PID_FILE)
    return {"ok": True, "task_name": TASK_NAME, "result": result, "pid_cleanup": pid_cleanup}


def stop_pid_tree(pid_file: Path) -> dict[str, Any]:
    if not pid_file.exists():
        return {"ok": True, "pid_file_exists": False}
    raw = pid_file.read_text(encoding="utf-8").strip()
    try:
        pid = int(raw)
    except Exception:
        return {"ok": False, "pid_file": str(pid_file), "raw": raw, "error": "PID file did not contain an integer"}
    result = run_command(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)
    alive = process_exists(pid)
    if result["ok"] or alive is False:
        try:
            pid_file.unlink()
        except OSError:
            pass
    return {"ok": True, "pid": pid, "result": result}


def wait_for_health(timeout_seconds: int = 20) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last = health(timeout=3)
    while time.time() < deadline:
        last = health(timeout=3)
        if last.get("ok"):
            return {"ok": True, "health": last}
        time.sleep(1)
    return {"ok": False, "health": last, "timeout_seconds": timeout_seconds}


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "task_name": TASK_NAME,
        "notifier_url": NOTIFIER_URL,
        "delivery": delivery_status(),
        "health": health(timeout=3),
        "tcp": tcp_status(),
        "heartbeat": heartbeat_status(),
        "inbox": inbox_status(),
        "gateway_lock": gateway_lock_status(),
        "transcript": transcript_status(),
        "pid_file": {
            "path": str(PID_FILE),
            "exists": PID_FILE.exists(),
            "value": PID_FILE.read_text(encoding="utf-8").strip() if PID_FILE.exists() else None,
        },
        "task": task_query(),
    }


def diagnostics() -> dict[str, Any]:
    payload = status()
    payload["app_server_control"] = app_server_control_status()
    return payload


def load_env_file() -> dict[str, str]:
    env_path = ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def delivery_status() -> dict[str, Any]:
    env_values = load_env_file()
    return {
        "mode": env_values.get("TELEGRAM_GATEWAY_DELIVERY_MODE", "active-app"),
        "fallback_to_resume": env_values.get("TELEGRAM_GATEWAY_FALLBACK_TO_RESUME", "true"),
        "interactive_codex": env_values.get("TELEGRAM_NOTIFIER_INTERACTIVE_CODEX", ""),
        "interactive_no_ack": env_values.get("TELEGRAM_NOTIFIER_INTERACTIVE_NO_ACK", ""),
        "codex_home": env_values.get("TELEGRAM_GATEWAY_CODEX_HOME", ""),
        "thread_id": env_values.get("TELEGRAM_GATEWAY_CODEX_THREAD_ID", env_values.get("CODEX_THREAD_ID", "")),
    }


def inbox_status() -> dict[str, Any]:
    payload = read_json(INBOX_PATH)
    messages = payload.get("messages") if isinstance(payload, dict) else []
    if not isinstance(messages, list):
        messages = []
    counts = {"pending": 0, "processing": 0, "done": 0, "error": 0, "unknown": 0}
    for message in messages:
        dispatch = message.get("dispatch") if isinstance(message, dict) else {}
        status_value = str((dispatch or {}).get("status") or "pending")
        counts[status_value if status_value in counts else "unknown"] += 1
    return {
        "path": str(INBOX_PATH),
        "exists": INBOX_PATH.exists(),
        "total": len(messages),
        "counts": counts,
        "offset": payload.get("offset") if isinstance(payload, dict) else None,
    }


def process_exists(pid: int | None) -> bool | None:
    if not pid or pid <= 0:
        return False
    result = run_command(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=10)
    if not result["ok"]:
        return None
    output = str(result["stdout"] or "")
    return f'"{pid}"' in output


def gateway_lock_status() -> dict[str, Any]:
    if not GATEWAY_LOCK_PATH.exists():
        return {"path": str(GATEWAY_LOCK_PATH), "exists": False}
    raw = GATEWAY_LOCK_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    try:
        pid = int(raw)
    except Exception:
        pid = None
    age = max(0.0, time.time() - GATEWAY_LOCK_PATH.stat().st_mtime)
    return {
        "path": str(GATEWAY_LOCK_PATH),
        "exists": True,
        "raw": raw,
        "pid": pid,
        "owner_alive": process_exists(pid),
        "age_seconds": age,
    }


def transcript_status() -> dict[str, Any]:
    return {
        "path": str(TRANSCRIPT_PATH),
        "exists": TRANSCRIPT_PATH.exists(),
        "size_bytes": TRANSCRIPT_PATH.stat().st_size if TRANSCRIPT_PATH.exists() else 0,
    }


def resolve_codex_path() -> str:
    env_values = load_env_file()
    configured = env_values.get("CODEX_PATH") or "codex"
    candidates = [configured]
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(str(Path(local_app_data) / "OpenAI" / "Codex" / "bin" / "codex.exe"))
        candidates.append(
            str(
                Path(local_app_data)
                / "Packages"
                / "OpenAI.Codex_2p2nqsd0c76g0"
                / "LocalCache"
                / "Local"
                / "OpenAI"
                / "Codex"
                / "bin"
                / "codex.exe"
            )
        )
    for candidate in candidates:
        expanded = os.path.expandvars(candidate.strip().strip('"'))
        if os.path.isabs(expanded) and Path(expanded).exists():
            return expanded
    return configured


def app_server_control_status() -> dict[str, Any]:
    codex_path = resolve_codex_path()
    env_values = load_env_file()
    env = os.environ.copy()
    codex_home = env_values.get("TELEGRAM_GATEWAY_CODEX_HOME") or env_values.get("CODEX_HOME") or str(Path.home() / ".codex")
    env["CODEX_HOME"] = os.path.expandvars(codex_home)
    try:
        process = subprocess.Popen(
            [codex_path, "app-server", "proxy"],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        time.sleep(1)
        returncode = process.poll()
        if returncode is None:
            process.kill()
            return {"ok": True, "codex_path": codex_path, "codex_home": env["CODEX_HOME"], "detail": "proxy stayed open"}
        stderr = process.stderr.read() if process.stderr else ""
        stdout = process.stdout.read() if process.stdout else ""
        return {
            "ok": False,
            "codex_path": codex_path,
            "codex_home": env["CODEX_HOME"],
            "returncode": returncode,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }
    except Exception as exc:
        return {"ok": False, "codex_path": codex_path, "codex_home": env["CODEX_HOME"], "error": str(exc)}


def start() -> dict[str, Any]:
    current = health(timeout=3)
    if current.get("ok"):
        return {"ok": True, "already_running": True, "health": current}
    run = task_run()
    ready = wait_for_health(timeout_seconds=25)
    return {"ok": bool(ready.get("ok")), "run": run, "ready": ready, "status": status()}


def stop() -> dict[str, Any]:
    shutdown = shutdown_notifier()
    time.sleep(2)
    if shutdown.get("ok"):
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    ended = task_end()
    time.sleep(2)
    return {"ok": True, "shutdown": shutdown, "ended": ended, "status": status()}


def restart() -> dict[str, Any]:
    stopped = stop()
    started = start()
    return {"ok": bool(started.get("ok")), "stopped": stopped, "started": started}


def ensure() -> dict[str, Any]:
    installed = install_task()
    current = health(timeout=3)
    if current.get("ok"):
        return {"ok": True, "installed": installed, "already_running": True, "health": current, "status": status()}
    started = start()
    return {"ok": bool(started.get("ok")), "installed": installed, "started": started}


def install_run_key() -> dict[str, Any]:
    command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{RUN_SCRIPT}"'
    result = run_command(
        [
            "reg",
            "add",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
            "/v",
            RUN_KEY_NAME,
            "/t",
            "REG_SZ",
            "/d",
            command,
            "/f",
        ],
        timeout=20,
    )
    return {"ok": result["ok"], "run_key": RUN_KEY_NAME, "command": command, "result": result}


def uninstall_run_key() -> dict[str, Any]:
    result = run_command(
        ["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", "/v", RUN_KEY_NAME, "/f"],
        timeout=20,
    )
    return {"ok": result["ok"], "run_key": RUN_KEY_NAME, "result": result}


def uninstall_task() -> dict[str, Any]:
    result = run_command(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=20)
    return {"ok": result["ok"], "task_name": TASK_NAME, "result": result}


COMMANDS = {
    "diagnostics": diagnostics,
    "status": status,
    "install-task": install_task,
    "install-run-key": install_run_key,
    "uninstall-run-key": uninstall_run_key,
    "uninstall-task": uninstall_task,
    "start": start,
    "stop": stop,
    "restart": restart,
    "ensure": ensure,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Telegram notifier service.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    payload = COMMANDS[args.command]()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
