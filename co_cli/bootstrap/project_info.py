"""Shared project metadata: version from pyproject.toml and git branch."""

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"


@dataclass
class ProjectInfo:
    version: str
    git_branch: str | None


def project_info() -> ProjectInfo:
    """Read version from pyproject.toml and current git branch from the working tree."""
    version = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]
    try:
        git_branch: str | None = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        git_branch = None
    return ProjectInfo(version=version, git_branch=git_branch)
