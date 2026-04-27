# Plan: Handler Modularization

**Task type:** refactor

**Position in series:** Plan 2 of 3 in the commands-modularization effort.
- Plan 1 (skills-sovereignty): all skills non-handler logic moves to `co_cli/skills/`; leaf `commands/_registry.py` extracted; skill handlers refactored thin in place. **Prerequisite for this plan.**
- **Plan 2 (this plan):** every slash command gets its own file; `session.py` splits into 5; `_commands.py` shrinks to ~50 lines; knowledge query helpers extract to `knowledge/_query.py`.
- Plan 3 (commands-import-audit): external callers (tests, evals) updated; final audit greps codified as ship gates.

---

## Context

After Plan 1 (skills-sovereignty) lands:

- `co_cli/skills/` owns all skills domain logic (`_skill_types.py`, `loader.py`, `registry.py`, `installer.py`). `skills/loader.py` is namespace-agnostic — knows nothing about reserved names or slash commands.
- `co_cli/commands/_registry.py` is the **leaf** module that holds `SlashCommand`, the empty `BUILTIN_COMMANDS` dict, `_build_completer_words`, `_refresh_completer`, and `filter_namespace_conflicts(loaded, reserved, errors)`. Imports nothing from sibling handlers.
- `co_cli/commands/_commands.py` is **~1,000 lines**: handler imports + `BUILTIN_COMMANDS` registrations + `dispatch()` + 17 handler bodies + `get_skill_registry` (Plan 2 moves it). Skill handlers (`_install_skill`, `_upgrade_skill`, `_cmd_skills_check`, `_cmd_skills_reload`) are already thin orchestrators calling into `skills/installer.py` and composing `load_skills(...) → filter_namespace_conflicts(...)` at the call site.
- `co_cli/commands/_types.py` already holds `_confirm(ctx, msg)` (added in Plan 1 TASK-6) — Plan 2 knowledge handlers use the same helper.
- `co_cli/commands/session.py` (145 lines) still groups 5 commands: `/clear`, `/new`, `/compact`, `/resume`, `/sessions`.
- Knowledge query helpers `_apply_memory_filters` and `_format_memory_row` still live in `_commands.py` (out of scope for Plan 1).

**This plan's job:** mechanically move every handler into its own file. The interesting refactoring already shipped in Plan 1, so this plan is low-risk surface area but high file count.

**Known import sites for handlers/knowledge symbols this plan moves:**
- `co_cli/commands/_commands.py` — registers all handlers via `BUILTIN_COMMANDS["name"] = SlashCommand(...)`
- `co_cli/commands/session.py` is imported only by `_commands.py` (verified at Plan 1 review)
- Internal references between handlers are minimal (each handler is largely self-contained)
- `get_skill_registry` has three core production callers (not tests/evals — not covered by Plan 3):
  - `co_cli/main.py:286`
  - `co_cli/bootstrap/check.py:484`
  - `co_cli/bootstrap/banner.py:39`
  These import sites are updated in TASK-2 when `get_skill_registry` moves to `commands/skills.py`.

External callers (tests, evals) still importing from `_commands.py` are migrated in Plan 3, not here.

---

## Problem & Outcome

**Problem:** `_commands.py` still holds 17 handler bodies in one file. Reviewing one command's logic requires navigating ~700 lines. `session.py` groups 5 unrelated commands; the one-file-per-command principle isn't applied uniformly. Knowledge query helpers leak into the commands package.

**Failure cost:** Contributors can't locate handler code by command name. Domain-specific changes risk touching unrelated handlers in the same file. Knowledge domain logic remains invisible to the knowledge package.

**Outcome:** Every slash command lives in its own file. `session.py` is split into `clear.py`, `new.py`, `compact.py`, `resume.py`, `sessions.py`. Knowledge query helpers move to `co_cli/knowledge/_query.py`. `_commands.py` shrinks to ~50 lines: handler imports + 17 `BUILTIN_COMMANDS["name"] = SlashCommand(...)` registrations + `dispatch()`. No behavior changes.

---

## Scope

**In scope:**
- Move `_apply_memory_filters` and `_format_memory_row` from `_commands.py` to `co_cli/knowledge/_query.py`
- Move every handler in `_commands.py` to its own file in `co_cli/commands/`:
  - `commands/skills.py` — `/skills` + subcommand router (mechanical move; refactor done in Plan 1) + `get_skill_registry` utility (3 core production callers updated here)
  - `commands/knowledge.py` — `/knowledge` + `/memory` (deprecated alias) + subcommand routers + `_parse_memory_args` (CLI-specific flag parsing stays in commands)
  - `commands/background.py` — `/background`
  - `commands/tasks.py` — `/tasks`
  - `commands/cancel.py` — `/cancel`
  - `commands/help.py` — `/help` (imports `BUILTIN_COMMANDS` from `_registry`, no lazy import)
  - `commands/status.py` — `/status`
  - `commands/tools.py` — `/tools`
  - `commands/history.py` — `/history`
  - `commands/approvals.py` — `/approvals` + `_rule_label`
  - `commands/reasoning.py` — `/reasoning` + `_REASONING_CYCLE`
- Split `session.py` into 5 per-command files (`clear.py`, `new.py`, `compact.py`, `resume.py`, `sessions.py`); delete `session.py`

**Out of scope (Plan 3):**
- External caller (tests, evals) import migration
- Final audit greps as codified ship gates

**Out of scope (general):**
- Behavior changes
- Skills domain logic (already in `skills/` after Plan 1)

---

## Behavioral Constraints

- All 17 commands remain reachable via `dispatch()` after every task. Verify: `python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17"`
- `_commands.py` ends with **zero handler function definitions** after this plan: `grep -c "^async def _cmd_\|^def _cmd_" co_cli/commands/_commands.py` returns 0
- `co_cli/commands/session.py` is deleted: `test ! -e co_cli/commands/session.py`
- `dispatch()` public signature unchanged
- No `__init__.py` may gain imports or re-exports
- No backward-compatibility re-exports — every moved symbol is imported from its new home directly at every call site
- **No lazy imports as cycle workarounds.** The leaf `_registry.py` (from Plan 1) means handlers can read `BUILTIN_COMMANDS` at top-level without cycles.
- After every task: `uv run pytest tests/commands/ tests/bootstrap/ tests/context/test_history.py tests/tools/test_background.py -x` must pass. Full suite (`uv run pytest tests/ -x`) must pass at end of plan.

---

## High-Level Design

```
co_cli/
  commands/
    _registry.py              # LEAF (from Plan 1) — unchanged
    _commands.py              # ~50 lines: handler imports + BUILTIN_COMMANDS registrations + dispatch
    _types.py                 # unchanged
    session.py                # DELETED
    clear.py            NEW   # /clear
    new.py              NEW   # /new
    compact.py          NEW   # /compact
    resume.py           NEW   # /resume
    sessions.py         NEW   # /sessions
    help.py             NEW   # /help — imports BUILTIN_COMMANDS from _registry (no lazy import)
    status.py           NEW   # /status
    tools.py            NEW   # /tools
    history.py          NEW   # /history
    skills.py           NEW   # /skills + subcommands (orchestrators are already thin from Plan 1)
    knowledge.py        NEW   # /knowledge + /memory + subcommand routers + _parse_memory_args
    background.py       NEW   # /background
    tasks.py            NEW   # /tasks
    cancel.py           NEW   # /cancel
    approvals.py        NEW   # /approvals + _rule_label
    reasoning.py        NEW   # /reasoning + _REASONING_CYCLE
  knowledge/
    _query.py           NEW   # _apply_memory_filters, _format_memory_row
    [existing files]          # unchanged
```

**Key ownership decisions:**

1. **One file per slash command, applied uniformly** — including the 5 commands currently grouped in `session.py`. Five new files (`clear.py`, `new.py`, `compact.py`, `resume.py`, `sessions.py`); `session.py` deleted. Verified at Plan 1 review: `session.py` is imported only by `_commands.py`.

2. **`commands/help.py` imports `BUILTIN_COMMANDS` from `_registry`** — no lazy import. Plan 1's leaf `_registry.py` is what makes this possible.

3. **`commands/skills.py` is a mechanical move** — `_install_skill`, `_upgrade_skill`, `_cmd_skills_check`, `_cmd_skills_reload`, `_cmd_skills_list`, `_cmd_skills` were refactored in Plan 1 to thin orchestrators that compose `load_skills(...) → filter_namespace_conflicts(...) → set_skill_commands(...) → _refresh_completer(...)` at the call site. This plan moves them as-is. `get_skill_registry` (presentation helper for `/skills list`, banner, status check) also moves here — Plan 1 left it in `_commands.py` pending this move. `_confirm` already lives in `commands/_types.py` from Plan 1; nothing relocates here.

4. **`commands/knowledge.py` owns `_parse_memory_args`** — CLI-specific flag parsing (`--older-than N`, `--kind X`); not knowledge domain. Subcommand handlers (`_subcmd_memory_list`, `_subcmd_memory_count`, `_subcmd_memory_forget`, `_subcmd_knowledge_dream`, `_subcmd_knowledge_restore`, `_subcmd_knowledge_decay_review`, `_subcmd_knowledge_stats`) move into `commands/knowledge.py`. Knowledge domain operations (`_apply_memory_filters`, `_format_memory_row`) move to `knowledge/_query.py` instead.

5. **`commands/background.py`, `commands/tasks.py`, `commands/cancel.py` as three separate files** — each is one handler with no shared state. The user's "one file per slash command" principle.

6. **`approvals.py` and `reasoning.py` each own one command + one local helper** — `_rule_label` moves with `/approvals`; `_REASONING_CYCLE` moves with `/reasoning`.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Extract knowledge query helpers to `co_cli/knowledge/_query.py`

**What moves from `_commands.py` → `co_cli/knowledge/_query.py`:**
- `_apply_memory_filters(entries: list[KnowledgeArtifact], filters: dict[str, Any]) -> list[KnowledgeArtifact]`
- `_format_memory_row(m: KnowledgeArtifact) -> str`

**Stays in commands:** `_parse_memory_args(args: str) -> tuple[str | None, dict]` — CLI-specific flag parsing; will move to `commands/knowledge.py` in TASK-3.

`_commands.py` imports the helpers from their new home (this import is removed in TASK-3 when knowledge handlers move out).

**Prerequisites:** None — independent of other Plan 2 tasks.

```
files:
  - co_cli/knowledge/_query.py       (create)
  - co_cli/commands/_commands.py     (remove moved code; add import)

done_when:
  python -c "from co_cli.knowledge._query import _apply_memory_filters, _format_memory_row" exits 0
  grep -n "def _apply_memory_filters\|def _format_memory_row" co_cli/commands/_commands.py returns empty
  uv run pytest tests/ -x passes
```

---

### ✓ DONE — TASK-2 — Move skill commands to `co_cli/commands/skills.py`

**What moves (mechanical move — Plan 1 already refactored these):**
- `_cmd_skills_list(ctx)`
- `_cmd_skills_check(ctx)` (already uses `discover_skill_files` per Plan 1)
- `_cmd_skills_reload(ctx)` (already composes `load_skills(...) → filter_namespace_conflicts(...) → set_skill_commands(...) → _refresh_completer(...)` per Plan 1)
- `_cmd_skills(ctx, args)` (router)
- `_install_skill(ctx, target, force=False)` (already thin per Plan 1; same end-block composition as reload)
- `_upgrade_skill(ctx, args)` (already uses `find_skill_source_url` per Plan 1)
- `get_skill_registry(skill_commands)` — presentation helper consumed by `/skills list`, banner, status check; moves here from `_commands.py` (Plan 1 left it in place pending this move)

`_confirm` is NOT moved here — it already lives in `commands/_types.py` (per Plan 1 TASK-6 ownership decision) so both `skills.py` and `knowledge.py` can import it from the same shared location. No relocation needed in this plan.

`commands/skills.py` imports from:
- `co_cli.commands._registry` for `BUILTIN_COMMANDS` (to compute `reserved`), `_refresh_completer`, `filter_namespace_conflicts`
- `co_cli.commands._types` for `CommandContext`, `_confirm`
- `co_cli.skills.loader` for `load_skills`, `_diagnose_requires_failures`, `_scan_skill_content`
- `co_cli.skills.registry` for `set_skill_commands`
- `co_cli.skills.installer` for `fetch_skill_content`, `write_skill_file`, `find_skill_source_url`, `discover_skill_files`, `SkillFetchError`
- `co_cli.display._core` for `console`
- `co_cli.config._core` for `settings`

**No lazy imports.** The leaf `_registry.py` makes top-level imports safe.

`_commands.py` after this task:
- Removes the 6 skill function bodies and `get_skill_registry`
- Imports `_cmd_skills` from `commands.skills` and registers it: `BUILTIN_COMMANDS["skills"] = SlashCommand("skills", "...", _cmd_skills)`
- Drops `from co_cli.skills.loader import ...`, `from co_cli.skills.registry import ...`, `from co_cli.skills.installer import ...`, and the `filter_namespace_conflicts` import — those all live in `commands/skills.py` now
- `_confirm` import remains (knowledge handlers in TASK-3 still use it)

**Prerequisites:** None (Plan 1 prereqs already satisfied).

```
files:
  - co_cli/commands/skills.py        (create)
  - co_cli/commands/_commands.py     (remove moved code; add import + registration)
  - co_cli/main.py                   (update get_skill_registry import site)
  - co_cli/bootstrap/check.py        (update get_skill_registry import site)
  - co_cli/bootstrap/banner.py       (update get_skill_registry import site)

done_when:
  python -c "from co_cli.commands.skills import _cmd_skills, get_skill_registry" exits 0
  python -c "from co_cli.commands._types import _confirm" exits 0   # already there from Plan 1
  grep -E "^(async )?def (_cmd_skills|_install_skill|_upgrade_skill|get_skill_registry)\b" co_cli/commands/_commands.py returns empty
  grep -rn "from co_cli.commands._commands import.*get_skill_registry" co_cli/ returns empty
  grep -n "lazy" co_cli/commands/skills.py returns empty   # no lazy-import comments
  grep -nE "^\s+from co_cli\." co_cli/commands/skills.py returns empty   # no function-body imports
  python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17" exits 0
  uv run pytest tests/commands/ tests/bootstrap/ -x passes
```

---

### ✓ DONE — TASK-3 — Move knowledge/memory commands to `co_cli/commands/knowledge.py`

**What moves from `_commands.py` → `co_cli/commands/knowledge.py`:**
- `_MEMORY_USAGE`, `_KNOWLEDGE_USAGE` (constants)
- `_parse_memory_args(args) -> tuple[str | None, dict]` (CLI flag parsing; stays in commands)
- `_subcmd_memory_list(ctx, query, filters)`
- `_subcmd_memory_count(ctx, query, filters)`
- `_subcmd_memory_forget(ctx, query, filters)`
- `_subcmd_knowledge_dream(ctx, rest)`
- `_subcmd_knowledge_restore(ctx, rest)`
- `_subcmd_knowledge_decay_review(ctx, rest)`
- `_subcmd_knowledge_stats(ctx)`
- `_cmd_knowledge(ctx, args)` (router)
- `_cmd_memory(ctx, args)` (deprecated router)

`commands/knowledge.py` imports `_apply_memory_filters` and `_format_memory_row` from `co_cli.knowledge._query` (created in TASK-1).

`_commands.py` registers `_cmd_knowledge` and `_cmd_memory` via `BUILTIN_COMMANDS`. The `_apply_memory_filters` / `_format_memory_row` import added to `_commands.py` in TASK-1 is now removed (handlers using them have moved out).

**Prerequisites:** TASK-1.

```
files:
  - co_cli/commands/knowledge.py     (create)
  - co_cli/commands/_commands.py     (remove moved code; add imports + registrations)

done_when:
  python -c "from co_cli.commands.knowledge import _cmd_knowledge, _cmd_memory" exits 0
  grep -E "^(async )?def (_parse_memory_args|_subcmd_memory|_subcmd_knowledge|_cmd_knowledge|_cmd_memory)\b" co_cli/commands/_commands.py returns empty
  grep -n "_apply_memory_filters\|_format_memory_row" co_cli/commands/_commands.py returns empty
  python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17" exits 0
  uv run pytest tests/commands/ -x passes
```

---

### ✓ DONE — TASK-4 — Extract background task commands to three separate files

**What moves from `_commands.py`:**
- `_cmd_background(ctx, args)` → `co_cli/commands/background.py`
- `_cmd_tasks(ctx, args)` → `co_cli/commands/tasks.py`
- `_cmd_cancel(ctx, args)` → `co_cli/commands/cancel.py`

`_commands.py` imports the three handlers and registers them via `BUILTIN_COMMANDS`.

**Prerequisites:** None.

**Visibility fix in TASK-4:** `_cmd_background` imports `_make_task_id` from `co_cli.tools.background` across package boundaries (underscore = package-private per CLAUDE.md). Rename `_make_task_id` → `make_task_id` in `tools/background.py` as part of this task; update both call sites (`commands/background.py` and `tools/task_control.py`).

```
files:
  - co_cli/commands/background.py    (create)
  - co_cli/commands/tasks.py         (create)
  - co_cli/commands/cancel.py        (create)
  - co_cli/commands/_commands.py     (remove moved code; add imports + registrations)
  - co_cli/tools/background.py       (rename _make_task_id → make_task_id)
  - co_cli/tools/task_control.py     (update call site)

done_when:
  python -c "from co_cli.commands.background import _cmd_background; from co_cli.commands.tasks import _cmd_tasks; from co_cli.commands.cancel import _cmd_cancel" exits 0
  grep -E "^(async )?def (_cmd_background|_cmd_tasks|_cmd_cancel)\b" co_cli/commands/_commands.py returns empty
  grep -rn "_make_task_id" co_cli/ returns empty   # private name fully retired
  python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17" exits 0
  uv run pytest tests/commands/ tests/tools/test_background.py -x passes
```

---

### ✓ DONE — TASK-5 — Extract remaining handlers into focused single-command files

**What moves from `_commands.py`:**
- `_cmd_help(ctx, args)` → `co_cli/commands/help.py` (imports `BUILTIN_COMMANDS` from `_registry`, **no lazy import**)
- `_cmd_status(ctx, args)` → `co_cli/commands/status.py`
- `_cmd_tools(ctx, args)` → `co_cli/commands/tools.py`
- `_cmd_history(ctx, args)` → `co_cli/commands/history.py` — also promote `_DELEGATION_TOOLS` from inline local variable to module-level constant (currently recreated on every call)
- `_rule_label(kind, value)` + `_cmd_approvals(ctx, args)` → `co_cli/commands/approvals.py`
- `_REASONING_CYCLE` + `_cmd_reasoning(ctx, args)` → `co_cli/commands/reasoning.py`

`_commands.py` imports the six handlers and registers them via `BUILTIN_COMMANDS`.

**Prerequisites:** None.

```
files:
  - co_cli/commands/help.py          (create)
  - co_cli/commands/status.py        (create)
  - co_cli/commands/tools.py         (create)
  - co_cli/commands/history.py       (create)
  - co_cli/commands/approvals.py     (create)
  - co_cli/commands/reasoning.py     (create)
  - co_cli/commands/_commands.py     (remove moved code; add imports + registrations)

done_when:
  python -c "from co_cli.commands.help import _cmd_help; from co_cli.commands.status import _cmd_status; from co_cli.commands.tools import _cmd_tools; from co_cli.commands.history import _cmd_history; from co_cli.commands.approvals import _cmd_approvals; from co_cli.commands.reasoning import _cmd_reasoning" exits 0
  grep -nE "^\s+from co_cli\." co_cli/commands/help.py returns empty   # no function-body imports
  grep -n "_commands" co_cli/commands/help.py returns empty   # help.py reads BUILTIN_COMMANDS from _registry, not _commands
  python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17" exits 0
  uv run pytest tests/commands/ -x passes
```

---

### ✓ DONE — TASK-6 — Split `session.py` into five per-command files

**What moves:** Each handler in `co_cli/commands/session.py` → its own file:
- `_cmd_clear` → `co_cli/commands/clear.py`
- `_cmd_new` → `co_cli/commands/new.py`
- `_cmd_compact` → `co_cli/commands/compact.py`
- `_cmd_resume` → `co_cli/commands/resume.py`
- `_cmd_sessions` → `co_cli/commands/sessions.py`

Each new file gets only the imports it needs. The lazy-import-inside-handler pattern in `session.py` (e.g., `from co_cli.memory.session import new_session_path` inside `_cmd_new`) is preserved per file — these are genuine deferred-I/O imports for optional modules, not cycle workarounds.

After all five handlers move, `co_cli/commands/session.py` is **deleted** — no shim, no re-export.

**Import sites to update:**
- `co_cli/commands/_commands.py`: replace the existing `from co_cli.commands.session import (_cmd_clear, _cmd_new, _cmd_compact, _cmd_resume, _cmd_sessions)` block with five separate imports from the new files; registrations unchanged.
- `tests/context/test_context_compaction.py`: two lazy-import sites (line 602: `_cmd_clear, _cmd_compact, _cmd_new, _cmd_resume`; line 662: `_cmd_compact`) — update each to import from the new per-command file.

**After this task:** `_commands.py` contains only handler imports + 17 `BUILTIN_COMMANDS["name"] = SlashCommand(...)` registrations + `dispatch()`. Target: ≤ 60 lines.

**Prerequisites:** None — independent of other Plan 2 tasks.

```
files:
  - co_cli/commands/clear.py                       (create)
  - co_cli/commands/new.py                         (create)
  - co_cli/commands/compact.py                     (create)
  - co_cli/commands/resume.py                      (create)
  - co_cli/commands/sessions.py                    (create)
  - co_cli/commands/session.py                     (delete)
  - co_cli/commands/_commands.py                   (replace one import block with five separate imports)
  - tests/context/test_context_compaction.py       (update two import sites)

done_when:
  python -c "from co_cli.commands.clear import _cmd_clear; from co_cli.commands.new import _cmd_new; from co_cli.commands.compact import _cmd_compact; from co_cli.commands.resume import _cmd_resume; from co_cli.commands.sessions import _cmd_sessions" exits 0
  test ! -e co_cli/commands/session.py
  grep -rn "from co_cli.commands.session" . --include="*.py" returns empty
  grep -c "^async def _cmd_\|^def _cmd_" co_cli/commands/_commands.py returns 0
  python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17" exits 0
  uv run pytest tests/commands/ tests/context/ -x passes
```

---

## Testing

Pure structural refactor — no behavior changes. The existing test suite is the gate. Each task's `done_when` runs `pytest` at the relevant scope; the global Behavioral Constraint requires the full suite to pass at the end of the plan. No new tests.

**Manual smoke tests after TASK-6:**
- `uv run co status` succeeds; `uv run co chat` starts
- All 17 slash commands work via REPL
- Tab completion lists all built-in commands and skill commands
- `/help` renders identical output to before this plan

If a test breaks, stop and diagnose — a regression means the move was wrong.

---

## Cycle Decisions (carry-forward from Cycle C3)

The decisions material to Plan 2:

| Issue ID | Decision | Applies to Plan 2 |
|----------|----------|-------------------|
| C2-2 | `_apply_memory_filters`/`_format_memory_row` are knowledge domain; `_parse_memory_args` is CLI-specific | TASK-1, TASK-3 |
| C2-3 | One file per slash command; grouping unrelated commands violates SoC | TASK-4, TASK-5 |
| C2-4 | One file per slash command must hold uniformly — split `session.py` | TASK-6 |
| C2-7 | No backward-compatibility re-exports | All tasks |

---

## Final — Team Lead

Plan 2 ready for Gate 1 (after Plan 1 ships).

> Run: `/orchestrate-dev handler-modularization` after Plan 1 lands and Plan 2's Gate 1 is approved.

---

## Delivery Summary — 2026-04-27

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | knowledge query helpers importable from `_query.py`; no defs in `_commands.py`; tests pass | ✓ pass |
| TASK-2 | `_cmd_skills`/`get_skill_registry` importable from `commands.skills`; no skill defs in `_commands.py`; no `_commands.get_skill_registry` callers; no lazy `co_cli.` imports in skills.py; 17 commands; tests pass | ✓ pass |
| TASK-3 | `_cmd_knowledge`/`_cmd_memory` importable from `commands.knowledge`; no subcommand defs in `_commands.py`; no `_apply_memory_filters`/`_format_memory_row` references in `_commands.py`; 17 commands; tests pass | ✓ pass |
| TASK-4 | three handlers importable from new files; no defs in `_commands.py`; `_make_task_id` fully retired; 17 commands; tests pass | ✓ pass |
| TASK-5 | six handlers importable from new files; no `co_cli.` lazy imports in help.py; help.py has no `_commands` module imports; 17 commands; tests pass | ✓ pass |
| TASK-6 | five session-handler files importable; `session.py` deleted; no remaining `commands.session` imports; zero handler defs in `_commands.py`; 17 commands; tests pass | ✓ pass |

**Tests:** scoped (tests/commands/, tests/bootstrap/, tests/context/, tests/tools/test_background.py) — 262 passed, 0 failed
**Doc Sync:** fixed — `docs/specs/llm-models.md`, `docs/specs/skills.md`, `docs/specs/bootstrap.md`, `docs/specs/dream.md` updated to point handler references at new per-command modules; permanent `REPORT-*.md` and historical exec-plans left untouched per spec-conventions

**Out-of-scope side fix:** `tests/bootstrap/test_bootstrap.py` line 304 import (`get_skill_registry`) updated from `_commands` to `skills` — TASK-2 broke this test under the post-task scoped-test gate; deferring to Plan 3 would have left the gate red. Plan 3's external-import migration scope reduces by one site.

**Final `_commands.py`:** 129 lines (handler imports + 17 `BUILTIN_COMMANDS` registrations + `dispatch`). Plan target was ~50 lines / ≤60; the 17 multi-line registration blocks alone account for the gap. Zero handler bodies remain — the structural goal (one file per slash command) is fully met.

**Overall: DELIVERED**
All 6 tasks shipped. 17 slash commands reachable via `dispatch()`. `session.py` deleted. `_make_task_id` renamed to `make_task_id` (cross-package visibility fix).
