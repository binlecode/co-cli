# Plan: Commands Import Audit

**Task type:** refactor

**Position in series:** Plan 3 of 3 in the commands-modularization effort.
- Plan 1 (skills-sovereignty): all skills non-handler logic moved to `co_cli/skills/`; leaf `commands/_registry.py` extracted; skill handlers refactored thin in place. **Prerequisite.**
- Plan 2 (handler-modularization): every handler in its own file; `session.py` deleted; knowledge query helpers extracted. **Prerequisite.**
- **Plan 3 (this plan):** external callers (tests, evals) updated to import from new homes; final audit greps codified as ship gates; no lazy imports as cycle workarounds anywhere in `commands/`.

---

## Context

After Plans 1 and 2 land:

- `co_cli/skills/` owns: `_skill_types.py`, `loader.py`, `registry.py`, `installer.py`
- `co_cli/knowledge/_query.py` owns: `_apply_memory_filters`, `_format_memory_row`
- `co_cli/commands/_registry.py` (leaf) owns: `SlashCommand`, `BUILTIN_COMMANDS`, `_build_completer_words`, `_refresh_completer`
- `co_cli/commands/_commands.py` owns: handler imports, `BUILTIN_COMMANDS` registrations, `dispatch()` — and **only** `dispatch()` is a legitimate public symbol from this module
- Every slash command lives in its own file under `co_cli/commands/`

**External callers (tests + evals) that may still import moved symbols from `co_cli.commands._commands`:**
- `tests/commands/test_commands.py:18` — imports symbols including `BUILTIN_COMMANDS`, `CommandContext`, `dispatch`, `ReplaceTranscript`
- `tests/bootstrap/test_bootstrap.py:304` — imports `get_skill_registry`, `load_skills`
- `tests/tools/test_background.py:390,419,446` — imports `BUILTIN_COMMANDS`, `CommandContext`
- `tests/context/test_history.py:21` — imports `CommandContext`, `ReplaceTranscript`, `dispatch`
- `evals/eval_bootstrap_flow_quality.py:33` — imports `BUILTIN_COMMANDS`, `_build_completer_words`, `get_skill_registry`

`tests/commands/test_skills_loader.py` is migrated in Plan 1, not here.

**This plan's job:** mechanically retarget every test/eval import to the new home; codify audit greps so future contributors can't accidentally re-introduce stale imports or lazy-import cycle workarounds.

---

## Problem & Outcome

**Problem:** External callers may still import symbols from `co_cli.commands._commands` using paths that, after Plans 1 and 2, only exist as transitive references via the registry. There is no codified safety net to catch regressions where someone re-introduces a sibling-package import inside a function body to dodge a cycle.

**Failure cost:** Tests/evals break silently when handlers move further; future refactors regress to lazy imports; the dependency direction discipline established in Plans 1–2 erodes.

**Outcome:** Only `dispatch` is legitimately imported from `co_cli.commands._commands`. All other prior callers updated to import from `_registry`, `_types`, `skills/*`, or `knowledge/_query`. No function-body imports of `co_cli.commands.*` or `co_cli.skills.*` remain in `commands/` except genuine deferred-I/O (e.g. heavy optional dependency loaded only on first use). Audit greps codified; ship-gate documented.

---

## Scope

**In scope:**
- Update `tests/commands/test_commands.py` imports
- Update `tests/bootstrap/test_bootstrap.py` imports
- Update `tests/tools/test_background.py` imports
- Update `tests/context/test_history.py` imports
- Update `evals/eval_bootstrap_flow_quality.py` imports
- Codify audit greps as `done_when` checks
- Audit and document any genuine deferred-I/O lazy imports remaining in `commands/` (whitelist if needed)

**Out of scope:**
- Behavior changes
- Any code outside `tests/`, `evals/`, and audit verification
- Re-running structural moves from Plan 1 or 2

---

## Behavioral Constraints

- All 17 commands remain reachable via `dispatch()`. Verify: `python -c "from co_cli.commands._registry import BUILTIN_COMMANDS; import co_cli.commands._commands; assert len(BUILTIN_COMMANDS) == 17"`
- After every task: `uv run pytest tests/ -x` must pass
- Final audit greps must return empty (see below)
- No backward-compatibility re-exports introduced anywhere

---

## High-Level Design

This plan has no new modules. It updates 5 callers and codifies 4 audit greps as ship gates.

**Final audit greps** (all must return empty after TASK-3):

```bash
# 1. No external caller imports from _commands except dispatch
grep -rn "from co_cli.commands._commands import" . --include="*.py" \
  | grep -v ":.*\bdispatch\b"

# 2. _commands.py itself has no leftover imports of moved domain symbols
grep -E "^from co_cli\.(skills|knowledge)" co_cli/commands/_commands.py

# 3. _registry.py imports nothing from sibling handlers
grep -nE "^(import|from)" co_cli/commands/_registry.py \
  | grep -E "co_cli\.commands\.(?!_types|_registry)"

# 4. No function-body imports of sibling packages in commands/ (except whitelisted deferred-I/O)
grep -rnE "^\s+from co_cli\." co_cli/commands/ --include="*.py" \
  | grep -vE "^[^:]+:[^:]+:\s+from co_cli\.memory\.session import"   # whitelist: existing session-handler deferred-I/O
```

**Whitelist policy:** the only acceptable function-body imports of `co_cli.*` modules in `commands/` are genuine deferred-I/O for heavy optional dependencies (e.g. `from co_cli.memory.session import new_session_path` inside `_cmd_new`). These are documented in TASK-3's whitelist. Cycle workarounds are forbidden — Plan 1 and 2 eliminated all such cycles structurally.

---

## Implementation Plan

### TASK-1 — Migrate test imports

**Files updated:**

- `tests/commands/test_commands.py` (line 18 import block):
  - `BUILTIN_COMMANDS` → `co_cli.commands._registry`
  - `CommandContext`, `ReplaceTranscript` → `co_cli.commands._types`
  - `dispatch` → `co_cli.commands._commands` (unchanged)

- `tests/bootstrap/test_bootstrap.py` (line 304):
  - `get_skill_registry` → `co_cli.commands.skills` (moved here in Plan 2 TASK-2, not to `skills/`)
  - `load_skills` → `co_cli.skills.loader`

- `tests/tools/test_background.py` (lines 390, 419, 446):
  - `BUILTIN_COMMANDS` → `co_cli.commands._registry`
  - `CommandContext` → `co_cli.commands._types`

- `tests/context/test_history.py` (line 21):
  - `CommandContext`, `ReplaceTranscript` → `co_cli.commands._types`
  - `dispatch` → `co_cli.commands._commands` (unchanged)

**Prerequisites:** None.

```
files:
  - tests/commands/test_commands.py         (update imports)
  - tests/bootstrap/test_bootstrap.py       (update imports)
  - tests/tools/test_background.py          (update imports)
  - tests/context/test_history.py           (update imports)

done_when:
  uv run pytest tests/commands/test_commands.py tests/bootstrap/test_bootstrap.py tests/tools/test_background.py tests/context/test_history.py -x passes
```

---

### TASK-2 — Migrate eval imports

**Files updated:**

- `evals/eval_bootstrap_flow_quality.py` (line 33):
  - `BUILTIN_COMMANDS`, `_build_completer_words` → `co_cli.commands._registry`
  - `get_skill_registry` → `co_cli.commands.skills` (moved here in Plan 2 TASK-2, not to `skills/`)

**Prerequisites:** None.

```
files:
  - evals/eval_bootstrap_flow_quality.py    (update imports)

done_when:
  python -c "import ast; ast.parse(open('evals/eval_bootstrap_flow_quality.py').read())" exits 0
  python -c "from evals.eval_bootstrap_flow_quality import *" exits 0   # imports resolve
```

(Evals run as standalone programs; we don't run the eval itself in this gate — only check its imports load. The eval's own gates run separately when invoked.)

---

### TASK-3 — Codify final audit and whitelist

**Audit greps that must return empty:**

```bash
# Audit 1: no external caller imports from _commands except dispatch
grep -rn "from co_cli.commands._commands import" . --include="*.py" \
  | grep -v ":.*\bdispatch\b"

# Audit 2: _commands.py itself imports no domain symbols (handlers carry their own imports now)
grep -E "^from co_cli\.(skills|knowledge)" co_cli/commands/_commands.py

# Audit 3: _registry.py is leaf — imports nothing from sibling handlers
grep -nE "^(import|from)" co_cli/commands/_registry.py \
  | grep -E "co_cli\.commands\.(?!_types|_registry)"
```

**Lazy-import whitelist for `commands/` tree:**

The following function-body imports of `co_cli.*` are intentional deferred-I/O (heavy optional dependencies, not cycle workarounds), inherited from the original `session.py` handlers and confirmed acceptable at Plan 1 review:

| File | Function | Lazy import | Reason |
|---|---|---|---|
| `co_cli/commands/new.py` | `_cmd_new` | `from co_cli.memory.session import new_session_path` | Defer memory/session module load until session rotation actually triggers |
| `co_cli/commands/compact.py` | `_cmd_compact` | `from pydantic_ai import RunContext`, `from pydantic_ai.messages import ...`, `from co_cli.context.compaction import apply_compaction`, `from co_cli.context.summarization import ...` | Defer heavy compaction stack until /compact is invoked |
| `co_cli/commands/resume.py` | `_cmd_resume` | `from co_cli.display._core import prompt_selection`, `from co_cli.memory.session_browser import ...`, `from co_cli.memory.transcript import ...` | Defer memory/UI modules until /resume invoked |
| `co_cli/commands/sessions.py` | `_cmd_sessions` | `from co_cli.memory.session_browser import ...` | Defer memory module until /sessions invoked |
| `co_cli/commands/background.py` | `_cmd_background` | `from co_cli.tools.background import BackgroundTaskState, make_task_id, spawn_task` | Defer tools/background until /background invoked (`make_task_id` renamed from `_make_task_id` in Plan 2 TASK-4) |
| `co_cli/commands/cancel.py` | `_cmd_cancel` | `from co_cli.tools.background import BackgroundCleanupError, kill_task` | Defer tools/background until /cancel invoked |
| `co_cli/commands/status.py` | `_cmd_status` | `from co_cli.bootstrap.render_status import check_security, get_status, render_security_findings, render_status_table` | Defer render_status (bootstrap) until /status invoked |

**Audit 4: every function-body `from co_cli.*` import in `commands/` must match the whitelist:**

```bash
grep -rnE "^\s+from co_cli\." co_cli/commands/ --include="*.py"
```

Each match is checked against the whitelist table above. Any match outside the whitelist is a violation — investigate root cause (likely a cycle the structural changes missed) instead of adding to the whitelist.

**Prerequisites:** TASK-1, TASK-2.

```
files:
  - (none — verification only)

done_when:
  grep -rn "from co_cli.commands._commands import" . --include="*.py" | grep -v ":.*\bdispatch\b" returns empty
  grep -E "^from co_cli\.(skills|knowledge)" co_cli/commands/_commands.py returns empty
  grep -nE "^(import|from)" co_cli/commands/_registry.py | grep -E "co_cli\.commands\.(?!_types|_registry)" returns empty
  every match of `grep -rnE "^\s+from co_cli\." co_cli/commands/ --include="*.py"` corresponds to a whitelist row above (manual cross-check)
  uv run pytest tests/ -x passes
```

---

## Testing

Pure verification + import-path migration. No behavior changes. The existing test suite (full `uv run pytest tests/ -x`) is the gate.

If audit greps return non-empty:
- Audit 1 violation: a caller still imports a moved symbol from `_commands`. Fix the import path.
- Audit 2 violation: `_commands.py` carries a stale import. Remove it.
- Audit 3 violation: `_registry.py` is no longer a leaf. Investigate — should never happen post Plan 1.
- Audit 4 violation: a function-body import in `commands/` is outside the whitelist. Investigate root cause; do not extend the whitelist without a documented reason.

---

## Cycle Decisions (carry-forward from Cycle C3)

The decisions material to Plan 3:

| Issue ID | Decision | Applies to Plan 3 |
|----------|----------|-------------------|
| C2-7 | No backward-compatibility re-exports | All tasks |
| C2-9 | No lazy imports as cycle workarounds | TASK-3 (whitelist + audit) |

---

## Final — Team Lead

Plan 3 ready for Gate 1 (after Plans 1 and 2 ship).

> Run: `/orchestrate-dev commands-import-audit` after Plan 2 lands and Plan 3's Gate 1 is approved.
