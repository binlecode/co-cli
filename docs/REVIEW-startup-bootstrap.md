# REVIEW: startup-bootstrap — Co-System Health Check
_Date: 2026-03-08_

## What Was Reviewed

**DESIGN docs:**
- `docs/DESIGN-system-bootstrap.md` — canonical startup flow (primary target)
- `docs/DESIGN-doctor.md` — integration health checks component
- `docs/DESIGN-system.md` — system architecture, agent factory, `CoDeps`
- `docs/DESIGN-core-loop.md` — main loop boundary after bootstrap
- `docs/DESIGN-index.md` — §2 Config Reference, §4 Modules (startup rows)
- `docs/DESIGN-llm-models.md` — model dependency check section

**Source modules:**
- `co_cli/_bootstrap.py`, `co_cli/_model_check.py`, `co_cli/_doctor.py`, `co_cli/_status.py`
- `co_cli/main.py`, `co_cli/deps.py`, `co_cli/config.py`
- `co_cli/tools/capabilities.py`, `co_cli/_session.py`

**TODO docs:**
- `docs/TODO-coconfig-from-settings.md` — plan-approved; TASK-5 shipped; awaiting Gate 1

---

## Code Dev — Doc Accuracy Audit

| Doc | Section | Status | Finding |
|-----|---------|--------|---------|
| DESIGN-system-bootstrap.md | Step 2 — Session Restore pseudocode | clean | `if is_fresh(session_data, ...)` — prior P1 blocking fix confirmed present; no redundant null check |
| DESIGN-system-bootstrap.md | Full Startup Sequence — TaskRunner call | clean | Pseudocode includes all 5 args, including `auto_cleanup` and `retention_days` |
| DESIGN-system-bootstrap.md | CoDeps Group Semantics table | clean | All four groups match `deps.py` field structure exactly |
| DESIGN-system-bootstrap.md | Model Dependency Check — check sequence | clean | Matches `_model_check.py` logic; `PreflightResult` fields accurate |
| DESIGN-system-bootstrap.md | State Mutations table | clean | `deps.config.role_models`, `deps.config.session_id`, `deps.services.knowledge_index` paths all match source |
| DESIGN-system-bootstrap.md | Owning Code table | clean | `_model_check.py` present; no stale `_preflight.py` ref |
| DESIGN-doctor.md | §2 Agent-owned callers — `checks` key | clean | "The result's `checks` list is serialized to the tool return dict" — prior P2 fix confirmed present |
| DESIGN-doctor.md | `check_skills` count source | clean | `count=len(deps.session.skill_registry)` — matches `_doctor.py:186` exactly |
| DESIGN-doctor.md | §1 diagram — three independent callers | clean | `_bootstrap.py`, `capabilities.py`, `_status.py` — accurate |
| DESIGN-system.md | Startup ownership boundary | clean | Correctly delegates startup sequencing to `DESIGN-system-bootstrap.md` |
| DESIGN-index.md | §2 Config Reference — `library_path` | clean | `library_path` is present in the consolidated config table |
| DESIGN-index.md | §4 Modules — `_approval_risk.py` | clean | File deleted; no stale row in table — prior P3 is a non-issue |
| DESIGN-index.md | §4 Modules — startup rows | clean | `_bootstrap.py`, `_model_check.py`, `_session.py`, `main.py` rows accurate |

### Finding Details

No active doc-accuracy findings remain after the doc split and follow-up cleanup.

**Overall: 0 blocking, 0 minor**

---

## Auditor — TODO Health

| TODO doc | Task | Verdict | Key finding |
|----------|------|---------|-------------|
| TODO-coconfig-from-settings.md | TASK-1 (Named constants in config.py) | not shipped | `DEFAULT_*` constants for model names exist; `Field(default=...)` inline literals not yet extracted |
| TODO-coconfig-from-settings.md | TASK-2 (CoConfig field defaults use constants) | not shipped | `DEFAULT_EXEC_APPROVALS_PATH` etc. absent from `deps.py` |
| TODO-coconfig-from-settings.md | TASK-3 (from_settings classmethod) | not shipped | `from_settings` not present in `deps.py` |
| TODO-coconfig-from-settings.md | TASK-4 (main.py refactor) | not shipped | `create_deps()` still uses inline ~40-line `CoConfig()` block |
| TODO-coconfig-from-settings.md | TASK-5 (.co-cli/settings.json) | **shipped** | File present with all sentinel values (`memory_max_count: 150`, etc.) — prerequisite for TASK-6 met |
| TODO-coconfig-from-settings.md | TASK-6 (Pattern A test file cleanup) | stale done_when | 5 test files still use old pattern; `evals/_common.py` stale refs (`s.model_roles` / `get_role_head`) already manually fixed — done_when condition references them as outstanding |

### TASK-6 Stale done_when Detail

The TODO's TASK-6 `done_when` says: "evals/_common.py's stale `s.model_roles` / `get_role_head` references are fixed."

Current `evals/_common.py` (line 82): `"role_models": {k: list(v) for k, v in s.role_models.items()}` — uses correct `s.role_models` API. No `get_role_head` reference exists anywhere in the file. The stale refs described in the TODO context have been manually resolved via a separate refactor into `make_eval_deps()`. The done_when for TASK-6 lists a condition that is already satisfied and therefore cannot serve as a meaningful verification gate.

TASK-6 files list (`evals/_common.py`) is still appropriate for the remaining work (replacing the manual `make_eval_deps` inline mapping with `from_settings()`) but the done_when needs to be updated to reflect the current actual verification.

**Well-formedness of unshipped tasks:** TASK-1 through TASK-4 are well-formed — each has a non-empty `files:` list and a machine-verifiable `done_when`. TASK-6 has a stale done_when that would trivially pass even before the task runs.

**Prerequisite chain:** TASK-1 → TASK-2 → TASK-3 → TASK-4; TASK-5 standalone (shipped); TASK-6 prereq TASK-3 + TASK-5 (TASK-5 met; TASK-3 not yet). No circular dependencies.

**Overall verdict for `TODO-coconfig-from-settings.md`: `needs_cleanup`**

TASK-5 shipped and verified. TASKS-1 through 4 well-formed and not yet shipped. TASK-6 has a stale done_when condition that references a manually-resolved issue — needs update before implementation starts to restore its value as a verification gate.

---

## Verdict

**Overall: HEALTHY**

| Priority | Action | Source |
|----------|--------|--------|
| P1 | Update TASK-6 `done_when` in `TODO-coconfig-from-settings.md` to remove stale `evals/_common.py` stale-ref condition; replace with verifiable check on the actual Pattern A replacement in `evals/_common.py` | Auditor finding |

**Recommended next step:** Update `TODO-coconfig-from-settings.md` TASK-6 `done_when` inline. The startup/bootstrap docs are structurally clean after the split. `TODO-coconfig-from-settings.md` is ready to proceed after that TODO hygiene update.
