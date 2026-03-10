# Plan Audit Log: Unified Model Resolution — `build_model()`
_Slug: unified-model-build | Date: 2026-03-09_

---

# Audit Log

## Cycle C1 — Team Lead

Shipped-work check performed against all four tasks. Every `done_when` criterion verified against
source and test output:

- TASK-1: `build_model()` at `_factory.py:17`; `make_subagent_model` absent. ✓
- TASK-2: `agent.py` imports and calls `build_model()`; no raw provider imports. ✓
- TASK-3: All subagent factories use `build_model()`; `delegation.py` passes `model_settings` per-run. ✓
- TASK-4: `test_build_model_api_params_in_extra_body` passes. ✓

No implementation work remains. Submitting to Core Dev and PO for verification sign-off only.

## Cycle C1 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** The refactor correctly closes the stated root cause — both paths now share a single merge
function with a defined precedence rule. Scope is tight and well-bounded. One dead-code smell and one
silent capability gap are noted as minors; neither blocks ship.

**Major issues:** none

**Minor issues:**

- **PO-m-1** [`co_cli/agent.py:90-91`]: `normalize_model_name` is imported and called *after*
  `build_model()` returns (line 86-88) purely to pass `normalized_model` into `assemble_prompt()`.
  This is a legitimate, separate usage (system-prompt assembly, not model construction), so it is not
  dead code. However it creates a subtle redundancy: `build_model()` internally normalises the name
  already (via `_factory.py:36`), then `agent.py` normalises the same string a second time two lines
  later. The result is correct but the double normalisation is invisible to the reader and could
  diverge if either call site is updated. Recommendation: expose the normalised name from
  `build_model()` return tuple (e.g. `model, model_settings, normalized`) or accept it as a
  parameter to `assemble_prompt()` directly — whichever keeps the callers in sync without a
  redundant import.

- **PO-m-2** [`co_cli/agents/_factory.py:60-67`]: Gemini silently drops `model_entry.api_params`.
  The TODO notes this as a known limitation ("no extra_body support for Gemini"), but there is no
  runtime warning when a Gemini `ModelEntry` carries non-empty `api_params`. A user who configures
  `api_params` on a Gemini role entry will get no error and no effect. Recommendation: log a warning
  (at debug or warning level) when `model_entry.api_params` is non-empty and provider is "gemini",
  so the silent drop is observable.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2
**Summary:** TASK-1, TASK-3, and TASK-4 are clean. TASK-2 has a stray live import the TL misread as absent. More critically, `resolve_role_model()` silently discards `model_settings` from `build_model()`, leaving the summarization and signal-analyzer callers unable to pass quirks settings to `agent.run()` — exactly the bug this refactor was designed to close.

**Checklist — TASK-1 (`co_cli/agents/_factory.py`)**
- [x] `build_model()` defined at line 17
- [x] `make_subagent_model` absent (grep: no matches anywhere in `co_cli/`)
- [x] `resolve_role_model()` calls `build_model()` at line 83
- [x] Merge precedence correct: quirks defaults → quirks `extra_body` → `model_entry.api_params` (each `dict.update()` overwrites, last wins)
- [x] `httpx`, `get_model_inference`, `normalize_model_name`, `ModelSettings` all imported at module level (lines 5, 8, 11)

**Checklist — TASK-2 (`co_cli/agent.py`)**
- [x] Imports `build_model` from `co_cli.agents._factory` (line 8)
- [x] `OpenAIChatModel`, `OpenAIProvider`, `get_model_inference` absent
- [ ] `normalize_model_name` is NOT absent — inline import at line 90, called at line 91. The TASK-2 `done_when` grep (line 71 of this TODO) lists `normalize_model_name` among the symbols to remove; TL's sign-off omits it. The import is live (not dead code), but its presence means the done_when criterion is unmet as written.
- [x] Gemini API key guard preserved before `build_model()` (lines 75–79)

**Checklist — TASK-3 (subagent factories + `delegation.py`)**
- [x] `coder.py` imports and calls `build_model()` (lines 7, 34); returns `tuple[Agent, ModelSettings | None]`
- [x] `research.py` imports and calls `build_model()` (lines 7, 32); returns `tuple[Agent, ModelSettings | None]`
- [x] `analysis.py` imports and calls `build_model()` (lines 7, 35); returns `tuple[Agent, ModelSettings | None]`
- [x] `delegation.py` unpacks `agent, model_settings` at all three callsites (lines 37, 98, 181)
- [x] All three `agent.run()` calls pass `model_settings=model_settings` (lines 42, 104, 186)
- [x] `ollama_num_ctx=ctx.deps.config.ollama_num_ctx` passed at all three callsites

**Checklist — TASK-4 (`tests/test_model_roles_config.py`)**
- [x] `test_build_model_api_params_in_extra_body` exists at line 123
- [x] Creates `ModelEntry(model="qwen3:q4_k_m", api_params={"think": False})`
- [x] Asserts `model_settings["extra_body"]["think"] is False`
- [x] Asserts `isinstance(model, OpenAIChatModel)`

**Additional checks**
- [x] No remaining `make_subagent_model` callers anywhere in `co_cli/` (grep: no matches)

---

**Major issues:**

- **CD-M-1** [`co_cli/agents/_factory.py:83`]: `resolve_role_model()` discards `model_settings` via `model, _ = build_model(...)`. Its two callers — `_history.py:200` (summarization) and `_signal_analyzer.py:107` (signal analysis) — receive only the model object. Both callers then invoke `agent.run()` without `model_settings`, silently dropping quirks-based temperature, top_p, max_tokens, and extra_body for all summarization and signal-analyzer runs. This is the same class of silent-drop bug the refactor was designed to eliminate; it is now present in a third code path not covered by any test. Recommendation: change `resolve_role_model()` to return `tuple[Any, ModelSettings | None]` and update both callers (`_history.py`, `_signal_analyzer.py`) to thread `model_settings` through to `agent.run()`.

- **CD-M-2** [`co_cli/agent.py:90–91`]: Inline `from co_cli.prompts.model_quirks import normalize_model_name` import persists inside `get_agent()` after the refactor. The TASK-2 `done_when` criterion (this file, line 71) lists `normalize_model_name` in the removal grep expression. The TL sign-off does not account for this import. The import is a live, legitimate dependency (used to normalise the model name for `assemble_prompt()`), so this is a scope boundary question rather than dead code — but the done_when criterion is not met as written. Recommendation: either (a) explicitly amend the done_when to exclude `normalize_model_name` and document why it is retained, or (b) move name normalisation into `assemble_prompt()` and remove the import from `agent.py`. Decision needed before closing TASK-2.

**Minor issues:**

- **CD-m-1** [`co_cli/agents/_factory.py:51–53`]: A new `httpx.AsyncClient` is created on every `build_model()` call with no lifecycle management — no `close()`, no connection sharing across calls. For delegation flows that invoke multiple subagents per turn, this leaks open connections until the event loop cleans them up. Recommendation: accept an optional `http_client: httpx.AsyncClient | None = None` parameter in `build_model()`, or move a shared client into `CoDeps.services` and pass it in at the callsite, so it is created once and closed on session teardown.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | Real gap: `resolve_role_model()` callers in `_history.py` and `_signal_analyzer.py` miss quirks settings. Added TASK-5 to fix return type and update both callers. |
| CD-M-2   | modify   | `normalize_model_name` at `agent.py:90-91` is a live dependency for `assemble_prompt()` — not model construction. Amended TASK-2 `done_when` note to explicitly document the retained import and its purpose. No code change needed. |
| CD-m-1   | reject   | httpx client lifecycle is a broader concern (shared client belongs in CoDeps.services overhaul). Out of scope for this refactor; consistent with current codebase patterns. |
| PO-m-1   | reject   | Expanding `build_model()` to return a third value (`normalized_name`) to avoid a cheap re-call is over-engineering. Double normalisation is harmless and correct. |
| PO-m-2   | adopt    | One-line warning log for Gemini api_params silent drop is a direct observability improvement. Added TASK-6. |

## Cycle C2 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** TASK-5 is correctly scoped — it changes exactly one function signature and two callers, no more. TASK-6 is the right one-liner. Both new tasks are a proportionate response to the C1 gaps. Plan is complete and ready to ship.

**Verification of C1 issues:**
- PO-m-1 rejection: agree — returning a third tuple element from `build_model()` to avoid one idempotent `normalize_model_name()` call is a net negative on readability. The double normalisation is pure string manipulation with no observable side effect; the TL call is correct.
- PO-m-2 adoption as TASK-6: confirmed — TASK-6 spec at `_factory.py` Gemini branch is exactly the right intervention. The warning carries the model name and the dropped params, which is enough for any operator to diagnose a misconfigured Gemini entry without changing the code path further.

**New issues (if any):**

None. Source verified: `resolve_role_model()` at `_factory.py:83` currently does `model, _ = build_model(...)` and both callers (`_history.py:200`, `_signal_analyzer.py:121`) invoke `agent.run()` without `model_settings`. TASK-5 spec directly closes this and touches exactly the three files listed — `_factory.py`, `_history.py`, `_signal_analyzer.py`. No collateral callers exist.

**Minor issues (if any):**

- **PO-m-3** [`co_cli/_history.py:_resolve_summarization_model`]: The wrapper `_resolve_summarization_model` currently returns `Any` and its caller `summarize_messages` accepts `model: str | Any`. Once TASK-5 changes `resolve_role_model()` to return `tuple[Any, ModelSettings | None]`, the wrapper signature and its two call-sites (`_history.py:401`, `_history.py:484`) will both need updating in the same sweep. The TASK-5 spec already lists `_history.py` as a file and describes updating `summarize_messages()`, so this is covered — but the `done_when` criterion should confirm `_resolve_summarization_model` itself is updated (not just its downstream callers), otherwise the wrapper silently absorbs the new return type without threading it through. Recommendation: extend the `done_when` grep to check that `_resolve_summarization_model` returns a tuple, not just that `model_settings` appears somewhere in the file.

## Cycle C2 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-3
**Summary:** Both C1 blockers are resolved by TL decisions and plan updates; CD-m-1 rejection is properly documented with rationale. TASK-6 is sound. TASK-5 has a spec completeness gap: `_run_summarization_with_policy()` is the actual intermediary between the two `_history.py` callsites and `summarize_messages()`, and the spec does not mention updating its signature — an implementer following the spec literally will not produce a working fix.

**Verification of C1 blockers:**

- **CD-M-1:** resolved — TASK-5 added with a `files:` list covering all three files (`_factory.py`, `_history.py`, `_signal_analyzer.py`), `prerequisites: [TASK-1]`, and a two-grep machine-verifiable `done_when`. Bug existence confirmed in source: `_history.py:200` returns `resolve_role_model(...)` as a bare value (single return); `summarize_messages()` at line 223 calls `summariser.run(prompt, message_history=messages)` with no `model_settings`. `_signal_analyzer.py:107` assigns `resolved_model: Any = resolve_role_model(...)` (single value, `Any` type annotation); `signal_agent.run(window)` at line 121 has no `model_settings`. Both bugs are live.

- **CD-M-2:** resolved — TASK-2 carries an explicit `Note:` (TODO lines 73–75) stating `normalize_model_name` is retained as a live dependency for `assemble_prompt()` (system-prompt assembly, separate concern from model construction), and the `done_when` grep "intentionally excludes it." Rationale is adequate; no code change was required.

- **CD-m-1 rejection:** confirmed documented — TL decisions table entry states: "httpx client lifecycle is a broader concern (shared client belongs in CoDeps.services overhaul). Out of scope for this refactor; consistent with current codebase patterns."

**New issues:**

- **CD-M-3** [`docs/TODO-unified-model-build.md` TASK-5 spec / `co_cli/_history.py:252–282`]: The TASK-5 spec describes updating `summarize_messages()` and "its caller" to unpack and pass `model_settings`, but the actual call chain has an intermediary that the spec omits: both `truncate_history_window()` (line 401) and `compact_history()` (line 484) invoke `_run_summarization_with_policy(dropped, model, ...)`, which itself calls `summarize_messages(messages, model, ...)`. Neither the spec nor the `done_when` criterion mentions `_run_summarization_with_policy()`. An implementer who updates only `_resolve_summarization_model()`, `summarize_messages()`, and the two top-level callsites will leave `_run_summarization_with_policy()` as a `model`-only intermediary, so `model_settings` will never reach `summariser.run()` — the bug will appear closed (grep passes) but remain functionally present. Recommendation: amend TASK-5 spec to add `_run_summarization_with_policy()` explicitly — add `model_settings: ModelSettings | None = None` to its signature and pass it through to `summarize_messages()`. Update `done_when` to verify `model_settings` threading inside `_run_summarization_with_policy` specifically.

**Minor issues:**

- **CD-m-2** [`docs/TODO-unified-model-build.md` TASK-6 `done_when`]: The `done_when` grep uses the pattern `"api_params\|warning\|logger"`. `logger` alone is not specific enough — the criterion would pass if any unrelated `logger` reference is present in `_factory.py` after TASK-5 ships. Today `_factory.py` has no logger at all (confirmed: `grep -n "logger\|logging" co_cli/agents/_factory.py` returns no output), so TASK-6 must add it. The criterion is correct as a completion signal once the file is otherwise clean, but it is fragile as a precision check. Recommendation: tighten to two explicit greps — one for `logger = logging.getLogger(__name__)` at module level, one for `logger.warning` inside a line that also references `api_params` — or split into separate `done_when` checks. Minor; does not block.

## Cycle C2 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-3   | adopt    | Confirmed full chain in source: `_run_summarization_with_policy()` and `summarize_messages()` both need `model_settings` parameters. Extended TASK-5 spec to cover all four functions plus thin wrapper. Tightened `done_when` to three targeted greps. |
| CD-m-2   | adopt    | TASK-6 `done_when` tightened to two specific greps: module-level logger definition + `logger.warning` line referencing `api_params`. |
| PO-m-3   | adopt    | Same issue as CD-M-3; already covered by TASK-5 spec update. |

## Cycle C3 — Core Dev
**Assessment:** approve
**Blocking:** none
**Summary:** Both C2 blockers are resolved. TASK-5 spec now names `_run_summarization_with_policy()` explicitly, specifies the `model_settings` parameter addition, covers the thin `/new` wrapper, and carries three targeted greps that are specific enough to detect a partial fix. TASK-6 `done_when` is now two precise greps. Plan is complete and ready to implement.

**Verification of C2 blockers:**
- **CD-M-3:** resolved — TASK-5 spec (lines 133–155) explicitly lists the full call chain, names `_run_summarization_with_policy()` with `model_settings: ModelSettings | None = None`, covers the thin `/new` wrapper at line ~230, and the middle `done_when` grep requires `model_settings` to appear in context of all three intermediaries (`_resolve_summarization_model`, `_run_summarization_with_policy`, `summarize_messages`) — not just anywhere in the file. The `_signal_analyzer.py` spec is unchanged and correct.
- **CD-m-2:** resolved — TASK-6 `done_when` now has two specific greps: `logger = logging.getLogger` for module-level logger presence and `logger.warning` referencing `api_params` for the functional log line. These are precise and non-falsifiable by unrelated log statements.
