# Telegram Service Manager

This project now includes a manager for the Telegram notifier:

```text
C:\Users\barru\Documents\New project\telegram-ai-bridge\telegram_service_manager.py
```

Its purpose is to let Codex start, stop, restart, and verify the Telegram notifier without manual user intervention.

## Commands

Run from:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge"
```

Status:

```powershell
py -3 .\telegram_service_manager.py status --json
```

The status output includes:

- notifier HTTP health
- TCP reachability
- heartbeat freshness
- delivery mode
- dispatch queue counts
- gateway lock owner/age
- transcript file status

Start:

```powershell
py -3 .\telegram_service_manager.py start --json
```

Stop:

```powershell
py -3 .\telegram_service_manager.py stop --json
```

Stop first calls the notifier's authenticated local `/shutdown` endpoint. That
is the normal path. `taskkill` is only a cleanup fallback and may be denied by
Windows policy.

Restart:

```powershell
py -3 .\telegram_service_manager.py restart --json
```

Ensure running:

```powershell
py -3 .\telegram_service_manager.py ensure --json
```

## Current Working Backend

The manager first tries to use a Windows Scheduled Task named:

```text
CodexTelegramNotifier
```

On this machine, Windows currently denies task creation from Codex:

```text
ERROR: Access is denied.
```

The manager therefore falls back to a direct detached process backend. This backend worked when launched with host-level execution permission and passed health checks on:

```text
http://127.0.0.1:8787/health
```

Important: the notifier must run with normal host networking. Starting it from
inside a restricted Codex command sandbox can make Telegram polling fail with
Windows socket errors such as `10013`. The manager command should therefore be
run with host permission when starting or restarting the notifier.

The manager writes the detached process PID to:

```text
telegram_notifier_restart_pid.txt
```

and reads the service heartbeat from:

```text
C:\Users\barru\Documents\New project\telegram-messages\bridge-heartbeats\telegram_notifier.json
```

## Interactive Telegram Mode

The notifier is configured for interactive dispatch:

```env
TELEGRAM_NOTIFIER_INTERACTIVE_CODEX=true
TELEGRAM_NOTIFIER_INTERACTIVE_NO_ACK=true
```

This means the notifier itself receives Telegram updates and immediately dispatches allowed messages to Codex. The old separate `telegram_codex_gateway.py` inbox poller should not be needed for normal interactive use.

Telegram receiving uses Bot API long polling:

```env
TELEGRAM_NOTIFIER_LONG_POLL_TIMEOUT_SECONDS=30
```

This is not a 30-second polling delay. Telegram holds the request open and
returns immediately when a message arrives. The longer timeout reduces request
churn while keeping message latency immediate.

The normal delivery configuration is:

```env
TELEGRAM_GATEWAY_DELIVERY_MODE=active-app
TELEGRAM_GATEWAY_FALLBACK_TO_RESUME=true
```

The bridge first tries the Codex app-server protocol and falls back to
`codex exec resume` if the desktop control socket is unavailable.

On Windows, Codex subprocesses are started without a visible console window.

## MCP Tools

The messaging bridge MCP server now exposes service-management tools after Codex reloads:

```text
messaging_telegram_service_status
messaging_telegram_service_start
messaging_telegram_service_stop
messaging_telegram_service_restart
messaging_telegram_service_ensure
```

If the current Codex session has an old MCP server process, restart Codex Desktop to reload these tool definitions.

## Operational Rule

There must be exactly one Telegram `getUpdates` receiver for the bot. If Telegram logs:

```text
409 Conflict: terminated by other getUpdates request
```

then another notifier/poller is still running and must be stopped before starting a new one.

Normal conversation must not use Codex heartbeat automations. Heartbeats are too
slow and create duplicate routing semantics. Use the notifier's interactive
dispatch instead.
