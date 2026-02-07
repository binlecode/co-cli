# Co CLI - System Design Document

**Version:** 1.2 (Feb 2026)
**Stack:** Python 3.12+, Pydantic AI, Ollama/Gemini, Docker, UV

---

## 1. System Overview

Co is a production-grade personal assistant CLI that combines local AI inference with cloud services. It provides a privacy-first, sandboxed environment for running AI-powered tasks.

### Design Principles

1. **Privacy-First**: Local LLM (Ollama) by default, all logs stored locally
2. **Safe Execution**: Docker sandbox for shell commands
3. **Observable**: Full OpenTelemetry tracing to local SQLite
4. **Human-in-the-Loop**: Confirmation required for high-risk actions

---

## 2. High-Level Architecture

```mermaid
graph TB
    subgraph User Interface
        CLI[Typer CLI]
        REPL[Prompt Toolkit REPL]
    end

    subgraph Core Brain
        Agent[Pydantic AI Agent]
        LLM{LLM Provider}
        Ollama[Ollama Local]
        Gemini[Gemini Cloud]
    end

    subgraph Tool Layer
        Shell[Shell Tool]
        Obsidian[Obsidian Tool]
        Drive[Drive Tool]
        Gmail[Gmail Tool]
        Calendar[Calendar Tool]
        SlackTool[Slack Tool]
    end

    subgraph External Services
        Docker[Docker Container]
        Obsidian[Obsidian Vault]
        GDrive[Google Drive API]
        Slack[Slack API]
        Gmail[Gmail API]
        GCal[Calendar API]
    end

    subgraph Infrastructure
        Config[Config Manager]
        Telemetry[OpenTelemetry]
        SQLite[(SQLite DB)]
    end

    CLI --> REPL
    REPL --> Agent
    Agent --> LLM
    LLM --> Ollama
    LLM --> Gemini

    Agent --> Shell
    Agent --> Obsidian
    Agent --> Drive
    Agent --> Gmail
    Agent --> Calendar
    Agent --> SlackTool

    Shell --> Docker
    Obsidian --> ObsVault[Obsidian Vault]
    Drive --> GDrive
    Gmail --> GmailAPI[Gmail API]
    Calendar --> GCal
    SlackTool --> Slack

    Config --> Agent
    Agent --> Telemetry
    Telemetry --> SQLite
```

---

## 3. Processing Flow

### 3.1 Chat Session Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI as CLI (main.py)
    participant Agent as Pydantic AI Agent
    participant LLM as LLM Provider
    participant Tools as Tool Layer
    participant External as External Services

    User->>CLI: co chat
    CLI->>CLI: Initialize Agent & PromptSession
    CLI->>CLI: create_deps() → CoDeps(sandbox, auto_confirm, ...)
    CLI->>CLI: message_history = []

    loop Interactive REPL
        User->>CLI: Enter query
        CLI->>CLI: Show "Co is thinking..."
        CLI->>Agent: agent.run(user_input, deps=deps, message_history=message_history)
        Agent->>LLM: Send prompt + prior conversation context
        LLM->>Agent: Response with tool calls

        opt Read-Only Tool
            Agent->>Tools: Execute tool (ctx: RunContext[CoDeps])
            Tools->>External: API call
            External->>Tools: Result
            Tools->>Agent: Tool output
            Agent->>LLM: Continue with tool result
        end

        opt Side-Effectful Tool (requires_approval=True)
            Agent->>CLI: DeferredToolRequests
            CLI->>User: Approve? [y/n/a(yolo)]
            User->>CLI: y / a
            CLI->>Agent: agent.run(deferred_tool_results=approvals)
            Agent->>Tools: Execute approved tool
            Tools->>External: API call / Docker exec
            External->>Tools: Result
            Tools->>Agent: Tool output
            Agent->>LLM: Continue with tool result
        end

        LLM->>Agent: Final response
        Agent->>CLI: result.output
        CLI->>CLI: message_history = result.all_messages()
        CLI->>User: Render Markdown response
    end

    User->>CLI: exit/quit or Ctrl+C ×2 (within 2s) or Ctrl+D
    CLI->>CLI: deps.sandbox.cleanup()
    CLI->>User: Session ended
```

### 3.2 Tool Execution Flow (Deferred Approval Pattern)

Side-effectful tools (`run_shell_command`, `draft_email`, `post_slack_message`) use pydantic-ai's `requires_approval=True` + `DeferredToolRequests` pattern. Approval lives in the chat loop, not inside tools.

```mermaid
sequenceDiagram
    participant User
    participant CLI as Chat Loop
    participant Agent as agent.run()
    participant Tool as shell.py
    participant Docker as Docker Container

    User->>CLI: "list files"
    CLI->>Agent: agent.run(user_input)
    Agent->>Agent: LLM calls run_shell_command("ls -la")
    Note over Agent: requires_approval=True → defer
    Agent->>CLI: result.output = DeferredToolRequests(approvals=[...])

    CLI->>CLI: _handle_approvals()
    CLI->>User: "Approve run_shell_command(cmd='ls -la')? [y/n/a(yolo)]"
    User->>CLI: y

    CLI->>Agent: agent.run(None, deferred_tool_results=approvals)
    Agent->>Tool: run_shell_command("ls -la")
    Tool->>Docker: sandbox.run_command("ls -la")
    Docker->>Tool: Output bytes
    Tool->>Agent: Command result
    Agent->>CLI: result.output = "..."
    CLI->>User: Render Markdown
```

**Denial flow:** When the user picks `n`, the chat loop sends `ToolDenied("User denied this action")`. The LLM sees the structured denial and can reason about it (e.g. suggest an alternative command).

**Session yolo flow:** When the user picks `a`, `deps.auto_confirm` is set to `True`. All subsequent approvals in the session are auto-approved without prompting.

### 3.3 Tool Execution Flow (Sandbox Detail)

```mermaid
sequenceDiagram
    participant Tool as shell.py
    participant Sandbox as sandbox.py
    participant Docker as Docker Container

    Tool->>Sandbox: sandbox.run_command("ls -la")
    Sandbox->>Sandbox: ensure_container()

    alt Container Not Running
        Sandbox->>Docker: containers.run(python:3.12-slim)
        Docker->>Sandbox: Container started
    end

    Sandbox->>Docker: container.exec_run("ls -la", workdir="/workspace")
    Docker->>Sandbox: Output bytes
    Sandbox->>Tool: Decoded output string
```

---

## 4. Core Components

### 4.1 Agent (`co_cli/agent.py`)

The Agent is the central orchestrator that connects the LLM to tools. Uses `deps_type=CoDeps` for dependency injection into tools via `RunContext`.

```mermaid
classDiagram
    class Agent~CoDeps, str | DeferredToolRequests~ {
        +model: Model
        +deps_type: CoDeps
        +system_prompt: str
        +tools: List[Tool]
        +run(input: str, deps: CoDeps) Result
    }

    class CoDeps {
        +sandbox: Sandbox
        +auto_confirm: bool
        +session_id: str
        +obsidian_vault_path: Path | None
        +google_drive: Any | None
        +google_gmail: Any | None
        +google_calendar: Any | None
        +slack_client: Any | None
    }

    class OpenAIChatModel {
        +model_name: str
        +provider: OpenAIProvider
    }

    Agent --> CoDeps : injects into tools
    Agent --> OpenAIChatModel : uses (ollama)
    note for Agent "Gemini uses google-gla: model string"
```

**Factory Function: `get_agent()`**

```python
def get_agent() -> tuple[Agent[CoDeps, str | DeferredToolRequests], ModelSettings | None]:
    provider_name = settings.llm_provider.lower()
    model_settings: ModelSettings | None = None

    if provider_name == "gemini":
        os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)
        model = f"google-gla:{settings.gemini_model}"
    else:
        # Ollama via OpenAI-compatible API
        provider = OpenAIProvider(base_url=f"{ollama_host}/v1", api_key="ollama")
        model = OpenAIChatModel(model_name, provider)
        model_settings = ModelSettings(temperature=0.7, top_p=1.0, max_tokens=16384)

    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        model,
        deps_type=CoDeps,
        system_prompt=system_prompt,
        retries=settings.tool_retries,
        output_type=[str, DeferredToolRequests],
    )

    # Side-effectful tools — require human approval via DeferredToolRequests
    agent.tool(run_shell_command, requires_approval=True)
    agent.tool(draft_email, requires_approval=True)
    agent.tool(post_slack_message, requires_approval=True)

    # Read-only tools — no approval needed
    agent.tool(search_notes)
    agent.tool(list_notes)
    # ... remaining read-only tools
    agent.tool(search_calendar_events)

    return agent, model_settings
```

**System Prompt:**
```
You are Co, a CLI assistant running in the user's terminal.

### Response Style
- Be terse: users want results, not explanations
- On success: show the output, then a brief note if needed
- On error: show the error, suggest a fix

### Tool Output
- Most tools return a dict with a `display` field — show the `display` value verbatim
- Never reformat, summarize, or drop URLs from tool output
- If the result has `has_more=true`, tell the user more results are available

### Tool Usage
- Use tools proactively to complete tasks
- Chain operations: read before modifying, test after changing
- Shell commands run in a Docker sandbox mounted at /workspace

### Pagination
- When a tool result has has_more=true, more results are available
- If the user asks for "more", "next", or "next 10", call the same tool with the same query and page incremented by 1
- Do NOT say "no more results" unless you called the tool and has_more was false
```

### 4.2 Configuration (`co_cli/config.py`)

XDG-compliant configuration management with environment variable fallback.

```mermaid
classDiagram
    class Settings {
        +obsidian_vault_path: Optional[str]
        +slack_bot_token: Optional[str]
        +google_credentials_path: Optional[str]
        +auto_confirm: bool
        +docker_image: str
        +theme: str
        +tool_retries: int
        +max_request_limit: int
        +gemini_api_key: Optional[str]
        +llm_provider: str
        +ollama_host: str
        +ollama_model: str
        +gemini_model: str
        +save()
        +fill_from_env() validator
    }

    class Paths {
        <<constants>>
        CONFIG_DIR: ~/.config/co-cli/
        DATA_DIR: ~/.local/share/co-cli/
        SETTINGS_FILE: settings.json
    }

    Settings --> Paths : uses
```

**Configuration Resolution Order:**
1. `~/.config/co-cli/settings.json` (primary)
2. Environment variables (fallback)
3. Default values (hardcoded)

**Environment Variable Mapping:**
| Setting | Env Var | Default |
|---------|---------|---------|
| `llm_provider` | `LLM_PROVIDER` | `"gemini"` |
| `gemini_api_key` | `GEMINI_API_KEY` | `None` |
| `gemini_model` | `GEMINI_MODEL` | `"gemini-2.0-flash"` |
| `ollama_host` | `OLLAMA_HOST` | `"http://localhost:11434"` |
| `ollama_model` | `OLLAMA_MODEL` | `"llama3"` |
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `None` |
| `slack_bot_token` | `SLACK_BOT_TOKEN` | `None` |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `None` |
| `auto_confirm` | `CO_CLI_AUTO_CONFIRM` | `false` |
| `docker_image` | `CO_CLI_DOCKER_IMAGE` | `"python:3.12-slim"` |
| `theme` | `CO_CLI_THEME` | `"light"` |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` |
| `max_request_limit` | `CO_CLI_MAX_REQUEST_LIMIT` | `25` |

### 4.3 Dependencies (`co_cli/deps.py`)

Runtime dependencies injected into tools via `RunContext[CoDeps]`. Settings creates these in `main.py`, tools access them via `ctx.deps`.

```python
@dataclass
class CoDeps:
    sandbox: Sandbox
    auto_confirm: bool = False
    session_id: str = ""
    obsidian_vault_path: Path | None = None
    google_drive: Any | None = None
    google_gmail: Any | None = None
    google_calendar: Any | None = None
    slack_client: Any | None = None
```

**Design Principle:** `CoDeps` contains runtime resources, NOT config objects. `Settings` creates resources in `main.py`, then injects here.

**Dependency Flow:**

```
main.py: create_deps()          →  CoDeps(sandbox, vault_path, google_drive, slack_client, ...)
    ↓
agent.run(user_input, deps=deps) →  Agent passes deps to tool calls
    ↓
tool(ctx: RunContext[CoDeps])    →  ctx.deps.sandbox, ctx.deps.google_drive, etc.
```

#### Multi-Session State Design (pydantic-ai pattern)

pydantic-ai separates state into three tiers:

| Tier | Scope | Lifetime | Where | Example |
|------|-------|----------|-------|---------|
| **Agent config** | Process | Entire process | `Agent(...)` constructor, module constants | Model name, system prompt, tool registrations |
| **Session deps** | Session | One REPL loop (`create_deps()` → `sandbox.cleanup()`) | `RunContext.deps` (`CoDeps`) | Sandbox handle, Google creds path, page tokens |
| **Run state** | Single run | One `agent.run()` call | `result.state` / `ctx.state` (pydantic-graph) | Per-turn counter (if needed) |

**Critical invariant: mutable per-session state belongs in `CoDeps`, never in module globals.** Module-level variables are process-scoped — they persist across sessions and are shared by all concurrent sessions in the same process.

`CoDeps` is the session boundary. `main.py:create_deps()` instantiates one `CoDeps` per chat session. Every `agent.run()` call within that session receives the same `CoDeps` instance, so tools accumulate state (like page tokens) across turns. But two sessions — whether concurrent or sequential — get separate `CoDeps` instances with independent state.

```
Process (one Python interpreter)
├── Module globals          ← shared, immutable config only
│   ├── Agent instance
│   └── Tool registrations
│
├── Session A
│   └── CoDeps instance A   ← mutable state lives here
│       ├── sandbox A
│       ├── drive_page_tokens: {"report": ["tok1", "tok2"]}
│       └── ...
│
└── Session B
    └── CoDeps instance B   ← independent, no cross-contamination
        ├── sandbox B
        ├── drive_page_tokens: {}    ← fresh, empty
        └── ...
```

Mutable fields use `field(default_factory=...)` so each `CoDeps` gets its own empty collection. The dict is keyed by query string, not session ID — the `CoDeps` instance *is* the session boundary, so no session key is needed. When the session ends, the `CoDeps` instance is garbage-collected along with all its accumulated state.

**Example — Drive pagination tokens:**

```python
# deps.py
drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)

# google_drive.py — reads/writes via ctx.deps, never module global
tokens = ctx.deps.drive_page_tokens.get(query, [])
ctx.deps.drive_page_tokens[query] = []
```

### 4.4 Sandbox (`co_cli/sandbox.py`)

Docker-based isolation for safe command execution.

```mermaid
classDiagram
    class Sandbox {
        -_client: DockerClient
        +image: str
        +container_name: str
        +workspace_dir: str
        +client: DockerClient
        +ensure_container() Container
        +run_command(cmd: str) str
        +cleanup()
    }

    class DockerContainer {
        +status: str
        +start()
        +stop()
        +remove()
        +exec_run(cmd, workdir) tuple
    }

    Sandbox --> DockerContainer : manages
```

**Container Configuration:**
- **Image:** `python:3.12-slim` (configurable)
- **Name:** `co-runner`
- **Volume:** Current working directory → `/workspace` (read-write)
- **Command:** `sh` (keeps container alive)

**Lifecycle:**
1. On first tool call: Create container if not exists
2. On subsequent calls: Reuse existing container
3. On CLI exit: Container remains for reuse (manual cleanup via `sandbox.cleanup()`)

### 4.5 CLI (`co_cli/main.py`)

Typer-based CLI. Owns dependency creation, lifecycle, and conversation memory.

#### Entry Point: How `co` Works

`pyproject.toml` declares a console script entry point:

```toml
[project.scripts]
co = "co_cli.main:app"
```

When `uv sync` runs, it generates `.venv/bin/co` — a small Python wrapper:

```python
#!/.../co-cli/.venv/bin/python3
import sys
from co_cli.main import app
if __name__ == "__main__":
    sys.exit(app())
```

This means `co` is not a compiled binary but an auto-generated script that calls the Typer `app()`. Two invocation methods:

| Method | Requires venv activated? |
|--------|--------------------------|
| `uv run co <cmd>` | No — uv activates the venv automatically |
| `co <cmd>` | Yes — `.venv/bin/` must be on `PATH` |


**Dependency Injection + Conversation Memory + Deferred Approvals:**

The chat loop has three responsibilities:
1. **Conversation memory** — accumulates `message_history` across turns via `result.all_messages()`
2. **Deferred approval** — when `result.output` is `DeferredToolRequests`, calls `_handle_approvals()` to prompt the user, then resumes the agent with `DeferredToolResults`
3. **Lifecycle** — creates `CoDeps` at session start, calls `sandbox.cleanup()` at session end

```
chat_loop():
    agent, model_settings = get_agent()
    deps = create_deps()
    info = get_status(tool_count=len(agent._function_toolset.tools))
    display_welcome_banner(info)
    message_history = []

    loop:
        result = agent.run(user_input, deps, message_history)

        while result.output is DeferredToolRequests:   # loop — resumed run may trigger more
            result = _handle_approvals(agent, deps, result)  # [y/n/a] prompt

        message_history = result.all_messages()
        display(result.output)

    finally:
        deps.sandbox.cleanup()
```

**Why `while`, not `if`:** A resumed `agent.run()` with approved tool results may itself produce another `DeferredToolRequests` — for example, when the LLM chains two side-effectful calls (e.g. user says "cd to /workspace and ls"). Each round needs its own approval cycle.

**Conversation Memory:** Each turn's full message history (user prompts, assistant responses, tool calls/results) is accumulated via `result.all_messages()` and passed to the next `agent.run()` call. This gives the LLM full context for follow-up queries like "try again" or "change the subject line". Memory is in-process only — it resets when the session ends.

```mermaid
stateDiagram-v2
    [*] --> Idle

    state "co chat" as Chat {
        Idle --> CreateDeps: Start REPL
        CreateDeps --> Waiting: CoDeps ready
        Waiting --> Thinking: User input
        Thinking --> ToolExec: Tool needed
        ToolExec --> Thinking: Tool result (read-only)
        Thinking --> Approval: DeferredToolRequests
        Approval --> Thinking: Approved (agent.run with DeferredToolResults)
        Approval --> Responding: Denied (ToolDenied)
        Thinking --> Responding: LLM done
        Responding --> Waiting: Display output
        Thinking --> Waiting: Ctrl+C (cancel operation)
        Waiting --> CtrlC1: Ctrl+C (1st)
        CtrlC1 --> Cleanup: Ctrl+C within 2s (2nd)
        CtrlC1 --> Waiting: Timeout >2s / new input
        Waiting --> Cleanup: exit/quit/Ctrl+D
        Cleanup --> [*]: deps.sandbox.cleanup()
    }

    state "co status" as Status {
        [*] --> CheckLLM
        CheckLLM --> CheckObsidian
        CheckObsidian --> CheckDB
        CheckDB --> DisplayTable
        DisplayTable --> [*]
    }

    state "co logs" as Logs {
        [*] --> LaunchDatasette
        LaunchDatasette --> [*]: Ctrl+C
    }
```

**Commands:**

| Command | Description | Implementation |
|---------|-------------|----------------|
| `co chat` | Interactive REPL | `asyncio.run(chat_loop())` |
| `co status` | System health check | Displays Rich table |
| `co tail` | Real-time span viewer | Polls SQLite, prints with Rich (`tail.py`) |
| `co logs` | Telemetry dashboard | Launches Datasette |
| `co traces` | Visual span tree (HTML) | Generates static HTML (`trace_viewer.py`) |

**REPL Features:**
- History: Saved to `~/.local/share/co-cli/history.txt`
- Spinner: "Co is thinking..." during inference
- Output: Rendered as Rich Markdown

**Exit Handling (double Ctrl+C pattern):**

Follows Node.js REPL / Aider / Gemini CLI conventions:

| Context | Action | Result |
|---------|--------|--------|
| During `agent.run()` | Ctrl+C | Cancels operation, patches dangling tool calls (§7.6), returns to prompt. Does **not** count toward exit. |
| At prompt | Ctrl+C (1st) | Prints "Press Ctrl+C again to exit" |
| At prompt | Ctrl+C (2nd within 2s) | Exits session |
| At prompt | Ctrl+C (2nd after 2s) | Treated as new 1st press (timeout reset) |
| Any input submitted | — | Resets the interrupt timer |
| Anywhere | Ctrl+D (EOF) | Exits immediately |

### 4.6 Telemetry (`co_cli/telemetry.py`)

OpenTelemetry traces exported to local SQLite.

```mermaid
classDiagram
    class SQLiteSpanExporter {
        +db_path: str
        -_init_db()
        +export(spans: List[ReadableSpan]) SpanExportResult
        +shutdown()
    }

    class SpansTable {
        <<SQLite>>
        id: TEXT PK
        name: TEXT
        context: TEXT
        kind: TEXT
        start_time: TEXT
        end_time: TEXT
        attributes: TEXT (JSON)
        events: TEXT (JSON)
        status: TEXT
    }

    SQLiteSpanExporter --> SpansTable : writes to
```

**Schema:**
```sql
CREATE TABLE spans (
    id TEXT PRIMARY KEY,
    name TEXT,
    context TEXT,
    kind TEXT,
    start_time TEXT,
    end_time TEXT,
    attributes TEXT,  -- JSON
    events TEXT,      -- JSON
    status TEXT
)
```

---

## 5. Tool System

### 5.1 Tool Architecture

```mermaid
graph LR
    subgraph "RunContext[CoDeps] Tools (all migrated)"
        A[run_shell_command]
        B[search_notes]
        C[list_notes]
        D[read_note]
        E[search_drive]
        F[read_drive_file]
        G[post_slack_message]
        H[draft_email]
        I[list_calendar_events]
    end

    subgraph Risk Level
        HR[High Risk<br/>Requires Confirmation]
        LR[Low Risk<br/>Auto-execute]
    end

    A --> HR
    G --> HR
    H --> HR

    B --> LR
    C --> LR
    D --> LR
    E --> LR
    F --> LR
    I --> LR
```

**Tool Registration:** All tools use `agent.tool()` with `RunContext[CoDeps]` pattern. Zero `tool_plain()` remaining.

### 5.2 Shell Tool (`co_cli/tools/shell.py`)

Uses `RunContext[CoDeps]` + `requires_approval=True`. Approval is handled by the chat loop via `DeferredToolRequests`, not inside the tool. See `docs/DESIGN-tool-shell-sandbox.md` for sandbox details.

```python
def run_shell_command(ctx: RunContext[CoDeps], cmd: str) -> str:
    """Execute a shell command in a sandboxed Docker container."""
    try:
        return ctx.deps.sandbox.run_command(cmd)
    except Exception as e:
        raise ModelRetry(f"Command failed ({e})")
```

### 5.3 Obsidian Tools (`co_cli/tools/obsidian.py`)

Uses `RunContext[CoDeps]` + `ModelRetry` for self-healing. Returns `dict[str, Any]` with `display` field (consistent with all other tools). See `docs/DESIGN-tool-obsidian.md` for full design.

```python
def search_notes(ctx: RunContext[CoDeps], query: str, limit: int = 10) -> dict[str, Any]:
    """Multi-keyword AND search with word boundaries.
    Returns {"display": "...", "count": N, "has_more": false}.
    Empty results return count=0 (not ModelRetry)."""

def list_notes(ctx: RunContext[CoDeps], tag: str | None = None) -> dict[str, Any]:
    """List markdown notes, optionally filtered by tag.
    Returns {"display": "...", "count": N}."""

def read_note(ctx: RunContext[CoDeps], filename: str) -> str:
    """Read note content with path traversal protection.
    Returns raw content string (not dict — appropriate for file reads)."""
```

### 5.4 Drive Tool (`co_cli/tools/google_drive.py`)

Uses `RunContext[CoDeps]` + `ModelRetry`. Auth is handled at startup via `google_auth.py` → `create_deps()`.

```python
def search_drive(ctx: RunContext[CoDeps], query: str) -> list[dict]:
    service = ctx.deps.google_drive
    if not service:
        raise ModelRetry("Google Drive not configured.")
    # API-level filter with ModelRetry on errors
```

### 5.5 Gmail Tool (`co_cli/tools/google_gmail.py`)

Uses `RunContext[CoDeps]` + `ModelRetry`. `draft_email` is registered with `requires_approval=True` — approval handled by the chat loop. Read-only tools (`list_emails`, `search_emails`) execute without approval.

### 5.6 Calendar Tool (`co_cli/tools/google_calendar.py`)

Uses `RunContext[CoDeps]` + `ModelRetry`. Read-only, no confirmation needed.

### 5.7 Slack Tool (`co_cli/tools/slack.py`)

Uses `RunContext[CoDeps]` + `ModelRetry` + `requires_approval=True`. Approval handled by the chat loop.

### 5.8 Google Auth (`co_cli/google_auth.py`)

Infrastructure module (not a tool). Single factory function used by `create_deps()`:

```python
def get_google_credentials(credentials_path, scopes) -> Any | None:
    # Authorized user file → ADC fallback → None
def build_google_service(service_name, version, credentials) -> Any | None:
    # Credentials → service client (or None)
```

**Cloud Tool Summary:**

| Tool | File | Service | Approval |
|------|------|---------|----------|
| `search_drive` | `google_drive.py` | Drive API v3 | No |
| `read_drive_file` | `google_drive.py` | Drive API v3 | No |
| `draft_email` | `google_gmail.py` | Gmail API v1 | `requires_approval=True` |
| `list_calendar_events` | `google_calendar.py` | Calendar API v3 | No |
| `post_slack_message` | `slack.py` | Slack WebClient | `requires_approval=True` |

---

## 6. Concurrency Model

```mermaid
graph TD
    subgraph Blocking REPL
        A[User Input] --> B[Agent Processing]
        B --> C[Tool Execution]
        C --> D[LLM Continuation]
        D --> E[Response Output]
        E --> A
    end

    subgraph Rationale
        R1[Context Consistency<br/>Prevents forking history]
        R2[Resource Safety<br/>Ollama single-threaded]
    end
```

**Design:** Single-threaded, synchronous execution loop.

**Mechanism:**
- Prompt is disabled while agent is "thinking"
- Uses `await agent.run()` inside async loop
- Query N must complete before Query N+1 begins
- `message_history` is updated sequentially after each turn — no risk of forking

**Rationale:**
1. Prevents conversation history forking
2. Prevents overloading Ollama (can't handle parallel inference on consumer hardware)

---

## 7. Conversation Memory

### 7.1 Design

Each chat session maintains an in-process message history that accumulates across turns. This gives the LLM full conversational context for follow-ups like "try again", "change the subject", or "show me more".

```mermaid
sequenceDiagram
    participant User
    participant CLI as Chat Loop
    participant Agent as agent.run()

    Note over CLI: message_history = []

    User->>CLI: "draft email to Bob"
    CLI->>Agent: agent.run("draft email", message_history=[])
    Agent->>CLI: result
    Note over CLI: message_history = result.all_messages()<br/>[user: "draft email", assistant: "Here's a draft..."]

    User->>CLI: "try again, more formal"
    CLI->>Agent: agent.run("try again", message_history=[user, assistant])
    Note over Agent: LLM sees full prior context
    Agent->>CLI: result
    Note over CLI: message_history = result.all_messages()<br/>[user, assistant, user: "try again", assistant: "Revised..."]
```

### 7.2 Implementation

Uses pydantic-ai's built-in `message_history` parameter and `result.all_messages()` accessor:

```python
message_history = []
while True:
    result = await agent.run(
        user_input, deps=deps, message_history=message_history
    )
    message_history = result.all_messages()
```

**What's stored in `message_history`:**
- User prompts (`ModelRequest` with `UserPromptPart`)
- Assistant responses (`ModelResponse` with `TextPart`)
- Tool calls and their results (`ToolCallPart`, `ToolReturnPart`)
- System prompt is sent separately by the agent on each call, not in history

### 7.3 Scope & Limitations

| Aspect | Current Design |
|--------|---------------|
| **Lifetime** | In-process only — resets when the session ends |
| **Persistence** | None. History is not saved to disk or database. |
| **Size management** | Unbounded — grows with each turn. For long sessions, the LLM's context window is the natural limit. |
| **Cross-session** | Not supported. Each `co chat` invocation starts fresh. |
| **Concurrency** | Sequential updates only — single-threaded loop prevents forking (see §6). |

### 7.4 History Length Control

Pydantic-ai provides `history_processors` — a list of callables that transform `message_history` before sending to the model. There is **no built-in max length, sliding window, or summarization**. If history exceeds the LLM's context window, the API call fails.

**Available hook:**

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage

def trim_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Keep first 2 messages (system setup) + last N messages."""
    MAX_MESSAGES = 20
    if len(messages) > MAX_MESSAGES:
        return messages[:2] + messages[-(MAX_MESSAGES - 2):]
    return messages

agent = Agent(model, history_processors=[trim_history])
```

**Current co-cli status:** No `history_processors` configured — history is unbounded. For typical interactive sessions (< 50 turns) this is fine.

### 7.5 Interrupt Recovery (Dangling Tool Call Patching)

When the user presses Ctrl+C during `agent.run()`, the LLM may have been mid-tool-call. If `message_history` contains a `ModelResponse` with a `ToolCallPart` but no matching `ToolReturnPart`, the next `agent.run()` call would fail because the LLM sees an unanswered tool call.

**Mitigation:** `_patch_dangling_tool_calls()` scans the last message in history after a `KeyboardInterrupt`. If it's a `ModelResponse` containing `ToolCallPart`s, the function appends a synthetic `ModelRequest` with `ToolReturnPart`(s) carrying an "Interrupted by user." error message.

```python
def _patch_dangling_tool_calls(messages, error_message="Interrupted by user."):
    last_msg = messages[-1]
    if last_msg.kind != "response":
        return messages
    tool_calls = [p for p in last_msg.parts if isinstance(p, ToolCallPart)]
    if not tool_calls:
        return messages
    return_parts = [
        ToolReturnPart(tool_name=tc.tool_name, tool_call_id=tc.tool_call_id, content=error_message)
        for tc in tool_calls
    ]
    return messages + [ModelRequest(parts=return_parts)]
```

**Why this works:** Wrapping `ToolReturnPart`s in a `ModelRequest` is the same pattern pydantic-ai uses internally in `_agent_graph.py:_handle_final_result()`, which explicitly comments: *"To allow this message history to be used in a future run without dangling tool calls, append a new ModelRequest using the tool returns and retries."*

**Current behavior note:** `agent.run()` copies the input list (`list(message_history)`), so on `KeyboardInterrupt` the original `message_history` is unchanged from the previous successful turn — no dangling calls exist. The patch is defensive: it protects against future adoption of `agent.iter()` or streaming where partial state may leak into the caller's list.

---

## 8. Security Model


### 8.1 Defense Layers

```mermaid
graph TB
    subgraph Layer 1: Configuration
        S1[Secrets in settings.json]
        S2[No hardcoded keys]
        S3[Env var fallback]
    end

    subgraph Layer 2: Confirmation
        C1[Shell commands]
        C2[Slack messages]
        C3[Email drafts]
    end

    subgraph Layer 3: Isolation
        I1[Docker sandbox]
        I2[Workspace mount only]
        I3[Transient containers]
    end

    subgraph Layer 4: Input Validation
        V1[Path traversal protection]
        V2[API scoping]
    end
```

### 8.2 High-Risk Tool Confirmation (Deferred Approval)

Side-effectful tools are registered with `requires_approval=True`. When the LLM calls one, `agent.run()` returns a `DeferredToolRequests` object instead of executing the tool. The chat loop's `_handle_approvals()` prompts the user with `[y/n/a(yolo)]` for each pending call, then resumes the agent with `DeferredToolResults`.

**Key design properties:**
- **Separation of concerns:** Tools contain only business logic — no UI imports, no prompt calls
- **LLM-visible denials:** Denied calls return `ToolDenied(message)`, giving the LLM a structured signal to reason about (vs opaque `"cancelled by user"` strings)
- **Session yolo:** Picking `a` sets `deps.auto_confirm = True`, auto-approving all subsequent calls in the session
- **Testability:** Tests can pass `DeferredToolResults` programmatically without a TTY
- **Chained approvals:** The approval loop uses `while`, not `if` — a resumed run can itself produce more `DeferredToolRequests` when the LLM chains multiple side-effectful calls

**`ToolCallPart.args` type handling:** pydantic-ai's `ToolCallPart.args` is typed `str | dict[str, Any] | None`. Some model providers send args as a JSON string rather than a parsed dict. The approval prompt formatter must handle all three variants (`json.loads` for `str`, `{}` for `None`) before calling `.items()`.

**Approval classification:**

| Tool | Approval | Rationale |
|------|----------|-----------|
| `run_shell_command` | `requires_approval=True` | Arbitrary code execution in sandbox |
| `draft_email` | `requires_approval=True` | Creates Gmail draft on user's behalf |
| `post_slack_message` | `requires_approval=True` | Sends message visible to others |
| All other tools | None | Read-only operations |

**Bypass for Testing:** Set `auto_confirm: true` in settings.

### 8.3 Path Traversal Protection (Obsidian)

```python
safe_path = (vault / filename).resolve()
if not safe_path.is_relative_to(vault.resolve()):
    raise ModelRetry("Access denied: path is outside the vault.")
```

---

## 9. Data Flow

### 9.1 XDG Directory Structure

```
~/.config/co-cli/
└── settings.json          # User configuration

~/.local/share/co-cli/
├── co-cli.db              # OpenTelemetry traces (SQLite)
└── history.txt            # REPL command history
```

### 9.2 External Service Integration

```mermaid
graph LR
    subgraph Local Services
        Ollama[Ollama API<br/>localhost:11434]
        Docker[Docker Engine<br/>docker.sock]
        FS[File System<br/>Obsidian Vault]
    end

    subgraph Google Cloud
        Gemini[Gemini API]
        Drive[Drive API v3]
        Gmail[Gmail API v1]
        Calendar[Calendar API v3]
    end

    subgraph Other Cloud
        Slack[Slack Web API]
    end

    Co[Co CLI] --> Ollama
    Co --> Docker
    Co --> FS
    Co --> Gemini
    Co --> Drive
    Co --> Gmail
    Co --> Calendar
    Co --> Slack
```

---

## 10. Testing Policy

### Functional Testing Only

```mermaid
graph LR
    subgraph Allowed
        A1[Real Ollama]
        A2[Real Docker]
        A3[Real File System]
        A4[Real APIs]
    end

    subgraph Prohibited
        P1[unittest.mock]
        P2[Fakes/Stubs]
        P3[MagicMock]
    end

    style P1 fill:#f66
    style P2 fill:#f66
    style P3 fill:#f66
```

**Rules:**
1. All tests MUST be functional/integration tests
2. NO `unittest.mock`, fakes, or stubs
3. Tests must interact with real services
4. Verify actual side effects, not function calls

**Example Test Pattern:**
```python
# GOOD: Verify real side effect
def test_sandbox_execution():
    sandbox = Sandbox()
    result = sandbox.run_command("echo hello")
    assert "hello" in result

# BAD: Mock verification
def test_sandbox_execution():
    mock_container.exec_run.assert_called_with("echo hello")
```

---

## 11. Dependencies

### Runtime

| Package | Version | Purpose |
|---------|---------|---------|
| `pydantic-ai` | ^1.52.0 | LLM orchestration |
| `typer` | ^0.21.1 | CLI framework |
| `rich` | ^14.3.2 | Terminal UI |
| `prompt-toolkit` | ^3.0.52 | Interactive REPL |
| `docker` | ^7.1.0 | Container management |
| `google-genai` | ^1.61.0 | Gemini API |
| `google-api-python-client` | ^2.189.0 | Drive/Gmail/Calendar |
| `google-auth-oauthlib` | ^1.2.4 | OAuth2 |
| `slack-sdk` | ^3.39.0 | Slack API |
| `opentelemetry-sdk` | ^1.39.1 | Tracing |
| `datasette` | ^0.65.2 | Telemetry dashboard |

### Development

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | ^9.0.2 | Testing framework |
| `pytest-asyncio` | ^1.3.0 | Async test support |

---

## 12. Module Summary

| Module | Purpose |
|--------|---------|
| `main.py` | CLI entry point, chat loop, `_handle_approvals()`, OTel setup |
| `agent.py` | `get_agent()` factory — model selection, tool registration, system prompt |
| `deps.py` | `CoDeps` dataclass — runtime dependencies injected via `RunContext` |
| `config.py` | `Settings` (Pydantic BaseModel) from `settings.json` + env vars |
| `sandbox.py` | Docker wrapper — persistent container, CWD mount, command execution |
| `telemetry.py` | `SQLiteSpanExporter` — OTel spans to SQLite with WAL mode |
| `display.py` | Themed Rich Console, semantic styles, display helpers |
| `status.py` | `StatusInfo` dataclass + `get_status()` — pure-data environment/health probes |
| `banner.py` | ASCII art welcome banner, consumes `StatusInfo` for display |
| `tail.py` | Real-time span viewer (`co tail`) |
| `trace_viewer.py` | Static HTML trace viewer (`co traces`) |
| `google_auth.py` | Google credential resolution + service builder |
| `tools/shell.py` | `run_shell_command` — sandbox execution, `requires_approval=True` |
| `tools/obsidian.py` | `search_notes`, `list_notes`, `read_note` — vault search |
| `tools/google_drive.py` | `search_drive`, `read_drive_file` — Drive API |
| `tools/google_gmail.py` | `list_emails`, `search_emails`, `draft_email` — Gmail API |
| `tools/google_calendar.py` | `list_calendar_events`, `search_calendar_events` — Calendar API |
| `tools/slack.py` | `post_slack_message` — Slack WebClient, `requires_approval=True` |
