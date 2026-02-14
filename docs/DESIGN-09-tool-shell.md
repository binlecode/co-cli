---
title: "09 — Shell Tool"
parent: Tools
nav_order: 1
---

# Design: Shell Tool

## 1. What & How

The shell tool executes commands as host subprocesses with approval as the explicit security boundary. No Docker, no container — approval-first (design principle #2) replaces OS-level isolation. Safe read-only commands are auto-approved; everything else requires user consent via `[y/n/a]`.

Environment sanitization (`restricted_env()`) and process-tree cleanup (`kill_process_tree()`) provide defense-in-depth for the subprocess execution path.

```
User: "list files"
       │
       ▼
┌─────────────────┐
│   Agent.run()   │
│   deps=CoDeps   │
└────────┬────────┘
         │ tool call: run_shell_command(cmd="ls -la")
         ▼
┌─────────────────────────────────────────────────────┐
│                 Approval Gate                         │
│  safe-prefix match? ──yes──▶ auto-approve            │
│                      ──no──▶ [y/n/a] prompt          │
└────────┬────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│              Subprocess Execution                     │
│  sh -c '{cmd}'                                       │
│  env: restricted_env() (allowlist, PAGER=cat)        │
│  cwd: host working directory                         │
│  timeout: asyncio.wait_for + kill_process_tree       │
└─────────────────────────────────────────────────────┘
```

## 2. Core Logic

### Shell Tool

The tool delegates to the shell backend and raises `ModelRetry` on errors so the LLM can self-correct. Confirmation is NOT a tool responsibility — it is registered with `requires_approval=True` and the chat loop handles the `[y/n/a(yolo)]` prompt via `DeferredToolRequests`.

Pseudocode:

```
run_shell_command(ctx, cmd, timeout=120):
    effective = min(timeout, ctx.deps.shell_max_timeout)
    try:
        return ctx.deps.shell.run_command(cmd, effective)
    on timeout → ModelRetry("timed out, use shorter command or increase timeout")
    on permission denied → terminal error (no retry)
    on other error → ModelRetry("command failed, try different approach")
```

### Safe-Prefix Auto-Approval

Shell commands matching a configurable safe-prefix list are auto-approved silently, skipping the `[y/n/a]` prompt. This is a UX convenience — **approval is the security boundary**.

The check runs in `_orchestrate.py` during the approval flow, before the user is prompted:

```
_is_safe_command(cmd, safe_commands):
    reject if cmd contains shell chaining operators: ; & | > < ` $( \n
    match cmd against safe_commands (longest prefix first)
    return True if prefix matches, else False
```

**Default safe commands:** `ls`, `tree`, `find`, `fd`, `cat`, `head`, `tail`, `grep`, `rg`, `ag`, `wc`, `sort`, `uniq`, `cut`, `jq`, `echo`, `printf`, `pwd`, `whoami`, `hostname`, `uname`, `date`, `env`, `which`, `file`, `id`, `du`, `df`, `git status`, `git diff`, `git log`, `git show`, `git branch`, `git tag`, `git blame`.

Multi-word prefixes (e.g. `git status`) are matched before single-word ones to prevent `git` from matching `git push`.

### Shell Backend

Single backend — subprocess with environment sanitization:

```
ShellBackend:
    workspace_dir: str (defaults to cwd)

    run_command(cmd, timeout=120):
        spawn sh -c cmd as subprocess
            cwd = workspace_dir
            env = restricted_env()
            start_new_session = True  (enables process group kill)
            stdout + stderr merged

        wait with asyncio.wait_for(timeout)
        on timeout → kill_process_tree(proc), capture partial output, raise RuntimeError
        on non-zero exit → raise RuntimeError with exit code + output
        return decoded stdout

    cleanup():
        no-op (no persistent resources)
```

### Environment Sanitization

`restricted_env()` builds the subprocess environment from an **allowlist** (not blocklist) to prevent pager/editor hijacking (CVE-2025-66032 vectors) and shared-library injection:

**Allowed vars:** `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`, `TERM`, `SHELL`, `TMPDIR`, `XDG_RUNTIME_DIR`

**Forced overrides:**
- `PYTHONUNBUFFERED=1` — ensures partial output is captured on timeout
- `PAGER=cat` — blocks arbitrary code execution via `PAGER`/`GIT_PAGER`

Everything else (`LD_PRELOAD`, `MANPAGER`, `EDITOR`, etc.) is stripped.

### Process Cleanup

`kill_process_tree()` terminates runaway processes on timeout:

```
kill_process_tree(proc):
    if already exited → return
    SIGTERM to process group (os.killpg)
    wait 200ms
    if still alive → SIGKILL to process group
```

Uses `start_new_session=True` on the subprocess so `os.killpg()` can kill the entire tree (matches Gemini CLI's `killProcessGroup` pattern).

### Security Model

```
┌─────────────────────────────────────────────────────────────────┐
│                        SECURITY LAYERS                            │
│                                                                   │
│  Layer 1: Approval gate                                          │
│    Safe-prefix → auto-approve silently                           │
│    Everything else → [y/n/a] prompt (user decides)               │
│                                                                   │
│  Layer 2: Environment sanitization                               │
│    Allowlist-only env vars (no LD_PRELOAD, PAGER forced to cat)  │
│                                                                   │
│  Layer 3: Process isolation                                      │
│    start_new_session=True (own process group)                    │
│    kill_process_tree on timeout (SIGTERM → SIGKILL)              │
│                                                                   │
│  Layer 4: Timeout enforcement                                    │
│    LLM-controlled timeout capped by shell_max_timeout            │
│    asyncio.wait_for + kill_process_tree as safety net            │
└─────────────────────────────────────────────────────────────────┘
```

**What is NOT protected:** The subprocess runs as the user, in the user's working directory, with read-write access to local files. This is a deliberate tradeoff — co is a single-user CLI companion, not a CI pipeline. Approval is the security boundary (design principle #2). See `TODO-drop-docker-sandbox.md` for the rationale.

### Timeout Control

LLM-controlled, two layers:

| Layer | Controls | Default |
|-------|----------|---------|
| Tool parameter (`timeout`) | LLM chooses per call, visible in tool schema | 120s |
| Hard ceiling (`shell_max_timeout`) | Settings-level cap, LLM cannot exceed | 600s |

The effective timeout is `min(timeout, shell_max_timeout)`. The system prompt instructs the LLM to set appropriate timeouts for long-running operations.

**stdout/stderr:** Merged (`2>&1`) — the LLM doesn't need to distinguish. `PYTHONUNBUFFERED=1` ensures partial output is captured on timeout.

### Output Control

Shell output reaches the user through two paths:

| Path | Trigger | Approval | LLM Involved |
|------|---------|----------|--------------|
| Agent-mediated | Natural language prompt | `[y/n/a]` via `DeferredToolRequests` | Yes |
| Direct (`!`) | `!cmd` prefix in REPL | None (user typed it) | No |

Both display raw output in a Rich `Panel` with `border_style="shell"`.

### Error Scenarios

| Scenario | Detection | Handling |
|----------|-----------|----------|
| Command fails | Non-zero exit code | `RuntimeError` with exit code + output → `ModelRetry` |
| Command timeout | `asyncio.TimeoutError` | `kill_process_tree`, `RuntimeError` with partial output → `ModelRetry` |
| Permission denied | "permission denied" in error | Terminal error (no retry — likely path issue) |

<details>
<summary>Cross-system research (Feb 2026)</summary>

**Approval-based systems (no OS sandbox):**

| System | Command analysis | Pattern learning |
|--------|-----------------|-----------------|
| **Aider** | None — `confirm_ask()` for everything | None |
| **Claude Code** | Hook-based (`PreToolUse`) | User-configured allow/deny rules |
| **OpenCode** | Tree-sitter bash AST | Arity-based: 137 prefixes |
| **Gemini CLI** | Tree-sitter bash AST | Root command prefix saved |
| **Co CLI** | Prefix match + chaining rejection | None (Phase J: tree-sitter AST) |

**OS-level sandbox systems:**

| System | Sandbox | FS isolation | Network | Env sanitization |
|--------|---------|-------------|---------|-----------------|
| **Codex CLI** | bwrap (Linux), Seatbelt (macOS) | Read-only root + writable workspace | Seccomp denies socket/connect | Clean sandbox env |
| **Gemini CLI** | Docker/Podman, Seatbelt (macOS) | Container with RW workspace | Container network isolation | `sanitizeEnvironment()` |

</details>

## 3. Config

| Setting | Env Var | Default | Description |
|---------|--------|---------|-------------|
| `shell_safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` | `["ls", "cat", ...]` | Auto-approved prefixes (comma-separated in env) |
| `shell_max_timeout` | `CO_CLI_SHELL_MAX_TIMEOUT` | `600` | Hard ceiling for per-command timeout (seconds) |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/shell.py` | Tool function — delegates to shell backend, `ModelRetry` on error |
| `co_cli/shell_backend.py` | `ShellBackend` — subprocess execution with `restricted_env()` |
| `co_cli/_approval.py` | `_is_safe_command()` — safe-prefix classification for auto-approval |
| `co_cli/_shell_env.py` | `restricted_env()` and `kill_process_tree()` |
| `co_cli/deps.py` | `CoDeps` — holds `shell` instance and `shell_max_timeout` |
| `co_cli/config.py` | Shell settings (`shell_safe_commands`, `shell_max_timeout`) |
