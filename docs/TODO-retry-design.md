# TODO: ModelRetry Semantics — When to Retry vs Return Empty

**Status:** Implemented (search_drive fix), design principles documented
**Created:** 2026-02-06
**Related:** `docs/TODO-tool-call-stability.md` (Gap 2: retry budget, Gap 3: shell ModelRetry)

---

## Motivation

Two `search_drive` ERROR spans crashed an entire agent run:

```
10:30:37  tool   execute_tool search_drive  "Chris 1:1 February 5 2026"       760ms  ERROR
10:30:37  tool   execute_tool search_drive  "Platform Sync February 5 2026"   694ms  ERROR
```

Both raised `ModelRetry("No results. Try different keywords.")` — but the queries were correct, the files simply didn't exist. The retries exhausted the budget (`max_retries=1`) and killed the agent with `UnexpectedModelBehavior: Tool 'search_drive' exceeded max retries count of 1`.

Root cause: treating "no results" as a parameter error when it's a valid answer.

---

## Design Principle

**`ModelRetry` = "you called this wrong, fix your parameters"**
**Empty result = "query was fine, nothing matched"**

### Raise `ModelRetry` when the LLM can self-correct:

| Scenario | Example hint |
|----------|-------------|
| Missing setup / config | `"Google Drive not configured. Set google_credentials_path..."` |
| API not enabled | `"Run: gcloud services enable drive.googleapis.com"` |
| Pagination violation | `"Page 5 not available. Search from page 1 first."` |
| Malformed parameters | `"Invalid date format. Use YYYY-MM-DD."` |
| Wrong parameter value | `"No user found with name 'John', provide their full name"` |
| Shell command error | `"Command failed: No such file or directory"` |

### Return empty result when there's nothing to fix:

| Scenario | Return |
|----------|--------|
| Search matched zero files | `{"display": "No files found.", "count": 0, ...}` |
| Time range had no events | `{"display": "No events found.", "count": 0}` |
| List is genuinely empty | `{"display": "No items.", "count": 0}` |

The distinction: can the LLM produce a better call on retry? If the answer is "the data just isn't there", don't waste retries.

---

## Industry Best Practices

### Retry ownership is layered

| Layer | What retries | Mechanism |
|-------|-------------|-----------|
| **HTTP transport** | Rate limits (429), server errors (502/503) | `RetryConfig` with exponential backoff |
| **Tool framework** | Wrong parameters, fixable preconditions | `ModelRetry` → `RetryPromptPart` fed back to LLM |
| **Orchestration** | Different strategy entirely | System prompt guidance ("try broader keywords") |

### Cross-framework consensus

| Framework | Pattern | Default retries |
|-----------|---------|-----------------|
| **pydantic-ai** | `ModelRetry` + `RetryPromptPart` | 1 (recommend 2-3) |
| **Anthropic Claude** | `is_error: true` on `tool_result` | 2-3 self-corrections |
| **OpenAI Agents SDK** | No built-in retry — system prompt driven | N/A |
| **LangGraph** | Node-level retry + circuit breaker | configurable |

### Recommended retry counts

- **Read-only tools** (search, list, read): `retries=3` — enough for malformed JSON + one logic correction
- **Side-effectful tools** (email, Slack, shell writes): `retries=1-2` — only retry parameter validation, not "action had unexpected results"
- **Circuit breaker**: `UsageLimits(request_limit=25)` to cap total agent steps

### The actionable hint pattern

Error messages must tell the LLM **what to do differently**, not just what went wrong:

```python
# Bad — LLM has no guidance
raise ModelRetry("No results.")

# Good — actionable correction hint
raise ModelRetry(
    f"No user found with name {name!r}, remember to provide their full name"
)
```

### Google Drive API: empty results are valid

The Drive `files.list` API returns HTTP 200 with an empty `files` array when nothing matches. This is a **valid response**, not an error. HTTP errors (400, 403, 404, 500) are real errors. The calendar tools already handle this correctly — `search_drive` was the outlier.

---

## Fix: `search_drive` empty-result handling

**File:** `co_cli/tools/google_drive.py`

### Before

```python
if not items:
    if page == 1:
        raise ModelRetry("No results. Try different keywords.")
    return {"display": "No more results.", "page": page, "has_more": False}
```

### After

```python
if not items:
    if page == 1:
        return {"display": "No files found.", "count": 0, "page": 1, "has_more": False}
    return {"display": "No more results.", "count": 0, "page": page, "has_more": False}
```

The LLM sees "No files found" as a successful result and can tell the user, try broader keywords on its own, or move on — without burning retries or crashing the agent run.

### What stays as `ModelRetry`

All other `ModelRetry` raises in `google_drive.py` are correct:

- `service is None` → "Google Drive not configured..."
- `page > 1` without prior page token → "Search from page 1 first..."
- `accessNotConfigured` → "Run: gcloud services enable..."
- Generic API exception → `"Drive API error: {e}"`

---

## Audit: All tools

| Tool | Empty result handling | `ModelRetry` usage | Status |
|------|----------------------|-------------------|--------|
| `search_drive` | Was `ModelRetry` → now returns empty | Config, API, pagination errors | **Fixed** |
| `read_drive_file` | N/A (returns content or errors) | Config, API errors | OK |
| `search_notes` | Returns empty list | N/A | OK (needs `display` migration — see stability doc Gap 5) |
| `list_notes` | Returns empty list | N/A | OK (needs `display` migration) |
| `read_note` | Returns content or error string | N/A | OK |
| `list_emails` | Returns `{"display": "No emails found.", ...}` | Config errors | OK |
| `search_emails` | Returns `{"display": "No emails found.", ...}` | Config errors | OK |
| `list_calendar_events` | Returns `{"display": "No events found.", ...}` | Config errors | OK |
| `search_calendar_events` | Returns `{"display": "No events found.", ...}` | Config errors | OK |
| `run_shell_command` | N/A | Returns error string (see stability doc Gap 3) | Needs fix |
| `post_slack_message` | N/A | Config errors | OK |

---

## Checklist

- [x] Fix `search_drive` to return empty result instead of `ModelRetry` on zero results
- [ ] Bump agent-level `retries=3` (tracked in `TODO-tool-call-stability.md` Gap 2)
- [ ] Add `UsageLimits(request_limit=25)` loop guard (tracked in `TODO-tool-call-stability.md` Gap 6)
- [ ] Convert `shell.py` error strings to `ModelRetry` (tracked in `TODO-tool-call-stability.md` Gap 3)
