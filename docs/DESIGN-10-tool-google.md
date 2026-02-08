---
title: "10 — Google Tools"
parent: Tools
nav_order: 3
---

# Design: Google Tools (Drive, Gmail, Calendar)

## 1. What & How

The Google tools provide agent access to three Google Cloud services: Drive (search and read files), Gmail (list, search, and draft emails), and Calendar (list and search events). All tools use `RunContext[CoDeps]` with `ModelRetry` for self-healing errors. Credentials are resolved lazily on first Google tool call via `get_cached_google_creds(ctx.deps)`, cached on the `CoDeps` instance for session lifecycle.

```
┌──────────────────────────────────────────────────────┐
│  Tool Execution (Lazy Auth)                           │
│                                                       │
│  tool(ctx: RunContext[CoDeps], ...)                   │
│    ├── creds = get_cached_google_creds(ctx.deps)      │
│    │       # cached on CoDeps, resolved once           │
│    ├── if not creds: raise ModelRetry("Not configured")│
│    ├── service = build("drive", "v3", credentials=creds)│
│    └── service.files().list(...).execute()             │
└──────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────┐
│  Google Cloud APIs                                    │
│  ├── Drive API v3    (drive.readonly)                │
│  ├── Gmail API v1    (gmail.modify)                  │
│  └── Calendar API v3 (calendar.readonly)             │
└──────────────────────────────────────────────────────┘
```

## 2. Core Logic

### Google Auth (`co_cli/google_auth.py`)

Three infrastructure functions for credential resolution:

| Function | Use | Interactive |
|----------|-----|-------------|
| `ensure_google_credentials()` | Chat sessions — auto-runs gcloud if needed | Yes |
| `get_google_credentials()` | Tests and CI — no prompts | No |
| `get_cached_google_creds()` | Tool calls — resolves once, caches on CoDeps | Delegates to ensure |

**Authentication flow (interactive):**

```
ensure_google_credentials(credentials_path, scopes)
  1. Explicit credentials_path exists? → Use it
  2. ~/.config/co-cli/google_token.json exists? → Use it
  3. ~/.config/gcloud/application_default_credentials.json? → Copy to token path, use it
  4. gcloud installed? → Run gcloud auth application-default login → Use result
  5. None of the above → Return None
```

Credentials are cached on the `CoDeps` instance (`deps.google_creds` / `deps._google_creds_resolved`), not module globals — follows session lifecycle.

### Structured Output Pattern

All Google tools return `dict[str, Any]` with a `display` field (pre-formatted string with URLs baked in) and metadata fields. The LLM's system prompt says "show tool output directly" — structured output makes this enforceable.

| Field | Type | Purpose |
|-------|------|---------|
| `display` | `str` | Pre-formatted output — URLs baked in |
| `count` | `int` | Number of items (Gmail, Calendar) |
| `page` | `int` | Current page number (Drive) |
| `has_more` | `bool` | More results available (Drive) |

### Tools

**`search_drive_files(query, page=1) → dict`** — Search Drive with server-managed pagination. Cursor tokens stored server-side, only `page: int` exposed to the LLM.

**`read_drive_file(file_id) → str`** — Fetch text content. Auto-detects Google Docs (export as text/plain) vs regular files (download raw).

**`list_emails(max_results=5) → dict`** — Recent inbox emails with From, Subject, Date, snippet, and Gmail link.

**`search_emails(query, max_results=5) → dict`** — Gmail native query syntax (`from:alice`, `is:unread`, `newer_than:2d`).

**`create_email_draft(to, subject, body) → str`** — Creates Gmail draft. Registered with `requires_approval=True`.

**`list_calendar_events(days_back=0, days_ahead=1, max_results=25) → dict`** — Lists events with calendar/Meet links and attendees. Auto-paginates internally (bounded by time window).

**`search_calendar_events(query, days_back=0, days_ahead=30, max_results=25) → dict`** — Keyword search within a date range.

### Error Handling

All tools use the `ModelRetry` re-raise pattern to prevent generic `except` blocks from swallowing retry signals. API-not-enabled errors return actionable `ModelRetry` messages with the exact `gcloud services enable` command.

| Scenario | ModelRetry Message |
|----------|-------------------|
| No credentials | "Not configured. Set google_credentials_path..." |
| API not enabled | "Run: gcloud services enable drive.googleapis.com" |
| Empty results | "No results. Try different keywords." |
| Auth expired | "API error: ..." |

### Human-in-the-Loop

Only `create_email_draft` requires confirmation. Uses `requires_approval=True` — approval handled by the chat loop via `DeferredToolRequests`.

### Security

Tools never import `settings` — they access credentials via `ctx.deps`. Write operations are scoped: `gmail.modify` for drafts only, Drive and Calendar are read-only.

<details>
<summary>Migration from legacy pattern</summary>

| Aspect | Before | After |
|--------|--------|-------|
| Registration | `agent.tool_plain()` | `agent.tool()` |
| Client access | `get_drive_service()` per call | `ctx.deps` via lazy `get_cached_google_creds()` |
| Settings import | `from co_cli.config import settings` | None in tools |
| Error handling | Return error strings | `raise ModelRetry(...)` |
| File layout | `drive.py` + `comm.py` (junk drawer) | `google_drive.py` + `google_gmail.py` + `google_calendar.py` |

</details>

## 3. Config

### Settings

| Setting | Env Var | Default | Description |
|---------|---------|---------|---------|
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `None` | OAuth token path for Drive, Gmail, Calendar |
| `auto_confirm` | `CO_CLI_AUTO_CONFIRM` | `false` | Skip approval for `create_email_draft` |

### OAuth Scopes

| Service | Scope | Rationale |
|---------|-------|-----------|
| Drive | `drive.readonly` | Search and read only |
| Gmail | `gmail.modify` | Required for creating drafts |
| Calendar | `calendar.readonly` | List events only |

### Setup Guide

1. **Authenticate:** `gcloud auth application-default login --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar.readonly'`
2. **Enable APIs:** `gcloud services enable drive.googleapis.com gmail.googleapis.com calendar-json.googleapis.com`
3. **Verify:** `uv run co status` — should show Google as configured

> `co chat` runs the gcloud auth command automatically if no token exists and `gcloud` is installed.

**Troubleshooting:**

| Symptom | Fix |
|---------|-----|
| "API is not enabled" | `gcloud services enable <api>.googleapis.com` |
| "Not configured" | Run gcloud auth step above, or `co chat` (auto-setup) |
| "Insufficient scopes" | Delete `~/.config/co-cli/google_token.json`, re-auth with all scopes |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/google_auth.py` | `ensure_google_credentials()` + `get_google_credentials()` + `get_cached_google_creds()` |
| `co_cli/tools/google_drive.py` | `search_drive_files`, `read_drive_file` |
| `co_cli/tools/google_gmail.py` | `list_emails`, `search_emails`, `create_email_draft` |
| `co_cli/tools/google_calendar.py` | `list_calendar_events`, `search_calendar_events` |
| `co_cli/deps.py` | CoDeps with `google_credentials_path`, `google_creds` fields |
| `tests/test_google_cloud.py` | Functional tests |
