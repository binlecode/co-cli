# REVIEW: delivery/doctor — Delivery Audit
_Date: 2026-03-07_

## What Was Scanned

**Source modules (doctor scope):**
- `co_cli/_doctor.py` (new — internal helper, no agent tool registrations)
- `co_cli/_bootstrap.py` (Step 4 added — internal)
- `co_cli/tools/capabilities.py` (modified — `check_capabilities` existing tool, enriched return)
- `co_cli/status.py` (modified — `get_status()` delegates to `run_doctor()`)

**DESIGN docs checked:**
- `docs/DESIGN-doctor.md` (new)
- `docs/DESIGN-tools-execution.md` (capabilities section)
- `docs/DESIGN-flow-bootstrap.md` (Step 4 section)
- `docs/DESIGN-tools.md` (approval table)
- `docs/DESIGN-core.md` (Bootstrap Phase)

---

## Delivery Audit

| Feature | Class | Source | Coverage | Severity | Gap |
|---------|-------|--------|----------|----------|-----|
| `check_capabilities` | agent tool | `co_cli/tools/capabilities.py` | full | — | Approval: "Most other native tools | No" (catch-all). Behavior: full section in DESIGN-tools-execution.md with updated `checks` field. |
| `run_doctor` | internal helper | `co_cli/_doctor.py` | full | — | Documented in DESIGN-doctor.md §Core Logic: entry point, both call paths, pseudocode |
| `CheckItem` / `DoctorResult` | internal schema | `co_cli/_doctor.py` | full | — | Documented in DESIGN-doctor.md §Core Logic: schema, properties, `summary_lines` |
| `check_*` functions (6) | internal helpers | `co_cli/_doctor.py` | full | — | All six functions documented in DESIGN-doctor.md with decision logic and return values |
| Bootstrap Step 4 | runtime behavior | `co_cli/_bootstrap.py` | full | — | Documented in DESIGN-flow-bootstrap.md §Step 4; OTel span, try/except, non-blocking contract |

**No new config settings introduced.**
**No new CLI commands introduced.**

**Summary: 0 blocking, 0 minor**

---

## Second Pass

1. **`check_capabilities` full coverage confirmed** — DESIGN-tools-execution.md §Capabilities has: what it does, return dict with all fields including new `checks`, approval classification (via catch-all in DESIGN-tools.md). Full.
2. **No new config settings** — nothing to check for env var documentation.
3. **`check_capabilities` approval table** — listed via catch-all "Most other native tools | No". Tool is read-only with no side effects. Catch-all is accurate and sufficient; no dedicated row required.

No downgrades from second pass.

---

## Verdict

**CLEAN**

All features from the doctor delivery have full documentation coverage:
- `co_cli/_doctor.py` → `DESIGN-doctor.md`
- Bootstrap Step 4 → `DESIGN-flow-bootstrap.md`
- `check_capabilities` enrichment → `DESIGN-tools-execution.md`
- Cross-references → `DESIGN-core.md`, `DESIGN-index.md`

No new agent tools, config settings, or CLI commands were added by this delivery.
