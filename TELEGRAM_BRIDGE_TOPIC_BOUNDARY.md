# Telegram Bridge Topic Boundary

This file defines what belongs in the Telegram bridge chat versus the AppStudio chat.

## Telegram Bridge Chat

Use this chat for:

- Telegram notifier service
- Telegram Codex gateway
- chat registry and aliases
- active chat selection
- voice-message download
- local transcription backend
- bridge restart scripts
- desktop host / Codex gateway mechanics
- Telegram command handling such as `/chats`, `/whoami`, `/use`, `/alias`, and `/voice-status`

Main reference:

- [TELEGRAM_CHAT_ROUTING.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/TELEGRAM_CHAT_ROUTING.md)

## AppStudio Chat

Use the dedicated AppStudio chat for:

- AppStudio project metadata
- AppStudio source projects under `artifacts\AppStudio`
- project specs under `project_specs`
- `.asppag` package generation
- `appstudio_gen`
- AppStudio components, containers, and layouts
- `CustomFunctions`
- `UserFunction`, `Instance`, `API`, and `RWS`
- RAPID/RWS/controller integration
- RW8 compatibility

Starter context:

- [APPSTUDIO_NEW_CHAT_STARTER.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/APPSTUDIO_NEW_CHAT_STARTER.md)

## Practical Routing Limitation

Only one Telegram chat is currently registered:

- `1049665217`, alias `raul`

I cannot create a new Telegram private chat or group from here. To create a separate Telegram channel for AppStudio:

1. Create a new Telegram group or chat with the bot.
2. Send any message there.
3. Run `/alias current appstudio` in that new chat.
4. Use `/use appstudio` when proactive bot replies should go there.

Until a second Telegram chat exists, the clean separation is done by topic discipline and by starting a separate Codex/AppStudio thread using `APPSTUDIO_NEW_CHAT_STARTER.md`.
