# Codex Plugin And MCP Integration Guide

This note captures the practical lesson from integrating the local messaging bridge into Codex Desktop.

The important conclusion is simple:

- A Codex plugin package is useful for distribution, skills, UI metadata, and marketplace discovery.
- An MCP server registration is the reliable way to expose callable tools inside the active Codex chat.
- A marketplace entry alone does not make tools callable.

## Working Integration Pattern

For a local tool bridge that Codex must call directly, register it as an MCP server in:

```text
C:\Users\barru\.codex\config.toml
```

Example:

```toml
[mcp_servers.messaging_bridge]
command = 'py'
args = ['-3', 'C:\\Users\\barru\\Documents\\New project\\telegram-ai-bridge\\plugins\\messaging-bridge\\mcp\\messaging_bridge_mcp.py']
cwd = 'C:\\Users\\barru\\Documents\\New project\\telegram-ai-bridge\\plugins\\messaging-bridge'
startup_timeout_sec = 15
tool_timeout_sec = 60
enabled = true
```

After editing `config.toml`, fully restart Codex Desktop. Tool discovery is resolved at session startup, so an already-running chat usually will not see the new MCP tools.

Validate from Codex after restart with tool discovery:

```text
Search for: messaging_bridge messaging_status telegram whatsapp MCP
```

When successful, tools appear under a namespace like:

```text
mcp__messaging_bridge__
```

For the messaging bridge, the exposed tools were:

```text
messaging_status
messaging_send_telegram
messaging_read_telegram_inbox
messaging_clear_telegram_inbox
messaging_list_telegram_chats
messaging_send_whatsapp
messaging_search_whatsapp_contacts
messaging_read_whatsapp_messages
messaging_diagnostics
```

## Plugin Package Pattern

A plugin folder should still be kept for structure and portability:

```text
plugins/<plugin-name>/
  .codex-plugin/plugin.json
  .mcp.json
  skills/
  mcp/
  README.md
```

The plugin manifest describes the plugin:

```json
{
  "name": "messaging-bridge",
  "version": "0.1.0",
  "description": "Embedded Codex messaging tools for Telegram and WhatsApp.",
  "skills": "./skills/",
  "mcpServers": "./.mcp.json",
  "interface": {
    "displayName": "Messaging Bridge",
    "category": "Productivity"
  }
}
```

The plugin `.mcp.json` describes the MCP server relative to the plugin folder:

```json
{
  "mcpServers": {
    "messaging-bridge": {
      "command": "py",
      "args": [
        "-3",
        "./mcp/messaging_bridge_mcp.py"
      ]
    }
  }
}
```

This package structure is good, but it is not enough by itself to expose tools in an active Codex chat.

## Marketplace Pattern

A local marketplace file can make a plugin discoverable in plugin UI flows:

```text
<marketplace-root>\.agents\plugins\marketplace.json
```

Example:

```json
{
  "name": "local-messaging-bridge",
  "interface": {
    "displayName": "Local Messaging Bridge"
  },
  "plugins": [
    {
      "name": "messaging-bridge",
      "source": {
        "source": "local",
        "path": "./telegram-ai-bridge/plugins/messaging-bridge"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

Registering a marketplace is done with:

```powershell
codex plugin marketplace add "C:\path\to\marketplace-root"
```

However, marketplace registration is not the same as MCP tool activation. If the goal is callable tools inside the current chat, use direct `[mcp_servers.<name>]` registration.

## What Did Not Work

Do not manually copy plugin files into:

```text
C:\Users\barru\.codex\plugins\cache\...
```

That cache is managed by Codex. Manual copies can have the wrong marketplace/version shape and may not be indexed. In our case, copying to:

```text
~\.codex\plugins\cache\openai-curated\messaging-bridge
```

did not expose the tools.

Also avoid manually adding fake plugin enablement entries such as:

```toml
[plugins."messaging-bridge@openai-curated"]
enabled = true
```

unless the plugin is actually installed by Codex through the supported plugin flow.

## Validation Checklist

1. Confirm the MCP server script runs directly.

```powershell
py -3 -m py_compile "C:\path\to\plugin\mcp\server.py"
```

2. Confirm the server answers MCP JSON-RPC if needed.

Minimum methods:

```text
initialize
tools/list
tools/call
```

3. Add `[mcp_servers.<name>]` to `~\.codex\config.toml`.

4. Fully restart Codex Desktop.

5. Use tool discovery from the new chat/session.

6. Call a harmless status tool first.

7. Only then test write/send operations.

## Design Rule For Future Plugins

If the plugin contains tools Codex must call during a conversation, treat MCP registration as the source of truth.

Use the plugin package and marketplace files for documentation, skills, metadata, and eventual installation UX, but do not depend on them for immediate tool availability unless Codex has clearly installed and loaded the plugin.

## References

- Codex MCP documentation: https://developers.openai.com/codex/mcp
- Codex plugin documentation: https://developers.openai.com/codex/plugins
- Build Codex plugins: https://developers.openai.com/codex/plugins/build
- Codex config reference: https://developers.openai.com/codex/config-reference
