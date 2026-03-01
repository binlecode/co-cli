# TODO: Knowledge Docs Restructure

Five doc-only edits to make `DESIGN-knowledge.md` and `DESIGN-14-memory-lifecycle-system.md`
accurate to the current codebase. No code changes. No new docs beyond the rename.

---

## TASK-1 — Fix tag filtering section in DESIGN-14 (P0)

**What:** Rewrite the "Tag Filtering and Temporal Search" section in
`docs/DESIGN-14-memory-lifecycle-system.md`. The current section describes a `doc_tags(doc_rowid, tag)`
SQL junction table that does not exist in the code.

**Why:** The `doc_tags` section is entirely wrong — it describes SQL operations that are not
implemented. A developer reading it would implement a junction table migration that is not needed.
The actual tag filtering is Python-side set logic after the FTS5 query.

**How:** Replace the "Tag junction table (`doc_tags`)" subsection with an accurate description:
- Tags are stored as space-separated TEXT in the `docs.tags` column (no separate table).
- When tags are requested, `_fts_search()` fetches `limit * 20` rows from FTS5, then filters
  in Python.
- `tag_match_mode="all"`: `tag_set <= row_tag_set` (subset check — all requested tags must
  be present in the row).
- `tag_match_mode="any"`: `tag_set & row_tag_set` (intersection — at least one requested tag
  must match).
- `dict.fromkeys()` dedup preserves first-occurrence order before slicing to `limit`.
- No SQL junction table — confirmed not implemented in `co_cli/knowledge_index.py`.

**Done when:** `grep "doc_tags" docs/DESIGN-14-memory-lifecycle-system.md` returns zero matches.
The "Tag Filtering and Temporal Search" section describes Python-side filtering and matches the
behavior in `knowledge_index.py:_fts_search()`.

---

## TASK-2 — Fix Phase 2 status in DESIGN-knowledge.md (P0)

**What:** Update the Evolution Path table and Config table in `docs/DESIGN-knowledge.md` to
reflect that Phase 2 (hybrid semantic search) code is complete but not yet ship-ready.

**Why:** The doc currently says Phase 2 is "not yet started" and the TODO references "4 open
bugs blocking ship-ready status." In reality, `knowledge_index.py` fully implements
`_hybrid_search()`, `_vec_search()`, `_hybrid_merge()`, `_embed_cached()`, and
`_generate_embedding()` with Ollama + Gemini providers and an `embedding_cache` table. The
status claim misleads contributors into thinking Phase 2 is future work.

**How:**
- Evolution Path: change Phase 2 row to "code complete, blocked by 4 bugs
  (see `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`)".
- Config table: add Phase 2 settings with their actual defaults from `KnowledgeIndex.__init__`:

| Setting | Env Var | Default | Notes |
|---------|---------|---------|-------|
| `knowledge_embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `"ollama"` | `"ollama"`, `"gemini"`, or `"none"` |
| `knowledge_embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `"embeddinggemma"` | |
| `knowledge_embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `256` | |
| `knowledge_hybrid_vector_weight` | *(no env var)* | `0.7` | Code gap: Settings field exists, no env var mapping |
| `knowledge_hybrid_text_weight` | *(no env var)* | `0.3` | Code gap: Settings field exists, no env var mapping |

The "no env var" note is a documentation note only — adding env var mappings for the weight
fields is out of scope for this doc-only task.

**Done when:** Evolution Path table shows Phase 2 as "code complete, blocked." Config table
includes all 5 Phase 2 settings with the code-gap note on the two weight fields.
`grep "not yet started" docs/DESIGN-knowledge.md` returns zero matches.

---

## TASK-3 — Add article lifecycle section to DESIGN-14 (P1)

**What:** Add a new "Article Lifecycle" section to `docs/DESIGN-14-memory-lifecycle-system.md`
covering how `kind: article` items are managed over time.

**Why:** DESIGN-14 only covers `kind: memory` lifecycle (signal detection, decay, dedup,
precision edits). Articles have distinct lifecycle behavior — URL-dedup consolidation, decay
protection by default, and save provenance — but none of this is documented. The title
"memory-lifecycle" already misleads contributors into thinking the doc only covers memories;
adding articles coverage corrects both the gap and sets up the rename (TASK-4).

**How:** Add a "Article Lifecycle" section after the existing memory lifecycle sections.
Content to cover:
- `save_article()` sets `provenance: web-fetch` and `decay_protected: true` by default.
- URL-dedup: `origin_url` exact-match consolidation via `_consolidate_article()` — if a
  record with the same `origin_url` already exists, the new content merges with the existing
  file rather than creating a duplicate.
- Decay protection: articles are marked `decay_protected: true` so they bypass the decay
  multiplier in memory scoring (they are reference material, not ephemeral preferences).
- No signal detection for articles — they are explicitly saved by the agent, never auto-triggered.

**Done when:** DESIGN-14 has an "Article Lifecycle" section that describes URL-dedup
consolidation, decay-protected default, and `save_article` provenance. No `kind: memory`
assumption in lifecycle coverage — the intro paragraph acknowledges both memory and article
kinds.

**Prerequisite:** None. TASK-3 can run in parallel with TASK-1 and TASK-2.

---

## TASK-4 — Rename DESIGN-14 and update all references (P1)

**What:** Rename `docs/DESIGN-14-memory-lifecycle-system.md` to
`docs/DESIGN-knowledge-lifecycle.md` and update every reference in the codebase.

**Why:** "memory-lifecycle-system" is misleading — the doc covers lifecycle behavior for all
knowledge items (memories and articles), not just memories. The new name matches the
knowledge system naming convention (`DESIGN-knowledge.md` / `DESIGN-knowledge-lifecycle.md`).

**How:**
1. `git mv docs/DESIGN-14-memory-lifecycle-system.md docs/DESIGN-knowledge-lifecycle.md`
2. Update the intro paragraph in the renamed file to say "all knowledge items" instead of "memories".
3. Update `CLAUDE.md` design doc table: replace `DESIGN-14-memory-lifecycle-system.md` with
   `DESIGN-knowledge-lifecycle.md` and update its description.
4. Update `docs/DESIGN-knowledge.md` Files section: replace the old filename with the new one.

**Done when:**
- `docs/DESIGN-14-memory-lifecycle-system.md` does not exist.
- `docs/DESIGN-knowledge-lifecycle.md` exists.
- `grep -r "DESIGN-14" .` returns zero matches (excluding git history).
- `grep -r "memory-lifecycle-system" .` returns zero matches.
- CLAUDE.md design doc table references `DESIGN-knowledge-lifecycle.md`.

**Prerequisite:** TASK-1 and TASK-3 must complete before this task (both edit the file
being renamed here).

---

## TASK-5 — Add External Source Extension Points to DESIGN-knowledge.md (P2)

**What:** Add a "External Source Extension Points" subsection to the Core Logic section of
`docs/DESIGN-knowledge.md`, describing how external sources (Obsidian, Drive, future: email,
calendar) plug into the knowledge index.

**Why:** The current doc describes the index schema and tool surface but does not explain the
extension pattern. A developer adding a new source (e.g. email indexing) has to reverse-engineer
the pattern from `sync_dir()` calls in the Obsidian and Drive tools. The extension point is
simple but undocumented.

**How:** Add a "External Source Extension Points" subsection under Core Logic with:
- The `source` column as the namespace discriminator: `'memory'` (local knowledge files),
  `'obsidian'`, `'drive'`. New sources add a new value here.
- `sync_dir(source, directory, glob='**/*.md')` as the extension entry point. The `glob`
  parameter is overridable for non-markdown sources (e.g. `glob='**/*.txt'`).
- On-demand indexing trigger pattern: tools call `sync_dir()` at tool invocation time, not
  at session start. Index is derived and rebuildable.
- Future pluggable sources (email, calendar) follow the same pattern: call `sync_dir()` with
  their source name and a directory of serialized `.md` files.
- Cross-reference `DESIGN-tools.md` for Obsidian and Drive tool implementations — do not
  duplicate their tool contracts here.

**Done when:** `docs/DESIGN-knowledge.md` has an "External Source Extension Points"
subsection under Core Logic. The section describes the `source` column, `sync_dir()` with
its `glob` parameter, on-demand indexing, and future sources. It cross-references
`DESIGN-tools.md` without duplicating Obsidian/Drive tool details.
