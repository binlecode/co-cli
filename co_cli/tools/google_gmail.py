"""Gmail tools using RunContext pattern."""

import base64
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps
from co_cli.google_auth import get_cached_google_creds
from co_cli.tools._errors import GOOGLE_NOT_CONFIGURED, GOOGLE_API_NOT_ENABLED, google_api_error


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
    """Extract and validate Gmail service from context."""
    creds = get_cached_google_creds(ctx.deps)
    if not creds:
        raise ModelRetry(GOOGLE_NOT_CONFIGURED.format(service="Gmail"))
    return build("gmail", "v1", credentials=creds)


def list_emails(ctx: RunContext[CoDeps], max_results: int = 5) -> dict[str, Any]:
    """List recent emails from the user's Gmail inbox.

    Returns a dict with:
    - display: pre-formatted email list with clickable links — show this directly to the user
    - count: number of emails returned

    Args:
        max_results: Maximum number of emails to return (default 5).
    """
    service = _get_gmail_service(ctx)

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
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                GOOGLE_API_NOT_ENABLED.format(service="Gmail", api_id="gmail.googleapis.com")
            )
        raise ModelRetry(google_api_error("Gmail", e))


def search_emails(ctx: RunContext[CoDeps], query: str, max_results: int = 5) -> dict[str, Any]:
    """Search emails in Gmail using Gmail search syntax.

    Returns a dict with:
    - display: pre-formatted search results with clickable links — show this directly to the user
    - count: number of emails returned

    Args:
        query: Gmail search query (e.g. "from:alice subject:invoice", "is:unread", "newer_than:2d").
        max_results: Maximum number of emails to return (default 5).
    """
    service = _get_gmail_service(ctx)

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
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                GOOGLE_API_NOT_ENABLED.format(service="Gmail", api_id="gmail.googleapis.com")
            )
        raise ModelRetry(google_api_error("Gmail", e))


def create_email_draft(ctx: RunContext[CoDeps], to: str, subject: str, body: str) -> str:
    """Create a draft email in Gmail.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text.
    """
    service = _get_gmail_service(ctx)

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
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                GOOGLE_API_NOT_ENABLED.format(service="Gmail", api_id="gmail.googleapis.com")
            )
        raise ModelRetry(google_api_error("Gmail", e))
