"""Lint validator for skill SKILL.md files -- checks R1-R10."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from co_cli.memory.frontmatter import parse_frontmatter


@dataclass
class LintFinding:
    rule: str
    message: str
    line: int  # 1-indexed; 0 if file-level


_PHASE_HEADER_RE = re.compile(r"^## Phase (\d+) — .+$")
_PHASE_START_RE = re.compile(r"^## Phase ")
_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


def _check_meta(meta: dict, has_frontmatter: bool) -> list[LintFinding]:
    """Check R1-R3: frontmatter presence and description field."""
    findings: list[LintFinding] = []
    if not has_frontmatter:
        findings.append(
            LintFinding(
                rule="R1", message="Frontmatter missing -- file must open with ---", line=0
            )
        )
    description = meta.get("description", "")
    if not description or not str(description).strip():
        findings.append(
            LintFinding(
                rule="R2", message="Frontmatter 'description' field is missing or empty", line=0
            )
        )
    elif len(str(description)) > 1024:
        findings.append(
            LintFinding(
                rule="R3",
                message=f"'description' exceeds 1024 chars ({len(str(description))})",
                line=0,
            )
        )
    return findings


def _check_phase_headers(body_lines: list[str]) -> list[LintFinding]:
    """Check R6-R7: phase section presence and header format."""
    findings: list[LintFinding] = []
    phase_headers = [line for line in body_lines if _PHASE_HEADER_RE.match(line)]
    if not phase_headers:
        findings.append(
            LintFinding(rule="R6", message="No '## Phase N -- <name>' section found", line=0)
        )
    malformed = [
        (i + 1, line)
        for i, line in enumerate(body_lines)
        if _PHASE_START_RE.match(line) and not _PHASE_HEADER_RE.match(line)
    ]
    if malformed:
        first_line_no, first_line = malformed[0]
        findings.append(
            LintFinding(
                rule="R7",
                message=f"Malformed phase header at line {first_line_no}: {first_line!r}",
                line=first_line_no,
            )
        )
    return findings


def _check_phase_sizes(body_lines: list[str]) -> list[LintFinding]:
    """Check R9: each phase section <= 2000 chars."""
    phase_indices = [i for i, line in enumerate(body_lines) if _PHASE_HEADER_RE.match(line)]
    for idx, start in enumerate(phase_indices):
        end = phase_indices[idx + 1] if idx + 1 < len(phase_indices) else len(body_lines)
        segment_text = "\n".join(body_lines[start:end])
        if len(segment_text) > 2000:
            return [
                LintFinding(
                    rule="R9",
                    message=f"Phase section at line {start + 1} exceeds 2000 chars ({len(segment_text)})",
                    line=start + 1,
                )
            ]
    return []


def _check_todo_markers(body_lines: list[str]) -> list[LintFinding]:
    """Check R10: no TODO, FIXME, or XXX markers."""
    for i, line in enumerate(body_lines):
        match = _TODO_RE.search(line)
        if match:
            return [
                LintFinding(
                    rule="R10",
                    message=f"Forbidden marker '{match.group()}' found at line {i + 1}",
                    line=i + 1,
                )
            ]
    return []


def lint_skill(content: str, path: Path | None = None) -> list[LintFinding]:
    """Run all R1-R10 checks. Returns findings list (empty = clean)."""
    has_frontmatter = content.startswith("---")
    if has_frontmatter:
        meta, body = parse_frontmatter(content)
    else:
        meta, body = {}, content

    findings: list[LintFinding] = []
    findings.extend(_check_meta(meta, has_frontmatter))

    body_lines = body.splitlines()

    # R4: H1 title present after frontmatter
    if not any(line.startswith("# ") for line in body_lines):
        findings.append(LintFinding(rule="R4", message="H1 title missing in body", line=0))

    # R5: **Invocation:** line present in first 10 lines of body
    if not any("**Invocation:**" in line for line in body_lines[:10]):
        findings.append(
            LintFinding(
                rule="R5", message="**Invocation:** line missing in first 10 body lines", line=0
            )
        )

    findings.extend(_check_phase_headers(body_lines))

    # R8: Body total <= 8000 chars
    if len(body) > 8000:
        findings.append(
            LintFinding(rule="R8", message=f"Body exceeds 8000 chars ({len(body)})", line=0)
        )

    findings.extend(_check_phase_sizes(body_lines))
    findings.extend(_check_todo_markers(body_lines))

    return findings
