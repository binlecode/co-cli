"""Tests for co_cli.skills.lint — R1-R10 per-rule behavioral coverage."""

from __future__ import annotations

from co_cli.skills.lint import lint_skill

_CLEAN_CONTENT = """\
---
description: Test skill — a clean §6-compliant skill for lint testing.
user-invocable: true
---

# Test Skill

**Invocation:** `/test`

A short opening summary paragraph describing what this skill does.

---

## Phase 1 — Prepare

Step one: gather inputs.
Step two: validate them.

## Phase 2 — Execute

Step one: run the operation.
Step two: verify output.
"""


def test_r1_fires_when_frontmatter_missing() -> None:
    """R1: fires when content does not start with frontmatter block."""
    content = "# My Skill\n\n**Invocation:** `/test`\n\n## Phase 1 — Load\n\nSome steps.\n"
    findings = lint_skill(content)
    assert any(f.rule == "R1" for f in findings)


def test_r2_fires_when_description_missing() -> None:
    """R2: fires when frontmatter has no description field."""
    content = (
        "---\nuser-invocable: true\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\n## Phase 1 — Load\n\nSteps.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R2" for f in findings)


def test_r2_fires_when_description_empty() -> None:
    """R2: fires when description field is present but blank."""
    content = (
        "---\ndescription:    \nuser-invocable: true\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\n## Phase 1 — Load\n\nSteps.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R2" for f in findings)


def test_r3_fires_when_description_over_1024() -> None:
    """R3: fires when description exceeds 1024 characters."""
    long_desc = "x" * 1025
    content = (
        f"---\ndescription: {long_desc}\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\n## Phase 1 — Load\n\nSteps.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R3" for f in findings)


def test_r4_fires_when_no_h1() -> None:
    """R4: fires when body has no H1 title."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "**Invocation:** `/test`\n\n## Phase 1 — Load\n\nSteps.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R4" for f in findings)


def test_r5_fires_when_invocation_line_beyond_10_body_lines() -> None:
    """R5: fires when **Invocation:** line is absent from the first 10 body lines."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n"
        "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6\nLine 7\nLine 8\nLine 9\nLine 10\n"
        "\n**Invocation:** `/test`\n\n"
        "## Phase 1 — Load\n\nSteps.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R5" for f in findings)


def test_r6_fires_when_no_phase_section() -> None:
    """R6: fires when no ## Phase N — <name> section exists."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary paragraph.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R6" for f in findings)


def test_r7_fires_on_malformed_phase_header_no_emdash() -> None:
    """R7: fires when a phase header has no em-dash separator."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary.\n\n"
        "## Phase 1 Loading\n\nSteps.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R7" for f in findings)


def test_r7_fires_on_malformed_phase_header_colon_separator() -> None:
    """R7: fires when a phase header uses colon instead of em-dash."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary.\n\n"
        "## Phase 1: Load\n\nSteps.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R7" for f in findings)


def test_r8_fires_when_body_exceeds_8000() -> None:
    """R8: fires when body total exceeds 8000 characters."""
    body_padding = "x" * 8001
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary.\n\n"
        f"## Phase 1 — Load\n\n{body_padding}\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R8" for f in findings)


def test_r9_fires_when_phase_exceeds_2000() -> None:
    """R9: fires when a single phase section exceeds 2000 characters."""
    phase_content = "Step.\n" * 350
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary.\n\n"
        f"## Phase 1 — Load\n\n{phase_content}\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R9" for f in findings)


def test_r10_fires_on_todo_marker() -> None:
    """R10: fires when body contains a TODO marker."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary.\n\n"
        "## Phase 1 — Load\n\nTODO: finish this step.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R10" for f in findings)


def test_r10_fires_on_fixme_marker() -> None:
    """R10: fires on FIXME marker."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary.\n\n"
        "## Phase 1 — Load\n\nFIXME: this is broken.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R10" for f in findings)


def test_r10_fires_on_xxx_marker() -> None:
    """R10: fires on XXX marker."""
    content = (
        "---\ndescription: A skill.\n---\n\n"
        "# Test\n\n**Invocation:** `/test`\n\nSummary.\n\n"
        "## Phase 1 — Load\n\nXXX: revisit.\n"
    )
    findings = lint_skill(content)
    assert any(f.rule == "R10" for f in findings)


def test_clean_content_produces_no_findings() -> None:
    """Assertion 11: §6-compliant body produces empty findings list."""
    findings = lint_skill(_CLEAN_CONTENT)
    assert findings == [], f"Expected clean, got: {findings}"
