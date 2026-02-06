# TODO: Replace Service Account with OAuth2 User Credentials

**Status:** Planned
**Blocked by:** None
**Goal:** Replace service account key auth (Option B) with personal OAuth2 credentials generated via `gcloud` CLI. Make this the preferred/recommended auth method.

---

## Motivation

The current Option B (service account) has fundamental limitations for personal use:

| Problem | Impact |
|---------|--------|
| Service accounts are separate identities | No access to user's personal Drive, Gmail, Calendar |
| Must explicitly share Drive files with SA email | Cumbersome setup for personal use |
| Gmail drafts go to SA's mailbox, not user's | Gmail tool is effectively unusable |
| Calendar shows SA's calendar, not user's | Calendar tool is effectively unusable |
| Key file contains permanent credentials | Security risk if leaked |

**Solution:** Use `gcloud auth application-default login` to generate an `authorized_user` credentials file. This runs as the user's own identity — full access to personal Drive, Gmail, and Calendar with no sharing required.

---

## How `gcloud` Credential Files Work

### Generation

```bash
gcloud auth application-default login \
  --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar.readonly'
```

This opens a browser for Google consent, then writes credentials to:
`~/.config/gcloud/application_default_credentials.json`

### File Format

```json
{
  "account": "",
  "client_id": "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com",
  "client_secret": "d-FL95Q19q7MQmFpd7hHD0Ty",
  "refresh_token": "1//0e...",
  "type": "authorized_user",
  "universe_domain": "googleapis.com"
}
```

Key fields: `client_id`, `client_secret`, `refresh_token`, `type: "authorized_user"`.

### Loading in Python

```python
from google.oauth2.credentials import Credentials

creds = Credentials.from_authorized_user_file(path, scopes=scopes)
```

The `Credentials` object auto-refreshes the access token using the `refresh_token` when making API calls (handled by `googleapiclient`'s HTTP transport layer). No manual refresh code needed.

### Scope Behavior

Scopes are fixed at `gcloud auth` login time. The `scopes` parameter passed to `from_authorized_user_file()` is for validation — it doesn't grant new scopes. The user must include all required scopes in the original `gcloud auth` command.

---

## New Auth Architecture

### Preferred: Explicit Credentials File (New Option B → Promoted to Recommended)

```
User runs:
  gcloud auth application-default login --scopes='...'
      │
      ▼
  ~/.config/gcloud/application_default_credentials.json
      │
      │  User copies to co-cli config (optional but recommended):
      │  cp ~/.config/gcloud/application_default_credentials.json \
      │     ~/.config/co-cli/google_token.json
      │
      ▼
  settings.json: "google_credentials_path": "~/.config/co-cli/google_token.json"
      │
      ▼
  google_auth.py: Credentials.from_authorized_user_file(path, scopes)
      │
      ▼
  build("drive", "v3", credentials=creds)
```

**Why copy the file?**
- Isolation: co-cli credentials are separate from gcloud's ADC (which other tools may overwrite)
- Portability: the file can be copied to another machine
- Explicitness: config points to a specific file

### Fallback: ADC (Current Option A → Demoted to Fallback)

```
User runs:
  gcloud auth application-default login --scopes='...'
      │
      ▼
  google_auth.py: google.auth.default(scopes=scopes)
      │
      ▼
  Automatically finds ~/.config/gcloud/application_default_credentials.json
```

Still works, but less explicit. If the user runs `gcloud auth application-default login` again for another project (without co-cli scopes), the credentials get overwritten.

---

## Implementation Steps

### Step 1: Rename Setting

**File:** `co_cli/config.py`

Rename `gcp_key_path` → `google_credentials_path`. Update env var mapping.

```python
# Before
gcp_key_path: Optional[str] = Field(default=None)
# env_map: "gcp_key_path": "GCP_KEY_PATH"

# After
google_credentials_path: Optional[str] = Field(default=None)
# env_map: "google_credentials_path": "GOOGLE_CREDENTIALS_PATH"
```

**Backward compat:** Not needed. Project is pre-1.0, no external users. Just rename everywhere.

### Step 2: Rewrite `co_cli/google_auth.py`

Replace `service_account.Credentials.from_service_account_file` with `google.oauth2.credentials.Credentials.from_authorized_user_file`.

```python
"""Google API authentication."""

import os
from typing import Any

import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def get_google_credentials(
    credentials_path: str | None,
    scopes: list[str],
) -> Any | None:
    """Get Google credentials from authorized-user file or ADC fallback.

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
```

**Key changes:**
- `build_google_service()` now takes `credentials` (not `scopes` + `key_path`) — it's a pure builder
- `get_google_credentials()` is the new entry point — handles auth strategy selection
- `service_account` import removed entirely
- `google.oauth2.credentials.Credentials` handles auto-refresh via the HTTP transport layer

### Step 3: Update `co_cli/main.py`

`create_deps()` calls `get_google_credentials()` once with combined scopes, then builds 3 services from the same credentials.

```python
from co_cli.google_auth import get_google_credentials, build_google_service

ALL_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
]

def create_deps() -> CoDeps:
    ...
    # Single auth call for all Google services
    google_creds = get_google_credentials(
        settings.google_credentials_path,
        ALL_GOOGLE_SCOPES,
    )
    google_drive = build_google_service("drive", "v3", google_creds)
    google_gmail = build_google_service("gmail", "v1", google_creds)
    google_calendar = build_google_service("calendar", "v3", google_creds)
    ...
```

**Key change:** One `get_google_credentials()` call instead of three `build_google_service()` calls with separate scopes. Scopes are combined because `authorized_user` credentials are scoped at login time (all-or-nothing).

### Step 4: Update `tests/test_cloud.py`

Update imports and test helper to use new function signatures.

```python
from co_cli.google_auth import get_google_credentials, build_google_service

ALL_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Skip condition changes
HAS_GCP = bool(settings.google_credentials_path
               and os.path.exists(settings.google_credentials_path))
# OR: try get_google_credentials() and check if it returns non-None

@pytest.mark.skipif(not HAS_GCP, reason="Google credentials missing")
def test_drive_search_functional():
    google_creds = get_google_credentials(
        settings.google_credentials_path, ALL_GOOGLE_SCOPES
    )
    drive_service = build_google_service("drive", "v3", google_creds)
    ctx = _make_ctx(google_drive=drive_service)
    ...
```

### Step 5: Update `settings.example.json`

```json
{
  "llm_provider": "gemini",
  "gemini_api_key": "",
  "gemini_model": "gemini-2.0-flash",
  "ollama_host": "http://localhost:11434",
  "ollama_model": "llama3",
  "obsidian_vault_path": "",
  "google_credentials_path": "",
  "slack_bot_token": "",
  "docker_image": "python:3.12-slim",
  "auto_confirm": false
}
```

### Step 6: Update `docs/DESIGN-tool-google.md`

Replace Option B (Service Account) with new OAuth2 user credentials approach. Remove "Why Not OAuth2 Client Credentials?" section (we now support it, via gcloud).

**New recommended option:**

```
### Recommended: Personal Credentials via gcloud (Option B)

1. Install Google Cloud CLI
2. Generate credentials with all required scopes:

    gcloud auth application-default login \
      --scopes='https://www.googleapis.com/auth/drive.readonly,\
    https://www.googleapis.com/auth/gmail.modify,\
    https://www.googleapis.com/auth/calendar.readonly'

3. Copy to co-cli config for isolation:

    cp ~/.config/gcloud/application_default_credentials.json \
       ~/.config/co-cli/google_token.json

4. Set in settings.json:

    { "google_credentials_path": "~/.config/co-cli/google_token.json" }

How it works: Credentials.from_authorized_user_file() loads the file.
Access tokens auto-refresh via the embedded refresh_token.
```

**Update Option A** to be the fallback (ADC, no file path needed).

**Update comparison table:**

| | Recommended (Credentials File) | Fallback (ADC) |
|--|-------------------------------|----------------|
| Best for | All personal use | Quick setup / testing |
| Auth method | `gcloud auth` → copy file | `gcloud auth` (auto-detected) |
| Drive/Gmail/Calendar | User's own | User's own |
| Isolation | Separate file, won't be overwritten | Shared with other gcloud tools |
| Portability | Copy file to another machine | Tied to gcloud on this machine |
| Config | `google_credentials_path: "/path/..."` | `google_credentials_path: ""` |

**Remove:** Service account sections, "Why Not OAuth2?" section, service account rows from comparison table.

### Step 7: Update `README.md`

- Rename "Option A / Option B" to match new design doc
- Update settings.json example (`gcp_key_path` → `google_credentials_path`)
- Update gcloud command examples
- Remove service account instructions entirely

### Step 8: Update `CLAUDE.md`

- Update config table (setting name change)

### Step 9: Update `docs/DESIGN-co-cli.md`

- Update Settings table (`gcp_key_path` → `google_credentials_path`, env var change)
- Update CoDeps sections if they reference `gcp_key_path`

---

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `co_cli/config.py` | MODIFY | Rename `gcp_key_path` → `google_credentials_path`, update env map |
| `co_cli/google_auth.py` | REWRITE | `get_google_credentials()` + `build_google_service()`, drop `service_account` |
| `co_cli/main.py` | MODIFY | Single `get_google_credentials()` call, combined scopes |
| `tests/test_cloud.py` | MODIFY | Update imports, use new function signatures |
| `settings.example.json` | MODIFY | Rename field |
| `docs/DESIGN-tool-google.md` | MODIFY | Replace Option B, update auth flow, remove SA references |
| `docs/DESIGN-co-cli.md` | MODIFY | Update config table, env var mapping |
| `README.md` | MODIFY | Update Google setup instructions, rename setting |
| `CLAUDE.md` | MODIFY | Update config references |

---

## What Gets Removed

- `from google.oauth2 import service_account` — no longer needed anywhere
- Service account setup instructions in README and design docs
- `GCP_KEY_PATH` env var (replaced by `GOOGLE_CREDENTIALS_PATH`)
- "Why Not OAuth2 Client Credentials?" section in design doc
- All references to `gcp_key_path` (grep to verify: zero matches after migration)

---

## Verification

```bash
# 1. Tests pass
uv run pytest -v

# 2. Grep for stale references
grep -r "gcp_key_path\|GCP_KEY_PATH\|service_account" co_cli/ tests/ docs/
# Should return zero matches (except maybe this TODO file)

# 3. Manual — with gcloud credentials
gcloud auth application-default login \
  --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar.readonly'
cp ~/.config/gcloud/application_default_credentials.json ~/.config/co-cli/google_token.json
# Set google_credentials_path in settings.json
uv run co chat
Co > search my drive for meeting notes

# 4. Manual — with ADC fallback (no google_credentials_path set)
# Should still work via google.auth.default()

# 5. Manual — unconfigured (no gcloud, no credentials)
# Should get ModelRetry → agent tells user to run gcloud command
```

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Existing `settings.json` has `gcp_key_path` | High (developer's own file) | Rename manually; load_config ignores unknown keys |
| `authorized_user` token expires | Low (auto-refresh handles it) | `Credentials` class refreshes via `refresh_token` automatically |
| User runs `gcloud auth` without all scopes | Medium | ModelRetry message tells user which scope failed; gcloud command in docs includes all scopes |
| `refresh_token` revoked by Google | Low | User re-runs `gcloud auth` command; ModelRetry surfaces the error |

---

## References

- [How ADC Works](https://docs.google.com/docs/authentication/application-default-credentials)
- [gcloud auth application-default login](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login)
- [google.oauth2.credentials.Credentials](https://googleapis.dev/python/google-auth/latest/reference/google.oauth2.credentials.html)
- [google-auth-library-python source](https://github.com/googleapis/google-auth-library-python/blob/main/google/oauth2/credentials.py)
