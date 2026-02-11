# TODO: User Preferences System (Phase 2b)

## Goal

Implement a workflow preferences system that adapts Co's behavior to user work contexts without changing communication style. Preferences control **what Co does** and **how deeply it explains**; personality (Phase 1b) controls **how Co communicates**.

## Problem

One-size-fits-all agent behavior fails across contexts. A senior developer debugging wants terse output with auto-approved safe commands, while a junior developer learning wants verbose explanations with explicit approval. Current Co has global settings but lacks nuanced, per-workflow preferences.

## Core Preference Dimensions

`UserPreferences` Pydantic model with 10 fields, stored in `preferences.json`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `verbosity` | `minimal \| normal \| detailed` | `normal` | Output verbosity level |
| `explain_reasoning` | `bool` | `true` | Show reasoning for decisions (tool selection, approach) |
| `show_steps` | `bool` | `false` | Narrate each step for multi-step tasks |
| `cautious_mode` | `strict \| balanced \| relaxed` | `balanced` | Approval behavior for risky operations |
| `prefer_shell_tools` | `bool` | `true` | Prefer shell commands over built-in tools |
| `search_depth` | `quick \| normal \| exhaustive` | `normal` | Search thoroughness |
| `output_format` | `conversational \| structured \| compact` | `conversational` | Output formatting style |
| `citation_style` | `inline \| footnotes \| none` | `inline` | How to cite sources |
| `track_patterns` | `bool` | `true` | Learn user patterns for knowledge system |
| `suggest_improvements` | `bool` | `true` | Offer proactive suggestions |

Personality vs preferences boundary: if it changes WHAT Co does or WHEN Co acts, it is a preference. If it changes HOW Co says it, it is personality.

## Config Approach

**Storage:** `.co-cli/preferences.json` (project) and `~/.config/co-cli/preferences.json` (user), consistent with existing `settings.json` pattern.

**Precedence:** project file > user file > built-in defaults. Project preferences completely override user preferences (no field-level merge) to avoid confusing partial overrides.

**Loading pseudocode:**
```
if .co-cli/preferences.json exists → validate and return
elif ~/.config/co-cli/preferences.json exists → validate and return
else → return UserPreferences() with all defaults
```

**Interaction with `auto_confirm`:** `auto_confirm=True` is YOLO mode (ignores `cautious_mode`). `auto_confirm=False` defers to `cautious_mode` preference.

## Prompt Injection

Preferences inject after personality, before project instructions (priority 50 in prompt assembly):

```
1. Base system.md (identity, principles, tool guidance)
2. Model conditionals ([IF gemini] / [IF ollama])
3. Personality template (communication style)
4. User preferences (workflow behavior)              ← Phase 2b
5. Project instructions (.co-cli/instructions.md)
```

**Progressive disclosure:** only non-default preferences appear in the injected prompt section. All defaults + no overrides = empty string (no injection). This keeps the prompt concise and highlights only what differs from baseline.

**Formatting pseudocode:**
```
compare each preference field against defaults
collect non-default fields as bullet points
if runtime overrides exist, append override notes
if no diffs and no overrides → return empty string
else → return "## User Workflow Preferences" section
```

## Conflict Resolution Precedence

```
1. Runtime command      (highest — /verbose on, /yolo)
   ↓ overrides
2. User preferences     (.co-cli/preferences.json)
   ↓ overrides
3. Personality template (communication style only)
   ↓ overrides
4. Base system prompt   (lowest — default behavior)
```

Conflicts are not errors — they are resolved at prompt assembly time via this precedence. Personality and preferences are orthogonal: personality controls tone, preferences control behavior. Result of personality="terse" + verbosity="detailed" is detailed information delivered in terse style.

## Runtime Override Slash Commands

Override flags live in `CoDeps` (runtime state, not persisted to disk).

| Command | Effect | Scope |
|---------|--------|-------|
| `/verbose on\|off` | Force detailed/minimal explanations | Current turn |
| `/terse on\|off` | Ultra-minimal output | Current turn |
| `/explain on\|off` | Show/hide reasoning for decisions | Current turn |
| `/cautious on\|off` | Require approval for everything / auto-approve safe+moderate | Current session |
| `/yolo` | Auto-approve ALL commands | Current session |

Turn-scoped overrides reset after one query. Session-scoped overrides persist until Co exits or user toggles off. `/yolo` requires intentional re-enablement after restart for safety.

## Success Criteria

### Code
- [ ] `UserPreferences` Pydantic model with 10 fields and enum validators
- [ ] `co_cli/preferences.py` module with model + `format_preferences_prompt()`
- [ ] `load_preferences()` and `save_preferences()` in config
- [ ] `CoDeps` includes preference fields and 4 override flags
- [ ] `get_system_prompt()` accepts preferences + overrides parameters
- [ ] Preferences inject after personality, before project instructions
- [ ] 5 override commands registered in command registry

### Tests
- [ ] 15+ functional tests in `tests/test_preferences.py`
- [ ] Covers: defaults, custom values, validation errors, loading precedence, save/load round-trip
- [ ] Covers: progressive disclosure, override display, prompt ordering
- [ ] Override command tests (toggle, scope, invalid args)
- [ ] No regressions in full test suite

### Behavior
- [ ] Default preferences produce no prompt injection
- [ ] Non-default preferences appear in prompt
- [ ] Runtime overrides supersede preferences
- [ ] Project preferences override user preferences
- [ ] `/yolo` warns about risk

## Files

### New
| File | Purpose |
|------|---------|
| `co_cli/preferences.py` | `UserPreferences` model + `format_preferences_prompt()` |
| `tests/test_preferences.py` | 15+ functional tests |

### Modified
| File | Change |
|------|--------|
| `co_cli/config.py` | Add `PREFERENCES_FILE`, `load_preferences()`, `save_preferences()` |
| `co_cli/agent.py` | Pass preferences + overrides to `get_system_prompt()` |
| `co_cli/prompts/__init__.py` | Accept preferences param, inject after personality |
| `co_cli/_commands.py` | Add 5 override command handlers + registry entries |

## Not in Scope

- Context-aware automatic preference switching
- Per-project preference profiles (beyond single file override)
- Preference learning from user corrections
- Per-tool preferences (e.g., "always use rg instead of grep")
- UI for preference discovery
