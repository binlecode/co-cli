# Plan: LLM Call Audit — Gap Remediation

**Task type:** code-feature

## Context

`scripts/llm_call_audit.py` audits LLM calls from a pytest run by correlating harness log
detail lines with OTel chat spans in `~/.co-cli/co-cli-logs.db`. A four-gap analysis
surfaced in session on 2026-04-18:

- **Gap 1:** `gen_ai.usage.output_tokens` bundles thinking + response tokens with no split.
  Ollama's OpenAI-compatible API does not return `completion_tokens_details`, so
  `gen_ai.usage.details.reasoning_tokens` is never populated. Throughput (tokens/s) is
  computable but thinking overhead is invisible.
- **Gap 2:** Thinking content (`ThinkingPart`) was present in output messages for `agent`
  spans up to 2026-04-14 (service.version 0.7.48) but is absent from all spans at
  v0.7.203+. Sub-agents (`_dream_miner_agent`, `_summarizer_agent`, etc.) never had it.
  The regression blocks any reasoning-effectiveness analysis.
- **Gap 3:** `gen_ai.input.messages` stores only the delta user message for each API call,
  not the full accumulated conversation history. This is a design constraint of per-call
  OTel instrumentation and is accepted as a known limitation (full history lives in JSONL
  session files; cross-referencing is future work).
- **Gap 4:** Pytest harness detail lines (`[pytest-harness]   Xs | chat <model> | ...`)
  carry only duration/model/api/provider. Token counts and finish reasons are absent,
  making the log alone insufficient for any metric beyond timing.

`include_content=True` is already set in `InstrumentationSettings` in both
`co_cli/main.py` and `tests/_co_harness.py`. The Gap 2 regression is therefore NOT an
`include_content` misconfiguration — it is in how pydantic-ai extracts `ThinkingPart` from
the Ollama response (tag-based vs structured field), which likely changed between
pydantic-ai releases.

**Workflow hygiene:** No stale exec-plans found for this slug.

## Problem & Outcome

**Problem:** The audit script can verify API routing and finish reasons but cannot report
on inference cost, reasoning overhead, or output quality signals — the three dimensions
most useful for detecting regressions in a thinking-model test suite.

**Failure cost:** Engineers running the audit after a model or config change have no
visibility into: (a) whether the model's throughput degraded, (b) whether reasoning traces
disappeared silently, or (c) whether background sub-agents are burning more tokens per
task than expected.

**Outcome:** After this delivery:
1. The harness log carries token + finish data per chat span; the audit script also
   surfaces these fields for unmatched spans (log-extracted path), making triage
   partially possible without a DB hit.
2. The audit script reports: total-run token cost, per-flow throughput (tokens/s), output
   efficiency ratio (output/input), and thinking-content presence/absence.
3. Thinking content is restored in chat spans for the main `agent`, enabling future
   reasoning-trace analysis.
4. Gap 3 (full conversation context) is documented as accepted limitation in the script
   and report scope section.

## Scope

**In scope:**
- Extend `_span_detail()` in `tests/_co_harness.py` to emit `in_tokens=N out_tokens=N finish=X`
  for `chat *` spans (Gap 4). Co-change: update `_DETAIL_PAT` and `_parse_log()` in
  `scripts/llm_call_audit.py` to extract these fields and populate unmatched spans.
- Add cost/throughput section to `scripts/llm_call_audit.py` using existing
  `gen_ai.usage.*` data (Gap 1 partial).
- Diagnose and restore `ThinkingPart` capture in chat spans (Gap 2) — root cause in
  pydantic-ai's Ollama thinking extraction path.
- Add reasoning-signal section to audit script: thinking-content presence rate,
  thinking char ratio, tool-call depth per flow (Gap 2 partial, using DB content).
  `ChatSpan` gains `output_msgs: str | None` to carry raw output JSON to reporting helpers.

**Out of scope:**
- Full conversation context capture per span (Gap 3) — accepted limitation.
- Thinking token count separate from output tokens — Ollama does not return
  `completion_tokens_details`; no server-side fix available.
- LLM-as-judge semantic completeness or coherence evaluation.
- Pricing-table dollar-cost estimates — local Ollama only; throughput is the meaningful
  signal.

## Behavioral Constraints

- `tests/_co_harness.py` changes must not alter the existing summary line format
  (`[pytest-harness] <test_id> | ...`); only detail lines are extended.
- `scripts/llm_call_audit.py` must degrade gracefully when a span has no DB match —
  new sections must tolerate `None` for all DB-sourced fields; log-extracted token values
  are used as fallback on unmatched spans.
- Thinking content restoration must not affect spans for tests that use mock/function
  models (`chat function::*`) — those have no `ThinkingPart`.
- No new pytest fixtures or `monkeypatch` usage anywhere.
- All new test assertions must exercise production code paths.

## High-Level Design

### TASK-1: Diagnose and restore thinking content in spans

Root cause: pydantic-ai extracts `ThinkingPart` from Ollama responses using either
`openai_chat_thinking_field` (structured JSON field in the response) or `thinking_tags`
(XML-style `<think>...</think>` tags). The Ollama profile for `qwen3.5:35b-a3b-think`
may have changed which mechanism is active. The extraction is in
`models/openai.py::_process_thinking()` and `split_content_into_text_and_thinking()`.

Approach: read the Ollama model profile in the current pydantic-ai version to confirm
which extraction path is used; run a minimal live call and inspect raw response; verify
`ThinkingPart` appears in `ModelResponse.parts`; confirm it reaches span attributes.
If the Ollama profile changed, apply the correct capability setting via
`co_cli/config/` or `ModelSettings`.

### TASK-2: Extend harness detail lines + audit script parsing (Gap 4)

`_span_detail()` in `tests/_co_harness.py` builds the per-span line. For `chat *` spans,
append `in_tokens=N out_tokens=N finish=X` using attributes `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`. Guard: emit only if
attribute is non-None (zero is a valid diagnostic value).

Co-change in `scripts/llm_call_audit.py`: update `_DETAIL_PAT` to also capture the
`in_tokens=` / `out_tokens=` / `finish=` fields, and update `_parse_log()` to return them
alongside duration. Populate `input_tokens` / `output_tokens` / `finish_reasons` on
unmatched `ChatSpan` entries from log-extracted values so log-only triage is useful.

### TASK-3: Add cost/throughput section to audit script (Gap 1 partial)

New `## 5.5 Cost & Throughput` section in `_generate_report()`:
- Total input/output tokens across the run (matched spans; log-extracted fallback for
  unmatched spans with log token data).
- Per-span throughput: `output_tokens / duration_ms * 1000` → tokens/s.
- Per-flow table: calls, total input tokens, total output tokens, median throughput,
  max throughput, output efficiency ratio (`output / input`).
- Header note: dollar cost is N/A for local Ollama; throughput is the cost proxy.

### TASK-4: Add reasoning-signal section to audit script (Gap 2 partial)

New `## 5.6 Reasoning Signals` section:
- `ChatSpan` gains `output_msgs: str | None` (raw `gen_ai.output.messages` JSON string).
- Parse output_msgs for each matched span: count parts with `type == "thinking"` and sum
  content length.
- Compute: spans_with_thinking, spans_without_thinking, mean thinking-char ratio.
- Tool-call depth: for each test_id, count `tool_call` finish spans vs `stop` spans among
  the test's matched spans, expressed as average depth per flow.
- Presence-rate line + per-flow reasoning table.
- All outputs labelled: "Proxy signals — not semantic verdicts."
- Degrade: if thinking presence rate is 0%, emit warning pointing to TASK-1.

## Implementation Plan

### ✓ DONE — TASK-1 — Diagnose and restore ThinkingPart capture

```
files:
  - co_cli/config/  (model profile settings if needed)
prerequisites: none
```

Steps:
1. Check the Ollama model profile in pydantic-ai for `qwen3.5:35b-a3b-think`: confirm
   `thinking_tags` or `openai_chat_thinking_field` is set.
2. Run a minimal live agent call; inspect `ModelResponse.parts` for `ThinkingPart`.
3. If missing: apply the correct `ModelSettings` or profile override in `co_cli/config/`.
4. Verify via DB query (see `done_when`).

```
done_when: >
  After running `uv run pytest tests/test_commands.py::test_approval_approve -x`,
  run the following one-liner and confirm it prints at least one match:
    python3 - <<'EOF'
    import sqlite3, json
    db = sqlite3.connect('/Users/binle/.co-cli/co-cli-logs.db')  # or CO_CLI_HOME path
    rows = db.execute(
        "SELECT attributes FROM spans WHERE name LIKE 'chat %'"
        " AND resource LIKE '%co-cli-pytest%'"
        " AND json_extract(attributes, '$.gen_ai.agent.name') = 'agent'"
        " ORDER BY start_time DESC LIMIT 5"
    ).fetchall()
    found = [r for (r,) in rows if '"type": "thinking"' in r]
    print(f"Thinking spans: {len(found)}/{len(rows)}")
    assert found, "No thinking content in recent main-agent chat spans"
    EOF
success_signal: >
  Thinking traces reappear in chat spans for the main `agent` in the OTel DB.
  Sub-agent spans are unaffected (they have no ThinkingPart by design).
```

### ✓ DONE — TASK-2 — Extend harness detail lines + audit script parsing co-change

```
files:
  - tests/_co_harness.py
  - scripts/llm_call_audit.py
prerequisites: none
```

`tests/_co_harness.py`: in `_span_detail()`, for `chat *` span rows append:
- `in_tokens=N` if `gen_ai.usage.input_tokens` is not None
- `out_tokens=N` if `gen_ai.usage.output_tokens` is not None
- `finish=X` using first element of `gen_ai.response.finish_reasons` if present

`scripts/llm_call_audit.py` (co-change — same task/commit):
- Update `_DETAIL_PAT` to optionally capture `in_tokens=(\d+)`, `out_tokens=(\d+)`,
  `finish=(\S+)` from the tail of the detail line.
- Update `_parse_log()` to return `(test_id, duration_ms, log_input_tokens, log_output_tokens, log_finish)`.
- Update `_match_spans()`: for unmatched spans, populate `input_tokens` / `output_tokens` /
  `finish_reasons` from log-extracted values (fallback path).

```
done_when: >
  uv run pytest tests/test_commands.py::test_approval_approve -x 2>&1 \
    | grep 'pytest-harness.*chat qwen'
  produces at least one detail line containing `in_tokens=` and `out_tokens=` and `finish=`.
  AND running the audit script on the same log shows non-None token values for at least
  one previously-unmatched span (verify via the per-call table in the generated report).
success_signal: >
  Token counts and finish reason are visible in raw log output and flow into the audit
  report even when a span has no DB match.
```

### ✓ DONE — TASK-3 — Cost/throughput section in audit script

```
files:
  - scripts/llm_call_audit.py
prerequisites: none
```

Add `_cost_section()` producing `## 5.5 Cost & Throughput`. Uses `input_tokens`,
`output_tokens`, `duration_ms` from each `ChatSpan` (DB-matched or log-extracted via
TASK-2 fallback). Guards: skip per-span throughput if `output_tokens` or `duration_ms`
is None/zero.

```
done_when: >
  uv run python scripts/llm_call_audit.py \
    --log .pytest-logs/20260418-110642-full-flow-audit.log 2>&1
  and the generated report contains `## 5.5 Cost & Throughput` with a per-flow table
  that has a `Median Tokens/s` column with at least one non-zero numeric value.
success_signal: >
  Audit report includes token totals and throughput breakdown by flow.
```

### ✓ DONE — TASK-4 — Reasoning-signal section in audit script

```
files:
  - scripts/llm_call_audit.py
prerequisites: [TASK-1]
```

Add `output_msgs: str | None` field to `ChatSpan` NamedTuple, populated in
`_match_spans()` from `attrs.get("gen_ai.output.messages")`.

Add `_reasoning_section()` producing `## 5.6 Reasoning Signals`. Parses `output_msgs`
JSON per span to detect `{"type": "thinking"}` parts; computes thinking-char ratio and
tool-call depth per flow. Labels all outputs as proxy signals.

```
done_when: >
  uv run python scripts/llm_call_audit.py \
    --log .pytest-logs/20260418-110642-full-flow-audit.log 2>&1
  and the generated report contains `## 5.6 Reasoning Signals` with:
  - a "Thinking presence" rate line (0% is acceptable if TASK-1 is pending)
  - a per-flow table with a `Tool-Call Depth` column
success_signal: >
  Audit report surfaces thinking trace presence rate and tool-call depth per flow,
  labelled as proxy signals.
```

## Testing

- TASK-1: Machine-verifiable DB one-liner in `done_when` above.
- TASK-2: `uv run pytest tests/test_commands.py::test_approval_approve -x` + grep on
  stdout for `in_tokens=` in a detail line; audit script report confirms non-None token
  values on unmatched spans.
- TASK-3/4: Run the audit script against the existing log; assert new sections present
  with non-trivially empty content (at least one numeric value in each table).
- Full suite gate before ship: `scripts/quality-gate.sh full`.

## Open Questions

None — all questions resolved by source inspection during TL research.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Replaced prose `done_when` with a self-contained DB one-liner that filters to `gen_ai.agent.name = 'agent'` | TASK-1 `done_when` rewritten with `python3 -` heredoc querying the DB directly |
| CD-M-2 | adopt (option a) | Outcome 1 is worth delivering; updating the regex + parse path keeps the two consumers (human log reader, audit script) in sync | TASK-2 expanded to include `_DETAIL_PAT`/`_parse_log()` co-change; `_match_spans()` fallback path for unmatched spans added; Outcome 1 wording updated |
| CD-m-1 | adopt | Zero output tokens is a valid diagnostic value; suppressing it silently loses signal | TASK-2 guard changed to `non-None` only throughout |
| CD-m-2 | adopt | Folded into CD-M-2 resolution — TASK-2 now explicitly owns both the harness change and the audit-script regex co-change in the same task | TASK-2 `files:` updated to include `scripts/llm_call_audit.py` |
| CD-m-3 | adopt | Needed for `_reasoning_section()` to parse thinking content without a second DB hit | `ChatSpan` `output_msgs: str | None` field added to TASK-4; populated in `_match_spans()` |
| PO-m-1 | adopt | Folded into CD-M-1 resolution — DB query already filters `gen_ai.agent.name = 'agent'` | `done_when` DB one-liner uses `json_extract(attributes, '$.gen_ai.agent.name') = 'agent'` |
| PO-m-2 | adopt | "Tool-call depth" is accurate; "multi-hop count" implies reasoning quality | Renamed to "tool-call depth" in Scope, TASK-4 description, and done_when |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev llm-call-audit-gaps`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `scripts/llm_call_audit.py` | `_reasoning_section()` traversal bug: iterated flat list of part dicts but `gen_ai.output.messages` is `[{role, parts: [...]}]`. Fixed: now `for msg in messages: for part in msg.get("parts", [])`. | blocking (fixed) | TASK-4 |
| `scripts/llm_call_audit.py` | `_reasoning_section()` used `p.get("thinking", "")` but OTel `ThinkingPart` key is `content`. Fixed: `p.get("content", "")`. | blocking (fixed) | TASK-4 |
| `scripts/llm_call_audit.py` | `total_in`/`total_out` rendered as `0` instead of `—` when no spans have token data. Fixed. | minor (fixed) | TASK-3 |
| `tests/test_thinking_capture.py` | Clean. | — | TASK-1 |
| `tests/_co_harness.py` | Clean. | — | TASK-2 |

**Overall: 2 blocking (fixed) / 1 minor (fixed)**

---

## Delivery Summary — 2026-04-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_thinking_capture.py -v` — both tests pass | ✓ pass |
| TASK-2 | harness detail lines contain `in_tokens=` / `out_tokens=` / `finish=`; audit report shows non-None fallback token values | ✓ pass |
| TASK-3 | audit report contains `## 5.5 Cost & Throughput` with non-zero `Median Tokens/s` | ✓ pass |
| TASK-4 | audit report contains `## 5.6 Reasoning Signals` with presence rate + per-flow `Tool-Call Depth` column | ✓ pass |

**Tests:** full suite — 648 passed, 0 failed
**Independent Review:** 2 blocking (fixed), 1 minor (fixed)
**Doc Sync:** N/A — changes confined to `tests/` and `scripts/`, no spec-documented APIs changed

**Overall: DELIVERED**

TASK-1 revised scope per user direction: added `tests/test_thinking_capture.py` testing both reasoning paths (ThinkingPart present with reasoning settings; absent with NOREASON as control). TASK-2–4 delivered as designed. Reviewer-found traversal + field-key bugs in `_reasoning_section()` fixed before ship.
