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

## ~~Gap 1: No Retry Budget on Tools~~ (done)

Agent-level `retries=settings.tool_retries` (default 3) in `agent.py`. Configurable via `CO_CLI_TOOL_RETRIES` env var or `settings.json`. All tools inherit; side-effectful tools are safe because `confirm_or_yolo` gates each attempt.

**Files changed:** `co_cli/agent.py`, `co_cli/config.py`

---

## ~~Gap 2: Shell Error Path Swallows Failures~~ (done)

`sandbox.run_command()` now raises `RuntimeError` on non-zero exit code (was swallowing errors as strings). `shell.py` catches all exceptions and raises `ModelRetry` so the LLM can self-correct.

**Files changed:** `co_cli/sandbox.py`, `co_cli/tools/shell.py`

---

## ~~Gap 1: System Prompt `display` Field Passthrough~~ (done)

Replaced vague "Show tool output directly" with explicit `### Tool Output` section instructing the LLM to show `display` values verbatim, never reformat/drop URLs, and mention `has_more`.

**Files changed:** `co_cli/agent.py`

---

## ~~Gap 2: Obsidian `display` Field Consistency~~ (done)

Migrated `search_notes` → `dict[str, Any]` with `display`/`count`/`has_more`. Migrated `list_notes` → `dict[str, Any]` with `display`/`count`. Empty results now return `{"count": 0}` (was `ModelRetry`) per the design principle. `read_note` unchanged — raw content is appropriate.

**Files changed:** `co_cli/tools/obsidian.py`

---

## ~~Gap 3: Explicit Tool-Call Loop Guard~~ (done)

Added `UsageLimits(request_limit=settings.max_request_limit)` to `agent.run()` call. Default 25, configurable via `max_request_limit` in `settings.json` or `CO_CLI_MAX_REQUEST_LIMIT` env var. Caps LLM round-trips per user turn (down from implicit default of 50). `UsageLimitExceeded` is caught by the existing `except Exception` block. (Subsumes FIX Finding A4.)

**Files changed:** `co_cli/main.py`, `co_cli/config.py`

---

## Gap 4: Sandbox Hardening

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

1. **Phase 1** — non-root + network isolation (one PR, low risk)
2. **Phase 2** — resource limits (one PR, configurable)
3. **Phase 3** — command timeout (one PR, builds on `RuntimeError` pattern from completed Gap 2)

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

| # | Gap | File(s) | Status |
|---|-----|---------|--------|
| ~~1~~ | ~~Tool retry budget (`retries=3`)~~ | ~~`agent.py`, `config.py`~~ | **Done** |
| ~~2~~ | ~~Shell error → `ModelRetry`~~ | ~~`sandbox.py`, `shell.py`~~ | **Done** |
| ~~3~~ | ~~System prompt `display` field instruction~~ | ~~`agent.py`~~ | **Done** |
| ~~4~~ | ~~Obsidian `display` field consistency~~ | ~~`obsidian.py`~~ | **Done** |
| ~~5~~ | ~~Tool-call loop guard (`UsageLimits`)~~ | ~~`main.py`~~ | **Done** |
| 6 | Sandbox hardening (non-root, network, limits, timeout) | `sandbox.py`, `config.py` | Medium |
