# FIX: General Issues (Codex Review)

## Scope
This plan consolidates architecture-level issues found in the current co-cli codebase, using `CLAUDE.md` as the baseline for intended patterns and constraints.

References consulted:
- `CLAUDE.md`
- `docs/TODO-tool-call-stability.md`
- Core modules in `co_cli/` and `co_cli/tools/`

## Priority Summary

1. ~~P1: Global pagination state in Drive tool~~ — **Fixed** (moved to `CoDeps.drive_page_tokens`, see DESIGN-co-cli.md §4.3)
3. P1: Gemini API key resolution can select stale env key over settings key
4. ~~P2: Telemetry SQLite lock contention risk~~ — **Fixed** (WAL mode + busy_timeout + retry, see DESIGN-otel-logging.md §2)
5. P2: Approval architecture still using manual prompt flow

---

## Issue 6 (P2): Telemetry SQLite writer may contend with readers

### Problem
Exporter writes to SQLite while `co tail`/Datasette read the same DB; default connection settings increase chance of lock errors under concurrent access.

### Evidence
- `co_cli/telemetry.py` creates per-export write connections with default pragmas

### Fix Plan
1. Enable WAL mode and `busy_timeout` during DB init.
2. Consider batched transaction tuning (already batching spans; keep this).
3. Add resiliency around transient `database is locked` with short retry/backoff.
4. Add a stress test scenario with concurrent reader and writer.

### Acceptance Criteria
- No frequent lock failures when tailing while chat is active.

---

## Issue 7 (P2): Approval flow architecture not yet migrated to pydantic-ai deferred approvals

### Problem
Current confirmation (`tools/_confirm.py`) mutates `auto_confirm` and handles prompts inline. Repo docs already identify migration to `requires_approval` + `DeferredToolRequests` as target architecture.

### Evidence
- `CLAUDE.md` migration status section
- `docs/TODO-approval-flow.md`

### Fix Plan
1. Implement pydantic-ai approval flow in chat loop.
2. Keep session-level yolo behavior as explicit policy in approval handler.
3. Remove direct prompt handling from tool implementations.
4. Update docs/tests for approval behavior matrix.

### Acceptance Criteria
- Tool approval is centralized and consistent.
- Tool code no longer owns interactive prompt logic.

---

## Agent Implementation Review (2026-02-06)

### Finding A2 (P1): Gemini API key resolution can select stale env key over settings key

**Problem**
Gemini path uses `os.environ.setdefault("GEMINI_API_KEY", api_key)`. If `GEMINI_API_KEY` already exists but is stale/invalid, settings value is ignored.

**Evidence**
- `co_cli/agent.py:34` uses `setdefault` (does not overwrite existing env)

**Fix Plan**
1. Avoid mutating process env here; pass provider credentials explicitly if supported.
2. If env mutation must remain, set explicit precedence and log which source won.
3. Add a regression test for conflicting env vs settings values.

---

## Execution Order (Recommended)

1. ~~Issue 5 (Drive session state)~~ — **Done**
3. Finding A2 (Gemini API key precedence)
4. ~~Issue 6 (telemetry locking)~~ — **Done**
5. Issue 7 (approval-flow migration)

## Test Strategy

- Keep functional/integration policy (no mocks) per repo rules.
- Add targeted tests per issue and run full suite:

```bash
uv run pytest
```

Suggested focused runs during implementation:

```bash
uv run pytest tests/test_sandbox.py -v
uv run pytest tests/test_shell.py -v
uv run pytest tests/test_google_cloud.py -v
```

## Doc Updates Required With Fixes

When implementing, also update:
- `README.md` (if behavior/commands/config flow changes)
- `settings.example.json` (if config keys change)
- `CLAUDE.md` migration status (when approval flow lands)
- `docs/DESIGN-*.md` impacted by architectural changes

## Out of Scope for This Plan

- Full structured-output migration beyond current tool contract alignment
- Streaming tool-output UX redesign (`run_stream`) unless needed for approval-flow migration
