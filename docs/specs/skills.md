# Co CLI — Skills

> Sibling surfaces: [memory.md](memory.md) · [personality.md](personality.md) · [tools.md](tools.md). Bootstrap (when skills load): [bootstrap.md](bootstrap.md). Per-turn skill-env lifecycle: [core-loop.md](core-loop.md). Dispatch discipline: [06_skill_protocol.md](../../co_cli/context/rules/06_skill_protocol.md).

Skills are procedural capability — name-addressable workflows injected as prompt overlays. They are distinct from memory (declarative state), tools (callable primitives), and personality (doctrine). Dispatching a skill does not register a new tool; it expands a body string into the main agent for a normal LLM turn.

## 1. Functional Architecture

```mermaid
flowchart LR
    SkillFile["skill .md file"] --> Loader["load_skills"]
    Loader --> Registry["deps.skill_index\n+ get_skill_index()"]
    Registry --> Dispatch["name args"]
    Dispatch --> AgentBody["expanded skill body"]
    AgentBody --> MainLoop["main.py"]
    MainLoop --> MainAgent["main Agent"]
    MainAgent --> Tools["existing tools"]
```

### Components

| Component | Role |
|-----------|------|
| `load_skills` | Two-pass loader — bundled then user-global; security scan applied to user-global only |
| `deps.skill_index` | Full skill registry (`dict[str, SkillInfo]`); used by slash-command dispatch |
| `get_skill_index()` | Model-facing subset — excludes hidden skills; source for `<available_skills>` manifest |
| `render_skill_manifest()` | Renders `<available_skills>` XML block; emitted per-turn via `skill_manifest_prompt` so newly created skills are visible on the next turn |
| `dispatch(raw_input, ctx)` | Routes slash commands — built-ins first, then `skill_index`, then error |
| `refresh_skills(deps)` | Hot-reload: re-loads both tiers, replaces `deps.skill_index`; called by the skill write tools and `/skills reload` |
| `skill_create` / `skill_edit` / `skill_patch` / `skill_delete` | Model-callable write tools — one monomorphic tool per operation |
| `skill_view` | Model-callable reader — returns full skill body inline |

### Entry Points

Startup: `create_deps()` in `co_cli/bootstrap/core.py` calls `load_skills()` during deps assembly.

Per-turn: `main.py` saves current env for keys in `skill_env`, calls `os.environ.update(skill_env)`, runs the turn, and restores previous values in a `finally` block.

## 2. Core Logic

### Invocation Paths

Skills are reached through three distinct paths:

**Path 1 — User slash-command.**
The user types `/skill-name [args]` in the REPL. `dispatch()` matches the name in `skill_index`, expands the body (argument substitution), and returns `DelegateToAgent`. The REPL then calls `run_turn()` with the expanded body as the user input — a full new agent turn. The skill body replaces the user's input for that turn; the model never sees the raw `/skill-name` string.

**Path 2 — Model inline use.**
The agent reads the `<available_skills>` manifest injected per-turn into the system prompt, identifies a matching skill, and calls `skill_view(name)` to load the full body. The body is returned as a tool result inside the current turn. The agent reads it and follows its phases as its procedure — no new turn, no dispatch, no REPL involvement. This is the primary path for agent-initiated skill use.

**Path 3 — Model write.**
The agent calls `skill_create`, `skill_edit`, `skill_patch`, or `skill_delete` to write a user skill. Used for drift fixes (stale steps), promoting a reusable procedure to a new skill, or removing an obsolete one. Each tool requires approval, runs the security scan, and calls `refresh_skills(deps)` on success so the change is live immediately.

The dream daemon's domain reviewers also write via the skill write tools (`skill_create`/`skill_edit`/`skill_patch` — not `skill_delete`) and the memory write tools (`memory_create`/`memory_append`/`memory_replace`) — they run in daemon-built `CoDeps` via `build_task_agent` with `requires_approval=False`. They are extensions of Path 3, not separate paths.

### Skill Model

`SkillInfo` in `co_cli/skills/skill_types.py`:

| Field | Purpose |
|-------|---------|
| `name` | slash-command name derived from file stem |
| `description` | listing text shown in `/skills` and exposed via `get_skill_index()` |
| `body` | prompt body injected into the main agent on dispatch |
| `argument_hint` | UI hint for `/help` and `/skills` |
| `user_invocable` | whether the skill appears as a slash command |
| `disable_model_invocation` | hide from `get_skill_index()` results and manifest |
| `skill_env` | env vars injected for the duration of the dispatched turn |
| `path` | absolute path to the `.md` file on disk; `None` for programmatic configs |

### File Format

Markdown files parsed with `parse_frontmatter()` from `co_cli/memory/frontmatter.py`. Skill name is always the filename stem. Built-in slash commands are reserved and cannot be shadowed.

| Frontmatter Field | Purpose |
|-------------------|---------|
| `description` | human-readable summary (required) |
| `argument-hint` | argument usage hint |
| `user-invocable` | include in slash-command completer and `/help` |
| `disable-model-invocation` | hide from `get_skill_index()` and manifest |
| `skill-env` | turn-scoped env injection, filtered through `_SKILL_ENV_BLOCKED` |

### Load Order

```
create_deps()
  ├─ Pass 1: load bundled skills from co_cli/skills/*.md
  │    no security scan (version-controlled)
  └─ Pass 2: load user-global skills from ~/.co-cli/skills/*.md
       security scan applied
       name collision → user-global overrides bundled
```

### Load Safety

**Containment.** For user-global skills, symlinks are rejected outright — only regular files are loaded. Symlink files are skipped with a warning. Bundled skills are not checked.

**Security scan.** `scan_skill_content()` runs static regex checks on every user-global file at startup and on `/skills reload`. Warning classes: credential exfiltration, curl/wget piped to shell, destructive shell fragments, prompt-injection text. Findings are warnings — the file still loads.

`skill-env` is filtered through `_SKILL_ENV_BLOCKED`, which prevents overriding `PATH`, `PYTHONPATH`, `HOME`, and shell-loader variables.

### Dispatch

Skill dispatch is the third branch of `dispatch()` (after built-ins and before unknown-command error). Full routing lives in `tui.md`; this section covers the skill branch only.

```
name matched in ctx.deps.skill_index
  body = skill.body
  if args non-empty AND "$ARGUMENTS" in body:
    args_list = args.split()           # whitespace-split positional args
    body = body.replace("$ARGUMENTS", args)   # raw argument string
    body = body.replace("$0", name)           # skill name
    for i, arg in reversed(enumerate(args_list, 1)):
      body = body.replace(f"${i}", arg)       # $1, $2, ... positional
  # else: body used as-is (no args or no $ARGUMENTS token in body)
  return DelegateToAgent(
    delegated_input=body,
    skill_env=dict(skill.skill_env),   # copy of filtered env vars
    skill_name=skill.name,             # stored as deps.runtime.active_skill_name by caller
  )
```

Positional replacements iterate in reverse order so `$1` does not partially match `$10`, `$11`, etc.

### Argument Expansion

| Token | Replacement | Condition |
|-------|------------|-----------|
| `$ARGUMENTS` | raw argument string (unsplit) | only when args non-empty and token present in body |
| `$0` | skill name | same |
| `$1`, `$2`, ... | whitespace-split positional args | same; missing positionals left as literal `$N` |

If no arguments are passed, or the body contains no `$ARGUMENTS` token, the body is used verbatim.

### Skill Env Lifecycle

```
main.py — per-turn
  saved = {k: os.environ.get(k) for k in skill_env}
  os.environ.update(skill_env)
  try:
    run_turn()
  finally:
    restore saved values (delete keys not previously present)
    clear deps.runtime.active_skill_name
```

### Skill Management Commands

| Command | Purpose |
|---------|---------|
| `/skills list` | show loaded skills |
| `/skills check` | compare available files vs loaded skills across both tiers; report skip reasons |
| `/skills lint [<name>\|--all]` | run R1–R4 advisory lint rules; exit 1 on any finding |
| `/skills reload` | rescan user-global directory and reload into live session |
| `/skills usage [<name>]` | print the per-skill usage sidecar (table for all; full record for one) |
| `/skills pin <name>` | pin an agent-created skill — exempt from dream-daemon skill decay/merge |
| `/skills unpin <name>` | clear the pinned flag |

`/skills reload` rescans only the user-global directory. `/skills check` covers both tiers.

### Authoring Contract

Every skill body has this minimum shape:

```markdown
---
description: <single sentence: when to use this skill, max 1024 chars>
argument-hint: <optional, max 80 chars>
user-invocable: true
---

# <Skill name>

<body — whatever structure fits the skill>
```

Section requirements:

| Section | Required | Notes |
|---------|----------|-------|
| Frontmatter `description` | Yes | ≤1024 chars; drives manifest injection |
| H1 title | Yes | First non-frontmatter heading |
| Body content | Yes | Whatever structure best fits the skill |

Length budget:

| Scope | Limit | Enforcement |
|-------|-------|-------------|
| Frontmatter `description` | ≤1024 chars | Hard — `_validate_skill_content` blocks the write |
| Total content | ≤50,000 chars | Hard — `_validate_skill_content` blocks the write |
| Body | ≤8000 chars | Soft — R4 lint warning ("consider splitting") |

Recommended structure for multi-step procedural skills (template, not requirement):

```markdown
## Phase 1 — <Accomplishment name>
<step-by-step instructions>

## Phase N — <Accomplishment name>
...

## Rules
- <terminal invariant>
```

Phase headers use H2 with integer N, em-dash (` — `), and a name describing what the phase **accomplishes** (e.g. `Phase 1 — Load`, not `Phase 1 — First steps`). Short skills, reference tables, and quick-action skills do not need this structure.

Style: imperative voice (`Run X`, `Check Y`), concrete tool names in backticks, no filler. `## Rules` entries are invariants, not steps.

### Lint Rules

Four advisory rules surfaced by `/skills lint` and attached to `skill_create`/`skill_edit`/`skill_patch` success output as `lint_warnings`. Each finding is `R<n>: <message>`; lint never blocks a write.

| Rule | Check | Why |
|------|-------|-----|
| **R1** | Frontmatter present | Loader rejects files without frontmatter |
| **R2** | `description` present, non-empty, ≤ 1024 chars | Missing description = invisible in manifest; long descriptions bloat manifest and degrade prompt cache hit rates |
| **R3** | H1 title present after frontmatter | Anchors skill identity in `skill_view` output |
| **R4** | Body ≤ 8000 chars (warning) | Long bodies signal overly broad skills that should be split; the hard cap is 50,000 chars |

One additional gate for the shipped reference library only (run from `tests/test_flow_skill_bundled_library.py`):

| Rule | Check | Scope |
|------|-------|-------|
| **B1** | No `TODO`, `FIXME`, or `XXX` markers | `co_cli/skills/*.md` only |

Lint is collaborative — it catches well-meaning skills that won't perform well. The security scan (`scan_skill_content`) is adversarial — it catches actively malicious content. Integrity rules (frontmatter integrity, description present and ≤1024, total content ≤100k) block the write via `_validate_skill_content`; lint never blocks; security scan blocks on findings at write time.

### Curation & Self-Improvement

Three in-session reflexes govern skill quality during a task:

- **Drift fix**: when a loaded skill has stale steps, patch immediately via `skill_patch` for surgical edits or `skill_edit` for structural overhauls.
- **Create**: after completing a multi-step task (3+ coherent steps), if the procedure is class-level reusable, promote it to a skill. Bar: "would I run this again for the same kind of task" — not one-offs.
- **Offer-to-save**: after iterative work where no skill was loaded, briefly offer skill creation before invoking `skill_create`.

**Dream daemon reviewers.** After every `review_memory_nudge_interval` turns (memory domain) or `review_skill_nudge_interval` LLM iterations (skill domain), the REPL writes a KICK file to `$CO_HOME/daemons/dream/queue/` and nudges the dream daemon over a Unix socket. The daemon dequeues work, loads the session transcript up to the queued message count, and runs the appropriate domain reviewer agent via `build_task_agent` with `requires_approval=False`.

```
memory_reviewer (KICK: domain=memory)
  ├─ extract user preferences, rules, and references from transcript
  ├─ create or update memory items for durable user facts
  └─ does not write skills

skill_reviewer (KICK: domain=skill)
  ├─ scan transcript for skill drift, corrections, and new reusable procedures
  ├─ patch or create skills as appropriate
  └─ does not write memory persona items
```

Both reviewer domains are triggered at session end regardless of counter state. KICK files are durable — the daemon picks them up on next start if it was down. Failed reviews (after `max_retry_attempts` timeouts) move to `$CO_HOME/daemons/dream/queue/failed/`.

The skill reviewer reloads skills from disk before its pass so it sees prior writes within the session.

Curation preference order: update a skill loaded in the current session → update an existing umbrella skill → create a new class-level skill only if nothing applicable exists.

**Dream-daemon skill housekeeping.** The dream daemon also runs scheduled-tick `merge_skills` and `decay_skills` phases against `user_skills_dir` — full mechanics in [dream.md §2.5](dream.md). Skill merge clusters similar user-skill bodies (token-Jaccard ≥ `skills.consolidation_similarity_threshold`), picks the highest-recall canonical, and LLM-merges the cluster into the anchor; siblings move to `user_skills_dir/.archive/`. Skill decay archives user skills whose sidecar `created_at` is older than `skills.decay_after_days` AND whose most recent `recall_days` entry is older than `skills.recall_protection_days` (or whose `recall_days` is empty). Pinned skills (`/skills pin <name>`) and skills without a sidecar are exempt. Restoration from `.archive/` is a manual `mv` for now — there is no slash-command surface.

**Usage sidecar.** `~/.co-cli/skills/<name>.usage.json` (one file per agent-created skill) tracks per-skill counters (`use_count`, `view_count`, `patch_count`), timestamps (`created_at`, `last_used_at`, `last_viewed_at`, `last_patched_at`), `pinned`, and `recall_days` (list of ISO-date strings recording which days the skill was recalled — deduped, updated by `skill_view` and `/<skill>` slash dispatch). Sidecar I/O is best-effort: failures are logged and swallowed so tracking never blocks the underlying tool. Populated only for skills in `user_skills_dir` — bundled skills are excluded.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `skills.review_enabled` | `CO_SKILLS_REVIEW_ENABLED` | `false` | Enable dream daemon reviewer KICKs |
| `skills.review_memory_nudge_interval` | `CO_SKILLS_REVIEW_MEMORY_NUDGE_INTERVAL` | `10` | User-turn count between memory-domain KICK triggers |
| `skills.review_skill_nudge_interval` | `CO_SKILLS_REVIEW_SKILL_NUDGE_INTERVAL` | `10` | LLM-iteration count between skill-domain KICK triggers |
| `skills.usage_tracking_enabled` | `CO_SKILLS_USAGE_TRACKING_ENABLED` | `true` | Persist per-skill counters/timestamps/recall_days sidecars |
| `skills.recall_protection_days` | `CO_SKILLS_RECALL_PROTECTION_DAYS` | `30` | Recent-recall window that protects an aged skill from dream-daemon decay |
| `skills.decay_after_days` | `CO_SKILLS_DECAY_AFTER_DAYS` | `90` | Minimum sidecar age before a skill is eligible for dream-daemon decay |
| `skills.consolidation_similarity_threshold` | `CO_SKILLS_CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.75` | Token-Jaccard threshold for skill merge clusters |
| `REVIEW_MAX_ITERATIONS` | — | `8` | Max LLM request budget per reviewer pass (code constant in `co_cli/config/skills.py`) |

### Paths

| Path | Source | Description |
|------|--------|-------------|
| `deps.skills_dir` | package directory `co_cli/skills/` | bundled skills (lowest priority) |
| `deps.user_skills_dir` | `~/.co-cli/skills/` | user-global skills (overrides bundled on name collision) |
| `~/.co-cli/skills/<name>.usage.json` | `co_cli/skills/usage.py` | per-skill usage sidecar (counters, timestamps, `pinned`, `recall_days`) |
| `~/.co-cli/skills/.archive/` | `co_cli/daemons/dream/_housekeeping.py` | archived skills moved here by dream-daemon skill merge/decay |

## 4. Public Interface

### Model-callable tools

The skills channel exposes four monomorphic write tools — one per operation, all in
`co_cli/tools/system/skills.py`, all `approval=True`. `name` is required on every one; an
invalid name (not matching `[a-z0-9_-]+`, ≤64 chars) is rejected before dispatch.

| Tool | Subject | Behaviour |
|------|---------|-----------|
| `skill_create(name, content)` | `tool:skill_create:<name>` | Write new `<name>.md` to `user_skills_dir`; reject if exists; validate frontmatter (`description` required, ≤1024 chars); security scan; rollback on flag; reload. |
| `skill_edit(name, content)` | `tool:skill_edit:<name>` | Full rewrite of an existing user-installed skill; validate + scan + rollback on flag; reload. |
| `skill_patch(name, old_string, new_string, replace_all=False)` | `tool:skill_patch:<name>` | Find-and-replace within a skill body; `replace_all=False` enforces exactly one match; scan + rollback; reload. |
| `skill_delete(name)` | `tool:skill_delete:<name>` | Remove a user-installed skill; reload; returns `shadowed_bundled=true` when a bundled skill of the same name becomes active. |

`skill_edit`, `skill_patch`, and `skill_delete` reject bundled-only skills ("copy to `~/.co-cli/skills/` first"). After every successful write, `refresh_skills(deps)` re-loads and re-indexes so the change is immediately dispatchable.

#### `skill_view(name)`

Returns a skill's full body, addressed by the `filename_stem` from the skill manifest. `spill_threshold_chars=inf` — body always lands inline regardless of size.

### Loader and registry

| Symbol | Source | Contract |
|--------|--------|---------|
| `load_skills(skills_dir, settings, user_skills_dir) -> dict[str, SkillInfo]` | `co_cli/skills/loader.py` | Two-pass loader; security scan on user-global only |
| `refresh_skills(deps) -> None` | `co_cli/skills/lifecycle.py` | Re-loads both tiers; replaces `deps.skill_index` |
| `get_skill_index(skill_index) -> list[dict]` | `co_cli/skills/index.py` | Model-facing list; excludes `disable_model_invocation=True` and blank-description skills |

### Manifest injection

| Symbol | Source | Contract |
|--------|--------|---------|
| `render_skill_manifest(skill_index, skills_dir, user_skills_dir) -> str` | `co_cli/context/manifests/skill_manifest.py` | Renders `<available_skills>` XML block; emitted per-turn via `skill_manifest_prompt` (`co_cli/agent/_instructions.py`) |

### Schema

| Symbol | Source | Contract |
|--------|--------|---------|
| `SkillInfo` | `co_cli/skills/skill_types.py` | Frozen dataclass — `name`, `description`, `body`, `argument_hint`, `user_invocable`, `disable_model_invocation`, `skill_env`, `path` |
| `LintFinding` | `co_cli/skills/_lint.py` | Frozen dataclass — `rule`, `line`, `message` |

## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/skills/skill_types.py` | `SkillInfo` frozen dataclass |
| `co_cli/skills/lint.py` | `lint_skill(content, path)` — R1–R4 advisory validator; `lint_bundled_extras(content)` — B1 no-marker gate; `LintFinding` dataclass |
| `co_cli/skills/loader.py` | `load_skills`, `_load_skill_file`, `scan_skill_content` |
| `co_cli/skills/index.py` | `set_skill_index()`, `get_skill_index()` |
| `co_cli/skills/lifecycle.py` | `refresh_skills`, `discover_skill_files`, `read_skill_meta`, `cleanup_skill_run_state` |
| `co_cli/config/skills.py` | `SkillsSettings` — Pydantic config model |
| `co_cli/context/manifests/skill_manifest.py` | `render_skill_manifest()` |
| `co_cli/commands/core.py` | `dispatch` and `BUILTIN_COMMANDS` registrations |
| `co_cli/commands/skills.py` | `/skills` command family (list/check/lint/reload/usage/pin/unpin) |
| `co_cli/commands/registry.py` | `BUILTIN_COMMANDS` dict, `SlashCommand` dataclass |
| `co_cli/bootstrap/core.py` | `create_deps()` — skill loading at startup |
| `co_cli/main.py` | per-turn skill-env lifecycle, live skill reload; `_post_turn_hook` (two-counter KICK dispatch), `_fire_session_end_kicks` |
| `co_cli/agent/_instructions.py` | `skill_manifest_prompt` — per-turn `@agent.instructions` callback that re-renders the manifest from live `ctx.deps.skill_index` |
| `co_cli/deps.py` | `skills_dir`, `user_skills_dir`, `skill_index`, `active_skill_name` on `CoDeps`; `fork_deps_for_reviewer` |
| `co_cli/memory/frontmatter.py` | markdown frontmatter parsing used by skill loader |
| `co_cli/tools/system/skills.py` | `skill_view`, `skill_create`, `skill_edit`, `skill_patch`, `skill_delete` — all call into `co_cli/skills/usage.py` on success |
| `co_cli/daemons/dream/_reviewer.py` | `MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC`, `process_review()` — daemon domain reviewers |
| `co_cli/daemons/dream/prompts/memory_review.md` | memory reviewer instructions |
| `co_cli/daemons/dream/prompts/skill_review.md` | skill reviewer instructions |
| `co_cli/skills/usage.py` | usage sidecar I/O (`bump_view`, `bump_use`, `bump_patch`, `bump_recall`, `record_create`, `forget`, `set_pinned`) |
| `co_cli/daemons/dream/_housekeeping.py` | `merge_skills` / `decay_skills` — dream-daemon skill housekeeping; see [dream.md §2.5](dream.md) |
| `co_cli/context/rules/06_skill_protocol.md` | dispatch discipline injected into the static system prompt |
| `co_cli/skills/` | package-default shipped skills |

## 6. Test Gates

| Property | Test file |
|----------|-----------|
| All bundled skills load without error | `tests/test_flow_skill_bundled_library.py` |
| All bundled skills pass lint (R1–R4) | `tests/test_flow_skill_bundled_library.py` |
| All bundled skills pass B1 (no TODO/FIXME/XXX markers) | `tests/test_flow_skill_bundled_library.py` |
| Skill manifest renders correct entry count for bundled set | `tests/test_flow_skill_bundled_library.py` |
| /skill-creator dispatches to DelegateToAgent | `tests/test_flow_skill_creator_dispatch.py` |
| skill-creator body references `skill_create` | `tests/test_flow_skill_creator_dispatch.py` |
| R1 fires on missing frontmatter | `tests/test_flow_skill_lint.py` |
| R2 fires on missing, empty, or overlong description | `tests/test_flow_skill_lint.py` |
| R3 fires on missing H1 title | `tests/test_flow_skill_lint.py` |
| R4 fires when body exceeds 8000 chars | `tests/test_flow_skill_lint.py` |
| B1 fires on TODO/FIXME/XXX markers | `tests/test_flow_skill_lint.py` |
| Clean content produces no lint findings | `tests/test_flow_skill_lint.py` |
| skill-write success output includes lint_warnings when content has advisory findings | `tests/test_flow_skills_manage.py` |
| Bundled skill renders as `<skill>` entry in manifest | `tests/test_flow_skill_manifest.py` |
| User-installed skills appear in manifest | `tests/test_flow_skill_manifest.py` |
| User skill shadows bundled skill with its own description | `tests/test_flow_skill_manifest.py` |
| Empty skill set returns empty string (no empty XML block) | `tests/test_flow_skill_manifest.py` |
| XML-special chars in descriptions are escaped in manifest | `tests/test_flow_skill_manifest.py` |
| 06_skill_protocol.md appears in assembled static instructions | `tests/test_flow_skill_protocol.py` |
| skill-creator present in `<available_skills>` manifest | `tests/test_flow_skill_protocol.py` |
| Background review section present in 06_skill_protocol.md | `tests/test_flow_skill_protocol.py` |
| create writes file and skill appears in deps.skill_index | `tests/test_flow_skills_manage.py` |
| create rejects missing description and existing skill | `tests/test_flow_skills_manage.py` |
| create rolls back on destructive shell pattern | `tests/test_flow_skills_manage.py` |
| edit rewrites user skill; rejects bundled-only | `tests/test_flow_skills_manage.py` |
| edit rolls back on security-flagged content | `tests/test_flow_skills_manage.py` |
| patch replaces unique match; errors on zero or multiple matches without replace_all | `tests/test_flow_skills_manage.py` |
| patch replace_all=True replaces all occurrences | `tests/test_flow_skills_manage.py` |
| patch rolls back on security-flagged result | `tests/test_flow_skills_manage.py` |
| delete removes user copy; promotes bundled shadow | `tests/test_flow_skills_manage.py` |
| delete rejects nonexistent and bundled-only skills | `tests/test_flow_skills_manage.py` |
| size_warning emitted when skill count reaches 30 | `tests/test_flow_skills_manage.py` |
| skill_view returns body inline regardless of size | `tests/test_flow_skills_manage.py` |
| skill_view errors on unknown name or hidden skill | `tests/test_flow_skills_manage.py` |
| Dream-daemon skill merge/decay coverage | `tests/daemons/dream/test_skill_housekeeping.py` (see [dream.md §7](dream.md)) |
| usage sidecar read/write roundtrip; returns empty when missing or corrupt | `tests/test_flow_skill_usage.py` |
| write_records is atomic | `tests/test_flow_skill_usage.py` |
| is_agent_created: true for user skill, false for bundled or nonexistent | `tests/test_flow_skill_usage.py` |
| bump_view/bump_use/bump_patch increment counters and set timestamps | `tests/test_flow_skill_usage.py` |
| bump_view skips bundled skills and short-circuits when tracking disabled | `tests/test_flow_skill_usage.py` |
| record_create initializes sidecar entry | `tests/test_flow_skill_usage.py` |
| forget removes sidecar entry; no-op on unknown | `tests/test_flow_skill_usage.py` |
| set_pinned creates stub when no record; toggles existing record | `tests/test_flow_skill_usage.py` |
| bump_view swallows write failures (best-effort) | `tests/test_flow_skill_usage.py` |
| skill_create/skill_view/skill_patch/skill_edit/skill_delete update sidecar counters | `tests/test_flow_skill_usage.py` |
| /skills pin sets pinned flag; /skills unpin clears it | `tests/test_flow_skills_pin.py` |
| /skills pin on bundled skill is rejected | `tests/test_flow_skills_pin.py` |
| /skills pin on unknown skill is rejected | `tests/test_flow_skills_pin.py` |
| /skills usage lists agent-created skills; excludes bundled | `tests/test_flow_skills_usage.py` |
| /skills usage <name> prints full record | `tests/test_flow_skills_usage.py` |
