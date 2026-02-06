"""Gmail tools using RunContext pattern."""

import base64
from email.mime.text import MIMEText

from rich.console import Console
from rich.prompt import Confirm
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps

_console = Console()


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
        output += (
            f"- From: {headers.get('From', 'unknown')}\n"
            f"  Subject: {headers.get('Subject', '(no subject)')}\n"
            f"  Date: {headers.get('Date', 'unknown')}\n"
            f"  Preview: {snippet}\n"
        )
    return output


def _get_gmail_service(ctx: RunContext[CoDeps]):
    """Extract and validate Gmail service from context."""
    service = ctx.deps.google_gmail
    if not service:
        raise ModelRetry(
            "Gmail not configured. "
            "Set google_credentials_path in settings or run: gcloud auth application-default login"
        )
    return service


def list_emails(ctx: RunContext[CoDeps], max_results: int = 5) -> str:
    """List recent emails from the user's Gmail inbox.

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
            return "No emails found."
        return "Recent Emails:\n" + _format_messages(service, messages)
    except ModelRetry:
        raise
    except Exception as e:
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                "Gmail API is not enabled for your project. "
                "Run: gcloud services enable gmail.googleapis.com"
            )
        raise ModelRetry(f"Gmail API error: {e}")


def search_emails(ctx: RunContext[CoDeps], query: str, max_results: int = 5) -> str:
    """Search emails in Gmail using Gmail search syntax.

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
            return f"No emails found for query: {query}"
        return f"Search results for '{query}':\n" + _format_messages(service, messages)
    except ModelRetry:
        raise
    except Exception as e:
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                "Gmail API is not enabled for your project. "
                "Run: gcloud services enable gmail.googleapis.com"
            )
        raise ModelRetry(f"Gmail API error: {e}")


def draft_email(ctx: RunContext[CoDeps], to: str, subject: str, body: str) -> str:
    """Draft an email in Gmail.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text.
    """
    service = ctx.deps.google_gmail
    if not service:
        raise ModelRetry(
            "Gmail not configured. "
            "Set google_credentials_path in settings or run: gcloud auth application-default login"
        )

    if not ctx.deps.auto_confirm:
        if not Confirm.ask(
            f"Draft email to [bold]{to}[/bold]?",
            default=False,
            console=_console,
        ):
            return "Email draft cancelled by user."

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
                "Gmail API is not enabled for your project. "
                "Run: gcloud services enable gmail.googleapis.com"
            )
        raise ModelRetry(f"Gmail API error: {e}")
