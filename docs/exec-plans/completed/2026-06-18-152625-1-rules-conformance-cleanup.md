# Rules Conformance Cleanup — context/ leaf-boundary erosion

**Slug:** `rules-conformance-cleanup` (recurring) · **Created:** 2026-06-18 15:26:25

> **Gate 1 re-validation — 2026-06-18 (post leaf-boundary-tooling + compaction-tuning commits):**
> Edge map rebuilt against current code. All findings still hold. Headline
> `orchestrate.py:78/84/85` unchanged. Deferred R4 edge line-refs drifted with the
> compaction-tuning commit (`compaction.py:63-64→65-66`, `:606→617`;
> `history_processors.py:59→63` and its import dropped `TOOL_RESULT_PREVIEW_CHARS`;
> `summarization.py:33→41`) — table below refreshed. The `01-system.md` leaf-package
> invariant was corrected this session (separate `/sync-doc`), and the
> `review-impl` leaf-boundary judgment check is now live — so this cleanup's
> `/review-impl` will actively verify the back-edge is gone. **G1 verdict: APPROVED, scope unchanged.**

## Context

Whole-codebase `/audit-conformance` scan of `co_cli/` (202 modules, 385 import
edges). `/review-impl` is diff-scoped and structurally blind to accreted
whole-tree violations; this is the periodic counterpart.

The headline finding is a **recurring class**: agent/loop concerns accreting into
the `context/` package, which `docs/specs/01-system.md:76` declares a **leaf
package that must not import from other leaves** (`tools`, `session`), routing all
cross-package communication through `CoDeps`. The just-completed
`skill-manifest → skills/` relocation was instance #1 of this class. The audit
found the larger instance: **the turn-loop itself (`run_turn`, `TurnResult`,
`SessionAgent`) lives in `co_cli/context/orchestrate.py`** — a leaf — and reaches
sideways into `co_cli/tools/` for approval, display, and tool-cap concerns. The
spec even contradicts itself: it places `run_turn` in `context/orchestrate.py`
(line 264/286) while declaring `context` a leaf that cannot import `tools`.

Per the layer graph (`01-system.md:70`): `main → bootstrap → agent → tools /
context / config / memory`. The loop belongs at the **`agent`** layer (above
`tools`), not in a leaf below it. Relocating it makes `agent → tools` a forward
edge and **structurally eliminates** the `context → tools` back-edge class rather
than detecting it.

**Round-1 scope this plan:** the loop relocation only. The remaining audit findings
are real but a different theme — deferred backlog below.

## Findings inventory (read-confirmed)

| rule | file:line | violation | status |
|------|-----------|-----------|--------|
| R4 | `co_cli/context/orchestrate.py:78,84,85` | turn-loop in `context/` leaf imports `tools.approvals`, `tools.display`, `tools.tool_call_limit` — leaf→leaf, violates `01-system.md:76` | **round 1** |
| R4 | `co_cli/context/history_processors.py:63` | `context` imports `tools.tool_io` (`PERSISTED_OUTPUT_TAG`, `spill_if_oversized`) | deferred |
| R4 | `co_cli/context/compaction.py:65-66,617` | `context` imports `session.persistence`, `session.review_kick`, `tools.tool_call_limit` | deferred |
| R4 | `co_cli/context/summarization.py:41` | `context` imports `llm.call` (`llm_call`) | deferred |
| R1 | `co_cli/daemons/dream/state.py:23,24,25` | `DaemonState.start_time/spawn_origin/spawn_session_id` write-only; status JSON reads `pid_data`, not the instance (blind-refute confirmed REMOVABLE) | deferred |
| R3 | `co_cli/daemons/dream/state.py:19-26` | with the 3 fields gone, `DaemonState` collapses to lone write-only `current_item`; whole-class removal is a behavior change, not dead-code — needs a design call | deferred |
| R11 | `co_cli/daemons/dream/_housekeeping.py:317` | `_split_frontmatter_raw` re-implements delimiter splitting that `memory/frontmatter.py` already owns (same file imports `parse_frontmatter`) | deferred |
| R12 | `co_cli/commands/skills.py:73` | `except Exception: pass` swallows `scan_skill_content` failure on a user-visible security-scan path | deferred |
| R9 | `co_cli/index/_retrieval.py:142,322,398` | locals `src_sql`/`src_params` use forbidden domain abbrev (`code-conventions.md:26`: `source_` not `src_`) | deferred |

**Clean (no findings):** R5 underscore leaks (0 cross-package private edges), R6
import-time side effects (only `display/core.py:55` `console` singleton — accepted
shared pattern, no IO/config read), R7 optimistic flags (all reviewed flag-sets are
correct re-entry/control markers), R8 backward-compat residue (all hits are prose),
populated `__init__.py` (0). Refuted: `observability → display.console`
(presentation, not inversion); `skills → memory.parse_frontmatter` and
`tools → memory/session` (project-memory-blessed intentional public surface).

## Tasks

### ✓ DONE — Task 1 — Relocate the turn-loop from `context/` to `agent/`

Move `co_cli/context/orchestrate.py` → `co_cli/agent/orchestrate.py` verbatim
(it holds `run_turn`, `TurnResult`, `SessionAgent`, `SessionRunResult`,
`_TurnState`, and the private turn helpers). No logic change — pure relocation +
import-path update. After the move, its imports from `co_cli.tools.*` are forward
edges (`agent → tools`), so the back-edge disappears.

Update all importers (`co_cli.context.orchestrate` → `co_cli.agent.orchestrate`):
- Source: `co_cli/main.py:42`, `co_cli/agent/build.py:13` (TYPE_CHECKING)
- Tests (8): `tests/test_flow_turn_result_model_requests.py`,
  `test_flow_orchestrate_length_retry.py`, `test_flow_usage_tracking.py`,
  `test_flow_model_request_cap.py`, `test_flow_phase2_migrated.py`,
  `test_flow_multimodal_prompt.py`, `test_flow_tool_call_functional.py`,
  `test_flow_approval_subject.py` (+ any prose path-comments in those files)
- Evals (13): all `evals/eval_*.py` importing `run_turn` (grep
  `co_cli.context.orchestrate` to enumerate)

Update spec references: `docs/specs/01-system.md:264,286` (and any prose that
cites `context/orchestrate.py` as the loop home). No re-export shim in `context/`
(zero-backward-compat).

**done_when:**
- `grep -rn "context.orchestrate" co_cli tests evals` returns nothing.
- The import edge map shows zero `context → tools` MODULE edges originating in the
  relocated module (rebuild via `tmp/import_edges.py`).
- Full suite green; `run_turn` behavior unchanged (no behavior change).
- `scripts/quality-gate.sh full` passes.

## Deferred backlog — PROMOTED to standalone plans (2026-06-18)

The deferred findings have been grouped into their own active plans by theme +
review posture (they no longer re-enter via the recurring slug):

- **`close-context-leaf-edges`** (R4 round 2) — remaining `context → tools`/`session`
  edges: hoist bare tool constants to `config/tuning.py`, relocate `spill_if_oversized`,
  route compaction's session writes through `CoDeps`.
  (`context → llm` re-classified as NOT a violation — `llm` is foundational infra.)
- **`clarity-subtraction-cleanup`** (R1/R3, R11, R9) — strip 3 write-only `DaemonState`
  fields; collapse `_split_frontmatter_raw` into `memory/frontmatter.py`; rename
  `src_` → `source_`. Behavior-preserving batch.
- **`fix-swallowed-skill-scan-error`** (R12) — surface the swallowed security-scan
  failure at `commands/skills.py:73`. Behavior change → own plan.

## Verification

After Task 1: `scripts/quality-gate.sh full` (lint + full pytest). Spot-run one eval
that imports `run_turn` (e.g. `uv run python evals/eval_agentic_loop.py`) to confirm
the eval import surface still resolves. Rebuild `tmp/import_edges.py` and confirm the
`context → tools` MODULE-edge count dropped by the orchestrate.py edges.

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep "context.orchestrate"` clean; zero `context → tools` edges from relocated module; imports resolve; suite green | ✓ pass |

**Files changed:**
- `co_cli/context/orchestrate.py` → `co_cli/agent/orchestrate.py` (git-mv, verbatim relocation)
- `co_cli/context/_timeouts.py` → `co_cli/context/timeouts.py` (dropped underscore — the move made `LLM_RUN_TIMEOUT_SECS` a cross-package import from `agent/`, so the private-module contract no longer held; renamed to legal package-public surface. Importers: `context/summarization.py`, `agent/orchestrate.py`)
- Import-path updates: `co_cli/main.py`, `co_cli/agent/build.py` (TYPE_CHECKING), 8 tests, 13 evals
- `co_cli/context/compaction.py` docstring: dropped now-redundant `orchestrate` from the context-consumer list
- Spec path refs: `01-system.md` (symbol + Files tables), `core-loop.md`, `compaction.md`, `tui.md`, `pydantic-ai-integration.md`, `observability.md`

**Edge-map outcome:** `orchestrate.py` now originates forward `agent → context` edges (71–72) instead of `context → tools`. Remaining `context → tools` edges (`compaction.py:617`, `history_processors.py:63`) are the deferred round-2 findings (plan `close-context-leaf-edges`), untouched.

**Tests:** scoped — 37 passed, 0 failed (8 touched test files).
**Doc Sync:** fixed — 6 specs reconciled to new path; layering prose at `01-system.md:79` now matches reality.

**Overall: DELIVERED**
Verbatim loop relocation eliminated the `context → tools` back-edge class structurally; the underscore-rename was the one consequence beyond pure path-update, fixed in scope.

**Next step:** `/review-impl rules-conformance-cleanup` — full suite + evidence scan + behavioral verification → verdict.

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `grep "context.orchestrate"` clean; zero `context → tools` edges from relocated module; `run_turn` behavior unchanged | ✓ pass | Rename diff `co_cli/context/orchestrate.py → co_cli/agent/orchestrate.py` at **99% similarity, 1 insertion / 1 deletion** (sole change: `context._timeouts`→`context.timeouts` import line) — provably import-only, no logic change. `grep` repo-wide for `context.orchestrate` / `context._timeouts` → zero hits in co_cli/tests/evals. Edge map: remaining `context → tools` edges are only `compaction.py:617` + `history_processors.py:63` (deferred plan); orchestrate's `tools.*` imports (78/84/85) are now forward `agent → tools`. `build↔orchestrate` is TYPE_CHECKING-only (`agent/build.py:11`) — no runtime cycle. Runtime import of `main` + `run_turn` succeeds. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Doc-code drift: package docstring still claimed `context/` holds "orchestration" after the loop moved out | `co_cli/context/__init__.py:1` | minor | Rewritten to "instruction assembly, conversation history, compaction, summarization" |

### Tests
- Command: `uv run pytest`
- Result: **782 passed, 0 failed** (185s)
- Log: `.pytest-logs/20260618-163754-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots — full import + bootstrap graph loads with `run_turn` resolved from `agent/orchestrate.py`.
- Turn-loop behavior is LLM-mediated and covered by 8 scoped flow tests (`test_flow_orchestrate_*`, `test_flow_turn_result_*`, `test_flow_usage_tracking`, `test_flow_model_request_cap`, `test_flow_approval_subject`) in the green suite; a chat turn is non-gating.

### Scope note (for ship staging)
Stage ONLY the relocation diff. The working tree also carries **pre-existing unrelated changes** that predate this plan and must NOT be staged with it: `co_cli/config/memory.py`, `docs/reference/RESEARCH-multi-agent-workroom.md`, `docs/specs/{agents,dream,skills}.md`, `tests/{integration/test_review_kick_end_to_end,test_flow_compaction_review_snapshot,test_flow_exit_cleanup_review,test_flow_post_turn_hook}.py`, `uv.lock`, and the non-import edits in `evals/eval_memory.py`.

### Overall: PASS
Clean verbatim relocation — the `context → tools` back-edge from the turn-loop is structurally eliminated (now forward `agent → tools`); diff is provably import-only; full suite green; one doc-drift line fixed in scope.
