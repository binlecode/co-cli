"""Gmail tools using RunContext pattern."""

import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools._google_auth import get_cached_google_creds
from co_cli.tools.tool_errors import handle_google_api_error, tool_error
from co_cli.tools.tool_output import tool_output

_GMAIL_NOT_CONFIGURED = (
    "Gmail: not configured. "
    "Set google_credentials_path in settings or run: "
    "gcloud auth application-default login"
)


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

    Returns (service, None) on success, or (None, ToolReturn) for terminal config errors.
    """
    creds = get_cached_google_creds(ctx.deps)
    if not creds:
        return None, tool_error(_GMAIL_NOT_CONFIGURED)
    return build("gmail", "v1", credentials=creds), None


def list_gmail_emails(ctx: RunContext[CoDeps], max_results: int = 5) -> ToolReturn:
    """List the most recent emails from the user's Gmail inbox.

    Use this for a quick inbox overview. For targeted queries (by sender,
    date range, subject, read status), use search_gmail_emails instead.

    Returns a dict with:
    - display: pre-formatted email list with sender, subject, date, preview,
      and clickable Gmail links — show directly to the user
    - count: number of emails returned

    Args:
        max_results: Number of emails to return (default 5, max ~100).
    """
    service, err = _get_gmail_service(ctx)
    if err:
        return err

    try:
        response = service.users().messages().list(userId="me", maxResults=max_results).execute()
        messages = response.get("messages", [])
        if not messages:
            return tool_output("No emails found.", ctx=ctx, count=0)
        display = f"Recent Emails ({len(messages)}):\n" + _format_messages(service, messages)
        return tool_output(display, ctx=ctx, count=len(messages))
    except ModelRetry:
        raise
    except Exception as e:
        return handle_google_api_error("Gmail", e)


def search_gmail_emails(ctx: RunContext[CoDeps], query: str, max_results: int = 5) -> ToolReturn:
    """Search emails in Gmail using Gmail search syntax.

    Supports the full Gmail query language. Common operators:
    - from:alice, to:bob — sender/recipient
    - subject:invoice — subject line
    - is:unread, is:starred — status flags
    - newer_than:2d, older_than:1w — relative dates
    - has:attachment — attachment filter
    - label:work — label filter
    Combine with spaces for AND logic: "from:alice subject:invoice newer_than:7d"

    Returns a dict with:
    - display: pre-formatted results with sender, subject, date, preview,
      and clickable Gmail links — show directly to the user
    - count: number of emails returned

    Args:
        query: Gmail search query (e.g. "from:alice subject:invoice newer_than:7d").
        max_results: Number of emails to return (default 5, max ~100).
    """
    service, err = _get_gmail_service(ctx)
    if err:
        return err

    try:
        response = (
            service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        )
        messages = response.get("messages", [])
        if not messages:
            return tool_output(f"No emails found for query: {query}", ctx=ctx, count=0)
        display = f"Search results for '{query}' ({len(messages)}):\n" + _format_messages(
            service, messages
        )
        return tool_output(display, ctx=ctx, count=len(messages))
    except ModelRetry:
        raise
    except Exception as e:
        return handle_google_api_error("Gmail", e)


def create_gmail_draft(ctx: RunContext[CoDeps], to: str, subject: str, body: str) -> ToolReturn:
    """Create a draft email in Gmail. Does NOT send — the user reviews and
    sends manually from Gmail.

    Creates a plain-text draft. For rich formatting, the user can edit the
    draft in Gmail before sending.

    Returns a confirmation string with the recipient, subject, and draft ID.

    Args:
        to: Recipient email address (e.g. "alice@example.com").
        subject: Email subject line.
        body: Email body as plain text.
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
        display = f"Draft created for {to} with subject '{subject}'. Draft ID: {draft['id']}"
        return tool_output(display, ctx=ctx, draft_id=draft["id"], to=to, subject=subject)
    except ModelRetry:
        raise
    except Exception as e:
        return handle_google_api_error("Gmail", e)
