"""Model-callable skill discovery, inspection, and lifecycle tools.

Hermes-parity port: skills_list + skill_view (read surface) and skill_manage (write surface).
Read surface: hermes-agent/tools/skills_tool.py:1440-1512.
Write surface: hermes-agent/tools/skill_manager_tool.py:647-720.

Co-cli's flat-file skill model degenerates file_path and linked_files to stubs for now.
"""

import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import ApprovalKindEnum, ApprovalSubject, CoDeps, VisibilityPolicyEnum
from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.skills.loader import load_skills, scan_skill_content
from co_cli.skills.registry import get_skill_registry, set_skill_commands
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output

# ---------------------------------------------------------------------------
# Read tools — skills_list + skill_view
# ---------------------------------------------------------------------------


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def skills_list(
    ctx: RunContext[CoDeps],
    category: str | None = None,
) -> ToolReturn:
    """List available skills (name + description). Use skill_view(name) to load full content.

    Skills with disable-model-invocation: true in frontmatter are excluded.

    Args:
        category: Optional category filter. Co-cli skills have no category field today;
            this filter is accepted for hermes parity and is a pass-through.
    """
    entries = get_skill_registry(ctx.deps.skill_commands)
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

    First call (no file_path) returns the skill body and an empty linked_files dict.
    Co-cli's flat-file skill model has no linked files today; any non-None file_path
    returns a tool_error.

    Args:
        name: Skill name. Plugin-qualified form 'plugin:skill' is accepted —
            co-cli has no plugin namespace, so the prefix is dropped.
        file_path: Linked file path within the skill. Not supported in co-cli today.
    """
    lookup = name.split(":", 1)[1] if ":" in name else name
    skill = ctx.deps.skill_commands.get(lookup)
    if skill is None:
        return tool_error(f"skill_view: unknown skill {name!r}.", ctx=ctx)
    if skill.disable_model_invocation:
        return tool_error(f"skill_view: skill {name!r} is not model-invocable.", ctx=ctx)
    if file_path is not None:
        return tool_error(f"skill_view: skill {name!r} has no linked files.", ctx=ctx)
    return tool_output(skill.body, ctx=ctx, name=lookup, linked_files={})


# ---------------------------------------------------------------------------
# skill_manage — write surface
# ---------------------------------------------------------------------------

_NAME_RE: re.Pattern = re.compile(r"^[a-z0-9_-]+$")
_MAX_DESCRIPTION_CHARS: int = 1024
_MAX_SKILL_CHARS: int = 100_000

_LINKED_FILE_ERROR = (
    "Linked files (file_path) are not yet supported in co-cli. "
    "SKILL.md is the only writable target. "
    "Track this gap in RESEARCH-skills-peers-tiers.md §T3-D."
)


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


def _atomic_write_skill(path: Path, content: str) -> None:
    """Write content to path atomically via a temp-file rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".md.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _scan_or_rollback(path: Path, content: str, original: str | None) -> str | None:
    """Scan content for security patterns; if flagged restore original and return error string."""
    flags = scan_skill_content(content)
    if not flags:
        return None
    if original is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write_skill(path, original)
    pattern_names = ", ".join(sorted({f.split("]")[0].lstrip("[") for f in flags}))
    return f"Security scan blocked write: matched pattern(s): {pattern_names}."


def _reload_skills(ctx: RunContext[CoDeps]) -> None:
    """Reload skills from disk into deps.skill_commands."""
    new_skills = load_skills(
        ctx.deps.skills_dir,
        ctx.deps.config,
        user_skills_dir=ctx.deps.user_skills_dir,
    )
    set_skill_commands(new_skills, ctx.deps)


def _skill_create(
    ctx: RunContext[CoDeps], name: str, content: str, category: str | None
) -> ToolReturn:
    err = _validate_skill_content(content)
    if err:
        return tool_error(err, ctx=ctx)
    if _find_user_skill(ctx.deps, name) is not None:
        return tool_error(
            f"Skill {name!r} already exists in user skills dir. Use 'edit' to replace it.",
            ctx=ctx,
        )
    path = ctx.deps.user_skills_dir / f"{name}.md"
    _atomic_write_skill(path, content)
    scan_err = _scan_or_rollback(path, content, original=None)
    if scan_err:
        return tool_error(scan_err, ctx=ctx)
    _reload_skills(ctx)
    result: dict = {"success": True, "message": f"Skill {name!r} created.", "path": str(path)}
    if category:
        result["category_ignored"] = True
    return tool_output(json.dumps(result), ctx=ctx)


def _skill_edit(ctx: RunContext[CoDeps], name: str, content: str) -> ToolReturn:
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
    _atomic_write_skill(path, content)
    scan_err = _scan_or_rollback(path, content, original=original)
    if scan_err:
        return tool_error(scan_err, ctx=ctx)
    _reload_skills(ctx)
    return tool_output(
        json.dumps({"success": True, "message": f"Skill {name!r} updated.", "path": str(path)}),
        ctx=ctx,
    )


def _skill_patch(
    ctx: RunContext[CoDeps],
    name: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> ToolReturn:
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
    _atomic_write_skill(path, new_content)
    scan_err = _scan_or_rollback(path, new_content, original=original)
    if scan_err:
        return tool_error(scan_err, ctx=ctx)
    _reload_skills(ctx)
    replaced_count = count if replace_all else 1
    return tool_output(
        json.dumps(
            {
                "success": True,
                "message": f"Skill {name!r} patched ({replaced_count} replacement(s)).",
                "path": str(path),
            }
        ),
        ctx=ctx,
    )


def _skill_delete(ctx: RunContext[CoDeps], name: str) -> ToolReturn:
    path = _find_user_skill(ctx.deps, name)
    if path is None:
        if (ctx.deps.skills_dir / f"{name}.md").exists():
            return tool_error(
                f"Skill {name!r} is bundled and cannot be modified via skill_manage. "
                "Copy it to ~/.co-cli/skills/ first.",
                ctx=ctx,
            )
        return tool_error(f"Skill {name!r} not found in user skills dir.", ctx=ctx)
    path.unlink()
    _reload_skills(ctx)
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


def _skill_manage_approval_subject(args: dict) -> ApprovalSubject:
    action = args.get("action", "unknown")
    name = args.get("name", "unknown")
    return ApprovalSubject(
        tool_name="skill_manage",
        kind=ApprovalKindEnum.TOOL,
        value=f"tool:skill_manage:{action}:{name}",
        display=f"skill_manage(action={action!r}, name={name!r})",
        can_remember=True,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    approval=True,
    approval_subject_fn=_skill_manage_approval_subject,
)
async def skill_manage(
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
) -> ToolReturn:
    """Create, edit, patch, or delete a user-installed skill.

    Use skills_list() to browse the skill inventory and skill_view(name) to inspect
    current content before editing. Bundled skills are read-only; copy to
    ~/.co-cli/skills/ first to modify them.

    Actions:
      create      Write a new skill (requires content with valid frontmatter + description).
      edit        Replace an existing user-installed skill's full content.
      patch       Surgical find-and-replace within a skill body.
      delete      Remove a user-installed skill.
      write_file  Not yet supported — returns error.
      remove_file Not yet supported — returns error.

    Args:
        action:      One of the six actions above.
        name:        Skill name (lowercase letters, digits, hyphens, underscores; max 64 chars).
        content:     Full SKILL.md content for create/edit (frontmatter + body).
        category:    Category hint (accepted for hermes parity; silently ignored today).
        file_path:   Linked-file path within the skill (not yet supported).
        file_content: Linked-file body (not yet supported).
        old_string:  Text to find for patch.
        new_string:  Replacement text for patch.
        replace_all: When True replace all occurrences; otherwise require exactly one match.
    """
    if not _NAME_RE.match(name) or len(name) > 64:
        return tool_error(
            f"Invalid skill name {name!r}. "
            "Name must be lowercase letters, digits, hyphens, or underscores; max 64 chars.",
            ctx=ctx,
        )
    if action == "create":
        if not content:
            return tool_error("content is required for 'create'.", ctx=ctx)
        return _skill_create(ctx, name, content, category)
    if action == "edit":
        if not content:
            return tool_error("content is required for 'edit'.", ctx=ctx)
        return _skill_edit(ctx, name, content)
    if action == "patch":
        if not old_string:
            return tool_error("old_string is required for 'patch'.", ctx=ctx)
        if new_string is None:
            return tool_error("new_string is required for 'patch'.", ctx=ctx)
        if file_path:
            return tool_error(_LINKED_FILE_ERROR, ctx=ctx)
        return _skill_patch(ctx, name, old_string, new_string, replace_all)
    if action == "delete":
        return _skill_delete(ctx, name)
    if action in ("write_file", "remove_file"):
        return tool_error(_LINKED_FILE_ERROR, ctx=ctx)
    return tool_error(
        f"Unknown action {action!r}. Valid actions: create, edit, patch, delete.",
        ctx=ctx,
    )
