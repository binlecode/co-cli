# TODO: Conditional Shell Approval (Safe-Prefix Whitelist)

**Status:** Core safe-prefix matching is **implemented** (`_approval.py`, `config.py`, `deps.py`, `main.py`). See `docs/DESIGN-co-cli.md` §9.2 for current architecture. Remaining work: harden the chaining operator rejection set (add `&`, `>`, `>>`, `<`, `<<`, `\n`) and consider `shlex.split()` for token-based classification.

Core approval flow (`requires_approval=True` + `DeferredToolRequests`) is complete and tested. See `docs/DESIGN-co-cli.md` §3.2, §5, §8.2 for architecture. Functional tests for approve/deny/auto-confirm live in `tests/test_commands.py`.

---

## Problem

Every shell command — even harmless read-only ones — requires the user to type `y`:

```
Co > what files are here?
Approve run_shell_command(cmd='ls -la')? [y/n/a(yolo)]   ← friction for a read-only command
```

This gets tedious fast. `ls`, `cat`, `pwd`, `whoami` can't damage anything, yet they get the same approval gate as `rm -rf /workspace` or `curl ... | sh`.

**Why it's UX, not security:** The sandbox already provides isolation (non-root, no network, capped resources, `cap_drop=ALL`). The approval prompt is a second layer — useful for destructive commands, but pure friction for read-only ones.

## Industry Research (Feb 2026)

### Comparison Matrix

| Tool | Built-in safe list | User allowlist | User denylist | Parsing depth | Decision layer |
|------|-------------------|----------------|---------------|---------------|----------------|
| **Codex CLI** | Yes — hardcoded, flag-aware | Prefix rules in TOML | `requirements.toml` | Deep (tokenize, inspect flags, shell wrappers) | Dedicated middleware (`exec_policy.rs`) |
| **Claude Code** | Removed post-CVE-2025-66032 | `settings.json` allow rules | `settings.json` deny rules | User hooks (arbitrary) | Hook middleware (`PreToolUse` event) |
| **Gemini CLI** | No | `tools.allowed` in settings | No | Prefix string match | Tool executor middleware |
| **Windsurf** | No | `cascadeCommandsAllowList` | `cascadeCommandsDenyList` | Prefix match | Cascade orchestrator |
| **Aider** | N/A | No | No | None | Chat loop (`io.confirm_ask()`) |

### Key Findings

**1. Approval lives in middleware, not inside tools.**
Every mature system (Codex, Claude Code, Gemini, Windsurf) places the decision between the agent loop and tool execution. Tools stay as pure executors. Aider is the exception (simplest model — all commands require explicit `y`).

**2. Codex CLI has the deepest command parsing.**
Its `is_safe_command.rs` tokenizes commands, inspects individual flags, and handles shell wrappers (`bash -lc`). Examples:
- `find` → safe UNLESS `-exec`, `-delete`, or `-fls` present
- `git status` → safe; `git push` → requires approval
- `sed -n 3,5p` → safe; any other `sed` → requires approval
- `bash -lc "ls && cat foo"` → parses inner commands recursively

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

## Proposed Change

### Design: Chat-loop pre-filter

Keep `requires_approval=True` on the tool registration — do NOT move approval logic into the tool itself. Instead, pre-filter in `_handle_approvals` (in `main.py`) before prompting the user. This matches the middleware pattern used by every major CLI.

```
DeferredToolRequests arrives
  → for each pending run_shell_command call:
      → if deps.auto_confirm: auto-approve (existing behavior)
      → if cmd matches safe prefix AND no shell chaining: auto-approve silently
      → otherwise: prompt user [y/n/a] (existing behavior)
```

pydantic-ai has no `ApprovalRequired` exception or `ctx.tool_call_approved` field. The only mechanism is the `DeferredToolRequests` / `DeferredToolResults` / `ToolDenied` flow already in use.

### Default safe list (conservative)

```python
_DEFAULT_SAFE_COMMANDS = [
    # Filesystem listing
    "ls", "tree", "find", "fd",
    # File reading
    "cat", "head", "tail",
    # Search
    "grep", "rg", "ag",
    # Text processing (read-only)
    "wc", "sort", "uniq", "cut", "jq",
    # Output
    "echo", "printf",
    # System info
    "pwd", "whoami", "hostname", "uname", "date",
    "env", "which", "file", "id", "du", "df",
    # Git read-only (prefix match: "git status", "git diff", etc.)
    "git status", "git diff", "git log", "git show",
    "git branch", "git tag", "git blame",
]
```

### Settings-sourced configuration

The list lives in `settings.json` as `shell_safe_commands`, overridable via `CO_CLI_SHELL_SAFE_COMMANDS` (comma-separated). Users can expand or restrict it.

```python
# config.py
class Settings(BaseModel):
    shell_safe_commands: list[str] = Field(default=_DEFAULT_SAFE_COMMANDS)
```

```jsonc
// ~/.config/co-cli/settings.json
{
    "shell_safe_commands": ["ls", "cat", "grep", "git status", "git diff"]
}
```

Setting `"shell_safe_commands": []` disables auto-approval entirely (current behavior).

### Injection into CoDeps

```python
# deps.py
@dataclass
class CoDeps:
    shell_safe_commands: list[str] = field(default_factory=list)

# main.py — create_deps()
CoDeps(shell_safe_commands=settings.shell_safe_commands, ...)
```

### Safety checker (standalone function)

```python
# co_cli/_approval.py  (new internal helper)

def _is_safe_command(cmd: str, safe_commands: list[str]) -> bool:
    """Check if cmd starts with a safe prefix and has no shell chaining."""
    # Reject shell chaining operators — force approval
    if any(op in cmd for op in [";", "&&", "||", "|", "`", "$("]):
        return False
    # Match first token (or multi-word prefix like "git status")
    for prefix in sorted(safe_commands, key=len, reverse=True):  # longest match first
        if cmd == prefix or cmd.startswith(prefix + " "):
            return True
    return False
```

### Chat-loop integration

```python
# main.py — _handle_approvals()
# After parsing call args, before prompting:

if deps.auto_confirm:
    approvals.approvals[call.tool_call_id] = True
    continue

# NEW: auto-approve safe shell commands silently
if call.tool_name == "run_shell_command":
    cmd = args.get("cmd", "")
    if _is_safe_command(cmd, deps.shell_safe_commands):
        approvals.approvals[call.tool_call_id] = True
        continue

# ... existing prompt logic unchanged ...
```

### What does NOT change

- `agent.py` — `requires_approval=True` stays on `run_shell_command`
- `tools/shell.py` — no changes, stays a pure executor
- Existing approve/deny/yolo tests — still pass as-is

## Risk: Prefix Matching

The safe list is a **UX convenience, not a security boundary**. The Docker sandbox is the real security layer.

Known bypass patterns (from Claude Code CVE-2025-66032 research):
- `sort --compress-program=bash` — executes arbitrary program
- `man` with custom pager — arbitrary code via `MANPAGER`
- `sed -i` — in-place file modification (not read-only)
- `history -a` — writes to arbitrary files
- `cat file; rm -rf /` — shell chaining after safe prefix

**Mitigations:**
1. Reject any command containing `;`, `&&`, `||`, `|`, backticks, `$()`
2. Longest-prefix-first matching for multi-word commands (`git status` before `git`)
3. Keep the default list conservative — users can expand via settings
4. Document clearly that this is not a security boundary — the sandbox is

## Files

| File | Change |
|------|--------|
| `co_cli/config.py` | Add `shell_safe_commands: list[str]` with defaults + env var |
| `co_cli/deps.py` | Add `shell_safe_commands: list[str]` field |
| `co_cli/main.py` | Pass `settings.shell_safe_commands` into `CoDeps`; add safe-command check in `_handle_approvals` |
| `co_cli/_approval.py` | New internal helper: `_is_safe_command()` |
| `tests/test_commands.py` | Add tests for `_is_safe_command()` — safe commands, chaining rejection, multi-word prefixes |

**No changes to:** `co_cli/agent.py` (registration stays `requires_approval=True`), `co_cli/tools/shell.py` (stays a pure executor)

## References

- [CVE-2025-66032 — Claude Code sandbox escape via "safe" commands](https://flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/)
- [Codex CLI — command safety implementation](https://github.com/openai/codex/tree/main/codex-rs/core/src/command_safety)
- [Codex CLI — configurable allowlist](https://developers.openai.com/codex/config-reference/)
- [Claude Code — hooks and permission model](https://code.claude.com/docs/en/hooks)
- [Gemini CLI — configuration and tools.allowed](https://google-gemini.github.io/gemini-cli/docs/get-started/configuration.html)
- [Windsurf — terminal allow/deny lists](https://docs.windsurf.com/windsurf/terminal)
- [Anthropic — Claude Code sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing)
