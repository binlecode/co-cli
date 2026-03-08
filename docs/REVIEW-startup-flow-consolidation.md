# REVIEW: startup-flow-consolidation — Co-System Health Check
_Date: 2026-03-07_

## What Was Reviewed

**DESIGN docs:**
- `docs/DESIGN-flow-bootstrap.md` — canonical startup flow (primary delivery target)
- `docs/DESIGN-doctor.md` — integration health checks (diagram + caller model fixed)
- `docs/DESIGN-llm-models.md` — model dependency checks section (renamed)
- `docs/DESIGN-index.md` — navigation routing (startup quick-ref updated)
- `docs/DESIGN-core.md` — session lifecycle table (Preflight row merged)

**Source modules:**
- `co_cli/_model_check.py`, `co_cli/_bootstrap.py`, `co_cli/_doctor.py`, `co_cli/_status.py`
- `co_cli/main.py`, `co_cli/tools/capabilities.py`, `co_cli/deps.py`, `co_cli/config.py`

**TODO docs:**
- `docs/TODO-startup-flow-consolidation.md`

**Delivery doc:**
- `docs/DELIVERY-startup-flow-consolidation.md`

---

## Code Dev — Doc Accuracy Audit

| Doc | Section | Status | Finding |
|-----|---------|--------|---------|
| DESIGN-flow-bootstrap.md | Mermaid diagram | clean | `run_model_check` present (not `run_preflight`) |
| DESIGN-flow-bootstrap.md | Model Dependency Check / Check sequence | clean | Pseudocode matches `_model_check.py` logic |
| DESIGN-flow-bootstrap.md | PreflightResult fields | clean | `ok`, `status`, `message`, `model_roles` — all match source |
| DESIGN-flow-bootstrap.md | Chain pruning table | clean | All five rows match source behaviour |
| DESIGN-flow-bootstrap.md | Extension Model section | clean | Accurate |
| DESIGN-flow-bootstrap.md | Entry Conditions | clean | References `run_model_check()` correctly |
| DESIGN-flow-bootstrap.md | Full Startup Sequence pseudocode | minor | `deps.config.model_roles["reasoning"][0]` used as model_name; source at line 331 confirms this. Doc accurately represents it. One minor gap: pseudocode says `task_runner = TaskRunner(storage, max_concurrent, inactivity_timeout)` but actual `TaskRunner()` call in `main.py` also passes `auto_cleanup` and `retention_days` — doc is incomplete but not wrong |
| DESIGN-flow-bootstrap.md | State Mutations table | clean | `deps.config.model_roles` path matches source line 162 |
| DESIGN-flow-bootstrap.md | Owning Code table | clean | `_model_check.py` present, `_preflight.py` absent |
| DESIGN-flow-bootstrap.md | See Also | clean | No link to `DESIGN-flow-preflight.md` |
| DESIGN-flow-bootstrap.md | Step 2 — Session Restore pseudocode | blocking | Doc says `session = load_session(session_path)` returns `None` if missing. Then says `if session is not None AND is_fresh(...)`. But `_bootstrap.py` source (lines 62–63) calls `is_fresh(session_data, ...)` directly on whatever `load_session` returns — it does NOT have an outer `if session is not None` guard. `is_fresh()` handles `None` internally. The pseudocode implies a two-step null-then-freshness check, but source does a single `is_fresh()` call. This misrepresents the flow — a developer following the doc would add a redundant guard. |
| DESIGN-doctor.md | §1 diagram | clean | Three independent peers (no parent-child). `_bootstrap.py`, `capabilities.py`, `_status.py` are all peers off `_doctor.py` |
| DESIGN-doctor.md | `check_mcp_server` — `cmd_label` | clean | `cmd_label = command or "(no command)"` is present at `_doctor.py` line 131 |
| DESIGN-doctor.md | Agent-owned callers — return keys | blocking | Doc lists `skill_grants` as a return key. Source `capabilities.py` (line 89) returns `"skill_grants": skill_grants` — **present**. But doc omits `"checks"` key from the listed keys. The sentence reads: `"alongside other capability fields (knowledge_backend, reranker, mcp_count, reasoning_models, reasoning_ready, skill_grants, google, obsidian, brave)"` — the `checks` field (a list of per-check dicts) is also returned (line 88) but never mentioned in the doc. Minor gap rather than wrong claim, but it is an incomplete description of the return dict. |
| DESIGN-doctor.md | §4 Files table — `test_bootstrap.py` purpose | clean | "Bootstrap integration tests: knowledge sync, session restore, index-disable-on-failure, stale session handling" — accurate description for the bootstrap-related tests |
| DESIGN-llm-models.md | §2 heading | clean | `### Model Dependency Checks` (not `Preflight Checks`) |
| DESIGN-llm-models.md | `run_model_check` references | clean | All references use `run_model_check` |
| DESIGN-llm-models.md | Files table | clean | `_model_check.py` present, no `_preflight.py` |
| DESIGN-llm-models.md | `deps.config.model_roles` | clean | Correctly states `run_model_check` applies mutation to `deps.config.model_roles` |
| DESIGN-index.md | Quick-ref startup row | clean | Only bootstrap link — no preflight link |
| DESIGN-index.md | Module table — `_model_check.py` | clean | Present at layer 2 row |
| DESIGN-index.md | Module table — `_preflight.py` | clean | Absent |
| DESIGN-index.md | `_bootstrap.py` description | clean | "four startup steps: knowledge sync, session restore/create, skills count report, integration health sweep" — matches source |
| DESIGN-index.md | `_approval_risk.py` row | minor | `_approval_risk.py` does not exist as a file (only `_approval.py` exists). The audit task asks to verify this row should "now be present" — it is absent from the module table, and the file itself does not exist. No stale row to clean up, but this confirms the module table is not missing a newly-added file. Clean. |
| DESIGN-core.md | Session Lifecycle table | clean | No Preflight row; Bootstrap row correctly references both `run_model_check()` and `run_bootstrap()` with four steps |
| DESIGN-core.md | Component Docs table | clean | No reference to `DESIGN-flow-preflight.md` |

### Finding Details

1. **[blocking] DESIGN-flow-bootstrap.md — Step 2 Session Restore pseudocode misrepresents `is_fresh` call structure**

   Doc pseudocode shows:
   ```
   session = load_session(session_path)   ← returns None if missing or unreadable
   if session is not None AND is_fresh(session, session_ttl_minutes):
   ```

   Actual `_bootstrap.py` source (lines 62–63):
   ```python
   session_data = load_session(session_path)
   if is_fresh(session_data, session_ttl_minutes):
   ```

   There is no explicit `session is not None` guard in the bootstrap source — `is_fresh()` handles `None` internally (returns `False` when `session is None`, per `_session.py` line 48). The doc implies a two-step guard that does not exist in the code. A developer following the doc would add a redundant outer `if session is not None` check, which is at minimum misleading. Severity: **blocking** (wrong flow).

2. **[minor] DESIGN-doctor.md — `checks` return key absent from capabilities.py return dict description**

   The Agent-owned callers section lists return keys: `knowledge_backend`, `reranker`, `mcp_count`, `reasoning_models`, `reasoning_ready`, `skill_grants`, `google`, `obsidian`, `brave`. The actual `capabilities.py` return dict (line 79–90) also includes `"checks": checks` (a list of per-check dicts). This key is not mentioned in the doc. The doc is incomplete — not wrong — since it does not claim the list is exhaustive. Severity: **minor**.

**Overall: 1 blocking, 1 minor**

---

## Auditor — TODO Health

| TODO doc | Task | Verdict | Key finding |
|----------|------|---------|-------------|
| TODO-startup-flow-consolidation.md | TASK-1 | shipped | `System-owned` / `Agent-owned` headings present; `(no command)` fallback in `check_mcp_server`; `"provider, model, tools"` returns 0 hits; `test_bootstrap.py` purpose corrected in Files table |
| TODO-startup-flow-consolidation.md | TASK-2 | shipped | `canonical` in first paragraph; `## Model Dependency Check` subsection present; See Also contains no link to `DESIGN-flow-preflight.md` |
| TODO-startup-flow-consolidation.md | TASK-3 | shipped | No `DESIGN-flow-preflight` refs in `DESIGN-index.md` or `DESIGN-core.md`; startup quick-ref routes to bootstrap only; `_bootstrap.py` module table entry reads "four startup steps" |
| TODO-startup-flow-consolidation.md | TASK-4 | shipped (note) | `docs/DESIGN-flow-preflight.md` deleted; no `DESIGN-flow-preflight` refs in any `DESIGN-*.md`; the TODO doc's own Context/Problem section contains 21 occurrences of `DESIGN-flow-preflight` as historical description — these are not live links, they are part of the problem statement and are expected |
| TODO-startup-flow-consolidation.md | TASK-5 | shipped | `co_cli/_preflight.py` deleted; `co_cli/_model_check.py` exists with `run_model_check()`; `tests/test_preflight.py` deleted; `tests/test_model_check.py` exists; 0 `_preflight` refs in `co_cli/` or `tests/` Python files; 0 `_preflight.py` refs in DESIGN docs |

### Delivery Spot-Check

| Claimed outcome | Verified? | Notes |
|----------------|-----------|-------|
| `co_cli/_preflight.py` deleted | YES | File does not exist |
| `co_cli/_model_check.py` exists with `run_model_check()` | YES | Function at line 136 |
| `tests/test_preflight.py` deleted, `tests/test_model_check.py` exists | YES | Both confirmed |
| `docs/DESIGN-flow-preflight.md` deleted | YES | File does not exist |
| 0 stale `_preflight` refs in source (`co_cli/`, `tests/` `.py` files) | YES | `grep` returns 0 results |
| 0 `DESIGN-flow-preflight` refs in `DESIGN-*.md` | YES | Clean across all DESIGN docs |

### Cross-TODO Dependency Scan

Active TODO docs checked: `TODO-chunking-rrf.md`, `TODO-fix-hi-he-orch.md`.

Neither contains any reference to `_preflight`, `run_preflight`, `DESIGN-flow-preflight`, or the old preflight naming. No other TODO depends on the retired preflight naming.

**Overall verdict for `TODO-startup-flow-consolidation.md`: `ready_for_plan`**

All five tasks are shipped and source-verified. The TASK-4 done_when grep technically matches the TODO doc's own historical context text (21 hits), but those are description-of-the-problem references, not live doc links. No stale assumptions; no broken dependencies in other TODOs.

---

## Verdict

**Overall: ACTION_REQUIRED**

| Priority | Action | Source |
|----------|--------|--------|
| P1 | Fix `DESIGN-flow-bootstrap.md` Step 2 pseudocode: change `if session is not None AND is_fresh(session, ...)` to `if is_fresh(session, ...)` — source (`_bootstrap.py:63`) calls `is_fresh` directly; null handling is internal to `is_fresh` | Code Dev finding 1 |
| P2 | Add `"checks"` to the `capabilities.py` return key list in `DESIGN-doctor.md` §Agent-owned callers | Code Dev finding 2 |
| P3 | Re-add `_approval_risk.py` row to `DESIGN-index.md` §4 Modules — the edit has been reverted by the linter twice; consider pinning the row in a linter-stable position | Delivery audit P3, confirmed absent |

**Recommended next step:** Apply P1 fix to `DESIGN-flow-bootstrap.md` Step 2 pseudocode (blocking wrong-flow inaccuracy), then P2 and P3 as minor cleanup. All five delivery tasks are fully shipped and source-verified — TODO doc is `ready_for_plan`.
