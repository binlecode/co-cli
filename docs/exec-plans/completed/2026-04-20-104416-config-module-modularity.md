# TODO: Config Module Modularity Cleanup

**Status:** Planned
**Target:** Reduce config-module coupling and duplication in `co_cli/config/` without changing runtime behavior.
**Scope:** `co_cli/config/_core.py`, `co_cli/config/_llm.py`, and `tests/test_config.py`.

## 1. Context & Motivation

The main config risk is structural, not behavioral. `co_cli/config/_core.py` contains LLM-provider-aware API key resolution logic that belongs in `_llm.py`, `InferenceOverride` violates the repo `*Settings` naming convention, and `Settings.save()` is dead code (zero call sites).

The flat `nested_env_map` in `_core.py` is intentionally centralized — it is the single canonical reference for all env vars the system accepts, and recent history (env-prefix unification, knowledge field additions) shows it is updated correctly alongside field changes. Moving it into 7 per-submodel classmethods would reduce discoverability and increase interface complexity for a sync hazard that has not produced a defect. It stays.

This plan keeps the work tight: move one block of misplaced LLM logic, fix two naming violations. No behavioral changes.

## 2. Current-State Validation

- `Settings.fill_from_env()` in [co_cli/config/_core.py:168](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:168) contains flat overrides, a nested env map across all submodels, provider-aware LLM API-key resolution, and MCP-server env parsing.
- The flat nested env map (lines 196–245) is deliberately centralized and stays.
- Provider-aware LLM API-key resolution (lines 252–267) is LLM-domain logic that does not belong in `_core.py`.
- `InferenceOverride` in [co_cli/config/_llm.py:123](/Users/binle/workspace_genai/co-cli/co_cli/config/_llm.py:123) is a persisted nested settings model that violates the repo-standard `*Settings` suffix.
- `Settings.save()` in [co_cli/config/_core.py:276](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:276) has zero call sites in the repo and is dead code.

Explicitly out of scope:

- No per-submodel env delegation interface or split of the flat `nested_env_map`.
- No env-prefix migration.
- No list-parsing deduplication (shell and web bodies differ by `.lower()` — intentional, not duplication).
- No deduplication of `reasoning_model_settings` / `noreason_model_settings` (already share `_inference()`; remainder is trivial).
- No config-format migration or broader settings redesign.

## 3. Implementation Plan

### Step 1: Move LLM API Key Resolution into `_llm.py`

- [x] Extract the provider-aware API-key block from [co_cli/config/_core.py:252](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:252) into a helper in `co_cli/config/_llm.py`. ✓ DONE
- [x] Call the helper from `Settings.fill_from_env()` with the env source and the current `data["llm"]` dict. ✓ DONE
- [x] The flat `nested_env_map` and MCP-server env parsing remain in `_core.py`. ✓ DONE

**Files:**
- [co_cli/config/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:1)
- [co_cli/config/_llm.py](/Users/binle/workspace_genai/co-cli/co_cli/config/_llm.py:1)

**Acceptance:**
- LLM API-key resolution logic lives in `_llm.py`.
- `load_config()` behavior is unchanged from a caller perspective.
- `_core.py` no longer contains provider-name constants or provider-to-env-var mappings.

### Step 2: Fix Naming Violations

- [x] Rename `InferenceOverride` in [co_cli/config/_llm.py:123](/Users/binle/workspace_genai/co-cli/co_cli/config/_llm.py:123) to `InferenceSettings` (matches `*Settings` convention for persisted nested models). ✓ DONE
- [x] Delete `Settings.save()` in [co_cli/config/_core.py:276](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:276) — zero call sites, dead code. ✓ DONE

**Files:**
- [co_cli/config/_llm.py](/Users/binle/workspace_genai/co-cli/co_cli/config/_llm.py:1)
- [co_cli/config/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:1)

**Acceptance:**
- `InferenceOverride` name does not appear anywhere in the repo.
- `Settings.save()` does not appear anywhere in the repo.
- No behavior change.

## 4. Testing Strategy

- [x] Keep [tests/test_config.py](/Users/binle/workspace_genai/co-cli/tests/test_config.py:1) as the primary behavior gate for config precedence and validation. ✓ DONE
- [x] Add a focused test for LLM API-key resolution covering: provider-specific env var wins, `CO_LLM_API_KEY` fallback, no key set. ✓ DONE (4 tests)
- [x] Update any tests broken by the `InferenceOverride` → `InferenceSettings` rename. ✓ DONE (no tests were referencing it)
- [x] Run focused config/LLM test targets during implementation, then the full gate before shipping. ✓ DONE (25/25 config, 320/321 full — 1 unrelated Gemini API timeout)

## 5. Execution Order

1. Move LLM API-key resolution into `_llm.py`
2. Rename `InferenceOverride` → `InferenceSettings`
3. Delete `Settings.save()`
4. Run focused tests, then full gate

## 6. Risks & Guardrails

- Preserve current config precedence exactly; this refactor is structural, not behavioral.
- The flat `nested_env_map` in `_core.py` is a deliberate design choice for discoverability — do not split it.
- Do not touch unrelated config consumers unless required by the refactor.
