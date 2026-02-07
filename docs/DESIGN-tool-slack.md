# Design: Slack Tool

**Status:** Implemented
**Last Updated:** 2026-02-06

## Overview

The Slack tool enables the agent to send messages to Slack channels on behalf of the user. Uses the `RunContext[CoDeps]` pattern with `ModelRetry` for self-healing errors and `requires_approval=True` for human-in-the-loop confirmation via pydantic-ai's `DeferredToolRequests` flow.

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
│  agent.tool(post_slack_message, requires_approval=True)         │
│    └── Side-effectful: triggers DeferredToolRequests             │
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
│    └── Prompt: "Approve post_slack_message(...)? [y/n/a(yolo)]" │
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
│  post_slack_message(ctx: RunContext[CoDeps], channel, text)     │
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
- `deps.py` stays lightweight — only imports `Sandbox`
- Type is `slack_sdk.WebClient` at runtime, but typed as `Any` to avoid coupling

**Why build client at startup, not per-request?**
- Avoids re-reading the token on every tool call
- Token absence surfaces immediately at startup
- Follows pydantic-ai pattern: `CoDeps` holds runtime resources

---

## Tool

### post_slack_message (`co_cli/tools/slack.py`)

Send a message to a Slack channel. Registered with `requires_approval=True` — approval handled by the chat loop, not the tool.

```
post_slack_message(ctx: RunContext[CoDeps], channel: str, text: str) -> dict[str, Any]

Args:
    channel: Slack channel name (e.g. '#general') or channel ID
    text:    Message text to send

Returns:
    {"display": "Message sent to #general. TS: ...", "channel": "#general", "ts": "..."}

Raises:
    ModelRetry: If not configured, invalid input, or API error
```

### Processing Flow

```
post_slack_message("#general", "Hello team!")
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
│ client = ctx.deps.slack_client       │
│   └── None? ──▶ ModelRetry           │
│        "Slack not configured.        │
│         Set slack_bot_token in       │
│         settings or SLACK_BOT_TOKEN  │
│         env var."                    │
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

1. `post_slack_message` is registered with `requires_approval=True` in `agent.py`
2. When the LLM calls the tool, pydantic-ai returns a `DeferredToolRequests` instead of executing
3. The chat loop in `main.py` calls `_handle_approvals()` which prompts: `Approve post_slack_message(channel='#general', text='...')? [y/n/a(yolo)]`
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

### Required Slack Bot Scopes

| Scope | Purpose |
|-------|---------|
| `chat:write` | Post messages to channels the bot is in |

The bot must be invited to a channel before it can post there.

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

### Write Protection

| Action | Protection |
|--------|------------|
| Send message | `requires_approval=True` → user prompted via `DeferredToolRequests` |
| Channel scope | Bot can only post to channels it's been invited to |
| Token scope | Limited to `chat:write` (no admin, no read history) |

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
post_slack_message():
  ctx.deps.slack_client is None
  └── raise ModelRetry("Slack not configured. Set slack_bot_token...")
       │
       ▼
Agent ──▶ "Slack is not configured. Set slack_bot_token in
           ~/.config/co-cli/settings.json or SLACK_BOT_TOKEN env var."
```

---

## Testing

### Functional Test (`tests/test_google_cloud.py`)

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

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/slack.py` | `post_slack_message` tool |
| `co_cli/deps.py` | `CoDeps` with `slack_client` field |
| `co_cli/agent.py` | Tool registration with `requires_approval=True` |
| `tests/test_google_cloud.py` | Functional test for Slack posting |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| Read tools | `list_channels`, `read_channel_messages`, `search_messages` | Not started |
| Thread replies | Reply in threads, not top-level | Not planned |
| File upload | Share files via Slack | Not planned |
| Reaction support | Add reactions to messages | Not planned |
