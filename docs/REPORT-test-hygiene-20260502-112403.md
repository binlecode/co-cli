# REPORT-test-hygiene-20260502-112403

## Meta
- Scan path: tests/
- Files: 20
- Started: 2026-05-02T11:24:03
- Status: IN PROGRESS

## Phase 1 — Load
- [x] rules loaded
- [x] file list enumerated (20 files)

## Phase 2 — File Read Progress
Note: user scope = memory paths only.
- [x] tests/test_flow_memory_lifecycle.py — CLEAN (1 test, replace+frontmatter preservation)
- [x] tests/test_flow_memory_search.py — CLEAN (1 test, FTS5 sync_dir path)
- [x] tests/test_flow_memory_write.py — [!] dead try/finally:pass wrapper
- [x] tests/test_flow_prompt_assembly.py — CLEAN (memory_search guidance emission tests, still valid)
- [x] tests/test_flow_approval_subject.py — CLEAN (memory_create approval subject, still valid)
- [x] All other files: no memory code paths, not in scope

## Phase 3 — Audit Findings
| File | Line | Rule | Severity | Status |
|------|------|------|----------|--------|
| tests/test_flow_memory_write.py | 74-103 | Dead code: `try/finally: pass` wraps code with no resource to close | Minor | FIXED |

## Phase 4 — Adversarial Review
- lifecycle.py 1-test file: not subsumed — write.py has no replace-success+frontmatter-preservation test
- search.py `test_fts5_search_finds_indexed_entry`: not redundant with write.py — tests `sync_dir()` path vs. `reindex()` path
- No deleted APIs referenced anywhere in memory tests (`memory_list`, `KnowledgeStore`, `_reranker` — all clear)

## Phase 5 — Fixes Applied
| File | Test | Rule | Action | Status |
|------|------|------|--------|--------|
| tests/test_flow_memory_write.py | test_save_artifact_url_keyed_dedup_updates_existing | Dead try/finally:pass | Removed wrapper | DONE |

## Phase 6 — Test Run
- Command: uv run pytest -x
- Log: .pytest-logs/20260502-112403-full.log
- Result: 112 passed, 0 failed

## Phase 7 — Final Verdict
CLEAN — all memory tests target real failure modes; one dead try/finally:pass removed.
