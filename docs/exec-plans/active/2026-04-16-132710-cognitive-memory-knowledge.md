# Plan: Cognitive Architecture — Two-Layer Memory & Knowledge

Task type: infra-feature (multi-phase)

## Context

co-cli has three persistent data tiers that evolved independently:

1. **Session transcripts** (`sessions/*.jsonl`) — append-only JSONL, the raw episodic timeline
2. **Extracted signals** (`memory/*.md`) — distilled user prefs, project rules, feedback, decisions
3. **Library articles** (`library/*.md`) — fetched docs, API specs, external references

The product language calls tier 2 "memory" and tier 3 "knowledge." This is backwards.

"User prefers pytest" is not a memory — it's a reusable distilled fact. "Session transcript from April 12" is the actual memory. The naming confusion cascades into:

- `search_memories()` searches distilled facts, not episodes
- `search_knowledge()` searches only articles, not all reusable content
- `always_on` standing context draws from "memory" but is semantically knowledge
- The extractor "saves memories" but is producing knowledge artifacts
- `memory.md` and `library.md` specs encode implementation-era boundaries, not cognitive role

### Decision

Adopt a strict two-layer model per the PROPOSAL (`docs/PROPOSAL-memory-vs-knowledge-architecture.md`):

- **Memory** = raw experiential timeline (transcripts, event logs)
- **Knowledge** = every reusable distilled artifact (extracted insights, articles, imported notes, media assets)

The corrective is: existing `memory/*.md` extracted facts are **knowledge**, not memory. They belong in the same conceptual tier as articles. The extractor's output target changes from "memory" to "knowledge." Transcript search becomes the memory-search path.

### Related Plans

- `2026-04-01-163505-daemon-util-1-knowledge-compaction.md` — nightly compaction job. This plan refines and supersedes the memory-facing portions.
- `2026-04-14-173800-dual-transcript-write.md` — session `.md` shadow files. Useful for memory-tier grep fallback but not a blocker.
- `docs/PROPOSAL-memory-vs-knowledge-architecture.md` — the target-state design. This plan is the implementation vehicle.

## Problem & Outcome

**Problem:** The cognitive model is incoherent — distilled reusable facts are called "memory" while the actual episodic timeline is relegated to a secondary "session" subsystem. This creates naming drift, schema drift, tool drift, and spec drift that compounds with every new feature.

**Failure cost:** Every new tool, prompt, or spec inherits the confusion. Consolidation and lifecycle machinery built on the wrong model creates a third variant (promoted articles) instead of simplifying to two clean tiers.

**Outcome:**
1. Specs and terminology describe memory as raw timeline, knowledge as reusable artifacts
2. Extracted insights and articles share one unified knowledge model
3. `search_knowledge()` becomes the universal reusable-recall surface
4. Episodic recall routes through transcript/session search
5. The extractor writes knowledge artifacts, not "memory facts"
6. Knowledge lifecycle is self-maintaining: dedup on write, recall tracking, automated decay, batch consolidation

## Scope

Seven phases, each independently shippable. Phases 0–3 correct the model. Phases 4–6 add lifecycle machinery on the corrected foundation. Lifecycle features gated via `knowledge.consolidation_enabled` (default `false`) through Phase 5.

## Behavioral Constraints

- **Never delete — only archive.** All decay/merge operations move originals to `knowledge_dir/_archive/`. Recovery is always possible.
- **Pinned and protected entries are immune** from automated decay and merge (`pin_mode` set, `decay_protected=True`).
- **Per-turn extractor stays.** It captures signals while fresh. It changes *what it writes* (knowledge artifacts) and *where* (knowledge dir), not *when* it runs.
- **No new storage backends.** SQLite FTS5 + .md files + JSONL transcripts cover all access patterns.
- **DB is the retrieval layer, disk is the source layer.** Search queries hit `chunks_fts`/`docs` tables directly — never read `.md` files at query time. `sync_dir()` keeps the DB current from disk. This is already how `KnowledgeStore` works; the two-layer model codifies it. A separate `content_index` DB field (per PROPOSAL §6.2) is deferred until non-text artifacts (media) require extracted-text indexing distinct from the raw body.
- **Memory .md format stays for knowledge files.** Frontmatter + body is the right format for individually-addressable, human-editable, agent-readable artifacts.
- **Single `knowledge_dir` replaces `memory_dir` + `library_dir`.** Memory is now sessions only — `memory_dir` has no conceptual reason to exist. Phase 2 migrates existing files into `knowledge_dir` via a one-time script and removes the two legacy dirs.
- **Consolidation failures must not corrupt.** Archive before merge, confirm write before unlinking originals.

## Failure Modes

- **Terminology migration confuses existing users**: Mitigation: staged rollout, compatibility aliases, clear changelog entries.
- **Unified schema loses type-specific semantics**: Mitigation: `artifact_kind` subtypes preserve the distinction between a preference and an article.
- **Consolidation sub-agent hallucinates**: Mitigation: prompt constraint "only combine existing text, never invent." Originals archived for recovery.
- **Decay removes something needed**: Mitigation: `last_recalled` tracking + 90-day threshold + archive recovery.
- **Dedup blocks legitimate distinct entries**: Mitigation: conservative threshold (0.75 Jaccard), only same-`artifact_kind` compared.

---

## Phase 0 — Foundation: Config & Data Model

No behavior change. Extend config and data model to support subsequent phases.

### ✓ DONE TASK-0.1: Knowledge lifecycle config fields

- `files:` `co_cli/config/_knowledge.py`
- Add fields to `KnowledgeSettings`:
  ```
  consolidation_enabled: bool = False
  consolidation_trigger: Literal["session_end", "manual"] = "session_end"
  consolidation_lookback_sessions: int = 5
  consolidation_similarity_threshold: float = 0.75
  max_artifact_count: int = 300
  decay_after_days: int = 90
  ```
- Env var mapping: `CO_KNOWLEDGE_CONSOLIDATION_ENABLED`, `CO_KNOWLEDGE_DECAY_AFTER_DAYS`, etc.
- Note: these go in `KnowledgeSettings`, not `MemorySettings` — under the new model, lifecycle management is a knowledge concern.
- `done_when:` `uv run pytest tests/test_config.py` passes. New fields visible in `co status` output.

### ✓ DONE TASK-0.2: Recall tracking fields on frontmatter

- `files:` `co_cli/knowledge/_frontmatter.py`, `co_cli/memory/recall.py` (lines 24–39)
- Add to `MemoryEntry` dataclass (will be renamed in Phase 2, but fields are forward-compatible):
  ```python
  provenance: str | None = None      # "detected", "user-told", "consolidated", "web-fetch", "manual"
  last_recalled: str | None = None   # ISO8601, updated on recall hit
  recall_count: int = 0              # Incremented on each recall hit
  ```
- Add all three to `validate_memory_frontmatter()` optional fields.
- Backward compatible: existing files without these fields load with `None`/`0`.
- `done_when:` Existing memory + article files parse without errors. `uv run pytest tests/test_memory*.py` passes.

### ✓ DONE TASK-0.3: Cognition spec

- `files:` `docs/specs/cognition.md` (new)
- Document: two-layer architecture, what belongs where, promotion/consolidation bridge, retrieval model.
- Cross-reference from `memory.md` and `library.md` Product Intent sections.
- This spec becomes the umbrella that `memory.md` (transcripts) and the future `knowledge.md` (artifacts) reference.
- `done_when:` Spec follows project conventions.
- **Note:** Pre-completed — `docs/specs/cognition.md` was already fully written before this delivery.

---

## Phase 1 — Spec & Terminology Correction

Align all product language with the two-layer model. No runtime behavior changes yet.

### ✓ DONE TASK-1.1: Rewrite `docs/specs/memory.md`

- `files:` `docs/specs/memory.md`
- Redefine memory as the **transcript/event timeline** only:
  - Session transcripts (JSONL)
  - Session index (FTS5 over transcripts)
  - Episodic recall via `session_search`
- Remove all references to "extracted durable facts" from the memory definition. Those move to the knowledge spec.
- Preserve the transcript persistence mechanics (append, branching, compaction, resume) — those are correct.
- `done_when:` Spec describes memory as raw timeline. No mention of `save_memory` or extracted facts.

### ✓ DONE TASK-1.2: Create `docs/specs/knowledge.md` (evolve from library.md)

- `files:` `docs/specs/knowledge.md` (new), `docs/specs/library.md` (deprecate or redirect)
- Merge the scope of `library.md` (articles, FTS5 index, chunk search) with the extracted-facts scope formerly in `memory.md`.
- Define knowledge as: extracted insights + articles + imported notes + synced sources.
- Document `artifact_kind` subtypes: `preference`, `decision`, `rule`, `feedback`, `article`, `reference`, `note`.
- Document standing context as knowledge metadata (`pin_mode`), not a separate memory concept.
- `done_when:` Spec covers all reusable artifacts under one umbrella.

### ✓ DONE TASK-1.3: Update system-level specs

- `files:` `docs/specs/system.md`, `docs/specs/context.md`, `docs/specs/tools.md`
- Update terminology: "memory search" → transcript search. "knowledge search" → unified reusable-recall.
- Update `context.md`: standing context sourced from knowledge, not from a "memory facts" tier.
- Update `tools.md` catalog: mark `search_memories` as transitional, document `search_knowledge` as universal.
- `done_when:` Specs internally consistent with the two-layer model.

### ✓ DONE TASK-1.4: Update extractor prompt

- `files:` `co_cli/memory/prompts/knowledge_extractor.md`
- Change framing: "You are extracting **knowledge artifacts** from this conversation — reusable facts, preferences, and decisions worth keeping."
- Keep the 4 signal types (user, feedback, project, reference) — these become `artifact_kind` values.
- No code change yet — the prompt update is safe because `save_memory()` still works.
- `done_when:` Prompt language aligned with two-layer model.

---

## Phase 2 — Knowledge Schema Unification

Make extracted facts and articles share one conceptual model. This is the structural migration.

### TASK-2.1: Unified `KnowledgeArtifact` model

- `files:` `co_cli/knowledge/_artifact.py` (new)
- Define `KnowledgeArtifact` dataclass — the successor to both `MemoryEntry` (for extracted facts) and the implicit article model:

  | Field | Source | Purpose |
  |-------|--------|---------|
  | `id` | both | UUID4 identity |
  | `artifact_kind` | new | `preference`, `decision`, `rule`, `feedback`, `article`, `reference`, `note` |
  | `title` | article's `title` / memory's `name` | Human-readable label |
  | `description` | both | Compact summary for retrieval |
  | `content` | both (`body`/`content`) | Primary text |
  | `created` | both | ISO8601 |
  | `updated` | both | ISO8601 |
  | `tags` | both | Retrieval labels |
  | `related` | both | Soft links |
  | `source_type` | new, subsumes `provenance` | `detected`, `web_fetch`, `manual`, `obsidian`, `drive`, `consolidated` |
  | `source_ref` | new, subsumes `origin_url` | Session ID, URL, file path, or artifact ID |
  | `certainty` | article's `certainty` | `high`, `medium`, `low` |
  | `pin_mode` | replaces `always_on` | `standing` (always injected), `none` (default) |
  | `decay_protected` | article's `decay_protected` | Boolean |
  | `last_recalled` | new (Phase 0) | ISO8601 |
  | `recall_count` | new (Phase 0) | Integer |

- `kind` field removed — everything in knowledge is `kind: knowledge`. `artifact_kind` provides the subtype.
- Implement `load_knowledge_artifact(path) -> KnowledgeArtifact` with backward compatibility:
  - Files with `kind: memory` map `type` → `artifact_kind`, `always_on` → `pin_mode`, `name` → `title`
  - Files with `kind: article` map `origin_url` → `source_ref`, `title` stays
- `done_when:` All existing `memory/*.md` and `library/*.md` files parse into `KnowledgeArtifact` without errors.

### TASK-2.2: Migrate frontmatter writer

- `files:` `co_cli/knowledge/_frontmatter.py`
- Add `render_knowledge_file(artifact: KnowledgeArtifact) -> str` that writes the new canonical frontmatter format:
  ```yaml
  ---
  id: <uuid>
  kind: knowledge
  artifact_kind: preference
  title: User prefers pytest
  ...
  ---
  ```
- Keep `render_memory_file()` as deprecated alias during transition.
- New writes (from extractor, save_article, manual save) use the new format.
- Existing files are NOT rewritten — they load via backward-compatible reader (TASK-2.1).
- `done_when:` New files written in canonical format. Old files still loadable.

### TASK-2.3: Merge into single `knowledge_dir`

- `files:` `co_cli/deps.py`, `co_cli/config/_core.py`, `co_cli/config/_knowledge.py`
- Replace `memory_dir` and `library_dir` with `knowledge_dir: Path` on CoDeps (default: `~/.co-cli/knowledge/`).
- Add `CO_KNOWLEDGE_DIR` env var override. Remove `CO_LIBRARY_PATH` (or alias to `CO_KNOWLEDGE_DIR` with deprecation warning).
- Remove `memory_dir` and `library_dir` fields from CoDeps. All reads/writes target `knowledge_dir`.
- `done_when:` CoDeps has one `knowledge_dir`. All tool and store code references updated. Tests pass.

### TASK-2.3b: One-time migration script

- `files:` `scripts/migrate-knowledge-dir.py` (new)
- On first run after upgrade (or via explicit `co migrate`):
  1. Create `~/.co-cli/knowledge/` if missing
  2. Move all `~/.co-cli/memory/*.md` → `~/.co-cli/knowledge/` (skip `_archive/`, `_dream_state.json`)
  3. Move all `~/.co-cli/library/*.md` → `~/.co-cli/knowledge/`
  4. Handle filename collisions (same slug in both dirs): append `-lib` or `-mem` suffix
  5. Rebuild `search.db` index from the merged directory
  6. Leave empty `memory/` and `library/` dirs in place (don't delete — user may have custom scripts pointing there). Print notice.
- Also wire auto-migration into bootstrap: if `knowledge_dir` is empty but `memory_dir` or `library_dir` have `.md` files, run migration automatically with console notice.
- `done_when:` Fresh install uses `knowledge_dir` only. Existing installs auto-migrate on first run. Manual `scripts/migrate-knowledge-dir.py` works standalone.

### TASK-2.4: Extractor writes knowledge artifacts

- `files:` `co_cli/memory/_extractor.py`, `co_cli/tools/memory.py`
- Change `save_memory()` → `save_knowledge()`:
  - Writes to `knowledge_dir` (not `memory_dir`)
  - Uses `render_knowledge_file()` format
  - Maps extractor's `type_` param to `artifact_kind`
  - Sets `source_type="detected"`, `source_ref=<session_id>`
- Register `save_knowledge()` as the extractor sub-agent's tool (replaces `save_memory` in `_knowledge_extractor_agent.tools`).
- Keep `save_memory()` as deprecated wrapper that delegates to `save_knowledge()`.
- `done_when:` New extractions write `kind: knowledge` files to `knowledge_dir/`. Extractor tests pass.

### TASK-2.5: Update `save_article()` to write knowledge artifacts

- `files:` `co_cli/tools/articles.py` (line 208)
- `save_article()` writes to `knowledge_dir/` using `render_knowledge_file()`:
  - `artifact_kind="article"` or `artifact_kind="reference"`
  - `source_type="web_fetch"`, `source_ref=origin_url`
  - Dedup still keyed on `source_ref` (same as current `origin_url` dedup)
- Keep function name `save_article()` — it's a valid action verb even under the new model. But the output format and location change.
- `done_when:` New articles written as knowledge artifacts. URL dedup still works.

### TASK-2.6: Knowledge store re-indexing

- `files:` `co_cli/bootstrap/core.py`, `co_cli/knowledge/_store.py`
- Update `sync_knowledge_store()` to index `knowledge_dir` as the sole source:
  - `knowledge_dir` → `source="knowledge"`
  - Drop `source="memory"` and `source="library"` indexing paths
- After migration (TASK-2.3b), all content lives in one directory — no multi-source scanning needed.
- `done_when:` `search.db` indexes `knowledge_dir` only. FTS search returns unified results.

---

## Phase 3 — Tool Surface Convergence

Align the agent's tool surface with the two-layer model.

### TASK-3.1: Expand `search_knowledge()` to cover all reusable artifacts

- `files:` `co_cli/tools/articles.py` (line 122)
- Remove the `source="memory"` rejection (line 178–185). Instead, search all knowledge sources including former memory-extracted facts.
- Default `source=None` searches everything: `knowledge_dir` + obsidian + drive.
- Update docstring: "Primary search over all reusable knowledge — preferences, rules, articles, notes, and synced sources."
- `done_when:` `search_knowledge("user prefers pytest")` returns extracted facts. No source rejection.

### TASK-3.2: Repurpose `search_memories()` for transcript search

- `files:` `co_cli/tools/memory.py` (line 183)
- Rewrite `search_memories()` to delegate to `session_search()`:
  ```python
  async def search_memories(ctx, query, limit=5):
      """Search episodic memory — past conversation transcripts."""
      return await session_search(ctx, query, limit)
  ```
- This preserves backward compatibility for any prompts or skills that call `search_memories()`.
- Update docstring to say "episodic memory (transcripts)" explicitly.
- `done_when:` `search_memories("pytest")` returns transcript excerpts, not extracted facts.

### TASK-3.3: Repurpose `list_memories()` for knowledge listing

- `files:` `co_cli/tools/memory.py` (line 253)
- Rename to `list_knowledge()` (keep `list_memories` as deprecated alias).
- Load from `knowledge_dir`.
- Update output to show `artifact_kind` column.
- `done_when:` `list_knowledge()` returns both old extracted facts and articles in one unified list.

### TASK-3.4: Update `_recall_for_context` to search knowledge

- `files:` `co_cli/tools/memory.py` (line 106)
- Change `_recall_for_context()` from:
  ```python
  knowledge_store.search(query, source="memory", kind="memory", ...)
  ```
  to:
  ```python
  knowledge_store.search(query, source="knowledge", ...)
  ```
- This means turn-time recall now surfaces both extracted facts AND articles — which is correct. A relevant article should be injected just like a relevant preference.
- Filter by relevance score, not by legacy `kind`.
- `done_when:` Per-turn recall returns both extracted facts and articles.

### TASK-3.5: Update standing context injection

- `files:` `co_cli/agent/_instructions.py` (line 24)
- Change `add_always_on_memories()` → `add_standing_knowledge()`:
  ```python
  def add_standing_knowledge(ctx: RunContext[CoDeps]) -> str:
      entries = load_pinned_knowledge(ctx.deps.knowledge_dir)
      ...
  ```
- `load_pinned_knowledge()` loads artifacts where `pin_mode="standing"` (new format) OR `always_on=True` (compat — old files migrated into `knowledge_dir` still have this field).
- Cap at 5 entries (unchanged).
- Register updated function in agent builder.
- `done_when:` Standing context injected from knowledge artifacts. Old `always_on` entries still work.

### TASK-3.6: Update tool registration

- `files:` `co_cli/agent/_native_toolset.py` (lines 123–138)
- Current ALWAYS-visible knowledge reads:
  ```python
  search_memories      # → now delegates to session_search (transcript)
  search_knowledge     # → now universal reusable-recall
  search_articles      # → deprecate, alias to search_knowledge(artifact_kind="article")
  read_article         # → keep as-is (reads full body by slug)
  list_memories        # → becomes list_knowledge
  ```
- Promote `session_search` from DEFERRED to ALWAYS-visible (it's now the memory search).
- Deprecation: `search_articles` and `search_memories` remain registered but delegate to the canonical tools. Remove in a future cleanup pass.
- `done_when:` Agent toolset reflects two-layer model. Old tool names still callable.

### TASK-3.7: Update `/memory` REPL commands

- `files:` `co_cli/commands/_commands.py`
- Add `/knowledge` as primary command namespace:
  ```
  /knowledge list [query] [flags]    → list knowledge artifacts
  /knowledge count [query] [flags]   → count artifacts
  /knowledge forget <query> [flags]  → preview + confirm → archive artifacts
  /knowledge stats                   → health dashboard (Phase 6)
  ```
- Keep `/memory` as alias during transition, with deprecation notice.
- `done_when:` `/knowledge list` works. `/memory list` still works with notice.

---

## Phase 4 — Dedup on Write & Recall Tracking

Now operating on the corrected knowledge model. Solves the known gap: "extractor produces duplicates."

### TASK-4.1: Token-level similarity utility

- `files:` `co_cli/knowledge/_similarity.py` (new)
- Implement `token_jaccard(a: str, b: str) -> float`:
  - Lowercase, split on whitespace, filter stopwords
  - Stopword set: factor from `_store.py` `STOPWORDS` (line 47) into `co_cli/knowledge/_stopwords.py` shared constant
  - Return `|intersection| / |union|`
- Implement `find_similar_artifacts(content, artifact_kind, artifacts, threshold) -> list[KnowledgeArtifact]`:
  - Filter to same `artifact_kind` (if provided)
  - Return matches above threshold, sorted by similarity descending
- Pure Python, no external deps.
- `done_when:` `uv run pytest tests/test_knowledge_similarity.py` passes (empty, identical, disjoint, unicode edge cases).

### TASK-4.2: Dedup check in save_knowledge()

- `files:` `co_cli/tools/knowledge.py` (the renamed save path from TASK-2.4)
- Before writing a new file:
  1. Load existing artifacts from `knowledge_dir`
  2. `matches = find_similar_artifacts(content, artifact_kind, entries, threshold)`
  3. Best match > threshold:
     - Near-identical (> 0.9): skip write → return `action="skipped"`
     - Superset: replace existing content → return `action="merged"`
     - Overlapping: append to existing → return `action="appended"`
  4. No match: create new file → return `action="saved"`
- Guard: skip dedup when `consolidation_enabled=False` (zero overhead for non-opt-in users).
- OTel attribute: `knowledge.dedup_action`.
- `done_when:` Tests cover: duplicate detection, merge, skip, distinct-write, and bypass-when-disabled paths.

### TASK-4.3: Touch last_recalled on recall hits

- `files:` `co_cli/tools/memory.py` (`_recall_for_context`, line 106)
- After knowledge store returns results, fire-and-forget `asyncio.create_task(_touch_recalled(paths))`:
  - Parse frontmatter
  - Increment `recall_count`
  - Set `last_recalled` to UTC now
  - Atomic write (tempfile + `os.replace`)
  - Re-index if knowledge_store available
- Do NOT block the recall return path.
- Guard: skip if path doesn't exist (race with `/knowledge forget`).
- `done_when:` Tests verify `recall_count` increments and `last_recalled` updates after recall.

---

## Phase 5 — Consolidation ("Dreaming")

Batch lifecycle management operating on the unified knowledge layer.

### TASK-5.1: Archive infrastructure

- `files:` `co_cli/knowledge/_archive.py` (new)
- `archive_artifacts(entries, knowledge_dir, knowledge_store) -> int`:
  - Create `knowledge_dir/_archive/` if missing
  - Move each file to `_archive/`
  - Remove from FTS index if store available
  - Return count archived
- `restore_artifact(slug, knowledge_dir, knowledge_store) -> bool`:
  - Find in `_archive/` by slug prefix
  - Move back to active dir
  - Re-index
- `done_when:` `uv run pytest tests/test_knowledge_archive.py` — archive, verify moved, verify FTS removed, restore, verify back.

### TASK-5.2: Decay candidate identification

- `files:` `co_cli/knowledge/_decay.py` (new)
- `find_decay_candidates(knowledge_dir, config) -> list[KnowledgeArtifact]`:
  - Load all artifacts from `knowledge_dir`
  - Filter: `not pin_mode and not decay_protected`
  - Filter: `created` older than `config.decay_after_days`
  - Filter: `last_recalled is None` OR `last_recalled` older than `config.decay_after_days`
  - Sort by age descending
- `done_when:` Tests with synthetic entries covering all filter combinations.

### TASK-5.3: Dream state persistence

- `files:` `co_cli/knowledge/_dream.py` (new)
- `DreamState` (Pydantic model at `knowledge_dir/_dream_state.json`):
  ```python
  class DreamState(BaseModel):
      last_dream_at: str | None = None
      processed_sessions: list[str] = []
      stats: DreamStats = DreamStats()
  
  class DreamStats(BaseModel):
      total_cycles: int = 0
      total_extracted: int = 0
      total_merged: int = 0
      total_decayed: int = 0
  ```
- Load/save helpers.
- `done_when:` Round-trip test passes.

### TASK-5.4: Transcript mining

- `files:` `co_cli/knowledge/_dream.py`
- `_mine_transcripts(deps, state) -> int`:
  1. List sessions in `deps.sessions_dir`, newest-first (lexicographic — timestamps in filenames)
  2. Take last `config.consolidation_lookback_sessions`
  3. Skip sessions in `state.processed_sessions`
  4. Per session:
     a. `load_transcript(path)` from `co_cli/context/transcript.py` (line 114)
     b. Build window via refactored helper. Current `_build_window()` in `_extractor.py` (line 39) caps at 10 text + 10 tool entries. Factor core tagging into `_tag_messages()` shared helper. Create `_build_dream_window(messages, max_text=50, max_tool=50)`.
     c. If window > ~16K chars, chunk into ~12K segments with 2K overlap. Run sub-agent on each chunk.
     d. Retrospective sub-agent (separate `Agent` instance, `NOREASON_SETTINGS`): "Extract cross-turn patterns, implicit preferences, corrections the per-turn extractor may have missed."
     e. Sub-agent calls `save_knowledge()` — TASK-4.2 dedup catches redundancy
     f. Mark session processed in state
  5. Return count extracted
- Bounds: max 5 saves per session. Malformed transcript → log warning, skip.
- `done_when:` Seed 2 transcripts with known patterns → verify extraction + session marking. Re-run → verify skip.

### TASK-5.5: Knowledge merge

- `files:` `co_cli/knowledge/_dream.py`
- `_merge_similar_artifacts(deps) -> int`:
  1. Load all active knowledge artifacts
  2. Group by `artifact_kind`
  3. Pairwise similarity within groups (TASK-4.1 utility)
  4. Identify clusters above threshold
  5. Per cluster (max 10 merges/cycle, max 5 entries/cluster):
     a. Skip if any entry has `pin_mode` or `decay_protected`
     b. Consolidation sub-agent: "Merge these entries. Only combine existing text. Never invent."
     c. Write merged artifact with `source_type="consolidated"`, tags = union
     d. Archive originals via TASK-5.1
  6. Re-index
  7. Return merge count
- `done_when:` Seed 4 similar artifacts → merge → verify 1 merged + 4 archived.

### TASK-5.6: Automated decay sweep

- `files:` `co_cli/knowledge/_dream.py`
- `_decay_sweep(deps) -> int`:
  1. `find_decay_candidates()` (TASK-5.2)
  2. Cap at 20 archives per cycle
  3. Archive via TASK-5.1
  4. Return count
- `done_when:` Seed old unretrieved artifacts → sweep → verify archived.

### TASK-5.7: Dream cycle orchestrator

- `files:` `co_cli/knowledge/_dream.py`
- `run_dream_cycle(deps, dry_run=False) -> DreamResult`:
  ```python
  @dataclass
  class DreamResult:
      extracted: int = 0
      merged: int = 0
      decayed: int = 0
      errors: list[str] = field(default_factory=list)
      
      @property
      def any_changes(self) -> bool:
          return (self.extracted + self.merged + self.decayed) > 0
  ```
  - Load dream state
  - Run: mine → merge → decay (each try/except'd — one failure doesn't block others)
  - Save state
  - OTel span `co.dream.cycle` with child spans per operation
  - `dry_run`: report without writing
- Note: no module-level mutable state. Unlike the per-turn extractor's `_in_flight` singleton guard, the dream cycle is called synchronously from `_drain_and_cleanup` or `/knowledge dream`. Caller awaits the result.
- `done_when:` Integration test: full cycle with seeded data, all ops execute, state persists.

### TASK-5.8: Session-end trigger

- `files:` `co_cli/main.py` (`_drain_and_cleanup`, line 184)
- After `drain_pending_extraction()` (line 188), within the `deps is not None` block (line 189):
  ```python
  if deps.config.knowledge.consolidation_enabled:
      if deps.config.knowledge.consolidation_trigger == "session_end":
          try:
              async with asyncio.timeout(60):
                  result = await run_dream_cycle(deps)
              if result.any_changes:
                  logger.info("Dream: %d new, %d merged, %d archived",
                              result.extracted, result.merged, result.decayed)
          except TimeoutError:
              logger.warning("Dream cycle timed out after 60s")
          except Exception:
              logger.warning("Dream cycle failed", exc_info=True)
  ```
- Note: `frontend` unavailable in `_drain_and_cleanup` — use `logger`. User has exited REPL; output goes to trace.
- `done_when:` Enable consolidation, chat, exit → observe in `co traces`.

### TASK-5.9: `/knowledge dream` and `/knowledge restore` commands

- `files:` `co_cli/commands/_commands.py`
- `/knowledge dream [--dry]` — run dream cycle manually
- `/knowledge restore [slug]` — list archived artifacts, or restore by slug
- `/knowledge decay-review [--dry]` — show decay candidates, confirm to archive
- `done_when:` Manual test of all three commands.

---

## Phase 6 — Observability & Health

### TASK-6.1: OTel spans for dream operations

- `files:` `co_cli/knowledge/_dream.py`
- Spans: `co.dream.cycle` (parent), `co.dream.mine`, `co.dream.merge`, `co.dream.decay`
- Attributes: `dream.extracted`, `dream.merged`, `dream.decayed`
- `done_when:` Visible in `co traces` after a cycle.

### TASK-6.2: `/knowledge stats`

- `files:` `co_cli/commands/_commands.py`
- Output:
  ```
  Knowledge: 176 artifacts
    preference: 87, feedback: 23, rule: 18, decision: 14, article: 34
    pinned: 5, decay-protected: 34
  Archived: 28
  Last dream: 2026-04-15T22:00:00Z (3 new, 2 merged, 1 archived)
  Decay candidates: 8
  ```
- Reads from `knowledge_dir` files + dream state + `_archive/` dir.
- `done_when:` Accurate counts displayed.

### TASK-6.3: Safety bounds documentation

- `files:` `docs/specs/cognition.md` (update from Phase 0)
- Document all bounds:
  - Max 10 merges per dream cycle
  - Max 20 archives per dream cycle
  - Max 5 entries per merge cluster
  - Max 5 saves per transcript mining session
  - Dream timeout: 60 seconds
  - All archives recoverable via `/knowledge restore`
  - `pin_mode` + `decay_protected` immunity
- `done_when:` Spec complete.

---

## Phase Dependencies & Ordering

```
Phase 0 ───→ Phase 1 ───→ Phase 2 ───→ Phase 3 ───→ Phase 4 ───→ Phase 5
(config)     (specs)      (schema)     (tools)      (dedup)      (dream)

Phase 6: observability (parallel, incremental from Phase 5 onward)
```

Phases 0–1 are spec/config only — zero runtime risk.
Phase 2 is the structural migration — highest risk, most careful testing.
Phase 3 is tool surface — user-visible changes, needs changelog.
Phases 4–5 are lifecycle machinery — feature-gated, opt-in.

### Phase Sizing

| Phase | Effort | New Files | Modified Files |
|-------|--------|-----------|----------------|
| 0 | S | 1 spec | 2 source |
| 1 | M | 1 spec (`knowledge.md`) | 3 specs + 1 prompt |
| 2 | L | 2 (`_artifact.py`, `migrate-knowledge-dir.py`) + tests | 6 source |
| 3 | L | 0 | 7 source + 1 commands |
| 4 | M | 2 (`_similarity.py`, `_stopwords.py`) + tests | 2 source |
| 5 | L | 3 (`_archive.py`, `_decay.py`, `_dream.py`) + tests | 2 source |
| 6 | S | 0 | 2 source + 1 spec |

## Open Questions

1. **Q:** Should `memory_dir` and `library_dir` be merged into `knowledge_dir`?
   **A:** Yes. Memory is sessions now — `memory_dir` has no reason to exist. One-time migration in Phase 2 (TASK-2.3b). Resolved.

2. **Q:** Should `docs/specs/library.md` be renamed to `knowledge.md` or kept with a redirect?
   **Lean:** Create new `knowledge.md`, reduce `library.md` to a one-line redirect. Clean break.

3. **Q:** What is the minimal `artifact_kind` set?
   **A:** `preference`, `decision`, `rule`, `feedback`, `article`, `reference`, `note`. No `media_asset` until media processing is on the roadmap. Map current `type` values: `user` → `preference`, `feedback` → `feedback`, `project` → `rule` or `decision`, `reference` → `reference`. Resolved.

4. **Q:** Should the dream cycle run on a daemon schedule?
   **Lean:** Session-end first. Daemon scheduling is a separate infra concern (covered by daemon-utils plans). Add `cron` trigger option later.

5. **Q:** Should archived artifacts be searchable?
   **Lean:** No. Archive = out of rotation. `/knowledge restore` for recovery. Keeps search clean.

6. **Q:** Indexing strategy for unified knowledge layer?
   **A:** All knowledge at chunk level. Extracted facts are short (< 2KB) — they become single-chunk artifacts. Articles remain multi-chunk. Unified chunk-level FTS5 + optional vector. Resolved.

7. **Q:** Should `content_index` (PROPOSAL §6.2) be a separate DB field?
   **A:** Deferred. For text `.md` files, `content_index` = the full body. `sync_dir()` already handles indexing body content into `chunks.content`. A separate field adds value only when non-text artifacts (media) need extracted-text indexing distinct from the raw file. Not on the near-term roadmap. Resolved — revisit when media support is planned.

---

# Audit Log

## Draft — Author

Plan drafted from:
- Deep code scan of `co_cli/memory/`, `co_cli/knowledge/`, `co_cli/tools/`, `co_cli/context/`, `co_cli/config/`, `co_cli/deps.py`, `co_cli/agent/`
- `docs/specs/memory.md`, `docs/specs/library.md`
- `docs/PROPOSAL-memory-vs-knowledge-architecture.md` (architect's target-state design)
- `docs/reference/RESEARCH-peer-memory-survey.md` (peer system survey)
- Existing plans: `daemon-util-1-knowledge-compaction`, `dual-transcript-write`

This plan accepts the PROPOSAL's cognitive model correction (memory = timeline, knowledge = all reusable artifacts) and adds the implementation precision (file:line references, done_when criteria, safety bounds, migration strategy) needed to execute it. Consolidation/dreaming infrastructure (from the first draft) is preserved but resequenced to Phase 5, after the model correction lands.

Key design decisions:
- Hard cutover to single `knowledge_dir` in Phase 2 (one-time migration script, auto-migration at bootstrap)
- Backward-compatible reader: old `kind: memory` and `kind: article` files parse into `KnowledgeArtifact`
- New writes use canonical `kind: knowledge` format with `artifact_kind` subtype
- `search_memories()` repurposed for transcript search (not deleted)
- `search_knowledge()` becomes universal reusable-recall surface
- Feature gate on all lifecycle machinery (`consolidation_enabled=False`)
- DB is retrieval layer, disk is source layer (codifies existing `KnowledgeStore` behavior)
- `content_index` (PROPOSAL §6.2) deferred until media support is on the roadmap
- `media_asset` artifact_kind deferred — initial set is text-only

## Cross-Review — PO

Cross-reviewed against updated `docs/PROPOSAL-memory-vs-knowledge-architecture.md`. Changes incorporated:
- PROPOSAL's DB-as-retrieval-truth principle added to behavioral constraints
- Physical merge of `memory_dir` + `library_dir` → `knowledge_dir` (PROPOSAL removed the open question; confirmed with architect: memory is sessions, dirs should merge)
- `content_index` assessed and deferred (text-only scope, `sync_dir()` already handles body indexing)
- `media_asset` deferred per architect direction (not near-term)
- `artifact_kind` initial set confirmed: no `media_asset`

> Gate 1 — Review required before proceeding.
> Right model? Right sequencing? Right migration strategy?
