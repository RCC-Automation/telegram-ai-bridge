# Messaging Bridge Plugin

This plugin exposes Telegram and WhatsApp as tools for the active Codex chat.

It intentionally does not run `codex exec resume`. The legacy Telegram gateway
can still exist as an optional wake adapter, but normal embedded messaging work
uses MCP tools only:

```text
Codex active chat -> MCP tools -> local message hub -> Telegram / WhatsApp
```

## Runtime Dependencies

- Telegram uses the existing local notifier at `http://127.0.0.1:8787`.
- Telegram image attachments are stored under
  `C:\Users\barru\Documents\New project\telegram-messages\images`.
- WhatsApp reads the existing SQLite store under `..\..\whatsapp-mcp`.
- WhatsApp sends through the existing local bridge API at `http://127.0.0.1:8080/api`.

## Telegram Images

Inbound Telegram photos and image documents are downloaded by the notifier and
recorded in inbox messages as:

```json
{
  "type": "image",
  "text": "optional caption",
  "image_path": "C:\\Users\\barru\\Documents\\New project\\telegram-messages\\images\\...",
  "image": {
    "file_id": "...",
    "source_type": "photo"
  }
}
```

Outbound images can be sent through the MCP tool
`messaging_send_telegram_image` or the CLI:

```powershell
py -3 telegram_notify.py send-image C:\path\to\image.png --caption "optional"
```

## WhatsApp Send Policy

WhatsApp sends are blocked unless:

1. the tool call sets `confirm` to `true`, and
2. the recipient is allowlisted.

Allowlist recipients with either environment variable:

```env
MESSAGING_BRIDGE_WHATSAPP_ALLOWED_RECIPIENTS=491234567890,491234567890@s.whatsapp.net
```

or a local policy file:

```json
{
  "whatsapp_allowed_recipients": [
    "491234567890",
    "491234567890@s.whatsapp.net"
  ]
}
```

The policy file path is:

```text
C:\Users\barru\Documents\New project\telegram-messages\messaging-bridge-policy.json
```
