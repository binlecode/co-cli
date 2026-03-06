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

### Loader

```
_load_skills(skills_dir, settings):
  pass 1: scan co_cli/skills/*.md   (package-default, sorted)
  pass 2: scan skills_dir/*.md      (project-local, overrides by name)
  each: _load_skill_file(path, result, reserved, settings)

_load_skill_file(path, ...):
  name = path.stem
  parse YAML frontmatter → meta, body
  reject if name in reserved (built-in commands)
  _check_requires(name, requires, settings) → skip if fails
  _scan_skill_content(text) → logger.warning per finding
  extract skill_env, filter _SKILL_ENV_BLOCKED
  extract allowed_tools (list guard: non-list → [])
  result[name] = SkillCommand(...)

_check_requires(name, requires, settings):
  bins: all must be on PATH
  anyBins: at least one must be on PATH
  env: all env vars must be set
  os: sys.platform must match
  settings: named Settings fields must be non-None/empty

_diagnose_requires_failures(requires, settings):
  same logic as _check_requires, returns list[str] of human-readable failure strings
```

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

### Dispatch pipeline (dispatch() in _commands.py)

```
1. ctx.deps.active_skill_env = dict(skill.skill_env)  [set before body processing]
2. ctx.deps.active_skill_allowed_tools = set(skill.allowed_tools)  [cleared after run_turn]
3. arg substitution:
   if $ARGUMENTS in body: replace $ARGUMENTS, $0, $1...$N
   else: append args after body
4. _preprocess_shell_blocks(body): evaluate !`cmd` blocks
5. ctx.skill_body = body  [chat_loop uses this as LLM user input]

_preprocess_shell_blocks(body, max_blocks=3, timeout=5.0):
  regex: !`([^`\n]+)`
  evaluate up to max_blocks matches via asyncio.create_subprocess_shell (/bin/sh)
  on error/timeout: replace with "", logger.warning
  blocks beyond cap: left unreplaced
```

### Env injection / rollback (chat_loop in main.py)

```
before run_turn:
  _saved_env = {k: os.environ.get(k) for k in deps.active_skill_env}
  os.environ.update(deps.active_skill_env)
try/finally (always — both clears guaranteed on all exit paths including exceptions):
  restore each key from _saved_env (set or pop)
  deps.active_skill_env.clear()
  deps.active_skill_allowed_tools.clear()
```

### Allowed-tools approval bypass (_orchestrate.py)

```
_check_skill_grant(tool_name, deps) → bool:
  return tool_name in deps.active_skill_allowed_tools

In _handle_approvals(): called before the user-prompt tier.
If returns True: auto-approve, continue (no prompt shown).
```

### Security scanner

```
_scan_skill_content(content) → list[str]:
  four patterns (credential_exfil, pipe_to_shell, destructive_shell, prompt_injection)
  returns list of tagged warning strings: "[tag] line N: <line>"

Called at:
  load time — logger.warning per finding (developer asset, warning-only)
  install time — console.print, user must confirm before install proceeds (blocking)
  reload time — console.print for loaded skills only (warning-only, user-visible)
```

### Autocompleter live update

`CommandContext` carries a `completer: Any = None` field (typed `Any` — design boundary keeps `_commands.py` free of `prompt_toolkit` imports). `chat_loop()` passes `completer=completer` when constructing each `CommandContext` for a slash-command turn.

```
_build_completer_words() → list[str]:
  [f"/{name}" for name in COMMANDS]
  + [f"/{name}" for name, s in SKILL_COMMANDS.items() if s.user_invocable]
  # single source of truth; called by both _refresh_completer and the file watcher

_refresh_completer(ctx):
  if ctx.completer is None: return  (no-op outside REPL)
  ctx.completer.words = _build_completer_words()
```

`_refresh_completer(ctx)` is called at the end of the `reload` and `install` branches so new skill slash-names appear in tab-completion immediately.

### File watcher / auto-reload

Zero-dependency polling via `Path.stat().st_mtime`. No background task needed — check runs in microseconds.

```
_skills_snapshot(skills_dir) → dict[str, float]:
  if not skills_dir.exists(): return {}
  return {str(p): mtime for p in sorted(skills_dir.glob("*.md"))}
```

`chat_loop()` captures `_skills_watch_snapshot` after startup load. At the top of each `while True:` iteration, before `session.prompt_async()`, it compares a fresh snapshot. On mismatch: reloads `SKILL_COMMANDS`, updates `deps.skill_registry`, calls `completer.words = _build_completer_words()`, prints `[dim]Skills reloaded (files changed).[/dim]`.

### Source-URL injection and upgrade flow

When a skill is installed from a URL, `_inject_source_url(content, url)` embeds the URL in the frontmatter before writing the file:
- No frontmatter → prepend `---\nsource-url: <url>\n---\n`
- Frontmatter without `source-url` → insert field before closing `---`
- Existing `source-url` → replace value in place

`_install_skill` accepts `force: bool = False`. When `force=True`, the overwrite-confirmation prompt is skipped (intent is unambiguous on upgrade).

`_upgrade_skill(ctx, name)`:
1. Check `name in SKILL_COMMANDS` — error if not found
2. Check `skills_dir / f"{name}.md"` exists — error if not
3. Parse `source-url` from frontmatter — error if absent ("not installed from a URL")
4. `await _install_skill(ctx, source_url, force=True)`

### /skills subcommands (_cmd_skills)

```
list (default): Rich table of SKILL_COMMANDS — name, description, requires keys, user-invocable

check: scans both skill dirs for *.md files; for each:
  if in SKILL_COMMANDS: "✓ Loaded"
  else: parse frontmatter, _diagnose_requires_failures → show reason

install <path|url>:
  fetch content (local path read directly; URL via httpx, text/* content-type validated)
  URL installs: _inject_source_url(content, url) → embed source-url in frontmatter
  _scan_skill_content → if warnings, user must confirm
  confirm overwrite if file exists (skipped when force=True)
  write to skills_dir / filename
  reload: _load_skills → SKILL_COMMANDS.clear() + update, deps.skill_registry update
  _refresh_completer(ctx) → live completer update

reload:
  _load_skills(skills_dir, settings)
  scan loaded files user-visibly (only p.stem in new_skills, avoiding requires-gated false positives)
  SKILL_COMMANDS.clear() + update
  deps.skill_registry update
  _refresh_completer(ctx) → live completer update

upgrade <name>:
  _upgrade_skill(ctx, name) → re-fetch from stored source-url, force reinstall
```

## 3. Config

| Setting | How set | Value |
|---------|---------|-------|
| Project-local skills dir | `deps.skills_dir` set in `chat_loop()` | `Path.cwd() / ".co-cli" / "skills"` |
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
| `co_cli/deps.py` | `CoDeps`: `skills_dir`, `skill_registry`, `active_skill_env`, `active_skill_allowed_tools` |
| `co_cli/main.py` | `chat_loop()`: session startup load, env inject/rollback (try/finally), allowed-tools clear, file watcher (mtime poll before each prompt), live completer update |
| `co_cli/_orchestrate.py` | `_check_skill_grant()` — approval bypass for `active_skill_allowed_tools` |
| `tests/test_skills_loader.py` | Functional tests: loader, dispatch, gating, env, security scan, allowed-tools, shell preprocessing, reload, completer update, file watcher, upgrade flow |
