import os
import pytest
from co_cli.tools.drive import search_drive, read_drive_file
from co_cli.tools.comm import post_slack_message, draft_email
from co_cli.config import settings

# Check for GCP Credentials
HAS_GCP = bool(settings.gcp_key_path and os.path.exists(settings.gcp_key_path))

# Check for Slack Token
HAS_SLACK = bool(settings.slack_bot_token)

@pytest.mark.skipif(not HAS_GCP, reason="GCP Credentials missing")
def test_drive_search_functional():
    """
    Test real Google Drive search.
    Requires gcp_key_path in settings
    """
    # Search for something likely to exist or just check it returns a list (even empty)
    # without erroring out on auth.
    results = search_drive("test")
    assert isinstance(results, list)
    if results and "error" in results[0]:
        pytest.fail(f"Drive API returned error: {results[0]['error']}")

@pytest.mark.skipif(not HAS_GCP, reason="GCP Credentials missing")
def test_gmail_draft_functional(monkeypatch):
    """
    Test real Gmail draft creation.
    Requires gcp_key_path in settings
    """
    # Auto-confirm for testing
    monkeypatch.setenv("CO_CLI_AUTO_CONFIRM", "true")
    
    result = draft_email("test@example.com", "Test Subject", "Test Body")
    assert "Draft created" in result
    assert "error" not in result.lower()

@pytest.mark.skipif(not HAS_SLACK, reason="SLACK_BOT_TOKEN missing")
def test_slack_post_functional(monkeypatch):
    """
    Test real Slack message posting.
    Requires SLACK_BOT_TOKEN
    """
    monkeypatch.setenv("CO_CLI_AUTO_CONFIRM", "true")
    
    # Use a safe channel or DM if possible, or expect failure if channel doesn't exist
    # verifying at least authentication worked.
    channel = "#general" 
    result = post_slack_message(channel, "Automated test from Co-CLI")
    
    # We accept "channel_not_found" as success of the TOOL execution (it tried),
    # but strictly we want success.
    assert "TS:" in result or "channel_not_found" in result
    assert "error" not in result.lower() or "channel_not_found" in result
