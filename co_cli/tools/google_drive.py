"""Google Drive tools using RunContext pattern."""

from typing import Any

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps

# Module-level page token cache: maps query -> list of page tokens
# Index 0 = token for page 2, index 1 = token for page 3, etc.
_page_tokens: dict[str, list[str]] = {}


def search_drive(ctx: RunContext[CoDeps], query: str, page: int = 1) -> dict[str, Any]:
    """Search for files in Google Drive. Returns 10 results per page.

    Returns a dict with:
    - display: pre-formatted results with clickable URLs — show this directly to the user
    - page: current page number
    - has_more: whether more results are available

    When the user asks for "more" or "next", call search_drive with the same query and page + 1.

    Args:
        query: Search keywords or metadata query.
        page: Page number (1-based). Use 1 for first page, 2 for second, etc.
    """
    service = ctx.deps.google_drive
    if not service:
        raise ModelRetry(
            "Google Drive not configured. "
            "Set google_credentials_path in settings or run: gcloud auth application-default login"
        )

    try:
        q = f"name contains '{query}' or fullText contains '{query}'"
        request_kwargs: dict[str, Any] = {
            "q": q,
            "pageSize": 10,
            "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
        }

        # Look up stored page token for pages > 1
        if page > 1:
            tokens = _page_tokens.get(query, [])
            token_index = page - 2  # page 2 -> index 0, page 3 -> index 1
            if token_index >= len(tokens):
                raise ModelRetry(
                    f"Page {page} not available. Search from page 1 first, "
                    f"then request pages sequentially."
                )
            request_kwargs["pageToken"] = tokens[token_index]

        results = service.files().list(**request_kwargs).execute()
        items = results.get("files", [])
        if not items:
            if page == 1:
                raise ModelRetry("No results. Try different keywords.")
            return {"display": "No more results.", "page": page, "has_more": False}

        # Store next page token for future use
        next_token = results.get("nextPageToken", "")
        if next_token:
            if query not in _page_tokens:
                _page_tokens[query] = []
            tokens = _page_tokens[query]
            # Ensure token is stored at the right index
            target_index = page - 1  # page 1 result -> index 0 stores token for page 2
            if len(tokens) <= target_index:
                tokens.append(next_token)
            else:
                tokens[target_index] = next_token

        lines = []
        for item in items:
            name = item.get("name", "Untitled")
            modified = item.get("modifiedTime", "")[:10]
            url = item.get("webViewLink", "")
            file_id = item.get("id", "")
            lines.append(f"- {modified}  {name}\n  {url}\n  (id: {file_id})")
        display = f"Page {page} — Found {len(items)} files:\n\n" + "\n".join(lines)
        has_more = bool(next_token)
        if has_more:
            display += f"\n\n(More results available — request page {page + 1})"
        return {"display": display, "page": page, "has_more": has_more}
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
