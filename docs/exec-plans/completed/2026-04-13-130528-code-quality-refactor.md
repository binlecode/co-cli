# TODO: Code Quality Refactor

**Slug:** `code-quality-refactor`
**Task type:** `refactor`
**Post-ship:** `/sync-doc`

---

## Context

Session 2026-04-13: quality gate audit and design anti-pattern review.

**Quality gate changes already shipped (not tasks here):**
- Removed pyright from dev deps and `[tool.pyright]` config entirely
- Collapsed quality-gate.sh to two tiers: `lint` (ruff) and `full` (lint + pytest)
- Added ruff rules: `C90` (McCabe, max=12), `PT` (pytest style), `T20` (no print in production)
- Configured per-file-ignores: evals, scripts, tests exempted where appropriate
- Fixed 3 PT violations in tests, 4 T201 noqa annotations in config bootstrap

**What this plan covers:**
- 17 C901 complexity violations blocking the ruff gate
- 8 design anti-patterns identified via architectural review

**Current gate state:** `ruff check` fails on 17 C901 violations. No other rule violations. `pytest` unaffected. Pre-commit (`lint`) blocks on these. Pre-push (`full`) blocks on these.

---

## Problem & Outcome

The 17 C901 violations are real structural debt, not false positives. They cluster into two root causes:

1. **Tool functions are too thick** — search logic, confidence scoring, fallback chains, snippet extraction, and result formatting are embedded in individual tool functions instead of being composed from focused helpers in the knowledge layer.
2. **No tool registration model** — tool behavioral categories (compactable, file-touching) are maintained as hardcoded string frozensets in unrelated files. When tools are added or renamed, multiple files break silently.

**Outcome:** After this delivery, `ruff check` passes cleanly, tool functions are composed from focused helpers, and tool categorization has a single source of truth.

---

## Scope

In scope:
- Decompose all 17 C901-violating functions to ≤12 McCabe complexity
- Centralize tool name categorization (`COMPACTABLE_TOOLS`, `FILE_TOOLS`) at the registration site
- Extract `_snippet_around()` into a shared search utility
- Move `_compute_confidence()` and `_detect_contradictions()` into the knowledge layer
- Add `validate_memory_frontmatter()` call in `save_article()`
- Fix history processor side effects (extract state mutations from pure processor bodies)
- Fix `MemoryEntry` re-export workaround (clear module ownership)

Out of scope:
- `CoDeps` god object decomposition — separate ticket; touches every file in the project
- `SearchResult` naming convention audit — low risk, separate ticket
- `TCH` (TYPE_CHECKING imports) migration — requires `from __future__ import annotations` first, separate ticket
- `PERF` (list.extend, dict comprehension) — separate sweep ticket

---

## TASK GROUP 1 — C901: Context & Orchestration Layer

### ✓ DONE TASK-1a `context/_history.py` — `truncate_tool_results` (complexity 13)
### ✓ DONE TASK-1b `context/_history.py` — `_gather_compaction_context` (complexity 15)
### ✓ DONE TASK-1c `context/_history.py` — `detect_safety_issues` (complexity 19)
### ✓ DONE TASK-1d `context/orchestrate.py` — `_execute_stream_segment` (complexity 15)

---

## TASK GROUP 2 — C901: Tools Layer

### ✓ DONE TASK-2a `tools/obsidian.py` — `search_notes` (complexity 20)
### ✓ DONE TASK-2b `tools/articles.py` — `search_knowledge` (complexity 13)
### ✓ DONE TASK-2c `tools/articles.py` — `search_articles` (complexity 14)
### ✓ DONE TASK-2d `tools/google_drive.py` — `search_drive_files` (complexity 13)
### ✓ DONE TASK-2e `tools/memory.py` — `_recall_for_context` (complexity 14)

---

## TASK GROUP 3 — C901: Knowledge Layer

### ✓ DONE TASK-3a `knowledge/_frontmatter.py` — `validate_memory_frontmatter` (complexity 32)
### ✓ DONE TASK-3b `knowledge/_chunker.py` — `chunk_text` (complexity 26)
### ✓ DONE TASK-3c `knowledge/_reranker.py` — `build_llm_reranker` (complexity 14)

---

## TASK GROUP 4 — C901: Commands & Display

### ✓ DONE TASK-4a `commands/_commands.py` — `_cmd_skills` (complexity 24)
### ✓ DONE TASK-4b `display/_core.py` — `prompt_selection` (complexity 16)
### ✓ DONE TASK-4c `main.py` — `_chat_loop` (complexity 18)

---

## TASK GROUP 5 — C901: Bootstrap & Observability

### ✓ DONE TASK-5a `bootstrap/render_status.py` — `get_status` (complexity 13)
### ✓ DONE TASK-5b `observability/_tail.py` — `_verbose_detail_lines` (complexity 27)

---

## TASK GROUP 6 — Design Anti-Patterns

### ✓ DONE TASK-6a History processor side effects (INTENTIONAL DEVIATION documented in code)
### ✓ DONE TASK-6b Tool name centralization (`tool_categories.py` + imports updated)
### ✓ DONE TASK-6c Extract shared snippet utility (`knowledge/_search_util.py`)
### ✓ DONE TASK-6d Move confidence scoring and contradiction detection (`knowledge/_ranking.py`)
### ✓ DONE TASK-6e Enforce frontmatter validation at write time (`save_article` calls `validate_memory_frontmatter`)
### ✓ DONE TASK-6f Resolve MemoryEntry module ownership (stale re-export comment removed)
### ✓ DONE TASK-6g SearchResult boundary encapsulation (`SearchResult.to_tool_output()` added)

---

## Delivery Order

Tasks can be done in any order within a group, but groups should be delivered in sequence to control scope:

1. **Group 6** first — anti-pattern fixes establish the right structure before functions are decomposed into it (e.g., shared snippet utility must exist before TASK-2a/2b/2c are done cleanly)
2. **Groups 1–5** — complexity decompositions, in any order
3. Full `ruff check` must pass with zero violations before ship

Each task is independently shippable (self-contained change + test update). Do not batch multiple TASK groups into a single commit.

---

## Delivery Summary

**Delivered:** 2026-04-13 | **Version:** 0.7.96

All 17 C901 violations cleared. `ruff check` exits 0. Full suite: 425 tests passed.

Key extractions:
- `tools/obsidian.py`: `_fts_search_notes`, `_grep_search_notes`, `_format_note_result`
- `tools/articles.py`: `_grep_fallback_knowledge`, `_post_process_knowledge_results`, `_fts_search_articles`, `_grep_search_articles`
- `tools/google_drive.py`: `_resolve_page_token`, `_store_page_token`, `_format_drive_results`
- `tools/memory.py`: `_collect_related_memories`, `_format_recall_results`
- `bootstrap/render_status.py`: `_resolve_llm_status`
- `display/_core.py`: `_read_key` lifted to module level
- Plus Groups 1, 3, 4, 5, 6 from previous session

**Review verdict:** PASS
