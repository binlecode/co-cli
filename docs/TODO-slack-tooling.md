# TODO: Slack Tooling

**Last Updated:** 2026-02-06
**Files:** `co_cli/tools/slack.py`, `co_cli/agent.py` (registration), `docs/DESIGN-12-tool-slack.md`

---

## Industry Landscape

Surveyed 10+ agentic systems: Anthropic MCP server, Slack's own MCP server (beta), korotovsky MCP, piekstra MCP, Composio, LangChain, CrewAI, OpenAI Codex, AutoGPT, Dust.tt.

### Feature Tiers (by adoption across systems)

| Tier | Capability | API Method | Type | Systems |
|------|-----------|-----------|------|---------|
| **T1** | List channels | `conversations.list` | Read | 7/10 |
| **T1** | Send message | `chat.postMessage` | Write | 10/10 |
| **T1** | Read channel history | `conversations.history` | Read | 7/10 |
| **T1** | Read thread replies | `conversations.replies` | Read | 5/10 |
| **T1** | Reply in thread | `chat.postMessage` + `thread_ts` | Write | 5/10 |
| **T1** | List/search users | `users.list` / `users.info` | Read | 5/10 |
| **T2** | Search messages | `search.messages` | Read | 5/10 (user token only) |
| **T2** | Add reaction | `reactions.add` | Write | 4/10 |
| **T2** | Send DM | `conversations.open` + `postMessage` | Write | 3/10 |
| **T2** | Get user profile | `users.profile.get` | Read | 3/10 |
| **T3** | Schedule message | `chat.scheduleMessage` | Write | 2/10 |
| **T3** | Update/delete message | `chat.update`/`chat.delete` | Write | 2/10 |
| **T3** | Upload file | `files.upload` | Write | 2/10 |
| **T3** | Create/archive channel | `conversations.create`/`archive` | Write | 2/10 |

### co-cli Current State

**1 tool:** `send_slack_message` (write, `chat:write` scope). Write-only — every other co-cli integration has read+write.

---

## Phase 1 — Core Reads (bot token) ✅ DONE

Implemented in `co_cli/tools/slack.py`. Registered as read-only tools (no approval) in `agent.py`. Functional tests in `tests/test_google_cloud.py`.

- `_get_slack_client` helper — extracts/validates client from `ctx.deps`
- `_format_message` / `_format_ts` — shared display helpers
- `list_slack_channels(ctx, limit=20, types="public_channel")`
- `list_slack_messages(ctx, channel, limit=15)` — capped at 50
- `list_slack_replies(ctx, channel, thread_ts, limit=20)`
- `list_slack_users(ctx, limit=30)` — filters deleted/bot users
- Refactored `send_slack_message` to use `_get_slack_client`

**Scopes added:** `channels:read`, `channels:history`, `users:read`

---

## Phase 2 — Core Writes + Reactions

Add thread replies and reactions. Both require approval.

### 2a. `reply_slack_thread`

Reply to a specific thread. The #1 requested write tool after `send_slack_message` across all surveyed systems.

```
reply_slack_thread(ctx, channel, thread_ts, text) -> dict[str, Any]

Args:
    channel:    Channel name or ID
    thread_ts:  Timestamp of the parent message
    text:       Reply text

Returns:
    {"display": "Reply sent in thread. TS: ...", "channel": ..., "ts": ...}
```

- Scope: `chat:write` (already have it)
- Registration: `agent.tool(reply_slack_thread, requires_approval=True)`
- Implementation: `chat.postMessage(channel=channel, thread_ts=thread_ts, text=text)`
- Input validation: same pattern as `send_slack_message`

### 2b. `add_slack_reaction`

Add an emoji reaction to a message. Lightweight acknowledgment — every MCP server includes this.

```
add_slack_reaction(ctx, channel, timestamp, emoji) -> dict[str, Any]

Args:
    channel:    Channel name or ID
    timestamp:  Message timestamp to react to
    emoji:      Emoji name without colons (e.g. 'thumbsup', 'eyes', 'white_check_mark')

Returns:
    {"display": "Reacted with :thumbsup: to message."}
```

- Scope: `reactions:write`
- Registration: `agent.tool(add_slack_reaction, requires_approval=True)`

---

## Phase 3 — Search (design decision needed)

### 3a. `search_slack_messages`

Search messages across channels. High value but has a **blocker**: `search.messages` requires a **user token** (`xoxp-`), not a bot token (`xoxb-`). This is a Slack platform limitation.

```
search_slack_messages(ctx, query, count=10) -> dict[str, Any]

Args:
    query:  Slack search syntax (e.g. "from:alice deploy", "in:#eng after:2026-01-01")
    count:  Max results (default 10)

Returns:
    {"display": "...", "count": 10, "has_more": true}
```

**Options:**

| Approach | Pros | Cons |
|----------|------|------|
| Add `slack_user_token` setting | Works today, full search | Two tokens to manage, user token has broader access |
| Wait for Slack RTS API (GA 2026) | Official, bot-token compatible | Timeline uncertain |
| Skip search entirely | No complexity | Agent can't find past conversations |

**Recommendation:** Add `slack_user_token` as optional. If set, enable search. If not, tool raises `ModelRetry("Search requires slack_user_token (xoxp-). Set it in settings or skip search.")`.

---

## Scopes Summary

| Phase | New Scopes | Token Type |
|-------|-----------|------------|
| Phase 1 | `channels:read`, `channels:history`, `groups:read`, `groups:history`, `users:read` | Bot (`xoxb-`) |
| Phase 2 | `reactions:write` | Bot (`xoxb-`) |
| Phase 3 | `search:read` | User (`xoxp-`) |

---

## Priority

| Item | Impact | Effort |
|------|--------|--------|
| Phase 1: Core reads (4 tools + helper) | High — feature parity, LLM context | Medium |
| Phase 2: Thread replies + reactions (2 tools) | Medium — write completeness | Small |
| Phase 3: Search (1 tool + token design) | High — but blocked by token decision | Medium |
