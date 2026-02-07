# Design: Shell Tool & Sandbox

**Status:** Implemented. Safe-prefix auto-approval, no-sandbox fallback MVP complete. Post-MVP enhancements in `docs/TODO-shell-safety.md`.
**Last Updated:** 2026-02-07

## Overview

The shell tool executes user commands in an isolated execution environment, protecting the host system from potentially destructive operations while maintaining access to the user's working directory.

The sandbox uses a protocol-based backend architecture — Docker provides full isolation (MVP), with a subprocess fallback for environments without Docker. The tool and its callers are backend-agnostic: they interact through a shared protocol (`run_command` + `cleanup`). Post-MVP: macOS Seatbelt jail can be added behind the protocol with zero caller changes.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Co CLI                                   │
│                                                                  │
│  User: "list files"                                             │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────┐                                            │
│  │   Agent.run()   │                                            │
│  │   deps=CoDeps   │                                            │
│  └────────┬────────┘                                            │
│           │ tool call: run_shell_command(cmd="ls -la")          │
│           ▼                                                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              run_shell_command()                         │    │
│  │  1. Deferred approval (chat loop, not tool)             │    │
│  │  2. Delegate to sandbox                                  │    │
│  └────────┬────────────────────────────────────────────────┘    │
└───────────┼──────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Docker Container                            │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  /workspace  ◀──── bind mount ────▶  Host $(pwd)        │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Shell Tool Design

### Responsibilities

| Responsibility | Description |
|----------------|-------------|
| **Delegation** | Passes command to sandbox, returns output |
| **Error → ModelRetry** | Catches exceptions, raises `ModelRetry` so LLM can self-correct |

**Note:** Confirmation is NOT a tool responsibility. The tool is registered with `requires_approval=True` — the chat loop handles the `[y/n/a(yolo)]` prompt via `DeferredToolRequests`. See `DESIGN-co-cli.md` §8.2.

### Processing Flow

```
LLM calls run_shell_command(cmd)
              │
              ▼
┌─────────────────────────────────┐
│ requires_approval=True          │
│   → Agent defers (not tool)     │
│   → Chat loop prompts [y/n/a]  │
│   → Approved: agent resumes    │
│   → Denied: ToolDenied to LLM  │
└─────────────────────────────────┘
              │ (approved)
              ▼
┌─────────────────────────────────┐
│ sandbox.run_command(cmd)        │
│   ├── Success ──▶ Return output │
│   └── Error   ──▶ RuntimeError  │
└─────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│ Exception?                      │
│   ├── No  ──▶ Return to LLM    │
│   └── Yes ──▶ raise ModelRetry  │
│        (LLM can self-correct)   │
└─────────────────────────────────┘
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Access sandbox via `ctx.deps` | No global state, testable, follows pydantic-ai pattern |
| `requires_approval=True` | Approval lives in chat loop, not in tool — separation of concerns |
| `ModelRetry` on errors | LLM sees the error message and can self-correct (e.g., fix a typo). Consistent with Google/Obsidian tools |
| Detailed docstring | Helps LLM understand when to use this tool |

---

## Safe-Prefix Auto-Approval

### Problem

Every shell command — even harmless read-only ones — requires the user to type `y`. `ls`, `cat`, `pwd`, `whoami` can't damage anything, yet they get the same approval gate as `rm -rf /workspace`.

The sandbox already provides isolation (non-root, no network, capped resources, `cap_drop=ALL`). The approval prompt is a second layer — useful for destructive commands, but pure friction for read-only ones.

### Industry Research (Feb 2026)

| Tool | Built-in safe list | User allowlist | User denylist | Parsing depth | Decision layer |
|------|-------------------|----------------|---------------|---------------|----------------|
| **Codex CLI** | Yes — hardcoded, flag-aware | Prefix rules in TOML | `requirements.toml` | Deep (tokenize, inspect flags, shell wrappers) | Dedicated middleware (`exec_policy.rs`) |
| **Claude Code** | Removed post-CVE-2025-66032 | `settings.json` allow rules | `settings.json` deny rules | User hooks (arbitrary) | Hook middleware (`PreToolUse` event) |
| **Gemini CLI** | No | `tools.allowed` in settings | No | Prefix string match | Tool executor middleware |
| **Windsurf** | No | `cascadeCommandsAllowList` | `cascadeCommandsDenyList` | Prefix match | Cascade orchestrator |
| **Aider** | N/A | No | No | None | Chat loop (`io.confirm_ask()`) |

**Key findings:**

1. **Approval lives in middleware, not inside tools.** Every mature system (Codex, Claude Code, Gemini, Windsurf) places the decision between the agent loop and tool execution. Tools stay as pure executors. Aider is the exception (simplest model — all commands require explicit `y`).

2. **Codex CLI has the deepest command parsing.** Its `is_safe_command.rs` tokenizes commands, inspects individual flags, and handles shell wrappers (`bash -lc`). Examples: `find` safe unless `-exec`/`-delete` present; `git status` safe but `git push` requires approval; `bash -lc "ls && cat foo"` parses inner commands recursively.

3. **Hardcoded safe lists are controversial.** Claude Code had one and removed it after CVE-2025-66032 showed even "read-only" commands can be exploited: `sort --compress-program=bash`, `man` with custom `MANPAGER`, `sed -i`, `history -a`. Post-CVE trend: sandbox is the security boundary, auto-approval is a UX convenience the user configures.

4. **Deny lists take precedence over allow lists.** Both Windsurf and Claude Code implement this pattern.

5. **Three preset modes are common.** Codex CLI: `read-only` / `auto` / `full-access`. Gemini CLI: `default` / `auto_edit` / `yolo`. Co CLI has a simpler version (`auto_confirm` / yolo `a`).

### Implementation

**Design: Chat-loop pre-filter.** `requires_approval=True` stays on the tool registration. `_handle_approvals` in `main.py` pre-filters before prompting:

```
DeferredToolRequests arrives
  -> for each pending run_shell_command call:
      -> if deps.auto_confirm: auto-approve (existing behavior)
      -> if cmd matches safe prefix AND no shell chaining: auto-approve silently
      -> otherwise: prompt user [y/n/a] (existing behavior)
```

**Safety checker** (`co_cli/_approval.py`):

```python
def _is_safe_command(cmd: str, safe_commands: list[str]) -> bool:
    # Reject shell chaining, redirection, and backgrounding.
    # Single-char ops also catch doubled forms (& catches &&, > catches >>, etc.)
    if any(op in cmd for op in [";", "&", "|", ">", "<", "`", "$(", "\n"]):
        return False
    # Match first token (or multi-word prefix like "git status")
    for prefix in sorted(safe_commands, key=len, reverse=True):
        if cmd == prefix or cmd.startswith(prefix + " "):
            return True
    return False
```

**Rejected operators:** `;`, `&` (catches `&&`), `|` (catches `||`), `>` (catches `>>`), `<` (catches `<<`), backtick, `$(`, `\n`.

**Config:** `shell_safe_commands` in `settings.json`, overridable via `CO_CLI_SHELL_SAFE_COMMANDS` (comma-separated). `[]` disables auto-approval entirely.

### Risk: Prefix Matching

The safe list is a **UX convenience, not a security boundary**. The Docker sandbox is the real security layer.

Known bypass patterns (from CVE-2025-66032 research):
- `sort --compress-program=bash` — executes arbitrary program
- `man` with custom pager — arbitrary code via `MANPAGER`
- `sed -i` — in-place file modification (not read-only)
- `history -a` — writes to arbitrary files

Mitigations:
1. Reject chaining/redirection/backgrounding operators
2. Longest-prefix-first matching for multi-word commands
3. Conservative default list — users expand via settings
4. Document clearly that this is not a security boundary

### Future: Token-Level Classification

Current prefix matching operates on the raw command string. `shlex.split()` would allow token-level classification (e.g., rejecting `find -exec` while allowing `find -name`). Low priority — the sandbox is the real boundary, and Codex CLI is the only tool that goes this deep.

---

## Sandbox Design

### Responsibilities

| Responsibility | Description |
|----------------|-------------|
| **Lazy client** | Connect to Docker only when first command runs |
| **Container reuse** | Single container per session, no startup overhead |
| **Volume mounting** | Map host CWD to `/workspace` inside container |
| **Cleanup** | Stop and remove container on session end |

### Container Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│                    Container States                              │
└─────────────────────────────────────────────────────────────────┘

                    ┌─────────────┐
                    │   (none)    │
                    └──────┬──────┘
                           │ First command
                           ▼
              ┌────────────────────────┐
              │ ensure_container()     │
              │                        │
              │ Container exists?      │
              │   ├── No  ──▶ Create   │
              │   └── Yes ──▶ Check    │
              │              status    │
              │              │         │
              │        ┌─────┴─────┐   │
              │        ▼           ▼   │
              │     Running    Stopped │
              │        │           │   │
              │        │       Start   │
              │        │           │   │
              │        └─────┬─────┘   │
              │              ▼         │
              │         Return         │
              │        container       │
              └────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │    Container Ready     │
              │ (name: co-runner-<id>) │
              └────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
    ┌─────────┐       ┌─────────┐       ┌─────────┐
    │ Command │       │ Command │       │ Command │
    │    1    │       │    2    │  ...  │    N    │
    └─────────┘       └─────────┘       └─────────┘
         │                 │                 │
         └─────────────────┴─────────────────┘
                           │
                           │ All reuse same container
                           │ (no startup overhead)
                           │
                           ▼
              ┌────────────────────────┐
              │     Session End        │
              │                        │
              │  cleanup()             │
              │    ├── stop()          │
              │    └── remove()        │
              └────────────────────────┘
```

### Container Configuration

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `name` | `"co-runner-{session_id[:8]}"` | Session-scoped name to avoid cross-session collisions |
| `image` | `co-cli-sandbox` | Custom image with dev tools (see below) |
| `user` | `"1000:1000"` | Non-root execution — matches typical host UID |
| `network_mode` | `"none"` (configurable) | No network by default; `"bridge"` opt-in via settings |
| `mem_limit` | `"1g"` (configurable) | OOM-kill at 1 GB — industry norm for agentic sandboxes |
| `nano_cpus` | `1_000_000_000` (configurable) | 1 CPU core |
| `pids_limit` | `256` | Fork bomb prevention |
| `cap_drop` | `["ALL"]` | Drop all Linux capabilities |
| `security_opt` | `["no-new-privileges"]` | Prevent setuid/setgid escalation |
| `detach` | `True` | Run in background |
| `tty` | `True` | Keep container alive |
| `command` | `"sh"` | Idle process to prevent exit |
| `working_dir` | `/workspace` | Default directory for exec |

### Volume Mount

```
Host                              Container
─────────────────────────────────────────────────
$(pwd)          ────▶            /workspace
(captured at                     (read-write)
 Sandbox init)
```

**Critical:** `workspace_dir` is captured at `Sandbox.__init__()` time, not at command execution time. This ensures consistent behavior throughout a session.

### Command Execution

```
run_command(cmd, timeout=120)
       │
       ▼
┌──────────────────────────────────────┐
│ ensure_container()                    │
│   └── Returns running container      │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ Wrap: timeout {N} sh -c '{cmd}'      │
│                                       │
│ asyncio.wait_for(                     │
│   asyncio.to_thread(                  │
│     container.exec_run(wrapped,       │
│       workdir="/workspace",           │
│       environment=PYTHONUNBUFFERED=1) │
│   ),                                  │
│   timeout=N+5  # Python safety net   │
│ )                                     │
│   └── Returns (exit_code, output)    │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ exit_code?                            │
│   ├── 124 ──▶ raise RuntimeError     │
│   │     (timeout + partial output)   │
│   ├── ≠0  ──▶ raise RuntimeError     │
│   │          (exit code + output)    │
│   └── 0   ──▶ Return decoded output  │
└──────────────────────────────────────┘
```

**Why `sh -c`:** Docker `exec_run(cmd)` without a shell wrapper treats the string as a raw executable path — shell builtins (`cd`), pipes (`grep foo | wc -l`), redirects (`> file.txt`), and aliases (`ll`) all fail. Wrapping in `sh -c` runs every command through a proper shell.

### Timeout: LLM-Controlled, Two Layers

#### Problem Solved

Without a timeout, any long-running or infinite-loop command (e.g., `python bot.py` with `while True: time.sleep(3600)`) blocks `container.exec_run()` forever. Since `exec_run` is synchronous, the thread is stuck, no spans are logged, and the CLI appears frozen — permanently.

A hardcoded timeout doesn't work either — `ls` needs 10s, a build might need 600s. The solution: the LLM picks the timeout per command, bounded by a settings-level ceiling.

#### Three Layers

| Layer | Controls | Default |
|-------|----------|---------|
| **Tool parameter** (`timeout`) | LLM chooses per call, visible in tool schema | 120s |
| **Hard ceiling** (`sandbox_max_timeout`) | Settings-level cap in `CoDeps`, LLM cannot exceed | 600s (configurable) |
| **System prompt** | Instructs LLM to set appropriate timeouts and warn about forever-running scripts | N/A |

The `timeout` parameter is part of the tool schema, so the LLM reasons about it naturally:
- `run_shell_command(cmd="ls -la", timeout=10)` — quick listing
- `run_shell_command(cmd="python train.py", timeout=600)` — long build
- `run_shell_command(cmd="python bot.py")` — default 120s, killed if it hangs

The tool clamps the value: `effective = min(timeout, ctx.deps.sandbox_max_timeout)`.

#### Two Timeout Mechanisms (Belt and Suspenders)

Two mechanisms run in parallel to ensure both the container process and the Python thread are bounded:

1. **In-container** — coreutils `timeout N` wraps the command inside the container. Sends SIGTERM at `N` seconds. Exit code 124 on kill. Available in all standard images (part of coreutils).
2. **Python-side** — `asyncio.wait_for(..., timeout=N+5)` wraps the `exec_run` call (run in a thread via `asyncio.to_thread`). Keeps the event loop free and responsive. The 5s grace period lets the in-container kill fire first under normal conditions; the Python timeout is a safety net if the container is unresponsive or the coreutils timeout fails to propagate.

Why both layers: `exec_run()` is a synchronous blocking call. Even with coreutils `timeout` inside the container, Python blocks waiting for `exec_run` to return. Without the `asyncio.wait_for` wrapper, the event loop would freeze for the entire timeout duration — no spans logged, no UI updates, CLI appears hung. The async wrapper ensures the event loop stays responsive and the error surfaces immediately.

On timeout, `RuntimeError` includes partial output captured before the kill, so the LLM (and user) can see what happened before the process was terminated.

### stdout / stderr Handling

`exec_run` defaults to `stdout=True, stderr=True` and returns both streams **merged** — equivalent to `2>&1`. This is intentional:

- The LLM sees all output regardless of which fd produced it
- No information is lost; error messages and normal output are interleaved in execution order
- Splitting (`demux=True`) would add complexity with no benefit — the LLM doesn't need to distinguish stderr from stdout
- This matches industry practice (E2B, Devin, Claude Code all merge streams)

### PYTHONUNBUFFERED

`exec_run` passes `environment={"PYTHONUNBUFFERED": "1"}`. Without this, Python buffers stdout when not connected to a TTY. If a timeout kills the process, unflushed output is lost — the user sees no output even though the script printed before hanging. `PYTHONUNBUFFERED` forces Python to flush after every write, so partial output is always captured. Harmless for non-Python commands.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Lazy `_client` | Don't connect to Docker until needed |
| Session-scoped named container | Enables reuse detection while isolating concurrent sessions |
| `tty=True` + `command="sh"` | Keeps container alive between exec calls |
| `["sh", "-c", cmd]` wrapping | Enables shell builtins, pipes, redirects, variable expansion |
| Capture CWD at init | Consistent workspace for entire session |
| Silent cleanup errors | Session end shouldn't fail if container already gone |
| `async run_command` | `exec_run` is synchronous — async wrapper via `to_thread` + `wait_for` keeps event loop free |
| Dual timeout layers | In-container coreutils kill + Python-side asyncio timeout. Either alone is insufficient: coreutils alone blocks the thread; asyncio alone can't kill the in-container process |
| LLM-visible `timeout` param | Exposed in tool schema so the LLM can reason about appropriate values per command |
| `PYTHONUNBUFFERED=1` | Ensures partial output is captured on timeout — Python buffers stdout when not on a TTY |
| Merged stdout/stderr | `exec_run` returns both streams merged (`2>&1`). LLM doesn't need to distinguish; splitting adds complexity with no benefit |
| Non-root `1000:1000` | Matches typical host UID on Linux/macOS. Prevents container processes from running as root. Configurable if CWD ownership differs |
| `network_mode="none"` | No network by default — agentic sandbox norm (E2B, Claude Code reference). Configurable to `"bridge"` for workflows needing network (e.g., `pip install`) |
| `mem_limit="1g"` | 1 GB hard limit. Industry range 512 MiB–2 GB; 1 GB balances dev tool headroom vs runaway protection. Configurable for heavy workloads |
| `nano_cpus=1_000_000_000` | 1 CPU core. Prevents runaway builds from starving the host. Configurable |
| `pids_limit=256` | Prevents fork bombs. Industry range 100–512; 256 is safe for normal dev workflows |
| `cap_drop=["ALL"]` | Drops all Linux capabilities — zero-cost hardening, standard in Anthropic reference sandbox and E2B |
| `no-new-privileges` | Prevents privilege escalation via setuid/setgid binaries. Zero overhead, blocks a common container escape vector |

---

## Security Model

### Isolation Boundary

```
┌─────────────────────────────────────────────────────────────────┐
│                        HOST SYSTEM                               │
│                                                                  │
│  PROTECTED (not accessible from container):                     │
│  ├── /home/user/.ssh/                                           │
│  ├── /home/user/.config/                                        │
│  ├── /etc/                                                       │
│  ├── Other directories                                          │
│  └── Docker socket (not mounted)                                │
│                                                                  │
│  EXPOSED (read-write):                                          │
│  └── Current working directory only                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ bind mount (rw)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      CONTAINER                                   │
│                                                                  │
│  /workspace/  ◀── Only this is accessible                       │
│                                                                  │
│  Hardening:                                                     │
│  ├── Non-root (user 1000:1000)                                  │
│  ├── No network (network_mode=none)                             │
│  ├── mem_limit=1g, 1 CPU, pids_limit=256                       │
│  ├── cap_drop=ALL, no-new-privileges                            │
│  │                                                               │
│  CAN do:                          CANNOT do:                    │
│  ├── List/read/write files        ├── Access ~/.ssh             │
│  ├── Run scripts                  ├── Access ~/.config          │
│  ├── Install packages (in         ├── Modify host /etc          │
│  │   container only)              ├── Access other dirs         │
│  └── Git operations               ├── Spawn host processes      │
│                                    ├── Escalate privileges       │
│                                    └── Access network (default)  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Destructive Command Protection

```
Scenario: LLM runs "rm -rf /"

┌─────────────────────────────────────────────────────────────────┐
│ 1. User confirmation required (unless auto_confirm=true)        │
│                                                                  │
│    Execute command: rm -rf /? [y/n] (n): █                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ 2. Even if confirmed, damage is limited:                        │
│                                                                  │
│    Container filesystem destroyed ──▶ Container discarded       │
│    /workspace files deleted       ──▶ Only CWD affected         │
│    Host system                    ──▶ UNTOUCHED                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Backend Architecture

Design informed by cross-system research of Codex CLI, Gemini CLI, OpenCode, Claude Code, and Aider. See Appendix for full research data; see `TODO-shell-safety.md` for post-MVP enhancements.

### Best practices from top systems

1. **Environment sanitization blocks CVE-2025-66032 vectors.** Variables like `MANPAGER`, `PAGER`, `GIT_EDITOR`, `LD_PRELOAD`, `BASH_ENV` enable code execution through otherwise-safe commands. Gemini CLI and Codex both sanitize these. All non-Docker backends must apply a shared env blocklist via `restricted_env()`.

2. **Process group killing, not single-process killing.** OpenCode and Gemini CLI kill process trees via `os.killpg()` with SIGTERM → 200ms → SIGKILL. A `sh -c` wrapper creates children; killing only the lead process leaves orphans.

3. **When no sandbox exists, the approval flow IS the security layer.** The mainstream no-sandbox systems (OpenCode, Gemini CLI) require explicit approval for all commands. Safe-prefix auto-approval is disabled without isolation.

### Isolation levels

| Level | Backend | Safe-prefix auto-approve | Approval prompt |
|-------|---------|-------------------------|-----------------|
| `"full"` | Docker | Yes | `[y/n/a]` |
| `"none"` | Subprocess | No — all commands require approval | `[y/n/a]` |

### Shared infrastructure

**`restricted_env()`** — strips dangerous env vars, forces `PAGER=cat`, `GIT_PAGER=cat`. Used by subprocess backend. Docker doesn't need it (clean container env).

**`kill_process_tree()`** — `os.killpg()` with SIGTERM → 200ms → SIGKILL. Used by subprocess backend on timeout. Docker handles cleanup through container exec.

### Backend: Docker (full isolation)

Current implementation. See Container Configuration table for hardening details.

### Backend: Subprocess (no isolation)

Fallback when Docker is unavailable. Simple `asyncio.create_subprocess_exec("sh", "-c", cmd)` with `restricted_env()`, `start_new_session=True`, and process group killing on timeout. No filesystem, network, or capability isolation.

Security relies on the approval flow: every command requires explicit user approval. Safe-prefix auto-approval is disabled when `isolation_level == "none"`.

### Auto-detection

`create_deps()` resolves the backend based on `sandbox_backend` setting (`"auto"` / `"docker"` / `"subprocess"`). In `"auto"` mode: try Docker first (ping daemon), fall back to subprocess with warning. In `"docker"` mode: fail hard if unavailable.

### Post-MVP backends

The `SandboxProtocol` abstraction makes these zero-caller-change additions:

- **macOS Seatbelt** (`isolation_level = "jail"`) — static `.sb` profile with `-D` parameter flags (Codex + Gemini pattern). Both ship this as automatic macOS fallback.
- **Protected subpaths** — `.git` and `.co-cli` read-only Docker volume mounts (Codex pattern). Prevents git hook injection.
- **Pattern learning approval** — `p` response to always-allow a root command prefix for the session (OpenCode + Gemini pattern, ~71 LOC).

### Status banner

The active backend and isolation level are reported in the welcome banner and `/status` output.

---

## Configuration

### Settings

| Setting | Default | Env Override | Description |
|---------|---------|--------------|-------------|
| `docker_image` | `co-cli-sandbox` | `CO_CLI_DOCKER_IMAGE` | Container image |
| `auto_confirm` | `false` | `CO_CLI_AUTO_CONFIRM` | Skip prompts |
| `shell_safe_commands` | `["ls", "cat", ...]` | `CO_CLI_SHELL_SAFE_COMMANDS` | Command prefixes auto-approved when sandboxed (comma-separated env) |
| `sandbox_backend` | `"auto"` | `CO_CLI_SANDBOX_BACKEND` | Backend selection: `"auto"` / `"docker"` / `"subprocess"` |
| `sandbox_max_timeout` | `600` | `CO_CLI_SANDBOX_MAX_TIMEOUT` | Hard ceiling for per-command timeout (seconds) |
| `sandbox_network` | `"none"` | `CO_CLI_SANDBOX_NETWORK` | Container network mode (`"none"` or `"bridge"`) |
| `sandbox_mem_limit` | `"1g"` | `CO_CLI_SANDBOX_MEM_LIMIT` | Container memory limit (Docker format) |
| `sandbox_cpus` | `1` | `CO_CLI_SANDBOX_CPUS` | Container CPU cores |

### Default Image: `co-cli-sandbox`

Built from `Dockerfile.sandbox` (based on `python:3.12-slim`). Adds the shell utilities an LLM naturally reaches for:

```
docker build -t co-cli-sandbox -f Dockerfile.sandbox .
```

| Package | Category | Why |
|---------|----------|-----|
| `curl` | Network | HTTP requests, API testing |
| `wget` | Network | File downloads |
| `git` | VCS | Version control — the #1 dev tool |
| `jq` | Data | JSON processing (LLMs love JSON) |
| `tree` | Files | Directory overview |
| `file` | Files | Identify file types |
| `less` | Paging | Browse long output |
| `zip`/`unzip` | Archive | Compress/decompress |
| `nano` | Editor | Quick non-interactive edits |

**Design rationale:** `python:3.12-slim` has coreutils (`grep`, `sed`, `awk`, `find`, `sort`, `shuf`) but lacks network and dev tools. LLMs frequently reach for `curl`, `git`, and `jq` — without them, every such attempt burns a `ModelRetry` round-trip. Interactive tools (`vim`, `htop`) are excluded because `exec_run` has no TTY interaction.

### Custom Images

For specialized workflows, override the image:

```json
{
  "docker_image": "node:20-slim"
}
```

---

## Error Scenarios

| Scenario | Detection | Handling |
|----------|-----------|----------|
| Docker not running | `docker.from_env()` fails | `RuntimeError` with message |
| Container create fails | `APIError` from Docker | `RuntimeError` with details |
| Command fails | Non-zero exit code | `RuntimeError` raised with exit code + output |
| Command timeout | Exit code 124 (coreutils) or `asyncio.TimeoutError` | `RuntimeError` with timeout message + partial output |
| Exec fails | Exception during `exec_run` | Exception propagates to caller |
| Stale container | Found but stopped | Auto-restart via `container.start()` |

### Manual Recovery

If a session container gets stuck:
```bash
docker ps -a --filter "name=co-runner-" --format "{{.Names}}"
docker rm -f <container-name>
```

---

## Integration Points

### With CoDeps

```
main.py: create_deps()
    │
    ├── session_id = uuid4().hex
    ├── sandbox = _create_sandbox(session_id)
    │      # auto: try Docker, fall back to subprocess
    │      # docker: fail hard if unavailable
    │      # subprocess: always use subprocess
    ├── auto_confirm = settings.auto_confirm
    └── session_id = session_id
    │
    ▼
CoDeps(sandbox, auto_confirm, session_id)
    │
    ▼
agent.run(user_input, deps=deps)
    │
    ▼
run_shell_command receives ctx.deps.sandbox
```

### With Chat Loop

```
chat_loop()
    │
    ├── deps = create_deps()     # Sandbox created
    │
    ├── while True:
    │       │
    │       ├── "!cmd" ──▶ Direct sandbox execution (see §Output Control)
    │       │
    │       └── Natural language ──▶ agent.run(deps=deps)
    │                                  │
    │                                  ├── Tool outputs displayed via _display_tool_outputs()
    │                                  └── LLM summary printed after
    │
    └── finally:
            deps.sandbox.cleanup()  # Sandbox destroyed
```

---

## Output Control

Shell output reaches the user through two paths: agent-mediated (LLM calls the tool) and direct (`!` prefix). Both display raw output in a Rich `Panel` with `border_style="shell"` (a semantic style resolved by the Rich Theme — see `DESIGN-theming-ascii.md`) — the user always sees actual command output, not just an LLM summary.

### Two Execution Paths

| Path | Trigger | Approval | Output Display | LLM Involved |
|------|---------|----------|----------------|--------------|
| **Agent-mediated** | Natural language prompt | `[y/n/a]` via `DeferredToolRequests` | `_display_tool_outputs()` shows raw output in Panel, then LLM commentary follows | Yes |
| **Direct (`!`)** | `!cmd` prefix in REPL | None (user typed it explicitly) | Output shown immediately in Panel | No |

### Agent-Mediated Output (`_display_tool_outputs`)

Without explicit output display, tool return values are consumed by the LLM internally — the user only sees the LLM's summary ("Done. Results above.") with no actual output visible. `_display_tool_outputs()` fixes this by scanning new messages after each agent turn:

```
agent.run() completes
        │
        ▼
_display_tool_outputs(old_len, all_msgs)
        │
        ├── Scan new ModelRequest messages for ToolReturnPart
        │
        ├── Shell tool (str content):
        │     └── Panel(output, title="$ {cmd}", border_style="shell")
        │          cmd extracted from matching ToolCallPart via tool_call_id
        │
        └── Dict tools with "display" field:
              └── Print display value verbatim
        │
        ▼
console.print(Markdown(result.output))   # LLM commentary after
```

### Direct Execution (`!` Prefix)

```
Co ❯ !python3 greeting_bot.py -r 2 -d 0.5
╭─ $ python3 greeting_bot.py -r 2 -d 0.5 ──────────────╮
│ 👋 Greeting bot started! ...                           │
│ Round 1: Good night! Hope you're having a great day.   │
│ Round 2: Good night! Ready to tackle your tasks?       │
│ 👋 Greeting bot stopped!                               │
╰────────────────────────────────────────────────────────╯
```

The `!` handler in the chat loop:
1. Strips the `!` prefix
2. Runs the command directly via `deps.sandbox.run_command()` (same sandbox, same container)
3. Displays output in a `Panel` titled with the command
4. `continue` — skips the LLM entirely

No approval prompt: the user explicitly typed the command, which is itself the approval. Uses `deps.sandbox_max_timeout` as the timeout ceiling, consistent with the tool path.

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/shell.py` | Tool function — delegates to sandbox, `ModelRetry` on error, LLM-visible `timeout` param |
| `co_cli/sandbox.py` | Sandbox protocol + Docker backend. MVP adds subprocess backend |
| `co_cli/_approval.py` | Safe-command classification for auto-approval |
| `co_cli/_sandbox_env.py` | MVP: shared `restricted_env()` and `kill_process_tree()` for subprocess backend |
| `co_cli/deps.py` | CoDeps dataclass — holds sandbox instance and `sandbox_max_timeout` ceiling |
| `co_cli/config.py` | `sandbox_backend`, `sandbox_max_timeout`, and other sandbox settings |
| `Dockerfile.sandbox` | Custom image build (python:3.12-slim + dev tools) |
| `scripts/e2e_timeout.py` | E2E test — LLM edits and runs a script, exercises full timeout pipeline |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| `requires_approval=True` | Pydantic-ai native approval flow | Done |
| Command timeout | LLM-controlled per-invocation timeout, async exec, partial output | Done |
| Tool output display | Raw tool output shown to user via `_display_tool_outputs()` | Done |
| `!` prefix direct exec | Bypass LLM, run command directly in sandbox | Done |
| Resource limits | `mem_limit=1g`, 1 CPU, `pids_limit=256` | Done |
| Network isolation | `network_mode="none"` default, configurable to `"bridge"` | Done |
| Privilege hardening | `cap_drop=ALL`, `no-new-privileges`, non-root `1000:1000` | Done |
| Safe-prefix whitelist | Auto-approve read-only commands; chaining operator rejection | Done |
| Subprocess fallback | Sandbox protocol + subprocess backend with auto-detection | Done |
| Env sanitization | `restricted_env()` allowlist for CVE-2025-66032 vectors | Done |
| Process group kill | `kill_process_tree()` via `os.killpg()` with SIGTERM→SIGKILL | Done |
| Protected subpaths | `.git` and `.co-cli` read-only Docker volume mounts | Post-MVP |
| macOS Seatbelt jail | `sandbox-exec` with static `.sb` profile | Post-MVP |
| Pattern learning approval | `p` response for session-scoped command prefix learning | Post-MVP |

---

## Appendix: Cross-System Research (Feb 2026)

<details>
<summary>Sandbox isolation comparison (Codex, Gemini CLI, Co CLI)</summary>

| System | Sandbox | FS isolation | Protected paths | Network | Env sanitization | Process cleanup |
|--------|---------|-------------|-----------------|---------|-----------------|-----------------|
| **Codex CLI** | bwrap (Linux), Seatbelt (macOS), restricted tokens (Windows) | Read-only root + writable workspace | `.git`, `.codex` auto-read-only; symlink attack detection | Seccomp denies socket/connect/bind; `--unshare-net` | Clean sandbox env | `--die-with-parent` |
| **Gemini CLI** | Docker/Podman (optional), Seatbelt (macOS) | Container with RW workspace | Docker volume access control | Container network isolation | `sanitizeEnvironment()` strips dangerous vars | `killProcessGroup(-pid)` SIGTERM→200ms→SIGKILL |
| **Co CLI** | Docker only | RW workspace bind | None | `network_mode="none"` | Clean container env | `container.exec_run` |

</details>

<details>
<summary>No-sandbox / permission-only comparison (OpenCode, Gemini CLI, Aider, Claude Code)</summary>

| System | Command analysis | Approval UX | Pattern learning | Redirection detection |
|--------|-----------------|-------------|-----------------|----------------------|
| **OpenCode** | Tree-sitter bash AST | `once` / `always` / `always+save` / `reject` | Arity-based: 137 command prefixes | AST-based; `external_directory` gate |
| **Gemini CLI** | Tree-sitter bash AST | `ProceedOnce` / `ProceedAlways` / `ProceedAlwaysAndSave` | Root command prefix saved to policy | `shouldDowngradeForRedirection()` |
| **Aider** | None | None — immediate execution | None | None |
| **Claude Code** | Hook-based (`PreToolUse`) | Hook middleware | User-configured rules | User hooks |
| **Co CLI** | Prefix match + chaining rejection | `y` / `n` / `a(yolo)` | None | Catches `>`, `<` in operators |

</details>

<details>
<summary>Actual implementation sizes from source code</summary>

| System | Sandbox LOC | Permission/safety LOC | Contributors | Notes |
|--------|------------|----------------------|-------------|-------|
| **Codex CLI** | 1,877 (seatbelt 623, bwrap 252, windows 166, shared 331, helpers 505) | 2,355 (safe 592, dangerous 382, windows 1,378) | 349 | Rust, deepest command parsing |
| **Gemini CLI** | 1,117 (Docker 859, utils 150, config 108) + 381 (6 .sb profiles) | 1,406 (policy 518, shell-utils 888) + 196 (env sanitization) | ~500 | TypeScript, tree-sitter |
| **OpenCode** | 0 (no container isolation) | 922 (bash tool 269, permission 490, arity 163) | 713 | TypeScript, permission-only model |
| **Claude Code** | Docker (size unknown, closed) | 1,080 (hooks 140, rule engine 313, config 297, security 280) | 1 (automated) | Python hooks |
| **Aider** | 0 | 0 (132 lines bare `shell=True`) | 1 | No safety model |

</details>

---

## References

- [CVE-2025-66032 — Claude Code sandbox escape via "safe" commands](https://flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/)
- Codex CLI — `codex-rs/core/src/seatbelt.rs`, `codex-rs/linux-sandbox/src/bwrap.rs`, `codex-rs/core/src/command_safety/`
- Gemini CLI — `packages/cli/src/utils/sandbox.ts`, `packages/core/src/services/environmentSanitization.ts`, `packages/core/src/utils/shell-utils.ts`
- OpenCode — `packages/opencode/src/tool/bash.ts`, `packages/opencode/src/permission/next.ts`, `packages/opencode/src/permission/arity.ts`
- Claude Code — hook system (`pretooluse.py`, `rule_engine.py`)
- Aider — `aider/run_cmd.py`
