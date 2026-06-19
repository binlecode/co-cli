"""Flow test for always-injected USER.md block in orchestrator static instructions.

Asserts presence/absence of the profile block by file-state and enable-flag, by
composing the real ORCHESTRATOR_SPEC static builders the same way build.py does.
"""

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend

_MARKER = "USER PROFILE"


def _compose_static(deps: CoDeps) -> str:
    parts = []
    for builder in ORCHESTRATOR_SPEC.static_instruction_builders:
        piece = builder(deps)
        if piece:
            parts.append(piece)
    return "\n\n".join(parts)


def _deps(tmp_path: Path, *, enabled: bool) -> CoDeps:
    memory_cfg = SETTINGS.memory.model_copy(update={"user_profile_enabled": enabled})
    config = SETTINGS.model_copy(update={"memory": memory_cfg})
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        session=CoSessionState(),
        user_profile_path=tmp_path / "USER.md",
    )


def test_nonempty_profile_enabled_injects_block(tmp_path: Path) -> None:
    """A non-empty profile with the flag on appears in the static instructions."""
    (tmp_path / "USER.md").write_text("User is a backend engineer.", encoding="utf-8")
    instructions = _compose_static(_deps(tmp_path, enabled=True))
    assert _MARKER in instructions
    assert "backend engineer" in instructions


def test_empty_profile_injects_nothing(tmp_path: Path) -> None:
    """An empty/absent profile emits no block even with the flag on."""
    instructions = _compose_static(_deps(tmp_path, enabled=True))
    assert _MARKER not in instructions


def test_flag_off_injects_nothing(tmp_path: Path) -> None:
    """A non-empty profile is not injected when the flag is off."""
    (tmp_path / "USER.md").write_text("User is a backend engineer.", encoding="utf-8")
    instructions = _compose_static(_deps(tmp_path, enabled=False))
    assert _MARKER not in instructions
