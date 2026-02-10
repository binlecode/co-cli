# Phase 1c Knowledge Base — Portable Identity Design

**Purpose:** Make co's internal knowledge, personality, and learned traits portable across machines.
Self-contained reference for Phase 1c implementation. See also `TODO-prompt-system-phase1c-kb-design-research.md` for full peer research data.

---

## 0. Design Foundations

This section summarizes the storage decisions that enable portability. These come from researching 4 peer system source codes (Claude Code, Codex, Gemini CLI, Aider) and 2026 shipping systems (Basic Memory, Khoj, Obsidian, Cursor, Firecrawl, sqlite-vec).

### Why markdown, not JSON

All 4 peer CLI tools use markdown for persistent knowledge (CLAUDE.md, AGENTS.md, GEMINI.md). Zero use JSON as primary knowledge format. Markdown wins because: LLMs consume it directly (no serialization), humans can edit it naturally, git diffs are clean, and it's extensible via free-form sections. Schema enforcement uses YAML frontmatter at the top of each file.

### Lakehouse pattern: files as source of truth

**Lakehouse** = plain files on disk (markdown) with an optional database index layer for search.
**Warehouse** = database as source of truth.

The lakehouse pattern (proven by Basic Memory, Obsidian, Claude Code, Gemini CLI) means:
- Markdown files are the authoritative store — human-readable, git-friendly, sync-safe
- SQLite FTS5 index is derived from files and fully rebuildable
- No binary database in the critical path — if the index is lost, rebuild it from files

This matters for portability: you only need to sync the markdown files. The index rebuilds itself.

### Retrieval ladder

| Corpus size | Method | Why |
|-------------|--------|-----|
| <200 memories | grep + frontmatter scan | Fast, zero dependencies |
| 200–500 docs | SQLite FTS5 (BM25 keyword) | Ranked search needed |
| 500+ diverse docs | Hybrid FTS5 + sqlite-vec (vectors) | Vocabulary mismatch from web-fetched content |

Phase 1c starts with grep. FTS5 and vectors are additive — they don't change the file format or directory structure.

### Memory file format

Each memory or knowledge file uses YAML frontmatter for metadata + markdown body for content:

```markdown
---
id: mem-001
created: 2026-02-09T14:30:00Z
source: user-told
tags: [python, style]
---

User prefers async/await over callbacks.
```

Frontmatter is machine-parsed (for indexing, filtering, deduplication). Body is LLM-consumed (injected into prompts). Frontmatter is stripped before prompt injection.

---

## 1. Problem

Co's identity — who it is to you, what it knows about you, how it communicates — lives in `~/.config/co-cli/`. When you switch macbooks, set up a new machine, or work across desktop and laptop, co starts from zero. You lose:

- Personality selection and communication style
- Learned user preferences (name, timezone, work context)
- Accumulated memories from past sessions
- Cross-project patterns and habits

This is worse than losing config. Config is re-creatable from docs. Identity is earned over time through interaction.

## 2. Peer Analysis

| System | Global state location | Portable? | Sync-safe? |
|--------|----------------------|-----------|------------|
| Claude Code | `~/.claude/CLAUDE.md` + managed memory | No | Partial (markdown yes, internal state no) |
| Codex | `~/.codex/state.db` (SQLite) | No | No (binary corruption risk) |
| Gemini CLI | `~/.gemini/GEMINI.md` | No | Yes (plain markdown) |
| Aider | `~/.aider.conf.yml` | No | Yes (plain YAML) |

**0/4 peers solve portability.** All treat global state as machine-local. None separate portable identity from machine infrastructure.

The lakehouse design (markdown files as source of truth, SQLite index as derived layer — see section 0) makes portability tractable — but only if the directory structure separates what must travel from what stays local.

## 3. What Must Be Portable vs Machine-Local

### Portable (your co's identity)

| State | Content | Why portable |
|-------|---------|-------------|
| Profile | Name, timezone, work context, communication preferences | Defines who you are to co |
| Personality | Selected personality + any overrides | Defines how co talks to you |
| Global knowledge | `context.md` — persistent context about you | Accumulated understanding |
| Memories | `memories/*.md` — explicit learned facts | Earned over sessions |
| Traits | Communication style, learned cross-project patterns | Co's adapted behavior |

### Machine-local (infrastructure)

| State | Content | Why local |
|-------|---------|----------|
| Settings | API keys, model selection, provider config | Different keys per machine, security risk if synced |
| SQLite index | FTS5 search index over knowledge files | Derived from files, rebuildable |
| Caches | Model metadata, token counts, temp files | Transient, machine-specific |
| Session history | Conversation logs, rollouts | Too large to sync, machine-specific context |

### The key insight

**Identity is what makes co *yours*. Infrastructure is what makes co *run*.** These have different lifecycles, different sync requirements, and different security profiles. Mixing them in a flat directory forces users to choose between "sync everything" (leaking API keys) or "sync nothing" (losing identity).

## 4. Solution: Separated Identity Directory

### Directory structure

```
~/.config/co-cli/
├── settings.json                       # machine-local: API keys, model, provider
├── identity/                           # PORTABLE: everything that makes co yours
│   ├── profile.md                      # who you are
│   ├── personality.md                  # how co communicates with you
│   ├── knowledge/
│   │   ├── context.md                  # global persistent context
│   │   └── memories/                   # explicit learned facts
│   │       ├── 001-prefers-async.md
│   │       ├── 002-timezone-pst.md
│   │       └── ...
│   └── traits/                         # co's adapted behaviors
│       ├── communication-style.md      # tone, verbosity, reasoning display
│       └── learned-patterns.md         # cross-project patterns
├── local/                              # machine-local: derived/transient state
│   ├── knowledge.db                    # SQLite FTS5 index (rebuildable)
│   └── cache/                          # model metadata, temp files
└── sessions/                           # machine-local: conversation history
    └── ...
```

### File contents

**`identity/profile.md`**
```markdown
---
version: 1
updated: 2026-02-09T14:30:00Z
---

# Profile

- Name: Bin
- Timezone: America/Los_Angeles
- Work context: AI/ML engineering, Python CLI tools
- Explain reasoning: yes
- Citation style: inline
```

**`identity/personality.md`**
```markdown
---
version: 1
selected: finch
updated: 2026-02-09T14:30:00Z
---

# Personality

Selected: finch

# Overrides

- Verbosity: concise (override finch's default moderate)
- Humor: dry (keep finch's default)
```

**`identity/traits/communication-style.md`**
```markdown
---
version: 1
updated: 2026-02-10T09:00:00Z
---

# Communication Style

- Prefers bullet points over paragraphs for technical content
- Wants reasoning shown before conclusions
- Appreciates when co flags assumptions explicitly
- Dislikes: unnecessary caveats, hedging language
```

**`identity/traits/learned-patterns.md`**
```markdown
---
version: 1
updated: 2026-02-10T09:00:00Z
---

# Learned Patterns (Cross-Project)

- Always uses uv over pip for Python projects
- Prefers async/await over callbacks in all languages
- Structures CLI tools with typer
- Writes functional tests, never mocks
- Commits with conventional commit messages
```

### Why this split works

| Concern | identity/ | local/ + settings.json |
|---------|-----------|----------------------|
| Contains secrets? | No | Yes (API keys in settings) |
| Sync-safe? | Yes (all markdown) | No (SQLite corruption, key leakage) |
| Rebuildable? | No (earned over time) | Yes (index from files, cache regenerates) |
| Human-readable? | Yes | Partially (SQLite is binary) |
| Git-friendly? | Yes (diffable markdown) | No |

## 5. Portability Mechanisms

Three mechanisms, from simplest to most integrated. All work because `identity/` is a self-contained directory of plain markdown files.

### Mechanism A: Symlink (zero code, user-managed)

```bash
# Machine A: move identity to synced location
mv ~/.config/co-cli/identity ~/dotfiles/co-identity
ln -s ~/dotfiles/co-identity ~/.config/co-cli/identity

# Machine B: clone dotfiles, create symlink
git clone <dotfiles-repo>
ln -s ~/dotfiles/co-identity ~/.config/co-cli/identity
```

Works with: dotfiles repos, iCloud Drive, Dropbox, Syncthing, any folder sync.

No code changes needed in co. Co reads `~/.config/co-cli/identity/` as normal — the symlink is transparent.

### Mechanism B: Export/import commands (co-managed, explicit)

```bash
# Export identity as portable archive
co identity export                              # → ~/.config/co-cli/co-identity-export.tar.gz
co identity export ~/Desktop/my-co.tar.gz       # → custom path
co identity export --format dir ~/co-backup/    # → plain directory copy

# Import on new machine
co identity import ~/Desktop/my-co.tar.gz
co identity import --merge                      # merge with existing (keep both, deduplicate)
co identity import --replace                    # overwrite existing identity
```

**Archive contents:**
```
co-identity-export/
├── manifest.md                     # export metadata (human-readable)
│   # source_machine: Bin-MacBook-Pro
│   # exported: 2026-02-09T14:30:00Z
│   # co_version: 0.5.0
│   # memory_count: 47
│   # identity_size: 12KB
├── profile.md
├── personality.md
├── knowledge/
│   ├── context.md
│   └── memories/
│       ├── 001-prefers-async.md
│       └── ...
└── traits/
    ├── communication-style.md
    └── learned-patterns.md
```

**Merge strategy for `--merge`:**
- Profile fields: incoming wins (most recently updated)
- Memories: union by content hash (deduplicate identical facts, keep both if different)
- Traits: incoming wins per-file (file-level granularity)
- Personality: incoming wins (explicit user choice)

### Mechanism C: Identity path override (config-driven)

```json
// ~/.config/co-cli/settings.json
{
  "identity_path": "~/Dropbox/co-identity"
}
```

Co reads identity from the configured path instead of the default `~/.config/co-cli/identity/`. This lets users point to any synced directory without symlinks.

**Resolution order:**
1. `CO_IDENTITY_PATH` env var (highest)
2. `identity_path` in settings.json
3. `~/.config/co-cli/identity/` (default)

## 6. Index Rebuild on New Machine

When co starts on a new machine with synced identity but no local index:

```
co starts
  → reads ~/.config/co-cli/identity/ (synced markdown files)
  → checks ~/.config/co-cli/local/knowledge.db
  → DB missing or stale? → rebuild index from identity/knowledge/ files
  → log: "Rebuilt knowledge index (47 memories, 0 articles)"
  → ready
```

Rebuild is fast (<1s for hundreds of markdown files) and fully automatic. The user never sees it unless they look at logs.

**Staleness detection:** Compare DB's `last_indexed` timestamp against newest file mtime in `identity/knowledge/`. If any file is newer, reindex.

## 7. Conflict Handling

When identity is synced via cloud storage or git, edits on two machines can conflict.

### Markdown files: conflicts are manageable

- Git dotfiles repo: standard git merge conflict markers. User resolves in editor.
- Cloud sync (iCloud/Dropbox): last-write-wins at file level. Acceptable for identity files because:
  - Profile changes are rare and intentional
  - Memories are append-only (new files, not edits to existing)
  - Traits evolve slowly

### Memories: append-only prevents most conflicts

Memory files are created, rarely edited, never deleted automatically. Two machines creating new memories simultaneously produce different files with different IDs — no conflict. The only conflict scenario is editing the same memory on two machines, which is rare.

### If co needs to detect sync conflicts

Future enhancement: add a `checksum` field to each file's frontmatter. On load, verify content matches checksum. If mismatch, warn user:

```
⚠ identity/traits/communication-style.md may have sync conflicts
  (content doesn't match checksum — edited on another machine?)
  Run `co identity check` to review.
```

Deferred to post-Phase 1c. The append-only memory design minimizes conflict risk enough for MVP.

## 8. Implementation Plan

### Phase 1c MVP (ship with initial knowledge system)

| Task | Effort | Description |
|------|--------|-------------|
| Create `identity/` directory structure | 1h | Init on first run, migrate existing settings if needed |
| Split personality from settings.json into `identity/personality.md` | 1h | Read personality from identity, fall back to settings.json |
| Load `identity/profile.md` + `identity/knowledge/context.md` at startup | 1h | Part of existing knowledge loading work |
| Add `identity_path` setting + env var override | 30m | Config precedence: env > settings > default |
| Add `co identity export` command | 2h | Tar/gz identity directory with manifest |
| Add `co identity import` command | 2h | Unpack with replace/merge strategy |
| Document portability in README/help | 30m | Symlink pattern, export/import usage |
| Test: export → fresh machine → import → verify context loads | 1h | Functional test, no mocks |

**Total additional effort:** ~9 hours on top of base Phase 1c work.

### Phase 2 (post-MVP enhancements)

| Task | Description |
|------|-------------|
| `co identity check` command | Detect sync conflicts, stale checksums |
| `co identity diff <archive>` | Show what would change on import |
| Auto-detect synced identity on first run | If `identity/` exists with content, skip onboarding |
| Merge intelligence for memories | Content-hash deduplication, semantic similarity check |
| `co identity sync` via git | Built-in git push/pull for identity directory |

## 9. Interaction with Project Knowledge

Project knowledge (`.co-cli/knowledge/`) stays in the repo — it's already portable via git. The identity system handles only global/cross-project knowledge.

```
Prompt assembly with portable identity:

  1. system.md (base)
  2. Personality (from identity/personality.md)               ← portable
  3. User profile (from identity/profile.md)                  ← portable
  4. Global knowledge (from identity/knowledge/context.md)    ← portable
  5. Global traits (from identity/traits/*.md)                ← portable
  6. Project knowledge (.co-cli/knowledge/context.md)         ← in repo
  7. Project instructions (.co-cli/instructions.md)           ← in repo
```

Layers 2–5 travel with the user. Layers 6–7 travel with the project. When both are present on a new machine, co is fully reconstituted with zero re-learning.

## 10. Differentiator

No peer system (Claude Code, Codex, Gemini CLI, Aider) separates portable identity from machine infrastructure. All treat `~/.<tool>/` as a monolithic local directory. This means:

- Syncing leaks API keys
- SQLite files corrupt during cloud sync
- Users must manually recreate their context on new machines

Co's identity separation is a genuine UX advantage:
- **One symlink** (or one `co identity import`) reconstitutes co on any machine
- **No secrets in identity** — safe to put in dotfiles repo or cloud sync
- **All markdown** — human-readable, auditable, git-diffable
- **Index is derived** — rebuilds automatically, never synced
