# Telegram Chat Routing

The local Telegram gateway now keeps a persistent chat registry in:

- `C:\Users\barru\Documents\New project\telegram-messages\chats.json`
- `C:\Users\barru\Documents\New project\telegram-messages\active-chat.json`

## Telegram commands

Send these commands from Telegram:

- `/chats`
  - list known chats
- `/whoami`
  - show the current incoming chat and selected active chat
- `/use <alias-or-chat-id>`
  - select the active chat for proactive sends
- `/alias current <name>`
  - give the current chat an alias
- `/alias <chat-id> <name>`
  - give any known chat an alias
- `/voice-status`
  - show whether voice transcription is configured
- `/threads`
  - list known local Codex chat threads
- `/use-thread <alias-or-thread-id-or-thread-name>`
  - route future Telegram requests to a different Codex thread
- `/use-thread latest`
  - route future Telegram requests to the newest discovered local Codex thread
- `/alias-thread <thread-id-or-name> <alias>`
  - give a Codex thread a shorter alias

## Topic split

Telegram bridge work and AppStudio work should stay in separate chats/threads.

- Telegram bridge topic boundary: [TELEGRAM_BRIDGE_TOPIC_BOUNDARY.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/TELEGRAM_BRIDGE_TOPIC_BOUNDARY.md)
- AppStudio starter context: [APPSTUDIO_NEW_CHAT_STARTER.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_NEW_CHAT_STARTER.md)

## Local CLI commands

From the project folder:

```powershell
py -3 telegram_notify.py chats
py -3 telegram_notify.py use raul
py -3 telegram_notify.py whoami
py -3 telegram_notify.py send "hello" --chat raul
py -3 telegram_notify.py voice-status
py -3 telegram_voice_transcribe.py --status
```

## Start both services

Use the integrated launcher:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge"
.\run_telegram_bridge.ps1
```

It starts the notifier. The separate legacy gateway process is disabled by
default because the notifier now owns immediate interactive dispatch itself.

To explicitly start the old standalone gateway loop as well:

```powershell
.\run_telegram_bridge.ps1 -EnableWakeGateway
```

Normal interactive use should not need that flag.

The launcher can manage:

- `telegram_notifier_service.py`
- optional `telegram_codex_gateway.py`

Logs are split because PowerShell does not allow stdout and stderr to redirect to the same file with `Start-Process`:

- `telegram_notifier_service.out.log`
- `telegram_notifier_service.err.log`
- `telegram_codex_gateway.out.log`
- `telegram_codex_gateway.err.log`

Press `Ctrl+C` in that window to stop both.

## Routing behavior

- Replies to incoming Telegram messages go back to the same incoming chat.
- Proactive sends without an explicit `chat_id` use the selected active chat.
- New inbound chats are registered automatically when messages arrive.
- Chats outside the allowlist are registered for discovery, but their messages are not forwarded to Codex until the chat is allowed.
- Voice messages are downloaded and passed through a configured transcription backend before they reach Codex.
- Telegram chat routing and Codex thread routing are separate. `/use` changes the Telegram destination; `/use-thread` changes the Codex conversation that receives the next request.
- Normal conversation must use the notifier's interactive dispatch mode, not heartbeat polling.
- Dispatch is durable: pending messages are stored in `telegram_notifier_inbox.json` and re-queued on notifier restart.

## Codex thread routing

The gateway keeps a persistent Codex-thread registry in:

- `C:\Users\barru\Documents\New project\telegram-messages\codex-threads.json`
- `C:\Users\barru\Documents\New project\telegram-messages\active-codex-thread.json`

The registry is synced from:

- `C:\Users\barru\.codex\session_index.jsonl`
- `C:\Users\barru\.codex\sessions\**\rollout-*.jsonl`

The session-file scan matters because the session index can be stale. `/threads`
therefore shows recent Codex Desktop and gateway-created threads more reliably
than the old index-only implementation.

Current seeded aliases:

- `telegram` -> current Telegram bridge thread
- `appstudio` -> latest AppStudio thread
- `appstudio-alt` -> earlier AppStudio-related thread

Use:

```text
/threads
/use-thread latest
/use-thread appstudio
/use-thread telegram
```

See also:

- [TELEGRAM_BRIDGE_ROBUST_ARCHITECTURE.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/TELEGRAM_BRIDGE_ROBUST_ARCHITECTURE.md)

## Voice messages

The minimum voice pipeline is:

`Telegram voice -> download audio -> transcribe -> queue transcript as a normal request`

Audio files are stored under:

- `C:\Users\barru\Documents\New project\telegram-messages\voice`

Configure one transcription backend:

```env
TELEGRAM_VOICE_TRANSCRIBE_COMMAND=your-local-command "{audio_path}"
```

The command must print the transcript to stdout.

For a no-extra-cost local backend, the project includes:

- `C:\Users\barru\Documents\New project\telegram-ai-bridge\telegram_voice_transcribe_local.py`

Configure:

```env
TELEGRAM_VOICE_TRANSCRIBE_COMMAND=py -3 "C:\Users\barru\Documents\New project\telegram-ai-bridge\telegram_voice_transcribe_local.py" "{audio_path}"
TELEGRAM_LOCAL_WHISPER_MODEL=base
```

The local backend is installed into the project-local `.vendor_py` folder. To reinstall or upgrade it:

```powershell
py -3 -m pip install --target ".vendor_py" faster-whisper
```

Models are downloaded on first use into:

- `C:\Users\barru\Documents\New project\telegram-messages\models\faster-whisper`

Use `tiny` for speed or `base` for better quality on short messages.

Alternatively, configure OpenAI transcription explicitly:

```env
OPENAI_API_KEY=...
TELEGRAM_VOICE_TRANSCRIBE_MODEL=<your-transcription-model>
```

If no backend is configured, voice messages are still downloaded, but Codex receives a clear placeholder saying transcription is unavailable.

To retry a downloaded voice file after configuring a backend:

```powershell
py -3 telegram_voice_transcribe.py "C:\Users\barru\Documents\New project\telegram-messages\voice\some-file.oga"
```

The currently seeded alias is:

- `raul` -> `1049665217`
