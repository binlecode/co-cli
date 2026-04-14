# Eval Report: Memory Edit Recall

## Run: 2026-04-14 22:01:52 UTC

**Total runtime:** 49ms  
**Result:** 4/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `save-recall` | PASS | 14ms |
| `update-reindex-recall` | PASS | 13ms |
| `append-reindex-recall` | PASS | 13ms |
| `edit-no-db` | PASS | 9ms |

### Step Traces

#### `save-recall` — PASS
- **save_memory** (4ms): saved=None
- **search_memories (FTS5)** (0ms): count=1

#### `update-reindex-recall` — PASS
- **save_memory** (4ms): slug=update-test-a5d99e35
- **search (before update)** (0ms): original_found=1
- **update_memory + reindex** (4ms): updated=None
- **search (after update, new keyword)** (0ms): new_found=1
- **search (after update, original keyword)** (0ms): original_still_found=0 (expected 0)

#### `append-reindex-recall` — PASS
- **save_memory** (4ms): slug=append-test-116a3504
- **append_memory + reindex** (4ms): appended keyword='zyxwquartz-eval-mem-edit-unique-append-addendum'
- **search (appended keyword)** (0ms): count=1
- **search (base keyword — must survive append)** (0ms): count=1

#### `edit-no-db` — PASS
- **save_memory (with DB, for file creation)** (0ms): slug=no-db-test-bac46ab2
- **update_memory (knowledge_store=None)** (1ms): exception=False
- **filesystem check** (0ms): file_updated=True contains_new=True

---

## Run: 2026-04-14 22:01:40 UTC

**Total runtime:** 55ms  
**Result:** 3/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `save-recall` | PASS | 14ms |
| `update-reindex-recall` | PASS | 14ms |
| `append-reindex-recall` | ERROR | 0ms |
| `edit-no-db` | PASS | 12ms |

### Step Traces

#### `save-recall` — PASS
- **save_memory** (4ms): saved=None
- **search_memories (FTS5)** (0ms): count=1

#### `update-reindex-recall` — PASS
- **save_memory** (4ms): slug=update-test-07199709
- **search (before update)** (0ms): original_found=1
- **update_memory + reindex** (4ms): updated=None
- **search (after update, new keyword)** (0ms): new_found=1
- **search (after update, original keyword)** (0ms): original_still_found=0 (expected 0)

#### `append-reindex-recall` — ERROR
- **Failure:** append_memory() got an unexpected keyword argument 'new_content'

#### `edit-no-db` — PASS
- **save_memory (with DB, for file creation)** (0ms): slug=no-db-test-a89f9251
- **update_memory (knowledge_store=None)** (1ms): exception=False
- **filesystem check** (0ms): file_updated=True contains_new=True

---

## Run: 2026-04-14 22:01:24 UTC

**Total runtime:** 25ms  
**Result:** 0/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `save-recall` | ERROR | 0ms |
| `update-reindex-recall` | ERROR | 0ms |
| `append-reindex-recall` | ERROR | 0ms |
| `edit-no-db` | ERROR | 0ms |

### Step Traces

#### `save-recall` — ERROR
- **Failure:** Unknown memory type: 'preference'. Valid values: ['feedback', 'project', 'reference', 'user']

#### `update-reindex-recall` — ERROR
- **Failure:** Unknown memory type: 'preference'. Valid values: ['feedback', 'project', 'reference', 'user']

#### `append-reindex-recall` — ERROR
- **Failure:** Unknown memory type: 'fact'. Valid values: ['feedback', 'project', 'reference', 'user']

#### `edit-no-db` — ERROR
- **Failure:** Unknown memory type: 'fact'. Valid values: ['feedback', 'project', 'reference', 'user']

---
