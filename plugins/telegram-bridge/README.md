# Telegram Bridge Plugin

This plugin is an independent Codex-facing management layer for the existing local Telegram bridge.

It does not replace or modify the currently working runtime files:

- `telegram_notifier_service.py`
- `telegram_codex_gateway.py`
- `run_telegram_bridge.ps1`
- `telegram_chat_registry.py`
- `codex_thread_registry.py`

The plugin calls those files through a small management wrapper so Codex can use structured operations instead of ad hoc shell commands.

## Current Scope

- Show bridge status.
- List Telegram chats.
- List Codex threads.
- Switch active Codex thread.
- Switch active Telegram chat.
- Check voice transcription status.
- Read recent logs.
- Start, stop, and restart the bridge through the existing PowerShell launcher.
- Report service health from heartbeat files written by the notifier and gateway.

## Important Constraint

Telegram still requires one long-running listener process. The plugin can manage that process, but it cannot remove the need for a running bridge service.

## Heartbeats

After the bridge is restarted with the current code, each service writes a heartbeat file under:

```text
C:\Users\barru\Documents\New project\telegram-messages\bridge-heartbeats
```

The plugin `status` command treats heartbeats newer than 20 seconds as healthy. This is more reliable than PID files, which can become stale after restarts.

## Manual Smoke Test

From the repository root:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge"
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py status
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py threads
py -3 .\plugins\telegram-bridge\scripts\telegram_bridge_manager.py chats
```
