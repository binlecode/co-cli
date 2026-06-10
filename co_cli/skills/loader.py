"""Skill loading domain logic — namespace-agnostic.

Loads .md skill files from bundled and user directories. Does not know about
slash-command namespaces, reserved names, or the CLI relay layer. Callers that
need to filter by reserved name should apply filter_namespace_conflicts()
(defined in co_cli.commands.registry) after calling load_skills().
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.skills.skill_types import SkillInfo

logger = logging.getLogger(__name__)

# Env vars that skill-env may never override — security boundary.
_SKILL_ENV_BLOCKED: frozenset[str] = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "HOME",
        "USER",
        "SHELL",
        "SUDO_UID",
    }
)

# Static security patterns for skill content scanning.
_SKILL_SCAN_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "credential_exfil",
        re.compile(
            r"(curl|wget|nc)\s[^\n]*\$\{?[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD|API)[A-Z_]*\}?",
            re.IGNORECASE,
        ),
    ),
    ("pipe_to_shell", re.compile(r"(curl|wget)\s[^|\n]+\|\s*(ba)?sh", re.IGNORECASE)),
    (
        "destructive_shell",
        re.compile(
            r"rm\s+-rf\s*/|dd\s+if=/dev/(zero|random|urandom)|:\(\)\s*\{",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_injection",
        re.compile(
            r"ignore\s+(all\s+)?previous\s+instructions|you\s+are\s+now\s+(a|an)\s",
            re.IGNORECASE,
        ),
    ),
]


def scan_skill_content(content: str) -> list[str]:
    """Scan skill content for security patterns.

    Returns a list of tagged warning strings. Empty list = content is clean.
    Each entry has the form '[tag] line N: <line>'.
    """
    warnings: list[str] = []
    for i, line in enumerate(content.splitlines(), 1):
        for tag, pattern in _SKILL_SCAN_PATTERNS:
            if pattern.search(line):
                warnings.append(f"[{tag}] line {i}: {line}")
    return warnings


def _load_skill_file(
    path: Path,
    result: dict[str, SkillInfo],
    *,
    root: Path,
    scan: bool = True,
    errors: list[str] | None = None,
) -> None:
    """Parse a single skill .md file and add to result dict if valid."""
    if path.is_symlink():
        logger.warning(f"Symlink skill rejected — skipping {path}")
        return
    name = path.parent.name
    try:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)

        if scan:
            for w in scan_skill_content(text):
                logger.warning(f"Security scan warning in {path}: {w}")

        raw_env = meta.get("skill-env", {})
        skill_env: dict[str, str] = {}
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                if isinstance(k, str) and isinstance(v, str) and k not in _SKILL_ENV_BLOCKED:
                    skill_env[k] = v

        result[name] = SkillInfo(
            name=name,
            description=meta.get("description", ""),
            body=body.strip(),
            argument_hint=meta.get("argument-hint", ""),
            user_invocable=meta.get("user-invocable", True),
            disable_model_invocation=meta.get("disable-model-invocation", False),
            skill_env=skill_env,
            path=path,
        )
    except Exception as e:
        msg = f"Skill {path.name!r} skipped: {e}"
        logger.warning(msg)
        if errors is not None:
            errors.append(msg)


def load_skills(
    skills_dir: Path,
    *,
    user_skills_dir: Path | None = None,
    errors: list[str] | None = None,
) -> dict[str, SkillInfo]:
    """Scan skills directories and return a dict of SkillInfo objects.

    Load order (lowest to highest precedence):
      1. Co-bundled skills (skills_dir = co_cli/skills/) — version-controlled, not user-editable
      2. User skills (user_skills_dir = ~/.co-cli/skills/) — override bundled on name collision

    Returns every parseable skill that passes skill-internal validation (.md, security scan).
    Reserved-name filtering is the caller's responsibility — apply
    filter_namespace_conflicts() from co_cli.commands.registry after this call.
    """
    result: dict[str, SkillInfo] = {}

    if skills_dir.exists():
        for path in sorted(skills_dir.glob("*/SKILL.md")):
            _load_skill_file(path, result, root=skills_dir, scan=False, errors=errors)

    if user_skills_dir is not None and user_skills_dir.exists():
        for path in sorted(user_skills_dir.glob("*/SKILL.md")):
            _load_skill_file(path, result, root=user_skills_dir, errors=errors)

    return result
