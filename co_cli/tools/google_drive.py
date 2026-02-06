"""Google Drive tools using RunContext pattern."""

from typing import Any

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


def search_drive(ctx: RunContext[CoDeps], query: str) -> list[dict[str, Any]]:
    """Search for files in Google Drive.

    Args:
        query: Search keywords or metadata query.
    """
    service = ctx.deps.google_drive
    if not service:
        raise ModelRetry(
            "Google Drive not configured. "
            "Set google_credentials_path in settings or run: gcloud auth application-default login"
        )

    try:
        q = f"name contains '{query}' or fullText contains '{query}'"
        results = service.files().list(
            q=q,
            pageSize=10,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
        ).execute()
        items = results.get("files", [])
        if not items:
            raise ModelRetry("No results. Try different keywords.")
        return items
    except ModelRetry:
        raise
    except Exception as e:
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                "Google Drive API is not enabled for your project. "
                "Run: gcloud services enable drive.googleapis.com"
            )
        raise ModelRetry(f"Drive API error: {e}")


def read_drive_file(ctx: RunContext[CoDeps], file_id: str) -> str:
    """Fetch the content of a text-based file from Google Drive.

    Args:
        file_id: The Google Drive file ID (from search_drive results).
    """
    service = ctx.deps.google_drive
    if not service:
        raise ModelRetry(
            "Google Drive not configured. "
            "Set google_credentials_path in settings or run: gcloud auth application-default login"
        )

    try:
        file = service.files().get(fileId=file_id, fields="name, mimeType").execute()
        if "application/vnd.google-apps" in file["mimeType"]:
            content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        else:
            content = service.files().get_media(fileId=file_id).execute()
        return content.decode("utf-8")
    except ModelRetry:
        raise
    except Exception as e:
        msg = str(e)
        if "has not been enabled" in msg or "accessNotConfigured" in msg.lower():
            raise ModelRetry(
                "Google Drive API is not enabled for your project. "
                "Run: gcloud services enable drive.googleapis.com"
            )
        raise ModelRetry(f"Drive API error: {e}")
