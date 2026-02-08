# Design: Google Tools (Drive, Gmail, Calendar)

**Status:** Implemented (Batch 3)
**Last Updated:** 2026-02-05

## Overview

The Google tools provide agent access to three Google Cloud services: Drive (search and read files), Gmail (list, search, and draft emails), and Calendar (list and search events). All tools use the `RunContext[CoDeps]` pattern with `ModelRetry` for self-healing errors. Authentication is centralized in `co_cli/google_auth.py`.

**Key design decision:** Credentials are resolved lazily on first Google tool call via `get_cached_google_creds(ctx.deps)`, cached on the `CoDeps` instance for session lifecycle. Each tool builds its own API service object inline from cached credentials. Tools never import `settings` — they access credentials via `ctx.deps`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Co CLI Startup                           │
│                                                                  │
│  main.py: create_deps()                                          │
│    │                                                             │
│    └── CoDeps(google_credentials_path=settings.google_          │
│               credentials_path, ...)                             │
│                                                                  │
│  Note: No Google services built at startup.                      │
│  Credentials resolved lazily on first tool call.                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            │ deps injected into agent.run()
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Tool Execution (Lazy Auth)                        │
│                                                                  │
│  tool(ctx: RunContext[CoDeps], ...)                              │
│    │                                                             │
│    ├── creds = get_cached_google_creds(ctx.deps)                  │
│    │       # cached on CoDeps instance, resolved once             │
│    ├── if not creds: raise ModelRetry("Not configured")          │
│    ├── service = build("drive", "v3", credentials=creds)         │
│    └── service.files().list(...).execute()                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Google Cloud APIs                              │
│                                                                  │
│  ├── Google Drive API v3    (drive.readonly)                    │
│  ├── Gmail API v1           (gmail.modify)                      │
│  └── Google Calendar API v3 (calendar.readonly)                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Google Auth (`co_cli/google_auth.py`)

Three infrastructure functions: `ensure_google_credentials()` for interactive auto-setup, `get_google_credentials()` for non-interactive use (tests/CI), and `get_cached_google_creds()` which caches on the `CoDeps` instance (resolved once on first call). Lives at package root, not in `tools/` — it's infrastructure, not a tool.

```
ensure_google_credentials(credentials_path, scopes) -> Credentials | None  # interactive
get_google_credentials(credentials_path, scopes) -> Credentials | None     # non-interactive
get_cached_google_creds(deps: CoDeps) -> Credentials | None               # cached on deps, used by all tools
```

### Constants

```python
GOOGLE_TOKEN_PATH = CONFIG_DIR / "google_token.json"          # ~/.config/co-cli/google_token.json
ADC_PATH = Path.home() / ".config/gcloud/application_default_credentials.json"
ALL_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
]
```

### Authentication Flow (Interactive — `ensure_google_credentials`)

```
ensure_google_credentials(credentials_path, scopes)
       │
       ▼
┌──────────────────────────────────────┐
│ 1. Explicit credentials_path exists? │
│    └── Yes ──▶ Use it               │
└──────────────────────────────────────┘
       │ No
       ▼
┌──────────────────────────────────────┐
│ 2. GOOGLE_TOKEN_PATH exists?         │
│    (~/.config/co-cli/google_token)   │
│    └── Yes ──▶ Use it               │
└──────────────────────────────────────┘
       │ No
       ▼
┌──────────────────────────────────────┐
│ 3. ADC_PATH exists?                  │
│    (~/.config/gcloud/adc.json)       │
│    └── Yes ──▶ Copy to              │
│         GOOGLE_TOKEN_PATH, use it   │
└──────────────────────────────────────┘
       │ No
       ▼
┌──────────────────────────────────────┐
│ 4. gcloud installed?                 │
│    └── Yes ──▶ Run gcloud auth      │
│         application-default login    │
│         --scopes=...                │
│         Copy ADC to GOOGLE_TOKEN,   │
│         use it                      │
│    └── No ──▶ Return None           │
└──────────────────────────────────────┘
       │
       ▼
  Returns Credentials or None
```

### Authentication Flow (Non-interactive — `get_google_credentials`)

```
get_google_credentials(credentials_path, scopes)
       │
       ▼
┌──────────────────────────────────────┐
│ credentials_path provided and exists?│
│   ├── Yes ──▶ Authorized User File   │
│   │   Credentials.from_authorized_   │
│   │   user_file(path, scopes)        │
│   └── No  ──▶ ADC Fallback          │
│              google.auth.default()   │
└──────────────────────────────────────┘
       │
       ▼
  Returns Credentials or None
```

### Credential Cache

```
get_cached_google_creds(deps: CoDeps)
       │
       ▼
┌──────────────────────────────────────┐
│ Already resolved                     │
│ (deps._google_creds_resolved)?       │
│   ├── Yes ──▶ Return deps.google_    │
│   │           creds                  │
│   └── No  ──▶ ensure_google_         │
│                credentials(deps.     │
│                google_credentials_   │
│                path, ALL_SCOPES)     │
│                Store on deps, return │
└──────────────────────────────────────┘
```

Cache is stored on the `CoDeps` instance (`deps.google_creds` / `deps._google_creds_resolved`), not module globals — follows session lifecycle per the §4.3 invariant.

Tools then build API service objects inline: `service = build("drive", "v3", credentials=creds)`. This is cheap — `build()` returns a lightweight proxy, no network call.

### Scope Behavior

Scopes are fixed at `gcloud auth` login time. The `scopes` parameter passed to `from_authorized_user_file()` is for validation — it doesn't grant new scopes. Users must include all required scopes in the original `gcloud auth` command:

```bash
gcloud auth application-default login \
  --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar.readonly'
```

`get_cached_google_creds(deps)` calls `ensure_google_credentials()` once with all scopes combined on first tool call, caches on the deps instance. The interactive flow automatically passes scopes to `gcloud auth application-default login`.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Three functions (ensure + get + cached) | `ensure` for interactive auth, `get` for tests/CI, `cached` for tool-call-time resolution |
| `ensure_google_credentials()` auto-runs gcloud | Zero-config UX — like `DockerSandbox.ensure_container()` lazily creates the container |
| `get_google_credentials()` kept for non-interactive use | Tests and CI should not prompt for browser auth |
| Returns `None` on failure | Callers raise `ModelRetry` with context-specific messages |
| ADC copied to `GOOGLE_TOKEN_PATH` | Isolation — co-cli credentials won't be overwritten by other gcloud commands |
| `ALL_GOOGLE_SCOPES` in `google_auth.py` | Single source of truth for scopes (was duplicated in `main.py`) |
| Combined scopes in one auth call | `authorized_user` tokens are scoped at login time (all-or-nothing) |
| Credential cache on CoDeps instance | Resolved once on first tool call, follows session lifecycle, no module globals |
| `Any` return type | `googleapiclient` has no typed stubs |
| Package root, not `tools/` | Auth is infrastructure shared across multiple tools |

---

## Deps Integration

```
┌──────────────────────────────────────────────────────────────────┐
│ main.py: create_deps()                                           │
│                                                                  │
│   return CoDeps(                                                 │
│       ...,                                                       │
│       google_credentials_path=settings.google_credentials_path,  │
│   )                                                              │
│                                                                  │
│   Note: No Google services built here. Only the credentials      │
│   path is injected. Resolution happens lazily on first tool call.│
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│ CoDeps dataclass                                                 │
│                                                                  │
│   google_credentials_path: str | None = None                     │
│   google_creds: Any | None = field(default=None, repr=False)     │
│   _google_creds_resolved: bool = field(default=False, init=False)│
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Tool functions resolve credentials lazily                        │
│                                                                  │
│   creds = get_cached_google_creds(ctx.deps)                      │
│   service = build("drive", "v3", credentials=creds)              │
│   service = build("gmail", "v1", credentials=creds)              │
│   service = build("calendar", "v3", credentials=creds)           │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Why lazy resolution, not startup?**
- Avoids blocking startup with interactive `gcloud auth` if creds are missing
- Google tools may never be called in a given session
- `get_cached_google_creds()` caches on CoDeps instance — resolved once, follows session lifecycle

---

## Structured Output Pattern

All Google tools return `dict[str, Any]` with a `display` field (pre-formatted string with URLs baked in) and metadata fields. This is a **critical design decision** for stable LLM output.

**Problem:** When tools return raw data (`list[dict]` or plain strings), the LLM reformats the output into tables, summaries, or other layouts — dropping URLs, links, and other important details the user needs.

**Solution:** Pre-format the display text in the tool itself. The LLM's system prompt says "show tool output directly — don't summarize," and structured output makes this enforceable.

```python
# BAD — LLM reformats, drops URLs
def search_drive_files(...) -> list[dict[str, Any]]:
    return [{"name": "doc", "webViewLink": "https://..."}]
    # LLM output: "Found 1 file: doc" (URL gone)

# GOOD — URLs baked into display, metadata separate
def search_drive_files(...) -> dict[str, Any]:
    return {
        "display": "- 2026-01-20  Meeting Notes\n  https://docs.google.com/...",
        "page": 1,
        "has_more": True,
    }
    # LLM output: shows the display text verbatim including URL
```

**Standard fields across all Google tools:**

| Field | Type | Purpose |
|-------|------|---------|
| `display` | `str` | Pre-formatted output — LLM shows this directly to the user |
| `count` | `int` | Number of items returned (Gmail, Calendar) |
| `page` | `int` | Current page number (Drive) |
| `has_more` | `bool` | Whether more results are available (Drive) |

**Why `display` as pre-formatted string:**
- URLs are baked in — LLM cannot drop them
- Formatting is consistent regardless of LLM model (Gemini, Ollama, etc.)
- System prompt "show tool output directly" is enforceable
- LLM still gets metadata fields (`count`, `has_more`) for programmatic decisions

---

## Tools

### search_drive_files (`co_cli/tools/google_drive.py`)

Search for files in Google Drive by keywords with server-managed pagination.

```
search_drive_files(ctx: RunContext[CoDeps], query: str, page: int = 1) -> dict[str, Any]

Args:
    query: Search keywords or metadata query
    page:  Page number (1-based). Use 1 for first page, 2 for next, etc.

Returns:
    dict with:
      display:  Pre-formatted results with clickable URLs (show directly to user)
      page:     Current page number
      has_more: Whether more results are available

Raises:
    ModelRetry: If not configured, no results, or API error
```

**Pagination Design — Server-Managed Cursors:**

LLMs cannot reliably pass opaque API cursor tokens between tool calls. Tokens contain special characters that get mangled through function calling serialization, and LLMs are biased toward answering "no more results" from memory instead of making another tool call.

The solution: **store cursor tokens server-side, expose only a simple `page: int` to the LLM.** This follows OpenAI's tool design guidance: "offload the burden from the model and use code where possible, not making the model fill arguments you already know."

```
_page_tokens: dict[str, list[str]]   # module-level cache
    │
    │  key = query string
    │  value = [token_for_page_2, token_for_page_3, ...]
    │
    ▼
search_drive_files(query="notes", page=1)
    │
    ├── page==1: no pageToken needed
    ├── API returns nextPageToken
    └── store token at _page_tokens["notes"][0]
         │
         ▼
search_drive_files(query="notes", page=2)
    │
    ├── page==2: look up _page_tokens["notes"][0]
    ├── pass as pageToken to API
    ├── API returns nextPageToken
    └── store token at _page_tokens["notes"][1]
         │
         ▼
search_drive_files(query="notes", page=3)  ...and so on
```

**Why this approach (vs alternatives):**

| Approach | Problem |
|----------|---------|
| Expose raw `page_token: str` to LLM | Token has special chars, gets mangled; LLM often skips the tool call entirely |
| Token in structured output + system prompt | LLM still ignores it or hallucinates "no more results" |
| Auto-fetch all results (like Calendar) | Drive can have thousands of results; too slow and too much data |
| Store cursor in `CoDeps` | Would work, but module-level dict is simpler for single-session CLI |

**Contrast with Calendar:** Calendar uses `_fetch_events()` which auto-paginates internally (while loop with pageToken). This works because calendar queries are bounded by time window (days_back/days_ahead). Drive searches are unbounded, so lazy pagination via `page: int` is the right choice.

**Processing Flow:**

```
search_drive_files("meeting notes", page=1)
       │
       ▼
┌──────────────────────────────────────┐
│ service = ctx.deps.google_drive      │
│   └── None? ──▶ ModelRetry           │
│        "Google Drive not configured" │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ page > 1?                            │
│   ├── Yes ──▶ Look up stored token  │
│   │    from _page_tokens[query]     │
│   │    (ModelRetry if not available) │
│   └── No  ──▶ Fresh search          │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ Build query:                         │
│   "name contains 'meeting notes'    │
│    or fullText contains             │
│    'meeting notes'"                 │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ service.files().list(                │
│     q=q, pageSize=10,               │
│     fields="...files(id, name,      │
│       mimeType, modifiedTime,       │
│       webViewLink)",                │
│     pageToken=... (if page > 1)     │
│ ).execute()                         │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ Store nextPageToken in              │
│ _page_tokens[query] for next page   │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ Format display with URLs:           │
│   "Page 1 — Found 10 files:        │
│    - 2026-01-20  Meeting Notes      │
│      https://docs.google.com/...    │
│      (id: abc123)"                  │
└──────────────────────────────────────┘
       │
       ▼
  Return {"display": ..., "page": 1, "has_more": true}
```

### read_drive_file (`co_cli/tools/google_drive.py`)

Fetch the content of a text-based file from Google Drive.

```
read_drive_file(ctx: RunContext[CoDeps], file_id: str) -> str

Args:
    file_id: Google Drive file ID (from search_drive_files results)

Returns:
    File content as UTF-8 string

Raises:
    ModelRetry: If not configured or API error
```

**Processing Flow:**

```
read_drive_file("abc123")
       │
       ▼
┌──────────────────────────────────────┐
│ service = ctx.deps.google_drive      │
│   └── None? ──▶ ModelRetry           │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ Get file metadata                    │
│   files().get(fileId, fields=        │
│     "name, mimeType")               │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ mimeType contains                    │
│ "application/vnd.google-apps"?       │
│   ├── Yes ──▶ Export as text/plain  │
│   │          files().export()        │
│   └── No  ──▶ Download raw          │
│              files().get_media()     │
└──────────────────────────────────────┘
       │
       ▼
  Return content.decode("utf-8")
```

### list_emails (`co_cli/tools/google_gmail.py`)

List recent emails from the user's Gmail inbox. Read-only, no confirmation required.

```
list_emails(ctx: RunContext[CoDeps], max_results: int = 5) -> dict[str, Any]

Args:
    max_results: Maximum number of emails to return (default 5)

Returns:
    dict with:
      display: Formatted email list with From, Subject, Date, preview snippet, and Gmail link
      count:   Number of emails returned

Raises:
    ModelRetry: If not configured or API error
```

**Processing Flow:**

```
list_emails(max_results=5)
       │
       ▼
┌──────────────────────────────────────┐
│ service = ctx.deps.google_gmail      │
│   └── None? ──▶ ModelRetry           │
│        "Gmail not configured"        │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ users().messages().list(             │
│     userId="me",                     │
│     maxResults=max_results           │
│ ).execute()                         │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ No messages? ──▶ Return              │
│   "No emails found."                │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ For each message:                    │
│   users().messages().get(            │
│     id=msg_id, format="metadata",   │
│     metadataHeaders=                │
│       ["From", "Subject", "Date"]   │
│   )                                 │
│   Extract headers + snippet         │
└──────────────────────────────────────┘
       │
       ▼
  Return formatted list:
    "Recent Emails:
     - From: alice@example.com
       Subject: Meeting notes
       Date: Thu, 5 Feb 2026 10:00:00
       Preview: Here are the notes from..."
```

### search_emails (`co_cli/tools/google_gmail.py`)

Search emails using Gmail's native query syntax. Read-only, no confirmation required.

```
search_emails(ctx: RunContext[CoDeps], query: str, max_results: int = 5) -> dict[str, Any]

Args:
    query:       Gmail search query (same syntax as the Gmail search box)
    max_results: Maximum number of emails to return (default 5)

Returns:
    dict with:
      display: Formatted search results with From, Subject, Date, preview snippet, and Gmail link
      count:   Number of emails returned

Raises:
    ModelRetry: If not configured or API error
```

**Gmail Query Examples:**

| Query | What it matches |
|-------|----------------|
| `from:alice@example.com` | Emails from a specific sender |
| `subject:invoice` | Subject contains "invoice" |
| `is:unread` | Unread emails only |
| `newer_than:2d` | Emails from last 2 days |
| `has:attachment` | Emails with attachments |
| `from:alice subject:report newer_than:7d` | Combined filters |

**Processing Flow:**

```
search_emails(query="from:alice", max_results=5)
       │
       ▼
┌──────────────────────────────────────┐
│ service = ctx.deps.google_gmail      │
│   └── None? ──▶ ModelRetry           │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ users().messages().list(             │
│     userId="me",                     │
│     q=query,                         │
│     maxResults=max_results           │
│ ).execute()                         │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ No messages? ──▶ Return              │
│   "No emails found for query: ..."  │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ For each message:                    │
│   Fetch metadata + snippet           │
│   (same as list_emails)             │
└──────────────────────────────────────┘
       │
       ▼
  Return formatted list:
    "Search results for 'from:alice':
     - From: alice@example.com
       Subject: Q1 Report
       Date: ..."
```

### create_email_draft (`co_cli/tools/google_gmail.py`)

Draft an email in Gmail. Requires human-in-the-loop confirmation.

```
create_email_draft(ctx: RunContext[CoDeps], to: str, subject: str, body: str) -> str

Args:
    to:      Recipient email address
    subject: Email subject line
    body:    Email body text

Returns:
    Success message with Draft ID, or "cancelled by user"

Raises:
    ModelRetry: If not configured or API error
```

**Processing Flow:**

```
create_email_draft("user@example.com", "Subject", "Body")
       │
       ▼
┌──────────────────────────────────────┐
│ service = ctx.deps.google_gmail      │
│   └── None? ──▶ ModelRetry           │
│        "Gmail not configured"        │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ auto_confirm = false?                │
│   ├── Yes ──▶ Skip prompt            │
│   └── No  ──▶ Confirm.ask(          │
│        "Draft email to user@...?")   │
│              │                       │
│         ┌────┴────┐                  │
│         ▼         ▼                  │
│       "y"        "n"                 │
│         │         │                  │
│         │    Return "cancelled"      │
│         ▼                            │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ Build MIME message                   │
│   MIMEText(body) + to + subject     │
│   base64url encode                   │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ users().drafts().create(             │
│     userId="me",                     │
│     body={"message": {"raw": raw}}  │
│ ).execute()                         │
└──────────────────────────────────────┘
       │
       ▼
  Return "Draft created for ... Draft ID: ..."
```

### list_calendar_events (`co_cli/tools/google_calendar.py`)

List today's calendar events. Read-only, no confirmation required.

```
list_calendar_events(ctx: RunContext[CoDeps], days_back=0, days_ahead=1, max_results=25) -> dict[str, Any]

Returns:
    dict with:
      display: Formatted event list with calendar links, Meet links, attendees
      count:   Number of events returned

Raises:
    ModelRetry: If not configured or API error
```

**Processing Flow:**

```
list_calendar_events()
       │
       ▼
┌──────────────────────────────────────┐
│ service = ctx.deps.google_calendar   │
│   └── None? ──▶ ModelRetry           │
│        "Calendar not configured"     │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ today_start = midnight UTC           │
│                                      │
│ events().list(                       │
│     calendarId="primary",            │
│     timeMin=today_start,             │
│     maxResults=10,                   │
│     singleEvents=True,              │
│     orderBy="startTime"             │
│ ).execute()                         │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ No events? ──▶ Return               │
│   "No upcoming events found."        │
└──────────────────────────────────────┘
       │
       ▼
  Return formatted list:
    "Calendar Events:
     - 2026-02-05T09:00: Standup
     - 2026-02-05T14:00: Design Review"
```

### search_calendar_events (`co_cli/tools/google_calendar.py`)

Search calendar events by keyword within a date range. Read-only, no confirmation required.

```
search_calendar_events(
    ctx: RunContext[CoDeps],
    query: str,
    days_back: int = 0,
    days_ahead: int = 30,
    max_results: int = 25,
) -> dict[str, Any]

Args:
    query:       Text to search for in event summaries, descriptions, and locations
    days_back:   How many days in the past to search (default 0)
    days_ahead:  How many days ahead to search (default 30)
    max_results: Maximum number of events to return (default 25)

Returns:
    dict with:
      display: Formatted event list with calendar links, Meet links, attendees
      count:   Number of events returned

Raises:
    ModelRetry: If not configured or API error
```

**Processing Flow:**

```
search_calendar_events(query="standup", days_ahead=7)
       │
       ▼
┌──────────────────────────────────────┐
│ service = ctx.deps.google_calendar   │
│   └── None? ──▶ ModelRetry           │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ time_min = today midnight UTC        │
│ time_max = now + days_ahead          │
│                                      │
│ events().list(                       │
│     calendarId="primary",            │
│     q=query,                         │
│     timeMin=time_min,                │
│     timeMax=time_max,                │
│     maxResults=max_results,          │
│     singleEvents=True,              │
│     orderBy="startTime"             │
│ ).execute()                         │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ No events? ──▶ Return               │
│   "No events found matching         │
│    'standup' in the next 7 days."   │
└──────────────────────────────────────┘
       │
       ▼
  Return formatted list:
    "Events matching 'standup':
     - 2026-02-06T09:00: Daily Standup
     - 2026-02-07T09:00: Daily Standup"
```

---

## Error Handling with ModelRetry

All Google tools use the `ModelRetry` re-raise pattern to prevent generic `except` blocks from swallowing retry signals:

```
try:
    ...
    if not results:
        raise ModelRetry("No results. Try different keywords.")
    ...
except ModelRetry:
    raise                          # <-- Re-raise, don't swallow
except Exception as e:
    raise ModelRetry(f"API error: {e}")
```

### Error Scenarios

| Scenario | Tool | ModelRetry Message | LLM Action |
|----------|------|-------------------|------------|
| No credentials, no ADC | All | "Not configured. Set google_credentials_path..." | Inform user |
| API not enabled on project | Drive | "Google Drive API is not enabled. Run: gcloud services enable drive.googleapis.com" | Inform user with exact command |
| API not enabled on project | Gmail | "Gmail API is not enabled. Run: gcloud services enable gmail.googleapis.com" | Inform user with exact command |
| API not enabled on project | Calendar | "Google Calendar API is not enabled. Run: gcloud services enable calendar-json.googleapis.com" | Inform user with exact command |
| Empty search results | Drive | "No results. Try different keywords." | Retry with broader terms |
| API rate limit | Any | "API error: HttpError 429..." | Wait and retry |
| Invalid file ID | Drive | "API error: File not found" | Ask user for correct ID |
| Auth expired | Any | "API error: ..." | Inform user to re-auth |

**Why ModelRetry over error strings?**

```
# Bad — LLM sees error text, has to guess what to do
return "Error: Drive API not configured."

# Good — LLM gets structured retry with guidance
raise ModelRetry(
    "Google Drive not configured. "
    "Set google_credentials_path in settings or run: gcloud auth application-default login"
)
```

---

## Human-in-the-Loop Confirmation

Only `create_email_draft` requires confirmation among Google tools. Matches the pattern from `shell.py`.

| Tool | Risk | Confirmation |
|------|------|-------------|
| `search_drive_files` | Low (read-only) | None |
| `read_drive_file` | Low (read-only) | None |
| `list_emails` | Low (read-only) | None |
| `search_emails` | Low (read-only) | None |
| `list_calendar_events` | Low (read-only) | None |
| `search_calendar_events` | Low (read-only) | None |
| `create_email_draft` | Medium (creates draft) | `rich.prompt.Confirm` |

### Confirmation Pattern

```python
if not ctx.deps.auto_confirm:
    if not Confirm.ask(
        f"Draft email to [bold]{to}[/bold]?",
        default=False,
        console=_console,
    ):
        return "Email draft cancelled by user."
```

| Decision | Rationale |
|----------|-----------|
| `rich.prompt.Confirm` not `typer.confirm` | Works correctly with async chat loop |
| `default=False` | Safe default: user must explicitly confirm |
| `console=_console` | Module-level Console for consistent output |
| Returns string on cancel | LLM sees "cancelled", can inform user |

**Bypass:** Set `auto_confirm: true` in settings or `CO_CLI_AUTO_CONFIRM=true` env var.

---

## Configuration

### Settings

| Setting | Env Var | Default | Used By |
|---------|---------|---------|---------|
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `None` | Drive, Gmail, Calendar |
| `auto_confirm` | `CO_CLI_AUTO_CONFIRM` | `false` | Gmail (create_email_draft) |

### OAuth Scopes

| Service | Scope | Rationale |
|---------|-------|-----------|
| Drive | `drive.readonly` | Search and read only, no file modification |
| Gmail | `gmail.modify` | Required for creating drafts |
| Calendar | `calendar.readonly` | List events only, no modifications |

All scopes are combined in a single `get_google_credentials()` call at startup. `authorized_user` tokens are scoped at login time — per-service splitting is unnecessary.

### Setup Guide: Google Tools from Scratch

Google tools require two things: (1) an OAuth2 token with the right scopes, and (2) the APIs enabled on the GCP project. Below is the complete step-by-step setup.

#### Prerequisites

Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) if you haven't already:

```bash
# macOS
brew install google-cloud-sdk

# Verify
gcloud version
```

#### Step 1: Authenticate and get an OAuth2 token

This opens a browser for Google sign-in and stores an `authorized_user` token locally:

```bash
gcloud auth application-default login \
  --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar.readonly'
```

The token is saved to `~/.config/gcloud/application_default_credentials.json`. On first `co chat`, co-cli copies it to `~/.config/co-cli/google_token.json` for isolation.

> **Note:** `co chat` runs this command automatically if no token exists and `gcloud` is installed. You can skip this step and let co-cli handle it.

#### Step 2: Find your GCP project

The token contains a `quota_project_id` — this is the GCP project where APIs must be enabled and API quota is charged:

```bash
# From co-cli's token (if already copied)
cat ~/.config/co-cli/google_token.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('quota_project_id', 'NOT SET'))"

# Or from gcloud's ADC
cat ~/.config/gcloud/application_default_credentials.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('quota_project_id', 'NOT SET'))"

# Or just ask gcloud
gcloud config get-value project
```

If `quota_project_id` is `NOT SET`, set it explicitly:

```bash
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

#### Step 3: Enable the required Google APIs

Each Google service requires its API to be enabled on the project. Without this, authentication succeeds but API calls return `403 accessNotConfigured`.

```bash
# Enable all three at once (replace PROJECT_ID if not your default project)
gcloud services enable \
  drive.googleapis.com \
  gmail.googleapis.com \
  calendar-json.googleapis.com

# Or target a specific project
gcloud services enable \
  drive.googleapis.com \
  gmail.googleapis.com \
  calendar-json.googleapis.com \
  --project=YOUR_PROJECT_ID
```

**Individual API commands:**

| API | gcloud Command |
|-----|----------------|
| Google Drive | `gcloud services enable drive.googleapis.com` |
| Gmail | `gcloud services enable gmail.googleapis.com` |
| Google Calendar | `gcloud services enable calendar-json.googleapis.com` |

#### Step 4: Verify everything is working

```bash
# Check APIs are enabled
gcloud services list --enabled --project=YOUR_PROJECT_ID \
  --filter="name:(drive OR gmail OR calendar)" \
  --format="table(config.name,config.title)"

# Check co-cli sees the credentials
uv run co status
# Should show: Google | Configured | ~/.config/co-cli/google_token.json

# Test with a real query
uv run co chat
# Co > what's on my calendar today?
```

#### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Google Calendar API is not enabled" | API not enabled on project | `gcloud services enable calendar-json.googleapis.com` |
| "Google Drive API is not enabled" | API not enabled on project | `gcloud services enable drive.googleapis.com` |
| "Gmail API is not enabled" | API not enabled on project | `gcloud services enable gmail.googleapis.com` |
| "Not configured. Set google_credentials_path..." | No token file found | Run Step 1 above, or `co chat` (auto-setup) |
| "Request had insufficient authentication scopes" | Token missing required scope | Delete token and re-run Step 1 with all scopes |
| `co status` shows "Google \| Not Found" | No gcloud, no token, no ADC | Install gcloud and run Step 1 |
| Token exists but API calls fail with 401 | Token expired and refresh failed | Delete `~/.config/co-cli/google_token.json` and re-run Step 1 |

**Deleting token to start fresh:**

```bash
rm ~/.config/co-cli/google_token.json
# Next 'co chat' will re-trigger auto-setup
```

#### How Auto-Setup Works

`ensure_google_credentials()` handles credential setup automatically at startup:

```
co chat (first run)
  │
  ├── 1. Check settings.google_credentials_path → not set
  ├── 2. Check ~/.config/co-cli/google_token.json → not found
  ├── 3. Check ~/.config/gcloud/application_default_credentials.json → not found
  ├── 4. gcloud installed? → yes
  │      └── Run: gcloud auth application-default login --scopes=...
  │          └── Browser opens → user signs in → token saved to ADC path
  │          └── Copy ADC → ~/.config/co-cli/google_token.json
  └── 5. Build service clients from token
         └── CoDeps(google_drive=..., google_gmail=..., google_calendar=...)
```

On subsequent runs, step 2 succeeds immediately (token file exists). Access tokens auto-refresh via the embedded `refresh_token`.

**Why copy to `~/.config/co-cli/`?** Isolation — co-cli credentials are separate from gcloud's ADC (which other tools may overwrite). The file can also be copied to another machine.

#### Manual Override

For advanced use, set `google_credentials_path` in `settings.json` to point to a custom credentials file. This takes priority over all auto-setup steps:

```json
{
  "google_credentials_path": "~/.config/co-cli/google_token.json"
}
```

#### Non-Interactive (`get_google_credentials`)

For tests and CI, `get_google_credentials()` provides the original non-interactive behavior: load from explicit path or fall back to ADC. No gcloud prompts.

#### Error Detection in Tools

All Google tools detect "API not enabled" errors specifically (checking for `"has not been enabled"` or `"accessNotConfigured"` in the API response) and return actionable `ModelRetry` messages with the exact `gcloud services enable` command for the specific API that failed.

---

## Security Model

### No Secrets in Tools

Tools never import `settings` or access credentials directly:

```
settings.json
    │
    └── main.py: create_deps()
            │
            ├── ensure_google_credentials() — auto-setup + load credentials
            └── build_google_service() — takes credentials, returns opaque client

Tools see:
    ctx.deps.google_drive    ──▶  Opaque service object (or None)
    ctx.deps.google_gmail    ──▶  Opaque service object (or None)
    ctx.deps.google_calendar ──▶  Opaque service object (or None)
```

### Write Protection

| Tool | Writes | Protection |
|------|--------|------------|
| `search_drive_files` | None | Read-only scope |
| `read_drive_file` | None | Read-only scope |
| `list_emails` | None | Read-only (gmail.modify includes read) |
| `search_emails` | None | Read-only (gmail.modify includes read) |
| `list_calendar_events` | None | Read-only scope |
| `search_calendar_events` | None | Read-only scope |
| `create_email_draft` | Creates Gmail draft | User confirmation + scoped to drafts only |

### Graceful Degradation

When GCP is not configured, tools raise `ModelRetry` instead of crashing:

```
Scenario: User has no credentials and no gcloud installed

User: "search my drive for meeting notes"
       │
       ▼
create_deps():
  google_creds = ensure_google_credentials(None, ALL_GOOGLE_SCOPES)
  └── No token file, no ADC, no gcloud ──▶ returns None
  google_drive = build_google_service(..., None) ──▶ returns None
       │
       ▼
search_drive_files():
  ctx.deps.google_drive is None
  └── raise ModelRetry("Google Drive not configured. Set google_credentials_path...")
       │
       ▼
Agent ──▶ "Google Drive is not configured. Install gcloud CLI and restart."
```

```
Scenario: First use with gcloud installed

User: "search my drive for meeting notes"
       │
       ▼
create_deps():
  google_creds = ensure_google_credentials(None, ALL_GOOGLE_SCOPES)
  └── No token file, no ADC
  └── gcloud installed ──▶ runs gcloud auth (opens browser)
  └── User completes auth ──▶ copies ADC to GOOGLE_TOKEN_PATH
  └── returns Credentials
  google_drive = build_google_service("drive", "v3", creds) ──▶ returns service
       │
       ▼
search_drive_files():
  ctx.deps.google_drive is present ──▶ normal execution
```

---

## Migration from Legacy Pattern

### Before (Batch 1-2 era)

```python
# drive.py — BAD: imports global settings, builds client per-call
from co_cli.config import settings

def get_drive_service():
    key_path = settings.google_credentials_path  # Global import
    creds = ...
    return build('drive', 'v3', credentials=creds)

def search_drive_files(query: str) -> list:          # tool_plain, no ctx
    service = get_drive_service()              # Built per-call
    if not service:
        return [{"error": "Not configured"}]   # Error string, not ModelRetry

# comm.py — BAD: junk drawer mixing Gmail + Calendar + Slack
def get_google_service(name, version):         # Shared auth with overly broad scopes
    ...
def create_email_draft(to, subject, body) -> str:     # tool_plain
    service = get_google_service('gmail', 'v1')
    ...
def list_calendar_events() -> str:             # tool_plain
    service = get_google_service('calendar', 'v3')
    ...
```

### After (Batch 3 + OAuth2 migration + auto-setup)

```python
# google_auth.py — Auto-setup + auth strategy + pure service builder
def ensure_google_credentials(credentials_path, scopes) -> Any | None:
    ...  # interactive: auto-runs gcloud if needed
def get_google_credentials(credentials_path, scopes) -> Any | None:
    ...  # non-interactive: for tests/CI
def build_google_service(service_name, version, credentials) -> Any | None:
    ...

# google_drive.py — RunContext, client from deps, ModelRetry, structured output
def search_drive_files(ctx: RunContext[CoDeps], query: str, page: int = 1) -> dict:
    service = ctx.deps.google_drive
    if not service:
        raise ModelRetry("Not configured...")

# google_gmail.py — Extracted from comm.py, own file
def create_email_draft(ctx: RunContext[CoDeps], to, subject, body) -> str:
    service = ctx.deps.google_gmail
    ...

# google_calendar.py — Extracted from comm.py, own file
def list_calendar_events(ctx: RunContext[CoDeps]) -> str:
    service = ctx.deps.google_calendar
    ...
```

### What Changed

| Aspect | Before | After |
|--------|--------|-------|
| Registration | `agent.tool_plain()` | `agent.tool()` |
| First param | None | `ctx: RunContext[CoDeps]` |
| Client access | `get_drive_service()` / `get_google_service()` per call | `ctx.deps.google_*` from deps |
| Settings import | `from co_cli.config import settings` in tool files | None in tools |
| Error handling | Return error strings | `raise ModelRetry(...)` |
| Confirmation | `typer.confirm()` | `rich.prompt.Confirm` |
| Auth scopes | Overly broad (gmail.modify + calendar in one) | Combined at login time, validated at load |
| File layout | `drive.py` + `comm.py` (junk drawer) | `google_drive.py` + `google_gmail.py` + `google_calendar.py` |
| Auth location | `get_drive_service()` in `drive.py`, `get_google_service()` in `comm.py` | `get_google_credentials()` + `build_google_service()` in `google_auth.py` |

---

## Testing

### Functional Tests (`tests/test_google_cloud.py`)

Tests use the same `Context` dataclass pattern as `test_obsidian.py`:

```python
@dataclass
class Context:
    deps: CoDeps

def _make_ctx(auto_confirm=True, google_drive=None, ...) -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        auto_confirm=auto_confirm,
        google_drive=google_drive,
        ...
    ))
```

All Google tests skip gracefully when GCP is unavailable:

| Test | Skip Condition | What It Verifies |
|------|---------------|-----------------|
| `test_drive_search_functional` | No GCP key | Real Drive API search |
| `test_list_emails_functional` | No GCP key | Real Gmail inbox listing |
| `test_search_emails_functional` | No GCP key | Real Gmail search query |
| `test_search_calendar_events_functional` | No GCP key | Real Calendar keyword search |
| `test_gmail_draft_functional` | No GCP key | Real Gmail draft creation |

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/google_auth.py` | `ensure_google_credentials()` + `get_google_credentials()` + `build_google_service()` — auto-setup, auth, service builder |
| `co_cli/deps.py` | `CoDeps` with `google_drive`, `google_gmail`, `google_calendar` fields |
| `co_cli/tools/google_drive.py` | `search_drive_files`, `read_drive_file` |
| `co_cli/tools/google_gmail.py` | `list_emails`, `search_emails`, `create_email_draft` |
| `co_cli/tools/google_calendar.py` | `list_calendar_events`, `search_calendar_events` |
| `tests/test_google_cloud.py` | Functional tests (Drive, Gmail) |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| `requires_approval=True` | Pydantic-ai native approval flow for Gmail | Batch 6 |
| Drive file upload | Write files to Drive | Not planned |
| Calendar event creation | Create/modify events | Not planned |
| OAuth2 user flow | User-delegated auth via gcloud | Done (authorized_user credentials) |
| Auto-setup (`ensure_google_credentials`) | Auto-run gcloud auth on first use, like `DockerSandbox.ensure_container()` | Done |
| Semantic Drive search | Re-rank results with embeddings | Not planned |
