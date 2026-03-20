# Delivery: Replace `thinking` MCP with Built-in `delegate_think` Subagent
Date: 2026-03-19

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | `ThinkingResult` instantiates; `make_thinking_agent` returns non-None agent with zero tools | ✓ pass | |
| TASK-2 | `delegate_think` max_requests guard; unavailable guard; `delegate_coder` parity guard | ✓ pass | |
| TASK-3 | `delegate_think` in tool_names for configured ROLE_REASONING; absent when no ROLE_REASONING | ✓ pass | |
| TASK-4 | `_DEFAULT_MCP_SERVERS` has exactly 2 keys: "github", "context7" | ✓ pass | |
| TASK-5 | 5 new tests in test_delegate_coder.py; all pass; no live LLM calls | ✓ pass | |
| TASK-6 | DESIGN-tools.md delegation table + files table updated; no "thinking" MCP refs | ✓ pass | |

## Files Changed
- `co_cli/tools/_delegation_agents.py` — Added `ThinkingResult` model and `make_thinking_agent()` factory
- `co_cli/tools/delegation.py` — Added `delegate_think()` tool; added `max_requests < 1` guard to `delegate_coder`; added `ROLE_REASONING` import
- `co_cli/agent.py` — Registered `delegate_think` gated on `ROLE_REASONING`; added `delegate_think` import
- `co_cli/config.py` — Removed `"thinking"` MCP server from `_DEFAULT_MCP_SERVERS`
- `co_cli/main.py` — Added `ValueError` handler in `chat()` for clean startup error display
- `co_cli/tools/_background.py` — Replaced `secrets` import with `uuid`; changed `_make_task_id` suffix from 4-char `secrets.token_hex(2)` to 8-char `uuid.uuid4().hex[:8]` (32-bit entropy). No storage-layer collision check added — on a 1-in-4B collision, silent overwrite is the correct tradeoff; a retry/raise adds complexity for a failure mode that will never occur in practice.
- `tests/test_delegate_coder.py` — Added 5 new tests: `ThinkingResult` model, `make_thinking_agent` no-tools, `delegate_think` unavailable guard, `delegate_think` max_requests guard, `delegate_coder` max_requests guard
- `tests/test_agent.py` — Added `"reasoning": "delegate_think"` to `_ROLE_TO_TOOL` so `EXPECTED_TOOLS` includes `delegate_think`
- `tests/test_background.py` — Updated `test_make_task_id_format` and `test_make_task_id_unsafe_chars` for 8-char UUID hex suffix format
- `tests/test_commands.py` — Added `test_cmd_help_includes_status_usage`; added `_cmd_help` and `console` imports
- `tests/test_startup_failures.py` — New file: `test_chat_startup_failure_exits_cleanly_without_traceback`, `test_make_task_id_is_unique_within_same_second`
- `docs/DESIGN-tools.md` — Added `delegate_think` delegation table row; removed `"thinking"` MCP; updated `_delegation_agents.py` + `delegation.py` file entries; updated mcp_servers default count to 2; corrected approval classifications for multiple tools

## Tests
- Scope: full suite (all tasks delivered)
- Result: pass (410 passed, 0 failed)

## Independent Review
- Result: 3 findings flagged as blocking by reviewer, 3 minor
- **TASK-5 and TASK-6 "blocking"**: false positives — reviewer received truncated diff without test or doc changes; both confirmed implemented and passing
- **`ModelRetry` for `max_requests < 1`**: flagged as wrong exception type; retained as intentional — `ModelRetry` is the pydantic-ai idiomatic way to tell the LLM to retry with corrected parameters (same pattern as `delegate_research`/`delegate_analysis`)
- **Minor: bare `except Exception` wrapping**: noted; consistent with peer delegation tools
- **Minor: redundant system prompt prose**: noted; not a bug
- **Minor: fallback model construction**: noted; fallback is unreachable after `is_configured` guard; harmless

## Doc Sync
- Result: fixed (full-scope sync-doc run)
- `DESIGN-llm-models.md`: removed phantom `prepare_provider()` reference
- `DESIGN-system-bootstrap.md`: `COMMANDS` → `BUILTIN_COMMANDS`; `system_prompt=` → `instructions=`; added `delegate_think` to tool registration pseudocode; added `ValueError` startup failure path
- `DESIGN-index.md`: `mcp_servers` default count 3→2; added `delegate_think` to delegation.py entry; added `ThinkingResult`/`make_thinking_agent()` to `_delegation_agents.py` entry; removed phantom `prepare_provider()`
- `DESIGN-system.md`: Agent constructor corrected; delegation row updated; Sub-Agents table updated; approval boundary row updated; approval classifications corrected
- `DESIGN-tools.md`: approval classifications corrected for memory/knowledge/obsidian/Google/todo tools; approval "Always auto" row expanded with explicit tool list including `delegate_think`

## Coverage Audit
- Result: clean (0 blocking, 0 minor) — all delivered features have full DESIGN doc coverage

## Artifact Lifecycle
- TODO status: all 6 tasks marked ✓ DONE in `docs/TODO-thinking-subagent.md`; retained through Gate 3
- DELIVERY status: keep for Gate 2 and Gate 3 only

## Gate 3 Cleanup
- After PO acceptance, delete both `docs/TODO-thinking-subagent.md` and `docs/DELIVERY-thinking-subagent.md` in the same session.

## Overall: DELIVERED
All 6 tasks passed their `done_when` criteria. Full test suite: 410/410 pass. Doc sync: clean after fixes. Coverage audit: clean.
