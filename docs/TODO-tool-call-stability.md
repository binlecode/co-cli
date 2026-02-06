# TODO: Production Tool-Call Stability for GLM-4.7-Flash

**Status:** Planned
**Created:** 2026-02-06
**Priority:** High — addresses the most common failure modes in local-model tool calling

---

## Motivation

GLM-4.7-Flash tool-calling reliability in production is hampered by five gaps: no retry budget, error strings instead of retryable exceptions, missing system prompt guidance for structured output, inconsistent tool return types, and no loop guard. This document specifies each gap and the exact fix.

---

## ModelRetry Design Principle (v0.2.2 — done)

**`ModelRetry` = "you called this wrong, fix your parameters"**
**Empty result = "query was fine, nothing matched"**

### Raise `ModelRetry` when the LLM can self-correct:

| Scenario | Example hint |
|----------|-------------|
| Missing setup / config | `"Google Drive not configured. Set google_credentials_path..."` |
| API not enabled | `"Run: gcloud services enable drive.googleapis.com"` |
| Pagination violation | `"Page 5 not available. Search from page 1 first."` |
| Malformed parameters | `"Invalid date format. Use YYYY-MM-DD."` |
| Shell command error | `"Command failed: No such file or directory"` |

### Return empty result when there's nothing to fix:

| Scenario | Return |
|----------|--------|
| Search matched zero files | `{"display": "No files found.", "count": 0, ...}` |
| Time range had no events | `{"display": "No events found.", "count": 0}` |
| List is genuinely empty | `{"display": "No items.", "count": 0}` |

### Industry consensus on retry counts

| Framework | Default retries | Recommendation |
|-----------|-----------------|----------------|
| pydantic-ai | 1 | 2-3 for production |
| Anthropic Claude | 2-3 self-corrections | built-in |
| OpenAI Agents SDK | N/A (system prompt driven) | — |
| LangGraph | configurable | + circuit breaker |

**Read-only tools**: `retries=3`. **Side-effectful tools**: `retries=1-2`.

### Completed

- [x] `search_drive` returns `{"count": 0}` on empty results instead of `ModelRetry` (`google_drive.py:56-58`)
- [x] `test_drive_search_empty_result` functional test added
- [x] Google test `HAS_GCP` checks all credential sources (explicit, token, ADC)
- [x] Removed unit tests (`test_agent.py`, `test_batch1_integration.py`)

---

## Gap 1: No Retry Budget on Tools No Retry Budget on Tools

**Impact:** High
**Files:** `co_cli/agent.py`

### Problem

Tools are registered with `agent.tool(fn)` — no `retries` argument. When a tool's `retries` is `None` (the default), it inherits the agent-level `retries`, which defaults to `1`. For a local model that occasionally produces malformed arguments, 1 retry is too low. Production systems typically use 2-3 retries.

### Current Code (`co_cli/agent.py`)

```python
agent: Agent[CoDeps, str] = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
)

# Register tools with RunContext pattern
agent.tool(run_shell_command)
agent.tool(search_notes)
# ... 10 more tools
```

### Fix

Set `retries=3` at the **agent level** — this becomes the default for all tools without repeating it 12 times:

```python
TOOL_RETRIES = 3

agent: Agent[CoDeps, str] = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
    retries=TOOL_RETRIES,
)

# Tool registrations unchanged — they inherit agent-level retries
agent.tool(run_shell_command)
agent.tool(search_notes)
# ...
```

Per-tool overrides are still possible if needed in the future (e.g., `agent.tool(draft_email, retries=1)` for tools with side effects that shouldn't auto-retry).

### API Reference (pydantic-ai v1.52.0)

The retry cascade:

| Level | Parameter | Default |
|-------|-----------|---------|
| **Agent-level** | `Agent(retries=N)` | `1` |
| **Tool-level** | `agent.tool(fn, retries=N)` | `None` (falls back to agent-level) |

When `ModelRetry` is raised (or a `ValidationError` on tool args), pydantic-ai sends the error back to the LLM as a `RetryPromptPart`. The current retry attempt is accessible inside tools via `ctx.retry`.

### Rationale

- 3 retries gives the model enough chances to self-correct malformed JSON or wrong argument types.
- Agent-level setting is DRY — one line instead of 12.
- A named constant (`TOOL_RETRIES`) makes the budget visible and easy to tune.
- pydantic-ai's retry mechanism uses the `ModelRetry` exception — tools that already raise `ModelRetry` (Google tools, Obsidian) benefit immediately.

---

## Gap 2: Shell Error Path Swallows Failures — Two-Layer Fix

**Impact:** Medium
**Files:** `co_cli/sandbox.py`, `co_cli/tools/shell.py`

### Problem

Errors are swallowed at **two layers**, so the LLM never learns that a command failed:

1. **`sandbox.py:56-57`** — `run_command()` catches all exceptions and returns `f"Sandbox Error: {e}"` as a string. It never raises.
2. **`sandbox.py:54-55`** — `run_command()` ignores `exit_code`. A command like `cat nonexistent.txt` returns exit code 1 with stderr, but the output is returned as if it succeeded.
3. **`shell.py:21-22`** — The `except Exception` block in `run_shell_command` almost never fires (because `run_command` never raises). Even if it did, it returns an error string instead of `ModelRetry`.

Net effect: the LLM sees every command result as a successful tool return, even for failures. It has no signal to self-correct.

### Current Code

**`co_cli/sandbox.py`:**

```python
def run_command(self, cmd: str) -> str:
    try:
        container = self.ensure_container()
        exit_code, output = container.exec_run(cmd, workdir="/workspace")
        return output.decode("utf-8")       # exit_code silently ignored
    except Exception as e:
        return f"Sandbox Error: {e}"         # error swallowed as string
```

**`co_cli/tools/shell.py`:**

```python
try:
    return ctx.deps.sandbox.run_command(cmd)  # always returns a string
except Exception as e:
    return f"Error executing command: {e}"    # dead code
```

### Fix

**Step 1 — `sandbox.py`:** Make `run_command()` raise on errors instead of returning strings. Add a custom exception for non-zero exit codes.

```python
class CommandError(Exception):
    """Raised when a sandbox command fails (non-zero exit or Docker error)."""
    def __init__(self, message: str, exit_code: int = -1, stderr: str = ""):
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(message)


def run_command(self, cmd: str) -> str:
    container = self.ensure_container()
    exit_code, output = container.exec_run(cmd, workdir="/workspace")
    decoded = output.decode("utf-8")
    if exit_code != 0:
        raise CommandError(
            f"Command exited with code {exit_code}: {decoded.strip()}",
            exit_code=exit_code,
            stderr=decoded,
        )
    return decoded
```

**Step 2 — `shell.py`:** Catch `CommandError` and raise `ModelRetry` with the error details.

```python
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.sandbox import CommandError
from co_cli.tools._confirm import confirm_or_yolo


def run_shell_command(ctx: RunContext[CoDeps], cmd: str) -> str:
    if not confirm_or_yolo(ctx, f"Execute command: [bold]{cmd}[/bold]?"):
        return "Command cancelled by user."

    try:
        return ctx.deps.sandbox.run_command(cmd)
    except CommandError as e:
        raise ModelRetry(f"Command failed (exit {e.exit_code}): {e}")
    except Exception as e:
        raise ModelRetry(f"Sandbox error: {e}")
```

### Why Two Layers

| Layer | Responsibility | Error handling |
|-------|---------------|----------------|
| `sandbox.py` | Docker execution, exit code semantics | Raises `CommandError` — clean separation, testable without pydantic-ai |
| `shell.py` | LLM tool interface | Translates `CommandError` → `ModelRetry` for the retry mechanism |

This keeps `sandbox.py` framework-agnostic (no pydantic-ai dependency) while `shell.py` handles the LLM-specific retry logic.

### Rationale

- `ModelRetry` tells pydantic-ai the tool call failed and the LLM should try again (up to `retries` times, see Gap 1).
- The LLM sees the error message and exit code, and can adjust — e.g., fixing a misspelled path or trying an alternative command.
- Separating `CommandError` from `ModelRetry` keeps the sandbox reusable outside pydantic-ai.
- This is consistent with how Google and Obsidian tools already handle errors via `ModelRetry`.

---

## Gap 3: System Prompt Doesn't Instruct `display` Field Passthrough

**Impact:** High
**Files:** `co_cli/agent.py`

### Problem

Tools return `{"display": "...", "count": N, "has_more": true}`. The system prompt says "show tool output directly" but never mentions the `display` field by name. The LLM reformats the dict, losing URLs and metadata.

### Current System Prompt Section

```
### Response Style
- Show tool output directly—don't summarize or paraphrase
```

### Fix

Add a new section to the system prompt in `get_agent()`:

```python
system_prompt = """You are Co, a CLI assistant running in the user's terminal.

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
"""
```

### Rationale

- The `display` field is pre-formatted with URLs baked in. If the LLM reformats into a table, URLs are lost.
- Explicit instruction ("show the `display` value verbatim") is clearer than the vague "show tool output directly".
- The old "Show tool output directly" line in Response Style is removed — replaced by the more specific Tool Output section.

---

## Gap 4: Obsidian Tools Don't Follow the `display` Return Pattern

**Impact:** Medium
**Files:** `co_cli/tools/obsidian.py`

### Problem

`search_notes` returns `list[dict]`, `list_notes` returns `list[str]`. Every other tool returns `dict[str, Any]` with a `display` field. This inconsistency forces the LLM to format Obsidian results differently, which is error-prone.

### Current Return Types

| Function | Current Return | Expected Return |
|----------|---------------|-----------------|
| `search_notes` | `list[dict]` with `{file, snippet}` | `dict[str, Any]` with `display` field |
| `list_notes` | `list[str]` of filenames | `dict[str, Any]` with `display` field |
| `read_note` | `str` (raw content) | No change — raw content is appropriate |

### Fix for `search_notes`

```python
from typing import Any  # add import

def search_notes(ctx: RunContext[CoDeps], query: str, limit: int = 10) -> dict[str, Any]:
    # ... existing search logic unchanged ...

    # Build display string
    lines = []
    for r in results:
        lines.append(f"**{r['file']}**")
        lines.append(f"  {r['snippet']}")
        lines.append("")

    return {
        "display": "\n".join(lines).rstrip(),
        "count": len(results),
        "has_more": False,  # current impl doesn't paginate
    }
```

### Fix for `list_notes`

```python
def list_notes(ctx: RunContext[CoDeps], tag: str | None = None) -> dict[str, Any]:
    # ... existing filter logic unchanged, producing `note_paths: list[str]` ...

    display = "\n".join(f"- {p}" for p in note_paths)

    return {
        "display": display,
        "count": len(note_paths),
    }
```

**Note:** `obsidian.py` currently has no `from typing import Any` import — this must be added.

### Rationale

- Uniform `{"display": ..., "count": ...}` return type across all tools means the system prompt instruction (Gap 3) works universally.
- The LLM doesn't need tool-specific formatting logic.
- `read_note` stays as `str` — it returns raw file content, not a formatted summary.

---

## Gap 5: Explicit Tool-Call Loop Guard

**Impact:** Medium
**Files:** `co_cli/main.py`

### Problem

We rely on pydantic-ai's default `UsageLimits(request_limit=50)` — a generous implicit guard. For a local model that is more loop-prone than cloud models, an explicit lower limit makes the failure mode visible and the budget intentional rather than accidental.

### Current Code (`co_cli/main.py`)

```python
result = await agent.run(
    user_input, deps=deps, message_history=message_history,
    model_settings=model_settings,
)
```

No explicit `usage_limits` — falls back to the pydantic-ai default of `request_limit=50`.

### `UsageLimits` API Reference (pydantic-ai v1.52.0)

`UsageLimits` is a **dataclass** (`kw_only=True`). Import from either location:

```python
from pydantic_ai import UsageLimits          # top-level re-export
from pydantic_ai.usage import UsageLimits    # canonical location
```

Available fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `request_limit` | `int \| None` | `50` | Max LLM round-trips (each may include multiple tool calls) |
| `tool_calls_limit` | `int \| None` | `None` | Max successful tool call executions |
| `input_tokens_limit` | `int \| None` | `None` | Max input/prompt tokens |
| `output_tokens_limit` | `int \| None` | `None` | Max output/response tokens |
| `total_tokens_limit` | `int \| None` | `None` | Max total tokens (input + output) |

**There is no `max_agent_steps` field.** The equivalent is `request_limit`.

When a limit is exceeded, pydantic-ai raises `UsageLimitExceeded` (importable from `pydantic_ai` or `pydantic_ai.exceptions`), which inherits from `AgentRunError -> Exception`.

### Fix

```python
from pydantic_ai.usage import UsageLimits

# ... in chat_loop() ...

result = await agent.run(
    user_input,
    deps=deps,
    message_history=message_history,
    model_settings=model_settings,
    usage_limits=UsageLimits(request_limit=25),
)
```

### Rationale

- `request_limit=25` (down from the implicit default of 50) caps the number of LLM round-trips per user turn. Each round can include one or more tool calls.
- 25 is generous enough for multi-step workflows (search, read, modify, test) but catches runaway loops earlier.
- Making the limit explicit documents the budget and prevents surprises if pydantic-ai changes its default.
- When the limit is hit, `UsageLimitExceeded` is raised, which the existing `except Exception as e` block in `chat_loop()` catches and displays.

---

## Gap 6: Sandbox Hardening

**Impact:** Medium
**Files:** `co_cli/sandbox.py`
**Related:** `docs/DESIGN-tool-shell-sandbox.md` (Future Enhancements section)

### Problem

The Docker sandbox provides basic filesystem isolation (only CWD is mounted) but lacks production-grade hardening. The container runs as root with full network access, no resource limits, and no command timeout. For an agentic assistant where the LLM chooses commands, these defaults are too permissive.

### Current Container Configuration (`sandbox.py`)

```python
self.client.containers.run(
    self.image,
    name=self.container_name,
    volumes={self.workspace_dir: {"bind": "/workspace", "mode": "rw"}},
    working_dir="/workspace",
    detach=True,
    tty=True,
    command="sh"
)
```

### Gap Analysis vs 2026 Agentic Sandbox Norms

| Dimension | Current | Target | Industry Reference |
|-----------|---------|--------|-------------------|
| **User** | Root (container default) | Non-root (`--user 1000:1000`) | E2B, Devin — all run as non-root |
| **Network** | Full access | `network_mode="none"` by default | E2B — no network; Devin — isolated VPC |
| **Resource limits** | None | `mem_limit="512m"`, `cpu_quota=50000` | All cloud sandboxes set limits |
| **Command timeout** | None | `timeout` param on `exec_run()` | Standard for any untrusted execution |
| **Mount mode** | `rw` on entire CWD | `rw` is acceptable (agent needs to write files) | Same as Devin, Claude Code |
| **Docker socket** | Not mounted | Not mounted | Correct — already aligned |

### Refactoring Plan

#### Phase 1: Non-root user + network isolation (low risk, high value)

```python
self.client.containers.run(
    self.image,
    name=self.container_name,
    volumes={self.workspace_dir: {"bind": "/workspace", "mode": "rw"}},
    working_dir="/workspace",
    user="1000:1000",         # non-root
    network_mode="none",      # no network by default
    detach=True,
    tty=True,
    command="sh"
)
```

**Considerations:**
- `user="1000:1000"` matches typical host UID on Linux/macOS. May need to be configurable for environments where CWD has different ownership.
- `network_mode="none"` breaks commands that need network (e.g., `pip install`, `curl`). Options:
  - Add a `sandbox_network` setting (`"none"` | `"bridge"`) in `config.py`
  - Or let specific commands opt in (more complex, deferred)

#### Phase 2: Resource limits (prevents runaway processes)

```python
self.client.containers.run(
    ...
    mem_limit="512m",         # OOM-kill at 512 MB
    cpu_quota=50000,          # 50% of one CPU core
    pids_limit=256,           # prevent fork bombs
)
```

**Considerations:**
- Values should be configurable via `config.py` for users with large builds.
- `mem_limit` may need to be higher for heavy workloads (e.g., compiling).

#### Phase 3: Command timeout (prevents hangs)

```python
def run_command(self, cmd: str, timeout: int = 30) -> str:
    container = self.ensure_container()
    exit_code, output = container.exec_run(
        cmd,
        workdir="/workspace",
    )
    # docker-py exec_run doesn't natively support timeout.
    # Options:
    #   a) Wrap cmd: f"timeout {timeout} {cmd}" (relies on coreutils in image)
    #   b) Use exec_create + exec_start with socket and asyncio.wait_for
    #   c) Thread-based timeout around exec_run (simplest)
```

**Recommended approach:** `timeout` shell wrapper (option a) — simplest, works with any image that has coreutils. Add `timeout` setting to `config.py` with default 30s.

```python
def run_command(self, cmd: str, timeout: int = 30) -> str:
    container = self.ensure_container()
    wrapped = f"timeout {timeout} sh -c {shlex.quote(cmd)}"
    exit_code, output = container.exec_run(wrapped, workdir="/workspace")
    decoded = output.decode("utf-8")
    if exit_code == 124:
        raise CommandError(
            f"Command timed out after {timeout}s",
            exit_code=124,
            stderr=decoded,
        )
    if exit_code != 0:
        raise CommandError(
            f"Command exited with code {exit_code}: {decoded.strip()}",
            exit_code=exit_code,
            stderr=decoded,
        )
    return decoded
```

#### New Settings (`config.py`)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `sandbox_network` | `CO_CLI_SANDBOX_NETWORK` | `"none"` | Container network mode |
| `sandbox_mem_limit` | `CO_CLI_SANDBOX_MEM` | `"512m"` | Container memory limit |
| `sandbox_timeout` | `CO_CLI_SANDBOX_TIMEOUT` | `30` | Per-command timeout in seconds |

### Implementation Order

1. **Gap 2 first** — error propagation (`CommandError` + `ModelRetry`) is prerequisite; Phase 3 timeout depends on `CommandError` existing
2. **Phase 1** — non-root + network isolation (one PR, low risk)
3. **Phase 2** — resource limits (one PR, configurable)
4. **Phase 3** — command timeout (one PR, depends on Gap 2's `CommandError`)

### Update to `DESIGN-tool-shell-sandbox.md`

After implementation, update the design doc's:
- Container Configuration table with new params
- Security Model diagram to reflect network isolation and non-root user
- Error Scenarios table with timeout handling
- Move items from Future Enhancements to implemented

---

## Out of Scope

These are tracked in separate design docs:

| Topic | Doc | Why Separate |
|-------|-----|--------------|
| CoResponse structured output migration | `docs/TODO-structured-output.md` | Large refactor touching all tools + agent result type |
| Approval flow migration to `requires_approval` | `docs/TODO-approval-flow.md` | Depends on pydantic-ai `DeferredToolRequests` API |
| Streaming tool output | `docs/TODO-streaming-tool-output.md` | Separate UX concern (chat loop rewrite) |

---

## Verification

```bash
uv run pytest tests/test_google_cloud.py -v   # includes empty-result test
uv run pytest -v                               # full suite — no regressions
```

---

## Summary

| # | Gap | File(s) | Impact |
|---|-----|---------|--------|
| 1 | Tool retry budget (`retries=3`) | `agent.py`, `config.py` | High |
| 2 | Shell error → `ModelRetry` (two-layer) | `sandbox.py`, `shell.py` | Medium |
| 3 | System prompt `display` field instruction | `agent.py` | High |
| 4 | Obsidian `display` field consistency | `obsidian.py` | Medium |
| 5 | Tool-call loop guard (`UsageLimits`) | `main.py` | Medium |
| 6 | Sandbox hardening (non-root, network, limits, timeout) | `sandbox.py`, `config.py` | Medium |
