# REPORT-test-hygiene-20260502-222939

## Meta
- Scan path: tests/test_flow_memory_store_nochunk.py tests/test_flow_bootstrap_canon.py tests/test_flow_canon_recall.py
- Files: 3
- Started: 2026-05-02T22:29:39Z
- Status: DONE

## Phase 1 — Load
- [x] rules loaded (agent_docs/testing.md)
- [x] support files read: conftest.py, _settings.py, _timeouts.py
- [x] file list enumerated (3 files)

## Phase 2 — File Read Progress
- [!] tests/test_flow_memory_store_nochunk.py — unused `title` param in helper; double make_settings() call
- [!] tests/test_flow_bootstrap_canon.py — tmp_path fixture not wired in one test; double make_settings()
- [!] tests/test_flow_canon_recall.py — truthy-only assert; unused `search_backend` param; double make_settings()

## Phase 3 — Audit Findings
| File | Location | Rule | Severity | Status |
|------|----------|------|----------|--------|
| test_flow_bootstrap_canon.py | test_sync_canon_store_noop_when_store_is_none signature | Fixture not wired: `tmp_path` in signature, never passed to any production function | Blocking | OPEN |
| test_flow_canon_recall.py | test_canon_hit_returns_full_body L3 of assertions | Truthy-only assertion: `assert top["body"]` — redundant, immediately followed by stronger content check | Blocking | OPEN |
| test_flow_memory_store_nochunk.py | _write_canon_file signature | Dead parameter: `title` accepted but never used | Minor | OPEN |
| test_flow_memory_store_nochunk.py | _make_store | double make_settings() call — inner `.knowledge` reference duplicates base load | Minor | OPEN |
| test_flow_bootstrap_canon.py | _make_store | double make_settings() call — same pattern | Minor | OPEN |
| test_flow_canon_recall.py | _make_store | double make_settings() call + unused `search_backend` param | Minor | OPEN |
| test_flow_bootstrap_canon.py / test_flow_canon_recall.py | _SilentFrontend class | Duplicated helper across files | Minor | OPEN |

## Phase 4 — Adversarial Review
- test_flow_bootstrap_canon.py `tmp_path` — confirmed violation: `test_sync_canon_store_noop_when_store_is_none` body contains only `make_settings()` and `_sync_canon_store(None, ...)`. No `tmp_path` usage whatsoever. Fix: remove from signature.
- test_flow_canon_recall.py truthy assert — confirmed: `assert top["body"]` followed by `assert "humor" in top["body"].lower()`. Empty body fails both; non-empty body with "humor" passes both. The truthy check adds zero coverage. Fix: remove it.
- `_write_canon_file` title param — confirmed unused across all 5 call sites. Dead code. Remove.
- double make_settings() — confirmed: `_make_store` calls `make_settings()` then also calls `make_settings().knowledge.model_copy(...)`. The inner call should reference the base directly using `SETTINGS` from the shared module. Not a blocking rule violation (not module-scope), but contradicts the spirit of the centralized-config rule.
- `_SilentFrontend` duplication — not a testing rule violation per se; the rules don't prohibit helper duplication. Mark as minor / not fixed.

## Phase 5 — Fixes Applied
| File | Finding | Action | Status |
|------|---------|--------|--------|
| test_flow_bootstrap_canon.py | tmp_path not wired | Removed `tmp_path: Path` from `test_sync_canon_store_noop_when_store_is_none` signature | DONE |
| test_flow_canon_recall.py | truthy-only assert | Removed `assert top["body"]`; renamed test to `test_canon_hit_body_contains_query_content` | DONE |
| test_flow_memory_store_nochunk.py | dead `title` param | Removed from `_write_canon_file` and all 5 call sites | DONE |
| all three files | double make_settings() | Hoisted `_FTS5_CONFIG`/`_STORE_CONFIG` to module scope using `SETTINGS`; `_make_store` is now a one-liner | DONE |
| test_flow_canon_recall.py | unused `search_backend` param | Removed from `_make_store` | DONE |
| test_flow_memory_store_nochunk.py | weak content assertion | Added `len(content) == len(_CANON_BODY.strip())` exact-length check to `test_get_chunk_content_returns_full_body` | DONE |

## Phase 6 — Test Run
- Command: `uv run pytest tests/test_flow_memory_store_nochunk.py tests/test_flow_bootstrap_canon.py tests/test_flow_canon_recall.py -x -v`
- Log: `.pytest-logs/20260502-222939-test-hygiene.log`
- Result: 12 passed, 0 failed

## Phase 7 — Final Verdict
CLEAN — 2 blocking violations fixed, 3 minor violations fixed; 12 tests green.
