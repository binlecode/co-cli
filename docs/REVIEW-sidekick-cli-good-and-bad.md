# Review: sidekick-cli Patterns Analysis (Updated for Current co-cli)

**sidekick source:** `/Users/binle/workspace_genai/sidekick-cli` (`82c6958`)
**co-cli source:** `/Users/binle/workspace_genai/co-cli` (`7a4b753`)
**Updated:** 2026-02-07

---

## Executive Summary

The original sidekick findings were directionally correct, but co-cli has already implemented many of the recommended patterns. The main remaining gaps are streaming-first run orchestration (`iter`/`run_stream`) and MCP integration.

| Category | Adopted in co-cli | Partial | Pending |
|---|---|---|---|
| Architecture | 5 | 2 | 1 |
| Tool Design | 4 | 1 | 1 |
| Error/Recovery | 4 | 0 | 0 |
| Security Model | 3 | 1 | 0 |

---

## Pattern-by-Pattern Status

### 1. Dependency injection via deps object

**sidekick pattern:** `ToolDeps` callbacks in `src/sidekick/deps.py`.

**co-cli status:** **Implemented**.

- `CoDeps` is the runtime dependency container.
- Agent is typed with `deps_type=CoDeps`.

Refs: `co_cli/deps.py`, `co_cli/agent.py:93`.

### 2. Fine-grained run orchestration (`agent.iter`)

**sidekick pattern:** node-by-node processing with `agent.iter()`.

**co-cli status:** **Pending**.

- co-cli currently uses `agent.run(...)` with deferred approvals.
- It does not yet process graph nodes via `iter()`.

Refs: `co_cli/main.py:297`, `co_cli/main.py:304`.

### 3. Recoverable tool failures with `ModelRetry`

**sidekick pattern:** actionable retries in tools.

**co-cli status:** **Implemented**.

- Tool modules consistently raise `ModelRetry` for user-correctable issues.

Refs: `co_cli/tools/shell.py`, `co_cli/tools/google_gmail.py`, `co_cli/tools/slack.py`.

### 4. Message-history patching after interrupts/errors

**sidekick pattern:** synthesize missing tool-return messages.

**co-cli status:** **Implemented**.

- `_patch_dangling_tool_calls()` repairs history after interruption.

Refs: `co_cli/main.py:118`, `co_cli/main.py:320`.

### 5. Signal handling and cancellation hygiene

**sidekick pattern:** explicit SIGINT strategy around async tasks.

**co-cli status:** **Implemented**.

- co-cli handles interrupt cancellation and double-ctrl-c exit behavior.
- Approval prompt temporarily restores default SIGINT handler.

Refs: `co_cli/main.py:155`, `co_cli/main.py:313`, `co_cli/main.py:325`.

### 6. Explicit tool registration and approval boundaries

**sidekick pattern discussed:** avoid implicit import-time registration.

**co-cli status:** **Implemented**.

- Tools are explicitly registered with `agent.tool(...)`.
- Side-effect tools use `requires_approval=True`.

Refs: `co_cli/agent.py:102`, `co_cli/agent.py:107`.

### 7. MCP integration pathway

**sidekick pattern:** custom MCP server integration callbacks.

**co-cli status:** **Pending**.

- No MCP subsystem in current co-cli runtime.

Refs: `co_cli/agent.py`, `co_cli/main.py`.

---

## Anti-Patterns Re-check

### 1. Global mutable runtime singletons

**sidekick issue:** global `session` and `usage_tracker`.

**co-cli status:** **Mostly avoided**.

- Runtime state is carried through `CoDeps` and local loop variables.
- `settings` is global config, but not a mutable per-request session singleton.

Refs: `co_cli/deps.py`, `co_cli/main.py`, `co_cli/config.py`.

### 2. Unsandboxed host command execution

**sidekick issue:** direct host `subprocess.run(shell=True)`.

**co-cli status:** **Partial improvement**.

- Docker isolation exists and is preferred.
- In `sandbox_backend=auto`, fallback can run in `SubprocessBackend` (`isolation_level="none"`).
- co-cli compensates by forcing approval for shell commands in no-isolation mode.

Refs: `co_cli/sandbox.py`, `co_cli/main.py:68`, `co_cli/main.py:174`.

### 3. Manual config validation

**sidekick issue:** manual JSON validation functions.

**co-cli status:** **Implemented better approach**.

- Pydantic model with validators and env overlay.

Refs: `co_cli/config.py:41`, `co_cli/config.py:85`.

### 4. Agent/UI concern mixing

**sidekick issue:** confirmation/UI callbacks inside agent module.

**co-cli status:** **Partial**.

- Tool logic is mostly decoupled.
- `main.py` still carries orchestration + approval UI + output rendering in one module.

Refs: `co_cli/main.py`.

---

## Updated Structural Comparison

| Aspect | sidekick-cli | co-cli (current) |
|---|---|---|
| Config | Manual structure checks | Pydantic settings + env + project override |
| Runtime state | Global session/usage singletons | `CoDeps` injection + local session flow |
| Shell isolation | Host subprocess (`shell=True`) | Docker sandbox + subprocess fallback mode |
| Approval flow | Callback-driven | Deferred tool approvals + safe-prefix shortcut |
| Tool registration | Wrapped import list | Explicit `agent.tool(...)` registrations |
| Interrupt recovery | Message patching | Message patching + prompt-safe SIGINT handling |
| Telemetry | In-memory usage tracker | OpenTelemetry -> SQLite exporter |
| MCP | Implemented custom wrapper | Not yet integrated |

---

## Priority Next Steps (from this review)

1. Implement `run_stream` or `agent.iter` orchestration for better streaming/tool-event control.
2. Decide policy for `sandbox_backend=subprocess` in production-like use (keep, tighten, or disable by default).
3. Split `co_cli/main.py` into smaller orchestration modules (approval UI, run loop, rendering).
4. Add MCP support only after web + memory + file-tool MVPs stabilize.

