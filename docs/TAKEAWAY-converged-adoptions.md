# Remaining Adoptions Backlog

Design logic for completed items has been merged into the relevant DESIGN docs. This file tracks only incomplete work.

**Status key:** PARTIAL = partially implemented, OPEN = not yet implemented, BLOCKED = blocker identified

---

### 3.6 Progressive Knowledge Loading — BLOCKED

**Source:** Claude Code (`plugins/plugin-dev/skills/agent-development/SKILL.md`, index + on-demand reference sections)

**Problem:** When the lakehouse tier ships, all article content should not be loaded upfront — doing so degrades context quality as the corpus grows.

**Design:** `articles/*/index.md` as summary, `references/` and `examples/` loaded on demand. `recall_article` returns the index; `read_article_detail` loads specific sections.

**Blocker:** Lakehouse tier (`TODO-knowledge-articles.md`) is not implemented. Progressive loading must be a first-class design decision when that work starts, not a retrofit — track here so it is not missed.

**Where:** `tools/knowledge.py` (future).

---

## Status Summary

| Item | Status |
|------|--------|
| 3.6 Progressive Knowledge Loading | BLOCKED |

**Last checked:** 2026-02-25
