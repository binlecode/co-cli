---
name: sync-doc
description: Verify DESIGN docs against current source code and fix inaccuracies in-place. Use after code changes that may have made docs stale, before a release, or when a doc/code contradiction is reported. This skill is also invoked internally by `orchestrate-dev`. Use it standalone for one-off housekeeping outside the planned dev flow.
---

# sync-doc

Direct execution skill — no planning ceremony. Read doc → read source → diff claims → fix in-place.

## Invocation

```
/sync-doc                          # all DESIGN docs
/sync-doc DESIGN-knowledge.md     # specific doc (filename or path)
/sync-doc DESIGN-14 DESIGN-core   # multiple docs (partial names matched)
```

If no argument is given, run against all `docs/DESIGN-*.md` files.

---

## Execution Steps

### Step 1 — Resolve scope

- No args: glob `docs/DESIGN-*.md`, process all.
- Args: match each arg against filenames in `docs/` (exact or prefix match). Abort with a clear error if a given name matches nothing.

### Step 2 — For each doc

#### 2a. Read the doc

Read the full doc. Note:
- The **Files** section (section 4) — lists exactly which source files to check. This is the primary read list.
- The **Config** section (section 3) — lists settings and env vars to verify against `config.py` and `deps.py`.
- The **Tool Surface** table (if present) — lists tool names and registration status to verify against `agent.py`.

#### 2b. Read source files

Read every file listed in the doc's Files section. Also read `co_cli/config.py` and `co_cli/deps.py` whenever the doc has a Config section — these are the ground truth for settings and env vars.

If a file listed in the Files section does not exist, that itself is an inaccuracy (stale path).

#### 2c. Check claims — inaccuracy patterns

Check every factual claim against the source. Inaccuracy patterns to look for:

| Pattern | Examples |
|---------|---------|
| **Phantom feature** | Doc describes a class, table, function, or field that doesn't exist in code |
| **Stale status** | "not yet implemented" / "Phase N ships next" when code is already there |
| **Wrong schema** | Column names, table names, SQL structure that don't match the actual CREATE statements |
| **Wrong flow** | Function call sequence, control flow, or branching that the code doesn't follow |
| **Wrong tool registration** | Tool listed as agent-registered when it isn't, or vice versa |
| **Missing config entry** | Setting/env var exists in `config.py` env_map but absent from doc's Config table |
| **Wrong default** | Default value in doc doesn't match `Field(default=...)` or dataclass default in code |
| **Wrong field name** | Struct/dataclass/schema field name misspelled or renamed in code |
| **Stale file path** | File listed in Files section has been moved or deleted |
| **Missing coverage** | Shipped feature with no doc coverage at all (add a minimal description, don't over-document) |

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
| DESIGN-knowledge.md | fixed | Phase 2 status corrected; 5 config entries added |
| DESIGN-14-memory-lifecycle-system.md | fixed | doc_tags section rewritten (phantom SQL table) |
| DESIGN-core.md | clean | no inaccuracies found |
| DESIGN-tools.md | fixed | search_knowledge tool registration corrected |
```

If any doc had a stale file path in its Files section (source file doesn't exist), flag it explicitly:
```
⚠ DESIGN-foo.md: Files section references co_cli/old_module.py — file not found. Manual review needed.
```

---

## Scope boundaries

- **Only DESIGN docs** — never modify TODO docs, ROADMAP docs, or CLAUDE.md
- **Factual fixes only** — never restructure sections, rename headings, or change the doc's scope
- **No new sections** unless a shipped feature has zero coverage anywhere in the doc
- **No code changes** — if you find a code gap (e.g., a setting in the doc that's missing from `config.py`'s env_map), document it as `*(no env var — code gap)*` in the Config table, don't fix the code
