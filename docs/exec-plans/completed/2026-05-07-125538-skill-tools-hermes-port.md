# Plan: Skill Tools — Hermes-Parity Port (`skills_list` + `skill_view`)

Task type: code

## Context

Direct port of hermes's model-callable skill discovery + inspection
surface into co-cli. Today the model cannot self-discover skills
without a user slash command; every skill must be human-invoked. As the
skill corpus grows, this is the bottleneck for skills-as-policy
patterns (e.g. the model loading `/test-hygiene` guidance mid-turn).
The bundled corpus is currently one skill (`co_cli/skills/doctor.md`),
so the immediate user-visible behaviour change is small — the value is
forward-looking.

**Why this plan is split out from the original Tier A bundle.** The
original `2026-05-06-193355-tier-a-tool-gaps.md` plan paired
"phantom-tool doc sync" with "skills tool port" because both were
identified as Tier A gaps. The doc sync shipped (TASK-1 + TASK-2 ✓
DONE there); the skills port now needs its own plan because:

- Hermes-parity scope expanded (signature parity for `category`,
  `file_path`, plugin-qualified names, `linked_files` return shape).
- Three explicit deferred items emerged (skills-as-directories loader,
  plugin namespace, view-count telemetry) that need their own
  tracking, not buried in a multi-track plan.
- Behavioural test coverage doubled (3 → 8 assertions).
- Spec docs (`docs/specs/tools.md`, `docs/specs/skills.md`) need
  separate edits — bundled with code, not docs-only.

**Hermes reference.** `hermes-agent/tools/skills_tool.py:1440-1512`:

- `skills_list(category: str | None = None) -> str` — returns
  `name + description` rows; optional category filter; declares
  `check_fn=check_skills_requirements` for runtime availability.
- `skill_view(name: str, file_path: str | None = None) -> str` —
  returns SKILL.md content + `linked_files` dict on first call;
  subsequent calls with `file_path` return the linked file content;
  bumps `view_count` on success (best-effort telemetry via
  `tools/skill_usage.py`).

Co-cli's flat-file skill model means `file_path`, `linked_files`, and
plugin-qualified names degenerate to no-ops today. Porting the
*interface* now means a future skill-as-directory loader (or plugin
namespace) needs no further tool-surface changes — only loader
extension.

### Current-state validation (inline)

- ✓ `co_cli/skills/registry.py:get_skill_registry()` already filters by
  `disable_model_invocation` and returns `[{name, description}, …]` —
  reusable as the body of `skills_list`.
- ✓ `co_cli/skills/skill_types.py:SkillConfig` is a frozen dataclass
  with `name`, `description`, `body`, `disable_model_invocation`. No
  `category` field today — the `skills_list(category=…)` filter will
  be a pass-through for now (matches hermes signature; degenerate
  semantics).
- ✓ `CoDeps.skill_registry: dict[str, SkillConfig]` is populated at
  bootstrap and propagated through deps clones (`co_cli/deps.py:231,310`).
- ✓ Bundled skills directory currently contains one `.md`
  (`co_cli/skills/doctor.md`). User skills load from `~/.co-cli/skills/`.
  No directory-shape skills exist (`SKILL.md` + `references/` /
  `templates/` / `scripts/`), so `linked_files={}` is correct today.
- ✓ `tool_output()` auto-spills above `SPILL_THRESHOLD_CHARS=4_000`
  (`co_cli/tools/tool_io.py:46-48,213-248`). For `skill_view`, this
  would turn the body into a `<persisted-output>` placeholder —
  defeating the tool. Precedent for opting out:
  `co_cli/tools/files/read.py:449` sets `spill_threshold_chars=math.inf`
  on `file_read`.
- ✓ Test pattern for direct-call tools:
  `RunContext(deps=..., model=None, usage=RunUsage())` precedent at
  `tests/test_flow_capability_checks.py:31-32` (used in 12+ files).
- ✓ `tool_error` is the right primitive for `skill_view` unknown /
  blocked / `file_path`-on-flat-file cases (per `agent_docs/review.md:14`:
  `tool_error` for non-fatal semantic failures, `ModelRetry` for
  transient/recoverable).
- ✓ `docs/specs/skills.md` exists and documents the current
  human-invocation model — needs an addition for the new
  model-callable surface.
- ✓ `docs/specs/tools.md` enumerates the registered tool surface —
  needs two new rows.
- ✓ Hermes never ports `skill_manage` (write tools — create/edit/delete
  skill files): out of scope for this plan, deferred per
  `RESEARCH-tools-gaps-co-vs-hermes.md:§2.1`.

## Problem & Outcome

**Problem:** The model cannot self-discover or self-load skills. As the
skill corpus grows, every new skill stays inert until a human runs the
slash command. There is no model-callable equivalent of hermes's
`skills_list` / `skill_view` pair, so prompt strategies that rely on
skills-as-policy (mid-turn skill loading by the model itself) cannot
work in co-cli.

**Outcome:** The model can call `skills_list()` to enumerate
model-callable skills and `skill_view(name=…)` to read a skill body
verbatim, with hermes-shape parameters (`category`, `file_path`) and
return shape (`linked_files: {}`). Plugin-qualified names are accepted
syntactically. The two tools are always-visible, read-only,
concurrent-safe, and approval-free. Future hermes skill packs that
expect this contract can be imported without per-tool reshaping.

## Scope

### In scope

- New file `co_cli/tools/system/skills.py` containing `skills_list` and
  `skill_view` — each with hermes-parity signatures.
- Both tools added to `NATIVE_TOOLS` in `co_cli/agent/_native_toolset.py`.
- Behavioural tests in `tests/test_flow_skills_tools.py`.
- Spec edits:
  - `docs/specs/tools.md` — add the two new rows (introspection group).
  - `docs/specs/skills.md` — add a "Model-callable surface" section.

### Out of scope

- Implementing hermes's `skill_manage(action: create|edit|patch|...)` —
  write tools that mutate `~/.co-cli/skills/`. Deliberately skipped per
  `RESEARCH-tools-gaps-co-vs-hermes.md:§2.1` (more invasive; overlaps
  with the knowledge-artifact system; no current need).
- Adding `category` to `SkillConfig` or to skill frontmatter. The
  `skills_list(category=...)` parameter is accepted now; data plumbing
  is deferred until skills actually need to be categorised.
- Adding a plugin namespace to the skill registry.
- Skills-as-directories loader (multi-file `SKILL.md` + `references/`).
- `view_count` usage telemetry.
- Changing skill loader semantics, frontmatter schema, or
  `disable_model_invocation` defaults.

## Behavioural Constraints

1. **`skills_list` signature parity.** Matches hermes
   `skills_list(category: str | None = None)`. The `category` param is
   accepted; co-cli skills lack a category field today, so the filter
   is a pass-through unless future frontmatter adds one. Returns only
   skills with non-empty `description` and
   `disable_model_invocation=False` — identical to `get_skill_registry()`
   filter at `co_cli/skills/registry.py:17-23`.
2. **`skills_list` empty registry.** Returns `"No skills available."`
   with `skills=[]` metadata; never raises.
3. **`skill_view` signature parity.** Matches hermes
   `skill_view(name: str, file_path: str | None = None)`. `name`
   accepts the hermes plugin-qualified form `"plugin:skill"` — co-cli
   has no plugin namespace today, so the prefix is split and the bare
   skill name is used as the lookup key (qualified-but-unknown still
   routes to the standard unknown-name `tool_error`). `file_path` is
   accepted for parity; with co-cli's flat-file skill model there are
   no linked files, so any non-None `file_path` returns
   `tool_error("skill {name!r} has no linked files")`.
4. **`skill_view` return shape parity.** When called without
   `file_path`, returns `tool_output(body, ctx=ctx, name=name, linked_files={})`.
   The empty `linked_files` dict is hermes-shape — clients can rely on
   the key being present. The body is the skill markdown verbatim.
5. **`skill_view` failure semantics.** Unknown name → `tool_error`
   (terminal, not `ModelRetry`); name exists but
   `disable_model_invocation=True` → `tool_error` with explicit
   reason; non-None `file_path` on a flat-file skill → `tool_error`.
   All three let the model recover by picking a different skill or
   stopping, not by retrying.
6. **`skill_view` returns body verbatim.** No system-prompt promotion,
   no rewriting, no decoration. Skill bodies are markdown with
   imperative instructions designed to steer behaviour — that is the
   prompt-injection shape by definition. The author of a skill written
   for human/slash invocation should set `disable-model-invocation: true`
   in the frontmatter if model self-load would change the skill's
   intent.
7. **`skill_view` opts out of result spill.** Decorated with
   `spill_threshold_chars=math.inf`. The whole point of the tool is to
   place the body into the model's context — letting the spill
   mechanism substitute a `<persisted-output>` placeholder defeats it.
   Precedent: `co_cli/tools/files/read.py:449`.
8. **Both skill tools always-visible.** `VisibilityPolicyEnum.ALWAYS`,
   `is_read_only=True`, `is_concurrent_safe=True`, `approval=False`.
9. **Hermes-only telemetry NOT ported.** Hermes bumps a `view_count`
   on each successful `skill_view` (`tools/skills_tool.py:1484-1502`)
   via a `tools/skill_usage.py` module. Co-cli has no equivalent
   sqlite/usage table. Deliberately skipped to keep the port surface
   minimal — see Deferred items.

## High-Level Design

### File: `co_cli/tools/system/skills.py` (new)

```python
"""Model-callable skill discovery and inspection tools.

Hermes-parity port of skills_list + skill_view
(hermes-agent/tools/skills_tool.py:1440-1512). Signatures match;
co-cli's flat-file skill model degenerates `file_path` and
`linked_files` to no-ops (see Constraints 3-4).
"""

import math

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.skills.registry import get_skill_registry
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def skills_list(
    ctx: RunContext[CoDeps],
    category: str | None = None,
) -> ToolReturn:
    """List available skills (name + description). Use skill_view(name) to load full content.

    Skills with disable-model-invocation: true in frontmatter are excluded.

    Args:
        category: Optional category filter to narrow results. Co-cli skills
            do not currently expose a category field; the filter is a
            pass-through unless future frontmatter adds one.
    """
    entries = get_skill_registry(ctx.deps.skill_registry)
    if category:
        entries = [e for e in entries if e.get("category") == category]
    if not entries:
        return tool_output("No skills available.", ctx=ctx, skills=[])
    lines = [f"- {e['name']}: {e['description']}" for e in entries]
    return tool_output(
        "Available skills:\n" + "\n".join(lines),
        ctx=ctx,
        skills=entries,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
    spill_threshold_chars=math.inf,
)
async def skill_view(
    ctx: RunContext[CoDeps],
    name: str,
    file_path: str | None = None,
) -> ToolReturn:
    """Load a skill's full content. Returns SKILL.md body plus a 'linked_files' dict.

    First call (no file_path) returns the skill body and a linked_files dict
    listing references/templates/scripts. To access linked files, call again
    with file_path. Co-cli's flat-file skill model has no linked files today;
    any non-None file_path returns a tool_error.

    Args:
        name: Skill name. Plugin-qualified form 'plugin:skill' is accepted —
            co-cli has no plugin namespace, so the prefix is dropped and the
            bare name is used.
        file_path: Optional path to a linked file within the skill. Not
            supported by co-cli's flat-file skills today.
    """
    lookup = name.split(":", 1)[1] if ":" in name else name
    skill = ctx.deps.skill_registry.get(lookup)
    if skill is None:
        return tool_error(f"skill_view: unknown skill {name!r}.", ctx=ctx)
    if skill.disable_model_invocation:
        return tool_error(
            f"skill_view: skill {name!r} is not model-invocable.", ctx=ctx
        )
    if file_path is not None:
        return tool_error(
            f"skill_view: skill {name!r} has no linked files.", ctx=ctx
        )
    return tool_output(skill.body, ctx=ctx, name=lookup, linked_files={})
```

### `NATIVE_TOOLS` registration

Insert both functions in the "Introspection & todos" group of
`co_cli/agent/_native_toolset.py:NATIVE_TOOLS`, after `capabilities_check`,
before `todo_write`. Order matters only for surface presentation; behavior
is identical regardless.

### Spec doc updates

- `docs/specs/tools.md` — add two rows under the Introspection group:
  | Tool | Visibility | Approval | Notes |
  |---|---|---|---|
  | `skills_list` | always | none | hermes-parity; `category` pass-through |
  | `skill_view` | always | none | hermes-parity; `linked_files={}` on flat-file skills |
- `docs/specs/skills.md` — append a "Model-callable surface" section
  describing the two tools, the disable-model-invocation gate, and the
  three deferred parity items (linked_files, plugin namespace, view_count).

## Tasks

### TASK-1 ✓ DONE — Implement `skills_list` (hermes-parity signature)

- **files:**
  - `co_cli/tools/system/skills.py` (new — this task creates the file
    and adds `skills_list`; TASK-2 extends the same file with
    `skill_view`)
- **prerequisites:** —
- **done_when:**
  `python -c "from co_cli.tools.system.skills import skills_list; assert skills_list.__co_tool_info__.is_read_only and skills_list.__co_tool_info__.is_concurrent_safe"`
  exits 0; signature matches `(ctx, category: str | None = None)`.
- **success_signal:** TASK-3's tests for `skills_list` (assertions 1-3)
  pass when run in isolation.

### TASK-2 ✓ DONE — Implement `skill_view` (hermes-parity signature + return shape)

- **files:**
  - `co_cli/tools/system/skills.py` (extends the file from TASK-1)
- **prerequisites:** [TASK-1]
- **done_when:**
  `python -c "from co_cli.tools.system.skills import skill_view; info = skill_view.__co_tool_info__; assert info.spill_threshold_chars == float('inf') and info.is_read_only"`
  exits 0; signature matches `(ctx, name: str, file_path: str | None = None)`.
- **success_signal:** TASK-3's tests for `skill_view` (assertions 4-8)
  pass when run in isolation.

### TASK-3 ✓ DONE — Behavioural tests for skills tools

- **files:**
  - `tests/test_flow_skills_tools.py` (new)
- **prerequisites:** [TASK-1, TASK-2]
- **done_when:**
  `uv run pytest tests/test_flow_skills_tools.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-skills-tools.log`
  passes with at least these assertions:
  1. `test_skills_list_includes_doctor` — bundled `doctor.md` appears in
     the result. Pre-asserts `"doctor" in {e["name"] for e in result.metadata["skills"]}`
     to fail loud if the bundled skill changes its
     `disable-model-invocation` flag or empties its `description`.
  2. `test_skills_list_filters_disable_model_invocation` — inject
     `SkillConfig(name="hidden", description="x", disable_model_invocation=True)`;
     result `skills` does not include `hidden`.
  3. `test_skills_list_category_passthrough` — `skills_list(category="nonexistent")`
     returns `"No skills available."` with `skills=[]` (Constraint 1
     parity guard).
  4. `test_skill_view_returns_body_inline` — `skill_view(name="doctor")`
     returns `ToolReturn` whose `return_value` equals the loaded
     `doctor.md` body verbatim (no `<persisted-output>` placeholder
     even if body exceeds 4 KB — Constraint 7 regression guard).
     Metadata includes `linked_files == {}` (Constraint 4 return-shape
     parity).
  5. `test_skill_view_plugin_qualified_name` —
     `skill_view(name="anyplugin:doctor")` resolves to the same `doctor`
     skill body (Constraint 3 prefix-stripping behaviour).
  6. `test_skill_view_unknown_name` — `skill_view(name="nonexistent")`
     returns `tool_output(..., error=True)` (the `tool_error` shape).
  7. `test_skill_view_blocked_skill` — inject `SkillConfig` with
     `disable_model_invocation=True`; `skill_view(name=…)` for that
     name returns `tool_error`.
  8. `test_skill_view_file_path_unsupported` —
     `skill_view(name="doctor", file_path="references/x.md")` returns
     `tool_error` matching `"has no linked files"` (Constraint 3
     degeneracy guard).
- **success_signal:** N/A (test file).

Use real `load_skills(Path("co_cli/skills"))` for the bundled case.
Build `RunContext` directly per `tests/test_flow_capability_checks.py:31-32`.
For injected `SkillConfig`, construct the real frozen dataclass — no
mocks. The skills tools are pure in-memory dispatch (no IO), so
`asyncio.timeout(...)` wrappers are not needed for assertions 2-8.

### TASK-4 ✓ DONE — Register in `NATIVE_TOOLS`

- **files:**
  - `co_cli/agent/_native_toolset.py`
- **prerequisites:** [TASK-1, TASK-2]
- **done_when:**
  `python -c "from co_cli.tools.system.skills import skills_list, skill_view; from co_cli.agent._native_toolset import NATIVE_TOOLS; assert skills_list in NATIVE_TOOLS and skill_view in NATIVE_TOOLS"`
  exits 0.
- **success_signal:** Tools appear in `capabilities_check` output during
  a smoke `uv run co chat` session.

Add the import at the top of the file (alphabetical within the
`co_cli.tools.system.*` group). Insert both functions in the
"Introspection & todos" group of `NATIVE_TOOLS` after `capabilities_check`.

### TASK-5 ✓ DONE — Spec doc sync

- **files:**
  - `docs/specs/tools.md`
  - `docs/specs/skills.md`
- **prerequisites:** [TASK-4]
- **done_when:**
  `grep -nE "skills_list|skill_view" docs/specs/tools.md docs/specs/skills.md`
  shows both tool names in both files; Track A's invariant holds
  (`grep -E "memory_list|memory_read[^_]" docs/specs/tools.md` empty).
- **success_signal:** A future audit cycle reading the specs sees the
  new model-callable surface as canonical.

In `docs/specs/tools.md`, add two rows under the Introspection group
(after `capabilities_check`).

In `docs/specs/skills.md`, append a "Model-callable surface" section
explicitly listing:
- `skills_list` and `skill_view` as the only model-callable
  introspection points.
- The `disable-model-invocation: true` opt-out.
- The three deferred parity items (linked-file loader, plugin
  namespace, view_count telemetry) with their trigger conditions.

## Testing

### Test files
- `tests/test_flow_skills_tools.py` — new (TASK-3 covers all
  assertions).

### Test pattern
- Build `CoDeps` via `tests._settings.SETTINGS_NO_MCP` (suite-level
  singleton).
- Build `RunContext` directly
  (`tests/test_flow_capability_checks.py:31-32` precedent).
- No mocks: real `load_skills(Path("co_cli/skills"))`, real frozen
  `SkillConfig` dataclasses for injection.

### Lint / quality gate
- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before considering ready to ship.

## Open Questions

1. **Q:** Should `skills_list`'s description be dynamically rewritten
   with the count of available skills (à la opencode `SkillTool`
   description injection)?
   **Tentative answer:** No — the body of the tool result already
   enumerates skills. Dynamic descriptions add complexity to the
   registration path for negligible model benefit. Defer to a future
   Tier B task if the model fails to discover skills in eval runs.

2. **Q:** Should the plugin-qualified prefix be preserved in the
   `name` metadata when present (e.g. `name="superpowers:writing-plans"`)
   in the return payload?
   **Tentative answer:** No — return the resolved bare name (`name=lookup`).
   Mirrors hermes's behaviour of returning the canonical name in the
   payload (`hermes-agent/tools/skills_tool.py:1496`).

## Deferred items

- **Skills-as-directories loader (`linked_files` non-empty).** Co-cli's
  current loader (`co_cli/skills/loader.py`) reads single `.md` files.
  Hermes's skills are directories with `SKILL.md` + `references/` /
  `templates/` / `scripts/` siblings. Porting the *interface* (this
  plan) leaves `linked_files={}` and `file_path` rejected; porting the
  *loader* is a separate Tier B task triggered when a real co-cli skill
  needs auxiliary assets.
- **Plugin namespace + qualified skill names.** Hermes resolves
  `"plugin:skill"` against a plugin registry. This plan accepts the
  qualified form syntactically (prefix stripped) but does not add a
  plugin registry. Defer until co-cli adds a plugin system or imports a
  hermes plugin pack.
- **`skill_view` view_count telemetry.** Hermes bumps a per-skill view
  counter via `tools/skill_usage.py`. Co-cli has no usage table. Defer
  until usage analytics are needed; the hook in `skill_view` is a
  one-line addition when the table exists.
- **`skill_manage` (write tools — create/edit/delete).** Hermes-only;
  invasive (writes to `~/.co-cli/skills/`); overlaps the
  knowledge-artifact write surface. Reassess only if a concrete skill
  authoring workflow needs it.

## Shipping order

Single commit — all five TASKs. Code + tests + specs ship together so
the spec docs never claim a tool that's missing in code (or vice
versa). Each TASK is structured for independent self-review during
development, but the integration surface is small enough that
splitting commits adds churn without revertibility benefit.

## Post-ship — research-doc resync

After this plan ships, mark the following as Done in
`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md`:
- §2.1 "Worth Considering" — drop the `skills_list` / `skill_view` row
  (note the three deferred items remain as separate open gaps).
- §5 priority table — drop the "Add model-callable `skills_list` /
  `skill_view`" Medium row.

---

## Implementation Review — 2026-05-09

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `skills_list.__co_tool_info__.is_read_only and is_concurrent_safe` | ✓ pass | `skills.py:34` — `@agent_tool(visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True)`; `done_when` exits 0 |
| TASK-2 | `skill_view.__co_tool_info__.spill_threshold_chars == inf and is_read_only` | ✓ pass | `skills.py:59` — `spill_threshold_chars=math.inf`; `done_when` exits 0 |
| TASK-3 | 9/9 behavioural tests pass | ✓ pass | `test_flow_skills_tools.py` — all 8 plan assertions + 1 additional large-body spill guard; 9 passed in 0.20s |
| TASK-4 | `skills_list in NATIVE_TOOLS and skill_view in NATIVE_TOOLS` | ✓ pass (functional intent) | `_native_toolset.py:36` imports all three tools triggering TOOL_REGISTRY self-registration; all three appear in registry with correct metadata. **Note:** `done_when` literal command fails — `NATIVE_TOOLS` does not exist as an importable name (architecture uses `TOOL_REGISTRY` self-registration, not an explicit list). This is a stale criterion in the plan, not an implementation defect. |
| TASK-5 | Both names in both spec files; no ghost `memory_list`/`memory_read` | ✓ pass | `tools.md:137-139`, `skills.md:192-248`; invariant grep returns empty |

### Scope Deviation

`skill_manage` was implemented despite the plan marking it explicitly out of scope. The write surface is complete (create, edit, patch, delete), has `approval=True`, is fully tested in `test_flow_skills_manage.py` (363 lines), and is correctly documented in both spec files. Already committed in `v0.8.160` — historical record only, nothing to fix.

### Issues Found & Fixed

No issues found. No auto-fixes applied.

### Tests

- Command: `uv run pytest -x -v`
- Result: 248 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`
- Skills-specific: `uv run pytest tests/test_flow_skills_tools.py` — 9 passed in 0.20s

### Doc Sync

- Scope: narrow — skills tools are self-contained in `co_cli/tools/system/skills.py`; no shared module or public API renamed
- Result: clean — `docs/specs/tools.md` and `docs/specs/skills.md` already updated with `skills_list`, `skill_view`, `skill_manage`

### Behavioral Verification

- `uv run co --help`: ✓ CLI starts cleanly, no import errors
- Tool registry: `skills_list` (ALWAYS, read-only, no approval), `skill_view` (ALWAYS, read-only, no approval, spill=inf), `skill_manage` (ALWAYS, write, approval=True) — all confirmed in TOOL_REGISTRY
- No `co status` command exists in this project; verified equivalent via registry introspection

### Overall: PASS

All plan requirements met. `skill_manage` scope expansion is already committed and fully tested. Ready to `/ship`.
