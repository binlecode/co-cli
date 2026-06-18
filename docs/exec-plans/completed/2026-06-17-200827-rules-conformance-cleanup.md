# Rules Conformance Cleanup — timeout unit-suffix drift (R9)

Task type: refactor — conformance rename. Behavior-preserving except one model-facing tool-param schema change (called out below).

## Context

Whole-codebase audit (`/audit-conformance`, scope `co_cli/`, 576 import edges scanned). The tree is in good shape — R1/R4/R5 were just drained (`97e3db1c`, `88df9b26`, `e99a6e67`), and this audit confirms **R1, R3, R6, R7, R10, R11, R12 are clean** (no read-confirmed findings). Two over-flagged subtraction candidates were refuted on blind cold-read (`cleanup_incomplete` is read at `tools/tasks/control.py:211` + a test; `status`/`exit_code` encode distinct facts).

The one coherent, **recurring-class** residue is **R9 naming drift — duration fields/params/constants/keys that omit the `_seconds` unit suffix** (`.agent_docs/code-conventions.md:32,42`). The convention is firmly established and followed elsewhere: `config/dream.py` (`review_timeout_seconds`, `max_pass_seconds`, `tick_interval_seconds`), `config/web.py` (`http_backoff_*_seconds`), and `config/shell.py` itself (`yield_window_seconds`). The drift sites sit *next to* compliant ones — `config/shell.py:90` compares `self.yield_window_seconds >= self.max_timeout` in a single expression, one field suffixed and one not.

This class recurs: `fd36effa` (`max_ctx → max_context_tokens`) and the `_SIZE`/unit-suffix rule in code-conventions are the same enforcement lineage. Fixing the whole timeout family in one pass closes it.

## Problem & Outcome

**Problem:** Several duration fields/params/constants in the shell + MCP surfaces are named bare `timeout`/`max_timeout`/`yield_window` with the unit (seconds) only documented in prose, violating `code-conventions.md:32,42` — directly adjacent to compliant `*_seconds` peers. The `yield_window` param sits in the *same `run_command` signature* as the `timeout` being renamed, and its own backing config field is already `yield_window_seconds`, so leaving it bare would reintroduce the exact one-suffixed-one-not drift at the param layer.

**Outcome:** Every co-owned duration identifier in scope carries its `_seconds` unit. Stdlib kwargs (`asyncio.timeout(...)`, `asyncio.wait_for(..., timeout=...)`, the MCP SDK's `timeout=` constructor arg) are **unchanged** — only our own fields/params/constants/env keys are renamed.

**Failure cost:** Low individually; the cost is class accretion — each bare-unit field added normalizes the next, and the audit shows the family already spans config + tools + agent.

## Scope

In scope (rename to `_seconds`, update all call sites + docstrings):
- `config/shell.py`: `DEFAULT_SHELL_MAX_TIMEOUT` → `DEFAULT_SHELL_MAX_TIMEOUT_SECONDS`; `ShellSettings.max_timeout` → `max_timeout_seconds`; env key `CO_SHELL_MAX_TIMEOUT` → `CO_SHELL_MAX_TIMEOUT_SECONDS`; the validator at `:90,93`.
- `config/mcp.py`: `MCPServerSettings.timeout` → `timeout_seconds` (no env-map entry; nested per-server config).
- `agent/mcp.py`: `MCPToolsetEntry.timeout` → `timeout_seconds`; all `cfg.timeout`/`entry.timeout` reads (`:95,99,111,130,141`) — the SDK kwarg `timeout=` keyword stays, only the RHS value reference changes.
- `tools/shell_backend.py`: `run_command(timeout=...)` and `_run_command_pty(timeout=...)` params → `timeout_seconds`; the sibling `run_command(yield_window=...)` param (`:71`) → `yield_window_seconds`; internal uses (`:91,92,94,127,128,144,209,213`). `asyncio.wait_for(..., timeout=window)` stdlib kwarg unchanged.
- `tools/shell/execute.py`: `shell_exec(timeout=...)` tool param → `timeout_seconds`; `ctx.deps.config.shell.max_timeout` → `max_timeout_seconds`; the local `yield_window` (`:100,108,126`) → `yield_window_seconds`; docstrings (`:50,62-63`, which currently say the non-existent `shell_max_timeout`) and the user-facing hint (`:148`).

Out of scope (deferred backlog, with counts):
- **R4 (1)** — `deps.py:22` imports `FileReadTracker` from `tools/`, the lone `deps→tools` module edge (root↔tools cycle). Borderline: the skill's layer rule explicitly carves out `deps`/`main` as composition roots, so this is a weak coupling, not a clear inversion. Relocating `file_read_tracker.py` to a foundational home (e.g. `co_cli/fileio/`) would eliminate it structurally — deferred, separate judgment call.
- **R2 (1)** — `background.py` `cleanup_incomplete` (bool) is always `cleanup_error is not None`; redundant same-lifecycle. NOT dead (surfaced at `tools/tasks/control.py:211` + asserted by a test), so collapsing changes a tool-result surface. Low value — deferred.
- **R9 data (1 class, ~18 files)** — canon `.md` frontmatter uses bare `created:` vs the `created_at` used everywhere in code. These are platform-core doctrine **data** files (not code fields), so it's a core-level review touching 18 souls/canon files; deferred per the platform-core change discipline.

## Behavioral Constraints

- **Zero-backward-compat:** `CO_SHELL_MAX_TIMEOUT` and `shell.max_timeout` / per-server `timeout` keys are renamed outright (no aliases). An operator `settings.json` carrying the old `shell.max_timeout` key trips `extra="forbid"` at config load; the old shell env var degrades silently once dropped from the env map. Check the operator's `~/.co-cli/settings.json` before ship (as the memory-lifecycle ship did).
- **Model-facing schema change (one):** `shell_exec`'s parameter rename `timeout → timeout_seconds` changes the tool JSON schema the model sees. This is intended and clearer; it is the only observable behavior change.
- **Surgical:** rename only; no logic changes, no default-value changes. Do not touch stdlib `timeout=` kwargs.
- `USER_DIR`/config-derived paths only; no new knobs.

## High-Level Design

Pure rename refactor, leaf-to-root so the tree stays importable:
1. **Config layer** (`config/shell.py`, `config/mcp.py`) — rename the fields, constant, and env key. These are the producers.
2. **Consumers** (`agent/mcp.py`, `tools/shell_backend.py`, `tools/shell/execute.py`) — update every read of the renamed config fields and the local params, plus docstrings.
3. Grep-sweep `co_cli/` + `tests/` for any remaining `\.max_timeout\b`, `\.timeout\b` on these types, `\byield_window\b`, and `CO_SHELL_MAX_TIMEOUT` to catch stragglers; update tests that reference the renamed identifiers (test edits are mechanical rename, not new behavior).

## Tasks

✓ DONE **TASK-1** — Rename shell timeout family to `_seconds`
- files: `co_cli/config/shell.py`, `co_cli/tools/shell_backend.py`, `co_cli/tools/shell/execute.py`
- done_when: `rg -n "\bmax_timeout\b|DEFAULT_SHELL_MAX_TIMEOUT\b|CO_SHELL_MAX_TIMEOUT\b|\byield_window\b" co_cli/` returns no hits without `_seconds`; `shell_exec` exposes `timeout_seconds`; `run_command`/`_run_command_pty` take `timeout_seconds` and `run_command` takes `yield_window_seconds`; stdlib `asyncio.wait_for(..., timeout=...)` untouched; `uv run python -c "from co_cli.config.core import load_config; load_config()"` succeeds; shell suite green.
- success_signal: shell tool config reads clean with explicit seconds units.
- prerequisites: none

✓ DONE **TASK-2** — Rename MCP timeout fields to `_seconds`
- files: `co_cli/config/mcp.py`, `co_cli/agent/mcp.py`
- done_when: `MCPServerSettings.timeout_seconds` and `MCPToolsetEntry.timeout_seconds` defined; `rg -n "\.timeout\b" co_cli/agent/mcp.py` shows only the SDK constructor kwarg `timeout=` (LHS) and `asyncio.timeout(...)`, never a `cfg.timeout`/`entry.timeout` value read; config loads; MCP/agent-build tests green.
- success_signal: MCP server config reads clean with explicit seconds units.
- prerequisites: none

✓ DONE **TASK-3** — Sweep remaining references + tests
- files: any test or module still referencing the old names (discovered by grep)
- done_when: `rg -n "max_timeout\b|CO_SHELL_MAX_TIMEOUT\b|\byield_window\b" co_cli/ tests/ | grep -v _seconds` returns nothing; `rg -n "\.timeout\b" tests/` shows no reference to the renamed config/entry fields; full suite green.
- success_signal: N/A (rename completeness).
- prerequisites: TASK-1, TASK-2

## Testing

- No new tests — this is a behavior-preserving rename. Existing shell/MCP/agent-build tests must stay green after the mechanical identifier update (functional behavior unchanged).
- Behavioral anchor: `uv run co --help` boots (import graph), config loads, and a `shell_exec` repro with `timeout_seconds=` runs. The model-facing schema change is verified by confirming `shell_exec`'s signature exposes `timeout_seconds`.
- Run scoped suites first: `uv run pytest tests/ -k "shell or mcp or background or agent" -x` piped to `.pytest-logs/`, then full suite at `/review-impl`.
- **Delivery callout:** before ship, check the operator's `~/.co-cli/settings.json` for `shell.max_timeout` / per-server `mcp.*.timeout` and rename them — `extra="forbid"` would otherwise fail config load on next run.

## Final — audit-conformance

> Gate 1 — PO + TL approve scope before proceeding.
> Right class (recurring unit-suffix drift)? Correct cap (timeout family only, R4/R2/canon deferred)?
> Once approved, run: `/orchestrate-dev rules-conformance-cleanup`

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | shell timeout family `_seconds`; `shell_exec` exposes `timeout_seconds`; `run_command`/`_run_command_pty` take `timeout_seconds`/`yield_window_seconds`; stdlib kwargs untouched; config loads; shell suite green | ✓ pass |
| TASK-2 | `MCPServerSettings.timeout_seconds` + `MCPToolsetEntry.timeout_seconds`; only SDK kwarg + `asyncio.timeout(entry.timeout_seconds)` remain; config loads; mcp tests green | ✓ pass |
| TASK-3 | no bare `max_timeout`/`CO_SHELL_MAX_TIMEOUT`/`yield_window` in `co_cli/`+`tests/`; no renamed-field refs left in tests | ✓ pass |

**Scope addition during dev:** `yield_window` (param in `run_command`/`_run_command_pty`, local in `execute.py`) renamed to `yield_window_seconds` alongside the timeout family — Gate-1 scope correction, already in the plan. Verified during sweep: the initial sweep regex (`\.timeout\b`) missed bare `timeout=` call sites in `tests/test_flow_shell.py`; caught and fixed (all `run_command`/`shell_exec` call sites now `_seconds`).

**Tests:** scoped — `test_flow_shell.py` + `test_flow_shell_exec.py` (20 passed), `test_flow_mcp_schema.py` (9 passed). Config load + `shell_exec` signature inspection confirm the model-facing schema change (`timeout_seconds`).
**Doc Sync:** fixed — `config.md` (shell + MCP config tables, validator ref), `tools.md` (config table), `pydantic-ai-integration.md` (MCPToolsetEntry pseudocode + mcp_servers field list). All-specs sweep clean; source docstrings updated inline during rename.

**Overall: DELIVERED**
Pure behavior-preserving rename; one intended model-facing schema change (`shell_exec.timeout_seconds`). Pre-ship: check operator `~/.co-cli/settings.json` for `shell.max_timeout` / per-server `mcp.*.timeout` keys (`extra="forbid"` would fail load) and rename them.

## Implementation Review — 2026-06-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | shell timeout/yield_window family `_seconds`; stdlib kwargs untouched | ✓ pass | `config/shell.py:5,15,17,75,90-93` constant/env/field/validator renamed; `shell_backend.py:67,71,157` params `timeout_seconds`/`yield_window_seconds`; `shell_backend.py:131,209` stdlib `asyncio.wait_for(timeout=…)` intact; `execute.py:23,97,100` tool param + config reads renamed |
| TASK-2 | MCP `timeout_seconds` fields; only SDK kwarg + `asyncio.timeout` remain | ✓ pass | `config/mcp.py:31` field; `agent/mcp.py:81` entry field; `agent/mcp.py:95,99,111` SDK `timeout=cfg.timeout_seconds` (kwarg kept); `agent/mcp.py:130` `timeout_seconds=`; `:141` `asyncio.timeout(entry.timeout_seconds)` |
| TASK-3 | no bare old names in co_cli/+tests/; suite green | ✓ pass | done_when greps return empty; all 22 `run_command`/`shell_exec` call sites in `test_flow_shell.py`/`test_flow_shell_exec.py` use `_seconds` kwargs; `git diff` confirms rename-only test edits (no assertion/mock change) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Missed reader of renamed `MCPToolsetEntry.timeout` — `entry.timeout` wrapped inside `asyncio.timeout(...)`, hidden from the agent/mcp.py-scoped grep. Frozen dataclass + no compat alias → `AttributeError` on every MCP connect. | `co_cli/bootstrap/core.py:404` | blocking | Renamed to `entry.timeout_seconds`; repo-wide re-sweep confirms zero remaining readers |

**Scope note:** `bootstrap/core.py` was not in any task's `files:` — the TASK-2 sweep was scoped to `agent/mcp.py` and missed the cross-module reader. Fixed here. Lesson: rename sweeps must grep the wrapped form (`entry.timeout` inside `asyncio.timeout(...)`) repo-wide, not just the producing module.

**Pre-existing out-of-scope working-tree changes** (NOT this delivery — exclude at ship): `co_cli/config/memory.py`, `co_cli/context/compaction.py`, `co_cli/main.py`, `docs/specs/{agents,dream,skills}.md`, `tests/{integration/test_review_kick_end_to_end,test_flow_compaction_review_snapshot,test_flow_exit_cleanup_review,test_flow_post_turn_hook}.py`, `uv.lock`. These were in the working tree before this plan and are unrelated to the rename.

### Tests
- Command: `uv run pytest`
- Result: 776 passed, 0 failed (187.62s)
- Log: `.pytest-logs/` (review-impl run)

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads, including the fixed MCP-connect path)
- Config load + `co_cli.bootstrap.core` import: ✓ clean after the `entry.timeout_seconds` fix
- `shell_exec` signature: ✓ exposes `timeout_seconds` (model-facing schema change verified via inspection)
- `success_signal` (TASK-1/TASK-2): ✓ shell + MCP config reads carry explicit `_seconds` units
- MCP-connect runtime path is not exercised by an automated test (no live MCP server in suite); the break was latent and is now closed by source fix + boot-graph load. Non-gating.

### Overall: PASS
Rename is now complete and total across the repo; the one cross-module straggler (`bootstrap/core.py`) is fixed and the full suite is green. Ready for Gate 2 / `/ship` — staging must exclude the pre-existing out-of-scope working-tree changes listed above.
