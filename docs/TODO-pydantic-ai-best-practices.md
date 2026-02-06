# TODO: Pydantic AI Best Practices Refactoring

**Status:** Ready for Implementation
**Goal:** Refactor `co-cli` to serve as a reference implementation of pydantic-ai production patterns.
**Approach:** Vertical slices - each batch is independently verifiable.

**Reference:**
- Best Practices: `docs/PYDANTIC-AI-CLI-BEST-PRACTICES.md`
- Local SDK: `/Users/binle/workspace_genai/pydantic-ai`
- Key Examples: `bank_support.py`, `rag.py`, `weather_agent.py`

---

## Gap Analysis

| Category | Current | Best Practice | Severity |
|----------|---------|---------------|----------|
| **State Management** | Global `settings` import | `deps_type` + `RunContext` | 🔴 Critical |
| **Tools** | `tool_plain()` + global state | `agent.tool()` + `RunContext[Deps]` | 🔴 Critical |
| **Gemini Model** | ~~`GeminiModel` (deprecated)~~ | `google-gla:` model string | ✅ Fixed (Batch 1) |
| **Tool Organization** | `comm.py` junk drawer | Single-responsibility modules | 🟡 Medium |
| **Error Handling** | Return error strings | `ModelRetry` for self-healing | 🟡 Medium |
| **Testing** | Real services only | `TestModel` for unit tests | 🟡 Medium |
| **Tool Approval** | `typer.confirm()` inside tools | `requires_approval=True` + `DeferredToolRequests` | 🔵 Deferred (Batch 6) |

---

## Implementation Batches (Vertical Slices)

Each batch is independently verifiable. Complete one before starting the next.

---

## Batch 1: Shell Tool End-to-End (Foundation)

**Goal:** Prove the deps pattern works with one tool before migrating others.

**Why shell first?**
- Simplest deps requirement (just Sandbox)
- No external API clients needed
- Core functionality users will test immediately

**Deferred to later batch:** `requires_approval=True` pattern (requires `DeferredToolRequests` handling in chat loop). For now, keep existing `typer.confirm()` inside the tool.

### 1.1 Create Minimal deps.py

**File:** `co_cli/deps.py` (new)

```python
from dataclasses import dataclass
from co_cli.sandbox import Sandbox

@dataclass
class CoDeps:
    """Runtime dependencies for agent tools.

    Design: Contains runtime resources, NOT config objects.
    Settings creates these in main.py, then injects here.
    """
    sandbox: Sandbox
    auto_confirm: bool = False  # For human-in-the-loop (until we adopt DeferredToolRequests)
    session_id: str = ""
```

### 1.2 Update shell.py to Use RunContext

**File:** `co_cli/tools/shell.py`

```python
import typer
from pydantic_ai import RunContext
from co_cli.deps import CoDeps


def run_shell_command(ctx: RunContext[CoDeps], cmd: str) -> str:
    """Execute a shell command inside a sandboxed Docker container.

    Args:
        cmd: The shell command to run.
    """
    # Human-in-the-loop confirmation (temporary until DeferredToolRequests migration)
    if not ctx.deps.auto_confirm:
        if not typer.confirm(f"Execute command: {cmd}?", default=False):
            return "Command cancelled by user."

    try:
        return ctx.deps.sandbox.run_command(cmd)
    except Exception as e:
        return f"Error executing command: {e}"
```

**Key changes:**
- Remove global `sandbox = Sandbox()` instance
- Remove `from co_cli.config import settings`
- Get sandbox and auto_confirm from `ctx.deps`

### 1.3 Update agent.py (Minimal Change)

**File:** `co_cli/agent.py`

```python
from pydantic_ai import Agent
from co_cli.deps import CoDeps
from co_cli.tools.shell import run_shell_command

# Keep existing tool imports for now (they still work with tool_plain)
from co_cli.tools.notes import list_notes, read_note
from co_cli.tools.drive import search_drive, read_drive_file
from co_cli.tools.comm import post_slack_message, draft_email, list_calendar_events

def get_agent() -> Agent[CoDeps, str]:
    """Factory function to create the Pydantic AI Agent."""
    # ... existing model selection logic ...

    agent: Agent[CoDeps, str] = Agent(
        model,
        deps_type=CoDeps,  # NEW: Add deps_type
        system_prompt=(
            "You are Co, a sarcastic but hyper-competent AI assistant. "
            # ... rest of prompt ...
        ),
    )

    # NEW: Register shell with RunContext pattern
    agent.tool(run_shell_command)

    # TEMPORARY: Keep old tools working during migration
    agent.tool_plain(list_notes)
    agent.tool_plain(read_note)
    agent.tool_plain(search_drive)
    agent.tool_plain(read_drive_file)
    agent.tool_plain(post_slack_message)
    agent.tool_plain(draft_email)
    agent.tool_plain(list_calendar_events)

    return agent
```

### 1.4 Update main.py to Inject Deps

**File:** `co_cli/main.py` (modify chat command)

```python
from uuid import uuid4
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli.config import settings


def create_deps() -> CoDeps:
    """Create deps from settings."""
    return CoDeps(
        sandbox=Sandbox(image=settings.docker_image),
        auto_confirm=settings.auto_confirm,
        session_id=uuid4().hex,
    )


async def chat_loop():
    agent = get_agent()
    deps = create_deps()
    try:
        # ... existing session setup ...
        while True:
            user_input = await session.prompt_async("Co > ")
            # ... existing exit/continue logic ...

            with console.status("[bold green]Co is thinking...", spinner="dots"):
                result = await agent.run(user_input, deps=deps)  # NEW: Pass deps

            console.print(Markdown(result.output))
    finally:
        deps.sandbox.cleanup()
```

### Batch 1 Checklist

- [x] Create `co_cli/deps.py` with `CoDeps` (sandbox, auto_confirm, session_id)
- [x] Update `co_cli/tools/shell.py`:
  - [x] Add `RunContext[CoDeps]` as first parameter
  - [x] Remove global `sandbox = Sandbox()` instance
  - [x] Remove `from co_cli.config import settings`
  - [x] Get sandbox from `ctx.deps.sandbox`
  - [x] Get auto_confirm from `ctx.deps.auto_confirm`
- [x] Update `co_cli/agent.py`:
  - [x] Add `deps_type=CoDeps` to Agent constructor
  - [x] Change return type to `Agent[CoDeps, str]`
  - [x] Register shell with `agent.tool()` (not `tool_plain`)
- [x] Update `co_cli/main.py`:
  - [x] Add `create_deps()` factory
  - [x] Pass `deps=deps` to `agent.run()`
  - [x] Add `finally` block to call `deps.sandbox.cleanup()`

### Batch 1 Verification

```bash
# 1. Run the CLI
uv run python -m co_cli.main chat

# 2. Test shell command (should prompt for approval via typer.confirm)
Co > list files in current directory

# 3. Verify:
# - Tool prompts "Execute command: ls?" (typer.confirm works)
# - Command executes in Docker sandbox
# - Result is displayed

# 4. Test auto_confirm bypass
CO_CLI_AUTO_CONFIRM=true uv run python -m co_cli.main chat
Co > list files in current directory
# - Should execute without prompting
```

---

## Batch 2: Obsidian Tools Migration

**Goal:** Add path-based deps, migrate notes tools.

### 2.1 Extend CoDeps

```python
@dataclass
class CoDeps:
    sandbox: Sandbox
    obsidian_vault_path: str | None = None  # NEW
    session_id: str = ""
```

### 2.2 Update Obsidian Tools

**File:** `co_cli/tools/notes.py` → rename to `obsidian.py`

```python
from pydantic_ai import RunContext, ModelRetry
from co_cli.deps import CoDeps

async def list_notes(ctx: RunContext[CoDeps], tag: str | None = None) -> list[str]:
    """List markdown notes in Obsidian vault."""
    vault = ctx.deps.obsidian_vault_path
    if not vault:
        raise ModelRetry("Obsidian vault not configured. Ask user to set obsidian_vault_path.")
    # ... rest of implementation using vault ...

async def read_note(ctx: RunContext[CoDeps], filename: str) -> str:
    """Read a note from Obsidian vault."""
    vault = ctx.deps.obsidian_vault_path
    if not vault:
        raise ModelRetry("Obsidian vault not configured.")
    # ... rest with ModelRetry for not found ...
```

### 2.3 Update create_deps()

```python
def create_deps() -> CoDeps:
    return CoDeps(
        sandbox=Sandbox(image=settings.docker_image),
        obsidian_vault_path=settings.obsidian_vault_path,  # NEW
        session_id=uuid4().hex,
    )
```

### 2.4 Update agent.py

```python
from co_cli.tools.obsidian import list_notes, read_note  # Updated import

# In get_agent():
agent.tool(list_notes)   # NEW pattern
agent.tool(read_note)    # NEW pattern
```

### Batch 2 Checklist

- [x] Rename `notes.py` → `obsidian.py`
- [x] Add `obsidian_vault_path` to `CoDeps`
- [x] Update `list_notes` and `read_note` to use `RunContext`
- [x] Add `ModelRetry` for missing vault / file not found
- [x] Update `create_deps()` to include vault path
- [x] Update `agent.py` imports and registration

### Batch 2 Verification

```bash
uv run python -m co_cli.main chat

# Test with vault configured
You: what notes do I have?
# Should list notes from vault

# Test with vault NOT configured (unset OBSIDIAN_VAULT_PATH)
You: what notes do I have?
# Should get ModelRetry message, agent asks user about configuration
```

---

## Batch 3: Google Tools Migration

**Goal:** Add API client deps, migrate Drive/Gmail/Calendar.

### 3.1 Create _google.py Helper

**File:** `co_cli/tools/_google.py` (new)

```python
"""Shared Google API authentication."""
import os
from typing import Any
import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build

def build_google_service(
    service_name: str,
    version: str,
    scopes: list[str],
    key_path: str | None,
) -> Any | None:
    """Build a Google API service client."""
    try:
        if key_path and os.path.exists(key_path):
            creds = service_account.Credentials.from_service_account_file(
                key_path, scopes=scopes
            )
        else:
            creds, _ = google.auth.default(scopes=scopes)
        return build(service_name, version, credentials=creds)
    except Exception:
        return None
```

### 3.2 Extend CoDeps

```python
@dataclass
class CoDeps:
    sandbox: Sandbox
    obsidian_vault_path: str | None = None
    google_drive: Any | None = None      # NEW
    google_gmail: Any | None = None      # NEW
    google_calendar: Any | None = None   # NEW
    session_id: str = ""
```

### 3.3 Update Tools

- `drive.py` - use `ctx.deps.google_drive`
- Split `comm.py` → `gmail.py`, `calendar.py`
- Add `ModelRetry` when service is None

### 3.4 Update create_deps()

```python
def create_deps() -> CoDeps:
    # Create Google clients if configured
    google_drive = google_gmail = google_calendar = None
    if settings.gcp_key_path:
        google_drive = build_google_service('drive', 'v3', [...], settings.gcp_key_path)
        google_gmail = build_google_service('gmail', 'v1', [...], settings.gcp_key_path)
        google_calendar = build_google_service('calendar', 'v3', [...], settings.gcp_key_path)

    return CoDeps(
        sandbox=Sandbox(image=settings.docker_image),
        obsidian_vault_path=settings.obsidian_vault_path,
        google_drive=google_drive,
        google_gmail=google_gmail,
        google_calendar=google_calendar,
        session_id=uuid4().hex,
    )
```

### Batch 3 Checklist

- [ ] Create `co_cli/tools/_google.py`
- [ ] Add Google clients to `CoDeps`
- [ ] Update `drive.py` to use `ctx.deps.google_drive`
- [ ] Create `gmail.py` from `comm.py` extract
- [ ] Create `calendar.py` from `comm.py` extract
- [ ] Add `ModelRetry` when clients are None
- [ ] Update `create_deps()` to build Google clients
- [ ] Update `agent.py` registrations
- [ ] Delete `comm.py`

### Batch 3 Verification

```bash
# With GCP configured
You: search my drive for meeting notes
# Should search Drive

# Without GCP configured
You: search my drive for meeting notes
# Should get helpful error via ModelRetry
```

---

## Batch 4: Slack Tool Migration

**Goal:** Add Slack client to deps, complete tool migration.

### 4.1 Extend CoDeps

```python
from slack_sdk import WebClient

@dataclass
class CoDeps:
    # ... existing ...
    slack_client: WebClient | None = None  # NEW
```

### 4.2 Create slack.py

```python
from pydantic_ai import RunContext, ModelRetry
from co_cli.deps import CoDeps

async def post_slack_message(ctx: RunContext[CoDeps], channel: str, text: str) -> str:
    """Send a message to a Slack channel."""
    if not ctx.deps.slack_client:
        raise ModelRetry("Slack not configured. Set SLACK_BOT_TOKEN.")

    response = ctx.deps.slack_client.chat_postMessage(channel=channel, text=text)
    return f"Message sent. TS: {response['ts']}"
```

### Batch 4 Checklist

- [ ] Add `slack_client` to `CoDeps`
- [ ] Create `slack.py` with `RunContext` pattern
- [ ] Keep `typer.confirm()` for now (like shell.py)
- [ ] Update `create_deps()` to build Slack client
- [ ] Remove Slack from `comm.py` (should be empty now)

### Batch 4 Verification

```bash
You: send a slack message to #general saying hello
# Should prompt for approval, then send
```

---

## Batch 5: Cleanup & Polish

**Goal:** Remove legacy patterns, add tests.

**Note:** GeminiModel deprecation was fixed in Batch 1 (now uses `google-gla:` model string).

### 5.1 Update tools/__init__.py

```python
"""Agent tools - external integrations."""
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import list_notes, read_note
from co_cli.tools.drive import search_drive, read_drive_file
from co_cli.tools.slack import post_slack_message
from co_cli.tools.gmail import draft_email
from co_cli.tools.calendar import list_calendar_events

__all__ = [
    "run_shell_command",
    "list_notes", "read_note",
    "search_drive", "read_drive_file",
    "post_slack_message",
    "draft_email",
    "list_calendar_events",
]
```

### 5.2 Add Unit Test

```python
# tests/test_agent_unit.py
import pytest
from pydantic_ai.models.test import TestModel
from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox

@pytest.fixture
def test_deps(tmp_path):
    return CoDeps(
        sandbox=Sandbox(),
        obsidian_vault_path=str(tmp_path),
        session_id="test",
    )

@pytest.mark.asyncio
async def test_agent_responds(test_deps):
    agent = get_agent()
    with agent.override(model=TestModel()):
        result = await agent.run("Hello", deps=test_deps)
        assert result.output
```

### Batch 5 Checklist

- [x] ~~Replace `GeminiModel` with model string or `GoogleModel`~~ (Done in Batch 1)
- [ ] Remove all `tool_plain()` calls (should all be `agent.tool()`)
- [ ] Remove global `settings` imports from all tools
- [ ] Update `tools/__init__.py` with clean exports
- [ ] Add basic unit test with `TestModel`
- [ ] Run full test suite

### Batch 5 Verification

```bash
# Run all tests
uv run pytest -v

# Manual verification
uv run python -m co_cli.main chat
# Test all tool categories work
```

---

## Summary

| Batch | Focus | New in CoDeps | Tools Migrated | Verification |
|-------|-------|---------------|----------------|--------------|
| **1** | Foundation | `sandbox`, `auto_confirm` | shell | Shell command works |
| **2** | Local files | `obsidian_vault_path` | list_notes, read_note | Notes queries work |
| **3** | Google APIs | `google_*` clients | drive, gmail, calendar | Drive search works |
| **4** | Slack | `slack_client` | post_slack_message | Slack post works |
| **5** | Cleanup | - | - | All tests pass |
| **6** | Human-in-the-loop | - | - | `DeferredToolRequests` approval flow |

**Note:** Batch 6 (deferred) will migrate from `typer.confirm()` inside tools to pydantic-ai's native `requires_approval=True` + `DeferredToolRequests` pattern. This requires significant chat loop changes and is intentionally deferred until the deps pattern is proven.

---

## Post-Migration Structure

```
co_cli/
├── deps.py              # CoDeps dataclass
├── agent.py             # Agent + tool registration
├── config.py            # Settings (unchanged)
├── sandbox.py           # Sandbox class (unchanged)
├── main.py              # CLI + create_deps()
└── tools/
    ├── __init__.py      # Re-exports
    ├── _google.py       # Shared Google auth
    ├── shell.py         # run_shell_command
    ├── obsidian.py      # list_notes, read_note
    ├── drive.py         # search_drive, read_drive_file
    ├── slack.py         # post_slack_message
    ├── gmail.py         # draft_email
    └── calendar.py      # list_calendar_events
```

---

## Reference

- Best Practices: `docs/PYDANTIC-AI-CLI-BEST-PRACTICES.md`
- Pydantic AI Docs: https://ai.pydantic.dev
- Deferred Tools: https://ai.pydantic.dev/deferred-tools/
