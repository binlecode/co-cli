# Plan: Prompt Cache — Message Normalization + Gemini Context Caching

Task type: code-feature

---

## Context

Identified via cross-peer review of co-cli vs. hermes-agent prompt cache design.
No prior plan exists for this slug.
No active exec-plans cover LLM call normalization or Gemini caching.
No spec update tasks are included — `sync-doc` will update `docs/specs/` post-delivery.

**Current-state validation:** `co_cli/context/_history.py` registers three history processors
(`truncate_tool_results`, `compact_assistant_responses`, `summarize_history_window`) in
`co_cli/agent/_core.py:build_agent()`. No normalization processor exists. `LlmSettings` in
`co_cli/config/_llm.py` supports `ollama-openai` and `gemini` providers; no Gemini context
caching fields exist. `google-genai>=1.61.0` is already a declared dependency in `pyproject.toml`.
pydantic-ai's `GoogleModelSettings` exposes `google_cached_content: str` (see
`pydantic-ai/pydantic_ai_slim/pydantic_ai/models/google.py:220`), which is passed through
to `cached_content=` in the `GenerateContentRequest`.

---

## Problem & Outcome

**Problem:** Co-cli does not normalize message content before API calls, and does not use
Gemini's context caching API for the stable system prompt.

**Failure cost:**
- Minor whitespace variations in message content break KV cache prefix matching on every backend
  (Ollama, Gemini, any inference server with a KV cache). Every turn pays a full re-encode of
  all prior context instead of reusing the cached prefix.
- For Gemini sessions, the static system prompt (~4–8K tokens depending on personality and rules)
  is re-encoded on every turn. With context caching that cost drops to a single write + cheap
  read hits.

**Outcome:**
- Normalized messages → consistent bit-exact prefixes across turns → improved KV cache hit rate
  on all backends. No visible behavior change to the user.
- Gemini sessions with caching enabled → ~75% reduction in effective input token cost for the
  system prompt portion per turn; reflected in lower `input_tokens` usage reported after the
  first turn.

---

## Scope

**In scope:**
- Message normalization history processor (whitespace strip on text content; tool-call arg
  JSON canonicalization for `ArgsStr` variants).
- Gemini context caching: config fields, cache manager module, bootstrap creation,
  per-turn injection into model settings.

**Out of scope:**
- Anthropic `cache_control` markers — no Anthropic provider exists in co-cli today.
- Cache invalidation UI — session boundary is sufficient for MVP.
- Cache hit metrics in the TUI — observable via existing OTel token usage spans.
- TTL extension / cache refresh within a session — single TTL per session is sufficient.

**Verifiability note:** For Gap 1, the test gate covers normalization correctness (deterministic
output). KV cache hit-rate improvement is an expected backend side-effect, not a testable outcome
from co-cli's vantage point.

---

## Behavioral Constraints

1. Normalization must not mutate content fields on shared part objects. pydantic-ai passes
   a shallow list copy to processors (`message_history[:]`), so the message objects inside
   are the same references as stored history. The processor must replace part objects (create
   new part instances with normalized content) rather than mutating `.content` in-place on
   shared objects. This matches the existing `truncate_tool_results` and
   `compact_assistant_responses` patterns.
2. Normalization must not change content semantics — only strip leading/trailing whitespace
   from string-typed text parts and canonicalize tool-call arg JSON layout.
3. If Gemini cache creation fails (API error, quota, network), the session must continue
   without caching. Failure is logged at WARNING level; no exception is surfaced to the user.
4. Gemini caching must be disabled by default (`context_cache_enabled: bool = False`).
   A user must explicitly opt in via `settings.json` or env var.
5. Cache creation is skipped when the static system prompt is below
   `context_cache_min_tokens` (default 4096). Tokens are estimated from character count
   (chars / 4), not via an API call.
6. The cached content name is session-scoped: created once at session start, stored in
   `CoSessionState`, never shared across sessions.
7. Per-turn model settings injection must not override an explicitly provided `model_settings`
   parameter — it merges only when `model_settings is None` at `run_turn()` entry.
   **Known limitation:** any future caller that passes a partial `model_settings` (e.g. to
   override temperature) will lose the cache injection silently. A future plan should address
   this with a dict-merge approach rather than None-replacement.

---

## High-Level Design

### Gap 1 — Message normalization

A new synchronous history processor `normalize_messages` is added to
`co_cli/context/_history.py`. For each message it returns a new message built with
**replaced** (not mutated) part objects:
- `SystemPromptPart`, `UserPromptPart` (str content), `TextPart`, `ThinkingPart`,
  `ToolReturnPart` (str content): replaced with a new part instance where `.content`
  is `.strip()`-ed.
- `ToolCallPart` with `ArgsStr` args: replaced with a new part instance where `.args`
  is re-serialized as `json.dumps(json.loads(raw), separators=(",", ":"), sort_keys=True)`.
  Invalid JSON is left unchanged (log at DEBUG).

Parts not matching these types (e.g. `RetryPromptPart`, image/binary parts) are passed
through unchanged. The processor returns a new list with new message objects — part
replacement follows the same pattern as `truncate_tool_results` and
`compact_assistant_responses` to avoid mutating shared part references.

It is registered as the **first** processor in `build_agent()` so normalization runs
before truncation and compaction.

### Gap 2 — Gemini context caching

**Config** (`co_cli/config/_llm.py`): three new fields on `LlmSettings`:
- `context_cache_enabled: bool = False`
- `context_cache_ttl_minutes: int = 60`
- `context_cache_min_tokens: int = 4096`

**Cache manager** (`co_cli/llm/_gemini_cache.py`): pure module, no class state.
`create_gemini_cache(api_key, model_name, system_instruction, ttl_minutes, min_tokens) -> str | None`
- Estimates token count (chars / 4); returns `None` **before constructing the client**
  if below threshold (so the below-threshold path incurs zero network activity).
- Creates a `google.genai.Client(api_key=api_key)` and calls:
  `cache = client.caches.create(model=model_name, config={"system_instruction": system_instruction, "ttl": f"{ttl_minutes * 60}s"})`
  `system_instruction`-only (no `contents`) is intentional and valid for system-prompt-only
  caching per the Gemini API spec.
- Returns `cache.name` (e.g. `"cachedContents/xyz123"`) — the `CachedContent` object
  returned by `caches.create()` carries the name in `.name`.
- Returns `None` on any exception (logs at WARNING).

**Bootstrap** (`co_cli/bootstrap/core.py`): inside `create_deps()`, after provider validation,
if `config.llm.uses_gemini() and config.llm.context_cache_enabled`:
- Calls `build_static_instructions(config)` to get the instruction text.
- Calls `create_gemini_cache(...)`.
- Stores result in `deps.session.gemini_cache_name`.

**Session state** (`co_cli/deps.py`): new field on `CoSessionState`:
`gemini_cache_name: str | None = None`

**Per-turn injection** (`co_cli/context/orchestrate.py`, inside `run_turn()`):
**Before the `while True:` loop**, resolve the effective model settings for the turn:
- If `model_settings is None` (no explicit override), AND
  `deps.config.llm.uses_gemini()`, AND
  `deps.session.gemini_cache_name is not None`
  → use `isinstance(deps.model.settings, GoogleModelSettings)` to safely downcast, then
  create `effective_settings = deps.model.settings.model_copy(update={"google_cached_content": deps.session.gemini_cache_name})`.
  The `uses_gemini()` guard ensures the cast is safe at runtime; the isinstance check satisfies the type checker.
- Otherwise: `effective_settings = model_settings` (passthrough, including explicit None).

Pass `effective_settings` to all `_execute_stream_segment()` calls and to `_run_approval_loop()`
within the turn, so both the initial segment and approval-loop resumptions receive
the cache-injected settings.

---

## Implementation Plan

### TASK-1: Message normalization history processor

```
files:
  - co_cli/context/_history.py        # add normalize_messages processor
  - co_cli/agent/_core.py             # register as first history_processor
  - tests/test_history.py             # add normalization tests

done_when: >
  uv run pytest tests/test_history.py -x passes, including:
  - a test that builds a ModelRequest with UserPromptPart(content="hello  \n") and
    ModelResponse with TextPart(content="  answer\t") and ToolCallPart with args='{"b": 1, "a": 2}',
    passes the list through normalize_messages(), and asserts on the *output* part objects:
    output UserPromptPart.content == "hello", output TextPart.content == "answer",
    output ToolCallPart.args == '{"a":2,"b":1}' (the args field on the replaced part).
  - a test that input part objects are not mutated (original UserPromptPart.content still == "hello  \n").
  All pre-existing test_history.py tests continue to pass.

success_signal: N/A — no user-visible behavior change.
```

### TASK-2: Gemini cache config + manager module

```
files:
  - co_cli/config/_llm.py             # add context_cache_enabled, context_cache_ttl_minutes,
                                       # context_cache_min_tokens to LlmSettings
  - co_cli/llm/_gemini_cache.py       # new: create_gemini_cache()
  - tests/test_gemini_cache.py        # new: functional tests for cache manager

done_when: >
  uv run pytest tests/test_gemini_cache.py -x passes, including:
  - test that create_gemini_cache() with real Gemini API key, valid model, and
    system_instruction of >= 4096 estimated tokens returns a non-empty cache name string.
  - test that create_gemini_cache() with system_instruction below min_tokens returns None;
    the implementation must check the threshold before constructing google.genai.Client,
    so this test asserts None is returned with no API interaction (pure return-value check).

success_signal: N/A — internal module; no user-visible behavior change at this task.
prerequisites: []
```

### TASK-3: Bootstrap wiring, session state, and per-turn injection

```
files:
  - co_cli/deps.py                    # add gemini_cache_name to CoSessionState
  - co_cli/bootstrap/core.py          # call create_gemini_cache and store in deps.session
  - co_cli/context/orchestrate.py     # merge cache name into GoogleModelSettings per turn
  - tests/test_llm_gemini.py          # add/update tests

done_when: >
  uv run pytest tests/test_llm_gemini.py -x passes, including a test that:
  - loads config with llm.provider="gemini", llm.context_cache_enabled=True,
    llm.context_cache_min_tokens=100 (low threshold for testing)
  - calls the bootstrap path (or the relevant create_gemini_cache() + session wiring)
  - asserts deps.session.gemini_cache_name is a non-empty string
  - performs a live agent.run_stream_events() call with the cache-merged model_settings
    and asserts the response usage includes cached_content_tokens > 0 in UsageDetails.

success_signal: >
  With Gemini provider and context_cache_enabled=true in settings.json, running `uv run co chat`
  shows lower input_tokens on turns after the first (system prompt tokens served from cache).

prerequisites: [TASK-2]
```

---

## Testing

- TASK-1 tests are fully offline (no LLM calls) — just message object construction and processor output assertions.
- TASK-2 tests require a live Gemini API key; mark with `pytest.mark.skipif` guard on missing `GEMINI_API_KEY`.
- TASK-3 tests require a live Gemini API key; same skipif guard.
- No new test files touch `docs/specs/`.
- Full suite run: `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log`

---

## Open Questions

None — all open questions resolved by source inspection before drafting.

## Final — Team Lead

Plan approved. All blocking issues resolved across two cycles (3× CD-M adopted; 2× PO-m adopted; 2× CD-m C2 adopted). Ready for Gate 1.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev prompt-cache-two-gaps`
