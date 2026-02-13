# TODO: Runtime Slash Command Overrides

## Goal

Add session-scoped slash commands that override Co's behavior for the current session or turn. These are lightweight CoDeps flags — no separate preferences system, no config files, no prompt injection.

## Background

Salvaged from Phase 2b (User Preferences), which was cancelled as redundant with the memory system + personality system. The runtime override commands were the only non-redundant piece: they provide immediate, temporary behavior toggles that memories and personality cannot cover (memories persist across sessions; these are ephemeral).

## Commands

| Command | Effect | Scope | CoDeps Field |
|---------|--------|-------|--------------|
| `/verbose on\|off` | Force detailed explanations | Current turn | `override_verbose: bool \| None` |
| `/terse on\|off` | Ultra-minimal output | Current turn | `override_terse: bool \| None` |
| `/yolo` | Auto-approve ALL tool calls | Current session | `override_yolo: bool` |

## Design

- Override flags live in `CoDeps` as `None` (not set) or `bool` (active).
- Turn-scoped overrides (`/verbose`, `/terse`) reset to `None` after one `run_turn()` cycle.
- Session-scoped overrides (`/yolo`) persist until Co exits or user toggles off.
- `/yolo` requires intentional re-enablement after restart for safety.
- `/yolo` prints a visible warning when activated.
- When `override_verbose=True`, personality style is overridden to show reasoning and steps regardless of current style.
- When `override_terse=True`, personality style is overridden to minimal output.

## Implementation

### CoDeps changes (`co_cli/deps.py`)

Add 3 fields:
- `override_verbose: bool | None = None`
- `override_terse: bool | None = None`
- `override_yolo: bool = False`

### Command handlers (`co_cli/_commands.py`)

Register 3 commands in the command registry. Each handler mutates the `CoDeps` instance directly (same pattern as existing `/forget`).

### Chat loop integration (`co_cli/main.py`)

- Before `run_turn()`: if `override_yolo`, set `auto_confirm=True` on the agent run.
- After `run_turn()`: reset turn-scoped overrides (`override_verbose`, `override_terse`) to `None`.

### Prompt integration

- If `override_verbose=True`: append "The user has requested detailed, verbose output for this response." to the system prompt for this turn.
- If `override_terse=True`: append "The user has requested ultra-minimal, terse output for this response." to the system prompt for this turn.
- No injection when overrides are `None`.

## Effort

2-3 hours. No new files needed beyond test file.

## Success Criteria

- [ ] 3 CoDeps fields added
- [ ] 3 command handlers registered
- [ ] Turn-scoped overrides reset after each turn
- [ ] `/yolo` prints warning and persists for session
- [ ] `/yolo` does not persist across restarts
- [ ] 8+ tests in `tests/test_slash_overrides.py`

## Files

### Modified
| File | Change |
|------|--------|
| `co_cli/deps.py` | Add 3 override fields |
| `co_cli/_commands.py` | Add 3 command handlers |
| `co_cli/main.py` | Turn-scoped reset logic, yolo → auto_confirm |
| `co_cli/prompts/__init__.py` | Conditional verbose/terse injection |

### New
| File | Purpose |
|------|---------|
| `tests/test_slash_overrides.py` | 8+ functional tests |
