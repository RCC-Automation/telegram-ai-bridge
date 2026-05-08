# Telegram Desktop Bridge Handoff

## Current State

This project is a Telegram-to-local-desktop bridge that lets Telegram messages trigger actions on this Windows computer.

There are now three major layers:

1. `telegram_ai_bridge.py`
   Receives Telegram messages, routes desktop-capable requests to the local desktop agent, and falls back to Codex CLI for broader task handling.

2. `desktop_agent.py`
   Shared local agent that:
   - parses natural-language desktop requests
   - supports chained actions like `open notepad then type hello`
   - keeps simple per-session task state
   - supports `status` and `continue`
   - can use a localhost broker/host for visible desktop actions

3. Desktop host / broker
   Preferred:
   - `desktop_host_app.py`
     Small interactive desktop host window that runs the localhost broker in a visible desktop-owned app.

   Alternate:
   - `desktop_broker.py`
     Console broker. Less preferred for visible desktop interaction.

There is also:

- `desktop_agent_cli.py`
  Lets us test the desktop agent directly from terminal/chat.

- `desktop_mcp_server.py`
  Minimal MCP server exposing desktop agent / broker tools over stdio.

## What Works

- Telegram bridge starts and can process messages when network/DNS is available.
- Desktop host app runs and exposes the broker on `http://127.0.0.1:8765`.
- Desktop agent can:
  - open apps, files, folders, URLs
  - open a file in an app
  - run commands
  - type text
  - send keys
  - focus windows
  - move/click/double-click/right-click/scroll
  - take screenshots
  - chain multiple steps in one request
- Optional screenshot confirmation works when requested with phrases like:
  - `with screenshot`
  - `confirm with screenshot`
  - `verify with screenshot`
- Stateful task sessions work:
  - `status`
  - `continue`
- MCP server responds to:
  - `initialize`
  - `tools/list`
  - `tools/call`

## Important Successful Test

From this chat, through the desktop host app path, the system successfully:

- opened `desktop-agent-notepad-test.txt` in Notepad
- focused Notepad
- typed text into it

This was the first strong sign that the interactive desktop host app solved the earlier visibility/focus problem better than the plain broker process.

## Known Problems / Weak Spots

1. Network / DNS instability
   The Telegram bridge sometimes fails with DNS lookup or timeout errors.
   This appears to be a machine/network issue, not purely a bot logic issue.
   The bridge was patched to retry and log clearer network messages.

2. Window observation is still imperfect
   Window enumeration and active-window reporting are better with the desktop host app, but still noisy.
   Example:
   - active window names are sometimes reported as `powershell: ...`
   - title/process mapping still needs refinement

3. Web tasks are still shallow
   Example:
   - `open YouTube and look for videos about X`
   currently turns into a generic Google search rather than a proper YouTube-native search flow.

4. Full computer-use loop is not finished
   The agent now has:
   - action tools
   - limited observation
   - persistent state
   - MCP exposure

   But it is still not a truly robust human-level desktop operator with:
   - screenshot understanding
   - UI element targeting
   - iterative reasoning based on visual state

## Key Files

- [telegram_ai_bridge.py](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/telegram_ai_bridge.py)
- [desktop_agent.py](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/desktop%20engine/desktop_agent.py)
- [desktop_host_app.py](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/desktop%20engine/desktop_host_app.py)
- [desktop_broker.py](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/desktop%20engine/desktop_broker.py)
- [desktop_agent_cli.py](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/desktop%20engine/desktop_agent_cli.py)
- [desktop_mcp_server.py](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/desktop%20engine/desktop_mcp_server.py)
- [README.md](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/README.md)
- [desktop_agent_state.json](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/desktop%20engine/desktop_agent_state.json)
- [bridge.log](/C:/Users/barru/Documents/New%20project/telegram-ai-bridge/bridge.log)

## Best Way To Run It Next Time

1. Start the interactive desktop host app:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge\desktop engine"
py .\desktop_host_app.py
```

2. Start the Telegram bridge:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge"
py .\telegram_ai_bridge.py
```

3. Optionally test from terminal/chat:

```powershell
cd "C:\Users\barru\Documents\New project\telegram-ai-bridge\desktop engine"
py .\desktop_agent_cli.py --broker-url "http://127.0.0.1:8765" "open notepad then type hello with screenshot"
```

## Recommended Next Steps

1. Improve web-intent parsing
   Turn requests like `open YouTube and search for X` into direct YouTube search URLs or browser-native flows.

2. Improve active window / window list accuracy
   Refine process/title labeling so observations are less misleading.

3. Add screenshot-driven verification helpers
   Right now screenshots can be taken and saved, but not interpreted automatically.
   The next step is to use screenshot artifacts more actively in task verification.

4. Add richer iterative task execution
   Build a loop that can:
   - act
   - observe
   - decide next action
   - continue until the task is done

5. Decide whether to deepen MCP integration
   The MCP server is minimal but working. It can be expanded once the desktop toolset stabilizes.

## Resume Prompt

To resume quickly next time, say:

`Continue the Telegram desktop bridge project. Read NEXT_STEPS.md and current state, then continue from there.`
