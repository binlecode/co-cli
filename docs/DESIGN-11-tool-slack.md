---
title: "11 — Slack Tool"
parent: Tools
nav_order: 4
---

# Design: Slack Tool

## 1. What & How

The Slack tools enable the agent to read channels/users/threads and send messages to Slack channels. Uses `RunContext[CoDeps]` with `ModelRetry` for self-healing errors. `send_slack_message` uses `requires_approval=True` for human-in-the-loop confirmation; read tools execute without approval. The `WebClient` is created once at startup in `create_deps()` and injected via `CoDeps`.

```
main.py: create_deps()
  └── WebClient(token=settings.slack_bot_token)
      └── CoDeps(slack_client=slack_client)
          │
          ▼
Tool Execution:
  send_slack_message(ctx, channel, text)
    ├── client = ctx.deps.slack_client
    └── client.chat_postMessage(channel, text)
          │
          ▼
      Slack Web API (chat.postMessage)
```

## 2. Core Logic

### Tools

**`send_slack_message(channel, text) → dict`** — Send a message. Registered with `requires_approval=True` — approval handled by the chat loop via `DeferredToolRequests`. Returns `{"display": "Message sent...", "channel": ..., "ts": ...}`.

**`list_slack_channels(limit=20, types="public_channel") → dict`** — List channels the bot can see. Uses `conversations.list`. Returns `{"display": ..., "count": N, "has_more": bool}`.

**`list_slack_messages(channel, limit=15) → dict`** — Recent messages from a channel. Uses `conversations.history`. Hard-capped at 50 to protect LLM context window. Returns `{"display": ..., "count": N, "has_more": bool}`.

**`list_slack_replies(channel, thread_ts, limit=20) → dict`** — Thread replies. Uses `conversations.replies`. Parent message is always the first entry.

**`list_slack_users(limit=30) → dict`** — Active (non-bot, non-deleted) users. Uses `users.list`.

### Shared Helpers

**`_get_slack_client(ctx)`** — Extracts and validates the WebClient from context. Raises `ModelRetry` if not configured.

**`_format_message(msg)`** — Formats a Slack message: `YYYY-MM-DD HH:MM <@USER_ID>: text [thread: N replies]`. Truncates to 200 chars. User IDs shown in mention format — no N+1 `users.info` calls.

### Error Handling

`SlackApiError` responses are mapped to actionable hints via `_SLACK_ERROR_HINTS`:

| Error Code | Hint |
|-----------|------|
| `channel_not_found` | Check the name or use a channel ID |
| `not_in_channel` | Invite bot with `/invite @bot` |
| `invalid_auth` | Token is invalid or expired |
| `ratelimited` | Wait a moment and retry |
| `msg_too_long` | Shorten the text |

### Human-in-the-Loop

`send_slack_message` is registered with `requires_approval=True`. The tool never checks `auto_confirm` — the chat loop handles it. `ToolDenied("User denied this action")` gives the LLM a structured signal on denial.

### Security

The tool never imports `settings` or accesses the token directly. `ctx.deps.slack_client` is an opaque `WebClient` object. Bot scope is limited to `chat:write`, `channels:read`, `channels:history`, `users:read` (no admin).

## 3. Config

### Settings

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `slack_bot_token` | `SLACK_BOT_TOKEN` | `None` | Slack Bot User OAuth Token |
| `auto_confirm` | `CO_CLI_AUTO_CONFIRM` | `false` | Skip confirmation prompts |

### Setup Guide

1. Create a Slack App at https://api.slack.com/apps
2. Add Bot Scopes: `chat:write`, `channels:read`, `channels:history`, `users:read`
3. Install to Workspace and copy the `xoxb-...` Bot Token
4. Configure: `settings.json` → `{"slack_bot_token": "xoxb-..."}` or `SLACK_BOT_TOKEN` env var
5. Invite bot to channels: `/invite @your-bot-name`
6. Verify: `uv run co status`

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/slack.py` | All Slack tools: send, channels, messages, replies, users |
| `co_cli/deps.py` | CoDeps with `slack_client` field |
| `co_cli/agent.py` | Tool registration (write with approval, read without) |
| `tests/test_slack.py` | Functional tests |
