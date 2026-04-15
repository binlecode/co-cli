"""Google Drive tools using RunContext pattern."""

from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.google._auth import _get_google_service
from co_cli.tools.tool_io import handle_google_api_error, tool_output

_DRIVE_NOT_CONFIGURED = (
    "Drive: not configured. "
    "Set google_credentials_path in settings or run: "
    "gcloud auth application-default login"
)


def _resolve_page_token(ctx: RunContext[CoDeps], query: str, page: int) -> str:
    """Return the stored page token for the given page, or raise ModelRetry."""
    tokens = ctx.deps.session.drive_page_tokens.get(query, [])
    token_index = page - 2  # page 2 -> index 0, page 3 -> index 1
    if token_index >= len(tokens):
        raise ModelRetry(
            f"Page {page} not available. Search from page 1 first, "
            f"then request pages sequentially."
        )
    return tokens[token_index]


def _store_page_token(ctx: RunContext[CoDeps], query: str, page: int, next_token: str) -> None:
    """Persist next_token so future pages can be requested."""
    if query not in ctx.deps.session.drive_page_tokens:
        ctx.deps.session.drive_page_tokens[query] = []
    tokens = ctx.deps.session.drive_page_tokens[query]
    target_index = page - 1  # page 1 result -> index 0 stores token for page 2
    if len(tokens) <= target_index:
        tokens.append(next_token)
    else:
        tokens[target_index] = next_token


def _format_drive_results(
    ctx: RunContext[CoDeps], items: list[dict[str, Any]], page: int, next_token: str
) -> ToolReturn:
    """Format Drive file list into a tool_output."""
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
    return tool_output(display, ctx=ctx, page=page, has_more=has_more)


def search_drive_files(ctx: RunContext[CoDeps], query: str, page: int = 1) -> ToolReturn:
    """Search files in Google Drive by name or content. Returns up to 10
    results per page. Matches files whose name contains the query OR whose
    full text body contains the query.

    Pagination: When has_more is true, call again with page + 1 to get the
    next batch. Keep paginating until has_more is false when the task requires
    complete results (counts, summaries, exhaustive listings). Pages must be
    requested sequentially — you cannot skip to page 3 without fetching page 2.

    To read a file's content, pass its id to read_drive_file.

    Returns a dict with:
    - display: pre-formatted results with clickable URLs — show directly to user
    - page: current page number
    - has_more: whether more results exist (paginate if you need complete data)

    Caveats:
    - fullText search only works on Google Workspace docs and indexed text files,
      not PDFs or binary formats
    - Results are unordered by default (Drive API relevance ranking)

    Args:
        query: Search keywords (e.g. "weekly meeting", "Q4 budget report").
        page: Page number (1-based). Use 1 for first page, 2 for next, etc.
    """
    service, err = _get_google_service(ctx, "drive", "v3", _DRIVE_NOT_CONFIGURED)
    if err:
        return err

    try:
        q = f"name contains '{query}' or fullText contains '{query}'"
        request_kwargs: dict[str, Any] = {
            "q": q,
            "pageSize": 10,
            "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
        }
        if page > 1:
            request_kwargs["pageToken"] = _resolve_page_token(ctx, query, page)

        results = service.files().list(**request_kwargs).execute()
        items = results.get("files", [])
        if not items:
            if page == 1:
                return tool_output("No files found.", ctx=ctx, count=0, page=1, has_more=False)
            return tool_output("No more results.", ctx=ctx, count=0, page=page, has_more=False)

        next_token = results.get("nextPageToken", "")
        if next_token:
            _store_page_token(ctx, query, page, next_token)

        return _format_drive_results(ctx, items, page, next_token)
    except ModelRetry:
        raise
    except Exception as e:
        return handle_google_api_error("Drive", e, ctx=ctx)


def read_drive_file(ctx: RunContext[CoDeps], file_id: str) -> ToolReturn:
    """Fetch the content of a file from Google Drive and return it as text.

    Google Workspace documents (Docs, Sheets, Slides) are exported as plain
    text. Other files are downloaded as-is and decoded as UTF-8.

    Use file IDs from search_drive_files results. Do not guess file IDs.

    Returns the file content as a ToolReturn — show directly to the user
    or pass to further processing.

    Caveats:
    - Binary files (images, videos, zip) will fail or produce garbled output
    - Large files may be slow or truncated by API limits

    Args:
        file_id: The Google Drive file ID (from search_drive_files results,
                 e.g. "1e2ijrBd74oruWB0b-xGTiQvSwxGO6KK9HPTGaXnmOtI").
    """
    service, err = _get_google_service(ctx, "drive", "v3", _DRIVE_NOT_CONFIGURED)
    if err:
        return err

    try:
        file = service.files().get(fileId=file_id, fields="name, mimeType").execute()
        if "application/vnd.google-apps" in file["mimeType"]:
            content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        else:
            content = service.files().get_media(fileId=file_id).execute()
        text = content.decode("utf-8")

        # FTS index — opportunistically cache Drive content after full fetch
        if ctx.deps.knowledge_store is not None:
            try:
                import hashlib as _hashlib

                ctx.deps.knowledge_store.index(
                    source="drive",
                    path=file_id,
                    title=file.get("name"),
                    content=text,
                    hash=_hashlib.sha256(text.encode()).hexdigest(),
                )
                from co_cli.knowledge._chunker import chunk_text

                drive_chunks = chunk_text(
                    text,
                    chunk_size=ctx.deps.config.knowledge.chunk_size,
                    overlap=ctx.deps.config.knowledge.chunk_overlap,
                )
                ctx.deps.knowledge_store.index_chunks("drive", file_id, drive_chunks)
            except Exception:
                pass

        return tool_output(text, ctx=ctx)
    except ModelRetry:
        raise
    except Exception as e:
        return handle_google_api_error("Drive", e, ctx=ctx)
