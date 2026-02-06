# Design: Shell Tool & Docker Sandbox

**Status:** Implemented (Batch 1)
**Last Updated:** 2026-02-04

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
│  │  1. Check auto_confirm flag                              │    │
│  │  2. Prompt user (if needed)                              │    │
│  │  3. Delegate to sandbox                                  │    │
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
| **Confirmation gate** | Prompts user before executing any command |
| **Delegation** | Passes command to sandbox, returns output |
| **Error wrapping** | Catches exceptions, returns error strings to LLM |

### Processing Flow

```
LLM calls run_shell_command(cmd)
              │
              ▼
┌─────────────────────────────────┐
│ auto_confirm = true?            │
│   ├── Yes ──▶ Skip prompt       │
│   └── No  ──▶ Show prompt       │
│                  │              │
│              ┌───┴───┐          │
│              ▼       ▼          │
│            "y"      "n"         │
│              │       │          │
│              │       └──▶ Return "Command cancelled"
│              ▼                  │
└─────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│ sandbox.run_command(cmd)        │
│   ├── Success ──▶ Return output │
│   └── Error   ──▶ Return error  │
└─────────────────────────────────┘
              │
              ▼
        Output to LLM
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Access sandbox via `ctx.deps` | No global state, testable, follows pydantic-ai pattern |
| Use `rich.prompt.Confirm` | Non-blocking (works with async), unlike `typer.confirm` |
| Return error strings | Let LLM handle errors gracefully instead of crashing |
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
              │    (name: co-runner)   │
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
| `name` | `"co-runner"` | Fixed name for reuse detection |
| `image` | `python:3.12-slim` | Lightweight, has Python |
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
│     cmd,                              │
│     workdir="/workspace"              │
│ )                                     │
│   └── Returns (exit_code, output)    │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│ output.decode("utf-8")               │
│   └── Return string to caller        │
└──────────────────────────────────────┘
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Lazy `_client` | Don't connect to Docker until needed |
| Named container | Enables reuse detection via `containers.get()` |
| `tty=True` + `command="sh"` | Keeps container alive between exec calls |
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
| `docker_image` | `python:3.12-slim` | `CO_CLI_DOCKER_IMAGE` | Container image |
| `auto_confirm` | `false` | `CO_CLI_AUTO_CONFIRM` | Skip prompts |

### Custom Images

For specialized workflows, configure a different image:

```json
{
  "docker_image": "node:20-slim"
}
```

Or build a custom image with pre-installed tools for faster execution.

---

## Error Scenarios

| Scenario | Detection | Handling |
|----------|-----------|----------|
| Docker not running | `docker.from_env()` fails | `RuntimeError` with message |
| Container create fails | `APIError` from Docker | `RuntimeError` with details |
| Command fails | Non-zero exit code | Output returned (includes error) |
| Exec fails | Exception during `exec_run` | `"Sandbox Error: ..."` string |
| Stale container | Found but stopped | Auto-restart via `container.start()` |

### Manual Recovery

If container gets stuck:
```bash
docker rm -f co-runner
```

---

## Integration Points

### With CoDeps

```
main.py: create_deps()
    │
    ├── sandbox = Sandbox(image=settings.docker_image)
    ├── auto_confirm = settings.auto_confirm
    └── session_id = uuid4().hex
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
| `co_cli/tools/shell.py` | Tool function with confirmation logic |
| `co_cli/sandbox.py` | Docker container lifecycle management |
| `co_cli/deps.py` | CoDeps dataclass holding sandbox instance |

---

## Future Enhancements

| Enhancement | Description | Status |
|-------------|-------------|--------|
| `requires_approval=True` | Pydantic-ai native approval flow | Batch 6 |
| Command timeout | Kill long-running commands | Planned |
| Resource limits | Memory/CPU constraints | Planned |
| Network isolation | `--network none` option | Planned |
