"""Google API authentication."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from co_cli.config import CONFIG_DIR

GOOGLE_TOKEN_PATH = CONFIG_DIR / "google_token.json"
ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"

ALL_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def ensure_google_credentials(
    credentials_path: str | None,
    scopes: list[str],
) -> Any | None:
    """Ensure Google credentials exist, running gcloud auth if needed.

    Resolution order:
    1. Explicit credentials_path from settings -> use it
    2. Default token path (~/.config/co-cli/google_token.json) exists -> use it
    3. ADC path exists -> copy to default token path, use it
    4. gcloud installed -> run interactive login, copy result, use it
    5. No gcloud -> return None (caller prints error + exits)
    """
    # 1. Explicit path from settings
    if credentials_path:
        expanded = os.path.expanduser(credentials_path)
        if os.path.exists(expanded):
            return Credentials.from_authorized_user_file(expanded, scopes=scopes)

    # 2. Default token path
    if GOOGLE_TOKEN_PATH.exists():
        return Credentials.from_authorized_user_file(
            str(GOOGLE_TOKEN_PATH), scopes=scopes
        )

    # 3. ADC exists -> copy to co-cli config
    if ADC_PATH.exists():
        shutil.copy2(ADC_PATH, GOOGLE_TOKEN_PATH)
        return Credentials.from_authorized_user_file(
            str(GOOGLE_TOKEN_PATH), scopes=scopes
        )

    # 4. Try gcloud interactive login
    if not shutil.which("gcloud"):
        return None  # caller handles error message

    scopes_str = ",".join(scopes)
    result = subprocess.run(
        [
            "gcloud",
            "auth",
            "application-default",
            "login",
            f"--scopes={scopes_str}",
        ],
    )
    if result.returncode != 0:
        return None

    # Copy ADC result to co-cli config
    if ADC_PATH.exists():
        shutil.copy2(ADC_PATH, GOOGLE_TOKEN_PATH)
        return Credentials.from_authorized_user_file(
            str(GOOGLE_TOKEN_PATH), scopes=scopes
        )

    return None


def get_google_credentials(
    credentials_path: str | None,
    scopes: list[str],
) -> Any | None:
    """Get Google credentials from authorized-user file or ADC fallback.

    Non-interactive version for tests and CI.

    Args:
        credentials_path: Path to authorized_user JSON (from gcloud auth).
                          If None/empty, falls back to ADC.
        scopes: OAuth2 scopes (for validation, not granting).

    Returns:
        Credentials object, or None on auth failure.
    """
    try:
        if credentials_path and os.path.exists(credentials_path):
            return Credentials.from_authorized_user_file(
                credentials_path, scopes=scopes
            )
        else:
            creds, _ = google.auth.default(scopes=scopes)
            return creds
    except Exception:
        return None


def build_google_service(
    service_name: str,
    version: str,
    credentials: Any,
) -> Any | None:
    """Build a Google API service client from credentials.

    Returns None if credentials are None or build fails.
    """
    if not credentials:
        return None
    try:
        return build(service_name, version, credentials=credentials)
    except Exception:
        return None
