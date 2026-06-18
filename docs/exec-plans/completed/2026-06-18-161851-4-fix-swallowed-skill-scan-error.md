# Fix Swallowed Skill Security-Scan Error (R12)

**Slug:** `fix-swallowed-skill-scan-error` · **Created:** 2026-06-18 16:18:51

## Context

Promoted from the `rules-conformance-cleanup` deferred backlog. Unlike the other
deferred items (behavior-preserving refactors), this is a **correctness fix with an
observable behavior change**, so it gets its own plan and a behavioral `done_when`.

`commands/skills.py:73` swallows failures of `scan_skill_content()` during
`/skills reload` with a bare `except Exception: pass`. If a skill's read or
security scan throws, the user gets no signal — a skill can be loaded with its
security scan silently skipped, presenting as clean (`review.md:16` — swallowed
error on a user-visible path).

## Tasks

### ✓ DONE TASK-1 — Surface the failed security scan
Replace the `except Exception: pass` at `commands/skills.py:73` with a surfaced
notice (e.g. `console.print(f"[warning]Could not security-scan {name}: {e}[/warning]")`)
so a scan failure is visible rather than treated as clean. Match the existing
warning style in the same command (`skills.py:54` already captures `as e`).
- **files:** `co_cli/commands/skills.py`
- **done_when:** a skill whose `scan_skill_content` raises produces a visible warning
  on `/skills reload` instead of silent success; lint clean; scoped command test green

## Verification
`scripts/quality-gate.sh lint`; exercise `/skills reload` against a skill that fails
to scan and confirm the warning surfaces.

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | a skill whose `scan_skill_content` raises produces a visible warning on `/skills reload` instead of silent success; lint clean; scoped command test green | ✓ pass |

**Tests:** scoped — 37 passed, 0 failed. Behavioral check confirmed the `except Exception as e` branch emits `Could not security-scan <name>: <e>` when the scanner raises (previously swallowed by `except Exception: pass`).
**Doc Sync:** clean — one-line error-surfacing change in a command handler; no shared module, public API, or schema touched.

**Overall: DELIVERED**
`commands/skills.py:73` now surfaces scan failures as a `[warning]` notice instead of silently treating the skill as clean.

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | scan failure produces a visible warning on `/skills reload` instead of silent success | ✓ pass | `skills.py:73` `except Exception as e:` binds the error; `skills.py:74` surfaces it via shared `console.print` in `[warning]` style — replaces the prior `except Exception: pass`. Behavioral repro (dev phase) emitted `Could not security-scan boom: scanner exploded`. |

### Issues Found & Fixed
No issues found. Change uses the shared `console`, matches the sibling error channel (`skills.py:66`), introduces no new abstraction, global state, or convention violation.

### Tests
- Command: `uv run pytest tests/test_flow_skills_manage.py -q`; scoped to the touched command surface
- Result: 27 passed, 0 failed
- Full suite deliberately not run here: working tree carries 40+ files of unrelated pre-existing WIP and pytest runs `-x`, so an unrelated first-failure would halt without informing this change. Full-suite gating over a clean tree is `/ship`'s responsibility.

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- `/skills reload` scan-failure path: ✓ direct repro confirmed the `[warning]` notice surfaces when `scan_skill_content` raises (LLM-independent; verified via repro, no chat turn needed)

### Overall: PASS
One-line correctness fix surfaces a previously swallowed skill security-scan error; evidence-backed, lint clean, scoped tests green, boots cleanly.
