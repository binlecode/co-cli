# Plan: rules-conformance-cleanup

## Context

Periodic whole-codebase conformance audit (`/audit-conformance`, scope `co_cli/`, ~28K LOC) run 2026-06-22 with explicit strictness against bloated/over-engineered top-level modularity, boundaries, visibility, and surface shape.

**Headline: the structural dimensions the run targeted are clean.** The AST import-graph (969 edges) shows **zero R4 MODULE-scope layer back-edges** and **zero R5 cross-package private-name leaks** (all 98 private imports are same-package, legal). No populated `__init__.py`. The over-design fan-out (Agent A) found no wrapper-bag classes, no single-impl speculative abstractions, no mis-drawn or over-fragmented package boundaries — small packages (`proc`=57, `fileio`=190, `personality`=183) each have multiple callers and a coherent concern; large files (`orchestrate.py`=1076, `main.py`=842, `display/core.py`=756) bundle coherent scope. Lifecycle/errors (R6/R7/R12) and backward-compat (R8) are clean. This is the residue of three `rules-conformance-cleanup` rounds completed 2026-06-16/17 — the tree is in strong shape and this run confirms it.

What remains is a small batch of **naming drift (R9), one dead function (R10), and duplication (R11)** accreted since 2026-06-17. All four tasks are behavior-preserving.

## Recurrence note

- **R9 unit-suffix drift recurs.** Commit `4e2864d7` ("add `_seconds` unit suffix to timeout/duration identifiers (R9)") fixed the duration sub-class but did not touch char-count constants. The `_CHARS` suffix is already the live convention (`code-conventions.md:36`; used at `main.py:497,509,549`, `skills/lint.py:23`), so the two un-suffixed char-count clusters below are the same recurring class, missed in that pass. Draining them now keeps the class from re-accreting.
- **R11 truncate idiom recurs across 4 packages and has already drifted** — the canonical failure mode of duplication (`feedback_clarity_by_subtraction`): the copies disagree on the ellipsis glyph and reserve width (`_tool_result_markers.py:51` uses ascii `"..."` / `max_len-3`; `queue_control.py:23` and `main.py:506,518,557` use unicode `"…"` / `budget-1`). Five reimplementations of a presentation primitive is past the R11 threshold.
- **Template-defect recurrence (already resolved — recorded so the next audit does not re-open it):** the L2 completed-plan grep surfaced ~10 historical `co status` "N/A — no such command" disclaimers across plans (2026-05-28 → 2026-06-13). The defect was a phantom verification step in a skill template; `review-impl/SKILL.md` now carries the clarifying note ("Health checks live behind the `/status` slash command inside `co chat`, not a non-interactive subcommand"). No active skill template still instructs `co status`. No task needed.
- **Already-deferred (do not re-list):** the prior plan (`2026-06-17-150206`) recorded the souls/canon `.md` bare YAML `created:`/`updated:` keys as inert stored-data drift (no Python reads them), not a code R9 finding. This audit confirms — left deferred.

## Scope (this round)

One coherent theme: **drain post-2026-06-17 conformance residue** — one dead deletion (TASK-1), one naming-suffix sweep (TASK-2), one durability/DRY fix (TASK-3), one in-place glyph-drift fix (TASK-4). All behavior-preserving; full suite green expected with no behavior change. No deferred backlog (the only outstanding item is the canon YAML note already tracked above).

**Gate-1 outcome:** TASK-1/2/3 approved as written. TASK-4 was **descoped** — the original cross-package truncate-primitive consolidation was rejected as over-engineering (would add `context→display` and `tools→display` edges for a 3-line function); only the in-place glyph fix survives. See the task for the reasoning.

---

## ✓ DONE TASK-1: Delete dead `snippet_around` (R10)

`snippet_around` — `co_cli/index/search_util.py:85` — has **zero callers**. Confirmed by a blind cold-read refute pass: no production, test, eval, string-dispatch, `__all__`, or spec reference; the only mentions are two historical completed-exec-plan notes. It was orphaned when `tools/obsidian/` (its sole caller) was removed in `6390d73c` and was carried through the memory-module refactor without being re-wired. The other five public functions in `search_util.py` (`normalize_bm25`, `run_fts`, `sanitize_fts5_query`, `kind_clause`, `source_clause`) all have live callers in `index/_retrieval.py`/`store.py`, so the module is sound — only this one function is orphaned.

**files:** `co_cli/index/search_util.py`

**done_when:** function removed (def + body); `rg "snippet_around" co_cli/ tests/ evals/` = 0; full suite green; no behavior change.

**success_signal:** N/A (internal subtraction; no user-observable surface).

---

## ✓ DONE TASK-2: Add `_CHARS` suffix to char-count constants (R9)

Char-count constants used as `len(text)` budgets must carry the `_CHARS` unit suffix (`code-conventions.md:36`). Two clusters drift:

- `co_cli/context/_tool_result_markers.py:18-21` — `_ARG_PREVIEW_MAX`, `_CMD_PREVIEW_MAX`, `_URL_PREVIEW_MAX`, `_QUERY_PREVIEW_MAX` (all char limits passed to `_truncate(value, max_len)`). Rename → `_ARG_PREVIEW_MAX_CHARS`, `_CMD_PREVIEW_MAX_CHARS`, `_URL_PREVIEW_MAX_CHARS`, `_QUERY_PREVIEW_MAX_CHARS`.
- `co_cli/commands/queue_control.py:16` — `_PREVIEW_BUDGET = 60` (char budget compared to `len(text)` at :21). Rename → `_PREVIEW_BUDGET_CHARS`.

Module-private renames; update each use site in the same module. (TASK-4 may absorb `queue_control._PREVIEW_BUDGET` entirely — sequence TASK-2 first or fold the rename into TASK-4 if done together.)

**files:** `co_cli/context/_tool_result_markers.py`, `co_cli/commands/queue_control.py`

**done_when:** all five constants carry `_CHARS`; their use sites updated; `rg "_ARG_PREVIEW_MAX\b|_CMD_PREVIEW_MAX\b|_URL_PREVIEW_MAX\b|_QUERY_PREVIEW_MAX\b|_PREVIEW_BUDGET\b" co_cli/` = 0; full suite green; no behavior change.

**success_signal:** N/A.

---

## ✓ DONE TASK-3: Route `_write_consolidated_skill` through `atomic_write_text` (R11)

`co_cli/daemons/dream/_housekeeping.py:334` does `anchor.path.write_text(new_text, encoding="utf-8")` — a full-overwrite mutation of a curated skill file in the dream daemon, bypassing the shared atomic primitive that `code-conventions.md:52` mandates and that every sibling write in `daemons/dream/` already uses (`state.py:92`, `_queue.py:25`). A crash mid-write here truncates a real skill file. Replace with `atomic_write_text(anchor.path, new_text)` (the primitive `mkdir`s the parent itself, so no pre-create needed; `_write_consolidated_skill` returns `anchor.path` unchanged).

Not in scope: `tools/files/write.py:302,380` (the user-owned file-write tool, which documents its own non-atomic behavior at :283) and `_process.py:54` (transient pid file) — both are deliberate, not residue.

**files:** `co_cli/daemons/dream/_housekeeping.py`

**done_when:** `_write_consolidated_skill` writes via `atomic_write_text`; `rg "\.write_text\(" co_cli/daemons/dream/` shows only the pid-file site (`_process.py:54`); full suite green; no behavior change.

**success_signal:** N/A.

---

## ✓ DONE TASK-4: Fix the truncate-idiom glyph drift in place (R11) — DESCOPED at Gate 1

The `text[: n] + ellipsis` "truncate to N chars" idiom appears in 5 sites across 4 packages, and the copies have drifted on glyph + reserve width:

- `co_cli/context/_tool_result_markers.py:51` `_truncate(value, max_len)` → `value[: max_len - 3] + "..."` (**ascii — the outlier**)
- `co_cli/commands/queue_control.py:19` `_truncate(text, budget)` → `text[: budget - 1] + "…"` (unicode)
- `co_cli/main.py:552` `_preview(text, budget)` → `text[: budget - 1] + "…"`
- `co_cli/main.py:506`, `co_cli/main.py:518` — two inline copies (queue head, session label)
- `co_cli/tools/deferred_prompt.py:38` — inline copy → `line[: _ONE_LINER_MAX_CHARS - 1] + "…"`

**Gate-1 decision: REJECT the cross-package shared primitive (original Option A).** The coupling check kills it: `context/_tool_result_markers.py` (imports only `re`/`typing`) and `tools/deferred_prompt.py` (imports `co_cli.deps`) do **not** currently import `display`. Routing all five through a `display.core.truncate_chars` would add two fresh cross-package edges (`context→display`, `tools→display`) to share a 3-line function — more coupling than the duplication it removes, and exactly the over-engineering this audit was scoped to resist (`feedback_no_util_modules`). A 3-line same-package `_truncate` is not a primitive that earns a shared home.

What remains genuinely worth fixing is the **glyph inconsistency** — a real user-visible rendering bug, independent of any abstraction:

- Fix `_tool_result_markers.py:51` to use the unicode `"…"` with `-1` reserve, matching every other site (the ascii `"..."` / `-3` is the lone outlier). In-place, zero new edges.
- *(Optional, low-value, same-module)* collapse `main.py`'s two inline copies (`_queue_head_preview:506`, `_session_label:518`) into the existing module-local `_preview` — zero new edges. Skip if it reads as churn.

Leave the remaining self-contained same-package `_truncate`/inline copies as-is; same-package duplication of a trivial 3-liner is below the bar for forced consolidation.

**files:** `co_cli/context/_tool_result_markers.py` (+ optionally `co_cli/main.py`)

**done_when:** no ascii-ellipsis truncation remains (`rg '\[: ?\w+ ?- ?3\] ?\+ ?"\.\.\."' co_cli/` = 0); all preview/label truncation renders the unicode `"…"`; full suite green; no behavior change beyond the glyph unification.

**success_signal:** tool-result markers truncate with the same `…` glyph as `/queue`, `/sessions`, and deferred-tool one-liners.

---

## Notes for the dev team

- TASK-4 no longer touches `queue_control` (descoped), so it no longer overlaps TASK-2's `_PREVIEW_BUDGET` rename — the two are now independent.
- All four tasks are behavior-preserving subtraction/rename/dedup. Run `scripts/quality-gate.sh full`. No spec changes expected (no runtime behavior surface changes); `/sync-doc` likely a no-op.

---

## Delivery Summary — 2026-06-22

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `snippet_around` removed; `rg snippet_around co_cli/ tests/ evals/` = 0 | ✓ pass |
| TASK-2 | all 5 char constants carry `_CHARS`; old names = 0 in `co_cli/` | ✓ pass |
| TASK-3 | `_write_consolidated_skill` writes via `atomic_write_text`; only pid-file `write_text` remains in `daemons/dream/` | ✓ pass |
| TASK-4 | no ascii-ellipsis truncation slice in `co_cli/`; glyph unified to `…` | ✓ pass |

**Tests:** scoped — 54 passed, 0 failed (`test_flow_tool_result_markers`, `test_flow_queue_command`, `test_housekeeping`, `test_skill_housekeeping`, `test_recall_floors`)
**Doc Sync:** clean (no-op — all changes are behavior-preserving module-private internals; no `docs/specs/` reference to any changed symbol)

**Changes:**
- `co_cli/index/search_util.py` — deleted dead `snippet_around` (zero callers; `re` import retained, still used by other functions).
- `co_cli/context/_tool_result_markers.py` — renamed 4 char constants to `_CHARS`; updated 4 use sites; fixed `_truncate` glyph (`"..."`/`-3` → `"…"`/`-1`).
- `co_cli/commands/queue_control.py` — renamed `_PREVIEW_BUDGET` → `_PREVIEW_BUDGET_CHARS`.
- `co_cli/daemons/dream/_housekeeping.py` — added `atomic_write_text` import; `_write_consolidated_skill` now writes atomically.
- TASK-4 optional `main.py` inline-copy dedup intentionally skipped (low-value, no inconsistency remains there).

**Overall: DELIVERED**
All four tasks passed `done_when`, lint clean, scoped tests green, doc sync no-op. Behavior-preserving except the intended `"..."`→`"…"` glyph unification in tool-result markers.

---

## Implementation Review — 2026-06-22

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `snippet_around` removed; `rg snippet_around co_cli/ tests/ evals/` = 0 | ✓ pass | `search_util.py` — def deleted (diff), `rg snippet_around` = 0 matches; `re` import retained, still used at `search_util.py:21-23,68-78`; clean spacing into `kind_clause:85` |
| TASK-2 | all 5 char constants carry `_CHARS`; old names = 0 in `co_cli/` | ✓ pass | `_tool_result_markers.py:18-21` new names + use sites `:66,94,101,118`; `queue_control.py:16,19` `_PREVIEW_BUDGET_CHARS`; `rg` old names = 0 |
| TASK-3 | writes via `atomic_write_text`; only pid-file `write_text` remains in `daemons/dream/` | ✓ pass | `_housekeeping.py:335` `atomic_write_text(anchor.path, new_text)`; import `:31`; primitive mkdirs parent + utf-8 default (`fileio/atomic.py:8`), behavior-preserving; only remaining `.write_text` is pid file `_process.py:54` |
| TASK-4 | no ascii reserve-width truncation; glyph unified to `…` | ✓ pass | `_tool_result_markers.py:55` `value[: max_len - 1] + "…"`; ascii-slice grep = 0; all peer sites unicode (`queue_control.py:23`, `main.py:506,518,557`, `deferred_prompt.py:38`) |

### Issues Found & Fixed
No issues found. Lint clean, all `done_when` verified literally, no blocking or minor findings — no fixes applied.

Two non-blocking observations (recorded, no action this round):
- **Scope-creep extras** in `git diff HEAD` not in any task's `files:` — `docs/reference/RESEARCH-prompting-system-{hermes,openclaw,opencode}.md`, `docs/reference/RESEARCH-self-learning-co-vs-hermes.md`, `docs/specs/prompt-assembly.md`, `uv.lock`. Pre-existing working-tree changes from before this plan's creation, unrelated to the delivery. **Do not stage at ship.**
- `co_cli/tools/google/calendar.py:37` `desc[:200] + "..."` — a fixed-cap idiom (no budget-relative reserve), outside TASK-4's enumerated 5-site cluster and pre-existing. Not a finding against this delivery; left for a future audit if desired.

### Tests
- Command: `uv run pytest -v`
- Result: 826 passed, 0 failed
- Log: `.pytest-logs/20260622-222618-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads, all 5 subcommands present — exercises the changed `_housekeeping.py`/`search_util.py`/`_tool_result_markers.py`/`queue_control.py` import edges)
- TASK-4 `success_signal` verified: tool-result markers truncate with the unicode `…` glyph (`_tool_result_markers.py:55`) matching `/queue`, `/sessions`, and deferred-tool one-liners — structurally confirmed across all five sites and covered by `test_flow_tool_result_markers` (green in suite). Chat rendering is LLM-mediated and non-gating.
- TASK-1/2/3: `success_signal` N/A (internal subtraction/rename/durability; no user-observable surface).

### Overall: PASS
All four `✓ DONE` tasks confirmed against `done_when` with file:line evidence; full suite green (826 passed); lint clean; behavior-preserving except the intended glyph unification. Ready for Gate 2 → ship.
