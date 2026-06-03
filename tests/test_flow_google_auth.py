"""Config-gating and not-configured-return behavior for the Google tool surface.

Traces the three gates that stand between config and a Google tool's return:
per-turn visibility (`_google_available`) and the shared not-configured return path
in `_get_google_service`. The registration gate (`requires_config` drops the tools
when the config field is absent) is covered in test_agent_build_task_agent.py.

Regression guard: `_get_google_service` must route an unresolved credential to a
terminal `tool_error` ToolReturn — not raise — so the model sees an actionable
message and can pick another tool.
"""

from __future__ import annotations

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent.core import build_native_toolset
from co_cli.deps import CoDeps
from co_cli.tools.agent_tool import TOOL_REGISTRY_BY_NAME
from co_cli.tools.google._auth import ALL_GOOGLE_SCOPES, _google_available
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import handle_google_api_error

_SETTINGS = make_settings(mcp_servers={}, google_credentials_path="/nonexistent/creds.json")
_, _INDEX = build_native_toolset(_SETTINGS)


def _deps() -> CoDeps:
    deps = CoDeps(shell=ShellBackend(), config=_SETTINGS)
    deps.tool_index = _INDEX
    return deps


def test_scope_set_is_least_privilege() -> None:
    """co requests exactly the read+draft scope floor — no modify/send/write authority.

    gmail.compose is the narrowest scope that permits drafts.create; gmail.readonly
    covers messages.list/get; drive and calendar are read-only. gmail.modify (which
    grants delete/trash/label rewrites) and gmail.send must never appear.
    """
    assert set(ALL_GOOGLE_SCOPES) == {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    }
    joined = " ".join(ALL_GOOGLE_SCOPES)
    assert "gmail.modify" not in joined
    assert "gmail.send" not in joined
    assert not any(s.endswith("/auth/drive") for s in ALL_GOOGLE_SCOPES)
    assert not any(s.endswith("/auth/calendar") for s in ALL_GOOGLE_SCOPES)


def test_google_available_hidden_when_resolved_to_no_creds() -> None:
    """After resolution with no credentials, the tool hides from the turn surface."""
    deps = _deps()
    deps.session.google.creds_resolved = True
    deps.session.google.creds = None
    assert _google_available(deps) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "kwargs", "label"),
    [
        ("google_drive_search", {"query": "budget"}, "Drive"),
        ("google_gmail_list", {}, "Gmail"),
        ("google_calendar_list", {}, "Calendar"),
    ],
)
async def test_not_configured_returns_terminal_tool_error(
    tool_name: str, kwargs: dict, label: str
) -> None:
    """A Google tool invoked with unresolved creds returns a not-configured ToolReturn.

    Drives the real tool body through the shared `_get_google_service` path with
    creds pre-resolved to None (so no interactive gcloud login fires). The body must
    return a terminal error ToolReturn carrying the service label — never raise.
    """
    deps = _deps()
    deps.session.google.creds_resolved = True
    deps.session.google.creds = None
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=tool_name)

    result = await TOOL_REGISTRY_BY_NAME[tool_name](ctx, **kwargs)

    assert isinstance(result, ToolReturn)
    assert result.metadata == {"error": True}
    assert f"{label}: not configured" in result.return_value


def test_refresh_error_is_terminal_and_actionable() -> None:
    """A token RefreshError classifies terminal — no ModelRetry, points at re-auth.

    The credential lacking a scope makes google-auth raise RefreshError on the
    auto-refresh (no HTTP status). It must return a terminal tool_error naming
    `co google auth` and the required scopes, never raise the retryable catch-all.
    """
    from google.auth.exceptions import RefreshError

    deps = _deps()
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="google_gmail_list")

    err = RefreshError("('invalid_scope: Bad Request', {'error': 'invalid_scope'})")
    result = handle_google_api_error("Gmail", err, ctx=ctx)

    assert isinstance(result, ToolReturn)
    assert result.metadata == {"error": True}
    assert "co google auth" in result.return_value
    assert "gmail.readonly" in result.return_value
    assert "gmail.compose" in result.return_value


def test_transient_429_still_retries() -> None:
    """A 429-bearing API error keeps raising ModelRetry — the retry path is preserved."""
    import httplib2
    from googleapiclient.errors import HttpError

    deps = _deps()
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="google_gmail_list")

    resp = httplib2.Response({"status": "429"})
    err = HttpError(resp, b"rate limited", uri="https://gmail.googleapis.com")
    with pytest.raises(ModelRetry):
        handle_google_api_error("Gmail", err, ctx=ctx)
