# Phase 1c Knowledge Base Design — Peer Research & Architecture

**Purpose:** Research-backed KB architecture for co's internal knowledge system.
Supersedes the storage/schema sections of `TODO-prompt-system-phase1c.md` (which assumed JSON).

**Research scope:** 4 peer system source code (Claude Code, Codex, Gemini CLI, Aider) + 2026 shipping systems (Basic Memory, Khoj, Obsidian, Cursor, Firecrawl, Jina Reader, sqlite-vec/sqlite-ai demos).

---

## 1. Peer System Convergence

### What 4 peers actually ship

| Dimension | Claude Code | Codex | Gemini CLI | Aider |
|-----------|------------|-------|------------|-------|
| Project context | `CLAUDE.md` hierarchical | `AGENTS.md` git-tree walk | `GEMINI.md` 3-tier | `.aider.conf.yml` |
| Format | Markdown | Markdown | Markdown | YAML/Markdown |
| Memory tool | Agent `memory:` frontmatter | `get_memory` (SQLite) | `save_memory` (appends to GEMINI.md) | None |
| Memory storage | Managed internally | SQLite `~/.codex/state.db` | Markdown file append | Chat history (opt-in restore) |
| Auto-learning | Per-agent scope recording | LLM summaries post-compaction | None | None |
| Context cap | Lazy load, defer >10% window | 32 KiB project docs | BFS 200-dir, JIT load | Token budget per map/history |
| Database | None for knowledge | SQLite | None | SQLite (AST cache only) |

### Converged best practices (2+ systems agree)

1. **Markdown is the knowledge format.** 4/4 use markdown for project instructions. 3/4 use markdown for user-facing memory. 0/4 use JSON as primary knowledge format.

2. **Hierarchical discovery with precedence.** 3/4 (Claude Code, Codex, Gemini CLI) walk directory trees. Precedence: subdirectory > project root > global.

3. **Always-loaded project context + on-demand memory.** 4/4 auto-load project instructions every session. Memory is either always-loaded (Gemini CLI injects into system prompt) or on-demand (Codex `get_memory` tool).

4. **No auto-inference of preferences.** 0/4 silently observe user behavior. Codex comes closest with LLM-generated summaries from conversation compaction, but only post-hoc. The rest require explicit user action (edit file or call tool).

5. **Size caps everywhere.** Codex: 32 KiB hard. Gemini CLI: 200-dir BFS. Aider: configurable token budgets. Claude Code: lazy load under context pressure.

### Impact on phase1c plan

The existing `TODO-prompt-system-phase1c.md` specifies `context.json` as the storage format. This contradicts all 4 peers. Recommendation: **switch to markdown files as source of truth**, with optional SQLite index for search.

---

## 2. Storage Architecture: Lakehouse

### Why lakehouse, not warehouse

| Approach | Source of truth | Query layer | Example systems |
|----------|----------------|-------------|-----------------|
| **Warehouse** | Database (SQLite/JSON) | SQL queries | — (no peer uses this for knowledge) |
| **Lakehouse** | Files on disk (markdown) | Index built from files | Basic Memory, Obsidian, Claude Code, Gemini CLI |

Lakehouse wins for CLI knowledge because:
- Files are human-readable and inspectable without tools
- Files are git-friendly (diffable, mergeable, reviewable)
- LLMs consume markdown directly — no serialization step
- SQLite index is derived and rebuildable from files
- Users can edit knowledge with any text editor

**Real system proof:** Basic Memory (github.com/basicmachines-co/basic-memory) stores all knowledge as markdown files in `~/basic-memory/`, indexes into SQLite for search and graph traversal. Markdown is source of truth; SQLite is derived.

### Proposed layout

```
.co-cli/
├── settings.json                      # config (Phase 1a, unchanged)
├── instructions.md                    # project conventions (Phase 1a, unchanged)
└── knowledge/                         # Phase 1c — NEW
    ├── context.md                     # always-loaded persistent context (replaces context.json)
    ├── memories/                      # explicit memories (save_memory tool)
    │   ├── 001-prefers-async.md
    │   ├── 002-project-uses-sqlalchemy.md
    │   └── ...
    └── articles/                      # web-fetched knowledge (future: Phase 2+)
        ├── python-asyncio-patterns.md
        └── ...

~/.config/co-cli/
└── knowledge/                         # global (cross-project) knowledge
    └── context.md                     # global always-loaded context
```

### Why not `~/.local/share/co-cli/knowledge/`

XDG data dir is for app-managed opaque data. Knowledge files are user-edited content closer to config. Following Gemini CLI's pattern: `~/.config/co-cli/knowledge/` for global, `.co-cli/knowledge/` for project.

---

## 3. Knowledge Format: Markdown with YAML Frontmatter

### context.md (always-loaded)

```markdown
---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User

- Name: Bin
- Timezone: America/Los_Angeles
- Prefers: concise explanations with reasoning shown

# Project

- Type: Python CLI (typer + pydantic-ai)
- Architecture: Agent with RunContext[CoDeps] tools
- Test policy: functional only, no mocks

# Learned

- User prefers async/await over callbacks
- This project uses SQLAlchemy ORM exclusively
- Always run `uv sync` before `pytest`
```

### Memory files (on-demand via tool)

```markdown
---
id: mem-001
created: 2026-02-09T14:30:00Z
source: user-told
tags: [python, style]
---

User prefers async/await over callbacks. When generating Python code
that involves concurrent operations, always use asyncio patterns
rather than callback-based approaches.
```

### Web-fetched articles (future)

```markdown
---
source: https://docs.python.org/3/library/asyncio.html
fetched: 2026-02-09T15:00:00Z
title: "asyncio — Asynchronous I/O"
tags: [python, async, reference]
---

# asyncio — Asynchronous I/O

asyncio is a library to write concurrent code using the async/await syntax...
```

### Why markdown over JSON

| Concern | JSON | Markdown |
|---------|------|----------|
| LLM consumption | Requires formatting step | Direct injection |
| Human editing | Error-prone (syntax) | Natural |
| Git diffs | Noisy (quotes, commas) | Clean |
| Peer convergence | 0/4 use JSON for knowledge | 4/4 use markdown |
| Schema enforcement | Native | Via frontmatter validation |
| Extensibility | Rigid structure | Free-form sections |

---

## 4. Context Budget Management

### Always-loaded budget

| Layer | Budget | Source |
|-------|--------|--------|
| `~/.config/co-cli/knowledge/context.md` | 3 KiB | Global user context |
| `.co-cli/knowledge/context.md` | 7 KiB | Project context |
| **Total always-loaded** | **10 KiB soft / 20 KiB hard** | Matches phase1c plan |

Validation at load time:
- Under 10 KiB: load silently
- 10–20 KiB: load with warning to user
- Over 20 KiB: truncate + warn (keep first 20 KiB, suggest trimming)

### On-demand budget (memories + articles)

Not counted against always-loaded budget. Retrieved via tools when the agent needs them. Token cost is per-retrieval, managed by the agent's context window governance (sliding window + summarization).

### Precedence (matches Gemini CLI's 3-tier + Codex's override model)

```
1. Explicit user command in current turn        (highest)
2. .co-cli/knowledge/context.md                 (project)
3. ~/.config/co-cli/knowledge/context.md        (global)
4. Personality template                         (selected style)
5. Base system prompt                           (lowest)
```

Project context.md overrides global context.md on conflicting facts (e.g., project says "use tabs" overrides global "use spaces").

---

## 5. Knowledge Input Mechanisms

### Phase 1c: Explicit only (matches peer consensus)

**`co learn` CLI command:**
```
co learn "I prefer async/await over callbacks"
co learn "This project uses SQLAlchemy ORM exclusively"
co forget "pattern about callbacks"
co knowledge --list
```

**`save_memory` tool (agent-callable):**
```
save_memory(fact="User prefers async/await over callbacks", tags=["python", "style"])
```

Appends a new markdown file to `.co-cli/knowledge/memories/` with auto-generated ID and YAML frontmatter.

**`recall_memory` tool (agent-callable):**
Searches memories by keyword (grep/FTS5). Returns matching memory content for injection into current turn.

**`list_memories` tool (agent-callable):**
Lists all memories with IDs, dates, and first-line summaries.

**Direct editing:**
Users can edit any markdown file in `knowledge/` with their text editor. No special tooling needed.

### Deferred: Auto-learning (Phase 2+)

No silent inference in Phase 1c. Matches 4/4 peer consensus. Future auto-learning should use opt-in `co learn --auto` mode with explicit confirmation before persisting.

---

## 6. Retrieval Architecture

### Phase 1c: grep + frontmatter scan

For a CLI tool starting with <100 memories, retrieval is simple:

```
recall_memory("async")
  1. glob .co-cli/knowledge/memories/*.md
  2. grep pattern across all files (ripgrep)
  3. parse frontmatter for tag matching
  4. return top-N matches sorted by recency
```

No database needed. File count is small, grep is fast, and the LLM handles relevance ranking from a short candidate list.

### Phase 2: Add SQLite FTS5 index (when memories > ~200)

```
knowledge.db (SQLite, derived from files)
├── memories (FTS5 virtual table)
│   ├── id TEXT
│   ├── content TEXT        -- full markdown body
│   ├── tags TEXT           -- space-separated tags
│   ├── created TEXT        -- ISO8601
│   └── source_path TEXT    -- link back to .md file
└── articles (FTS5 virtual table, same schema)
```

Index is rebuilt from files on startup (or on `co knowledge --reindex`). Files remain source of truth. This matches the Basic Memory pattern: markdown files as data lake, SQLite as query layer.

**Query:**
```sql
SELECT id, source_path, snippet(memories, 1, '<b>', '</b>', '...', 32)
FROM memories
WHERE memories MATCH 'async await'
ORDER BY rank
LIMIT 5;
```

### Phase 3: Add sqlite-vec for semantic search (when articles > ~500)

Only needed when web-fetched articles from diverse sources introduce vocabulary mismatch (e.g., searching "concurrency" should find articles about "parallelism").

```sql
-- Add to knowledge.db
CREATE VIRTUAL TABLE memory_vectors USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[384]     -- sentence-transformers/all-MiniLM-L6-v2
);
```

Hybrid retrieval via Reciprocal Rank Fusion:
```
score = w_fts / (k + fts_rank) + w_vec / (k + vec_rank)
```

Embedding generation: local model via Ollama, or small API call. Deferred until retrieval quality measurably degrades.

### Retrieval threshold summary

| Corpus size | Retrieval method | Rationale |
|-------------|-----------------|-----------|
| <200 memories | grep + frontmatter | Fast, zero dependencies |
| 200–500 docs | SQLite FTS5 (BM25) | Ranked keyword search |
| 500–5,000 docs | Hybrid FTS5 + sqlite-vec | Vocabulary mismatch from diverse sources |
| >5,000 docs | Vector search essential | BM25 alone misses too many results |

**Evidence:** Cursor uses ripgrep alongside full vector search. sqlite-vec blog shows FTS5 alone can't find "global warming" when searching "climate change". But for a CLI tool's own knowledge where you control vocabulary, the semantic gap is small until corpus grows.

---

## 7. Multimodal Knowledge (Future: Phase 2+)

When co gains web fetch + internalization capabilities:

### Storage pattern (converged from Khoj, Obsidian, Firecrawl)

```
.co-cli/knowledge/
├── articles/                    # extracted text (markdown)
│   └── python-asyncio.md        # YAML frontmatter + body
└── attachments/                 # binary originals
    ├── api-diagram.png
    └── rfc-9110.pdf
```

1. Store binary originals as-is in `attachments/`
2. Extract text into markdown sidecars in `articles/`
3. Index extracted text in SQLite FTS5
4. Reference original via path in frontmatter: `attachment: attachments/rfc-9110.pdf`

### Web fetch → internalization flow

```
Agent decides to fetch URL
  → web_fetch tool returns markdown
  → Agent judges relevance (is this worth keeping?)
  → If yes: save_knowledge tool writes to articles/ with frontmatter
  → FTS5 index updated (or deferred to next reindex)
  → Original URL preserved in frontmatter for provenance
```

The agent acts as the quality gate. No automatic internalization of every fetched page. This matches the "explicit knowledge input" principle from peer consensus.

---

## 8. Prompt Injection

### Assembly order (extends phase1c plan)

```
System prompt layers:
  1. system.md (base, with model conditionals)
  2. Personality template ({name}.md)
  3. Global knowledge (~/.config/co-cli/knowledge/context.md)      ← NEW
  4. Project knowledge (.co-cli/knowledge/context.md)               ← NEW
  5. Project instructions (.co-cli/instructions.md)
  6. [system_reminder at end — recency bias, per Aider pattern]     ← Phase 1d
```

Knowledge injection format in prompt:

```markdown
## Internal Knowledge

### Global Context
[contents of ~/.config/co-cli/knowledge/context.md, body only]

### Project Context
[contents of .co-cli/knowledge/context.md, body only]
```

Frontmatter (version, updated timestamp) is stripped before injection — metadata is for tooling, not for the LLM.

---

## 9. Diff from Existing Phase 1c Plan

| Aspect | Current phase1c plan | This KB design | Reason |
|--------|---------------------|----------------|--------|
| Primary format | `context.json` (JSON) | `context.md` (Markdown) | 4/4 peers use markdown |
| Storage model | JSON file = source of truth | Markdown files + optional SQLite index | Lakehouse pattern from Basic Memory |
| Memory storage | `memories/*.json` | `memories/*.md` | Human-editable, git-friendly |
| Schema enforcement | Pydantic models | YAML frontmatter validation | Extensible, no serialization step |
| Retrieval (initial) | Direct JSON field access | grep + frontmatter scan | Simpler, no schema coupling |
| Retrieval (scaled) | Not specified | FTS5 → hybrid FTS5+vectors | Evidence-based thresholds |
| Global knowledge | Not specified | `~/.config/co-cli/knowledge/context.md` | Matches 3/4 peer hierarchy |
| Multimodal | Not specified | `attachments/` + markdown sidecars | Khoj/Obsidian pattern |
| Web internalization | Not specified | Agent-judged save to `articles/` | Quality-gated, not automatic |

### What stays the same

- Memory tools: `save_memory`, `recall_memory`, `list_memories` (names unchanged)
- Context budget: 10 KiB soft / 20 KiB hard (unchanged)
- Injection point: after personality, before project instructions (unchanged)
- No auto-inference in Phase 1c (unchanged)
- `co learn` / `co forget` / `co knowledge --list` CLI commands (unchanged)

---

## 10. Sources

### Peer systems (local source code)

| System | Key findings |
|--------|-------------|
| Claude Code (`~/workspace_genai/claude-code/`) | CLAUDE.md hierarchy, agent memory with 3 scopes (user/project/local), no database for knowledge |
| Codex (`~/workspace_genai/codex/`) | SQLite thread memory, AGENTS.md git-tree walk, LLM-generated memory summaries, 32 KiB cap |
| Gemini CLI (`~/workspace_genai/gemini-cli/`) | GEMINI.md 3-tier hierarchy, `save_memory` appends to markdown, BFS 200-dir, JIT loading |
| Aider (`~/workspace_genai/aider/`) | Chat history markdown, repo map SQLite cache, config YAML, no persistent learning |

### Online research (2026 shipping systems)

| System | Pattern | Source |
|--------|---------|--------|
| Basic Memory | Markdown files + SQLite index (lakehouse) | github.com/basicmachines-co/basic-memory |
| Khoj | PDF/image → text extraction → embeddings → pgvector | github.com/khoj-ai/khoj |
| Obsidian | Plain markdown files, no database | obsidian.md |
| Cursor | Hybrid: ripgrep + vector search + AI reranking | cursor.com/docs/context/codebase-indexing |
| Firecrawl | HTML → markdown as primary output for LLM consumption | github.com/firecrawl/firecrawl |
| Jina Reader | HTML → markdown via ReaderLM-v2 | jina.ai/reader |
| sqlite-vec | Hybrid FTS5 + vector search in SQLite, RRF merge | alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search |
| SQLite RAG | FTS5 + sqlite-vec, ~370ms queries | blog.sqlite.ai/building-a-rag-on-sqlite |
