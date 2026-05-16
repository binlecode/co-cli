"""Lint validator for skill SKILL.md files -- advisory authoring checks.

Four runtime rules (R1-R4), all advisory. Per docs/specs/skills.md §6,
lint never blocks load; integrity-blocking checks live in
co_cli.tools.system.skills._validate_skill_content.

Rules R1-R3 are hermes parity (tools/skill_manager_tool.py:_validate_frontmatter).
R4 is co-cli-specific: a soft body-size warning at 8000 chars to flag overly
broad skills that should be split.

A separate lint_bundled_extras() function enforces the no-TODO-marker rule on
the shipped reference library only (co_cli/skills/*.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from co_cli.memory.frontmatter import parse_frontmatter

_DESCRIPTION_MAX_CHARS = 1024
_BODY_WARNING_CHARS = 8000
_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


@dataclass
class LintFinding:
    rule: str
    message: str
    line: int


def lint_skill(content: str, path: Path | None = None) -> list[LintFinding]:
    """Run advisory rules R1-R4. Returns findings list (empty = clean).

    The `path` parameter is accepted for caller convenience but not used.
    """
    del path

    findings: list[LintFinding] = []

    if not content.startswith("---"):
        findings.append(
            LintFinding(
                rule="R1", message="frontmatter missing -- file must open with ---", line=0
            )
        )
        meta, body = {}, content
    else:
        meta, body = parse_frontmatter(content)

    description = meta.get("description", "")
    desc_str = str(description).strip() if description is not None else ""
    if not desc_str:
        findings.append(
            LintFinding(rule="R2", message="frontmatter 'description' missing or empty", line=0)
        )
    elif len(desc_str) > _DESCRIPTION_MAX_CHARS:
        findings.append(
            LintFinding(
                rule="R2",
                message=f"'description' exceeds {_DESCRIPTION_MAX_CHARS} chars ({len(desc_str)})",
                line=0,
            )
        )

    body_lines = body.splitlines()
    if not any(line.startswith("# ") for line in body_lines):
        findings.append(LintFinding(rule="R3", message="H1 title missing in body", line=0))

    if len(body) > _BODY_WARNING_CHARS:
        findings.append(
            LintFinding(
                rule="R4",
                message=(
                    f"body exceeds {_BODY_WARNING_CHARS} chars ({len(body)}); "
                    "consider splitting into a narrower skill"
                ),
                line=0,
            )
        )

    return findings


def lint_bundled_extras(content: str) -> list[LintFinding]:
    """Extra rule for the shipped reference library: no TODO/FIXME/XXX markers.

    Bundled skills are reference-quality and must not carry in-progress markers.
    User-installed skills are exempt; this check runs only from the bundled
    library test gate.
    """
    findings: list[LintFinding] = []
    if content.startswith("---"):
        _, body = parse_frontmatter(content)
    else:
        body = content
    for i, line in enumerate(body.splitlines()):
        match = _TODO_RE.search(line)
        if match:
            findings.append(
                LintFinding(
                    rule="B1",
                    message=f"forbidden marker {match.group()!r} at line {i + 1}",
                    line=i + 1,
                )
            )
            break
    return findings
