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
run_command(cmd)
       │
       ▼
┌──────────────────────────────────────┐
│ ensure_container()                    │
│   └── Returns running container      │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ container.exec_run(                   │
│     ["sh", "-c", cmd],               │
│     workdir="/workspace"              │
│ )                                     │
│   └── Returns (exit_code, output)    │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ exit_code != 0?                       │
│   ├── Yes ──▶ raise RuntimeError     │
│   │          (exit code + output)    │
│   └── No  ──▶ Return decoded output  │
└──────────────────────────────────────┘
```

**Why `sh -c`:** Docker `exec_run(cmd)` without a shell wrapper treats the string as a raw executable path — shell builtins (`cd`), pipes (`grep foo | wc -l`), redirects (`> file.txt`), and aliases (`ll`) all fail. Wrapping in `["sh", "-c", cmd]` runs every command through a proper shell.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Lazy `_client` | Don't connect to Docker until needed |
| Session-scoped named container | Enables reuse detection while isolating concurrent sessions |
| `tty=True` + `command="sh"` | Keeps container alive between exec calls |
| `["sh", "-c", cmd]` wrapping | Enables shell builtins, pipes, redirects, variable expansion |
| Capture CWD at init | Consistent workspace for entire session |
| Silent cleanup errors | Session end shouldn't fail if container already gone |

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
    │       agent.run(deps=deps)  # Sandbox reused
    │
    └── finally:
            deps.sandbox.cleanup()  # Sandbox destroyed
```

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/shell.py` | Tool function — delegates to sandbox, `ModelRetry` on error |
| `co_cli/sandbox.py` | Docker container lifecycle, `sh -c` command execution |
| `co_cli/deps.py` | CoDeps dataclass holding sandbox instance |
| `Dockerfile.sandbox` | Custom image build (python:3.12-slim + dev tools) |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| `requires_approval=True` | Pydantic-ai native approval flow | Done |
| Command timeout | Kill long-running commands | Planned |
| Resource limits | Memory/CPU constraints | Planned |
| Network isolation | `--network none` option | Planned |
