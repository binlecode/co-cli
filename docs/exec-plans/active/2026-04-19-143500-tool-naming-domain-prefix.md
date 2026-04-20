# Plan: Align Tool Naming to Domain-Prefix Convention

**Task type:** `refactor` (behavior-preserving; pure rename — no logic changes)

## Context

**Prerequisite:** `2026-04-18-132300-remove-search-articles-surface-debt.md` must be completed first. This plan assumes `search_articles` is already removed from the surface.

**Problem:** The tool surface uses two competing naming conventions — domain-prefix (`web_search`, `task_start`) and domain-suffix (`search_knowledge`, `list_knowledge`). Frontier best practice for multi-tool agents is uniformly domain-prefix: it groups tools visually in the model context window, eliminates same-verb ambiguity across domains, and matches MCP spec examples and major agent framework conventions.

**Scope of inconsistency:** 25 of 37 post-cleanup tools need renaming. 12 are already correct (`web_*`, `task_*`, `todo_*`, `clarify`, `shell`).

**Subpackage split (shipped, a5a8e08):** The monolith files `co_cli/tools/knowledge.py` and `co_cli/tools/files.py` were already split into subpackages. All task file lists use the actual current paths:
- `co_cli/tools/knowledge/read.py` — `search_knowledge`, `list_knowledge`, `read_article`
- `co_cli/tools/knowledge/write.py` — `save_knowledge`, `save_article`, `append_knowledge`, `update_knowledge`
- `co_cli/tools/files/read.py` — `glob`, `read_file`, `grep`
- `co_cli/tools/files/write.py` — `write_file`, `patch`

## Rename Table

| Current | New | Domain |
|---------|-----|--------|
| `search_knowledge` | `knowledge_search` | knowledge |
| `list_knowledge` | `knowledge_list` | knowledge |
| `update_knowledge` | `knowledge_update` | knowledge |
| `append_knowledge` | `knowledge_append` | knowledge |
| `analyze_knowledge` | `knowledge_analyze` | knowledge |
| `read_article` | `knowledge_article_read` | knowledge |
| `save_article` | `knowledge_article_save` | knowledge |
| `save_knowledge` *(internal)* | `knowledge_save` | knowledge |
| `search_memory` | `memory_search` | memory |
| `glob` | `file_glob` | file |
| `read_file` | `file_read` | file |
| `write_file` | `file_write` | file |
| `grep` | `file_grep` | file |
| `patch` | `file_patch` | file |
| `execute_code` | `code_execute` | code |
| `check_capabilities` | `capabilities_check` | capabilities |
| `research_web` | `web_research` | web |
| `reason_about` | `reason` | *(none — single-concept)* |
| `search_notes` | `obsidian_search` | obsidian |
| `list_notes` | `obsidian_list` | obsidian |
| `read_note` | `obsidian_read` | obsidian |
| `search_drive_files` | `drive_search` | drive |
| `read_drive_file` | `drive_read` | drive |
| `list_gmail_emails` | `gmail_list` | gmail |
| `search_gmail_emails` | `gmail_search` | gmail |
| `create_gmail_draft` | `gmail_draft` | gmail |
| `list_calendar_events` | `calendar_list` | calendar |
| `search_calendar_events` | `calendar_search` | calendar |

**Unchanged (already correct):** `clarify`, `shell`, `web_search`, `web_fetch`, `task_start`, `task_status`, `task_cancel`, `task_list`, `todo_write`, `todo_read`.

## Behavioral Constraints

1. Zero behavior changes — this is pure symbol rename.
2. Every renamed function retains its `@agent_tool(...)` decorator unchanged.
3. All visibility tiers, approval flags, concurrency settings, and config gates remain unchanged.
4. `search_knowledge(..., kind="article", result_mode="article_index")` references in docstrings update to use `knowledge_search` — no functional change.
5. **Single-concept tool policy:** `clarify`, `shell`, and `reason` are single-concept cognitive primitives with no domain ambiguity — they carry no domain prefix by design. This is not a gap; it is the policy.

## Scope

**In scope:**
- Function renames in all tool definition files
- Import updates in `_native_toolset.py`, `tool_approvals.py`, `_deferred_tool_prompt.py`, `tool_categories.py`, and any cross-module callers
- String-key updates in `tool_display.py`, `tool_approvals.py`, `_deferred_tool_prompt.py`, `tool_categories.py`, `_commands.py`
- Docstring cross-references updated to new names (including `shell.py` model-visible docstring)
- Prompt `.md` files in `co_cli/knowledge/prompts/` that reference tool names the model calls (`save_knowledge` → `knowledge_save`)
- Test import and assertion updates
- `docs/specs/tools.md` tool catalog updated

**Out of scope:**
- Logic changes inside any tool
- Visibility or approval policy changes
- Adding or removing tools
- Renaming the tool definition *files* (only the function names change)

## Implementation Plan

### ✓ DONE — TASK-1 — Knowledge domain renames

```text
files:
  - co_cli/tools/knowledge/read.py
  - co_cli/tools/knowledge/write.py

done_when: >
  search_knowledge → knowledge_search,
  list_knowledge → knowledge_list,
  update_knowledge → knowledge_update,
  append_knowledge → knowledge_append,
  read_article → knowledge_article_read,
  save_article → knowledge_article_save,
  save_knowledge → knowledge_save (internal function, not a registered tool).
  All @agent_tool decorators unchanged.

prerequisites: []
```

### ✓ DONE — TASK-2 — Memory and file domain renames

```text
files:
  - co_cli/tools/memory.py
  - co_cli/tools/files/read.py
  - co_cli/tools/files/write.py

done_when: >
  search_memory → memory_search.
  glob → file_glob, read_file → file_read, write_file → file_write,
  grep → file_grep, patch → file_patch.
  All @agent_tool decorators unchanged.

prerequisites: []
```

### ✓ DONE — TASK-3 — Execution and agent domain renames

```text
files:
  - co_cli/tools/execute_code.py
  - co_cli/tools/capabilities.py
  - co_cli/tools/agents.py

done_when: >
  execute_code → code_execute.
  check_capabilities → capabilities_check.
  research_web → web_research, analyze_knowledge → knowledge_analyze,
  reason_about → reason.
  All @agent_tool decorators unchanged.
  Zero references to old names remain in agents.py — including string literals
  used as tracer span names (e.g. "analyze_knowledge", "research_web") and any
  internal import references to renamed tools.

prerequisites: []
```

### ✓ DONE — TASK-4 — Obsidian domain renames

```text
files:
  - co_cli/tools/obsidian.py

done_when: >
  search_notes → obsidian_search, list_notes → obsidian_list,
  read_note → obsidian_read.
  All @agent_tool decorators unchanged.

prerequisites: []
```

### ✓ DONE — TASK-5 — Google domain renames

```text
files:
  - co_cli/tools/google/drive.py
  - co_cli/tools/google/gmail.py
  - co_cli/tools/google/calendar.py

done_when: >
  search_drive_files → drive_search, read_drive_file → drive_read.
  list_gmail_emails → gmail_list, search_gmail_emails → gmail_search,
  create_gmail_draft → gmail_draft.
  list_calendar_events → calendar_list,
  search_calendar_events → calendar_search.
  All @agent_tool decorators unchanged.

prerequisites: []
```

**Implementation note:** `search_drive_files` / `read_drive_file` drop the `_files` / `_file` suffix — the domain prefix already disambiguates them. `create_gmail_draft` → `gmail_draft` drops the verb since "draft" is already an action noun; `gmail_send` remains the natural next verb if a send tool is ever added.

### ✓ DONE — TASK-6 — Update registration, approval, display, and category files

```text
files:
  - co_cli/agent/_native_toolset.py
  - co_cli/context/tool_display.py
  - co_cli/context/tool_approvals.py
  - co_cli/context/_deferred_tool_prompt.py
  - co_cli/context/tool_categories.py

done_when: >
  All imports in _native_toolset.py use new function names.
  NATIVE_TOOLS tuple references all new names.
  TOOL_START_DISPLAY_ARG string keys in tool_display.py match new tool names.
  tool_approvals.py: "write_file" → "file_write", "patch" → "file_patch" in
    resolve_approval_subject string literals (~lines 111, 116); private helper
    _build_write_file_preview renamed to _build_file_write_preview.
  _deferred_tool_prompt.py: all old tool name string keys updated to new names.
  tool_categories.py: PATH_NORMALIZATION_TOOLS, FILE_TOOLS, COMPACTABLE_TOOLS frozensets
    updated with new names (read_file → file_read, write_file → file_write, patch → file_patch,
    glob → file_glob, grep → file_grep, read_article → knowledge_article_read,
    read_note → obsidian_read).

prerequisites: [TASK-1, TASK-2, TASK-3, TASK-4, TASK-5]
```

### ✓ DONE — TASK-7 — Update cross-module callers, docstrings, and prompt files

```text
files:
  - co_cli/knowledge/_distiller.py
  - co_cli/knowledge/_dream.py
  - co_cli/knowledge/prompts/dream_miner.md
  - co_cli/knowledge/prompts/knowledge_extractor.md
  - co_cli/tools/knowledge/read.py (docstring cross-references)
  - co_cli/tools/knowledge/write.py (docstring cross-references)
  - co_cli/tools/files/read.py (docstring cross-references)
  - co_cli/tools/files/write.py (docstring cross-references)
  - co_cli/tools/shell.py (model-visible docstring references old tool names)
  - co_cli/knowledge/mutator.py (inline comments referencing old tool names)
  - co_cli/commands/_commands.py (_DELEGATION_TOOLS frozenset: old agent tool name strings)

done_when: >
  _distiller.py and _dream.py import knowledge_save (was save_knowledge).
  dream_miner.md and knowledge_extractor.md reference `knowledge_save` (not `save_knowledge`) —
    these prompts instruct the model which tool to call; stale names break extraction at runtime.
  Docstrings in knowledge/read.py, knowledge/write.py, files/read.py, files/write.py
    that name other tools use new names (e.g. read_article guidance updated to knowledge_article_read).
  shell.py docstring updated to reference new tool names (file_read, file_write, obsidian_read, etc.).
  mutator.py inline comments referencing old names updated.
  _commands.py _DELEGATION_TOOLS frozenset updated to new agent tool names
    (research_web → web_research, analyze_knowledge → knowledge_analyze, reason_about → reason).

prerequisites: [TASK-1]
```

**Implementation note:** `dream_miner.md` and `knowledge_extractor.md` are critical — they instruct the model which tool name to call. A stale `save_knowledge` reference causes a tool-not-found failure at runtime even though the function is registered under `knowledge_save`.

### ✓ DONE — TASK-8 — Update tests

```text
files:
  - tests/test_articles.py
  - tests/test_knowledge_tools.py
  - tests/test_agent.py
  - tests/test_approvals.py
  - tests/test_capabilities.py
  - tests/test_execute_code.py
  - tests/test_obsidian.py
  - tests/test_session_search_tool.py
  - tests/test_telemetry_redaction.py
  - tests/test_tool_calling_functional.py
  - tests/test_tool_registry.py
  - tests/test_tools_files.py
  - tests/test_files.py
  - tests/test_display.py
  - tests/test_history.py
  - tests/test_context_compaction.py
  - tests/test_transcript.py
  - tests/test_tool_output_sizing.py
  - tests/test_distiller_window.py

done_when: >
  All test imports use new function names.
  String assertions referencing old tool names updated:
    "write_file" → "file_write" in test_display.py;
    "search_memory" → "memory_search" in test_telemetry_redaction.py;
    "read_file" → "file_read", "glob" → "file_glob", "grep" → "file_grep",
      "patch" → "file_patch" in test_tools_files.py ctx-string args;
    "read_file" → "file_read", "grep" → "file_grep" in test_history.py,
      test_context_compaction.py, test_transcript.py, test_tool_output_sizing.py,
      test_distiller_window.py assertions;
    "read_article" → "knowledge_article_read", "save_article" → "knowledge_article_save"
      in test_articles.py, test_knowledge_tools.py, test_agent.py.

success_signal: >
  uv run pytest tests/ -x

prerequisites: [TASK-6, TASK-7]
```

### ✓ DONE — TASK-9 — Grep sweep and full test gate

```text
done_when: >
  rg finds zero references to any old tool name in co_cli/ or tests/.
  Full pytest passes.

success_signal: >
  rg -n "search_knowledge|list_knowledge|update_knowledge|append_knowledge|analyze_knowledge|read_article|save_article|save_knowledge|search_memory|\"read_file\"|\"write_file\"|\"glob\"|\"grep\"|\"patch\"|execute_code|check_capabilities|research_web|reason_about|search_notes|list_notes|read_note|search_drive_files|read_drive_file|list_gmail_emails|search_gmail_emails|list_calendar_events|search_calendar_events|create_gmail_draft" co_cli/ tests/ | grep -v "\.pyc"
  uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-naming.log

prerequisites: [TASK-8]
```

**Implementation note:** `glob`, `grep`, and `patch` are common English words — the sweep uses quoted-string form (`"glob"`, `"grep"`, `"patch"`) to minimize false positives while still catching string literal references in test assertions and display maps. Function renames in `.py` files are unambiguous; the quoted form targets string-keyed usages specifically.

## Testing

Focused dev test after TASK-8:
```bash
mkdir -p .pytest-logs
uv run pytest tests/test_articles.py tests/test_knowledge_tools.py tests/test_agent.py tests/test_capabilities.py tests/test_execute_code.py tests/test_obsidian.py tests/test_tools_files.py tests/test_files.py tests/test_display.py tests/test_history.py tests/test_context_compaction.py tests/test_transcript.py tests/test_tool_output_sizing.py tests/test_distiller_window.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-naming-dev.log
```

Full gate before shipping:
```bash
mkdir -p .pytest-logs
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-naming-full.log
```

## Delivery Notes

- `sync-doc` should run after implementation to reconcile `docs/specs/tools.md` with the new tool surface.
- TASK-1 through TASK-5 are fully parallelizable — no cross-task dependencies.
- `glob` → `file_glob` and `grep` → `file_grep` are the renames most likely to have stale string references in test assertions — check carefully during TASK-8.
- `save_knowledge` → `knowledge_save` is internal but critical: `_distiller.py`, `_dream.py`, and both prompt `.md` files must all update together (TASK-7).
- `tool_approvals.py`, `_deferred_tool_prompt.py`, and `tool_categories.py` contain runtime-critical string literals — these are the highest-risk misses if overlooked.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tool-naming-domain-prefix`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| All renamed tool files | All function definitions use new domain-prefix names — no old names re-exported or aliased | clean | all |
| `co_cli/agent/_native_toolset.py` | All imports and NATIVE_TOOLS tuple use new names | clean | TASK-6 |
| `co_cli/context/tool_*.py` | All string keys updated (display, categories, approvals, deferred prompt) | clean | TASK-6 |
| `co_cli/knowledge/_distiller.py`, `_dream.py` | Import `knowledge_save` correctly | clean | TASK-7 |
| `co_cli/knowledge/prompts/*.md` | All `knowledge_save` references correct (runtime critical) | clean | TASK-7 |
| `tests/` (all files) | All imports and call sites updated; no stale names in code paths | clean | TASK-8 |

**Overall: clean — 0 blocking, 0 minor**

## Delivery Summary — 2026-04-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | knowledge_search, knowledge_list, knowledge_article_read, knowledge_save renamed | ✓ pass |
| TASK-2 | memory_search, file_glob, file_read, file_write, file_grep, file_patch renamed | ✓ pass |
| TASK-3 | code_execute, capabilities_check, web_research, knowledge_analyze, reason renamed | ✓ pass |
| TASK-4 | obsidian_search, obsidian_list, obsidian_read renamed | ✓ pass |
| TASK-5 | drive_search, drive_read, gmail_list, gmail_search, gmail_draft, calendar_list, calendar_search renamed | ✓ pass |
| TASK-6 | _native_toolset.py, tool_display.py, tool_approvals.py, _deferred_tool_prompt.py, tool_categories.py updated | ✓ pass |
| TASK-7 | _distiller.py, _dream.py, prompts/*.md, shell.py, _commands.py, mutator.py, deps.py updated | ✓ pass |
| TASK-8 | All test files updated — imports and call sites; 541 tests pass | ✓ pass |
| TASK-9 | Zero stale references in co_cli/ or tests/ (string literals); quality gate PASS | ✓ pass |

**Extra files (beyond plan scope, required for correctness):**
- `co_cli/skills/doctor.md` — model-visible skill referencing old tool names
- `co_cli/knowledge/_frontmatter.py` — inline comment stale names
- `co_cli/deps.py` — comment references
- `tests/test_capabilities.py` — missed in plan's test list
- `tests/test_files.py` — missed in plan's test list
- `tests/test_resource_lock.py` — missed in plan's test list
- `scripts/llm_call_audit.py` — pre-existing F841 lint error fixed (unrelated to plan scope)

**Tests:** full suite — 541 passed, 0 failed
**Independent Review:** clean — 0 blocking, 0 minor
**Doc Sync:** fixed (tools.md, cognition.md, compaction.md, core-loop.md, bootstrap.md, observability.md, session.md all updated)

**Overall: DELIVERED**
Pure rename of 28 tool symbols across 37 native tools. All doc specs, prompt files, tests, and cross-module callers updated. Zero behavior changes.
