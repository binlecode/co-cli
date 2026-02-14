# TODO: Drop Docker Sandbox — Subprocess + Approval Model

**Goal**: Remove the Docker sandbox and promote the existing subprocess backend as the sole shell execution model. Approval becomes the explicit, first-class security boundary — aligned with co's design principle #2 ("approval-first").

**Problem**: The Docker sandbox is over-engineered for co's vision. Co is a knowledge work companion, not a CI pipeline. Shell is 1 of 16+ tools. Docker imposes a hard runtime dependency, container startup latency, a custom image build step, container lifecycle management, UID mapping issues, and 8 sandbox-specific config settings — all to solve a problem that approval-first already solves.

**Evidence**: Aider (35k+ users, zero sandbox, `confirm_ask()` for everything) and Claude Code (most widely used AI coding tool, no OS sandbox, hook-based permissions) prove that approval-gated shell execution is sufficient for single-user CLI tools. No security incidents from this model.

**Non-goals**: Tree-sitter AST command analysis (Phase J, future). Sandbox-as-optional-plugin for power users (not planned).

---

## Alignment Case

| Principle | Docker model | Subprocess + Approval model |
|-----------|-------------|----------------------------|
| **Approval-first** | Redundant — Docker is a parallel security layer alongside approval | Approval IS the security boundary, single model |
| **Local-first** | Requires Docker daemon running | Just the shell, zero external dependencies |
| **Incremental delivery / MVP** | Over-engineered for day 1 | Smallest thing that works |
| **Single loop, no feature islands** | Sandbox is a parallel concern with its own config/lifecycle | Approval is already in the loop |

Co's own codebase acknowledges this. `_approval.py:7`:

> "This is a UX convenience, not a security boundary — the Docker sandbox provides isolation."

After this change, the comment inverts: safe-command classification is a UX convenience, and **approval is the security boundary**. One model, not two.

---

## What Gets Removed

| Item | File(s) | Lines |
|------|---------|-------|
| `DockerSandbox` class | `co_cli/sandbox.py` | ~110 lines |
| `SandboxProtocol` abstraction | `co_cli/sandbox.py` | ~10 lines (one backend doesn't need a protocol) |
| `Sandbox` backward-compat alias | `co_cli/sandbox.py` | 1 line |
| `Dockerfile.sandbox` | root | 17 lines |
| `_create_sandbox()` factory | `co_cli/main.py` | ~20 lines |
| Docker config fields | `co_cli/config.py` | `docker_image`, `sandbox_backend`, `sandbox_network`, `sandbox_mem_limit`, `sandbox_cpus` (5 fields + validator + env mappings) |
| Docker env vars | `co_cli/config.py` | `CO_CLI_DOCKER_IMAGE`, `CO_CLI_SANDBOX_BACKEND`, `CO_CLI_SANDBOX_NETWORK`, `CO_CLI_SANDBOX_MEM_LIMIT`, `CO_CLI_SANDBOX_CPUS` |
| `docker` dependency | `pyproject.toml` | Runtime dependency removal |
| Docker-specific tests | `tests/test_sandbox.py`, Docker sections of `tests/test_shell.py`, `tests/test_commands.py` (`Sandbox` imports) | ~400 lines of Docker-dependent tests |
| Docker container lifecycle | `co_cli/sandbox.py` | `ensure_container()`, `cleanup()` stop/remove logic |

---

## What Stays and Gets Stronger

| Component | Current state | After |
|-----------|--------------|-------|
| `SubprocessBackend` | Fallback, second-class | Primary and only backend — renamed or inlined |
| `restricted_env()` | Used only in fallback path | THE env security layer for all shell execution |
| `kill_process_tree()` | Used only in fallback path | THE process cleanup for all shell execution |
| `_is_safe_command()` | Active only when Docker present (`isolation_level != "none"`) | Active always — auto-approves read-only commands universally |
| Approval gate | Skipped for safe commands in Docker; required for all in subprocess | Required for all non-safe commands, always |
| `sandbox_max_timeout` | Capped by Docker + asyncio | Capped by asyncio `wait_for` + `kill_process_tree` |

---

## Config After

**Removed** (5 settings, 5 env vars, 1 validator):

- `docker_image` / `CO_CLI_DOCKER_IMAGE`
- `sandbox_backend` / `CO_CLI_SANDBOX_BACKEND`
- `sandbox_network` / `CO_CLI_SANDBOX_NETWORK`
- `sandbox_mem_limit` / `CO_CLI_SANDBOX_MEM_LIMIT`
- `sandbox_cpus` / `CO_CLI_SANDBOX_CPUS`
- `_validate_mem_limit` validator

**Kept** (2 settings, 2 env vars):

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `shell_safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` | `["ls", "cat", ...]` | Auto-approved prefixes |
| `shell_max_timeout` | `CO_CLI_SHELL_MAX_TIMEOUT` | `600` | Hard ceiling for per-command timeout (seconds) |

Note: `sandbox_max_timeout` / `CO_CLI_SANDBOX_MAX_TIMEOUT` renamed to `shell_max_timeout` / `CO_CLI_SHELL_MAX_TIMEOUT` — no sandbox concept anymore.

---

## Code Changes

### 1. `co_cli/sandbox.py` → `co_cli/shell_backend.py`

Rename file. Remove `SandboxProtocol`, `DockerSandbox`, `Sandbox` alias. Promote `SubprocessBackend` to a module-level function or simple class:

```
async def run_shell(cmd: str, timeout: int = 120, workspace: str | None = None) -> str
    Run cmd as subprocess with restricted_env(), kill on timeout.
    Raise RuntimeError on non-zero exit or timeout.
```

Or keep as a class if statefulness (workspace_dir) is useful:

```
class ShellBackend:
    isolation_level = "none"  # Honest about what it is
    workspace_dir: str
    async run_command(cmd, timeout) -> str
    cleanup() -> None  # no-op
```

### 2. `co_cli/_sandbox_env.py` → `co_cli/_shell_env.py`

Rename. Contents unchanged — `restricted_env()` and `kill_process_tree()` are already correct.

### 3. `co_cli/deps.py`

```
- sandbox: SandboxProtocol
+ shell: ShellBackend
- sandbox_max_timeout: int = 600
+ shell_max_timeout: int = 600
```

### 4. `co_cli/tools/shell.py`

- Update docstring: remove "sandboxed Docker container" → "subprocess with approval"
- `ctx.deps.sandbox` → `ctx.deps.shell`
- `ctx.deps.sandbox_max_timeout` → `ctx.deps.shell_max_timeout`

### 5. `co_cli/_approval.py`

- Update docstring: "UX convenience, not a security boundary — the Docker sandbox provides isolation" → "UX convenience — approval is the security boundary"

### 6. `co_cli/_orchestrate.py`

- Remove `isolation_level` gate at the approval flow (line ~371): `deps.sandbox.isolation_level != "none"` check removed — safe-command auto-approval applies always, regardless of backend
- `deps.sandbox` → `deps.shell` reference updates

### 7. `co_cli/main.py`

- Remove `_create_sandbox()` factory
- Remove Docker import, Docker ping, Docker fallback warning
- `create_deps()`: `shell=ShellBackend()` directly
- Remove `deps.sandbox.cleanup()` from shutdown (cleanup is a no-op)

### 8. `co_cli/config.py`

- Remove 5 Docker config fields + `_validate_mem_limit` validator
- Remove 5 Docker env var mappings from `fill_from_env`
- Rename `sandbox_max_timeout` / `CO_CLI_SANDBOX_MAX_TIMEOUT` → `shell_max_timeout` / `CO_CLI_SHELL_MAX_TIMEOUT`
- Update `_DEFAULT_SAFE_COMMANDS` comment: "approval is the security boundary" (not Docker)

### 9. `co_cli/status.py`

- Remove Docker detection logic (`docker info` probe, sandbox status string)
- Simplify sandbox status to always report subprocess/approval model
- Update `StatusInfo.sandbox` field semantics

### 10. `Dockerfile.sandbox`

Delete.

### 11. `pyproject.toml`

- Remove `docker` from dependencies
- Update project description: "sandboxed shell" → "approval-gated shell"

### 12. Tests

- `tests/test_sandbox.py` → delete (Docker-only tests)
- `tests/test_shell.py` → remove all `DockerSandbox` test functions (~25 tests), keep `SubprocessBackend` tests, rename references
- `tests/test_shell.py` → remove `test_docker_sandbox_satisfies_protocol`, `test_docker_sandbox_isolation_level`
- `tests/test_commands.py` → replace `Sandbox(container_name=...)` imports with `ShellBackend()`, remove `deps.sandbox.cleanup()` calls
- `tests/test_orchestrate.py` → update `from co_cli.sandbox import SubprocessBackend` path
- `tests/test_history.py` → update `from co_cli.sandbox import SubprocessBackend` path
- Add test: safe-command auto-approval works without Docker (was gated on `isolation_level != "none"`)

### 13. Docs

- `docs/DESIGN-09-tool-shell.md` → full rewrite: remove Docker architecture, sandbox protocol, container hardening table. New focus: subprocess + approval model, `restricted_env()`, safe-command classification, timeout control
- `docs/DESIGN-00-co-cli.md` → update component index: remove Docker/sandbox references, update architecture diagram, file table, dependency table
- `CLAUDE.md` → update: remove "Docker must be running for shell/sandbox tests", update shell tool description, update DESIGN-09 description

---

## Impact on Roadmap Phases

| Phase | Impact |
|-------|--------|
| **A** (Agentic loop safety) | No change — loop detection, turn limits are orthogonal |
| **C** (Shell security hardening) | **Simplified** — no longer maintaining two parallel security models. Focus narrows to: (1) unify `!cmd` with approval, (2) tighten safe-command classification, (3) one backend, no fallback policy needed |
| **E** (Background execution) | **Simplified** — no Docker container lifecycle to manage for background tasks. `start_new_session=True` + `kill_process_tree()` handles process isolation |
| **J** (Shell policy engine) | Unchanged — tree-sitter AST analysis applies to subprocess commands the same way |

---

## Migration Checklist

1. Rename `sandbox.py` → `shell_backend.py`, remove Docker classes
2. Rename `_sandbox_env.py` → `_shell_env.py`
3. Update `deps.py`: `sandbox` → `shell`, `sandbox_max_timeout` → `shell_max_timeout`
4. Update `tools/shell.py`: deps references + docstring
5. Update `_approval.py`: update docstring (approval is the security boundary)
6. Update `_orchestrate.py`: remove `isolation_level` gate, update `deps.sandbox` → `deps.shell`
7. Update `main.py`: remove `_create_sandbox()`, direct `ShellBackend()` construction
8. Update `config.py`: remove Docker fields, rename timeout field + env var
9. Update `status.py`: remove Docker detection, simplify sandbox status
10. Delete `Dockerfile.sandbox`
11. Remove `docker` from `pyproject.toml`, update project description
12. Update tests: remove Docker tests, fix imports across all test files (`test_sandbox.py`, `test_shell.py`, `test_commands.py`, `test_orchestrate.py`, `test_history.py`)
13. Rewrite `DESIGN-09-tool-shell.md`
14. Update `DESIGN-00-co-cli.md`: component index, architecture diagram, dependency table
15. Update `CLAUDE.md`: shell tool description, testing requirements, DESIGN-09 reference
16. Run full test suite: `uv run pytest -v`
