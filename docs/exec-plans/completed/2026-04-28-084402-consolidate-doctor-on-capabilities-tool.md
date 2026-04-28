# Plan: Consolidate Doctor on `capabilities_check` — Remove Non-Agent System-Check Paths

Task type: refactor / surface simplification

## Context

`co-cli` currently has **three** paths that exercise overlapping runtime/settings
health-check logic, each with its own scope, output format, and audience. The
agent-facing path (`capabilities_check`) is already the richest of the three, and
the bundled `/doctor` skill explicitly delegates to it as "the canonical runtime
self-check; `/doctor` is a troubleshooting workflow layered on top of it"
(`co_cli/skills/doctor.md:7`). The two non-agent paths predate that consolidation
and now duplicate work.

This plan retires the two non-agent paths and lands all runtime-health
introspection on the agent surface.

### Current paths (verified by code walk)

| Path | Entry point | Logic invoked | Audience | Scope |
|------|-------------|---------------|----------|-------|
| **Agent tool** | `capabilities_check` (`co_cli/tools/capabilities.py:142`) | `check_runtime(deps)` | model | full: provider + integrations + knowledge + skills + MCP probes + fallbacks + tool surface |
| **REPL slash** | `/status` (`co_cli/commands/status.py:9`, registered `commands/core.py:44`) | `get_status` → `check_settings(config)` + `check_security()` | user, in-REPL | narrow: integrations + MCP, **no** provider/knowledge/skills, **no** tool surface |
| **Pre-agent CLI** | `co config` (`co_cli/main.py:364`) | same as `/status` | user, before chat | narrow: same as `/status` |

`/status` also has a second, unrelated branch: `/status <task-id>` displays a
single background task. That branch shares only the command name with the
system-health one — different code path, different concern.

### Why three paths is wrong

1. **Duplicated logic, drifted scope.** `check_settings` is a strict subset of
   `check_runtime` minus knowledge/skills/provider checks. Two functions cover
   the same conceptual surface; the smaller one will go stale.
2. **Drifted output.** `render_status_table` has its own categories
   (LLM/Shell/Google/Obsidian/Web Search/MCP/Database) which don't match the
   agent-tool surface (Available now / Discoverable / Approval-gated /
   Unavailable / Active fallbacks). Users get one mental model, model gets
   another.
3. **Inconsistent answer to "is X up?"**: `co config` says Brave/Google/MCP
   states; `capabilities_check` adds knowledge backend, skill registry,
   reasoning-model readiness, and bootstrap-recorded `deps.degradations`. The
   non-agent paths under-report.
4. **Pre-agent path has no deps.** `check_settings` only takes `config`; it
   can't see `deps.knowledge_store`, `deps.degradations`, or
   `deps.tool_index` — all of which carry the most useful diagnostic signal.
5. **`/doctor` skill already routes to the agent tool.** The slash-command
   `/status` and CLI `co config` are the only consumers of `check_settings`
   left; killing them cleans up the whole subgraph.

### Why "surface land it" (this plan) instead of "redraw the surface"

A separate question is whether co should adopt hermes-style schema-level
`check_fn` filtering (tools disappear from the model's view when their
prerequisites fail). That option was rejected upthread:

- Mid-session redrawing the tool schema busts the prompt cache (5-min TTL).
- `requires_config` already gates registration at boot.
- Mid-session degradation (credential expiry, MCP crash) can be handled at
  dispatch time as a follow-up port (research §3.1), without redrawing.

This plan keeps the **existing surface** and consolidates introspection on the
agent tool. The §3.1 dispatcher pre-check is a separate, optional follow-up.

## Outcome

After this plan:

- `capabilities_check` is the **only** runtime-health introspection surface.
  Users ask "what can you do?" or invoke `/doctor`; both flow through the agent
  tool.
- `/status` is removed; `/status <task-id>` migrates to `/tasks <task-id>`.
- `co config` is removed.
- `bootstrap/render_status.py` is deleted.
- `bootstrap/check.py:check_settings()` is deleted; the lower-level IO probes
  it composes (`check_agent_llm`, `check_mcp_server`, …) stay because
  `check_runtime` and `bootstrap/core.py` still use them.
- Security posture checks (`check_security`, `SecurityCheckResult`,
  `render_security_findings`) move to a dedicated module and are invoked at
  `co chat` startup instead of only via `co config`.

The agent's reasoning-and-decision tool for system checks is the existing
`capabilities_check` — no new tool needed. We polish its docstring so the
model picks it up for plain "is X up / can I do Y?" prompts.

## Non-goals

- Adding hermes-style `check_fn` dispatcher pre-check (separate port; research §3.1).
- Reworking `capabilities_check` output schema or split (its current form was
  shipped by `2026-04-23-103613-capability-self-check-surface.md`).
- Touching `/doctor` skill — it already delegates correctly.
- Replacing `co traces` / `co logs` / `co tail` Typer commands.

## Decisions

### D1: Delete `co config` outright; do not preserve a pre-agent health check

Pre-agent CLI cannot see `deps`, so it cannot run `check_runtime`. A reduced
`check_settings`-style command would just resurrect today's narrower path under
a new name. Users who need a quick pre-flight can run `co chat` and ask "what
can you do?" — same answer, richer detail. Cost: one extra LLM round-trip on
demand vs. a Rich table.

### D2: Migrate `/status <task-id>` to `/tasks <task-id>`

`/tasks` already lists tasks (`co_cli/commands/tasks.py:11`). Extend it to
accept an optional `<task-id>` arg that prints the same single-task table that
`_cmd_status` currently produces. Keep its existing `[status-filter]` form by
disambiguating: a 12-hex-char arg is a task id, anything else is a status
filter. Single command, one mental model.

### D3: Move security posture check to `co chat` startup

`check_security()` runs once at command entry today, only via `co config`. With
that gone, the warnings would never surface. Move the call into the
`display_welcome_banner` path or the post-bootstrap pre-prompt section in
`_chat_loop`, so any flagged conditions print once on session start. Same
findings, same rendering, different trigger.

### D4: Keep IO probe helpers in `bootstrap/check.py`

`check_agent_llm`, `check_reranker_llm`, `check_embedder`,
`check_cross_encoder`, `check_ollama_model`, `check_mcp_server`, `check_tei`,
`probe_ollama_context` are used by `bootstrap/core.py` and `check_runtime`.
Delete only `check_settings` and the `DoctorResult` / `CheckItem` types it
uses. Update the module docstring.

### D5: Polish `capabilities_check` docstring for system-check intent

The current docstring already nudges in the right direction
(`co_cli/tools/capabilities.py:143`). Tighten it to explicitly cover "is X up,
can I do Y, why is Z degraded" — the kinds of plain-language system checks the
model should route through this tool rather than guessing.

## Implementation tasks

### ✓ DONE — TASK-1: Migrate `/status <task-id>` into `/tasks <task-id>`

- Edit `co_cli/commands/tasks.py:_cmd_tasks`:
  - When `args.strip()` matches `^[0-9a-f]{8,}$`, look up `tasks_dict.get(arg)`
    and render the single-task table + tail (lift the body from
    `co_cli/commands/status.py:13-38`).
  - Otherwise keep current behavior (status filter / list all).
  - Update docstring: `Usage: /tasks [status-filter | task-id]`.
- Update `BUILTIN_COMMANDS["tasks"].description` in `commands/core.py:71` to
  reflect the new arg form.
- Done when `/tasks <id>` matches today's `/status <id>` output.

### ✓ DONE — TASK-2: Move security posture check to chat startup

- Create `co_cli/bootstrap/security.py` containing `SecurityCheckResult`,
  `check_security`, `render_security_findings` (moved as-is from
  `bootstrap/render_status.py:130-194`).
- Wire one call site in `co_cli/main.py` `_start_chat` after the welcome
  banner and before the prompt loop:
  ```python
  from co_cli.bootstrap.security import check_security, render_security_findings
  render_security_findings(check_security())
  ```
- Done when `co chat` prints the same warnings `co config` used to.

### ✓ DONE — TASK-3: Delete `/status` slash command

- Delete `co_cli/commands/status.py`.
- Delete `BUILTIN_COMMANDS["status"]` registration in
  `co_cli/commands/core.py:44-46` and the import at `core.py:24`.
- Update `co_cli/commands/help.py:25-27` — drop the "/status shows system
  health…" hint line; replace with a one-liner pointing model-callable
  introspection at `capabilities_check`/`/doctor` (or just delete; the table
  itself now lists `/tasks`).
- Done when `grep -r "_cmd_status" co_cli/` returns zero.

### ✓ DONE — TASK-4: Delete `co config` Typer command

- Delete the `@app.command() def config()` block in `co_cli/main.py:364-370`.
- Delete the import block `from co_cli.bootstrap.render_status import (...)`
  at `co_cli/main.py:19-24`.
- Done when `grep -r "render_status" co_cli/` returns zero.

### ✓ DONE — TASK-5: Delete `bootstrap/render_status.py`

- Delete the file. (TASK-2 already lifted its security half.)
- Done when the file no longer exists and `grep -rn "bootstrap.render_status"
  co_cli/ tests/` returns zero.

### ✓ DONE — TASK-6: Delete `check_settings()` and related dataclasses

- In `co_cli/bootstrap/check.py`:
  - Delete `DoctorResult` (lines ~83-113) and `CheckItem` (lines ~75-80).
  - Delete `check_settings()` (lines ~398-444).
  - Update module docstring at lines 1-19: drop the `check_settings` mention,
    keep `check_runtime`.
- Done when `grep -rn "check_settings\|DoctorResult\|CheckItem" co_cli/
  tests/` returns zero except in archival exec-plans under `completed/`.

### ✓ DONE — TASK-7: Update tests

- Delete `tests/bootstrap/test_status.py:test_get_status_reads_repo_root_pyproject` (uses
  `get_status`).
- Move the three `test_check_security_*` tests to `tests/bootstrap/test_security.py`
  with imports updated to `co_cli.bootstrap.security`.
- Add a smoke test for `/tasks <id>` in
  `tests/commands/` (or wherever existing `/tasks` tests live; if none, add one
  alongside the existing slash-command tests).
- Done when `pytest tests/bootstrap` and `pytest tests/commands` pass.

### ✓ DONE — TASK-8: Polish `capabilities_check` docstring

- Update `co_cli/tools/capabilities.py:142-147` docstring to explicitly cover
  "is X up", "why is Y degraded", "can I do Z right now" prompts — these are
  the system-check questions the model should route through this tool, but the
  current wording leans on capability-discovery framing only. Keep the existing
  paragraph; add one short line listing the "system check" intent so the
  description-vector embedding picks it up.
- Done when the docstring mentions both capability discovery and runtime
  health/degradation framing.

### ✓ DONE — TASK-9: Spec + doc updates (sync-doc)

- `docs/specs/bootstrap.md`:
  - Line 14: drop the `Runtime health checks (owned by /status tool)` bullet
    from the responsibilities section (or rewrite to reference
    `capabilities_check`).
  - Line 40: change the closing sentence from
    `… is invoked later by /status, not during startup` to
    `… is invoked later by capabilities_check (the agent tool exercised by
    /doctor), not during startup.`
- `docs/specs/tui.md` line 157: delete the `/status` row from the slash-table;
  update the `/tasks` row to reflect the new `[status-filter | task-id]` arg.
- `docs/specs/tools.md` line 130: leave as-is (already correct).
- `CLAUDE.md` line 16: change `uv run co status` (which never existed; this is
  a stale CLAUDE.md reference) to remove the line.
- `README.md`:
  - Lines 113-116: remove the "`co config` — System Health Check" section.
  - Line 142: replace the `/status` row with a pointer to `/doctor` and
    `capabilities_check`.
- `co_cli/context/orchestrate.py:682`: update the timeout error message —
  replace `check model health with \`co config\`` with `ask Co "what can you
  do right now?" or run /doctor`.
- `docs/REPORT-compaction-flow-quality.md` lines 178/185: same edit as above
  (these are committed reports; treat as minor copy fix, not retroactive
  rewrite).

## Validation

- `scripts/quality-gate.sh full` passes.
- `uv run co chat` starts; security warnings print at startup if applicable;
  `/help` does not list `/status`; `/tasks <id>` shows the same single-task
  view that `/status <id>` used to.
- `uv run co config` returns "No such command" — this is the desired regression.
- `uv run co chat` → ask "what can you do right now?" → `capabilities_check`
  is invoked; output covers tool surface + integrations + fallbacks.
- `uv run co chat` → invoke `/doctor` → still works; agent calls
  `capabilities_check` and renders the structured triage.
- Grep audit: zero hits for `check_settings`, `render_status`, `StatusResult`,
  `_cmd_status`, `bootstrap.render_status`, or `co config` in `co_cli/` and
  `tests/` (excluding `completed/` exec-plans).

## Risks

- **R1 (low):** Users with muscle-memory for `co config` will see a missing
  command. Mitigation: short note in CHANGELOG; no migration shim.
- **R2 (low):** `/status <id>` muscle-memory for task lookup. `/tasks <id>`
  is a clear successor; document in `/help` and the migration line.
- **R3 (low):** The startup security warning gets noisier than today (today
  it only prints on `co config`). Acceptable: the warnings should be visible
  every session, not gated behind an opt-in command.
- **R4 (none):** Agent-facing behavior is unchanged. `capabilities_check`
  emits the same `ToolReturn`; `/doctor` still works.

## Effort

- TASK-1: 30m
- TASK-2: 15m
- TASK-3: 10m
- TASK-4: 5m
- TASK-5: 5m
- TASK-6: 15m
- TASK-7: 30m
- TASK-8: 10m
- TASK-9: 30m

Total: ~2.5h, single-dev. Suitable for `/deliver` (no orchestration needed).

## Deferred / follow-up (out of scope)

- §3.1 `check_fn` dispatcher pre-check for mid-session credential expiry
  (research doc, RESEARCH-tools-gaps-co-vs-hermes.md).
- Lazy / cached `check_runtime` snapshot if `capabilities_check` latency
  becomes a concern with model-frequent calls (currently rare; not a problem).

## Delivery Summary — 2026-04-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `/tasks <id>` matches today's `/status <id>` output | ✓ pass |
| TASK-2 | `co chat` prints security warnings at startup | ✓ pass |
| TASK-3 | `grep -r "_cmd_status" co_cli/` returns zero | ✓ pass |
| TASK-4 | `grep -r "render_status" co_cli/` returns zero | ✓ pass |
| TASK-5 | `render_status.py` deleted; `grep -rn "bootstrap.render_status"` returns zero | ✓ pass |
| TASK-6 | `grep -rn "check_settings\|DoctorResult\|CheckItem" co_cli/ tests/` returns zero | ✓ pass |
| TASK-7 | `pytest tests/bootstrap` and `pytest tests/commands` pass | ✓ pass |
| TASK-8 | docstring mentions both capability discovery and runtime health/degradation framing | ✓ pass |
| TASK-9 | sync-doc clean — bootstrap.md, tui.md, CLAUDE.md, README.md, orchestrate.py, REPORT fixed | ✓ pass |

**Tests:** scoped (touched files) — 33 passed, 0 failed
**Doc Sync:** fixed (bootstrap.md: 5 fixes; tui.md: /status row deleted, /tasks args updated; CLAUDE.md: stale co status removed; README.md: co config section deleted, /status row replaced; orchestrate.py + REPORT: co config copy updated)

**Overall: DELIVERED**
All 9 tasks passed. `capabilities_check` is now the sole runtime-health introspection surface. `/status` and `co config` are removed; security checks run at chat startup; `/tasks <id>` provides task detail lookup.

## Implementation Review — 2026-04-28

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `/tasks <id>` matches `/status <id>` output | ✓ pass | tasks.py:12 — `_TASK_ID_RE = re.compile(r"^[0-9a-f]{8,}$")`; tasks.py:20-44 — task-id branch renders same table as old status.py:18-38 |
| TASK-2 | `co chat` prints security warnings at startup | ✓ pass | security.py:23-66 — `check_security` and `render_security_findings` present; main.py:265-267 — wired after `display_welcome_banner` |
| TASK-3 | `grep -r "_cmd_status" co_cli/` returns zero | ✓ pass | status.py deleted; core.py:24 import removed; core.py:44-46 registration removed; help.py:25-27 hint updated |
| TASK-4 | `grep -r "render_status" co_cli/` returns zero | ✓ pass | main.py:19-24 import block removed; main.py:364-370 `config()` command deleted |
| TASK-5 | file deleted; `bootstrap.render_status` grep zero | ✓ pass | file removed; grep confirmed zero |
| TASK-6 | `grep -rn "check_settings\|DoctorResult\|CheckItem"` zero | ✓ pass | check.py:75-113 dataclasses removed; check.py:398-444 `check_settings()` removed; stale comment at check.py:261 also fixed |
| TASK-7 | `pytest tests/bootstrap` and `pytest tests/commands` pass | ✓ pass | test_security.py:1-52 — 4 tests, imports from `co_cli.bootstrap.security`; test_commands.py — `test_cmd_help_includes_tasks_usage` and `test_cmd_tasks_task_id_shows_detail` added |
| TASK-8 | docstring covers capability discovery + runtime health | ✓ pass | capabilities.py:147 — "Also use for runtime health checks and system check questions: is X up, why is Y degraded, can I do Z right now." |
| TASK-9 | sync-doc complete | ✓ pass | bootstrap.md, tui.md, CLAUDE.md, README.md, orchestrate.py, REPORT all updated |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale docstring: "Exec-approvals wildcard entries" when code checks `shell.safe_commands` | security.py:29-31 | blocking | Fixed: "shell.safe_commands wildcard entries (pattern == '*' auto-approves all shell commands)" |

### Tests
- Command: `uv run pytest -v` (full suite, 642 tests)
- Result: 641 passed (non-compaction), 58/58 compaction passed on second run
- Flaky test: `test_summarizer_verbatim_anchor_in_next_step` — confirmed pre-existing LLM non-determinism (1/3 fail rate on Ollama qwen3.5:35b-a3b-agentic); unrelated to this delivery (no compaction/summarizer code changed)
- Log: `.pytest-logs/YYYYMMDD-HHMMSS-review-impl.log`

### Doc Sync
- Scope: full (public API removal: `/status`, `co config`)
- Result: fixed during orchestrate-dev; no additional fixes needed in review-impl

### Behavioral Verification
- `uv run co --help`: ✓ `config` command absent from Commands list
- `uv run co config`: ✓ exits with "No such command 'config'" (D1 behavior)
- `test_cmd_tasks_task_id_shows_detail`: ✓ `/tasks <id>` renders task detail table with task_id, status, command fields
- `test_cmd_help_includes_tasks_usage`: ✓ `/help` lists `/tasks` with `task-id` in description and tip line
- Security check at startup: ✓ `main.py:265-267` calls `render_security_findings(check_security())` after banner; prints nothing when no issues (silent path confirmed)

### Overall: PASS
All 9 tasks implemented correctly. One blocking issue found and fixed (stale docstring in security.py). Full test suite green (642 tests; 1 pre-existing flaky LLM test documented). `capabilities_check` is the sole runtime-health surface; `/status` and `co config` are fully retired.
