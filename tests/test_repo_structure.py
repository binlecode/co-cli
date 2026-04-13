"""Structural tests — verify required docs and packages exist.

These tests enforce the harness: if a required file or package is deleted,
pytest fails before the breakage can propagate.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

REQUIRED_DOCS = [
    "CLAUDE.md",
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/DESIGN-system.md",
    "docs/DESIGN-core-loop.md",
    "docs/DESIGN-tools.md",
    "docs/DESIGN-context.md",
]

REQUIRED_DIRS = [
    "docs/exec-plans/active",
    "docs/exec-plans/completed",
]


@pytest.mark.parametrize("doc", REQUIRED_DOCS)
def test_required_doc_exists(doc: str) -> None:
    assert (ROOT / doc).is_file(), f"Required doc missing: {doc}"


@pytest.mark.parametrize("directory", REQUIRED_DIRS)
def test_required_dir_exists(directory: str) -> None:
    assert (ROOT / directory).is_dir(), f"Required directory missing: {directory}"
