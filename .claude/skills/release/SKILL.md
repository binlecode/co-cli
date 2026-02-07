---
name: release
description: Version bump, changelog, design doc sync, TODO cleanup, and commit
disable-model-invocation: true
user-invocable: true
---

# Release Workflow

Release type: **$ARGUMENTS**

Argument is either a version number (e.g. `0.2.14`) or a type (`feature` / `bugfix`).

If a type is given, compute the version from current `pyproject.toml`:
- `feature`: increment patch to next **even** number
- `bugfix`: increment patch to next **odd** number

If a type is given, confirm the computed version with the user. If an explicit version is given, proceed directly.

## Step 1: Run tests

```bash
uv run pytest tests/ -v
```

Stop if any test fails. Do not proceed with failing tests.

## Step 2: Bump version

Edit `pyproject.toml` — update the `version` field. This is the **only** place the version lives (read at runtime via `tomllib`).

## Step 3: Update CHANGELOG.md

Add an entry at the top, **above** the previous release. Use format: `## [<version>] - <YYYY-MM-DD>`.

Categorize under `### Added`, `### Fixed`, `### Changed`, `### Removed` as appropriate.

To determine what changed since the last release:
```bash
git log --oneline <last-release-commit>..HEAD
git diff <last-release-commit>..HEAD --stat
```

Find the last release commit by searching for the previous version's bump commit. Write concise entries — reference module names and file paths. Do not pad with generic descriptions. Match the style of existing entries.

## Step 4: Sync design docs

Check `docs/DESIGN-*.md` files **that are affected by changes in this release**:
- Mermaid diagrams reflect actual classes, fields, and relationships
- Text descriptions match current behavior
- Module summary table in `DESIGN-co-cli.md` lists all current modules

Only update what's stale. Do not audit unrelated design docs.

## Step 5: Clean up TODO docs

- If a `docs/TODO-*.md` is fully implemented, fold valuable design rationale into the relevant `DESIGN-*.md`, then delete the TODO file and remove its entry from `CLAUDE.md`
- Do not delete TODOs that still have open work items
- If no TODOs are complete, skip this step

## Step 6: Update CLAUDE.md

Check that `CLAUDE.md` references affected by this release are current:
- Doc file lists (DESIGN and TODO sections)
- Any coding standards or config descriptions that changed

Only touch lines that are actually stale. If nothing changed, skip this step.

## Step 7: Commit

Stage only files related to this release. Do not stage unrelated uncommitted changes.

Use a HEREDOC for the commit message, following the repo's imperative style:

```bash
git add <specific files>
git commit -m "$(cat <<'EOF'
<Imperative summary of what this release adds/fixes>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

Show `git log --oneline -1` and `git status` to confirm.

Do **not** push or tag unless the user explicitly asks.
