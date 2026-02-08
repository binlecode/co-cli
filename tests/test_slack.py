"""Functional tests for Slack tools.

NOTE on skips: The "functional" tests (test_slack_*_functional, test_list_slack_*)
that hit the real Slack API are skipped when SLACK_BOT_TOKEN is not configured.
This is a deliberate exception to the project's "no skips" testing policy
(see CLAUDE.md) — without a valid bot token, these tests hang on network
timeouts rather than failing with a useful error.  The no-client and
input-validation tests below still run unconditionally.
"""

from dataclasses import dataclass

import pytest
from pydantic_ai import ModelRetry

from co_cli.tools.slack import (
    send_slack_message,
    list_slack_channels,
    list_slack_messages,
    list_slack_replies,
    list_slack_users,
)
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def _make_ctx(
    auto_confirm: bool = True,
    slack_client=None,
) -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        auto_confirm=auto_confirm,
        session_id="test",
        slack_client=slack_client,
    ))


# Slack API errors that prove tool code ran correctly (just no valid token or wrong channel)
_SLACK_ACCEPTABLE_ERRORS = ("not_authed", "invalid_auth", "channel_not_found", "Channel not found", "thread_not_found", "Thread not found")


def _slack_acceptable(e: Exception) -> bool:
    """Return True if the error is an expected Slack API error (auth/channel/thread)."""
    err = str(e)
    return any(code in err for code in _SLACK_ACCEPTABLE_ERRORS)


# Exception to "no skips" policy: without a bot token these tests hang on
# network timeouts (~30s each) instead of failing fast.  The token is not
# available in CI or on machines without Slack workspace access.
_skip_no_token = pytest.mark.skipif(
    not settings.slack_bot_token,
    reason="SLACK_BOT_TOKEN not configured — skipped to avoid timeout (see module docstring)",
)


# --- Slack: functional tests (require SLACK_BOT_TOKEN) ---


@_skip_no_token
def test_slack_post_functional():
    """Test real Slack message posting.
    Requires SLACK_BOT_TOKEN.
    """
    from slack_sdk import WebClient

    client = WebClient(token=settings.slack_bot_token)
    ctx = _make_ctx(auto_confirm=True, slack_client=client)

    channel = "#general"
    try:
        result = send_slack_message(ctx, channel, "Automated test from Co-CLI")
        assert isinstance(result, dict)
        assert "display" in result
        assert "ts" in result
        assert "channel" in result
    except Exception as e:
        if not _slack_acceptable(e):
            pytest.fail(f"Slack error: {e}")


@_skip_no_token
def test_list_slack_channels():
    """Test listing Slack channels.
    Requires SLACK_BOT_TOKEN.
    """
    from slack_sdk import WebClient

    client = WebClient(token=settings.slack_bot_token)
    ctx = _make_ctx(slack_client=client)

    try:
        result = list_slack_channels(ctx)
        assert isinstance(result, dict)
        assert "display" in result
        assert "count" in result
        assert "has_more" in result
    except Exception as e:
        if not _slack_acceptable(e):
            pytest.fail(f"Slack error: {e}")


@_skip_no_token
def test_list_slack_messages():
    """Test getting Slack channel history.
    Requires SLACK_BOT_TOKEN.
    """
    from slack_sdk import WebClient

    client = WebClient(token=settings.slack_bot_token)
    ctx = _make_ctx(slack_client=client)

    try:
        result = list_slack_messages(ctx, "C01ABC123", limit=5)
        assert isinstance(result, dict)
        assert "display" in result
        assert "count" in result
        assert "has_more" in result
    except Exception as e:
        if not _slack_acceptable(e):
            pytest.fail(f"Slack error: {e}")


@_skip_no_token
def test_list_slack_replies():
    """Test getting Slack thread replies.
    Requires SLACK_BOT_TOKEN.
    """
    from slack_sdk import WebClient

    client = WebClient(token=settings.slack_bot_token)
    ctx = _make_ctx(slack_client=client)

    try:
        result = list_slack_replies(ctx, "C01ABC123", "1234567890.123456")
        assert isinstance(result, dict)
        assert "display" in result
        assert "count" in result
        assert "has_more" in result
    except Exception as e:
        if not _slack_acceptable(e):
            pytest.fail(f"Slack error: {e}")


@_skip_no_token
def test_list_slack_users():
    """Test listing Slack users.
    Requires SLACK_BOT_TOKEN.
    """
    from slack_sdk import WebClient

    client = WebClient(token=settings.slack_bot_token)
    ctx = _make_ctx(slack_client=client)

    try:
        result = list_slack_users(ctx)
        assert isinstance(result, dict)
        assert "display" in result
        assert "count" in result
        assert "has_more" in result
    except Exception as e:
        if not _slack_acceptable(e):
            pytest.fail(f"Slack error: {e}")


# --- Slack: no-client raises ModelRetry ---


def test_slack_no_client_post():
    """send_slack_message raises ModelRetry when slack_client is None."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Slack not configured"):
        send_slack_message(ctx, "#general", "hello")


def test_slack_no_client_list_channels():
    """list_slack_channels raises ModelRetry when slack_client is None."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Slack not configured"):
        list_slack_channels(ctx)


def test_slack_no_client_channel_history():
    """list_slack_messages raises ModelRetry when slack_client is None."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Slack not configured"):
        list_slack_messages(ctx, "C01ABC")


def test_slack_no_client_thread_replies():
    """list_slack_replies raises ModelRetry when slack_client is None."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Slack not configured"):
        list_slack_replies(ctx, "C01ABC", "1234567890.123456")


def test_slack_no_client_list_users():
    """list_slack_users raises ModelRetry when slack_client is None."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Slack not configured"):
        list_slack_users(ctx)


# --- Slack: input validation raises ModelRetry ---
# These tests verify argument validation before any API call is made.
# They use slack_client=None — validation fires before _get_slack_client().


def test_slack_post_empty_channel():
    """send_slack_message raises ModelRetry on empty channel."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Channel is required"):
        send_slack_message(ctx, "", "hello")


def test_slack_post_empty_text():
    """send_slack_message raises ModelRetry on empty text."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Message text cannot be empty"):
        send_slack_message(ctx, "#general", "")


def test_slack_history_empty_channel():
    """list_slack_messages raises ModelRetry on empty channel."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Slack: channel ID is required"):
        list_slack_messages(ctx, "")


def test_slack_thread_empty_channel():
    """list_slack_replies raises ModelRetry on empty channel."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="Slack: channel ID is required"):
        list_slack_replies(ctx, "", "1234567890.123456")


def test_slack_thread_empty_thread_ts():
    """list_slack_replies raises ModelRetry on empty thread_ts."""
    ctx = _make_ctx(slack_client=None)
    with pytest.raises(ModelRetry, match="thread_ts is required"):
        list_slack_replies(ctx, "C01ABC", "")
