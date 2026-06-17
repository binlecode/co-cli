"""CLI command group for ``co google`` — credential setup and verification.

`co google auth` is the sole credential-acquisition path: it runs the browser
OAuth flow with the user's Desktop-app client and writes an authorized-user token
that the Google tools read on the next `co chat`. No gcloud, no settings.json edit.
`co google check` verifies an existing token against co's required scopes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import typer

from co_cli.config.core import GOOGLE_TOKEN_PATH
from co_cli.fileio.atomic import atomic_write_text
from co_cli.tools.google.auth import ALL_GOOGLE_SCOPES

google_app = typer.Typer(
    name="google",
    help="Set up and verify Google (Gmail/Drive/Calendar) credentials.",
)

_MANUAL_REDIRECT_URI = "http://localhost:1"


def _client_secret_prerequisites(path: str) -> str:
    """Actionable Cloud-Console setup steps shown when the client secret is missing."""
    return (
        f"OAuth client secret not found at: {path}\n\n"
        "co needs a Google OAuth *Desktop-app* client to authorize. In the Google "
        "Cloud Console:\n"
        "  1. Create or select a project.\n"
        "  2. Enable the Gmail API, Google Drive API, and Google Calendar API.\n"
        "  3. Configure the OAuth consent screen (User type: External) and add your "
        "own Google account under 'Test users'.\n"
        "  4. Create credentials → OAuth client ID → Application type: 'Desktop app'.\n"
        f"  5. Download the client JSON and save it to: {path}\n"
        "     (or pass a different path with --client-secret).\n\n"
        "Then re-run `co google auth`."
    )


def _auth_success_message(token_path: str, scopes: list[str]) -> str:
    """Success line for `co google auth` — names the path + granted scopes only.

    Never includes the token, refresh_token, client_secret, or the json blob.
    """
    scope_lines = "\n".join(f"  - {s}" for s in scopes)
    return f"✓ Authorized. Token written to: {token_path}\nGranted scopes:\n{scope_lines}"


def _write_token(creds: Any, target: Path) -> None:
    """Write an authorized-user token (creds.to_json()) to target, locked to 0600.

    The file holds a refresh_token — a secret — so it is written atomically and
    restricted to the owner.
    """
    atomic_write_text(target, creds.to_json())
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)


def _extract_auth_code(pasted: str) -> str:
    """Pull the authorization code from a pasted bare code or full redirect URL.

    With --no-browser the browser is sent to an unreachable loopback URI; the user
    copies back either the `code` query parameter or the whole address-bar URL.
    """
    stripped = pasted.strip()
    if stripped.startswith("http"):
        codes = parse_qs(urlparse(stripped).query).get("code")
        if not codes:
            raise typer.BadParameter("No 'code' parameter found in the pasted URL.")
        return codes[0]
    return stripped


def _authorize_no_browser(flow: Any) -> Any:
    """Headless OAuth for machines with no local browser (SSH/remote).

    Google deprecated the out-of-band flow, so we redirect to an unreachable
    loopback URI and have the user paste the resulting code (or full URL) back.
    The same `flow` object carries the request state through the exchange, and the
    requested scopes are still ALL_GOOGLE_SCOPES — only the redirect mechanism
    differs from `run_local_server`. Returns the authorized Credentials.
    """
    flow.redirect_uri = _MANUAL_REDIRECT_URI
    auth_url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    typer.echo("Visit this URL on any browser, authorize, then paste the result back:")
    typer.echo(f"  {auth_url}")
    pasted = typer.prompt("Paste the authorization code or full redirect URL")
    flow.fetch_token(code=_extract_auth_code(pasted))
    return flow.credentials


@google_app.command("auth")
def google_auth(
    client_secret: str = typer.Option(
        None,
        "--client-secret",
        help="Path to your OAuth Desktop-app client JSON "
        "(default: google_client_secret_path from settings).",
    ),
    credentials_path: str = typer.Option(
        None,
        "--credentials-path",
        help="Token write target (default: ~/.co-cli/google_token.json).",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Print the consent URL and paste the code back, for machines with "
        "no local browser (SSH/remote).",
    ),
) -> None:
    """Run the OAuth flow and write a Google credential token.

    Defaults to a local browser (loopback redirect); pass --no-browser to print
    the consent URL and paste the resulting code on a headless machine.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    from co_cli.config.core import load_config

    config = load_config()
    secret_path = os.path.expanduser(client_secret or config.google_client_secret_path)
    token_target = (
        Path(os.path.expanduser(credentials_path)) if credentials_path else GOOGLE_TOKEN_PATH
    )

    if not os.path.exists(secret_path):
        typer.echo(_client_secret_prerequisites(secret_path), err=True)
        raise typer.Exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(secret_path, ALL_GOOGLE_SCOPES)
    creds = _authorize_no_browser(flow) if no_browser else flow.run_local_server(port=0)
    _write_token(creds, token_target)

    typer.echo(_auth_success_message(str(token_target), list(creds.scopes or ALL_GOOGLE_SCOPES)))
    if credentials_path:
        typer.echo(
            "\nThis is a non-default path. Add it to settings.json so the tools find it:\n"
            f'  "google_credentials_path": "{token_target}"'
        )


def _check_report(granted: list[str], required: list[str]) -> str:
    """Granted-vs-required scope diff + next-step guidance on a shortfall.

    No secrets: operates on scope strings only, never the token or client secret.
    Reuses the re-auth guidance so a scope shortfall yields the same actionable
    `co google auth` instruction as the tool-layer RefreshError classification.
    """
    granted_set = set(granted)
    lines = ["Required scopes:"]
    missing: list[str] = []
    for scope in required:
        ok = scope in granted_set
        lines.append(f"  [{'✓' if ok else '✗'}] {scope}")
        if not ok:
            missing.append(scope)
    if missing:
        lines.append("")
        lines.append(
            "Missing required scope(s). Re-authorize by running `co google auth` to "
            "grant the full set above."
        )
    else:
        lines.append("")
        lines.append("✓ Credential satisfies all required scopes.")
    return "\n".join(lines)


@google_app.command("check")
def google_check(
    credentials_path: str = typer.Option(
        None,
        "--credentials-path",
        help="Token to verify (default: google_credentials_path from settings, "
        "else ~/.co-cli/google_token.json).",
    ),
) -> None:
    """Verify an existing Google credential against co's required scopes."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    from co_cli.config.core import load_config

    config = load_config()
    if credentials_path:
        resolved = os.path.expanduser(credentials_path)
    elif config.google_credentials_path and os.path.exists(
        os.path.expanduser(config.google_credentials_path)
    ):
        resolved = os.path.expanduser(config.google_credentials_path)
    else:
        resolved = str(GOOGLE_TOKEN_PATH)

    if not os.path.exists(resolved):
        typer.echo(
            f"No credential found at: {resolved}\nRun `co google auth` to authorize.", err=True
        )
        raise typer.Exit(1)

    creds = Credentials.from_authorized_user_file(resolved, scopes=ALL_GOOGLE_SCOPES)
    try:
        creds.refresh(Request())
    except RefreshError:
        typer.echo(
            f"Credential at {resolved} is invalid or missing required scopes. "
            "Re-authorize by running `co google auth`.",
            err=True,
        )
        raise typer.Exit(1) from None

    typer.echo(f"Credential: {resolved}")
    typer.echo(_check_report(list(creds.scopes or []), ALL_GOOGLE_SCOPES))
