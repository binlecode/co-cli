# Co - The Production-Grade Personal Assistant CLI

**Co** is an opinionated, privacy-first AI agent that lives in your terminal. It connects your local tools (Obsidian, Shell) with cloud services (Google Drive, Gmail, Calendar) using a local LLM "Brain" and approval-gated shell execution for safe operation.

Designed for developers who want a personal assistant that:
1.  **Respects Privacy**: Runs on local models (Ollama) or cloud (Gemini).
2.  **Is Safe**: Approval-first security model — side-effectful commands require explicit consent.
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
    *You can use other models by updating `~/.co-cli/settings.json`.*

3.  **[uv](https://github.com/astral-sh/uv)**: Python package manager.
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

2.  **Install dependencies and git hooks**:
    ```bash
    uv sync
    bash scripts/install-hooks.sh
    ```

3.  **Configure Co**:

    All user-global files live under `~/.co-cli/`. Create your config file:

    ```bash
    mkdir -p ~/.co-cli
    cp settings.reference.json ~/.co-cli/settings.json
    # Edit with your values
    ```

    **`~/.co-cli/settings.json`** (user config):
    ```json
    {
      "llm_provider": "gemini",
      "gemini_api_key": "AIza...",
      "gemini_model": "gemini-2.0-flash",
      "obsidian_vault_path": "/path/to/vault"
    }
    ```

    **Configuration precedence** (highest to lowest):
    1. Environment variables (for CI/automation)
    2. Project config (`.co-cli/settings.json` in cwd)
    3. User config (`~/.co-cli/settings.json`)
    4. Built-in defaults

    > Environment variables (e.g., `GEMINI_API_KEY`, `LLM_PROVIDER`) override all file-based settings.

    > **Google Authentication**: If `google_credentials_path` is empty, Co falls back to Application Default Credentials (ADC).

## Google Services (Drive/Gmail/Calendar)

Just run `uv run co chat`. On first use, Co will:
1. Check if credentials already exist (`~/.co-cli/google_token.json`)
2. If not, detect and copy existing `gcloud` ADC credentials
3. If no ADC exists, run `gcloud auth application-default login` automatically (opens browser)
4. Copy the result to `~/.co-cli/google_token.json`

**Prerequisite:** Install [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)

> **Advanced:** Set `google_credentials_path` in `settings.json` to use a custom credentials file. This takes priority over the auto-setup flow.

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

#### `co config` — System Health Check
Verify LLM provider, shell, and config health (pre-agent, no session required).
```bash
uv run co config
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
| `/status` | Show live system health inside chat (uses active session deps) |
| `/tools` | List registered agent tools |
| `/history` | Show conversation turn count and total messages |
| `/compact` | Summarize conversation via LLM to reduce context (2-message compacted history) |
| `/yolo` | Toggle auto-approve mode for tool calls |

**Context governance** runs automatically — you don't need to use `/compact` manually unless you want an immediate full compaction. The agent's `history_processors` trim old tool output and summarize dropped messages via LLM when history exceeds the configured threshold (default 40 messages). See `docs/DESIGN-prompt-design.md`.

---

## Knowledge System

Co remembers preferences, decisions, and project context across sessions. All knowledge is dynamic — loaded on-demand via tools, never baked into the system prompt.

**Memory** — conversation-derived knowledge:
- Storage: `.co-cli/memory/*.md` (markdown with YAML frontmatter)
- Tools: `save_memory`, `recall_memory`, `search_knowledge`

**Articles** — saved references and web content:
- Storage: `.co-cli/library/*.md`
- Tool: `save_article`, `search_knowledge`

**Usage during chat:**
```
You: "Remember that I prefer async/await over callbacks"
Co: ✓ Saved memory: prefers-async-await.md

You: "What do you remember about my coding style?"
Co: Found 1 memory matching 'coding style':
    User prefers async/await over callbacks (2026-02-09)
```

All knowledge files are plain markdown — edit with any text editor, changes take effect on next sync.

### Knowledge Search Backend

Three backends in degradation order: `hybrid → fts5 → grep`.

| Setting | Default | Description |
|---------|---------|-------------|
| `knowledge_search_backend` | `"hybrid"` | `hybrid` (vec + FTS5), `fts5` (BM25 only), or `grep` (no index) |
| `knowledge_embedding_provider` | `"tei"` | Embedding provider: `tei`, `ollama`, `gemini`, or `none` |
| `knowledge_embedding_model` | `"embeddinggemma"` | Model name passed to the provider |
| `knowledge_embedding_dims` | `1024` | Output vector dimensions — **must match the model** |
| `knowledge_embed_api_url` | `"http://127.0.0.1:8283"` | TEI or compatible embedding API endpoint |

> **Dimension mismatch fix**: if you change `knowledge_embedding_model` or `knowledge_embedding_dims`, the vec table schema becomes stale. Delete `~/.co-cli/co-cli-search.db` — it rebuilds automatically on next `co chat`.
>
> ```bash
> rm ~/.co-cli/co-cli-search.db
> ```

---

## Security & Architecture

### Shell (Approval-Gated Subprocess)

Co runs shell commands as host subprocesses with approval as the security boundary:

*   **Safe commands** (`ls`, `cat`, `git status`, etc.) are auto-approved via a configurable safe-prefix list.
*   **Everything else** requires explicit `[y/n/a]` consent before execution.
*   **Environment sanitization**: Allowlist-only env vars prevent pager/editor hijacking and shared-library injection.

Use `/yolo` to toggle auto-approve for all tools.

### Human-in-the-Loop

Side-effectful tools require explicit approval:
*   Shell commands (safe read-only commands auto-approved)
*   Sending emails

### Automatic Context Governance

Two `history_processors` prevent context overflow:

1. **Tool output trimming** — truncates large `ToolReturnPart.content` in older messages (threshold: 2000 chars)
2. **Sliding window** — when history exceeds 40 messages, drops middle messages and replaces with an LLM summary. Preserves the first exchange (session context) and recent messages (working context)

### Privacy
*   **LLM**: Local (Ollama) or cloud (Gemini) — your choice.
*   **Logs**: Stored locally in SQLite (`~/.co-cli/co-cli-logs.db`).
*   **API Keys**: Managed via `settings.json` and never logged.

---

## Shell Configuration

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `shell_max_timeout` | `600` | `CO_CLI_SHELL_MAX_TIMEOUT` | Max command timeout in seconds |
| `shell_safe_commands` | 29 safe prefixes | `CO_CLI_SHELL_SAFE_COMMANDS` | Auto-approved command prefixes (comma-separated in env) |

---

## Code Quality

Dev tooling is included in `uv sync` — no extra installs needed. All checks run through `scripts/quality-gate.sh`:

```bash
scripts/quality-gate.sh lint          # ruff lint + format (pre-commit hook runs this)
scripts/quality-gate.sh lint --fix    # ruff auto-fix + format
scripts/quality-gate.sh types         # lint + pyright type checking
scripts/quality-gate.sh full          # lint + pyright + pytest (ship gate)
```

| Tool | Purpose | When it runs |
|------|---------|--------------|
| **[ruff](https://docs.astral.sh/ruff/)** | Linter + formatter (replaces flake8, isort, black) | Pre-commit hook (`quality-gate.sh lint`) |
| **[pyright](https://github.com/microsoft/pyright)** | Static type checker (replaces mypy) | CI + delivery skills (`quality-gate.sh types`) — warn-only while pre-existing errors are worked down |
| **[pytest](https://docs.pytest.org/)** | Test runner (functional tests, no mocks) | CI + delivery skills (`quality-gate.sh full`) |

Configuration for all three tools lives in `pyproject.toml`.

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
| **Shell** | None (uses host subprocess) |
| **Notes** | None (uses temp files) |
| **Drive/Gmail/Calendar** | GCP credentials |
| **History/Memory** | LLM provider (Gemini or Ollama) |
| **LLM E2E** | `LLM_PROVIDER=gemini` or `LLM_PROVIDER=ollama` |

### CI/CD

```bash
export GEMINI_API_KEY="your-key"
export LLM_PROVIDER="gemini"
export CO_CLI_AUTO_CONFIRM="true"
uv run pytest -v
```

---

## Modules

| Module | Description | Tools |
| :--- | :--- | :--- |
| **Shell** | Approval-gated command execution | `run_shell_command` |
| **Notes** | RAG over local Obsidian vault | `search_notes`, `list_notes`, `read_note` |
| **Drive** | Google Drive search and reading | `search_drive_files`, `read_drive_file` |
| **Gmail** | Inbox, search, and draft | `list_emails`, `search_emails`, `create_email_draft` |
| **Calendar** | List and search events | `list_calendar_events`, `search_calendar_events` |
| **Web** | Web search and URL fetch | `web_search`, `web_fetch` |

---

## Documentation

Design docs are published as a GitHub Pages book: **https://binlecode.github.io/co-cli/**

## Contributing

1.  `uv sync` — install all dependencies (includes ruff, pyright, pytest).
2.  `bash scripts/install-hooks.sh` — install the pre-commit hook.
3.  `scripts/quality-gate.sh full` must pass before shipping. The pre-commit hook enforces `lint`; CI enforces `full`.
4.  Follow the coding rules in `CLAUDE.md` Engineering Rules section.
5.  Type hints required on all public functions.
