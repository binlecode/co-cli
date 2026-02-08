# Co - The Production-Grade Personal Assistant CLI

**Co** is an opinionated, privacy-first AI agent that lives in your terminal. It connects your local tools (Obsidian, Shell) with cloud services (Google Drive, Slack, Gmail) using a local LLM "Brain" and a sandboxed "Body" for safe execution.

Designed for developers who want a personal assistant that:
1.  **Respects Privacy**: Runs on local models (Ollama) or cloud (Gemini).
2.  **Is Safe**: Executes commands inside a Docker sandbox (with subprocess fallback).
3.  **Is Observable**: Traces every thought and tool call via OpenTelemetry.
4.  **Is Useful**: Connects to your actual work context (Notes, Drive, Calendar).

---

## Prerequisites

Before you begin, ensure you have the following installed:

1.  **Python 3.12+**
    *   The repo includes a `.python-version` file for `pyenv` users.

2.  **[Ollama](https://ollama.com/)** (optional): To serve a local LLM.
    ```bash
    ollama run llama3
    ```
    *You can use other models by updating `~/.config/co-cli/settings.json`.*

3.  **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (recommended): For the sandboxed execution environment.
    *   If Docker is unavailable, Co falls back to a subprocess backend with environment sanitization and mandatory approval for all commands.

4.  **[uv](https://github.com/astral-sh/uv)**: Python package manager.
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

---

## Installation & Setup

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/co-cli.git
    cd co-cli
    ```

2.  **Install dependencies**:
    ```bash
    uv sync
    ```

3.  **Configure Co**:

    Co uses XDG-compliant paths. Create your config file:

    ```bash
    mkdir -p ~/.config/co-cli
    cp settings.reference.json ~/.config/co-cli/settings.json
    # Edit with your values
    ```

    **`~/.config/co-cli/settings.json`** (user config):
    ```json
    {
      "llm_provider": "gemini",
      "gemini_api_key": "AIza...",
      "gemini_model": "gemini-2.0-flash",
      "obsidian_vault_path": "/path/to/vault",
      "slack_bot_token": "xoxb-your-bot-token",
      "docker_image": "co-cli-sandbox"
    }
    ```

    **Configuration precedence** (highest to lowest):
    1. Environment variables (for CI/automation)
    2. Project config (`.co-cli/settings.json` in cwd)
    3. User config (`~/.config/co-cli/settings.json`)
    4. Built-in defaults

    > Environment variables (e.g., `GEMINI_API_KEY`, `LLM_PROVIDER`) override all file-based settings.

    > **Google Authentication**: If `google_credentials_path` is empty, Co falls back to Application Default Credentials (ADC).

## Google Services (Drive/Gmail/Calendar)

Just run `uv run co chat`. On first use, Co will:
1. Check if credentials already exist (`~/.config/co-cli/google_token.json`)
2. If not, detect and copy existing `gcloud` ADC credentials
3. If no ADC exists, run `gcloud auth application-default login` automatically (opens browser)
4. Copy the result to `~/.config/co-cli/google_token.json`

**Prerequisite:** Install [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)

> **Advanced:** Set `google_credentials_path` in `settings.json` to use a custom credentials file. This takes priority over the auto-setup flow.

## Slack Configuration (Optional)

To enable Slack messaging:
1.  Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps).
2.  Navigate to **OAuth & Permissions** and add Bot Token Scopes:
    *   `chat:write`, `channels:read`, `channels:history`, `users:read`
3.  **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).
4.  Add the token to your `settings.json`.

---

## Usage

### How the `co` Command Works

`uv sync` reads the `[project.scripts]` entry in `pyproject.toml` and generates `.venv/bin/co`. Two ways to invoke:

| Method | Requires venv activated? | Notes |
|--------|--------------------------|-------|
| `uv run co <command>` | No | Always uses the project's venv (recommended) |
| `co <command>` | Yes (`. .venv/bin/activate`) | Shorter, but only works with active venv |

### CLI Commands

#### `co chat` — Interactive Chat
Enter the interactive loop. Co maintains context across turns with automatic context governance.
```bash
uv run co chat
```
**Example Prompts:**
> "What's in my 'Projects' note?"
> "Search Google Drive for the 'Q1 Proposal' and summarize it."
> "Check my calendar for today and draft an email to the team."
> "Run `ls -la` to see what files are in this directory."

#### `co status` — System Health Check
Verify LLM provider, sandbox, and config health.
```bash
uv run co status
```

#### `co tail` — Real-Time Span Viewer
Tail agent spans live from a second terminal while `co chat` is running.
```bash
uv run co tail              # Follow all spans
uv run co tail -v           # Include LLM output content
uv run co tail --tools-only # Only tool calls
uv run co tail -n -l 10    # Print last 10 spans and exit
```

#### `co logs` — Datasette Dashboard
Launch a local Datasette dashboard to inspect traces with SQL.
```bash
uv run co logs
```

#### `co traces` — Visual Trace Viewer
Generate and open a static HTML page with nested, collapsible span trees.
```bash
uv run co traces
```

### REPL Slash Commands

Inside `co chat`, type `/` followed by a command name. Tab completion is available.

| Command | Effect |
|---------|--------|
| `/help` | List all slash commands |
| `/clear` | Clear conversation history |
| `/status` | Show system health (same as `co status`) |
| `/tools` | List registered agent tools |
| `/history` | Show conversation turn count and total messages |
| `/compact` | Summarize conversation via LLM to reduce context (2-message compacted history) |
| `/yolo` | Toggle auto-approve mode for tool calls |

**Context governance** runs automatically — you don't need to use `/compact` manually unless you want an immediate full compaction. The agent's `history_processors` trim old tool output and summarize dropped messages via LLM when history exceeds the configured threshold (default 40 messages). See `docs/DESIGN-06-conversation-memory.md`.

---

## Security & Architecture

### Sandbox (Docker + Subprocess Fallback)

Co runs shell commands inside a sandboxed environment:

*   **Docker (default)**: `co-cli-sandbox` image with `cap_drop=ALL`, `no-new-privileges`, `pids_limit=256`, non-root user (`1000:1000`), configurable network isolation and resource limits.
*   **Subprocess fallback**: When Docker is unavailable (`sandbox_backend=auto`), falls back to `SubprocessBackend` with allowlist-based environment sanitization. All commands require explicit approval in this mode.

Your current working directory is mounted to `/workspace` inside the container.

### Human-in-the-Loop

Side-effectful tools require explicit approval:
*   Shell commands (auto-approved for read-only commands when Docker sandbox has full isolation)
*   Sending emails
*   Posting to Slack

A whitelist of 29 safe commands (`ls`, `cat`, `git status`, etc.) is auto-approved when running inside Docker. Use `/yolo` to toggle auto-approve for all tools.

### Automatic Context Governance

Two `history_processors` prevent context overflow:

1. **Tool output trimming** — truncates large `ToolReturnPart.content` in older messages (threshold: 2000 chars)
2. **Sliding window** — when history exceeds 40 messages, drops middle messages and replaces with an LLM summary. Preserves the first exchange (session context) and recent messages (working context)

### Privacy
*   **LLM**: Local (Ollama) or cloud (Gemini) — your choice.
*   **Logs**: Stored locally in SQLite (`~/.local/share/co-cli/co-cli.db`).
*   **API Keys**: Managed via `settings.json` and never logged.

---

## Sandbox Configuration

### Container Lifecycle

| Event | Behavior |
|-------|----------|
| **First command** | Creates container `co-runner`, keeps it running |
| **Subsequent commands** | Reuses existing container via `docker exec` |
| **Session end** | Stops and removes the container |

### Configuration

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `docker_image` | `co-cli-sandbox` | `CO_CLI_DOCKER_IMAGE` | Docker image for the sandbox |
| `sandbox_backend` | `auto` | `CO_CLI_SANDBOX_BACKEND` | `auto`, `docker`, or `subprocess` |
| `sandbox_network` | `none` | `CO_CLI_SANDBOX_NETWORK` | `none` (isolated) or `bridge` (host network) |
| `sandbox_mem_limit` | `1g` | `CO_CLI_SANDBOX_MEM_LIMIT` | Docker memory limit |
| `sandbox_cpus` | `1` | `CO_CLI_SANDBOX_CPUS` | CPU limit (1–4) |
| `sandbox_max_timeout` | `600` | `CO_CLI_SANDBOX_MAX_TIMEOUT` | Max command timeout in seconds |
| `auto_confirm` | `false` | `CO_CLI_AUTO_CONFIRM` | Skip confirmation prompts |

### Custom Docker Images

Build a custom image with pre-installed tools:

```dockerfile
# Dockerfile.sandbox
FROM python:3.12-slim
RUN apt-get update && apt-get install -y curl git jq tree
RUN pip install pandas numpy requests
```

```bash
docker build -t co-cli-sandbox -f Dockerfile.sandbox .
```

### Troubleshooting

**"Docker is not available"** — Co will fall back to subprocess mode with a warning. To use Docker: ensure Docker Desktop is running (`docker ps`).

**Container conflicts** — If a stale `co-runner` container exists: `docker rm -f co-runner`

---

## Testing

Co uses **functional tests only** — no mocks or stubs. Tests hit real services and fail (not skip) when a service is unavailable.

```bash
uv run pytest              # Run all tests
uv run pytest -v           # Verbose output
uv run pytest tests/test_history.py  # Single test file
```

### Test Requirements

| Test Category | Required Setup |
|---------------|----------------|
| **Shell/Sandbox** | Docker running |
| **Notes** | None (uses temp files) |
| **Drive/Gmail/Calendar** | GCP credentials |
| **Slack** | Slack bot token |
| **History/Memory** | LLM provider (Gemini or Ollama) |
| **LLM E2E** | `LLM_PROVIDER=gemini` or `LLM_PROVIDER=ollama` |

### CI/CD

```bash
export GEMINI_API_KEY="your-key"
export LLM_PROVIDER="gemini"
export CO_CLI_AUTO_CONFIRM="true"
export SLACK_BOT_TOKEN="xoxb-..."
uv run pytest -v
```

---

## Modules

| Module | Description | Tools |
| :--- | :--- | :--- |
| **Shell** | Sandboxed command execution | `run_shell_command` |
| **Notes** | RAG over local Obsidian vault | `search_notes`, `list_notes`, `read_note` |
| **Drive** | Google Drive search and reading | `search_drive_files`, `read_drive_file` |
| **Gmail** | Inbox, search, and draft | `list_emails`, `search_emails`, `create_email_draft` |
| **Calendar** | List and search events | `list_calendar_events`, `search_calendar_events` |
| **Slack** | Channels, threads, users, posting | `list_slack_channels`, `list_slack_messages`, `list_slack_replies`, `list_slack_users`, `send_slack_message` |
| **Web** | Web search and URL fetch | `web_search`, `web_fetch` |

---

## Documentation

Design docs are published as a GitHub Pages book: **https://binlecode.github.io/co-cli/**

## Contributing

1.  Run `uv sync` to install dev dependencies.
2.  Follow the style in `docs/DESIGN-co-cli.md`.
3.  Ensure type hints are used everywhere.
