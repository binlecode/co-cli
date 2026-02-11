# Phase 2b: User Preferences System - Implementation Plan

**Status:** ⏳ Pending
**Prerequisites:** Phase 2b research complete (peer system analysis, 2026 best practices)
**Estimated Effort:** 10-12 hours (includes research integration)
**Started:** TBD

---

## Executive Summary

### Goal

Implement a workflow preferences system that adapts Co's behavior to user work contexts without changing communication style. Preferences control what Co does and how deeply it explains, while personality (Phase 1b) controls how Co communicates.

### Problem

One-size-fits-all agent behavior doesn't work across different contexts:
- Senior developer debugging: wants terse output, auto-approve safe commands, skip explanations
- Junior developer learning: wants verbose explanations, explicit approval, step-by-step guidance
- Writing mode: wants focused search, citation formatting, no shell commands
- Coding mode: wants aggressive tooling, file operations, lint checks

Current Co has global settings (auto_confirm, theme) but lacks nuanced workflow preferences that adapt to task context.

### Solution

Add `UserPreferences` dataclass with 8-10 core preference dimensions. Load from `.co-cli/preferences.json` and inject into system prompt. Support runtime overrides via slash commands (`/verbose on`, `/explain off`).

Preferences sit between personality and project instructions in prompt assembly order.

### Scope

**Phase 2b delivers:**
- `UserPreferences` pydantic model with 8-10 core preferences
- Config integration (load/save/validate)
- `co_cli/preferences.py` module with prompt template
- Prompt injection after personality, before project instructions
- Runtime override commands (`/verbose`, `/explain`, `/cautious`, `/yolo`)
- Conflict resolution rules (command > preference > personality > base)
- 15+ functional tests

**NOT in Phase 2b:**
- Context-aware automatic preference switching (future enhancement)
- Per-project preference profiles (future enhancement)
- Preference learning from user corrections (future enhancement)
- UI for preference discovery (future enhancement)

### Effort Estimate

| Phase | Task | Hours |
|-------|------|-------|
| 1 | Research verification (peer systems, 2026 patterns) | 2-3 |
| 2 | UserPreferences dataclass design | 1 |
| 3 | Config integration (load/save/validate) | 1 |
| 4 | Preference loading logic | 1 |
| 5 | Prompt injection template | 1-2 |
| 6 | Runtime override commands | 1.5 |
| 7 | Testing (15+ tests) | 2-3 |
| **Total** | | **10-12 hours** |

### Dependencies

**Phase 2b research complete:**
- Peer system analysis (Claude Code, Aider, Codex, Gemini CLI preference patterns)
- 2026 workflow automation best practices (context switching, progressive disclosure)
- Conflict resolution strategies (precedence hierarchies, override patterns)

**Phase 1b complete (✅):**
- Personality system provides communication style baseline
- Prompt assembly order established (base → conditionals → personality → preferences → project)

---

## Architecture Overview

### Personality vs Preferences Distinction

| Dimension | Personality | Preferences |
|-----------|------------|-------------|
| **What it controls** | Communication style | Behavior & workflow |
| **Examples** | Formal vs casual, verbose vs terse tone | Auto-approve level, explanation depth |
| **Scope** | How Co talks | What Co does |
| **User metaphor** | "Co's voice" | "Co's work mode" |
| **Storage** | Settings field (enum) | `.co-cli/preferences.json` (struct) |
| **Change frequency** | Rarely (select once) | Context-dependent (coding vs writing) |
| **Conflicts with** | Preferences (verbosity) | Personality (tone), runtime commands |

**Example interaction:**
- Personality="terse" (communication style: minimal words, bullet points)
- Preference: verbosity="detailed" (workflow: explain reasoning, show alternatives)
- **Result:** Co provides detailed information (preference) in terse style (personality)
  - Terse: "3 options: Docker (recommended), Podman (rootless), subprocess (fallback)."
  - Friendly with same preference: "Great question! Let me walk through 3 approaches: Docker is our default because..."

### Conflict Resolution Precedence

When preferences conflict with personality or base behavior:

```
1. Runtime command (highest precedence)
   ↓ overrides
2. User preferences (.co-cli/preferences.json)
   ↓ overrides
3. Personality template (selected style)
   ↓ overrides
4. Base system prompt (lowest precedence)
```

**Examples:**

1. **Verbosity conflict:**
   - Base: default explanation level
   - Personality="terse": minimal verbosity
   - Preference: verbosity="detailed": full explanations
   - Command: `/terse on`: ultra-minimal this turn
   - **Resolution:** `/terse on` wins → minimal output despite "detailed" preference

2. **Approval conflict:**
   - Base: requires approval for side effects
   - Personality: no effect on approval (communication only)
   - Preference: cautious_mode="relaxed": auto-approve safe+moderate risk
   - Command: `/yolo`: auto-approve all
   - **Resolution:** `/yolo` wins → all commands auto-approved this session

3. **Explanation conflict:**
   - Base: provide explanations when helpful
   - Personality="jeff": enthusiastic over-explanation
   - Preference: explain_reasoning=false: skip explanations
   - Command: (none)
   - **Resolution:** Preference wins → Jeff's enthusiasm doesn't override explicit preference

### Prompt Injection Strategy

Preferences inject after personality, before project instructions:

```markdown
# System Prompt Assembly

1. Base system.md (identity, principles, tool guidance)
2. Model conditionals ([IF gemini] / [IF ollama])
3. Personality template (communication style)
4. User preferences (workflow behavior)         ← Phase 2b injection point
5. Project instructions (.co-cli/instructions.md)
```

**Injection template:**

```markdown
## User Workflow Preferences

{preferences_summary}

### Explanation Depth
{explanation_preference}

### Approval Behavior
{approval_preference}

### Output Format
{format_preference}

### Tool Behavior
{tool_preference}

### Reasoning Display
{reasoning_preference}
```

Dynamic content based on `UserPreferences` values. Only non-default preferences appear (progressive disclosure).

### Runtime Override Commands

| Command | Effect | Scope | Conflicts With |
|---------|--------|-------|----------------|
| `/verbose on` | Force detailed explanations | Current turn | Preference: verbosity, Personality: terse |
| `/verbose off` | Force minimal explanations | Current turn | Preference: verbosity, Personality: inquisitive |
| `/terse on` | Ultra-minimal output | Current turn | All verbosity settings |
| `/explain on` | Show reasoning for every decision | Current turn | Preference: explain_reasoning=false |
| `/explain off` | Skip all explanations | Current turn | Preference: explain_reasoning=true |
| `/cautious on` | Require approval for everything | Current session | Preference: cautious_mode |
| `/cautious off` | Auto-approve safe commands only | Current session | Preference: cautious_mode |
| `/yolo` | Auto-approve ALL commands | Current session | All approval settings |

**Scope distinctions:**
- **Current turn:** Override lasts for one user query only
- **Current session:** Override lasts until Co exits or user toggles off

**Implementation:** Commands set flags in `CoDeps` that override preference values during prompt assembly.

---

## Implementation Plan

### Phase 1: Research Completion Verification (2-3 hours)

**Goal:** Ensure Phase 2b research is complete before implementation begins.

**Tasks:**
1. [ ] Verify peer system analysis complete
   - [ ] Claude Code: approval levels, context profiles
   - [ ] Aider: auto-mode, yes-mode, explain flags
   - [ ] Codex: workflow presets (dev, review, explore)
   - [ ] Gemini CLI: verbosity levels, output formats
   - [ ] Document 8-10 converged patterns across 2+ systems
2. [ ] Verify 2026 best practices research
   - [ ] Context-aware preference switching patterns
   - [ ] Progressive disclosure (show preferences only when non-default)
   - [ ] Conflict resolution hierarchies (precedence rules)
   - [ ] Runtime override UX patterns
3. [ ] Verify research gaps filled
   - [ ] Personality vs preference conflict resolution
   - [ ] Override command scope (turn vs session)
   - [ ] Preference discovery UX (how users learn what's configurable)
4. [ ] Create research summary document
   - [ ] File: `docs/RESEARCH-preferences-2026.md`
   - [ ] Peer system comparison table
   - [ ] Best practice recommendations
   - [ ] Anti-patterns to avoid

**Deliverables:**
- `docs/RESEARCH-preferences-2026.md` (if not exists)
- Research summary section in this TODO
- Decision log documenting why each preference dimension was chosen

**Time:** 2-3 hours (research integration + documentation)

---

### Phase 2: UserPreferences Dataclass Design (1 hour)

**Goal:** Define `UserPreferences` pydantic model with 8-10 core preference dimensions.

**File:** `co_cli/preferences.py` (NEW)

**Dataclass specification:**

```python
from pydantic import BaseModel, Field
from typing import Literal

class UserPreferences(BaseModel):
    """User workflow preferences controlling Co's behavior.

    Preferences control WHAT Co does and HOW MUCH it explains.
    Personality (Phase 1b) controls HOW Co communicates (tone, style).

    When preferences conflict with personality:
    - Command > Preference > Personality > Base

    Example: personality="terse" + verbosity="detailed"
    → Co provides detailed info in terse style (bullet points, no fluff)
    """

    # Explanation & Verbosity
    verbosity: Literal["minimal", "normal", "detailed"] = Field(default="normal")
    """Output verbosity level.
    - minimal: Terse results only, no explanations
    - normal: Brief context when helpful
    - detailed: Full reasoning, alternatives, trade-offs
    """

    explain_reasoning: bool = Field(default=True)
    """Show reasoning for decisions (tool selection, approach choice).
    False = execute directly, True = explain "why I chose X over Y".
    """

    show_steps: bool = Field(default=False)
    """Show step-by-step progress for multi-step tasks.
    False = show final result only, True = narrate each step.
    """

    # Approval & Risk
    cautious_mode: Literal["strict", "balanced", "relaxed"] = Field(default="balanced")
    """Approval behavior for potentially risky operations.
    - strict: Approve everything (even safe read-only commands)
    - balanced: Approve side effects, auto-approve safe reads (default)
    - relaxed: Auto-approve safe+moderate risk, prompt for destructive only

    Note: This augments existing auto_confirm setting.
    auto_confirm=True → YOLO mode (approve all, ignore cautious_mode)
    auto_confirm=False → Use cautious_mode rules
    """

    # Tool Behavior
    prefer_shell_tools: bool = Field(default=True)
    """Prefer shell commands over built-in tools when both work.
    True = use grep/find over search APIs when in sandbox
    False = use built-in tools (Obsidian search, web_search) first
    """

    search_depth: Literal["quick", "normal", "exhaustive"] = Field(default="normal")
    """Search thoroughness for code/file/web searches.
    - quick: First good result (fast, may miss edge cases)
    - normal: Check 3-5 sources
    - exhaustive: Comprehensive search, multiple tools
    """

    # Output Format
    output_format: Literal["conversational", "structured", "compact"] = Field(default="conversational")
    """Output formatting preference.
    - conversational: Natural language with context
    - structured: Bullet points, tables, headers
    - compact: Dense information, minimal whitespace
    """

    citation_style: Literal["inline", "footnotes", "none"] = Field(default="inline")
    """How to cite sources when providing information.
    - inline: "[Source: X]" in text
    - footnotes: Numbered references at end
    - none: No explicit citations (for trusted contexts)
    """

    # Learning & Memory
    track_patterns: bool = Field(default=True)
    """Learn user patterns for internal knowledge system (Phase 1c).
    True = Co remembers preferences expressed during conversation
    False = Co forgets each session (stateless mode)
    """

    suggest_improvements: bool = Field(default=True)
    """Offer proactive suggestions for better approaches.
    True = "Have you considered X?" when Co sees opportunities
    False = Only answer what's asked (strict directive mode)
    """
```

**Validation:**
```python
    @field_validator("verbosity")
    @classmethod
    def _validate_verbosity(cls, v: str) -> str:
        valid = ["minimal", "normal", "detailed"]
        if v not in valid:
            raise ValueError(f"verbosity must be one of {valid}, got: {v}")
        return v

    @field_validator("cautious_mode")
    @classmethod
    def _validate_cautious_mode(cls, v: str) -> str:
        valid = ["strict", "balanced", "relaxed"]
        if v not in valid:
            raise ValueError(f"cautious_mode must be one of {valid}, got: {v}")
        return v

    @field_validator("search_depth")
    @classmethod
    def _validate_search_depth(cls, v: str) -> str:
        valid = ["quick", "normal", "exhaustive"]
        if v not in valid:
            raise ValueError(f"search_depth must be one of {valid}, got: {v}")
        return v

    @field_validator("output_format")
    @classmethod
    def _validate_output_format(cls, v: str) -> str:
        valid = ["conversational", "structured", "compact"]
        if v not in valid:
            raise ValueError(f"output_format must be one of {valid}, got: {v}")
        return v

    @field_validator("citation_style")
    @classmethod
    def _validate_citation_style(cls, v: str) -> str:
        valid = ["inline", "footnotes", "none"]
        if v not in valid:
            raise ValueError(f"citation_style must be one of {valid}, got: {v}")
        return v
```

**Tasks:**
1. [ ] Create `co_cli/preferences.py`
2. [ ] Define `UserPreferences` dataclass with 10 fields
3. [ ] Add field validators for enums
4. [ ] Add comprehensive docstrings for each field
5. [ ] Add conflict resolution docstring
6. [ ] Add usage examples in module docstring

**Time:** 1 hour

---

### Phase 3: Config Integration (1 hour)

**Goal:** Add preference loading/saving to config system.

**File:** `co_cli/config.py` (MODIFY)

**Changes:**

1. Add preferences file path constant:
```python
# After existing XDG paths
PREFERENCES_FILE = CONFIG_DIR / "preferences.json"
```

2. Add preference loading helper:
```python
def load_preferences() -> UserPreferences:
    """Load user preferences from preferences.json.

    Resolution order:
    1. Project: .co-cli/preferences.json (if exists)
    2. User: ~/.config/co-cli/preferences.json (if exists)
    3. Defaults: UserPreferences() built-in defaults

    Project preferences completely override user preferences (no merge).
    Unlike Settings (which uses shallow merge), preferences are all-or-nothing
    to avoid confusing partial overrides.

    Returns:
        UserPreferences instance with loaded or default values.
    """
    from co_cli.preferences import UserPreferences

    # Try project preferences first
    project_prefs = Path.cwd() / ".co-cli" / "preferences.json"
    if project_prefs.exists():
        with open(project_prefs, "r") as f:
            try:
                data = json.load(f)
                return UserPreferences.model_validate(data)
            except Exception as e:
                print(f"Error loading project preferences {project_prefs}: {e}. Using defaults.")

    # Try user preferences
    if PREFERENCES_FILE.exists():
        with open(PREFERENCES_FILE, "r") as f:
            try:
                data = json.load(f)
                return UserPreferences.model_validate(data)
            except Exception as e:
                print(f"Error loading preferences.json: {e}. Using defaults.")

    # Use defaults
    return UserPreferences()
```

3. Add preference saving helper:
```python
def save_preferences(prefs: UserPreferences, project: bool = False):
    """Save preferences to JSON file.

    Args:
        prefs: UserPreferences instance to save
        project: If True, save to .co-cli/preferences.json (project-level)
                If False, save to ~/.config/co-cli/preferences.json (user-level)
    """
    if project:
        project_dir = Path.cwd() / ".co-cli"
        project_dir.mkdir(parents=True, exist_ok=True)
        target = project_dir / "preferences.json"
    else:
        target = PREFERENCES_FILE

    with open(target, "w") as f:
        f.write(prefs.model_dump_json(indent=2, exclude_none=False))
```

4. Add global preferences instance:
```python
# After settings = load_config()
preferences = load_preferences()
```

**Tasks:**
1. [ ] Add `PREFERENCES_FILE` constant
2. [ ] Implement `load_preferences()` function
3. [ ] Implement `save_preferences()` function
4. [ ] Add global `preferences` instance
5. [ ] Update module docstring with preferences info
6. [ ] Add preference precedence comment (project > user > default)

**Time:** 1 hour

---

### Phase 4: Preference Loading Logic (1 hour)

**Goal:** Inject preferences into `CoDeps` for runtime access.

**File:** `co_cli/deps.py` (MODIFY)

**Changes:**

1. Add preferences field to `CoDeps`:
```python
from co_cli.preferences import UserPreferences

@dataclass
class CoDeps:
    """Runtime dependencies injected via RunContext."""
    settings: Settings
    preferences: UserPreferences  # ← ADD THIS
    sandbox: SandboxProtocol
    console: Console

    # Runtime override flags (set by slash commands)
    override_verbose: bool | None = None  # None=use preference, True/False=override
    override_terse: bool | None = None
    override_explain: bool | None = None
    override_cautious: Literal["strict", "relaxed", "yolo"] | None = None
```

2. Update `CoDeps` factory in `get_agent()`:
```python
# In co_cli/agent.py
from co_cli.config import settings, preferences

deps = CoDeps(
    settings=settings,
    preferences=preferences,  # ← ADD THIS
    sandbox=sandbox,
    console=console,
    override_verbose=None,    # ← ADD THESE
    override_terse=None,
    override_explain=None,
    override_cautious=None,
)
```

**Tasks:**
1. [ ] Import `UserPreferences` in `deps.py`
2. [ ] Add `preferences: UserPreferences` field to `CoDeps`
3. [ ] Add 4 override flag fields to `CoDeps`
4. [ ] Update `CoDeps` docstring with preference override explanation
5. [ ] Update `get_agent()` to inject preferences
6. [ ] Verify tools can access `ctx.deps.preferences`

**Time:** 1 hour

---

### Phase 5: Prompt Injection Template (1-2 hours)

**Goal:** Inject preferences into system prompt during assembly.

**File:** `co_cli/preferences.py` (ADD FUNCTION)

**Function specification:**

```python
def format_preferences_prompt(prefs: UserPreferences, overrides: dict[str, any] | None = None) -> str:
    """Format user preferences as system prompt section.

    Uses progressive disclosure: only show preferences that differ from defaults
    or have active overrides. This keeps the prompt concise.

    Args:
        prefs: UserPreferences instance
        overrides: Optional override flags from CoDeps (runtime commands)

    Returns:
        Formatted markdown prompt section (empty string if all defaults + no overrides)

    Example output:
        ## User Workflow Preferences

        The user has configured the following workflow preferences:

        - **Verbosity:** detailed (explain fully, show alternatives)
        - **Approval:** relaxed (auto-approve safe+moderate risk commands)
        - **Output format:** structured (bullet points, tables)

        **Active overrides (this turn only):**
        - `/terse on` → Minimal output this turn (overrides detailed preference)
    """
    overrides = overrides or {}
    defaults = UserPreferences()

    # Collect non-default preferences
    diffs = []

    # Verbosity
    if prefs.verbosity != defaults.verbosity or overrides.get("verbose") is not None:
        if overrides.get("terse"):
            diffs.append("- **Verbosity:** minimal (override: /terse on)")
        elif overrides.get("verbose") is True:
            diffs.append("- **Verbosity:** detailed (override: /verbose on)")
        elif overrides.get("verbose") is False:
            diffs.append("- **Verbosity:** minimal (override: /verbose off)")
        elif prefs.verbosity == "minimal":
            diffs.append("- **Verbosity:** minimal (terse output only)")
        elif prefs.verbosity == "detailed":
            diffs.append("- **Verbosity:** detailed (full explanations, alternatives)")

    # Reasoning
    if not prefs.explain_reasoning or overrides.get("explain") is not None:
        if overrides.get("explain") is True:
            diffs.append("- **Reasoning:** show all reasoning (override: /explain on)")
        elif overrides.get("explain") is False:
            diffs.append("- **Reasoning:** skip explanations (override: /explain off)")
        elif not prefs.explain_reasoning:
            diffs.append("- **Reasoning:** skip explanations (execute directly)")

    # Steps
    if prefs.show_steps:
        diffs.append("- **Progress:** narrate each step for multi-step tasks")

    # Approval
    if prefs.cautious_mode != defaults.cautious_mode or overrides.get("cautious") is not None:
        if overrides.get("cautious") == "yolo":
            diffs.append("- **Approval:** auto-approve ALL (override: /yolo)")
        elif overrides.get("cautious") == "strict":
            diffs.append("- **Approval:** require approval for everything (override: /cautious on)")
        elif overrides.get("cautious") == "relaxed":
            diffs.append("- **Approval:** auto-approve safe+moderate (override: /cautious off)")
        elif prefs.cautious_mode == "strict":
            diffs.append("- **Approval:** strict (approve everything, even reads)")
        elif prefs.cautious_mode == "relaxed":
            diffs.append("- **Approval:** relaxed (auto-approve safe+moderate risk)")

    # Shell tools
    if not prefs.prefer_shell_tools:
        diffs.append("- **Tools:** prefer built-in tools over shell commands")

    # Search depth
    if prefs.search_depth != defaults.search_depth:
        if prefs.search_depth == "quick":
            diffs.append("- **Search:** quick (first good result)")
        elif prefs.search_depth == "exhaustive":
            diffs.append("- **Search:** exhaustive (comprehensive, multiple sources)")

    # Output format
    if prefs.output_format != defaults.output_format:
        if prefs.output_format == "structured":
            diffs.append("- **Format:** structured (bullet points, tables, headers)")
        elif prefs.output_format == "compact":
            diffs.append("- **Format:** compact (dense info, minimal whitespace)")

    # Citations
    if prefs.citation_style != defaults.citation_style:
        if prefs.citation_style == "footnotes":
            diffs.append("- **Citations:** footnotes (numbered references at end)")
        elif prefs.citation_style == "none":
            diffs.append("- **Citations:** none (omit source references)")

    # Pattern tracking
    if not prefs.track_patterns:
        diffs.append("- **Learning:** stateless (forget each session)")

    # Suggestions
    if not prefs.suggest_improvements:
        diffs.append("- **Suggestions:** strict directive mode (no proactive suggestions)")

    # Build prompt
    if not diffs:
        return ""  # All defaults, no overrides → skip section

    lines = ["## User Workflow Preferences", ""]
    lines.append("The user has configured the following workflow preferences:")
    lines.append("")
    lines.extend(diffs)

    return "\n".join(lines)
```

**File:** `co_cli/prompts/__init__.py` (MODIFY)

**Changes:**

1. Update `get_system_prompt()` signature:
```python
def get_system_prompt(
    provider: str,
    personality: str | None = None,
    preferences: UserPreferences | None = None,
    overrides: dict[str, any] | None = None,
) -> str:
```

2. Add preference injection after personality:
```python
    # 3. Inject personality (if specified)
    if personality:
        personality_content = load_personality(personality)
        base_prompt += f"\n\n{personality_content}"

    # 4. Inject user preferences (if specified)
    if preferences:
        from co_cli.preferences import format_preferences_prompt
        prefs_content = format_preferences_prompt(preferences, overrides)
        if prefs_content:  # Only inject if non-default
            base_prompt += f"\n\n{prefs_content}"

    # 5. Load project instructions if present
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    ...
```

**File:** `co_cli/agent.py` (MODIFY)

**Changes:**

Update `get_system_prompt()` call to include preferences:
```python
    # Collect runtime overrides from deps
    overrides = {}
    if deps.override_verbose is not None:
        overrides["verbose"] = deps.override_verbose
    if deps.override_terse:
        overrides["terse"] = deps.override_terse
    if deps.override_explain is not None:
        overrides["explain"] = deps.override_explain
    if deps.override_cautious is not None:
        overrides["cautious"] = deps.override_cautious

    system_prompt = get_system_prompt(
        provider=provider_name,
        personality=settings.personality,
        preferences=deps.preferences,
        overrides=overrides if overrides else None,
    )
```

**Tasks:**
1. [ ] Implement `format_preferences_prompt()` in `preferences.py`
2. [ ] Add progressive disclosure logic (skip defaults)
3. [ ] Add override display logic
4. [ ] Update `get_system_prompt()` signature
5. [ ] Add preference injection after personality
6. [ ] Update agent factory to pass preferences + overrides
7. [ ] Test preference injection with various combinations

**Time:** 1-2 hours

---

### Phase 6: Runtime Override Commands (1.5 hours)

**Goal:** Add slash commands for runtime preference overrides.

**File:** `co_cli/_commands.py` (MODIFY)

**New command handlers:**

```python
def handle_verbose(args: str, deps: CoDeps) -> str:
    """Toggle verbose mode for current turn.

    Usage:
        /verbose on   - Force detailed explanations (this turn)
        /verbose off  - Force minimal explanations (this turn)
        /verbose      - Show current verbosity setting
    """
    if not args:
        current = deps.preferences.verbosity
        override = "on" if deps.override_verbose is True else "off" if deps.override_verbose is False else "none"
        return f"Verbosity: {current} (override: {override})"

    if args == "on":
        deps.override_verbose = True
        return "Verbose mode ON (detailed explanations this turn)"
    elif args == "off":
        deps.override_verbose = False
        return "Verbose mode OFF (minimal output this turn)"
    else:
        return f"Invalid argument: {args}. Use 'on' or 'off'."


def handle_terse(args: str, deps: CoDeps) -> str:
    """Toggle ultra-terse mode for current turn.

    Usage:
        /terse on   - Force minimal output (this turn)
        /terse off  - Restore normal verbosity (this turn)
        /terse      - Show current terse setting
    """
    if not args:
        override = "on" if deps.override_terse else "off"
        return f"Terse mode: {override}"

    if args == "on":
        deps.override_terse = True
        return "Terse mode ON (ultra-minimal output)"
    elif args == "off":
        deps.override_terse = False
        return "Terse mode OFF"
    else:
        return f"Invalid argument: {args}. Use 'on' or 'off'."


def handle_explain(args: str, deps: CoDeps) -> str:
    """Toggle reasoning explanations for current turn.

    Usage:
        /explain on   - Show reasoning for every decision (this turn)
        /explain off  - Skip all explanations (this turn)
        /explain      - Show current setting
    """
    if not args:
        current = deps.preferences.explain_reasoning
        override = "on" if deps.override_explain is True else "off" if deps.override_explain is False else "none"
        return f"Explain reasoning: {current} (override: {override})"

    if args == "on":
        deps.override_explain = True
        return "Explain mode ON (show reasoning for all decisions)"
    elif args == "off":
        deps.override_explain = False
        return "Explain mode OFF (skip explanations)"
    else:
        return f"Invalid argument: {args}. Use 'on' or 'off'."


def handle_cautious(args: str, deps: CoDeps) -> str:
    """Toggle approval behavior for current session.

    Usage:
        /cautious on   - Require approval for everything (strict mode)
        /cautious off  - Auto-approve safe+moderate risk (relaxed mode)
        /cautious      - Show current setting
    """
    if not args:
        current = deps.preferences.cautious_mode
        override = deps.override_cautious or "none"
        return f"Cautious mode: {current} (override: {override})"

    if args == "on":
        deps.override_cautious = "strict"
        return "Cautious mode ON (require approval for everything)"
    elif args == "off":
        deps.override_cautious = "relaxed"
        return "Cautious mode OFF (auto-approve safe+moderate risk)"
    else:
        return f"Invalid argument: {args}. Use 'on' or 'off'."


def handle_yolo(args: str, deps: CoDeps) -> str:
    """Enable YOLO mode (auto-approve ALL commands).

    Usage:
        /yolo      - Auto-approve everything (USE WITH CAUTION)

    This is equivalent to auto_confirm=True but for current session only.
    Use /cautious on to restore normal approval behavior.
    """
    deps.override_cautious = "yolo"
    return "⚠️  YOLO mode ENABLED (auto-approving ALL commands this session)"
```

**Command registration:**

```python
# In COMMAND_REGISTRY
COMMAND_REGISTRY = {
    # ... existing commands ...
    "verbose": handle_verbose,
    "terse": handle_terse,
    "explain": handle_explain,
    "cautious": handle_cautious,
    "yolo": handle_yolo,
}
```

**Tasks:**
1. [ ] Implement 5 command handlers
2. [ ] Add commands to registry
3. [ ] Add command help text
4. [ ] Update `/help` to show new commands
5. [ ] Test each command with various arguments
6. [ ] Test override persistence (turn vs session scope)
7. [ ] Test override interaction with preferences

**Time:** 1.5 hours

---

### Phase 7: Testing (2-3 hours)

**Goal:** Comprehensive test coverage for preferences system.

**File:** `tests/test_preferences.py` (NEW)

**Test specification (15+ tests):**

```python
"""Tests for user preferences system (Phase 2b)."""

import json
import pytest
from pathlib import Path
from co_cli.preferences import UserPreferences, format_preferences_prompt
from co_cli.config import load_preferences, save_preferences


class TestUserPreferences:
    """Test UserPreferences dataclass."""

    def test_default_preferences(self):
        """All fields have sensible defaults."""
        prefs = UserPreferences()
        assert prefs.verbosity == "normal"
        assert prefs.explain_reasoning is True
        assert prefs.show_steps is False
        assert prefs.cautious_mode == "balanced"
        assert prefs.prefer_shell_tools is True
        assert prefs.search_depth == "normal"
        assert prefs.output_format == "conversational"
        assert prefs.citation_style == "inline"
        assert prefs.track_patterns is True
        assert prefs.suggest_improvements is True

    def test_custom_preferences(self):
        """Can override all defaults."""
        prefs = UserPreferences(
            verbosity="detailed",
            explain_reasoning=False,
            show_steps=True,
            cautious_mode="strict",
            prefer_shell_tools=False,
            search_depth="exhaustive",
            output_format="structured",
            citation_style="footnotes",
            track_patterns=False,
            suggest_improvements=False,
        )
        assert prefs.verbosity == "detailed"
        assert prefs.explain_reasoning is False
        assert prefs.show_steps is True
        # ... verify all overrides

    def test_invalid_verbosity(self):
        """Invalid verbosity raises ValidationError."""
        with pytest.raises(ValueError, match="verbosity must be one of"):
            UserPreferences(verbosity="ultra")

    def test_invalid_cautious_mode(self):
        """Invalid cautious_mode raises ValidationError."""
        with pytest.raises(ValueError, match="cautious_mode must be one of"):
            UserPreferences(cautious_mode="paranoid")

    def test_invalid_search_depth(self):
        """Invalid search_depth raises ValidationError."""
        with pytest.raises(ValueError, match="search_depth must be one of"):
            UserPreferences(search_depth="deep")

    def test_invalid_output_format(self):
        """Invalid output_format raises ValidationError."""
        with pytest.raises(ValueError, match="output_format must be one of"):
            UserPreferences(output_format="markdown")

    def test_invalid_citation_style(self):
        """Invalid citation_style raises ValidationError."""
        with pytest.raises(ValueError, match="citation_style must be one of"):
            UserPreferences(citation_style="apa")


class TestPreferenceLoading:
    """Test preference loading from JSON files."""

    def test_load_defaults_when_no_files(self, tmp_path, monkeypatch):
        """Load defaults when no preference files exist."""
        monkeypatch.setattr("co_cli.config.PREFERENCES_FILE", tmp_path / "preferences.json")
        monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path)

        prefs = load_preferences()
        assert prefs.verbosity == "normal"
        assert prefs.cautious_mode == "balanced"

    def test_load_user_preferences(self, tmp_path, monkeypatch):
        """Load user-level preferences."""
        prefs_file = tmp_path / "preferences.json"
        prefs_file.write_text(json.dumps({
            "verbosity": "detailed",
            "cautious_mode": "strict"
        }))

        monkeypatch.setattr("co_cli.config.PREFERENCES_FILE", prefs_file)
        monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path / "other")

        prefs = load_preferences()
        assert prefs.verbosity == "detailed"
        assert prefs.cautious_mode == "strict"

    def test_load_project_preferences(self, tmp_path, monkeypatch):
        """Project preferences override user preferences."""
        # User prefs
        user_prefs = tmp_path / "user_preferences.json"
        user_prefs.write_text(json.dumps({"verbosity": "detailed"}))

        # Project prefs
        project_dir = tmp_path / "project" / ".co-cli"
        project_dir.mkdir(parents=True)
        project_prefs = project_dir / "preferences.json"
        project_prefs.write_text(json.dumps({"verbosity": "minimal"}))

        monkeypatch.setattr("co_cli.config.PREFERENCES_FILE", user_prefs)
        monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path / "project")

        prefs = load_preferences()
        assert prefs.verbosity == "minimal"  # Project wins

    def test_save_user_preferences(self, tmp_path, monkeypatch):
        """Save preferences to user config."""
        prefs_file = tmp_path / "preferences.json"
        monkeypatch.setattr("co_cli.config.PREFERENCES_FILE", prefs_file)

        prefs = UserPreferences(verbosity="detailed", cautious_mode="relaxed")
        save_preferences(prefs, project=False)

        assert prefs_file.exists()
        loaded = json.loads(prefs_file.read_text())
        assert loaded["verbosity"] == "detailed"
        assert loaded["cautious_mode"] == "relaxed"

    def test_save_project_preferences(self, tmp_path, monkeypatch):
        """Save preferences to project config."""
        monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path)

        prefs = UserPreferences(verbosity="terse")
        save_preferences(prefs, project=True)

        project_prefs = tmp_path / ".co-cli" / "preferences.json"
        assert project_prefs.exists()
        loaded = json.loads(project_prefs.read_text())
        assert loaded["verbosity"] == "terse"


class TestPreferencePromptFormatting:
    """Test preference prompt injection."""

    def test_all_defaults_returns_empty(self):
        """All defaults + no overrides = empty prompt (progressive disclosure)."""
        prefs = UserPreferences()
        prompt = format_preferences_prompt(prefs)
        assert prompt == ""

    def test_non_default_verbosity(self):
        """Non-default verbosity appears in prompt."""
        prefs = UserPreferences(verbosity="detailed")
        prompt = format_preferences_prompt(prefs)
        assert "Verbosity: detailed" in prompt
        assert "full explanations" in prompt.lower()

    def test_non_default_cautious_mode(self):
        """Non-default cautious_mode appears in prompt."""
        prefs = UserPreferences(cautious_mode="strict")
        prompt = format_preferences_prompt(prefs)
        assert "Approval: strict" in prompt
        assert "approve everything" in prompt.lower()

    def test_multiple_non_defaults(self):
        """Multiple non-defaults all appear."""
        prefs = UserPreferences(
            verbosity="minimal",
            output_format="structured",
            citation_style="footnotes"
        )
        prompt = format_preferences_prompt(prefs)
        assert "Verbosity: minimal" in prompt
        assert "Format: structured" in prompt
        assert "Citations: footnotes" in prompt

    def test_verbose_override(self):
        """Verbose override appears in prompt."""
        prefs = UserPreferences()
        overrides = {"verbose": True}
        prompt = format_preferences_prompt(prefs, overrides)
        assert "override: /verbose on" in prompt

    def test_terse_override(self):
        """Terse override appears and supersedes verbosity preference."""
        prefs = UserPreferences(verbosity="detailed")
        overrides = {"terse": True}
        prompt = format_preferences_prompt(prefs, overrides)
        assert "minimal" in prompt.lower()
        assert "override: /terse on" in prompt

    def test_yolo_override(self):
        """YOLO override appears with appropriate warning."""
        prefs = UserPreferences()
        overrides = {"cautious": "yolo"}
        prompt = format_preferences_prompt(prefs, overrides)
        assert "auto-approve ALL" in prompt
        assert "/yolo" in prompt


class TestPreferenceIntegration:
    """Integration tests with prompt assembly."""

    def test_preferences_inject_after_personality(self):
        """Preferences appear after personality in prompt."""
        from co_cli.prompts import get_system_prompt

        prefs = UserPreferences(verbosity="detailed")
        prompt = get_system_prompt(
            provider="gemini",
            personality="finch",
            preferences=prefs
        )

        # Find personality section
        personality_idx = prompt.find("# Finch Weinberg")
        assert personality_idx > 0

        # Find preferences section
        prefs_idx = prompt.find("## User Workflow Preferences")
        assert prefs_idx > 0

        # Preferences come after personality
        assert prefs_idx > personality_idx

    def test_preferences_inject_before_project(self):
        """Preferences appear before project instructions."""
        from co_cli.prompts import get_system_prompt

        # Create project instructions
        project_dir = Path.cwd() / ".co-cli"
        project_dir.mkdir(exist_ok=True)
        instructions_file = project_dir / "instructions.md"
        instructions_file.write_text("# Project Rules\n\nAlways use pytest.")

        try:
            prefs = UserPreferences(verbosity="detailed")
            prompt = get_system_prompt(
                provider="gemini",
                personality="finch",
                preferences=prefs
            )

            prefs_idx = prompt.find("## User Workflow Preferences")
            project_idx = prompt.find("# Project Rules")

            assert prefs_idx > 0
            assert project_idx > 0
            assert prefs_idx < project_idx
        finally:
            instructions_file.unlink()
            project_dir.rmdir()
```

**File:** `tests/test_commands.py` (MODIFY)

Add tests for new override commands:

```python
class TestOverrideCommands:
    """Test runtime override commands."""

    def test_verbose_on(self, deps):
        """'/verbose on' sets override flag."""
        result = handle_verbose("on", deps)
        assert "ON" in result
        assert deps.override_verbose is True

    def test_verbose_off(self, deps):
        """'/verbose off' sets override flag."""
        result = handle_verbose("off", deps)
        assert "OFF" in result
        assert deps.override_verbose is False

    def test_terse_on(self, deps):
        """'/terse on' sets override flag."""
        result = handle_terse("on", deps)
        assert "ON" in result
        assert deps.override_terse is True

    def test_explain_toggle(self, deps):
        """'/explain on/off' toggles reasoning display."""
        result = handle_explain("on", deps)
        assert "ON" in result
        assert deps.override_explain is True

        result = handle_explain("off", deps)
        assert "OFF" in result
        assert deps.override_explain is False

    def test_cautious_toggle(self, deps):
        """'/cautious on/off' changes approval mode."""
        result = handle_cautious("on", deps)
        assert "strict" in result.lower()
        assert deps.override_cautious == "strict"

        result = handle_cautious("off", deps)
        assert "relaxed" in result.lower()
        assert deps.override_cautious == "relaxed"

    def test_yolo_mode(self, deps):
        """'/yolo' enables auto-approve-all mode."""
        result = handle_yolo("", deps)
        assert "YOLO" in result
        assert deps.override_cautious == "yolo"
```

**Tasks:**
1. [ ] Create `tests/test_preferences.py`
2. [ ] Write 15+ tests covering all preference dimensions
3. [ ] Add tests for loading/saving
4. [ ] Add tests for prompt formatting
5. [ ] Add tests for override commands
6. [ ] Add integration tests with prompt assembly
7. [ ] Run all tests: `uv run pytest tests/test_preferences.py -v`
8. [ ] Verify no regressions: `uv run pytest`

**Time:** 2-3 hours

---

## Conflict Resolution Rules

### Precedence Hierarchy

```
1. Runtime command (highest)
   Examples: /verbose on, /terse on, /yolo
   Scope: Current turn (verbosity) or current session (approval)

   ↓ overrides

2. User preferences (.co-cli/preferences.json)
   Examples: verbosity="detailed", cautious_mode="relaxed"
   Scope: All turns in all sessions (persistent)

   ↓ overrides

3. Personality template (selected style)
   Examples: personality="terse" (implies minimal verbosity)
   Scope: Communication style only (tone, structure)

   ↓ overrides

4. Base system prompt (lowest)
   Examples: Default behavior when nothing specified
   Scope: Core identity and principles
```

### Conflict Examples with Commentary

**Example 1: Verbosity conflict with all layers**

**Setup:**
- Base: default verbosity (brief context when helpful)
- Personality: "terse" (ultra-minimal communication style)
- Preference: verbosity="detailed" (full explanations)
- Command: `/verbose off` (minimal output)

**Resolution:**
```
Command wins → Minimal output this turn

Reasoning:
1. /verbose off has highest precedence (runtime command)
2. Overrides "detailed" preference
3. Reinforces "terse" personality (they align)
4. Result: Co provides minimal output with terse tone
```

**Example 2: Approval conflict**

**Setup:**
- Base: requires_approval=True for side effects
- Personality: (no effect on approval)
- Preference: cautious_mode="relaxed" (auto-approve safe+moderate)
- Command: `/yolo` (auto-approve all)

**Resolution:**
```
Command wins → Auto-approve everything this session

Reasoning:
1. /yolo overrides all approval settings
2. Preference cautious_mode="relaxed" ignored
3. Personality has no say in approval (communication only)
4. Result: All commands auto-approved until session ends
```

**Example 3: Explanation conflict with personality**

**Setup:**
- Base: explain when helpful
- Personality: "jeff" (over-explains everything enthusiastically)
- Preference: explain_reasoning=False (skip explanations)
- Command: (none)

**Resolution:**
```
Preference wins → Skip explanations, but Jeff's enthusiasm remains

Reasoning:
1. No command override, so preference takes precedence
2. explain_reasoning=False stops Co from explaining reasoning
3. Personality "jeff" still uses enthusiastic tone
4. Result: Co executes directly in Jeff's voice ("I'll do that! *whirr*")
   but doesn't explain WHY it chose that approach
```

**Example 4: Format conflict**

**Setup:**
- Base: conversational format
- Personality: "finch" (structured educator style)
- Preference: output_format="compact" (dense, minimal whitespace)
- Command: (none)

**Resolution:**
```
Preference wins for format, personality wins for tone

Reasoning:
1. No command override
2. output_format="compact" controls structure (dense layout)
3. personality="finch" controls tone (educational, protective)
4. Result: Dense compact format with Finch's teaching voice
   "Three options: A (recommended), B (fallback), C (edge case)."
   vs friendly personality: "Here are 3 options: A, B, C!"
```

**Example 5: Search depth conflict**

**Setup:**
- Base: normal search depth (3-5 sources)
- Personality: "inquisitive" (asks questions, explores alternatives)
- Preference: search_depth="quick" (first good result)
- Command: (none)

**Resolution:**
```
Preference wins for behavior, personality wins for communication

Reasoning:
1. search_depth="quick" controls WHAT Co does (stop at first result)
2. personality="inquisitive" controls HOW Co communicates the result
3. Result: Co finds first good result (quick) but asks follow-up questions
   "I found this approach. Does it address your use case? Should I check
    for alternatives?"
```

### Key Principles

1. **Commands are temporary**: Runtime overrides last one turn (verbosity) or one session (approval).
2. **Preferences are persistent**: JSON-backed, survive restarts.
3. **Personality is communication-only**: Never affects behavior, only tone/style.
4. **Base is fallback**: Only applies when nothing else specifies.

---

## Success Criteria

Phase 2b is complete when ALL of the following are true:

### Code Criteria
- [ ] `UserPreferences` dataclass exists with 10 fields
- [ ] All enum fields have validators
- [ ] `co_cli/preferences.py` module created
- [ ] `load_preferences()` and `save_preferences()` functions work
- [ ] `format_preferences_prompt()` implements progressive disclosure
- [ ] `CoDeps` includes preferences and override flags
- [ ] `get_system_prompt()` accepts preferences parameter
- [ ] Preferences inject after personality, before project
- [ ] 5 runtime override commands implemented (`/verbose`, `/terse`, `/explain`, `/cautious`, `/yolo`)
- [ ] All commands registered in registry

### Test Criteria
- [ ] 15+ preference tests in `tests/test_preferences.py` pass
- [ ] All loading/saving tests pass
- [ ] All validation tests pass
- [ ] All prompt formatting tests pass
- [ ] All override command tests pass
- [ ] Integration tests pass (preferences + personality + project)
- [ ] No regressions: full test suite passes

### Behavioral Criteria
- [ ] Default preferences don't inject (progressive disclosure)
- [ ] Non-default preferences appear in prompt
- [ ] Runtime overrides supersede preferences
- [ ] Personality and preferences don't conflict (complementary)
- [ ] Project preferences override user preferences
- [ ] Override scope correct (turn vs session)
- [ ] `/yolo` warns about risk
- [ ] Preference changes persist across sessions

### Quality Criteria
- [ ] All functions have type hints
- [ ] All functions have docstrings
- [ ] Conflict resolution documented with examples
- [ ] Progressive disclosure works (empty prompt when all defaults)
- [ ] Code follows project style (explicit imports, no globals)
- [ ] No breaking changes to existing config system

---

## Files Modified/Created

### New Files
- `co_cli/preferences.py` - UserPreferences dataclass + formatting
- `tests/test_preferences.py` - 15+ preference tests
- `docs/RESEARCH-preferences-2026.md` - Peer system research summary
- `docs/TODO-co-evolution-phase2b.md` - This implementation plan

### Modified Files
- `co_cli/config.py` - Add load_preferences(), save_preferences(), PREFERENCES_FILE
- `co_cli/deps.py` - Add preferences and override flags to CoDeps
- `co_cli/prompts/__init__.py` - Update get_system_prompt() with preferences parameter
- `co_cli/agent.py` - Pass preferences + overrides to get_system_prompt()
- `co_cli/_commands.py` - Add 5 override command handlers
- `tests/test_commands.py` - Add override command tests

---

## Prompt Assembly Order (After Phase 2b)

```
1. Base system.md (identity, principles, tool guidance)
2. Model conditionals ([IF gemini] / [IF ollama])          ← Phase 1a ✓
3. Personality template (communication style)              ← Phase 1b ✓
4. User preferences (workflow behavior)                    ← Phase 2b (THIS)
5. Project instructions (.co-cli/instructions.md)          ← Phase 1a ✓
```

---

## Usage Examples

### Default Behavior (No Preferences File)

```bash
uv run co chat
# Uses all default preferences (progressive disclosure = no injection)
```

### Create User Preferences

```bash
cat > ~/.config/co-cli/preferences.json <<EOF
{
  "verbosity": "detailed",
  "cautious_mode": "relaxed",
  "output_format": "structured",
  "citation_style": "footnotes"
}
EOF

uv run co chat
# Co now provides detailed explanations in structured format with footnotes
```

### Project-Specific Preferences

```bash
mkdir -p .co-cli
cat > .co-cli/preferences.json <<EOF
{
  "verbosity": "minimal",
  "prefer_shell_tools": true,
  "search_depth": "quick"
}
EOF

uv run co chat
# Project preferences override user preferences (minimal verbosity)
```

### Runtime Overrides

```bash
uv run co chat

> /verbose on
Verbose mode ON (detailed explanations this turn)

> List files in src/
# Co provides detailed explanation of directory structure

> /terse on
Terse mode ON (ultra-minimal output)

> List files in src/
# Co provides compact listing only

> /yolo
⚠️ YOLO mode ENABLED (auto-approving ALL commands this session)

> rm -rf temp/
# Command executes immediately without approval prompt
```

---

## Design Decisions

### Storage Format

**Chosen:** JSON file at `~/.config/co-cli/preferences.json` (user) and `.co-cli/preferences.json` (project)

**Rationale:**
- Consistent with existing settings.json pattern
- Human-editable for power users
- Version-controllable (project preferences)
- Pydantic validation on load

**Alternatives considered:**
- SQLite: Too heavy for 10 fields
- YAML: Requires extra dependency
- Environment variables: Too many to manage (10+ fields × 3-5 values each)

### Project vs User Precedence

**Chosen:** Project completely overrides user (no merge)

**Rationale:**
- Clear precedence (no confusing partial overrides)
- Consistent with settings.json shallow merge
- Project context often requires different workflow (coding vs writing)

**Alternative considered:**
- Field-level merge: Too complex, hard to reason about which preference came from where

### Progressive Disclosure

**Chosen:** Only show non-default preferences in prompt

**Rationale:**
- Keeps prompt concise (token efficiency)
- Highlights what's different from baseline
- Follows "Principle of Least Surprise" (only mention what matters)

**Alternative considered:**
- Always show all preferences: Wastes tokens, clutters prompt

### Override Scope

**Chosen:** Turn-scoped (verbosity) vs session-scoped (approval)

**Rationale:**
- Verbosity changes frequently per query → turn scope natural
- Approval changes rarely, disruptive to toggle every turn → session scope better
- Safety: Session scope for `/yolo` requires intentional re-enablement after restart

### Preference Count

**Chosen:** 10 core preferences for Phase 2b

**Rationale:**
- Research identified 8-10 converged patterns across peer systems
- Enough coverage for 80% of workflow variations
- Small enough to avoid decision paralysis
- Room to expand post-MVP based on user feedback

---

## Implementation Notes

### Personality vs Preferences Boundary

**Hard rule:** Personality NEVER affects behavior, only communication.

**Examples of correct classification:**

| Feature | Type | Reason |
|---------|------|--------|
| Verbosity level (minimal/normal/detailed) | Preference | Controls behavior (how much info) |
| Tone (formal/casual) | Personality | Controls communication (how to say it) |
| Explain reasoning | Preference | Controls behavior (whether to explain) |
| Use emoji | Personality | Controls communication (how to express) |
| Auto-approve level | Preference | Controls behavior (when to prompt) |
| Question phrasing style | Personality | Controls communication (how to ask) |

**When in doubt:**
- If it changes WHAT Co does or WHEN Co acts → Preference
- If it changes HOW Co says it → Personality

### Override Persistence

**Turn-scoped overrides:**
- `/verbose on|off` - Lasts one query
- `/terse on|off` - Lasts one query
- `/explain on|off` - Lasts one query

**Session-scoped overrides:**
- `/cautious on|off` - Lasts until Co exits or user toggles
- `/yolo` - Lasts until Co exits (no auto-persistence for safety)

**Implementation:** Override flags live in `CoDeps` (runtime state, not persisted to disk).

### Validation Strategy

**Validate early (config load time):**
- Enum values (verbosity, cautious_mode, etc.)
- Field types (bool, str)
- Required fields present

**Validate late (prompt assembly):**
- Conflict resolution
- Override application
- Progressive disclosure logic

---

## Testing Strategy

### Functional Tests Only

No mocks or stubs. Test real preference loading, real prompt assembly, real override commands.

**Focus areas:**
1. ✅ Preference loading (user, project, defaults)
2. ✅ Preference validation (invalid values raise errors)
3. ✅ Prompt formatting (progressive disclosure, overrides)
4. ✅ Override commands (toggle flags, scope)
5. ✅ Integration (preferences + personality + project)

**Don't test:**
- ❌ LLM actually follows preferences (too variable)
- ❌ Mock file I/O (use temp files)
- ❌ Implementation details (JSON structure, string formatting)

### Test Data

Use realistic preference combinations:

**Senior developer (minimal interaction):**
```json
{
  "verbosity": "minimal",
  "explain_reasoning": false,
  "cautious_mode": "relaxed",
  "output_format": "compact"
}
```

**Junior developer (learning mode):**
```json
{
  "verbosity": "detailed",
  "explain_reasoning": true,
  "show_steps": true,
  "suggest_improvements": true
}
```

**Writing mode (focused research):**
```json
{
  "verbosity": "detailed",
  "search_depth": "exhaustive",
  "citation_style": "footnotes",
  "prefer_shell_tools": false
}
```

---

## Known Issues

None yet - this is initial implementation.

---

## Next Steps After Phase 2b

### Phase 2c: Background Execution (Future)

**Goal:** Async task runner for long-running operations

**Design:**
- Submit tasks to background queue
- Approval inheritance (don't re-prompt for approved commands)
- Status inspection: `co tasks`, `co status <task-id>`
- Cancellation: `co cancel <task-id>`
- Persisted logs in SQLite

**Estimated effort:** 12-15 hours

### Phase 3: Autonomy & Voice (Future)

**Phase 3a:** Task checkpoints & planner/result contract
**Phase 3b:** Voice runtime (push-to-talk, cascading architecture)
**Phase 3c:** Selective autonomy (scheduled tasks, computer-use)

---

## Questions to Resolve

**Q1: Should preferences affect tool approval prompts?**
**A:** No (Phase 2b). Preferences affect Co's conversational behavior, not approval UX. Approval UX is safety-critical and should remain consistent.

**Q2: Should we support per-tool preferences (e.g., "always use rg instead of grep")?**
**A:** Not in Phase 2b (too granular). Consider post-MVP if user feedback shows need.

**Q3: How do preferences interact with auto_confirm setting?**
**A:** `auto_confirm=True` (global setting) → YOLO mode (ignore cautious_mode preference). `auto_confirm=False` → Use cautious_mode preference.

**Q4: Should `/verbose on` persist across turns?**
**A:** No. Turn-scoped by design (frequent toggling expected). Users wanting persistent verbosity change should update preferences.json.

**Q5: Should we validate conflicts at config load time?**
**A:** No. Conflicts aren't errors—they're resolved via precedence rules at runtime. Validation happens only for invalid enum values.

**Q6: How do runtime overrides interact with personality?**
**A:** Overrides control behavior (what Co does). Personality controls communication (how Co says it). No conflict—they're orthogonal.

---

## Timeline Estimate

| Phase | Task | Hours | Dependencies |
|-------|------|-------|-------------|
| 1 | Research verification | 2-3 | Phase 2b research doc |
| 2 | UserPreferences dataclass | 1 | None |
| 3 | Config integration | 1 | Phase 2 complete |
| 4 | Preference loading | 1 | Phase 3 complete |
| 5 | Prompt injection | 1-2 | Phase 4 complete |
| 6 | Override commands | 1.5 | Phase 5 complete |
| 7 | Testing | 2-3 | All phases complete |
| **Total** | | **10-12 hours** | |

---

## Completion Tracking

- [ ] **Phase 1:** Research verification (2-3 hours)
- [ ] **Phase 2:** UserPreferences dataclass (1 hour)
- [ ] **Phase 3:** Config integration (1 hour)
- [ ] **Phase 4:** Preference loading (1 hour)
- [ ] **Phase 5:** Prompt injection (1-2 hours)
- [ ] **Phase 6:** Override commands (1.5 hours)
- [ ] **Phase 7:** Testing (2-3 hours)
- [ ] All success criteria met
- [ ] Git commit created
- [ ] Phase 2b marked as complete

---

**Current Status:** ⏳ Ready to implement (pending research verification)

**Next Action:** Verify Phase 2b research complete, document peer findings

---

**"Preferences control what Co does. Personality controls how Co says it."**
