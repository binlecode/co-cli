# TODO: Doc Sync and Corrections

Five doc-only edits to bring TODO files and CLAUDE.md into sync with the current codebase.
No code changes. Tasks on the same file must run in the order listed.

---

## TASK-1 — Strip shipped content from the knowledge/sqlite TODO (P0)

**What:** Remove all Prereq A, Prereq B, and Phase 1 content from
`docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md`. Add a single redirect line at the
top of the implementation section pointing to `docs/DESIGN-knowledge.md`.

**Why:** Prereq A (flat storage), Prereq B (articles), and Phase 1 (FTS5 BM25) have all
shipped. Their design detail now lives in `docs/DESIGN-knowledge.md`. Keeping it in the TODO
contradicts the lifecycle rule and bloats the doc with stale content.

**How:**
Before deleting any content, read `docs/DESIGN-knowledge.md` and verify:
- `CREATE VIRTUAL TABLE docs_fts` appears (FTS5 schema is documented).
- `class KnowledgeIndex` or `KnowledgeIndex` appears (class is documented).
- Prereq A/B and Phase 1 implementation detail is present (not merely referenced).
If any check fails, stop and report the missing section before proceeding.

Remove:
- `## Design Decision: Flat knowledge dir` section
- `## Conceptual Model` section
- `## Storage Layout` section
- `## KnowledgeIndex Design Principle` section
- `## OpenClaw Reference` section
- `## Architecture` section
- `## Schema` section (FTS5 DDL, trigger definitions, query example)
- `## Implementation / Prerequisite A — ✅ Shipped` section and its content
- `## Implementation / Prerequisite B — ✅ Shipped` section and its content
- `## Implementation / Phase 1 — ✅ Shipped` section and its content
- `## Extended Frontmatter Schema` section
- Evolution Path table rows for Prereq A, Prereq B, Phase 1 (shipped rows)
- Files table rows for shipped files (all Prereq A/B/Phase 1 rows)

Replace the Implementation section header with:
> Prereq A, Prereq B, and Phase 1 shipped. See `docs/DESIGN-knowledge.md`. Remaining work:

**Done when:** `grep -c "Prereq A\|Prereq B\|Phase 1" docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md`
returns 0.

---

## TASK-2 — Consolidate audit history into a single Open Bugs section (P0)

**What:** Replace the six sequentially-appended `## Audit` / `## Audit Addendum` /
`## Audit Recheck` sections in `docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md` with
a single `## Open Bugs` section containing the 4 current blocking items.

**Why:** The audit sections contain duplicated findings from the same 4 bugs at different
dates. The signal-to-noise ratio is very low. The 4 open blocking bugs are hard to find.

**How:** Replace all audit sections with:

```markdown
## Open Bugs (blocking Phase 1 → ship-ready)

All 4 confirmed via runtime reproduction as of 2026-03-01.

### BUG-1 (HIGH) — Obsidian folder filter leaks in FTS path
- File: `co_cli/tools/obsidian.py` (`search_notes()` FTS branch)
- Cause: FTS query filters by `source='obsidian'` but does not constrain result paths
  to the requested `folder` prefix.
- Impact: `search_notes(folder="Work")` returns notes from sibling folders already indexed.
- Missing test: `tests/test_obsidian.py` — FTS folder-scoped query must exclude
  notes outside `folder` after prior broad indexing.

### BUG-2 (HIGH) — `/forget` leaves stale FTS rows (ghost recall)
- File: `co_cli/_commands.py` (`_cmd_forget`)
- Cause: file unlink has no matching de-index or `remove_stale()` call.
- Impact: deleted memories/articles still appear in `search_knowledge` until a later sync.
- Missing test: integration test — `/forget` must evict deleted item from FTS results
  in same session.

### BUG-3 (MEDIUM) — `all_approval=True` does not gate precision-write tools
- File: `co_cli/agent.py`
- Cause: `update_memory` and `append_memory` are hardcoded `requires_approval=False`
  instead of respecting the eval-mode `all_approval` flag.
- Impact: eval mode does not consistently defer all write tool calls.
- Missing test: `tests/test_agent.py` — `all_approval=True` must force `update_memory`
  and `append_memory` to require approval.

### BUG-4 (MEDIUM) — `search_knowledge` grep fallback ignores `source` contract
- File: `co_cli/tools/articles.py` (`search_knowledge()` fallback branch)
- Cause: fallback always searches local knowledge files and emits `source='memory'`
  results regardless of requested `source`.
- Impact: `search_knowledge(source='obsidian')` returns memory results in non-FTS mode.
- Missing test: `tests/test_save_article.py` — grep fallback must honor `source` filter
  (return empty for non-memory sources when index unavailable).
```

**Done when:** Exactly one `## Open Bugs` section with 4 `### BUG-N` subsections (BUG-1
through BUG-4). All prior `## Audit` section headings are gone.
`grep -c "^## Audit" docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md` returns 0.

**Prerequisite:** TASK-1 must complete first.

---

## TASK-3 — Update tool surface table in the knowledge TODO (P0)

**What:** Update the tool surface table in `docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md`
to reflect the current agent tool registration. Mark `recall_memory`, `recall_article`, and
`search_notes` as internal-only adapters.

**Why:** `recall_memory`, `recall_article`, and `search_notes` were de-registered from the
agent in favour of `search_knowledge` as the sole agent-facing search tool. But the TODO's
tool table still lists all four as if they are agent-registered, which is wrong.

**How:** Replace the tool surface table with:

| Tool | Backed by | Notes |
|------|-----------|-------|
| `search_knowledge(query, source?, kind?, tags?, created_after?, created_before?)` | `KnowledgeIndex.search()` | Agent-registered. Cross-source: memories, articles, Obsidian, Drive. |
| `save_article(content, title, origin_url, tags?)` | Writes flat `kind: article` file | Agent-registered. |
| `read_article_detail(slug)` | Direct file read | Agent-registered (two-step progressive load). |
| `list_memories(kind?)` | Filesystem scan | Agent-registered. `kind=` filter supported. |
| `recall_memory(query)` | `search(query, source="memory")` | Internal adapter only — not agent-registered. |
| `recall_article(query)` | `search(query, kind="article")` | Internal adapter only — not agent-registered. |
| `search_notes(query, folder?)` | `search(query, source="obsidian")` | Internal adapter only — not agent-registered. BUG-1 open. |
| `search_drive_files(query)` | Drive API `fullText` | Agent-registered (Drive not yet in KnowledgeIndex). |

Remove any architecture ASCII diagram from this TODO (the full diagram lives in `DESIGN-knowledge.md`).
Replace with: `> See docs/DESIGN-knowledge.md for full architecture and search flow.`

**Done when:** `grep "recall_memory\|recall_article\|search_notes" docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md`
returns only rows from the Tool Surface table with the "internal adapter" annotation.
No architecture ASCII diagram remains in this TODO.

**Prerequisite:** TASK-2 must complete first.

---

## TASK-4 — Register orphan TODOs in CLAUDE.md (P0)

**What:** Add two missing TODO entries to the `### TODO` section of `CLAUDE.md`'s Docs table:
`TODO-gap-openclaw-analysis.md` and `TODO-coding-tool-convergence.md`.

**Why:** Both docs exist in `docs/` but are not listed in CLAUDE.md. Agent sessions reading
CLAUDE.md for context cannot discover these TODO docs. Using the label "Openclaw adoption
action plan" (not "gap analysis") to match the doc's own self-description.

**How:** Add to the `### TODO` section of CLAUDE.md's Docs table:
```
- `docs/TODO-gap-openclaw-analysis.md` — Openclaw adoption action plan: shell arg validation,
  exec approval persistence, temporal decay scoring, model fallback, session persistence, doctor
  security checks, MMR re-ranking, embedding provider layer, cron scheduling, config includes (P1–P3)
- `docs/TODO-coding-tool-convergence.md` — Coding tool convergence: native file tools
  (read/list/find/write/edit), shell policy engine, coder subagent delegation, coding eval
  gates, workspace checkpoint + rewind, approval risk classifier (P0–P2)
```

**Done when:** `grep "TODO-gap-openclaw-analysis\|TODO-coding-tool-convergence" CLAUDE.md`
returns exactly 2 lines.

---

## TASK-5 — Update CLAUDE.md Knowledge System section (P0)

**What:** Update the Knowledge System section in `CLAUDE.md` to reflect current shipped state.
Remove the "Planned" bullet block. Update the sqlite TODO's Docs table entry.

**Why:** The current CLAUDE.md says memories live in `.co-cli/knowledge/memories/*.md` (stale
— flat layout shipped) and has a "Planned" block describing items that have already shipped.

**How:**

Replace the current state paragraph with:
```
**Current state:** All knowledge in flat `.co-cli/knowledge/*.md` (YAML frontmatter, markdown
body). Memories (`kind: memory`) and articles (`kind: article`) coexist, distinguished by `kind`
frontmatter. FTS5 (BM25) search via `KnowledgeIndex` in `search.db`.

Agent-facing tools: `save_memory`, `save_article`, `search_knowledge` (cross-source, primary
search), `list_memories`, `read_article_detail`.
Internal adapters (not agent-registered): `recall_memory`, `recall_article`, `search_notes`.
```

Remove the entire `**Planned (see TODO-sqlite-...):` block and its 4 bullet points.

Update the sqlite TODO's Docs table entry description from whatever it currently says to:
> "Phase 2 (hybrid semantic search, sqlite-vec) and Phase 3 (cross-encoder rerank) — not yet
> started; 4 open bugs blocking ship-ready status"

**Done when:** CLAUDE.md Knowledge System section has no "Planned" block and no reference to
`memories/*.md` subdir. It contains "search_knowledge" in the tools list.
The sqlite TODO's Docs table entry mentions "Phase 2" and "4 open bugs".
`grep "memories/\*.md\|Planned" CLAUDE.md` returns 0.
`grep -c "^\`\`\`" CLAUDE.md` returns an even number (balanced fences).
