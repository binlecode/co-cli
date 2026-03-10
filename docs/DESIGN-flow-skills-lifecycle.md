# Flow: Skills Lifecycle

End-to-end lifecycle for the skills subsystem — from startup load through dispatch execution,
env injection, allowed-tools grants, file watcher auto-reload, and the install/upgrade/security
scan flows.

```mermaid
flowchart TD
    subgraph Startup["Startup Load"]
        L1[_load_skills called] --> L2[Pass 1: co_cli/skills/*.md package-default]
        L2 --> L3[Pass 2: .co-cli/skills/*.md project-local]
        L3 --> L4{Name collision?}
        L4 -->|Yes| L5[project-local overrides package-default]
        L4 -->|No| L6[SKILL_COMMANDS dict updated]
        L5 --> L6
        L6 --> L7[skill_registry fed to add_available_skills layer]
    end

    subgraph Dispatch["Dispatch Pipeline"]
        D1[user /skill-name args] --> D2[Stage skill_env into deps.session.active_skill_env]
        D2 --> D3[Stage allowed_tools into deps.session.skill_tool_grants]
        D3 --> D4[Arg substitution: ARGUMENTS / $0 / $N]
        D4 --> D5[Shell block preprocessing: eval backtick blocks]
        D5 --> D6[Set ctx.skill_body — return to chat_loop]
        D6 --> D7[chat_loop: inject os.environ → run_turn → restore finally]
        D7 --> D8[grants + env cleared in finally block]
    end

    subgraph SideFlows["Side Flows"]
        F1[file watcher: mtime poll before each REPL prompt] -->|change detected| F2[_load_skills reload]
        F2 --> F3[SKILL_COMMANDS + completer refreshed]
        I1[/skills install URL or path] --> I2[fetch content]
        I2 --> I3[security scan — blocking confirm if warnings found]
        I3 -->|confirmed| I4[write file to skills_dir]
        I4 --> I5[reload + completer refresh]
    end
```

## Entry Conditions

- Session startup: `chat_loop()` is initializing.
- `deps.config.skills_dir` is set to `Path.cwd() / ".co-cli" / "skills"` (project-local).
- Package-default skills dir is `co_cli/skills/` (relative to package root).
- `SKILL_COMMANDS` dict is empty (module-level, populated at load time).
- `deps.session.skill_registry` is empty (populated after load).

---

## Part 1: Startup Load

`_load_skills()` performs a two-pass scan at session startup and on every reload.

### Two-pass load sequence

```
_load_skills(skills_dir: Path, settings: Settings) → dict[str, SkillCommand]:

PASS 1 — Package-default skills:
  scan co_cli/skills/*.md (sorted by filename)
  for each .md file:
    _load_skill_file(path, result, reserved, settings)

PASS 2 — Project-local skills:
  if skills_dir.exists():
    scan skills_dir/*.md (sorted by filename)
    for each .md file:
      _load_skill_file(path, result, reserved, settings)
      (if same name already in result from Pass 1: overrides it — project-local wins)

return result dict (name → SkillCommand)
```

Name collision resolution: project-local files override package-default on exact name match.
No warning is emitted — override is intentional extension point.

### Per-file load (_load_skill_file)

```
_load_skill_file(path, result, reserved, settings):
  name = path.stem
  if name in reserved (built-in slash commands): skip silently
  parse YAML frontmatter → meta dict + body string
  _check_requires(name, meta.requires, settings):
    bins: all listed binaries must be on PATH (shutil.which)
    anyBins: at least one listed binary must be on PATH
    env: all listed env vars must be non-empty in os.environ
    os: sys.platform must match listed value (e.g. "darwin", "linux")
    settings: named Settings fields must be non-None and non-empty
    any gate fails → skip file (not added to result)
  _scan_skill_content(body + frontmatter_str):
    check four patterns: credential_exfil, pipe_to_shell, destructive_shell, prompt_injection
    findings → logger.warning per finding (load-time: developer-facing only)
  extract skill_env from meta:
    filter out blocked env vars (PATH, PYTHONPATH, PYTHONHOME, LD_PRELOAD,
                                  LD_LIBRARY_PATH, DYLD_INSERT_LIBRARIES,
                                  HOME, USER, SHELL, SUDO_UID)
  extract allowed_tools from meta:
    if not a list: coerce to [] (guard against scalar YAML values)
  result[name] = SkillCommand(
    name, description, body, argument_hint, user_invocable,
    disable_model_invocation, requires, skill_env, allowed_tools
  )
```

After `_load_skills()` returns:
```
SKILL_COMMANDS.clear()
SKILL_COMMANDS.update(new_skills)
deps.session.skill_registry = [
    {"name": s.name, "description": s.description}
    for s in SKILL_COMMANDS.values()
    if s.description and not s.disable_model_invocation
]
```

`skill_registry` is the list injected into `add_available_skills` per-turn instruction layer.
Skills with `disable_model_invocation: true` are excluded so the LLM does not know they exist.

---

## Part 2: Dispatch Pipeline

When the user types `/skill-name args`, `dispatch()` in `_commands.py` processes the invocation.

### Dispatch sequence

```
dispatch(ctx: CommandContext, name: str, args: str):

STEP 1 — Stage env vars (before body processing):
  ctx.deps.session.active_skill_env = dict(skill.skill_env)
    (copy — mutations to active_skill_env don't affect the SkillCommand)

STEP 2 — Stage allowed-tools grant:
  ctx.deps.session.skill_tool_grants = set(skill.allowed_tools)

STEP 3 — Argument substitution:
  if "$ARGUMENTS" in body:
    replace $ARGUMENTS with full args string
    replace $0 with name, $1..$N with positional args (split by whitespace)
  else:
    if args non-empty: append "\n{args}" after body

STEP 4 — Shell block preprocessing:
  _preprocess_shell_blocks(body, max_blocks=3, timeout=5.0):
    regex: !`([^`\n]+)`
    evaluate up to max_blocks matches via asyncio.create_subprocess_shell (/bin/sh)
    replace !`cmd` with stdout of command
    on error or timeout: replace with "", logger.warning
    blocks beyond max_blocks cap: left unreplaced in body (not evaluated)

STEP 5 — Set skill body for chat_loop:
  ctx.skill_body = body
  (chat_loop reads skill_body as the LLM user input for this turn)
```

`dispatch()` does NOT call `run_turn()` directly — it sets `ctx.skill_body` and returns.
`chat_loop` picks up `skill_body` as the effective user input.

---

## Part 3: Env Injection and Rollback

`chat_loop` performs the actual `os.environ` mutation immediately before `run_turn()`, and
restores it in a `try/finally` block that is guaranteed to run on all exit paths (including
exceptions and interrupts).

### Injection and rollback sequence

```
# In chat_loop, after dispatch sets ctx.skill_body:

_saved_env = {k: os.environ.get(k) for k in deps.session.active_skill_env}
os.environ.update(deps.session.active_skill_env)

try:
  await run_turn(agent, deps, message_history, skill_body, ...)
finally:
  # always runs — both clears guaranteed
  for k, v in _saved_env.items():
    if v is None:
      os.environ.pop(k, None)   (key was not previously set → remove)
    else:
      os.environ[k] = v         (key had a prior value → restore it)
  deps.session.active_skill_env.clear()
  deps.session.skill_tool_grants.clear()
```

The finally block runs even on `KeyboardInterrupt`, ensuring env is always restored and
grants are always cleared after a skill turn. No leaked state between skill invocations.

---

## Part 4: Allowed-Tools Grant

`deps.session.skill_tool_grants` is checked in the approval three-tier chain before the user prompt
tier. Skills can pre-authorize tools for their LLM turn.

### Grant check in _collect_deferred_tool_approvals

```
_check_skill_grant(tool_name: str, deps: CoDeps) → bool:
  if tool_name in deps.session.skill_tool_grants:
    logger.warning("Skill grant bypass: tool=%s active_grants=%s", ...)  # observability
    return True
  return False

In _collect_deferred_tool_approvals() (tier 1, runs before session auto-approve):
  if _check_skill_grant(tool_name, deps):
    auto-approve this call (no user prompt shown)
    continue to next pending call
```

The grant is tool-name exact-match. Wildcards are not supported. The grant is active only for
the duration of the skill's `run_turn()` — cleared by the `finally` block in `chat_loop()` immediately after.

If `allowed-tools` is not set in the skill frontmatter, `deps.session.skill_tool_grants` remains
empty and normal approval rules apply.

---

## Part 5: File Watcher and Auto-Reload

Zero-dependency polling via `Path.stat().st_mtime`. No background thread or task needed.

### Watch and reload sequence

```
# chat_loop initialization:
_skills_watch_snapshot = _skills_snapshot(deps.config.skills_dir)

# top of each while True: iteration, before session.prompt_async():
current_snapshot = _skills_snapshot(deps.config.skills_dir)
if current_snapshot != _skills_watch_snapshot:
  new_skills = _load_skills(deps.config.skills_dir, settings)
  SKILL_COMMANDS.clear()
  SKILL_COMMANDS.update(new_skills)
  deps.session.skill_registry = [{name, description} for non-hidden skills]
  completer.words = _build_completer_words()
  console.print("[dim]Skills reloaded (files changed).[/dim]")
  _skills_watch_snapshot = current_snapshot

_skills_snapshot(skills_dir) → dict[str, float]:
  if not skills_dir.exists(): return {}
  return {str(p): p.stat().st_mtime for p in sorted(skills_dir.glob("*.md"))}
```

Change detection covers new files, deleted files, and modified files (mtime change).
Snapshot comparison runs in microseconds — no I/O cost on unchanged directories.

---

## Part 6: Install Flow

`/skills install <path|url>` adds a new skill file to the project-local skills directory.

### Install sequence

```
_install_skill(ctx, source: str, force: bool = False):

STEP 1 — Fetch content:
  if source is a URL (starts with http:// or https://):
    httpx.get(source)
    validate Content-Type starts with "text/" (reject binary)
    content = response.text
    _inject_source_url(content, source):
      if no frontmatter: prepend "---\nsource-url: {url}\n---\n"
      if frontmatter without source-url: insert field before closing "---"
      if frontmatter has source-url: replace value in place
  else (local path):
    content = Path(source).read_text()
    (no source-url injection for local installs)

STEP 2 — Security scan (blocking):
  _scan_skill_content(content) → list of warning strings
  if warnings:
    print each warning to console
    prompt user: "Install anyway? [y/N]: "
    user enters anything except "y"/"Y" → abort install (no file written)

STEP 3 — Overwrite confirmation:
  if target file (skills_dir / filename) already exists:
    if not force:
      prompt user: "Overwrite {filename}? [y/N]: "
      user enters anything except "y"/"Y" → abort

STEP 4 — Write file:
  skills_dir.mkdir(parents=True, exist_ok=True)
  (skills_dir / filename).write_text(content)

STEP 5 — Reload:
  new_skills = _load_skills(skills_dir, settings)
  SKILL_COMMANDS.clear() + update
  deps.session.skill_registry update
  _refresh_completer(ctx) → completer.words = _build_completer_words()
```

Security scan at install time is blocking — user must explicitly confirm to proceed past warnings.
This differs from load-time (developer warning only) and reload-time (user-visible but not
blocking).

---

## Part 7: Upgrade Flow

`/skills upgrade <name>` re-fetches a URL-installed skill from its stored source URL.

### Upgrade sequence

```
_upgrade_skill(ctx, name):

STEP 1: check name in SKILL_COMMANDS
  not found → error: "Unknown skill: {name}"

STEP 2: check skills_dir / f"{name}.md" exists
  not found → error: "Skill file not found"

STEP 3: parse frontmatter of existing file
  extract source-url field
  absent → error: "Skill '{name}' was not installed from a URL"

STEP 4: call _install_skill(ctx, source_url, force=True)
  force=True: skips overwrite confirmation (intent is unambiguous)
  security scan still runs (content may have changed)
```

Upgrade re-runs the full install flow including security scan on new content. The `force=True`
flag only skips the overwrite confirmation prompt.

---

## Part 8: Reload Flow

`/skills reload` reloads all skills from both source directories.

```
_cmd_skills_reload(ctx):
  new_skills = _load_skills(deps.config.skills_dir, settings)
  scan loaded files (p.stem in new_skills — avoids requires-gated false positives):
    print each successfully loaded skill name
  SKILL_COMMANDS.clear()
  SKILL_COMMANDS.update(new_skills)
  deps.session.skill_registry = [{name, description} for non-hidden skills]
  _refresh_completer(ctx) → completer.words = _build_completer_words()
  security scan findings at reload time: console.print (user-visible, not blocking)
```

---

## Part 9: Security Scanner

`_scan_skill_content(content)` checks for four risk patterns:

| Pattern tag | What it detects |
|-------------|----------------|
| `credential_exfil` | References to credential files combined with network commands (curl/wget + ~/.ssh, ~/.aws, ~/.config) |
| `pipe_to_shell` | Piping remote content directly to shell (curl \| bash, wget \| sh patterns) |
| `destructive_shell` | Dangerous shell operations (rm -rf /, mkfs, dd if=/dev/zero) |
| `prompt_injection` | Instruction override patterns ("ignore previous instructions", "disregard your") |

Returns `list[str]` of tagged warning strings: `"[tag] line N: <line>"`.

| Callsite | Severity | User sees? | Blocking? |
|----------|----------|-----------|-----------|
| Load time | Developer warning | No (logger.warning) | No |
| Install time | User warning | Yes (console.print) | Yes (must confirm) |
| Reload time | User notice | Yes (console.print) | No |

---

## Part 10: Tab-Completer Live Update

`_build_completer_words()` produces the unified list for the REPL completer:

```
_build_completer_words() → list[str]:
  [f"/{name}" for name in COMMANDS]          ← built-in slash commands
  + [f"/{name}" for name, s in SKILL_COMMANDS.items() if s.user_invocable]

_refresh_completer(ctx):
  if ctx.completer is None: return  (no-op outside REPL / in tests)
  ctx.completer.words = _build_completer_words()
```

`_refresh_completer(ctx)` is called at end of `install`, `upgrade`, and `reload` branches so
new skill slash-names appear in tab-completion immediately without restart. The file watcher
auto-reload also calls `completer.words = _build_completer_words()` directly.

`CommandContext.completer` is typed `Any` — this keeps `_commands.py` free of `prompt_toolkit`
imports. `chat_loop` passes `completer=completer` when constructing each `CommandContext`.

---

## Failure Paths

| Failure | Behavior |
|---------|----------|
| `requires` gate fails on load | Skill file skipped silently (not added to `SKILL_COMMANDS`); visible via `/skills check` |
| Shell block in skill body times out or errors | Block replaced with empty string; `logger.warning`; dispatch continues |
| Shell blocks beyond max_blocks cap (3) | Left unreplaced in body (not evaluated) |
| Install from URL: non-text content-type | Error: rejected before writing |
| Install from URL: security scan warnings, user declines | Install aborted; no file written |
| Upgrade: skill has no source-url | Error message; no fetch attempted |
| File watcher: skills_dir does not exist | `_skills_snapshot` returns `{}` (no reload triggered) |
| `_refresh_completer` outside REPL | No-op (ctx.completer is None) |
| Active skill env blocked var | Silently filtered at load time (never staged to `active_skill_env`) |

---

## State Mutations

| Field | When mutated | By whom |
|-------|-------------|---------|
| `SKILL_COMMANDS` (module-level) | Startup load, reload, install, upgrade, file watcher | `_load_skills` via `_commands.py` |
| `deps.session.skill_registry` | Same as SKILL_COMMANDS | `chat_loop` / `_commands.py` |
| `deps.session.active_skill_env` | Set by `dispatch()`, cleared by `finally` in `chat_loop` | `_commands.py` (set), `main.py` (clear) |
| `deps.session.skill_tool_grants` | Set by `dispatch()`, cleared by `finally` in `chat_loop` | `_commands.py` (set), `main.py` (clear) |
| `os.environ` | Updated by `chat_loop` before `run_turn`, restored by `finally` | `main.py` |
| `completer.words` | After install, upgrade, reload, file watcher | `_refresh_completer` / `chat_loop` |
| `_skills_watch_snapshot` | After file watcher detects change | `chat_loop` |

---

## Owning Code

| File | Role |
|------|------|
| `co_cli/_commands.py` | `SkillCommand` model, `_load_skills`, `_load_skill_file`, `dispatch`, `_preprocess_shell_blocks`, `_scan_skill_content`, `_check_requires`, `/skills` subcommand handlers, `_refresh_completer`, `_build_completer_words` |
| `co_cli/skills/` | Package-default skill files (e.g. `doctor.md`) |
| `co_cli/deps.py` | `CoConfig.skills_dir`; `CoSessionState.skill_registry`, `CoSessionState.active_skill_env`, `CoSessionState.skill_tool_grants` |
| `co_cli/main.py` | `chat_loop`: startup load, env inject/rollback `try/finally`, file watcher poll, live completer update |
| `co_cli/_orchestrate.py` | `_check_skill_grant()` — allowed-tools approval bypass |

## See Also

- `docs/DESIGN-skills.md` — authoritative deep spec for skills subsystem
- `docs/DESIGN-flow-approval.md` — three-tier approval chain; how allowed-tools grant bypasses user prompt
