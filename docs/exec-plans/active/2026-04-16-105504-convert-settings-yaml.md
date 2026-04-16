Task type: code-feature

# Plan: Convert settings.json to settings.yaml (Hermes Pattern)

## Context
The project currently uses `settings.json` for all user configurations via Pydantic. Based on a recent peer review (`docs/reference/RESEARCH-peer-config-survey.md`), `co-cli` lacks support for comments in configuration and environment variable templating (`${VAR}`)—features supported by Hermes using YAML. We need to transition `settings.json` to `settings.yaml` while adding template expansion and migrating existing users transparently.
Code accuracy verification shows `settings.json` is explicitly referenced in `co_cli/config/_core.py`, `co_cli/bootstrap/render_status.py`, `co_cli/config/_llm.py`, and `co_cli/config/_observability.py`.

## Problem & Outcome
**Problem:** `settings.json` does not support comments, preventing users from documenting their configuration overrides. It also requires hardcoding values rather than securely referencing system environment variables within the file.
**Failure cost:** Users either abandon configuring complex settings due to lack of readability/commenting, or accidentally leak API keys by committing them in dotfile backups.
**Outcome:** Configuration uses `settings.yaml`, supporting both `# comments` and `${VAR}` templating. Existing users' `settings.json` files are automatically migrated.

## Scope
- Switch `SETTINGS_FILE` constant to `settings.yaml`.
- Add `ruamel.yaml` to dependencies.
- Update `co_cli/config/_core.py` to parse YAML instead of JSON.
- Implement post-parsing template expansion (recursively traverse dictionary values and apply `re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), val)` only on strings, matching Hermes' exact logic).
- **Remove** `Settings.save()` entirely to prevent runtime state from destructively overwriting user templates/YAML.
- Implement an automatic one-time migration from `settings.json` to `settings.yaml`.
- Update static references (log messages, errors) in `_llm.py`, `_observability.py`, and `render_status.py`.
- Update tests in `tests/test_config.py` and `tests/test_status.py`.

## Behavioral Constraints
- **Migration:** Must be non-destructive. Read `settings.json`, write `settings.yaml`, and rename the JSON file to `settings.json.YYYYMMDD-HHMMSS.bak`.
- **Fail-Fast Validation:** Invalid YAML or unparseable JSON during migration must block startup identically to invalid JSON. Handle `json.JSONDecodeError` gracefully during migration by throwing a clear configuration error.
- **Variable Expansion:** Expansion must happen *after* parsing (on the resulting dict) to prevent YAML syntax corruption from injected characters. Unknown `${VAR}` templates left unexpanded by the regex replacement will fall through to Pydantic validation, preserving unresolved templates as strings (matching Hermes behavior).

## High-Level Design
1. **Dependency:** Add `ruamel.yaml` to `pyproject.toml` using `uv`.
2. **File Loading/Migration:** In `load_config()` of `_core.py`, check if `settings.yaml` exists. If not, check if `settings.json` exists. If it does, run the migration (load JSON, write YAML using `ruamel.yaml`, rename JSON to `.bak` with a timestamp).
3. **Template Expansion:** Create a helper function `_expand_env_vars(data)` that recursively walks the parsed YAML dictionary and applies Hermes' regex replacement (`re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), val)`) to string values.
4. **Immutability:** Delete `Settings.save()` from `_core.py`. Future programmatic edits (if needed) will use a dedicated AST-mutator function, avoiding model-dump overwrite issues. Add `*.yaml` and `*.bak` to `.gitignore` if not already ignored globally.

## Tasks

- **TASK-1: Add ruamel.yaml dependency**
  - `files:` `pyproject.toml`
  - `done_when:` `uv run python -c "import ruamel.yaml"` succeeds.
  - `success_signal:` N/A (Build configuration change).
  
- **TASK-2: Core config YAML migration and template support**
  - `files:` `co_cli/config/_core.py`, `tests/test_config.py`
  - `prerequisites:` [TASK-1]
  - `done_when:` `uv run pytest tests/test_config.py` passes, verifying templates are safely expanded post-parse and `Settings.save()` is removed.
  - `success_signal:` The application can start and load settings containing YAML comments and `${VAR}` expansions.

- **TASK-3: Update status rendering and static text references**
  - `files:` `co_cli/bootstrap/render_status.py`, `co_cli/config/_llm.py`, `co_cli/config/_observability.py`, `tests/test_status.py`
  - `prerequisites:` [TASK-2]
  - `done_when:` `uv run co status` prints checks related to `settings.yaml` (not `.json`) and `uv run pytest tests/test_status.py` passes.
  - `success_signal:` User-facing health checks, error messages, and documentation references accurately name `settings.yaml`.

## Testing
- Unit tests will verify `load_config` successfully parses YAML.
- Unit tests will verify the regex replacement correctly and safely injects environment variables into the config dictionary values without altering structure, while leaving unresolved variables intact.
- Verify migration logic by creating a dummy `settings.json` and asserting it is converted to `settings.yaml` on startup without data loss, catching malformed JSON explicitly.

## Open Questions
- None.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev <slug>`
