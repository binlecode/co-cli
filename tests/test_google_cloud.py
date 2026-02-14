"""Functional tests for Google tools (Drive, Gmail, Calendar)."""

from dataclasses import dataclass

import pytest
from pydantic_ai import ModelRetry

from co_cli.tools.google_drive import search_drive_files, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, create_email_draft
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox


@dataclass
class Context:
    """Minimal context for tool testing."""
    deps: CoDeps


def _make_ctx() -> Context:
    return Context(deps=CoDeps(
        sandbox=Sandbox(container_name="test"),
        session_id="test",
        google_credentials_path=settings.google_credentials_path,
    ))


def test_drive_search_functional():
    """Test real Google Drive search.
    Requires google_credentials_path in settings.
    """
    ctx = _make_ctx()

    results = search_drive_files(ctx, "test")
    assert isinstance(results, dict)
    assert "display" in results
    assert "page" in results
    assert "has_more" in results
    # Either found files or returned empty result — both are valid
    assert "Found" in results["display"] or results.get("count") == 0


def test_drive_search_empty_result():
    """search_drive_files returns a valid dict with count=0 on no matches (not ModelRetry)."""
    ctx = _make_ctx()

    result = search_drive_files(ctx, "zzz_nonexistent_xkcd_42_qwerty")
    assert isinstance(result, dict)
    assert result["count"] == 0
    assert result["page"] == 1
    assert result["has_more"] is False
    assert "No files found" in result["display"]


def test_drive_search_pagination():
    """Test Drive search pagination with page number."""
    ctx = _make_ctx()

    page1 = search_drive_files(ctx, "notes", page=1)
    assert isinstance(page1, dict)
    assert page1["page"] == 1

    if page1.get("count") == 0 or not page1["has_more"]:
        # Not enough files to test pagination — pass without asserting page 2
        return

    page2 = search_drive_files(ctx, "notes", page=2)
    assert isinstance(page2, dict)
    assert page2["page"] == 2
    assert "Found" in page2["display"]
    # Page 2 should have different files than page 1
    assert page2["display"] != page1["display"]


def test_list_emails_functional():
    """Test real Gmail list emails.
    Requires google_credentials_path in settings.
    """
    ctx = _make_ctx()

    result = list_emails(ctx, max_results=2)
    assert isinstance(result, dict)
    assert "display" in result
    assert "count" in result


def test_search_emails_functional():
    """Test real Gmail search.
    Requires google_credentials_path in settings.
    """
    ctx = _make_ctx()

    result = search_emails(ctx, query="is:unread", max_results=2)
    assert isinstance(result, dict)
    assert "display" in result
    assert "count" in result


def test_list_calendar_events_functional():
    """Test listing calendar events with default params (today only)."""
    ctx = _make_ctx()

    result = list_calendar_events(ctx)
    assert isinstance(result, dict)
    assert "display" in result
    assert "count" in result


def test_list_calendar_events_with_time_window():
    """Test listing calendar events with days_back and days_ahead."""
    ctx = _make_ctx()

    result = list_calendar_events(ctx, days_back=7, days_ahead=7, max_results=50)
    assert isinstance(result, dict)
    assert "display" in result
    assert result["count"] > 0


def test_search_calendar_events_functional():
    """Test searching calendar events by keyword."""
    ctx = _make_ctx()

    result = search_calendar_events(ctx, query="meeting", days_ahead=30, max_results=2)
    assert isinstance(result, dict)
    assert "display" in result
    assert "count" in result


def test_search_calendar_events_with_days_back():
    """Test searching past calendar events with days_back."""
    ctx = _make_ctx()

    result = search_calendar_events(
        ctx, query="meeting", days_back=30, days_ahead=0, max_results=5
    )
    assert isinstance(result, dict)
    assert "display" in result
    assert "count" in result


def test_gmail_draft_functional():
    """Test real Gmail draft creation.
    Requires google_credentials_path in settings.
    """
    ctx = _make_ctx()

    result = create_email_draft(ctx, "test@example.com", "Test Subject", "Test Body")
    assert "Draft created" in result
