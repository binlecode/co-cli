---
name: sync-doc
description: Fix doc-only inaccuracies in specs (docs/specs/) in-place — wrong schema, stale names, phantom features. Use when the fix is docs-only and doesn't warrant a full plan→dev cycle. Also invoked automatically by orchestrate-dev after each delivery.
---

# sync-doc

Direct execution skill — no planning ceremony. Read spec → read source → diff claims → fix in-place.

**When to use sync-doc vs orchestrate-dev:**
- Use **sync-doc** when the fix is doc-only: wrong schema description, stale function name, phantom feature in a spec. No code changes, no Gate 1 approval needed — it's a 5-minute correction, not a delivery.
- Use **orchestrate-dev** when fixing the inaccuracy requires code changes, schema migrations, or new tests. Doc-only fixes don't warrant the full plan → dev → gates ceremony.

## Invocation

```
/sync-doc                          # all specs
/sync-doc context.md               # specific spec (filename or path)
/sync-doc context core-loop        # multiple specs (partial names matched)
```

If no argument is given, run against all `docs/specs/*.md` files.

---

## Execution Steps

### Step 1 — Resolve scope

- No args: glob `docs/specs/*.md`, process all.
- Args: match each arg against filenames in `docs/specs/` (exact or prefix match). Abort with a clear error if a given name matches nothing.

### Step 2 — For each doc

#### 2a. Read the doc

Read the full doc. Note:
- The **Files** section (section 4) — lists exactly which source files to check. This is the primary read list.
- The **Config** section (section 3) — lists settings and env vars to verify against the project's config source files (identified from CLAUDE.md).
- The **Tool Surface** table (if present) — lists tool names and registration status to verify against the project's agent entry point (identified from CLAUDE.md).

#### 2b. Read source files

Read every file listed in the doc's Files section. Also read the project's config and dependency source files whenever the doc has a Config section — these are the ground truth for settings and env vars. Identify these files from CLAUDE.md's architecture section and the doc's own Files section.

If a file listed in the Files section does not exist, that itself is an inaccuracy (stale path).

#### 2b2. Check source file docstrings and comments

For every **Python source file** read in 2b, also scan its module-level docstring, function/class docstrings, and inline comments for stale API references. Fix stale docstrings/comments directly in the source file using the Edit tool. This is the mirror of 2c: 2c checks whether docs accurately describe code; 2b2 checks whether source-file prose accurately describes the code it lives in.

Focus on:
- Stale decorator names (e.g. legacy `@agent.system_prompt` where current code uses `@agent.instructions`)
- Function/method names referenced in docstrings that have been renamed or removed
- Behaviour descriptions that no longer match the implementation

If the fix would require changing anything beyond comment or docstring text (e.g. updating a function body, adding an import, changing a return value), stop and flag it as a code gap instead:
```
⚠ <path>: code gap — cannot fix with docstring edit. Manual fix required.
```
Do not edit functional logic under any circumstances.

#### 2c. Check claims — inaccuracy patterns

Check every factual claim against the source. Inaccuracy patterns to look for:

| Pattern | Examples |
|---------|---------|
| **Phantom feature** | Doc describes a class, table, function, or field that doesn't exist in code |
| **Stale status** | "not yet implemented" / "Phase N ships next" when code is already there |
| **Wrong schema** | Column names, table names, SQL structure that don't match the actual CREATE statements |
| **Wrong flow** | Function call sequence, control flow, or branching that the code doesn't follow |
| **Wrong tool registration** | Tool listed as agent-registered when it isn't, or vice versa |
| **Missing config entry** | Setting/env var exists in the project's config module but absent from doc's Config table |
| **Wrong default** | Default value in doc doesn't match `Field(default=...)` or dataclass default in code |
| **Wrong field name** | Struct/dataclass/schema field name misspelled or renamed in code |
| **Stale file path** | File listed in Files section has been moved or deleted |
| **Missing coverage** | Shipped feature with no doc coverage at all (add a minimal description, don't over-document) |
| **Stale cross-doc index** | `system.md` Component Docs table missing a `docs/specs/` file, or referencing a renamed/deleted one. Check once per sync-doc run regardless of scope — glob actual `docs/specs/*.md` files and diff against the table in `system.md`. |
| **Stale term in out-of-scope docs** | A renamed API, decorator, or concept appears in specs outside the explicit invocation scope. When an API rename is *confirmed* by reading source (not suspected), widen scope to grep all `docs/specs/*.md` files for the old term. Announce the expansion before fixing: `⚠ Expanding scope: renaming <old> → <new> across all specs.` |

#### 2d. Fix in-place

Use the Edit tool to fix each inaccuracy directly in the doc. Rules:
- Keep the doc's structure, tone, and style intact — only change wrong content
- Use pseudocode, not source code paste (DESIGN doc convention)
- For missing config entries: add rows to the Config table in the correct format: `Setting | Env Var | Default | Description`
- For phantom features: delete or replace the wrong description — do not leave a note saying "this was removed"
- For stale status: update the status claim to reflect reality; if a TODO reference is now wrong, remove it or update it
- For missing coverage: add a minimal accurate description — don't pad

### Step 3 — Output summary

After processing all docs, output a table:

```
## sync-doc results

| Doc | Status | Changes |
|-----|--------|---------|
| context.md | fixed | Phase 2 status corrected; 5 config entries added |
| context.md | fixed | doc_tags section rewritten (phantom SQL table) |
| system.md | clean | no inaccuracies found |
| tools.md | fixed | search_knowledge tool registration corrected |
```

If any doc had a stale file path in its Files section (source file doesn't exist), flag it explicitly:
```
⚠ foo.md: Files section references co_cli/old_module.py — file not found. Manual review needed.
```

---

## Scope boundaries

- **Only specs** (`docs/specs/`) — never modify exec-plans, ROADMAP docs, CLAUDE.md, or `docs/reference/RESEARCH-*.md` / `docs/reference/ROADMAP-*.md` files. These are permanent research and reference records that are never edited by sync-doc.
- **`## Product Intent` is human-maintained** — never touch it. sync-doc scope is sections 1–4 only.
- **Factual fixes only** — never restructure sections, rename headings, or change the doc's scope
- **No new sections** unless a shipped feature has zero coverage anywhere in the doc
- **No code changes** — if you find a code gap (e.g., a setting in the doc that's missing from the project's config module), document it as `*(no env var — code gap)*` in the Config table, don't fix the code. **Exception (step 2b2):** source-file docstring and comment fixes are required — when step 2b2 identifies stale API refs, decorator renames, or behaviour description mismatches in a source file's docstrings or inline comments, fix them directly in the source file using the Edit tool. Only docstring/comment text is in scope; functional logic changes remain forbidden.
