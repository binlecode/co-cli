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
        Notes[Notes Tool]
        Drive[Drive Tool]
        Comm[Comm Tool]
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
    Agent --> Notes
    Agent --> Drive
    Agent --> Comm

    Shell --> Docker
    Notes --> Obsidian
    Drive --> GDrive
    Comm --> Slack
    Comm --> Gmail
    Comm --> GCal

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

    loop Interactive REPL
        User->>CLI: Enter query
        CLI->>CLI: Show spinner "Co is thinking..."
        CLI->>Agent: agent.run(user_input)
        Agent->>LLM: Send prompt + context
        LLM->>Agent: Response with tool calls

        opt Tool Execution
            Agent->>Tools: Execute tool
            Tools->>User: Confirm (if high-risk)
            User->>Tools: y/n
            Tools->>External: API call / Docker exec
            External->>Tools: Result
            Tools->>Agent: Tool output
            Agent->>LLM: Continue with tool result
        end

        LLM->>Agent: Final response
        Agent->>CLI: result.output
        CLI->>User: Render Markdown response
    end

    User->>CLI: exit/quit
    CLI->>User: Session ended
```

### 3.2 Tool Execution Flow (Shell Example)

```mermaid
sequenceDiagram
    participant Agent
    participant ShellTool as shell.py
    participant Sandbox as sandbox.py
    participant Docker as Docker Container

    Agent->>ShellTool: run_shell_command("ls -la")
    ShellTool->>ShellTool: Check auto_confirm setting

    alt Confirmation Required
        ShellTool->>User: "Execute command: ls -la? [y/N]"
        User->>ShellTool: y
    end

    ShellTool->>Sandbox: sandbox.run_command("ls -la")
    Sandbox->>Sandbox: ensure_container()

    alt Container Not Running
        Sandbox->>Docker: containers.run(python:3.12-slim)
        Docker->>Sandbox: Container started
    end

    Sandbox->>Docker: container.exec_run("ls -la", workdir="/workspace")
    Docker->>Sandbox: Output bytes
    Sandbox->>ShellTool: Decoded output string
    ShellTool->>Agent: Command result
```

---

## 4. Core Components

### 4.1 Agent (`co_cli/agent.py`)

The Agent is the central orchestrator that connects the LLM to tools.

```mermaid
classDiagram
    class Agent {
        +model: Model
        +system_prompt: str
        +tools: List[Tool]
        +run(input: str) Result
    }

    class GeminiModel {
        +model_name: str
        +api_key: str
    }

    class OpenAIChatModel {
        +model_name: str
        +provider: OpenAIProvider
    }

    Agent --> GeminiModel : uses (gemini)
    Agent --> OpenAIChatModel : uses (ollama)
```

**Factory Function: `get_agent()`**

```python
def get_agent() -> Agent:
    # 1. Read provider from settings
    provider_name = settings.llm_provider.lower()

    # 2. Initialize appropriate model
    if provider_name == "gemini":
        model = GeminiModel(model_name, api_key)
    else:
        # Ollama via OpenAI-compatible API
        provider = OpenAIProvider(base_url=f"{ollama_host}/v1")
        model = OpenAIChatModel(model_name, provider)

    # 3. Create agent with system prompt
    agent = Agent(model, system_prompt="You are Co...")

    # 4. Register all tools
    agent.tool_plain(run_shell_command)
    agent.tool_plain(list_notes)
    # ... more tools

    return agent
```

**System Prompt:**
```
You are Co, a sarcastic but hyper-competent AI assistant.
You provide concise, useful, and occasionally opinionated help.
You have access to a sandboxed shell tool, local Obsidian notes,
Google Drive, Slack, and Google Workspace (Gmail, Calendar).
```

### 4.2 Configuration (`co_cli/config.py`)

XDG-compliant configuration management with environment variable fallback.

```mermaid
classDiagram
    class Settings {
        +obsidian_vault_path: Optional[str]
        +slack_bot_token: Optional[str]
        +gcp_key_path: Optional[str]
        +auto_confirm: bool
        +docker_image: str
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
| `gcp_key_path` | `GCP_KEY_PATH` | `None` |
| `auto_confirm` | `CO_CLI_AUTO_CONFIRM` | `false` |
| `docker_image` | `CO_CLI_DOCKER_IMAGE` | `"python:3.12-slim"` |

### 4.3 Sandbox (`co_cli/sandbox.py`)

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

### 4.4 CLI (`co_cli/main.py`)

Typer-based CLI with three main commands.

```mermaid
stateDiagram-v2
    [*] --> Idle

    state "co chat" as Chat {
        Idle --> Waiting: Start REPL
        Waiting --> Thinking: User input
        Thinking --> ToolExec: Tool needed
        ToolExec --> Confirm: High-risk tool
        Confirm --> ToolExec: Confirmed
        ToolExec --> Thinking: Tool result
        Thinking --> Responding: LLM done
        Responding --> Waiting: Display output
        Waiting --> [*]: exit/quit
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
| `co logs` | Telemetry dashboard | Launches Datasette |

**REPL Features:**
- History: Saved to `~/.local/share/co-cli/history.txt`
- Spinner: "Co is thinking..." during inference
- Output: Rendered as Rich Markdown

### 4.5 Telemetry (`co_cli/telemetry.py`)

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
    subgraph Agent Tools
        A[run_shell_command]
        B[list_notes]
        C[read_note]
        D[search_drive]
        E[read_drive_file]
        F[post_slack_message]
        G[draft_email]
        H[list_calendar_events]
    end

    subgraph Risk Level
        HR[High Risk<br/>Requires Confirmation]
        LR[Low Risk<br/>Auto-execute]
    end

    A --> HR
    F --> HR
    G --> HR

    B --> LR
    C --> LR
    D --> LR
    E --> LR
    H --> LR
```

### 5.2 Shell Tool (`co_cli/tools/shell.py`)

```python
def run_shell_command(cmd: str) -> str:
    """Execute a shell command inside a sandboxed Docker container."""
    # 1. Confirmation check
    if not settings.auto_confirm:
        if not typer.confirm(f"Execute command: {cmd}?"):
            return "Command cancelled by user."

    # 2. Execute in sandbox
    return sandbox.run_command(cmd)
```

### 5.3 Notes Tool (`co_cli/tools/notes.py`)

```python
def list_notes(tag: Optional[str] = None) -> List[str]:
    """List markdown notes, optionally filtered by tag."""
    # Glob search: vault_path/**/*.md
    # If tag provided: filter by content containing tag

def read_note(filename: str) -> str:
    """Read note content with directory traversal protection."""
    # Sanitize path with os.path.normpath
    # Verify path starts with vault_path
```

### 5.4 Drive Tool (`co_cli/tools/drive.py`)

```mermaid
sequenceDiagram
    participant Tool as search_drive()
    participant Auth as get_drive_service()
    participant API as Drive API v3

    Tool->>Auth: Get credentials

    alt Service Account Key Exists
        Auth->>Auth: Load from gcp_key_path
    else Use ADC
        Auth->>Auth: google.auth.default()
    end

    Auth->>Tool: Drive service
    Tool->>API: files().list(q="name contains 'query'")
    API->>Tool: List of files (id, name, mimeType, modifiedTime)
```

**Hybrid Search Strategy:**
1. **API-Level Filter:** Use Drive API `q` parameter for keyword/metadata filtering
2. **Result:** Returns top 10 matches (no semantic re-ranking in current impl)

### 5.5 Communication Tools (`co_cli/tools/comm.py`)

| Tool | Service | Auth | Confirmation |
|------|---------|------|--------------|
| `post_slack_message` | Slack WebClient | Bot Token | Required |
| `draft_email` | Gmail API | GCP Key/ADC | Required |
| `list_calendar_events` | Calendar API | GCP Key/ADC | Not required |

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

**Rationale:**
1. Prevents conversation history forking
2. Prevents overloading Ollama (can't handle parallel inference on consumer hardware)

---

## 7. Security Model

### 7.1 Defense Layers

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

### 7.2 High-Risk Tool Confirmation

```python
# Pattern used in all high-risk tools
if not settings.auto_confirm:
    if not typer.confirm(f"Execute: {action}?", default=False):
        return "Action cancelled by user."
```

**Bypass for Testing:** Set `auto_confirm: true` in settings.

### 7.3 Path Traversal Protection (Notes)

```python
safe_path = os.path.normpath(os.path.join(vault_path, filename))
if not safe_path.startswith(os.path.abspath(vault_path)):
    return "Error: Access denied. File is outside the vault."
```

---

## 8. Data Flow

### 8.1 XDG Directory Structure

```
~/.config/co-cli/
└── settings.json          # User configuration

~/.local/share/co-cli/
├── co-cli.db              # OpenTelemetry traces (SQLite)
└── history.txt            # REPL command history
```

### 8.2 External Service Integration

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

## 9. Testing Policy

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

## 10. Dependencies

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
| `platformdirs` | ^4.0.0 | XDG paths |

### Development

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | ^9.0.2 | Testing framework |
| `pytest-asyncio` | ^1.3.0 | Async test support |

---

## 11. Implementation Status

| Phase | Component | Status |
|-------|-----------|--------|
| 1 | CLI Skeleton + LLM Connection | ✅ Complete |
| 2 | Docker Sandbox | ✅ Complete |
| 3 | Obsidian Notes Tool | ✅ Complete |
| 4 | Google Drive Tool | ✅ Complete |
| 5 | Slack/Gmail/Calendar Tools | ✅ Complete |
| 6 | Telemetry + Datasette | ✅ Complete |
| 7 | Dual-Engine (Gemini) | ✅ Complete |
| 8 | XDG Configuration | ✅ Complete |
| 9 | Functional Testing | ✅ Complete |
