"""Google API authentication — package-private, only imported within co_cli/tools/google/."""

import os
from typing import Any

from google.oauth2.credentials import Credentials

from co_cli.config.core import GOOGLE_TOKEN_PATH
from co_cli.tools.tool_io import tool_error

ALL_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def ensure_google_credentials(
    credentials_path: str | None,
    scopes: list[str],
) -> Any | None:
    """Read existing Google credentials. Acquisition is `co google auth` only.

    This function only *reads* a token already on disk — it never acquires one
    (no gcloud, no ADC copy). gcloud's built-in OAuth client cannot grant Workspace
    user scopes, so `co google auth` (InstalledAppFlow with a user Desktop client)
    is the sole acquisition path.

    Resolution order:
    1. Explicit credentials_path from settings exists -> use it
    2. Default token path (~/.co-cli/google_token.json) exists -> use it
    3. Neither -> return None (caller returns not-configured pointing at `co google auth`)
    """
    # 1. Explicit path from settings
    if credentials_path:
        expanded = os.path.expanduser(credentials_path)
        if os.path.exists(expanded):
            return Credentials.from_authorized_user_file(expanded, scopes=scopes)

    # 2. Default token path
    if GOOGLE_TOKEN_PATH.exists():
        return Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), scopes=scopes)

    # 3. No readable token -> not configured
    return None


def _google_available(deps: Any) -> bool:
    """Per-turn availability check for all Google tools.

    This is the sole gate: registration is unconditional and visibility is
    decided here each turn.

    Before first resolution: show the tool only when a credential source exists on
    disk — an explicit google_credentials_path file, or the default GOOGLE_TOKEN_PATH
    that `co google auth` writes. Users with no Google setup never see the tools,
    while a freshly-authorized token surfaces them with no settings.json edit. The
    tool body then resolves and caches creds.
    After resolution: hide if creds are absent or permanently expired.
    Expired-but-refreshable tokens (refresh_token present) are still shown —
    googleapiclient auto-refreshes on the first API call.
    """
    if not deps.session.google.creds_resolved:
        configured_path = deps.config.google_credentials_path
        if configured_path and os.path.exists(os.path.expanduser(configured_path)):
            return True
        return GOOGLE_TOKEN_PATH.exists()
    creds = deps.session.google.creds
    if creds is None:
        return False
    return not (creds.expired and not creds.refresh_token)


def get_cached_google_creds(deps: Any) -> Any | None:
    """Return cached Google credentials, resolving on first call.

    Cache is stored on the CoDeps instance (not module globals)
    so it follows session lifecycle.

    Args:
        deps: CoDeps instance (typed Any to avoid circular import).
    """
    if not deps.session.google.creds_resolved:
        deps.session.google.creds = ensure_google_credentials(
            deps.config.google_credentials_path,
            ALL_GOOGLE_SCOPES,
        )
        deps.session.google.creds_resolved = True
    return deps.session.google.creds


def _get_google_service(
    ctx: Any,
    service_name: str,
    version: str,
    not_configured_msg: str,
) -> tuple[Any, Any]:
    """Build and return a Google API service client.

    Returns (service, None) on success, or (None, tool_error_result) when
    credentials are not configured.

    Args:
        ctx: RunContext[CoDeps] instance.
        service_name: Google API service name (e.g. "calendar", "drive", "gmail").
        version: API version string (e.g. "v3", "v1").
        not_configured_msg: Error message to surface when credentials are absent.
    """
    from googleapiclient.discovery import build

    creds = get_cached_google_creds(ctx.deps)
    if creds is None:
        return None, tool_error(not_configured_msg, ctx=ctx)
    return build(service_name, version, credentials=creds), None
