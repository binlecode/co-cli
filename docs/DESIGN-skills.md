# Skills

Skills are slash-command prompt macros with lifecycle management — `.md` files that expand into LLM turns with optional arg substitution, shell preprocessing, env injection, and tool-approval grants.

## 1. What & How

Skills are loaded from two sources at session startup: package-default (`co_cli/skills/*.md`) and project-local (`.co-cli/skills/*.md`). Both populate the `SKILL_COMMANDS` dict; project-local files override package-default on name collision. When the user invokes `/skill-name args`, `dispatch()` applies arg substitution, evaluates shell blocks, stages env vars on deps, sets the allowed-tools grant, then hands the expanded body to `chat_loop()` as the LLM user turn. `chat_loop()` performs the actual `os.environ` mutation just before the LLM turn and restores it after. After `/skills install` or `/skills reload`, the REPL tab-completer is updated live without restart. A file watcher polls `.co-cli/skills/` mtimes before each prompt and silently reloads on change. Skills installed from a URL have `source-url` persisted in their frontmatter; `/skills upgrade <name>` re-fetches from the stored URL and reinstalls.

```
Startup:
  co_cli/skills/*.md  ──┐
                         ├── _load_skills() ──▶ SKILL_COMMANDS dict
  .co-cli/skills/*.md ──┘     (project overrides package-default on name collision)

User input /skill-name args:
  dispatch()
    ──▶ active_skill_env ← skill.skill_env   (set before body processing)
    ──▶ active_skill_allowed_tools ← skill.allowed_tools
    ──▶ arg substitution ($ARGUMENTS / $N)
    ──▶ _preprocess_shell_blocks()    (!`cmd` → stdout, /bin/sh, max 3, 5s)
    ──▶ ctx.skill_body = body
    ──▶ chat_loop() → os.environ.update(active_skill_env) → LLM turn
    (after run_turn: os.environ restored, active_skill_env.clear(), active_skill_allowed_tools.clear())
```

## 2. Data Model

> **Full lifecycle spec:** [DESIGN-flow-skills-lifecycle.md](DESIGN-flow-skills-lifecycle.md) — two-pass startup load, `requires` gates, dispatch pipeline, arg substitution, shell preprocessing, env injection/rollback, allowed-tools grant, file watcher, security scanner, install/upgrade/reload flows.

### SkillCommand model (frozen dataclass)

```
name: str
description: str = ""
body: str = ""
argument_hint: str = ""
user_invocable: bool = True
disable_model_invocation: bool = False
requires: dict = field(default_factory=dict)
skill_env: dict[str, str] = field(default_factory=dict)   # env vars to inject (blocked vars filtered at load)
allowed_tools: list[str] = field(default_factory=list)    # tools auto-approved for this skill's LLM turn
```

### Security scanner patterns

`_scan_skill_content(content)` checks four patterns:
- `credential_exfil` — env vars piped/sent to external URLs
- `pipe_to_shell` — output piped to `sh`, `bash`, `eval`
- `destructive_shell` — `rm -rf`, `dd if=`, `mkfs`, `:(){ :|: & }:`
- `prompt_injection` — `IGNORE`, `DISREGARD`, `NEW INSTRUCTIONS` in body

Called at load time (logger.warning only), install time (blocking — user must confirm), and reload time (user-visible warning).

## 3. Config

| Setting | How set | Value |
|---------|---------|-------|
| Project-local skills dir | `deps.config.skills_dir` set in `chat_loop()` | `Path.cwd() / ".co-cli" / "skills"` |
| Package-default skills dir | Computed in `_load_skills()` | `Path(__file__).parent / "skills"` |

Neither is user-configurable via env var. Project-local path is always relative to the working directory at session start.

Blocked env var names (skill-env may not override):
`PATH`, `PYTHONPATH`, `PYTHONHOME`, `LD_PRELOAD`, `LD_LIBRARY_PATH`,
`DYLD_INSERT_LIBRARIES`, `HOME`, `USER`, `SHELL`, `SUDO_UID`

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_commands.py` | Full skills subsystem: SkillCommand model, loader, dispatch, scanner, /skills handlers |
| `co_cli/skills/` | Package-default skills (always available); ships `doctor.md` |
| `co_cli/deps.py` | `skills_dir` in `CoConfig`; `skill_registry`, `active_skill_env`, `skill_tool_grants` in `CoSessionState` |
| `co_cli/main.py` | `chat_loop()`: session startup load, env inject/rollback (try/finally), allowed-tools clear, file watcher (mtime poll before each prompt), live completer update |
| `co_cli/_orchestrate.py` | `_check_skill_grant()` — approval bypass for `active_skill_allowed_tools` |
| `tests/test_skills_loader.py` | Functional tests: loader, dispatch, gating, env, security scan, allowed-tools, shell preprocessing, reload, completer update, file watcher, upgrade flow |
