# Design: Google Tools (Drive, Gmail, Calendar)

**Status:** Implemented (Batch 3)
**Last Updated:** 2026-02-05

## Overview

The Google tools provide agent access to three Google Cloud services: Drive (search and read files), Gmail (list, search, and draft emails), and Calendar (list events). All tools use the `RunContext[CoDeps]` pattern with `ModelRetry` for self-healing errors. Authentication is centralized in `co_cli/google_auth.py`.

**Key design decision:** API clients are built once at startup in `create_deps()` via a shared auth factory, then injected via `CoDeps` — tools never build their own clients or import `settings`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Co CLI Startup                           │
│                                                                  │
│  main.py: create_deps()                                          │
│    │                                                             │
│    ├── google_creds = ensure_google_credentials(                │
│    │     settings.google_credentials_path, ALL_GOOGLE_SCOPES)   │
│    ├── build_google_service("drive", "v3", google_creds)        │
│    ├── build_google_service("gmail", "v1", google_creds)        │
│    └── build_google_service("calendar", "v3", google_creds)     │
│    │                                                             │
│    ▼                                                             │
│  CoDeps(google_drive=..., google_gmail=..., google_calendar=...)│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            │ deps injected into agent.run()
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Tool Execution                            │
│                                                                  │
│  tool(ctx: RunContext[CoDeps], ...)                              │
│    │                                                             │
│    ├── service = ctx.deps.google_drive  (or gmail, calendar)     │
│    ├── if not service: raise ModelRetry("Not configured")        │
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

Three infrastructure functions: `ensure_google_credentials()` for interactive auto-setup (used at startup), `get_google_credentials()` for non-interactive use (tests/CI), and `build_google_service()` as a pure service builder. Lives at package root, not in `tools/` — it's infrastructure, not a tool.

```
ensure_google_credentials(credentials_path, scopes) -> Credentials | None  # interactive
get_google_credentials(credentials_path, scopes) -> Credentials | None     # non-interactive
build_google_service(service_name, version, credentials) -> Any | None
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

### Service Builder

```
build_google_service(service_name, version, credentials)
       │
       ▼
┌──────────────────────────────────────┐
│ credentials not None?                │
│   ├── Yes ──▶ build(name, version,   │
│   │            credentials=creds)    │
│   │            Return service        │
│   └── No  ──▶ Return None           │
│              (callers use ModelRetry) │
└──────────────────────────────────────┘
```

### Scope Behavior

Scopes are fixed at `gcloud auth` login time. The `scopes` parameter passed to `from_authorized_user_file()` is for validation — it doesn't grant new scopes. Users must include all required scopes in the original `gcloud auth` command:

```bash
gcloud auth application-default login \
  --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar.readonly'
```

`create_deps()` calls `ensure_google_credentials()` once with all scopes combined, then builds three services from the same credentials. The interactive flow automatically passes scopes to `gcloud auth application-default login`.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Three functions (ensure + get + build) | `ensure` for interactive startup, `get` for tests/CI, `build` for service construction |
| `ensure_google_credentials()` auto-runs gcloud | Zero-config UX — like `Sandbox.ensure_container()` lazily creates Docker |
| `get_google_credentials()` kept for non-interactive use | Tests and CI should not prompt for browser auth |
| Returns `None` on failure | Callers raise `ModelRetry` with context-specific messages |
| ADC copied to `GOOGLE_TOKEN_PATH` | Isolation — co-cli credentials won't be overwritten by other gcloud commands |
| `ALL_GOOGLE_SCOPES` in `google_auth.py` | Single source of truth for scopes (was duplicated in `main.py`) |
| Combined scopes in one auth call | `authorized_user` tokens are scoped at login time (all-or-nothing) |
| No retry/caching | Clients are built once at startup, not per-request |
| `Any` return type | `googleapiclient` has no typed stubs |
| Package root, not `tools/` | Auth is infrastructure shared across multiple tools |

---

## Deps Integration

```
┌──────────────────────────────────────────────────────────────────┐
│ main.py: create_deps()                                           │
│                                                                  │
│   google_creds = ensure_google_credentials(                      │
│       settings.google_credentials_path, ALL_GOOGLE_SCOPES)      │
│                                                                  │
│   google_drive    = build_google_service("drive", "v3",          │
│                       google_creds)                              │
│   google_gmail    = build_google_service("gmail", "v1",          │
│                       google_creds)                              │
│   google_calendar = build_google_service("calendar", "v3",       │
│                       google_creds)                              │
│                                                                  │
│   return CoDeps(                                                 │
│       ...,                                                       │
│       google_drive=google_drive,                                 │
│       google_gmail=google_gmail,                                 │
│       google_calendar=google_calendar,                           │
│   )                                                              │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│ CoDeps dataclass                                                 │
│                                                                  │
│   google_drive:    Any | None = None                             │
│   google_gmail:    Any | None = None                             │
│   google_calendar: Any | None = None                             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Tool functions access via ctx.deps                               │
│                                                                  │
│   service = ctx.deps.google_drive                                │
│   service = ctx.deps.google_gmail                                │
│   service = ctx.deps.google_calendar                             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Why build clients at startup, not per-request?**
- Avoids repeated auth overhead on every tool call
- Auth failures surface immediately, not mid-conversation
- Follows pydantic-ai pattern: `CoDeps` holds runtime resources

---

## Tools

### search_drive (`co_cli/tools/google_drive.py`)

Search for files in Google Drive by keywords.

```
search_drive(ctx: RunContext[CoDeps], query: str) -> list[dict[str, Any]]

Args:
    query: Search keywords or metadata query

Returns:
    List of file dicts with id, name, mimeType, modifiedTime

Raises:
    ModelRetry: If not configured, no results, or API error
```

**Processing Flow:**

```
search_drive("meeting notes")
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
│             mimeType, modifiedTime)" │
│ ).execute()                         │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ No items? ──▶ ModelRetry             │
│   "No results. Try different         │
│    keywords."                        │
└──────────────────────────────────────┘
       │
       ▼
  Return items list
```

### read_drive_file (`co_cli/tools/google_drive.py`)

Fetch the content of a text-based file from Google Drive.

```
read_drive_file(ctx: RunContext[CoDeps], file_id: str) -> str

Args:
    file_id: Google Drive file ID (from search_drive results)

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
list_emails(ctx: RunContext[CoDeps], max_results: int = 5) -> str

Args:
    max_results: Maximum number of emails to return (default 5)

Returns:
    Formatted email list with From, Subject, Date, and preview snippet

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
search_emails(ctx: RunContext[CoDeps], query: str, max_results: int = 5) -> str

Args:
    query:       Gmail search query (same syntax as the Gmail search box)
    max_results: Maximum number of emails to return (default 5)

Returns:
    Formatted search results with From, Subject, Date, and preview snippet

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

### draft_email (`co_cli/tools/google_gmail.py`)

Draft an email in Gmail. Requires human-in-the-loop confirmation.

```
draft_email(ctx: RunContext[CoDeps], to: str, subject: str, body: str) -> str

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
draft_email("user@example.com", "Subject", "Body")
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
list_calendar_events(ctx: RunContext[CoDeps]) -> str

Returns:
    Formatted event list, or "No upcoming events found."

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

Only `draft_email` requires confirmation among Google tools. Matches the pattern from `shell.py`.

| Tool | Risk | Confirmation |
|------|------|-------------|
| `search_drive` | Low (read-only) | None |
| `read_drive_file` | Low (read-only) | None |
| `list_emails` | Low (read-only) | None |
| `search_emails` | Low (read-only) | None |
| `list_calendar_events` | Low (read-only) | None |
| `draft_email` | Medium (creates draft) | `rich.prompt.Confirm` |

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
| `auto_confirm` | `CO_CLI_AUTO_CONFIRM` | `false` | Gmail (draft_email) |

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
| `search_drive` | None | Read-only scope |
| `read_drive_file` | None | Read-only scope |
| `list_emails` | None | Read-only (gmail.modify includes read) |
| `search_emails` | None | Read-only (gmail.modify includes read) |
| `list_calendar_events` | None | Read-only scope |
| `draft_email` | Creates Gmail draft | User confirmation + scoped to drafts only |

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
search_drive():
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
search_drive():
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

def search_drive(query: str) -> list:          # tool_plain, no ctx
    service = get_drive_service()              # Built per-call
    if not service:
        return [{"error": "Not configured"}]   # Error string, not ModelRetry

# comm.py — BAD: junk drawer mixing Gmail + Calendar + Slack
def get_google_service(name, version):         # Shared auth with overly broad scopes
    ...
def draft_email(to, subject, body) -> str:     # tool_plain
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

# google_drive.py — RunContext, client from deps, ModelRetry
def search_drive(ctx: RunContext[CoDeps], query: str) -> list:
    service = ctx.deps.google_drive
    if not service:
        raise ModelRetry("Not configured...")

# google_gmail.py — Extracted from comm.py, own file
def draft_email(ctx: RunContext[CoDeps], to, subject, body) -> str:
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

### Functional Tests (`tests/test_cloud.py`)

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
| `test_gmail_draft_functional` | No GCP key | Real Gmail draft creation |

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/google_auth.py` | `ensure_google_credentials()` + `get_google_credentials()` + `build_google_service()` — auto-setup, auth, service builder |
| `co_cli/deps.py` | `CoDeps` with `google_drive`, `google_gmail`, `google_calendar` fields |
| `co_cli/tools/google_drive.py` | `search_drive`, `read_drive_file` |
| `co_cli/tools/google_gmail.py` | `list_emails`, `search_emails`, `draft_email` |
| `co_cli/tools/google_calendar.py` | `list_calendar_events` |
| `tests/test_cloud.py` | Functional tests (Drive, Gmail) |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| `requires_approval=True` | Pydantic-ai native approval flow for Gmail | Batch 6 |
| Drive file upload | Write files to Drive | Not planned |
| Calendar event creation | Create/modify events | Not planned |
| OAuth2 user flow | User-delegated auth via gcloud | Done (authorized_user credentials) |
| Auto-setup (`ensure_google_credentials`) | Auto-run gcloud auth on first use, like `Sandbox.ensure_container()` | Done |
| Semantic Drive search | Re-rank results with embeddings | Not planned |
