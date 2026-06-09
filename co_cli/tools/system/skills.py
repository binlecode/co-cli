"""Model-callable skill discovery, inspection, and lifecycle tools.

Hermes-parity port: skill_view (read surface) and the monomorphic write tools
skill_create / skill_edit / skill_patch / skill_delete.
Read surface: hermes-agent/tools/skills_tool.py:1440-1512.
Write surface: hermes-agent/tools/skill_manager_tool.py:647-720.

Co-cli's flat-file skill model has no linked files; skill_view returns the SKILL.md body only.
"""

import json
import math
import re
from collections.abc import Callable
from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import ApprovalKindEnum, ApprovalSubject, CoDeps, VisibilityPolicyEnum
from co_cli.fileio.atomic import atomic_write_text
from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.skills.lint import lint_skill
from co_cli.skills.loader import scan_skill_content
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output

# ---------------------------------------------------------------------------
# Read tools — skill_view
# ---------------------------------------------------------------------------


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_concurrent_safe=True,
    spill_threshold_chars=math.inf,
)
async def skill_view(
    ctx: RunContext[CoDeps],
    name: str,
) -> ToolReturn:
    """Load a skill's full SKILL.md content.

    Call before skill_edit(name) or skill_patch(name, ...) to read current
    content — don't edit blind.

    Args:
        name: Skill name, e.g. "deep-research" (the filename_stem from the skill manifest).
    """
    skill = ctx.deps.skill_catalog.get(name)
    if skill is None:
        return tool_error(f"skill_view: unknown skill {name!r}.", ctx=ctx)
    if skill.disable_model_invocation:
        return tool_error(f"skill_view: skill {name!r} is not model-invocable.", ctx=ctx)
    from co_cli.skills.usage import bump_recall, bump_use, bump_view

    bump_view(ctx.deps, name)
    bump_use(ctx.deps, name)
    bump_recall(ctx.deps, name)
    return tool_output(skill.body, ctx=ctx, name=name)


# ---------------------------------------------------------------------------
# skill write surface — skill_create / skill_edit / skill_patch / skill_delete
# ---------------------------------------------------------------------------

_NAME_RE: re.Pattern = re.compile(r"^[a-z0-9_-]+$")
_MAX_DESCRIPTION_CHARS: int = 1024
_MAX_SKILL_CHARS: int = 50_000


def _find_user_skill(deps: CoDeps, name: str) -> Path | None:
    """Return Path under user_skills_dir if <name>.md exists, else None."""
    path = deps.user_skills_dir / f"{name}.md"
    return path if path.exists() else None


def _validate_skill_content(content: str) -> str | None:
    """Return error message or None. Checks: size ≤ MAX_SKILL_CHARS, description present and ≤ 1024."""
    if len(content) > _MAX_SKILL_CHARS:
        return f"Content too large ({len(content):,} chars; max {_MAX_SKILL_CHARS:,})."
    meta, _ = parse_frontmatter(content)
    description = meta.get("description", "")
    if not description:
        return "Skill content must have a non-empty 'description' in frontmatter."
    if len(str(description)) > _MAX_DESCRIPTION_CHARS:
        return (
            f"Description too long ({len(str(description)):,} chars; "
            f"max {_MAX_DESCRIPTION_CHARS})."
        )
    return None


def _scan_or_rollback(path: Path, content: str, original: str | None) -> str | None:
    """Scan content for security patterns; if flagged restore original and return error string."""
    flags = scan_skill_content(content)
    if not flags:
        return None
    if original is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write_text(path, original)
    pattern_names = ", ".join(sorted({f.split("]")[0].lstrip("[") for f in flags}))
    return f"Security scan blocked write: matched pattern(s): {pattern_names}."


def _reload_skills(ctx: RunContext[CoDeps]) -> None:
    """Reload skills from disk and reindex into MemoryStore."""
    from co_cli.skills.lifecycle import refresh_skills

    refresh_skills(ctx.deps)


def _lint_warnings(content: str) -> list[str]:
    """Run advisory lint and format findings for tool output.

    Lint never blocks writes; integrity checks live in _validate_skill_content.
    """
    return [f"{f.rule}: {f.message}" for f in lint_skill(content)]


def _skill_create(ctx: RunContext[CoDeps], name: str, content: str | None) -> ToolReturn:
    if not content:
        return tool_error("content is required for 'create'.", ctx=ctx)
    err = _validate_skill_content(content)
    if err:
        return tool_error(err, ctx=ctx)
    if _find_user_skill(ctx.deps, name) is not None:
        return tool_error(
            f"Skill {name!r} already exists in user skills dir. Use 'edit' to replace it.",
            ctx=ctx,
        )
    path = ctx.deps.user_skills_dir / f"{name}.md"
    atomic_write_text(path, content)
    scan_err = _scan_or_rollback(path, content, original=None)
    if scan_err:
        return tool_error(scan_err, ctx=ctx)
    _reload_skills(ctx)
    from co_cli.skills.usage import record_create

    record_create(ctx.deps, name)
    result: dict = {"success": True, "message": f"Skill {name!r} created.", "path": str(path)}
    warnings = _lint_warnings(content)
    if warnings:
        result["lint_warnings"] = warnings
    if len(ctx.deps.skill_catalog) >= 30:
        result["size_warning"] = (
            f"Skill count is now {len(ctx.deps.skill_catalog)}; "
            "consider reviewing and pruning unused skills."
        )
    ctx.deps.session.model_requests_since_skill_review = 0
    return tool_output(json.dumps(result), ctx=ctx)


def _skill_edit(ctx: RunContext[CoDeps], name: str, content: str | None) -> ToolReturn:
    if not content:
        return tool_error("content is required for 'edit'.", ctx=ctx)
    path = _find_user_skill(ctx.deps, name)
    if path is None:
        return tool_error(
            f"Skill {name!r} not found in user skills dir. "
            "Use 'create' for a new skill, or copy a bundled skill to ~/.co-cli/skills/ first.",
            ctx=ctx,
        )
    err = _validate_skill_content(content)
    if err:
        return tool_error(err, ctx=ctx)
    original = path.read_text(encoding="utf-8")
    atomic_write_text(path, content)
    scan_err = _scan_or_rollback(path, content, original=original)
    if scan_err:
        return tool_error(scan_err, ctx=ctx)
    _reload_skills(ctx)
    from co_cli.skills.usage import bump_patch

    bump_patch(ctx.deps, name)
    result: dict = {"success": True, "message": f"Skill {name!r} updated.", "path": str(path)}
    warnings = _lint_warnings(content)
    if warnings:
        result["lint_warnings"] = warnings
    ctx.deps.session.model_requests_since_skill_review = 0
    return tool_output(json.dumps(result), ctx=ctx)


def _skill_patch(
    ctx: RunContext[CoDeps],
    name: str,
    old_string: str | None,
    new_string: str | None,
    replace_all: bool,
) -> ToolReturn:
    if not old_string:
        return tool_error("old_string is required for 'patch'.", ctx=ctx)
    if new_string is None:
        return tool_error("new_string is required for 'patch'.", ctx=ctx)
    path = _find_user_skill(ctx.deps, name)
    if path is None:
        return tool_error(
            f"Skill {name!r} not found in user skills dir. "
            "Bundled skills cannot be patched; copy to ~/.co-cli/skills/ first.",
            ctx=ctx,
        )
    original = path.read_text(encoding="utf-8")
    count = original.count(old_string)
    if count == 0:
        return tool_error(
            f"patch: old_string not found in skill {name!r} (0 matches).",
            ctx=ctx,
        )
    if not replace_all and count != 1:
        return tool_error(
            f"patch: old_string matches {count} times in skill {name!r}; "
            "use replace_all=true to replace all, or narrow old_string for a unique match.",
            ctx=ctx,
        )
    new_content = (
        original.replace(old_string, new_string)
        if replace_all
        else original.replace(old_string, new_string, 1)
    )
    atomic_write_text(path, new_content)
    scan_err = _scan_or_rollback(path, new_content, original=original)
    if scan_err:
        return tool_error(scan_err, ctx=ctx)
    _reload_skills(ctx)
    from co_cli.skills.usage import bump_patch

    bump_patch(ctx.deps, name)
    replaced_count = count if replace_all else 1
    result: dict = {
        "success": True,
        "message": f"Skill {name!r} patched ({replaced_count} replacement(s)).",
        "path": str(path),
    }
    warnings = _lint_warnings(new_content)
    if warnings:
        result["lint_warnings"] = warnings
    ctx.deps.session.model_requests_since_skill_review = 0
    return tool_output(json.dumps(result), ctx=ctx)


def _skill_delete(ctx: RunContext[CoDeps], name: str) -> ToolReturn:
    path = _find_user_skill(ctx.deps, name)
    if path is None:
        if (ctx.deps.skills_dir / f"{name}.md").exists():
            return tool_error(
                f"Skill {name!r} is bundled and cannot be deleted. "
                "Copy it to ~/.co-cli/skills/ first.",
                ctx=ctx,
            )
        return tool_error(f"Skill {name!r} not found in user skills dir.", ctx=ctx)
    path.unlink()
    _reload_skills(ctx)
    from co_cli.skills.usage import forget

    forget(ctx.deps, name)
    shadowed_bundled = (ctx.deps.skills_dir / f"{name}.md").exists()
    return tool_output(
        json.dumps(
            {
                "success": True,
                "message": f"Skill {name!r} deleted.",
                "shadowed_bundled": shadowed_bundled,
            }
        ),
        ctx=ctx,
    )


def _subject_fn(tool_name: str, arg_key: str) -> Callable[[dict], ApprovalSubject]:
    """Build an approval-subject fn that keys on a single name-bearing arg."""

    def subject(args: dict) -> ApprovalSubject:
        name = args.get(arg_key, "unknown")
        return ApprovalSubject(
            tool_name=tool_name,
            kind=ApprovalKindEnum.TOOL,
            value=f"tool:{tool_name}:{name}",
            display=f"{tool_name}(name={name!r})",
            can_remember=True,
        )

    return subject


def _require_valid_name(ctx: RunContext[CoDeps], name: str) -> ToolReturn | None:
    """Return a tool_error when the skill name is invalid, else None."""
    if not _NAME_RE.match(name) or len(name) > 64:
        return tool_error(
            f"Invalid skill name {name!r}. "
            "Name must be lowercase letters, digits, hyphens, or underscores; max 64 chars.",
            ctx=ctx,
        )
    return None


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("skill_create", "name"),
)
async def skill_create(
    ctx: RunContext[CoDeps],
    name: str,
    content: str,
) -> ToolReturn:
    """Create a new user-installed skill.

    Create when: a multi-step procedure (3+ coherent steps) succeeded and is
    likely to recur for the same kind of task — or the user explicitly asks to
    save a workflow. Name by task type, not the specific instance.

    offer-to-save: after difficult or iterative work, briefly offer the user a
    save — "Want me to save this as a /<task-type> skill?" Confirm before
    creating on their behalf.

    Args:
        name: Skill name (lowercase letters, digits, hyphens, underscores; max 64).
        content: Full SKILL.md content — must conform to skill.md §6: frontmatter
            with a non-empty description, H1, **Invocation:** line, at least one
            ## Phase N — <name> section.
    """
    err = _require_valid_name(ctx, name)
    if err is not None:
        return err
    return _skill_create(ctx, name, content)


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("skill_edit", "name"),
)
async def skill_edit(
    ctx: RunContext[CoDeps],
    name: str,
    content: str,
) -> ToolReturn:
    """Replace an existing user-installed skill's full content.

    Read first: call skill_view(name) before editing to inspect current content
    — don't edit blind. Use skill_patch for a surgical find-and-replace; use this
    for a structural overhaul.

    Bundled skills are read-only; copy to ~/.co-cli/skills/ first to modify them.

    Args:
        name: Skill name (the filename_stem of an existing user skill).
        content: Full replacement SKILL.md content (frontmatter + body).
    """
    err = _require_valid_name(ctx, name)
    if err is not None:
        return err
    return _skill_edit(ctx, name, content)


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("skill_patch", "name"),
)
async def skill_patch(
    ctx: RunContext[CoDeps],
    name: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolReturn:
    """Surgical find-and-replace within an existing skill body.

    patch immediately when: you loaded and executed a skill and hit an issue not
    covered by it — wrong command, missing step, stale output. Fix the skill
    before finishing the task; don't leave it degraded. Call skill_view(name)
    first to read current content.

    Bundled skills are read-only; copy to ~/.co-cli/skills/ first to modify them.

    Args:
        name: Skill name (the filename_stem of an existing user skill).
        old_string: Text to find. Must match exactly once unless replace_all=True.
        new_string: Replacement text.
        replace_all: Replace every match (default False = require exactly one match).
    """
    err = _require_valid_name(ctx, name)
    if err is not None:
        return err
    return _skill_patch(ctx, name, old_string, new_string, replace_all)


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("skill_delete", "name"),
)
async def skill_delete(
    ctx: RunContext[CoDeps],
    name: str,
) -> ToolReturn:
    """Delete a user-installed skill.

    Bundled skills are read-only and cannot be deleted; only user skills under
    ~/.co-cli/skills/ can be removed.

    Args:
        name: Skill name (the filename_stem of an existing user skill).
    """
    err = _require_valid_name(ctx, name)
    if err is not None:
        return err
    return _skill_delete(ctx, name)
