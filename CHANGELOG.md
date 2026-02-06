# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-02-03

### Added
- **Core CLI**: Interactive chat loop using `typer`, `rich`, and `prompt_toolkit`.
- **Intelligence**: Dual-engine LLM support using `pydantic-ai`.
    - **Local**: Ollama (Llama 3 default) for privacy-first operations.
    - **Cloud**: Google Gemini (via `google-genai`) for complex reasoning.
- **Configuration**:
    - Centralized `settings.json` following XDG standards (`~/.config/co-cli/`).
    - Robust fallback to environment variables (`.env`) for backward compatibility.
- **Sandboxing**: Docker-based execution environment for safe shell command running (`python:3.12-slim`).
- **Tools & Skills**:
    - **Obsidian**: RAG over local Markdown notes (`list_notes`, `read_note`).
    - **Google Drive**: Hybrid semantic/metadata search and file reading.
    - **Communication**: Slack message sending and Gmail drafting (with human-in-the-loop confirmation).
    - **Calendar**: Listing today's events.
- **Observability**:
    - Full OpenTelemetry tracing stored in a local SQLite database (`~/.local/share/co-cli/co-cli.db`).
    - `co logs` command to launch a local Datasette dashboard for trace inspection.
- **System Health**: `co status` command to verify tool connections and configuration.

### Security
- **Privacy**: Local-first design; logs and vector search (if added later) stay on-device.
- **Safety**: High-risk actions (sending emails, posting to Slack, shell commands) require explicit user confirmation.
