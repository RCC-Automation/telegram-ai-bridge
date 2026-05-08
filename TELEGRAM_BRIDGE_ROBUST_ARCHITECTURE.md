# Telegram Bridge Robust Architecture

This document is the source of truth for the current Telegram-to-Codex bridge.

## Goal

Telegram should behave like an immediate input channel for Codex:

```text
Telegram message
  -> exactly one Telegram receiver
  -> durable local inbox / dispatch state
  -> selected Codex thread
  -> assistant final answer
  -> same answer sent back to Telegram
  -> transcript persisted locally
```

Important: for Telegram-originated turns, the assistant should not manually
send the normal final answer through `telegram_notify.py`. The gateway captures
the final Codex answer and sends it to Telegram automatically. Manual notifier
sends are only for extra proactive messages that are separate from the final
answer.

The bridge must not drop messages because Codex is busy, because a stale lock
exists, or because the desktop app control socket is unavailable.

## Current Components

- `telegram_notifier_service.py`
  - Owns Telegram `getUpdates` through Telegram long polling.
  - Exposes local HTTP send/inbox/health endpoints on `127.0.0.1:8787`.
  - Stores inbound messages in `telegram_notifier_inbox.json`.
  - Runs the interactive dispatcher when `TELEGRAM_NOTIFIER_INTERACTIVE_CODEX=true`.

- `telegram_codex_gateway.py`
  - Receives dispatches from the notifier.
  - Handles Telegram commands such as `/threads` and `/use-thread`.
  - Routes normal messages to Codex.
  - Sends the final Codex answer back to Telegram.

- `codex_app_server_bridge.py`
  - Uses `codex app-server proxy`.
  - Attempts to resume a selected Codex thread and start a turn through the
    app-server protocol.
  - Captures the final assistant answer from app-server events.

- `telegram_service_manager.py`
  - Starts/stops/restarts the notifier.
  - Reports receiver health, dispatch mode, inbox queue state, gateway lock
    state, and transcript status.

- `telegram_bridge_transcript.py`
  - Appends bridge events to:
    `C:\Users\barru\Documents\New project\telegram-messages\telegram-codex-transcript.jsonl`

## Delivery Modes

Configured in `.env`:

```env
TELEGRAM_GATEWAY_DELIVERY_MODE=active-app
TELEGRAM_GATEWAY_FALLBACK_TO_RESUME=true
```

### `active-app`

Primary route. The bridge tries:

```text
codex app-server proxy
  -> thread/resume selected thread
  -> turn/start Telegram prompt
  -> capture final assistant message
  -> send final text back to Telegram
```

This is the correct architectural path for recording the turn through the
Codex app-server protocol. It depends on the Codex Desktop app exposing a
healthy app-server control socket.

Verified current machine state:

```text
C:\Users\barru\.codex\app-server-control\app-server-control.sock
```

currently fails with Windows socket error `10050`. The desktop app does run a
child `codex.exe app-server --analytics-default-enabled`, but that child is
attached to the Electron app over its own stdio channel and is not reachable
through `app-server proxy` from an external process in this run. When that
happens, the bridge falls back instead of losing the Telegram message.

### `resume`

Fallback route:

```text
codex exec resume <selected-thread-id> <telegram prompt>
```

This records the message and answer in the selected Codex thread's persisted
session history and returns the final answer to Telegram. It may not live-update
the currently visible Codex Desktop window until that thread is opened or
refreshed by the app.

The fallback is started hidden on Windows so the user does not see an extra
console window for each Telegram message.

## Queue Semantics

Inbound messages are never treated as fire-and-forget.

Each stored inbox message has dispatch state:

```json
{
  "dispatch": {
    "status": "pending | processing | done | error",
    "attempts": 0,
    "started_at": 0,
    "finished_at": 0
  }
}
```

On notifier startup, pending messages are re-queued. If a processing message is
stale, it is reset to pending.

## Lock Semantics

The gateway lock prevents simultaneous Codex runs. A lock is removed when:

- its age exceeds `TELEGRAM_GATEWAY_LOCK_MAX_AGE_SECONDS`, or
- its owner PID no longer exists.

The dispatcher waits for a lock instead of skipping the message.

Relevant settings:

```env
TELEGRAM_GATEWAY_LOCK_MAX_AGE_SECONDS=900
TELEGRAM_GATEWAY_LOCK_WAIT_SECONDS=1800
```

## Thread Routing

Telegram commands:

```text
/threads
/use-thread <alias-or-thread-id-or-thread-name>
/use-thread latest
/alias-thread <thread-id-or-name> <alias>
/whoami
```

The selected thread is stored in:

```text
C:\Users\barru\Documents\New project\telegram-messages\active-codex-thread.json
```

Important: there is no confirmed stable Codex Desktop API yet that means
"whatever chat is visually selected right now" from an external process. The
professional routing mechanism is therefore an explicit selected Codex thread,
with `/use-thread latest` as a practical shortcut.

The thread registry is built from both:

```text
C:\Users\barru\.codex\session_index.jsonl
C:\Users\barru\.codex\sessions\**\rollout-*.jsonl
```

The session-file scan is required because `session_index.jsonl` can lag behind
recent desktop or gateway-created threads.

## Verification Commands

Status:

```powershell
py -3 .\telegram_service_manager.py status --json
```

Send test:

```powershell
py -3 .\telegram_notify.py send "Bridge test. Reply with: robust bridge test"
```

Read inbox:

```powershell
py -3 .\telegram_notify.py inbox
```

Restart:

```powershell
py -3 .\telegram_service_manager.py restart --json
```

## Non-Negotiable Operating Rules

- Run exactly one Telegram `getUpdates` receiver.
- Do not run the old `telegram_ai_bridge.py` poller beside the notifier.
- Do not use heartbeat automations for normal conversation.
- Do not drop messages when Codex is busy; queue and retry.
- Keep `TELEGRAM_NOTIFIER_INTERACTIVE_CODEX=true` for immediate dispatch.
- Use `/use-thread` to switch the Codex conversation targeted by Telegram.
