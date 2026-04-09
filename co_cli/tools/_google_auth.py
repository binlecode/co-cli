"""Google API authentication."""

import os
import shutil
import subprocess
from typing import Any

import google.auth
from google.oauth2.credentials import Credentials
from co_cli.config._core import GOOGLE_TOKEN_PATH, ADC_PATH

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
    2. Default token path (~/.co-cli/google_token.json) exists -> use it
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



def get_cached_google_creds(deps: Any) -> Any | None:
    """Return cached Google credentials, resolving on first call.

    Cache is stored on the CoDeps instance (not module globals)
    so it follows session lifecycle.

    Args:
        deps: CoDeps instance (typed Any to avoid circular import).
    """
    if not deps.session.google_creds_resolved:
        deps.session.google_creds = ensure_google_credentials(
            deps.config.google_credentials_path, ALL_GOOGLE_SCOPES,
        )
        deps.session.google_creds_resolved = True
    return deps.session.google_creds


