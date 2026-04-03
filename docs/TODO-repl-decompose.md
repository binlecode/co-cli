# TODO: Decompose REPL Layer

## Problem

`co_cli/commands/_commands.py` (1010 lines) is a monolith mixing three concerns:
1. Command registry, dispatch, and completer management (REPL routing)
2. Skill loading, parsing, and security scanning (deps assembly)
3. 14 slash-command handler implementations (business logic)

REPL concerns are also scattered across `main.py` (chat loop, prompt session, interrupt handling) and `display/` (frontend, streaming) without a common namespace.

## Goal

Each REPL concern lives in one place. `commands/_commands.py` splits into focused modules. REPL-specific helpers (completer, dispatch, command context) share a namespace distinct from skill loading (which is deps assembly) and handler implementations (which are business logic).

## Current State

| Concern | Where | Lines (approx) |
|---------|-------|----------------|
| Command registry + dispatch | `commands/_commands.py:948-1010` | 60 |
| Completer + refresh | `commands/_commands.py:128-139` | 12 |
| CommandContext | `commands/_commands.py:30-39` | 10 |
| SlashOutcome types (LocalOnly, ReplaceTranscript, DelegateToAgent) | `commands/_commands.py:41-73` | 33 |
| Skill loading + parsing + security scan | `commands/_commands.py:854-943, 76-125` | 140 |
| Skill management handlers (/skills list\|check\|install\|reload\|upgrade) | `commands/_commands.py:414-527` | 113 |
| All other handlers (/help, /clear, /new, /compact, /status, /tools, /history, /approvals, /background, /tasks, /cancel, /forget) | `commands/_commands.py:140-413, 528-853` | ~470 |

## Tasks

### 1. Extract skill loading into `commands/_skill_loader.py`
- Move `_load_skills()`, `_load_skill_file()`, `_check_requires()`, `_is_safe_skill_path()`, `_scan_skill_content()`
- These are deps-assembly functions called from `create_deps()` and `/skills reload` — not REPL routing
- `_skill_types.py` (SkillConfig) already exists as a separate module

files: `co_cli/commands/_commands.py`, `co_cli/commands/_skill_loader.py` (new)

### 2. Extract command handlers into `commands/_handlers.py`
- Move all 14 handler functions (`_cmd_help`, `_cmd_clear`, `_cmd_new`, `_cmd_compact`, `_cmd_status`, `_cmd_tools`, `_cmd_history`, `_cmd_forget`, `_cmd_approvals`, `_cmd_skills`, `_cmd_background`, `_cmd_tasks`, `_cmd_cancel`)
- Each handler takes `(args: str, ctx: CommandContext) -> SlashOutcome`
- `_commands.py` retains only the registry dict and dispatch function

files: `co_cli/commands/_commands.py`, `co_cli/commands/_handlers.py` (new)

### 3. Extract dispatch types into `commands/_types.py`
- Move `CommandContext`, `LocalOnly`, `ReplaceTranscript`, `DelegateToAgent`
- These are the REPL↔command contract — used by `main.py` and every handler

files: `co_cli/commands/_commands.py`, `co_cli/commands/_types.py` (new)

### 4. Slim `_commands.py` to registry + dispatch + completer
- After tasks 1-3, `_commands.py` contains only:
  - `BUILTIN_COMMANDS` dict (name → handler)
  - `dispatch(raw_input, ctx) -> SlashOutcome`
  - `_build_completer_words(skill_commands)` / `_refresh_completer(ctx)`
  - `set_skill_commands()` / `get_skill_registry()`
- Target: ~100 lines

files: `co_cli/commands/_commands.py`

### 5. Update imports across codebase
- `main.py` imports dispatch types from `commands/_types.py`
- `_bootstrap.py` imports `_load_skills` from `commands/_skill_loader.py`
- Handlers import from `_types.py` and `_skill_loader.py` as needed
- Grep for all `from co_cli.commands._commands import` and update

files: `co_cli/main.py`, `co_cli/bootstrap/_bootstrap.py`, `tests/test_bootstrap.py`

### 6. Update DESIGN docs
- `DESIGN-bootstrap.md` — skill loading file reference
- `DESIGN-skills.md` — skill loader file reference
- `DESIGN-core-loop.md` — command dispatch file reference

files: `docs/DESIGN-bootstrap.md`, `docs/DESIGN-skills.md`, `docs/DESIGN-core-loop.md`

## Out of Scope

- Moving chat loop out of `main.py` (it's the natural home for the top-level async loop)
- Refactoring `display/` (already well-namespaced: `_core.py` + `_stream_renderer.py`)
- Changing handler signatures or dispatch semantics (pure extraction, no behavior change)
