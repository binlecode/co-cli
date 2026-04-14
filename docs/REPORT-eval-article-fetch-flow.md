# Eval Report: Article Fetch Flow

## Run: 2026-04-14 22:01:23 UTC

**Total runtime:** 58ms  
**Result:** 4/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `fts5-save-search` | PASS | 33ms |
| `url-consolidation` | PASS | 22ms |
| `read-full-body` | PASS | 1ms |
| `grep-fallback` | PASS | 1ms |

### Step Traces

#### `fts5-save-search` — PASS
- **save_article** (8ms): action=saved id=78a679b4
- **search_articles (FTS5)** (7ms): count=1 results=['FTS5 Search Test']
- **KnowledgeStore.search direct** (6ms): db_count=1

#### `url-consolidation` — PASS
- **save_article (first)** (7ms): action=saved
- **save_article (second, same URL)** (9ms): action=consolidated
- **filesystem check** (0ms): file_count=1 merged_tags=['tagA', 'tagB']

#### `read-full-body` — PASS
- **save_article** (0ms): action=saved
- **read_article** (0ms): title='Read Body Test' content_len=89

#### `grep-fallback` — PASS
- **save_article (no KnowledgeStore)** (0ms): knowledge_store=None
- **search_articles (grep fallback)** (1ms): count=1

---
