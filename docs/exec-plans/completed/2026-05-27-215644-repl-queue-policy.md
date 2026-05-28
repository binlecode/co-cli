# repl-queue-policy

## Context

Phase 3 — the final phase of the REPL input-queue arc, **narrowed at Gate 1 (2026-05-28) to the
bounded-queue fix only**. **Phase 1**
(`docs/exec-plans/completed/2026-05-23-151807-repl-input-queue.md`, shipped v0.8.260) built the FIFO
foundation; **Phase 2** (`docs/exec-plans/completed/2026-05-27-214118-repl-queue-ux.md`, shipped
v0.8.264) added the `/queue` management surface (list/clear/pop) + a head-item toolbar preview. This
phase adds a **bounded queue**: a config-gated cap with a drop policy so a runaway paste or a wall of
type-ahead can't grow the queue without limit.

**Gate 1 descope (2026-05-28).** The original Phase-3 charter also proposed collect mode,
`busy_input_mode` (interrupt), background auto-enqueue, and edit-before-send. All four were **deferred
at Gate 1** as parity/ergonomics-driven with no demonstrated need (auto-enqueue additionally raised an
always-on autonomy concern — it would auto-run a turn on background completion with no config gate).
They are recorded under "Out / Deferred" with rationale, to revisit only on real demand. The full
design detail for each (the interrupt `appendleft`+cancel mechanic, the auto-enqueue callback seam) is
preserved in this plan's git history (pre-2026-05-28 revision). What remains is the one item backed by
an actual defect: the unbounded queue.

### Code-accuracy verification (current anchors, re-read 2026-05-28)

- Queue + drain: `_ReplRuntime.queue: deque[str]` (`co_cli/display/_app.py:69`); `_drain_next`
  (`co_cli/main.py:497-504`) guards `should_exit`/empty, `popleft()`s one, repaints, arms next. The
  mid-turn enqueue branch (`_build_accept_handler`, `co_cli/main.py:474`; append at `:517-519`) is the
  seam this phase routes through a centralized `_enqueue` helper.
- Config: `Settings` (`co_cli/config/core.py:84`); nested sub-model groups each own an env-map module
  (e.g. `co_cli/config/dream.py`), registered in `nested_env_map` (`core.py:159-167`) and added as a
  `Field(default_factory=...)` (`core.py:88-95`). No `repl` group exists yet.
- Phase 1 key map (fixed): `Esc` = interrupt + advance queue; `Ctrl+C` = exit (double-press);
  mid-turn input = enqueue. This phase does not touch any key binding.

## Problem & Outcome

**Problem.** The Phase 1/2 queue is unbounded: it grows without limit. A runaway paste or a wall of
type-ahead can balloon it, flooding the user with turns they no longer want, with no lever to cap or
drop.

**Outcome.** The queue becomes **bounded**: a config-gated cap with a drop policy keeps it within a
fixed size. `repl.queue_cap` sets the limit (0 = unbounded, the default); `repl.drop_policy` chooses
which item goes when an enqueue would exceed it. A user who sets nothing sees zero behavior change.

**Failure cost.** Without bounding, a runaway paste or a wall of type-ahead floods the user with turns,
with no policy lever to cap or drop.

## Scope

**In (Phase 3, narrowed):**
- **Queue cap + drop policy** (config `repl.queue_cap: int = 0` [0 = unbounded], `repl.drop_policy:
  "oldest" | "newest" = "oldest"`): when an enqueue would exceed the cap, drop per policy and surface a
  one-line notice. Centralized in a single `_enqueue` helper that the accept_handler (and any future
  enqueue source) routes through.

**Out / Deferred (Gate 1 2026-05-28 — revisit on real demand):**
- **Collect mode** (`repl.collect_mode`, merge pending items into one turn) → deferred. Cheap but
  speculative; also carries a collect × queued `exit` footgun (a merged `exit` won't match the exit
  check).
- **`busy_input_mode` / interrupt** (cancel the active turn and run the new submission next) →
  deferred. Parity-driven (hermes/gemini); cheap on the existing cancel+arm foundation but no
  demonstrated need.
- **Background-event auto-enqueue** (synthesize a prompt on background-task completion) → deferred.
  Highest coupling (threads a callback through `spawn_task`/`_monitor` across 4 files) and would
  silently auto-run a turn on completion — an always-on autonomy increase. Revisit only if daemons
  need to inject prompts, and then behind a default-off gate.
- **Edit-before-send** (`/queue edit [n]`) → deferred. Requires changing `build_repl_app`'s signature
  and every integration harness for a fix-a-typo convenience.
- **`steer` mode**, `drop_policy: "summarize"`, reordering, per-item metadata, file-backed persistence
  → deferred (as in the original charter).

## Behavioral Constraints

C1. **`run_turn` stays single-turn (Phase 1 C1).** No policy logic in the agent loop. Cap and drop
live in the REPL layer (`main.py`). Each enqueued item is still one `user_input: str` to one `run_turn`.

C2. **Queue stays `deque[str]`.** Cap/drop operate on the existing string deque — no `QueueItem`
promotion. The reserved-option comment lives at `_app.py:62-63`; promotion remains out of scope.

C3. **Cap is enforced at enqueue, drop is policy-driven.** When `queue_cap > 0` and an append would
exceed it: `drop_policy="oldest"` → `popleft()` then append; `"newest"` → reject the new item. Either
way emit exactly one notice and one status repaint. `queue_cap=0` = unbounded (Phase 1/2 behavior, the
default). Blank input is dropped **first** (Phase 1) — a blank never counts against the cap.

C8. **All policy is config, default-off / default-Phase-1.** `queue_cap=0` and (inert at cap 0)
`drop_policy="oldest"` preserve current behavior. A user who sets nothing sees zero behavior change.
One `ReplSettings` group holds both fields.

## High-Level Design

```
 repl.* config (ReplSettings)            REPL layer (main.py)
 ─────────────────────────────           ────────────────────
 queue_cap: int            ───────────▶  _enqueue: blank-drop → cap check → drop → one repaint (C3)
 drop_policy: oldest|newest─┘
```

**Cap + drop.** A single `_enqueue(runtime, text, deps, on_status)` helper centralizes the append:
blank-drop **first** (a blank never counts against the cap), then cap check + drop policy (C3), then
`on_status()` — one repaint. The accept_handler's mid-turn append (`main.py:517-519`) routes through it,
so there is one enforcement point.

**Why one config group.** `ReplSettings` (`co_cli/config/repl.py`) holds `queue_cap`, `drop_policy` +
`REPL_ENV_MAP`; registered in `nested_env_map` and as a `Field(default_factory=ReplSettings)`. Mirrors
`DreamSettings` exactly (`co_cli/config/dream.py`).

## Tasks (Phase 3, narrowed)

### ✓ DONE TASK-1: `ReplSettings` config group

- files:
  - `co_cli/config/repl.py` (new) — `ReplSettings` (`queue_cap: int = 0` ge=0, `drop_policy:
    Literal["oldest","newest"] = "oldest"`) + `REPL_ENV_MAP`.
  - `co_cli/config/core.py` — import; add `repl: ReplSettings = Field(default_factory=ReplSettings)`
    (`:88-95`); register `"repl": REPL_ENV_MAP` in `nested_env_map` (`:159-167`).
  - `tests/test_flow_bootstrap_config_loading.py` (the project's config-loading test module).
- done_when: a config test asserts defaults (`queue_cap=0`, `drop_policy="oldest"`) and that env vars
  (`CO_REPL_QUEUE_CAP`, `CO_REPL_DROP_POLICY`) override via `fill_from_env`.
- success_signal: `repl.*` settings load from file + env with Phase-1-preserving defaults.
- prerequisites: none (Phase 2 shipped)

### ✓ DONE TASK-2: Centralized `_enqueue` + cap + drop policy

- files:
  - `co_cli/main.py` — extract a single `_enqueue(runtime, text, deps, on_status)` helper, in order:
    blank-drop **first** (a blank never counts against the cap), then cap check + drop policy (C3), then
    exactly one `on_status()` repaint; route the `accept_handler` mid-turn append (`:517-519`) through it.
  - `tests/test_flow_chat_loop.py` — new tests.
- done_when: `test_queue_cap_drops_oldest` — cap=2, three mid-turn submits → queue holds the **last two**
  (oldest dropped), one notice emitted; `test_queue_cap_newest_rejects` — `drop_policy="newest"`, cap=2,
  third submit rejected, queue holds the **first two**; `test_cap_zero_unbounded` — cap=0 enqueues all
  (Phase 1 regression). Asserted on `runtime.queue` via the real accept_handler.
- success_signal: a runaway paste can't grow the queue past the cap; the user sees which items dropped.
- prerequisites: TASK-1

## Testing

Per `.agent_docs/testing.md` and project memory:
- `pytest -x` (fail-fast); rerun `--lf` after fixing. Runs piped to `.pytest-logs/`.

Coverage focus (critical functionality, not count):
- Cap + drop: oldest-drop, newest-reject, cap=0 unbounded regression (C3).
- Blank-drop precedes cap (a blank never counts against the cap).

## Open Questions

Resolved at Gate 1 (2026-05-28):
- Scope narrowed to the bounded-queue fix. Collect / `busy_input_mode` / auto-enqueue /
  edit-before-send all deferred (see "Out / Deferred") — parity/ergonomics-driven with no demonstrated
  need; auto-enqueue also raised an always-on autonomy concern. The one item backed by a real defect
  (unbounded queue) ships.

## Final — Team Lead

Plan approved at Gate 1 (2026-05-28), narrowed to TASK-1 + TASK-2 (bounded queue).

> Run: `/orchestrate-dev repl-queue-policy`

## Delivery Summary — 2026-05-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | config test asserts defaults (`queue_cap=0`, `drop_policy="oldest"`) + env overrides (`CO_REPL_QUEUE_CAP`, `CO_REPL_DROP_POLICY`) via `fill_from_env` | ✓ pass |
| TASK-2 | `test_queue_cap_drops_oldest` (oldest dropped, 1 notice), `test_queue_cap_newest_rejects` (third rejected), `test_cap_zero_unbounded` (Phase 1 regression) on `runtime.queue` via the real accept_handler | ✓ pass |

**Tests:** scoped — 32 passed, 0 failed (`test_flow_chat_loop.py`, `test_flow_bootstrap_config_loading.py`, `test_repl_input_queue.py`, `test_repl_terminal_owner.py`)
**Doc Sync:** fixed — config.md (`repl.*` group table + `ReplSettings`/`DreamSettings` sub-model rows); tui.md (`_enqueue` blank→cap→drop behavior paragraph + `repl.*` Config rows)

**Files touched beyond plan scope (signature change ripple):**
- `tests/integration/test_repl_input_queue.py`, `tests/integration/test_repl_terminal_owner.py` — `_build_accept_handler` gained a `deps` param; both existing call sites updated to pass `deps`.

**Overall: DELIVERED**
Bounded queue ships: `_enqueue` centralizes blank-drop → cap → drop-policy with one repaint; `repl.queue_cap=0` default preserves Phase 1/2 behavior. Adding `deps` to `_build_accept_handler` rippled to two integration call sites (fixed).

## Implementation Review — 2026-05-28

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | config test asserts defaults (`queue_cap=0`, `drop_policy="oldest"`) + env overrides via `fill_from_env` | ✓ pass | `co_cli/config/repl.py:13-19` — `ReplSettings` (`queue_cap: int = 0` ge=0, `drop_policy: Literal["oldest","newest"]`); registered `core.py:97` (Field) + `core.py:170` (`nested_env_map["repl"]`). Tests `test_flow_bootstrap_config_loading.py:111,119` pass via real `load_config`. Faithful mirror of `DreamSettings`. |
| TASK-2 | oldest-drops / newest-rejects / cap-0-unbounded on `runtime.queue` via real accept_handler | ✓ pass | `co_cli/main.py:485-516` — `_enqueue`: blank-drop first (`:500`), cap check `>= cap` so queue never exceeds cap (`:503`), oldest pops-then-appends (`:505-506`), newest rejects (`:511`), exactly one `on_status()` (`:516`). Single enqueue point: only caller is accept_handler `main.py:563`; `_drain_next` uses `popleft()` (dequeue). Tests `test_flow_chat_loop.py:498,529,560` pass with real `CoDeps`/`make_settings`, asserting `runtime.queue` + real console output. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Name collision: TASK-2's `_preview` introduced a second module-level `_QUEUE_PREVIEW_BUDGET = 60`, shadowing the Phase-2 `= 30` used by `_queue_head_preview`. At runtime the toolbar head-preview silently truncated to 60 instead of 30, failing `test_display.py::test_build_status_snapshot_queue_head_preview_truncated_past_budget` | `co_cli/main.py:474` | blocking | Renamed the new constant to `_QUEUE_NOTICE_BUDGET` (it backs drop/reject notices); Phase-2 `_QUEUE_PREVIEW_BUDGET = 30` left intact |

### Tests
- Command: `uv run pytest -x -q` (full suite, after fix)
- Result: 640 passed, 0 failed
- Log: `.pytest-logs/*-review-impl-full2.log`
- Note: the working tree carries substantial unrelated changes from other in-flight plans; the full suite is green across all of them.

### Behavioral Verification
- No `co status` command in this project (commands: `chat`/`tail`/`trace`/`dream`). User-facing surface = config loading + REPL queue.
- Config loading (TASK-1 `success_signal`): verified `repl.*` loads from defaults (`0`/`oldest`), file (`3`/`newest`), and env override beating file (`9`/`oldest`) via real `load_config`.
- Queue cap (TASK-2 `success_signal`): verified by green `test_queue_cap_drops_oldest` / `test_queue_cap_newest_rejects` asserting the queue stays at cap and the drop notice prints to the real console; real-LLM integration tests (`test_repl_input_queue.py`) confirm enqueue/drain under a live turn.

### Overall: PASS
Both tasks implemented to spec. One real regression (a `_QUEUE_PREVIEW_BUDGET` name collision introduced by TASK-2's `_preview`) was caught and fixed; full suite green, lint clean.
