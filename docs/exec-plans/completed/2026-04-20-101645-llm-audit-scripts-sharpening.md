# Plan: Build co-cli LLM Audit Suite — Five Scripts + Shared Utils

**Task type:** refactor + code-feature

## Context

Two audit scripts exist after renaming:

- `scripts/llm_audit_quality.py` — correctness and semantic quality of LLM outputs (pytest runs)
- `scripts/llm_audit_performance.py` — runtime latency, throughput, token usage (production spans)

A cross-review and best-practice survey identified three problems and one gap:

**Problem 1 — Quality script contaminated with runtime content.**  
`llm_audit_quality.py` contains §5.4 Latency Hotspots and §5.5 Cost & Throughput — runtime
metrics with no correctness signal. These must be migrated to the performance script, not dropped.
Migration requires shared log-parsing logic since flow labels come from pytest test node IDs.

**Problem 2 — Quality script buries its primary value.**  
Semantic eval (§5.7, LLM-as-judge) is opt-in via `--eval`; it should run by default. The
`_reasoning_section` mixes structural signals with the semantic concern. `_verdict()` returns
`"OK, minimal"` for content-producing flows that returned ≤3 tokens — a false negative.

**Problem 3 — No home for non-quality operational concerns.**  
Neither existing script covers tool health (error rates, latency, result size), session health
(provider errors, context pressure, turn depth), or subagent role efficiency. All three are
queryable from existing OTel spans without new instrumentation.

**Decision — Five-script audit suite + one shared module:**

| Script | Source | Scope |
|---|---|---|
| `llm_audit_quality.py` | pytest log + OTel DB | Correctness, reasoning, semantic eval |
| `llm_audit_performance.py` | OTel DB + optional log | Latency, throughput, tokens, per-flow |
| `llm_audit_tools.py` | OTel DB | Tool error rates, latency, result size, backend |
| `llm_audit_session.py` | OTel DB | Provider errors, context pressure, session depth |
| `llm_audit_roles.py` | OTel DB | Subagent role usage, saturation, per-role cost |
| `_audit_utils.py` | — | Shared log-parsing and log+DB correlation helpers |

## Problem & Outcome

**Problem:** Quality script mixes runtime and correctness concerns, buries semantic eval, and
three operational audit dimensions (tools, sessions, roles) have no script home.

**Failure cost:** Semantic eval regressions go undetected in routine runs; tool error spikes,
provider reliability issues, and subagent saturation have no dedicated investigation path.

**Outcome:** After this delivery, each audit dimension has a focused, independently runnable
script. Quality script is correctness-only with eval on by default. Performance script gains
per-flow sections from test runs. Three new scripts cover tool health, session health, and
role efficiency from production spans.

## Scope

**In scope:**
- Extract log-parsing and log+DB correlation helpers to `scripts/_audit_utils.py`
- Add `--log` flag + per-flow sections to `llm_audit_performance.py`
- Remove §5.4/5.5 from `llm_audit_quality.py` (after migration)
- Slim `_reasoning_section` → `_thinking_presence_section` in quality script
- Flip `--eval` → `--no-eval` in quality script
- Fix `_verdict()` in quality script; split `warn_spans` into `length_warns` + `minimal_warns`
- Create `scripts/llm_audit_tools.py`
- Create `scripts/llm_audit_session.py`
- Create `scripts/llm_audit_roles.py`

**Out of scope:**
- Migrating `_fmt`, `_dur_s` to shared module — trivial one-liners; local copies are fine
- Changes to OTel schema, DB query logic, or judge rubric
- New judge dimensions
- New OTel instrumentation (compaction fidelity, cache hit rate) — deferred; not yet in DB

## Behavioral Constraints

- `llm_audit_performance.py` without `--log` must produce output identical in structure to today
- `llm_audit_performance.py --log <path>` appends per-flow sections only when log has parseable spans
- `llm_audit_quality.py --no-eval` must produce a complete report without any Ollama calls
- `llm_audit_quality.py` with no flags must invoke `_judge_all_spans` and append §5.5 Semantic Evaluation
- `--eval-only` flag behavior in quality script is unchanged
- `_verdict()` must never downgrade a span already returning `"WARN: length"`
- Each new script (`tools`, `session`, `roles`) must produce output when the DB has no spans
  in range — emit a "no data" notice rather than raising an exception
- All new scripts follow the same CLI pattern: `--db`, `--since`, `--until`, `--out`

## High-Level Design

### `_audit_utils.py` — Shared helpers

Move from `llm_audit_quality.py`:

*Log parsing:*  
Regex constants (`_SUMMARY_PAT`, `_DETAIL_PAT`, `_DETAIL_IN_PAT`, `_DETAIL_OUT_PAT`,
`_DETAIL_FINISH_PAT`, `_SESSION_PAT`), `_LogSpan` type alias, `_parse_kv`, `_parse_log`,
`_infer_flow`.

*Log+DB correlation:*  
Constants `_DURATION_TOLERANCE_MS`, `_WINDOW_BUFFER_S`; `FlowChatSpan` NamedTuple (renamed
from quality's `ChatSpan`; same fields); `_build_api`, `_query_db_spans`, `_match_spans`.

Quality script imports all of the above; replace local `ChatSpan` with `FlowChatSpan`.
Performance script's own `ChatSpan` (production variant with `name`, `has_thinking`,
`start_time_ns`) stays local.

### `llm_audit_performance.py` — Per-flow extension

When `--log` is provided: `_parse_log` → `_query_db_spans` → `_match_spans` → `list[FlowChatSpan]`.  
New functions `_perf_flow_latency_section` and `_perf_flow_cost_section` append §8 and §9
after the existing §7 Tool Execution Profile. No renumbering of existing sections.

### `llm_audit_quality.py` — Four sharpening changes

1. Remove `_cost_section` and §5.4 latency block; renumber retained sections.
2. `_reasoning_section` → `_thinking_presence_section`; drop per-flow table.
3. `--eval` → `--no-eval`; `run_eval = not args.no_eval`.
4. `_CONTENT_FLOWS`; `_verdict()` returns `"WARN: minimal"` for content flows with ≤3-token stop;
   `warn_spans` split into `length_warns` + `minimal_warns`.

### `llm_audit_tools.py` — Tool execution health

Queries production `execute_tool *` spans plus `co.tool.*` and `rag.backend` attributes.

Sections:
- **§1 Scope** — time range, total tool calls, distinct tools
- **§2 Error Rate by Tool** — `status_code=ERROR` count and rate per tool name
- **§3 Latency by Tool** — p50/p95/max per tool (top 15 by call count)
- **§4 Result Size Distribution** — `co.tool.result_size` percentiles overall and per tool
- **§5 Approval & Source Profile** — requires_approval rate; MCP vs native split
- **§6 RAG Backend Distribution** — fts5 / hybrid / grep call counts (spans with `rag.backend`)

Output: `docs/REPORT-llm-audit-tools-YYYYMMDD-HHMMSS.md`

### `llm_audit_session.py` — Session and context health

Queries `co.turn` spans (attrs: `turn.input_tokens`, `turn.output_tokens`, `turn.outcome`,
`turn.interrupted`; events: `provider_error` with `http.status_code`) and orchestration spans
(`ctx_overflow_check`, `restore_session`).

Sections:
- **§1 Scope** — time range, total turns, total sessions (restore_session count)
- **§2 Provider Reliability** — 429 rate, 5xx rate, error body distribution from `provider_error` events
- **§3 Context Pressure** — `ctx_overflow_check` count and rate per session; overflow frequency
- **§4 Session Depth** — turns per session distribution (p50/p95/max)
- **§5 Token Accumulation** — per-turn input/output token distribution; cumulative trend

Output: `docs/REPORT-llm-audit-session-YYYYMMDD-HHMMSS.md`

### `llm_audit_roles.py` — Subagent and role efficiency

Queries spans with `agent.role`, `agent.model`, `agent.requests_used`, `agent.request_limit`
attributes (role delegation spans emitted by subagent invocations).

Sections:
- **§1 Scope** — time range, total role invocations, distinct roles
- **§2 Role Usage** — invocation count and share per role
- **§3 Request Limit Saturation** — `requests_used / request_limit` ratio distribution per role;
  flag roles where p95 saturation > 0.8
- **§4 Per-Role Latency** — p50/p95/max per role
- **§5 Per-Role Token Cost** — input/output tokens per role; output/input efficiency ratio

Output: `docs/REPORT-llm-audit-roles-YYYYMMDD-HHMMSS.md`

## Implementation Plan

### ✓ DONE — TASK-1 — Extract shared helpers to `scripts/_audit_utils.py`

**files:**
- `scripts/_audit_utils.py` (new)
- `scripts/llm_audit_quality.py`

**done_when:**
```bash
python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('_audit_utils', 'scripts/_audit_utils.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for name in ('_parse_log', '_infer_flow', '_parse_kv', '_query_db_spans',
             '_match_spans', '_build_api', 'FlowChatSpan'):
    assert hasattr(m, name), f'missing {name}'
print('PASS')
"
grep "from _audit_utils import" scripts/llm_audit_quality.py
grep -c "def _parse_log\|def _infer_flow\|def _parse_kv\|def _query_db_spans\|def _match_spans" \
  scripts/llm_audit_quality.py
```
Must show: all seven names in `_audit_utils`; quality script imports from it; zero remaining
local definitions of the moved functions.

**success_signal:** N/A

---

### ✓ DONE — TASK-2 — Add `--log` flag + per-flow sections to `llm_audit_performance.py`

**files:**
- `scripts/llm_audit_performance.py`

**done_when:**
```bash
uv run python scripts/llm_audit_performance.py --help | grep "\-\-log"
grep -n "_perf_flow_latency_section\|_perf_flow_cost_section" scripts/llm_audit_performance.py
grep "from _audit_utils import" scripts/llm_audit_performance.py
uv run python scripts/llm_audit_performance.py --log .pytest-logs/<any-log>
grep "## 8. Per-Flow Latency\|## 9. Per-Flow Cost" docs/REPORT-llm-audit-performance-*.md | tail -2
```

**success_signal:** `uv run python scripts/llm_audit_performance.py --log <log>` produces a
report with §8 Per-Flow Latency and §9 Per-Flow Cost after §7, with token data from DB-matched
spans.

**prerequisites:** [TASK-1]

---

### ✓ DONE — TASK-3 — Remove §5.4 and §5.5 from `llm_audit_quality.py`

**files:**
- `scripts/llm_audit_quality.py`

**done_when:**
```bash
python -c "
import ast, pathlib
src = pathlib.Path('scripts/llm_audit_quality.py').read_text()
tree = ast.parse(src)
names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
assert '_cost_section' not in names, '_cost_section still present'
assert 'latency_lines' not in src, 'latency_lines still present'
print('PASS')
"
```

**success_signal:** N/A

**prerequisites:** [TASK-2]

---

### ✓ DONE — TASK-4 — Slim `_reasoning_section` → `_thinking_presence_section`

**files:**
- `scripts/llm_audit_quality.py`

**done_when:**
```bash
grep -n "_reasoning_section\|_thinking_presence_section\|Tool-Call Depth" \
  scripts/llm_audit_quality.py
```
Must show: `_thinking_presence_section` defined and called; no `_reasoning_section`,
no `Tool-Call Depth` heading, no `flow_rows` in the function body.

**success_signal:** N/A

---

### ✓ DONE — TASK-5 — Flip eval to default-on; add `--no-eval` flag

**files:**
- `scripts/llm_audit_quality.py`

**done_when:**
```bash
uv run python scripts/llm_audit_quality.py --help | grep -E "no-eval|no_eval"
uv run python scripts/llm_audit_quality.py --help | grep -iE "ollama|judge"
grep -n "no_eval\|run_eval" scripts/llm_audit_quality.py
uv run python scripts/llm_audit_quality.py --no-eval --log .pytest-logs/<any-log>
grep "## 5.4 Thinking Presence" docs/REPORT-llm-audit-eval-*.md | tail -1
grep -L "5.5 Semantic Evaluation" docs/REPORT-llm-audit-eval-*.md | tail -1
```

**success_signal:** `uv run python scripts/llm_audit_quality.py --no-eval --log <log>` produces
a report with §5.4 Thinking Presence and without §5.5 Semantic Evaluation.

**prerequisites:** [TASK-3, TASK-4]

---

### ✓ DONE — TASK-6 — Flow-aware `_verdict()`; split warn lists

**files:**
- `scripts/llm_audit_quality.py`

**done_when:**
```bash
grep -n "_CONTENT_FLOWS\|WARN: minimal\|length_warns\|minimal_warns" \
  scripts/llm_audit_quality.py
```
Must show: `_CONTENT_FLOWS` at module level; `"WARN: minimal"` branch in `_verdict()`;
`length_warns` and `minimal_warns` replacing `warn_spans`; Executive Summary and
`_cut_finding` updated to distinguish the two categories.

**success_signal:** N/A

---

### ✓ DONE — TASK-7 — Create `scripts/llm_audit_tools.py`

**files:**
- `scripts/llm_audit_tools.py` (new)

**done_when:**
```bash
uv run python scripts/llm_audit_tools.py --help
uv run python scripts/llm_audit_tools.py
grep "## 2. Error Rate\|## 3. Latency\|## 4. Result Size\|## 5. Approval\|## 6. RAG" \
  docs/REPORT-llm-audit-tools-*.md | tail -5
```
Must show: all six section headings in the output report.

**success_signal:** `uv run python scripts/llm_audit_tools.py` produces
`docs/REPORT-llm-audit-tools-YYYYMMDD-HHMMSS.md` with error rates and latency per tool.

---

### ✓ DONE — TASK-8 — Create `scripts/llm_audit_session.py`

**files:**
- `scripts/llm_audit_session.py` (new)

**done_when:**
```bash
uv run python scripts/llm_audit_session.py --help
uv run python scripts/llm_audit_session.py
grep "## 2. Provider Reliability\|## 3. Context Pressure\|## 4. Session Depth\|## 5. Token" \
  docs/REPORT-llm-audit-session-*.md | tail -4
```
Must show: all five section headings in the output report; no uncaught exception when DB has
zero `co.turn` spans for the given range.

**success_signal:** `uv run python scripts/llm_audit_session.py` produces
`docs/REPORT-llm-audit-session-YYYYMMDD-HHMMSS.md` with provider error counts and
ctx_overflow_check frequency.

---

### ✓ DONE — TASK-9 — Create `scripts/llm_audit_roles.py`

**files:**
- `scripts/llm_audit_roles.py` (new)

**done_when:**
```bash
uv run python scripts/llm_audit_roles.py --help
uv run python scripts/llm_audit_roles.py
grep "## 2. Role Usage\|## 3. Request Limit\|## 4. Per-Role Latency\|## 5. Per-Role Token" \
  docs/REPORT-llm-audit-roles-*.md | tail -4
```
Must show: all five section headings in the output report; no uncaught exception when no role
spans exist for the given range.

**success_signal:** `uv run python scripts/llm_audit_roles.py` produces
`docs/REPORT-llm-audit-roles-YYYYMMDD-HHMMSS.md` with per-role saturation and latency data.

---

## Testing

Standalone scripts — no pytest suite warranted. Full functional check after all tasks complete:

```bash
# Quality script — no eval (no Ollama required)
uv run python scripts/llm_audit_quality.py --no-eval --log .pytest-logs/<log>
grep "## 5.4 Thinking Presence" docs/REPORT-llm-audit-eval-*.md | tail -1
grep -L "Latency Hotspots\|Cost & Throughput" docs/REPORT-llm-audit-eval-*.md | tail -1

# Performance script — with log
uv run python scripts/llm_audit_performance.py --log .pytest-logs/<log>
grep "## 8. Per-Flow Latency\|## 9. Per-Flow Cost" docs/REPORT-llm-audit-performance-*.md | tail -2

# Performance script — without log (regression: §1–7 unchanged, no Per-Flow sections)
uv run python scripts/llm_audit_performance.py
grep -L "Per-Flow" docs/REPORT-llm-audit-performance-*.md | tail -1

# Tools audit
uv run python scripts/llm_audit_tools.py
grep "## 2. Error Rate" docs/REPORT-llm-audit-tools-*.md | tail -1

# Session audit
uv run python scripts/llm_audit_session.py
grep "## 3. Context Pressure" docs/REPORT-llm-audit-session-*.md | tail -1

# Roles audit
uv run python scripts/llm_audit_roles.py
grep "## 3. Request Limit" docs/REPORT-llm-audit-roles-*.md | tail -1

# Optional (requires Ollama with judge model)
uv run python scripts/llm_audit_quality.py --log .pytest-logs/<log>
grep "## 5.5 Semantic Evaluation" docs/REPORT-llm-audit-eval-*.md | tail -1
```

## Open Questions

None — all design decisions are resolvable from source inspection.

---
## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev llm-audit-scripts-sharpening`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `llm_audit_quality.py` | Section numbering gap: after removing old §5.4/§5.5, `_thinking_presence_section` was renumbered to §5.4 correctly, but `_eval_section` was left at `## 5.7 Semantic Evaluation` — skipping §5.5 and §5.6. The plan spec says "§5.5 Semantic Evaluation" in three places (Behavioral Constraints line 77, TASK-5 done_when `grep "## 5.5 Semantic Evaluation"`, and Testing block `grep "## 5.5 Semantic Evaluation"`). All three verification commands would fail against the current output. | blocking | TASK-5 |
| `llm_audit_roles.py` | `total_out is not None` (line 297) is always `True` because `total_out = sum(out_vals)` is always `int`. When `out_vals` is empty, `total_out == 0` and `total_in > 0`, so efficiency renders as `"0.000"` instead of `"—"`. Fix: `if total_in > 0 and total_out > 0:`. | minor | TASK-9 |
| `llm_audit_session.py`, `llm_audit_roles.py` | `# type: ignore[arg-type]` at lines 69 and 68 respectively have no justification comment. CLAUDE.md policy: "Never add `# type: ignore` without a comment explaining why the tool is wrong for that line." Fix: append `# int() accepts any numeric-like object; mypy flags object as non-numeric`. | minor | TASK-8, TASK-9 |
| `llm_audit_session.py` | When DB has zero spans for the given range, `session_count = len(restore_spans) + 1 = 1`, so §1 reports "1 session" when there are none. Not a crash, but a misleading "1 session (restore_session + 1)" when there is no data at all. | minor | TASK-8 |

**Overall: 1 blocking / 3 minor**

- **Blocking (TASK-5):** The `_eval_section` heading `## 5.7 Semantic Evaluation` must be renumbered to `## 5.5 Semantic Evaluation` to match the plan spec and pass the plan's own verification grep. The `_thinking_presence_section` docstring says `## 5.4 Thinking Presence` — that is correct and stays.
- **Minor (TASK-9, roles):** `total_out is not None` → `total_out > 0`.
- **Minor (TASK-8/9):** Both `_parse_optional_int` functions need a `# why mypy is wrong` suffix on the `# type: ignore` line.
- **Minor (TASK-8, session):** Guard `session_count` display: if `len(spans) == 0`, show `0` sessions rather than the formula default of `1`.

---

## Delivery Summary — 2026-04-20

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | All 7 names in `_audit_utils`; quality script imports from it; 0 local defs of moved functions | ✓ pass |
| TASK-2 | `--log` flag present; `_perf_flow_latency_section` + `_perf_flow_cost_section` defined; report contains §8 + §9 | ✓ pass |
| TASK-3 | `_cost_section` absent from AST; `latency_lines` absent from source | ✓ pass |
| TASK-4 | `_thinking_presence_section` defined + called; no `_reasoning_section`, no `Tool-Call Depth`, no `flow_rows` | ✓ pass |
| TASK-5 | `--no-eval` flag present; `no_eval` / `run_eval` in source; report without eval omits §5.5; report with §5.4 | ✓ pass |
| TASK-6 | `_CONTENT_FLOWS` at module level; `"WARN: minimal"` in `_verdict()`; `length_warns` + `minimal_warns` in source | ✓ pass |
| TASK-7 | `llm_audit_tools.py` runnable; report contains §2–§6 headings | ✓ pass |
| TASK-8 | `llm_audit_session.py` runnable; report contains §2–§5 headings; no crash on empty DB range | ✓ pass |
| TASK-9 | `llm_audit_roles.py` runnable; report contains §2–§5 headings; no crash on empty DB range | ✓ pass |

**Tests:** full suite — 546 collected, all passed (`.pytest-logs/20260420-132009-gate.log`)
**Independent Review:** 1 blocking + 3 minor → all resolved → clean
**Doc Sync:** clean — no `docs/specs/` paths touched; no spec update required

**Overall: DELIVERED**
Five-script audit suite complete: quality/performance scripts sharpened; three new OTel-backed scripts (`tools`, `session`, `roles`) added; shared `_audit_utils.py` module extracted.

---

## Implementation Review — 2026-04-20

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | 7 names in `_audit_utils`; quality imports from it; 0 local defs | ✓ pass | `_audit_utils.py` exports all 7 names confirmed by live import check; `llm_audit_quality.py:24-30` imports from it; grep returns 0 local function defs |
| TASK-2 | `--log` flag; both section functions defined; §8+§9 in report | ✓ pass | `llm_audit_performance.py:475` `--log PATH` arg; functions at `:392` and `:415`; called at `:527-528` in `main()` |
| TASK-3 | `_cost_section` absent from AST; `latency_lines` absent | ✓ pass | AST walk of quality script confirms no `_cost_section` FunctionDef; `latency_lines` not in source |
| TASK-4 | `_thinking_presence_section` defined+called; no `_reasoning_section` | ✓ pass | `llm_audit_quality.py:301` defines it; `:621` calls it; grep confirms no `_reasoning_section` or `Tool-Call Depth` |
| TASK-5 | `--no-eval` flag; `run_eval = not args.no_eval`; §5.4/§5.5 headings | ✓ pass | `llm_audit_quality.py:642-644` `--no-eval` arg; `:725` `run_eval = not args.no_eval`; `## 5.4 Thinking Presence` at `:349`; `## 5.5 Semantic Evaluation` at `:450` |
| TASK-6 | `_CONTENT_FLOWS` at module level; `"WARN: minimal"` in `_verdict()`; split warn lists | ✓ pass | `llm_audit_quality.py:45-58` `_CONTENT_FLOWS` frozenset; `:236` `return "WARN: minimal"`; `:488-489` `length_warns`/`minimal_warns` split |
| TASK-7 | All 6 section headings (§1–§6) in live report | ✓ pass | Live run: 1671 spans processed; `REPORT-llm-audit-tools-20260420-132524.md` contains §2–§6 |
| TASK-8 | All 5 section headings (§1–§5) in live report; no crash on empty | ✓ pass | Live run: 69 co.turn, 29 restore_session, 64 ctx_overflow_check; `REPORT-llm-audit-session-20260420-132527.md` contains §2–§5 |
| TASK-9 | All 5 section headings (§1–§5); no crash with 0 role spans | ✓ pass | Live run: 0 role delegation spans → no crash; `REPORT-llm-audit-roles-20260420-132530.md` contains §2–§5 |

### Issues Found & Fixed

No issues found. All blocking findings from Independent Review were resolved before this review pass. All done_when criteria pass on re-execution.

### Tests
- Command: `scripts/quality-gate.sh full`
- Result: 546 passed, 0 failed
- Log: `.pytest-logs/20260420-132009-final.log` (exit code 0)

### Doc Sync
- Scope: narrow — all tasks confined to `scripts/` standalone audit scripts; no `co_cli/` package changes, no public API changes, no schema changes
- Result: clean — no spec updates required

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM Online, Shell Active, DB Active, MCP ready
- Scripts: all 5 audit scripts run without error against live DB; section headings and "no data" paths all verified
- No user-facing co_cli changes — REPL and tool behavior unchanged

### Overall: PASS
All 9 tasks deliver exactly their spec, done_when criteria re-verified on cold read, lint clean, 546 tests green, no blocking or minor findings remain.
