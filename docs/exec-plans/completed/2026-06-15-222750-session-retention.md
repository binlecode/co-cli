# Session Retention — age-based pruning of session transcripts

## Context

Session transcripts accumulate **indefinitely** in `~/.co-cli/sessions/`. Confirmed at HEAD: no
`unlink`/`prune`/`retention` path touches session `.jsonl` files anywhere in `co_cli/`. Compaction
rewrites a session *in place* (same file), so it bounds a single file's *size* but never the *count*
of files. The dream daemon already owns a retention pass — `prune_done_and_snapshots` /
`_prune_aged_files` (`co_cli/daemons/dream/_housekeeping.py`) — but it targets only
`DREAM_QUEUE_DONE_DIR` and `DREAM_SNAPSHOTS_DIR`, never `sessions_dir`.

Sessions are **not** in the FTS index (`IndexSourceEnum` = MEMORY, DRIVE only; `session_search` is
lexical ripgrep over raw files). So deleting a session file needs **no index sync** — it simply
leaves ripgrep's reach.

Peer practice (surveyed 2026-06-15/16): Claude Code prunes by **age only** (`cleanupPeriodDays`=30) in
a background task and relies on file mtime to protect the active session — a session being appended to
each turn keeps a recent mtime and so never crosses the cutoff (no explicit active-session guard).
openclaw adds a count cap + disk budget and therefore needs an explicit active-session exclusion;
hermes prunes by age (90d). This plan adopts the **simplest converged shape — age only** — which is
exactly Claude Code's, on co's existing housekeeping vehicle.

Relevant current state (verified accurate, safe to plan):
- `co_cli/config/dream.py` — `DreamSettings` (pydantic, `extra="forbid"`), `DREAM_ENV_MAP`. Pattern:
  each knob has an env entry + a bounded `Field`. `done_retention_days: int = Field(default=7, ge=1)`.
- `co_cli/daemons/dream/_housekeeping.py` — `_prune_aged_files(directory, cutoff)` (mtime < cutoff →
  unlink, best-effort log+skip on `OSError`), `prune_done_and_snapshots(...)`,
  `run_housekeeping(deps, cfg, state)` runs the full pass and calls the pruner outside the merge timeout.
- `co_cli/daemons/dream/_state.py` — `HousekeepingStats` cumulative counters (`done_pruned`, etc.).
- `co_cli/config/core.py:39` — `SESSIONS_DIR = USER_DIR / "sessions"`; `deps.sessions_dir` carries it
  (a real `CoDeps` field, already used by the daemon's reviewer).
- `co_cli/session/filename.py` — `parse_session_filename(name)` validates the canonical
  `YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl` scheme; every session file now conforms.

## Problem & Outcome

**Problem:** Session transcript files grow without bound; the operator's only recourse is manual `rm`.
There is no age ceiling.

**Outcome:** One opt-in `DreamSettings` knob — `session_retention_days` — caps session age: the daemon's
existing housekeeping pass deletes sessions whose mtime is older than N days. `0` (default) = disabled,
today's behavior.

**Failure cost:** Without this, long-lived CO_HOMEs accumulate unbounded session files — monotonic disk
growth and a `/resume` / browse list that grows without end and is never curated. Silent until disk
pressure or an unwieldy resume list surfaces it.

## Scope

**In scope:**
- One new `DreamSettings` field (`session_retention_days`) + env entry + bounds.
- Age-based session pruning in `_housekeeping.py` (`*.jsonl` + canonical-name guarded).
- Wiring it into `run_housekeeping` (synchronous, outside the merge timeout, like the existing pruner).
- A `session_pruned` cumulative counter + a per-pass log line naming what was pruned.

**Out of scope (deliberately dropped for simplicity):**
- **Count cap** (`session_retention_max`) — removed; age alone bounds the corpus, and dropping it
  eliminates the active-session-protection guard, mtime-tiebreak, and cap-headroom complexity.
- Disk-byte budget (openclaw's third axis).
- **Soft-delete / trash / revive** — pruning is a hard `unlink()` (see Open Question 4, resolved). No
  archival, no recovery path; no compaction/search/index changes.
- `docs/specs/` edits (handled by `sync-doc` post-delivery).

## Behavioral Constraints

- **Active session protected by mtime (no explicit guard needed).** The live session is appended every
  turn, so its mtime stays recent and an age cutoff of N days never selects it — this is Claude Code's
  model, valid precisely because there is no count dimension. Residual edge: a session left open but
  **idle** beyond the full N-day window could be selected; unlinking its still-open fd would lose pending
  writes. This is the same edge Claude Code accepts, and at a sane cutoff (≥ days) it is vanishingly
  rare. Accepted, not guarded — noted in Open Questions for the record.
- **Off by default; recommended 30.** `session_retention_days` defaults to `0` = disabled. co's design
  retains full on-disk history; silently deleting existing transcripts on first post-upgrade daemon run
  would be a surprising regression. Opt-in only. The documented recommended value is **30 days**,
  matching co's architectural twin Claude Code (`cleanupPeriodDays=30`) and openclaw (`pruneAfter=30d`);
  hermes' 90 is a longer grace tuned for an intermittent multi-user gateway and does not apply to co's
  single local user. Surface 30 in the config field description / docs as the suggested starting value.
- **Observable.** A pass that prunes ≥1 session logs one line: count + oldest→newest pruned mtime date
  range, so an operator who set a value can see what a pass removed.
- **Best-effort, non-fatal.** A per-file `OSError` is logged and skipped; one unlinkable file never
  aborts the sweep (matches `_prune_aged_files`).
- **Only operate on session files.** Glob `*.jsonl` under `sessions_dir` and guard with
  `parse_session_filename` so any foreign file is left untouched.
- **Idempotent / convergent.** Re-running with unchanged config and no new sessions deletes nothing further.

## High-Level Design

1. **Config (`co_cli/config/dream.py`):** add `session_retention_days: int = Field(default=0, ge=0)`
   and `CO_DREAM_SESSION_RETENTION_DAYS` to `DREAM_ENV_MAP`. `0` = disabled.

2. **Pruner (`co_cli/daemons/dream/_housekeeping.py`):** new `prune_sessions(cfg, state, *, sessions_dir)
   -> int`:
   - If `session_retention_days == 0`, return `0` (no-op).
   - `cutoff = now - session_retention_days days`.
   - For each `p in sessions_dir.glob("*.jsonl")` with `parse_session_filename(p.name) is not None` and
     `p.stat().st_mtime < cutoff`: `unlink()`, best-effort (log+skip on `OSError`).
   - On a non-empty delete set, emit one log line: count + oldest→newest pruned mtime date range.
   - Increment `state.stats.session_pruned`; return the count.
   - This is `_prune_aged_files` plus the `*.jsonl`/canonical guard and the summary log line; implement
     as a dedicated function (the canonical guard is session-specific) rather than overloading the
     existing helper.

3. **State (`co_cli/daemons/dream/_state.py`):** add `session_pruned: int = 0` to `HousekeepingStats`.
   Pydantic defaults make this backward-compatible — existing on-disk `_dream_state.json` loads with the
   default applied; no migration code.

4. **Wiring (`run_housekeeping`):** after `prune_done_and_snapshots(...)`, call
   `prune_sessions(cfg, state, sessions_dir=deps.sessions_dir)` synchronously (outside the merge timeout).
   The existing `test_run_housekeeping_decay_runs_after_merge_timeout` fixture builds `deps` as a
   `SimpleNamespace` without `sessions_dir`; it must gain `sessions_dir=<temp sessions dir>` or the call
   raises `AttributeError`.

## Tasks

### ✓ DONE TASK-1 — Add `session_retention_days` config knob
- **files:** `co_cli/config/dream.py`
- **done_when:** `DreamSettings()` exposes `session_retention_days` (default `0`, `ge=0`);
  `CO_DREAM_SESSION_RETENTION_DAYS` resolves through `DREAM_ENV_MAP`; a negative value raises
  `ValidationError`.
- **success_signal:** An operator can set `CO_DREAM_SESSION_RETENTION_DAYS=30` and the loaded config reflects it.
- **prerequisites:** none

### ✓ DONE TASK-2 — Add `session_pruned` counter
- **files:** `co_cli/daemons/dream/_state.py`
- **done_when:** `HousekeepingStats` has `session_pruned: int = 0`; a state JSON written without the
  field still loads (default applies) and a written-then-loaded state round-trips the counter.
- **success_signal:** N/A (pure field add).
- **prerequisites:** none

### ✓ DONE TASK-3 — Implement `prune_sessions` (age-based)
- **files:** `co_cli/daemons/dream/_housekeeping.py`
- **done_when:** `prune_sessions(cfg, state, sessions_dir=tmp)` over a fixture set deletes exactly the
  sessions older than the cutoff, keeps newer ones, leaves non-canonical files untouched, returns the
  deleted count, bumps `state.stats.session_pruned`; `session_retention_days == 0` is a no-op returning
  `0`. Verified by a unit test exercising: deletes-old/keeps-new, disabled no-op, non-canonical
  untouched, and a continuously-recent file never deleted.
- **success_signal:** Over a dir of mixed-age sessions, only those older than the cutoff are gone.
- **prerequisites:** TASK-1, TASK-2

### ✓ DONE TASK-4 — Wire into the housekeeping pass
- **files:** `co_cli/daemons/dream/_housekeeping.py`, `tests/daemons/dream/test_housekeeping.py`
- **done_when:** `run_housekeeping` calls `prune_sessions(cfg, state, sessions_dir=deps.sessions_dir)`
  after `prune_done_and_snapshots`; the existing
  `test_run_housekeeping_decay_runs_after_merge_timeout` `SimpleNamespace` deps fixture gains
  `sessions_dir` so it still passes; and an end-to-end test against a temp CO_HOME with retention
  configured deletes the expired session files and **persists `session_pruned` through
  `save_housekeeping_state` then reads it back via `load_housekeeping_state`**, while a recent file survives.
- **success_signal:** One full housekeeping pass enforces session retention with no effect on memory/skill phases.
- **prerequisites:** TASK-3

## Testing

Extend `tests/daemons/dream/test_housekeeping.py` (existing prune coverage lives there). Functional,
behavior-only assertions mirroring `done_when`:
- `prune_sessions`: deletes sessions older than the cutoff; keeps newer; `session_retention_days == 0`
  deletes nothing; a recent (active-stand-in) file is never deleted.
- Non-canonical `.jsonl` in `sessions_dir` is left untouched.
- Best-effort: an unlinkable file logs and does not abort (skip if not cheaply simulable; do not assert on log text).
- Fixture fix: existing `test_run_housekeeping_decay_runs_after_merge_timeout` still passes with
  `sessions_dir` added to its `SimpleNamespace` deps.
- `run_housekeeping` end-to-end: retention configured → expired sessions deleted, `session_pruned`
  persisted and reloaded from disk, memory/skill counters unaffected.

Real data, temp CO_HOME via `CO_HOME` override; no filesystem mocks. Build `.jsonl` files with
`session_filename(...)` so names are canonical; set mtimes with `os.utime` to simulate age. Run:
`uv run pytest tests/daemons/dream/test_housekeeping.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-session-retention.log`

## Open Questions

1. **[RESOLVED] Age-only scope.** Count cap dropped per direction — age alone bounds the corpus and
   removes the active-session-guard/tiebreak/headroom complexity. Matches Claude Code.
2. **[RESOLVED] Recency = mtime.** mtime tracks real activity (incl. post-compaction in-place rewrites)
   and intrinsically protects the active session under an age-only rule.
3. **[RESOLVED] Default = off (`0`), recommended 30.** Opt-in; co's retain-full-history divergence makes
   a non-zero shipped default a regression. Recommended value **30 days** (Claude Code + openclaw);
   hermes' 90 is multi-user-gateway-specific, not co's profile.
4. **[RESOLVED] Hard delete (not soft / trash / revive).** Pruning is a hard `unlink()`, matching
   `_prune_aged_files` and Claude Code — co's architectural twin (single local user, sessions not
   FTS-indexed, durable value already mined into the memory tier by the dreamer). The peer soft-delete
   rationales do **not** transfer: openclaw/hermes archive because sessions are an indexed and/or
   resumable primary substrate and to avoid orphan/index-sync hazards — none of which co has (co's
   single-file model has no orphan class, and hermes' "soft delete" actually leaks orphaned transcripts
   forever). Recoverability is low-value here: the session was distilled before it aged out, and
   retention is opt-in, so an aged-out session is one the operator explicitly chose to expire. Safety
   comes from off-by-default + an operator-chosen age, not from a trash tier.
5. **Idle-but-open session beyond the cutoff** (accepted edge) — unlinking an open fd loses pending
   writes. Vanishingly rare at a days-scale cutoff; matches Claude Code's accepted behavior. Confirm acceptance.
6. **Disk-byte budget (openclaw's third axis)** is deferred. Confirm it stays out of scope.

## Final — Team Lead

Plan approved (simplified to age-only after the C1/C2 review; the count cap and its
active-session-guard complexity that the review surfaced are now out of scope, mooting those blockers).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> All design questions are resolved (Q4 locked to hard-delete; recommended value 30 days). Only two
> low-stakes confirmations remain: the accepted idle-open-fd edge (Q5) and disk-budget staying out of
> scope (Q6).
> Once approved, run: `/orchestrate-dev session-retention`

## Delivery Summary — 2026-06-16

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `session_retention_days` (default 0, ge=0) exposed; env resolves; negative → ValidationError | ✓ pass |
| TASK-2 | `session_pruned: int = 0` on `HousekeepingStats`; round-trips through save/load | ✓ pass |
| TASK-3 | `prune_sessions` deletes aged, keeps recent, ignores non-canonical, no-op at 0, bumps counter | ✓ pass |
| TASK-4 | wired after `prune_done_and_snapshots`; fixture gained `sessions_dir`; e2e deletes + persists/reloads counter | ✓ pass |

**Tests:** scoped — 20 passed, 0 failed (`tests/daemons/dream/test_housekeeping.py`; 4 new + fixed timeout fixture)
**Doc Sync:** fixed — sessions.md (lifecycle + config note), dream.md (prose, mermaid, stats/config/API/files/test-gate tables); memory.md clean

**Overall: DELIVERED**
All four tasks passed `done_when`, lint clean, scoped suite green, specs synced. Implementation touched only plan-scoped files plus dream.md/sessions.md docs.

## Implementation Review — 2026-06-16

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `session_retention_days` (default 0, ge=0), env resolves, negative → ValidationError | ✓ pass | `config/dream.py:33-37` field (`default=0, ge=0`); `dream.py:15` env map entry; `core.py:181` consumes `DREAM_ENV_MAP`. Verified live: `CO_DREAM_SESSION_RETENTION_DAYS=30` → `load_config().dream.session_retention_days == 30`; `DreamSettings(session_retention_days=-1)` raises ValidationError |
| TASK-2 | `session_pruned: int = 0` on `HousekeepingStats`, round-trips save/load | ✓ pass | `_state.py:50` field add; e2e test asserts persist+reload (`test_housekeeping.py` line ~102) |
| TASK-3 | deletes aged, keeps recent, ignores non-canonical, no-op at 0, bumps counter | ✓ pass | `_housekeeping.py:535-577` `prune_sessions`: 0-guard, `glob("*.jsonl")` + `parse_session_filename` guard, mtime<cutoff unlink, best-effort `OSError` log+skip, summary log line, `state.stats.session_pruned += …`. 4 unit tests green |
| TASK-4 | wired after `prune_done_and_snapshots`, fixture gains `sessions_dir`, e2e deletes+persists/reloads | ✓ pass | `_housekeeping.py:618` call inside `run_housekeeping` (outside merge timeout); `test_run_housekeeping_decay_runs_after_merge_timeout` passes; `test_run_housekeeping_prunes_sessions_and_persists_counter` green |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Working tree carries unrelated WIP from two other active plans (architecture-fitness-functions, test-hygiene-fitness-functions): `co_cli/bootstrap/banner.py`, `pyproject.toml` (import-linter), `.claude/skills/clean-tests/SKILL.md`, `uv.lock`, and untracked `tests/test_arch_*.py`, `tests/_surface_snapshot.json`, `tests/_test_hygiene_debt.txt` | n/a (cross-plan) | scope-pollution | Not a session-retention defect — left as-is. **`/ship` must stage ONLY the session-retention files** (config/dream.py, _state.py, _housekeeping.py, test_housekeeping.py, docs/specs/sessions.md, docs/specs/dream.md). Do not stage the other plans' files. |
| Global `scripts/quality-gate.sh lint` fails: 2 errors in `tests/test_arch_public_surface.py` (T201 print, PT018 assertion) | test_arch_public_surface.py:130 | n/a (other plan) | belongs to architecture-fitness-functions; session-retention files lint clean in isolation |

_No issues in session-retention scope._

### Tests
- Scoped: `uv run pytest tests/daemons/dream/test_housekeeping.py` — 20 passed (4 new prune_sessions + e2e + fixed timeout fixture). No mocks; only sanctioned `monkeypatch.setenv("CO_HOME")`.
- Full (excluding the 3 other-plan arch test files): 751 passed, 0 failed (`.pytest-logs/*-review-impl-full.log`). Arch files excluded because they are unrelated WIP that fails lint — not session-retention scope.

### Behavioral Verification
- `uv run co dream --help`: ✓ daemon command boots (status/start/stop/run) — `prune_sessions` runs inside `run_housekeeping`.
- `success_signal` TASK-1 verified: operator sets `CO_DREAM_SESSION_RETENTION_DAYS=30` → loaded config reflects `30`.
- `success_signal` TASK-3/TASK-4 verified by unit + e2e tests (mixed-age dir → only aged deleted; full pass persists `session_pruned` through disk round-trip).

### Overall: PASS
Session-retention implementation is correct, clean, and fully tested. One non-blocking caveat: the working tree is polluted with two other plans' uncommitted WIP — `/ship` must stage only the six session-retention files listed above.
