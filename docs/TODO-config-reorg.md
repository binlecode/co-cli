# TODO: Reorganize `co_cli/config.py` into focused config modules

**Slug:** `config-reorg`
**Task type:** `code-refactor`

---

## Context

`co_cli/config.py` currently mixes several distinct responsibilities in one module:

- static defaults for settings fields and role-model policy
- XDG path resolution and directory creation
- Pydantic config schema definitions
- field parsing and validation
- environment variable overlay logic
- layered file loading and deep-merge behavior
- settings persistence
- lazy singleton access for global settings

This makes the file harder to navigate and increases the blast radius of every config
change. Small changes to defaults, schema validation, env precedence, or initialization
all land in the same module and are easy to couple accidentally.

**Current-state validation:**
- `co_cli/config.py` is 581 lines.
- `co_cli/config.py:10` to `co_cli/config.py:47` defines provider role defaults and
  model defaults.
- `co_cli/config.py:51` to `co_cli/config.py:78` defines shell policy and XDG/data paths.
- `co_cli/config.py:110` to `co_cli/config.py:156` defines `MCPServerConfig` and
  `ModelConfig`.
- `co_cli/config.py:161` to `co_cli/config.py:218` defines role constants and dozens of
  unrelated defaults in one flat block.
- `co_cli/config.py:221` to `co_cli/config.py:491` defines `Settings`, parsing validators,
  env overlay behavior, provider-role default injection, and persistence.
- `co_cli/config.py:493` to `co_cli/config.py:581` defines file discovery, layered loading,
  singleton initialization, and module-level lazy access.

---

## Problem & Outcome

**Problem:** the config layer has no clear separation between pure data modeling and runtime
assembly. `Settings` is not just a schema; it also contains env precedence logic and write
logic. Provider-specific role defaults are embedded inside env overlay code. Path constants,
loader behavior, and singleton lifecycle all live beside schema concerns.

**Outcome:** split the config layer into focused modules so each concern has one
home:

- schema types and validation are isolated from IO and precedence
- defaults and role policy live together
- path resolution is centralized
- layered loading and env overlay live in the loader, not in the schema
- public imports become explicit and minimal

The re-org is structural. Runtime behavior and config precedence should not change, but
repo import paths may change if the result is cleaner and more maintainable.

---

## Scope

**In scope:**
- convert `co_cli/config.py` into a package `co_cli/config/`
- adopt a clean public config surface rather than preserving the current wide one
- extract defaults, paths, schema, and loader responsibilities into separate modules
- move env-overlay logic out of `Settings`
- move provider-specific role default resolution out of `Settings`
- keep `get_settings()` and lazy global settings access working
- update imports across the repo
- add or update targeted tests for config loading behavior

**Out of scope:**
- changing config names, env var names, or precedence rules
- redesigning the settings schema itself
- changing `CoConfig` semantics in `deps.py`
- changing DESIGN docs as an implementation task item

---

## Design Constraints

- `co_cli/config/__init__.py` must remain docstring-only per repo policy.
- The re-org must not introduce import-time side effects beyond what already exists.
- The loader remains the only place that assembles precedence layers:
  built-in defaults → user config → project config → env vars.
- `Settings` remains a pure Pydantic schema plus schema-level validators only.
- Provider-specific default role expansion must be a standalone loader concern, not a
  schema validator side effect.
- The public config surface should be intentionally small and explicit; avoid broad
  compatibility re-exports unless a concrete external consumer requires them.

---

## Proposed Module Layout

### `co_cli/config/_schema.py`

Owns:
- `MCPServerConfig`
- `ModelConfig`
- `Settings`
- schema-only validators such as:
  - parsing comma-separated list fields
  - validating reasoning display
  - validating personality membership
  - validating web retry bound relationships
  - validating role-model key membership

Must not own:
- env var reads
- file reads/writes
- path creation
- singleton state

### `co_cli/config/_defaults.py`

Owns:
- `ROLE_REASONING`, `ROLE_SUMMARIZATION`, `ROLE_CODING`, `ROLE_RESEARCH`,
  `ROLE_ANALYSIS`, `ROLE_TASK`
- `VALID_ROLE_NAMES`
- general setting default constants
- model default definitions for Ollama and Gemini
- default MCP server definitions
- helper functions for provider-specific role defaults

Recommended helper surface:

```python
def get_default_role_models(provider: str) -> dict[str, ModelConfig | dict[str, Any] | str]:
    ...
```

This removes provider-role logic from `Settings.fill_from_env()`.

### `co_cli/config/_paths.py`

Owns:
- `APP_NAME`
- `CONFIG_DIR`
- `DATA_DIR`
- `GOOGLE_TOKEN_PATH`
- `ADC_PATH`
- `SETTINGS_FILE`
- `SEARCH_DB`
- `LOGS_DB`
- `ensure_dirs()`
- `find_project_config()`

This keeps XDG path policy in one place and removes path noise from schema/default logic.

### `co_cli/config/_loader.py`

Owns:
- `_deep_merge_settings()`
- env mapping constants
- env overlay helpers
- provider-role default merge helpers
- `load_config()`
- singleton cache
- `get_settings()`
- lazy `settings` accessor support via package-level `__getattr__` in public module

Optional helper split if `_loader.py` grows too large:
- `_env.py` for env-map and overlay helpers

### `co_cli/config/_core.py`

Owns:
- the deliberate repo-facing config API
- `get_settings()`
- lazy singleton state
- `settings`
- `load_config()`
- `Settings`

Because repo policy forbids code in `__init__.py`, `_core.py` is the executable public
entrypoint for repo-internal imports.

---

## Public Surface Strategy

The current module acts as both implementation and public API. After converting `config.py`
into a package, simplify that surface deliberately instead of preserving the old wide one.

Recommended structure:

```text
co_cli/config/
  __init__.py          # docstring only
  _core.py             # minimal repo-facing config API
  _schema.py
  _defaults.py
  _paths.py
  _loader.py
```

Target repo-internal import mapping:

- `from co_cli.config._core import Settings, load_config, get_settings, settings`
- `from co_cli.config._schema import ModelConfig, MCPServerConfig`
- `from co_cli.config._defaults import ROLE_*`
- `from co_cli.config._defaults import REASONING_DISPLAY_*`
- `from co_cli.config._defaults import DEFAULT_*`
- `from co_cli.config._paths import CONFIG_DIR, DATA_DIR, SETTINGS_FILE, SEARCH_DB, LOGS_DB, GOOGLE_TOKEN_PATH, ADC_PATH, project_config_path`

Do not add a compatibility shim unless a concrete consumer requires it. The cleanest path is
repo-wide import rewrites to the explicit `_core.py` surface.

`_core.py` should stay intentionally small. Do not re-export path constants, role constants,
or generic defaults from `_core.py`; callers should import those from their owning modules.

### Final `_core.py` API

Implementation should treat this as fixed unless a concrete blocker appears:

```python
from co_cli.config._core import Settings, get_settings, load_config, settings
```

Nothing else belongs in `_core.py`.

### Final module ownership

Use this as the implementation target rather than rediscovering boundaries during coding:

- `_schema.py`: `Settings`, `ModelConfig`, `MCPServerConfig`
- `_defaults.py`: all `ROLE_*`, all `DEFAULT_*`, `REASONING_DISPLAY_*`,
  `VALID_ROLE_NAMES`, `VALID_REASONING_DISPLAY_MODES`, default MCP server definitions,
  provider-specific role default helper
- `_paths.py`: all path constants, `ensure_dirs()`, `find_project_config()`,
  `project_config_path`
- `_loader.py`: merge helpers, env-map, env overlay, JSON load/save helpers,
  `load_config()`, personality warning hook
- `_core.py`: singleton state, `get_settings()`, `settings`, narrow re-export of
  `Settings` and `load_config()`

---

## Implementation Plan

### TASK-1: Inventory current import surface and rewrite plan

**prerequisites:** none

**What to do:**
- grep the repo for all imports from `co_cli.config`
- group imported names into:
  - schema/types
  - defaults/constants
  - path constants
  - loader/runtime functions
  - lazy `settings`
- map each import to its target owner module using the "Final module ownership" section above
- record any import that does not fit the target ownership as a TODO correction before coding

**files:**
- `co_cli/**`
- `tests/**`
- `docs/TODO-config-reorg.md`

**done_when:**
```bash
rg -n "from co_cli\\.config import|import co_cli\\.config" co_cli tests
```

Produces a reviewed list of imported names and a deliberate `_core.py` API with no ambiguity.

---

### TASK-2: Extract path policy into `_paths.py`

**prerequisites:** [TASK-1]

**What to do:**
- create `co_cli/config/_paths.py`
- move:
  - `APP_NAME`
  - `CONFIG_DIR`
  - `DATA_DIR`
  - `GOOGLE_TOKEN_PATH`
  - `ADC_PATH`
  - `SETTINGS_FILE`
  - `SEARCH_DB`
  - `LOGS_DB`
  - `_ensure_dirs()` → `ensure_dirs()`
  - `find_project_config()`
- keep behavior identical
- update imports in loader/runtime callers

**files:**
- `co_cli/config/_paths.py`
- import callers found in TASK-1

**done_when:**
```bash
uv run python -c "
from co_cli.config._paths import CONFIG_DIR, DATA_DIR, SETTINGS_FILE, find_project_config
assert CONFIG_DIR.name == 'co-cli'
assert DATA_DIR.name == 'co-cli'
assert SETTINGS_FILE.name == 'settings.json'
assert find_project_config() is None or find_project_config().name == 'settings.json'
print('PASS: config paths module')
"
```

---

### TASK-3: Extract defaults and role policy into `_defaults.py`

**prerequisites:** [TASK-1]

**What to do:**
- create `co_cli/config/_defaults.py`
- move all generic defaults from the flat constants block
- move model default definitions and default MCP server definitions
- move role constants and `VALID_ROLE_NAMES`
- add a single helper for provider-specific default role-model resolution
- avoid dict mutation aliasing; if defaults are compound structures, return fresh values

**files:**
- `co_cli/config/_defaults.py`

**done_when:**
```bash
uv run python -c "
from co_cli.config._defaults import ROLE_REASONING, VALID_ROLE_NAMES, get_default_role_models
assert ROLE_REASONING in VALID_ROLE_NAMES
ollama = get_default_role_models('ollama-openai')
gemini = get_default_role_models('gemini')
assert 'reasoning' in ollama
assert 'reasoning' in gemini
print('PASS: defaults and role policy extracted')
"
```

---

### TASK-4: Extract schema into `_schema.py` and make `Settings` pure

**prerequisites:** [TASK-2, TASK-3]

**What to do:**
- create `co_cli/config/_schema.py`
- move `MCPServerConfig`, `ModelConfig`, and `Settings`
- keep only schema-level validators on `Settings`
- remove env reads from `Settings`
- remove provider-role default injection from `Settings`
- delete `Settings.save()`
- if persistence remains needed later, add a standalone loader helper instead of restoring a
  model instance method

**files:**
- `co_cli/config/_schema.py`
- any callers relying on `Settings.save()`

**done_when:**
```bash
uv run python -c "
from co_cli.config._schema import Settings
s = Settings.model_validate({})
assert s.personality
assert isinstance(s.role_models, dict)
assert not hasattr(Settings, 'save')
print('PASS: schema validates without loader side effects')
"
```

---

### TASK-5: Move env overlay and layered loading into `_loader.py`

**prerequisites:** [TASK-2, TASK-3, TASK-4]

**What to do:**
- create `co_cli/config/_loader.py`
- move `_deep_merge_settings()` into loader
- define env-map constants there
- implement helpers such as:
  - `_apply_env_overrides(data, env_source)`
  - `_merge_default_role_models(data)`
  - `_load_json_file(path)`
- make `load_config()` assemble config in this order:
  1. read user config if present
  2. deep-merge project config if present
  3. inject provider-based role defaults for missing roles
  4. apply env overrides
  5. validate via `Settings.model_validate(...)`
  6. run non-blocking personality file diagnostics
- preserve existing error attribution on validation failures

**files:**
- `co_cli/config/_loader.py`

**done_when:**
```bash
uv run python -c "
from pathlib import Path
from co_cli.config._loader import load_config
s = load_config(_project_dir=Path.cwd(), _env={})
assert s.role_models.get('reasoning') is not None
print('PASS: layered loader works')
"
```

---

### TASK-6: Introduce package public surface and migrate imports

**prerequisites:** [TASK-2, TASK-3, TASK-4, TASK-5]

**What to do:**
- convert `co_cli/config.py` to package form under `co_cli/config/`
- keep `co_cli/config/__init__.py` docstring-only
- add `co_cli/config/_core.py` with only `Settings`, `load_config`, `get_settings`, `settings`
- move lazy singleton access (`get_settings()`, `_settings`, `settings` accessor behavior)
  into `_core.py` or `_loader.py`
- rewrite repo-internal imports to use the target owner modules from the Public Surface
  Strategy section
- remove all `from co_cli.config import ...` imports across `co_cli/` and `tests/`

**files:**
- `co_cli/config/__init__.py`
- `co_cli/config/_core.py`
- repo-wide import callers

**done_when:**
```bash
rg -n "from co_cli\\.config import|import co_cli\\.config" co_cli tests
```

Returns no matches.

---

### TASK-7: Add regression tests for config loading behavior

**prerequisites:** [TASK-5, TASK-6]

**What to do:**
- add or update focused tests that exercise real config loading behavior without mocks:
  - user config only
  - user + project deep merge
  - env overrides beat file settings
  - provider-specific role defaults are injected for missing roles
  - invalid role key still fails validation
  - `web_http_backoff_base_seconds > web_http_backoff_max_seconds` still fails
  - `Settings` has no `save()` method
  - `get_settings()` still initializes lazily and returns a singleton within process
- use `_env` and `_project_dir` injection points rather than mutating global process state
- keep tests production-path only

**files:**
- `tests/test_config.py` or the existing closest config-loading test module

**done_when:**
```bash
mkdir -p .pytest-logs && uv run pytest tests/test_config.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-config-reorg.log
```

---

### TASK-8: Full integration sweep and stale-reference cleanup

**prerequisites:** [TASK-6, TASK-7]

**What to do:**
- grep for stale references to removed helpers or old paths
- verify no code still expects env overlay inside `Settings`
- verify no code still calls removed `Settings.save()` if it was eliminated
- run the full test suite before considering the refactor done

**files:**
- whole repo

**done_when:**
```bash
mkdir -p .pytest-logs && uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-config-reorg-full.log
```

---

## Risks & Review Focus

- **Module-to-package migration risk:** `co_cli.config` is imported widely today. The new
  surface must be chosen deliberately so the rewrite reduces sprawl rather than recreating it.
- **Hidden side-effect risk:** the current lazy `settings` accessor hides lifecycle behavior.
  The refactor must preserve initialization timing.
- **Default-role precedence risk:** moving role default resolution out of `Settings` can
  subtly change precedence if merge order is wrong.
- **Mutable default risk:** model default dicts and default MCP server objects must not be
  shared mutably across settings instances.
- **Path policy drift risk:** path constants must remain exactly aligned with current XDG
  behavior.

Reviewer should explicitly verify:

- file precedence behavior is unchanged
- env precedence behavior is unchanged
- role model defaults remain correct for both Ollama and Gemini
- public import surface is intentionally rewritten repo-wide and stays small
- `_core.py` exports only `Settings`, `load_config`, `get_settings`, `settings`
- `__init__.py` policy is still satisfied

---

## Suggested Execution Order

1. Complete TASK-1 first; the new `_core.py` surface determines the rest.
2. Extract `_paths.py` and `_defaults.py`; these are low-risk and reduce noise.
3. Extract `_schema.py`; keep validation behavior unchanged.
4. Extract `_loader.py`; move env logic and role default injection there.
5. Convert to package form and rewrite imports in one focused pass.
6. Run targeted config tests.
7. Run full suite and fix stale references immediately.

---

## Ship Criteria

The refactor is done when all of the following are true:

- `co_cli/config.py` no longer exists as a monolithic implementation module
- schema, defaults, paths, and loader responsibilities each have one clear home
- `Settings` is a pure schema type with no env IO or file IO responsibilities
- `_core.py` exports only the four names declared in this TODO
- all repo imports resolve cleanly against the declared owner modules in this TODO
- targeted config tests pass
- full pytest suite passes
- no stale references remain to removed helpers or old import paths
