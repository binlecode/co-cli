# Refactoring Plan: Migrate Configuration to XDG Standard

**Goal**: Centralize all configuration into a single `settings.json` file at `~/.config/co-cli/settings.json` (XDG compliant).

## 1. Analysis & Preparation
- [x] **Identified Paths**:
    - `.co_logs.db` -> Moved to XDG Data dir.
- [x] **Dependency**: Added `platformdirs` to `pyproject.toml`.
- [x] **Schema Definition**:
    - Simplified Schema:
      ```json
      {
        "gemini_api_key": "AIza...",
        "obsidian_vault_path": "/Users/binle/Documents/obsidian_vault",
        "slack_bot_token": "xoxb-...",
        "ollama_host": "http://localhost:11434",
        "ollama_model": "llama3",
        "llm_provider": "gemini"
      }
      ```
      *No other files (like gcp-key.json) should be required in the config folder.*

## 2. Implementation Steps
- [x] **Central Config Module**:
    - Created `co_cli/config.py`.
    - `CONFIG_DIR`: `~/.config/co-cli/` (via `platformdirs.user_config_dir`).
    - `DATA_DIR`: `~/.local/share/co-cli/` (via `platformdirs.user_data_dir`).
    - `SETTINGS_FILE`: `CONFIG_DIR / "settings.json"`.
- [x] **Environment Fallback**:
    - Implemented Pydantic validator to fill missing settings from environment variables.
- [x] **Update Usage**:
    - Refactored `agent.py`, `main.py`, `telemetry.py`, and tools to use `co_cli.config.settings`.

## 3. Updates
- [x] Update `README.md` to reflect the clean XDG installation and `settings.json` schema.
- [x] Update `SPEC-CO-CLI.md` if necessary.

## 4. Verification
- [ ] Test clean install (ensure `~/.config/co-cli` is created).
- [ ] Verify `XDG_CONFIG_HOME` and `XDG_DATA_HOME` overrides.