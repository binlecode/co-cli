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
- **Flatten the `_outputs/` layout** from per-run folder to flat prefixed files: `_outputs/<scenario>-<ts>-run.jsonl`, `-case_<id>.jsonl`, `-spans.jsonl` (no per-run directory). Touches `_observability.py` (`open_eval_run`, `EvalRun` path helpers), `_perf.py` (`setup_perf_spans`), `eval_context_stability.py` (`_setup_isolated_spans_log`), the `setup_perf_spans(run.dir)` call site in all spans-collecting evals, and the `trace_files=[…relative_to(run.dir.parent)]` sites in `eval_memory.py` / `eval_skills.py`
- Rewrite `_drift.py` to scan `_outputs/<scenario>-<ts>-run.jsonl` (the new flat names) instead of parsing markdown
- `git rm docs/REPORT-eval-*.md` (13 files)
- Update `CLAUDE.md` permanence policy line to reflect eval run records live in `evals/_outputs/`

**Out:**
- Non-eval `REPORT-*.md` in `docs/` (`REPORT-compaction-*`, `REPORT-fts-*`, etc.) — unchanged
- `docs/specs/uat_evals.md` — handled by `sync-doc` post-delivery; specific stale targets: diagram node `REPORT-eval-<scenario>.md` (line 17), module table row for `evals/_report.py` (line 200), config table row `eval.report_dir`, inline references to `REPORT-eval-<scenario>.md` in the coverage-gaps section, and the two `evals/_outputs/<scenario>-<UTC>/` folder-layout references (lines 145, 203) which must become the flat `<scenario>-<UTC>-{run,case_*,spans}.jsonl` form
- No new render-on-demand CLI feature for human-readable output

## Behavioral Constraints

- **BC-1:** `_drift.py` must keep the same drift algorithm and field mapping after the rewrite: `verdict` field directly (uppercased), `judge.score=N` via the same `_JUDGE_SCORE_RE` applied to the `reason` field, same flip-ratio / score-delta thresholds and aggregate tokens (STABLE / DRIFT_SOFT_FAIL / INSUFFICIENT_HISTORY). What changes by design: (a) the run *set* becomes the actual `evals/_outputs/<scenario>-<ts>-run.jsonl` files — the real, more-complete history (e.g. skills has 26 runs vs 11 markdown sections; memory 17 vs 13) rather than the append-forever markdown log, so the newest-`K` window may select different runs and the aggregate verdict may differ from the old markdown-derived one; (b) skipped cases are now correctly excluded (the old markdown parser kept `SKIP:*` rows as observations, inflating the flip-ratio denominator). These divergences are the intended correction, not regressions — do not assert verdict-identity against the old markdown output.
- **BC-2:** Non-eval `REPORT-*.md` files in `docs/` must not be touched.
- **BC-3:** The `run.jsonl` line schema (`CaseResult` fields) must not change. The on-disk *layout* changes by design: per-run folder → flat prefixed files (`<scenario>-<ts>-run.jsonl` / `-case_<id>.jsonl` / `-spans.jsonl`) directly under `_outputs/`. Per the project's zero-backward-compat / no-migration-code rules, the new flat-glob drift does **not** read pre-existing folder-layout `_outputs/` dirs — they are abandoned (manual `rm` if desired), and drift history rebuilds as evals re-run. No reader code for the old layout.
- **BC-5:** Spans/perf collection must keep working — `setup_perf_spans` still enables span logging and `collect_perf` still reads it; only the spans-file path changes to `<scenario>-<ts>-spans.jsonl`. `trace_files` paths in `run.jsonl` stay relative to `_outputs/` and must resolve to real files.
- **BC-4:** `_drift.py` CLI arg normalization must continue to accept both `agentic_loop` and `eval_agentic_loop` as valid scenario names.

## High-Level Design

### Flat `_outputs/` layout

Replace the per-run folder with flat prefixed files keyed by a run *stem* `<scenario>-<ts>`:

- `EvalRun` drops `dir: Path`; gains `stem: str` (e.g. `agentic_loop-20260609T211649Z`) plus path helpers: `run_jsonl_path → _OUTPUTS_DIR / f"{stem}-run.jsonl"`, `case_trace_path(case_id) → _OUTPUTS_DIR / f"{stem}-case_{case_id}.jsonl"`, and a new `spans_path → _OUTPUTS_DIR / f"{stem}-spans.jsonl"`
- `open_eval_run` no longer `mkdir`s a per-run dir — it ensures `_OUTPUTS_DIR` exists, builds the stem, and `touch`es `run_jsonl_path`
- `setup_perf_spans` takes the spans path (pass `run.spans_path`) instead of a `run_dir`; `eval_context_stability._setup_isolated_spans_log` follows the same change. All `setup_perf_spans(run.dir)` call sites become `setup_perf_spans(run.spans_path)`
- `trace_files` sites in `eval_memory.py` / `eval_skills.py` change `relative_to(run.dir.parent)` → `relative_to(_OUTPUTS_DIR)`, so stored paths become `<scenario>-<ts>-case_<id>.jsonl` (still `_outputs/`-relative; co trace links resolve)

### `_drift.py` rewrite

- `_OUTPUTS_DIR = Path(__file__).parent / "_outputs"` (replaces `_REPORTS_DIR`)
- `_STEM_RE = re.compile(r"^(?P<scenario>.+)-\d{8}T\d{6}Z$")` — splits a run stem into scenario + timestamp; the greedy `.+` correctly keeps hyphens inside scenario names like `context-stability`
- `_discover_scenarios()` → globs `_OUTPUTS_DIR.glob("*-run.jsonl")`, strips the `-run.jsonl` suffix to get the stem, applies `_STEM_RE` to recover the scenario, returns sorted unique scenarios
- `parse_runs(scenario, limit)` → globs `_OUTPUTS_DIR.glob(f"{scenario}-????????T??????Z-run.jsonl")`, sorts newest-first (filename sorts chronologically), reads each, extracts `{case_name: CaseObservation}` per run
- `_parse_run_jsonl(run_jsonl_path)` → reads the file line-by-line; extracts `name`, normalizes `verdict` to uppercase (`.upper()`) before comparing against `_VERDICT_TOKENS` — required because `Verdict` is a `StrEnum` with lowercase values (`"pass"`, `"soft_fail"`) in JSONL; skips rows where `"skipped": true`; applies `_JUDGE_SCORE_RE` to `reason` field for `judge_score`
- CLI normalization: strip `eval_`/`eval-` prefix as before; exact-match against discovered scenarios first, then try `_`↔`-` swap. Two scenarios write hyphenated stems (`open_eval_run("context-stability")` at `eval_context_stability.py:1075`, `open_eval_run("session-continuity")` at `eval_session_continuity.py:478`); all others use underscores. The generic `_`↔`-` swap covers both — no per-scenario special-casing.
- `scenario_to_report_path` → deleted; `main()` resolves a scenario to its `*-run.jsonl` glob directly
- No-arg `_drift.py` run now covers all `_outputs/` scenarios including those that never had a REPORT file (`mindset_selection`, `research_direct`, `smoke`, etc.) — correct expanded behavior, not a bug

Dead code removed from `_observability.py`: `prior_run_dir`, `load_prior_cases`, and the stale comment in `load_prior_cases` that reads "the drift aggregator (T-9) reads the REPORT markdown, not this JSONL".

## Tasks

### ✓ DONE TASK-1: Delete `_report.py` and dead helpers in `_observability.py`

**files:** `evals/_report.py`, `evals/_observability.py`

- Delete `evals/_report.py` entirely
- Remove `prior_run_dir` and `load_prior_cases` functions from `_observability.py`
- Remove stale comment block in `load_prior_cases` (now gone with the function)

**done_when:** `grep -r "from evals._report\|prior_run_dir\|load_prior_cases" evals/` returns empty

**success_signal:** N/A

**prerequisites:** none

---

### ✓ DONE TASK-2: Remove `_REPORT_PATH` and `prepend_report` from all eval scripts

**files:** `evals/eval_agentic_loop.py`, `evals/eval_approval_discipline.py`, `evals/eval_bounded_autonomy.py`, `evals/eval_context_stability.py`, `evals/eval_daily_chat.py`, `evals/eval_groundedness.py`, `evals/eval_memory.py`, `evals/eval_multistep_plan.py`, `evals/eval_session_continuity.py`, `evals/eval_skills.py`, `evals/eval_user_model.py`

- Remove `_REPORT_PATH = ...` module-level constant
- Remove `from evals._report import prepend_report` import
- Remove `prepend_report(...)` call block at end of each eval's main function
- Update `eval_session_continuity.py` and `eval_context_stability.py` module docstrings that reference `prepend_report`

**done_when:** `grep -r "_REPORT_PATH\|prepend_report" evals/` returns empty

**success_signal:** N/A

**prerequisites:** TASK-1

---

### ✓ DONE TASK-3: Flatten `_outputs/` layout to prefixed files

**files:** `evals/_observability.py`, `evals/_perf.py`, `evals/eval_context_stability.py`, plus the `setup_perf_spans(run.dir)` call sites in `evals/eval_agentic_loop.py`, `evals/eval_approval_discipline.py`, `evals/eval_bounded_autonomy.py`, `evals/eval_groundedness.py`, `evals/eval_multistep_plan.py`, `evals/eval_user_model.py`, and the `trace_files` sites in `evals/eval_memory.py`, `evals/eval_skills.py`

- `EvalRun`: drop `dir: Path`; add `stem: str`; add path helpers `run_jsonl_path`, `case_trace_path(case_id)`, `spans_path` that build `_OUTPUTS_DIR / f"{stem}-…"`
- `open_eval_run`: ensure `_OUTPUTS_DIR` exists (no per-run `mkdir`), set `stem = f"{name}-{iso_compact}"`, `touch` `run_jsonl_path`; update the docstring example (`run.case_dir(...)` → `run.case_trace_path(...)`)
- `_perf.setup_perf_spans(spans_log: Path)`: take the spans path directly; callers pass `run.spans_path`
- `eval_context_stability._setup_isolated_spans_log`: same change; replace `run_dir = run.dir` usages with `run.spans_path` / outputs-relative paths
- `setup_perf_spans(run.dir)` → `setup_perf_spans(run.spans_path)` at all 6+ call sites
- `eval_memory.py` / `eval_skills.py`: `relative_to(run.dir.parent)` → `relative_to(_OUTPUTS_DIR)` (import the constant or expose it on `EvalRun`)

**done_when:** `grep -rn "run\.dir\b\|\.case_dir\b" evals/` returns empty; one eval run (e.g. `uv run python evals/eval_daily_chat.py`) produces `_outputs/daily_chat-<ts>-run.jsonl` + `-case_*.jsonl` + `-spans.jsonl` as flat files (no per-run folder)

**success_signal:** N/A

**prerequisites:** TASK-1 (removes `prior_run_dir`/`load_prior_cases` that also reference the old dir helper)

---

### ✓ DONE TASK-4: Rewrite `_drift.py` to read `_outputs/<scenario>-<ts>-run.jsonl`

**files:** `evals/_drift.py`

- Replace `_REPORTS_DIR` with `_OUTPUTS_DIR = Path(__file__).parent / "_outputs"`
- Add `_STEM_RE = re.compile(r"^(?P<scenario>.+)-\d{8}T\d{6}Z$")` to split a run stem into scenario + timestamp
- Replace `scenario_to_report_path` with direct `*-run.jsonl` glob resolution
- Replace `parse_runs(report_path, limit)` with `parse_runs(scenario, limit)` that globs `f"{scenario}-????????T??????Z-run.jsonl"`, sorts newest-first, reads each file
- Replace `_parse_run_section` (markdown regex) with `_parse_run_jsonl(run_jsonl_path)` that reads JSONL; normalize `verdict` to uppercase (`.upper()`) before `_VERDICT_TOKENS` check; skip rows where `"skipped": true`
- Update `_discover_scenarios()` to glob `*-run.jsonl`, strip the suffix, apply `_STEM_RE` for unique scenario names
- Update `_render` signature to remove `report_path` arg (no longer needed)
- Update `main()` accordingly; preserve CLI arg normalization (strip `eval_`/`eval-`, handle `_`↔`-`)

**done_when:** `uv run python evals/_drift.py daily_chat` prints a drift table (or "insufficient history") without error after a fresh flat-layout run exists; `uv run python evals/_drift.py` runs over all discovered scenarios without error

**success_signal:** N/A

**prerequisites:** TASK-3 (drift reads the new flat names)

---

### ✓ DONE TASK-5: Delete `docs/REPORT-eval-*.md`

**files:** `docs/REPORT-eval-agentic-loop.md`, `docs/REPORT-eval-approval-discipline.md`, `docs/REPORT-eval-background.md`, `docs/REPORT-eval-bounded-autonomy.md`, `docs/REPORT-eval-context-stability.md`, `docs/REPORT-eval-daily-chat.md`, `docs/REPORT-eval-groundedness.md`, `docs/REPORT-eval-memory.md`, `docs/REPORT-eval-multistep-plan.md`, `docs/REPORT-eval-session-continuity.md`, `docs/REPORT-eval-skills.md`, `docs/REPORT-eval-trust-visibility.md`, `docs/REPORT-eval-user-model.md`

- `git rm docs/REPORT-eval-*.md`

**done_when:** `find docs -name "REPORT-eval-*.md" | wc -l` outputs `0`

**success_signal:** N/A

**prerequisites:** TASK-1, TASK-2, TASK-4 (confirm no remaining callers first)

---

### ✓ DONE TASK-6: Update `CLAUDE.md` permanence policy

**files:** `CLAUDE.md`

- Update the line `` `REPORT-*.md`: permanent, lives in `docs/`, only produced by eval/benchmark/script runs. `` to clarify that eval run records now live in `evals/_outputs/` (not `docs/`); `REPORT-*.md` in `docs/` are still permanent but now refers only to non-eval reports

**done_when:** `grep "REPORT-eval" CLAUDE.md` returns empty

**success_signal:** N/A

**prerequisites:** TASK-5

---

## Testing

No new tests are needed — this is a deletion + refactor with no new runtime behavior. Verification criteria:

1. One eval re-run (e.g. `uv run python evals/eval_daily_chat.py`) writes flat `_outputs/<scenario>-<ts>-{run,case_*,spans}.jsonl` files and no per-run folder (TASK-3 done_when)
2. `uv run python evals/_drift.py` runs clean over the new flat-layout `_outputs/` data (TASK-4 done_when)
3. `scripts/quality-gate.sh full` passes (lint + pytest)
4. `grep -rn "prepend_report\|_REPORT_PATH\|prior_run_dir\|load_prior_cases\|REPORT-eval\|run\.dir\b\|\.case_dir\b" evals/` returns empty (no orphaned references to the removed report path or old dir layout)

## Open Questions

None — source inspection confirms `run.jsonl` carries all fields the markdown rendered. Judge score extraction uses the same `_JUDGE_SCORE_RE` pattern on the `reason` field.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | `Verdict` is indeed a lowercase `StrEnum`; the uppercase `_VERDICT_TOKENS` check would silently drop all cases | TASK-4 bullet and High-Level Design updated: `_parse_run_jsonl` normalizes verdict to `.upper()` before token check |
| CD-M-2 | adopt | `skipped=true` rows in JSONL have a real `verdict` field — without an explicit guard, skipped cases would pollute drift history | TASK-4 bullet updated: `_parse_run_jsonl` skips rows where `"skipped": true` |
| CD-m-1 | adopt | `eval_context_stability.py` docstring also references `prepend_report` | TASK-2 updated to name both `eval_session_continuity.py` and `eval_context_stability.py` |
| CD-m-2 | adopt | Expanded discovery scope is correct and expected behavior | High-Level Design note added: no-arg run now covers all `_outputs/` scenarios |
| PO-m-1 | adopt | Concrete sync-doc targets prevent under-correction | Scope Out updated to enumerate the four specific stale targets in `uat_evals.md` |

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev drop-eval-markdown-reports`


---

## Delivery Summary — 2026-06-10

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep "from evals._report\|prior_run_dir\|load_prior_cases" evals/` empty | ✓ pass |
| TASK-2 | `grep "_REPORT_PATH\|prepend_report" evals/` empty | ✓ pass |
| TASK-3 | `grep "run.dir\|.case_dir" evals/` empty; eval writes flat `<scenario>-<ts>-{run,case_*,spans}.jsonl` | ✓ pass |
| TASK-4 | `_drift.py` renders a drift table over flat run.jsonl + runs clean over all scenarios | ✓ pass |
| TASK-5 | `find docs -name "REPORT-eval-*.md" \| wc -l` == 0 | ✓ pass |
| TASK-6 | `grep "REPORT-eval" CLAUDE.md` empty | ✓ pass |

**Tests:** scoped — 9 passed, 0 failed (`tests/test_eval_perf.py`). Drift parse/render/aggregate validated against synthesized real flat `run.jsonl` files in a temp dir (lowercase-verdict uppercasing, skip filtering, judge.score-from-reason, flip + score-regression detection, table render) — all assertions passed.
**Doc Sync:** fixed — `docs/specs/uat_evals.md` (diagram, lifecycle, verdict taxonomy, config table, Files table, coverage-gaps drift row) + source docstrings/comments in `_observability.py`, `_rubrics.py`, `_trace.py`, `eval_user_model.py` (stale `REPORT` references). Cross-doc index clean.

**Integration notes (TL):**
- The plan scoped `trace_files` rewires to `eval_memory`/`eval_skills` only, but `eval_context_stability` (lines 663/1001) and `eval_session_continuity` (lines 230/420) built trace paths from `f"{case_dir.parent.name}/{case_dir.name}"` — layout-dependent on the old per-run folder. Under the flat layout this would have produced double-prefixed garbage paths. Fixed all four to `str(case_dir.relative_to(run.outputs_dir))` (scope miss in the plan, caught at integration).
- New `EvalRun.outputs_dir` property added (flat `_outputs/` handle) so `trace_files` relative-path construction has a clean anchor without importing the private `_OUTPUTS_DIR` across modules.
- Per BC-3: the new flat-glob `_drift.py` does not read pre-existing folder-layout `_outputs/` dirs — drift history rebuilds as evals re-run; old dirs are abandoned (manual `rm` if desired). No full eval was run during delivery (real-LLM cost); flat-write path is verified by the new EvalRun API unit-probe + `setup_perf_spans` import check.

**Overall: DELIVERED**
All six tasks pass `done_when`; lint clean; scoped tests green; doc sync fixed and verified.

---

## Implementation Review — 2026-06-10

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `grep "from evals._report\|prior_run_dir\|load_prior_cases" evals/` empty | ✓ pass | `evals/_report.py` deleted (staged `D`); `_observability.py` — `prior_run_dir`/`load_prior_cases` gone, no orphaned imports (all of `json`/`time`/`asdict`/`field`/`Verdict` still live) |
| TASK-2 | `grep "_REPORT_PATH\|prepend_report" evals/` empty | ✓ pass | All 11 evals: no `_report` import / `_REPORT_PATH` / `prepend_report`; orphan locals (`iso`, `run_dir`, `_PROJECT_ROOT`) removed; docstrings de-referenced |
| TASK-3 | no `run.dir`/`.case_dir`; eval writes flat files | ✓ pass | `EvalRun` flat path helpers (`_observability.py:121-134`); `open_eval_run` no per-run mkdir (`:146-152`); `setup_perf_spans(spans_log)` (`_perf.py:96`); 6 call sites pass `run.spans_path`; `_setup_isolated_spans_log(run.spans_path)` (`eval_context_stability.py:330,1072`) |
| TASK-4 | `_drift.py` renders over flat run.jsonl + clean over all scenarios | ✓ pass | flat glob + greedy `_STEM_RE` (`_drift.py:41,98,181`); `_parse_run_jsonl` skips `skipped`, uppercases verdict, judge.score from `reason` (`:77,83,86`); `_`↔`-` + `eval_` normalization (`:189-208`); thresholds K=5/0.20/2 unchanged |
| TASK-5 | `find docs -name "REPORT-eval-*.md" \| wc -l` == 0 | ✓ pass | 13 files staged `D`; find count 0 |
| TASK-6 | `grep "REPORT-eval" CLAUDE.md` empty | ✓ pass | permanence line rewritten to point at `evals/_outputs/` JSONL |

Call-path / BC verification: `trace_files` layout-correctness re-verified COLD by an adversarial reviewer across all 8 sites (context_stability:663/1001, session_continuity:230/420, memory:125/259, skills:117/213) — all use `str(<var>.relative_to(run.outputs_dir))`, none use the old `.parent.name` form. `_OUTPUTS_DIR` confirmed not leaked outside the `evals` package (no `_prefix` visibility violation).

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `trace_files` built from `case_dir.parent.name` (old folder layout) — under flat layout yields broken double-prefixed paths | `eval_context_stability.py:663,1001`, `eval_session_continuity.py:230,420` | blocking | Fixed during /orchestrate-dev integration (plan scope-miss; caught by TL). Review re-confirmed all four corrected to `relative_to(run.outputs_dir)` |
| `_drift.py` re-declares `_OUTPUTS_DIR` instead of importing the private one | `_drift.py:35` | minor | Left as-is — package-internal, no functional impact; importing a private constant cross-module would be worse; matches the prior `_REPORTS_DIR` pattern |
| `case_dir` local now holds a file path, not a dir (pre-existing misnomer) | `eval_context_stability.py:373,873`, `eval_session_continuity.py:117,279` | minor | Left as-is — cosmetic, functionally correct; renaming is out of scope per surgical-changes |

### Tests
- Command: `uv run pytest -x -q`
- Result: 654 passed, 0 failed (1 warning) in 147.98s — all durations sub-second, no stalled LLM calls
- Log: `.pytest-logs/` (review-impl run)
- Plus: scoped `tests/test_eval_perf.py` 9 passed; drift parse/render/aggregate validated against synthesized real flat `run.jsonl` (temp dir)

### Behavioral Verification
- No user-facing `co` CLI surface changed (this is the eval harness). `co status` is not a command in this project.
- Eval-harness behavior verified end-to-end against real code (no LLM, temp `_OUTPUTS_DIR`): `open_eval_run` writes flat `<stem>-{run,case_X1,spans}.jsonl` directly under `_outputs/` (no subdir); `trace_files` entry resolves to a real file; one model span emitted via `tracing` landed at the flat `spans_path` and `collect_perf`/`model_spans_for_traces` read it back (`peak_input_tokens=1234`) — confirms BC-5. Hyphenated scenario name exercised.
- `_drift.py` CLI verified: clean no-op over current store, insufficient-history per scenario, `eval_` prefix normalization.
- `success_signal`: all tasks N/A — nothing to smoke-check.

### Overall: PASS
All six tasks meet `done_when` with file:line evidence; full suite green; lint clean; the one real (layout-dependent trace_files) defect was caught and fixed at integration and re-confirmed here; behavioral BC-5 verified end-to-end. Ready for Gate 2 / `/ship`.
