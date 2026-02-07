# TODO: Slack Tooling Gaps

**Last Audited:** 2026-02-06
**Files:** `co_cli/tools/slack.py`, `co_cli/agent.py` (registration), `docs/DESIGN-tool-slack.md`

---

## 1. Write-only — no read tools

Every other integration provides read+write. Slack is write-only:

| Integration | Read tools | Write tools |
|------------|-----------|------------|
| Gmail | `list_emails`, `search_emails` | `draft_email` |
| Calendar | `list_calendar_events`, `search_calendar_events` | — |
| Drive | `search_drive`, `read_drive_file` | — |
| Obsidian | `search_notes`, `list_notes`, `read_note` | — |
| **Slack** | **none** | `post_slack_message` |

Without read, the LLM has no context about which channels exist or what's been discussed.

**Candidates:**

| Tool | Scope | Approval |
|------|-------|----------|
| `list_channels` | List channels the bot can see | Read-only, no approval |
| `read_channel_messages` | Read recent messages from a channel | Read-only, no approval |
| `search_messages` | Search across workspace messages | Read-only, no approval |

**Required scopes:** `channels:read`, `groups:read` (list), `channels:history`, `groups:history` (read), `search:read` (search — user token only, not bot).

---

## 2. No `_get_slack_client` helper

Gmail uses `_get_gmail_service`, Calendar uses `_get_calendar_service` — both extract the service from context with validation. Slack inlines this. Fine for one tool, but becomes a DRY violation when read tools are added.

**Fix (when adding read tools):** Extract:

```python
def _get_slack_client(ctx: RunContext[CoDeps]):
    client = ctx.deps.slack_client
    if not client:
        raise ModelRetry(
            "Slack not configured. Set slack_bot_token in settings or SLACK_BOT_TOKEN env var."
        )
    return client
```

---

## Priority

| Item | Impact | Effort |
|------|--------|--------|
| 1. Read tools | High — feature parity, LLM context | Large |
| 2. Helper extraction | Low — do alongside item 1 | Small |
