# Design: Slack Tool

**Status:** Implemented
**Last Updated:** 2026-02-06

## Overview

The Slack tools enable the agent to read channels/users/threads and send messages to Slack channels. Uses the `RunContext[CoDeps]` pattern with `ModelRetry` for self-healing errors. Write tools use `requires_approval=True` for human-in-the-loop confirmation; read tools execute without approval.

**Key design decision:** The `WebClient` is created once at startup in `create_deps()` and injected via `CoDeps` — the tool never imports `settings` or creates its own client.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Co CLI Startup                           │
│                                                                  │
│  main.py: create_deps()                                          │
│    │                                                             │
│    └── if settings.slack_bot_token:                              │
│            slack_client = WebClient(token=...)                   │
│    │                                                             │
│    ▼                                                             │
│  CoDeps(slack_client=slack_client)                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            │ deps injected into agent.run()
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Tool Registration                         │
│                                                                  │
│  agent.tool(send_slack_message, requires_approval=True)         │
│    └── Side-effectful: triggers DeferredToolRequests             │
│                                                                  │
│  agent.tool(list_slack_channels)       # read-only, no approval  │
│  agent.tool(list_slack_messages) # read-only, no approval  │
│  agent.tool(list_slack_replies)  # read-only, no approval  │
│  agent.tool(list_slack_users)          # read-only, no approval  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            │ LLM calls tool → pydantic-ai defers for approval
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Approval Flow (main.py)                      │
│                                                                  │
│  _handle_approvals(agent, deps, result, model_settings)         │
│    │                                                             │
│    ├── deps.auto_confirm? ──▶ auto-approve                      │
│    └── Prompt: "Approve send_slack_message(...)? [y/n/a(yolo)]" │
│         ├── y ──▶ approve                                        │
│         ├── a ──▶ enable yolo, approve                           │
│         └── n ──▶ ToolDenied("User denied this action")         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            │ approved → tool executes
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Tool Execution                            │
│                                                                  │
│  send_slack_message(ctx: RunContext[CoDeps], channel, text)     │
│    │                                                             │
│    ├── Validate channel, text (non-empty)                        │
│    ├── client = ctx.deps.slack_client                            │
│    ├── if not client: raise ModelRetry("Not configured")         │
│    └── client.chat_postMessage(channel, text)                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Slack Web API                               │
│                                                                  │
│  chat.postMessage                                                │
│  └── Requires: chat:write bot scope                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Deps Integration

```
┌──────────────────────────────────────────────────────────────────┐
│ main.py: create_deps()                                           │
│                                                                  │
│   slack_client = None                                            │
│   if settings.slack_bot_token:                                   │
│       from slack_sdk import WebClient                            │
│       slack_client = WebClient(token=settings.slack_bot_token)   │
│                                                                  │
│   return CoDeps(                                                 │
│       ...,                                                       │
│       slack_client=slack_client,                                 │
│   )                                                              │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│ CoDeps dataclass                                                 │
│                                                                  │
│   slack_client: Any | None = None   # WebClient at runtime      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Tool accesses via ctx.deps                                       │
│                                                                  │
│   client = ctx.deps.slack_client                                 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Why `Any` type instead of `WebClient`?**
- Avoids making `slack_sdk` a hard import for `deps.py`
- `deps.py` stays lightweight — only imports `SandboxProtocol`
- Type is `slack_sdk.WebClient` at runtime, but typed as `Any` to avoid coupling

**Why build client at startup, not per-request?**
- Avoids re-reading the token on every tool call
- Token absence surfaces immediately at startup
- Follows pydantic-ai pattern: `CoDeps` holds runtime resources

---

## Shared Helpers

### `_get_slack_client(ctx)`

Extracts and validates the Slack `WebClient` from context. Matches `_get_gmail_service` / `_get_calendar_service` pattern.

```python
def _get_slack_client(ctx: RunContext[CoDeps]):
    client = ctx.deps.slack_client
    if not client:
        raise ModelRetry("Slack not configured. Set slack_bot_token in settings or SLACK_BOT_TOKEN env var.")
    return client
```

### `_format_message(msg)`

Formats a single Slack message for display: `YYYY-MM-DD HH:MM <@USER_ID>: text [thread: N replies]`. Truncates long messages to 200 chars. User IDs shown in Slack mention format (`<@U01ABC>`) — no N+1 `users.info` calls.

---

## Tools

### send_slack_message (write, requires approval)

Send a message to a Slack channel. Registered with `requires_approval=True` — approval handled by the chat loop, not the tool.

```
send_slack_message(ctx: RunContext[CoDeps], channel: str, text: str) -> dict[str, Any]

Args:
    channel: Slack channel name (e.g. '#general') or channel ID
    text:    Message text to send

Returns:
    {"display": "Message sent to #general. TS: ...", "channel": "#general", "ts": "..."}

Raises:
    ModelRetry: If not configured, invalid input, or API error
```

#### Processing Flow

```
send_slack_message("#general", "Hello team!")
       │
       ▼
┌──────────────────────────────────────┐
│ Input validation                     │
│   ├── empty channel? ──▶ ModelRetry  │
│   └── empty text?    ──▶ ModelRetry  │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ _get_slack_client(ctx)               │
│   └── None? ──▶ ModelRetry           │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ client.chat_postMessage(             │
│     channel="#general",              │
│     text="Hello team!"              │
│ )                                    │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ Success?                             │
│   ├── Yes ──▶ Return dict with      │
│   │    display, channel, ts          │
│   └── No  ──▶ ModelRetry            │
│        (actionable error hint)       │
└──────────────────────────────────────┘
```

### list_slack_channels (read-only, no approval)

List Slack channels the bot can see.

```
list_slack_channels(ctx: RunContext[CoDeps], limit: int = 20, types: str = "public_channel") -> dict[str, Any]

Args:
    limit: Maximum channels to return (default 20)
    types: Comma-separated channel types (default 'public_channel')

Returns:
    {"display": "Channels (N):\n#name (ID) - purpose\n...", "count": N, "has_more": bool}

Raises:
    ModelRetry: If not configured or API error

Slack API: conversations.list(types=types, limit=limit, exclude_archived=True)
Scope: channels:read
```

### list_slack_messages (read-only, no approval)

Get recent messages from a Slack channel.

```
list_slack_messages(ctx: RunContext[CoDeps], channel: str, limit: int = 15) -> dict[str, Any]

Args:
    channel: Channel ID (e.g. 'C01ABC123'). Use list_slack_channels to find IDs
    limit:   Maximum messages to return (default 15, capped at 50)

Returns:
    {"display": "Messages in C01ABC (N):\nYYYY-MM-DD HH:MM <@U01>: text\n...", "count": N, "has_more": bool}

Raises:
    ModelRetry: If channel empty, not configured, or API error

Slack API: conversations.history(channel=channel, limit=min(limit, 50))
Scope: channels:history
```

**Limit cap:** Hard-capped at 50 to protect the LLM context window from large message dumps.

### list_slack_replies (read-only, no approval)

Get replies in a Slack thread.

```
list_slack_replies(ctx: RunContext[CoDeps], channel: str, thread_ts: str, limit: int = 20) -> dict[str, Any]

Args:
    channel:   Channel ID where the thread lives
    thread_ts: Timestamp of the parent message (e.g. '1234567890.123456')
    limit:     Maximum replies to return (default 20)

Returns:
    {"display": "Thread replies (N):\nYYYY-MM-DD HH:MM <@U01>: text\n...", "count": N, "has_more": bool}

Raises:
    ModelRetry: If channel/thread_ts empty, not configured, or API error

Slack API: conversations.replies(channel=channel, ts=thread_ts, limit=limit)
Scope: channels:history
```

**Note:** Parent message is always the first entry (Slack API default behavior).

### list_slack_users (read-only, no approval)

List active (non-bot, non-deleted) users in the Slack workspace.

```
list_slack_users(ctx: RunContext[CoDeps], limit: int = 30) -> dict[str, Any]

Args:
    limit: Maximum users to return (default 30)

Returns:
    {"display": "Users (N):\n@display_name (ID) - real_name, title\n...", "count": N, "has_more": bool}

Raises:
    ModelRetry: If not configured or API error

Slack API: users.list(limit=limit)
Scope: users:read
```

**Filtering:** Deleted users and bots are excluded from results.

---

## Error Handling

### ModelRetry with Actionable Hints

`SlackApiError` responses are mapped to actionable hint messages via `_SLACK_ERROR_HINTS`. Unknown error codes fall back to the raw error. Generic non-Slack exceptions are caught as a final fallback.

```python
_SLACK_ERROR_HINTS = {
    "channel_not_found": "Channel not found. Check the name or use a channel ID.",
    "not_in_channel":    "Bot is not in this channel. Invite it with /invite @bot.",
    "invalid_auth":      "Slack token is invalid or expired. Refresh slack_bot_token.",
    "ratelimited":       "Rate limited by Slack API. Wait a moment and retry.",
    "no_text":           "Message text cannot be empty.",
    "msg_too_long":      "Message exceeds Slack's length limit. Shorten the text.",
    "thread_not_found":  "Thread not found. Check the thread_ts value.",
}
```

### Error Scenarios

| Scenario | ModelRetry Message | LLM Action |
|----------|--------------------|------------|
| No bot token configured | "Set slack_bot_token or SLACK_BOT_TOKEN" | Inform user to configure |
| Empty channel/text | "Channel is required..." / "Message text cannot be empty." | Fix input and retry |
| Invalid channel | "Channel not found. Check the name or use a channel ID." | Ask user for correct channel |
| Token expired/revoked | "Slack token is invalid or expired. Refresh slack_bot_token." | Inform user to refresh token |
| Rate limited | "Rate limited by Slack API. Wait a moment and retry." | Wait and retry |
| Bot not in channel | "Bot is not in this channel. Invite it with /invite @bot." | Ask user to invite bot |

### Exception Priority

```python
try:
    response = client.chat_postMessage(...)
except ModelRetry:
    raise                    # Re-raise — don't swallow retry signals
except SlackApiError as e:
    # Map to actionable hint
    raise ModelRetry(hint)
except Exception as e:
    # Final fallback for non-Slack errors
    raise ModelRetry(f"Slack API error: {e}")
```

---

## Human-in-the-Loop Confirmation

Sending Slack messages is a high-risk, externally-visible action. Approval uses pydantic-ai's native `DeferredToolRequests` flow.

### How It Works

1. `send_slack_message` is registered with `requires_approval=True` in `agent.py`
2. When the LLM calls the tool, pydantic-ai returns a `DeferredToolRequests` instead of executing
3. The chat loop in `main.py` calls `_handle_approvals()` which prompts: `Approve send_slack_message(channel='#general', text='...')? [y/n/a(yolo)]`
4. On `y` or `a`: tool executes normally
5. On `n`: `ToolDenied("User denied this action")` — LLM sees denial and informs user

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| `requires_approval=True` on registration | Approval logic lives in the chat loop, not inside tools |
| `DeferredToolRequests` flow | Pydantic-ai native — no custom `Confirm.ask()` needed in tools |
| `a` (yolo) option | Sets `deps.auto_confirm = True` for the rest of the session |
| Tool never checks `auto_confirm` | The chat loop handles it — tool code stays pure |

**Bypass:** Set `auto_confirm: true` in settings or `CO_CLI_AUTO_CONFIRM=true` env var. Used by tests and CI.

---

## Configuration

### Settings

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `slack_bot_token` | `SLACK_BOT_TOKEN` | `None` | Slack Bot User OAuth Token |
| `auto_confirm` | `CO_CLI_AUTO_CONFIRM` | `false` | Skip confirmation prompts |

### Example settings.json

```json
{
  "slack_bot_token": "xoxb-your-bot-token"
}
```

### Setup Guide

1. **Create a Slack App** at https://api.slack.com/apps → "Create New App" → "From scratch"
2. **Add Bot Scopes** — Go to "OAuth & Permissions" → "Scopes" → "Bot Token Scopes", add:
   - `chat:write` — send messages
   - `channels:read` — list channels
   - `channels:history` — read channel messages and threads
   - `users:read` — list workspace users
3. **Install to Workspace** — "OAuth & Permissions" → "Install to Workspace" → Authorize
4. **Copy Bot Token** — Copy the `xoxb-...` token from "Bot User OAuth Token"
5. **Configure co-cli** — either:
   - `~/.config/co-cli/settings.json`: `{"slack_bot_token": "xoxb-..."}`
   - Environment variable: `export SLACK_BOT_TOKEN=xoxb-...`
6. **Invite bot to channels** — In Slack, type `/invite @your-bot-name` in each channel the bot should access
7. **Verify** — `uv run co status` should show Slack as connected

### Required Slack Bot Scopes

| Scope | Purpose | Tools |
|-------|---------|-------|
| `chat:write` | Post messages to channels the bot is in | `send_slack_message` |
| `channels:read` | List public channels | `list_slack_channels` |
| `channels:history` | Read messages in channels the bot is in | `list_slack_messages`, `list_slack_replies` |
| `users:read` | List workspace users | `list_slack_users` |

The bot must be invited to a channel before it can read history or post there.

---

## Security Model

### No Secrets in Tools

The tool never imports `settings` or accesses the token directly:

```
settings.json / SLACK_BOT_TOKEN env var
    │
    └── main.py: create_deps()
            │
            └── WebClient(token=...) — token consumed, not stored

Tool sees:
    ctx.deps.slack_client ──▶ Opaque WebClient object (or None)
```

### Read/Write Protection

| Action | Protection |
|--------|------------|
| Send message | `requires_approval=True` → user prompted via `DeferredToolRequests` |
| Read channels/history/users | No approval needed — read-only |
| Channel scope | Bot can only read/post to channels it's been invited to |
| Token scope | Limited to `chat:write`, `channels:read`, `channels:history`, `users:read` (no admin) |

### Graceful Degradation

When Slack is not configured, the tool raises `ModelRetry` instead of crashing:

```
Scenario: No SLACK_BOT_TOKEN configured

User: "send a slack message to #general saying hello"
       │
       ▼
create_deps():
  slack_client = None  (no token)
       │
       ▼
send_slack_message():
  ctx.deps.slack_client is None
  └── raise ModelRetry("Slack not configured. Set slack_bot_token...")
       │
       ▼
Agent ──▶ "Slack is not configured. Set slack_bot_token in
           ~/.config/co-cli/settings.json or SLACK_BOT_TOKEN env var."
```

---

## Testing

### Functional Test (`tests/test_slack.py`)

Test uses the same `Context` dataclass pattern as `test_obsidian.py`:

```python
@dataclass
class Context:
    deps: CoDeps

def _make_ctx(auto_confirm=True, slack_client=None) -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        auto_confirm=auto_confirm,
        slack_client=slack_client,
    ))
```

| Test | What It Verifies |
|------|-----------------|
| `test_slack_post_functional` | Real Slack message posting; accepts `channel_not_found` as proof of auth+API connectivity |
| `test_list_slack_channels` | Lists channels; asserts dict with `display`, `count`, `has_more` |
| `test_list_slack_messages` | Gets channel messages; accepts `channel_not_found` |
| `test_list_slack_replies` | Gets thread replies; accepts `channel_not_found`/`thread_not_found` |
| `test_list_slack_users` | Lists users; asserts dict with `display`, `count`, `has_more` |

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/slack.py` | All Slack tools: `send_slack_message`, `list_slack_channels`, `list_slack_messages`, `list_slack_replies`, `list_slack_users` |
| `co_cli/deps.py` | `CoDeps` with `slack_client` field |
| `co_cli/agent.py` | Tool registration (write tools with approval, read tools without) |
| `tests/test_slack.py` | Functional tests for all Slack tools |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| Cursor pagination | Add cursor params for large workspaces | Not planned |
| User name cache | Cache user ID → display name to resolve mentions in history | Not planned |
| Thread replies (write) | Reply in threads, not top-level | Not planned |
| File upload | Share files via Slack | Not planned |
| Reaction support | Add reactions to messages | Not planned |
| Search messages | `search_messages` using Slack search API | Not planned |
