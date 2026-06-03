"""Tests for _repair_json_args and CoToolLifecycle.before_tool_validate."""

import json

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.lifecycle import CoToolLifecycle, _repair_json_args
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
    )


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage(), run_step=1)


# ---------------------------------------------------------------------------
# _repair_json_args unit tests
# ---------------------------------------------------------------------------


def test_clean_json_passes_through():
    result = _repair_json_args('{"cmd": "ls"}')
    assert json.loads(result) == {"cmd": "ls"}


def test_empty_string_becomes_empty_object():
    assert _repair_json_args("") == "{}"


def test_none_literal_becomes_empty_object():
    assert _repair_json_args("None") == "{}"


def test_trailing_comma_stripped():
    result = _repair_json_args('{"a": 1,}')
    assert json.loads(result) == {"a": 1}


def test_control_chars_escaped():
    # Literal tab inside a string value — json.loads(strict=True) rejects this
    raw = '{"cmd": "git\tstatus"}'
    result = _repair_json_args(raw)
    parsed = json.loads(result)
    assert parsed["cmd"] == "git\tstatus"


def test_unclosed_brace_balanced():
    result = _repair_json_args('{"a": 1')
    assert json.loads(result) == {"a": 1}


def test_nested_unclosed_brace_balanced():
    result = _repair_json_args('{"a": {"b": 2')
    assert json.loads(result) == {"a": {"b": 2}}


def test_excess_closing_delimiters_trimmed():
    result = _repair_json_args('{"a": 1}}}')
    assert json.loads(result) == {"a": 1}


def test_combined_trailing_comma_and_unclosed():
    result = _repair_json_args('{"a": 1,')
    assert json.loads(result) == {"a": 1}


# ---------------------------------------------------------------------------
# CoToolLifecycle.before_tool_validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_tool_validate_dict_passes_through():
    lc = CoToolLifecycle()
    deps = _make_deps()
    ctx = _ctx(deps)
    args = {"cmd": "ls"}
    result = await lc.before_tool_validate(ctx, call=None, tool_def=None, args=args)
    assert result is args


@pytest.mark.asyncio
async def test_before_tool_validate_repairs_malformed_string():
    lc = CoToolLifecycle()
    deps = _make_deps()
    ctx = _ctx(deps)
    result = await lc.before_tool_validate(ctx, call=None, tool_def=None, args='{"cmd": "ls",')
    assert json.loads(result) == {"cmd": "ls"}
