# RESEARCH: Config Gaps — `co-cli` vs Hermes

Code-verified comparison of the current `co-cli` config loader against Hermes.

## Sources

### `co-cli`

- [`co_cli/config/_core.py`](/Users/binle/workspace_genai/co-cli/co_cli/config/_core.py:1)
- [`docs/specs/bootstrap.md`](/Users/binle/workspace_genai/co-cli/docs/specs/bootstrap.md:74)
- [`tests/test_config.py`](/Users/binle/workspace_genai/co-cli/tests/test_config.py:1)

### Hermes

- `/Users/binle/workspace_genai/hermes-agent/hermes_cli/config.py`
- `/Users/binle/workspace_genai/hermes-agent/tests/hermes_cli/test_config_env_expansion.py`
- `/Users/binle/workspace_genai/hermes-agent/AGENTS.md`

## Verified Current State

### `co-cli`

- Main config file: `~/.co-cli/settings.json`
- Precedence: `defaults -> ~/.co-cli/settings.json -> env vars`
- Typed Pydantic settings model
- No `${VAR}` expansion inside file-backed config values
- No separate secrets file
- Plain JSON, so no comments

### Hermes

- Main config file: `~/.hermes/config.yaml`
- Separate secrets file: `~/.hermes/.env`
- Recursive `${VAR}` expansion during config load
- YAML config, so comments are supported

## Gaps

### 1. `${VAR}` template expansion in config values

Hermes expands `${VAR}` inside loaded config values. `co-cli` does not: it parses `settings.json`, then applies only explicit mapped env overrides.

Example that works in Hermes but not in `co-cli`:

```json
{
  "llm": {
    "provider": "gemini",
    "api_key": "${TEAM_GEMINI_API_KEY}"
  }
}
```

**ROI:** medium. Small loader change, direct Hermes parity, no file-format migration required.

### 2. Separate secrets file

Hermes splits settings and secrets across `config.yaml` and `.env`. `co-cli` supports env vars, but still allows secret-bearing fields in `settings.json`, so secrets can end up persisted in the main config file.

**ROI:** medium to high. Useful if the goal is safer shared config templates or less secret sprawl, but broader than `${VAR}` expansion.

### 3. Commentable config

Hermes uses YAML, so config can be documented inline. `co-cli` uses plain JSON, so it cannot.

**ROI:** low to medium alone. Mostly a readability and maintainability improvement, and likely bundled with a format change such as YAML or JSONC.

## Bottom Line

Against the latest `co-cli` implementation, the real Hermes config gaps are:

1. `${VAR}` expansion in file-backed config values
2. a first-class split between settings and secrets
3. a commentable config format
