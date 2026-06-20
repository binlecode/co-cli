---
name: ship
description: Ship a completed exec-plan — run tests, verify staged files, bump version, archive plan, commit.
---

# Ship

Two use cases:
- **Post-Gate-2 ship** (orchestrate-dev flow): invoke `/ship <slug>` after Gate 2 PASS instead of re-running `/orchestrate-dev`. Tests are already green from `/review-impl` — Step 2 is a safety net.
- **Ad-hoc ship**: work completed outside the plan flow. Step 2 (test suite) guards against shipping a broken state.

**Invocation:** `/ship <slug>` or `/ship` (ad-hoc, no plan)

---

## Step 1 — Format gate

Run before staging anything:
```bash
scripts/quality-gate.sh lint --fix
```

Fix any remaining violations before continuing.

---

## Step 2 — Full test suite

**Skip if `/review-impl` just ran the full suite green and there has been no source-code change since** (post-Gate-2 ship flow — the review-impl run already is this safety net; re-running buys nothing). State that you're skipping and why. Otherwise (ad-hoc ship, or any source change after review-impl), run it:

```bash
mkdir -p .pytest-logs
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-ship.log
```

Abort on any failure — diagnose, fix, re-run.

---

## Step 3 — Verify staged files

Show `git status`. Confirm only files related to this delivery are staged. Ask the user before staging any file that seems tangential.

---

## Step 4 — Version bump + CHANGELOG

Read current version from `pyproject.toml`. Bump the **patch digit only** to the nearest number with the correct parity:
- Feature / refactor → next **even** patch number
- Bugfix → next **odd** patch number

The increment (+1 or +2) depends on the current parity. Examples:
- `0.8.113` (odd) + feature → `0.8.114` (+1, next even)
- `0.8.113` (odd) + bugfix  → `0.8.115` (+2, next odd)
- `0.8.114` (even) + feature → `0.8.116` (+2, next even)
- `0.8.114` (even) + bugfix  → `0.8.115` (+1, next odd)

Update `CHANGELOG.md` — add a new `## [x.y.z]` section with a short description of what changed.

Stage both `pyproject.toml` and `CHANGELOG.md`.

---

## Step 5 — Archive plan (if a slug was given)

1. Verify all delivered tasks are already marked `✓ DONE` (orchestrate-dev marks them during delivery). Mark any unmarked ones only in an ad-hoc ship where orchestrate-dev was not run.
2. Archive:
```bash
git mv docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md docs/exec-plans/completed/
```
3. Grep the repo for the slug to confirm no stale references remain in source code (DESIGN docs referencing this plan by name are expected — only source code references are stale).

Never delete the plan file.

---

## Step 6 — Commit

Stage only delivery files + `pyproject.toml` + the plan archive move (if applicable).

Commit message format:
- `feat:` / `fix:` / `refactor:` prefix
- One-line subject
- 3–6 body bullets for significant changes
- Ends with `Co-Authored-By: Claude <noreply@anthropic.com>`

---

## Step 7 — Report

State the version shipped and confirm test suite passed.
