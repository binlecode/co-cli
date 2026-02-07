# TODO: Slack Tooling

**Last Updated:** 2026-02-06
**Files:** `co_cli/tools/slack.py`, `co_cli/agent.py` (registration), `docs/DESIGN-tool-slack.md`

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

**1 tool:** `post_slack_message` (write, `chat:write` scope). Write-only — every other co-cli integration has read+write.

---

## Phase 1 — Core Reads (bot token)

Extract `_get_slack_client` helper, add four read tools. No new token type needed — all work with the existing bot token (`xoxb-`).

**New scopes:** `channels:read`, `channels:history`, `groups:read`, `groups:history`, `users:read`

### 1a. `_get_slack_client` helper

Gmail uses `_get_gmail_service`, Calendar uses `_get_calendar_service`. With 5+ tools, inline client extraction becomes a DRY violation.

```python
def _get_slack_client(ctx: RunContext[CoDeps]):
    client = ctx.deps.slack_client
    if not client:
        raise ModelRetry(
            "Slack not configured. Set slack_bot_token in settings or SLACK_BOT_TOKEN env var."
        )
    return client
```

Refactor `post_slack_message` to use it too.

### 1b. `list_slack_channels`

List channels the bot can see. Essential — agent needs to know what channels exist before posting.

```
list_slack_channels(ctx, limit=20, types="public_channel") -> dict[str, Any]

Args:
    limit:  Max channels to return (default 20)
    types:  Comma-separated channel types: public_channel, private_channel, mpim, im

Returns:
    {"display": "#general (C01ABC) - Company-wide...\n#eng (C02DEF) - ...",
     "count": 20, "has_more": true}

Raises:
    ModelRetry: If not configured or API error
```

- Scope: `channels:read`, `groups:read`
- Registration: `agent.tool(list_slack_channels)` — read-only, no approval
- Pagination: Use Slack cursor. Return `has_more` flag. Agent can request more via `cursor` param.
- Display format: `#name (ID) - purpose` per line — keeps output compact, gives agent both name and ID

### 1c. `get_slack_channel_history`

Read recent messages from a channel. Core context-gathering tool — lets the agent understand what's been discussed before responding.

```
get_slack_channel_history(ctx, channel, limit=15) -> dict[str, Any]

Args:
    channel:  Channel name or ID
    limit:    Max messages to return (default 15, max 50)

Returns:
    {"display": "2026-02-06 14:30 @alice: Hey team, the deploy...\n...",
     "count": 15, "has_more": true}

Raises:
    ModelRetry: If not configured, channel not found, or API error
```

- Scope: `channels:history`, `groups:history`
- Registration: `agent.tool(get_slack_channel_history)` — read-only, no approval
- Default limit 15 — conservative to avoid blowing up context window. Anthropic MCP defaults to 10, korotovsky to 20
- Display format: `timestamp @user: message` per line. Truncate long messages to ~200 chars
- Thread indicator: Append `[thread: N replies]` for messages that have threads

### 1d. `get_slack_thread_replies`

Read replies in a specific thread. Needed for thread context before `reply_slack_thread` (Phase 2).

```
get_slack_thread_replies(ctx, channel, thread_ts, limit=20) -> dict[str, Any]

Args:
    channel:    Channel name or ID
    thread_ts:  Timestamp of the parent message (from channel history)
    limit:      Max replies to return (default 20)

Returns:
    {"display": "2026-02-06 14:30 @alice: Original message...\n  14:32 @bob: Reply...",
     "count": 5, "has_more": false}

Raises:
    ModelRetry: If not configured, channel/thread not found, or API error
```

- Scope: `channels:history`, `groups:history`
- Registration: `agent.tool(get_slack_thread_replies)` — read-only, no approval
- Parent message included as first entry (Slack API returns it by default)
- `thread_ts` comes from channel history output — agent chains: read history → pick thread → read replies

### 1e. `list_slack_users`

List workspace users. Needed to resolve "send DM to Alice" → user ID, and to understand who's in a channel.

```
list_slack_users(ctx, limit=30) -> dict[str, Any]

Args:
    limit:  Max users to return (default 30)

Returns:
    {"display": "@alice (U01ABC) - Alice Smith, Engineering\n@bob (U02DEF) - ...",
     "count": 30, "has_more": true}

Raises:
    ModelRetry: If not configured or API error
```

- Scope: `users:read`
- Registration: `agent.tool(list_slack_users)` — read-only, no approval
- Display format: `@display_name (ID) - real_name, title` per line
- Filter out deleted/bot users by default
- Pagination: Slack cursor, return `has_more`

---

## Phase 2 — Core Writes + Reactions

Add thread replies and reactions. Both require approval.

### 2a. `reply_slack_thread`

Reply to a specific thread. The #1 requested write tool after `post_slack_message` across all surveyed systems.

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
- Input validation: same pattern as `post_slack_message`

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
