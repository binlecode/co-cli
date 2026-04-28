"""Skill loading domain logic — namespace-agnostic.

Loads .md skill files from bundled and user directories. Does not know about
slash-command namespaces, reserved names, or the CLI relay layer. Callers that
need to filter by reserved name should apply filter_namespace_conflicts()
(defined in co_cli.commands.registry) after calling load_skills().
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.skills.skill_types import SkillConfig

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


def _scan_skill_content(content: str) -> list[str]:
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


def _inject_source_url(content: str, url: str) -> str:
    """Inject or update source-url field in skill frontmatter."""
    if not content.startswith("---\n"):
        return f"---\nsource-url: {url}\n---\n{content}"
    rest = content[4:]
    close_match = re.search(r"\n---(\n|$)", rest)
    if close_match is None:
        return f"---\nsource-url: {url}\n---\n{content}"
    close_start = close_match.start()
    fm_block = rest[:close_start]
    after_close = rest[close_match.end() :]
    lines = fm_block.splitlines()
    new_lines = []
    replaced = False
    for line in lines:
        if line.startswith("source-url:"):
            new_lines.append(f"source-url: {url}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"source-url: {url}")
    new_fm = "\n".join(new_lines)
    return f"---\n{new_fm}\n---\n{after_close}"


def _check_requires(name: str, requires: dict, settings: Any = None) -> bool:
    """Evaluate the requires block. Returns True when all conditions are met."""
    bins = requires.get("bins", [])
    if bins and not all(shutil.which(b) for b in bins):
        logger.info(f"Skipping skill {name}: requires bins not satisfied: {bins}")
        return False

    any_bins = requires.get("anyBins", [])
    if any_bins and not any(shutil.which(b) for b in any_bins):
        logger.info(f"Skipping skill {name}: requires anyBins not satisfied: {any_bins}")
        return False

    env_vars = requires.get("env", [])
    if env_vars and not all(os.getenv(e) for e in env_vars):
        logger.info(f"Skipping skill {name}: requires env not satisfied: {env_vars}")
        return False

    platforms = requires.get("os", [])
    if platforms and not sys.platform.startswith(tuple(platforms)):
        logger.info(f"Skipping skill {name}: requires os not satisfied: {platforms}")
        return False

    settings_fields = requires.get("settings", [])
    if settings_fields and (
        settings is None or not all(getattr(settings, f, None) for f in settings_fields)
    ):
        logger.info(f"Skipping skill {name}: requires settings not satisfied: {settings_fields}")
        return False

    return True


def _diagnose_requires_failures(requires: dict, settings: Any = None) -> list[str]:
    """Evaluate the requires block and return human-readable failure strings.

    Empty list means all requirements are met.
    """
    failures: list[str] = []

    bins = requires.get("bins", [])
    if bins:
        missing = [b for b in bins if not shutil.which(b)]
        if missing:
            failures.append(f"missing bins: {', '.join(missing)}")

    any_bins = requires.get("anyBins", [])
    if any_bins and not any(shutil.which(b) for b in any_bins):
        failures.append(f"none of anyBins found: {', '.join(any_bins)}")

    env_vars = requires.get("env", [])
    if env_vars:
        missing_env = [e for e in env_vars if not os.getenv(e)]
        if missing_env:
            failures.append(f"missing env vars: {', '.join(missing_env)}")

    platforms = requires.get("os", [])
    if platforms and not sys.platform.startswith(tuple(platforms)):
        failures.append(f"os not satisfied: need {platforms}, got {sys.platform}")

    settings_fields = requires.get("settings", [])
    if settings_fields:
        if settings is None:
            failures.append(f"missing settings: {', '.join(settings_fields)}")
        else:
            missing_settings = [f for f in settings_fields if not getattr(settings, f, None)]
            if missing_settings:
                failures.append(f"missing settings: {', '.join(missing_settings)}")

    return failures


def _is_safe_skill_path(path: Path, root: Path) -> bool:
    """Return True when path is safe to load (not a symlink pointing outside root)."""
    if not path.is_symlink():
        return True
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _load_skill_file(
    path: Path,
    result: dict[str, SkillConfig],
    settings: Any = None,
    *,
    root: Path,
    scan: bool = True,
    errors: list[str] | None = None,
) -> None:
    """Parse a single skill .md file and add to result dict if valid."""
    if not _is_safe_skill_path(path, root):
        logger.warning(
            f"Skill path containment violation — skipping {path} (expected root: {root})"
        )
        return
    name = path.stem
    try:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)

        requires = meta.get("requires", {}) if isinstance(meta.get("requires"), dict) else {}
        if not _check_requires(name, requires, settings):
            return

        if scan:
            for w in _scan_skill_content(text):
                logger.warning(f"Security scan warning in {path}: {w}")

        raw_env = meta.get("skill-env", {})
        skill_env: dict[str, str] = {}
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                if isinstance(k, str) and isinstance(v, str) and k not in _SKILL_ENV_BLOCKED:
                    skill_env[k] = v

        result[name] = SkillConfig(
            name=name,
            description=meta.get("description", ""),
            body=body.strip(),
            argument_hint=meta.get("argument-hint", ""),
            user_invocable=meta.get("user-invocable", True),
            disable_model_invocation=meta.get("disable-model-invocation", False),
            requires=requires,
            skill_env=skill_env,
        )
    except Exception as e:
        msg = f"Skill {path.name!r} skipped: {e}"
        logger.warning(msg)
        if errors is not None:
            errors.append(msg)


def load_skills(
    skills_dir: Path,
    settings: Any = None,
    *,
    user_skills_dir: Path | None = None,
    errors: list[str] | None = None,
) -> dict[str, SkillConfig]:
    """Scan skills directories and return a dict of SkillConfig objects.

    Load order (lowest to highest precedence):
      1. Co-bundled skills (skills_dir = co_cli/skills/) — version-controlled, not user-editable
      2. User skills (user_skills_dir = ~/.co-cli/skills/) — override bundled on name collision

    Returns every parseable skill that passes skill-internal validation (requires, env, .md,
    security scan). Reserved-name filtering is the caller's responsibility — apply
    filter_namespace_conflicts() from co_cli.commands.registry after this call.
    """
    result: dict[str, SkillConfig] = {}

    if skills_dir.exists():
        for path in sorted(skills_dir.glob("*.md")):
            _load_skill_file(path, result, settings, root=skills_dir, scan=False, errors=errors)

    if user_skills_dir is not None and user_skills_dir.exists():
        for path in sorted(user_skills_dir.glob("*.md")):
            _load_skill_file(path, result, settings, root=user_skills_dir, errors=errors)

    return result
