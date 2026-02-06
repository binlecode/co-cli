# Co - The Production-Grade Personal Assistant CLI

**Co** is an opinionated, privacy-first AI agent that lives in your terminal. It connects your local tools (Obsidian, Shell) with cloud services (Google Drive, Slack, Gmail) using a local LLM "Brain" and a Docker-based "Body" for safe execution.

Designed for developers who want a personal assistant that:
1.  **Respects Privacy**: Runs on local models (Ollama).
2.  **Is Safe**: Executes commands inside a transient Docker sandbox.
3.  **Is Observable**: Traces every thought and tool call via OpenTelemetry.
4.  **Is Useful**: Connects to your actual work context (Notes, Drive, Calendar).

---

## 🚀 Prerequisites

Before you begin, ensure you have the following installed:

1.  **Python 3.12**: We use version 3.12 for its proven stability and widespread support in 2026, ensuring a reliable production environment.
    *   The repo includes a `.python-version` file to help `pyenv` users auto-select the correct interpreter. It’s optional and does not affect runtime/distribution.

2.  **[Ollama](https://ollama.com/)**: To serve the local LLM.
    ```bash
    # Install Ollama and pull the model
    ollama run llama3
    ```
    *Note: You can use other models (Mistral, Llama 3.1) by updating `~/.config/co-cli/settings.json`.*

3.  **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**: For the sandboxed execution environment.
    *   Ensure Docker is running (`docker ps`).

4.  **[uv](https://github.com/astral-sh/uv)**: An extremely fast Python package manager.
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

---

## 🛠️ Installation & Setup

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
    cp settings.example.json ~/.config/co-cli/settings.json
    # Edit with your values
    ```

    **`~/.config/co-cli/settings.json`** (primary config):
    ```json
    {
      "llm_provider": "gemini",
      "gemini_api_key": "AIza...",
      "gemini_model": "gemini-2.0-flash",
      "obsidian_vault_path": "/Users/yourname/Documents/ObsidianVault",
      "slack_bot_token": "xoxb-your-bot-token",
      "gcp_key_path": "",
      "docker_image": "python:3.12-slim",
      "auto_confirm": false
    }
    ```

    **Configuration precedence** (highest to lowest):
    1. Environment variables (for CI/automation)
    2. `~/.config/co-cli/settings.json` (primary)
    3. Built-in defaults

    > 💡 Environment variables (e.g., `GEMINI_API_KEY`, `LLM_PROVIDER`) override settings.json for CI/automation.

    > 💡 **Google Authentication**: If `gcp_key_path` is empty, Co falls back to **Application Default Credentials (ADC)**.

## 🔌 Google Services (Drive/Gmail/Calendar)

Co supports two methods for accessing Google APIs. Choose the one that fits your use case.

### Option A: Personal Use (Recommended)
Use **Application Default Credentials (ADC)** to run Co as yourself. This is easiest for local development.

1.  **Install gcloud**: [Google Cloud CLI Install Guide](https://cloud.google.com/sdk/docs/install).
2.  **Login with Scopes**: Run this exact command to authorize Drive, Gmail, and Calendar access:
    ```bash
    gcloud auth application-default login --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar'
    ```
3.  **Configure**: Leave `"gcp_key_path": ""` in your `settings.json`.

### Option B: Automation / Headless (Service Account)
Use a Service Account if running on a server or if you want strict permission isolation.

1.  **Create Service Account**:
    ```bash
    gcloud iam service-accounts create co-cli-agent --display-name="Co CLI Agent"
    ```
2.  **Download Key**:
    ```bash
    gcloud iam service-accounts keys create ~/.config/co-cli/gcp-key.json \
        --iam-account=co-cli-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com
    ```
3.  **Share Access**: You MUST share specific Google Drive folders/files with the service account email (e.g., `co-cli-agent@...`) for it to see them.
4.  **Configure**: Update `settings.json` with `"gcp_key_path": "/Users/yourname/.config/co-cli/gcp-key.json"`.

## 🔌 Slack Configuration (Optional)

To enable Slack messaging:
1.  Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps).
2.  Navigate to **OAuth & Permissions** and add the following **Bot Token Scopes**:
    *   `chat:write` (to send messages)
    *   `channels:read` (to list channels)
    *   `users:read` (to find users)
3.  **Install to Workspace** and copy the **Bot User OAuth Token** (starts with `xoxb-`).
4.  Add the token to your `settings.json`.

---

## 💻 Usage

To run `co` commands, prefix them with `uv run co` (or install the tool globally).

### 1. Check System Status
Verify that Ollama, Docker, and your config are healthy.
```bash
uv run co status
```

### 2. Start Chatting
Enter the interactive loop. Co will maintain context until you exit.
```bash
uv run co chat
```
**Example Prompts:**
> "Co, what's in my 'Projects' note?"
> "Search Google Drive for the 'Q1 Proposal' and summarize it."
> "Check my calendar for today and draft an email to the team if I have free time."
> "Run `ls -la` to see what files are in this directory." (Runs in Docker!)

### 3. View Telemetry Logs
Launch a local Datasette dashboard to inspect the agent's "thought process" and tool usage.
```bash
uv run co logs
```

---

## 🛡️ Security & Architecture

### The "Safety Net" (Docker Sandbox)
Co **never** runs shell commands directly on your host machine.
*   When you ask to "run a script" or "list files", Co spins up a transient `python:3.12-slim` container.
*   Your current working directory is mounted to `/workspace` inside the container.
*   If the agent tries to `rm -rf /`, it only destroys the container, not your laptop.

### Human-in-the-Loop
For high-stakes actions, Co is configured to **ask for permission**:
*   sending emails
*   posting to Slack
*   executing shell commands

### Privacy
*   **LLM**: 100% Local (Ollama) by default.
*   **Logs**: Stored locally in SQLite (`~/.local/share/co-cli/co-cli.db`).
*   **API Keys**: Managed via `settings.json` and never logged.

---

## 🐳 Docker Sandbox Configuration

The shell tool executes commands inside a Docker container for safety. Here's how it works:

### Container Lifecycle

| Event | Behavior |
|-------|----------|
| **First command** | Creates container `co-runner`, keeps it running |
| **Subsequent commands** | Reuses existing container via `docker exec` |
| **Session end** | Stops and removes the container |

The container stays alive throughout your chat session, so there's no startup overhead per command.

### Configuration Options

Add these to your `settings.json`:

```json
{
  "docker_image": "python:3.12-slim",
  "auto_confirm": false
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `docker_image` | `python:3.12-slim` | Docker image for the sandbox |
| `auto_confirm` | `false` | Skip confirmation prompts (use with caution) |

Environment variable overrides:
- `CO_CLI_DOCKER_IMAGE` → `docker_image`
- `CO_CLI_AUTO_CONFIRM` → `auto_confirm`

### Volume Mounts

The sandbox mounts your **current working directory** to `/workspace` inside the container:

```
Host: $(pwd)  →  Container: /workspace (read-write)
```

Commands execute with `/workspace` as the working directory, so relative paths work as expected.

### Custom Docker Images

For specialized workflows, use a custom image:

```json
{
  "docker_image": "node:20-slim"
}
```

Or build your own with pre-installed tools:

```dockerfile
# Dockerfile.co-sandbox
FROM python:3.12-slim
RUN pip install pandas numpy requests
```

```bash
docker build -t co-sandbox -f Dockerfile.co-sandbox .
```

```json
{
  "docker_image": "co-sandbox"
}
```

### Troubleshooting

**"Docker is not available"**
- Ensure Docker Desktop is running: `docker ps`
- Check Docker socket permissions

**Container conflicts**
- If a stale `co-runner` container exists: `docker rm -f co-runner`

**Permission denied on mounted files**
- The container runs as root by default
- Files created inside `/workspace` will be owned by root on the host

---

## 🧪 Testing

Co uses **functional tests only** - no mocks or stubs. Tests interact with real services.

### Quick Start

```bash
# Run all tests (some will skip if services unavailable)
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_tools.py
```

### Test Requirements by Category

| Test Category | Required Setup | Skip Reason if Missing |
|---------------|----------------|------------------------|
| **Shell/Sandbox** | Docker running | "Docker not available" |
| **Notes** | None (uses temp files) | - |
| **Drive/Gmail/Calendar** | GCP credentials | "GCP Credentials missing" |
| **Slack** | Slack bot token | "SLACK_BOT_TOKEN missing" |
| **LLM E2E (Gemini)** | Gemini API key + provider | "Provider not set to gemini" |
| **LLM E2E (Ollama)** | Ollama running + provider | "Provider not set to ollama" |

### Setting Up All Services

#### 1. Docker (for Shell tests)

```bash
# Install Docker Desktop: https://www.docker.com/products/docker-desktop/

# Verify Docker is running
docker ps

# Pull the sandbox image (optional, auto-pulled on first use)
docker pull python:3.12-slim
```

#### 2. Ollama (for local LLM tests)

```bash
# Install Ollama: https://ollama.com/

# Pull a model
ollama pull llama3

# Verify Ollama is running
curl http://localhost:11434/api/tags
```

#### 3. Gemini API (for cloud LLM tests)

1. Get an API key from [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Add to your settings:
   ```bash
   # In ~/.config/co-cli/settings.json
   {
     "gemini_api_key": "AIza...",
     "llm_provider": "gemini"
   }
   ```

#### 4. Google Services (Drive/Gmail/Calendar)

**Option A: Personal Use (ADC)**
```bash
gcloud auth application-default login \
  --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar'
```

**Option B: Service Account**
```bash
# Create and download key
gcloud iam service-accounts create co-cli-agent
gcloud iam service-accounts keys create ~/.config/co-cli/gcp-key.json \
  --iam-account=co-cli-agent@YOUR_PROJECT.iam.gserviceaccount.com

# Add to settings.json
{
  "gcp_key_path": "/Users/yourname/.config/co-cli/gcp-key.json"
}
```

#### 5. Slack (for messaging tests)

1. Create app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add Bot Token Scopes: `chat:write`, `channels:read`, `users:read`
3. Install to workspace, copy Bot Token (`xoxb-...`)
4. Add to settings:
   ```json
   {
     "slack_bot_token": "xoxb-your-token"
   }
   ```

### Running Full Test Suite

Once all services are configured:

```bash
# Set provider for E2E tests
export LLM_PROVIDER=gemini  # or 'ollama'

# Run all tests - should show 0 skipped
uv run pytest -v

# Expected output:
# ==================== 18 passed in X.XXs ====================
```

### Test Configuration for CI/CD

For automated testing, use environment variables:

```bash
export GEMINI_API_KEY="your-key"
export LLM_PROVIDER="gemini"
export CO_CLI_AUTO_CONFIRM="true"
export SLACK_BOT_TOKEN="xoxb-..."
# GCP uses ADC or GOOGLE_APPLICATION_CREDENTIALS

uv run pytest
```

---

## 🧩 Modules

| Module | Description | Tooling |
| :--- | :--- | :--- |
| **Brain** | Decision making & Conversation | `pydantic-ai`, `ollama` |
| **Shell** | Safe file/script execution | `docker` |
| **Notes** | RAG over local Markdown files | `glob`, `fs` |
| **Drive** | Hybrid Search (Metadata + Semantic) | `google-api-python-client` |
| **Comm** | Slack msgs, Gmail drafts, GCal events | `slack_sdk`, `google-auth` |

---

## 🤝 Contributing

1.  Run `uv sync` to install dev dependencies.
2.  Follow the style in `docs/DESIGN-co-cli.md`.
3.  Ensure type hints are used everywhere.
