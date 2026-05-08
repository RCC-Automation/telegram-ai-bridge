---
name: telegram-bridge
description: Manage the local Telegram-to-Codex bridge through structured plugin scripts and MCP tools. Use when the user asks about Telegram bridge status, restarts, chat routing, Codex thread routing, voice transcription, Telegram command integration, or bridge debugging.
---

# Telegram Bridge Skill

Use this skill when working on the local Telegram bridge.

## Boundaries

- Do not replace the existing bridge runtime unless explicitly requested.
- Treat the existing bridge files in the repository root as the current production implementation.
- Use the plugin tools as a management facade over that implementation.
- Telegram chat routing and Codex thread routing are separate concepts.

## Main Files

Existing bridge runtime:

- `telegram_notifier_service.py`
- `telegram_codex_gateway.py`
- `telegram_notify.py`
- `telegram_chat_registry.py`
- `codex_thread_registry.py`
- `telegram_voice_transcription.py`
- `run_telegram_bridge.ps1`

Plugin management layer:

- `plugins/telegram-bridge/scripts/telegram_bridge_manager.py`
- `plugins/telegram-bridge/mcp/telegram_bridge_mcp.py`

Persistent state:

- `../telegram-messages/chats.json`
- `../telegram-messages/active-chat.json`
- `../telegram-messages/codex-threads.json`
- `../telegram-messages/active-codex-thread.json`
- `../telegram-messages/bridge-heartbeats/*.json`

## Commands

Prefer the manager script or MCP tools over manual file edits:

```powershell
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py status
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py chats
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py threads
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py use-thread appstudio
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py use-chat raul
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py voice-status
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py logs --lines 80
```

## Restart Policy

Use restart only when needed. If restarting from Codex, prefer the structured tool:

```powershell
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py restart
```

This starts the existing `run_telegram_bridge.ps1` launcher. It does not invent a second bridge implementation.

## Telegram Bot Commands

The gateway should support Telegram commands such as:

- `/threads`
- `/codex_threads`
- `/use-thread appstudio`
- `/use_thread appstudio`
- `/whoami`
- `/chats`
- `/voice-status`
- `/voice_status`

If a command reaches Codex as a normal message, the running bridge process is probably stale and needs restart.

## Health Checks

Bridge status should be judged from heartbeat freshness first, then from PID files and logs as fallback evidence.

Heartbeat files are written by the running services:

- `telegram_notifier.json`
- `telegram_gateway.json`

If heartbeat state is `missing`, the bridge may still be running old code and should be restarted to activate heartbeat reporting. If heartbeat state is `stale`, the service has probably stopped or is blocked.
