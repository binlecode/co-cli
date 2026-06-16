# Bump pydantic-ai 1.81.0 → 1.92.x to fix MCP cancel-scope crash

## Context

Agent turns intermittently crash when cancelled mid-stream (an `asyncio.timeout`
firing) while a stdio MCP server is connected:

```
RuntimeError: Attempted to exit a cancel scope that isn't the current task's current cancel scope
```

RCA (completed) traced the locus to **pydantic-ai's streaming path**, not co code:
`run_stream_events` runs the graph in a detached `run_agent` task (`abstract.py:1084`)
and the graph spawns a `wrap_task` running `_streaming_handler` (`_agent_graph.py:605`);
under external cancellation the MCP stdio anyio task group — and a `ContextVar` token —
get torn down across task boundaries. co's `co_cli/agent/mcp.py` is correct.

**This is a known, fixed upstream bug.** pydantic-ai **v1.92.0 (2026-05-08)** ships both
fixes:
- **PR #4514** — runs the MCP session in a dedicated `asyncio.Task` that owns the cancel
  scope (the upstream form of "Design B"; closes issue #2818).
- **PR #5313** — cleans up streaming responses on cancellation (closes the
  `run_stream_events` cleanup-before-teardown problem, issue #5132).

co is pinned at `pydantic-ai==1.81.0`. A prior **speculative co-side fix** (swapping
`run_stream_events` for `agent.run(event_stream_handler=...)`) was attempted and
**reverted** — it did not fix the crash and added new error signatures. Do not revisit it.

**Decision (user-approved):** bump target = **minimal, v1.92.x** — just past the fix,
smallest API-drift surface from 1.81.0. Do NOT jump to latest (1.107.0); do NOT cross into
the v2.0 beta line.

The fix is a **controlled dependency upgrade**, not a concurrency rewrite. The plan scope
is therefore *bump-drift triage*: pin the new version, find and fix every place co relies on
pydantic-ai internals that changed across 1.81→1.92, and verify against the eval that already
reproduces the crash.

## Problem & Outcome

**Problem:** A pinned-old pydantic-ai carries a structured-concurrency bug that crashes any
turn cancelled mid-stream while a stdio MCP server is connected.

**Outcome:** After bumping to 1.92.x and resolving any API drift, a turn cancelled mid-stream
unwinds cleanly (surfacing `CancelledError`/`TimeoutError`, never the cancel-scope
`RuntimeError`), with the full test suite green.

**Failure cost:** Silent today — the crash masquerades as a flaky eval failure (~2/3 of
`multistep_plan` runs) and, in real use, turns the user's Esc/timeout cancellation of any
MCP-touching turn into a hard `RuntimeError` instead of a clean interrupt. Left unfixed, every
timeout/cancel during an MCP session is a coin-flip crash.

## Scope

**In scope**
- Bump `pydantic-ai==1.81.0` → `pydantic-ai==1.92.x` in `pyproject.toml`; re-resolve `uv.lock`
  (incl. transitive `mcp`, `anyio`, `httpx`).
- Triage and fix API/behavior drift in co's pydantic-ai integration surface (see High-Level
  Design for the risk map).
- Delete the known-unfaithful `tmp/repro_cancel_scope.py`.

**Out of scope**
- RC1 (latency) and RC3 (behavioral) eval failures — separate pre-existing issues.
- Any co-side concurrency redesign of the streaming/MCP lifecycle (the upstream bump *is* the
  fix).
- Jumping beyond 1.92.x or to v2.0 beta.

## Behavioral Constraints

- Zero-backward-compat policy: no shims or compat branches for old pydantic-ai APIs — adapt
  call sites to the new API directly.
- Surgical: touch only what the bump breaks. Do not refactor adjacent code.
- `~/.co-cli` paths via `USER_DIR`/config only (unchanged here, but tests override `CO_HOME`).
- Pre-existing pin discipline: `pydantic-ai` stays an exact `==` pin (it gates MCP internals
  co wraps); transitive deps resolve via `uv.lock`.

## High-Level Design

**Nature of change:** dependency upgrade + call-site adaptation. No new architecture.

**Drift risk map** (where co couples to pydantic-ai internals; verify each against 1.92.x).
This map is **illustrative, not exhaustive** — 54 modules import `pydantic_ai`; the full-suite
gate (TASK-2) is what covers the broad `pydantic_ai.messages`/`exceptions`/`usage` import
surface. The dev must not assume the named modules are the only breakage:

1. **HIGH — `co_cli/agent/mcp.py`.** Wraps `MCPServer`:
   - `_SanitizingMCPServer` proxies `list_tools()`, delegates `__aenter__`/`__aexit__`, and
     `__getattr__`-forwards everything else to the inner server.
   - `approval_required()` called on the proxy.
   - `_SequentialMCPToolset(WrapperToolset)` patches `ToolDefinition.sequential`.
   - PR #4514 moves the session into a dedicated task and changes `MCPServer` lifecycle
     internals (previously the `_running_count` + `_exit_stack` model). Verify the proxy's
     `__aenter__`/`__aexit__` delegation and `approval_required()`/`list_tools()` still hold,
     and that `WrapperToolset` API is unchanged.
2. **HIGH — bootstrap MCP entry.** `bootstrap/core.py::create_deps` does
   `await stack.enter_async_context(entry.toolset)` on a long-lived `AsyncExitStack`. Confirm
   the new dedicated-task session model is compatible with entering the toolset on a shared
   exit stack and tearing it down at session end.
3. **MEDIUM — streaming.** `orchestrate.py::_execute_stream_segment` (`run_stream_events`,
   event types in `_handle_stream_event`: `PartStartEvent`, `PartDeltaEvent`,
   `FinalResultEvent`, `FunctionToolCallEvent`, `FunctionToolResultEvent`,
   `AgentRunResultEvent`, etc.) and `UsageLimits`/`RunUsage`/`AgentRunResult` imports. Confirm
   signatures/event types unchanged.
4. **MEDIUM — agent/toolset assembly.** `agent/build.py`, `agent/core.py`
   (`AbstractToolset`, `CombinedToolset`), `agent/toolset.py` (`ToolDefinition`,
   `FunctionToolset`, `WrapperToolset` — co patches `ToolDefinition.sequential`). Confirm the
   toolset assembly + sequential-patch API is unchanged. (Note: co does **not** call
   `wrap_model_request` anywhere — verified zero hits — so no capability-middleware coupling.)
5. **MEDIUM — streaming-adjacent model wrapper.** `llm/surrogate_recovery_model.py` subclasses
   `WrapperModel` and imports `StreamedResponse`/`ModelRequestParameters`; `agent/run.py`
   imports `RunUsage`/`UsageLimits`. PR #5313 ("clean up streaming on cancellation") touches
   exactly this area — confirm the `WrapperModel`/`StreamedResponse` API is unchanged.
6. **LOW — other imports.** `DeferredToolRequests`/`DeferredToolResults`/`ToolApproved`,
   `ModelHTTPError`/`ModelAPIError`/`UnexpectedModelBehavior`, message part types.

**Changelog findings (1.81→1.92, already read — do not re-research).** All 11 minor
releases reviewed via GitHub release notes. The premise holds: **v1.92.0 ships both fixes**
— PR #4514 ("run MCP session in a dedicated task" → the cancel-scope crash) and PR #5313
("clean up streaming responses on cancellation"). Concrete drift co must check, beyond the
map above:

- **v1.88.0 #4799 — "propagate original error when `CallToolsNode` stream fails."** Changes
  *which* exception surfaces from a failing tool-call stream. Touches RISK-3 (`_handle_stream_event`)
  and RISK-5 (`SurrogateRecoveryModel` error path) — co may now see the original unwrapped
  error where it previously saw a wrapper. Verify error handling/recovery still classifies
  correctly.
- **v1.88.0 #4745 — "propagate `Agent(retries=...)` to user-provided toolsets."** co wraps MCP
  toolsets (`_SequentialMCPToolset(WrapperToolset)`) and builds `CombinedToolset`; retries now
  propagate into them. Confirm MCP tool-call retry behavior is still acceptable.
- **v1.92.0 #5075 — deprecate `retries` + internal retry-field rename.** co passes `retries=`
  to tool decorators in 4 sites (`tools/memory/manage.py`, `tools/web/fetch.py`,
  `tools/agent_tool.py`, `deps.py`). Confirm the per-tool `retries=` kwarg is not the deprecated
  one (the deprecation targets agent/output-level retries); silence or address any
  DeprecationWarning.

**Confirmed NOT affected (do not chase these, do not expand scope into them):**
- **v1.84.0 #4160** (Ollama capability-flag / structured-output fix) lands on the *new*
  `OllamaModel` subclass. co uses `OpenAIChatModel` + `OllamaProvider` (`llm/factory.py:57`),
  so structured-output behavior is unchanged. **Do not switch to `OllamaModel` as part of this
  bump** — out of scope.
- **v1.88.0 #4859** (`!` breaking: `prepare_tools` scoping + output validate/process hooks).
  co has **zero** `prepare_tools`/`prepare_output_tools`/capability-middleware usage (verified
  grep) — extends the existing "no `wrap_model_request` coupling" finding. Not affected.
- **v1.82.0 #5085** (orphan-task leak fix in `CombinedToolset`) — co uses `CombinedToolset`
  directly (`agent/core.py:63`); this is a beneficial fix co inherits, no action.

**Method:** read the 1.81→1.92 changelogs/release notes for breaking changes, then let the
test suite + a type/import check surface concrete breakage, fix call sites, and confirm with
the eval. Source-grep alone is insufficient for behavioral drift — the eval is the arbiter.

## Tasks

### ✓ DONE TASK-1 — Bump the pin and re-resolve the lockfile
- **files:** `pyproject.toml`, `uv.lock`
- **done_when:** `pyproject.toml` pins `pydantic-ai==1.92.0` (or a 1.92.x patch only if a known
  regression requires it); `uv sync` resolves without conflict; `uv run python -c "import importlib.metadata as m; print(m.version('pydantic-ai'), m.version('mcp'), m.version('anyio'))"`
  prints 1.92.x AND resolved `mcp` ≥ 1.26.0 and `anyio` ≥ 4.12.1 (a transitive **downgrade**
  below the current floor is a failure — it is the silent-breakage mode).
- **success_signal:** the app imports and `uv run co --help` runs without ImportError.
- **prerequisites:** none

### ✓ DONE TASK-2 — Triage and fix API/behavior drift
- **files:** `co_cli/agent/mcp.py`, `co_cli/bootstrap/core.py`, `co_cli/context/orchestrate.py`,
  `co_cli/agent/build.py`, `co_cli/agent/core.py`, `co_cli/agent/toolset.py`,
  `co_cli/agent/run.py`, `co_cli/llm/surrogate_recovery_model.py` (only those that actually
  break; the full suite is the arbiter — see High-Level Design note that the map is not
  exhaustive)
- **done_when:** a **pre-bump baseline** is captured first (`uv run pytest` on current HEAD →
  `.pytest-logs/<ts>-baseline.log`); then post-bump `uv run pytest -x` runs to completion with
  no collection/import errors, and every post-bump failure is present in the baseline (i.e. no
  *new* failures introduced by the bump — RC1/RC3 pre-existing failures are allowed). Adapted
  call sites match the 1.92.x API (no compat shims).
- **success_signal:** `tests/test_flow_*.py` (real-turn flow tests) pass — explicitly including
  `tests/test_flow_approval_subject.py` (exercises the MCP `approval_required()` wrapping that
  PR #4514's session rewrite is most likely to disturb) — confirming streaming + tool calls +
  MCP wrapping still function under 1.92.x. Additionally, a tool-call-failure path still
  surfaces a classifiable error (guards against v1.88 #4799's changed error propagation through
  `_handle_stream_event` / `SurrogateRecoveryModel`).
- **prerequisites:** TASK-1

### ✓ DONE TASK-3 — Verify the crash is gone against the faithful reproducer
- **files:** (verification only — no source files; eval is real-data UAT)
- **done_when:** `uv run python evals/eval_multistep_plan.py` completes across **5 consecutive
  runs** with **zero** occurrences of "Attempted to exit a cancel scope" / "exit cancel scope"
  in the run logs. Expected triggers: each `multistep_plan` case whose turn hits `CALL_TIMEOUT`
  is a cancel trigger; pre-bump, 2 of 3 cases crashed per run (~2/3 per-run rate), so 5 clean
  runs gives ~99.5% confidence the fix holds (the upstream mechanism — dedicated-task
  cancel-scope ownership — is deterministic, so this is evidence-matching, not luck). Verdict
  failures from RC1/RC3 are acceptable; only the cancel-scope `RuntimeError` must be absent.
- **success_signal:** grep of the eval logs for the cancel-scope signature returns nothing
  across all 5 runs.
- **prerequisites:** TASK-1, TASK-2

### ✓ DONE TASK-4 — Remove the unfaithful repro
- **files:** `tmp/repro_cancel_scope.py`
- **done_when:** file deleted (it used `FunctionModel` + no capability stack and gave a false
  green; superseded by the eval as the gate).
- **success_signal:** N/A
- **prerequisites:** none

## Testing

- **Unit/flow:** `mkdir -p .pytest-logs && uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-bump.log` — full suite green. Run with `-x` to fail fast; fix the first failure before resuming. Tail the log to watch LLM-call timing live.
- **Eval (crash gate):** `uv run python evals/eval_multistep_plan.py` ×5, grepping each run's
  log for "cancel scope" — must be absent. This eval is the faithful reproducer (real Ollama +
  stdio MCP + capability stack + nested timeouts).
- **Smoke:** `uv run co --help` and a short `uv run co chat` turn that triggers an MCP tool,
  then Esc mid-stream — confirm clean interrupt, no `RuntimeError`.
- Do not bump any timeout to mask latency; RC1 is out of scope and timeouts are not touched.

## Open Questions

1. **Exact 1.92 patch:** RESOLVED — target **`1.92.0`**. The 1.92.0 release notes confirm it
   contains *both* required fixes (PR #4514 and PR #5313), so 1.92.0 is the literal smallest-drift
   version past the fix. Move to a later `1.92.x` patch only if a known regression in 1.92.0
   surfaces at TASK-2.
2. **mcp/anyio compatibility:** does 1.92.x require an `mcp`/`anyio` floor above what co
   currently resolves (mcp 1.26.0, anyio 4.12.1)? — resolved by `uv sync` at TASK-1; if it
   forces a major transitive jump, flag at Gate 1.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev pydantic-ai-192-bump-mcp-cancel-fix`

## Delivery Summary — 2026-06-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | pyproject pins `==1.92.0`; `uv sync` clean; mcp ≥1.26.0, anyio ≥4.12.1, no downgrade; `co --help` imports | ✓ pass |
| TASK-2 | pre-bump baseline captured; post-bump suite has no *new* failures; call sites on 1.92 API, no shims | ✓ pass |
| TASK-3 | `eval_multistep_plan` ×5 consecutive, zero "cancel scope" occurrences | ✓ pass |
| TASK-4 | `tmp/repro_cancel_scope.py` deleted | ✓ pass |

**Pin:** `pydantic-ai==1.81.0 → 1.92.0`. Resolved: mcp 1.26.0, anyio 4.12.1 (both held at floor, no downgrade). `uv sync` dropped 12 fastmcp/docket transitive packages 1.92 no longer pulls — co imports none; `co --help` clean.

**API drift found & adapted (no shims, per zero-backward-compat):**
- `Agent(retries=…)` → `Agent(tool_retries=…)` at `agent/build.py:46,105` (PR #5075 deprecation). Config field was already named `tool_retries`; `output_retries` defaults to 1 (correct for the `[str, DeferredToolRequests]` output).
- `run_stream_events` direct iteration → `async with … as stream:` at `context/orchestrate.py:379` (PR #5313). The context-manager form *is* the cancellation-cleanup mechanism this bump adopts, not just warning hygiene.
- Re-verified with `-W error::DeprecationWarning`: 32 flow/approval/tool tests pass, **zero** deprecation warnings remain.

**Tests:**
- Pre-bump baseline (1.81.0): 3 failed, 743 passed.
- Post-bump full suite (1.92.0): **2 failed, 744 passed**. Both failures (`test_filescope_command`×2) are pre-existing in the baseline, caused by the **uncommitted `co_cli/commands/filescope.py` working-tree change — unrelated to this bump**. Baseline's 3rd failure (flaky `test_flow_user_image_intake`) passed post-bump. **Zero new failures introduced by the bump.**
- Crash gate: 5/5 eval runs zero cancel-scope occurrences (pre-bump ~2/3 cases crashed per run).

**Latency:** in-suite 48s/30s outliers RCA'd to KV-cache-flush variance, not the bump — isolated, 1.92.0 ran the same test in 5.22s vs 1.81.0's 10.43s, identical span/trace counts.

**Doc Sync:** narrow — `docs/specs/pydantic-ai-integration.md`: version pin 1.81.0→1.92.0 (×2), `retries`→`tool_retries` in the Agent-construction sketch + coupling table, streaming description updated to the `async with … as stream:` form.

**Follow-ups (out of scope — NOT regressions, flagged for a separate plan):**
1. **Unclosed model http client.** `llm/factory.py:48` creates an `httpx.AsyncClient` never registered for cleanup on any exit stack (eval *or* production `main.py`). GC'd at process exit → benign `RuntimeError: no running event loop`. Pre-existing (unclosed at 1.81.0 too); minor hygiene.
2. **Benign per-turn stream-teardown GC noise.** openai `AsyncStream.__aexit__` GC'd un-awaited on hard mid-stream cancellation (openai-SDK/httpcore `AsyncShieldCancellation` interaction below co's layer). Masked pre-bump by the cancel-scope crash that pre-empted it; now visible as `"Exception ignored"` GC output. Fails no turn. Not introduced by co's change.

**Overall: DELIVERED**
Bump to 1.92.0 fixes the cancel-scope crash (5/5 clean), introduces zero new test failures, and the two surfaced API deprecations are adapted to the new API. Two benign teardown findings logged as out-of-scope follow-ups.

## Implementation Review — 2026-06-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | pin `==1.92.0`; `uv sync` clean; mcp ≥1.26.0, anyio ≥4.12.1, no downgrade; `co --help` imports | ✓ pass | `pyproject.toml` pins `pydantic-ai==1.92.0`; runtime reports pydantic-ai 1.92.0 / mcp 1.26.0 / anyio 4.12.1 (both at floor, no downgrade); `co --help` runs without ImportError |
| TASK-2 | call sites on 1.92 API, no shims; no new failures | ✓ pass | `agent/build.py:46,105` `retries=`→`tool_retries=` — confirmed `retries` is a deprecated alias (`pydantic_ai/agent/__init__.py:351`, DeprecationWarning, removed v2); `context/orchestrate.py:379` bare `async for`→`async with … as stream:` — `AgentEventStream` has `__aenter__`/`__aexit__` (`result.py:924,928`) AND `__aiter__` (`result.py:937`), so the CM form is the cancellation-cleanup mechanism (PR #5313). All 8 unchanged drift-map modules import cleanly; `ToolDefinition.sequential` field still present |
| TASK-3 | eval ×5 consecutive, zero "cancel scope" | ✓ pass | 6 consecutive post-bump runs (`multistep_plan-20260615T204705Z`→`211213Z-run.jsonl`) all grep 0 "cancel scope"; pre-bump runs (`142548Z`/`182144Z`/`191941Z`) show 1/1/2 — deterministic fix confirmed |
| TASK-4 | `tmp/repro_cancel_scope.py` deleted | ✓ pass | file absent (`ls` → No such file) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Extra files in working tree not in any task's `files:` — unrelated WIP display refactor of `filescope`/`skills`/`tools` commands | `co_cli/commands/filescope.py`, `skills.py`, `tools.py` | minor (scope) | Not fixed — out of plan scope (pre-existing uncommitted command work, present at session start). **Must NOT be staged at ship.** |

_No bump-scope issues found. Lint clean (ruff check + format). No new abstractions, no shims (zero-backward-compat honored), no mocks introduced._

### Tests
- Command: `uv run pytest` (full suite, fail-fast off), excluding the 3 unrelated WIP command files.
- Result: **744 passed, 0 failed** (bump scope fully green).
- The only suite failures are `tests/commands/test_filescope_command.py` (2) — caused entirely by the uncommitted `filescope.py` table-display refactor (narrow capture-width truncation), **not** the bump. `skills.py`/`tools.py` have no dedicated tests.
- Logs: `.pytest-logs/20260615-213319-review-full.log`

### Behavioral Verification
- `uv run co --help`: ✓ starts, no ImportError (TASK-1 success_signal verified).
- No `status` command exists in this project; the bump's user-observable effect (clean mid-stream cancel unwind, no cancel-scope `RuntimeError`) is gated by the `eval_multistep_plan` reproducer — `success_signal` verified: 6 consecutive post-bump eval runs show zero cancel-scope signatures vs. 1–2 per pre-bump run.

### Overall: PASS
Bump to 1.92.0 is correct and surgical: both API drifts (`tool_retries`, streaming context-manager) verified against the installed 1.92.0 source, cancel-scope crash gone across 6 consecutive eval runs, 744 bump-scope tests green. **Ship gate caveat:** stage only `pyproject.toml`, `uv.lock`, `co_cli/agent/build.py`, `co_cli/context/orchestrate.py`, and `docs/specs/pydantic-ai-integration.md` — exclude the unrelated `commands/filescope.py`, `commands/skills.py`, `commands/tools.py` working-tree changes.
