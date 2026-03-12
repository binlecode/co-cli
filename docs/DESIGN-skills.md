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

## 2. Core Logic

### 2.1 Startup load and gating

`_load_skills()` runs a two-pass load:
- pass 1 scans package-default skills in `co_cli/skills/*.md`
- pass 2 scans project-local skills in `.co-cli/skills/*.md`
- exact-name collisions are resolved in favor of project-local files

Each file load:
- skips reserved built-in slash-command names
- parses frontmatter and body
- applies `requires` gates (`bins`, `anyBins`, `env`, `os`, `settings`)
- runs `_scan_skill_content(...)`
- filters blocked env vars from `skill_env`
- coerces invalid `allowed_tools` metadata to `[]`

After load, `SKILL_COMMANDS` is replaced atomically and `deps.session.skill_registry` is rebuilt from non-hidden skills.

### 2.2 SkillCommand model (frozen dataclass)

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

### 2.3 Dispatch, env injection, and grants

Dispatch path:
- `dispatch()` stages `active_skill_env` and `skill_tool_grants`
- performs `$ARGUMENTS` / `$0` / `$N` substitution
- preprocesses shell blocks (`!`-backtick form, capped count and timeout)
- sets `ctx.skill_body` and returns without calling `run_turn()` directly

`chat_loop()` then:
- snapshots the previous values of injected env vars
- mutates `os.environ` immediately before the LLM turn
- restores env vars in `try/finally`
- clears both `active_skill_env` and `skill_tool_grants` in the same `finally`

Allowed-tools grants are exact-match tool-name auto-approvals checked at the first tier of the approval chain for that skill turn only.

### 2.4 File watcher, install, and upgrade flows

Live reload:
- `chat_loop()` polls `.co-cli/skills/` mtimes before each prompt
- on change it reruns `_load_skills()`, rebuilds `skill_registry`, and refreshes the completer

Install flow:
- `/skills install` fetches local or remote content
- remote installs persist `source-url` into frontmatter
- `_scan_skill_content(...)` is blocking at install time: warnings require explicit user confirmation
- successful install writes to `skills_dir`, reloads skills, and refreshes the completer

Upgrade flow:
- `/skills upgrade <name>` re-fetches from the stored `source-url`
- local-only installs without `source-url` are not upgradeable

### 2.5 Security scanner patterns

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
