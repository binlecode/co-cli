# Delivery: <feature name>
Date: <ISO 8601 date>

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | <done_when text> | ✓ pass | |
| TASK-2 | <done_when text> | ✗ fail | <what broke> |
| TASK-3 | <done_when text> | — skipped | blocked by TASK-2 |

## Files Changed
- `<path>` — <one-line description of change>
- `<path>` — <one-line description of change>

## Tests
- Scope: full suite (DELIVERED) / touched files only (partial delivery)
- Result: pass / fail (<N> passed, <N> failed)

## Independent Review
- Result: clean / <N> blocking / <N> minor
- (findings table here if any)

## Doc Sync
- Scope: full / narrow — <rationale>
- Result: clean / fixed (<what was fixed>)

## Coverage Audit
- Result: clean / gaps found (<list missing features>)

## Breaking Changes
<!-- Omit this section if no public APIs, schemas, or CLI commands were changed incompatibly. -->
- `<what changed>` — <migration path or "no migration needed">

## Artifact Lifecycle
- TODO status: tasks marked ✓ DONE (not removed) / retained through Gate 3 — delete alongside DELIVERY after PO acceptance
- DELIVERY status: keep for Gate 2 and Gate 3 only

## Gate 3 Cleanup
- After PO acceptance, delete both `docs/TODO-<slug>.md` and `docs/DELIVERY-<slug>.md` in the same session.
- If PO acceptance is not part of this run, stop after writing this report and surface both deletes as the next step.

## Overall: DELIVERED / BLOCKED
<one sentence summary>
