# Telegram Codex Desktop Bridge

This connects your phone to Codex on this computer through a private Telegram bot.
Messages from Telegram are sent to the local Codex CLI, Codex can work in the configured computer folder, and the final result is sent back to Telegram.
Common desktop actions are handled first by a reusable local desktop agent. For actions that must appear on your visible Windows desktop, the agent can forward them to a localhost desktop broker running in your logged-in session.

## What you need

- Telegram installed on your phone
- A Telegram bot token from `@BotFather`
- Codex already logged in on this computer
- Python 3

## Install the project

The Telegram bridge itself uses Python standard-library modules. The desktop engine has its own dependency file:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py -m pip install -r requirements.txt
```

## Project Layout

- `telegram_*.py`, `run_telegram_*.ps1`, `plugins/telegram-bridge`, and `plugins/messaging-bridge` belong to the Telegram bridge.
- `C:\Users\barru\Documents\New project\desktop engine\` contains the desktop automation engine: agent, host app, broker, MCP server, desktop state, screenshots, and local desktop dependencies.
- `telegram_ai_bridge.py` still depends on that sibling desktop engine for direct desktop requests and desktop-host autostart.

## Documentation map

If you are working on the AppStudio side of this repository, start with:

- [APPSTUDIO_REFERENCE_MAP.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_REFERENCE_MAP.md)
- [APPSTUDIO_FRAMEWORK_GOLDEN_WORKFLOW.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_FRAMEWORK_GOLDEN_WORKFLOW.md)
- [APPSTUDIO_FULL_DOCS_AUDIT.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_FULL_DOCS_AUDIT.md)
- [APPSTUDIO_GENERATION_FRAMEWORK.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_GENERATION_FRAMEWORK.md)
- [APPSTUDIO_JSON_PROJECT_SPEC_GUIDE.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_JSON_PROJECT_SPEC_GUIDE.md)
- [APPSTUDIO_CANONICAL_EXAMPLES.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_CANONICAL_EXAMPLES.md)
- [APPSTUDIO_FRAMEWORK_VALIDATION.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_FRAMEWORK_VALIDATION.md)
- [APPSTUDIO_FRAMEWORK_BEHAVIOR_SHORTCUTS.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_FRAMEWORK_BEHAVIOR_SHORTCUTS.md)
- [APPSTUDIO_ABB_TYPED_SHAPES_GUIDE.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_ABB_TYPED_SHAPES_GUIDE.md)
- [APPSTUDIO_BLOCK_LIBRARY.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_BLOCK_LIBRARY.md)
- [APPSTUDIO_CONTROLLER_BLOCKS.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_CONTROLLER_BLOCKS.md)
- [APPSTUDIO_LAYOUT_INTELLIGENCE_RULES.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_LAYOUT_INTELLIGENCE_RULES.md)
- [APPSTUDIO_USAGE_DOCS_SUMMARY.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_USAGE_DOCS_SUMMARY.md)

It points to the right reference depending on whether the task is about:

- AppStudio project structure
- direct JSON authoring
- container composition
- business logic wiring
- framework/runtime behavior
- layout tuning

The generation-framework guide explains the reusable Python package we extracted for building new AppStudio projects and the planned next step toward JSON-based project specs.

The JSON project-spec guide explains how to define a project in data and build it without hardcoding the layout in Python.

If you prefer to keep this isolated, you can use a virtual environment first:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
cd "..\desktop engine"
py -m pip install -r requirements.txt
```

## Setup

1. In Telegram, open `@BotFather`.
2. Send `/newbot`, follow the prompts, and copy the bot token.
3. Copy `.env.example` to `.env`.
4. Put your bot token in `TELEGRAM_BOT_TOKEN`.
5. Confirm `CODEX_THREAD_ID` and `CODEX_CWD` in `.env`.
6. Start the bridge:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge"
py .\telegram_ai_bridge.py
```

If you want apps and windows to appear on your actual desktop, start the interactive desktop host app in your logged-in Windows session:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_host_app.py
```

You can still use the console broker if needed:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_broker.py
```

7. On your phone, open your new bot in Telegram and send any message.
8. The bot will reply with your chat id.
9. Put that id into `TELEGRAM_ALLOWED_CHAT_IDS` in `.env`, then stop and restart the bridge.

After that, messages you send to the bot from your phone will run as Codex tasks on this computer. The bridge waits for Codex to finish, then sends the final result back to Telegram.

## Speed modes

The desktop agent now supports two execution styles:

- `fast`: reduced automatic post-action observation, faster default drag behavior, and batched action execution through the desktop host
- `safe`: more conservative behavior for fragile flows and troubleshooting

The default is `fast`. You can change it in `.env`:

```env
DESKTOP_AGENT_MODE=fast
```

You can also tune the drag and focus timing:

```env
DESKTOP_AGENT_DRAG_STEPS=8
DESKTOP_AGENT_DRAG_DURATION_MS=240
DESKTOP_AGENT_FOCUS_RESTORE_DELAY_MS=60
DESKTOP_AGENT_FOCUS_RETRY_DELAY_MS=25
```

For one-off CLI tests, you can choose the mode directly:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_agent_cli.py --mode fast --broker-url "http://127.0.0.1:8765" "open notepad then type hello"
```

Examples of plain messages that the local desktop agent can execute directly:

- `open browser`
- `open youtube`
- `open notepad`
- `open rewrite_mtt_manual.py in notepad`
- `open the project folder`
- `open downloads`
- `run dir`
- `type hello world`
- `press ctrl+s`
- `focus notepad`
- `take screenshot`
- `take window screenshot`
- `open notepad then type hello with screenshot`
- `what windows are open`
- `what is the active window`
- `where is the mouse`
- `screen size`
- `move mouse to 400 300`
- `double click 400 300`
- `right click 400 300`
- `scroll down 600`
- `wait 2 seconds`

You can also chain several steps in one natural request:

- `open notepad then type hello world`
- `screen size then cursor position`
- `focus notepad then type testing`
- `open desktop-agent-notepad-test.txt in notepad then type hello with screenshot`

The same agent can also be used outside Telegram:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_agent_cli.py "open rewrite_mtt_manual.py in notepad"
```

Or through the visible-session broker:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_agent_cli.py --broker-url "http://127.0.0.1:8765" "open rewrite_mtt_manual.py in notepad"
```

The desktop agent now keeps a simple persistent task session per channel or CLI session. You can ask:

- `status`
- `continue`
- `open notepad then type hello then wait 1 second then status`

There is also an MCP server that exposes the desktop agent and broker tools over stdio:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_mcp_server.py
```

This MCP server is the preferred integration point for GitHub Copilot, Copilot CLI, and other MCP-capable clients. The visible desktop host should still be running if you want real desktop actions:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_host_app.py
```

### Copilot-friendly MCP tools

The MCP server now exposes a clearer tool surface for Copilot-style clients:

- `desktop_request`
- `desktop_status`
- `desktop_continue`
- `desktop_open_app`
- `desktop_open_url`
- `desktop_focus_window`
- `desktop_type_text`
- `desktop_send_keys`
- `desktop_click`
- `desktop_double_click`
- `desktop_right_click`
- `desktop_move_mouse`
- `desktop_drag_mouse`
- `desktop_take_screenshot`
- `desktop_list_windows`
- `desktop_active_window`
- `desktop_window_bounds`
- `desktop_cursor_position`
- `desktop_screen_size`
- `desktop_execute` as a compatibility escape hatch

Use the specific `desktop_*` tools whenever possible. Use `desktop_request` for higher-level natural-language tasks that need the shared desktop agent.

### Copilot setup direction

GitHub documents MCP as the standard way to extend Copilot with local or remote tools, and Copilot CLI supports both local and remote MCP servers. For the smoothest setup, use this server as a local STDIO MCP server:

```powershell
cd "C:\Users\barru\Documents\New project\desktop engine"
py .\desktop_mcp_server.py
```

Then configure your Copilot client to launch that command as an MCP server. Keep the desktop host app running in parallel for visible app, mouse, keyboard, and screenshot actions.

## Commands

- `/start` or `/help`: show a short ready message
- `/status`: confirm the bridge is running

## Notes

This uses Telegram long polling, so you do not need router changes, public hosting, or a webhook. Keep `.env` private because it contains your Telegram bot token.

The default configuration uses `CODEX_FULL_AUTO=true`, which is intended for normal task execution inside the configured workspace. Do not enable dangerous bypass options for a phone bridge.

The interactive desktop host app is the preferred broker for visible desktop control. Run it in the Windows session where you can actually see the desktop. The Telegram bridge can run separately and forward local desktop actions to the host over `localhost`.

The host and broker now also support batched action execution internally, which reduces the stop-and-go feel for chained desktop tasks such as focus, drag, type, and deploy flows.

In `fast` mode, automatic screenshot confirmation now prefers an active-window capture instead of a full-screen capture. That makes verification feel more natural on large monitors because the captured image is smaller and faster to inspect.

## Telegram Side Channel

`telegram_sidechannel.py` lets the current Codex chat communicate with you through Telegram without spawning a separate Codex process. It reuses `.env` values, or separate side-channel values if configured:

```env
TELEGRAM_SIDECHANNEL_BOT_TOKEN=
TELEGRAM_SIDECHANNEL_CHAT_IDS=
TELEGRAM_SIDECHANNEL_DEFAULT_CHAT_ID=
```

Examples:

```powershell
py .\telegram_sidechannel.py send "Codex is done with the build."
py .\telegram_sidechannel.py ask "Can I send this WhatsApp message?" --timeout 300
```

Use a separate bot token for the side channel if `telegram_ai_bridge.py` is also running. Two long-polling consumers on the same bot can race for replies.

If Codex network permission is inconvenient, run the localhost notifier service once:

```powershell
.\run_telegram_notifier.ps1
```

Then this chat can send through localhost:

```powershell
py .\telegram_notify.py "Codex finished the task."
```

The service binds only to `127.0.0.1`, sends only to the configured Telegram chat allowlist, and stores allowed incoming Telegram messages in a local inbox. Read and clear that inbox with:

```powershell
py .\telegram_notify.py inbox --clear
```

If you update `telegram_notifier_service.py`, restart `run_telegram_notifier.ps1` so the long-running service picks up the new code.

For faster Telegram-triggered work, run the Codex gateway in a second PowerShell window:

```powershell
.\run_telegram_codex_gateway.ps1
```

This watches the local notifier inbox every second and runs `codex exec resume` against the configured thread when a Telegram message arrives. It is more immediate than the heartbeat, but it is a local daemon that invokes Codex CLI.

## WhatsApp API

For professional WhatsApp messaging, use Meta's official WhatsApp Business Cloud API instead of desktop automation. The helper in `whatsapp_cloud_api.py` sends through the Graph API using:

```env
WHATSAPP_CLOUD_ACCESS_TOKEN=
WHATSAPP_CLOUD_PHONE_NUMBER_ID=
WHATSAPP_CLOUD_GRAPH_VERSION=v25.0
```

Example:

```powershell
py .\whatsapp_cloud_api.py --to 491701234567 --text "Hello from the WhatsApp Cloud API"
```

Important limitation: the official Cloud API is for a WhatsApp Business number sending to individual recipients. It is not a supported way to post into your personal WhatsApp Desktop groups. For existing personal/group chats, keep using the Desktop Host path, or move that workflow to a platform with official group bot support such as Telegram.

## Faster desktop vision

The desktop host now supports a faster local vision path using a normal Python dependency install:

- `mss` for fast desktop capture and PNG encoding

When the Desktop Host app starts, it now tries to keep a lightweight **active-window preview cache** warm in the background. In practice this means:

- fast-mode verification can use a smaller active-window image instead of a full-screen image
- the agent can often inspect a recent frame instead of forcing a full fresh desktop capture
- verification pauses should feel shorter and less abrupt
- the live preview can stay cheap while full screenshots remain available on demand
- the cached preview keeps a full-resolution frame in memory for precise follow-up actions, while the UI only displays a smaller thumbnail

You can tune the preview cache behavior in `.env`:

```env
DESKTOP_AGENT_PREVIEW_INTERVAL_MS=300
DESKTOP_AGENT_PREVIEW_IDLE_INTERVAL_MS=1200
DESKTOP_AGENT_PREVIEW_ACTIVE_FOR_MS=5000
DESKTOP_AGENT_PREVIEW_SOURCE=active_monitor
DESKTOP_AGENT_PREVIEW_MAX_WIDTH=480
DESKTOP_AGENT_PREVIEW_MAX_HEIGHT=300
DESKTOP_AGENT_PREVIEW_PERSIST_TO_DISK=false
```

`DESKTOP_AGENT_PREVIEW_INTERVAL_MS` is the faster polling rate used while the agent is actively working. `DESKTOP_AGENT_PREVIEW_IDLE_INTERVAL_MS` is the slower rate used while the desktop is idle, which helps reduce CPU use. `DESKTOP_AGENT_PREVIEW_ACTIVE_FOR_MS` controls how long the host stays in the faster mode after a desktop action or an explicit preview refresh.

The preview size settings affect only the always-on cached preview shown in the host UI. Normal screenshots and explicit window screenshots still keep their full resolution, and the fast preview path keeps the latest full-resolution frame in memory for precise verification and motion planning. By default, the host now keeps preview frames in memory instead of writing them to disk constantly; set `DESKTOP_AGENT_PREVIEW_PERSIST_TO_DISK=true` only when you specifically want a rolling preview image file for debugging.

`DESKTOP_AGENT_PREVIEW_SOURCE=active_monitor` is usually the best choice for the Desktop Host UI because it shows the whole monitor of the active working app instead of blindly following the primary screen. Use `screen` for the primary monitor or `window` if you explicitly want the preview to follow only the active foreground window.

Useful commands:

- `interaction screenshot`
- `take window screenshot`
- `desktop_take_window_screenshot`
- `desktop_preview_status`
- `desktop_refresh_preview`

## Safe interaction view

For desktop control, there are now three different image concepts, and they are **not interchangeable**:

- `interaction screenshot`
  The action-planning image. This uses the same coordinate space as preview-relative mouse actions such as preview-relative click and drag. Use this image when deciding where to move, click, drag, or resize.
- `take window screenshot`
  A full-resolution evidence image of the target window. Use this for documentation, review, or saving proof of the result.
- live host preview
  A lightweight thumbnail used for fast awareness and quick verification. This is useful for staying oriented, but it is not the right artifact for manual coordinate picking unless you deliberately work in preview coordinates.

The important operating rule is:

- use `interaction screenshot` for action planning
- use `take window screenshot` for evidence
- do not derive preview-relative coordinates from a full-resolution evidence screenshot

The desktop agent now rejects preview-relative coordinates that fall outside the live interaction view. That makes coordinate-space mistakes fail clearly instead of silently clamping to the wrong on-screen point.
