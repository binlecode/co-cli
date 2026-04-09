# RESEARCH: letta Memory Architecture — Deep Scan

Source: `~/workspace_genai/letta` (MemGPT/Letta agent framework, v0.16.7)
Scan date: 2026-04-06

---

## 1. Architecture Overview

Two-tier memory model:
- **Core blocks**: labeled in-context text sections, always in the LLM prompt
- **Archival passages**: vector-indexed long-term storage, retrieved via tool call

**Memory class hierarchy** (`letta/schemas/memory.py`):
- `Memory` (lines 68–803): base class, holds blocks + file_blocks, compile() renders to prompt
- `BasicBlockMemory` (lines 783–838): adds `core_memory_append` / `core_memory_replace`
- `ChatMemory` (lines 840+): extends with default "persona" + "human" blocks

---

## 2. Core Block System

### BaseBlock schema (`letta/schemas/block.py:13–210`)

| Field | Line | Type | Purpose |
|-------|------|------|---------|
| `value` | 19 | `str` | Block content |
| `limit` | 20 | `int` (default 100,000) | Character limit, enforced by Pydantic validator (lines 51–64) |
| `label` | 33 | `Optional[str]` | Semantic label ("human", "persona", custom) |
| `read_only` | 36 | `bool` | Agent cannot modify if True |
| `description` | 39 | `Optional[str]` | Block description |
| `metadata` | 40 | `Optional[dict]` | Arbitrary metadata |
| `hidden` | 41 | `Optional[bool]` | Visibility flag |
| `is_template` | 25 | `bool` | Saved template flag |
| `project_id` | 22 | `Optional[str]` | Organization scoping |

`CORE_MEMORY_BLOCK_CHAR_LIMIT = 100,000` (`constants.py:435`)

### Block ORM (`letta/orm/block.py:20–116`)

| Field | Line | Purpose |
|-------|------|---------|
| `version` | 56–58 | Optimistic locking counter (`__mapper_args__["version_id_col"]` at line 61) |
| `current_history_entry_id` | 53–55 | FK to `block_history.id` — pointer to current snapshot |
| All BaseBlock fields | 35–50 | Mirrored as ORM columns |

**Relationships** (lines 64–92):
- `agents` — many-to-many via `blocks_agents`
- `identities` — many-to-many via `identities_blocks`
- `groups` — many-to-many via `groups_blocks`
- `tags` — relationship to `BlocksTags`, cascade delete

### Standard block labels
- `"human"` — user information (line 120)
- `"persona"` — agent personality (line 127)
- `"system/human"`, `"system/persona"` — git-memory variants (`memory.py:96–99`)
- Custom labels allowed (no fixed enum)

---

## 3. Block Operations

### memory() dispatcher (`letta/functions/function_sets/base.py:10–67`)

```
memory(agent_state, command, ...)
  command ∈ {create, str_replace, insert, delete, rename}
```

Stub raises `NotImplementedError` (line 68); actual dispatch in agent handler.

### Operation details

**str_replace** (`base.py:311–388`):
```
 → find exact match of old_string in block
 → validate old_string is unique (no multiline prefix)
 → replace with new_string
 → agent_state.memory.update_block_value()
```

**insert** (`base.py:391–450`):
```
 → split block into lines
 → insert new_string at insert_line (0=beginning, -1=end)
 → agent_state.memory.update_block_value()
```

**rethink** (`base.py:488–517`):
```
 → complete rewrite of block content
 → creates block if not exists
```

**apply_patch** (`base.py:453–485`):
```
 → unified diff-style patching
 → multi-block mode: *** Add Block:, *** Update Block:, *** Delete Block:, *** Move to:
 → single-block mode: patch with label parameter
```

### Guards & validation
- `read_only` check: raises `ValueError` if block marked read-only (`core_tool_executor.py:320–321`)
- Character limit: Pydantic validator on `BaseBlock.value` (`block.py:51–64`)
- Regex validation: rejects line number prefixes in edit strings (`base.py:343–356`)

---

## 4. Block Mutation History

### BlockHistory ORM (`letta/orm/block_history.py:12–48`)

| Field | Line | Purpose |
|-------|------|---------|
| `id` | 25 | PK (format: `block_hist-<uuid>`) |
| `block_id` | 40 | FK to `block.id`, CASCADE delete |
| `sequence_number` | 46 | Monotonic counter per block_id, starting from 1 |
| `value` | 30 | Snapshot of block content |
| `limit` | 31 | Snapshot of limit |
| `label` | 29 | Snapshot of label |
| `description` | 28 | Snapshot of description |
| `metadata_` | 32 | JSON snapshot of metadata |
| `actor_type` | 36 | Type of editor (not FK, allows user deletion) |
| `actor_id` | 37 | ID of editor |

**Index** (line 19): `(block_id, sequence_number)` UNIQUE

**Lifecycle**:
- Triggered when `Block.version` increments (optimistic locking via SQLAlchemy)
- Snapshot saved atomically with block mutation
- `Block.current_history_entry_id` points to latest snapshot

**Migration**: `alembic/versions/bff040379479_add_block_history_tables.py` (lines 23–74)

---

## 5. Archival Memory System

### archival_memory_insert (`base.py:164–191`)

```python
async def archival_memory_insert(
    self: "Agent",
    content: str,
    tags: Optional[list[str]] = None
) -> Optional[str]
```
- Stores fact/summary to long-term memory with semantic embedding
- Creates `ArchivalPassage` with embedding via `insert_passage()` (`passage_manager.py:543–637`)

### archival_memory_search (`base.py:194–243`)

```python
async def archival_memory_search(
    self: "Agent",
    query: str,
    tags: Optional[list[str]] = None,
    tag_match_mode: Literal["any", "all"] = "any",
    top_k: Optional[int] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
) -> Optional[str]
```
- Semantic similarity search via `search_agent_archival_memory_async()` (`agent_manager.py:2534–2643`)
- Tag filtering: `any` (OR) or `all` (AND) via `PassageTag` junction
- Datetime filtering: ISO 8601, timezone-aware, inclusive both ends

### ArchivalPassage ORM (`letta/orm/passage.py:76–105`)

| Field | Line | Purpose |
|-------|------|---------|
| `text` | 28 (BasePassage) | Passage content |
| `embedding` | 38/40 | pgvector `Vector(4096)` or SQLite `CommonVector` |
| `tags` | 32 (BasePassage) | JSON column (dual storage with junction) |
| `metadata_` | 30 (BasePassage) | JSON metadata |
| `passage_tags` | 82–84 | Relationship to `PassageTag` junction, cascade delete |
| `archive_id` | 80 (ArchiveMixin) | FK to owning archive |

**Indexes** (lines 91–104):
- `(organization_id, archive_id)` — agent archive lookup
- `(created_at, id)` — range queries
- `archive_id` — archive filtering

### SourcePassage vs ArchivalPassage (`passage.py:48–74` vs `76–105`)
- `SourcePassage`: extends `FileMixin` + `SourceMixin` — for file-uploaded documents
- `ArchivalPassage`: extends `ArchiveMixin` — for agent-inserted passages with tags

### PassageTag junction (`letta/orm/passage_tag.py:14–54`)
- Fields: `id`, `tag`, `passage_id` (FK, CASCADE), `archive_id` (denormalized), `organization_id`
- UNIQUE(passage_id, tag) — one tag per passage
- Dual storage: JSON column for fast read + junction for efficient aggregation queries

### Archive ORM (`letta/orm/archive.py:24–98`)

| Field | Line | Purpose |
|-------|------|---------|
| `name` | 41 | Archive name |
| `description` | 42 | Optional description |
| `vector_db_provider` | 43 | NATIVE (pgvector/SQLite) or TPUF (Turbopuffer) |
| `embedding_config` | 49 | Embedding model metadata |
| `metadata_` | 51 | Arbitrary metadata |

**ArchivesAgents junction** (`archives_agents.py:14–33`):
- `agent_id`, `archive_id`, `is_owner` (bool)
- UNIQUE(agent_id) — currently one archive per agent

---

## 6. Memory Injection into Prompt

### Memory.compile() (`memory.py:688–732`)

```
Memory.compile(tool_usage_rules, sources, ...)
 → check agent_type + model provider
     React/Workflow agents → skip blocks                     line 705
     git-enabled → _render_memory_blocks_git()               line 708
     Anthropic + specific types → _render_memory_blocks_line_numbered()  lines 700–702
     default → _render_memory_blocks_standard()              line 712
 → append tool_usage_rules, sources sections
 → return compiled prompt string
```

### Rendering helpers
- `_render_memory_blocks_standard()` (lines 143–162): wraps blocks in `<memory_blocks>` XML
- `_render_memory_blocks_git()`: structured self + memory for git-backed agents
- `_render_memory_blocks_line_numbered()`: annotated with line numbers (Anthropic-optimized)

### Injection point
```
base_agent.py:134–139
 → curr_memory_str = agent_state.memory.compile()           line 134
 → memory_with_sources = curr_memory_str                     line 159
 → passed to PromptGenerator
```

---

## 7. Storage Backends

**DatabaseChoice enum** (`settings.py:273–275`): `POSTGRES | SQLITE`

**PostgreSQL + pgvector**:
- Embedding column: `Vector(MAX_EMBEDDING_DIM=4096)` (`passage.py:38`)
- Full vector similarity search
- `MAX_EMBEDDING_DIM = 4096` (`constants.py:93`) — do NOT change without DB reset

**SQLite + CommonVector**:
- Custom `CommonVector` column type (`passage.py:40`)
- Padding logic in validator (lines 51–77): pads to 4096 for pgvector, conditional for Turbopuffer

**Turbopuffer (TPUF)**:
- External vector DB option via `Archive.vector_db_provider = TPUF`
- Dual-write in `insert_passage()` (`passage_manager.py:606–633`)

**Database selection** (`settings.py:492–493`):
- `letta_pg_uri` set → POSTGRES
- Otherwise → SQLITE (default)

**Migration**: Alembic (`alembic/versions/`)

---

## 8. Agent-Block Relationship

### blocks_agents junction (`letta/orm/blocks_agents.py:7–35`)

| Field | Purpose |
|-------|---------|
| `agent_id` | FK to agents.id, CASCADE |
| `block_id` | Part of composite FK to Block(id, label) |
| `block_label` | Part of composite FK |

**Constraints** (lines 11–29):
- UNIQUE(agent_id, block_label) — one label per agent
- UNIQUE(agent_id, block_id) — no duplicate block assignments
- Deferrable initial=IMMEDIATE for safer transactions

**Sharing**: blocks can be shared across agents, identities, and groups via their respective junction tables.

---

## 9. Consolidation & Cleanup

- **No block-level consolidation** — confirmed by grep for `consolidat`, `compact`, `dedup` in block-related code
- **No automatic garbage collection** — requires explicit deletion
- **Message history compaction** exists but operates on conversation history, not blocks
- **Block deletion**: soft-delete via `is_deleted` flag (inherited from SqlalchemyBase). CASCADE to BlockHistory on hard delete
- **Archive deletion**: cascades to archival_passages + passage_tags

---

## 10. Configuration

### Embedding config (`letta/schemas/embedding_config.py:8–89`)
- `embedding_endpoint_type`: provider (openai, anthropic, bedrock, google, ollama, etc.)
- `embedding_model`: model name (e.g., text-embedding-3-small)
- `embedding_dim`: vector dimension (default 1536)
- `embedding_chunk_size`: default 300 tokens
- `batch_size`: default 32

### Environment variables
- `LETTA_PG_DB`, `LETTA_PG_USER`, `LETTA_PG_PASSWORD`, `LETTA_PG_HOST`, `LETTA_PG_PORT`
- `LETTA_SQLITE_PATH`
- `LETTA_DEFAULT_EMBEDDING_HANDLE`
- No memory-specific env vars

### Constants (`constants.py`)
- `CORE_MEMORY_BLOCK_CHAR_LIMIT = 100,000` (line 435)
- `MAX_EMBEDDING_DIM = 4,096` (line 93)
- `DEFAULT_HUMAN_BLOCK_DESCRIPTION` (line 110)
- `DEFAULT_PERSONA_BLOCK_DESCRIPTION` (line 109)

---

## 11. Gap Analysis: letta vs co-cli

### letta has, co-cli does not

| Gap | letta | co-cli status | Severity |
|-----|-------|---------------|----------|
| **In-context core blocks** | Labeled text blocks always in prompt, rendered via `Memory.compile()` (`memory.py:688–732`). Direct character-level editing (str_replace, insert, rename). 100K char limit per block | co-cli injects up to 5 `always_on` memories as text (`agent.py:324–332`). No character-level editing of injected content; edits go through `update_memory` str_replace on file | **Medium** — letta's blocks are live-editable in prompt; co-cli's always-on are read-only in context |
| **Full mutation audit trail** | `BlockHistory` ORM (`block_history.py:12–48`) with sequence-numbered snapshots, actor tracking, optimistic locking via `Block.version`. Every mutation recorded | co-cli tracks `updated` timestamp + `provenance` only. No old/new value tracking, no sequence numbers, no actor attribution | **Medium** — letta can undo/replay block changes; co-cli cannot |
| **Structured passage tagging** | `PassageTag` junction table (`passage_tag.py:14–54`) with UNIQUE(passage_id, tag) + denormalized `archive_id`. Dual storage (JSON + junction) for fast queries. `tag_match_mode` (any/all) on search | co-cli has `tags` list in frontmatter (flat file, `_frontmatter.py:168–174`). FTS5 search supports tag filtering but no junction-table-level optimization | **Low** — co-cli's tag filtering works; letta's dual storage is a query optimization, not a capability gap |
| **Datetime range filtering on archival** | `archival_memory_search` accepts `start_datetime` / `end_datetime` (ISO 8601, timezone-aware, `base.py:194–243`) | co-cli has `created_after` / `created_before` filters on search tools (`memory.py:358–572`). Functionally equivalent | **None** — both support temporal filtering |
| **Multi-agent block sharing** | Blocks shared across agents via `blocks_agents` junction (`blocks_agents.py:7–35`). Identities and groups can also share blocks | co-cli is single-agent. No block sharing mechanism | **Not applicable** — co-cli is single-agent CLI |
| **Block templates** | `is_template` flag (`block.py:25`) + template lifecycle fields. Saved templates can be reused across agents | No template system for memories | **Low** — useful for multi-agent setups, not for single-user CLI |

### co-cli has, letta does not

| Advantage | co-cli | letta status |
|-----------|--------|--------------|
| **Temporal decay scoring** | 0.6×relevance + 0.4×decay (exponential half-life, `memory.py:457–462`). Recent memories scored higher than old | No decay. Archival search uses pure vector similarity. Core blocks have no age weighting |
| **Write-time dedup** | Agent-based upsert via `check_and_save()` (`_save.py:64–100`) before write | No dedup at any level. Agent manually decides via memory() tool |
| **Automatic retention** | `enforce_retention()` (`_retention.py:16–52`) prunes oldest non-protected when > 200 | No automatic retention. Manual deletion only |
| **Cross-source knowledge search** | `search_knowledge` (`articles.py:161–309`) across memory, articles, Obsidian, Google Drive | Single-source: archival passages only. No cross-archive or external source search |
| **FTS5 text search** | BM25 keyword search via SQLite FTS5 (`_store.py:213`), complementing semantic search | Archival search is vector-only. No keyword/BM25 fallback |
| **Background auto-extraction** | Post-turn signal detection (`_extractor.py:141–225`) with tag-based admission control | No automatic extraction. Agent must explicitly call `archival_memory_insert()` |
| **Consolidation** | Write-time agent-based upsert merges duplicates. autoDream equivalent not needed — dedup happens at write time | No consolidation or dedup at any level |
