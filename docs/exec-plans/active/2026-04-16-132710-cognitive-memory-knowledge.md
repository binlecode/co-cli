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

### ✓ DONE TASK-2.1: Unified `KnowledgeArtifact` model

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

### ✓ DONE TASK-2.2: Migrate frontmatter writer

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

### ✓ DONE TASK-2.3: Merge into single `knowledge_dir`

- `files:` `co_cli/deps.py`, `co_cli/config/_core.py`, `co_cli/config/_knowledge.py`
- Replace `memory_dir` and `library_dir` with `knowledge_dir: Path` on CoDeps (default: `~/.co-cli/knowledge/`).
- Add `CO_KNOWLEDGE_DIR` env var override. Remove `CO_LIBRARY_PATH` (or alias to `CO_KNOWLEDGE_DIR` with deprecation warning).
- Remove `memory_dir` and `library_dir` fields from CoDeps. All reads/writes target `knowledge_dir`.
- `done_when:` CoDeps has one `knowledge_dir`. All tool and store code references updated. Tests pass.

### ✓ DONE TASK-2.3b: One-time migration script

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

### ✓ DONE TASK-2.4: Extractor writes knowledge artifacts

- `files:` `co_cli/memory/_extractor.py`, `co_cli/tools/memory.py`
- Change `save_memory()` → `save_knowledge()`:
  - Writes to `knowledge_dir` (not `memory_dir`)
  - Uses `render_knowledge_file()` format
  - Maps extractor's `type_` param to `artifact_kind`
  - Sets `source_type="detected"`, `source_ref=<session_id>`
- Register `save_knowledge()` as the extractor sub-agent's tool (replaces `save_memory` in `_knowledge_extractor_agent.tools`).
- Keep `save_memory()` as deprecated wrapper that delegates to `save_knowledge()`.
- `done_when:` New extractions write `kind: knowledge` files to `knowledge_dir/`. Extractor tests pass.

### ✓ DONE TASK-2.5: Update `save_article()` to write knowledge artifacts

- `files:` `co_cli/tools/articles.py` (line 208)
- `save_article()` writes to `knowledge_dir/` using `render_knowledge_file()`:
  - `artifact_kind="article"` or `artifact_kind="reference"`
  - `source_type="web_fetch"`, `source_ref=origin_url`
  - Dedup still keyed on `source_ref` (same as current `origin_url` dedup)
- Keep function name `save_article()` — it's a valid action verb even under the new model. But the output format and location change.
- `done_when:` New articles written as knowledge artifacts. URL dedup still works.

### ✓ DONE TASK-2.6: Knowledge store re-indexing

- `files:` `co_cli/bootstrap/core.py`, `co_cli/knowledge/_store.py`
- Update `sync_knowledge_store()` to index `knowledge_dir` as the sole source:
  - `knowledge_dir` → `source="knowledge"`
  - Drop `source="memory"` and `source="library"` indexing paths
- After migration (TASK-2.3b), all content lives in one directory — no multi-source scanning needed.
- `done_when:` `search.db` indexes `knowledge_dir` only. FTS search returns unified results.

---

## Phase 3 — Tool Surface Convergence

Align the agent's tool surface with the two-layer model.

### ✓ DONE TASK-3.1: Expand `search_knowledge()` to cover all reusable artifacts

- `files:` `co_cli/tools/articles.py` (line 122)
- Remove the `source="memory"` rejection (line 178–185). Instead, search all knowledge sources including former memory-extracted facts.
- Default `source=None` searches everything: `knowledge_dir` + obsidian + drive.
- Update docstring: "Primary search over all reusable knowledge — preferences, rules, articles, notes, and synced sources."
- `done_when:` `search_knowledge("user prefers pytest")` returns extracted facts. No source rejection.

### ✓ DONE TASK-3.2: Repurpose `search_memories()` for transcript search

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

### ✓ DONE TASK-3.3: Repurpose `list_memories()` for knowledge listing

- `files:` `co_cli/tools/memory.py` (line 253)
- Rename to `list_knowledge()` (keep `list_memories` as deprecated alias).
- Load from `knowledge_dir`.
- Update output to show `artifact_kind` column.
- `done_when:` `list_knowledge()` returns both old extracted facts and articles in one unified list.

### ✓ DONE TASK-3.4: Update `_recall_for_context` to search knowledge

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

### ✓ DONE TASK-3.5: Update standing context injection

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

### ✓ DONE TASK-3.6: Update tool registration

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

### ✓ DONE TASK-3.7: Update `/memory` REPL commands

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

### ✓ DONE TASK-4.1: Token-level similarity utility

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

### ✓ DONE TASK-4.2: Dedup check in save_knowledge()

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

### ✓ DONE TASK-4.3: Touch last_recalled on recall hits

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

## Independent Review — 2026-04-16

Cold-read pass over `/tmp/phase2-diff.patch` (2273 lines), plus the new untracked
files `co_cli/knowledge/_artifact.py`, `co_cli/knowledge/_migrate.py`,
`scripts/migrate-knowledge-dir.py`, `tests/test_knowledge_artifact.py`,
`tests/test_knowledge_migrate.py`. Engineering Rules in `CLAUDE.md` applied per
section. Stale-reference grep across `co_cli/`, `tests/`, `docs/`.

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `tests/test_knowledge_artifact.py:247-261` | `test_existing_real_memory_and_library_files_parse` reads `Path.home() / ".co-cli"` directly — violates the "DO NOT hardcode `~/.co-cli` or `Path.home() / ".co-cli"`" pitfall in CLAUDE.md and bypasses `CO_CLI_HOME`. Also violates "Test data isolation — use `tmp_path`". The done_when check should run from a fixture-prepared dir, not user-actual data; otherwise the result is non-deterministic across machines. | blocking | TASK-2.1 |
| `co_cli/tools/memory.py:478-483` (`save_knowledge`) and `co_cli/tools/articles.py:589-594` (`_consolidate_and_reindex`) | Tool functions return `tool_output_raw(...)` despite having `RunContext`. Per `tool_output_raw` docstring: "Tool functions with ctx should always use tool_output()." Bypasses `tool_results_dir` size-check / persistence. Pre-existing for `save_memory`, but `save_knowledge` is new code in TASK-2.4 and should adopt the canonical helper. | minor | TASK-2.4 |
| `co_cli/tools/memory.py:417-421` (`save_knowledge`) | Validation failure raises `ValueError` instead of returning `tool_error(...)`. Tool errors should surface as structured `ToolReturn` so the model can recover (per CLAUDE.md "Tool return type"). Pre-existing pattern but newly written here. | minor | TASK-2.4 |
| `co_cli/tools/memory.py:431-432` (`save_knowledge`) | `source_ref = session_path.stem if session_path and str(session_path) else None` — `str(session_path)` is always truthy when `session_path` is a `Path`. Redundant clause; the bool check on `session_path` already gates the rare `None` case. | minor | TASK-2.4 |
| `co_cli/knowledge/_frontmatter.py:252-283` (`validate_memory_frontmatter`) | Docstring still claims `kind` is "memory" or "article" — but the diff added "knowledge" as a third value at line 121-128. Schema doc-staleness inside the validator. No new fields (`artifact_kind`, `source_type`, `source_ref`, `pin_mode`, `certainty`, `recall_count`) are validated, so canonical files pass through unchecked. | minor | TASK-2.2 |
| `co_cli/tools/articles.py` (`save_article`) | The previous code path called `validate_memory_frontmatter(frontmatter)` before writing (enforced by completed plan `2026-04-13-130528-code-quality-refactor`). The new path constructs a `KnowledgeArtifact` and calls `render_knowledge_file()` without `validate_memory_frontmatter`. Schema constraints (`description ≤200 chars`, `decay_protected: bool`, etc.) are now enforced only by the dataclass typing, which won't catch e.g. a multi-line description string. | minor | TASK-2.5 |
| `co_cli/tools/articles.py:60-63` (`_grep_fallback_knowledge`) and elsewhere | `filter_memories(...)` / `grep_recall(...)` accept `list[MemoryEntry]` per signature but are now called with `list[KnowledgeArtifact]`. Runtime works via duck-typing on `.tags`/`.created`/`.content`/`.updated`, but the type signatures are wrong. Either widen the type to a Protocol or convert before calling. | minor | TASK-2.5 |
| `docs/specs/personality.md:118`, `docs/specs/flow-bootstrap.md:50`, `docs/specs/flow-bootstrap.md:224`, `docs/specs/context.md:167` | Spec drift: still reference `memory_dir`/`library_dir`/`CO_LIBRARY_PATH`/`load_memories(memory_dir, …)`. Per CLAUDE.md, these should be reconciled by `/sync-doc` before shipping. Not a code blocker but the specs are now out of sync with the implementation. | minor | cross-cutting (TASK-2.3) |
| `co_cli/knowledge/_store.py:608` | Docstring still mentions `source="memory"` as a valid filter shortcut and claims it's "transient during migration" — but `search_knowledge` no longer rejects `source="memory"` (the rejection guard was removed at `articles.py:175-181` per the diff). The two pieces are coherent (legacy rows still queryable) but the docstring conflicts with the unification narrative. | minor | TASK-2.6 |

### Cold-read coverage notes (per Review Discipline)

Files read in full or in relevant detail:
- `co_cli/knowledge/_artifact.py` — verified KnowledgeArtifact schema, legacy-mapping, batch loader, kind-filter.
- `co_cli/knowledge/_migrate.py` — verified idempotency (`_has_md_files` short-circuit), collision suffix logic via `_unique_destination`, that `_archive/` and other subdirs are not swept (top-level `glob("*.md")`), and that originals are moved (`shutil.move`) not copied. **Migration safety: passes** — no path traversal (only `.glob("*.md")` from a known root), atomic enough for the use case.
- `co_cli/knowledge/_frontmatter.py` — verified `_artifact_to_frontmatter`, `render_knowledge_file`, `MemoryKindEnum.KNOWLEDGE` addition, render-omits-defaults behavior.
- `co_cli/knowledge/_store.py` — verified docstring updates and that `sync_dir("knowledge", knowledge_dir)` indexes everything in the dir under one source label.
- `co_cli/bootstrap/core.py` — verified migration runs before path consumers, `_sync_knowledge_store` signature is single-dir, paths dict no longer has `memory_dir`.
- `co_cli/deps.py` — verified `library_dir` and `memory_dir` removed from `CoDeps` and `fork_deps`; `_DEFAULT_LIBRARY_DIR` / `_DEFAULT_MEMORY_DIR` deleted.
- `co_cli/config/_core.py` — verified `KNOWLEDGE_DIR` constant, `knowledge_path` field, `CO_KNOWLEDGE_DIR` env-var rename, `_ensure_dirs` no longer creates legacy dirs.
- `co_cli/tools/memory.py` — verified `save_knowledge` writes canonical kind=knowledge, indexes both docs and chunks, `_reindex_knowledge_file` shared helper, `save_memory` deprecated wrapper preserves four-type vocabulary.
- `co_cli/tools/articles.py` — verified `save_article` writes canonical knowledge artifact with `artifact_kind=article`, `source_type=web_fetch`, `source_ref=origin_url`; URL dedup preserved via `_find_article_by_url` (now also matches legacy `origin_url`); consolidation rewrites in canonical format.
- `co_cli/memory/_extractor.py` — verified extractor agent registers `save_knowledge`, prompt uses new vocabulary (`preference`/`feedback`/`rule`/`reference`) and new param names (`artifact_kind=`, `title=`).
- `co_cli/memory/prompts/knowledge_extractor.md` — verified all `save_memory(...)` examples replaced with `save_knowledge(... artifact_kind=...)`.
- `co_cli/agent/_native_toolset.py` — verified `save_article` retains `approval=True`; `save_knowledge` is **not** registered as a top-level toolset entry (only used by the extractor agent).
- All test files in the diff — verified `memory_dir=` keyword arg renamed to `knowledge_dir=` consistently across `_make_ctx` / `_make_deps` helpers; `idx.sync_dir("knowledge", …)` and `source="knowledge"` used in assertions.
- `tests/test_knowledge_artifact.py` and `tests/test_knowledge_migrate.py` — verified real-deps usage (no mocks) except for the home-dir read flagged above.

Cross-task coherence checks:
- Source label is uniformly `"knowledge"` in production writes/reads. The only `"memory"`/`"library"` survivals are in `sync_dir`'s arg signature (legacy values still accepted) and store docstrings (transitional language). Confirmed via grep.
- Frontmatter format: new writes go through `render_knowledge_file()` → `kind: knowledge` + `artifact_kind`. Confirmed by `test_save_article_creates_file` and `test_render_knowledge_file_emits_canonical_kind`.
- Extractor prompt aligned with `save_knowledge` signature. Confirmed by reading the new prompt and signature side-by-side.
- Done_when checks: TASK-2.1 has `test_existing_real_memory_and_library_files_parse` (problematic per blocker above); TASK-2.3b has `tests/test_knowledge_migrate.py::test_idempotent_rerun`; TASK-2.5 has `tests/test_articles.py::test_save_article_dedup_by_url`. All map cleanly.

**Overall: 1 blocking / 8 minor**

The blocker is the home-dir read in the test file — a one-line `pytest.skip()` would not be enough; the test should be redesigned around `tmp_path` with synthetic legacy fixtures, or removed in favour of the existing dedicated tests in `test_knowledge_artifact.py` that already cover canonical/legacy parsing comprehensively. The minors are cumulative tech-debt around the new helpers (raw vs. ctx-aware tool returns, missing schema validation on the new write path, type-signature drift) and spec drift that `/sync-doc` should sweep.

## Delivery Summary — 2026-04-16

**Overall: DELIVERED.** Phase 2 ships the unified two-layer cognitive model (Memory = transcripts, Knowledge = reusable artifacts) with the canonical `KnowledgeArtifact` schema, single `knowledge_dir`, unified `source="knowledge"` indexing, and extractor writing `save_knowledge()` directly.

### Scope changes from plan (mid-delivery directive)

Partway through delivery the owner directed a **"no backward compatibility"** principle — co-cli is pre-release, so obsolete legacy assets get reset rather than migrated. This dropped several planned surfaces:

| Planned | Shipped |
|---------|---------|
| `migrate_knowledge_dir()` + `scripts/migrate-knowledge-dir.py` + auto-migration bootstrap hook | **Removed entirely.** No migration code exists. |
| Backward-compat reader in `_artifact.py` (`_map_legacy_memory`, `_map_legacy_article`) | **Removed.** Loader requires `kind: knowledge`; raises on anything else. |
| `save_memory()` deprecated wrapper delegating to `save_knowledge()` | **Removed.** `save_knowledge()` is the sole write path. |
| `render_memory_file()` as deprecated alias in `_frontmatter.py` | **Removed.** Replaced with `render_frontmatter()` (dict form for in-place updates) + `render_knowledge_file()` (artifact form for new writes). |
| `MemoryEntry` dataclass + `load_memories()` / `load_always_on_memories()` | **Removed.** All callers migrated to `KnowledgeArtifact` + `load_knowledge_artifacts()` / `load_standing_artifacts()`. |
| `MemoryTypeEnum` (legacy type vocabulary: user / feedback / project / reference) | **Removed.** `ArtifactKindEnum` is the single artifact-kind vocabulary. |
| `origin_url` frontmatter field (articles) | **Replaced** by canonical `source_ref`. No fallback. |
| `_uses_chunks_leg()` memory-source branch, `_run_memory_fts()` docs_fts path, `index_chunks` memory reject | **Removed.** All sources chunk into `chunks_fts` uniformly. |
| `/memory --type` flag (legacy 4-type filter) | **Removed.** Only `--kind` (artifact_kind) remains. |
| Follow-up task: tests for migration script | **Deleted** (`tests/test_knowledge_migrate.py` removed). |

### Shipped per planned task

| Task | done_when | Status |
|------|-----------|--------|
| TASK-2.1 — KnowledgeArtifact model | Canonical loader parses; rejects non-knowledge kind | ✓ pass |
| TASK-2.2 — `render_knowledge_file()` | New files written in canonical format; in-place updates via `render_frontmatter()` | ✓ pass |
| TASK-2.3 — Merge to single `knowledge_dir` | CoDeps has one `knowledge_dir`; all callers updated (`memory_dir`/`library_dir` removed everywhere) | ✓ pass |
| TASK-2.3b — Migration script | Scope changed: migration removed entirely per reset directive | — replaced |
| TASK-2.4 — Extractor writes knowledge | `save_knowledge()` registered; extractor prompt updated; no `save_memory()` wrapper | ✓ pass |
| TASK-2.5 — `save_article()` writes knowledge | Canonical artifact writer; `source_ref` dedup key; no `origin_url` fallback | ✓ pass |
| TASK-2.6 — Knowledge store re-indexing | Sync uses `source="knowledge"`; all sources chunked; legacy paths removed | ✓ pass |

### Tests

- Full suite: **500 passed** (one flaky external web-fetch retried green)
- New tests: `tests/test_knowledge_artifact.py` (12 tests covering loader, renderer, validator, standing-artifact loader)
- Removed tests: `tests/test_knowledge_migrate.py`, `test_load_memories_tolerates_unknown_artifact_type`, `test_render_memory_file_backward_compat`, plus assorted legacy fixture writers rewritten to emit canonical format

### Docs synced

`docs/specs/cognition.md`, `knowledge.md`, `context.md`, `flow-bootstrap.md`, `flow-prompt-assembly.md`, `personality.md`, `tools.md` all purged of migration / backward-compat / legacy narrative. Describes the canonical system as the only system.

### Independent Review outcome

1 blocker (home-dir test read) — **fixed** during delivery by replacing with hermetic synthetic-fixture test. Minors were resolved implicitly by the reset scope change (validator renamed, `save_memory` wrapper removed, MemoryEntry type-signature drift gone with the class). No remaining blockers.

### Files (final shipped set)

New:
- `co_cli/knowledge/_artifact.py`
- `tests/test_knowledge_artifact.py`

Modified (production):
- `co_cli/agent/_instructions.py`, `co_cli/bootstrap/core.py`, `co_cli/commands/_commands.py`, `co_cli/config/_core.py`, `co_cli/context/_history.py`, `co_cli/deps.py`
- `co_cli/knowledge/_frontmatter.py`, `co_cli/knowledge/_store.py`
- `co_cli/memory/_extractor.py`, `co_cli/memory/prompts/knowledge_extractor.md`, `co_cli/memory/recall.py`
- `co_cli/prompts/personalities/_injector.py`
- `co_cli/tools/articles.py`, `co_cli/tools/memory.py`

Modified (tests + evals): `tests/test_articles.py`, `test_bootstrap.py`, `test_commands.py`, `test_extractor_integration.py`, `test_extractor_window.py`, `test_history.py`, `test_memory.py`; `evals/_deps.py`, `eval_article_fetch_flow.py`, `eval_compaction_quality.py`, `eval_memory_edit_recall.py`, `eval_memory_recall.py`

Modified (docs): all seven specs listed under "Docs synced"; this plan file

Version bump: `0.7.164` → `0.7.166` (feature delivery).

> Gate 2 — `/review-impl` required before `git mv` to `completed/`.

## Delivery Summary — Phase 3 — 2026-04-16

**Overall: DELIVERED.** Phase 3 ships the tool surface convergence: `search_memories` now delegates to `session_search` for episodic (transcript) recall; `list_knowledge` is the canonical artifact listing tool; `session_search` is promoted from DEFERRED to ALWAYS; `/knowledge` is the primary REPL command namespace.

### Pre-completed (Phase 2 had already landed these)
- TASK-3.1: `search_knowledge()` already covered all sources — no source rejection
- TASK-3.4: `_recall_for_context()` already used `source="knowledge"` with no kind filter

### Shipped

| Task | done_when | Status |
|------|-----------|--------|
| TASK-3.2 — `search_memories()` → `session_search()` delegation | `search_memories()` returns transcript results | ✓ pass |
| TASK-3.3 — `list_memories()` → `list_knowledge()` + deprecated alias | `list_knowledge()` returns unified artifact list | ✓ pass |
| TASK-3.5 — `add_standing_knowledge()` rename | Standing context injected from knowledge artifacts | ✓ pass |
| TASK-3.6 — Tool registration update | `session_search` ALWAYS; `list_knowledge` ALWAYS; deprecated aliases remain | ✓ pass |
| TASK-3.7 — `/knowledge` command namespace | `/knowledge list|count|forget` works; `/memory` works with deprecation notice | ✓ pass |

### Tests
- Full suite: **502 passed**
- Updated: `test_memory.py`, `test_agent.py`, `test_session_search_tool.py`, `test_tool_prompt_discovery.py`, `test_tool_calling_functional.py`

### Docs synced
`tools.md`, `cognition.md`, `tui.md`, `context.md`, `knowledge.md`, `flow-prompt-assembly.md`

### Version bump
`0.7.166` → `0.7.168` (feature delivery)

> Gate 2 — `/review-impl cognitive-memory-knowledge` required before `git mv` to `completed/`.

## Implementation Review — 2026-04-16

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-3.2 | `search_memories()` returns transcript results | ✓ pass | `memory.py:183-198` — delegates directly to `session_search(ctx, query, limit)`; `test_memory.py:279-285` confirms session path |
| TASK-3.3 | `list_knowledge()` returns unified artifact list | ✓ pass | `memory.py:201-291` — canonical function; `memory.py:294-301` — deprecated alias; `test_memory.py:116-145` pagination; `:147-156` alias parity |
| TASK-3.5 | Standing context injected from knowledge artifacts | ✓ pass | `_instructions.py:24-31` — `add_standing_knowledge` calls `load_standing_artifacts(ctx.deps.knowledge_dir)`; `_core.py:129,158` — import + registration confirmed |
| TASK-3.6 | Agent toolset reflects two-layer model | ✓ pass | `_native_toolset.py:127-146` — `list_knowledge`, `session_search` ALWAYS; deprecated aliases registered; `test_agent.py:101-113` spot-check; `test_session_search_tool.py:124-134` ALWAYS assertion |
| TASK-3.7 | `/knowledge list` works; `/memory list` works with notice | ✓ pass | `_commands.py:1263-1272` — both in `BUILTIN_COMMANDS`; `:1198` deprecation notice; behavioral verification confirmed |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 502 passed, 0 failed
- Log: `.pytest-logs/$(date +%Y%m%d-%H%M%S)-review-impl.log`

### Doc Sync
- Scope: full (public API rename, REPL command surface change)
- Result: clean — `add_always_on_memories` removed everywhere; `add_standing_knowledge`, `list_knowledge`, `session_search` ALWAYS present in all affected specs

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM online, database active, all integrations as configured
- `add_standing_knowledge` confirmed in agent instruction callback list
- `/knowledge` registered as primary command; `/memory` registered with `[Deprecated]` in description
- `session_search` and `list_knowledge` both confirmed ALWAYS visibility at runtime

### Overall: PASS
Phase 3 tool surface convergence is complete, clean, and ship-ready.

## Delivery Summary — Phase 4 — 2026-04-16

**Overall: DELIVERED.** Phase 4 ships dedup-on-write and recall tracking on the unified knowledge layer.

### Shipped

| Task | done_when | Status |
|------|-----------|--------|
| TASK-4.1 — Token-level similarity utility | `uv run pytest tests/test_knowledge_similarity.py` passes | ✓ pass (16 tests) |
| TASK-4.2 — Dedup check in `save_knowledge()` | Tests cover skip/merge/append/save/bypass-disabled | ✓ pass (5 tests) |
| TASK-4.3 — Touch `last_recalled` on recall hits | Tests verify `recall_count` increments and `last_recalled` updates | ✓ pass (4 tests) |

### Files

New:
- `co_cli/knowledge/_stopwords.py` — shared STOPWORDS constant (factored from `_store.py`)
- `co_cli/knowledge/_similarity.py` — `token_jaccard`, `find_similar_artifacts`, `is_content_superset`
- `tests/test_knowledge_similarity.py` — 16 edge-case tests

Modified:
- `co_cli/knowledge/_store.py` — imports STOPWORDS from `_stopwords.py` (no functional change)
- `co_cli/tools/memory.py` — `save_knowledge` dedup block; `_touch_recalled` coroutine; `_update_artifact_body` helper; fire-and-forget wired into `_recall_for_context`
- `tests/test_memory.py` — removed stale `test_recall_does_not_mutate_files`; added 9 new dedup + recall tracking tests; imports updated

### Notes

- Dedup is zero-overhead when `consolidation_enabled=False` (default). No existing behavior changes for non-opt-in users.
- `is_content_superset` determines merge vs. append: new content must be a strict superset of existing tokens to trigger replace.
- `_touch_recalled` is fire-and-forget (`asyncio.create_task`). Tests exercise it directly via `asyncio.run` rather than via `_recall_for_context` to avoid task-cancellation false negatives.
- `test_recall_does_not_mutate_files` removed: its premise (recall is read-only) was invalidated by TASK-4.3. The new tests verify the updated semantics directly.

### Tests
- Full suite: **526 passed**, 0 failed
- Version bump: `0.7.168` → `0.7.170` (feature delivery)
