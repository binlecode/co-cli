# REVIEW: doctor — Co-System Health Check
_Date: 2026-03-07_

## What Was Reviewed

**DESIGN docs:**
- `docs/DESIGN-doctor.md`

**Source modules:**
- `co_cli/_doctor.py`
- `co_cli/_bootstrap.py`
- `co_cli/tools/capabilities.py`
- `co_cli/_status.py`
- `tests/test_bootstrap.py`

**TODO docs:** None matched scope.

**Delivery doc:** `docs/DELIVERY-doctor.md` (spot-check by Auditor)

## Auditor — TODO Health

| Source | Task/Claim | Verdict | Key finding |
|--------|-----------|---------|-------------|
| `DELIVERY-doctor.md` TASK-1 | `CheckItem`, `DoctorResult`, `run_doctor` exported from `co_cli/_doctor.py` | CONFIRMED | All three symbols exist and are importable. `run_doctor(deps=None)` signature is correct. `(settings.mcp_servers or {}).items()` guard (the bug fix mentioned in Independent Review) is present at line 178. `span.set_attribute("status", "ok")` on success path is confirmed in `_bootstrap.py` line 86. |
| `DELIVERY-doctor.md` TASK-2 | Bootstrap Step 4 integration health sweep: `run_doctor(deps)` called inside `tracer.start_as_current_span("integration_health")` with OTel span attrs and try/except | CONFIRMED | `_bootstrap.py` lines 80–92 show exactly this: span named `"integration_health"`, `run_doctor(deps)` called, `has_errors`/`has_warnings` set as span attributes, try/except present. All three DELIVERY claims (OTel span, try/except, `span.set_attribute("status","ok")` on success) verified. |
| `DELIVERY-doctor.md` TASK-3 | `capabilities.py` delegates to `run_doctor(ctx.deps)` and adds `checks` field to return dict | CONFIRMED | `capabilities.py` calls `run_doctor(ctx.deps)` at line 28, builds `checks` list at lines 74–77, and returns it in the dict at line 88. Also confirmed: `google`, `obsidian`, `brave` are now derived via `result.by_name()` rather than direct file-existence checks — consistent with DESIGN claim that "google/obsidian/brave now file-existence [checks routed through doctor]". |
| `DELIVERY-doctor.md` Files Changed | `co_cli/status.py` delegates to `run_doctor()`, removes `shutil` import, maps DoctorResult → StatusInfo | CONFIRMED | `_status.py` imports `run_doctor` at line 12, calls `run_doctor()` at line 97 (no deps), no `shutil` import present, and maps `google_item`, `obsidian_item`, `brave_item` from doctor checks. The delivery note says `co_cli/status.py` but the actual module is `co_cli/_status.py` — minor naming discrepancy in the delivery doc, not a functional issue. |
| `TODO-fix-hi-he-orch.md` | Any tasks assuming doctor doesn't exist / dependency on doctor feature | NOT STALE | All four gaps are orchestration-cycle improvements (sandboxed execution, re-planning, impact analysis, eval harness). None reference the doctor subsystem or capability checks. Zero staleness due to doctor delivery. |
| `TODO-chunking-rrf.md` | Any tasks assuming doctor doesn't exist / dependency on doctor feature | NOT STALE | Entirely focused on FTS5 chunking and RRF hybrid merge for the knowledge search pipeline. No reference to doctor, `_doctor.py`, or capability checks. Zero staleness due to doctor delivery. |

**Overall: delivery is fully accurate and complete.** All four spot-checked claims (TASK-1 module structure, TASK-2 bootstrap Step 4 integration, TASK-3 capabilities delegation, status.py delegation) are confirmed against live source. One minor delivery doc inaccuracy: the Files Changed section lists `co_cli/status.py` but the module was renamed to `co_cli/_status.py` (underscore prefix convention); the delivery note reflects the pre-rename name. No impact on correctness. Both active TODO docs (`TODO-fix-hi-he-orch.md`, `TODO-chunking-rrf.md`) are fully independent of the doctor delivery — zero stale tasks.

## Code Dev — Doc Accuracy Audit

| Doc | Section | Status | Finding |
|-----|---------|--------|---------|
| DESIGN-doctor.md | §1 Architecture diagram | blocking | `capabilities.py` is shown as a child callsite of `_bootstrap.py Step 4` (connected via a downward arrow). In source both are independent callers of `run_doctor` — `capabilities.py` is called by the agent mid-turn, completely separately from bootstrap. The diagram implies bootstrap invokes capabilities, which it does not. |
| DESIGN-doctor.md | §2 `check_mcp_server` detail | minor | Doc says error detail is `"{command} not found"`. Source uses `cmd_label = command or "(no command)"` and produces `f"{cmd_label} not found"` — when `command` is `None`, the actual detail is `"(no command) not found"`, not the bare command string. |
| DESIGN-doctor.md | §2 `capabilities.py` return fields | minor | Doc says capabilities maps result "alongside existing capability fields (provider, model, tools count, etc.)". Actual return dict contains `knowledge_backend`, `reranker`, `mcp_count`, `reasoning_models`, `reasoning_ready`, `skill_grants` — no `provider` key and no `tools count` key. The description is misleading about the field names present. |
| DESIGN-doctor.md | §4 `tests/test_bootstrap.py` coverage | minor | Doc claims test covers "Bootstrap Step 4 integration: doctor sweep fires during run_bootstrap, status lines emitted." No test in the file asserts on Step 4 doctor output specifically — the four tests cover knowledge sync, session restore, index disable on sync failure, and stale session creation. The Step 4 doctor sweep is exercised as a side effect but is not the subject of any assertion. Coverage claim overstates what is tested. |

### Finding Details

**F1 (blocking) — Diagram implies capabilities.py is called from bootstrap**

`DESIGN-doctor.md` §1 diagram:

```
   _bootstrap.py Step 4
   run_doctor(deps)
   emit summary_lines
   via frontend.on_status
              │
              ▼
   capabilities.py
   run_doctor(ctx.deps)
   map to dict + checks field
```

This layout makes `capabilities.py` look like a downstream step triggered by bootstrap. In reality `capabilities.py` (`check_capabilities` tool, registered in `agent.py` line 275) is called independently during agent turns — it has no connection to bootstrap. The diagram has three peers: `_bootstrap.py`, `status.py`, and `capabilities.py`. All three call `run_doctor` directly and independently. A developer reading this diagram would think bootstrap calls into capabilities, which is wrong and would mislead any debugging or refactoring effort.

**F2 (minor) — `check_mcp_server` error detail when `command` is None**

Doc: `"error"` detail is `"{command} not found"`.

Source (`_doctor.py` line 131–136):
```python
cmd_label = command or "(no command)"
return CheckItem(
    name=f"mcp:{name}",
    status="error",
    detail=f"{cmd_label} not found",
    ...
)
```

When a server config has no command and no url (validation normally prevents this but `run_doctor` handles it gracefully), the detail is `"(no command) not found"`. This is a minor description gap — the doc only covers the case where `command` is a non-empty string.

**F3 (minor) — capabilities.py return dict field names inaccurate in description**

Doc §2 Callers says: "serialized to the tool return dict alongside existing capability fields (provider, model, tools count, etc.)".

Actual `check_capabilities` return keys (`capabilities.py` lines 79–90): `display`, `knowledge_backend`, `reranker`, `google`, `obsidian`, `brave`, `mcp_count`, `reasoning_models`, `reasoning_ready`, `checks`, `skill_grants`.

There is no `provider` key, no `model` key, and no `tools count` key. The description "provider, model, tools count" does not match the actual fields returned. A developer implementing or extending this tool would look for the wrong field names.

**F4 (minor) — test_bootstrap.py coverage claim overstated**

Doc §4: `tests/test_bootstrap.py` purpose listed as "Bootstrap Step 4 integration: doctor sweep fires during run_bootstrap, status lines emitted."

The file contains four tests: `test_bootstrap_syncs_knowledge_and_restores_fresh_session`, `test_bootstrap_two_pass_sync_partitions_by_kind`, `test_bootstrap_disables_index_when_sync_fails`, `test_bootstrap_stale_session_creates_new_session`. None assert on Step 4 doctor output — no assertion on `frontend.on_status` call content related to doctor checks, no assertion that `run_doctor` was invoked, no assertion on health check lines. The doctor sweep runs as a side effect of `run_bootstrap` but is not verified by any test assertion.

**Overall: 1 blocking, 3 minor**

## Verdict

**Overall: ACTION_REQUIRED**

| Priority | Action | Source |
|----------|--------|--------|
| P1 | Fix §1 architecture diagram — `capabilities.py` must appear as a peer callsite of `_bootstrap.py` and `status.py`, not as a child step below bootstrap | F1 (blocking) |
| P2 | Fix §2 `check_mcp_server` description — error detail is `"{cmd_label} not found"` where `cmd_label = command or "(no command)"` | F2 (minor) |
| P3 | Fix §2 Callers `capabilities.py` field description — remove `(provider, model, tools count, etc.)` and list actual keys or drop the parenthetical | F3 (minor) |
| P4 | Fix §4 `tests/test_bootstrap.py` purpose — remove the overstated "doctor sweep fires / status lines emitted" claim; the four tests cover knowledge sync and session restore | F4 (minor) |

**Recommended next step:** Run `/sync-doc docs/DESIGN-doctor.md` to fix the four inaccuracies in-place (diagram peer layout + three prose corrections).
