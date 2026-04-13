# Research: Memory System Deep Scan — `hermes-agent`

Deep scan of the memory subsystem in the local checkout at `~/workspace_genai/hermes-agent`. All claims are code-backed with file:line citations. This document covers architecture, lifecycle, retrieval, agent-loop integration, and configuration — with a closing gap/convergence analysis relevant to co-cli.

Scan date: 2026-04-11.

## Sources Scanned

- `tools/memory_tool.py` — built-in file-backed memory store (561 lines)
- `agent/memory_manager.py` — orchestrator bridging built-in and external providers (363 lines)
- `agent/memory_provider.py` — abstract base class for all memory providers (232 lines)
- `run_agent.py` — agent loop integration: init, prefetch, inject, sync, hooks (~10k lines total; relevant excerpts cited below)
- `hermes_cli/config.py` — memory configuration schema
- `plugins/memory/__init__.py` — plugin discovery and loading (318 lines)
- `plugins/memory/honcho/__init__.py` — Honcho AI-native external provider (600+ lines)
- `plugins/memory/holographic/holographic.py` — HRR phase encoding algebra (204 lines)
- `plugins/memory/holographic/store.py` — SQLite fact store with entity resolution (500+ lines)
- `plugins/memory/holographic/retrieval.py` — hybrid search: FTS5 + Jaccard + HRR + trust (594 lines)

---

## 1. Architecture Overview

Hermes uses a **two-tier memory architecture**: one built-in file-backed tier that is always active, and one optional external plugin tier (at most one active at a time).

```
┌─────────────────────────────────────────────────────────┐
│                     AIAgent (run_agent.py)              │
│                                                         │
│  ┌──────────────────┐    ┌───────────────────────────┐  │
│  │  MemoryStore     │    │  MemoryManager            │  │
│  │  (built-in)      │    │  (external orchestrator)  │  │
│  │                  │    │                           │  │
│  │  MEMORY.md       │    │  ┌─────────────────────┐  │  │
│  │  USER.md         │    │  │  MemoryProvider      │  │  │
│  │  (file-backed)   │    │  │  (honcho / hrr /     │  │  │
│  └──────────────────┘    │  │   mem0 / custom)     │  │  │
│         ↓ snapshot       │  └─────────────────────┘  │  │
│    frozen at session      └───────────────────────────┘  │
│    start → system prompt        ↓ prefetch per turn      │
│                             injected into user message   │
└─────────────────────────────────────────────────────────┘
```

**Built-in tier** (`tools/memory_tool.py`):
- Two Markdown files: `MEMORY.md` (agent notes, 2200-char limit) and `USER.md` (user profile, 1375-char limit)
- Loaded once at session start; content frozen in a system prompt snapshot
- Snapshot never refreshes mid-session — preserves Anthropic prefix cache across all turns
- Writes go to disk immediately; model only sees updated content on next session or context compression
- Exposed as a `memory` tool with actions: `add`, `replace`, `remove`, `read`

**External tier** (`agent/memory_manager.py` + `plugins/memory/`):
- One active plugin at a time: Honcho, holographic/HRR, mem0, or custom
- Dynamic per-turn prefetch: query constructed from user message, results injected into that turn's API call
- Lifecycle hooks: `on_memory_write`, `sync_all`, `on_pre_compress`, `on_session_end`

---

## 2. Built-in Memory: File Format and Schema

**Files:** `tools/memory_tool.py` lines 44–97

Storage path: `$HERMES_HOME/memories/MEMORY.md` and `USER.md`.

**Entry format:** entries are delimited by section sign `§`:
```
§
[entry content — free text, often one semantic fact per entry]
§
```

**MemoryStore class** (`memory_tool.py:100`):
```
memory_entries: List[str]        # entries from MEMORY.md
user_entries: List[str]          # entries from USER.md
memory_char_limit: int = 2200    # ~800 tokens
user_char_limit: int = 1375      # ~500 tokens
_system_prompt_snapshot: Dict    # frozen state captured at load time
```

**Atomicity and locking** (`memory_tool.py:139–153`, `408–436`):
- Writes use temp file + `os.replace()` for crash safety
- `.lock` file per memory file prevents concurrent write corruption

**Content safety scan** (`memory_tool.py:85–97`):
- Blocks invisible Unicode characters
- Rejects prompt injection patterns: "ignore previous instructions", "you are now", etc.
- Blocks exfiltration attempts: `curl`/`wget` with `$SECRETS`-style substitution
- Blocks SSH backdoor and credential-theft patterns

**Deduplication at load time** (`memory_tool.py:128–129`, `168`):
- `list(dict.fromkeys(entries))` — order-preserving dedup on load, keeps first occurrence
- Exact duplicate check on every `add` action before writing (`memory_tool.py:217`)

---

## 3. Built-in Memory: Lifecycle

**Add** (`memory_tool.py:457–460`):
1. Scan content for injection threats (line 205)
2. Exact duplicate check against all existing entries (line 217)
3. Enforce character budget; reject if `current + new > limit` (lines 224–235)
4. Acquire file lock, write with temp-file atomicity (lines 209–211, 408–436)
5. Return JSON: `{usage_pct, entry_count, chars_used, chars_limit}`

**Replace** (`memory_tool.py:462–467`):
1. Substring match against all entries (line 261)
2. If multiple matches exist and they differ: reject with error (lines 266–276)
3. Re-check character budget with new content (lines 282–293)
4. Atomic write

**Remove** (`memory_tool.py:469–472`):
- Same substring-match logic; no budget re-check needed

**No automatic expiry.** Memory persists until the agent or user explicitly removes it. No TTL, no age-based pruning.

**Frozen snapshot pattern:**
- `load_from_disk()` captures content into `_system_prompt_snapshot` once at session start (`memory_tool.py:119–135`)
- All subsequent `build_for_system_prompt()` calls return the frozen snapshot, not the live file
- Mid-session writes update disk only — model does not see them until next session
- Snapshot is rebuilt only on context compression or session reset

---

## 4. Holographic/HRR External Provider: Storage Schema

**Files:** `plugins/memory/holographic/store.py`

SQLite with WAL mode. Schema (lines 16–76):

```sql
CREATE TABLE facts (
    fact_id     INTEGER PRIMARY KEY,
    content     TEXT NOT NULL UNIQUE,
    category    TEXT,
    tags        TEXT,
    trust_score REAL DEFAULT 0.5,    -- [0, 1]; adjusted via feedback
    retrieval_count INTEGER,
    helpful_count   INTEGER,
    hrr_vector  BLOB,                -- serialized HRR phase vector
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP
);

CREATE TABLE entities (
    entity_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    entity_type TEXT,
    aliases     TEXT
);

CREATE TABLE fact_entities (         -- many-to-many
    fact_id    INTEGER REFERENCES facts,
    entity_id  INTEGER REFERENCES entities
);

CREATE VIRTUAL TABLE facts_fts       -- FTS5 for BM25-style text search
    USING fts5(content, tags);

CREATE TABLE memory_banks (          -- HRR superposition vectors per category
    bank_id    INTEGER PRIMARY KEY,
    bank_name  TEXT UNIQUE,
    vector     BLOB,                 -- bundled (superposition) of all facts in category
    dim        INTEGER,
    fact_count INTEGER,
    updated_at TIMESTAMP
);
```

**FTS5 triggers** (`store.py:48–66`): incremental insert/update/delete sync to `facts_fts`.

**B-tree indexes**: `trust_score DESC`, `category`, `entities.name`.

**HRR vectors** (`holographic.py:43–67`):
- Phase encoding: each dimension is an angle in `[0, 2π)` (not complex numbers)
- Deterministic from SHA-256: `encode_atom(word, dim=1024)`
- Fact vector: structured binding of content role + entity roles (lines 135–160)
- Memory bank: superposition (bundling = addition mod 2π) of all fact vectors in a category

---

## 5. Holographic/HRR External Provider: Lifecycle

**add_fact** (`store.py:159–174`):
1. UNIQUE constraint: on duplicate content, returns existing `fact_id` without modification
2. Extract entities via 4 regex patterns: capitalized phrases, quoted terms, "aka" aliases, generic regex
3. Compute HRR vector: `structured_encode(content, entities)`
4. Rebuild memory bank (re-bundle all facts in category)
5. Return `fact_id`

**update_fact** (`store.py:238–300`):
- Partial field updates: `content`, `trust_delta`, `tags`, `category`
- If content changes: re-extract entities, recompute HRR vectors, rebuild bank
- Trust delta clamped to `[0, 1]`: asymmetric feedback via `record_feedback()` (lines 349–388)
  - `helpful=True` → `trust += 0.05`
  - `helpful=False` → `trust -= 0.10`
- `retrieval_count` incremented on every search (`store.py:231`)

**remove_fact**: cascading delete — `facts` → `fact_entities` → `facts_fts`; triggers bank rebuild.

**Temporal decay** (`retrieval.py:569–593`):
- Optional: `score × 0.5^(age_days / half_life_days)`
- Disabled by default (`half_life=0`)

---

## 6. Holographic/HRR External Provider: Retrieval

**Files:** `plugins/memory/holographic/retrieval.py`

**Hybrid search (default, lines 30–110):**
1. FTS5 candidates: `limit × 3` from full-text index (line 66)
2. Jaccard rerank: token overlap scoring over FTS5 hits (lines 560–567)
3. HRR similarity: phase cosine distance (holographic.py lines 101–108)
4. Combine: `0.4 × FTS5 + 0.3 × Jaccard + 0.3 × HRR`
5. Trust weighting: `final = combined × trust_score`
6. Optional temporal decay applied last

**Compositional (algebraic) retrieval modes:**

- **probe(entity)** (lines 200–260): unbind entity from memory bank via circular correlation (phase subtraction) to extract all facts structurally bound to that entity. Fallback to FTS5 if numpy unavailable.

- **related(entity)** (lines 280–340): find facts that share structural connections with an entity. Unbinds entity from each individual fact vector, scores residuals — captures indirect relationships.

- **reason(entities)** (lines 360–420): multi-entity compositional query. All entities must be structurally present (AND semantics via min). Enables "find facts about A and B together."

- **contradict()** (lines 440–500): detect potentially contradictory facts. High entity overlap + low content similarity flags a contradiction. O(n²) with guard: only checks recent facts when corpus > 500.

---

## 7. Agent Loop Integration

**Initialization** (`run_agent.py:1050–1123`):
- Instantiate `MemoryStore` if `memory_enabled` or `user_profile_enabled`
- `load_from_disk()` → captures `_system_prompt_snapshot`
- Create `MemoryManager`, load configured plugin provider
- Initialize all providers with: `session_id`, `platform`, `hermes_home`, `user_id`, `agent_identity`
- Inject memory tool schemas into `valid_tool_names`

**System prompt construction** (`run_agent.py:2777–2795`):
```
built-in memory block  ← from frozen snapshot, static for session
built-in user block    ← from frozen snapshot, static for session
external memory block  ← provider instructions + status, also static
```
Dynamic external context is NOT in the system prompt — it enters via user message injection.

**Per-turn prefetch** (`run_agent.py:7430–7435`):
- Runs once per turn, before the tool loop, using `original_user_message` as query
- Result cached in `_ext_prefetch_cache`; reused across all tool calls in the same turn
- Avoids repeated latency from redundant provider calls

**Per-turn injection** (`run_agent.py:7505–7516`):
- On the current turn's user message, appends prefetch result wrapped in fence tags
- `<memory-context>…</memory-context>` prevents model treating injected content as user input
- API-call-time only — never persisted to the messages history

**Post-write bridge** (`run_agent.py:6314–6322`):
- After built-in `memory` tool fires `add` or `replace`, calls `memory_manager.on_memory_write()`
- Allows external providers to mirror built-in writes to their own stores

**Per-turn sync** (`run_agent.py:9636–9641`):
- After final response delivered: `memory_manager.sync_all(user_message, response)`
- Queues background prefetch for next turn: `queue_prefetch_all()`

**Memory review nudge** (`run_agent.py:7247–7254`):
- Counter increments each turn; fires every `nudge_interval` turns (default: 10)
- Spawns a background sub-agent to review and consolidate memory after response delivery
- Non-blocking: user sees response before review starts

**Context compression hook** (`run_agent.py:6162–6166`):
- Before compression discards messages: `memory_manager.on_pre_compress(messages)`
- Providers extract insights from about-to-be-discarded context
- Extracted insights fed into the compression summary prompt

**Session end** (`run_agent.py:2638–2644`):
- Called on `/reset`, CLI exit, or session timeout
- `memory_manager.on_session_end(messages)` → providers perform final extraction, flush, cleanup
- `memory_manager.shutdown_all()` in `finally` block

---

## 8. Memory Write Triggers and Classification

**Built-in (agent-driven):** The `memory` tool schema (`memory_tool.py:494–513`) instructs the model when to save:
- User corrections: "remember this", "don't do that again"
- User shares preferences, habits, name, role, timezone
- Agent discovers environment facts: OS, tools, project structure
- Agent learns stable conventions or API quirks

**Target classification:**
- `target="memory"` — agent's own notes (facts, patterns, lessons learned)
- `target="user"` — user profile (communication style, preferences, habits)

**Priority order encoded in schema**: user preferences > environment facts > procedural knowledge.

**Explicit exclusions:** task progress, session outcomes, completed work, temporary state. ("Use `session_search` to recall those from past transcripts instead.")

**External provider write path:**
- Honcho: `honcho_conclude` tool for agent/user to write conclusions
- HRR: `add_fact` called directly via `handle_tool_call` routing (`run_agent.py:6324–6325`)
- Mirror path: `on_memory_write` hook from built-in writes → external provider replication

---

## 9. Configuration

**Location:** `hermes_cli/config.py:506–520`

| Setting | Default | Description |
|---|---|---|
| `memory_enabled` | `true` | Inject MEMORY.md block into system prompt |
| `user_profile_enabled` | `true` | Inject USER.md block into system prompt |
| `memory_char_limit` | `2200` | ~800 tokens; hard budget for MEMORY.md |
| `user_char_limit` | `1375` | ~500 tokens; hard budget for USER.md |
| `nudge_interval` | `10` | Trigger background memory review every N turns |
| `flush_min_turns` | `6` | Min turns before flushing to session DB |
| `provider` | `""` | External provider: `honcho`, `holographic`, `mem0`, custom |

**Honcho provider** (via `$HERMES_HOME/honcho.json`):

| Setting | Description |
|---|---|
| `recall_mode` | `"context"`, `"tools"`, or `"hybrid"` |
| `injection_frequency` | `"every-turn"` or `"first-turn"` |
| `contextCadence` | Min turns between context API calls |
| `dialecticCadence` | Min turns between dialectic API calls |
| `reasoningLevelCap` | `"minimal"`, `"low"`, `"mid"`, `"high"` |

Auto-activation: if `honcho.json` exists with `enabled=true`, provider activates at startup (`run_agent.py:1060–1078`).

**Holographic/HRR provider:** no explicit config file yet; hardcoded `hrr_dim=1024`, weights `fts=0.4`, `jaccard=0.3`, `hrr=0.3`.

---

## 10. Non-Findings

- **No semantic embedding for built-in memory.** MEMORY.md and USER.md have no vector index. Retrieval is static (full injection every session) — there is no search or ranking for the built-in tier.
- **No automatic memory consolidation on the built-in tier.** Consolidation happens only when the background review sub-agent fires (every N turns), and only if the model chooses to act on the nudge.
- **No TTL or age-based expiry on either tier.** The HRR temporal decay exists but is disabled by default.
- **External tier is not always active.** `provider=""` disables it entirely. Many sessions run with built-in only.
- **Honcho and HRR are not composable.** Only one external provider can be active at a time (`memory_manager.py` loads exactly one plugin).
- **No cross-session memory merging or conflict resolution.** Deduplication is intra-session; no merge logic for contradicting facts across sessions beyond what `contradict()` can surface on query.

---

## 11. Convergence Analysis vs co-cli

| Dimension | hermes-agent | co-cli |
|---|---|---|
| Storage format | Two Markdown files (`§`-delimited) + optional SQLite | Flat `.md` files with YAML frontmatter (FTS5 in `search.db`) |
| Schema granularity | Flat entries (built-in); structured facts with entities (HRR) | Flat `kind: memory` / `kind: article` with frontmatter fields |
| System prompt injection | Frozen snapshot at session start; never refreshes mid-session | Dynamic, loaded per-tool-call from FTS5 search |
| Search | Static (built-in); hybrid FTS5+Jaccard+HRR (external) | BM25 FTS5 search |
| Trust / scoring | Trust score `[0, 1]` with asymmetric feedback (HRR) | Not present |
| Compositional retrieval | HRR probe/related/reason/contradict | Not present |
| Temporal decay | Optional half-life (disabled by default) | Not present |
| Write triggers | Agent-driven via schema instructions | Agent-driven via tool schema + lifecycle tools |
| Injection point | System prompt (built-in) + per-turn user message (external) | System prompt and/or per-turn injection (configurable) |
| Background consolidation | Background sub-agent every N turns | Not present as a loop feature |
| Safety scanning | Content scan on every write (injection, exfil, credential theft) | Not present in write path |
| Plugin extensibility | MemoryProvider base class + plugin discovery | Not present (single memory implementation) |
| External provider support | Honcho, HRR, mem0, custom via plugin API | Not present |

**Convergent patterns** (both systems have independently arrived at these):
- Free-form Markdown as the canonical memory representation
- Agent decides when to save via schema-embedded behavioral instructions
- System prompt as the primary delivery channel for memory context
- Explicit write-path classification (`memory` vs `user` in hermes; `kind:` frontmatter in co-cli)
- No automatic expiry — human or agent intent required to remove entries

**Divergent approaches worth studying:**

1. **Frozen snapshot vs. live search.** Hermes freezes the system prompt snapshot at session start to preserve prefix cache; co-cli queries FTS5 fresh per tool call. Hermes's approach is lower latency per call and cache-friendly; co-cli's approach allows mid-session recall of newly written memories. The tradeoff is real: cache stability vs. recency.

2. **Explicit trust scoring.** Hermes HRR tracks `trust_score` per fact with asymmetric positive/negative feedback. co-cli has no equivalent. For long-running agents, trust scoring enables degrading unreliable facts over time rather than wholesale deletion.

3. **Compositional retrieval.** HRR's algebraic probe/related/reason modes enable multi-entity structured queries impossible with BM25 alone. The implementation is novel (phase vectors rather than float embeddings), lightweight (no GPU, no external model), and numerically stable. Worth evaluating as a co-cli extension if multi-entity memory queries become a need.

4. **Background consolidation loop.** Hermes spawns a non-blocking sub-agent every N turns to review and compress memory. co-cli has no equivalent background consolidation pass — stale or redundant entries accumulate until the agent manually cleans them.

5. **Per-turn user message injection.** External provider results are injected into the current turn's user message with fence tags, not into the system prompt. This enables per-turn freshness without dirtying the system prompt or breaking prefix cache on the static portion. co-cli could adopt this pattern for memory retrieval results to separate the static system context from dynamic recalled content.

6. **Write-path safety scanning.** Hermes checks every memory write for injection patterns, exfiltration, and credential theft before persisting. co-cli has no equivalent guard in the memory write path.

---

## Bottom Line

Hermes runs a well-layered two-tier memory system. The built-in tier is simple, cache-friendly, and always active; the external tier is pluggable and adds semantic depth when configured. The frozen-snapshot pattern for the built-in tier is a deliberate prefix-cache optimization that co-cli does not currently use. The HRR holographic provider is the most technically novel component — phase-based compositional retrieval without embeddings — but is optional and disabled by default. The most immediately applicable patterns for co-cli are: (1) frozen snapshot for prefix cache stability, (2) per-turn user-message injection with fence tags for dynamic memory context, (3) write-path safety scanning, and (4) background consolidation sub-agent as a loop feature.

---

## 12. Source-Verified Side-by-Side Comparison

All co-cli citations from the scan of `/Users/binle/workspace_genai/co-cli`. All hermes citations from the scan of `~/workspace_genai/hermes-agent`.

### Storage

| Dimension | co-cli | hermes-agent |
|---|---|---|
| Format | `{slug}.md` with YAML frontmatter (`---`) | `MEMORY.md` / `USER.md`, `§`-delimited entries |
| Path | `.co-cli/memory/` (project-local) | `$HERMES_HOME/memories/` |
| Schema | `id`, `kind`, `created`, `type`, `tags`, `name`, `description`, `related`, `always_on`, `artifact_type` | flat entry list (built-in); structured `facts` + `entities` + `memory_banks` tables (HRR) |
| Indexing for search | None — memories are grep-only (`tools/memory.py:45–61`) | FTS5 on `facts_fts`; B-tree on `trust_score`, `category` (HRR `store.py:16–76`) |
| External DB | FTS5 in `search.db` for **articles only**, not memories | SQLite WAL `holographic.db` (HRR); external Honcho API |
| Atomicity on write | Per-file resource lock + in-place overwrite (`_lifecycle.py:134`) | Temp file + `os.replace()` + `.lock` file (`memory_tool.py:139–153`, `408–436`) |

### Retrieval

| Dimension | co-cli | hermes-agent |
|---|---|---|
| Search algorithm | Case-insensitive substring (`grep_recall`, `tools/memory.py:45–61`) | Static full-injection (built-in); hybrid FTS5 + Jaccard + HRR cosine (external) |
| Ranking | Recency only — `sorted by updated or created desc` (line 60) | Combined score: `0.4×FTS5 + 0.3×Jaccard + 0.3×HRR × trust_score` |
| One-hop graph expansion | Yes — `related` slugs followed up to 5 hops (`_history.py:213–233`) | No equivalent in built-in; HRR `related()` mode covers structural links |
| Max recalled | 5 matches + up to 5 related (`_history.py:201`, `213`) | Configurable `limit`; built-in is all-or-nothing static injection |
| Injection point | Trailing `SystemPromptPart` appended to message list (`_history.py:626–632`) | System prompt (frozen built-in snapshot); per-turn user message with fence tags (external) |
| Always-on memories | Yes — `always_on: bool` field; max 5, capped at 2K chars (`agent.py:366–373`) | Entire MEMORY.md injected (built-in); no per-entry flag |
| Dedup at injection | No — duplicates possible if same fact saved twice | `dict.fromkeys()` at load time removes exact duplicates (`memory_tool.py:128–129`) |

### Write Path

| Dimension | co-cli | hermes-agent |
|---|---|---|
| Explicit tool | `save_memory()` (`tools/memory.py:101–164`) | `memory` tool with `add`/`replace`/`remove`/`read` actions (`memory_tool.py:457–472`) |
| Auto-extraction | Post-turn signal analysis via extractor agent (`_extractor.py`) | Background sub-agent nudged every N turns (`run_agent.py:7247–7254`) |
| Extraction confidence | `high` / `low`; high + allowed tag → auto-save (`_extractor.py:166`) | No extraction confidence — model decides when to call tool |
| Write-time dedup | Save agent compares against manifest and decides UPDATE vs SAVE_NEW | Exact duplicate check on `add` before write (`memory_tool.py:217`) |
| Content safety scan | None — no filtering on write (`_lifecycle.py:74–81` only validates `artifact_type`) | Regex scan: injection patterns, exfiltration, credential theft (`memory_tool.py:85–97`) |
| Budget feedback to model | No | Yes — tool returns `{usage_pct, entry_count, chars_used, chars_limit}` |

### Lifecycle

| Dimension | co-cli | hermes-agent |
|---|---|---|
| TTL / expiry | None — `recall_half_life_days=30` config exists but is **unused** (`config/_memory.py:15`) | None on built-in; HRR temporal decay optional, disabled by default |
| Background consolidation | None — no periodic review or compaction loop | Background sub-agent every `nudge_interval` turns (default 10), non-blocking |
| Compression hook | None | `on_pre_compress(messages)` — providers extract insights before discard |
| Session-end hook | None | `on_session_end(messages)` — providers flush and extract final insights |
| Trust / feedback | None | `trust_score ∈ [0,1]`, asymmetric: `+0.05` helpful / `−0.10` unhelpful (HRR `store.py:349–388`) |

### Configuration

| Dimension | co-cli | hermes-agent |
|---|---|---|
| Budget controls | `injection_max_chars=2000` (`config/_memory.py:17`) | `memory_char_limit=2200`, `user_char_limit=1375` (`config.py:506–520`) |
| Auto-save gate | `auto_save_tags` list (`config/_memory.py:16`) | No equivalent (model-driven; nudge interval governs cadence) |
| Provider extensibility | Single implementation — no plugin API | `MemoryProvider` ABC + plugin discovery (`plugins/memory/__init__.py`) |
| Prefix cache optimization | No — always-on re-evaluated every turn, recall injected per-turn as `SystemPromptPart` | Yes — frozen snapshot at session start preserves cache across all turns |

---

## 13. High-ROI Gap Adoptions (ROI-Ranked)

Gaps are scored by: impact × implementation cost⁻¹ × risk⁻¹. Each gap notes the hermes source pattern and the co-cli adoption target.

---

### Gap 1 — Activate `recall_half_life_days` for Recency-Weighted Scoring

**Adoption verdict: ADOPT**

**Gap:** `recall_half_life_days = 30` is defined in `co_cli/config/_memory.py:15` but never used. `grep_recall()` (`tools/memory.py:60`) sorts by recency as a flat sort, not a decayed score.

**Hermes equivalent:** HRR temporal decay (`retrieval.py:569–593`) applies `0.5^(age_days / half_life)` to candidate scores. Also disabled by default but trivially activated via config.

**What to adopt:** Apply the half-life formula as a multiplier inside `grep_recall()` — after substring filtering, score each match as `1.0 × 0.5^(age_days / half_life_days)` and sort by score descending. When `half_life_days = 0`, skip decay (preserves existing behavior). This uses the config that already exists without adding new schema.

**Why high ROI:** Single-function change in `tools/memory.py:grep_recall`. No schema migration, no new config key — the field already exists. Immediately makes stale memories rank below fresh ones without any extra infrastructure. Low risk: default behavior unchanged when half_life = 0.

---

### Gap 2 — Budget Feedback in `save_memory` Tool Response

**Adoption verdict: ADOPT**

**Gap:** `save_memory()` returns a generic `ToolReturn` with action metadata but does not report memory corpus size or budget pressure. The model has no signal about how crowded the memory store is.

**Hermes equivalent:** Every `memory` tool write returns `{usage_pct, entry_count, chars_used, chars_limit}` (`memory_tool.py:457–460`). The model uses this to decide whether to consolidate or delete old entries before adding new ones.

**What to adopt:** After a successful write in `persist_memory()` (`_lifecycle.py`), compute and include in the `ToolReturn` data dict: total memory count, approximate total content bytes, and a derived `usage_pct` against a configurable soft cap. No new storage, no schema change — just count files in `memory_dir` and sum content lengths at write time.

**Why high ROI:** Trivial to compute at write time. Enables the model to self-manage corpus size without a separate `list_memories` call. Cost: one `glob` + `sum(len)` call per write, negligible.

---

### Gap 3 — Write-Path Content Safety Scan

**Adoption verdict: ADOPT**

**Gap:** `persist_memory()` (`_lifecycle.py:74–81`) only validates `artifact_type` enum. No content filtering. A compromised tool result or adversarial conversation turn could write a prompt-injection payload into a persistent memory that survives across sessions.

**Hermes equivalent:** `memory_tool.py:85–97` scans every write for: invisible Unicode, prompt injection phrases ("ignore previous instructions", "you are now"), `curl`/`wget` with `$SECRETS`-style substitution, SSH backdoor and credential-theft patterns.

**What to adopt:** Add a `_scan_memory_content(content: str) -> None` guard called at the top of `persist_memory()`, before any write. Raise `ValueError` with a descriptive message on match. Pattern set: (1) invisible Unicode (`\u200b`–`\u200f`, `\u202a`–`\u202e`), (2) prompt override phrases (case-insensitive), (3) shell exfiltration (`curl|wget.*\$[A-Z_]+`), (4) credential references (`password|secret|token.*=`). Do NOT suppress — surface as a tool error so the agent sees the rejection.

**Why high ROI:** Memories persist across sessions, making them the highest-impact injection surface in the system. The fix is a pure read (no write side effects), self-contained in one function, and adds no dependencies. The risk of not having this escalates as the memory corpus grows.

---

### Gap 4 — Load-Time Deduplication

**Adoption verdict: ADOPT**

**Gap:** `grep_recall()` and `load_always_on_memories()` do not deduplicate on load. If the save agent makes two SAVE_NEW decisions for near-identical content (possible on concurrent fire-and-forget writes, or after a lock-failure retry), identical memories accumulate and both inject into context.

**Hermes equivalent:** `load_from_disk()` (`memory_tool.py:128–129`) runs `list(dict.fromkeys(entries))` — order-preserving dedup on every load, keeping the first occurrence. Lightweight and safe.

**What to adopt:** In `recall.py:load_memories()`, after loading all `MemoryEntry` objects, deduplicate by normalized content hash (`hashlib.md5(e.content.strip().lower().encode()).hexdigest()`), keeping the entry with the most recent `updated or created` timestamp. Drop exact-content duplicates silently. Log at debug level when a duplicate is dropped.

**Why high ROI:** One pass over an in-memory list — zero disk I/O overhead. Guards against both concurrent write races and historical accumulation from retried writes. No schema change needed.

---

### Gap 5 — Per-Turn Fence-Tag Injection for Recalled Memories

**Adoption verdict: ADAPT**

**Gap:** `inject_opening_context()` (`_history.py:626–632`) injects recalled memories as a trailing `SystemPromptPart` appended to the message list. This lands in the system-prompt position and is structurally indistinguishable from static instructions, making it harder for the model to calibrate how much weight to assign recalled context vs. authoritative instructions.

**Hermes equivalent:** External provider results are wrapped in `<memory-context>…</memory-context>` fence tags and appended to the **user message content** for that turn only (`run_agent.py:7505–7516`). Never persisted to message history. The fence tag signals "this is retrieved context, not a user instruction."

**What to adapt for co-cli:** Keep `SystemPromptPart` as the injection vehicle (pydantic-ai convention), but wrap the injected content in explicit fence tags: `<recalled-memories>\n{content}\n</recalled-memories>`. Update the system prompt instructions to explain the tag semantics. This preserves the existing injection mechanism while giving the model a clear signal that the block is retrieved evidence, not authoritative instruction.

**Why high ROI:** String change to `inject_opening_context()` lines 626–632. No architecture change. Fenced context reduces risk of the model treating a stale recalled memory as an active instruction, which is a real failure mode as the corpus grows.

---

### Gap 6 — Background Consolidation Nudge

**Adoption verdict: ADAPT**

**Gap:** co-cli has no periodic memory review. The post-turn extractor (`_extractor.py`) adds new memories but never consolidates or prunes. The corpus grows monotonically. Stale, superseded, or redundant entries accumulate and compete for injection budget.

**Hermes equivalent:** `run_agent.py:7247–7254` counts turns and fires a background non-blocking sub-agent when `turns_since_memory >= nudge_interval`. Sub-agent reviews the full memory set and may merge, delete, or rewrite entries. Runs **after** the response is delivered — invisible to the user.

**What to adapt for co-cli:** Add a `consolidation_nudge_interval` config key (default 0 = disabled). In `_finalize_turn()` (after extraction), if the turn counter reaches the interval, fire a `fire_and_forget` background call to a consolidation agent with `deps.memory_dir`. The consolidation agent should: load all memories, identify entries whose content is a subset of another, merge near-duplicate entries, and delete entries superseded by more recent updates. Use the existing `persist_memory()` + delete-memory tool path — no new infrastructure needed.

**Why high ROI:** The extraction pipeline already fires background tasks. Adding a consolidation trigger is a small addition to `_finalize_turn()`. The consolidation agent can be implemented as a standard pydantic-ai agent using existing memory tools. No schema changes required. Disabled by default so it doesn't affect existing behavior until opted in.

---

### Gap 7 — Compression Hook: Extract Before Discard

**Adoption verdict: ADAPT**

**Gap:** When `summarize_history_window` (`_history.py`, processor #5) compacts old messages, no extraction pass runs on the about-to-be-discarded content. Any ephemeral decisions or preferences expressed in compressed turns are lost.

**Hermes equivalent:** `memory_manager.on_pre_compress(messages)` (`run_agent.py:6162–6166`) is called before compression. Providers extract insights from the about-to-be-discarded message slice and feed them to the compression summary prompt.

**What to adapt for co-cli:** In `summarize_history_window()`, before summarizing the compaction window, pass the window messages to the memory extractor with `interactive=False`. This is the same extractor already used post-turn — just called earlier on a targeted message slice. Use `on_failure="skip"` so extraction failures don't block compaction.

**Why high ROI:** Re-uses the existing extractor pipeline with no new logic. The only addition is calling `handle_extraction()` on the compaction window. Compaction is rare (only fires when history budget is hit), so the cost is not per-turn. High impact: recovers ephemeral signal that would otherwise be permanently lost.

---

### Gap 8 — Explicit Usage Cap and Soft Limit Warning

**Adoption verdict: DEFER**

**Gap:** co-cli has no hard or soft cap on total memory corpus size. `injection_max_chars` only caps what is injected, not what is stored. A large corpus degrades recall quality (grep over thousands of files) and eventually degrades injection relevance (the cap truncates arbitrarily).

**Hermes equivalent:** `memory_char_limit = 2200` / `user_char_limit = 1375` are enforced at write time — a write is rejected if it would exceed the character budget (`memory_tool.py:224–235`). Budget usage is returned in every tool response.

**Why DEFER for co-cli:** The co-cli corpus is structured (one file per memory, YAML frontmatter, slug-based dedup) rather than a flat append list. A hard character limit is the wrong abstraction — entry count or total token budget would be more appropriate. This requires a design decision about what "full" means and what happens when the limit is hit (reject? evict oldest?). The more urgent fixes (gaps 1–7) address the quality and safety problems without requiring that design decision. Revisit after the consolidation nudge (gap 6) is implemented, since that naturally bounds corpus growth.
