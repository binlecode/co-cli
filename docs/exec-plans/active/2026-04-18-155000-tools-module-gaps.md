# Plan: Tools Module Gaps — Naming, Indirection, and Monolith Splits

**Task type: refactor** — code reorganization without behavior change. No new tool, no schema change, no API surface change.

---

## Context

Audit of `co_cli/tools/` following the `@agent_tool` decorator introduction (shipped 2026-04-18) identified five structural gaps:

1. `_agent_tool.py` uses a `_prefix` convention but is imported from outside the package (`co_cli/agent/`), violating CLAUDE.md's rule: drop the underscore when the module is consumed cross-package.
2. `_agent_outputs.py` is a 9-line, single-class file with one consumer (`agents.py`). Over-isolated.
3. `memory.py` + `session_search.py` are a two-file implementation of one registered tool (`search_memory`): `memory.py` is the decorated surface, `session_search.py` holds the logic. The indirection adds noise without value.
4. `files.py` (732 lines, 5 tools) and `knowledge.py` (1138 lines, 7 registered tools) are monoliths that bundle tool implementations with substantial local helpers, making navigation and isolated testing harder.
5. `web.py` (624 lines, 2 tools) inlines SSRF protection and HTTP retry as a comment-marked block; security-sensitive code deserves its own module.

Peer research on hermes-agent (closest architectural peer) confirmed that keeping tool infrastructure inside `tools/` is the correct call — no service namespace migration needed.

No workflow hygiene issues found in exec-plans.

---

## Problem & Outcome

**Problem:** `co_cli/tools/` violates naming conventions, contains redundant indirection, and three large files resist navigation and review.

**Failure cost:** Developers searching for tool policy (`@agent_tool`) land in a `_prefix` module that signals package-private scope but is actually public. Approval code authors confused by the two-file `memory`/`session_search` split. File-system tool reviewers must load a 732-line file to find a single tool.

**Outcome:** After this refactor:
- `agent_tool.py` has no leading underscore, matching its cross-package visibility.
- `_agent_outputs.py` is deleted; `AgentOutput` lives in `agents.py`.
- One `memory.py` file owns the `search_memory` tool end-to-end.
- `files.py`, `knowledge.py`, and `web.py` are replaced by subpackages with focused modules.
- All 38 registered tools remain in `NATIVE_TOOLS`, behavior unchanged, full test suite passes.

---

## Scope

**In scope:**
- Rename `_agent_tool.py` → `agent_tool.py`; update all 16 import sites.
- Inline `_agent_outputs.py` (9 lines) into `agents.py`; delete the file.
- Collapse `session_search.py` into `memory.py`; delete `session_search.py`.
- Split `files.py` → `files/` subpackage: `_helpers.py`, `_read.py`, `_write.py`.
- Split `knowledge.py` → `knowledge/` subpackage: `_helpers.py`, `_read.py`, `_write.py`.
- Split `web.py` → `web/` subpackage: `_ssrf.py`, `_search.py`, `_fetch.py`.

**Out of scope:**
- Moving `shell_backend.py`, `background.py`, `resource_lock.py`, `tool_io.py` (defensible co-location per hermes peer research).
- Any change to tool function signatures, docstrings, return types, or `@agent_tool` policy metadata.
- Any change to `co_cli/context/tool_approvals.py` or `co_cli/context/tool_display.py` placement.
- New tests beyond verifying existing tests still pass.

---

## Behavioral Constraints

- **BC-1:** Tool count invariant — `NATIVE_TOOLS` tuple in `_native_toolset.py` must contain exactly the same 38 callables after every task. Any deviation fails the build.
- **BC-2:** Zero behavior change — no tool function body, docstring, parameter name, or `@agent_tool` metadata may be altered. Refactor is rename/move only.
- **BC-3:** `__init__.py` files for new subpackages must be docstring-only. No imports, no re-exports.
- **BC-4:** Old module paths must be fully removed — no `_agent_tool.py`, no `session_search.py`, no `_agent_outputs.py`, no `files.py`, no `knowledge.py`, no `web.py` after the respective tasks. Stale paths cause confusion and incomplete refactors.
- **BC-5:** All existing tests must pass after each individual task, not only after all tasks are complete. Each task is a green-to-green step.

---

## High-Level Design

### TASK-1: Rename `_agent_tool.py` → `agent_tool.py`

File rename + 16 import site updates. `AGENT_TOOL_ATTR` and `agent_tool` function are unchanged. All tool files update `from co_cli.tools._agent_tool import ...` → `from co_cli.tools.agent_tool import ...`.

### TASK-2: Inline `_agent_outputs.py` → `agents.py`

Move `class AgentOutput(BaseModel)` into `agents.py` immediately after the imports block. Delete `_agent_outputs.py`. Remove the cross-file import in `agents.py`.

### TASK-3: Collapse `session_search.py` → `memory.py`

Move the implementation body of `session_search()` (lines 12–61) directly into `search_memory()` in `memory.py`, replacing the delegation call `return await session_search(ctx, query, limit)`. Remove `from co_cli.tools.session_search import session_search`. Delete `session_search.py`.

`tests/test_session_search_tool.py` imports `session_search` directly at line 17 (`from co_cli.tools.session_search import session_search`) and calls it at lines 82 and 110 — the test is not an indirect caller. It must be rewritten to import and exercise `search_memory` from `co_cli.tools.memory` instead. The test scenarios are preserved; only the call target changes.

### TASK-4: Split `files.py` → `files/` subpackage

```
co_cli/tools/files/
  __init__.py       # docstring-only
  _helpers.py       # _enforce_workspace_boundary, _safe_mtime, _detect_encoding,
                    # _is_recursive_pattern
  _read.py          # glob, read_file, grep  (+ their private helpers)
  _write.py         # write_file, patch      (+ fuzzy match, diff, lint helpers)
                    # _MAX_EDIT_BYTES lives here (used only by patch)
```

Private symbol allocation: `_enforce_workspace_boundary` → `_helpers.py`; `_is_recursive_pattern` → `_helpers.py`; `_safe_mtime` → `_helpers.py`; `_detect_encoding` → `_helpers.py`; `_MAX_EDIT_BYTES` → `_write.py`.

`_native_toolset.py` changes:
```python
# before
from co_cli.tools.files import glob, grep, patch, read_file, write_file
# after
from co_cli.tools.files._read import glob, grep, read_file
from co_cli.tools.files._write import patch, write_file
```

Delete `co_cli/tools/files.py`.

### TASK-5: Split `web.py` → `web/` subpackage

```
co_cli/tools/web/
  __init__.py   # docstring-only
  _ssrf.py      # is_url_safe, _BLOCKED_NETWORKS, _BLOCKED_HOSTNAMES (security-isolated)
  _search.py    # web_search tool + Brave API retry helpers
  _fetch.py     # web_fetch tool + HTTP client + html2text + domain allow/block
                # _html_to_markdown, _is_content_type_allowed live here (used only by web_fetch)
```

`tests/test_web.py:15` imports `_html_to_markdown` and `_is_content_type_allowed` directly. After the split these import from `co_cli.tools.web._fetch`.

`co_cli/tools/agents.py:197` has a lazy import `from co_cli.tools.web import web_fetch, web_search` inside `run_web_research()`. This must be updated to import from the new submodule paths.

`_native_toolset.py` changes:
```python
# before
from co_cli.tools.web import web_fetch, web_search
# after
from co_cli.tools.web._fetch import web_fetch
from co_cli.tools.web._search import web_search
```

Delete `co_cli/tools/web.py`.

### TASK-6: Split `knowledge.py` → `knowledge/` subpackage

```
co_cli/tools/knowledge/
  __init__.py    # docstring-only
  _helpers.py    # _slugify, _find_by_slug, _touch_recalled, _find_article_by_url
  _read.py       # search_knowledge, list_knowledge, read_article, search_articles
                 # grep_recall, _recall_for_context (non-tool helpers — read-side)
  _write.py      # update_knowledge, append_knowledge, save_article
                 # save_knowledge (unregistered callable — used by _dream.py, _distiller.py)
```

`grep_recall` and `_recall_for_context` are read-side helpers consumed by non-tool callers: `co_cli/commands/_commands.py:26` imports `grep_recall` and `co_cli/context/_history.py:752` lazily imports `_recall_for_context`. BC-3 forbids `__init__.py` re-exports, so both callers must be updated to import from `co_cli.tools.knowledge._read`.

`co_cli/tools/agents.py:302` has a lazy import `from co_cli.tools.knowledge import search_knowledge` inside `analyze_knowledge()`. This must be updated to `from co_cli.tools.knowledge._read import search_knowledge`.

`_native_toolset.py` changes:
```python
# before
from co_cli.tools.knowledge import (
    append_knowledge, list_knowledge, read_article, save_article,
    search_articles, search_knowledge, update_knowledge,
)
# after
from co_cli.tools.knowledge._read import (
    list_knowledge, read_article, search_articles, search_knowledge,
)
from co_cli.tools.knowledge._write import append_knowledge, save_article, update_knowledge
```

Delete `co_cli/tools/knowledge.py`.

Note: `co_cli/knowledge/` (data layer) and `co_cli/tools/knowledge/` (tool layer) coexist — different package paths, no ambiguity.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Rename `_agent_tool.py` → `agent_tool.py`

**files:**
- `co_cli/tools/agent_tool.py` (new, content identical to current `_agent_tool.py`)
- `co_cli/tools/_agent_tool.py` (delete)
- `co_cli/agent/_native_toolset.py` (update import)
- `co_cli/tools/execute_code.py`, `knowledge.py`, `todo.py`, `obsidian.py`, `capabilities.py`
- `co_cli/tools/google/drive.py`, `gmail.py`, `calendar.py`
- `co_cli/tools/agents.py`, `memory.py`, `web.py`, `shell.py`, `task_control.py`, `files.py`, `user_input.py`

**done_when:** `grep -r '_agent_tool' co_cli/` returns zero matches AND `uv run pytest tests/test_tool_registry.py tests/test_tool_calling_functional.py -x` passes.

**success_signal:** N/A — no user-visible change.

---

### ✓ DONE — TASK-2 — Inline `_agent_outputs.py` into `agents.py`

**files:**
- `co_cli/tools/agents.py` (add `AgentOutput` class, remove cross-file import)
- `co_cli/tools/_agent_outputs.py` (delete)

**prerequisites:** [TASK-1]

**done_when:** `grep -r '_agent_outputs' co_cli/` returns zero matches AND `uv run pytest tests/test_agents.py -x` passes.

**success_signal:** N/A — no user-visible change.

---

### ✓ DONE — TASK-3 — Collapse `session_search.py` → `memory.py`

**files:**
- `co_cli/tools/memory.py` (absorb `session_search` logic inline; remove delegation import)
- `co_cli/tools/session_search.py` (delete)
- `tests/test_session_search_tool.py` (rewrite to import and call `search_memory` from `co_cli.tools.memory` instead of `session_search` from the now-deleted module)

**prerequisites:** [TASK-1]

**done_when:** `grep -r 'from co_cli.tools.session_search import' co_cli/ tests/ evals/` returns zero matches AND `uv run pytest tests/test_session_search_tool.py -x` passes.

**success_signal:** N/A — no user-visible change.

---

### ✓ DONE — TASK-4 — Split `files.py` → `files/` subpackage

**files:**
- `co_cli/tools/files/__init__.py` (new, docstring-only)
- `co_cli/tools/files/_helpers.py` (new — `_enforce_workspace_boundary`, `_safe_mtime`, `_detect_encoding`, `_is_recursive_pattern`)
- `co_cli/tools/files/_read.py` (new — `glob`, `read_file`, `grep`)
- `co_cli/tools/files/_write.py` (new — `write_file`, `patch`, `_MAX_EDIT_BYTES`)
- `co_cli/tools/files.py` (delete)
- `co_cli/agent/_native_toolset.py` (update imports)
- `tests/test_tools_files.py` (update module-level and inline imports; `_MAX_EDIT_BYTES` → `files._write`, private helpers → `files._helpers`)
- `tests/test_files.py` (update imports if any direct path references)
- `tests/test_resource_lock.py` (imports `patch` from `co_cli.tools.files` at line 14 — update to `co_cli.tools.files._write`)

**prerequisites:** [TASK-1]

**done_when:** `grep -r 'from co_cli.tools.files import' co_cli/ tests/ evals/` returns zero matches AND `uv run pytest tests/test_tools_files.py tests/test_files.py tests/test_resource_lock.py -x` passes.

**success_signal:** N/A — no user-visible change.

---

### ✓ DONE — TASK-5 — Split `web.py` → `web/` subpackage

**files:**
- `co_cli/tools/web/__init__.py` (new, docstring-only)
- `co_cli/tools/web/_ssrf.py` (new — `is_url_safe`, blocked networks/hostnames)
- `co_cli/tools/web/_search.py` (new — `web_search` tool + Brave retry helpers)
- `co_cli/tools/web/_fetch.py` (new — `web_fetch` tool + HTTP client + html2text + domain policy + `_html_to_markdown` + `_is_content_type_allowed`)
- `co_cli/tools/web.py` (delete)
- `co_cli/agent/_native_toolset.py` (update imports)
- `co_cli/tools/agents.py` (update lazy import at line 197 from `co_cli.tools.web` → `co_cli.tools.web._fetch` / `co_cli.tools.web._search`)
- `tests/test_web.py` (update line 15: `from co_cli.tools.web import _html_to_markdown, _is_content_type_allowed` → `from co_cli.tools.web._fetch import ...`)

**prerequisites:** [TASK-1]

**done_when:** `grep -r 'from co_cli.tools.web import' co_cli/ tests/ evals/` returns zero matches AND `uv run pytest tests/test_web.py -x` passes.

**success_signal:** N/A — no user-visible change.

---

### ✓ DONE — TASK-6 — Split `knowledge.py` → `knowledge/` subpackage

**files:**
- `co_cli/tools/knowledge/__init__.py` (new, docstring-only)
- `co_cli/tools/knowledge/_helpers.py` (new — `_slugify`, `_find_by_slug`, `_touch_recalled`, `_find_article_by_url`)
- `co_cli/tools/knowledge/_read.py` (new — `search_knowledge`, `list_knowledge`, `read_article`, `search_articles`, `grep_recall`, `_recall_for_context`)
- `co_cli/tools/knowledge/_write.py` (new — `update_knowledge`, `append_knowledge`, `save_article`, `save_knowledge`)
- `co_cli/tools/knowledge.py` (delete)
- `co_cli/agent/_native_toolset.py` (update imports)
- `co_cli/tools/agents.py` (update lazy import at line 302 from `co_cli.tools.knowledge` → `co_cli.tools.knowledge._read`)
- `co_cli/commands/_commands.py` (update import of `grep_recall` → `from co_cli.tools.knowledge._read import grep_recall`)
- `co_cli/context/_history.py` (update lazy import of `_recall_for_context` → `from co_cli.tools.knowledge._read import _recall_for_context`)
- `co_cli/knowledge/_dream.py` (update imports: `_slugify` → `co_cli.tools.knowledge._helpers`, `save_knowledge` → `co_cli.tools.knowledge._write`)
- `co_cli/knowledge/_distiller.py` (update import: `save_knowledge` → `from co_cli.tools.knowledge._write import save_knowledge`)
- `tests/test_knowledge_tools.py` (update both line 19 module-level import and line 301 inline import to `co_cli.tools.knowledge._read`)
- `tests/test_articles.py` (update imports if needed)

**prerequisites:** [TASK-1]

**done_when:** `grep -r 'from co_cli.tools.knowledge import' co_cli/ tests/ evals/` returns zero matches AND `uv run pytest tests/test_knowledge_tools.py tests/test_articles.py -x` passes.

**success_signal:** N/A — no user-visible change.

---

## Testing

All tasks are pure refactors. The criterion for every task is:
1. Old module path unreachable (grep confirms zero stale references).
2. Existing test file for that domain passes unchanged (or with updated import path only).
3. Full suite passes before shipping: `uv run pytest -x`.

No new test files are required. Behavior is identical — the test suite is the regression surface.

---

## Open Questions

None — all questions answerable by inspection before drafting.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tools-module-gaps`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/tools/files/_helpers.py` | Module has `_` prefix but is imported cross-package — violates CLAUDE.md `_prefix.py` rule | blocking | TASK-4 |
| `co_cli/tools/files/_read.py` | Same cross-package `_` prefix violation | blocking | TASK-4 |
| `co_cli/tools/files/_write.py` | Same cross-package `_` prefix violation | blocking | TASK-4 |
| `co_cli/tools/web/_fetch.py` | Same cross-package `_` prefix violation | blocking | TASK-5 |
| `co_cli/tools/web/_search.py` | Same cross-package `_` prefix violation | blocking | TASK-5 |
| `co_cli/tools/knowledge/_helpers.py` | Same cross-package `_` prefix violation | blocking | TASK-6 |
| `co_cli/tools/knowledge/_read.py` | Same cross-package `_` prefix violation | blocking | TASK-6 |
| `co_cli/tools/knowledge/_write.py` | Same cross-package `_` prefix violation | blocking | TASK-6 |
| `co_cli/tools/knowledge/read.py` | `_recall_for_context` and `_slugify` have `_` prefix but are imported from `_history.py` and `_dream.py` — pre-existing violation, not introduced by this refactor | minor | TASK-6 |

**Resolution:** All 8 blocking findings fixed by renaming modules (dropping `_` prefix from all cross-package submodules); `web/_ssrf.py` retained its underscore (truly private within `web/` only). `sed` pass updated all import sites. Lint re-run: PASS. Minor findings noted as pre-existing; no action taken.

**Overall: 8 blocking (all fixed) / 1 minor**

---

## Delivery Summary — 2026-04-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep -r '_agent_tool' co_cli/` zero matches + tests pass | ✓ pass |
| TASK-2 | `grep -r '_agent_outputs' co_cli/` zero matches + tests pass | ✓ pass |
| TASK-3 | `grep -r 'from co_cli.tools.session_search import' ...` zero matches + tests pass | ✓ pass |
| TASK-4 | `grep -r 'from co_cli.tools.files import' ...` zero matches + tests pass | ✓ pass |
| TASK-5 | `grep -r 'from co_cli.tools.web import' ...` zero matches + tests pass | ✓ pass |
| TASK-6 | `grep -r 'from co_cli.tools.knowledge import' ...` zero matches + tests pass | ✓ pass |

**Tests:** full suite — 542 passed, 0 failed
**Independent Review:** 8 blocking (all fixed by renaming cross-package submodules) / 1 minor (pre-existing)
**Doc Sync:** pending

**Overall: DELIVERED**
All 6 tasks complete. Tools module reorganized: `_agent_tool.py` renamed, `_agent_outputs.py` inlined, `session_search.py` collapsed, and `files.py`/`web.py`/`knowledge.py` split into focused subpackages with no behavioral change.

