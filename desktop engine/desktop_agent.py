#! python3
"""Tool-based desktop agent for local OS actions."""

from __future__ import annotations

import ctypes
import csv
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ctypes import wintypes


# Prefer normal installed packages. Keep a local fallback for older setups that
# still have project-vendored dependencies lying around.
ENGINE_ROOT = Path(__file__).resolve().parent
VENDOR_DIR = ENGINE_ROOT / "_vendor"
if VENDOR_DIR.exists():
    vendor_str = str(VENDOR_DIR)
    if vendor_str not in sys.path:
        sys.path.insert(0, vendor_str)

mss = None


INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


COMMON_URLS = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "telegram": "https://web.telegram.org",
    "whatsapp": "whatsapp:",
    "github": "https://github.com",
}

APP_ALIASES = {
    "appstudio": r"C:\Program Files (x86)\ABB\AppStudio\AppStudio.Desktop.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "wordpad": "write.exe",
    "explorer": "explorer.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "whatsapp": "whatsapp:",
}

KEY_ALIASES = {
    "enter": "{ENTER}",
    "tab": "{TAB}",
    "escape": "{ESC}",
    "esc": "{ESC}",
    "space": " ",
    "backspace": "{BACKSPACE}",
    "delete": "{DELETE}",
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
}


@dataclass
class AgentAction:
    kind: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass
class AgentResult:
    handled: bool
    message: str
    actions: list[AgentAction] = field(default_factory=list)
    task_id: str | None = None


@dataclass
class TaskSession:
    task_id: str
    session_key: str
    request: str
    status: str
    created_at: float
    updated_at: float
    action_history: list[dict[str, Any]] = field(default_factory=list)
    last_message: str = ""
    last_observation: str = ""
    require_screenshot: bool = False


class LocalDesktopExecutor:
    def __init__(self, cwd: Path, artifacts_dir: Path) -> None:
        self.cwd = cwd
        self.artifacts_dir = artifacts_dir
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.default_drag_steps = max(4, int(os.getenv("DESKTOP_AGENT_DRAG_STEPS", "8")))
        self.default_drag_duration_ms = max(80, int(os.getenv("DESKTOP_AGENT_DRAG_DURATION_MS", "240")))
        self.focus_restore_delay_ms = max(10, int(os.getenv("DESKTOP_AGENT_FOCUS_RESTORE_DELAY_MS", "60")))
        self.focus_retry_delay_ms = max(10, int(os.getenv("DESKTOP_AGENT_FOCUS_RETRY_DELAY_MS", "25")))
        self._mss_mod = None
        self.preview_backend_reason = ""
        self.preview_enabled = self._load_preview_backend()
        self.preview_interval_ms = max(100, int(os.getenv("DESKTOP_AGENT_PREVIEW_INTERVAL_MS", "300")))
        self.preview_idle_interval_ms = max(
            self.preview_interval_ms,
            int(os.getenv("DESKTOP_AGENT_PREVIEW_IDLE_INTERVAL_MS", "1200")),
        )
        self.preview_active_for_ms = max(
            self.preview_interval_ms,
            int(os.getenv("DESKTOP_AGENT_PREVIEW_ACTIVE_FOR_MS", "5000")),
        )
        self.preview_source = (os.getenv("DESKTOP_AGENT_PREVIEW_SOURCE", "active_monitor") or "active_monitor").strip().lower()
        if self.preview_source not in {"screen", "window", "active_monitor"}:
            self.preview_source = "active_monitor"
        self.preview_max_width = max(160, int(os.getenv("DESKTOP_AGENT_PREVIEW_MAX_WIDTH", "480")))
        self.preview_max_height = max(120, int(os.getenv("DESKTOP_AGENT_PREVIEW_MAX_HEIGHT", "300")))
        self.preview_persist_frames = os.getenv("DESKTOP_AGENT_PREVIEW_PERSIST_TO_DISK", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.preview_paths = [
            self.artifacts_dir / "preview-a.png",
            self.artifacts_dir / "preview-b.png",
        ]
        self.preview_path = self.preview_paths[0]
        self.preview_meta_path = self.artifacts_dir / "preview-latest.json"
        self._preview_stop = threading.Event()
        self._preview_thread: threading.Thread | None = None
        self._preview_lock = threading.Lock()
        self._latest_preview: dict[str, Any] = {}
        self._latest_preview_frame: dict[str, Any] | None = None
        self._latest_preview_png: bytes | None = None
        self._preview_target_handle: int | None = None
        self._preview_target_locked_until = 0.0
        self._preview_slot = 0
        self._process_name_cache: dict[int, tuple[float, str]] = {}
        self._preview_active_until = 0.0

    def execute(self, action: AgentAction) -> str:
        self._mark_preview_activity()
        handlers = {
            "open_url": self._exec_open_url,
            "open_path": self._exec_open_path,
            "open_app": self._exec_open_app,
            "open_in_app": self._exec_open_in_app,
            "run_command": self._exec_run_command,
            "type_text": self._exec_type_text,
            "send_keys": self._exec_send_keys,
            "focus_window": self._exec_focus_window,
            "click": self._exec_click,
            "click_preview": self._exec_click_preview,
            "double_click": self._exec_double_click,
            "double_click_preview": self._exec_double_click_preview,
            "right_click": self._exec_right_click,
            "right_click_preview": self._exec_right_click_preview,
            "move_mouse": self._exec_move_mouse,
            "move_mouse_preview": self._exec_move_mouse_preview,
            "mouse_down": self._exec_mouse_down,
            "mouse_up": self._exec_mouse_up,
            "drag_mouse": self._exec_drag_mouse,
            "drag_mouse_preview": self._exec_drag_mouse_preview,
            "scroll": self._exec_scroll,
            "wait": self._exec_wait,
            "screenshot": self._exec_screenshot,
            "window_screenshot": self._exec_window_screenshot,
            "interaction_screenshot": self._exec_interaction_screenshot,
            "list_windows": self._exec_list_windows,
            "active_window": self._exec_active_window,
            "window_bounds": self._exec_window_bounds,
            "cursor_position": self._exec_cursor_position,
            "screen_size": self._exec_screen_size,
            "session_info": self._exec_session_info,
            "preview_status": self._exec_preview_status,
            "refresh_preview": self._exec_refresh_preview,
            "message": self._exec_message,
        }
        return handlers[action.kind](action.params)

    def execute_many(self, actions: list[AgentAction]) -> list[str]:
        return [self.execute(action) for action in actions]

    def _exec_open_url(self, params: dict[str, object]) -> str:
        url = str(params["url"])
        os.startfile(url)
        return f"Opened in the default browser: {url}"

    def _exec_open_path(self, params: dict[str, object]) -> str:
        path = Path(str(params["path"]))
        if path.is_dir():
            subprocess.Popen(["explorer.exe", str(path)], cwd=str(self.cwd))
            return f"Opened folder: {path}"
        os.startfile(str(path))
        return f"Opened file: {path}"

    def _exec_open_app(self, params: dict[str, object]) -> str:
        app = str(params["app"]).strip()
        launch = self._resolve_app_launch(app)
        if not launch:
            raise FileNotFoundError(app)
        kind = launch["kind"]
        target = launch["target"]
        if kind == "command":
            if re.match(r"^[a-z][a-z0-9+.-]*:$", target, flags=re.IGNORECASE):
                os.startfile(target)
            else:
                subprocess.Popen([target], cwd=str(self.cwd))
        elif kind == "path":
            os.startfile(target)
        elif kind == "appid":
            self._powershell(f"Start-Process 'shell:AppsFolder\\{target}'")
        else:
            raise FileNotFoundError(app)
        return f"Started {app}."

    def _exec_open_in_app(self, params: dict[str, object]) -> str:
        path = str(params["path"])
        app = str(params["app"]).strip()
        launch = self._resolve_app_launch(app)
        if not launch:
            raise FileNotFoundError(app)
        kind = launch["kind"]
        target = launch["target"]
        if kind in {"command", "path"}:
            subprocess.Popen([target, path], cwd=str(self.cwd))
        else:
            raise RuntimeError(f"Opening files in {app} is not supported through this launch path yet.")
        return f"Opened in {app}: {path}"

    def _exec_run_command(self, params: dict[str, object]) -> str:
        command = str(params["command"])
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=str(self.cwd),
            text=True,
            capture_output=True,
            timeout=120,
        )
        output = (completed.stdout or completed.stderr or "").strip() or "Command finished with no text output."
        if len(output) > 3000:
            output = output[:3000] + "\n...\nOutput truncated."
        if completed.returncode == 0:
            return f"Command completed.\n\n{output}"
        return f"Command failed with exit code {completed.returncode}.\n\n{output}"

    def _exec_type_text(self, params: dict[str, object]) -> str:
        text = str(params["text"])
        payload = self._escape_sendkeys_text(text)
        self._powershell(f"(New-Object -ComObject WScript.Shell).SendKeys('{payload}')")
        return f"Typed text: {text}"

    def _exec_send_keys(self, params: dict[str, object]) -> str:
        keys = str(params["keys"])
        self._powershell(f"(New-Object -ComObject WScript.Shell).SendKeys('{keys}')")
        return f"Sent keys: {keys}"

    def _exec_focus_window(self, params: dict[str, object]) -> str:
        title = str(params["title"])
        target = self._find_window(title)
        if not target:
            raise RuntimeError("Window not found.")
        self._restore_and_focus_window(target)
        self._preview_target_handle = int(target["Handle"])
        self._preview_target_locked_until = time.time() + 10
        return f"Focused window matching: {title}"

    def _exec_click(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        self._set_cursor_pos(x, y)
        self._send_mouse_event(MOUSEEVENTF_LEFTDOWN)
        self._send_mouse_event(MOUSEEVENTF_LEFTUP)
        return f"Clicked at {x}, {y}."

    def _exec_click_preview(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        screen_x, screen_y = self._preview_point_to_screen(x, y, refresh=True)
        self._exec_click({"x": screen_x, "y": screen_y})
        return f"Clicked at preview-relative {x}, {y} (screen {screen_x}, {screen_y})."

    def _exec_double_click(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        self._set_cursor_pos(x, y)
        self._send_mouse_event(MOUSEEVENTF_LEFTDOWN)
        self._send_mouse_event(MOUSEEVENTF_LEFTUP)
        time.sleep(0.08)
        self._send_mouse_event(MOUSEEVENTF_LEFTDOWN)
        self._send_mouse_event(MOUSEEVENTF_LEFTUP)
        return f"Double-clicked at {x}, {y}."

    def _exec_double_click_preview(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        screen_x, screen_y = self._preview_point_to_screen(x, y, refresh=True)
        self._exec_double_click({"x": screen_x, "y": screen_y})
        return f"Double-clicked at preview-relative {x}, {y} (screen {screen_x}, {screen_y})."

    def _exec_right_click(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        self._set_cursor_pos(x, y)
        self._send_mouse_event(MOUSEEVENTF_RIGHTDOWN)
        self._send_mouse_event(MOUSEEVENTF_RIGHTUP)
        return f"Right-clicked at {x}, {y}."

    def _exec_right_click_preview(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        screen_x, screen_y = self._preview_point_to_screen(x, y, refresh=True)
        self._exec_right_click({"x": screen_x, "y": screen_y})
        return f"Right-clicked at preview-relative {x}, {y} (screen {screen_x}, {screen_y})."

    def _exec_move_mouse(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        self._set_cursor_pos(x, y)
        return f"Moved the mouse to {x}, {y}."

    def _exec_move_mouse_preview(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        screen_x, screen_y = self._preview_point_to_screen(x, y, refresh=True)
        self._mouse_script(screen_x, screen_y, [])
        return f"Moved the mouse to preview-relative {x}, {y} (screen {screen_x}, {screen_y})."

    def _exec_mouse_down(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        self._set_cursor_pos(x, y)
        self._send_mouse_event(MOUSEEVENTF_LEFTDOWN)
        return f"Held the left mouse button at {x}, {y}."

    def _exec_mouse_up(self, params: dict[str, object]) -> str:
        x = int(params["x"])
        y = int(params["y"])
        self._set_cursor_pos(x, y)
        self._send_mouse_event(MOUSEEVENTF_LEFTUP)
        return f"Released the left mouse button at {x}, {y}."

    def _exec_drag_mouse(self, params: dict[str, object]) -> str:
        start_x = int(params["start_x"])
        start_y = int(params["start_y"])
        end_x = int(params["end_x"])
        end_y = int(params["end_y"])
        steps = max(4, int(params.get("steps", self.default_drag_steps)))
        duration_ms = max(80, int(params.get("duration_ms", self.default_drag_duration_ms)))
        hold_start_ms = max(20, int(params.get("hold_start_ms", self.focus_restore_delay_ms)))
        hold_after_down_ms = max(0, int(params.get("hold_after_down_ms", "120")))
        step_sleep = max(1, duration_ms // steps)
        self._set_cursor_pos(start_x, start_y)
        time.sleep(hold_start_ms / 1000)
        self._send_mouse_event(MOUSEEVENTF_LEFTDOWN)
        if hold_after_down_ms:
            time.sleep(hold_after_down_ms / 1000)
        for index in range(1, steps + 1):
            x = round(start_x + (end_x - start_x) * (index / steps))
            y = round(start_y + (end_y - start_y) * (index / steps))
            self._set_cursor_pos(x, y)
            time.sleep(step_sleep / 1000)
        self._send_mouse_event(MOUSEEVENTF_LEFTUP)
        return f"Dragged the mouse from {start_x}, {start_y} to {end_x}, {end_y}."

    def _exec_drag_mouse_preview(self, params: dict[str, object]) -> str:
        start_x = int(params["start_x"])
        start_y = int(params["start_y"])
        end_x = int(params["end_x"])
        end_y = int(params["end_y"])
        screen_start_x, screen_start_y = self._preview_point_to_screen(start_x, start_y, refresh=True)
        screen_end_x, screen_end_y = self._preview_point_to_screen(end_x, end_y)
        preview_action = AgentAction(
            "drag_mouse",
            {
                "start_x": screen_start_x,
                "start_y": screen_start_y,
                "end_x": screen_end_x,
                "end_y": screen_end_y,
                "steps": params.get("steps", self.default_drag_steps),
                "duration_ms": params.get("duration_ms", self.default_drag_duration_ms),
                "hold_start_ms": params.get("hold_start_ms", self.focus_restore_delay_ms),
                "hold_after_down_ms": params.get("hold_after_down_ms", 120),
            },
        )
        self._exec_drag_mouse(preview_action.params)
        return (
            "Dragged the mouse from preview-relative "
            f"{start_x}, {start_y} to {end_x}, {end_y} "
            f"(screen {screen_start_x}, {screen_start_y} to {screen_end_x}, {screen_end_y})."
        )

    def _exec_scroll(self, params: dict[str, object]) -> str:
        amount = int(params["amount"])
        self._send_mouse_event(MOUSEEVENTF_WHEEL, data=amount)
        direction = "down" if amount < 0 else "up"
        return f"Scrolled {direction} by {abs(amount)}."

    def _exec_wait(self, params: dict[str, object]) -> str:
        seconds = float(params["seconds"])
        time.sleep(seconds)
        return f"Waited {seconds:g} seconds."

    def _exec_screenshot(self, params: dict[str, object]) -> str:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.artifacts_dir / f"screenshot-{timestamp}.png"
        if self.preview_enabled:
            try:
                self._capture_screen_fast(target)
            except Exception:
                script = self._region_capture_script(0, 0, None, None, str(target), "Png", use_primary_screen=True)
                self._powershell(script)
        else:
            script = self._region_capture_script(0, 0, None, None, str(target), "Png", use_primary_screen=True)
            self._powershell(script)
        return f"Saved screenshot: {target}"

    def _exec_window_screenshot(self, params: dict[str, object]) -> str:
        window = None
        title = str(params.get("title", "")).strip()
        if title:
            window = self._find_window(title)
        else:
            hwnd = int(self._user32.GetForegroundWindow())
            if hwnd:
                for item in self._visible_windows(include_minimized=True):
                    if item["Handle"] == hwnd:
                        window = item
                        break
        if not window:
            raise RuntimeError("Window not found for screenshot.")
        width = max(1, int(window["Width"]))
        height = max(1, int(window["Height"]))
        left = int(window["Left"])
        top = int(window["Top"])
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.artifacts_dir / f"window-screenshot-{timestamp}.png"
        if self.preview_enabled:
            copied = False
            with self._preview_lock:
                cached = dict(self._latest_preview)
                cached_frame = dict(self._latest_preview_frame or {})
            if not title and cached:
                if int(cached.get("Handle", -1)) == int(window["Handle"]):
                    if cached_frame.get("rgb") and cached_frame.get("size"):
                        self._save_png_bytes(
                            cached_frame["rgb"],
                            tuple(cached_frame["size"]),
                            target,
                        )
                        copied = True
                    elif cached.get("path") and Path(str(cached["path"])).exists():
                        shutil.copyfile(str(cached["path"]), target)
                        copied = True
            if not copied:
                try:
                    self._capture_region_fast(left, top, width, height, target, image_format="PNG")
                    copied = True
                except Exception:
                    script = self._region_capture_script(left, top, width, height, str(target), "Png")
                    self._powershell(script)
        else:
            script = self._region_capture_script(left, top, width, height, str(target), "Png")
            self._powershell(script)
        return f"Saved window screenshot: {target} ({width}x{height})."

    def _exec_interaction_screenshot(self, params: dict[str, object]) -> str:
        if not self.preview_enabled:
            raise RuntimeError(
                f"Interaction snapshot is unavailable because {self.preview_backend_reason or 'fast preview is disabled.'}"
            )
        self._mark_preview_activity()
        cached = self._capture_active_window_preview() if self._preview_target_handle else self._capture_preview_frame()
        if not cached:
            raise RuntimeError("No interaction preview is available yet.")
        with self._preview_lock:
            preview = dict(self._latest_preview)
            preview_png = bytes(self._latest_preview_png) if self._latest_preview_png is not None else None
        if not preview or not preview_png:
            raise RuntimeError("Interaction preview image is not ready yet.")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.artifacts_dir / f"interaction-screenshot-{timestamp}.png"
        target.write_bytes(preview_png)
        preview_width = int(preview.get("preview_width", preview.get("Width", 0)))
        preview_height = int(preview.get("preview_height", preview.get("Height", 0)))
        source_width = int(preview.get("Width", preview_width))
        source_height = int(preview.get("Height", preview_height))
        title = str(preview.get("Title", "Unknown"))
        return (
            f"Saved interaction screenshot: {target} "
            f"(interaction={preview_width}x{preview_height}, source={source_width}x{source_height}, title={title})."
        )

    def _exec_list_windows(self, params: dict[str, object]) -> str:
        data = self._visible_windows()[:20]
        if not data:
            return "No visible windows were found."
        lines = [
            f"{item['ProcessName']} ({item['ProcessId']}): {item['Title']}"
            + (" [minimized]" if item["Minimized"] else "")
            for item in data
        ]
        return "Visible windows:\n" + "\n".join(lines)

    def _exec_active_window(self, params: dict[str, object]) -> str:
        hwnd = int(self._user32.GetForegroundWindow())
        if not hwnd:
            return "Active window: Unknown"
        for item in self._visible_windows(include_minimized=True):
            if item["Handle"] == hwnd:
                return f"Active window: {item['ProcessName']}: {item['Title']}"
        return "Active window: Unknown"

    def _exec_window_bounds(self, params: dict[str, object]) -> str:
        title = str(params["title"])
        data = self._find_window(title)
        if not data:
            raise RuntimeError("Window not found.")
        return (
            f"Window bounds for {title}: "
            f"left={data['Left']}, top={data['Top']}, width={data['Width']}, height={data['Height']}"
        )

    def _exec_cursor_position(self, params: dict[str, object]) -> str:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$p=[System.Windows.Forms.Cursor]::Position; "
            'Write-Output ($p.X.ToString() + "," + $p.Y.ToString())'
        )
        output = self._powershell_capture(script).strip()
        return f"Cursor position: {output}"

    def _exec_screen_size(self, params: dict[str, object]) -> str:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            'Write-Output ($b.Width.ToString() + "x" + $b.Height.ToString())'
        )
        output = self._powershell_capture(script).strip()
        return f"Screen size: {output}"

    def _exec_session_info(self, params: dict[str, object]) -> str:
        script = (
            "$p = Get-Process -Id $PID; "
            "$explorer = Get-Process explorer -ErrorAction SilentlyContinue | Select-Object -First 1 Id,SessionId,ProcessName; "
            "$me = [pscustomobject]@{ ProcessId=$p.Id; SessionId=$p.SessionId; ProcessName=$p.ProcessName; UserName=$env:USERNAME }; "
            'Write-Output ("Broker process: " + ($me | ConvertTo-Json -Compress)); '
            'if ($explorer) { Write-Output ("Explorer: " + ($explorer | ConvertTo-Json -Compress)) } else { Write-Output "Explorer: none" }'
        )
        output = self._powershell_capture(script).strip()
        return output

    def _exec_preview_status(self, params: dict[str, object]) -> str:
        payload = self.preview_status_payload()
        if not payload["enabled"]:
            reason = payload.get("reason") or "fast local image libraries are not installed."
            return f"Preview cache is unavailable because {reason}"
        if not payload["ready"]:
            return "Preview cache is enabled but no frame is cached yet."
        return (
            "Preview cache is ready: "
            f"mode={payload.get('source_mode', self.preview_source)}, "
            f"title={payload.get('title', 'Unknown')}, "
            f"source={payload.get('width', '?')}x{payload.get('height', '?')}, "
            f"cached={payload.get('preview_width', payload.get('width', '?'))}x{payload.get('preview_height', payload.get('height', '?'))}, "
            f"age_ms={payload.get('age_ms', '?')}, "
            f"path={payload.get('path', self.preview_path)}"
        )

    def _exec_refresh_preview(self, params: dict[str, object]) -> str:
        if not self.preview_enabled:
            return f"Preview cache is unavailable because {self.preview_backend_reason or 'fast local image libraries are not installed.'}"
        self._mark_preview_activity()
        if self._preview_target_handle:
            cached = self._capture_active_window_preview()
        else:
            cached = self._capture_preview_frame()
        if not cached:
            return "Preview refresh could not capture the current view."
        return f"Refreshed preview cache: {cached.get('Title', 'Unknown')}"

    def preview_status_payload(self) -> dict[str, Any]:
        if not self.preview_enabled:
            return {"enabled": False, "ready": False, "reason": self.preview_backend_reason}
        with self._preview_lock:
            cached = dict(self._latest_preview)
        if not cached:
            return {
                "enabled": True,
                "ready": False,
                "interval_ms": self.preview_interval_ms,
                "idle_interval_ms": self.preview_idle_interval_ms,
                "active_for_ms": self.preview_active_for_ms,
                "source_mode": self.preview_source,
                "format": "png",
                "preview_max_width": self.preview_max_width,
                "preview_max_height": self.preview_max_height,
            }
        return {
            "enabled": True,
            "ready": True,
            "interval_ms": self.preview_interval_ms,
            "idle_interval_ms": self.preview_idle_interval_ms,
            "active_for_ms": self.preview_active_for_ms,
            "source_mode": self.preview_source,
            "format": "png",
            "preview_max_width": self.preview_max_width,
            "preview_max_height": self.preview_max_height,
            "title": cached.get("Title", "Unknown"),
            "width": cached.get("Width"),
            "height": cached.get("Height"),
            "preview_width": cached.get("preview_width", cached.get("Width")),
            "preview_height": cached.get("preview_height", cached.get("Height")),
            "captured_at": cached.get("captured_at", 0),
            "precision_mode": "full-frame-in-memory",
            "path": cached.get("path", "memory://preview"),
            "age_ms": max(0, int((time.time() - float(cached.get("captured_at", 0))) * 1000)),
        }

    def _exec_message(self, params: dict[str, object]) -> str:
        return str(params["text"])

    def _set_cursor_pos(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(int(x), int(y))

    def _send_mouse_event(self, flags: int, data: int = 0) -> None:
        mouse_input = MOUSEINPUT(0, 0, int(data), int(flags), 0, None)
        input_union = INPUTUNION()
        input_union.mi = mouse_input
        input_struct = INPUT(INPUT_MOUSE, input_union)
        sent = self._user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        if sent != 1:
            raise RuntimeError(f"SendInput failed for mouse flags {flags}.")

    def _mouse_script(self, x: int, y: int, steps: list[str]) -> None:
        script = self._native_mouse_prelude() + f"; [NativeMouse]::SetCursorPos({x}, {y}) | Out-Null"
        if steps:
            script += "; " + "; ".join(steps)
        self._powershell(script)

    def _native_mouse_prelude(self) -> str:
        return (
            "Add-Type @'\n"
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public static class NativeMouse {\n"
            "  [DllImport(\"user32.dll\")] public static extern bool SetCursorPos(int X, int Y);\n"
            "  [DllImport(\"user32.dll\")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, int dwData, UIntPtr dwExtraInfo);\n"
            "}\n"
            "'@"
        )

    def _region_capture_script(
        self,
        left: int,
        top: int,
        width: int | None,
        height: int | None,
        target: str,
        image_format: str,
        use_primary_screen: bool = False,
    ) -> str:
        escaped = target.replace("\\", "\\\\")
        if use_primary_screen:
            bounds_script = "$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            width_expr = "$bounds.Width"
            height_expr = "$bounds.Height"
            src_expr = "$bounds.Location"
        else:
            bounds_script = (
                f"$left = {left}; $top = {top}; $width = {max(1, width or 1)}; $height = {max(1, height or 1)}; "
                "$bounds = New-Object System.Drawing.Rectangle $left, $top, $width, $height; "
            )
            width_expr = "$width"
            height_expr = "$height"
            src_expr = "New-Object System.Drawing.Point $left, $top"
        return (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            + bounds_script
            + f"$bmp = New-Object System.Drawing.Bitmap {width_expr}, {height_expr}; "
            "$gfx = [System.Drawing.Graphics]::FromImage($bmp); "
            f"$gfx.CopyFromScreen({src_expr}, [System.Drawing.Point]::Empty, $bounds.Size); "
            f"$bmp.Save('{escaped}', [System.Drawing.Imaging.ImageFormat]::{image_format}); "
            "$gfx.Dispose(); $bmp.Dispose()"
        )

    def start_preview_cache(self) -> bool:
        if not self.preview_enabled:
            return False
        if self._preview_thread and self._preview_thread.is_alive():
            return True
        self._preview_stop.clear()
        self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self._preview_thread.start()
        return True

    def stop_preview_cache(self) -> None:
        self._preview_stop.set()
        if self._preview_thread and self._preview_thread.is_alive():
            self._preview_thread.join(timeout=1.5)

    def _preview_loop(self) -> None:
        while True:
            interval_ms = self._current_preview_interval_ms()
            if self._preview_stop.wait(interval_ms / 1000):
                break
            try:
                self._capture_preview_frame()
            except Exception:
                continue

    def _current_preview_interval_ms(self) -> int:
        if time.time() < self._preview_active_until:
            return self.preview_interval_ms
        return self.preview_idle_interval_ms

    def _mark_preview_activity(self) -> None:
        self._preview_active_until = max(self._preview_active_until, time.time() + (self.preview_active_for_ms / 1000))

    def latest_preview_png_bytes(self) -> bytes | None:
        with self._preview_lock:
            if self._latest_preview_png is None:
                return None
            return bytes(self._latest_preview_png)

    def _capture_preview_frame(self) -> dict[str, Any] | None:
        if self._preview_target_handle and time.time() < self._preview_target_locked_until:
            return self._capture_active_window_preview()
        if self.preview_source == "window":
            return self._capture_active_window_preview()
        if self.preview_source == "active_monitor":
            return self._capture_active_monitor_preview()
        return self._capture_screen_preview()

    def _capture_active_window_preview(self) -> dict[str, Any] | None:
        if not self.preview_enabled:
            return None
        window = self._preferred_preview_window()
        if not window:
            return None
        if int(window["Width"]) <= 1 or int(window["Height"]) <= 1:
            return None
        temp_path = self.artifacts_dir / f"preview-write-{self._preview_slot}.tmp.png"
        try:
            with self._mss_mod.mss() as sct:
                bbox = {
                    "left": int(window["Left"]),
                    "top": int(window["Top"]),
                    "width": int(window["Width"]),
                    "height": int(window["Height"]),
                }
                shot = sct.grab(bbox)
                preview_rgb, preview_width, preview_height = self._downsample_rgb(
                    shot.rgb,
                    int(shot.size.width),
                    int(shot.size.height),
                    max_width=self.preview_max_width,
                    max_height=self.preview_max_height,
                )
                preview_png = self._png_bytes(preview_rgb, (preview_width, preview_height))
                if self.preview_persist_frames:
                    temp_path.write_bytes(preview_png)
                full_frame = {
                    "rgb": shot.rgb,
                    "size": (int(shot.size.width), int(shot.size.height)),
                    "captured_at": time.time(),
                    "Handle": int(window["Handle"]),
                }
        except Exception:
            return None
        final_path = self._rotate_preview_path(temp_path) if self.preview_persist_frames else None
        payload = {
            **window,
            "captured_at": time.time(),
            "path": str(final_path) if final_path else "memory://preview",
            "preview_width": preview_width,
            "preview_height": preview_height,
        }
        with self._preview_lock:
            if final_path:
                self.preview_path = final_path
            self._latest_preview = payload
            self._latest_preview_frame = full_frame
            self._latest_preview_png = preview_png
        self.preview_meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _capture_screen_preview(self) -> dict[str, Any] | None:
        if not self.preview_enabled:
            return None
        temp_path = self.artifacts_dir / f"preview-write-{self._preview_slot}.tmp.png"
        try:
            with self._mss_mod.mss() as sct:
                monitor = dict(sct.monitors[1])
                shot = sct.grab(monitor)
                preview_rgb, preview_width, preview_height = self._downsample_rgb(
                    shot.rgb,
                    int(shot.size.width),
                    int(shot.size.height),
                    max_width=self.preview_max_width,
                    max_height=self.preview_max_height,
                )
                preview_png = self._png_bytes(preview_rgb, (preview_width, preview_height))
                if self.preview_persist_frames:
                    temp_path.write_bytes(preview_png)
                full_frame = {
                    "rgb": shot.rgb,
                    "size": (int(shot.size.width), int(shot.size.height)),
                    "captured_at": time.time(),
                    "Handle": None,
                }
        except Exception:
            return None
        final_path = self._rotate_preview_path(temp_path) if self.preview_persist_frames else None
        payload = {
            "Handle": 0,
            "ProcessId": 0,
            "ProcessName": "desktop",
            "Title": "Full desktop",
            "Left": int(monitor.get("left", 0)),
            "Top": int(monitor.get("top", 0)),
            "Right": int(monitor.get("left", 0)) + int(shot.size.width),
            "Bottom": int(monitor.get("top", 0)) + int(shot.size.height),
            "Width": int(shot.size.width),
            "Height": int(shot.size.height),
            "Minimized": False,
            "Visible": True,
            "captured_at": time.time(),
            "path": str(final_path) if final_path else "memory://preview",
            "preview_width": preview_width,
            "preview_height": preview_height,
        }
        with self._preview_lock:
            if final_path:
                self.preview_path = final_path
            self._latest_preview = payload
            self._latest_preview_frame = full_frame
            self._latest_preview_png = preview_png
        self.preview_meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _capture_active_monitor_preview(self) -> dict[str, Any] | None:
        if not self.preview_enabled:
            return None
        window = self._preferred_preview_window()
        if not window:
            return self._capture_screen_preview()
        temp_path = self.artifacts_dir / f"preview-write-{self._preview_slot}.tmp.png"
        try:
            with self._mss_mod.mss() as sct:
                monitor = self._monitor_for_window(sct.monitors[1:], window)
                shot = sct.grab(monitor)
                preview_rgb, preview_width, preview_height = self._downsample_rgb(
                    shot.rgb,
                    int(shot.size.width),
                    int(shot.size.height),
                    max_width=self.preview_max_width,
                    max_height=self.preview_max_height,
                )
                preview_png = self._png_bytes(preview_rgb, (preview_width, preview_height))
                if self.preview_persist_frames:
                    temp_path.write_bytes(preview_png)
                full_frame = {
                    "rgb": shot.rgb,
                    "size": (int(shot.size.width), int(shot.size.height)),
                    "captured_at": time.time(),
                    "Handle": int(window["Handle"]),
                }
        except Exception:
            return None
        final_path = self._rotate_preview_path(temp_path) if self.preview_persist_frames else None
        payload = {
            "Handle": int(window["Handle"]),
            "ProcessId": int(window["ProcessId"]),
            "ProcessName": "desktop",
            "Title": f"Monitor of {window['Title']}",
            "Left": int(monitor.get("left", 0)),
            "Top": int(monitor.get("top", 0)),
            "Right": int(monitor.get("left", 0)) + int(shot.size.width),
            "Bottom": int(monitor.get("top", 0)) + int(shot.size.height),
            "Width": int(shot.size.width),
            "Height": int(shot.size.height),
            "Minimized": False,
            "Visible": True,
            "captured_at": time.time(),
            "path": str(final_path) if final_path else "memory://preview",
            "preview_width": preview_width,
            "preview_height": preview_height,
        }
        with self._preview_lock:
            if final_path:
                self.preview_path = final_path
            self._latest_preview = payload
            self._latest_preview_frame = full_frame
            self._latest_preview_png = preview_png
        self.preview_meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _preferred_preview_window(self) -> dict[str, Any] | None:
        target_handle = self._preview_target_handle or 0
        if target_handle:
            target = self._window_info_for_handle(int(target_handle))
            if target and not bool(target.get("Minimized")):
                return target
        hwnd = int(self._user32.GetForegroundWindow())
        if not hwnd:
            return None
        window = self._window_info_for_handle(hwnd)
        if window:
            self._preview_target_handle = int(window["Handle"])
        return window

    def _preview_point_to_screen(self, x: int, y: int, refresh: bool = False) -> tuple[int, int]:
        if refresh and self._preview_target_handle:
            self._mark_preview_activity()
            fresh = self._capture_active_window_preview()
            if fresh:
                self._preview_target_locked_until = time.time() + 10
        with self._preview_lock:
            preview = dict(self._latest_preview)
        if not preview:
            raise RuntimeError("No preview frame is available for preview-relative mouse control.")
        source_width = max(1, int(preview.get("Width", 0)))
        source_height = max(1, int(preview.get("Height", 0)))
        preview_width = max(1, int(preview.get("preview_width", source_width)))
        preview_height = max(1, int(preview.get("preview_height", source_height)))
        left = int(preview.get("Left", 0))
        top = int(preview.get("Top", 0))
        if x < 0 or y < 0 or x >= preview_width or y >= preview_height:
            title = str(preview.get("Title", "Unknown"))
            raise RuntimeError(
                "Preview-relative coordinates are outside the interaction view: "
                f"got ({x}, {y}), but the current interaction view is {preview_width}x{preview_height} "
                f"for '{title}'. Use the interaction screenshot or preview dimensions, not the full evidence screenshot."
            )
        source_x = round(x * source_width / preview_width)
        source_y = round(y * source_height / preview_height)
        screen_x = left + min(max(source_x, 0), max(0, source_width - 1))
        screen_y = top + min(max(source_y, 0), max(0, source_height - 1))
        return screen_x, screen_y

    def _rotate_preview_path(self, temp_path: Path) -> Path:
        next_slot = (self._preview_slot + 1) % len(self.preview_paths)
        final_path = self.preview_paths[next_slot]
        os.replace(temp_path, final_path)
        self._preview_slot = next_slot
        return final_path

    def _capture_screen_fast(self, target: Path) -> None:
        if not self.preview_enabled:
            raise RuntimeError("Fast screen capture backend is unavailable.")
        with self._mss_mod.mss() as sct:
            monitor = dict(sct.monitors[1])
            shot = sct.grab(monitor)
            self._save_shot_png(shot, target)

    def _capture_region_fast(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        target: Path,
        image_format: str = "PNG",
        max_width: int | None = None,
        max_height: int | None = None,
    ) -> tuple[int, int]:
        if not self.preview_enabled:
            raise RuntimeError("Fast region capture backend is unavailable.")
        bbox = {
            "left": max(0, int(left)),
            "top": max(0, int(top)),
            "width": max(1, int(width)),
            "height": max(1, int(height)),
        }
        with self._mss_mod.mss() as sct:
            shot = sct.grab(bbox)
            if image_format.upper() != "PNG":
                raise RuntimeError(f"Unsupported fast image format: {image_format}")
            rgb = shot.rgb
            out_width = int(shot.size.width)
            out_height = int(shot.size.height)
            if max_width or max_height:
                rgb, out_width, out_height = self._downsample_rgb(
                    rgb,
                    int(shot.size.width),
                    int(shot.size.height),
                    max_width=max_width,
                    max_height=max_height,
                )
            self._save_png_bytes(rgb, (out_width, out_height), target)
            return out_width, out_height

    def _load_preview_backend(self) -> bool:
        try:
            self._mss_mod = importlib.import_module("mss")
        except Exception as exc:
            self.preview_backend_reason = f"mss import failed: {exc}"
            return False
        self.preview_backend_reason = ""
        return True

    def _save_shot_png(self, shot: Any, target: Path) -> None:
        self._save_png_bytes(shot.rgb, (int(shot.size.width), int(shot.size.height)), target)

    def _png_bytes(self, rgb: bytes, size: tuple[int, int]) -> bytes:
        tools = importlib.import_module("mss.tools")
        return tools.to_png(rgb, size)

    def _save_png_bytes(self, rgb: bytes, size: tuple[int, int], target: Path) -> None:
        target.write_bytes(self._png_bytes(rgb, size))

    def _downsample_rgb(
        self,
        rgb: bytes,
        width: int,
        height: int,
        max_width: int | None = None,
        max_height: int | None = None,
    ) -> tuple[bytes, int, int]:
        if width <= 0 or height <= 0:
            return rgb, width, height
        width_limit = max_width or width
        height_limit = max_height or height
        scale = max(width / max(1, width_limit), height / max(1, height_limit), 1.0)
        if scale <= 1.0:
            return rgb, width, height
        new_width = max(1, int(round(width / scale)))
        new_height = max(1, int(round(height / scale)))
        source = memoryview(rgb)
        row_stride = width * 3
        x_offsets = [min(width - 1, int(i * width / new_width)) * 3 for i in range(new_width)]
        out = bytearray(new_width * new_height * 3)
        write_index = 0
        for y in range(new_height):
            src_y = min(height - 1, int(y * height / new_height))
            row_start = src_y * row_stride
            for x_offset in x_offsets:
                idx = row_start + x_offset
                out[write_index : write_index + 3] = source[idx : idx + 3]
                write_index += 3
        return bytes(out), new_width, new_height

    def _monitor_for_window(self, monitors: list[Any], window: dict[str, Any]) -> dict[str, Any]:
        if not monitors:
            return {"left": 0, "top": 0, "width": int(window["Width"]), "height": int(window["Height"])}
        center_x = int(window["Left"]) + max(1, int(window["Width"])) // 2
        center_y = int(window["Top"]) + max(1, int(window["Height"])) // 2
        for monitor in monitors:
            left = int(monitor.get("left", 0))
            top = int(monitor.get("top", 0))
            width = int(monitor.get("width", 0))
            height = int(monitor.get("height", 0))
            if left <= center_x < left + width and top <= center_y < top + height:
                return dict(monitor)
        best = dict(monitors[0])
        best_overlap = -1
        w_left = int(window["Left"])
        w_top = int(window["Top"])
        w_right = w_left + int(window["Width"])
        w_bottom = w_top + int(window["Height"])
        for monitor in monitors:
            left = int(monitor.get("left", 0))
            top = int(monitor.get("top", 0))
            right = left + int(monitor.get("width", 0))
            bottom = top + int(monitor.get("height", 0))
            overlap_w = max(0, min(w_right, right) - max(w_left, left))
            overlap_h = max(0, min(w_bottom, bottom) - max(w_top, top))
            overlap = overlap_w * overlap_h
            if overlap > best_overlap:
                best_overlap = overlap
                best = dict(monitor)
        return best

    def _escape_sendkeys_text(self, text: str) -> str:
        escaped = text.replace("{", "{{}").replace("}", "{}}")
        for char in ["+", "^", "%", "~", "(", ")"]:
            escaped = escaped.replace(char, "{" + char + "}")
        escaped = escaped.replace("'", "''")
        return escaped

    def _powershell(self, script: str) -> None:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=str(self.cwd),
            text=True,
            capture_output=True,
            timeout=60,
            check=True,
        )

    def _powershell_capture(self, script: str) -> str:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=str(self.cwd),
            text=True,
            capture_output=True,
            timeout=60,
            check=True,
        )
        return completed.stdout.strip()

    def _visible_windows(self, include_minimized: bool = False) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        user32 = self._user32

        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        is_visible = user32.IsWindowVisible
        is_iconic = user32.IsIconic

        @EnumWindowsProc
        def callback(hwnd: int, lparam: int) -> bool:
            minimized = bool(is_iconic(hwnd))
            visible = bool(is_visible(hwnd))
            if not visible and not (include_minimized and minimized):
                return True
            info = self._window_info_for_handle(int(hwnd))
            if not info or not info["Title"]:
                return True
            info["Minimized"] = minimized
            info["Visible"] = visible
            windows.append(info)
            return True

        user32.EnumWindows(callback, 0)
        return windows

    def _process_name_for_pid(self, pid: int) -> str:
        if pid <= 0:
            return "unknown"
        now = time.time()
        cached = self._process_name_cache.get(pid)
        if cached and (now - cached[0]) < 5:
            return cached[1]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return "unknown"
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            if not ok:
                return "unknown"
            result = Path(buffer.value).stem or "unknown"
        finally:
            self._kernel32.CloseHandle(handle)
        self._process_name_cache[pid] = (now, result)
        return result

    def _window_info_for_handle(self, hwnd: int) -> dict[str, Any] | None:
        get_text_length = self._user32.GetWindowTextLengthW
        get_text = self._user32.GetWindowTextW
        get_pid = self._user32.GetWindowThreadProcessId
        get_rect = self._user32.GetWindowRect
        if hwnd <= 0:
            return None
        length = int(get_text_length(hwnd))
        if length <= 0:
            return None
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        get_text(hwnd, title_buffer, length + 1)
        title = title_buffer.value.strip()
        if not title:
            return None

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        pid = wintypes.DWORD()
        get_pid(hwnd, ctypes.byref(pid))
        rect = RECT()
        get_rect(hwnd, ctypes.byref(rect))
        return {
            "Handle": int(hwnd),
            "ProcessId": int(pid.value),
            "ProcessName": self._process_name_for_pid(int(pid.value)),
            "Title": title,
            "Left": int(rect.left),
            "Top": int(rect.top),
            "Right": int(rect.right),
            "Bottom": int(rect.bottom),
            "Width": int(rect.right - rect.left),
            "Height": int(rect.bottom - rect.top),
            "Minimized": bool(self._user32.IsIconic(hwnd)),
            "Visible": bool(self._user32.IsWindowVisible(hwnd)),
        }

    def _find_window(self, query: str) -> dict[str, Any] | None:
        lowered = query.strip().lower()
        windows = self._visible_windows(include_minimized=True)
        exact = [
            item
            for item in windows
            if item["Title"].lower() == lowered or item["ProcessName"].lower() == lowered
        ]
        if exact:
            return exact[0]
        partial = [
            item
            for item in windows
            if lowered in item["Title"].lower() or lowered in item["ProcessName"].lower()
        ]
        if partial:
            partial.sort(key=lambda item: (item["Minimized"], len(item["Title"])))
            return partial[0]
        return None

    def _restore_and_focus_window(self, window: dict[str, Any]) -> None:
        hwnd = int(window["Handle"])
        SW_RESTORE = 9
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002

        self._user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(self.focus_restore_delay_ms / 1000)
        ok = bool(self._user32.SetForegroundWindow(hwnd))
        if not ok:
            foreground = int(self._user32.GetForegroundWindow())
            current_thread = int(self._kernel32.GetCurrentThreadId())
            target_thread = int(self._user32.GetWindowThreadProcessId(hwnd, None))
            foreground_thread = int(self._user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
            attached_threads: list[int] = []
            try:
                for thread_id in {target_thread, foreground_thread}:
                    if thread_id and thread_id != current_thread:
                        if self._user32.AttachThreadInput(thread_id, current_thread, True):
                            attached_threads.append(thread_id)
                self._user32.BringWindowToTop(hwnd)
                self._user32.SetActiveWindow(hwnd)
                self._user32.SetFocus(hwnd)
                ok = bool(self._user32.SetForegroundWindow(hwnd))
            finally:
                for thread_id in attached_threads:
                    self._user32.AttachThreadInput(thread_id, current_thread, False)
        if not ok:
            # Nudging ALT often helps Windows allow foreground activation.
            self._user32.keybd_event(VK_MENU, 0, 0, 0)
            self._user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(self.focus_retry_delay_ms / 1000)
            ok = bool(self._user32.SetForegroundWindow(hwnd))
        if not ok and window["Title"]:
            escaped_title = window["Title"].replace("'", "''")
            self._powershell(f"$wshell = New-Object -ComObject WScript.Shell; [void]$wshell.AppActivate('{escaped_title}')")
            time.sleep(self.focus_restore_delay_ms / 1000)
            ok = bool(self._user32.SetForegroundWindow(hwnd))
        if not ok:
            raise RuntimeError("Window activation failed.")

    def _resolve_app_launch(self, app: str) -> dict[str, str] | None:
        lowered = app.lower().strip()

        alias = APP_ALIASES.get(lowered)
        if alias:
            return {"kind": "command", "target": alias}

        explicit = Path(os.path.expandvars(app)).expanduser()
        if explicit.exists():
            return {"kind": "path", "target": str(explicit)}

        if lowered.endswith(".exe"):
            return {"kind": "command", "target": app}

        start_app = self._find_start_app(lowered)
        if start_app:
            return start_app

        executable = self._find_executable_path(lowered)
        if executable:
            return executable

        return None

    def _find_start_app(self, query: str) -> dict[str, str] | None:
        escaped = query.replace("'", "''")
        script = (
            "$app = Get-StartApps | "
            f"Where-Object {{ $_.Name -match '{escaped}' -or $_.AppID -match '{escaped}' }} | "
            "Select-Object -First 1 Name,AppID | ConvertTo-Json -Compress"
        )
        output = self._powershell_capture(script)
        if not output:
            return None
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return None
        appid = data.get("AppID")
        if appid:
            return {"kind": "appid", "target": appid}
        return None

    def _find_executable_path(self, query: str) -> dict[str, str] | None:
        roots = [
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
        ]
        roots = [root for root in roots if root and os.path.isdir(root)]
        for root in roots:
            for current_root, dirs, files in os.walk(root):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d.lower() not in {"cache", "caches", "logs", "temp", "tmp", "packages"}
                ]
                for filename in files:
                    lowered = filename.lower()
                    stem = Path(filename).stem.lower()
                    if query in lowered or query in stem:
                        return {"kind": "path", "target": str(Path(current_root) / filename)}
        return None

    def _window_api_prelude(self) -> str:
        return (
            "Add-Type @'\n"
            "using System;\n"
            "using System.Text;\n"
            "using System.Runtime.InteropServices;\n"
            "using System.Diagnostics;\n"
            "public static class WindowApi {\n"
            "  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }\n"
            "  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n"
            "  [DllImport(\"user32.dll\")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);\n"
            "  [DllImport(\"user32.dll\")] public static extern int GetWindowTextLength(IntPtr hWnd);\n"
            "  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n"
            "  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);\n"
            "  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);\n"
            "}\n"
            "'@; "
            "function Get-VisibleWindows { "
            "  $items = New-Object System.Collections.Generic.List[object]; "
            "  $callback = [WindowApi+EnumWindowsProc]{ param($hWnd, $lParam) "
            "    if (-not [WindowApi]::IsWindowVisible($hWnd)) { return $true } "
            "    $len = [WindowApi]::GetWindowTextLength($hWnd); "
            "    if ($len -le 0) { return $true } "
            "    $sb = New-Object System.Text.StringBuilder ($len + 1); "
            "    [void][WindowApi]::GetWindowText($hWnd, $sb, $sb.Capacity); "
            "    $title = $sb.ToString(); "
            "    if ([string]::IsNullOrWhiteSpace($title)) { return $true } "
            "    [uint32]$pid = 0; "
            "    [void][WindowApi]::GetWindowThreadProcessId($hWnd, [ref]$pid); "
            "    if ($pid -le 0) { return $true } "
            "    try { $proc = [System.Diagnostics.Process]::GetProcessById([int]$pid) } catch { return $true } "
            "    $rect = New-Object WindowApi+RECT; "
            "    [void][WindowApi]::GetWindowRect($hWnd, [ref]$rect); "
            "    $items.Add([pscustomobject]@{ "
            "      Handle = $hWnd.ToInt64(); "
            "      ProcessId = [int]$pid; "
            "      ProcessName = $proc.ProcessName; "
            "      Title = $title; "
            "      Left = $rect.Left; "
            "      Top = $rect.Top; "
            "      Right = $rect.Right; "
            "      Bottom = $rect.Bottom; "
            "      Width = ($rect.Right - $rect.Left); "
            "      Height = ($rect.Bottom - $rect.Top) "
            "    }); "
            "    return $true "
            "  }; "
            "  [void][WindowApi]::EnumWindows($callback, [IntPtr]::Zero); "
            "  $items "
            "}"
        )


class BrokerDesktopExecutor:
    def __init__(self, broker_url: str, broker_token: str | None = None) -> None:
        self.broker_url = broker_url.rstrip("/")
        self.broker_token = broker_token or ""

    def execute(self, action: AgentAction) -> str:
        payload = {"kind": action.kind, "params": action.params, "token": self.broker_token}
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.broker_url}/execute",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok", False):
            raise RuntimeError(result.get("error", "Broker execution failed."))
        return str(result.get("message", "Broker executed the action."))

    def execute_many(self, actions: list[AgentAction]) -> list[str]:
        payload = {
            "actions": [{"kind": action.kind, "params": action.params} for action in actions],
            "token": self.broker_token,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.broker_url}/execute_many",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok", False):
            raise RuntimeError(result.get("error", "Broker execution failed."))
        messages = result.get("messages", [])
        return [str(message) for message in messages]

    def ping(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.broker_url}/health", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("ok"))
        except Exception:
            return False


class HybridDesktopExecutor:
    GUI_ACTIONS = {
        "open_url",
        "open_path",
        "open_app",
        "open_in_app",
        "type_text",
        "send_keys",
        "focus_window",
        "click",
        "double_click",
        "right_click",
        "move_mouse",
        "mouse_down",
        "mouse_up",
        "drag_mouse",
        "scroll",
        "screenshot",
        "window_screenshot",
        "list_windows",
        "active_window",
        "window_bounds",
        "cursor_position",
        "screen_size",
        "session_info",
        "preview_status",
        "refresh_preview",
    }

    def __init__(self, local_executor: LocalDesktopExecutor, broker_executor: BrokerDesktopExecutor | None) -> None:
        self.local_executor = local_executor
        self.broker_executor = broker_executor

    def execute(self, action: AgentAction) -> str:
        if self.broker_executor:
            try:
                return self.broker_executor.execute(action)
            except Exception as exc:
                if action.kind in self.GUI_ACTIONS:
                    raise RuntimeError(
                        "The visible desktop host is not available. "
                        "Start Desktop Host or let the Telegram bridge auto-start it, then retry."
                    ) from exc
        return self.local_executor.execute(action)

    def execute_many(self, actions: list[AgentAction]) -> list[str]:
        if self.broker_executor:
            try:
                return self.broker_executor.execute_many(actions)
            except Exception as exc:
                if any(action.kind in self.GUI_ACTIONS for action in actions):
                    raise RuntimeError(
                        "The visible desktop host is not available. "
                        "Start Desktop Host or let the Telegram bridge auto-start it, then retry."
                    ) from exc
        return self.local_executor.execute_many(actions)


class DesktopAgent:
    def __init__(
        self,
        cwd: Path,
        artifacts_dir: Path | None = None,
        broker_url: str | None = None,
        broker_token: str | None = None,
        state_path: Path | None = None,
        execution_mode: str | None = None,
    ) -> None:
        self.cwd = cwd
        self.artifacts_dir = artifacts_dir or (ENGINE_ROOT / "artifacts")
        self.executor = self._build_executor(broker_url, broker_token)
        self.state_path = state_path or (ENGINE_ROOT / "desktop_agent_state.json")
        self.execution_mode = (execution_mode or os.getenv("DESKTOP_AGENT_MODE", "fast")).strip().lower()
        if self.execution_mode not in {"fast", "safe"}:
            self.execution_mode = "fast"

    def _build_executor(self, broker_url: str | None, broker_token: str | None):
        local = LocalDesktopExecutor(self.cwd, self.artifacts_dir)
        if broker_url:
            broker = BrokerDesktopExecutor(broker_url, broker_token)
            return HybridDesktopExecutor(local, broker)
        return local

    def handle(self, text: str, session_key: str = "default") -> AgentResult:
        normalized = " ".join(text.strip().split())
        lowered = normalized.lower()
        if not normalized:
            return AgentResult(False, "")

        sessions = self._load_sessions()
        if lowered in {"status", "task status", "progress"}:
            session = sessions.get(session_key)
            if not session:
                return AgentResult(True, "No active task session was found for this channel.")
            return AgentResult(True, self._format_session_status(session), task_id=session.task_id)
        if lowered == "continue":
            session = sessions.get(session_key)
            if not session:
                return AgentResult(True, "There is no previous task to continue.")
            message = self._run_observation_cycle(session, "Manual continue requested.", force=True)
            sessions[session_key] = session
            self._save_sessions(sessions)
            return AgentResult(True, message, task_id=session.task_id)

        try:
            require_screenshot = self._request_needs_screenshot(normalized)
            actions = self._plan_actions(normalized, lowered)
            if not actions:
                return AgentResult(False, "")
            messages = self.executor.execute_many(actions)
            session = self._new_session(session_key, normalized)
            session.require_screenshot = require_screenshot
            now = time.time()
            session.action_history.extend(
                [{"kind": action.kind, "params": action.params, "message": message, "at": now} for action, message in zip(actions, messages)]
            )
            session.updated_at = now
            session.last_message = "\n\n".join(msg for msg in messages if msg)
            observation = self._run_observation_cycle(session)
            sessions[session_key] = session
            self._save_sessions(sessions)
            final_message = session.last_message
            if observation:
                final_message += "\n\n" + observation
            return AgentResult(True, final_message, actions, task_id=session.task_id)
        except PermissionError as exc:
            return AgentResult(True, f"Windows blocked that action.\n\n{exc}")
        except FileNotFoundError as exc:
            return AgentResult(True, f"The requested app or file was not found.\n\n{exc}")
        except subprocess.TimeoutExpired:
            return AgentResult(True, "The requested command took too long and was stopped.")
        except Exception as exc:
            return AgentResult(True, f"The desktop agent hit an unexpected error.\n\n{exc}")

    def _plan_actions(self, original: str, lowered: str) -> list[AgentAction]:
        parts = self._split_steps(original)
        all_actions: list[AgentAction] = []
        for part in parts:
            lowered_part = part.lower()
            actions = (
                self._plan_start_app(part)
                or self._plan_open_in_app(part)
                or self._plan_open_url(part, lowered_part)
                or self._plan_open_target(part, lowered_part)
                or self._plan_run_command(part)
                or self._plan_type_text(part)
                or self._plan_press_keys(lowered_part)
                or self._plan_focus_window(part)
                or self._plan_click(lowered_part)
                or self._plan_mouse_move(lowered_part)
                or self._plan_drag_mouse(lowered_part)
                or self._plan_mouse_down(lowered_part)
                or self._plan_mouse_up(lowered_part)
                or self._plan_double_click(lowered_part)
                or self._plan_right_click(lowered_part)
                or self._plan_scroll(lowered_part)
                or self._plan_wait(lowered_part)
                or self._plan_observation(lowered_part)
                or self._plan_screenshot(lowered_part)
            )
            if not actions:
                return []
            all_actions.extend(actions)
        return all_actions

    def _plan_start_app(self, original: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?start (?P<target>.+)$", original, flags=re.IGNORECASE)
        if not match:
            return None
        return [AgentAction("open_app", {"app": match.group("target").strip()})]

    def _split_steps(self, original: str) -> list[str]:
        normalized = " ".join(original.strip().split())
        parts = re.split(r"\s+(?:and then|then|after that)\s+", normalized, flags=re.IGNORECASE)
        return [part.strip(" ,") for part in parts if part.strip(" ,")]

    def _new_session(self, session_key: str, request: str) -> TaskSession:
        now = time.time()
        return TaskSession(
            task_id=f"task-{int(now * 1000)}",
            session_key=session_key,
            request=request,
            status="completed",
            created_at=now,
            updated_at=now,
        )

    def _run_observation_cycle(self, session: TaskSession, prefix: str | None = None, force: bool = False) -> str:
        if self.execution_mode == "fast" and not session.require_screenshot and not force:
            session.last_observation = ""
            session.updated_at = time.time()
            return prefix or ""
        observation_actions = [AgentAction("active_window", {}), AgentAction("cursor_position", {})]
        observation_actions.extend([AgentAction("refresh_preview", {}), AgentAction("preview_status", {})])
        if session.require_screenshot:
            screenshot_kind = "window_screenshot" if self.execution_mode == "fast" else "screenshot"
            observation_actions.append(AgentAction(screenshot_kind, {}))
        messages: list[str] = []
        try:
            results = self.executor.execute_many(observation_actions)
            for action, message in zip(observation_actions, results):
                session.action_history.append(
                    {"kind": action.kind, "params": action.params, "message": message, "at": time.time()}
                )
                messages.append(message)
        except Exception:
            for action in observation_actions:
                try:
                    message = self.executor.execute(action)
                    session.action_history.append(
                        {"kind": action.kind, "params": action.params, "message": message, "at": time.time()}
                    )
                    messages.append(message)
                except Exception as exc:
                    messages.append(f"{action.kind}: {exc}")
        observation = "\n".join(messages).strip()
        session.last_observation = observation
        session.updated_at = time.time()
        if prefix:
            return prefix + "\n" + observation
        return observation

    def _request_needs_screenshot(self, request: str) -> bool:
        lowered = request.lower()
        triggers = [
            "with screenshot",
            "take screenshot after",
            "confirm with screenshot",
            "verify with screenshot",
            "screenshot confirmation",
        ]
        return any(trigger in lowered for trigger in triggers)

    def _format_session_status(self, session: TaskSession) -> str:
        lines = [
            f"Task id: {session.task_id}",
            f"Request: {session.request}",
            f"Status: {session.status}",
            f"Last update: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.updated_at))}",
        ]
        if session.last_message:
            lines.append("Last result: " + session.last_message.replace("\n", " | "))
        if session.last_observation:
            lines.append("Last observation: " + session.last_observation.replace("\n", " | "))
        return "\n".join(lines)

    def _load_sessions(self) -> dict[str, TaskSession]:
        if not self.state_path.exists():
            return {}
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        sessions: dict[str, TaskSession] = {}
        for key, value in data.get("sessions", {}).items():
            sessions[key] = TaskSession(**value)
        return sessions

    def _save_sessions(self, sessions: dict[str, TaskSession]) -> None:
        payload = {"sessions": {key: asdict(value) for key, value in sessions.items()}}
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _plan_open_in_app(self, original: str) -> list[AgentAction] | None:
        patterns = [
            r"^(?:please\s+)?open (?P<target>.+?) in (?P<app>notepad|wordpad|paint|chrome|edge|firefox)$",
            r"^(?:please\s+)?open (?P<app>notepad|wordpad|paint|chrome|edge|firefox) with (?P<target>.+)$",
            r"^(?:please\s+)?open (?P<app>notepad|wordpad|paint|chrome|edge|firefox) and (?P<target>.+?)(?: on it| in it)?$",
        ]
        for pattern in patterns:
            match = re.match(pattern, original, flags=re.IGNORECASE)
            if not match:
                continue
            target = match.group("target").strip().strip('"')
            app = match.group("app").lower()
            resolved = self._resolve_target(target)
            if not resolved:
                return [AgentAction("message", {"text": f"I could not find '{target}' from {self.cwd}."})]
            return [AgentAction("open_in_app", {"path": str(resolved), "app": app})]
        return None

    def _plan_open_url(self, original: str, lowered: str) -> list[AgentAction] | None:
        patterns = [
            r"^(?:please\s+)?open (?:the )?browser$",
            r"^(?:please\s+)?open (?:the )?browser (?:to|on) (?P<target>.+)$",
            r"^(?:please\s+)?open (?P<target>https?://\S+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, original, flags=re.IGNORECASE)
            if not match:
                continue
            target = match.groupdict().get("target")
            if not target:
                return [AgentAction("open_url", {"url": "https://www.google.com"})]
            return [AgentAction("open_url", {"url": self._normalize_url(target)})]

        bare_site = re.match(r"^(?:please\s+)?open (?P<target>[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?)$", lowered)
        if bare_site:
            return [AgentAction("open_url", {"url": self._normalize_url(bare_site.group("target"))})]

        if lowered in COMMON_URLS:
            return [AgentAction("open_url", {"url": COMMON_URLS[lowered]})]
        if lowered.startswith("open ") and lowered[5:] in COMMON_URLS:
            return [AgentAction("open_url", {"url": COMMON_URLS[lowered[5:]]})]
        return None

    def _plan_open_target(self, original: str, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?open (?P<target>.+)$", original, flags=re.IGNORECASE)
        if not match:
            return None

        target = match.group("target").strip().strip('"')
        lowered_target = target.lower()

        if lowered_target in {"the project folder", "project folder"}:
            return [AgentAction("open_path", {"path": str(self.cwd)})]

        special = self._special_folder_path(lowered_target)
        if special:
            return [AgentAction("open_path", {"path": str(special)})]

        if lowered_target in APP_ALIASES:
            return [AgentAction("open_app", {"app": lowered_target})]

        resolved = self._resolve_target(target)
        if resolved:
            return [AgentAction("open_path", {"path": str(resolved)})]

        likely_app = target.removesuffix(".exe").lower()
        if likely_app in APP_ALIASES:
            return [AgentAction("open_app", {"app": likely_app})]
        if "\\" not in target and "/" not in target:
            return [AgentAction("open_app", {"app": target})]
        return None

    def _plan_run_command(self, original: str) -> list[AgentAction] | None:
        for pattern in [r"^(?:please\s+)?run (?P<command>.+)$", r"^(?:please\s+)?execute (?P<command>.+)$"]:
            match = re.match(pattern, original, flags=re.IGNORECASE)
            if match:
                return [AgentAction("run_command", {"command": match.group("command").strip()})]
        return None

    def _plan_type_text(self, original: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?type (?P<text>.+)$", original, flags=re.IGNORECASE)
        if match:
            return [AgentAction("type_text", {"text": match.group("text")})]
        return None

    def _plan_press_keys(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?press (?P<keys>.+)$", lowered)
        if not match:
            return None
        key_phrase = match.group("keys").strip()
        translated = self._translate_keys(key_phrase)
        if not translated:
            return [AgentAction("message", {"text": f"I could not understand the key sequence '{key_phrase}'."})]
        return [AgentAction("send_keys", {"keys": translated})]

    def _plan_focus_window(self, original: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?focus (?P<title>.+)$", original, flags=re.IGNORECASE)
        if match:
            return [AgentAction("focus_window", {"title": match.group("title").strip()})]
        return None

    def _plan_click(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?click (?P<x>\d+)\s+(?P<y>\d+)$", lowered)
        if match:
            return [AgentAction("click", {"x": int(match.group("x")), "y": int(match.group("y"))})]
        return None

    def _plan_mouse_move(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?move (?:the )?mouse to (?P<x>\d+)\s+(?P<y>\d+)$", lowered)
        if match:
            return [AgentAction("move_mouse", {"x": int(match.group("x")), "y": int(match.group("y"))})]
        return None

    def _plan_double_click(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?double click (?P<x>\d+)\s+(?P<y>\d+)$", lowered)
        if match:
            return [AgentAction("double_click", {"x": int(match.group("x")), "y": int(match.group("y"))})]
        return None

    def _plan_mouse_down(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?mouse down(?: at)? (?P<x>\d+)\s+(?P<y>\d+)$", lowered)
        if match:
            return [AgentAction("mouse_down", {"x": int(match.group("x")), "y": int(match.group("y"))})]
        return None

    def _plan_mouse_up(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?mouse up(?: at)? (?P<x>\d+)\s+(?P<y>\d+)$", lowered)
        if match:
            return [AgentAction("mouse_up", {"x": int(match.group("x")), "y": int(match.group("y"))})]
        return None

    def _plan_drag_mouse(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(
            r"^(?:please\s+)?drag(?: the mouse)? from (?P<x1>\d+)\s+(?P<y1>\d+) to (?P<x2>\d+)\s+(?P<y2>\d+)(?: in (?P<duration>\d+)ms)?$",
            lowered,
        )
        if match:
            params = {
                "start_x": int(match.group("x1")),
                "start_y": int(match.group("y1")),
                "end_x": int(match.group("x2")),
                "end_y": int(match.group("y2")),
            }
            if match.group("duration"):
                params["duration_ms"] = int(match.group("duration"))
            return [AgentAction("drag_mouse", params)]
        return None

    def _plan_right_click(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?right click (?P<x>\d+)\s+(?P<y>\d+)$", lowered)
        if match:
            return [AgentAction("right_click", {"x": int(match.group("x")), "y": int(match.group("y"))})]
        return None

    def _plan_scroll(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?scroll (?P<direction>up|down)(?: (?P<amount>\d+))?$", lowered)
        if match:
            direction = match.group("direction")
            amount = int(match.group("amount") or "600")
            signed = amount if direction == "up" else -amount
            return [AgentAction("scroll", {"amount": signed})]
        return None

    def _plan_wait(self, lowered: str) -> list[AgentAction] | None:
        match = re.match(r"^(?:please\s+)?wait (?P<seconds>\d+(?:\.\d+)?) ?seconds?$", lowered)
        if match:
            return [AgentAction("wait", {"seconds": float(match.group("seconds"))})]
        return None

    def _plan_observation(self, lowered: str) -> list[AgentAction] | None:
        if lowered in {"list windows", "what windows are open", "show windows"}:
            return [AgentAction("list_windows", {})]
        if lowered in {"active window", "what is the active window", "show active window"}:
            return [AgentAction("active_window", {})]
        if lowered in {"preview status", "window preview status", "desktop preview status"}:
            return [AgentAction("preview_status", {})]
        if lowered in {"refresh preview", "refresh window preview", "update preview"}:
            return [AgentAction("refresh_preview", {})]
        match = re.match(r"^(?:what are the |show |get )?bounds(?: of| for)? (?P<title>.+)$", lowered)
        if match:
            return [AgentAction("window_bounds", {"title": match.group("title").strip()})]
        if lowered in {"cursor position", "where is the mouse", "mouse position"}:
            return [AgentAction("cursor_position", {})]
        if lowered in {"screen size", "what is the screen size", "display size"}:
            return [AgentAction("screen_size", {})]
        if lowered in {"session info", "desktop session info", "broker session info"}:
            return [AgentAction("session_info", {})]
        return None

    def _plan_screenshot(self, lowered: str) -> list[AgentAction] | None:
        if lowered in {"interaction screenshot", "take interaction screenshot", "interaction snapshot", "take interaction snapshot"}:
            return [AgentAction("interaction_screenshot", {})]
        if lowered in {"take window screenshot", "window screenshot", "active window screenshot"}:
            return [AgentAction("window_screenshot", {})]
        if lowered in {"take screenshot", "take a screenshot", "screenshot"}:
            return [AgentAction("screenshot", {})]
        return None

    def _resolve_target(self, raw: str) -> Path | None:
        raw = raw.strip().strip('"')
        expanded = Path(os.path.expandvars(raw)).expanduser()
        if expanded.exists():
            return expanded
        if not expanded.is_absolute():
            candidate = self.cwd / expanded
            if candidate.exists():
                return candidate
        if raw.lower() in {"it", "the file", "that file", "latest file", "last file"}:
            return self._most_recent_file()
        fuzzy = self._fuzzy_find_file(raw)
        if fuzzy:
            return fuzzy
        return None

    def _fuzzy_find_file(self, raw: str) -> Path | None:
        cleaned = raw.lower()
        for phrase in [" on it", " in it", " with notepad", " in notepad"]:
            cleaned = cleaned.replace(phrase, "")
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", cleaned)
            if token not in {"the", "a", "an", "file", "document"}
        ]
        if not tokens:
            return None

        candidates = [path for path in self.cwd.iterdir() if path.is_file()]
        matches: list[Path] = []
        for path in candidates:
            searchable = " ".join(re.findall(r"[a-z0-9]+", f"{path.stem} {path.name}".lower()))
            if all(token in searchable for token in tokens):
                matches.append(path)
        if not matches:
            return None
        return max(matches, key=lambda path: path.stat().st_mtime)

    def _special_folder_path(self, lowered_target: str) -> Path | None:
        home = Path.home()
        mapping = {
            "downloads": home / "Downloads",
            "downloads folder": home / "Downloads",
            "documents": home / "Documents",
            "documents folder": home / "Documents",
            "desktop": home / "Desktop",
            "desktop folder": home / "Desktop",
            "pictures": home / "Pictures",
            "pictures folder": home / "Pictures",
        }
        path = mapping.get(lowered_target)
        if path and path.exists():
            return path
        return None

    def _normalize_url(self, target: str) -> str:
        target = target.strip()
        if target.lower() in COMMON_URLS:
            return COMMON_URLS[target.lower()]
        if target.startswith(("http://", "https://")):
            return target
        if " " in target:
            return "https://www.google.com/search?q=" + urllib.parse.quote_plus(target)
        return "https://" + target

    def _translate_keys(self, phrase: str) -> str | None:
        parts = [part.strip() for part in re.split(r"\s*\+\s*|\s+and\s+", phrase) if part.strip()]
        if not parts:
            return None
        translated: list[str] = []
        for part in parts:
            if part in {"ctrl", "control"}:
                translated.append("^")
            elif part == "alt":
                translated.append("%")
            elif part == "shift":
                translated.append("+")
            elif part == "win":
                return None
            elif part in KEY_ALIASES:
                translated.append(KEY_ALIASES[part])
            elif len(part) == 1:
                translated.append(part)
            else:
                return None
        return "".join(translated)

    def _most_recent_file(self) -> Path | None:
        candidates = [path for path in self.cwd.iterdir() if path.is_file()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)


def run_cli(
    cwd: Path,
    request: str,
    broker_url: str | None = None,
    broker_token: str | None = None,
    session_key: str = "cli",
    execution_mode: str | None = None,
) -> int:
    agent = DesktopAgent(cwd=cwd, broker_url=broker_url, broker_token=broker_token, execution_mode=execution_mode)
    result = agent.handle(request, session_key=session_key)
    print(
        json.dumps(
            {
                "handled": result.handled,
                "message": result.message,
                "actions": [asdict(action) for action in result.actions],
                "task_id": result.task_id,
            },
            indent=2,
        )
    )
    return 0 if result.handled else 2
