"""Versioned judge-rubric loader.

Rubrics live as markdown under ``evals/_rubrics/<name>.<version>.md`` (e.g.
``groundedness.v1.md``). Each rubric carries: scenario summary, numbered pass
criteria, tone notes for the judge, and one PASS + one FAIL calibration
transcript so the judge has anchor points.

A rubric change is a behavioral-contract change — bump the version suffix
(``v1`` → ``v2``) so historical REPORT runs remain interpretable against the
rubric they were scored under.

Usage:
    text, version = load_rubric("groundedness")
    verdict = await judge_with_llm(text, transcript, deps=deps, model=deps.judge_model)
"""

from __future__ import annotations

from pathlib import Path

_RUBRICS_DIR = Path(__file__).parent / "_rubrics"


def load_rubric(name: str, version: str = "v1") -> tuple[str, str]:
    """Load a versioned rubric markdown file.

    Args:
        name: Scenario name without extension — must match a file
            ``evals/_rubrics/<name>.<version>.md``.
        version: Version suffix (default ``v1``). Bump when criteria change.

    Returns:
        ``(rubric_text, version)`` — the full markdown body plus the version
        string for embedding in ``CaseResult.reason``.

    Raises:
        FileNotFoundError if the rubric file is absent.
    """
    path = _RUBRICS_DIR / f"{name}.{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Rubric {name!r} version {version!r} not found at {path}")
    return path.read_text(encoding="utf-8"), version
