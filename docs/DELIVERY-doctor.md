# Delivery: Doctor — System-Wide Health-Check Subsystem
Date: 2026-03-07

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | `python -c "from co_cli._doctor import run_doctor, DoctorResult, CheckItem; print('ok')"` | ✓ pass | |
| TASK-2 | `uv run pytest tests/test_bootstrap.py -v` passes | ✓ pass | 4 passed |
| TASK-3 | `python -c "from co_cli.tools.capabilities import check_capabilities; print('ok')"` | ✓ pass | |
| TASK-4 | `python -c "from co_cli.status import get_status; print('ok')"` + StatusInfo shape unchanged | ✓ pass | |
| TASK-5 | `docs/DESIGN-doctor.md` exists with 4-section template | ✓ pass | 179 lines |
| TASK-6 | `grep -n "DESIGN-doctor" docs/DESIGN-index.md` returns 2 rows | ✓ pass | quick-ref + component table |
| TASK-7 | `docs/DESIGN-flow-bootstrap.md` Step 4 in diagram + prose | ✓ pass | |

## Files Changed

- `co_cli/_doctor.py` — new module: CheckItem, DoctorResult, check_* functions, run_doctor(deps=None)
- `co_cli/_bootstrap.py` — Step 4 integration health sweep added (run_doctor + OTel span + try/except)
- `co_cli/tools/capabilities.py` — delegates to run_doctor(ctx.deps); adds `checks` field to return dict
- `co_cli/status.py` — delegates integration checks to run_doctor(); removes shutil import; maps DoctorResult → StatusInfo
- `docs/DESIGN-doctor.md` — new component doc (4-section template)
- `docs/DESIGN-flow-bootstrap.md` — Step 4 added to flowchart, prose, state mutations, failure paths, owning code
- `docs/DESIGN-index.md` — Doctor row added to Layer 4 quick-ref and component table
- `docs/DESIGN-core.md` — Bootstrap Phase updated: "three steps" → "four steps"
- `docs/DESIGN-tools-execution.md` — check_capabilities return dict updated: google/obsidian/brave now file-existence, checks field added

## Tests

- Scope: touched files (pre-existing LLM-dependent tests have unrelated timeout failures from WIP test-audit changes)
- Result: pass — 114 passed across test_bootstrap.py, test_preflight.py, test_status.py, test_shell.py, test_memory.py, test_skills_loader.py, test_knowledge_index.py
- Pre-existing failure: `test_cmd_compact`, `test_approval_*`, `test_cmd_new_checkpoints_and_clears` — all timeout on LLM calls; `asyncio.timeout(10)` added by pre-existing WIP test-audit changes; unrelated to this delivery (confirmed by git stash check)

## Independent Review

- Result: 1 minor functional bug fixed, 3 minor notes (string coupling — inherent to status.py mapping design, not fixed)
- Fixed: `(settings.mcp_servers or {}).items()` guard; `span.set_attribute("status", "ok")` on success path

## Doc Sync

- Result: fixed — 8 inaccuracies corrected across DESIGN-doctor.md (7), DESIGN-core.md (1), DESIGN-tools-execution.md (1); DESIGN-doctor.md Files section phantom test entry corrected to test_bootstrap.py

## Coverage Audit

- Result: CLEAN — 0 blocking, 0 minor. No new agent tools, config settings, or CLI commands. All internal symbols documented in DESIGN-doctor.md.

## Overall: DELIVERED

Single entry point `run_doctor(deps=None)` consolidates all non-LLM integration health checks. Bootstrap Step 4 surfaces integration health at startup. `check_capabilities` and `status.py` both delegate to it — no forked logic.
