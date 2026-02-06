# Design: Slack Tool

**Status:** Implemented (Batch 4)
**Last Updated:** 2026-02-05

## Overview

The Slack tool enables the agent to send messages to Slack channels on behalf of the user. Uses the `RunContext[CoDeps]` pattern with `ModelRetry` for self-healing errors and `rich.prompt.Confirm` for human-in-the-loop confirmation before sending.

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
│                        Tool Execution                            │
│                                                                  │
│  post_slack_message(ctx: RunContext[CoDeps], channel, text)     │
│    │                                                             │
│    ├── client = ctx.deps.slack_client                            │
│    ├── if not client: raise ModelRetry("Not configured")         │
│    ├── Confirm.ask("Send to #channel?")                         │
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

Send a message to a Slack channel. Requires human-in-the-loop confirmation.

```
post_slack_message(ctx: RunContext[CoDeps], channel: str, text: str) -> str

Args:
    channel: Slack channel name (e.g. '#general') or channel ID
    text:    Message text to send

Returns:
    Success message with timestamp, or "cancelled by user"

Raises:
    ModelRetry: If not configured or API error
```

### Processing Flow

```
post_slack_message("#general", "Hello team!")
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
│ auto_confirm = true?                 │
│   ├── Yes ──▶ Skip prompt            │
│   └── No  ──▶ Confirm.ask(          │
│        "Send Slack message to        │
│         #general?")                  │
│              │                       │
│         ┌────┴────┐                  │
│         ▼         ▼                  │
│       "y"        "n"                 │
│         │         │                  │
│         │    Return "Slack post      │
│         │     cancelled by user."    │
│         ▼                            │
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
│   ├── Yes ──▶ Return "Message sent  │
│   │    to #general. TS: 12345..."    │
│   └── No  ──▶ ModelRetry            │
│        "Slack API error: ..."        │
└──────────────────────────────────────┘
```

---

## Error Handling with ModelRetry

Uses the `ModelRetry` re-raise pattern to prevent generic `except` blocks from swallowing retry signals:

```
try:
    response = client.chat_postMessage(channel=channel, text=text)
    return f"Message sent to {channel}. TS: {response['ts']}"
except ModelRetry:
    raise                          # <-- Re-raise, don't swallow
except Exception as e:
    raise ModelRetry(f"Slack API error: {e}")
```

### Error Scenarios

| Scenario | ModelRetry Message | LLM Action |
|----------|--------------------|------------|
| No bot token configured | "Set slack_bot_token or SLACK_BOT_TOKEN" | Inform user to configure |
| Invalid channel | "Slack API error: channel_not_found" | Ask user for correct channel |
| Token expired/revoked | "Slack API error: invalid_auth" | Inform user to refresh token |
| Rate limited | "Slack API error: ratelimited" | Wait and retry |
| Bot not in channel | "Slack API error: not_in_channel" | Ask user to invite bot |

**Why ModelRetry over error strings?**

```
# Bad — LLM sees error text, has to guess what to do
return f"Slack error: {e}"

# Good — LLM gets structured retry with guidance
raise ModelRetry(f"Slack API error: {e}")
```

---

## Human-in-the-Loop Confirmation

Sending Slack messages is a high-risk, externally-visible action. Confirmation is always required (unless `auto_confirm=true`).

### Confirmation Pattern

```python
if not ctx.deps.auto_confirm:
    if not Confirm.ask(
        f"Send Slack message to [bold]{channel}[/bold]?",
        default=False,
        console=_console,
    ):
        return "Slack post cancelled by user."
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| `rich.prompt.Confirm` not `typer.confirm` | Works correctly with async chat loop |
| `default=False` | Safe default: user must explicitly type "y" |
| `console=_console` | Module-level Console for consistent Rich output |
| Returns string on cancel | LLM sees "cancelled", can inform user naturally |
| Bold channel in prompt | User clearly sees where the message goes |

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
| Send message | User confirmation required |
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

## Migration from Legacy Pattern

### Before (Batch 1-2 era, in `comm.py`)

```python
# comm.py — BAD: imports settings, builds client per-call, typer.confirm
from co_cli.config import settings

def post_slack_message(channel: str, text: str) -> str:  # tool_plain
    token = settings.slack_bot_token                      # Global import
    if not token:
        return "Error: SLACK_BOT_TOKEN not found..."      # Error string

    if not settings.auto_confirm:                         # settings.auto_confirm
        if not typer.confirm(f"Send Slack message to {channel}?"):
            return "Slack post cancelled by user."

    client = WebClient(token=token)                       # Built per-call
    response = client.chat_postMessage(...)
    return f"Message sent to {channel}. TS: {response['ts']}"
```

### After (Batch 4, in `slack.py`)

```python
# slack.py — GOOD: no settings import, client from deps, ModelRetry
from pydantic_ai import RunContext, ModelRetry
from co_cli.deps import CoDeps

def post_slack_message(ctx: RunContext[CoDeps], channel: str, text: str) -> str:
    client = ctx.deps.slack_client                         # From deps
    if not client:
        raise ModelRetry("Slack not configured...")        # Self-healing

    if not ctx.deps.auto_confirm:                          # From deps
        if not Confirm.ask(f"Send to {channel}?"):
            return "Slack post cancelled by user."

    response = client.chat_postMessage(...)
    return f"Message sent to {channel}. TS: {response['ts']}"
```

### What Changed

| Aspect | Before | After |
|--------|--------|-------|
| File | `comm.py` (shared with Gmail, Calendar) | `slack.py` (dedicated) |
| Registration | `agent.tool_plain()` | `agent.tool()` |
| First param | None | `ctx: RunContext[CoDeps]` |
| Client access | `WebClient(token=settings.slack_bot_token)` per call | `ctx.deps.slack_client` from deps |
| Settings import | `from co_cli.config import settings` | None |
| Error handling | Return error strings | `raise ModelRetry(...)` |
| Confirmation | `typer.confirm()` | `rich.prompt.Confirm` |
| auto_confirm | `settings.auto_confirm` | `ctx.deps.auto_confirm` |

---

## Testing

### Functional Test (`tests/test_cloud.py`)

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

Test skips gracefully when Slack is unavailable:

| Test | Skip Condition | What It Verifies |
|------|---------------|-----------------|
| `test_slack_post_functional` | No `SLACK_BOT_TOKEN` | Real Slack message posting |

The test accepts `channel_not_found` as a successful outcome — it proves authentication and API connectivity work even if the test channel doesn't exist.

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/slack.py` | `post_slack_message` tool |
| `co_cli/deps.py` | `CoDeps` with `slack_client` field |
| `tests/test_cloud.py` | Functional test for Slack posting |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| `requires_approval=True` | Pydantic-ai native approval flow | Batch 6 |
| Thread replies | Reply in threads, not top-level | Not planned |
| Channel listing | Tool to list available channels | Not planned |
| File upload | Share files via Slack | Not planned |
| Reaction support | Add reactions to messages | Not planned |
