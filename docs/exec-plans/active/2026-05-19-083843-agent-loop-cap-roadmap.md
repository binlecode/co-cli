# agent-loop-cap-roadmap

> Meeting briefing — summary of recent agent-loop and review-counter work,
> open plans, and deferred items.

## Status overview

Three plans plus three deferred items, organized on three orthogonal axes
(cost / safety / resource / cadence).

| Plan / Item | Axis | Status |
|---|---|---|
| `2026-05-18-234544-agent-tool-concurrent-default.md` | resource | **DONE** — T1-T6 ✓ |
| `2026-05-19-080633-session-review-counter-simplify.md` | cadence | **pending** — Gate 1 |
| Iteration cap per turn (50 Ollama, TBD Gemini) | cost | not yet planned |
| Tool-call cap per iteration (~10, error-back-to-LLM) | safety | not yet planned |
| Inline-tool-use reset of review counter | cadence | deferred |
| Per-tier counter split (hermes-style turn vs iter) | cadence | deferred |

## What's done (resource axis)

`agent-tool-concurrent-default`:

- `is_concurrent_safe` default flipped `False → True` (matches pydantic-ai
  upstream default)
- `is_read_only=True` auto-implies concurrent-safe (no longer requires both
  flags)
- 3 tools explicitly opt out: `code_execute`, `file_write`, `file_patch`
- Dispatch backstop: `_MAX_PARALLEL_TOOL_WORKERS = 10` via per-session
  `asyncio.Semaphore` on `CoDeps`, shared by reference into forked daemon
  agents
- Tests + spec sync

Net: 33 tools' redundant flags can be cleaned up as a follow-up sweep
(deferred, optional).

## What's pending (cadence axis)

`session-review-counter-simplify`:

- Drop the `ToolCallPart` filter from the session-review iteration counter
  in `orchestrate.py:406-410`
- Rename `tool_iterations` → `iterations` on `_TurnState` and `TurnResult`
- Bump `review_nudge_interval` default `5 → 10` (matches hermes)
- Chat-only sessions begin triggering memory harvest (was: silently
  zero-counted, never fired)
- Source-grounded: in both co-cli and hermes a text-only turn produces
  exactly 1 LLM iteration, so the simpler counter is well-defined

Awaiting Gate 1.

## What's not yet planned

### Iteration cap (cost ceiling)
- Default: 50 for Ollama, ~100 for Gemini
- Unit: count of all `ModelResponse`s per user turn (text + tool-emitting)
- Enforcement: fail-hard, stop the turn when exceeded
- Aligns with hermes `max_iterations=90`, letta `max_steps=10`, autogen
  `max_tool_iterations=1` — the production cost cap pattern
- Open: per-provider defaults, where to wire (orchestrate.py?), exit
  behavior when hit (return partial response? error?)

### Tool-call cap per iteration (safety ceiling)
- Default: ~10 `ToolCallPart`s per `ModelResponse`
- Enforcement: reject the whole batch pre-dispatch, error back to LLM with
  a clear "reduce to ≤N" message
- Loop guard: hard-stop the turn after 2 consecutive cap-exceeded
- No peer enforces this — would be co-original. Anthropic/OpenAI APIs
  expose binary `disable_parallel_tool_use` / `parallel_tool_calls=false`,
  not a numeric cap.

## Deferred (cadence axis follow-ups)

### Inline-tool-use reset
- When foreground agent calls `memory_manage` / `skill_manage` inline,
  reset `iterations_since_review = 0`
- Avoids redundant reviews after foreground already did the harvest
- Hermes does this at `run_agent.py:9173-9177, 9523-9527`
- ~5 LOC change; small follow-up plan worthwhile

### Per-tier counter split (hermes parity)
- Memory counter: bumped per user turn, default 10, fires memory-only review
- Skill counter: bumped per LLM iteration, default 10, fires skill-only
  review
- Reviewer prompt selects from memory-only / skill-only / combined variants
- Larger refactor: `session_review.py`, `session_review_prompts.py`,
  `main.py`, `deps.py`, spec
- Rationale: memory captures user-message-driven signal (preferences,
  corrections); skill captures tool-activity-driven signal (procedures).
  Different units fit different signals.

## Key design decisions captured

1. **Filter the LLM-iteration counter, or not?** Decision: don't filter
   (drop the `ToolCallPart` filter). Hermes ships this at scale; co-cli's
   filter silently zero-counts chat-only sessions, suppressing memory
   harvest.

2. **What's the right cap unit per axis?**
   - Cost → iterations (per turn)
   - Safety → tool calls per iteration
   - Resource → concurrent workers (already done)
   - Cadence → iterations between reviews (this plan)

3. **Per-tool concurrent-safety: default to safe or strict?** Decision:
   default to safe (`True`), aligning with pydantic-ai upstream and the
   empirical 80% opt-in rate. `ResourceLockStore` catches the few unsafe
   tools that get past the explicit opt-out flag.

4. **Read-only as a shortcut.** `is_read_only=True` now auto-implies
   `is_concurrent_safe=True` — no need to repeat both flags.

5. **Dispatch backstop.** 10 parallel workers max, per-session
   `asyncio.Semaphore`, shared into forked daemon agents so total session
   concurrency is bounded.

## Open questions for next meeting

1. **Iteration cap defaults — which providers?**
   - Ollama: 50 (proposed)
   - Gemini: 100 (proposed)
   - Other providers we'll support?

2. **Tool-call-per-iteration cap behavior on exceeded?**
   - Reject + error-back-to-LLM (proposed)
   - Or: silently truncate to the first N (rejected as dishonest)
   - Or: re-prompt with "reduce to N" feedback (subset of "error back")

3. **Hard-stop after 2 consecutive cap-exceeded — right threshold?**
   - 2 (proposed) — gives the LLM exactly one retry after the first
     rejection
   - 1 — strictest, no retry
   - 3 — more forgiving

4. **Per-tier counter split — when?**
   - Now alongside the simplification — but doubles the scope
   - After the simplification ships — incremental, safer
   - Recommendation: after, as a follow-up plan

5. **Inline-tool-use reset — same plan as simplification or follow-up?**
   - Same plan: 5 LOC addition, low risk
   - Follow-up: cleaner reviewable diff
   - Recommendation: follow-up plan, but lands soon after simplification

## Plan files referenced

- `docs/exec-plans/active/2026-05-18-234544-agent-tool-concurrent-default.md`
  (DONE, awaiting ship)
- `docs/exec-plans/active/2026-05-19-080633-session-review-counter-simplify.md`
  (pending Gate 1)

## Source code anchors

- `co_cli/tools/agent_tool.py:28` — `is_concurrent_safe` flag
- `co_cli/context/orchestrate.py:406-410` — iteration counter (filter to drop)
- `co_cli/main.py:269-300` — `_post_turn_hook` (post-turn review trigger)
- `co_cli/skills/session_review.py:56-70` — `SESSION_REVIEW_SPEC`
- `co_cli/skills/curator.py:208` — `CURATOR_SPEC`
- `hermes-agent/run_agent.py:10193, 10474, 10417` — nudge counters and
  iteration loop
- `pydantic-ai/.../tools.py:397,506,636` — upstream `sequential=False`
  default

## Research backing

- `docs/reference/RESEARCH-self-improvement-architecture.md` — full
  architectural comparison of co-cli vs hermes self-improvement runners
  (session reviewer, curator, dream), with the Option A vs Option B
  decision recorded.
