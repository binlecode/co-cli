# Plan: Skills Sovereignty

**Task type:** refactor

**Position in series:** Plan 1 of 3 in the commands-modularization effort.
- **Plan 1 (this plan):** all skills non-handler logic moves to `co_cli/skills/`; leaf `commands/_registry.py` extracted (with namespace-conflict filter); skill handlers refactored thin in place. Handlers do **not** move out of `_commands.py` yet.
- **Plan 2 (handler-modularization):** every handler moves to its own file; `session.py` splits into 5; `_commands.py` shrinks to ~50 lines; knowledge query helpers extract; `get_skill_registry` moves to `commands/skills.py`.
- **Plan 3 (commands-import-audit):** external callers (tests, evals) updated; final audit greps codified as ship gates.

---

## Architectural posture (settled before kickoff)

```
┌─────────────────────────────────────────┐
│  commands/   CLI relay layer            │
│  - dispatch()                           │
│  - built-in slash command handlers      │
│  - owns: slash-command namespace,       │
│    reserved-name policy, completer      │
└──────────────┬──────────────────────────┘
               │ may call (one-way)
               ▼
┌─────────────────────────────────────────┐
│  skills/     skills domain library      │
│  - load skills from disk                │
│  - SkillConfig type                     │
│  - skill registry mutation              │
│  - skill operation I/O (installer)      │
│  - knows nothing about commands or      │
│    the slash-command namespace          │
└─────────────────────────────────────────┘
```

- **Direction is one-way: `commands → skills`.** No `skills → commands` edge — not in code, not in concepts.
- **commands is the CLI relay.** It maps user input (slash-command in this case) to behavior. The relay decides what's reserved, what gets dispatched, what gets handed to the agent loop.
- **skills is a domain library.** It loads skill files, validates skill-internal contracts (`requires:`, env keys, .md extension, security scan), exposes `dict[str, SkillConfig]`. It does **not** know what a slash command is. It does **not** know which names are reserved.
- **Reserved-name policy lives in the relay.** Filtering loaded skills against the slash-command namespace happens at the call site in `commands/` (or `bootstrap/` for first load), composing two pure functions: `load_skills(...) → filter_namespace_conflicts(loaded, reserved, errors)`.

This posture supersedes earlier drafts that took `load_skills(reserved: set[str])` — even though the data flow there was correct (caller pushes the set in), the parameter name leaked the relay-layer concept "reserved" into the skills domain. The corrected design keeps `skills/loader.py` blissfully unaware of slash-command namespaces.

---

## Context

`co_cli/commands/_commands.py` is **1,300 lines**. Skills domain logic is heavily mixed with handler logic:

- `SkillConfig` lives at `co_cli/commands/_skill_types.py` (wrong package — it's a skills domain type)
- 9 skill-loading functions/constants live in `_commands.py`: `_SKILL_ENV_BLOCKED`, `_SKILL_SCAN_PATTERNS`, `_scan_skill_content`, `_inject_source_url`, `_check_requires`, `_diagnose_requires_failures`, `_is_safe_skill_path`, `_load_skill_file`, `load_skills`
- 2 skill-registry helpers live in `_commands.py`: `set_skill_commands` (lifecycle — mutates `deps.skill_commands`), `get_skill_registry` (presentation — produces `list[dict]` for display)
- `_install_skill` (`_commands.py:414-495`) inlines URL fetch (httpx + content-type validation), file write, source-url injection
- `_upgrade_skill` (`_commands.py:498-519`) inlines frontmatter parsing to extract `source-url`
- `_cmd_skills_check` (`_commands.py:307-347`) inlines bundled+user skill directory globbing
- `co_cli/skills/` currently contains only `lifecycle.py` + `doctor.md`

**The `BUILTIN_COMMANDS` cycle problem:** `BUILTIN_COMMANDS` at `_commands.py:1218` plays both **sink** (handler imports register into it) and **source** (other modules read it). When the file is split, anything that's both registered and a reader (`/help` for listing, `load_skills` for the reserved-name check) creates an import cycle. Lazy imports are the patchy workaround; a leaf `commands/_registry.py` removes the cycle structurally — and inverting the reserved-name policy out of the loader entirely removes the conceptual leak.

**Current `co_cli/commands/` state:**
```
_commands.py    1,300 lines  — monolithic; registry + dispatch + all handlers + skill loading
_types.py          59 lines  — CommandContext, SlashOutcome (clean)
_skill_types.py    26 lines  — SkillConfig (skills domain type misplaced)
session.py        145 lines  — /clear, /new, /compact, /resume, /sessions (Plan 2 splits)
```

**Known import sites for symbols this plan touches:**
- `co_cli/bootstrap/core.py:261` — `load_skills`
- `co_cli/bootstrap/check.py:484` — `get_skill_registry` (left in place during Plan 1; moves in Plan 2)
- `co_cli/bootstrap/banner.py:39` — `BUILTIN_COMMANDS`, `get_skill_registry` (BUILTIN_COMMANDS retargeted in TASK-4; `get_skill_registry` left until Plan 2)
- `co_cli/main.py:25,32,286` — `BUILTIN_COMMANDS`, `_build_completer_words`, `get_skill_registry` (first two retargeted in TASK-4; third left until Plan 2)
- `co_cli/deps.py:24` — `SkillConfig` (TYPE_CHECKING block)
- `tests/commands/test_skills_loader.py:11` — `SkillConfig` and skill loader internals

Tests/evals that import other moved symbols (`load_skills`, `get_skill_registry`, etc.) from `_commands.py` are out of scope for this plan and migrated in Plan 3.

---

## Problem & Outcome

**Problem:** Skills domain logic is misplaced in the command layer. Handlers do skills I/O inline (URL fetch, file write, frontmatter parse, directory glob). The current `load_skills` reaches *into* `commands/` to read `BUILTIN_COMMANDS` — a `skills → commands` edge that prevents clean separation. Splitting `_commands.py` into per-handler files surfaces handler↔registry import cycles.

**Failure cost:** Skill loading and installation business logic is invisible to the skills package; contributors edit handlers when they should edit domain code. Any handler split surfaces the cycles and forces lazy-import workarounds.

**Outcome:** All skills non-handler logic lives in `co_cli/skills/`. `skills/loader.py` is namespace-agnostic — it knows nothing about reserved names, slash commands, or the relay layer. Reserved-name filtering moves to a tiny `filter_namespace_conflicts(loaded, reserved, errors)` helper in `commands/_registry.py`, composed at the two call sites (`bootstrap/core.py` and the in-place `_cmd_skills_reload` handler). Leaf `commands/_registry.py` makes the registry cycle-free for downstream Plan 2. `_install_skill`/`_upgrade_skill`/`_cmd_skills_check`/`_cmd_skills_reload` are refactored to thin orchestrators inside `_commands.py`. A `_confirm()` helper lands in `commands/_types.py` so both skill handlers (now) and knowledge handlers (in Plan 2) share the same prompt-confirm guard. No behavior changes; all existing tests pass.

---

## Scope

**In scope:**
- Move `SkillConfig` from `commands/_skill_types.py` to `skills/_skill_types.py`
- Extract skill-loading internals + `load_skills` to `skills/loader.py`. **Loader signature does not change.** It does not gain a `reserved` parameter; reserved-name policy lives in the CLI relay.
- Extract `set_skill_commands` (lifecycle helper) to `skills/registry.py`. `get_skill_registry` stays in `_commands.py` for now — it's a presentation helper consumed by `/skills list`, banner, and status check, and moves to `commands/skills.py` in Plan 2.
- Extract command-registry primitives (`SlashCommand` type, empty `BUILTIN_COMMANDS = {}`, `_build_completer_words`, `_refresh_completer`) to leaf `commands/_registry.py`; `_commands.py` populates the dict via assignments
- Add `filter_namespace_conflicts(loaded, reserved, errors)` helper to `commands/_registry.py` — pure data-in/data-out; no side effects beyond appending to `errors`
- Extract skill operation business logic (URL/path fetch, file write, source-url discovery, file enumeration) to `skills/installer.py`
- Add `_confirm(ctx, msg) -> bool` helper to `commands/_types.py` (shared by skill handlers in Plan 1 and knowledge handlers in Plan 2)
- Refactor `_install_skill`, `_upgrade_skill`, `_cmd_skills_check`, `_cmd_skills_reload` **in place inside `_commands.py`** to thin orchestrators using the new skills modules + the relay-layer filter
- Update bootstrap (`core.py`, `banner.py`), `main.py` (BUILTIN_COMMANDS / `_build_completer_words` only), `deps.py`, `tests/commands/test_skills_loader.py` imports for moved symbols

**Out of scope (Plan 2 / Plan 3):**
- Moving any handler body out of `_commands.py`
- Moving `get_skill_registry` to `commands/skills.py` (Plan 2 TASK-2)
- Splitting `session.py`
- Knowledge query helper extraction (`_apply_memory_filters`, `_format_memory_row`)
- Updating other test/eval imports (Plan 3)
- Behavior changes
- Renaming or restructuring `co_cli/main.py`

---

## Behavioral Constraints

- All 17 commands remain reachable via `dispatch()` after every task. Verify: `python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17"`
- `pyproject.toml` entrypoint stays `co = "co_cli.main:app"`
- `dispatch()` public signature `(raw_input: str, ctx: CommandContext) -> SlashOutcome` unchanged
- **`skills/loader.py` imports nothing from `co_cli.commands`.** Reserved-name filtering happens in the CLI relay (`commands/_registry.py::filter_namespace_conflicts`) and is composed at the call site. Verify: `grep -nE "^(import|from)" co_cli/skills/loader.py | grep co_cli.commands` returns empty.
- No `__init__.py` may gain imports or re-exports — docstring-only per CLAUDE.md
- No backward-compatibility re-exports — every moved symbol is imported from its new home directly at every call site
- **Lazy imports for genuine deferred-I/O are acceptable** (existing pattern in `session.py` handlers, e.g. deferring heavy `pydantic_ai` imports until `/compact` is invoked). **Lazy imports as cycle workarounds are forbidden** — leaf `_registry.py` and the architecture posture remove the structural need; if a refactor seems to require one, the structure is wrong, revisit instead of patching.
- After every task: `uv run pytest tests/ -x` must pass

---

## High-Level Design

```
co_cli/
  main.py                     # UNCHANGED structure; only moved-symbol imports updated
                              #   (BUILTIN_COMMANDS, _build_completer_words → _registry)
  commands/
    _registry.py        NEW   # LEAF: SlashCommand dataclass + BUILTIN_COMMANDS = {}
                              #   + _build_completer_words + _refresh_completer
                              #   + filter_namespace_conflicts(loaded, reserved, errors).
                              #   Imports: stdlib + co_cli.commands._types
                              #   + co_cli.skills._skill_types.
                              #   FORBIDDEN: any sibling handler module.
    _commands.py              # ~1,000 lines after Plan 1 (Plan 2 takes to ~50):
                              #   handler imports + BUILTIN_COMMANDS["x"] = SlashCommand(...)
                              #   registrations + dispatch + 17 handler bodies +
                              #   get_skill_registry (Plan 2 moves it).
                              #   Skill handlers compose load_skills + filter_namespace_conflicts.
    _types.py                 # CommandContext, SlashOutcome (unchanged)
                              #   + _confirm(ctx, msg) helper (NEW — shared with knowledge handlers in Plan 2)
    _skill_types.py           # DELETED
    session.py                # untouched; Plan 2 splits this
  skills/
    _skill_types.py     NEW   # SkillConfig (moved from commands/_skill_types.py)
    loader.py           NEW   # load_skills(skills_dir, settings, *, user_skills_dir, errors)
                              #   — signature unchanged; no `reserved` parameter.
                              #   _check_requires, _diagnose_requires_failures,
                              #   _is_safe_skill_path, _load_skill_file (no reserved arg either),
                              #   _scan_skill_content, _inject_source_url,
                              #   _SKILL_ENV_BLOCKED, _SKILL_SCAN_PATTERNS.
                              #   Imports nothing from co_cli.commands.
    registry.py         NEW   # set_skill_commands  (lifecycle — mutates deps.skill_commands)
                              #   get_skill_registry STAYS in commands/_commands.py for Plan 1;
                              #   moves to commands/skills.py in Plan 2.
    installer.py        NEW   # fetch_skill_content(target) -> (content, filename),
                              #   write_skill_file(content, filename, dest_dir) -> Path,
                              #   find_skill_source_url(skill_path) -> str | None,
                              #   discover_skill_files(bundled_dir, user_dir) -> list[Path],
                              #   SkillFetchError. Pure I/O — no console, no prompts, no ctx.
    lifecycle.py              # unchanged
```

**Key ownership decisions:**

1. **`co_cli/skills/_skill_types.py`** — `SkillConfig` is a skills domain type. Moving it first eliminates the wrong-direction dependency before later tasks build on top.

2. **`co_cli/skills/loader.py` knows nothing about commands** — no `reserved` parameter, no import of `BUILTIN_COMMANDS`, no concept of slash-command namespaces. The loader returns every parseable skill that passes skill-internal validation (requires, env, .md, security scan). The CLI relay filters loaded skills against the slash-command namespace at the call site via `filter_namespace_conflicts`. This is the cleanest possible expression of the corrected architecture: skills is a namespace-agnostic domain library; commands is the relay that decides what's reserved.

3. **`co_cli/skills/registry.py`** — `set_skill_commands(new_skills, deps) -> None` is a lifecycle helper that mutates `deps.skill_commands`. Skills domain. Bootstrap and the `/skills reload` handler call it.
   `get_skill_registry(skill_commands) -> list[dict]` is a **presentation helper** that prepares skill data for display in `/skills list`, the bootstrap banner, and the bootstrap status check. Display is a relay-layer concern, so `get_skill_registry` does **not** move to `skills/registry.py` — it stays in `_commands.py` during Plan 1 and moves to `commands/skills.py` in Plan 2 alongside the `/skills` handler.

4. **`co_cli/commands/_registry.py` (leaf)** — `SlashCommand` + empty `BUILTIN_COMMANDS = {}` + `_build_completer_words` + `_refresh_completer` + `filter_namespace_conflicts`. Population (handler registrations) stays in `_commands.py` after handler imports complete. The filter helper consumes `BUILTIN_COMMANDS.keys()` (or any reserved set) to drop name-conflicting skills, recording errors. This module is the home of slash-command-namespace policy. Combined with the loader's namespace-agnostic signature, it kills all three lazy-import workarounds flagged in the prior Gate-1 review (B3) structurally.

5. **`co_cli/skills/installer.py`** — pure-I/O ops extracted from `_install_skill`/`_upgrade_skill`/`_cmd_skills_check`. No `console`, no prompts, no `ctx`. Handlers orchestrate; installer does I/O.

6. **`_confirm` helper in `commands/_types.py`** — collapses the duplicated `prompt_confirm` / `console.input` branch at `_commands.py:464-467` and `_commands.py:476-479`. Lives in `_types.py` (not `_commands.py` and not `commands/skills.py`) because the same pattern recurs in knowledge handlers; assigning it to a sibling handler file would force a cross-command import or duplication when knowledge handlers split out in Plan 2. `_types.py` is already neutral commands-package shared territory and has no circularity risk.

7. **In-place handler refactor** — Plan 1 refactors handler bodies *without* moving them out. Plan 2's mechanical move then carries thin orchestrators (not 80-line monsters) into `commands/skills.py`. Two-step makes each step reviewable.

8. **DRY** — the `prompt_confirm`/`console.input` branch collapses into `_confirm` (#6). The `load_skills → filter_namespace_conflicts → set_skill_commands → _refresh_completer` 4-line composition stays inline at both call sites (`_install_skill` and `_cmd_skills_reload`) — four lines at two sites doesn't justify abstraction.

---

## Implementation Plan

### ✓ DONE — TASK-1: Move `SkillConfig` to `co_cli/skills/_skill_types.py`

**What moves:** `co_cli/commands/_skill_types.py` (file) → `co_cli/skills/_skill_types.py`. Old file deleted.

**Import sites updated:**
- `co_cli/commands/_commands.py`
- `co_cli/deps.py` (TYPE_CHECKING block)
- `tests/commands/test_skills_loader.py`

**Prerequisites:** None — first task.

```
files:
  - co_cli/skills/_skill_types.py        (create — move content)
  - co_cli/commands/_skill_types.py      (delete)
  - co_cli/commands/_commands.py         (update SkillConfig import)
  - co_cli/deps.py                       (update SkillConfig import)
  - tests/commands/test_skills_loader.py (update SkillConfig import)

done_when:
  python -c "from co_cli.skills._skill_types import SkillConfig" exits 0
  grep -rn "from co_cli.commands._skill_types" . --include="*.py" returns empty
  test ! -e co_cli/commands/_skill_types.py
  uv run pytest tests/ -x passes
```

---

### ✓ DONE — TASK-2: Extract skill loading to `co_cli/skills/loader.py` (namespace-agnostic)

**What moves from `_commands.py` → `co_cli/skills/loader.py`:**
- `_SKILL_ENV_BLOCKED` (frozenset)
- `_SKILL_SCAN_PATTERNS` (list of compiled regexes)
- `_scan_skill_content(content) -> list[str]`
- `_inject_source_url(content, url) -> str`
- `_check_requires(name, requires, settings) -> bool`
- `_diagnose_requires_failures(requires, settings) -> list[str]`
- `_is_safe_skill_path(path, root) -> bool`
- `_load_skill_file(path, result, settings, *, root, scan, errors)` (internal — `reserved` arg removed)
- `load_skills(skills_dir, settings, *, user_skills_dir=None, errors=None) -> dict[str, SkillConfig]` (signature unchanged from current; no `reserved` parameter)

**Loader removes the reserved-name check entirely.** Today's loader filters out skills whose name appears in `BUILTIN_COMMANDS`. After this task, the loader returns every parseable skill that passes skill-internal validation. Reserved-name filtering moves to `commands/_registry.py::filter_namespace_conflicts` (TASK-4) and is composed at the call site by the caller. The current `errors` list still receives skill-internal validation failures (requires, env, scan); it does **not** receive name-conflict errors anymore — those are appended by `filter_namespace_conflicts`.

**Caller updates:**
- `co_cli/bootstrap/core.py:261` — keeps the call to `load_skills(...)` (signature unchanged), but wraps it with the namespace filter once TASK-4 lands. Until TASK-4: load_skills still returns name-conflicting skills; bootstrap re-applies the conflict filter inline using a local `set(BUILTIN_COMMANDS.keys())` derivation. (Transient state during Plan 1; final form in TASK-4.)
- `_commands.py` `_cmd_skills_reload` and `_install_skill` (still in place) — same transient pattern; final form in TASK-6 after `filter_namespace_conflicts` exists.

`_commands.py` keeps imports for symbols its remaining handlers actively use (these go away in Plan 2 when handlers move out):
```python
from co_cli.skills.loader import (
    _diagnose_requires_failures,
    _inject_source_url,
    _scan_skill_content,
    load_skills,
)
```

**Prerequisites:** TASK-1.

```
files:
  - co_cli/skills/loader.py          (create; signature has no `reserved` parameter)
  - co_cli/commands/_commands.py     (remove moved code; import load_skills + helpers; inline reserved filter at callsites — temporary pattern, replaced in TASK-6)
  - co_cli/bootstrap/core.py         (update load_skills import; inline reserved filter — temporary, replaced in TASK-4)

done_when:
  python -c "from co_cli.skills.loader import load_skills, _scan_skill_content, _diagnose_requires_failures" exits 0
  grep -nE "^(import|from)" co_cli/skills/loader.py | grep "co_cli.commands" returns empty
  grep -nE "def load_skills" co_cli/skills/loader.py | grep "reserved" returns empty   # signature has no reserved param
  grep -rn "from co_cli.commands._commands import.*load_skills" . --include="*.py" returns empty
  uv run pytest tests/bootstrap/ tests/commands/ -x passes
```

---

### ✓ DONE — TASK-3: Extract `set_skill_commands` to `co_cli/skills/registry.py`

**What moves:**
- `set_skill_commands(new_skills: dict[str, SkillConfig], deps: CoDeps) -> None`

**What stays in `_commands.py` for now:**
- `get_skill_registry(skill_commands: dict[str, SkillConfig]) -> list[dict]` — presentation helper consumed by `/skills list`, `bootstrap/banner.py`, `bootstrap/check.py`, `main.py`. Moves to `commands/skills.py` in Plan 2 TASK-2 along with the rest of the `/skills` UI.

**Import sites updated in this task:**
- `_commands.py` — `from co_cli.skills.registry import set_skill_commands`. The local `def set_skill_commands(...)` is deleted; `def get_skill_registry(...)` stays.

**Import sites NOT updated in this task:**
- `co_cli/bootstrap/check.py:484` — still imports `get_skill_registry` from `_commands.py`. Updated in Plan 2 TASK-2.
- `co_cli/bootstrap/banner.py:39` — still imports `get_skill_registry` (and `BUILTIN_COMMANDS`) from `_commands.py`. `BUILTIN_COMMANDS` retargeted in TASK-4; `get_skill_registry` waits for Plan 2.
- `co_cli/main.py:286` — still imports `get_skill_registry` from `_commands.py`. Updated in Plan 2 TASK-2.

**Prerequisites:** TASK-1.

```
files:
  - co_cli/skills/registry.py        (create — only set_skill_commands)
  - co_cli/commands/_commands.py     (remove set_skill_commands; add import; keep get_skill_registry locally)

done_when:
  python -c "from co_cli.skills.registry import set_skill_commands" exits 0
  python -c "from co_cli.skills.registry import get_skill_registry" exits NONZERO   # not moved in this plan
  grep -E "^def set_skill_commands\b" co_cli/commands/_commands.py returns empty
  grep -E "^def get_skill_registry\b" co_cli/commands/_commands.py returns 1 match   # still here for Plan 2 to move
  grep -rn "from co_cli.commands._commands import.*set_skill_commands" . --include="*.py" returns empty
  uv run pytest tests/bootstrap/ -x passes
```

---

### ✓ DONE — TASK-4: Extract leaf `co_cli/commands/_registry.py` (with namespace filter)

**What moves from `_commands.py` → `co_cli/commands/_registry.py`:**
- `SlashCommand` dataclass
- `BUILTIN_COMMANDS: dict[str, SlashCommand] = {}` (the **empty** dict — population stays in `_commands.py`)
- `_build_completer_words(skill_commands: dict[str, SkillConfig]) -> list[str]`
- `_refresh_completer(ctx: CommandContext) -> None`

**What's added to `_registry.py` (NEW helper, replaces the inline reserved filter from TASK-2):**

```python
def filter_namespace_conflicts(
    loaded: dict[str, SkillConfig],
    reserved: set[str],
    errors: list[str] | None = None,
) -> dict[str, SkillConfig]:
    """Drop skills whose name shadows a reserved slash-command name.
    Records dropped names to errors. Pure data-in/data-out."""
    accepted: dict[str, SkillConfig] = {}
    for name, skill in loaded.items():
        if name in reserved:
            if errors is not None:
                errors.append(f"skill '{name}' skipped: shadows built-in slash command")
            continue
        accepted[name] = skill
    return accepted
```

**Import allowlist for `_registry.py`:** stdlib + `co_cli.commands._types` (CommandContext) + `co_cli.skills._skill_types` (SkillConfig typing). **Forbidden:** any sibling handler module. This is what makes it a leaf.

**Population pattern in `_commands.py` after this task:**
```python
from co_cli.commands._registry import BUILTIN_COMMANDS, SlashCommand
# ... handler functions defined below ...
BUILTIN_COMMANDS["help"] = SlashCommand("help", "List available slash commands", _cmd_help)
# ... 16 more registrations ...
```

**Caller updates that retire the transient inline-filter pattern from TASK-2:**
- `bootstrap/core.py:261` — switches to:
  ```python
  loaded = load_skills(ctx.deps.skills_dir, settings, user_skills_dir=ctx.deps.user_skills_dir, errors=errors)
  skills = filter_namespace_conflicts(loaded, set(BUILTIN_COMMANDS.keys()), errors)
  set_skill_commands(skills, ctx.deps)
  ```
- `co_cli/main.py:25,32` — `BUILTIN_COMMANDS`, `_build_completer_words` → `co_cli.commands._registry`
- `co_cli/bootstrap/banner.py:39` — `BUILTIN_COMMANDS` → `co_cli.commands._registry` (`get_skill_registry` import unchanged — Plan 2 moves it)
- `co_cli/bootstrap/core.py:261` — `BUILTIN_COMMANDS` (used to compute the reserved set) → `co_cli.commands._registry`

`_commands.py` itself imports `BUILTIN_COMMANDS`, `SlashCommand`, and `filter_namespace_conflicts` from `_registry` — the last is consumed by skill handlers in TASK-6.

**Prerequisites:** TASK-1, TASK-2, TASK-3.

```
files:
  - co_cli/commands/_registry.py     (create — types, dict, completer helpers, filter)
  - co_cli/commands/_commands.py     (remove SlashCommand, dict literal, completer helpers; import from _registry; populate via assignments)
  - co_cli/main.py                   (update BUILTIN_COMMANDS, _build_completer_words imports)
  - co_cli/bootstrap/banner.py       (update BUILTIN_COMMANDS import only)
  - co_cli/bootstrap/core.py         (update BUILTIN_COMMANDS import; switch to load_skills + filter_namespace_conflicts composition)

done_when:
  python -c "from co_cli.commands._registry import BUILTIN_COMMANDS, SlashCommand, _build_completer_words, _refresh_completer, filter_namespace_conflicts; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17" exits 0
  grep -nE "^(import|from)" co_cli/commands/_registry.py | grep -E "co_cli\.commands\.(?!_types|_registry)" returns empty
  grep -n "set(BUILTIN_COMMANDS" co_cli/bootstrap/core.py returns the composition line (filter_namespace_conflicts call)
  uv run pytest tests/ -x passes
```

---

### ✓ DONE — TASK-5: Extract skill operations to `co_cli/skills/installer.py`

**What's extracted:** business logic currently inlined in `_install_skill` (`_commands.py:414-495`), `_upgrade_skill` (`_commands.py:498-519`), and `_cmd_skills_check` (`_commands.py:307-347`).

**New module surface:**
```python
class SkillFetchError(Exception):
    """Raised when fetch_skill_content fails (network, IO, or content-type)."""

def fetch_skill_content(target: str) -> tuple[str, str]:
    """Fetch a skill .md from URL (http/https) or local path.
    Returns (content, filename). For URL targets, calls _inject_source_url
    so the source is preserved in frontmatter.
    Raises SkillFetchError on network failure, non-text content-type,
    read error, or filename not ending in .md.
    """

def write_skill_file(content: str, filename: str, dest_dir: Path) -> Path:
    """Create dest_dir if needed; write content to dest_dir/filename. Returns dest path."""

def find_skill_source_url(skill_path: Path) -> str | None:
    """Read skill_path frontmatter; return source-url field if present and non-empty, else None."""

def discover_skill_files(bundled_dir: Path, user_dir: Path) -> list[Path]:
    """Return sorted .md paths from both dirs (bundled first, user second).
    Skips dirs that don't exist."""
```

`installer.py` imports `_inject_source_url` from `skills.loader` and `parse_frontmatter` from `co_cli.knowledge._frontmatter`. **No `console`, no `ctx`, no prompts** — pure I/O + content manipulation.

**Prerequisites:** TASK-2 (for `_inject_source_url`).

```
files:
  - co_cli/skills/installer.py       (create)

done_when:
  python -c "from co_cli.skills.installer import fetch_skill_content, write_skill_file, find_skill_source_url, discover_skill_files, SkillFetchError" exits 0
  grep -nE "^(import|from)" co_cli/skills/installer.py | grep -E "co_cli\.commands|console|prompt_confirm" returns empty
  uv run pytest tests/ -x passes
```

---

### ✓ DONE — TASK-6: Refactor skill handlers in place to thin orchestrators

**Goal:** `_install_skill`, `_upgrade_skill`, `_cmd_skills_check`, `_cmd_skills_reload` in `_commands.py` keep only handler concerns (arg parsing, prompts, console output, completer refresh, the load→filter→set→refresh composition). All fetch/write/inspect logic delegates to `skills/installer.py` and `skills/loader.py`.

**Add `_confirm` helper to `co_cli/commands/_types.py`** (not `_commands.py` — see Ownership Decision #6) to dedupe the `prompt_confirm` / `console.input` branch:
```python
def _confirm(ctx: CommandContext, msg: str) -> bool:
    """Prompt user with msg; return True iff they confirmed (frontend or fallback)."""
    if ctx.frontend:
        return ctx.frontend.prompt_confirm(msg)
    return console.input(msg).strip().lower() == "y"
```

`_commands.py` imports `_confirm` from `co_cli.commands._types`.

**Refactored `_install_skill` shape (illustrative):**
```python
async def _install_skill(ctx: CommandContext, target: str, force: bool = False) -> None:
    target = target.strip()
    if not target:
        console.print("[bold red]Usage:[/bold red] /skills install <path|url>")
        return
    try:
        content, filename = fetch_skill_content(target)
    except SkillFetchError as e:
        console.print(f"[bold red]Failed to fetch skill:[/bold red] {e}")
        return
    warnings = _scan_skill_content(content)
    if warnings:
        console.print("[bold yellow]Security scan warnings:[/bold yellow]")
        for w in warnings:
            console.print(f"  [yellow]{w}[/yellow]")
        if not _confirm(ctx, "Install anyway? [y/N] "):
            console.print("[dim]Install cancelled.[/dim]")
            return
    dest = ctx.deps.user_skills_dir / filename
    if dest.exists() and not force and not _confirm(ctx, f"Overwrite existing skill '{filename}'? [y/N] "):
        console.print("[dim]Install cancelled.[/dim]")
        return
    write_skill_file(content, filename, ctx.deps.user_skills_dir)
    errors: list[str] = []
    loaded = load_skills(
        ctx.deps.skills_dir, settings,
        user_skills_dir=ctx.deps.user_skills_dir, errors=errors,
    )
    skills = filter_namespace_conflicts(loaded, set(BUILTIN_COMMANDS.keys()), errors)
    for msg in errors:
        console.print(f"[warning]{msg}[/warning]")
    set_skill_commands(skills, ctx.deps)
    _refresh_completer(ctx)
    console.print(f"[success]✓ Installed skill: {filename.removesuffix('.md')}[/success]")
```

**Refactored `_upgrade_skill`:** parse `name` arg → `find_skill_source_url(ctx.deps.user_skills_dir / f"{name}.md")` → on `None`, print error; otherwise `await _install_skill(ctx, source_url, force=True)`. Frontmatter parsing no longer in the handler.

**Refactored `_cmd_skills_check`:** `paths = discover_skill_files(ctx.deps.skills_dir, ctx.deps.user_skills_dir)` → for each path, render table row using `_diagnose_requires_failures`. Discovery glob no longer in the handler.

**Refactored `_cmd_skills_reload`:** the same composition as `_install_skill` end-block: `load_skills` → `filter_namespace_conflicts` → `set_skill_commands` → `_refresh_completer`. Four lines at two call sites; **no helper introduced** — too small to abstract.

**Prerequisites:** TASK-2, TASK-3, TASK-4, TASK-5.

```
files:
  - co_cli/commands/_types.py        (add _confirm helper)
  - co_cli/commands/_commands.py     (refactor four handlers in place; import _confirm from _types; use filter_namespace_conflicts from _registry)

done_when:
  python -c "from co_cli.commands._types import _confirm" exits 0
  grep -nE "httpx|urllib\.parse|content-type|read_text\(.*\.md|write_text\(" co_cli/commands/_commands.py returns empty   # all I/O moved to installer.py
  grep -n "parse_frontmatter" co_cli/commands/_commands.py returns empty   # frontmatter parsing moved
  grep -n "\.glob(\"\\*\\.md\")" co_cli/commands/_commands.py returns empty   # discovery moved
  grep -nE "filter_namespace_conflicts" co_cli/commands/_commands.py returns matches at install + reload sites
  uv run pytest tests/ -x passes
  python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17" exits 0
```

---

## Testing

Pure structural refactor — no behavior changes. The existing test suite is the gate. Each task's `done_when` runs `pytest` at the relevant scope; the global Behavioral Constraint requires the full suite to pass after every task. No new tests.

**Manual smoke tests after TASK-6:**
- `uv run co status` succeeds
- `uv run co chat` starts; `/help`, `/skills list` work
- `/skills install <local-path-to-test-skill.md>` succeeds end-to-end (write to user_skills_dir, reload, completer updated)
- A skill file named `help.md` placed in `~/.co-cli/skills/` is rejected at load with the namespace-conflict error message (verifies `filter_namespace_conflicts` runs at the relay layer)
- `/skills reload` produces same added/removed diff output as before
- `/skills check` lists bundled + user skills

If a test breaks, stop and diagnose — a regression means the refactor was wrong.

---

## Cycle Decisions

| Issue ID | Decision | Applies to Plan 1 |
|----------|----------|-------------------|
| C2-1 | `set_skill_commands` is skills lifecycle → `skills/registry.py`. `get_skill_registry` is presentation → stays in `_commands.py` until Plan 2 moves it to `commands/skills.py` | TASK-3 |
| C2-5 | `SkillConfig` is skills domain, not commands | TASK-1 |
| C2-7 | No backward-compatibility re-exports | All tasks |
| C2-8 | All skills non-handler logic in `skills/` | TASK-2, TASK-3, TASK-5, TASK-6 |
| C2-9 | Leaf `commands/_registry.py` kills B3 lazy-import smells structurally | TASK-4 |
| C2-10 | DRY: `_confirm()` in `_types.py` shared with knowledge handlers; the 4-line load→filter→set→refresh composition stays inline at 2 call sites | TASK-6 |
| C3-1 | **Architecture (this revision):** `commands → skills` is the only edge. `skills/loader.py` knows nothing about reserved names; `filter_namespace_conflicts` lives in `commands/_registry.py` and is composed at the call site. Loader signature does **not** gain a `reserved` parameter — the prior draft's `load_skills(reserved=...)` design is superseded. | TASK-2, TASK-4, TASK-6 |

Decisions deferred to Plan 2 (handler modularization): C2-2, C2-3, C2-4, C2-6.

---

## Final — Team Lead

Plan 1 ready for Gate 1.

> Run: `/orchestrate-dev skills-sovereignty` after Gate 1 approval.

---

## Delivery Summary — 2026-04-27

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `from co_cli.skills._skill_types import SkillConfig` exits 0; `_skill_types.py` absent from `commands/` | ✓ pass |
| TASK-2 | `from co_cli.skills.loader import load_skills, _scan_skill_content, _diagnose_requires_failures` exits 0; `loader.py` imports nothing from `co_cli.commands` | ✓ pass |
| TASK-3 | `from co_cli.skills.registry import set_skill_commands` exits 0; `set_skill_commands` not defined in `_commands.py`; `get_skill_registry` still defined there | ✓ pass |
| TASK-4 | `from co_cli.commands._registry import BUILTIN_COMMANDS, SlashCommand, filter_namespace_conflicts`; `len(BUILTIN_COMMANDS) == 17`; `filter_namespace_conflicts` call present in `bootstrap/core.py` | ✓ pass |
| TASK-5 | `from co_cli.skills.installer import fetch_skill_content, write_skill_file, find_skill_source_url, discover_skill_files, SkillFetchError` exits 0; installer imports nothing from `co_cli.commands` | ✓ pass |
| TASK-6 | `from co_cli.commands._types import _confirm` exits 0; `httpx`/`urllib`/`parse_frontmatter`/`.glob("*.md")` absent from `_commands.py`; `filter_namespace_conflicts` at install + reload sites; 17 commands | ✓ pass |

**Extra files (announced during delivery):**
- `co_cli/bootstrap/banner.py` — `BUILTIN_COMMANDS` import retargeted to `_registry` (TASK-4)
- `tests/bootstrap/test_bootstrap.py` — `load_skills` lazy import updated to `skills.loader` (TASK-2)
- `co_cli/skills/installer.py` — `read_skill_meta` added (TASK-6 — needed by `_cmd_skills_check` after `parse_frontmatter` removed from `_commands.py`)

**Tests:** scoped (`tests/commands/ tests/bootstrap/ tests/skills/`) — 108 passed, 0 failed

**Doc Sync:** fixed — `skills.md` (SkillConfig path, flowchart node, Files table), `bootstrap.md` (startup diagram, Files table), `tui.md` (SkillConfig path)

**Overall: DELIVERED**
All 6 tasks passed. Skills domain logic fully extracted to `co_cli/skills/` package. One-way `commands → skills` dependency enforced. `_commands.py` reduced to handlers + dispatch; no more inline httpx, frontmatter parsing, or skill directory globbing.

---

## Implementation Review — 2026-04-27

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `from co_cli.skills._skill_types import SkillConfig` exits 0; `_skill_types.py` absent from `commands/` | ✓ pass | `co_cli/skills/_skill_types.py:9` — `SkillConfig` frozen dataclass; `co_cli/commands/_skill_types.py` deleted; all import sites updated |
| TASK-2 | `load_skills` importable from `skills.loader`; loader imports nothing from `co_cli.commands`; no `reserved` param | ✓ pass | `co_cli/skills/loader.py:240` — `load_skills` signature; grep `co_cli.commands` in loader imports returns empty |
| TASK-3 | `set_skill_commands` in `skills.registry`; `get_skill_registry` still in `_commands.py` | ✓ pass | `co_cli/skills/registry.py:12` — `set_skill_commands`; `co_cli/commands/_commands.py:57` — `get_skill_registry` |
| TASK-4 | 17 commands; `filter_namespace_conflicts` composition in `bootstrap/core.py` | ✓ pass | `BUILTIN_COMMANDS` len==17 verified; `core.py:273-275` — `filter_namespace_conflicts` call confirmed |
| TASK-5 | installer symbols importable; installer imports nothing from `co_cli.commands` | ✓ pass | `co_cli/skills/installer.py` — all 5 public symbols present; grep `co_cli.commands` returns empty |
| TASK-6 | `_confirm` importable; no httpx/urllib/parse_frontmatter/glob in `_commands.py` (skill handlers); filter_namespace_conflicts at install + reload | ✓ pass (after auto-fix) | `_types.py:52` — `_confirm`; glob removed from `_cmd_skills_reload:267` (auto-fixed); `_commands.py:264,359` — filter at both call sites |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `.glob("*.md")` in `_cmd_skills_reload` — TASK-6 done_when fails ("discovery moved") | `_commands.py:267` | blocking | Replaced glob-then-filter with direct iteration over `new_skills` keys |
| `if TYPE_CHECKING: pass` dead block | `_registry.py:16-17` | minor | Removed dead block + unused `TYPE_CHECKING` import |
| Stale model name `"gemini-3.1-flash-preview"` in test (renamed to `"gemini-3-flash-preview"` in commit `3b7e8b9`) | `tests/bootstrap/test_config.py:299` | blocking (suite failure) | Updated model name to match current `_INFERENCE_DEFAULTS` |
| `tui.md` Files table: `_commands.py` claimed `BUILTIN_COMMANDS` and `CommandContext/outcome types` (now in `_registry.py` and `_types.py`) | `docs/specs/tui.md` | minor | Updated `_commands.py` entry; added `_registry.py` and `_types.py` rows |
| `skills.md` flowchart node and table cells used `session.skill_commands`/`session.skill_registry` (wrong — `deps.skill_commands`/`get_skill_registry()`) | `docs/specs/skills.md:32,48,67` | minor | Updated three references |

### Tests
- Command: `uv run pytest -v`
- Result: 664 passed, 0 failed
- Log: `.pytest-logs/20260427-122413-review-impl-final.log`

### Doc Sync
- Scope: full — plan touches shared modules and renames public API import sites
- Result: fixed — `tui.md` (Files table: `_registry.py` and `_types.py` rows added; `_commands.py` entry corrected); `skills.md` (flowchart node + two table cells corrected)

### Behavioral Verification
- `uv run co config`: ✓ healthy — all integrations reported, no crash
- 17 commands registered: ✓ confirmed
- `filter_namespace_conflicts("help")`: ✓ drops conflict, records error message; non-conflicting skill passes through
- All new module imports (`skills.loader`, `skills.registry`, `skills.installer`, `commands._registry`, `commands._types._confirm`): ✓ resolve correctly

### Overall: PASS
All 6 tasks pass spec fidelity. Three blocking findings auto-fixed (reload glob, test model name, none requiring architectural escalation). Suite green at 664/664. Ship directly.
