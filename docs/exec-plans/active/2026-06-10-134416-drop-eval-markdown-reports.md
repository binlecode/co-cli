# Plan: drop-eval-markdown-reports

## Context

The eval harness writes two persistent artifacts per run:

1. `evals/_outputs/<name>-<ts>/` — JSONL traces (`run.jsonl` per-case results, `case_<id>.jsonl` per-turn traces, `spans.jsonl`)
2. `docs/REPORT-eval-<scenario>.md` — accumulated human-readable markdown, one file per scenario, newest-run prepended on each execution

`run.jsonl` already carries every field the markdown renders: verdict, duration, model_call_seconds, token_usage, reason (including `judge.score=N`), perf, trace_files. The markdown is a derived formatted view, nothing more.

`evals/_drift.py` parses the markdown with regex (`_ROW_RE`, `_RUN_SPLIT_RE`, `_JUDGE_SCORE_RE`) to extract cross-run verdict and judge-score history — fragile markdown parsing of data that is already available as structured JSONL.

`evals/_report.py` is the markdown writer. `evals/_observability.py` exports `prior_run_dir` and `load_prior_cases` solely to serve `_report.py`'s regression-diff section.

## Problem & Outcome

**Problem:** `docs/REPORT-eval-*.md` duplicate data already stored in `_outputs/*/run.jsonl`. `_drift.py` does fragile markdown parsing when it could read structured JSONL directly.

**Outcome:** `_outputs/` becomes the single source of truth for eval run records. `_drift.py` reads `run.jsonl` directly. `_report.py` and its 13 markdown outputs are deleted. `_observability.py` sheds two dead helpers.

**Failure cost:** No silent breakage today — duplication is harmless — but any drift in the markdown rendering format silently breaks `_drift.py`'s regex parsers.

## Scope

**In:**
- Delete `evals/_report.py`
- Remove `prepend_report` call + `_REPORT_PATH` constant from all 11 eval scripts
- Remove `prior_run_dir` + `load_prior_cases` from `_observability.py` (only callers are in `_report.py`)
- Rewrite `_drift.py` to scan `_outputs/<name>-*/run.jsonl` instead of parsing markdown
- `git rm docs/REPORT-eval-*.md` (13 files)
- Update `CLAUDE.md` permanence policy line to reflect eval run records live in `evals/_outputs/`

**Out:**
- Non-eval `REPORT-*.md` in `docs/` (`REPORT-compaction-*`, `REPORT-fts-*`, etc.) — unchanged
- `docs/specs/uat_evals.md` — handled by `sync-doc` post-delivery; specific stale targets: diagram node `REPORT-eval-<scenario>.md`, module table row for `evals/_report.py`, config table row `eval.report_dir`, and inline references to `REPORT-eval-<scenario>.md` in the coverage-gaps section
- No new render-on-demand CLI feature for human-readable output

## Behavioral Constraints

- **BC-1:** `_drift.py` must produce identical aggregate verdicts (STABLE / DRIFT_SOFT_FAIL / INSUFFICIENT_HISTORY) for the same historical runs before and after the rewrite. The JSONL fields map 1:1 to what the markdown parser extracted: `verdict` field directly, `judge.score=N` via the same `_JUDGE_SCORE_RE` applied to the `reason` field.
- **BC-2:** Non-eval `REPORT-*.md` files in `docs/` must not be touched.
- **BC-3:** `_outputs/` directory structure and `run.jsonl` schema must not change.
- **BC-4:** `_drift.py` CLI arg normalization must continue to accept both `agentic_loop` and `eval_agentic_loop` as valid scenario names.

## High-Level Design

`_drift.py` rewrite:

- `_OUTPUTS_DIR = Path(__file__).parent / "_outputs"` (replaces `_REPORTS_DIR`)
- `_TS_RE = re.compile(r"-\d{8}T\d{6}Z$")` — strips timestamp suffix from dir names; correctly handles hyphens in scenario names like `context-stability`
- `_discover_scenarios()` → scans `_OUTPUTS_DIR`, applies `_TS_RE` to each dir name, returns sorted unique prefixes
- `parse_runs(scenario, limit)` → globs `_OUTPUTS_DIR.glob(f"{scenario}-????????T??????Z")`, sorts newest-first, reads each `run.jsonl`, extracts `{case_name: CaseObservation}` per run
- `_parse_run_jsonl(run_dir)` → reads `run.jsonl` line-by-line; extracts `name`, normalizes `verdict` to uppercase (`.upper()`) before comparing against `_VERDICT_TOKENS` — required because `Verdict` is a `StrEnum` with lowercase values (`"pass"`, `"soft_fail"`) in JSONL; skips rows where `"skipped": true`; applies `_JUDGE_SCORE_RE` to `reason` field for `judge_score`
- CLI normalization: strip `eval_`/`eval-` prefix as before; exact-match against discovered scenarios first, then try `_`↔`-` swap for the one inconsistency (`context-stability` vs `context_stability`)
- `scenario_to_report_path` → deleted (no longer needed); `main()` uses `scenario_to_run_dirs` helper
- No-arg `_drift.py` run now covers all `_outputs/` scenarios including those that never had a REPORT file (`mindset_selection`, `research_direct`, `smoke`, etc.) — this is the correct expanded behavior, not a bug

Dead code removed from `_observability.py`: `prior_run_dir`, `load_prior_cases`, and the stale comment in `load_prior_cases` that reads "the drift aggregator (T-9) reads the REPORT markdown, not this JSONL".

## Tasks

### TASK-1: Delete `_report.py` and dead helpers in `_observability.py`

**files:** `evals/_report.py`, `evals/_observability.py`

- Delete `evals/_report.py` entirely
- Remove `prior_run_dir` and `load_prior_cases` functions from `_observability.py`
- Remove stale comment block in `load_prior_cases` (now gone with the function)

**done_when:** `grep -r "from evals._report\|prior_run_dir\|load_prior_cases" evals/` returns empty

**success_signal:** N/A

**prerequisites:** none

---

### TASK-2: Remove `_REPORT_PATH` and `prepend_report` from all eval scripts

**files:** `evals/eval_agentic_loop.py`, `evals/eval_approval_discipline.py`, `evals/eval_bounded_autonomy.py`, `evals/eval_context_stability.py`, `evals/eval_daily_chat.py`, `evals/eval_groundedness.py`, `evals/eval_memory.py`, `evals/eval_multistep_plan.py`, `evals/eval_session_continuity.py`, `evals/eval_skills.py`, `evals/eval_user_model.py`

- Remove `_REPORT_PATH = ...` module-level constant
- Remove `from evals._report import prepend_report` import
- Remove `prepend_report(...)` call block at end of each eval's main function
- Update `eval_session_continuity.py` and `eval_context_stability.py` module docstrings that reference `prepend_report`

**done_when:** `grep -r "_REPORT_PATH\|prepend_report" evals/` returns empty

**success_signal:** N/A

**prerequisites:** TASK-1

---

### TASK-3: Rewrite `_drift.py` to read `_outputs/*/run.jsonl`

**files:** `evals/_drift.py`

- Replace `_REPORTS_DIR` with `_OUTPUTS_DIR = Path(__file__).parent / "_outputs"`
- Add `_TS_RE = re.compile(r"-\d{8}T\d{6}Z$")` for timestamp stripping
- Replace `scenario_to_report_path` with `_scenario_to_run_dirs(scenario, limit)` that returns sorted `Path` list of run dirs
- Replace `parse_runs(report_path, limit)` with `parse_runs(scenario, limit)` that reads `run.jsonl` files
- Replace `_parse_run_section` (markdown regex) with `_parse_run_jsonl(run_dir)` that reads JSONL; normalize `verdict` to uppercase (`.upper()`) before `_VERDICT_TOKENS` check; skip rows where `"skipped": true`
- Update `_discover_scenarios()` to scan `_OUTPUTS_DIR` and strip `_TS_RE` for unique names
- Update `_render` signature to remove `report_path` arg (no longer needed)
- Update `main()` accordingly; preserve CLI arg normalization (strip `eval_`/`eval-`, handle `_`↔`-`)

**done_when:** `uv run python evals/_drift.py agentic_loop` prints a drift table (or "insufficient history" message) without error; `uv run python evals/_drift.py` runs over all discovered scenarios without error

**success_signal:** N/A

**prerequisites:** none (can run before TASK-1/2, but logically after)

---

### TASK-4: Delete `docs/REPORT-eval-*.md`

**files:** `docs/REPORT-eval-agentic-loop.md`, `docs/REPORT-eval-approval-discipline.md`, `docs/REPORT-eval-background.md`, `docs/REPORT-eval-bounded-autonomy.md`, `docs/REPORT-eval-context-stability.md`, `docs/REPORT-eval-daily-chat.md`, `docs/REPORT-eval-groundedness.md`, `docs/REPORT-eval-memory.md`, `docs/REPORT-eval-multistep-plan.md`, `docs/REPORT-eval-session-continuity.md`, `docs/REPORT-eval-skills.md`, `docs/REPORT-eval-trust-visibility.md`, `docs/REPORT-eval-user-model.md`

- `git rm docs/REPORT-eval-*.md`

**done_when:** `find docs -name "REPORT-eval-*.md" | wc -l` outputs `0`

**success_signal:** N/A

**prerequisites:** TASK-1, TASK-2, TASK-3 (confirm no remaining callers first)

---

### TASK-5: Update `CLAUDE.md` permanence policy

**files:** `CLAUDE.md`

- Update the line `` `REPORT-*.md`: permanent, lives in `docs/`, only produced by eval/benchmark/script runs. `` to clarify that eval run records now live in `evals/_outputs/` (not `docs/`); `REPORT-*.md` in `docs/` are still permanent but now refers only to non-eval reports

**done_when:** `grep "REPORT-eval" CLAUDE.md` returns empty

**success_signal:** N/A

**prerequisites:** TASK-4

---

## Testing

No new tests are needed — this is a deletion + refactor with no new runtime behavior. Verification criteria:

1. `uv run python evals/_drift.py` runs clean over existing `_outputs/` data (TASK-3 done_when)
2. `scripts/quality-gate.sh full` passes (lint + pytest)
3. `grep -r "prepend_report\|_REPORT_PATH\|prior_run_dir\|load_prior_cases\|REPORT-eval" evals/` returns empty (confirming no orphaned references)

## Open Questions

None — source inspection confirms `run.jsonl` carries all fields the markdown rendered. Judge score extraction uses the same `_JUDGE_SCORE_RE` pattern on the `reason` field.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | `Verdict` is indeed a lowercase `StrEnum`; the uppercase `_VERDICT_TOKENS` check would silently drop all cases | TASK-3 bullet and High-Level Design updated: `_parse_run_jsonl` normalizes verdict to `.upper()` before token check |
| CD-M-2 | adopt | `skipped=true` rows in JSONL have a real `verdict` field — without an explicit guard, skipped cases would pollute drift history | TASK-3 bullet updated: `_parse_run_jsonl` skips rows where `"skipped": true` |
| CD-m-1 | adopt | `eval_context_stability.py` docstring also references `prepend_report` | TASK-2 updated to name both `eval_session_continuity.py` and `eval_context_stability.py` |
| CD-m-2 | adopt | Expanded discovery scope is correct and expected behavior | High-Level Design note added: no-arg run now covers all `_outputs/` scenarios |
| PO-m-1 | adopt | Concrete sync-doc targets prevent under-correction | Scope Out updated to enumerate the four specific stale targets in `uat_evals.md` |

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev drop-eval-markdown-reports`

