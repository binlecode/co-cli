# RESEARCH: Tool Lifecycle Gaps — `co-cli` vs Hermes

Code-verified cross-review of co-cli's tool lifecycle against hermes-agent's design.
Focus: gaps (things co-cli lacks) and anti-patterns (things co-cli does that cause subtle problems).

## Sources

### `co-cli`

- [`co_cli/agent/_native_toolset.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_native_toolset.py)
- [`co_cli/agent/_mcp.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_mcp.py)
- [`co_cli/agent/_core.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_core.py)
- [`co_cli/context/_tool_lifecycle.py`](/Users/binle/workspace_genai/co-cli/co_cli/context/_tool_lifecycle.py)
- [`co_cli/context/tool_approvals.py`](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [`co_cli/context/orchestrate.py`](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [`co_cli/tools/agents.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/agents.py)
- [`co_cli/tools/tool_io.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_io.py)
- [`co_cli/tools/user_input.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/user_input.py)
- [`co_cli/tools/background.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/background.py)
- [`co_cli/deps.py`](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [`docs/specs/tools.md`](/Users/binle/workspace_genai/co-cli/docs/specs/tools.md)

### Hermes

- `/Users/binle/workspace_genai/hermes-agent/tools/registry.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/approval.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/mcp_tool.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/delegate_tool.py`
- `/Users/binle/workspace_genai/hermes-agent/model_tools.py`
- `/Users/binle/workspace_genai/hermes-agent/run_agent.py`

## Methodology

co-cli uses pydantic-ai's `FunctionToolset` with decorator-based registration, structured
`RunContext[CoDeps]` injection, a two-tier visibility system (ALWAYS/DEFERRED), and a
deferred-approval loop that narrows the active toolset on resume. Hermes uses a thread-safe
singleton `ToolRegistry`, AST-based auto-discovery, a `**kw` injection convention, and a
blocking gateway pattern for approval. Both are production-grade but solve the same problems
with different tradeoffs.

---

## Gaps (things co-cli lacks)

### 1. No runtime tool availability check (`check_fn` pattern)

Hermes registers a `check_fn` per tool (`registry.py:176`) evaluated before each invocation
to determine if the tool is currently usable (API key present, service reachable, sandbox
available, etc.). co-cli's `requires_config` (`_native_toolset.py:137`) is a build-time gate
only — if `google_credentials_path` is set at startup, all Google tools are registered for
the session. If credentials expire mid-session, the tools remain in the model's schema and
appear callable but fail at the network layer with a `tool_error()` or `ModelRetry`. The
model cannot distinguish "always broken" from "transient failure."

**Impact:** Stale-credential errors show up as retryable tool failures rather than a clear
"this tool is unavailable" signal. The model may spin on Google tools after an auth expiry.

### 2. No MCP dynamic tool refresh

`discover_mcp_tools()` (`_mcp.py:75`) runs once at bootstrap and populates the tool index.
There is no handling for MCP protocol's `notifications/tools/list_changed` notification.
If an MCP server adds, removes, or renames tools during a session, co-cli's index and the
model's context become stale. Hermes supports deregister/re-register on server refresh
(`mcp_tool.py:2362`).

**Impact:** Tools added to a long-running MCP server after co-cli starts are invisible for
the session. Removed tools remain callable and fail at execution.

### 3. `NATIVE_TOOLS` tuple is a closed, manually-maintained list

Every native tool requires two edits: define it in its own module, then add it to the tuple
in `_native_toolset.py:42-93`. Forgetting the second step silently omits the tool — no
registration failure, no warning, the tool just doesn't exist. Hermes uses AST-based
discovery (`registry.py:56-73`) that scans `tools/*.py` for `registry.register()` calls,
so a tool in a new file is auto-discovered at startup with no secondary step.

**Impact:** Authoring discipline gap. The `@agent_tool` decorator on an unregistered function
is a no-op — the tool exists as code but is never reachable.

### 4. Background task output is in-memory only with lossy ring-buffer truncation

`BackgroundTaskState.output_lines` is `deque(maxlen=500)` (`background.py:23`). For
long-running commands producing more than 500 lines, oldest output is silently dropped with
no on-disk fallback. If the session crashes (OOM, SIGKILL, exception in the event loop),
all background task output is lost. The `task_status()` tool's `tail_lines` parameter works
correctly but only within the surviving in-memory buffer.

**Impact:** Silent data loss for commands like builds or test suites that emit thousands of
lines. No post-crash recovery of task output.

### 5. No toolset composition for delegation

Delegation agents (`web_research`, `knowledge_analyze`, `reason` in `agents.py`) receive
an explicit list of tool functions at agent-build time. There is no named toolset concept
for expressing "give the researcher the `web` group." Hermes's `toolsets.py` + `resolve_toolset()`
expresses access control at group level; co-cli's delegation is manual per-agent tool
enumeration, which drifts when new tools are added to the codebase.

**Impact:** A new web tool added to co-cli is not automatically available to `web_research`
— it must also be threaded into the delegation agent's explicit tool list.

### 6. Approval session rules have no persistence layer

`session_approval_rules` lives in `CoSessionState` and is cleared at session end by design
(security boundary). Hermes has a `_permanent_approved` set that can be persisted to config,
giving users a "remember this forever" path beyond the session's `a` keypress. co-cli's
approval UX offers only yes/no/remember-for-session — no permanent allowlist that survives
session end.

**Impact:** Power users must re-approve the same safe shell commands (e.g. `git`, `make test`)
in every session. This is a deliberate tradeoff, not an oversight, but the friction is real.

---

## Anti-Patterns (things co-cli does that cause subtle problems)

### 1. `clarify` one-shot injection is fragile under model confusion

`clarify` raises `QuestionRequired` unconditionally on any call where
`ctx.tool_call_approved` is false (`user_input.py:51-52`). The user's answer is injected
via `ToolApproved(override_args={"user_answer": answer})` in the approval loop. If the
model calls `clarify` twice in the same reasoning step (duplicate parallel calls or model
confusion), only the first call's `ToolApproved` is paired correctly; the second call fails
with `"No answer was received from the user."` The docstring warns "CRITICAL — one call
only" but this is prompt discipline, not a framework-level guard.

Hermes's blocking gateway pattern handles the answer channel independently of tool call
identity, making it robust to model misbehavior at this level.

### 2. `tool_output_raw()` silently bypasses size gate and telemetry

`tool_output_raw()` (`tool_io.py:174`) exists for ctx-less helpers but silently omits:
(a) oversized result persistence — a large result returned via `tool_output_raw()` goes to
the model in full regardless of the per-tool `max_result_size` threshold; (b) OTel span
enrichment from `CoToolLifecycle.after_tool_execute`; (c) telemetry metadata. If a tool
author accidentally uses `tool_output_raw()` where they have a `ctx`, the oversight is
silent — the tool appears to work, oversized output floods the context window, and there's
no metric capturing it.

### 3. `ModelRetry` vs. `tool_error()` classification is convention with no framework guardrail

The distinction between transient failures (`raise ModelRetry(...)`) and terminal failures
(`return tool_error(...)`) is critical: `ModelRetry` consumes a retry budget, `tool_error`
stops immediately and lets the model pick a different path. Classification is entirely
tool-author responsibility with no static enforcement or linting signal.
`handle_google_api_error()` (`tool_io.py:233`) shows the right pattern, but it's a
convention. A tool that raises `ModelRetry` for a 401 (auth failure — always terminal) will
exhaust the retry budget spinning on a non-recoverable error.

### 4. `file_read_mtimes` grows unbounded with no per-turn cleanup

`CoDeps.file_read_mtimes: dict[str, float]` accumulates one entry per unique file read,
shared across parent and forked child agents via `fork_deps()` (`deps.py:262`). There is
no eviction at turn boundary or session cleanup. In a long coding session reading hundreds
of files, the dict grows monotonically. Both parent and child carry the full accumulated
history.

This is intentional for cross-agent staleness detection, but the accumulation is unbounded.
A session-level cap or turn-boundary eviction of entries not modified in the current turn
would address this without breaking the staleness invariant.

### 5. `CoToolLifecycle.before_tool_execute` path normalization is a hidden arg rewrite

Path normalization in `_tool_lifecycle.py` rewrites `path` args from relative to absolute
before the tool function body executes. A tool author testing their function directly
(without the lifecycle capability registered) gets different `path` values than in production:
relative paths stay relative in tests, always-absolute in production. This implicit
transformation is a hidden contract. Tool implementations appear to accept relative paths,
but they never actually receive them in a running system.

### 6. MCP server `list_tools()` at startup has no per-server timeout

`discover_mcp_tools()` calls `entry.server.list_tools()` (`_mcp.py:93`) with no explicit
`asyncio.timeout()` wrapper. If an MCP server hangs (process stuck, SSE connection stalled),
startup blocks until the OS-level socket timeout fires. Additionally, entries are iterated
sequentially — a stalled server blocks discovery of all subsequent servers in the list.

**Fix:** Wrap each `list_tools()` call in `asyncio.timeout(cfg.timeout)` and run discovery
in parallel with `asyncio.gather(..., return_exceptions=True)`.

---

## Priority Ordering

| Priority | Item | Risk | Effort |
|----------|------|------|--------|
| High | MCP `list_tools()` has no per-server timeout | Startup hangs | Low — `asyncio.timeout()` per call + `gather` |
| High | `tool_output_raw()` bypasses size gate | Silent context overflow | Medium — audit callsites, restrict to ctx-less helpers only |
| Medium | `ModelRetry` / `tool_error` unenforced | Retry budget waste on unrecoverable errors | Medium — ruff rule or base class signal |
| Medium | No `check_fn` runtime availability | Tools callable after credential expiry | Medium — hook into visibility filter |
| Medium | `NATIVE_TOOLS` is a manual list | Tool silently omitted on authoring slip | Low — decorator-level validation catches it at startup |
| Low | `file_read_mtimes` unbounded growth | Memory accumulation in very long sessions | Low — cap dict size or evict on turn reset |
| Low | Clarify one-shot fragility | Model confusion on duplicate calls | Hard — requires dedup at approval loop |
| Low | No permanent approval persistence | UX friction for power users | Medium — blocked by security tradeoff |
| Low | Background task ring-buffer is lossy | Output loss for long-running commands | Medium — optional file sink on spawn |
