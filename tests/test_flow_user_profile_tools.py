"""Flow tests for user_profile_view / user_profile_write tool entrypoints.

Real I/O against a tmp_path-rooted profile (never the user-global file).
"""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.user_profile.view import user_profile_view
from co_cli.tools.user_profile.write import user_profile_write


def _ctx(tmp_path: Path) -> RunContext[CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        user_profile_path=tmp_path / "USER.md",
    )
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.mark.asyncio
async def test_write_then_view_round_trips(tmp_path: Path) -> None:
    """Writing the profile, then viewing it, shows the written text back."""
    ctx = _ctx(tmp_path)
    text = "User is a Python engineer who prefers terse answers."

    await user_profile_write(ctx, content=text)
    result = await user_profile_view(ctx)

    assert text in result.return_value


@pytest.mark.asyncio
async def test_empty_profile_view_reports_empty(tmp_path: Path) -> None:
    """Viewing before any write reports the empty state, not an error."""
    result = await user_profile_view(_ctx(tmp_path))
    assert result.metadata is None or not result.metadata.get("error")
    assert "empty" in result.return_value.lower()


@pytest.mark.asyncio
async def test_over_budget_write_rejected_at_tool_boundary(tmp_path: Path) -> None:
    """An over-budget write returns a tool_error and does not persist."""
    ctx = _ctx(tmp_path)
    budget = SETTINGS.memory.user_profile_char_budget

    result = await user_profile_write(ctx, content="x" * (budget + 1))
    assert result.metadata is not None
    assert result.metadata.get("error") is True

    view = await user_profile_view(ctx)
    assert "empty" in view.return_value.lower()
