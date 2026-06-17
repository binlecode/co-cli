# Loop Timing Visibility

## Context

A turn is an agentic loop driven by one `run_stream_events` call (`co_cli/context/orchestrate.py:398`),
whose event stream interleaves reasoning bursts, tool calls, tool results, and text — potentially many
cycles, plus re-entry via `_run_approval_loop` and HTTP-retry/overflow paths. The TUI surfaces timing
unevenly across these phases:

Current state (verified against source):
- **Pre-first-token:** `frontend.on_status("Co is thinking...")` (`orchestrate.py:768`) — a static `dim`
  status string (`display/core.py:422-427`), no elapsed counter.
- **Reasoning:** `StreamRenderer` paints a live `Thinking… Ns` header driven by a 1s wall-clock ticker
  (`display/stream_renderer.py:97-134`), then commits a durable `Thought for Ns` footer on flush
  (`stream_renderer.py:163-176`). ✓ Already good.
- **Tool executing:** `on_tool_start` records the label in `_active_tools` and paints it via
  `_refresh_tool_inflight` (`display/core.py:404-410, 381-387`); a `tool_progress_callback` seam exists
  (`orchestrate.py:285-287`) but no start time is recorded and the in-flight panel does not tick.
- **Tool complete:** `on_tool_complete` closes the panel and renders the result payload
  (`display/core.py:417-420`) — no duration shown.
- **Post-tool-result resume:** between `FunctionToolResultEvent` (`orchestrate.py:320`) and the next
  `PartStartEvent`, nothing ticks — the model re-ingests tool output with no heartbeat.

Internally the timing already exists as spans (`push_span`, viewable via `co trace`), but it is not
surfaced in the interactive panel.

### Peer survey (hermes-agent, opencode)

| Behavior | hermes-agent | opencode | co (now) |
|---|---|---|---|
| Live tool-exec timer | ✅ `(7.3s)` ~150ms repaint (`cli.py:4096`) | ❌ static spinner | ❌ |
| Committed tool duration | ✅ per-tool completion line, always emitted (`tool_executor.py:702`, `display.py:899`) | ❌ none generic — only message/subagent span (`index.tsx:2264`); the `1623` timer is the **reasoning** part, not a tool | ❌ |
| Committed reasoning duration | ⚠️ live status-bar timer, no footer | ✅ (`index.tsx:1596`) | ✅ `Thought for Ns` |
| Ticking pre-first-token | ⚠️ via turn-level status bar | ❌ static "Thinking" | ❌ static |
| Post-tool-resume heartbeat | ❌ silent | ❌ static spinner | ❌ |

Two takeaways: (1) hermes attaches committed tool duration to a **per-tool completion line that is
always emitted**, independent of the result payload (result is used only for failure detection,
`display.py:889`); opencode renders no generic per-tool duration at all. So the validated pattern is a
tool-line label, NOT a suffix on an optional result panel; live tool timer is likewise validated by
hermes. (2) A ticking model-wait status and a post-tool-resume heartbeat are
**beyond both peers** — defensible for local-model latency but not peer-driven. Hermes also suggests a
cleaner architecture: one turn-level wall-clock (`⏱` live → `⏲` frozen, `cli.py:3834`) that covers all
dead zones at once — but it assumes a persistent status-bar region, which co's scrollback-streaming
display (transient single `_inflight` string, `display/core.py:307-316`) does not have.

## Problem & Outcome

**Problem:** With a local Ollama model, tool execution is invisible — while a tool runs, the TUI shows a
static panel with no elapsed time, so the user cannot tell a working tool from a hung one, nor see how
much of a slow turn's wall-clock the tool consumed. (The adjacent model-re-ingestion dead zone is a
related but separate motivation, deferred to Tier 2 / OQ-1 — see below.)

**Outcome:** Tool execution shows a live elapsed timer while running and a committed duration when done,
giving a consistent timing vocabulary alongside the existing `Thinking… Ns` / `Thought for Ns`.

**Failure cost:** Without this, a user on a slow local model sees a frozen tool panel during one of the
longest gaps of a turn and cannot distinguish a working tool from a hung one — the exact failure mode the
existing reasoning ticker was built to prevent, left unaddressed for the tool phase.

## Scope

**In scope (Tier 1 — validated against peers):**
- Live elapsed timer on the in-flight tool line while a tool runs.
- Committed tool duration on every finished tool, as a standalone line independent of the result payload
  (not coupled to the string-only result panel — see High-Level Design step 3).
- Flip the default `reasoning_display` from `collapsed` to `full` so reasoning tokens stream visibly by
  default (paired visibility change — same "make the model's work visible" motivation as the tool timers).

**Tier 2 (divergent — decision required, see Open Questions, NOT yet committed to tasks):**
- Ticking pre-first-token and post-tool-resume model-wait status.

**Out of scope:**
- Persistent status-bar region / turn-level timer rearchitecture (hermes model) — see OQ-1.
- Changes to reasoning display (`Thinking… Ns` / `Thought for Ns` already correct).
- Token-rate / tps display.

## Behavioral Constraints

- Reuse the `_format_elapsed` helper for all elapsed labels — do not coin a second time-format helper
  (DRY; matches the existing `Ns` / `NmNs` vocabulary). It currently lives in `display/stream_renderer.py`
  as package-private; the consumer `display/core.py` is in the **same package** `co_cli/display/`, so the
  intra-package import keeps the underscore (the drop-underscore rule is cross-package only).
- Show the committed duration for **all** tools (no min-threshold), consistent with `Thought for Ns`.
- Use `time.monotonic()` for elapsed measurement (consistent with renderer).
- The tool ticker must degrade safely with no running loop (headless/sync callers), mirroring the
  renderer's `_start_ticker` loop-availability guard (`stream_renderer.py:113-125`).
- No import-time side effects; the ticker spawns at call time only.
- Surgical: touch the tool-display path only; do not alter reasoning or text surfaces.

## High-Level Design

Tool lifecycle currently flows orchestrator → `frontend.on_tool_start/on_tool_progress/on_tool_complete`
directly (the `StreamRenderer` is not involved in tool ids). So tool timing lives in the **display App**
(`display/core.py`), which owns `_active_tools`, `_refresh_tool_inflight`, the bound `_app`, and
`_invalidate()` — the same repaint mechanism the reasoning ticker ultimately drives.

1. **Record start:** in `on_tool_start`, stamp `time.monotonic()` per `tool_id` (parallel dict to
   `_active_tools`, or a small per-tool record).
2. **Live timer:** `_refresh_tool_inflight` appends ` (Ns)` per active tool from its start stamp.
   Because tool execution emits no stream deltas, a **single App-level 1s wall-clock ticker** (one
   asyncio task gated on "any tool active," modeled on `stream_renderer._tick` — NOT one task per tool)
   calls `_refresh_tool_inflight` while tools are in flight; it starts when the first tool opens and is
   cancelled when the last closes. Parallel in-flight tools share the one ticker.
3. **Commit duration:** in `on_tool_complete`, compute final elapsed and commit a tool-completion line
   carrying the duration for **every** tool — independent of the result payload. This is the
   peer-validated shape (hermes always emits a per-tool line; the duration is not gated on result type).
   The existing `_render_tool_panel` only commits a Panel for non-empty **string** results
   (`display/core.py:401`), so threading the duration into that panel would silently drop it for the
   common structured-result / empty-result tools — exactly the "is it hung?" gap this plan closes. Emit
   the committed `label (Ns)` line at the `on_tool_complete` call site regardless of whether a result
   panel also renders. Clear the start stamp.

The ticker reuses the loop-availability guard pattern so headless callers (no `_app`) skip it. **Teardown
invariant:** the ticker must be cancelled on every exit path — not only when `_active_tools` empties in
`_close_tool`, but also from `cleanup()` (the turn-teardown hook), since on error/interrupt a tool can be
in flight; an uncancelled ticker would repaint a torn-down region. Start stamps are also cleared on the
retry/overflow re-entry paths.

## Tasks

### ✓ DONE — TASK-1 — Tool start-time tracking + committed duration
- **files:** `co_cli/display/core.py`
- **done_when:** running `uv run co chat` and invoking **any** tool — including one whose result is
  non-string or empty (so no result Panel renders) — commits a completion line carrying the elapsed
  duration (`label (Ns)`); verified by an assertion at the `on_tool_start`→`on_tool_complete` boundary
  that the committed line carries the elapsed suffix for a tool with a non-string/empty payload (not only
  the string-result panel path). The per-tool start-stamp dict is also cleared in `cleanup()`
  (`display/core.py:632`) alongside `_active_tools`/`_tool_labels` so no stamp leaks on error/interrupt.
- **success_signal:** a completed tool commits a line reading e.g. `memory_search "x" (2s)` even when it
  returns structured (non-string) data.
- **prerequisites:** none

### ✓ DONE — TASK-2 — Live tool-exec ticker on the in-flight panel
- **files:** `co_cli/display/core.py`
- **done_when:** with a tool deliberately held in-flight, the in-flight panel's elapsed label advances
  on a ~1s cadence without any `on_tool_progress` call; verified at the integration boundary that
  repeated `_refresh_tool_inflight` invocations between start and complete yield monotonically
  increasing elapsed text, and that the ticker is a no-op when no app/loop is bound.
- **success_signal:** an in-flight tool shows `… (1s)`, `(2s)`, `(3s)` while waiting.
- **implementation notes (from C1 review):** a single App-level ticker gated on "any tool active" (not
  one per tool); cancelled both when the last tool closes in `_close_tool` and from `cleanup()` to avoid
  a leaked ticker repainting a torn-down region on error/interrupt with a tool in flight.
- **prerequisites:** TASK-1

### ✓ DONE — TASK-3 — Default reasoning_display to `full`
- **files:** `co_cli/config/core.py`, `docs/specs/core-loop.md`, `docs/specs/tui.md`
- **status:** ALREADY APPLIED — `DEFAULT_REASONING_DISPLAY = REASONING_DISPLAY_FULL` (`config/core.py:60`)
  and the four doc default-annotations synced (core-loop.md:198/399, tui.md:211/225-226/242). Dev should
  verify, not re-implement.
- **done_when:** a fresh `uv run co chat` with no `CO_REASONING_DISPLAY` / config override streams the raw
  reasoning body under the `Thinking… Ns` header and commits body + `Thought for Ns` (full-mode behavior);
  `off`/`collapsed` remain selectable via config, env, `--reasoning-display`, and `/reasoning`.
- **success_signal:** default-config turn shows streamed chain-of-thought, not a header-only line.
- **prerequisites:** none (independent of TASK-1/2)

## Testing

- `co_cli/display/` has functional display tests — add coverage asserting observable behavior only
  (committed label carries elapsed suffix; in-flight elapsed increases across ticks; headless no-op).
  No structural assertions (field/method existence) per testing.md.
- Manual: `uv run co chat`, trigger a tool, confirm live tick + committed duration.
- Manual (TASK-3): fresh `uv run co chat` with no override streams the raw reasoning body by default;
  `CO_REASONING_DISPLAY=collapsed` still yields header-only.
- Scoped run: `uv run pytest tests/display -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-display.log`
  (tail the log live).

## Open Questions

- **OQ-1 (architecture, Gate-1 decision):** Tier 1 fits co's existing transient `_inflight` model
  cleanly. Tier 2 (ticking model-wait status pre-token + post-tool-resume) is beyond both peers. Two
  ways to deliver it: (a) extend the existing transient `on_status` to tick in place — small, matches
  current architecture; or (b) adopt a hermes-style persistent turn-level timer region — cleaner, covers
  every dead zone at once, but introduces a new persistent status region into a scrollback display.
  **Decision needed before scoping Tier 2 into tasks.** Recommendation: ship Tier 1, defer Tier 2 to a
  follow-up plan unless the post-tool dead-zone is observed to matter in practice.

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | finished tool commits a duration line even for a non-string/empty result; start-stamp cleared in `cleanup()` | ✓ pass |
| TASK-2 | in-flight elapsed advances on ~1s cadence with no `on_tool_progress`; ticker is a no-op with no app/loop bound; cancelled in `_close_tool` (last tool) and `cleanup()` | ✓ pass |
| TASK-3 | default config streams reasoning body + `Thought for Ns`; `off`/`collapsed` still selectable | ✓ pass (verify-only — already applied) |

**Implementation:** all changes in `co_cli/display/core.py`. Added `_tool_started_at` start-stamp dict + a single App-level `_tool_ticker_task` gated on "any tool active" (modeled on the reasoning ticker). `on_tool_start` stamps `time.monotonic()` and starts the ticker; `_refresh_tool_inflight` appends a live `(Ns)` elapsed label per active tool; `on_tool_complete` commits a standalone `label (Ns)` line for every tool independent of the result payload; `_close_tool` stops the ticker when the last tool closes; `cleanup()` stops the ticker and clears stamps. Reuses the package-private `_format_elapsed` from `stream_renderer` (intra-package import keeps the underscore). TASK-3 confirmed already in place at `config/core.py:60`.

**Tests:** scoped (`tests/test_display.py`) — 27 passed, 0 failed. New functional coverage: committed duration line for a non-string result; in-flight elapsed advances across refreshes; ticker no-op without a running loop; ticker repaints while in flight then stops on completion.
**Doc Sync:** clean — no shared-module/public-API/schema change (internal display path only); TASK-3's spec annotations were already synced when the default was applied.

**Overall: DELIVERED**
All three tasks pass done_when, lint clean, scoped tests green.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev loop-timing-visibility`

## Implementation Review — 2026-06-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | finished tool commits a duration line even for a non-string/empty result; stamp cleared in `cleanup()` | ✓ pass | `core.py:469-475` commits `label (Ns)` from the popped start-stamp before `_render_tool_panel`, independent of result type; `core.py:693-695` clears `_tool_started_at` in `cleanup()`. Verified by `test_tool_complete_commits_duration_line_for_non_string_result`. |
| TASK-2 | in-flight elapsed advances ~1s with no `on_tool_progress`; no-op without app/loop; cancelled in `_close_tool` (last tool) + `cleanup()` | ✓ pass | `core.py:393-403` appends live `(Ns)`; single ticker `core.py:407-433` gated on `while self._active_tools`; `_start_tool_ticker` guards `get_running_loop()` (no-op headless); cancelled at `core.py:440-441` and `692`. Verified by ticker-repaint, advances-across-refreshes, and headless-no-op tests. |
| TASK-3 | default streams reasoning body + `Thought for Ns`; `off`/`collapsed` selectable | ✓ pass | `config/core.py:60` `DEFAULT_REASONING_DISPLAY = REASONING_DISPLAY_FULL`; `test_full_mode_commits_body_and_timer`. Already applied — verified, not re-implemented. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Structural assertion (`_tool_ticker_task is None`) violates testing.md functional-only | tests/test_display.py | minor | Strengthened to assert observable degraded behavior (label + elapsed suffix render without crash) |

_Notes (non-blocking, not changed — out of scope):_ `_format_elapsed` is now shared by two `co_cli/display/` modules; it stays in `stream_renderer.py` per the plan's explicit reuse-in-place decision (moving it is an unscoped refactor that would reverse the import direction). Intra-package import correctly keeps the underscore; stream_renderer imports core only under `TYPE_CHECKING`, so no runtime cycle (boot smoke confirms).

### Tests
- Command: `uv run pytest`
- Result: 764 passed, 0 failed
- Log: `.pytest-logs/20260617-*-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots — import + bootstrap graph loads (confirms the new `_format_elapsed` import has no runtime cycle)
- Tool-timing display surface is LLM-mediated (requires a live tool call in `co chat`); verified via functional tests driving `on_tool_start`/`on_tool_complete` directly — chat interaction non-gating
- `success_signal` TASK-1 (`label (Ns)` line for non-string result), TASK-2 (in-flight `(1s)(2s)(3s)` advance), TASK-3 (default streams body): all verified via functional tests

### Overall: PASS
Tool-exec live timer + committed duration land cleanly in the existing transient-inflight model; teardown invariant holds on every exit path; suite green, lint clean, one test strengthened.
