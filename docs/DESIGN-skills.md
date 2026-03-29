# Co CLI Skills Design

This doc owns the skill system: markdown skill files, frontmatter parsing, load order, capability gating, security scanning, slash-command dispatch, argument substitution, and skill-env injection. It does not own callable tools or turn execution internals.

## 1. What & How

Skills in `co-cli` are prompt overlays loaded from markdown files and exposed through slash commands. A skill does not register a new tool. Instead, it expands into an agent-body string that is fed back into the main agent for a normal LLM turn.

```mermaid
flowchart LR
    SkillFile[skill .md file] --> Loader[_load_skills]
    Loader --> Registry[session.skill_commands + session.skill_registry]
    Registry --> Dispatch[name args]
    Dispatch --> AgentBody[expanded skill body]
    AgentBody --> MainLoop[main.py]
    MainLoop --> MainAgent[main Agent]
    MainAgent --> Tools[existing tools]
```

## 2. Core Logic

### Skill Model

The in-memory shape is `SkillConfig` in `co_cli/commands/_skill_types.py`.

| Field | Purpose |
| --- | --- |
| `name` | slash-command name derived from file stem |
| `description` | listing text shown in `/skills` and exposed in `session.skill_registry` |
| `body` | prompt body injected into the main agent on dispatch |
| `argument_hint` | UI hint for `/help` and `/skills` |
| `user_invocable` | whether the skill appears as a slash command |
| `disable_model_invocation` | hide from model-facing `skill_registry` |
| `requires` | environment/platform/settings gates |
| `skill_env` | env vars injected only for the duration of the dispatched turn |

### File Format

Skills are markdown files parsed with `parse_frontmatter()` from `co_cli/knowledge/_frontmatter.py`.

Supported frontmatter fields parsed from the skill file:

| Field | Purpose |
| --- | --- |
| `description` | human-readable summary |
| `argument-hint` | argument usage hint |
| `user-invocable` | include in slash-command completer and `/help` |
| `disable-model-invocation` | hide from `session.skill_registry` |
| `requires` | gate loading on bins, anyBins, env, os, or settings |
| `skill-env` | turn-scoped env injection, filtered through a blocked-key list |
| `source-url` | installation provenance; read at upgrade time by `_upgrade_skill()`, not stored in `SkillConfig` |

The skill name is always the filename stem. Built-in slash commands are reserved names and cannot be shadowed.

### Load Order

Skills are loaded by `_load_skills(skills_dir, settings)` in two passes:

1. package defaults from `co_cli/skills/*.md`
2. project-local overrides from `<cwd>/.co-cli/skills/*.md`

Project-local files win on name collision. Loading happens at startup inside `initialize_session_capabilities()` (in `bootstrap/_bootstrap.py`) after MCP initialization and before the welcome banner:

1. `_load_skills(deps.config.skills_dir, settings)`
2. `set_skill_commands(skill_commands, deps.session)`
3. `completer.words = _build_completer_words(deps.session.skill_commands)` — called in `main.py` immediately after `initialize_session_capabilities()` returns

### Load Gating

The `requires` block is evaluated by `_check_requires()` before a skill enters the registry.

Current gates:

| Key | Rule |
| --- | --- |
| `bins` | all listed binaries must exist on `PATH` |
| `anyBins` | at least one listed binary must exist |
| `env` | all listed environment variables must be set |
| `os` | `sys.platform` must match one of the prefixes |
| `settings` | named settings fields must be present and truthy |

Skills that fail a gate are skipped, not loaded in a degraded state.

### Security Scan

Skill content is scanned by `_scan_skill_content()` using static regex checks before or during load. Current warning classes include:

1. credential exfiltration patterns
2. curl or wget piped into shell
3. destructive shell fragments
4. prompt-injection style text

Behavior differs by path:

| Path | Behavior |
| --- | --- |
| startup / reload load path | warning only; file may still load |
| `/skills install` | warnings are shown and require explicit user confirmation |

`skill-env` is additionally filtered through `_SKILL_ENV_BLOCKED`, which prevents overriding critical process variables such as `PATH`, `PYTHONPATH`, `HOME`, and shell-loader variables.

### Registry

There are two skill registries:

| Registry | Purpose |
| --- | --- |
| `deps.session.skill_commands` | full loaded skill set used by slash-command dispatch |
| `deps.session.skill_registry` | model-facing list of visible skills with name and description only |

`set_skill_commands()` updates both. `session.skill_registry` excludes hidden skills by filtering out entries with `disable_model_invocation=True` or blank descriptions.

### Dispatch

Slash-command routing lives in `dispatch(raw_input, ctx)`.

Dispatch order:

1. built-in commands in `BUILTIN_COMMANDS`
2. skills in `ctx.deps.session.skill_commands`
3. unknown command error

When a skill matches:

1. the skill body is copied into `delegated_input`
2. argument placeholders are expanded
3. `DelegateToAgent(delegated_input, skill_env, skill_name)` is returned

`main.py` sets `deps.runtime.active_skill_name = outcome.skill_name` after receiving `DelegateToAgent`, before entering `run_turn()`.

The main chat loop receives a `DelegateToAgent` outcome, injects skill env, and runs a normal LLM turn with `delegated_input`. Skills do not bypass the agent loop, approval system, or tool contracts.

### Argument Expansion

The dispatch path supports simple positional substitution when arguments are supplied:

| Token | Replacement |
| --- | --- |
| `$ARGUMENTS` | raw argument string |
| `$0` | skill name |
| `$1`, `$2`, ... | positional whitespace-split arguments |

If no arguments are passed, the body is used as-is.

### Skill Env Lifecycle

Skill env injection is managed in `main.py`, not in the skill loader.

For a dispatched skill turn:

1. `deps.runtime.active_skill_name` is set from `outcome.skill_name`
2. current values for the selected env keys are saved
3. `os.environ` is updated from `outcome.skill_env`
4. `run_turn()` executes
5. a `finally` block restores previous env values and clears `active_skill_name`

This guarantees rollback on success, interruption, or exception.

### Skill Management Commands

The built-in `/skills` command family is implemented in `_cmd_skills()` and related helpers.

| Command | Purpose |
| --- | --- |
| `/skills list` | show loaded skills |
| `/skills check` | compare available files vs actually loaded skills and report skip reasons |
| `/skills install <path|url>` | copy skill into project skills dir and reload |
| `/skills reload` | reload package and project skill files into the live session |
| `/skills upgrade <name>` | reinstall from stored `source-url` |

Installed skills are written to `<cwd>/.co-cli/skills/`.

## 3. Config

The skill system is lightly configured. The main runtime dependency is the resolved skills path in `CoConfig`.

| Setting | Source | Purpose |
| --- | --- | --- |
| `skills_dir` | resolved in `CoConfig.from_settings()` as `<cwd>/.co-cli/skills` | project-local skill directory |
| `settings` values referenced by `requires.settings` | `co_cli/config.py` | load gating only |

There is no separate skills config object today.

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/commands/_skill_types.py` | `SkillConfig` frozen dataclass |
| `co_cli/commands/_commands.py` | skill loader, scanner, dispatch, and `/skills` commands |
| `co_cli/bootstrap/_bootstrap.py` | `initialize_session_capabilities()` — MCP discovery and skill loading at startup |
| `co_cli/main.py` | per-turn skill-env lifecycle and live skill reload |
| `co_cli/deps.py` | `skills_dir` (config); `skill_commands`, `skill_registry` (session); `active_skill_name` (runtime) |
| `co_cli/knowledge/_frontmatter.py` | markdown frontmatter parsing used by skill loader |
| `co_cli/skills/` | package-default shipped skills |
| `<cwd>/.co-cli/skills/` | project-local skill files and overrides |
| `docs/DESIGN-bootstrap.md` | when skills load during startup |
| `docs/DESIGN-core-loop.md` | how dispatched skill bodies flow through a normal turn |
| `docs/DESIGN-tools.md` | callable tool capabilities used by skills after dispatch |
