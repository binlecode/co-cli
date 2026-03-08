# Delivery: CoDeps Refactor — Grouped Dependency Structure
Date: 2026-03-07

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | `grep CoServices co_cli/deps.py` returns match | ✓ pass | |
| TASK-2 | `grep "CoServices\|CoConfig\|CoSessionState\|CoRuntimeState" co_cli/main.py` returns 4 matches | ✓ pass | |
| TASK-3 | No plain `ctx.deps.shell\|knowledge_index\|task_runner` in tools/ | ✓ pass | |
| TASK-4 | All orchestration files updated — no flat deps access remaining | ✓ pass | evals/ not covered by done_when; fixed post-review |
| TASK-5 | memory_lifecycle.py and memory_retention.py updated | ✓ pass | |
| TASK-6 | `uv run pytest -v` passes | ✓ pass | 528 passed, 2 skipped; 1 pre-existing LLM timeout flake |
| TASK-7 | `grep CoServices docs/DESIGN-core.md` returns match | ✓ pass | |
| TASK-8 | test_tool_calling_functional.py accepts search_memories | ✓ pass | |

## Files Changed

**Core structure:**
- `co_cli/deps.py` — Replaced flat `CoDeps` with grouped `CoServices` / `CoConfig` / `CoSessionState` / `CoRuntimeState` + `CoDeps` shell; updated `make_subagent_deps()` to share services/config by reference and reset session/runtime
- `co_cli/main.py` — Populates four sub-structs; initializes `CoRuntimeState` with `SafetyState` + `OpeningContextState`

**Migrated files (access pattern: `ctx.deps.field` → `ctx.deps.{services|config|session|runtime}.field`):**
- `co_cli/_bootstrap.py` — `knowledge_index`, `knowledge_search_backend`
- `co_cli/_commands.py` — `session_tool_approvals`, `skill_registry`, `session_todos`, `skill_tool_grants`, config fields
- `co_cli/_history.py` — `precomputed_compaction`, `opening_ctx_state`, `safety_state`, config fields; stale docstrings fixed
- `co_cli/_orchestrate.py` — `turn_usage`, `safety_state`, `opening_ctx_state`, config fields
- `co_cli/_preflight.py` — `config.gemini_api_key`
- `co_cli/agent.py` — `config.model_roles`, `session.skill_registry`
- `co_cli/memory_lifecycle.py` — config fields
- `co_cli/memory_retention.py` — config fields
- `co_cli/tools/_google_auth.py` — `session.google_creds`, `session.google_creds_resolved`, `config.google_credentials_path`
- `co_cli/tools/articles.py` — `services.knowledge_index`, `config.memory_dir`, `config.library_dir`, config fields
- `co_cli/tools/capabilities.py` — config fields
- `co_cli/tools/delegation.py` — config fields
- `co_cli/tools/google_drive.py` — `session.drive_page_tokens`, config fields
- `co_cli/tools/memory.py` — config fields
- `co_cli/tools/obsidian.py` — config fields
- `co_cli/tools/shell.py` — `services.shell`, `session.session_tool_approvals`, config fields
- `co_cli/tools/task_control.py` — `services.task_runner`
- `co_cli/tools/todo.py` — `session.session_todos`; stale docstring fixed
- `co_cli/tools/web.py` — config fields

**Tests (all updated to use `CoDeps(services=CoServices(...), config=CoConfig(...))`):**
- `tests/test_agent.py`, `tests/test_approval.py`, `tests/test_background.py`, `tests/test_bootstrap.py`, `tests/test_commands.py`, `tests/test_context_overflow.py`, `tests/test_delegate_coder.py`, `tests/test_doom_loop.py`, `tests/test_google_cloud.py`, `tests/test_history.py`, `tests/test_knowledge_index.py`, `tests/test_llm_e2e.py`, `tests/test_memory.py`, `tests/test_memory_decay.py`, `tests/test_memory_lifecycle.py`, `tests/test_model_roles_config.py`, `tests/test_obsidian.py`, `tests/test_orchestrate.py`, `tests/test_preflight.py`, `tests/test_save_article.py`, `tests/test_shell.py`, `tests/test_signal_analyzer.py`, `tests/test_skills_loader.py`, `tests/test_tool_calling_functional.py`, `tests/test_web.py`
- `tests/test_skills_loader.py` — stale comment fixed (`active_skill_allowed_tools` → `skill_tool_grants`)

**Evals (fixed post independent review — not covered by pytest):**
- `evals/_common.py` — `make_eval_deps()` rewritten to use `CoServices`/`CoConfig`/`CoSessionState`; `detect_model_tag()` fixed to use `get_role_head()` instead of non-existent `settings.gemini_model`/`settings.ollama_model`; removed non-existent `memory_decay_strategy`, `memory_decay_percentage` fields; `deps.personality` → `deps.config.personality`
- `evals/eval_tool_chains.py` — `deps._safety_state` → `deps.runtime.safety_state`
- `evals/eval_safety_grace_turn.py` — `deps._safety_state` → `deps.runtime.safety_state`
- `evals/eval_safety_abort_marker.py` — `deps._safety_state` → `deps.runtime.safety_state`
- `evals/eval_memory_proactive_recall.py` — `deps._safety_state` / `deps._opening_ctx_state` → `deps.runtime.*`
- `evals/eval_memory_signal_detection.py` — `deps._safety_state` / `deps._opening_ctx_state` → `deps.runtime.*`

**Docs:**
- `docs/DESIGN-core.md` — Full section on CoDeps grouped structure: ownership rules, CoServices/CoConfig/CoSessionState/CoRuntimeState fields, make_subagent_deps() isolation contract
- `docs/DESIGN-knowledge.md`, `docs/DESIGN-llm-models.md`, `docs/DESIGN-mcp-client.md`, `docs/DESIGN-memory.md`, `docs/DESIGN-personality.md`, `docs/DESIGN-prompt-design.md`, `docs/DESIGN-skills.md`, `docs/DESIGN-tools-delegation.md`, `docs/DESIGN-tools-execution.md`, `docs/DESIGN-tools-integrations.md` — All stale `deps.field_name` access patterns updated to `deps.config.*` / `deps.services.*` / `deps.session.*` / `deps.runtime.*`

**Lifecycle:**
- `docs/TODO-codeps-refactor.md` — Deleted (all 8 tasks shipped)

## Tests
- Scope: full suite (DELIVERED)
- Result: pass (528 passed, 2 skipped; 1 pre-existing LLM timeout flake in `test_summarize_messages_personality_active` — passes on re-run, confirmed not caused by this change)

## Independent Review
- Result: 8 blocking / 4 minor (all evals/ — not covered by `uv run pytest`)
- All blocking issues fixed before writing this report

| File | Finding | Severity | Fixed |
|------|---------|----------|-------|
| `evals/_common.py` | `make_eval_deps()` used flat `CoDeps(**kwargs)` | blocking | yes |
| `evals/_common.py` | `settings.gemini_model`, `settings.ollama_model`, `memory_decay_strategy`, `memory_decay_percentage`, `summarization_model` don't exist on `Settings` | blocking | yes |
| `evals/_common.py` | `deps.personality` flat access | blocking | yes |
| `evals/eval_tool_chains.py` (×2) | `deps._safety_state = SafetyState()` flat assign | blocking | yes |
| `evals/eval_safety_grace_turn.py` | `deps._safety_state = SafetyState()` flat assign | blocking | yes |
| `evals/eval_safety_abort_marker.py` | `deps._safety_state = SafetyState()` flat assign | blocking | yes |
| `evals/eval_memory_proactive_recall.py` | `deps._safety_state` / `deps._opening_ctx_state` flat assign | blocking | yes |
| `evals/eval_memory_signal_detection.py` | `deps._safety_state` / `deps._opening_ctx_state` flat assign | blocking | yes |
| `co_cli/_history.py` | Stale docstrings referencing old access paths | minor | yes |
| `co_cli/tools/todo.py` | Module docstring said `CoDeps.session_todos` | minor | yes |
| `tests/test_skills_loader.py` | Comment said `active_skill_allowed_tools` (old name) | minor | yes |
| `co_cli/deps.py` | `google_creds_resolved` now caller-settable (was `init=False`); low real-world risk given `CoSessionState` is only constructed in controlled paths | minor | accepted by design |

**Post-fix: clean**

## Doc Sync
- Result: fixed (full-scope sync-doc run across all 13 DESIGN docs — stale `deps.field` access patterns updated in DESIGN-core, DESIGN-knowledge, DESIGN-llm-models, DESIGN-mcp-client, DESIGN-memory, DESIGN-personality, DESIGN-prompt-design, DESIGN-skills, DESIGN-tools-delegation, DESIGN-tools-execution, DESIGN-tools-integrations)

## Coverage Audit
- Result: GAPS_FOUND — 0 blocking, 2 minor
  - P2: `web_search`/`web_fetch` approval table entry in DESIGN-tools.md shows "No" but approval is conditional on `web_policy` (conditional behavior documented in DESIGN-tools-integrations.md, just not reflected in the approval table)
  - P2: `delegate_coder`/`delegate_research`/`delegate_analysis` not explicitly in approval table (they spawn sub-agents; `requires_approval=False` registration not noted in approval rationale)

## Overall: DELIVERED
All 8 tasks passed their `done_when` criteria. Tests pass (pre-existing LLM timeout flakiness unchanged). Independent review found 8 blocking issues in `evals/` (not covered by pytest scope) — all fixed before delivery. Doc sync and coverage audit complete.
