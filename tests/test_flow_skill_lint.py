"""Tests for co_cli.skills.lint -- R1-R4 runtime rules + bundled-extras."""

from __future__ import annotations

from co_cli.skills.lint import lint_bundled_extras, lint_skill

_CLEAN_CONTENT = """\
---
description: Test skill -- a clean skill for lint testing.
user-invocable: true
---

# Test Skill

A short opening summary paragraph describing what this skill does.

Step one: gather inputs.
Step two: validate them.
Step three: emit output.
"""


def test_r1_fires_when_frontmatter_missing() -> None:
    content = "# My Skill\n\nSome steps.\n"
    findings = lint_skill(content)
    assert any(f.rule == "R1" for f in findings)


def test_r2_fires_when_description_missing() -> None:
    content = "---\nuser-invocable: true\n---\n\n# Test\n\nSteps.\n"
    findings = lint_skill(content)
    assert any(f.rule == "R2" for f in findings)


def test_r2_fires_when_description_empty() -> None:
    content = "---\ndescription:    \nuser-invocable: true\n---\n\n# Test\n\nSteps.\n"
    findings = lint_skill(content)
    assert any(f.rule == "R2" for f in findings)


def test_r2_fires_when_description_over_1024() -> None:
    long_desc = "x" * 1025
    content = f"---\ndescription: {long_desc}\n---\n\n# Test\n\nSteps.\n"
    findings = lint_skill(content)
    assert any(f.rule == "R2" for f in findings)


def test_r3_fires_when_no_h1() -> None:
    content = "---\ndescription: A skill.\n---\n\nSteps.\n"
    findings = lint_skill(content)
    assert any(f.rule == "R3" for f in findings)


def test_r4_fires_when_body_exceeds_8000() -> None:
    body_padding = "x" * 8001
    content = f"---\ndescription: A skill.\n---\n\n# Test\n\n{body_padding}\n"
    findings = lint_skill(content)
    assert any(f.rule == "R4" for f in findings)


def test_clean_content_produces_no_findings() -> None:
    findings = lint_skill(_CLEAN_CONTENT)
    assert findings == [], f"Expected clean, got: {findings}"


def test_bundled_extras_fires_on_todo_marker() -> None:
    content = "---\ndescription: A skill.\n---\n\n# Test\n\nTODO: finish this step.\n"
    findings = lint_bundled_extras(content)
    assert any(f.rule == "B1" for f in findings)


def test_bundled_extras_clean_when_no_markers() -> None:
    findings = lint_bundled_extras(_CLEAN_CONTENT)
    assert findings == []
