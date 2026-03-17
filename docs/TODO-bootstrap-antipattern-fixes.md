# TODO: Bootstrap Anti-Pattern Fixes

**Task type: refactor** — code reorganization without behavior change.
Regression surface: `get_agent()`, `CoConfig`, `CoSessionState`, `CoRuntimeState`, and all tests that touch these.

---

## Context

**Deep-scan re-validated 2026-03-16 against latest source.** All task steps and line references verified against the current codebase.

**Pre-shipped — skip these:**
- **TASK-AP2** (prune hoist): `_prune_stale_approvals` confirmed as peer call in `main.py::_chat_loop()` — NOT in `create_deps()`. ✓ done.
- **TASK-AP3** (`skip_provider_checks`): `check_runtime()` has the param; `check_integration_health()` calls it with `skip_provider_checks=True`. ✓ done.
- **TASK-6** (`memory_dir` hardcode): `_build_system_prompt()` uses `config.memory_dir`. ✓ done.

**Remaining work confirmed by scan:** TASK-1, TASK-4, TASK-5, TASK-7, TASK-8.

**TASK-9 deferred.** The `model_dump()`-driven bulk copy is verbose but self-documenting. The correctness risk from a silent name-intersection algorithm is not justified at this codebase scale. Revisit if ≥5 new `Settings` fields are added at once.

**Deep-scan findings (line refs verified against current source):**

- **TASK-1 call-sites confirmed:** `deps.py:92` (field), `_bootstrap.py:103,110` (assign), `_check.py:356` (status dict), `capabilities.py:59` (display), `test_delegate_coder.py:65` (CoConfig construction) and `test_delegate_coder.py:100` (assertion), `evals/_deps.py:42` (config_defaults), `test_bootstrap.py:151,153,156` (assertions). All 8 sites pending.
- **TASK-1 docstring:** `from_settings()` exclusion list at `deps.py:169` lists both `session_id` and `mcp_count` — both must be removed (TASK-1 and TASK-7 respectively).
- **TASK-4 confirmed:** `prepare_provider()` env mutation at `_model_factory.py:46`; `build_model()` gemini branch returns bare string at line 316; `from_config()` call at line 80 has no `api_key` param. **pydantic-ai API validated against live `.venv`:** `GeminiModel` (in `pydantic_ai.models.gemini`) is deprecated and fails at runtime with `AttributeError: 'Client' object has no attribute 'base_url'` when any Google env var is set. `GoogleGLAProvider` does not exist — the module exports only `GoogleProvider`. Correct API: `GoogleModel` from `pydantic_ai.models.google` + `GoogleProvider` from `pydantic_ai.providers.google`.
- **TASK-5 test call-sites confirmed:** `test_agent.py` lines 84, 91, 105, 149 all call `get_agent()` without config. Lines 118 and 128 already pass `config=` explicitly — no change needed there. `test_delegate_coder.py:18` module-level `get_agent()` also needs updating.
- **TASK-5 eval call-sites confirmed:** 11 eval files call `get_agent()` without `config` and use a stale 4-value unpack (`agent, model_settings, _, _ = get_agent()`). `get_agent()` returns a 3-tuple — these evals are already broken pre-TASK-5. Fixing the `config=` issue and the unpack is required; see TASK-5 step 4 for the eval caller list.
- **TASK-7 confirmed:** `deps.py:153` field, `_bootstrap.py:34` set, `_check.py:363,379` read, `evals/_deps.py:60` config_defaults.
- **TASK-8 confirmed:** `CoRuntimeState` fields `opening_ctx_state` and `safety_state` at `deps.py:265–266` typed `Any`. TYPE_CHECKING block at `deps.py:64–66` imports only `ModelRegistry` and `Settings` — no circular risk confirmed since `_bootstrap.py` already imports `OpeningContextState, SafetyState` directly. No callers of `get_type_hints()` on `CoRuntimeState` found in the codebase.
- **`evals/_deps.py` live bugs confirmed (all 3):** `mcp_count` line 60, `list(ModelEntry)` line 61, `s.ollama_host` line 63 (should be `s.llm_host`).
- **`_check.py` local `mcp_count` is safe:** Line 314 declares a local `mcp_count = sum(...)` variable counting live MCP probes — this is the capabilities dict key at line 349. Lines 363 and 379 use `deps.config.mcp_count` (the field). TASK-7 corrects only the field accesses; the local variable is untouched.

---

## Problem & Outcome

**Problem:** The bootstrap path has 5 remaining issues that violate stated group contracts (`session_id` in config), leak global state (env mutation), leave dead coupling (settings fallback), and add maintenance tax (`mcp_count` redundancy, `Any` types on orchestration state). None are behavior bugs today, but all create maintenance risk or correctness traps as the codebase evolves.

**Outcome:**
- `session_id` lives in `CoSessionState` where its mutability is contractually correct
- Gemini API key is injected via `GoogleModel(provider=GoogleProvider(api_key=...))` — no env mutation
- `get_agent` always receives `config` — no global settings fallback, no `settings` import in `agent.py`
- `mcp_count` field removed; all callers use `len(mcp_servers)` directly
- `CoRuntimeState` fields are properly typed — `Any` replaced with concrete types under `TYPE_CHECKING`

---

## Scope

**In scope:**
- Move `session_id: str` from `CoConfig` to `CoSessionState`; update 8 callsites + evals fixes + test construction
- Replace `os.environ["GEMINI_API_KEY"] = ...` with `GoogleModel(provider=GoogleProvider(api_key=...))` in `build_model()`
- Make `config: CoConfig` required in `get_agent()`; update callers in `tests/test_agent.py`, `tests/test_delegate_coder.py`, and 11 eval files
- Remove `mcp_count: int` from `CoConfig` and all set/read callsites; update `from_settings()` docstring
- Replace `Any` types on `CoRuntimeState.opening_ctx_state` and `.safety_state` with concrete types via `TYPE_CHECKING`

**Out of scope:**
- `CoConfig.from_settings()` bulk copy via `model_dump()` — deferred (see Context)
- Unifying `CheckResult`, `CheckItem`, `DoctorResult`, `RuntimeCheck` into a single type
- Any change to MCP server lifecycle or `discover_mcp_tools`
- Behavior changes to any tool or REPL command
- P4 Gate-3 file cleanup
- Eval `model_settings` sourcing: eval files that previously used `model_settings` from the stale `get_agent()` 4-tuple must source it from `make_eval_settings(None)` — this is a pre-existing breakage; fixing the model_settings wiring in each eval is out of scope for this delivery.

**Deferred follow-ons:**
- `_types.py` extraction: break the `deps.py` ↔ `_history.py` circular import by extracting `OpeningContextState` and `SafetyState` into a shared `_types.py` module — this makes the TASK-8 string-literal annotations proper forward refs resolvable at runtime. Track separately when the circular import becomes a maintenance burden.

---

## High-Level Design

### Group contract: `session_id` belongs in `CoSessionState`

`CoConfig` is the injected read-only settings group. `session_id` is a per-session mutable value written during wakeup. Moving it to `CoSessionState` makes the group contracts enforceable — `CoConfig` can in future be made frozen. Sub-agents intentionally receive a fresh `CoSessionState` and therefore start with `session_id=""`. This is the correct isolation contract.

### Gemini key injection via model constructor

`build_model()` currently returns the bare string `"google-gla:<model_name>"` for gemini — pydantic-ai resolves this string by reading `GEMINI_API_KEY` from the environment. The fix replaces this with direct `GoogleModel` construction using `GoogleProvider(api_key=api_key)`, eliminating the env mutation. The `api_key` flows from `ModelRegistry.from_config()` into `build_model()`. `GoogleModel` and `GoogleProvider` are added to top-level imports. `prepare_provider()` retains its validation guard but loses the env mutation and becomes a pure guard function.

**pydantic-ai API note:** `GeminiModel` (in `pydantic_ai.models.gemini`) is deprecated and fails at runtime. `GoogleGLAProvider` does not exist. Use `GoogleModel` from `pydantic_ai.models.google` and `GoogleProvider` from `pydantic_ai.providers.google`. Verified against the live `.venv`.

### `config: CoConfig` as required parameter

The `settings` fallback in `get_agent()` violates the "no direct Settings import in agent files" rule. Tests and evals must pass `config=CoConfig.from_settings(settings)` — the same pattern as live sessions.

### `mcp_count` removal

`deps.config.mcp_count` and `len(deps.config.mcp_servers)` are always identical. The local variable `mcp_count` inside `check_runtime()` (line 314, counting live reachable servers from probes) is a different value and is unaffected.

### `CoRuntimeState` concrete typing

`OpeningContextState` and `SafetyState` are already imported in `_bootstrap.py` with no circular import issues. Adding them to `deps.py` under `TYPE_CHECKING` with string-literal annotations removes the `Any` annotations without changing runtime behavior. **Important:** since `CoDeps` is a plain dataclass, callers must NOT call `get_type_hints()` on `CoRuntimeState` fields — the string-literal annotations will fail to resolve at runtime due to the circular import. pydantic-ai does not call `get_type_hints()` on `deps_type`. A future follow-on should break the circular dependency properly by extracting a `_types.py` module.

---

## Implementation Plan

### TASK-1: Move `session_id` from `CoConfig` to `CoSessionState`

**files:**
- `co_cli/deps.py`
- `co_cli/bootstrap/_bootstrap.py`
- `co_cli/bootstrap/_check.py`
- `co_cli/tools/capabilities.py`
- `tests/test_delegate_coder.py`
- `tests/test_bootstrap.py`
- `evals/_deps.py`

**done_when:**
`grep -rn "config\.session_id\|config_defaults.*session_id" co_cli/ tests/ evals/` returns no matches.
`python -c "from co_cli.deps import CoSessionState; s = CoSessionState(); assert hasattr(s, 'session_id')"` exits cleanly.
`python -c "from evals._deps import make_eval_deps; make_eval_deps(session_id='test-eval')"` exits cleanly (verifies all three live-bug fixes and the `overrides` pop path in step 6).

**prerequisites:** none

Steps:
1. In `deps.py`: remove `session_id: str = ""` from `CoConfig` (line 92); add `session_id: str = ""` to `CoSessionState` (after the existing fields, before `active_skill_name`); update `from_settings()` docstring (line 169) to remove `session_id` from the exclusion list. **Note: `deps.py:169` is a shared edit site — TASK-7 also removes `mcp_count` from the same docstring line. Apply only the `session_id` removal here; the `mcp_count` removal is in TASK-7 step 1.**
2. In `_bootstrap.py` `restore_session()`: change `deps.config.session_id = ...` (×2, lines 103 and 110) → `deps.session.session_id = ...`
3. In `_check.py` `check_runtime()` line 356: change `"session_id": deps.config.session_id` → `"session_id": deps.session.session_id`
4. In `capabilities.py` line 59: change `ctx.deps.config.session_id` → `ctx.deps.session.session_id`
5. In `test_delegate_coder.py`:
   - Lines 65–69: remove `session_id="parent-session"` from the `CoConfig(...)` call. Then in the **existing** `session=CoSessionState(...)` call at lines 70–81 (which already has six fields: `session_tool_approvals`, `active_skill_env`, `skill_tool_grants`, `drive_page_tokens`, `session_todos`, `skill_registry`), add `session_id="parent-session"` as a seventh keyword argument. Do NOT create a new `session=` kwarg on `CoDeps` — there is already one; add `session_id=` inside the existing `CoSessionState(...)`.
   - Line 100: change `assert isolated.config.session_id == "parent-session"` → `assert isolated.session.session_id == ""` and add comment: `# sub-agents get fresh CoSessionState — session_id is not inherited (correct isolation behavior)`
6. In `tests/test_bootstrap.py`: change `deps.config.session_id` → `deps.session.session_id` in all three assertions at lines 151, 153, and 156.
7. In `evals/_deps.py`:
   - At the top of `make_eval_deps()`, alongside the existing service-field pops (lines 36–39), add: `session_id_override = overrides.pop("session_id", "eval")` — this pops `session_id` from `overrides` before `config_defaults.update(overrides)` runs, preventing `TypeError: unexpected keyword argument 'session_id'` when callers pass `make_eval_deps(session_id=...)`
   - Remove `"session_id": "eval"` from `config_defaults` (line 42) — it is now handled via `session_id_override`
   - Replace `"role_models": {k: list(v) for k, v in s.role_models.items()}` (line 61) → `"role_models": dict(s.role_models)` (live TypeError bug — `list(ModelEntry)` fails at runtime)
   - Fix `"ollama_host": s.ollama_host` → `"llm_host": s.llm_host` (line 63, live AttributeError bug)
   - Change `session=CoSessionState(skill_registry=skill_registry)` (line 75) → `session=CoSessionState(session_id=session_id_override, skill_registry=skill_registry)`
   - Update the `make_eval_deps()` docstring to remove `session_id=` from the example call — it is no longer a `CoConfig` field and must be passed as an override that gets routed to `CoSessionState`

---

### TASK-4: Inject Gemini API key via model constructor, remove `os.environ` mutation

**files:**
- `co_cli/_model_factory.py`

**done_when:**
`grep -n "GEMINI_API_KEY" co_cli/_model_factory.py` returns no matches.
`grep -n "GoogleModel\|GoogleProvider" co_cli/_model_factory.py` returns ≥1 match at the top-level import block (not inside a function body).
`python -c "from co_cli._model_factory import build_model; from co_cli.config import ModelEntry; from pydantic_ai.models.google import GoogleModel; m, _ = build_model(ModelEntry(model='gemini-2.0-flash'), 'gemini', '', api_key='dummy'); assert isinstance(m, GoogleModel)"` exits cleanly.
`grep -n "api_key=config.llm_api_key" co_cli/_model_factory.py` returns ≥1 match (verifies step 4 — `ModelRegistry.from_config()` wires the key through).

**prerequisites:** none

**Note on current code:** `build_model()` returns the bare string `"google-gla:{model_name}"` for gemini at line 316. pydantic-ai resolves this string by reading `GEMINI_API_KEY` from the environment (set by `prepare_provider()` at line 46). The fix replaces the string return with a real `GoogleModel` object and removes the env mutation.

**Note on pydantic-ai API:** `GeminiModel` from `pydantic_ai.models.gemini` is deprecated and fails at runtime with `AttributeError: 'Client' object has no attribute 'base_url'` when any Google env var is present. `GoogleGLAProvider` does not exist in the installed package. Correct API: `GoogleModel` + `GoogleProvider`. Verified against the live `.venv`.

**Note on `prepare_provider()` in `agent.py`:** `agent.py:101` calls `prepare_provider(provider_name, _cfg.llm_api_key)` (after TASK-5 renames `_cfg` to `config`). After the fix, `prepare_provider()` retains its validation guard (raises `ValueError` if key missing for gemini) but no longer sets the env var — it becomes a pure validation function. The `prepare_provider()` call in `ModelRegistry.from_config()` at line 76 is also intentionally retained as a validation guard at registry creation time — do not remove it. `agent.py` does not call `build_model()` and does not need changes for this task.

Steps:
1. In `_model_factory.py` top-level imports: add (alongside the existing provider imports):
   ```python
   from pydantic_ai.providers.google import GoogleProvider
   from pydantic_ai.models.google import GoogleModel
   ```
2. In `_model_factory.py` `build_model()`: add `api_key: str | None = None` parameter to the function signature; update the return type annotation from `tuple[OpenAIChatModel | OllamaNativeModel | str, ModelSettings | None]` → `tuple[OpenAIChatModel | OllamaNativeModel | GoogleModel, ModelSettings | None]`; in the `gemini` branch (line 304), replace:
   ```python
   return f"google-gla:{model_name}", model_settings
   ```
   with:
   ```python
   google_model = GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
   return google_model, model_settings
   ```
3. In `_model_factory.py` `prepare_provider()` (line 36): remove the `os.environ["GEMINI_API_KEY"] = llm_api_key` line; update the docstring to reflect it is now a pure validation guard. The validation guard stays:
   ```python
   if not llm_api_key:
       raise ValueError("llm_api_key is required in settings when llm_provider is 'gemini'.")
   ```
4. In `_model_factory.py` `ModelRegistry.from_config()` (line 80): update the `build_model()` call to pass `api_key`:
   ```python
   model, settings = build_model(entry, config.llm_provider, config.llm_host, api_key=config.llm_api_key)
   ```
   The `prepare_provider()` call at line 76 is retained as-is — it is the validation-only guard at registry creation time.

---

### TASK-5: Make `config` required in `get_agent()`; update all callers

**files:**
- `co_cli/agent.py`
- `tests/test_agent.py`
- `tests/test_delegate_coder.py`
- `evals/eval_tool_chains.py`
- `evals/eval_signal_analyzer.py`
- `evals/eval_conversation_history.py`
- `evals/eval_knowledge_pipeline.py`
- `evals/eval_memory_proactive_recall.py`
- `evals/eval_safety_abort_marker.py`
- `evals/eval_jeff_learns_finch.py`
- `evals/eval_safety_grace_turn.py`
- `evals/eval_memory_signal_detection.py`
- `evals/eval_signal_detector_approval.py`

**done_when:**
`grep -n "config if config is not None" co_cli/agent.py` returns no matches.
`grep -n "from co_cli.config import settings" co_cli/agent.py` returns no matches.
`uv run pytest tests/test_agent.py tests/test_delegate_coder.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-ap-t5.log` passes.

**prerequisites:** none

**Note on current code:** `agent.py:83` has `config: CoConfig | None = None`; `agent.py:97` has `_cfg: CoConfig = config if config is not None else CoConfig.from_settings(settings)`. The `settings` import at `agent.py:10` is on the same line as `ROLE_REASONING, ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS` — `settings` must be removed; the other four must be preserved.

**Note on test callers:**
- `tests/test_agent.py` lines 84, 91, 105, 149: `get_agent()` called without `config` — all need `config=CoConfig.from_settings(settings)`
- `tests/test_agent.py` lines 118, 128: already pass `config=` explicitly — no change needed
- `tests/test_delegate_coder.py` line 18: module-level `_AGENT, _, _ = get_agent()` — needs `config=CoConfig.from_settings(settings)`

**Note on imports in `test_agent.py`:** `CoConfig` is currently imported only inside `test_instructions_reevaluated_on_turn2()` (line 140). After this task, multiple top-level call sites need it — add `from co_cli.deps import CoConfig` to the module-level imports. `settings` is already imported at line 10.

**Note on eval callers:** 11 eval files call `get_agent()` without `config` and use a stale 4-value unpack (`agent, model_settings, _, _ = get_agent()`). `get_agent()` returns a 3-tuple — these evals are already broken before this task (wrong unpack count). The fix adds `config=` and corrects the unpack to 3-value. Files that captured `model_settings` from the stale API must source it from `make_eval_settings(None)` going forward — this is a pre-existing issue outside this task's scope; leave a `# TODO: source model_settings from make_eval_settings()` comment at those sites.

**Note for implementer:** `test_instructions_reevaluated_on_turn2` (test_agent.py:133) calls `get_agent(config=CoConfig.from_settings(settings))` at line 149 and also constructs `deps = CoDeps(services=..., config=CoConfig())` at line 151 with bare defaults. These two configs are independent: the agent is built from real settings-derived config, but the `deps` used for `agent.run()` uses a minimal `CoConfig()`. This is pre-existing; the test exercises instruction evaluation (not config field reads) and remains correct as-is.

Steps:
1. In `agent.py`: change `config: CoConfig | None = None` → `config: CoConfig`; remove the fallback line `_cfg = config if config is not None else CoConfig.from_settings(settings)`; replace all `_cfg.` references with `config.`; remove `settings` from the `from co_cli.config import ...` line (keep all other imports: `ROLE_REASONING`, `ROLE_CODING`, `ROLE_RESEARCH`, `ROLE_ANALYSIS`)
2. In `tests/test_agent.py`: add `from co_cli.deps import CoConfig` to module-level imports (alongside the existing `from co_cli.config import WebPolicy, settings` at line 10); for each `get_agent()` call that omits `config` (lines 84, 91, 105, 149), add `config=CoConfig.from_settings(settings)`. Remove the now-duplicate local `from co_cli.deps import CoConfig` import inside `test_instructions_reevaluated_on_turn2()`.
3. In `tests/test_delegate_coder.py`: add `from co_cli.config import settings` to imports (alongside the existing `from co_cli.config import ModelEntry`); `CoConfig` is already imported at the module level — no change needed; update `_AGENT, _, _ = get_agent()` at line 18 to `_AGENT, _, _ = get_agent(config=CoConfig.from_settings(settings))`
4. In each of the 11 eval files listed above: add `from co_cli.config import settings` and `from co_cli.deps import CoConfig` to imports (alongside existing `co_cli` imports); change each `agent, model_settings, _, _ = get_agent()` (and any variant) to `agent, _, _ = get_agent(config=CoConfig.from_settings(settings))`; at lines that previously used `model_settings` add a `# TODO: source model_settings from make_eval_settings()` comment. The eval `_deps.py` docstring references `get_agent()` for model_settings sourcing — update accordingly.

---

### TASK-7: Remove redundant `mcp_count` field from `CoConfig`

**files:**
- `co_cli/deps.py`
- `co_cli/bootstrap/_bootstrap.py`
- `co_cli/bootstrap/_check.py`
- `evals/_deps.py`

**done_when:**
`grep -rn "\.mcp_count" co_cli/ evals/ tests/` returns no matches. (The local variable `mcp_count` inside `check_runtime()` at line 314 counting live servers is unaffected — that is not a field access.)

**prerequisites:** none

Steps:
1. In `deps.py`: remove `mcp_count: int = 0` field from `CoConfig` (line 153); update the `from_settings()` docstring (line 169) to remove `mcp_count` from the exclusion list. **Note: `deps.py:169` is a shared edit site — TASK-1 also removes `session_id` from the same docstring line. Apply only the `mcp_count` removal here; the `session_id` removal is in TASK-1 step 1.**
2. In `_bootstrap.py` `create_deps()` line 34: remove `mcp_count=len(settings.mcp_servers)` from the `dataclasses.replace()` call.
3. In `_check.py` `check_runtime()`:
   - Line 363: replace `"mcp_mode": "mcp" if deps.config.mcp_count > 0 else "native-only"` → `"mcp_mode": "mcp" if len(deps.config.mcp_servers) > 0 else "native-only"`
   - Line 379: replace `if deps.config.mcp_count == 0:` → `if len(deps.config.mcp_servers) == 0:`
4. In `evals/_deps.py`: remove `"mcp_count": len(s.mcp_servers)` from `config_defaults` (line 60).

---

### TASK-8: Replace `Any` types on `CoRuntimeState` with concrete types

**files:**
- `co_cli/deps.py`

**done_when:**
`grep -n "Any" co_cli/deps.py` does not match `opening_ctx_state` or `safety_state` field annotations.
`python -c "from co_cli.deps import CoDeps"` exits cleanly.

**prerequisites:** none

Steps:
1. Confirm exact class names: `OpeningContextState` and `SafetyState` are imported at `_bootstrap.py:8` — confirmed by scan.
2. In `deps.py`: add to the `TYPE_CHECKING` block (currently lines 64–66):
   ```python
   from co_cli.context._history import OpeningContextState, SafetyState
   ```
3. Directly above the `opening_ctx_state` field line: add the comment:
   ```python
   # TYPE_CHECKING-only forward refs — get_type_hints() is unsafe on these fields.
   # TODO: resolve by extracting a _types.py module to break the circular import properly.
   ```
4. Change `opening_ctx_state: Any = field(default=None, repr=False)` → `opening_ctx_state: "OpeningContextState | None" = field(default=None, repr=False)`
5. Change `safety_state: Any = field(default=None, repr=False)` → `safety_state: "SafetyState | None" = field(default=None, repr=False)`
6. Verify no circular import: `python -c "from co_cli.deps import CoDeps"` must exit cleanly.

---

## Testing

```bash
mkdir -p .pytest-logs

# After TASK-1 (session_id move):
uv run pytest tests/test_bootstrap.py tests/test_delegate_coder.py tests/test_capabilities.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-ap-t1.log

# After TASK-4 (Gemini env mutation):
uv run pytest tests/test_google_cloud.py tests/test_bootstrap.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-ap-t4.log

# After TASK-5 (config required):
uv run pytest tests/test_agent.py tests/test_delegate_coder.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-ap-t5.log

# After TASK-7 (mcp_count removal):
uv run pytest tests/test_bootstrap.py tests/test_capabilities.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-ap-t7.log

# Full regression:
uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-bootstrap-ap-full.log
```

No new test files required. All changes are refactors with existing functional coverage.

---

## Open Questions

None — all questions resolved by source inspection.

---

## Summary Table

| # | Category | Issue | Task |
|---|---|---|---|
| 1 | Anti-pattern | `session_id` in `CoConfig` instead of `CoSessionState` — wrong group, forces mutability on read-only config | TASK-1 |
| 2 | Anti-pattern | `prune_stale_approvals()` called inside `create_deps()` — SRP violation in deps constructor | **✓ AP2 shipped** |
| 3 | Anti-pattern | `check_provider`/`check_role_models` run twice at startup — double Ollama HTTP on every boot | **✓ AP3 shipped** |
| 4 | Anti-pattern | `os.environ["GEMINI_API_KEY"]` mutation in `prepare_provider()` — global side effect, leaks across tests | TASK-4 |
| 5 | Anti-pattern | `settings` fallback in `agent.py` — breaks "no direct Settings import" rule, silent global read | TASK-5 |
| 6 | Anti-pattern | `memory_dir` hardcoded in `get_agent()` — ignores `config.memory_dir` | **✓ TASK-6 shipped** |
| 7 | Overdesign | `mcp_count: int` redundant with `len(mcp_servers)` — stale cache risk, derivable on the spot | TASK-7 |
| 8 | Overdesign | `CoRuntimeState.opening_ctx_state` and `.safety_state` typed `Any` — defeats type system on orchestration state | TASK-8 |
| 9 | Overdesign | `CoConfig.from_settings()` is 57-line manual field copy — two-write tax per new Setting field | **deferred — see Context** |

---

## Final — Team Lead

Plan approved. C3 stop conditions met. TL corrections applied post-cycle: (1) TASK-4 class names updated to `GoogleModel`/`GoogleProvider` — `GeminiModel` deprecated and fails at runtime, `GoogleGLAProvider` does not exist (verified against live `.venv`); (2) `tests/test_bootstrap.py` added to TASK-1 files — 3 assertions at lines 151/153/156 use `deps.config.session_id`; (3) 11 eval files added to TASK-5 — stale 4-value unpack + missing `config=` (pre-existing breakage, both issues fixed together).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev bootstrap-antipattern-fixes`
