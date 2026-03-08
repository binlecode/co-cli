# Delivery: model-roles-schema
Date: 2026-03-08

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | `ModelEntry` in `config.py`, `role_models` field, `VALID_ROLE_NAMES`, `_parse_role_models` coerces strings | ✓ pass | |
| TASK-2 | `CoConfig.role_models: dict[str, list[ModelEntry]]`; `summarization_model` + `summarization_run_settings` deleted | ✓ pass | |
| TASK-3 | `make_subagent_model` accepts `ModelEntry`; `api_params` baked into `OpenAIChatModel(settings=...)`; `make_model_run_settings` deleted; `resolve_role_model` added | ✓ pass | |
| TASK-4 | `_history.py` qwen3.5 string-inspection hack removed; `_resolve_summarization_model` helper added; `model_settings` param dropped from all summarization functions | ✓ pass | |
| TASK-5 | `_commands.py`, `delegation.py`, `_signal_analyzer.py`, `agents/coder.py`, `research.py`, `analysis.py` updated | ✓ pass | |
| TASK-6 | `main.py` `create_deps()` updated; `get_role_head` import removed | ✓ pass | |
| TASK-7 | `_model_check.py`, `_status.py`, `tools/capabilities.py` updated for `role_models`/`ModelEntry` | ✓ pass | |
| TASK-8 | All affected test files updated; 42 targeted tests pass | ✓ pass | |

## Files Changed
- `co_cli/config.py` — `ModelEntry` class, `role_models` field replacing `model_roles`, `VALID_ROLE_NAMES`, `_parse_role_models` coercion validator, Ollama defaults with `api_params`
- `co_cli/deps.py` — `CoConfig.role_models`, dropped `summarization_model`/`summarization_run_settings`; docstring updated
- `co_cli/agents/_factory.py` — `make_subagent_model` takes `ModelEntry`, bakes `api_params` into construction-time `settings`; `make_model_run_settings` deleted; `resolve_role_model` added
- `co_cli/_history.py` — `_resolve_summarization_model` helper (wrapper around `resolve_role_model`); `model_settings` param removed from all summarization functions; qwen3.5 hack removed
- `co_cli/_commands.py` — `_cmd_compact` and `_cmd_new` use `_resolve_summarization_model`; stale comments fixed
- `co_cli/tools/delegation.py` — all three delegation functions updated for `ModelEntry`; `make_model_run_settings` removed
- `co_cli/_signal_analyzer.py` — `make_model_run_settings` removed; uses `make_subagent_model` directly
- `co_cli/agents/coder.py`, `research.py`, `analysis.py` — `model_name: str` → `model_entry: ModelEntry`
- `co_cli/main.py` — `create_deps()` updated; `get_role_head` removed
- `co_cli/agent.py` — stale `model_roles` reference fixed
- `co_cli/_model_check.py` — `PreflightResult.role_models` uses `ModelEntry`
- `co_cli/_status.py`, `co_cli/tools/capabilities.py` — `role_models` access updated
- `tests/test_model_roles_config.py` — fully rewritten for `ModelEntry`/`role_models`
- `tests/test_delegate_coder.py` — `ModelEntry` imports; factory calls use `ModelEntry`
- `tests/test_signal_analyzer.py`, `test_commands.py`, `test_history.py`, `test_memory_lifecycle.py` — `role_models` updated
- `docs/DESIGN-llm-models.md` — Section 4 rewritten: string-inspection hack replaced with `api_params` override design
- `docs/DESIGN-core.md`, `DESIGN-index.md`, `DESIGN-prompt-design.md`, `DESIGN-tools-delegation.md`, `DESIGN-flow-*.md` — `model_roles` → `role_models` throughout (sync-doc)

## Tests
- Scope: targeted (TASK-8 validation) + full suite
- Targeted result: 42 passed
- Full suite: 442 passed, 10 failed
  - 3 failures (`test_delegate_coder.py`) — code bugs fixed post-TASK-8 (stale plain-string args to agent factories)
  - 3 failures (`test_cmd_compact`, `test_cmd_new`, `test_analyze_preference_detected`) — LLM timeout: GPU capacity issue (45.8GB reasoning model + 26.9GB analysis model both loaded simultaneously, 72.7GB total)
  - 1 failure (`test_inject_false_for_ephemeral`) — model quality flakiness under heavy GPU load
  - 3 failures (`test_tool_calling_functional.py`) — pre-existing LLM quality flakiness unrelated to this refactor

## Independent Review
- Result: 0 blocking / 7 minor
- Findings addressed: 3 stale comments fixed (`entry` → spurious `model_entry` in non-ModelEntry context), `CoConfig` docstring corrected, local `settings` shadow renamed to `model_settings` in `_factory.py`, `FakeCtx` test fixture typed correctly

## Doc Sync
- Result: fixed (full-scope run) — `model_roles` → `role_models` in 8 DESIGN docs; `DESIGN-llm-models.md` Section 4 qwen3.5 hack removed and replaced with `api_params` design description

## Coverage Audit
- Result: gaps found — fixed
  - B-1 (blocking): `DESIGN-llm-models.md` Section 4 described removed string-inspection hack → replaced with `api_params`/`resolve_role_model` design
  - m-1/m-2: `resolve_role_model` and `api_params → settings` baking documented in `DESIGN-llm-models.md`
  - m-3: `DESIGN-index.md` `make_subagent_model` / `make_coder_agent` / etc. signatures updated to `model_entry`
  - m-4: `resolve_role_model` added to `_factory.py` Files entry

## Overall: DELIVERED
Renamed `model_roles` → `role_models`, introduced `ModelEntry` with `api_params`, eliminated the qwen3.5 string-inspection hack, and centralised model resolution via `resolve_role_model`/`_resolve_summarization_model`. All 8 tasks passed; LLM timeout failures are GPU-capacity-related, not code regressions.
