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
    "docs/DESIGN-system.md",
    "docs/DESIGN-core-loop.md",
    "docs/DESIGN-tools.md",
    "docs/DESIGN-context.md",
]

REQUIRED_PACKAGES = [
    "co_cli/tools",
    "co_cli/context",
    "co_cli/config",
    "co_cli/knowledge",
    "co_cli/memory",
    "co_cli/bootstrap",
    "co_cli/commands",
    "co_cli/observability",
]


@pytest.mark.parametrize("doc", REQUIRED_DOCS)
def test_required_doc_exists(doc: str) -> None:
    assert (ROOT / doc).is_file(), f"Required doc missing: {doc}"


@pytest.mark.parametrize("package", REQUIRED_PACKAGES)
def test_required_package_exists(package: str) -> None:
    assert (ROOT / package).is_dir(), f"Required package missing: {package}"
