"""Behavior of the `co google` command group — auth setup and credential verify.

`co google auth` is the sole credential-acquisition path; `co google check` verifies
an existing token against the required scopes. The interactive browser leg
(`run_local_server`) is verified manually — these tests cover the surrounding,
deterministic logic: CLI registration, the token write/round-trip, no-secrets
output, the not-configured redirect, the missing-client-secret guidance, and the
scope-diff report.
"""

from __future__ import annotations

import importlib
import os
import stat
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from co_cli.commands.google import (
    _auth_success_message,
    _check_report,
    _client_secret_prerequisites,
    _extract_auth_code,
    _write_token,
    google_app,
)
from co_cli.tools.google._auth import ALL_GOOGLE_SCOPES


def test_auth_help_lists_options() -> None:
    """(a) `co google auth --help` exits 0 and documents both options."""
    result = CliRunner().invoke(google_app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "--client-secret" in result.output
    assert "--credentials-path" in result.output


def test_auth_help_lists_no_browser_option() -> None:
    """The headless path is discoverable: `auth --help` documents --no-browser."""
    result = CliRunner().invoke(google_app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "--no-browser" in result.output


def test_extract_auth_code_from_bare_code() -> None:
    """A pasted bare authorization code is returned verbatim (trimmed)."""
    assert _extract_auth_code("  4/0AbCdEf-code  ") == "4/0AbCdEf-code"


def test_extract_auth_code_from_redirect_url() -> None:
    """A pasted full redirect URL yields its `code` query parameter."""
    url = "http://localhost:1/?state=xyz&code=4/0AbCdEf-code&scope=https://mail.google.com"
    assert _extract_auth_code(url) == "4/0AbCdEf-code"


def test_extract_auth_code_url_without_code_raises() -> None:
    """A redirect URL missing the `code` param is a usage error, not a silent pass."""
    with pytest.raises(typer.BadParameter):
        _extract_auth_code("http://localhost:1/?state=xyz&error=access_denied")


def test_check_help_exits_zero() -> None:
    """(TASK-4) `co google check --help` exits 0."""
    result = CliRunner().invoke(google_app, ["check", "--help"])
    assert result.exit_code == 0


def test_token_round_trip_loads_back(tmp_path: Path) -> None:
    """(b) A token written from a populated Credentials loads via from_authorized_user_file.

    Requires client_id/client_secret/refresh_token in the json — to_json() carries
    them. The token holds a secret, so it must be locked to 0600.
    """
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token="ya29.fake-access-token",
        refresh_token="1//fake-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="123456789.apps.googleusercontent.com",
        client_secret="GOCSPX-fake-client-secret",
        scopes=ALL_GOOGLE_SCOPES,
    )
    target = tmp_path / "google_token.json"

    _write_token(creds, target)

    loaded = Credentials.from_authorized_user_file(str(target), scopes=ALL_GOOGLE_SCOPES)
    assert loaded.refresh_token == "1//fake-refresh-token"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_auth_success_message_names_path_and_scopes_no_secrets() -> None:
    """(c) The success line carries the path + scopes and no secret material."""
    msg = _auth_success_message("/home/u/.co-cli/google_token.json", ALL_GOOGLE_SCOPES)
    assert "/home/u/.co-cli/google_token.json" in msg
    assert "gmail.readonly" in msg
    assert "gmail.compose" in msg
    assert "refresh_token" not in msg
    assert "client_secret" not in msg
    assert "ya29." not in msg


def test_ensure_credentials_returns_none_without_token(tmp_path, monkeypatch) -> None:
    """(d) With no readable token, resolution returns None — no gcloud/ADC acquisition.

    The acquisition legs are gone, so the helper module no longer imports subprocess
    or shutil (CD-m-8). Point the default token path at an absent file so the result
    is deterministic regardless of the dev machine's real ~/.co-cli token.
    """
    import co_cli.tools.google._auth as auth_mod

    monkeypatch.setattr(auth_mod, "GOOGLE_TOKEN_PATH", tmp_path / "absent_token.json")
    result = auth_mod.ensure_google_credentials(
        str(tmp_path / "nonexistent_creds.json"), ALL_GOOGLE_SCOPES
    )
    assert result is None
    assert not hasattr(auth_mod, "subprocess")
    assert not hasattr(auth_mod, "shutil")


def test_not_configured_messages_point_at_co_google_auth() -> None:
    """(d) The three not-configured strings name `co google auth`, not gcloud."""
    from co_cli.tools.google.calendar import _CALENDAR_NOT_CONFIGURED
    from co_cli.tools.google.drive import _DRIVE_NOT_CONFIGURED
    from co_cli.tools.google.gmail import _GMAIL_NOT_CONFIGURED

    for msg in (_GMAIL_NOT_CONFIGURED, _DRIVE_NOT_CONFIGURED, _CALENDAR_NOT_CONFIGURED):
        assert "co google auth" in msg
        assert "gcloud" not in msg


def test_client_secret_prerequisites_lists_console_steps() -> None:
    """(e) The missing-client-secret message enumerates the Cloud-Console setup steps."""
    msg = _client_secret_prerequisites("/x/secret.json")
    assert "/x/secret.json" in msg
    for keyword in ("Cloud Console", "Gmail API", "consent screen", "Desktop app", "Test users"):
        assert keyword in msg


def test_auth_missing_client_secret_exits_nonzero(tmp_path, monkeypatch) -> None:
    """(e) Invoking auth with an absent client secret exits 1 with the guidance."""
    monkeypatch.setenv("CO_HOME", str(tmp_path))
    import co_cli.commands.google as google_mod
    import co_cli.config.core as core_mod

    importlib.reload(core_mod)
    importlib.reload(google_mod)

    result = CliRunner().invoke(
        google_mod.google_app, ["auth", "--client-secret", str(tmp_path / "nope.json")]
    )
    assert result.exit_code == 1
    assert "Desktop app" in result.output


def test_check_report_flags_shortfall_with_guidance() -> None:
    """(TASK-4) A granted set missing required scopes yields the actionable re-auth message."""
    granted = ["https://www.googleapis.com/auth/gmail.readonly"]
    report = _check_report(granted, ALL_GOOGLE_SCOPES)
    assert "co google auth" in report
    assert "gmail.compose" in report
    assert "refresh_token" not in report
    assert "client_secret" not in report


def test_check_report_satisfied_set_has_no_reauth() -> None:
    """(TASK-4) A fully-granted set reports satisfied and does not nag to re-auth."""
    report = _check_report(list(ALL_GOOGLE_SCOPES), ALL_GOOGLE_SCOPES)
    assert "satisfies all required" in report
    assert "co google auth" not in report
