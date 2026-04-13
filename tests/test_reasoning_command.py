"""Functional tests for /reasoning slash command.

Tests use real CoDeps — no mocks, no stubs. No LLM model needed;
these tests exercise the command handler directly against deps state.
"""

import pytest
from tests._settings import make_settings

from co_cli.commands._commands import CommandContext, _cmd_reasoning
from co_cli.config._core import DEFAULT_REASONING_DISPLAY
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend


def _make_ctx(reasoning_display: str = DEFAULT_REASONING_DISPLAY) -> CommandContext:
    """Build a minimal real CommandContext for /reasoning tests."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(mcp_servers={}),
        session=CoSessionState(reasoning_display=reasoning_display),
    )
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=None,  # type: ignore[arg-type]  # not needed for local-only command
    )


@pytest.mark.asyncio
async def test_no_arg_shows_current_mode_without_mutation():
    """/reasoning with no arg shows the current mode and does not change it."""
    ctx = _make_ctx("summary")
    result = await _cmd_reasoning(ctx, "")
    assert result is None
    assert ctx.deps.session.reasoning_display == "summary"


@pytest.mark.asyncio
async def test_explicit_valid_arg_sets_mode():
    """/reasoning full sets the mode to full directly."""
    ctx = _make_ctx("summary")
    result = await _cmd_reasoning(ctx, "full")
    assert result is None
    assert ctx.deps.session.reasoning_display == "full"


@pytest.mark.asyncio
async def test_explicit_valid_arg_off():
    """/reasoning off sets the mode to off from any starting point."""
    ctx = _make_ctx("full")
    result = await _cmd_reasoning(ctx, "off")
    assert result is None
    assert ctx.deps.session.reasoning_display == "off"


@pytest.mark.asyncio
async def test_next_cycles_from_off_to_summary():
    """/reasoning next advances off → summary."""
    ctx = _make_ctx("off")
    result = await _cmd_reasoning(ctx, "next")
    assert result is None
    assert ctx.deps.session.reasoning_display == "summary"


@pytest.mark.asyncio
async def test_next_cycles_from_summary_to_full():
    """/reasoning next advances summary → full."""
    ctx = _make_ctx("summary")
    result = await _cmd_reasoning(ctx, "next")
    assert result is None
    assert ctx.deps.session.reasoning_display == "full"


@pytest.mark.asyncio
async def test_next_cycles_from_full_to_off():
    """/reasoning next wraps full → off."""
    ctx = _make_ctx("full")
    result = await _cmd_reasoning(ctx, "next")
    assert result is None
    assert ctx.deps.session.reasoning_display == "off"


@pytest.mark.asyncio
async def test_cycle_keyword_behaves_same_as_next():
    """/reasoning cycle is an alias for next."""
    ctx = _make_ctx("off")
    result = await _cmd_reasoning(ctx, "cycle")
    assert result is None
    assert ctx.deps.session.reasoning_display == "summary"


@pytest.mark.asyncio
async def test_invalid_arg_leaves_mode_unchanged():
    """/reasoning bogus prints error and leaves the mode unchanged."""
    ctx = _make_ctx("summary")
    result = await _cmd_reasoning(ctx, "bogus")
    assert result is None
    assert ctx.deps.session.reasoning_display == "summary"
