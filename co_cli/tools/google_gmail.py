"""Gmail tools using RunContext pattern."""

import base64
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.google_auth import get_cached_google_creds
from co_cli.tools._errors import terminal_error, http_status_code


_GMAIL_NOT_CONFIGURED = (
    "Gmail: not configured. "
    "Set google_credentials_path in settings or run: "
    "gcloud auth application-default login"
)


def _handle_gmail_error(e: Exception) -> dict[str, Any]:
    """Route Gmail API errors with Gmail-specific guidance."""
    status = http_status_code(e)
    if status == 401:
        return terminal_error("Gmail: authentication error (401). Check credentials.")
    if status == 403:
        raise ModelRetry(
            "Gmail: access forbidden (403). Check API enablement and Gmail OAuth scopes."
        )
    if status == 404:
        raise ModelRetry("Gmail: message or draft not found (404). Verify IDs and retry.")
    if status == 429:
        raise ModelRetry("Gmail: rate limited (429). Wait a moment and retry.")
    if status and status >= 500:
        raise ModelRetry(f"Gmail: server error ({status}). Retry shortly.")
    raise ModelRetry(f"Gmail: API error ({e}). Check credentials, API enablement, and quota.")


def _format_messages(service, message_ids: list[dict]) -> str:
    """Fetch metadata for message IDs and format as readable string."""
    output = ""
    for msg in message_ids:
        detail = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        snippet = detail.get("snippet", "")
        msg_id = msg["id"]
        gmail_url = f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"
        output += (
            f"- From: {headers.get('From', 'unknown')}\n"
            f"  Subject: {headers.get('Subject', '(no subject)')}\n"
            f"  Date: {headers.get('Date', 'unknown')}\n"
            f"  Preview: {snippet}\n"
            f"  Link: {gmail_url}\n"
        )
    return output


def _get_gmail_service(ctx: RunContext[CoDeps]):
    """Extract and validate Gmail service from context.

    Returns (service, None) on success, or (None, error_dict) for terminal config errors.
    """
    creds = get_cached_google_creds(ctx.deps)
    if not creds:
        return None, terminal_error(_GMAIL_NOT_CONFIGURED)
    return build("gmail", "v1", credentials=creds), None


def list_emails(ctx: RunContext[CoDeps], max_results: int = 5) -> dict[str, Any]:
    """List recent emails from the user's Gmail inbox.

    Returns a dict with:
    - display: pre-formatted email list with clickable links — show this directly to the user
    - count: number of emails returned

    Args:
        max_results: Maximum number of emails to return (default 5).
    """
    service, err = _get_gmail_service(ctx)
    if err:
        return err

    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", maxResults=max_results)
            .execute()
        )
        messages = response.get("messages", [])
        if not messages:
            return {"display": "No emails found.", "count": 0}
        display = f"Recent Emails ({len(messages)}):\n" + _format_messages(service, messages)
        return {"display": display, "count": len(messages)}
    except ModelRetry:
        raise
    except Exception as e:
        return _handle_gmail_error(e)


def search_emails(ctx: RunContext[CoDeps], query: str, max_results: int = 5) -> dict[str, Any]:
    """Search emails in Gmail using Gmail search syntax.

    Returns a dict with:
    - display: pre-formatted search results with clickable links — show this directly to the user
    - count: number of emails returned

    Args:
        query: Gmail search query (e.g. "from:alice subject:invoice", "is:unread", "newer_than:2d").
        max_results: Maximum number of emails to return (default 5).
    """
    service, err = _get_gmail_service(ctx)
    if err:
        return err

    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        messages = response.get("messages", [])
        if not messages:
            return {"display": f"No emails found for query: {query}", "count": 0}
        display = f"Search results for '{query}' ({len(messages)}):\n" + _format_messages(service, messages)
        return {"display": display, "count": len(messages)}
    except ModelRetry:
        raise
    except Exception as e:
        return _handle_gmail_error(e)


def create_email_draft(ctx: RunContext[CoDeps], to: str, subject: str, body: str) -> str | dict[str, Any]:
    """Create a draft email in Gmail.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text.
    """
    service, err = _get_gmail_service(ctx)
    if err:
        return err

    try:
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        draft_body = {"message": {"raw": raw}}
        draft = service.users().drafts().create(userId="me", body=draft_body).execute()
        return f"Draft created for {to} with subject '{subject}'. Draft ID: {draft['id']}"
    except ModelRetry:
        raise
    except Exception as e:
        return _handle_gmail_error(e)
