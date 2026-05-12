# Co CLI — Skill Surface

> Sibling surfaces: [memory.md](memory.md) (declarative state) · [personality.md](personality.md) (doctrine) · [tools.md](tools.md) (capability registration). Bootstrap (when skills load): [bootstrap.md](bootstrap.md). Per-turn skill-env lifecycle: [core-loop.md](core-loop.md).

Skills are procedural capability — name-addressable workflows that shape **how** the agent approaches a recurring task. They are distinct from memory (declarative state), tools (callable primitives), and personality (a priori identity). Skills get their own surface because procedural discipline benefits from pre-action treatment; they are not just-another-recall-result.

This doc owns the skill tier — the storage model (markdown skill files), the slash-command dispatch path, and the three model-callable tools (`skill_search`, `skill_view`, `skill_manage`). Bundled skills are also declared in the static system prompt via the bundled skill manifest; user-installed skills are discoverable on-demand via `skill_search`.

## 1. What & How

Skills in `co-cli` are prompt overlays loaded from markdown files and exposed through slash commands. A skill does not register a new tool. Instead, it expands into an agent-body string that is fed back into the main agent for a normal LLM turn.

```mermaid
flowchart LR
    SkillFile[skill .md file] --> Loader[load_skills]
    Loader --> Registry[deps.skill_commands + get_skill_registry()]
    Registry --> Dispatch[name args]
    Dispatch --> AgentBody[expanded skill body]
    AgentBody --> MainLoop[main.py]
    MainLoop --> MainAgent[main Agent]
    MainAgent --> Tools[existing tools]
```

## 2. Core Logic

### Skill Model

The in-memory shape is `SkillConfig` in `co_cli/skills/skill_types.py`.

| Field | Purpose |
| --- | --- |
| `name` | slash-command name derived from file stem |
| `description` | listing text shown in `/skills` and exposed via `get_skill_registry()` |
| `body` | prompt body injected into the main agent on dispatch |
| `argument_hint` | UI hint for `/help` and `/skills` |
| `user_invocable` | whether the skill appears as a slash command |
| `disable_model_invocation` | hide from model-facing skill search results |
| `requires` | environment/platform/settings gates |
| `skill_env` | env vars injected only for the duration of the dispatched turn |

### File Format

Skills are markdown files parsed with `parse_frontmatter()` from `co_cli/memory/frontmatter.py`.

Supported frontmatter fields parsed from the skill file:

| Field | Purpose |
| --- | --- |
| `description` | human-readable summary |
| `argument-hint` | argument usage hint |
| `user-invocable` | include in slash-command completer and `/help` |
| `disable-model-invocation` | hide from `get_skill_registry()` output and skills channel search results |
| `requires` | gate loading on bins, anyBins, env, os, or settings |
| `skill-env` | turn-scoped env injection, filtered through a blocked-key list |
| `source-url` | installation provenance; read at upgrade time by `_upgrade_skill()`, not stored in `SkillConfig` |

The skill name is always the filename stem. Built-in slash commands are reserved names and cannot be shadowed.

### Load Order

Skills are loaded in two passes, lowest-priority first:

1. **bundled** — package defaults from `co_cli/skills/*.md` (version-controlled; no runtime security scan)
2. **user-global** — `~/.co-cli/skills/*.md` (from `deps.user_skills_dir`; security scan applied)

User-global skills override bundled skills on name collision.

`_load_skill_file(path, root, scan)` is the per-file loader. The `root` parameter is the load root used for containment checking. `scan=False` is passed for the bundled pass (version-controlled, no runtime scan needed); `scan=True` is passed for the user-global pass.

Loading happens at startup inside `create_deps()` (in `bootstrap/core.py`) as part of deps assembly:

1. load bundled, then user-global skills
2. `skill_commands` passed into `CoDeps` constructor
3. `completer.words = _build_completer_words(deps.skill_commands)` — called in `main.py` immediately after `create_deps()` returns

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

### Containment Check

`_is_safe_skill_path(path, root)` is called for every file before it is loaded during the user-global pass. It resolves symlinks and verifies the resolved path is still inside `root`. If a symlink points outside the load root, the file is skipped and a `logger.warning` is emitted. Bundled skills are version-controlled and not subject to this check.

### Security Scan

Skill content is scanned by `scan_skill_content()` using static regex checks before or during load. Current warning classes include:

1. credential exfiltration patterns
2. curl or wget piped into shell
3. destructive shell fragments
4. prompt-injection style text

Behavior differs by path:

| Path | Behavior |
| --- | --- |
| startup / reload load path | warning only; file may still load |
| `skill_manage(action='install')` | warnings are shown and require explicit user confirmation |

`skill-env` is additionally filtered through `_SKILL_ENV_BLOCKED`, which prevents overriding critical process variables such as `PATH`, `PYTHONPATH`, `HOME`, and shell-loader variables.

### Registry

There are two skill registries:

| Registry | Purpose |
| --- | --- |
| `deps.skill_commands` | full loaded skill set used by slash-command dispatch |
| `get_skill_registry(deps.skill_commands)` | model-facing list of visible skills (name + description); excludes entries with `disable_model_invocation=True` or blank descriptions |

`set_skill_commands()` replaces `deps.skill_commands`. The model-facing skill registry is derived on read via `get_skill_registry()`, which excludes hidden skills by filtering out entries with `disable_model_invocation=True` or blank descriptions.

### Dispatch

Slash-command routing lives in `dispatch(raw_input, ctx)`.

Dispatch order:

1. built-in commands in `BUILTIN_COMMANDS`
2. skills in `ctx.deps.skill_commands`
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
| `/skills check` | compare available files vs actually loaded skills across both tiers and report skip reasons |
| `/skills install <path|url>` | copy skill into user skills dir and reload (CLI parity with `skill_manage(action='install')`; see §3 above) |
| `/skills reload` | rescan the user-global skill directory and reload into the live session |
| `/skills upgrade <name>` | reinstall from stored `source-url` |

`/skills reload` rescans only the user-global directory; bundled skills are version-controlled and not rescanned at runtime. `/skills check` covers both tiers (bundled and user-global).

Installed skills are written to `~/.co-cli/skills/`.

## 3. Model-Callable Surface

The skill tier has three model-callable tools: ranked discovery (`skill_search`), a name-addressable reader (`skill_view`), and a write surface (`skill_manage`). Bundled skills are additionally declared in the static system prompt via the bundled skill manifest (see §3.5).

### `skill_manage(action, name, ...)`

Single write entry point for the skills channel. Lives at `co_cli/tools/system/skills.py`.

| Action | Behaviour |
| --- | --- |
| `create` | Write new `<name>.md` to `user_skills_dir`; reject if already exists; validate frontmatter (`description` required, ≤ 1 024 chars); run security scan; rollback (delete) on flag; reload. |
| `edit` | Full rewrite of an existing user-installed skill; validate + scan + rollback on flag; reload. |
| `patch` | Find-and-replace within a skill body; `replace_all=False` enforces exactly one match; scan + rollback on flag; reload. |
| `delete` | Remove user-installed skill; reload; returns `shadowed_bundled=true` when a bundled skill of the same name becomes active. |
| `install` | Fetch skill content from a path or URL; copy into `user_skills_dir`; security scan with rollback on flag; reload. |

**Bundled-skill protection.** `edit`, `patch`, and `delete` reject any name that exists only in the bundled directory with a "copy to `~/.co-cli/skills/` first" error.

**Security scan.** After every successful write, `scan_skill_content()` is run on the full file text. If any pattern fires (credential exfil, pipe-to-shell, destructive shell, prompt injection), the write is rolled back and `tool_error` is returned listing the matched pattern names.

**Reload on success.** After any successful write, `refresh_skills(deps)` re-loads skill files, replaces `deps.skill_commands`, and re-upserts the skills source in `chunks_fts` so the new/changed skill is immediately dispatchable and searchable in the same session.

**Approval.** `skill_manage` is `approval=True`. The approval subject is scoped per-action and per-skill:

| Action | Approval subject |
| --- | --- |
| create / edit / patch / delete | `tool:skill_manage:<action>:<name>` |
| install (URL source) | `tool:skill_manage:install:url:<host>` |
| install (local file source) | `tool:skill_manage:install:localfile` |

### `skill_view(name, file_path=None)`

Returns a skill's full SKILL.md body. Plugin-qualified names (`plugin:skill`) are accepted; the prefix is stripped and the bare name used. `spill_threshold_chars=inf` so the body always lands inline regardless of size. `file_path` always returns `tool_error` (flat-file model; no linked files).

Registered in `co_cli/tools/system/skills.py`.

### `skill_search(query, limit=5)`

Ranked discovery over the skill index by name and description. Lives at `co_cli/tools/system/skills.py`.

- **Indexed content**: name + description only. Bodies are not indexed in FTS5; load via `skill_view(name)` after a hit.
- **Storage**: backed by `SkillIndex` in `co_cli/skills/index.py` — an FTS5 index over `source='skill'` in the shared `co-cli-search.db`. Separate API from `MemoryStore` (the boundary is by API, not by storage).
- **Empty query**: rejected with `tool_error` — browse the bundled skills via the manifest in the static prompt; discover user-installed ones via a targeted keyword query.
- **Approval-free**: `is_read_only=True`, `is_concurrent_safe=True`. No approval.
- **Description**: returned via `skill_commands` lookup with FTS-row fallback, so descriptions are always current even right after a write.

Result shape:

```python
{
    "name": <skill name>,
    "description": <skill description>,
    "score": <BM25>,
    "path": <absolute path to the skill .md>,
}
```

### Bundled skill manifest (static prompt injection)

Bundled skills (`co_cli/skills/*.md` not shadowed in `~/.co-cli/skills/`) are declared in the static system prompt as an `<available_skills>` XML block:

```
<available_skills>
  <skill name="doctor" description="Diagnose problems in the current repo." />
  ...
</available_skills>
```

Rendered by `co_cli/context/manifests/skill_manifest.py:render_skill_manifest(skill_commands, skills_dir, user_skills_dir)` and injected by `build_agent()` after the tool guidance, before personality content. The manifest is bundled-only — user-installed and dynamically-created skills are surfaced via `skill_search` (the long tail stays out of the cacheable prefix).

### Indexer hook

`refresh_skills(deps)` is the single entry point for re-indexing skills. It:

1. Re-loads skill files from bundled and user-global directories.
2. Replaces `deps.skill_commands`.
3. Calls `deps.skill_index.upsert(name, description, path)` for every loaded skill.
4. Removes stale entries via `deps.skill_index.remove(name)` for names that disappeared.

It is invoked on every `skill_manage(action=...)` write, on `/skills reload`, and at bootstrap (via the direct upsert loop in `create_deps()` Step 7c, before `CoDeps` is assembled).

## 4. Config

The skill system is lightly configured. The main runtime dependencies are the resolved skill paths on `CoDeps`.

| Setting | Source | Purpose |
| --- | --- | --- |
| `deps.skills_dir` | package directory `co_cli/skills/` | bundled skills directory (lowest priority) |
| `deps.user_skills_dir` | `~/.co-cli/skills/` | user-global skill directory (overrides bundled) |
| `settings` values referenced by `requires.settings` | `co_cli/config/` | load gating only |

There is no separate skills config object today.

## 5. Files

| File | Purpose |
| --- | --- |
| `co_cli/skills/skill_types.py` | `SkillConfig` frozen dataclass |
| `co_cli/skills/index.py` | `SkillIndex` — FTS5 index over name+description (same DB as MemoryStore, separate API); `SkillHit` dataclass |
| `co_cli/skills/loader.py` | `load_skills`, `_load_skill_file`, `_is_safe_skill_path`, `scan_skill_content`, `_check_requires` |
| `co_cli/skills/installer.py` | `fetch_skill_content`, `write_skill_file`, `discover_skill_files`, `find_skill_source_url`, `read_skill_meta` |
| `co_cli/skills/registry.py` | `set_skill_commands()` — replaces `deps.skill_commands`; `get_skill_registry()` — derives model-facing list |
| `co_cli/skills/lifecycle.py` | skill load, install, upgrade, reload orchestration |
| `co_cli/context/manifests/skill_manifest.py` | `render_skill_manifest()` — renders the `<available_skills>` block injected into the static system prompt |
| `co_cli/commands/core.py` | `dispatch` and `BUILTIN_COMMANDS` registrations |
| `co_cli/commands/skills.py` | `/skills` command family (list/check/install/reload/upgrade) |
| `co_cli/commands/registry.py` | `BUILTIN_COMMANDS` dict, `SlashCommand` dataclass, `filter_namespace_conflicts`, `_build_completer_words` |
| `co_cli/bootstrap/core.py` | `create_deps()` — MCP discovery, skill loading, SkillIndex construction, and knowledge store init at startup |
| `co_cli/main.py` | per-turn skill-env lifecycle, live skill reload, skill manifest injection at agent construction |
| `co_cli/deps.py` | `skills_dir`, `user_skills_dir` (workspace paths on CoDeps); `skill_commands` and `skill_index` (top-level); `active_skill_name` (runtime) |
| `co_cli/memory/frontmatter.py` | markdown frontmatter parsing used by skill loader |
| `co_cli/tools/system/skills.py` | `skill_search`, `skill_view`, `skill_manage` — model-callable surface for the skill tier |
| `co_cli/skills/` | package-default shipped skills |
| `~/.co-cli/skills/` | user-global skill files; override bundled skills on name collision |
| `docs/specs/memory.md` | sibling surface — declarative memory (session + knowledge) |
| `docs/specs/personality.md` | sibling surface — doctrine (canon, soul seed, mindsets) |
| `docs/specs/bootstrap.md` | when skills load during startup |
| `docs/specs/core-loop.md` | how dispatched skill bodies flow through a normal turn |
| `docs/specs/tools.md` | callable tool capabilities used by skills after dispatch |

## 6. Authoring Contract

*Placeholder — to be added in Plan 2.*

## 7. Lint Rules

*Placeholder — to be added in Plan 2.*
