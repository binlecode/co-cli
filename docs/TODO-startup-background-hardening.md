# TODO: Startup And Background Task Hardening

Scope: fix two user-visible reliability bugs now covered by regression tests in [`tests/test_startup_failures.py`](/Users/binle/workspace_genai/co-cli/tests/test_startup_failures.py).

## Tasks

- [x] TASK-1: clean startup failure path for `co chat`
  done_when:
  - missing required provider credentials exits cleanly with a short user-facing error
  - `co chat` does not print a Python traceback for expected bootstrap failures
  - startup cleanup remains safe when failure happens before `_chat_loop()` finishes initialization
  files:
  - `co_cli/main.py`
  - `co_cli/bootstrap/_bootstrap.py`

- [x] TASK-2: unique background task IDs
  done_when:
  - two tasks started in the same second never reuse the same `task_id`
  - task metadata/output/result files are isolated per task
  - task IDs remain readable in CLI output
  files:
  - `co_cli/tools/_background.py`

- [x] TASK-3: bootstrap knowledge backend degradation policy
  done_when:
  - `hybrid` startup does not hard-fail the session when sqlite-vec setup is unavailable
  - bootstrap degrades to a supported backend with an explicit status message
  - DESIGN docs match the implemented fallback behavior
  files:
  - `co_cli/bootstrap/_bootstrap.py`
  - `co_cli/knowledge/_index_store.py`
  - `docs/DESIGN-system-bootstrap.md`

## Notes

- Current regression coverage intentionally fails on:
  - raw traceback during startup failure
  - same-second task ID collision
- `TASK-3` now has deterministic regression coverage via a real invalid hybrid vector schema (`knowledge_embedding_dims=0` at the `CoConfig` layer), which exercises bootstrap degradation without a test-only hook.

---

# Audit Log

## Cycle C1 — Team Lead

Assessment: revise

Findings:
- TASK-1 and TASK-2 were already effectively implemented in the active worktree and covered by `tests/test_startup_failures.py`; the TODO still described them as pending.
- DESIGN docs already claimed `hybrid -> fts5 -> grep`, but `create_deps()` still instantiated `KnowledgeIndex` directly, so TASK-3 remained incomplete.
- TASK-3 needed a deterministic production-safe regression seam before dev flow could close the loop.

Decision:
- Proceed to dev flow with scope narrowed to formalize the backend resolver, surface startup status, backfill regression coverage, and sync delivery artifacts.

## Cycle C1 — Dev Flow Result

Status: delivered

Implemented:
- Added `resolve_knowledge_backend()` in [co_cli/bootstrap/_bootstrap.py](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/_bootstrap.py) to resolve `hybrid -> fts5 -> grep`
- Surfaced degradation statuses through `deps.runtime.startup_statuses` and printed them during startup in [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py)
- Closed the SQLite connection before re-raising hybrid init failure in [co_cli/knowledge/_index_store.py](/Users/binle/workspace_genai/co-cli/co_cli/knowledge/_index_store.py)
- Added deterministic regression coverage in [tests/test_bootstrap.py](/Users/binle/workspace_genai/co-cli/tests/test_bootstrap.py)
- Synced [docs/DESIGN-system-bootstrap.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-system-bootstrap.md) and created [docs/DELIVERY-startup-background-hardening.md](/Users/binle/workspace_genai/co-cli/docs/DELIVERY-startup-background-hardening.md)

Verification:
- `uv run pytest tests/test_startup_failures.py tests/test_bootstrap.py`
- Result: pass
