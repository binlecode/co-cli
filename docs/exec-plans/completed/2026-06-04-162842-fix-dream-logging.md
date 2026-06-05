# Fix Dream Daemon Logging

## Context

The dream daemon runs as a **detached separate process** (`spawn_detached` → `co dream start --foreground`). Its logging is wired by `_install_daemon_log_handler()` in `co_cli/daemons/dream/process.py:201-220`, which attaches a single plain-text `FileHandler` to the **root** logger writing to `DREAM_LOG_DIR/{ts}.log` (one accumulating file per daemon run, formatter `%(asctime)s %(levelname)s %(name)s: %(message)s`).

The main app process, by contrast, wires observability via `main.py:_setup_observability()` (lines 84-98), which does three things:
1. `setup_file_logging(LOGS_DIR, ...)` → rotating JSONL `co-cli.jsonl` (INFO+) + `errors.jsonl` (WARNING+)
2. `setup_spans_log(LOGS_DIR / "co-cli-spans.jsonl", ...)` → rotating JSONL span stream on the dedicated `co_cli.observability.spans` logger with `propagate=False`
3. Suppresses noisy third-party loggers (`_SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]`)

The dream daemon does **none** of (1)–(3) the same way.

**Verified current behavior** (code analysis of `tracing.py:_emit` + `process.py`):
- `setup_spans_log` is **never** called in the dream process. The `co_cli.observability.spans` logger therefore keeps its default `propagate=True` and has no handler of its own. When `_emit` calls `logging.getLogger("co_cli.observability.spans").info(json_record)`, the record **propagates to root** and is caught by the dream's plain-text `FileHandler`. So dream span records are **not dropped — they are written into `{ts}.log` wrapped in the plain-text formatter** (`<ts> INFO co_cli.observability.spans: {...json...}`), which is **not valid JSONL** and is **invisible to `co tail` / `co trace`** (both hardcode `LOGS_DIR / "co-cli-spans.jsonl"` as their only data source — `tail.py:18`, `trace_view.py:20`).
- Noisy loggers (`openai`/`httpx`/`anthropic`/`hpack`) are **not** suppressed in the dream process, so the daemon log carries HTTP/SDK noise.
- `setup_file_logging` **hardcodes** the filenames `co-cli.jsonl` and `errors.jsonl` (`file_logging.py:114-131`); it cannot currently emit a `co-dream.jsonl`.

**Constraint that shapes the design:** `RotatingFileHandler` is **not multi-process safe**. The dream daemon and main app run concurrently, so the dream process must write to **distinct filenames** (`co-dream*`) — it cannot share `co-cli.jsonl` / `errors.jsonl` / `co-cli-spans.jsonl` with the main app without rotation races.

`DREAM_LOG_DIR = LOGS_DIR / "dream"` (`config/core.py:47`) is referenced only inside `process.py` (import, mkdir loop, the handler). No tests, no other source. No test currently asserts on dream logging.

## Problem & Outcome

**Problem:** Dream daemon observability is unusable. App logs accumulate as unbounded per-run plain-text files; span records are mangled into those plain-text files via root propagation and are unreadable by the span viewers.

**Outcome:** The dream daemon emits the same structured-log shape as the main app — a rotating JSONL app log `co-dream.jsonl` and a rotating JSONL span stream `co-dream-spans.jsonl`, both directly under `LOGS_DIR`, with noisy third-party loggers suppressed and spans cleanly separated from app logs (`propagate=False`). Dream spans become inspectable as valid JSONL (`jq` over `co-dream-spans.jsonl`).

**Failure cost:** Without this fix, all dream daemon LLM activity (memory review, skill review, housekeeping merge/decay) produces no usable observability. Span records are corrupted into plain text invisible to `co tail`/`co trace`, and app logs grow without bound (one file per daemon start, never rotated, never cleaned). Diagnosing a daemon failure requires manual archaeology across dozens of `{ts}.log` files.

## Scope

**In scope:**
- Parameterize `setup_file_logging` so the app-log filename is caller-controlled (and the errors file is optional).
- A shared observability-bootstrap coordinator used by both main and dream, so the three-step sequence and the noisy-logger suppression list live in one place.
- Wire the dream daemon to emit `co-dream.jsonl` + `co-dream-spans.jsonl` under `LOGS_DIR`.
- Remove the now-orphan `DREAM_LOG_DIR` constant and the per-run `{ts}.log` handler.

**Out of scope:**
- Making `co tail` / `co trace` read dream spans. They remain pointed at `co-cli-spans.jsonl`. Dream spans are inspectable via `jq` on `co-dream-spans.jsonl`; teaching the viewers to glob both streams is a separate enhancement.
- Cleanup of pre-existing accumulated `logs/dream/*.log` files from prior runs (manual `rm`; not production migration code — see `feedback_no_migration_code`).
- Any change to `docs/specs/` (handled by `sync-doc` post-delivery).
- Background-task (`bg-*.log`) cleanup — a separate, already-identified issue.

## Behavioral Constraints

- **No shared log files across processes.** Dream must use `co-dream*` filenames distinct from the main app's `co-cli*` (RotatingFileHandler is not multi-process safe).
- **Main app logging behavior is unchanged.** Same filenames (`co-cli.jsonl`, `errors.jsonl`, `co-cli-spans.jsonl`), same levels, same rotation. The refactor must be behavior-preserving for the main process.
- **Spans stay separated from app logs** in the dream process too: the spans logger must end up with `propagate=False` and its own handler, so span records no longer leak into the app log.
- **Zero backward compat** (`feedback_zero_backward_compat`): `DREAM_LOG_DIR` is removed outright, no alias, no compat shim.
- **Both dream files land directly in `LOGS_DIR`** (`logs/`), not in a `logs/dream/` subfolder (explicit user direction).

## High-Level Design

Introduce one shared coordinator and route both processes through it.

```
co_cli/observability/setup.py  (new)
  # NOTE: the spans-setup function is tracing.setup_log
  # (main.py imports it as `setup_log as setup_spans_log`).
  # Signature: setup_log(log_path, *, max_size_mb, backup_count, redact_patterns)
  from co_cli.observability.tracing import setup_log
  SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]
  setup_observability(
      log_dir, *, app_log_name, spans_log_name,
      settings, errors_log_name=None,
  ):
      setup_file_logging(log_dir, level, max, backup,
                         app_log_name=app_log_name,
                         errors_log_name=errors_log_name)
      setup_log(log_path=log_dir / spans_log_name,
                max_size_mb=settings.observability.spans_log_max_size_mb,
                backup_count=settings.observability.spans_log_backup_count,
                redact_patterns=settings.observability.redact_patterns)
      for name in SUPPRESS_LOGGERS: getLogger(name).setLevel(WARNING)

main.py:_setup_observability()
  → setup_observability(LOGS_DIR,
        app_log_name="co-cli.jsonl",
        spans_log_name="co-cli-spans.jsonl",
        errors_log_name="errors.jsonl",
        settings=settings)

dream/process.py (replaces _install_daemon_log_handler)
  → setup_observability(LOGS_DIR,
        app_log_name="co-dream.jsonl",
        spans_log_name="co-dream-spans.jsonl",
        errors_log_name=None,
        settings=settings)
```

`setup_file_logging` gains keyword-only `app_log_name="co-cli.jsonl"` and `errors_log_name: str | None = "errors.jsonl"`. Defaults preserve the main app exactly. `errors_log_name=None` skips the errors handler — the dream daemon gets the requested **pair** only; WARNING+ records are still captured in `co-dream.jsonl` (INFO+). Both `setup_file_logging` and `setup_spans_log` are already idempotent (dedupe by `baseFilename`), so calling the coordinator more than once in a process is safe.

The dream process reads the module-level `settings` singleton (`config.core.get_settings()`, available before `create_deps`), matching how `main.py` sources config. `DREAM_LOG_DIR` and the `{ts}.log` handler are deleted; the directory-creation loop drops `DREAM_LOG_DIR` (other dirs created from existing `*_DIR` constants remain).

## Tasks

### ✓ DONE TASK-1 — Parameterize `setup_file_logging` filenames
- **files:** `co_cli/observability/file_logging.py`
- Add keyword-only params `app_log_name: str = "co-cli.jsonl"` and `errors_log_name: str | None = "errors.jsonl"`. Use them for the two `_attach_handler` calls; when `errors_log_name is None`, skip the errors handler entirely. Update the module/function docstrings to reflect parameterized names.
- **done_when:** a test calls `setup_file_logging(tmp, app_log_name="co-dream.jsonl", errors_log_name=None)`, emits an INFO record, and asserts `tmp/co-dream.jsonl` exists with a valid-JSON line and `tmp/errors.jsonl` does **not** exist.
- **success_signal:** the app-log filename is caller-controlled and the errors file is optional.
- **prerequisites:** none

### ✓ DONE TASK-2 — Shared `setup_observability` coordinator
- **files:** `co_cli/observability/setup.py` (new)
- Define `SUPPRESS_LOGGERS` and `setup_observability(log_dir, *, app_log_name, spans_log_name, settings, errors_log_name=None)` that calls `setup_file_logging` + `tracing.setup_log` (the real spans-setup symbol; import as `setup_log as setup_spans_log` for readability if desired), reading `settings.observability.*` for level/sizes/backups/redact_patterns, and sets each noisy logger to WARNING. Keep the coordinator scoped to exactly what the two real callers need — do not generalize for hypothetical third processes. `__init__.py` stays docstring-only.
- **done_when:** a test calls `setup_observability(tmp, app_log_name="co-dream.jsonl", spans_log_name="co-dream-spans.jsonl", errors_log_name=None, settings=<real settings>)`, then emits one app-log record and one `push_span`/`pop_span` cycle, and asserts: (a) `tmp/co-dream.jsonl` contains the app record as valid JSON, (b) `tmp/co-dream-spans.jsonl` contains the span record as valid JSON with a `trace_id`, (c) the span record does **not** appear in `co-dream.jsonl` (separation holds), (d) a `logging.getLogger("httpx").level == WARNING`.
- **success_signal:** one call wires the full observability stack (app log + span stream + noise suppression) for any process with caller-chosen filenames.
- **prerequisites:** TASK-1

### ✓ DONE TASK-3 — Wire dream daemon; remove `DREAM_LOG_DIR` orphan
- **files:** `co_cli/daemons/dream/process.py`, `co_cli/config/core.py`
- Replace `_install_daemon_log_handler()` with a call to `setup_observability(LOGS_DIR, app_log_name="co-dream.jsonl", spans_log_name="co-dream-spans.jsonl", errors_log_name=None, settings=settings)` (rename/inline as fits; keep it called at the same point in `_run_foreground`, before `create_deps`). Read `settings` at call-time via `config.core.get_settings()`/the module `settings` attribute — not at import-time. The coordinator must run before any code writes under `LOGS_DIR`; `setup_file_logging`/`setup_log` both `mkdir(exist_ok=True)` their target and `get_settings()→_ensure_dirs()` creates `LOGS_DIR`, so removing it from the mkdir loop is safe. Remove `DREAM_LOG_DIR` from the import and the mkdir loop; update the `_run_foreground` docstring that cites `logs/dream/<ts>.log`. Delete `DREAM_LOG_DIR` from `config/core.py:47`.
- **done_when:** a test drives the dream observability-setup entrypoint within a temp `CO_HOME`, emits one INFO log record, one **WARNING** log record, and one span cycle, and asserts: both `LOGS_DIR/co-dream.jsonl` and `LOGS_DIR/co-dream-spans.jsonl` exist (directly under `logs/`, not `logs/dream/`) and contain valid JSON; the WARNING record is present in `co-dream.jsonl` (confirming WARNING+ is captured without a dedicated errors file); and `grep -r DREAM_LOG_DIR co_cli/` returns nothing.
- **success_signal:** starting the dream daemon produces `co-dream.jsonl` + `co-dream-spans.jsonl` under `logs/` instead of `logs/dream/{ts}.log`.
- **prerequisites:** TASK-2

### ✓ DONE TASK-4 — Route main app through the coordinator
- **files:** `co_cli/main.py`
- Replace the body of `_setup_observability()` with a `setup_observability(LOGS_DIR, app_log_name="co-cli.jsonl", spans_log_name="co-cli-spans.jsonl", errors_log_name="errors.jsonl", settings=settings)` call. Remove the now-duplicated `_SUPPRESS_LOGGERS` constant from `main.py` (it lives in `setup.py`).
- **done_when:** existing suite passes and a test asserts that after `_setup_observability()` runs in a temp `CO_HOME`, `LOGS_DIR/co-cli.jsonl` and `LOGS_DIR/co-cli-spans.jsonl` are the configured handler targets (emit one record of each and assert both files appear with valid JSON) — i.e. main-app behavior is unchanged.
- **success_signal:** N/A (behavior-preserving refactor)
- **prerequisites:** TASK-2

## Testing

- **New test module:** `tests/observability/test_setup_observability.py` (or extend the nearest existing observability test module if one exists). Covers TASK-1/2/4 at the integration boundary using **real** handlers, real `logging`/`tracing` emit, and real files under a temp `CO_HOME` — no mocks.
- **Dream wiring test:** under `tests/daemons/dream/`, assert the two files land directly under `LOGS_DIR` (not `logs/dream/`) after the dream setup entrypoint runs.
- **Global-logging-state hygiene (required):** these setup functions mutate three pieces of global state that **must** be restored by a teardown fixture, or state bleeds across tests:
  1. Handlers added to the **root** logger (`co-dream.jsonl`/`errors.jsonl` app handlers) — pop the specific handlers the test added.
  2. The handler added to the **`co_cli.observability.spans`** logger by `tracing.setup_log` — remove the specific spans handler the test added (deduped by `baseFilename`; the harness `tests/_co_harness.py:_ensure_tracing` only re-adds its own and never removes a foreign one, so an un-removed dream-spans handler is a known cross-test hazard).
  3. The **`propagate`** flag on `co_cli.observability.spans`, which `setup_log` flips to `False` (sticky global) — snapshot it before and restore after.
  Also restore the levels raised on the `SUPPRESS_LOGGERS`. A single fixture that snapshots (handlers, propagate, levels) and restores them on teardown is the required mitigation — "pop handlers / restore levels" alone is insufficient because it omits the spans-logger handler and `propagate`.
- **Functional assertions only** (`feedback_functional_tests_only`): assert observable outcomes — files created, JSON parses, `trace_id` present, span absent from app log, noisy logger level raised. Do **not** assert on handler internals beyond what is needed to confirm the target files.
- Run the scoped suite fail-fast (`pytest -x`) piped to a timestamped `.pytest-logs/` file; tail the log to watch timing.

## Resolved Decisions

1. **Dream errors file → none.** `errors_log_name=None`; just the `co-dream.jsonl` + `co-dream-spans.jsonl` pair the user asked for. WARNING+ records are still captured in the INFO+ `co-dream.jsonl`, so triage is not lost — only a dedicated convenience filter. A separate `co-dream-errors.jsonl` is unrequested scope; file separately if error-triage friction surfaces. (PO-m-1)
2. **Coordinator, not inline.** The new `setup.py` is the minimum that prevents the daemon from re-diverging from the main app's setup sequence — which is the root cause of this very bug. Kept scoped to the two real callers' params, not generalized. (PO-m-2)
3. **Stale `logs/dream/` artifacts → manual one-liner in delivery.** No migration code. The delivery summary must name `rm -rf ~/.co-cli/logs/dream` so the user reaches a clean end state; the daemon never recreates the dir after this fix. (PO-m-3)
4. **Rotation thresholds → shared, no dream-specific cut.** The dream files inherit `settings.observability.*` verbatim — `co-dream.jsonl` at `log_max_size_mb`×`log_backup_count` (5 MB×3), `co-dream-spans.jsonl` at `spans_log_max_size_mb`×`spans_log_backup_count` (50 MB×5). No new `dream_*` config fields, no hardcoded smaller ceiling. Rationale: the dream daemon is low-volume (fires on queue KICKs, not interactive turns) and unattended/long-lived, so it rarely approaches the ceiling and benefits from generous span retention for post-mortem debugging. Splitting the streams doubles the *theoretical* worst-case span budget (~300 MB → ~600 MB across both), but a low-volume stream will not fill it; cutting the ceiling would trade away the retention that justifies daemon logging. The coordinator passes the same `settings` object both call sites already use.

## Delivery-Summary Requirements

The `/orchestrate-dev` delivery summary must explicitly:
- Give the cleanup one-liner `rm -rf ~/.co-cli/logs/dream` for pre-existing per-run logs (Resolved Decision 3).
- Flag the follow-up that `co tail`/`co trace` still do **not** read dream spans (`co-dream-spans.jsonl` is `jq`-inspectable only) so "fix dream logging" doesn't over-imply viewer coverage. (PO-m-4)

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev fix-dream-logging`

---

## Delivery Summary — 2026-06-04

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `setup_file_logging(tmp, app_log_name="co-dream.jsonl", errors_log_name=None)` writes valid-JSON `co-dream.jsonl`, no `errors.jsonl` | ✓ pass |
| TASK-2 | `setup_observability(...)` produces app log + separated span stream (`trace_id`, absent from app log) + `httpx` at WARNING | ✓ pass |
| TASK-3 | dream wiring lands `co-dream.jsonl` + `co-dream-spans.jsonl` directly under `logs/` (not `logs/dream/`); WARNING captured in app log; `grep -r DREAM_LOG_DIR co_cli/` empty | ✓ pass |
| TASK-4 | real `main._setup_observability()` produces `co-cli.jsonl` + `co-cli-spans.jsonl` with valid JSON | ✓ pass |

**Tests:** scoped — 4 new passed (`tests/observability/test_setup_observability.py` ×3, `tests/daemons/dream/test_observability_wiring.py` ×1). Regression sweep over observability + dream-daemon suites: 63 passed, 0 failed. Lint clean.

**Doc Sync:** fixed (full scope — shared-module signature + runtime behavior change). `dream.md` (diagram + lifecycle flow + log-streaming guidance), `observability.md` (bootstrap sequence rewritten around coordinator, `setup_file_logging` signature, new `setup_observability`/`setup.py`/`co-dream*` rows), `bootstrap.md` (coordinator call), `config.md` (`LOGS_DIR` contents). No stale `logs/dream` / `DREAM_LOG_DIR` / `_install_daemon_log_handler` references remain in any spec.

**Implementation note — linter interaction:** during the `main.py` edits, the lint hook auto-removed the freshly-added `setup_observability` import (F401) while the function body still referenced the old symbols, leaving a transient broken state. Recovered by replacing the body first, then re-adding the import. Final state verified by successful import (`import co_cli.main` OK).

### Cleanup & follow-up (per Resolved Decisions)

- **Stale per-run logs (Resolved Decision 3):** the daemon no longer creates `logs/dream/`. To clear pre-existing artifacts from prior runs, run once manually:
  ```
  rm -rf ~/.co-cli/logs/dream
  ```
  This is a one-off op, not migration code.
- **Viewer coverage (PO-m-4):** `co tail` / `co trace` still read only `co-cli-spans.jsonl`. Dream spans are inspectable via `jq` over `~/.co-cli/logs/co-dream-spans.jsonl`, not the live viewers. Teaching the viewers to glob both streams is a separate, unscoped enhancement — "fix dream logging" does not deliver viewer parity.

**Overall: DELIVERED**
All four tasks passed `done_when`, scoped + regression tests green, lint clean, specs synced.

**Next step:** `/review-impl fix-dream-logging` — full suite + evidence scan + auto-fix → verdict appended to plan.

---

## Implementation Review — 2026-06-04

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `co-dream.jsonl` valid JSON, no `errors.jsonl` when `errors_log_name=None` | ✓ pass | `file_logging.py:88-89` kw-only params; `:133` `if errors_log_name is not None` guard; test re-run passed |
| TASK-2 | coordinator wires app + separated spans + httpx WARNING | ✓ pass | `setup.py:20` SUPPRESS_LOGGERS, `:23-63` coordinator; two real callers confirmed (`main.py:84`, `process.py:180`); test re-run passed |
| TASK-3 | `co-dream*` under `logs/` (not `logs/dream/`), WARNING captured, `DREAM_LOG_DIR` gone | ✓ pass | `process.py:180-186` call before `create_deps` via `get_settings()`; `grep DREAM_LOG_DIR co_cli/` empty; `_install_daemon_log_handler` deleted; test re-run passed |
| TASK-4 | real `main._setup_observability()` produces `co-cli.jsonl` + `co-cli-spans.jsonl` | ✓ pass | `main.py:83-90` routes through coordinator; `_SUPPRESS_LOGGERS` removed; test drives the **real** function (CO_HOME + reload, no monkeypatch) and passed |

Adversarial pass cold-re-verified 6 load-bearing claims (behavior-preservation, span separation, WARNING capture, clean removal, idempotency, test integrity) — all CONFIRMED, 0 blocking.

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Test used `monkeypatch.setattr` — violates enforced "no monkeypatch" test policy | `tests/observability/test_setup_observability.py:111-112` | blocking | Rewritten to the repo's sanctioned `CO_HOME` env + `importlib.reload(core, main)` isolation pattern; drives the real `_setup_observability()` |
| Relative-path defeats `baseFilename` dedup → duplicate handlers double-write every record (pre-existing latent bug; user-directed fix) | `file_logging.py:157`, `tracing.py:67` | blocking | Compare against `os.path.abspath(log_path)` (matches how RotatingFileHandler stores baseFilename); added regression test `test_setup_file_logging_idempotent_for_relative_path` |
| TASK-2 test built a local `Settings()` instead of the centralized config singleton (rule 37) | `tests/observability/test_setup_observability.py:104` | minor | Switched to `from tests._settings import SETTINGS` (real `load_config()`) |

**Scope note:** the dedup fix touched `tracing.py` (not in any task's `files:`) — a directed fix of the same latent bug in the symmetric dedup site, authorized by the user.

### Tests
- Command: `uv run pytest` (full suite)
- Result: **642 passed, 0 failed** in 144s
- New tests: 5 (4 observability-module + 1 dream-wiring); all functional, no mocks/monkeypatch, global-logging-state restored via autouse fixtures.
- Log: `.pytest-logs/20260604-201323-review-impl-full.log`

### Behavioral Verification
- `co dream start` → both `~/.co-cli/logs/co-dream.jsonl` (57 lines, valid JSONL) and `co-dream-spans.jsonl` (valid JSONL) appeared within ~1s; **no `logs/dream/` dir**; `co dream stop` clean. TASK-3 `success_signal` verified in the real system.
- Main-app `_setup_observability()` verified via the TASK-4 test driving the real function (CLI has no `co status` command; bootstrap wiring is exercised there + by the full suite).

### Out-of-scope (flagged, not touched)
- `evals/eval_context_stability.py` has F401 lint errors — coworker's actively-edited WIP; auto-fixing would clobber in-progress work. Not in this delivery's scope.
- `co_cli/main.py` also carries a coworker's token-usage-tracking changes. At `/ship`, stage only the fix-dream-logging hunks unless bundling is intended.

### Overall: PASS
All four tasks confirmed with file:line evidence; full suite green; the one test-policy violation and the user-directed relative-path dedup bug fixed with a regression test; behavioral verification of the real daemon confirms the headline fix. Ready for Gate 2 → `/ship`.
