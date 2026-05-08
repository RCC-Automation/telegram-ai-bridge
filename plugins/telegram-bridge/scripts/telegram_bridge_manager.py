#! python3
"""Management facade for the local Telegram bridge.

This script intentionally reuses the existing bridge implementation instead of
forking it. It is the stable command surface for the Codex plugin/MCP layer.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = PLUGIN_ROOT.parents[1]
RUN_BRIDGE = BRIDGE_ROOT / "run_telegram_bridge.ps1"
GATEWAY_LOG = BRIDGE_ROOT / "telegram_codex_gateway.log"
NOTIFIER_OUT_LOG = BRIDGE_ROOT / "telegram_notifier_service.out.log"
GATEWAY_OUT_LOG = BRIDGE_ROOT / "telegram_codex_gateway.out.log"
NOTIFIER_ERR_LOG = BRIDGE_ROOT / "telegram_notifier_service.err.log"
GATEWAY_ERR_LOG = BRIDGE_ROOT / "telegram_codex_gateway.err.log"
NOTIFIER_PID = BRIDGE_ROOT / "telegram_notifier_restart_pid.txt"
GATEWAY_PID = BRIDGE_ROOT / "telegram_gateway_restart_pid.txt"
HEARTBEAT_DIR = BRIDGE_ROOT.parent / "telegram-messages" / "bridge-heartbeats"
HEARTBEAT_STALE_AFTER_SECONDS = 20.0
TOKEN_PATH = BRIDGE_ROOT / "telegram_notifier_token.txt"
NOTIFIER_URL = "http://127.0.0.1:8787"

if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


def _import_runtime_modules() -> dict[str, Any]:
    import codex_thread_registry
    import telegram_chat_registry
    import telegram_voice_transcription

    return {
        "threads": codex_thread_registry,
        "chats": telegram_chat_registry,
        "voice": telegram_voice_transcription,
    }


def _pid_from_file(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except Exception:
        return None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _heartbeat_status(service: str) -> dict[str, Any]:
    record = _read_json(HEARTBEAT_DIR / f"{service}.json", None)
    if not isinstance(record, dict):
        return {"state": "missing", "fresh": False, "record": None}
    age_seconds = max(0.0, time.time() - float(record.get("timestamp") or 0))
    fresh = age_seconds <= HEARTBEAT_STALE_AFTER_SECONDS and record.get("status") != "stopping"
    return {
        "state": "healthy" if fresh else "stale",
        "fresh": fresh,
        "age_seconds": round(age_seconds, 1),
        "stale_after_seconds": HEARTBEAT_STALE_AFTER_SECONDS,
        "record": record,
    }


def _notifier_http_status() -> dict[str, Any]:
    if not TOKEN_PATH.exists():
        return {"reachable": False, "error": f"Missing token file: {TOKEN_PATH}"}
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    try:
        with urllib.request.urlopen(f"{NOTIFIER_URL}/inbox?token={token}&clear=false", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}
    return {"reachable": bool(payload.get("ok")), "url": NOTIFIER_URL, "messages_pending": len(payload.get("messages") or [])}


def _process_exists(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Id"],
            cwd=str(BRIDGE_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except Exception:
        return False
    return str(pid) in result.stdout


def _process_command(pid: int | None, include_command: bool = False) -> str:
    if not include_command:
        return ""
    if not pid:
        return ""
    command = f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction SilentlyContinue).CommandLine"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=str(BRIDGE_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _find_bridge_processes(enabled: bool = False) -> list[dict[str, Any]]:
    if not enabled:
        return []
    script_names = ("telegram_notifier_service.py", "telegram_codex_gateway.py")
    script_filter = " -or ".join([f"$_.CommandLine -like '*{name}*'" for name in script_names])
    command = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ {script_filter} }} | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=str(BRIDGE_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except Exception:
        return []
    raw = result.stdout.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    items = data if isinstance(data, list) else [data]
    processes: list[dict[str, Any]] = []
    for item in items:
        command_line = str(item.get("CommandLine") or "")
        kind = "gateway" if "telegram_codex_gateway.py" in command_line else "notifier"
        processes.append(
            {
                "pid": int(item.get("ProcessId") or 0),
                "kind": kind,
                "command_line": command_line,
            }
        )
    return processes


def _service_snapshot(service: str, pid_path: Path, include_command: bool = False) -> dict[str, Any]:
    pid = _pid_from_file(pid_path)
    heartbeat = _heartbeat_status(service)
    return {
        "pid": pid,
        "process_exists": _process_exists(pid),
        "process_command": _process_command(pid, include_command=include_command),
        "heartbeat": heartbeat,
        "running": bool(heartbeat["fresh"]) or _process_exists(pid),
    }


def status(args: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    discover_processes = bool(getattr(args, "discover_processes", False))
    discovered = _find_bridge_processes(enabled=discover_processes)
    notifier_discovered = [item for item in discovered if item["kind"] == "notifier"]
    gateway_discovered = [item for item in discovered if item["kind"] == "gateway"]
    active_chat = modules["chats"].active_chat()
    active_thread = modules["threads"].active_codex_thread()
    notifier = _service_snapshot("telegram_notifier", NOTIFIER_PID, include_command=discover_processes)
    gateway = _service_snapshot("telegram_gateway", GATEWAY_PID, include_command=discover_processes)
    notifier_http = _notifier_http_status()
    notifier["http"] = notifier_http
    notifier["running"] = bool(notifier["running"]) or bool(notifier_discovered)
    notifier["running"] = bool(notifier["running"]) or bool(notifier_http.get("reachable"))
    gateway["running"] = bool(gateway["running"]) or bool(gateway_discovered)
    notifier["discovered"] = notifier_discovered
    gateway["discovered"] = gateway_discovered
    payload = {
        "bridge_root": str(BRIDGE_ROOT),
        "notifier": notifier,
        "gateway": gateway,
        "active_chat": active_chat,
        "active_codex_thread": active_thread,
        "logs": {
            "heartbeats": str(HEARTBEAT_DIR),
            "gateway": str(GATEWAY_LOG),
            "notifier_errors": str(NOTIFIER_ERR_LOG),
            "gateway_errors": str(GATEWAY_ERR_LOG),
        },
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def chats(_: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    print(modules["chats"].format_chats())
    return 0


def threads(_: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    print(modules["threads"].format_codex_threads())
    return 0


def use_thread(args: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    selected = modules["threads"].set_active_codex_thread(args.ref)
    print(f"Active Codex thread set to {selected.get('name') or selected.get('id')} ({selected.get('id')}).")
    return 0


def alias_thread(args: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    updated = modules["threads"].set_codex_thread_alias(args.ref, args.alias)
    print(f"Alias set: {updated.get('id')} = {updated.get('alias')}")
    return 0


def use_chat(args: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    selected = modules["chats"].set_active_chat(args.ref)
    print(f"Active Telegram chat set to {selected.get('label') or selected.get('chat_id')} ({selected.get('chat_id')}).")
    return 0


def alias_chat(args: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    updated = modules["chats"].set_alias(args.ref, args.alias)
    print(f"Alias set: {updated.get('chat_id')} = {updated.get('alias')}")
    return 0


def voice_status(_: argparse.Namespace) -> int:
    modules = _import_runtime_modules()
    print(modules["voice"].format_voice_transcription_status())
    return 0


def logs(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else GATEWAY_LOG
    if not path.exists():
        print(f"Log does not exist: {path}")
        return 1
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def _start_python_service(script: str, pid_path: Path, out_log: Path, err_log: Path) -> int:
    script_path = BRIDGE_ROOT / script
    bootstrap_path = BRIDGE_ROOT / "telegram_service_bootstrap.py"
    if not script_path.exists():
        raise FileNotFoundError(f"Missing service script: {script_path}")
    if not bootstrap_path.exists():
        raise FileNotFoundError(f"Missing service bootstrap: {bootstrap_path}")
    out_log.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        python_exe = Path(sys.executable)
        pythonw = python_exe.with_name("pythonw.exe")
        service_python = pythonw if pythonw.exists() else python_exe
        argument_list = f'"{bootstrap_path}" "{script_path}" --stdout "{out_log}" --stderr "{err_log}"'
        ps = (
            "$ErrorActionPreference = 'Stop'; "
            "$p = Start-Process "
            f"-FilePath '{service_python}' "
            f"-ArgumentList '{argument_list}' "
            f"-WorkingDirectory '{BRIDGE_ROOT}' "
            "-WindowStyle Hidden "
            "-PassThru; "
            "$p.Id"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            cwd=str(BRIDGE_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "Start-Process failed.")
        pid = int(result.stdout.strip().splitlines()[-1])
        pid_path.write_text(str(pid), encoding="utf-8")
        return pid

    out_handle = out_log.open("ab")
    err_handle = err_log.open("ab")
    try:
        process = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(BRIDGE_ROOT),
            stdout=out_handle,
            stderr=err_handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        out_handle.close()
        err_handle.close()
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return int(process.pid)


def _wait_for_heartbeat(service: str, timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _heartbeat_status(service)["fresh"]:
            return True
        time.sleep(0.5)
    return False


def start(_: argparse.Namespace) -> int:
    notifier = _service_snapshot("telegram_notifier", NOTIFIER_PID)
    notifier_http = _notifier_http_status()
    gateway = _service_snapshot("telegram_gateway", GATEWAY_PID)
    started: list[str] = []

    if not notifier["running"] and not notifier_http.get("reachable"):
        pid = _start_python_service("telegram_notifier_service.py", NOTIFIER_PID, NOTIFIER_OUT_LOG, NOTIFIER_ERR_LOG)
        started.append(f"notifier pid={pid}")
        _wait_for_heartbeat("telegram_notifier", timeout_seconds=12)
    elif notifier_http.get("reachable") and not notifier["running"]:
        started.append("notifier already reachable on localhost")

    if not gateway["running"]:
        pid = _start_python_service("telegram_codex_gateway.py", GATEWAY_PID, GATEWAY_OUT_LOG, GATEWAY_ERR_LOG)
        started.append(f"gateway pid={pid}")
        _wait_for_heartbeat("telegram_gateway", timeout_seconds=12)

    if not started:
        print("Telegram bridge already appears to be running.")
        return 0
    print("Started Telegram bridge services: " + ", ".join(started))
    status(argparse.Namespace())
    return 0


def stop(_: argparse.Namespace) -> int:
    stopped: list[int] = []
    discovered_pids = [int(item["pid"]) for item in _find_bridge_processes(enabled=True) if item.get("pid")]
    heartbeat_pids = []
    for service in ("telegram_notifier", "telegram_gateway"):
        record = _read_json(HEARTBEAT_DIR / f"{service}.json", {})
        if isinstance(record, dict) and record.get("pid"):
            heartbeat_pids.append(int(record["pid"]))
    pid_candidates = [_pid_from_file(NOTIFIER_PID), _pid_from_file(GATEWAY_PID), *heartbeat_pids, *discovered_pids]
    for pid in dict.fromkeys(pid_candidates):
        if not _process_exists(pid):
            continue
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
            cwd=str(BRIDGE_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if pid:
            stopped.append(pid)
    for service in ("telegram_notifier", "telegram_gateway"):
        heartbeat_path = HEARTBEAT_DIR / f"{service}.json"
        if heartbeat_path.exists():
            try:
                record = _read_json(heartbeat_path, {})
                if isinstance(record, dict):
                    record["status"] = "stopping"
                    record["timestamp"] = time.time() - (HEARTBEAT_STALE_AFTER_SECONDS + 1)
                    heartbeat_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            except Exception:
                pass
    print("Stopped bridge processes: " + (", ".join(str(pid) for pid in stopped) if stopped else "none"))
    return 0


def restart(args: argparse.Namespace) -> int:
    stop(args)
    time.sleep(1)
    return start(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the local Telegram bridge.")
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--discover-processes", action="store_true", help="Include best-effort Win32 process command-line discovery.")
    status_parser.set_defaults(func=status)
    sub.add_parser("chats").set_defaults(func=chats)
    sub.add_parser("threads").set_defaults(func=threads)
    sub.add_parser("voice-status").set_defaults(func=voice_status)
    sub.add_parser("start").set_defaults(func=start)
    sub.add_parser("stop").set_defaults(func=stop)
    sub.add_parser("restart").set_defaults(func=restart)

    use_thread_parser = sub.add_parser("use-thread")
    use_thread_parser.add_argument("ref")
    use_thread_parser.set_defaults(func=use_thread)

    alias_thread_parser = sub.add_parser("alias-thread")
    alias_thread_parser.add_argument("ref")
    alias_thread_parser.add_argument("alias")
    alias_thread_parser.set_defaults(func=alias_thread)

    use_chat_parser = sub.add_parser("use-chat")
    use_chat_parser.add_argument("ref")
    use_chat_parser.set_defaults(func=use_chat)

    alias_chat_parser = sub.add_parser("alias-chat")
    alias_chat_parser.add_argument("ref")
    alias_chat_parser.add_argument("alias")
    alias_chat_parser.set_defaults(func=alias_chat)

    logs_parser = sub.add_parser("logs")
    logs_parser.add_argument("--lines", type=int, default=80)
    logs_parser.add_argument("--path", default="")
    logs_parser.set_defaults(func=logs)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
