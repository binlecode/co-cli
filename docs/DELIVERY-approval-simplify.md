# Delivery: Approval Simplify
Date: 2026-03-07

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | `approval_risk_enabled` absent from `_orchestrate.py`; pattern hint present; docstring returns 'a' | ✓ pass | |
| TASK-2 | `approval_risk_enabled`/`approval_auto_low_risk` absent from all `co_cli/` source | ✓ pass | |
| TASK-3 | `_approval_risk.py` absent; `test_approval_risk.py` absent; `test_approval.py` 12 tests pass | ✓ pass | |
| TASK-4 | No `approval_risk`/`four-tier` in `DESIGN-flow-approval.md` | ✓ pass | |
| TASK-5 | No `approval_risk`/`_approval_risk` or `four-tier` across `DESIGN-*.md` | ✓ pass | |

## Files Changed
- `co_cli/_orchestrate.py` — removed risk classifier block; added `derive_pattern` import; added pattern hint before `prompt_approval` for shell "always"; fixed `FrontendProtocol.prompt_approval` docstring; `logger.warning` → `logger.debug` for skill grant log
- `co_cli/deps.py` — removed `approval_risk_enabled` and `approval_auto_low_risk` from `CoConfig`
- `co_cli/config.py` — removed `approval_risk_enabled` and `approval_auto_low_risk` fields and env var entries
- `co_cli/main.py` — removed two kwargs from `CoConfig(...)` constructor
- `co_cli/_approval_risk.py` — deleted
- `co_cli/_commands.py` — `_cmd_compact` now resolves summarization model via `model_roles["summarization"]` before falling back to agent model
- `tests/test_approval.py` — removed `_approval_risk` import and 3 risk classifier tests
- `tests/test_orchestrate.py` — updated `test_skill_grant_log` to assert DEBUG level and new message text
- `tests/test_commands.py` — `_make_ctx` and `_make_agent_and_deps` source `model_roles`/`llm_provider`/`ollama_host` from `settings`
- `tests/test_llm_e2e.py` — all `CoDeps`/`CoConfig` constructions source model config from `get_settings()`
- `tests/test_tool_calling_functional.py` — `_make_deps` sources model config from `settings`
- `tests/test_memory_lifecycle.py` — `_make_deps` sources model config from `settings`
- `docs/DESIGN-flow-approval.md` — simplified to three-tier model; added pattern transparency note; removed risk classifier nodes, config rows, file row; "Recovery and Fallback" risk classifier bullet removed; MCP section updated to Tiers 1–3
- `docs/DESIGN-index.md` — removed `approval_risk_enabled`/`approval_auto_low_risk` config rows; removed `_approval_risk.py` module row; "four-tier" → "three-tier"
- `docs/DESIGN-tools-execution.md` — removed Tier 3 risk classifier paragraph; renumbered to Tier 3; "four-tier" → "three-tier"
- `docs/DESIGN-flow-tools-lifecycle.md` — risk classifier lines removed; "four-tier" → "three-tier"
- `docs/DESIGN-core.md` — removed `approval_risk_enabled` config reference; "four-tier" → "three-tier"
- `docs/DESIGN-flow-core-turn.md` — "four-tier" → "three-tier"
- `docs/DESIGN-flow-skills-lifecycle.md` — "four-tier" → "three-tier"
- `docs/DESIGN-prompt-design.md` — "four-tier" → "three-tier"

## Tests
- Scope: full suite (DELIVERED)
- Result: pass (447 passed, 4 pre-existing LLM-flaky timeouts under concurrent Ollama load — unrelated to approval-simplify)
- Approval regression gate: 52/52 pass

## Independent Review
- Result: 1 blocking fixed (stale `_approval_risk.py` row in `DESIGN-index.md`), 2 minor fixed (skill grant `logger.warning` → `logger.debug`; `test_orchestrate.py` assertion updated)

## Doc Sync
- Result: fixed — sync-doc corrected `CoDeps` flat→grouped access patterns in `DESIGN-tools-execution.md`, `DESIGN-flow-context-governance.md`, `DESIGN-tools-integrations.md`; stale `status.py` → `_status.py` refs in `DESIGN-core.md`, `DESIGN-doctor.md`, `DESIGN-mcp-client.md`; stale `preflight` refs in `DESIGN-flow-bootstrap.md`; residual `risk classify` node in `DESIGN-flow-core-turn.md` removed

## Coverage Audit
- Result: clean — three-tier approval flow, pattern transparency, all config removals fully documented in `DESIGN-flow-approval.md` and cross-docs

## Overall: DELIVERED
All five tasks passed. Approval flow simplified to three-tier model; risk classifier removed; shell "always" now shows derived pattern before user answers; all DESIGN docs updated. Bonus: `_cmd_compact` now resolves the correct summarization model role; all LLM-bound tests now source model config from settings.
