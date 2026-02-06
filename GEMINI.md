# GEMINI.md

This file provides guidance to the Gemini agent when working with code in this repository.

## Project Overview

**Co** is an opinionated, privacy-first AI agent that lives in your terminal. It connects your local tools (Obsidian, Shell) with cloud services (Google Drive, Slack, Gmail) using a local LLM "Brain" and a Docker-based "Body" for safe execution.

## Project Structure

```
co-cli/
├── co_cli/                 # Main package
│   ├── main.py             # CLI entry: chat, status, logs commands
│   ├── agent.py            # Pydantic AI agent factory & tool registration
│   ├── config.py           # XDG-compliant settings management (Settings.json)
│   ├── sandbox.py          # Docker wrapper for safe command execution
│   ├── telemetry.py        # OpenTelemetry to SQLite exporter
│   └── tools/              # Agent tools
│       ├── shell.py        # run_shell_command (Docker sandbox)
│       ├── notes.py        # list_notes, read_note (Obsidian)
│       ├── drive.py        # search_drive, read_drive_file (Google Drive)
│       └── comm.py         # Slack, Gmail, Calendar tools
├── tests/                  # Functional test suite
├── docs/                   # Documentation & Specs
│   ├── SPEC-CO-CLI.md      # Architecture & testing policy
│   ├── TODO-local-settings.md # Refactoring progress
│   ├── WORK-CO-CLI-pair-programming.md # Pair programming notes
│   └── FIX-findings-codex.md # Bug findings
├── CHANGELOG.md            # Release history
├── README.md               # Usage guide & setup
├── settings.example.json   # Example configuration
└── pyproject.toml          # Project metadata & dependencies
```

## Build & Development Commands

```bash
uv sync                              # Install dependencies
co chat                              # Start interactive chat
co status                            # Check system health
co logs                              # View telemetry (Datasette)
uv run pytest                        # Run functional tests
```

## Configuration (XDG Standard)

- **Config**: `~/.config/co-cli/settings.json`
- **Data**: `~/.local/share/co-cli/co-cli.db`
- **History**: `~/.local/share/co-cli/history.txt`

### settings.json Schema
```json
{
  "llm_provider": "gemini",
  "gemini_api_key": "...",
  "gemini_model": "gemini-2.0-flash",
  "ollama_host": "http://localhost:11434",
  "ollama_model": "llama3",
  "obsidian_vault_path": "...",
  "slack_bot_token": "...",
  "docker_image": "python:3.12-slim",
  "auto_confirm": false,
  "gcp_key_path": "..."
}
```

## Key Principles

1. **Privacy First**: Local LLM (Ollama) preferred; logs stored locally.
2. **Safe Execution**: Shell commands run in a transient Docker sandbox.
3. **Human-in-the-Loop**: Confirmations required for high-risk tools (shell, Slack, email).
4. **Functional Testing**: No mocks. Tests must verify real side effects.
5. **Stability**: Python 3.12 is used for production-grade reliability in 2026.
