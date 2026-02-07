# Design: Shell Tool & Docker Sandbox

**Status:** Implemented (Batch 1)
**Last Updated:** 2026-02-06

## Overview

The shell tool executes user commands in a sandboxed Docker container, protecting the host system from potentially destructive operations while maintaining access to the user's working directory.

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
│  CAN do:                          CANNOT do:                    │
│  ├── List/read/write files        ├── Access ~/.ssh             │
│  ├── Run scripts                  ├── Access ~/.config          │
│  ├── Install packages (in         ├── Modify host /etc          │
│  │   container only)              ├── Access other dirs         │
│  └── Git operations               └── Spawn host processes      │
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

## Configuration

### Settings

| Setting | Default | Env Override | Description |
|---------|---------|--------------|-------------|
| `docker_image` | `co-cli-sandbox` | `CO_CLI_DOCKER_IMAGE` | Container image |
| `auto_confirm` | `false` | `CO_CLI_AUTO_CONFIRM` | Skip prompts |
| `sandbox_max_timeout` | `600` | `CO_CLI_SANDBOX_MAX_TIMEOUT` | Hard ceiling for per-command timeout (seconds) |

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
    ├── sandbox = Sandbox(
    │      image=settings.docker_image,
    │      container_name=f"co-runner-{session_id[:8]}",
    │  )
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
| `co_cli/sandbox.py` | Docker container lifecycle, async command execution with dual-layer timeout |
| `co_cli/deps.py` | CoDeps dataclass — holds sandbox instance and `sandbox_max_timeout` ceiling |
| `co_cli/config.py` | `sandbox_max_timeout` setting (env: `CO_CLI_SANDBOX_MAX_TIMEOUT`) |
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
| Resource limits | Memory/CPU constraints | Planned |
| Network isolation | `--network none` option | Planned |
