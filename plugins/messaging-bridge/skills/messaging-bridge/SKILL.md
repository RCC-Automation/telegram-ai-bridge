---
name: messaging-bridge
description: Use Telegram and WhatsApp as embedded messaging tools inside the active Codex chat through the local Messaging Bridge MCP plugin.
---

# Messaging Bridge

Use this skill when the user wants Codex to communicate through Telegram or
WhatsApp without spawning a separate Codex run.

## Operating Model

- Prefer the `messaging-bridge` MCP tools over shell scripts.
- Treat Telegram and WhatsApp as embedded tools in the current Codex chat.
- Do not use `telegram_codex_gateway.py` or `codex exec resume` for normal
  messaging work.
- Use the legacy gateway only when the user explicitly asks for unattended
  Telegram-to-Codex wakeup.

## Safety Rules

- Telegram sends must go through the local notifier and configured chat registry.
- WhatsApp sends require an explicit user confirmation and an allowlisted
  recipient.
- If a transport is down, report the transport status and the local command the
  user can run to start it.
- Do not send WhatsApp messages to arbitrary contacts inferred from text.

## Normal Workflow

1. Call `messaging_status` before debugging transport problems.
2. Use `messaging_read_telegram_inbox` to inspect pending Telegram messages.
3. Use `messaging_send_telegram` for outbound Telegram messages.
4. Use `messaging_send_telegram_image` for outbound local image files.
5. When Telegram inbox messages include `media.type == "image"`, inspect
   `media.image_path` before answering image-specific questions.
6. Use `messaging_search_whatsapp_contacts` before discussing WhatsApp sends.
7. Use `messaging_send_whatsapp` only with `confirm: true` and an allowlisted
   recipient.
