"""Functional tests for cloud tools (Drive, Gmail, Slack)."""

import os
from dataclasses import dataclass

import pytest

from co_cli.tools.google_drive import search_drive, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, draft_email
from co_cli.tools.slack import post_slack_message
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli.google_auth import get_google_credentials, build_google_service

ALL_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Check for Google Credentials
HAS_GCP = bool(
    settings.google_credentials_path
    and os.path.exists(settings.google_credentials_path)
)

# Check for Slack Token
HAS_SLACK = bool(settings.slack_bot_token)


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def _make_ctx(
    auto_confirm: bool = True,
    google_drive=None,
    google_gmail=None,
    slack_client=None,
) -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        auto_confirm=auto_confirm,
        session_id="test",
        google_drive=google_drive,
        google_gmail=google_gmail,
        slack_client=slack_client,
    ))


@pytest.mark.skipif(not HAS_GCP, reason="Google credentials missing")
def test_drive_search_functional():
    """Test real Google Drive search.
    Requires google_credentials_path in settings.
    """
    google_creds = get_google_credentials(
        settings.google_credentials_path, ALL_GOOGLE_SCOPES
    )
    drive_service = build_google_service("drive", "v3", google_creds)
    ctx = _make_ctx(google_drive=drive_service)

    # search_drive raises ModelRetry on no results, so we just check it doesn't error on auth
    try:
        results = search_drive(ctx, "test")
        assert isinstance(results, list)
    except Exception as e:
        # ModelRetry for "No results" is acceptable
        if "No results" not in str(e):
            pytest.fail(f"Drive API returned error: {e}")


@pytest.mark.skipif(not HAS_GCP, reason="Google credentials missing")
def test_list_emails_functional():
    """Test real Gmail list emails.
    Requires google_credentials_path in settings.
    """
    google_creds = get_google_credentials(
        settings.google_credentials_path, ALL_GOOGLE_SCOPES
    )
    gmail_service = build_google_service("gmail", "v1", google_creds)
    ctx = _make_ctx(google_gmail=gmail_service)

    result = list_emails(ctx, max_results=2)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.skipif(not HAS_GCP, reason="Google credentials missing")
def test_search_emails_functional():
    """Test real Gmail search.
    Requires google_credentials_path in settings.
    """
    google_creds = get_google_credentials(
        settings.google_credentials_path, ALL_GOOGLE_SCOPES
    )
    gmail_service = build_google_service("gmail", "v1", google_creds)
    ctx = _make_ctx(google_gmail=gmail_service)

    result = search_emails(ctx, query="is:unread", max_results=2)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.skipif(not HAS_GCP, reason="Google credentials missing")
def test_gmail_draft_functional():
    """Test real Gmail draft creation.
    Requires google_credentials_path in settings.
    """
    google_creds = get_google_credentials(
        settings.google_credentials_path, ALL_GOOGLE_SCOPES
    )
    gmail_service = build_google_service("gmail", "v1", google_creds)
    ctx = _make_ctx(auto_confirm=True, google_gmail=gmail_service)

    result = draft_email(ctx, "test@example.com", "Test Subject", "Test Body")
    assert "Draft created" in result


@pytest.mark.skipif(not HAS_SLACK, reason="SLACK_BOT_TOKEN missing")
def test_slack_post_functional():
    """Test real Slack message posting.
    Requires SLACK_BOT_TOKEN.
    """
    from slack_sdk import WebClient

    client = WebClient(token=settings.slack_bot_token)
    ctx = _make_ctx(auto_confirm=True, slack_client=client)

    channel = "#general"
    try:
        result = post_slack_message(ctx, channel, "Automated test from Co-CLI")
        assert "TS:" in result or "channel_not_found" in str(result)
    except Exception as e:
        # channel_not_found is acceptable (tool auth worked)
        if "channel_not_found" not in str(e):
            pytest.fail(f"Slack error: {e}")
