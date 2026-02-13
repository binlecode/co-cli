# TODO: Knowledge System Evolution

Future enhancements to the memory/knowledge system beyond what Phase 1c shipped.

---

## Current State

Phase 1c delivered:
- **Memory:** on-demand `save_memory`/`recall_memory`/`list_memories` tools, markdown files with YAML frontmatter, dedup, consolidation, decay
- **Search:** grep + frontmatter matching (sufficient for <200 memories)
- **Architecture:** all knowledge is dynamic, loaded via tools — nothing baked into the system prompt

## Planned Enhancements

### 1. Articles Storage

Add curated web-fetched content alongside memories.

- New storage: `.co-cli/knowledge/articles/*.md` with frontmatter (source URL, fetch date, tags)
- New tools: `save_article(content, source_url, title, tags)`, `recall_article(query)`, `list_articles()`
- Same lakehouse pattern as memories — markdown files as source of truth
- Quality gate: agent evaluates source quality before proposing saves
- Design rule: learned from conversation → memory; fetched from web → article

### 2. Learn Mode (Prompt Overlay)

Knowledge curation behavior via prompt mode overlay — not a separate agent.

- The main chat agent with a "learn" mode overlay handles curation
- Uses existing tools: `web_search`, `web_fetch`, `recall_memory`, `save_memory` (+ future `save_article`)
- Agent classifies input (topic/suggestion/fact/question), researches, evaluates quality, proposes structured saves
- User approves/edits/rejects via standard approval flow (`requires_approval=True`)
- Wired through `get_mode_overlay("learn")` in prompt assembly (see DESIGN-16-prompt-design.md)

### 3. Search Scaling

When memory count exceeds grep performance (~200+ files):

- SQLite FTS5 index alongside markdown files (index is derived, files remain source of truth)
- Unified search across memories + articles
- See also: `TODO-cross-tool-rag.md` for cross-source search

### 4. Multimodal Assets

Store images, PDFs, and code snippets alongside articles:

- Asset directory: `.co-cli/knowledge/articles/assets/{slug}/`
- Frontmatter references: `assets: [diagram.png, example.py]`
- `.gitignore` for large binary assets

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/memory.py` | Memory tools (save, recall, list) |
| `co_cli/_frontmatter.py` | YAML frontmatter parsing |
| Future: `co_cli/tools/articles.py` | Article/lakehouse tools |
