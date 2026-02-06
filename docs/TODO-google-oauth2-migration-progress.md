# OAuth2 Migration Progress

- [x] Step 1: Rename setting `gcp_key_path` -> `google_credentials_path` in `config.py`
- [x] Step 2: Rewrite `google_auth.py` (drop service_account, add `get_google_credentials` + `build_google_service`)
- [x] Step 3: Update `main.py` (single `get_google_credentials` call, combined scopes)
- [x] Step 4: Update `tests/test_cloud.py` (new imports, function signatures)
- [x] Step 5: Update `settings.example.json`
- [x] Step 6: Update ModelRetry messages in tools (drive, gmail, calendar)
- [x] Step 7: Update `docs/DESIGN-tool-google.md`
- [x] Step 8: Update `README.md`
- [x] Step 9: Update `CLAUDE.md`
- [x] Step 10: Update `docs/DESIGN-co-cli.md`
- [x] Step 11: Update `GEMINI.md`
- [x] Step 12: Rename tool files: `drive.py` -> `google_drive.py`, `gmail.py` -> `google_gmail.py`, `calendar.py` -> `google_calendar.py`
- [x] Step 13: Update all imports for renamed tool files (agent.py, __init__.py, tests, docs)
- [x] Step 14: Verify — grep for stale references (zero in co_cli/ and tests/)
- [x] Step 15: Run tests — 9 passed, 5 skipped (expected)
