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

```bash
mkdir -p .pytest-logs
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-ship.log
```

Abort on any failure — diagnose, fix, re-run.

---

## Step 3 — Verify staged files

Show `git status`. Confirm only files related to this delivery are staged. Ask the user before staging any file that seems tangential.

---

## Step 4 — Version bump

Read current version from `pyproject.toml`. Bump the **patch digit only**:
- `+2` for a feature or enhancement (result is even)
- `+1` for a bugfix (result is odd)

Stage `pyproject.toml`.

---

## Step 5 — Mark and archive plan (if a slug was given)

1. Mark all completed tasks `✓ DONE` in `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`.
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
