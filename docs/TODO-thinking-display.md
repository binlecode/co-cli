# TODO: Streaming Thinking Display

**Origin:** During a chat session, the status line shows "Co is thinking..." but the actual thinking/reasoning content from the LLM is invisible. Users can only see it after the fact via `co tail --verbose`. This TODO adds real-time thinking display during streaming, gated behind `--verbose`.

**Related issue:** When thinking is hidden, the user has no visibility into *why* the model chose a wrong tool (e.g. calling `search_drive_files` when the user said "that file is in your folder"). Showing thinking makes bad tool routing debuggable in real time.

---

## Current State

- pydantic-ai emits `ThinkingPartDelta` events during streaming (confirmed in `messages.py:1591`)
- Thinking is captured in OTel spans and viewable post-hoc via `co tail --verbose`

## Design Decision: `--verbose` (not always-on)

Peer convergence (2+ top systems agree):

| System | Thinking display | Default? |
|--------|-----------------|----------|
| **Claude Code** | Dimmed italic text | Always shown (exception — Claude is verbose by nature) |
| **Gemini CLI** | "Thinking..." spinner; content hidden | Spinner default, content opt-in |
| **Aider** | Not shown during streaming | Hidden |
| **Codex** | Collapsed reasoning blocks | Collapsed by default |

**Decision:** Show thinking content only with `--verbose` / `-v` flag. Reasons:
1. Thinking tokens are 2-5x the final response — too noisy for default display
2. Consistent with existing `co tail --verbose` pattern
3. Keeps the default UX clean — users who want debug visibility opt in
4. The "Co is thinking..." status line already provides awareness without content

---

## Phase 1 (MVP): Stream thinking in a dim Panel — DONE

### Items

- [x] Add `"thinking"` style to both theme palettes in `co_cli/display.py`
- [x] Add `--verbose` / `-v` flag to `chat()` command in `co_cli/main.py`
- [x] Thread `verbose` through `chat_loop()` → `_stream_agent_run()` → `_handle_approvals()`
- [x] Import `ThinkingPartDelta` from `pydantic_ai.messages` in `main.py`
- [x] Add `thinking_buffer` + `thinking_live` state to `_stream_agent_run()`
- [x] Handle `ThinkingPartDelta` events: accumulate + render in Panel (verbose) or discard (default)
- [x] Flush thinking panel on transition to text/tool events
- [x] Cleanup thinking `Live` in the `finally` block

### File changes

| File | Change |
|---|---|
| `co_cli/main.py` | `--verbose` flag, `ThinkingPartDelta` handler, thinking buffer/panel lifecycle |
| `co_cli/display.py` | Add `"thinking"` semantic style to both palettes |

---

## Phase 2: Terminal ModelRetry for config/auth errors

**Context:** The thinking display makes wrong-tool-choice *visible* but doesn't fix it. The deeper issue is that `ModelRetry` for config/auth errors causes blind retries (same tool called 4 times) instead of reflecting the error back so the model can pick a different tool.

This phase is scoped narrowly to terminal errors in Google tools. The broader `ModelHTTPError` reflection pattern is tracked in `TODO-ollama-tool-call-resilience.md`.

### Classify terminal vs transient errors in Google tools

In `co_cli/tools/google_drive.py`, `google_gmail.py`, `google_calendar.py`:

- `GOOGLE_NOT_CONFIGURED` (missing credentials) → **terminal** — retrying won't help
- `GOOGLE_API_NOT_ENABLED` (API disabled in project) → **terminal**
- Network/quota errors from `googleapiclient` → **transient** — retry is appropriate

### Non-retryable tool errors

The simplest approach: return an error dict instead of raising `ModelRetry` for terminal failures:

```python
if not creds:
    return {"display": "Google Drive is not configured. ...", "error": True}
```

This stops the retry loop immediately — the model sees the error in the tool result and can pick a different tool.

### Items

- [ ] Research pydantic-ai's pattern for non-retryable tool errors (return error dict vs raise)
- [ ] Change `GOOGLE_NOT_CONFIGURED` handling from `ModelRetry` to error return in all Google tools
- [ ] Change `GOOGLE_API_NOT_ENABLED` handling similarly
- [ ] Keep `ModelRetry` only for transient errors (network, quota, rate limit)
- [ ] Add eval case: prompt that should route to shell, not Drive, when Drive is unconfigured

### File changes

| File | Change |
|---|---|
| `co_cli/tools/google_drive.py` | Terminal errors return error dict instead of `ModelRetry` |
| `co_cli/tools/google_gmail.py` | Same |
| `co_cli/tools/google_calendar.py` | Same |
| `evals/eval_tool_calling-data.json` | Add eval case for tool routing with unconfigured service |

---

## Phase 3 (Post-MVP): Tool routing hints in system prompt

Improve the model's tool selection when multiple tools could match.

### Add tool-priority guidance to system prompt

In the `agent.py` system prompt, add a section:

```
### Tool Selection Priority
- For files in the current project directory: use run_shell_command (ls, cat, find)
- For notes/knowledge base: use search_notes / read_note (Obsidian vault)
- For cloud documents: use search_drive_files / read_drive_file (Google Drive)
- For web content: use web_search / web_fetch
- When a tool returns a config error, switch to an alternative tool — do not retry the same tool
```

### Items

- [ ] Add tool-priority section to system prompt in `co_cli/agent.py`
- [ ] Add eval cases for ambiguous "find this file" prompts

### File changes

| File | Change |
|---|---|
| `co_cli/agent.py` | Tool routing guidance in system prompt |
| `evals/eval_tool_calling-data.json` | Ambiguous file-finding eval cases |

---

## Verification

- Phase 1: `uv run co chat --verbose` → ask a question that triggers thinking → thinking content streams in a dim bordered panel before the response
- Phase 1: `uv run co chat` (no flag) → same question → no thinking panel, only "Co is thinking..." status
- Phase 2: Unconfigure Google credentials → ask "find the TODO file in this folder" → model should get error dict back, not retry 4 times, and fall back to shell
- Phase 3: `uv run python scripts/eval_tool_calling.py` → new eval cases pass
