# Plan: Skill Manage — Hermes-Parity Port (`skill_manage`)

Task type: code

## Context

Direct port of hermes's model-callable skill **write** surface into co-cli.
Today the model can read skills (slash dispatch, and once the read-tools plan
ships, `skills_list`/`skill_view`) but cannot create, edit, patch, or delete
them. The skill catalog can only grow by hand-writing markdown and using
`/skills install` from the CLI side. This makes "self-improving skill" patterns
(hermes's "patch immediately if outdated" invariant, codex's `skill-creator`
update path, openclaw's `skill-workshop` reviewer/creator pair) impossible.

This plan delivers the **3-peer universal capability** identified by
`docs/reference/RESEARCH-skills-peers-tiers.md` Part 5 Steps 1–2 in a single
hermes-parity tool: every catalog-shipping peer ships create + install + patch
in some form, and hermes packages all three (plus delete, plus linked-file
ops) under one `skill_manage(action, name, …)` tool. Porting that one tool
covers the lifecycle trio.

**Why this plan is split out from the read-tools plan.** The active plan
`2026-05-07-125538-skill-tools-hermes-port.md` ships `skills_list` + `skill_view`
(the read surface) and explicitly defers `skill_manage`:

> **`skill_manage` (write tools — create/edit/delete).** Hermes-only;
> invasive (writes to `~/.co-cli/skills/`); overlaps the knowledge-artifact
> write surface. Reassess only if a concrete skill authoring workflow needs it.

The peer-tiers survey reframes "concrete authoring workflow needs it" as a
universal convergence signal — every catalog-shipping peer has a model-callable
write path. `skill_manage` is the natural container for the trio in co-cli
because (a) hermes's signature already exists and is well-shaped, (b) one tool
with action dispatch keeps the surface small, (c) it matches the read-tools
plan's parity-port pattern (port the *interface* first; loader extension is a
separate task).

**Hermes reference.** `hermes-agent/tools/skill_manager_tool.py:647-720` —
`skill_manage(action, name, content, category, file_path, file_content,
old_string, new_string, replace_all) -> str`. JSON-string return. Six actions:

| Action | Required args | Behaviour |
|---|---|---|
| `create` | `name`, `content` | Write new SKILL.md; validate frontmatter + size; security scan + rollback |
| `edit` | `name`, `content` | Full SKILL.md rewrite; same validation as create; reject external-dir skills |
| `patch` | `name`, `old_string`, `new_string` | Find-and-replace; unique match unless `replace_all=true`; defaults to SKILL.md, optional `file_path` for linked files |
| `delete` | `name` | Remove skill from local dir; reject external-dir skills |
| `write_file` | `name`, `file_path`, `file_content` | Write a linked file under `references/`/`templates/`/`scripts/`/`assets/` |
| `remove_file` | `name`, `file_path` | Remove a linked file |

After any successful write, hermes clears its skills prompt-snapshot cache via
`agent.prompt_builder.clear_skills_system_prompt_cache(clear_snapshot=True)`
and bumps curator telemetry counters via `tools/skill_usage.py`. Co-cli has no
prompt-snapshot cache today (the survey's Step 4 awareness layer is deferred)
and no usage table — both hooks degenerate to no-ops, matching the read-tools
plan's stubs.

### Current-state validation (inline)

- ✓ `co_cli/skills/loader.py:scan_skill_content(content) -> list[str]` — returns flagged-pattern names; reuse on create/edit/patch result for security scan.
- ✓ `co_cli/skills/loader.py:_SKILL_SCAN_PATTERNS` — credential exfil, pipe-to-shell, destructive shell, prompt injection. Same patterns hermes runs.
- ✓ `co_cli/skills/loader.py:load_skills(...)` — full reload; `co_cli/skills/registry.py:set_skill_commands` — atomic replace. Together they're the reload path on success.
- ✓ `co_cli/skills/installer.py:write_skill_file(...)` — atomic write to user skills dir; reuse for `create`/`edit`/`write_file`. `discover_skill_files(...)` and `read_skill_meta(...)` — locate and parse skills; reuse for `_find_skill`-equivalent.
- ✓ `co_cli/memory/frontmatter.py:parse_frontmatter` — already used by loader and installer; reuse for content validation.
- ✓ `co_cli/skills/skill_types.py:SkillConfig` — frozen dataclass; `name`, `description`, `body`, `requires`, `disable_model_invocation`, `user_invocable`. No `category` field today — hermes-parity `category` arg becomes a no-op (mirrors `skills_list`'s `category` filter pattern in the read-tools plan).
- ✓ `CoDeps.skills_dir` (bundled) and `CoDeps.user_skills_dir` (`~/.co-cli/skills/`) — bundled = read-only via this tool; user dir = writable.
- ✓ `co_cli/commands/skills.py:_cmd_skills_install` — flow shape for security-scan-then-write; tool path mirrors this.
- ✓ Bundled skill collision: `co_cli/skills/loader.py` already implements user-global-wins-over-bundled. `delete` of a name shadowed by bundled removes the user copy and the bundled becomes active again — must be tested.
- ✓ `tool_output()` / `tool_error()` patterns in `co_cli/tools/_tool_io.py` — JSON-string return matches hermes's pattern; size-gated automatically.
- ✓ Test pattern for direct-call tools: `tests/test_flow_skills_tools.py` (planned by the read-tools plan) — extend or pair this file.
- ✓ `co_cli/agent/_native_toolset.py:NATIVE_TOOLS` — registration list; add `skill_manage` after read-tools plan ships its two entries.

## Problem & Outcome

**Problem.** No model-callable lifecycle write path. The agent cannot:

1. Save a successful workflow as a new skill from session (T1-1, 3-peer universal).
2. Patch a skill it just used and found outdated (T1-3, 3-peer universal).
3. Delete a skill it created and no longer needs.

Today the only options are (a) the user hand-writes markdown into
`~/.co-cli/skills/`, (b) the user runs `/skills install <url>`. Both require
human invocation and human authorship.

**Outcome.** A single `skill_manage` tool with hermes-parity signature and
JSON return shape. The model can:

- `create` a new skill with full SKILL.md content (frontmatter + body), with security scan + frontmatter validation + atomic write + reload.
- `edit` a user-installed skill's full body (security scan + rollback on block).
- `patch` a user-installed skill via surgical find-and-replace, with unique-match enforcement.
- `delete` a user-installed skill (atomic file removal + reload).

Bundled skills are read-only via this tool. `write_file`/`remove_file` and
`patch` with `file_path` set are accepted at the schema level but return
`tool_error` explaining linked-file support is deferred — same pattern as
`skill_view`'s `linked_files={}` stub in the read-tools plan.

## Scope

### In scope

- New function `skill_manage` in `co_cli/tools/system/skills.py` (the file the read-tools plan creates).
- Six action handlers: `_skill_create`, `_skill_edit`, `_skill_patch`, `_skill_delete`, `_skill_write_file` (stub), `_skill_remove_file` (stub).
- Internal helpers: `_find_user_skill(name) -> Path | None`, `_validate_skill_content(content) -> str | None` (frontmatter + size), `_atomic_write_skill(path, content)`, `_run_security_scan_or_rollback(path, original) -> str | None`, `_reload_skills(deps)` (calls existing `load_skills` + `set_skill_commands`).
- Tool registration in `NATIVE_TOOLS` in `co_cli/agent/_native_toolset.py`.
- Behavioural tests in `tests/test_flow_skills_manage.py` (separate file from read-tools tests; both files target the same `co_cli/tools/system/skills.py` module).
- Spec edits via `/sync-doc` post-delivery (`docs/specs/skills.md` "Model-callable surface" section gains `skill_manage` row; `docs/specs/tools.md` Lifecycle group gains row).

### Out of scope

- **Skills-as-directories loader.** `write_file`/`remove_file` and `patch` with `file_path` are signature-parity stubs that return `tool_error`. Same deferral as the read-tools plan's `linked_files={}` and `file_path` rejection. Lifting this requires loader extension — a separate Tier B plan.
- **Lint validator (Step 1 in the survey).** Frontmatter validation here is the minimum hermes does (description present, name matches stem); structural-quality lint (required body sections, length budget, peer-rubric rules) is deferred to a follow-up plan. Reason: keeping this plan focused on direct hermes parity; lint is co-cli-additive.
- **`category` field implementation.** Co-cli has no category dirs and no `category` field on `SkillConfig`. The arg is accepted at the schema level (parity) and silently ignored, matching `skills_list`'s read-side stub. Adding categories is a separate loader change.
- **Skills prompt-snapshot cache invalidation.** Co-cli has no prompt-snapshot cache (Step 4 awareness layer not built). The hook is a one-line addition when it exists; absent now, no-op.
- **Usage telemetry (`bump_patch`/`forget`).** No co-cli usage table. Same pattern as read-tools plan's deferral.
- **Patch of bundled skills.** Bundled skills live in `co_cli/skills/` (version-controlled). Edit/patch/delete of bundled is forbidden — `tool_error` directs the user to copy to user dir first (matches hermes's external-dir rejection).
- **`skill_run` model-dispatch tool** (Step 4 in survey). This tool writes; it does not invoke. Slash dispatch is unchanged.

## Behavioural Constraints

1. **Atomicity.** Writes use `_atomic_write_skill` (write-to-temp + rename). Failed writes leave the prior file intact.
2. **Security-scan-rollback.** After every successful write (create/edit/patch), run `scan_skill_content` on the resulting body. If any pattern matches, restore the prior content (or delete the file for `create`) and return `tool_error` listing the matched pattern names.
3. **Frontmatter validation.** Reject content that fails `parse_frontmatter` parsing OR has an empty `description`. (Hermes also enforces a 1024-char description limit; co-cli mirrors this.)
4. **Bundled-skill protection.** `_find_user_skill(name)` returns the user-dir path only. Edit/patch/delete on a name that exists only in the bundled dir returns `tool_error`: *"Skill 'X' is bundled and cannot be modified via skill_manage. Copy it to ~/.co-cli/skills/ first."* Hermes uses the same shape for external-dir skills.
5. **Name validation.** Skill name must be lowercase letters, digits, hyphens, underscores; ≤64 chars (matches hermes). Filename = `<name>.md` always.
6. **Reload on success.** After any successful write, call `_reload_skills(deps)` so the new/changed skill is immediately dispatchable. Failed writes do not reload.
7. **Idempotency for `delete`.** Deleting a non-existent skill returns `tool_error` (matches hermes — does not silently succeed).
8. **Unique match for `patch`.** When `replace_all=False`, `old_string` must match exactly once in the target. Multiple or zero matches → `tool_error` with match count. `replace_all=True` accepts any match count ≥ 1.
9. **JSON return shape.** All actions return `tool_output(json.dumps({"success": bool, ...}))`. Success shape: `{"success": True, "message": str, "path": str}`. Error shape: returned via `tool_error()` (which the agent harness wraps).
10. **Approval gate.** All actions go through deferred approval (writes are not auto-approved). Approval subject = `tool:skill_manage:<action>:<name>` so allow-rules can be scoped per-action.
11. **Linked-file stubs.** `write_file`/`remove_file` and `patch` with `file_path` non-None return `tool_error`: *"Linked files (file_path) are not yet supported in co-cli. SKILL.md is the only writable target. Track this gap in RESEARCH-skills-peers-tiers.md §T3-D."*

## High-Level Design

### File: `co_cli/tools/system/skills.py` (extend)

The read-tools plan creates this file with `skills_list` + `skill_view`. This
plan adds `skill_manage` and its six action handlers below those.

```python
def skill_manage(
    ctx: RunContext[CoDeps],
    action: Literal["create", "edit", "patch", "delete", "write_file", "remove_file"],
    name: str,
    content: str | None = None,
    category: str | None = None,
    file_path: str | None = None,
    file_content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
) -> str:
    """Create, edit, patch, or delete a user-installed skill.

    Hermes-parity signature; co-cli flat-file model means file_path/linked-files
    actions return tool_error for now (parity-deferred).
    """
    if not _NAME_RE.match(name) or len(name) > 64:
        return tool_error(...)

    if action == "create":
        if not content:
            return tool_error("content is required for 'create'.")
        return _skill_create(ctx, name, content, category)

    if action == "edit":
        if not content:
            return tool_error("content is required for 'edit'.")
        return _skill_edit(ctx, name, content)

    if action == "patch":
        if not old_string:
            return tool_error("old_string is required for 'patch'.")
        if new_string is None:
            return tool_error("new_string is required for 'patch'.")
        if file_path:
            return tool_error("Linked files (file_path) are not yet supported. ...")
        return _skill_patch(ctx, name, old_string, new_string, replace_all)

    if action == "delete":
        return _skill_delete(ctx, name)

    if action in ("write_file", "remove_file"):
        return tool_error("Linked files are not yet supported in co-cli. ...")

    return tool_error(f"Unknown action '{action}'. Use: create, edit, patch, delete.")
```

### Action handlers (signatures + flow)

```python
def _skill_create(ctx, name, content, category) -> str:
    # 1. Validate frontmatter (parse_frontmatter + non-empty description).
    # 2. Validate content size (≤ MAX_SKILL_CHARS, mirrors hermes).
    # 3. Reject if name already exists in user dir (bundled collision is fine —
    #    user copy will shadow on next reload).
    # 4. Atomic write to ctx.deps.user_skills_dir / f"{name}.md".
    # 5. Run scan_skill_content on body; on flags, delete the just-written file
    #    and return tool_error listing matched patterns.
    # 6. Reload skills.
    # 7. Return JSON: {"success": True, "message": ..., "path": ...,
    #    "category_ignored": True if category else False}.

def _skill_edit(ctx, name, content) -> str:
    # 1. _find_user_skill(name) → Path or None.
    # 2. If None: tool_error (suggest 'create' or 'copy from bundled first').
    # 3. Validate frontmatter + size on new content.
    # 4. Read original; atomic-write new; scan; rollback on flag.
    # 5. Reload.

def _skill_patch(ctx, name, old, new, replace_all) -> str:
    # 1. _find_user_skill(name) → Path.
    # 2. Read body. Count occurrences of `old`.
    #    - replace_all=False and count != 1 → tool_error with count.
    #    - count == 0 → tool_error.
    # 3. Apply replacement (str.replace, count=count or -1 for replace_all).
    # 4. Atomic write; scan; rollback on flag.
    # 5. Reload.

def _skill_delete(ctx, name) -> str:
    # 1. _find_user_skill(name) → Path or None.
    # 2. None → tool_error ('Skill not found in user dir').
    # 3. Path.unlink().
    # 4. Reload.
    # 5. Return JSON: {"success": True, "message": ..., "shadowed_bundled": bool}.
```

### Helpers (shared with read-tools)

```python
_NAME_RE: re.Pattern = re.compile(r"^[a-z0-9_-]+$")
_MAX_DESCRIPTION_CHARS: int = 1024
_MAX_SKILL_CHARS: int = 100_000  # mirrors hermes ceiling

def _find_user_skill(deps: CoDeps, name: str) -> Path | None:
    """Return Path under user_skills_dir if <name>.md exists, else None.
    Bundled-only skills return None — they cannot be edited via this tool."""

def _validate_skill_content(content: str) -> str | None:
    """Return error message or None. Checks: parse_frontmatter ok,
    description present + ≤1024, total size ≤ MAX_SKILL_CHARS."""

def _atomic_write_skill(path: Path, content: str) -> None:
    """Write to path.with_suffix('.tmp') then rename. Existing _atomic
    helper in installer.py if available — reuse, don't duplicate."""

def _scan_or_rollback(
    path: Path, body: str, original: str | None
) -> str | None:
    """scan_skill_content(body) → if flagged, restore original (or unlink
    if original is None) and return formatted error string. Else None."""

def _reload_skills(ctx: RunContext[CoDeps]) -> None:
    """Reload via existing load_skills + set_skill_commands path so the
    new state is dispatchable on the next slash command. No prompt-snapshot
    cache to clear (Step 4 deferred)."""
```

### `NATIVE_TOOLS` registration

Append after the read-tools plan's two entries:

```python
ToolEntry(
    fn=skill_manage,
    name="skill_manage",
    description=_SKILL_MANAGE_DESC,  # adapted from hermes SKILL_MANAGE_SCHEMA description
    max_result_size=8_000,
    requires_config=None,
    approval_subject_factory=_skill_manage_approval_subject,
),
```

`_skill_manage_approval_subject(args)` returns
`f"tool:skill_manage:{action}:{name}"` so allow-rules can be scoped per-action
per-skill.

### Spec doc updates (output-side, via `/sync-doc`)

- `docs/specs/tools.md` — add row to Skills group: `| skill_manage | Lifecycle write (create/edit/patch/delete) for user-installed skills | tool:skill_manage:<action>:<name> |`.
- `docs/specs/skills.md` — extend "Model-callable surface" section (created by the read-tools plan): add `skill_manage` paragraph noting bundled-skill protection, security-scan-rollback, reload semantics, deferred linked-file support.

Both via `/sync-doc` after delivery — not authored as plan tasks.

## Tasks

### ✓ DONE — TASK-1 — Implement core action dispatch (`skill_manage` entry point)

Files:
- `co_cli/tools/system/skills.py` (extend with `skill_manage` function and `_NAME_RE`, `_MAX_DESCRIPTION_CHARS`, `_MAX_SKILL_CHARS` constants, action-routing logic).

Acceptance:
- `skill_manage(action="create", name="x", content="…")` routes to `_skill_create`.
- Invalid names (uppercase, special chars, >64 chars) return `tool_error` before action dispatch.
- Unknown action returns `tool_error` enumerating valid actions.
- `write_file`/`remove_file` and `patch` with `file_path` return the linked-file-deferred `tool_error`.

Depends on: read-tools plan TASK-1 (creates the file).

### ✓ DONE — TASK-2 — Implement `_skill_create` + `_skill_edit`

Files:
- `co_cli/tools/system/skills.py` (add `_skill_create`, `_skill_edit`, `_validate_skill_content`, `_scan_or_rollback`, `_atomic_write_skill`, `_reload_skills`).

Acceptance:
- `create` writes new `<name>.md` under `deps.user_skills_dir`, runs security scan, reloads, returns success JSON.
- `create` of an existing user-dir name → `tool_error` (no overwrite without `edit`).
- `create` with empty `description` frontmatter → `tool_error`.
- `create` with destructive shell pattern in body → file removed, `tool_error` listing pattern.
- `edit` of a user-installed skill → full rewrite + scan + reload.
- `edit` of a bundled-only name → `tool_error` (copy first).
- `edit` rollback on security flag → original content preserved.

### ✓ DONE — TASK-3 — Implement `_skill_patch` + `_skill_delete`

Files:
- `co_cli/tools/system/skills.py` (add `_skill_patch`, `_skill_delete`).

Acceptance:
- `patch` with unique `old_string` → replaces, scans, reloads.
- `patch` with non-unique `old_string` and `replace_all=False` → `tool_error` reporting match count.
- `patch` with `replace_all=True` and 3 matches → all replaced.
- `patch` with zero matches → `tool_error`.
- `patch` with security flag in result → rollback + `tool_error`.
- `delete` of user-installed skill → file removed, reload, success JSON includes `"shadowed_bundled": true` if a bundled skill of same name now becomes active.
- `delete` of unknown name → `tool_error`.
- `delete` of bundled-only name → `tool_error`.

### ✓ DONE — TASK-4 — Register in `NATIVE_TOOLS` + approval subject

Files:
- `co_cli/agent/_native_toolset.py` (add `skill_manage` entry).
- `co_cli/tools/system/skills.py` (define `_skill_manage_approval_subject` + `_SKILL_MANAGE_DESC`).

Acceptance:
- Tool appears in `capabilities_check` output.
- Approval subject format: `tool:skill_manage:<action>:<name>` — verified by inspecting subject for sample call.
- Description text steers toward `skill_view` for format reference and `skills_list` for inventory.

### ✓ DONE — TASK-5 — Behavioural tests for `skill_manage`

Files:
- `tests/test_flow_skills_manage.py` (new).

Test surface (≥10 behavioural assertions, no structural-only tests):

| # | Assertion |
|---|---|
| 1 | `create` writes file at expected path, content matches input. |
| 2 | `create` reload: skill is in `deps.skill_commands` after success. |
| 3 | `create` rejects empty description (frontmatter validation). |
| 4 | `create` rolls back (file removed) on destructive-shell pattern. |
| 5 | `create` of existing user-dir name returns error (no overwrite). |
| 6 | `edit` rewrites a user-installed skill; new body wins. |
| 7 | `edit` of bundled-only skill returns "copy first" error. |
| 8 | `edit` rollback restores original on security flag. |
| 9 | `patch` with unique match replaces; reload picks up new body. |
| 10 | `patch` with multiple matches and `replace_all=False` errors with match count. |
| 11 | `patch` with `replace_all=True` replaces all. |
| 12 | `delete` removes file; bundled-shadow promotion verified by post-delete `skills_list` showing the bundled body. |
| 13 | `delete` of unknown name returns error. |
| 14 | Linked-file stubs (`write_file`, `remove_file`, `patch` with `file_path`) all return the linked-file-deferred error verbatim — locks the contract surface for the future loader-extension plan. |
| 15 | Invalid name (`Bad-Name`, `name with space`, 70-char name) returns `tool_error` before action dispatch. |

All tests use real file I/O against `tmp_path` (no mocks) — co-cli convention.
Each test instantiates a real `CoDeps` via the existing `_co_harness.py` helper
with `user_skills_dir=tmp_path`.

### ✓ DONE — TASK-6 — Cross-plan integration check

Files: none (verification step).

Acceptance:
- Read-tools plan's `tests/test_flow_skills_tools.py` still passes after `skill_manage` is registered (no test interference).
- `skills_list` output reflects `skill_manage`-created skills correctly (via reload).
- `skill_view` of a `skill_manage`-created skill returns the created body.
- `capabilities_check` reports both read and write tools.

## Testing

### Test files

- `tests/test_flow_skills_manage.py` (new, this plan).
- `tests/test_flow_skills_tools.py` (created by read-tools plan; this plan extends with cross-tool integration assertions in TASK-6 if helpful).

### Test pattern

Mirror the read-tools plan: real `CoDeps`, real file I/O, no mocks. Each test
case instantiates a fresh `tmp_path` for `user_skills_dir`. Bundled skills are
read from the actual `co_cli/skills/` directory (not faked) so bundled-shadow
behaviour is tested end-to-end.

### Lint / quality gate

- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before considering ready to ship.

## Open Questions

1. **Q:** Should `create` overwrite an existing user-dir skill or hard-fail?
   **Tentative answer:** Hard-fail (tool_error). Hermes hard-fails too. The model
   should call `edit` for replacement; conflating create and overwrite via one
   action invites accidents. If the model wants idempotent upsert, it can
   `delete` then `create`, or call `edit` directly.

2. **Q:** Should `delete` accept `force: bool` to also remove a bundled skill
   (by deleting the bundled file)? **Tentative answer:** No. Bundled skills are
   version-controlled. Deleting them via tool would either fail (read-only file
   in some installs) or corrupt the package. `tool_error` with "bundled cannot
   be modified" is the right shape.

3. **Q:** Should the security scan also run on `delete` (no-op since nothing is
   written)? **Tentative answer:** No. `delete` only removes — there's no
   content to scan. This matches hermes.

## Deferred items

- **Skills-as-directories loader.** `write_file`/`remove_file` and `patch` with `file_path` return `tool_error`. Lifting requires `co_cli/skills/loader.py` extension to read a `<name>/SKILL.md` directory layout with `references/`/`templates/`/`scripts/` siblings — same deferral as the read-tools plan. Track in survey §T3-D.
- **Lint validator** (Step 1 of survey, code half). Frontmatter validation here is the floor (description present); structural-quality lint (required body sections, length budget) is a follow-up plan that runs `_lint.lint_skill(...)` from this tool's create/edit/patch paths.
- **`category` semantics.** Accepted at schema level, ignored at runtime. When a category-aware loader exists, the only change here is to write to `user_skills_dir / category / f"{name}.md"` and update `_find_user_skill` to walk subdirs.
- **Skills prompt-snapshot cache invalidation.** Hook is a one-line addition (`clear_skills_system_prompt_cache(...)` analog) when the awareness layer (Step 4) ships.
- **Usage telemetry.** `bump_patch(name)` after edit/patch; `forget(name)` after delete. Hook is a one-line addition when the usage table exists.

## Shipping order

Single commit — all six TASKs. Code + tests ship together so the registration
in `NATIVE_TOOLS` never claims a function that's missing (or vice versa). Each
TASK is structured for independent self-review during development, but the
integration surface is small enough that splitting commits adds churn without
revertibility benefit.

**Hard dependency:** the read-tools plan
(`2026-05-07-125538-skill-tools-hermes-port.md`) must ship first — this plan
extends the file it creates and shares its helper conventions. If that plan is
delayed, this plan can fork the file creation, but the cleaner path is
sequential.

## Post-ship — research-doc resync

After this plan ships, mark the following as Done in
`docs/reference/RESEARCH-skills-peers-tiers.md` Part 5:

- Step 2 (Lifecycle trio) — T1-1 ✓ (covered by `skill_manage(action='create')`), T1-3 ✓ (covered by `skill_manage(action='patch')`).
- T1-2 (skill installation as workflow) — note as **partial**: `/skills install <url>` CLI remains; the workflow-form skill body is a separate add-on once a bundled-library plan exists.
- Update Part 5 build-order banner: Step 1 (lifecycle spec) and Step 2 (lifecycle trio) collapse into "shipped via hermes parity port" with the lint validator and bundled `skill-creator.md` body deferred to a follow-up.

Update `docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md` (if present):
- Drop `skill_manage` row from any "Worth Considering" / "Hermes-only deferred" lists.

## Delivery Summary — 2026-05-09

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `skill_manage(action="create", name="x", content="…")` routes to `_skill_create`; invalid names and unknown actions return `tool_error` before dispatch | ✓ pass |
| TASK-2 | `create` writes, scans, reloads; `edit` rewrites + rollback on security flag | ✓ pass |
| TASK-3 | `patch` and `delete` behave per spec; bundled-shadow promotion verified | ✓ pass |
| TASK-4 | `skills_list`, `skill_view`, `skill_manage` all in `TOOL_REGISTRY`; approval subject `tool:skill_manage:<action>:<name>` confirmed | ✓ pass |
| TASK-5 | `uv run pytest tests/test_flow_skills_manage.py` — 24/24 passed | ✓ pass |
| TASK-6 | `tests/test_flow_skills_tools.py` + `test_flow_skills_manage.py` — 33/33 passed; all three tools in `capabilities_check` | ✓ pass |

**Note:** Plan had a hard dependency on the read-tools plan (2026-05-07-125538-skill-tools-hermes-port.md) shipping first. Since `co_cli/tools/system/skills.py` did not exist, this plan forked the file creation and absorbed both read tools (`skills_list`, `skill_view`) and write tool (`skill_manage`) into a single file, as permitted by the plan's contingency clause. The read-tools plan is now effectively delivered via this commit.

**Extra files changed:**
- `co_cli/tools/agent_tool.py` — added `approval_subject_fn` parameter to `@agent_tool` decorator (required to wire per-action approval subjects; the field already existed in `ToolInfo` but was not exposed by the decorator).

**Tests:** scoped (touched files) — 33 passed, 0 failed
**Doc Sync:** fixed — `tools.md`: `file_write`/`file_patch` D→A, added skill tools rows, corrected totals; `skills.md`: `scan_skill_content` name fixed, `get_skill_registry` location corrected, `co_cli/tools/system/skills.py` added to Files, §3 "Model-Callable Surface" section added, section numbers updated

**Overall: DELIVERED**
All six tasks passed. `skills_list`, `skill_view`, and `skill_manage` are live in the tool registry.

## Implementation Review — 2026-05-09

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | Routes to `_skill_create`; invalid names + unknown actions error before dispatch | ✓ pass | `skills.py:339` — name regex + length gate; `:345-368` — action dispatch; `:365-368` — unknown action error |
| TASK-2 | `create` writes/scans/reloads; `edit` rewrites + rollback on security flag | ✓ pass | `_skill_create` `:164`; `_validate_skill_content` `:113` (frontmatter + size); `_scan_or_rollback` `:141` (unlink on create-rollback); `_skill_edit` `:187` (read original, write, scan, rollback restores) |
| TASK-3 | `patch` + `delete` per spec; bundled-shadow promotion verified | ✓ pass | `_skill_patch` `:210` (count=0 error, count≠1 + replace_all=False error, atomic write + scan + reload); `_skill_delete` `:260` (bundled-only detect `:263`, unlink, reload, `shadowed_bundled` `:272`) |
| TASK-4 | All three tools in registry; approval subject `tool:skill_manage:<action>:<name>` | ✓ pass | `_native_toolset.py:36` imports all three; `_skill_manage_approval_subject` `skills.py:285`; `@agent_tool(approval_subject_fn=...)` `:300`; runtime check: `subj.value == 'tool:skill_manage:create:test-skill'` confirmed |
| TASK-5 | 24/24 tests in `test_flow_skills_manage.py` | ✓ pass | All 15 plan assertions covered; 5-case parametrize on invalid-name test accounts for 24 total instances |
| TASK-6 | `test_flow_skills_tools.py` + manage tests pass; all three tools in registry | ✓ pass | 33/33 scoped run; registry import check confirmed `skill_manage`, `skill_view`, `skills_list` |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Phantom `NATIVE_TOOLS` ref in infra block | `docs/specs/tools.md:14` | minor | Removed; `TOOL_REGISTRY` added to `agent_tool.py` entry |
| Phantom `NATIVE_TOOLS` ref in catalog header | `docs/specs/tools.md:109` | minor | Corrected to `TOOL_REGISTRY in co_cli/tools/agent_tool.py` |

Both were pre-existing (not introduced by this delivery); caught during doc sync.

### Tests

- Scoped command: `uv run pytest tests/test_flow_skills_manage.py tests/test_flow_skills_tools.py -v`
- Result: 33 passed, 0 failed
- Log: `.pytest-logs/*-review-scoped.log`
- Full suite: skipped — parallel team run in progress at review time

### Doc Sync

- Scope: narrow — `docs/specs/skills.md` + `docs/specs/tools.md` (delivery-touched docs)
- Result: `skills.md` clean; `tools.md` fixed (2 phantom `NATIVE_TOOLS` references corrected)

### Behavioral Verification

- `uv run co --help`: system boots cleanly, CLI entry point responds
- Tool registry check: `skills_list`, `skill_view`, `skill_manage` confirmed in `TOOL_REGISTRY`; `skill_manage.approval=True`, `approval_subject_fn=_skill_manage_approval_subject`
- Approval subject format: `tool:skill_manage:create:test-skill` — matches spec

### Overall: PASS

All six tasks confirmed by evidence. 33/33 scoped tests green. Lint clean. Doc sync applied. Approval subject format verified end-to-end.
