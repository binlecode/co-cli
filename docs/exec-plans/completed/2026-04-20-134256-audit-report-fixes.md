# Plan: Audit Report Fixes

**Task type: code-feature**

## Context

Six audit scripts (`llm_audit_quality.py`, `llm_audit_performance.py`, `llm_audit_tools.py`,
`llm_audit_session.py`, `llm_audit_roles.py`) and the shared `_audit_utils.py` were audited
against their generated reports. Five correctness defects and one coverage gap were found
after the `llm-audit-scripts-sharpening` delivery (v0.7.238).

**Issues found (verified against source and DB):**

1. `llm_audit_session.py:97` — `has_error` treats `turn.outcome == "continue"` as an error.
   SQL confirmed 69 production spans where `turn.outcome = 'continue'`. Result: 100% false
   error rate in session report.

2. `llm_audit_quality.py:350` — `## 5.4 Thinking Presence` is H2 but lives inside the
   `## 5. Findings` block alongside `### 5.1/5.2/5.3` subsections. Heading level
   inconsistency; `## 5.5 Semantic Evaluation` continues the numbering but at H2 level.

3. `llm_audit_quality.py` — when `--no-eval` is passed (or eval is skipped), the report is
   generated with no indication that the semantic evaluation was skipped. Reader assumes
   evaluation ran.

4. `llm_audit_session.py` `_session_depth_summary()` — does not surface discrepancy when
   `restore_spans` count + 1 mismatches `turn_spans` count; useful operational signal.

5. `co_cli/context/_tool_lifecycle.py:39` — `co.tool.result_size` only set when `info` is
   not None (native tool in `tool_index`). Delegation agent tools and internal agents
   (`_dream.py`, `_distiller.py`) have no `tool_index` entry, so 1649/1671 tool spans get
   no `result_size`. The delegation path (`_core.py:154–165`) has no
   `capabilities=[CoToolLifecycle()]`.

6. `co_cli/knowledge/_dream.py:202–209` and `_distiller.py:152–154` — dream miner and
   memory extractor agents invoked via `agent.run()` directly with no co-cli tracer span.
   `agent.role` is never set; roles report is empty for internal agents.
   Note: `_distiller.py:153` already wraps with a `co.memory.extraction` span — only needs
   a role attribute. Dream miner has no wrapper at all.

**Hygiene:** No stale exec-plans with all tasks done. No phantom features in scope.

---

## Problem & Outcome

**Problem:** Five of six defects produce silently misleading report data.

**Failure cost:**
- 100% false error rate in session report hides real health signals.
- Tool-coverage metrics are structurally 1.3% even when the system is healthy.
- Internal agent role metrics are always zero.
- Quality report heading inconsistency breaks programmatic parsing.
- Eval-skip is invisible to report readers.

**Outcome:**
- Session report error rate reflects real errors only.
- Quality report heading hierarchy is consistent (H3 subsections within H2 sections).
- Quality report notes when eval was skipped.
- Session report notes span-count discrepancies.
- `co.tool.result_size` present on all tool spans (native, delegation, internal).
- `agent.role` present on dream miner and memory extractor spans.

---

## Scope

**In:** Fix the six report defects across `scripts/` and `co_cli/`, including the
internal-agent instrumentation gap that causes the roles report to be structurally empty.
**Out:** No new audit sections, no new DB schema, no new report structure beyond the
heading fix and eval-skip notice.

---

## Behavioral Constraints

- `CoToolLifecycle.before_tool_execute` path-normalization must not change — it is already
  guarded by `call.tool_name in PATH_NORMALIZATION_TOOLS`.
- Dream miner role span must not alter `agent.run()` result or error-handling flow.
- Delegation agents must not gain any path-normalization side effects.
- `co.tool.result_size` becomes unconditional; `co.tool.source` and
  `co.tool.requires_approval` remain gated on `info` (they have no sensible default for
  delegation tools).

---

## High-Level Design

**TASK-1** (`llm_audit_session.py`): Change `outcome != "success"` to
`outcome not in ("success", "continue", None)`.

**TASK-2** (`llm_audit_quality.py`): Change `## 5.4 Thinking Presence` → `### 5.4`.
Promote `## 5.5 Semantic Evaluation` → `## 6. Semantic Evaluation` (own top-level section
with its own scoring tables). Update docstrings in `_thinking_presence_section()` and
`_eval_section()`.

**TASK-3** (`llm_audit_quality.py`): When `run_eval` is False, append
`> Semantic evaluation skipped (--no-eval).` at the location in the report where `## 6`
would appear. Requires TASK-2 so the section number is stable.

**TASK-4** (`llm_audit_session.py`): In `_session_depth_summary()`, compare
`len(restore_spans) + 1` vs `len(turn_spans)`. If they diverge by more than 1, emit a
`> Warning: span count mismatch…` line.

**TASK-5** (`co_cli/context/_tool_lifecycle.py`, `co_cli/agent/_core.py`):
- Set `co.tool.result_size` unconditionally (outside the `info` guard).
- Add `capabilities=[CoToolLifecycle()]` to the delegation agent branch in `_core.py`.
- No change to `before_tool_execute` (path-normalization guard is already name-based).

**TASK-6** (`co_cli/knowledge/_dream.py`, `co_cli/knowledge/_distiller.py`):
- Dream miner: wrap `miner_agent.run(chunk, ...)` in a
  `_TRACER.start_as_current_span("invoke_agent _dream_miner_agent")` block that sets
  `span.set_attribute("agent.role", "dream_miner")`.
- Memory extractor: change `with tracer.start_as_current_span("co.memory.extraction"):` to
  `with tracer.start_as_current_span("co.memory.extraction") as span:` (add `as span`),
  then add `span.set_attribute("agent.role", "memory_extractor")` inside the block.
- Reuse existing `tracer` variable in `_distiller.py:145`; add `_TRACER` in `_dream.py`.

---

## Implementation Plan

### ✓ DONE — TASK-1: Fix has_error false-positive for turn.outcome="continue"

**files:**
- `scripts/llm_audit_session.py`

**done_when:**
`grep -n "outcome not in" scripts/llm_audit_session.py` returns a line containing both
`"success"` and `"continue"`, AND:
```
python -c "
import sys; sys.path.insert(0, 'scripts')
from llm_audit_session import _extract_session_attrs
_, _, _, _, _, has_error = _extract_session_attrs({'turn.outcome': 'continue'})
assert not has_error, f'continue outcome incorrectly flagged as error'
print('OK')
"
```
exits 0.

**success_signal:** Session report shows realistic (non-100%) error rate in production runs.

---

### ✓ DONE — TASK-2: Fix heading level inconsistency in quality report

**files:**
- `scripts/llm_audit_quality.py`

**done_when:**
`grep -n "^## 5\." scripts/llm_audit_quality.py` returns only one line (`## 5. Findings`),
`grep -n "^### 5\.4" scripts/llm_audit_quality.py` returns `### 5.4 Thinking Presence`,
and `grep -n "^## 6\." scripts/llm_audit_quality.py` returns `## 6. Semantic Evaluation`.

**success_signal:** Generated quality report has consistent heading hierarchy.

---

### ✓ DONE — TASK-3: Add eval-skipped notice in quality report when --no-eval

**files:**
- `scripts/llm_audit_quality.py`

**done_when:**
`uv run python scripts/llm_audit_quality.py --log $(ls -t .pytest-logs/*.log | head -1) --no-eval`
exits 0 and the generated report file contains the text `Semantic evaluation skipped`.

**success_signal:** Reports generated with `--no-eval` clearly state the section was omitted.

**prerequisites:** [TASK-2]

---

### ✓ DONE — TASK-4: Add span-count discrepancy warning in session depth summary

**files:**
- `scripts/llm_audit_session.py`

**done_when:**
```
python -c "
import sys; sys.path.insert(0, 'scripts')
from llm_audit_session import _session_depth_summary, SessionSpan
# 2 restore spans but only 1 session has turns — mismatched
spans = [
    SessionSpan('restore_session', 1.0, None, None, None, False, False, None, 1),
    SessionSpan('restore_session', 1.0, None, None, None, False, False, None, 2),
    SessionSpan('co.turn', 1.0, None, None, 'continue', False, False, None, 3),
]
result = _session_depth_summary(spans)
assert 'Warning' in result, f'Expected warning in: {result!r}'
print('OK')
"
```
exits 0.

**success_signal:** Session report flags orphaned or missing spans in the depth summary.

**prerequisites:** [TASK-1]

---

### ✓ DONE — TASK-5: Fix CoToolLifecycle result_size gap + add to delegation agent

**files:**
- `co_cli/context/_tool_lifecycle.py`
- `co_cli/agent/_core.py`

**done_when:**
`uv run pytest tests/test_tool_calling_functional.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task5.log`
passes, AND `grep -n "result_size" co_cli/context/_tool_lifecycle.py` shows the attribute
set outside the `if info:` guard, AND `grep -c "CoToolLifecycle" co_cli/agent/_core.py`
returns `3`.

**success_signal:** Tool spans in the audit DB include `co.tool.result_size` for delegation
agent tool calls (e.g. `save_knowledge`).

---

### ✓ DONE — TASK-6: Add agent.role spans to dream miner and memory extractor

**files:**
- `co_cli/knowledge/_dream.py`
- `co_cli/knowledge/_distiller.py`

**done_when:**
`uv run pytest tests/test_llm_thinking.py tests/test_tool_calling_functional.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task6.log`
passes, AND `grep -n "agent.role" co_cli/knowledge/_dream.py co_cli/knowledge/_distiller.py`
returns matches in both files.

**success_signal:** After a dream cycle, roles report shows `dream_miner` and
`memory_extractor` entries instead of empty tables.

---

## Testing

Targeted pytest `done_when` criteria in each task. Full suite run covers integration.
No new test files required — existing functional tests cover the tool-calling and thinking
paths affected by TASK-5 and TASK-6.

---

## Open Questions

None — all questions answered by source inspection before drafting.

## Final — Team Lead

Plan approved. All six issues addressed: three `done_when` criteria strengthened to behavioral
checks, TASK-5 count corrected to 3, TASK-6 `as span` gap documented, scope expanded to
cover the instrumentation gap, and TASK-6 prerequisite removed (independent of TASK-5).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev audit-report-fixes`

---

## Delivery Summary — 2026-04-20

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `outcome not in ("success", "continue", None)` grep passes + behavioral assert exits 0 | ✓ pass |
| TASK-2 | `^## 5\.` = 1 line, `^### 5\.4` present, `^## 6\.` present | ✓ pass |
| TASK-3 | `--no-eval` exits 0 and report contains `Semantic evaluation skipped` | ✓ pass |
| TASK-4 | `_session_depth_summary` with 2 restore/1 turn returns string containing `Warning` | ✓ pass |
| TASK-5 | `test_tool_calling_functional.py` 6 passed; `result_size` outside `if info:`; `CoToolLifecycle` count = 3 | ✓ pass |
| TASK-6 | `test_llm_thinking.py` + `test_tool_calling_functional.py` 8 passed; `agent.role` in both files | ✓ pass |

**Tests:** full suite — 546 passed, 0 failed
**Independent Review:** clean / 0 blocking / 0 minor
**Doc Sync:** fixed (`observability.md` and `tools.md` — `co.tool.result_size` noted as unconditional; `source`/`requires_approval` native-only)

**Overall: DELIVERED**
All six audit report defects resolved: session false-error rate fixed, quality heading hierarchy consistent, eval-skip visible, span-count mismatch warned, tool result_size covers delegation spans, dream miner and memory extractor now emit `agent.role` attributes.

---

## Implementation Review — 2026-04-20

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `outcome not in ("success", "continue", None)` + behavioral assert | ✓ pass | `llm_audit_session.py:97` — tuple check confirmed; `continue`, `None`, `success` all pass; `error` flagged |
| TASK-2 | `^## 5\.` = 1, `^### 5\.4` present, `^## 6\.` present | ✓ pass | `llm_audit_quality.py:350` `### 5.4`; `:451` `## 6. Semantic Evaluation`; `:459` `### 6.1`; `:463` `### 6.2` |
| TASK-3 | `--no-eval` exits 0, report contains `Semantic evaluation skipped` | ✓ pass | `llm_audit_quality.py:744-745` — `else:` branch appends notice; report verified at `docs/REPORT-llm-audit-eval-20260420-145414.md` |
| TASK-4 | `_session_depth_summary` with 2 restore / 1 turn emits `Warning` | ✓ pass | `llm_audit_session.py:161-184` — upfront counts, `abs(expected_sessions - actual_turns) > 1` guard |
| TASK-5 | tests pass; `result_size` outside `if info:`; `CoToolLifecycle` count = 3 | ✓ pass | `_tool_lifecycle.py:39-43` — `result_size` inside `if span.is_recording():`, `source`/`requires_approval` inside nested `if info:`; `_core.py:162` delegation path gains `capabilities=[CoToolLifecycle()]` |
| TASK-6 | tests pass; `agent.role` in both files | ✓ pass | `_dream.py:206-209` — `_TRACER.start_as_current_span` wrapping `miner_agent.run()` with `agent.role = "dream_miner"`; `_distiller.py:153-154` — `as span` + `agent.role = "memory_extractor"` |

### Issues Found & Fixed

No issues found.

### Tests

- Command: `uv run pytest -v`
- Result: 546 passed, 0 failed
- Log: `.pytest-logs/20260420-145420-review-impl.log`

### Doc Sync

- Scope: narrow — all tasks confined to scripts/ and co_cli/knowledge,context,agent modules; no public API renamed
- Result: fixed — `observability.md` and `tools.md` corrected to distinguish `result_size` (unconditional, all spans) from `source`/`requires_approval` (native-tool-only); already applied during orchestrate-dev

### Behavioral Verification

- `uv run co config`: healthy — LLM online, Shell active, Google configured, MCP ready, DB active
- Script changes (TASK-1–4) are standalone audit scripts, not user-facing CLI — no chat interaction required
- `co_cli` changes (TASK-5–6) affect OTel span attributes at tool-execute time; structural changes confirmed by grep evidence + 546-test green suite
- `success_signal` TASK-3: verified — `--no-eval` report contains `## 6. Semantic Evaluation\n\n> Semantic evaluation skipped (--no-eval).`

### Overall: PASS

All six defects fixed with minimal, targeted changes; 546 tests green; no blocking findings; doc sync complete; system boots healthy.

