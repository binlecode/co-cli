# TODO: Shell Execution Safety

**Status:** Safe-prefix whitelist and chaining operator hardening are **complete** (`_approval.py`, `config.py`, `deps.py`, `main.py`). See `docs/DESIGN-co-cli.md` §9.2 for architecture. Remaining work: no-sandbox fallback (§2 below) — MVP scoped, best-practice-aligned with cross-system research (Codex, Gemini CLI, OpenCode, Claude Code, Aider).

Core approval flow (`requires_approval=True` + `DeferredToolRequests`) is complete and tested. See `docs/DESIGN-co-cli.md` §3.2, §5, §8.2 for architecture. Functional tests for approve/deny/auto-confirm live in `tests/test_commands.py`.

---

## 1. Safe-Prefix Whitelist (DONE)

### Problem

Every shell command — even harmless read-only ones — requires the user to type `y`:

```
Co > what files are here?
Approve run_shell_command(cmd='ls -la')? [y/n/a(yolo)]   <- friction for a read-only command
```

This gets tedious fast. `ls`, `cat`, `pwd`, `whoami` can't damage anything, yet they get the same approval gate as `rm -rf /workspace` or `curl ... | sh`.

**Why it's UX, not security:** The sandbox already provides isolation (non-root, no network, capped resources, `cap_drop=ALL`). The approval prompt is a second layer — useful for destructive commands, but pure friction for read-only ones.

### Industry Research (Feb 2026)

#### Comparison Matrix

| Tool | Built-in safe list | User allowlist | User denylist | Parsing depth | Decision layer |
|------|-------------------|----------------|---------------|---------------|----------------|
| **Codex CLI** | Yes — hardcoded, flag-aware | Prefix rules in TOML | `requirements.toml` | Deep (tokenize, inspect flags, shell wrappers) | Dedicated middleware (`exec_policy.rs`) |
| **Claude Code** | Removed post-CVE-2025-66032 | `settings.json` allow rules | `settings.json` deny rules | User hooks (arbitrary) | Hook middleware (`PreToolUse` event) |
| **Gemini CLI** | No | `tools.allowed` in settings | No | Prefix string match | Tool executor middleware |
| **Windsurf** | No | `cascadeCommandsAllowList` | `cascadeCommandsDenyList` | Prefix match | Cascade orchestrator |
| **Aider** | N/A | No | No | None | Chat loop (`io.confirm_ask()`) |

#### Key Findings

**1. Approval lives in middleware, not inside tools.**
Every mature system (Codex, Claude Code, Gemini, Windsurf) places the decision between the agent loop and tool execution. Tools stay as pure executors. Aider is the exception (simplest model — all commands require explicit `y`).

**2. Codex CLI has the deepest command parsing.**
Its `is_safe_command.rs` tokenizes commands, inspects individual flags, and handles shell wrappers (`bash -lc`). Examples:
- `find` -> safe UNLESS `-exec`, `-delete`, or `-fls` present
- `git status` -> safe; `git push` -> requires approval
- `sed -n 3,5p` -> safe; any other `sed` -> requires approval
- `bash -lc "ls && cat foo"` -> parses inner commands recursively

Its `is_dangerous_command.rs` has an explicit blocklist: `rm -rf`, `git reset`, `git push --force`, `sudo` wrapping dangerous commands.

**3. Hardcoded safe lists are controversial.**
Claude Code had one and removed it after CVE-2025-66032 showed even "read-only" commands can be exploited:
- `sort --compress-program=bash` — executes arbitrary program
- `man` with custom `MANPAGER` — arbitrary code execution
- `sed -i` — in-place file modification
- `history -a` — writes to arbitrary files

Post-CVE, the safe list became entirely user-configured. The trend: treat the sandbox as the security boundary, treat auto-approval as a **UX convenience** the user configures.

**4. Deny lists take precedence over allow lists.**
Both Windsurf and Claude Code implement this pattern.

**5. Three preset modes are common.**
Codex CLI: `read-only` / `auto` / `full-access`. Gemini CLI: `default` / `auto_edit` / `yolo`. Claude Code: `plan` / `default` / `accept-edits` / `don't-ask` / `bypass`. Co CLI already has a simpler version (`auto_confirm` / yolo `a`).

### Implementation (complete)

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

**Files touched:** `_approval.py`, `config.py`, `deps.py`, `main.py`, `tests/test_commands.py`.

### Risk: Prefix Matching

The safe list is a **UX convenience, not a security boundary**. The Docker sandbox (or local jail — see §2) is the real security layer.

Known bypass patterns (from Claude Code CVE-2025-66032 research):
- `sort --compress-program=bash` — executes arbitrary program
- `man` with custom pager — arbitrary code via `MANPAGER`
- `sed -i` — in-place file modification (not read-only)
- `history -a` — writes to arbitrary files

Mitigations:
1. Reject chaining/redirection/backgrounding operators (complete)
2. Longest-prefix-first matching for multi-word commands (complete)
3. Conservative default list — users expand via settings (complete)
4. Document clearly that this is not a security boundary (complete)

### Nice-to-have: `shlex.split()` token classification

Current prefix matching operates on the raw command string. `shlex.split()` would allow token-level classification (e.g. rejecting `find -exec` while allowing `find -name`). Low priority — the sandbox is the real boundary, and Codex CLI is the only tool that goes this deep.

---

## 2. No-Sandbox Fallback (TODO)

### Problem

The `Sandbox` class hard-requires Docker. If Docker isn't available, `sandbox.py:33-35` raises `RuntimeError("Docker is not available")` and every shell command fails. This blocks:

- Developers without Docker Desktop (licensing, weight, corporate IT policy)
- Quick-try users evaluating co-cli without Docker setup
- CI environments or lightweight VMs without a container runtime

Currently there is no graceful degradation — shell tools are completely dead without Docker.

### Best practice alignment

Studied 5 local repos (Codex CLI, Gemini CLI, OpenCode, Claude Code, Aider). Key best practices that apply regardless of project scale:

| Practice | Who ships it | Our take |
|----------|-------------|----------|
| Sandbox protocol abstraction | Codex (trait), Gemini (class) | **MVP** — enables fallback without changing callers |
| Env sanitization (CVE-2025-66032 vectors) | Gemini (196 LOC), Codex (clean env) | **MVP** — small, high-value, prevents pager/editor hijacking |
| Process group killing | Gemini, OpenCode | **MVP** — prevents zombie processes on timeout |
| Subprocess fallback when no container | OpenCode (entire model), Codex (returns None) | **MVP** — this IS the fix |
| Disable auto-approval without sandbox | OpenCode, Gemini (all commands require approval) | **MVP** — approval becomes the security layer |
| macOS Seatbelt jail | Codex (623 LOC), Gemini (6 static .sb files) | **Post-MVP** — best practice but not blocking |
| Protected subpaths (.git RO) | Codex (all platforms) | **Post-MVP** — one-line Docker volume change |
| Pattern learning approval (always-allow) | OpenCode (71 LOC), Gemini | **Post-MVP** — reduces friction, small impl |

### MVP Design

#### Sandbox protocol

```python
# co_cli/sandbox.py

from typing import Protocol, runtime_checkable

@runtime_checkable
class SandboxProtocol(Protocol):
    """Execution environment for shell commands."""
    isolation_level: str  # "full" | "none"
    async def run_command(self, cmd: str, timeout: int = 120) -> str: ...
    def cleanup(self) -> None: ...
```

Rename existing `Sandbox` → `DockerSandbox` with `isolation_level = "full"`. `CoDeps.sandbox` type becomes `SandboxProtocol`. All callers unaffected.

#### Subprocess backend

```python
class SubprocessBackend:
    isolation_level = "none"

    async def run_command(self, cmd: str, timeout: int = 120) -> str:
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=restricted_env(),
            start_new_session=True,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await kill_process_tree(proc)
            raise RuntimeError(f"Command timed out after {timeout}s: {cmd}")
        decoded = stdout.decode("utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"exit code {proc.returncode}: {decoded.strip()}")
        return decoded

    def cleanup(self) -> None:
        pass
```

#### Shared infrastructure (`_sandbox_env.py`)

```python
_DANGEROUS_ENV_VARS = {
    "MANPAGER", "PAGER", "GIT_PAGER",       # Arbitrary code via pager
    "EDITOR", "VISUAL", "GIT_EDITOR",        # Arbitrary code via editor
    "BROWSER",                                # Arbitrary code via browser
    "PYTHONSTARTUP",                          # Python startup script injection
    "PERL5OPT", "RUBYOPT",                   # Language-level injection
    "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",   # Shared library injection
    "BASH_ENV", "ENV",                        # Shell startup injection
}

_SAFE_ENV_VARS = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
    "TERM", "SHELL", "TMPDIR", "XDG_RUNTIME_DIR",
}

def restricted_env() -> dict[str, str]:
    """Build a sanitized environment for subprocess execution."""
    base = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_VARS}
    base["PYTHONUNBUFFERED"] = "1"
    base["PAGER"] = "cat"
    base["GIT_PAGER"] = "cat"
    return base

async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill process and all children via process group."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    await asyncio.sleep(0.2)
    if proc.returncode is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
```

#### Auto-detection

```python
def _create_sandbox(settings) -> SandboxProtocol:
    backend = settings.sandbox_backend  # "auto" | "docker" | "subprocess"

    if backend in ("docker", "auto"):
        try:
            import docker
            docker.from_env().ping()
            return DockerSandbox(...)
        except Exception:
            if backend == "docker":
                raise  # explicit — don't hide
            # auto: fall through

    console.print("[yellow]Docker unavailable — running without sandbox[/yellow]")
    return SubprocessBackend(os.getcwd())
```

#### Approval behavior

When `isolation_level == "none"`: disable safe-prefix auto-approval. All commands go through `y/n/a` prompt. The existing approval flow handles this — just check `isolation_level` before calling `_is_safe_command()`.

```python
# main.py — _handle_approvals()
if sandbox.isolation_level != "none" and _is_safe_command(cmd, deps.shell_safe_commands):
    approvals.approvals[call.tool_call_id] = True
    continue
# otherwise: prompt user
```

#### Status banner

```
Sandbox   active   Docker (full isolation)
Sandbox   active   subprocess (no isolation)
```

### MVP files

| File | Change |
|------|--------|
| `co_cli/sandbox.py` | Extract `SandboxProtocol`; rename `Sandbox` → `DockerSandbox`; add `SubprocessBackend` |
| `co_cli/_sandbox_env.py` | New: `restricted_env()`, `kill_process_tree()` |
| `co_cli/config.py` | Add `sandbox_backend: Literal["auto", "docker", "subprocess"]` |
| `co_cli/deps.py` | Type `sandbox` as `SandboxProtocol` |
| `co_cli/main.py` | Auto-detect in `create_deps()`; guard `_is_safe_command()` on `isolation_level` |
| `co_cli/status.py` | Report active backend in `StatusInfo` |
| `tests/test_shell.py` | Add subprocess backend tests: timeout, exit code, env scrubbing |

**No changes to:** `co_cli/agent.py`, `co_cli/tools/shell.py` — same `run_command()` interface.

### Post-MVP enhancements

Protocol makes these zero-caller-change additions:

1. **DarwinJail** (macOS Seatbelt) — `isolation_level = "jail"`. Static `.sb` profile (Gemini pattern, not dynamic generation). Both Codex and Gemini ship this.
2. **Protected subpaths** — `.git` and `.co-cli` mounted read-only in Docker. One-line volume change (Codex pattern).
3. **Pattern learning approval** — `y/n/p/a` prompt for `isolation_level == "none"`. `p` = always-allow root command prefix for session. OpenCode ships this in 71 LOC.

### Risks

1. **False sense of security.** Subprocess has no isolation. Mitigation: warning banner + `isolation_level` drives approval behavior.
2. **`sandbox-exec` deprecated** (post-MVP). Mitigation: if removed, DarwinJail degrades to SubprocessBackend with warning.

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

## References

- [CVE-2025-66032 — Claude Code sandbox escape via "safe" commands](https://flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/)
- Codex CLI — `codex-rs/core/src/seatbelt.rs`, `codex-rs/linux-sandbox/src/bwrap.rs`, `codex-rs/core/src/command_safety/`
- Gemini CLI — `packages/cli/src/utils/sandbox.ts`, `packages/core/src/services/environmentSanitization.ts`, `packages/core/src/utils/shell-utils.ts`
- OpenCode — `packages/opencode/src/tool/bash.ts`, `packages/opencode/src/permission/next.ts`, `packages/opencode/src/permission/arity.ts`
- Claude Code — hook system (`pretooluse.py`, `rule_engine.py`)
- Aider — `aider/run_cmd.py`
