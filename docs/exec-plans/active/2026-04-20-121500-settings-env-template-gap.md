# Plan: Gap 1 — Add `${VAR}` Template Expansion to `settings.json`

**Status:** Planned
**Task type:** `code-feature`
**Target gap:** Gap 1 from [`docs/reference/config-co-vs-hermes-gaps.md`](/Users/binle/workspace_genai/co-cli/docs/reference/config-co-vs-hermes-gaps.md:1) — `co-cli` cannot reference environment variables inside `~/.co-cli/settings.json`; Hermes supports `${VAR}` expansion during config load.
**Scope:** `co_cli/config/_core.py`, `tests/test_config.py`

## 1. Context & Motivation

The current config stack is `defaults -> ~/.co-cli/settings.json -> env vars`. It does not support file-local templates such as:

```json
{
  "llm": {
    "provider": "gemini",
    "api_key": "${TEAM_GEMINI_API_KEY}"
  }
}
```

Hermes already supports this pattern in its main config loader.

This plan fills only the `${VAR}` syntax gap. It does not change config format, config precedence, or secret-storage policy.

## 2. Current-State Validation

- [`co_cli/config/_core.py:265`](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:265) `load_config()` reads `settings.json` with `json.load()` and passes the parsed object directly into `Settings.model_validate(...)`.
- [`co_cli/config/_core.py:161`](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:161) `Settings.fill_from_env()` applies env overrides after the file has been loaded, but only for explicitly mapped env vars. It does not expand placeholders embedded in file values.
- [`tests/test_config.py:1`](/Users/binle/workspace_genai/co-cli/tests/test_config.py:1) covers config precedence and validation, but has no behavioral test for `${VAR}` expansion inside file-backed settings.
- [`docs/specs/bootstrap.md:79`](/Users/binle/workspace_genai/co-cli/docs/specs/bootstrap.md:79) documents the current precedence as `settings.json` then env vars. Any implementation must preserve that order.

## 3. Problem & Outcome

**Problem:** file-backed config values are literal-only. Users must either hardcode the final value in `settings.json` or move the entire value to a top-level env override path. That is awkward for nested config and prevents Hermes-style `${VAR}` references in file values.

**Failure cost:** config values that should be expressed as file-local templates cannot be represented in the current loader.

**Outcome:** string values inside `settings.json` may contain `${VAR}` placeholders. During config load, `co-cli` resolves them from the same env source already used by `load_config(_env=...)` and `Settings.fill_from_env()`. Unresolved placeholders remain literal. Explicit env overrides still win.

## 4. Scope

In scope:

- Add recursive `${VAR}` expansion for string values in parsed `settings.json` data.
- Use the same env source passed to `load_config(_env=...)` for deterministic tests and correct precedence.
- Expand nested dict values and list items.
- Leave unresolved placeholders untouched.
- Add focused config tests for expansion, precedence, and `_env`-source correctness.

Out of scope:

- Any config-format change.
- Any secret-file or `.env` introduction.
- Any new env var names.
- Any config precedence change.
- Spec updates as explicit tasks.

## 5. Behavioral Constraints

1. Precedence remains `defaults -> settings.json (after template expansion) -> env overrides`.
2. Expansion applies only to string **values**, not dict keys.
3. Expansion uses the `load_config(_env=...)` mapping when provided; it must not silently read from ambient `os.environ` in tests.
4. Unresolved `${VAR}` placeholders remain verbatim, matching Hermes behavior.
5. Non-string values are untouched.
6. Invalid config still fails in the same `Settings.model_validate(...)` path with file attribution.

## 6. High-Level Design

Add a small helper in [`co_cli/config/_core.py`](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:1):

```python
def _expand_env_templates(value: object, env_source: Mapping[str, str]) -> object:
    ...
```

Behavior:

- If `value` is a `str`, replace every `${NAME}` with `env_source.get("NAME", original_match)`.
- If `value` is a `dict`, recurse over values only.
- If `value` is a `list`, recurse over each item.
- Otherwise return the value unchanged.

Insertion point:

1. `load_config()` reads JSON into `data`.
2. `load_config()` resolves `env_source = _env if _env is not None else os.environ`.
3. `load_config()` applies `_expand_env_templates(data, env_source)`.
4. `Settings.model_validate(..., context={"env": env_source})` runs as today.

This preserves the existing layering:

- file values can depend on env via `${VAR}`
- mapped env overrides in `fill_from_env()` still take final precedence

## 7. Implementation Plan

### TASK-1 — Add recursive template expansion to config load

```text
files:
  - co_cli/config/_core.py

prerequisites: []

done_with:
  - `co_cli/config/_core.py` defines a helper that recursively expands `${VAR}` in string values using an injected env mapping.
  - `load_config()` computes one env source (`_env` if passed, else `os.environ`) and uses it for both template expansion and `Settings.model_validate(..., context={"env": ...})`.
  - Expansion happens after `json.load()` and before `Settings.model_validate(...)`.
  - Dict keys are not expanded. Unresolved placeholders remain literal.

success_signal:
  - A config value such as `"api_key": "${TEAM_KEY}"` is presented to Pydantic as the resolved string when `TEAM_KEY` is set in the selected env source.
```

**Concrete implementation:**

- Add `import re` near the top of [`co_cli/config/_core.py`](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:1).
- Add `_expand_env_templates(value, env_source)` as a private helper near `load_config()`.
- In `load_config()`, compute `env_source` once before validation.
- After successful `json.load()`, replace `data` with `_expand_env_templates(data, env_source)`.
- Pass `context={"env": env_source}` into `Settings.model_validate(...)`.
- Keep malformed JSON handling unchanged.

### TASK-2 — Add focused regression coverage for file-template expansion

```text
files:
  - tests/test_config.py

prerequisites: [TASK-1]

done_with:
  - `tests/test_config.py` covers simple `${VAR}` substitution from file-backed config.
  - Coverage exists for nested dict values and list items.
  - Coverage exists for unresolved placeholders staying literal.
  - Coverage exists for `_env` being the source of expansion, not ambient process env.
  - Coverage exists for explicit env overrides still winning over expanded file values.

success_signal:
  - `load_config()` behavior is locked by tests for both template expansion and precedence.
```

**Concrete implementation:**

- Add one test where `settings.json` contains `{"llm": {"api_key": "${TEAM_KEY}"}}` and `_env={"TEAM_KEY": "secret"}` resolves `settings.llm.api_key == "secret"`.
- Add one test with nested/list content, e.g. MCP server args/env or web domain lists containing `${VAR}`.
- Add one test where the placeholder is missing and the literal `${MISSING}` survives.
- Add one test proving `_env` is authoritative by setting a conflicting real process env var and asserting `load_config(_env=...)` uses the injected mapping.
- Add one test proving explicit mapped env overrides still win, e.g. file `"theme": "${TEAM_THEME}"` with `_env={"TEAM_THEME": "dark", "CO_THEME": "light"}` yields `settings.theme == "light"`.

### TASK-3 — Focused gate for the config loader behavior

```text
files:
  - co_cli/config/_core.py
  - tests/test_config.py

prerequisites: [TASK-2]

done_with:
  - The focused config test target passes after the change.
  - No unrelated config-precedence tests regress.
  - The change does not introduce new env var names, config layers, or validation behavior.

success_signal:
  - The repo has a minimal, behavior-preserving implementation of `${VAR}` support in `settings.json`.
```

**Concrete verification command:**

- `uv run pytest tests/test_config.py`

## 8. Risks & Guardrails

- The main trap is accidentally reading ambient `os.environ` during template expansion while using `_env` for `fill_from_env()`. That would make tests non-deterministic and break the intended contract. The same `env_source` object must drive both steps.
- Expansion must happen on parsed JSON values, not raw file text. Raw-text replacement risks corrupting JSON syntax when values contain quotes or braces.
- Do not let this plan expand into YAML migration, `.env` support, or broader config redesign. Those are separate gaps.

## 9. Execution Order

1. Add `_expand_env_templates(...)` and wire it into `load_config()`.
2. Add focused regression tests in `tests/test_config.py`.
3. Run the focused config gate.

## 10. Done With

This gap is done with when all of the following are true:

- `settings.json` may safely contain `${VAR}` placeholders in string values.
- `load_config(_env=...)` resolves those placeholders from the injected env mapping.
- unresolved placeholders remain literal rather than failing expansion.
- explicit env overrides still take precedence over expanded file values.
- `uv run pytest tests/test_config.py` passes.
