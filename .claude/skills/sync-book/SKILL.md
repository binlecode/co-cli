---
name: sync-book
description: Sync the GitHub Pages design book — update index, nav ordering, cross-references, and _config.yml excludes
disable-model-invocation: true
user-invocable: true
---

# Sync Design Book

Synchronise the GitHub Pages book in `docs/` with the current set of DESIGN docs.

## Step 1: Inventory DESIGN docs

List all `docs/DESIGN-*.md` files. Identify:
- **Book docs**: `DESIGN-co-cli.md` (skeleton) and all numbered `DESIGN-{NN}-*.md` (component docs)
- **Excluded docs**: Any DESIGN doc not numbered (e.g. `DESIGN-co-evolution.md`) — these stay out of the book

Check for new DESIGN docs that exist but are not yet numbered. If found, assign the next number following the layer convention:
- **01–03**: Core (agent, chat loop, models)
- **04–07**: Infrastructure (otel, tail, memory, theming)
- **08–11+**: Tools

Rename and add Jekyll front matter if needed:

```yaml
---
title: "NN — Component Name"
parent: Core        # or Infrastructure, or Tools
nav_order: <order within parent group>
---
```

Parent pages (`core.md`, `infrastructure.md`, `tools.md`) have `has_children: true`. Component docs are children of their layer's parent page. `DESIGN-co-cli.md` is nav_order 1 (top-level, no parent).

## Step 2: Sync version

Read the current version from `pyproject.toml`. Update `**Synced:** v{version}` in every `docs/DESIGN-*.md` to match.

## Step 3: Update index.md

Regenerate `docs/index.md` to list all numbered DESIGN docs, grouped by layer:
- Core (01–03)
- Infrastructure (04–07)
- Tools (08+)

Each entry: `[NN — Title](DESIGN-NN-name.md)` with a brief summary from the doc's "What & How" section.

## Step 4: Update DESIGN-co-cli.md component table

Update the "Component Docs" table in `docs/DESIGN-co-cli.md` to match the current set of numbered docs, in order.

## Step 5: Verify cross-references

Search all `docs/DESIGN-*.md` files for links to other DESIGN docs. Verify every link target matches an actual filename. Fix any stale references from renames or additions.

## Step 6: Update _config.yml excludes

Ensure `docs/_config.yml` excludes all non-book doc prefixes present in `docs/`:
- `TODO-*.md`, `todo-*.md`
- `BACKLOG-*.md`
- `RESEARCH-*.md`
- Any non-numbered DESIGN docs that should stay out of the book

## Step 7: Update CLAUDE.md

Update the `## Docs` → `### Design` section in `CLAUDE.md` to list all current DESIGN docs with their numbered filenames and descriptions, in order.

## Step 8: Commit and push

Stage only book-related files. Commit with:

```bash
git add docs/index.md docs/_config.yml docs/DESIGN-*.md docs/core.md docs/infrastructure.md docs/tools.md docs/quickstart.md CLAUDE.md
git commit -m "$(cat <<'EOF'
Sync design book: <brief summary of what changed>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
git push
```

Show `git log --oneline -1` and the GitHub Pages URL to confirm.
