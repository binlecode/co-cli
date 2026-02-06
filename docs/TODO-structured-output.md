# TODO: Structured Output — `Agent[CoDeps, str | CoResponse]`

**Goal:** Migrate from `Agent[CoDeps, str]` to `Agent[CoDeps, str | CoResponse]` so tool results reach the user verbatim — no reformatting, no dropped URLs, no ignored pagination.

**Reference:** https://ai.pydantic.dev/output-types/

---

## 1. Problem

The LLM reformats tool output. Tools return carefully formatted `dict[str, Any]` with a `display` field containing URLs, pagination hints, and structured text — but the LLM receives the dict as a tool result and then generates a **free-form `str`** as its final output. Common failures:

| Symptom | Example |
|---------|---------|
| URLs dropped | `search_drive` returns `webViewLink` per file; LLM outputs a table without links |
| Pagination ignored | `search_drive` returns `has_more=true`; LLM says "here are your results" with no mention of more pages |
| Content summarized | `list_emails` returns 5 emails with previews; LLM condenses to "You have 5 recent emails" |
| Metadata lost | `list_calendar_events` returns attendees + Meet links; LLM shows only title + time |

The system prompt (`agent.py:47-64`) instructs the LLM to "show tool output directly — don't summarize or paraphrase", but this is a suggestion, not a contract. With `str` output, the LLM can always ignore it.

## 2. Root Cause

```python
# agent.py:66
agent: Agent[CoDeps, str] = Agent(model, deps_type=CoDeps, ...)
```

`output_type=str` means the agent's final output is unconstrained text. The system prompt asks for passthrough behavior, but there's no structural enforcement. The LLM generates a brand new string rather than forwarding the tool's `display` field.

## 3. Solution: `str | CoResponse` Union Output

pydantic-ai supports union output types. When the agent returns a `CoResponse`, it **must** fill the `display` field (tool output verbatim) and a `summary` field (LLM commentary). When it returns `str`, it's a normal conversational reply.

```python
from pydantic import BaseModel

class CoResponse(BaseModel):
    """Structured response wrapping tool output for direct display."""
    display: str   # Tool output verbatim — rendered as-is to user
    summary: str   # LLM commentary (e.g. "Here are your Drive results")
```

The agent chooses which type to return:
- **Tool results** → `CoResponse` with `display` = tool's `display` field, `summary` = brief context
- **Conversation** → plain `str` (greetings, follow-up questions, explanations)

## 4. Changes

### 4.1 New model: `co_cli/models.py`

```python
from pydantic import BaseModel

class CoResponse(BaseModel):
    """Structured response wrapping tool output for direct display."""
    display: str
    summary: str
```

Small enough to be a single file. No deps, no imports beyond pydantic.

### 4.2 `agent.py` — output_type + system prompt update

```python
from co_cli.models import CoResponse

agent: Agent[CoDeps, str | CoResponse] = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
    output_type=[str, CoResponse],  # union
)
```

Add to system prompt:

```
### Output Format
When showing tool results, respond with a CoResponse:
- display: the tool's `display` field verbatim — do not modify, summarize, or reformat
- summary: a brief one-line note (e.g. "Found 10 files" or "Here are your emails")

For normal conversation (greetings, questions, explanations), respond with plain text.
```

### 4.3 `main.py` — handle `CoResponse` in display

Current code (`main.py:151`):
```python
console.print(Markdown(result.output))
```

New code:
```python
from co_cli.models import CoResponse

output = result.output
if isinstance(output, CoResponse):
    if output.summary:
        console.print(f"[dim]{output.summary}[/dim]")
    console.print(Markdown(output.display))
else:
    console.print(Markdown(output))
```

`CoResponse.display` is rendered as Markdown (preserving URLs). `summary` is shown dimmed above it as context.

## 5. Interaction with TODO-approval-flow.md

Both this change and the approval-flow migration modify `output_type`. When both are implemented, combine them:

```python
from pydantic_ai import DeferredToolRequests
from co_cli.models import CoResponse

agent: Agent[CoDeps, str | CoResponse | DeferredToolRequests] = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
    output_type=[str, CoResponse, DeferredToolRequests],
)
```

The `main.py` display handler becomes:

```python
output = result.output
if isinstance(output, DeferredToolRequests):
    result = await _handle_approvals(agent, deps, result)
    output = result.output

if isinstance(output, CoResponse):
    if output.summary:
        console.print(f"[dim]{output.summary}[/dim]")
    console.print(Markdown(output.display))
else:
    console.print(Markdown(output))
```

**Order of implementation doesn't matter** — either can land first. The second one adds its type to the existing union.

## 6. Gemini Compatibility

pydantic-ai + Gemini works with flat Pydantic models. `CoResponse` is flat (two `str` fields) so no wrapper is needed.

If issues arise with union discrimination (Gemini struggling to choose between `str` and `CoResponse`), there are two fallbacks:

1. **`NativeOutput` wrapper** (per pydantic-ai issue #3483) — wraps the union in a discriminated container
2. **Single output type** — always return `CoResponse`, with `display=""` for conversational replies

Start with the union approach. Only fall back if Gemini fails to pick the right type.

## 7. Tool Return Types — No Change Needed

Tools already return `dict[str, Any]` with a `display` field. These return values go to the LLM as **tool results** (not agent output). The LLM then constructs a `CoResponse` from them.

Current tool return types stay exactly as they are:

| Tool | Return type | Has `display`? |
|------|------------|----------------|
| `search_drive` | `dict[str, Any]` | Yes — files with URLs, pagination |
| `read_drive_file` | `str` | No — raw file content |
| `list_emails` | `dict[str, Any]` | Yes — emails with links |
| `search_emails` | `dict[str, Any]` | Yes — emails with links |
| `draft_email` | `str` | No — confirmation message |
| `list_calendar_events` | `dict[str, Any]` | Yes — events with links |
| `search_calendar_events` | `dict[str, Any]` | Yes — events with links |
| `search_notes` | `list[dict]` | No — file + snippet pairs |
| `list_notes` | `list[str]` | No — file paths |
| `read_note` | `str` | No — raw note content |
| `run_shell_command` | `str` | No — command output |
| `post_slack_message` | `str` | No — confirmation message |

## 8. Affected Tools Audit

### High benefit — should use `CoResponse` passthrough

These tools have a `display` field designed for verbatim rendering. The LLM must use `CoResponse` with `display` = tool's display field:

- **`search_drive`** — URLs, pagination, file IDs all in `display`
- **`list_emails`** — clickable Gmail links in `display`
- **`search_emails`** — clickable Gmail links in `display`
- **`list_calendar_events`** — Meet links, attendees, event links in `display`
- **`search_calendar_events`** — same as above

### Low benefit — `str` output is fine

These tools return plain strings or simple data. The LLM can respond with `str`:

- **`read_drive_file`** — raw file content, LLM may need to summarize or explain
- **`draft_email`** — one-line confirmation
- **`run_shell_command`** — raw terminal output, LLM may need to interpret
- **`read_note`** — raw note content
- **`post_slack_message`** — one-line confirmation

### Needs migration — currently returns `list`, not `dict` with `display`

These Obsidian tools predate the `display` convention. They should be migrated separately (not part of this change):

- **`search_notes`** — returns `list[dict]` (file + snippet pairs). Should become `dict[str, Any]` with `display`.
- **`list_notes`** — returns `list[str]` (file paths). Should become `dict[str, Any]` with `display`.

## 9. Verification Steps

### Manual testing

1. `uv run co chat` → "search drive for invoices"
   - Verify: URLs visible in output (not dropped)
   - Verify: pagination hint shown when `has_more=true`
2. `uv run co chat` → "show my emails"
   - Verify: Gmail links visible in output
3. `uv run co chat` → "what's on my calendar today"
   - Verify: Meet links and attendees visible
4. `uv run co chat` → "hello" (conversational)
   - Verify: returns plain `str`, not `CoResponse`

### Structural checks

- `result.output` is `CoResponse` after tool calls with `display` fields
- `result.output` is `str` for conversational turns
- `CoResponse.display` exactly matches the tool's `display` field (no modification)

## Checklist

- [ ] Create `co_cli/models.py` with `CoResponse`
- [ ] Update `agent.py`: `output_type=[str, CoResponse]`
- [ ] Update `agent.py`: system prompt with `### Output Format` section
- [ ] Update `main.py`: handle `CoResponse` in display logic
- [ ] Manual test: Drive search shows URLs
- [ ] Manual test: Gmail list shows links
- [ ] Manual test: Calendar shows Meet links
- [ ] Manual test: conversational reply is plain `str`
- [ ] (Follow-up) Migrate `search_notes` and `list_notes` to `dict` with `display` field
- [ ] (Follow-up) Combine with `DeferredToolRequests` when approval-flow lands
