# TODO: ModelRetry Message Normalization

**Origin:** RESEARCH-PYDANTIC-AI-CLI-BEST-PRACTICES.md gap analysis (§4.4 consistency pass)

---

## Gap Analysis

### Current ModelRetry audit

Every `ModelRetry` raise across all tool modules, categorized by issue:

#### Issue 1: Generic fallback messages with inconsistent prefixes

| File | Line | Current message | Problem |
|---|---|---|---|
| `shell.py` | 21 | `"Command failed ({e})"` | No tool prefix, no action hint |
| `google_drive.py` | 94 | `"Drive API error: {e}"` | No action hint |
| `google_gmail.py` | 87 | `"Gmail API error: {e}"` | No action hint |
| `google_gmail.py` | 160 | `"Gmail API error: {e}"` | Duplicate of :87, no action hint |
| `google_calendar.py` | 72 | `"Calendar API error: {e}"` | No action hint |
| `obsidian.py` | 223 | `"Error reading note: {e}"` | No tool prefix, no action hint |

Each tool uses a different prefix format for the same kind of error (unexpected exception). The model receives inconsistent error shapes, making self-correction less reliable.

#### Issue 2: Duplicate "not configured" messages

Three Google tool files repeat nearly identical configuration messages:

| File | Line | Message |
|---|---|---|
| `google_drive.py` | 28–31 | `"Google Drive not configured. Set google_credentials_path..."` |
| `google_gmail.py` | 47–49 | `"Gmail not configured. Set google_credentials_path..."` |
| `google_gmail.py` | 137–139 | `"Gmail not configured..."` (duplicated within same file) |
| `google_calendar.py` | 17–19 | `"Google Calendar not configured. Set google_credentials_path..."` |

The action hint (`Set google_credentials_path in settings or run: gcloud auth...`) is identical. This should be a shared constant.

#### Issue 3: Confusing phrasing

| File | Line | Current | Problem |
|---|---|---|---|
| `obsidian.py` | 77–79 | `"...Ask user to set obsidian_vault_path in settings."` | "Ask user" is confusing — the LLM is the recipient. Should say "Set obsidian_vault_path..." |
| `slack.py` | 201 | `"Channel ID is required."` | Less specific than the identical check at :161 which adds "Use list_slack_channels to find channel IDs." |

#### What already works well

- **Slack module** — best practice: `_SLACK_ERROR_HINTS` dict maps API error codes to specific, actionable messages. Other modules should adopt this pattern where applicable.
- **Obsidian `read_note`** — excellent: `"Note '{filename}' not found. Available notes: {available}. Use exact path from list_notes."` gives context + alternative + action.
- **Google "API not enabled"** messages — specific command to fix: `"Run: gcloud services enable drive.googleapis.com"`
- **Drive pagination** — `"Page {page} not available. Search from page 1 first, then request pages sequentially."` gives clear recovery instructions.

---

## Design

### Standard message format

```
{Tool}: {problem}. {Action hint}.
```

Rules:
1. **Tool prefix** — tool domain name (Shell, Drive, Gmail, Calendar, Obsidian, Slack). Helps the model identify which tool errored when multiple tools are in context.
2. **Problem** — what went wrong, concise.
3. **Action hint** — what the model should do differently. Required for all messages except validation errors where the fix is self-evident.

### Shared constants

```python
# co_cli/tools/_errors.py (new internal helper)

GOOGLE_NOT_CONFIGURED = (
    "{service} not configured. "
    "Set google_credentials_path in settings or run: "
    "gcloud auth application-default login"
)

GOOGLE_API_NOT_ENABLED = (
    "{service} API is not enabled for your project. "
    "Run: gcloud services enable {api_id}"
)

def google_api_error(service: str, error: Exception) -> str:
    """Format a generic Google API error with tool prefix and hint."""
    msg = str(error)
    if "has not been enabled" in msg or "accessnotconfigured" in msg.lower():
        # Caller handles this case specifically
        raise  # should not reach here
    return f"{service}: API error ({error}). Check credentials and API quota."
```

### Proposed message updates

| File | Line | Current | Proposed |
|---|---|---|---|
| `shell.py` | 21 | `"Command failed ({e})"` | `"Shell: command failed ({e}). Check command syntax or try a different approach."` |
| `obsidian.py` | 77 | `"...Ask user to set obsidian_vault_path..."` | `"Obsidian: vault not configured. Set obsidian_vault_path in settings."` |
| `obsidian.py` | 85 | `"Empty query. Provide keywords to search."` | `"Obsidian: empty query. Provide keywords to search."` |
| `obsidian.py` | 210 | `"Access denied: path is outside the vault."` | `"Obsidian: access denied — path is outside the vault."` |
| `obsidian.py` | 223 | `"Error reading note: {e}"` | `"Obsidian: error reading note ({e})."` |
| `google_drive.py` | 28 | `"Google Drive not configured..."` | `GOOGLE_NOT_CONFIGURED.format(service="Drive")` |
| `google_drive.py` | 94 | `"Drive API error: {e}"` | `f"Drive: API error ({e}). Check credentials and API quota."` |
| `google_gmail.py` | 47 | `"Gmail not configured..."` | `GOOGLE_NOT_CONFIGURED.format(service="Gmail")` |
| `google_gmail.py` | 87 | `"Gmail API error: {e}"` | `f"Gmail: API error ({e}). Check credentials and API quota."` |
| `google_gmail.py` | 137 | `"Gmail not configured..."` (dup) | Replace with `_get_gmail_service()` call (remove duplication) |
| `google_calendar.py` | 17 | `"Google Calendar not configured..."` | `GOOGLE_NOT_CONFIGURED.format(service="Calendar")` |
| `google_calendar.py` | 72 | `"Calendar API error: {e}"` | `f"Calendar: API error ({e}). Check credentials and API quota."` |
| `slack.py` | 27 | (fine) | No change |
| `slack.py` | 201 | `"Channel ID is required."` | `"Slack: channel ID is required. Use list_slack_channels to find channel IDs."` |

### `create_email_draft` deduplication

`create_email_draft` (google_gmail.py:127) manually builds credentials and error handling instead of using `_get_gmail_service()`. Refactor to use the shared helper, which eliminates the duplicated "not configured" message at line 137.

---

## Implementation Plan

### Items

- [ ] Create `co_cli/tools/_errors.py` with `GOOGLE_NOT_CONFIGURED`, `GOOGLE_API_NOT_ENABLED` constants
- [ ] Update `google_drive.py` — use shared constants for "not configured" and "API not enabled" messages
- [ ] Update `google_gmail.py` — use shared constants; refactor `create_email_draft` to use `_get_gmail_service()`
- [ ] Update `google_calendar.py` — use shared constants
- [ ] Update `shell.py` — add tool prefix and action hint to error message
- [ ] Update `obsidian.py` — add tool prefix, fix "Ask user" phrasing
- [ ] Update `slack.py` — add action hint to bare "Channel ID is required" at line 201
- [ ] Verify all `ModelRetry` messages follow `"{Tool}: {problem}. {Action hint}."` format
- [ ] Run full test suite — existing tests that match on `ModelRetry` message strings will need updated match patterns
- [ ] Update `tests/test_shell.py` — update `match="Command failed"` patterns to `match="Shell: command failed"`

### File changes

| File | Change |
|---|---|
| `co_cli/tools/_errors.py` | New — shared Google error constants |
| `co_cli/tools/shell.py` | Update ModelRetry message |
| `co_cli/tools/obsidian.py` | Update 4 ModelRetry messages |
| `co_cli/tools/google_drive.py` | Use shared constants (2 sites) |
| `co_cli/tools/google_gmail.py` | Use shared constants (3 sites) + refactor `create_email_draft` |
| `co_cli/tools/google_calendar.py` | Use shared constants (2 sites) |
| `co_cli/tools/slack.py` | Update 1 ModelRetry message |
| `tests/test_shell.py` | Update `match=` strings in `pytest.raises(ModelRetry, ...)` |

### Ordering

This is a leaf change — no dependencies on other TODOs. Can land at any time. Should land **before** `TODO-approval-interrupt-tests.md` Group 1 tests, which will encode the new message format in their assertions.
