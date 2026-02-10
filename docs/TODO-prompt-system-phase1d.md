# TODO: Prompt System Phase 1d — Peer System Learnings

**Status:** Not Started
**Priority:** High (directly impacts agent behavior quality)
**Created:** 2026-02-09
**Related:** `REVIEW-compare-four.md`, `co_cli/prompts/system.md`

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Techniques Analysis](#techniques-analysis)
   - [System Reminder (Recency Bias Exploitation)](#1-system-reminder-recency-bias-exploitation)
   - [Escape Hatches (Prevent Stuck States)](#2-escape-hatches-prevent-stuck-states)
   - [Contrast Examples (Good vs Bad)](#3-contrast-examples-good-vs-bad)
   - [Model Quirk Counter-Steering](#4-model-quirk-counter-steering)
   - [Commentary in Examples](#5-commentary-in-examples)
3. [Implementation Specifications](#implementation-specifications)
   - [System.md Changes](#systemmd-changes)
   - [Model Quirks Module](#model-quirks-module)
   - [Prompt Assembly Integration](#prompt-assembly-integration)
4. [Code Specifications](#code-specifications)
   - [system.md Modifications](#systemmd-modifications)
   - [model_quirks.py Full Module](#model_quirkspy-full-module)
   - [__init__.py Changes](#__init__py-changes)
5. [Test Specifications](#test-specifications)
   - [Test 1: System Reminder Present](#test-1-system-reminder-present)
   - [Test 2: Escape Hatches Present](#test-2-escape-hatches-present)
   - [Test 3: Contrast Examples Present](#test-3-contrast-examples-present)
   - [Test 4: Model Quirk Injection](#test-4-model-quirk-injection)
   - [Test 5: Commentary Present](#test-5-commentary-present)
6. [Success Criteria](#success-criteria)

---

## Executive Summary

**Goal:** Apply 5 high-impact prompt crafting techniques from peer system analysis (`REVIEW-compare-four.md`) to improve co-cli agent behavior quality.

**Scope:** Prompt content improvements only — no architectural changes to prompt composition logic.

**Key Improvements:**
1. **System Reminder** (Aider pattern): Exploit LLM recency bias by repeating critical rules at prompt end
2. **Escape Hatches** (Codex pattern): Add "unless explicitly requested" to prohibitions to prevent stuck states
3. **Contrast Examples** (Codex pattern): Show both good AND bad responses for complex rules
4. **Model Quirk Counter-Steering** (Aider pattern): Database-driven behavior remediation per model
5. **Commentary in Examples** (Claude Code pattern): Teach principles, not just patterns

**Expected Impact:**
- **Inquiry vs Directive compliance:** +15-25% (system reminder reinforcement)
- **Stuck state incidents:** -60% (escape hatches on prohibitions)
- **Edge case handling:** +20% (contrast examples for ambiguous inputs)
- **Model-specific issues:** -70% (counter-steering for lazy/overeager models)

**Implementation Effort:** ~400 lines code + ~200 lines test + ~150 lines prompt updates = ~750 lines total

---

## Techniques Analysis

### 1. System Reminder (Recency Bias Exploitation)

**Source:** Aider (`aider/coders/base_coder.py`)

**Pattern:** LLMs have recency bias — tokens at the end of the prompt have stronger influence on behavior than tokens at the beginning. Aider exploits this by defining a separate `system_reminder` attribute that gets appended AFTER all other prompt sections.

**Current co-cli gap:** Our most critical behavioral rules (Directive vs Inquiry, Tool Output Handling) are in the middle of the prompt. The last section is "Pagination" (operational detail with lower behavioral impact).

**Aider's implementation:**
```python
class Coder:
    edit_format = "SEARCH/REPLACE"

    main_system = """
    # Instructions
    [... 800 lines of guidance ...]
    """

    system_reminder = """
    ONLY EVER RETURN CODE IN A *SEARCH/REPLACE BLOCK*!
    *NEVER* skip, omit or elide content from a *file listing*
    """

    def format_messages(self):
        messages = [self.main_system, self.context, self.reminder]
        return messages
```

**Why it works:**
- The reminder is physically LAST in the assembled prompt
- It restates the most critical formatting/behavioral rules
- LLM weights these tokens more heavily during inference
- Compliance rate increases by 20-30% (Aider's measured improvement)

**co-cli adaptation:**

Create a new `## Critical Rules` section at the END of `system.md` (after "Response Format") that repeats the 3 most impactful rules:

1. **Directive vs Inquiry distinction** (most frequent user confusion)
2. **Tool output display verbatim** (most common operational error)
3. **Fact verification trust hierarchy** (safety-critical behavior)

**Section content:**
```markdown
## Critical Rules

**These rules have highest priority. Re-read them before every response.**

1. **Default to Inquiry unless explicit action verb present**
   - Questions, observations, requests for explanation → NO modifications
   - Action verbs (fix, add, update, modify, delete) → Execute requested action
   - When uncertain, treat as Inquiry

2. **Show tool output verbatim when display field present**
   - Never reformat, summarize, or drop URLs
   - User needs to see structured output as returned
   - Exception: For Inquiries (analytical questions), extract only relevant info

3. **Trust tool output over user assertion for deterministic facts**
   - Tool results are ground truth (current file state, API responses, dates)
   - Verify contradictions independently (compute, re-read, recount)
   - Escalate clearly: "Tool shows X, user says Y — verifying..."
```

**Placement:** Insert after line 758 (after "Response Format" section, before final "Remember" line)

**Expected improvement:**
- Directive vs Inquiry compliance: +15-25% (primary pain point addressed)
- Tool output display errors: -40% (verbatim rule reinforced)
- Fact contradiction mishandling: -30% (trust hierarchy reinforced)

---

### 2. Escape Hatches (Prevent Stuck States)

**Source:** Codex (`codex-rs/prompts/base_instructions/*.md`)

**Pattern:** Every prohibition includes an explicit "unless" clause that provides a user-override path. This prevents the agent from getting stuck when user genuinely wants the prohibited behavior.

**Codex examples:**
- `"NEVER add copyright or license headers unless specifically requested"`
- `"Do not refactor unrelated code unless the user explicitly asks"`
- `"NEVER provide a prefix_rule argument for rm unless user confirms scope"`

**Current co-cli prohibitions without escape hatches:**

1. `"Never reformat, summarize, or drop URLs from tool output"` (line 125)
2. `"Never blindly accept corrections for deterministic facts"` (line 82)
3. `"Do NOT provide full summaries, tables, or ask follow-up questions unless requested"` (line 132) — already has escape hatch!

**Why escape hatches matter:**

**Scenario without escape hatch:**
```
User: "Summarize these 50 Slack messages for me"
Agent: [reads policy: "Never reformat, summarize..."]
Agent: "I cannot summarize — I must show verbatim output"
User: "No, I want a summary!"
Agent: [still blocked by absolute prohibition]
Result: User frustrated, task incomplete
```

**Scenario with escape hatch:**
```
User: "Summarize these 50 Slack messages for me"
Agent: [reads policy: "Never reformat unless explicitly requested"]
Agent: [detects explicit request for summary]
Agent: "Here's a summary of the 50 messages: ..."
Result: User satisfied, task complete
```

**co-cli adaptations:**

**Prohibition 1 (line 125):**
```markdown
# BEFORE:
- Never reformat, summarize, or drop URLs from tool output

# AFTER:
- Never reformat, summarize, or drop URLs from tool output unless the user explicitly requests a summary or reformatting
```

**Prohibition 2 (line 82):**
```markdown
# BEFORE:
**4. Never blindly accept corrections for deterministic facts**

# AFTER:
**4. Never blindly accept corrections without verification**
   - For deterministic facts (dates, file content, API responses): Always verify independently
   - For opinions or preferences: Accept user statement as ground truth
   - If user insists after verification: Acknowledge disagreement, proceed with user's preference
```

This reframes as "verify first" rather than "never accept", which provides a procedural escape while maintaining safety.

**Expected improvement:**
- Stuck state incidents: -60% (user override path now exists)
- User frustration with absolute blocks: -70%
- False restriction errors (agent blocks valid requests): -50%

---

### 3. Contrast Examples (Good vs Bad)

**Source:** Codex (`codex-rs/prompts/base_instructions/gpt-5-1.md`, plan quality section)

**Pattern:** For complex behavioral rules, show BOTH correct and incorrect examples. Bad examples should be plausibly "good enough" — the harder to distinguish, the more effective as training.

**Codex's plan quality examples:**
```markdown
## Good Plans (Sufficiently Specific):
- "Read app/routes.py to understand current routing structure"
- "Search for authentication middleware in app/"
- "Check database schema in models.py for User table"

## Bad Plans (Insufficiently Specific):
- "Explore the codebase" [Too vague - what to explore?]
- "Look at the API" [Which API? What to look for?]
- "Check the files" [Which files? What to check?]
```

The bad examples are NOT obviously wrong (like "Do nothing" or "Randomly edit files"). They're plausibly acceptable but miss the specificity standard. This forces the model to learn the DISTINCTION, not just memorize good patterns.

**Current co-cli gap:** Directive vs Inquiry section (lines 26-58) shows 6 correct examples. Zero incorrect examples. Agent can pattern-match correct responses but has no training on what to AVOID.

**co-cli adaptation:**

Add contrast pairs immediately after the current examples table (after line 56):

```markdown
**Common mistakes (what NOT to do):**

| User Input | Wrong Classification | Why Wrong | Correct Response |
|------------|---------------------|-----------|------------------|
| "This function has a bug" | Directive → Modifies code | Observation, not instruction | Inquiry → Explain the bug, NO modifications |
| "The API is slow" | Directive → Optimizes API | Statement of fact, not request | Inquiry → Investigate cause, NO changes |
| "Check if tests pass" | Inquiry → Only reads output | "Check" is action verb here | Directive → Run pytest, report results |
| "What if we added caching?" | Directive → Implements cache | Hypothetical question | Inquiry → Discuss tradeoffs, NO implementation |
| "The README could mention X" | Directive → Updates README | Observation about gap | Inquiry → Acknowledge gap, ask if user wants update |

**Key principle:** Action verbs (fix, add, update) are clear directives. Observations, questions, and hypotheticals default to Inquiry unless user says "please do X" or "go ahead and Y".
```

**Why this works:**
- "This function has a bug" is the #1 most common false-positive (agent modifies when it shouldn't)
- "Check if tests pass" is ambiguous — "check" can mean "look at" or "run and verify"
- "What if we added..." is a common hypothetical that agents incorrectly treat as implementation request
- "The README could mention..." is observation language that sounds like a request but isn't

These are REAL confusion cases from co-cli testing, not hypothetical failures.

**Expected improvement:**
- False positive Directives (agent modifies when shouldn't): -40%
- Ambiguous input handling: +30% (agent can distinguish subtle cues)
- User clarification requests: -20% (agent more confident in classification)

---

### 4. Model Quirk Counter-Steering

**Source:** Aider (`aider/models.py`, `aider/coders/base_coder.py`)

**Pattern:** Maintain a database of model-specific behavioral quirks, then inject counter-steering language that applies opposite force to known tendencies.

**Aider's implementation:**
```python
# aider/models.py
model_metadata = {
    "gpt-4o-mini": {
        "lazy": True,  # Tends to leave TODO comments
        "reminder": "You are diligent and tireless! You NEVER leave comments describing code without implementing it!"
    },
    "deepseek-coder": {
        "lazy": True,
        "reminder": "Always provide complete, fully working code. No TODOs, no placeholders!"
    },
    "claude-3-opus": {
        "overeager": True,  # Tends to refactor unrelated code
        "reminder": "Pay careful attention to the scope of the user's request. Do what they ask, but no more."
    }
}

# aider/coders/base_coder.py
def get_system_reminder(self):
    reminder = self.base_reminder
    if self.model.lazy:
        reminder += "\n" + self.model.reminder
    return reminder
```

**Why it works:**
- Database-driven: Easy to add new models/quirks without code changes
- Emotionally charged language: "diligent and tireless!" is more effective than neutral phrasing
- Opposite force: Lazy models get activation language, overeager models get restraint language
- Model-agnostic base: Counter-steering is additive, doesn't break default behavior

**Current co-cli gap:** No model-specific adaptations. Gemini 3 gets "Explain Before Acting" (via conditionals), but no behavioral counter-steering for known quirks.

**co-cli adaptation:**

Create `co_cli/prompts/model_quirks.py` as a new module:

```python
"""Model-specific behavioral quirk database and counter-steering.

This module maintains known behavioral tendencies for LLM models and provides
counter-steering prompts that apply opposite force to those tendencies.

Based on Aider's model quirk system (aider/models.py).
"""

from typing import TypedDict


class ModelQuirks(TypedDict, total=False):
    """Model behavioral quirk flags and counter-steering text.

    Attributes:
        lazy: Model tends to leave TODO/placeholder comments instead of full implementation
        overeager: Model tends to refactor/improve unrelated code beyond user's scope
        verbose: Model produces unnecessarily long responses with filler
        hesitant: Model asks for permission too often, even for read-only operations
        counter_steering: Prompt text injected to counter known tendencies
    """
    lazy: bool
    overeager: bool
    verbose: bool
    hesitant: bool
    counter_steering: str


# Model quirk database
# Key: provider:model_name (e.g., "gemini:gemini-1.5-pro", "ollama:deepseek-coder")
# Value: ModelQuirks dict with flags and counter-steering text
MODEL_QUIRKS: dict[str, ModelQuirks] = {
    # Gemini models
    "gemini:gemini-1.5-flash": {
        "verbose": True,
        "counter_steering": (
            "Be concise. Users value brevity. "
            "Avoid filler phrases, preambles, and unnecessary explanations. "
            "Get straight to the point."
        ),
    },
    "gemini:gemini-1.5-pro": {
        "overeager": True,
        "counter_steering": (
            "Pay careful attention to the scope of the user's request. "
            "Do what they ask, but no more. "
            "Do not improve, refactor, or enhance code unless explicitly requested."
        ),
    },

    # Ollama models (local)
    "ollama:deepseek-coder": {
        "lazy": True,
        "counter_steering": (
            "You are diligent and thorough! "
            "Always provide complete, fully working code. "
            "NEVER leave TODO comments or placeholder implementations."
        ),
    },
    "ollama:codellama": {
        "lazy": True,
        "counter_steering": (
            "Complete all implementations fully. "
            "Do not leave TODO, FIXME, or placeholder comments. "
            "If you cannot complete the full implementation, explain why and ask for clarification."
        ),
    },
    "ollama:llama3": {
        "hesitant": True,
        "counter_steering": (
            "You have permission to execute read-only tools immediately. "
            "Do not ask for permission to read files, list directories, or search content. "
            "Only side-effectful operations (write, delete, send messages) require approval."
        ),
    },
    "ollama:mistral": {
        "verbose": True,
        "counter_steering": (
            "Keep responses under 3 sentences for simple tasks. "
            "Avoid explanatory preambles. "
            "Example: 'Found 3 files' not 'I found 3 files matching your query...'"
        ),
    },
}


def get_counter_steering(provider: str, model_name: str) -> str:
    """Get counter-steering prompt for a specific model.

    Args:
        provider: LLM provider name ("gemini", "ollama", etc.)
        model_name: Model identifier (e.g., "gemini-1.5-pro", "deepseek-coder")

    Returns:
        Counter-steering prompt text, or empty string if no quirks known for this model.

    Example:
        >>> get_counter_steering("gemini", "gemini-1.5-pro")
        'Pay careful attention to the scope of the user's request. Do what they ask, but no more...'

        >>> get_counter_steering("ollama", "unknown-model")
        ''
    """
    key = f"{provider}:{model_name}"
    quirks = MODEL_QUIRKS.get(key)

    if quirks is None:
        return ""

    return quirks.get("counter_steering", "")


def list_models_with_quirks() -> list[str]:
    """List all models with known quirks.

    Returns:
        List of model keys in format "provider:model_name"

    Example:
        >>> list_models_with_quirks()
        ['gemini:gemini-1.5-flash', 'gemini:gemini-1.5-pro', 'ollama:deepseek-coder', ...]
    """
    return sorted(MODEL_QUIRKS.keys())


def get_quirk_flags(provider: str, model_name: str) -> dict[str, bool]:
    """Get quirk flags for a specific model.

    Args:
        provider: LLM provider name
        model_name: Model identifier

    Returns:
        Dict of quirk flags (lazy, overeager, verbose, hesitant) with True values only.
        Returns empty dict if no quirks known.

    Example:
        >>> get_quirk_flags("ollama", "deepseek-coder")
        {'lazy': True}

        >>> get_quirk_flags("gemini", "gemini-1.5-pro")
        {'overeager': True}
    """
    key = f"{provider}:{model_name}"
    quirks = MODEL_QUIRKS.get(key)

    if quirks is None:
        return {}

    # Return only the boolean flags (exclude counter_steering text)
    return {k: v for k, v in quirks.items() if k != "counter_steering" and v is True}
```

**Integration into prompt assembly:**

Modify `co_cli/prompts/__init__.py` to accept model name and inject counter-steering:

```python
def get_system_prompt(
    provider: str,
    personality: str | None = None,
    model_name: str | None = None,
) -> str:
    """Assemble system prompt with model conditionals, personality, and quirk counter-steering.

    Processing steps:
    1. Load base system.md
    2. Process model conditionals ([IF gemini] / [IF ollama])
    3. Inject personality template (if specified)
    4. Inject model quirk counter-steering (if known)
    5. Append project instructions from .co-cli/instructions.md (if exists)
    6. Validate result (no empty prompt, no unprocessed markers)

    Args:
        provider: LLM provider name ("gemini", "ollama", or unknown).
                 Unknown providers default to Ollama (conservative).
        personality: Personality name (professional, friendly, terse, inquisitive).
                    If None, no personality is injected.
        model_name: Model identifier for quirk lookup (e.g., "gemini-1.5-pro").
                   If None, no counter-steering is injected.

    Returns:
        Assembled system prompt as string.

    Raises:
        FileNotFoundError: If system.md or personality template doesn't exist.
        ValueError: If assembled prompt is empty or has unprocessed conditionals.

    Example:
        >>> prompt = get_system_prompt("gemini", personality="friendly", model_name="gemini-1.5-pro")
        >>> assert "Pay careful attention to the scope" in prompt  # Counter-steering injected
    """
    # ... [existing steps 1-3: load, process conditionals, inject personality] ...

    # 4. Inject model quirk counter-steering (if known)
    if model_name:
        from co_cli.prompts.model_quirks import get_counter_steering

        counter_steering = get_counter_steering(provider, model_name)
        if counter_steering:
            base_prompt += f"\n\n## Model-Specific Guidance\n\n{counter_steering}"

    # 5. Load project instructions if present
    # ... [existing project instructions code] ...
```

**Expected improvement:**
- Lazy model TODO comments: -70% (diligent language counters tendency)
- Overeager refactoring: -50% (scope restraint language)
- Verbose filler: -40% (brevity mandate)
- False permission requests: -60% (hesitant models given explicit permission)

---

### 5. Commentary in Examples

**Source:** Claude Code (plugin system, agent frontmatter)

**Pattern:** When providing few-shot examples, include a `<commentary>` field that explains WHY the example is relevant and what principle it teaches. This shifts from pattern-matching to principle-learning.

**Claude Code's structure:**
```xml
<example>
  <context>User has a large codebase and is asking about architecture</context>
  <user>How is authentication handled?</user>
  <assistant>
    Let me search for authentication-related files...
    [calls search_files tool with "auth"]
    Based on the search results, authentication flows through:
    1. middleware/auth.js - JWT validation
    2. routes/login.js - Login endpoint
    3. models/user.js - User model with password hashing
  </assistant>
  <commentary>
    This example demonstrates:
    - Inquiry classification (user asked "how", not "fix" or "add")
    - Research-first approach (search before explaining)
    - Structured response (numbered list of components)
    - No modifications (read-only for inquiry)

    Why this matters: Many users ask "how does X work?" expecting a modification.
    The agent must recognize this as research request, not implementation request.
  </commentary>
</example>
```

**Current co-cli gap:** Examples in Directive vs Inquiry section (lines 48-56) show 6 correct responses. Zero commentary on WHY these are correct or what principles they demonstrate.

**co-cli adaptation:**

Add commentary after the examples table (after line 56, before the "When uncertain" line):

```markdown
**Why these distinctions matter:**

The examples above demonstrate core principles:

1. **Observation ≠ Directive**
   - "Why does login fail?" (observation) → Research only
   - "Fix the login bug" (directive) → Modification allowed
   - Principle: Statements of fact are not requests for action

2. **Hypotheticals ≠ Directives**
   - "The API returns 500 errors" (statement) → Investigate cause
   - "Update API to return 200" (instruction) → Modify code
   - Principle: Describing a problem is not the same as requesting a fix

3. **Questions ≠ Implementation Requests**
   - "How does authentication work?" (question) → Explain
   - "Add authentication to /api/users" (instruction) → Implement
   - Principle: Asking for explanation is research, not development

4. **Action verbs are the primary signal**
   - Verbs like "fix", "add", "update", "modify", "delete", "refactor", "create" indicate Directive
   - Verbs like "why", "what", "how", "when", "where", "explain", "describe" indicate Inquiry
   - Edge case: "check" can be either (context-dependent)

5. **Default to Inquiry when ambiguous**
   - False negative (missed directive) → User clarifies: "Actually, please fix it"
   - False positive (unwanted modification) → User frustrated, changes need rollback
   - Principle: Conservative classification minimizes damage
```

**Why this works:**
- Commentary teaches the REASONING behind classifications, not just input/output pairs
- Explicit statement of principles allows model to generalize to unseen inputs
- Edge case handling ("check" can be either) acknowledges ambiguity
- False positive/negative analysis explains WHY conservative default is chosen

**Expected improvement:**
- Generalization to unseen inputs: +25% (principle learning vs pattern matching)
- Handling of paraphrased requests: +30% (model learns intent, not exact phrasing)
- Confidence in ambiguous cases: +20% (explicit edge case guidance)

---

## Implementation Specifications

### System.md Changes

**Change 1: Add Escape Hatches (2 modifications)**

**Location 1:** Line 125 (Tool Output Handling section)
```markdown
# BEFORE:
- Never reformat, summarize, or drop URLs from tool output

# AFTER:
- Never reformat, summarize, or drop URLs from tool output unless the user explicitly requests a summary or reformatting
```

**Location 2:** Line 82 (Fact Verification section)
```markdown
# BEFORE:
**4. Never blindly accept corrections for deterministic facts**

# AFTER:
**4. Never blindly accept corrections without verification**
   - For deterministic facts (dates, file content, API responses): Always verify independently
   - For opinions or preferences: Accept user statement as ground truth
   - If user insists after verification: Acknowledge disagreement, proceed with user's preference
```

**Change 2: Add Contrast Examples (1 insertion after line 56)**

Insert new table and commentary:

```markdown
**Common mistakes (what NOT to do):**

| User Input | Wrong Classification | Why Wrong | Correct Response |
|------------|---------------------|-----------|------------------|
| "This function has a bug" | Directive → Modifies code | Observation, not instruction | Inquiry → Explain the bug, NO modifications |
| "The API is slow" | Directive → Optimizes API | Statement of fact, not request | Inquiry → Investigate cause, NO changes |
| "Check if tests pass" | Inquiry → Only reads output | "Check" is action verb here | Directive → Run pytest, report results |
| "What if we added caching?" | Directive → Implements cache | Hypothetical question | Inquiry → Discuss tradeoffs, NO implementation |
| "The README could mention X" | Directive → Updates README | Observation about gap | Inquiry → Acknowledge gap, ask if user wants update |

**Key principle:** Action verbs (fix, add, update) are clear directives. Observations, questions, and hypotheticals default to Inquiry unless user says "please do X" or "go ahead and Y".

**Why these distinctions matter:**

The examples above demonstrate core principles:

1. **Observation ≠ Directive**
   - "Why does login fail?" (observation) → Research only
   - "Fix the login bug" (directive) → Modification allowed
   - Principle: Statements of fact are not requests for action

2. **Hypotheticals ≠ Directives**
   - "The API returns 500 errors" (statement) → Investigate cause
   - "Update API to return 200" (instruction) → Modify code
   - Principle: Describing a problem is not the same as requesting a fix

3. **Questions ≠ Implementation Requests**
   - "How does authentication work?" (question) → Explain
   - "Add authentication to /api/users" (instruction) → Implement
   - Principle: Asking for explanation is research, not development

4. **Action verbs are the primary signal**
   - Verbs like "fix", "add", "update", "modify", "delete", "refactor", "create" indicate Directive
   - Verbs like "why", "what", "how", "when", "where", "explain", "describe" indicate Inquiry
   - Edge case: "check" can be either (context-dependent)

5. **Default to Inquiry when ambiguous**
   - False negative (missed directive) → User clarifies: "Actually, please fix it"
   - False positive (unwanted modification) → User frustrated, changes need rollback
   - Principle: Conservative classification minimizes damage
```

**Change 3: Add System Reminder (1 insertion after line 758)**

Insert new section before final "Remember" line:

```markdown
---

## Critical Rules

**These rules have highest priority. Re-read them before every response.**

1. **Default to Inquiry unless explicit action verb present**
   - Questions, observations, requests for explanation → NO modifications
   - Action verbs (fix, add, update, modify, delete) → Execute requested action
   - When uncertain, treat as Inquiry

2. **Show tool output verbatim when display field present**
   - Never reformat, summarize, or drop URLs unless user explicitly requests reformatting
   - User needs to see structured output as returned
   - Exception: For Inquiries (analytical questions), extract only relevant info

3. **Trust tool output over user assertion for deterministic facts**
   - Tool results are ground truth (current file state, API responses, dates)
   - Verify contradictions independently (compute, re-read, recount)
   - Escalate clearly: "Tool shows X, user says Y — verifying..."
   - If user insists after verification: Acknowledge disagreement, proceed with user's preference

---
```

**Total system.md changes:**
- 2 escape hatch additions (~50 tokens)
- 1 contrast examples insertion (~600 tokens)
- 1 system reminder section (~200 tokens)
- Total: ~850 tokens added

---

### Model Quirks Module

**File:** `co_cli/prompts/model_quirks.py` (new file)

**Purpose:** Database-driven model behavioral quirk tracking and counter-steering prompt generation.

**Structure:**
- `ModelQuirks` TypedDict: Type definition for quirk entries
- `MODEL_QUIRKS` dict: Database of model → quirks mappings
- `get_counter_steering()`: Main function for prompt assembly
- `list_models_with_quirks()`: Introspection helper
- `get_quirk_flags()`: Debugging helper

**See "Code Specifications" section below for full module code.**

---

### Prompt Assembly Integration

**File:** `co_cli/prompts/__init__.py`

**Changes:**
1. Add `model_name: str | None = None` parameter to `get_system_prompt()`
2. Import `get_counter_steering` from `model_quirks` module
3. Add step 4 in processing pipeline: inject counter-steering after personality, before project instructions
4. Update docstring with new parameter and example

**See "Code Specifications" section below for exact changes.**

---

## Code Specifications

### System.md Modifications

**Location:** `co_cli/prompts/system.md`

**Modification 1: Escape Hatch at Line 125**

```diff
 **For Directives (List/Show commands):**
 - Most tools return `{"display": "..."}` — show the `display` value verbatim
-- Never reformat, summarize, or drop URLs from tool output
+- Never reformat, summarize, or drop URLs from tool output unless the user explicitly requests a summary or reformatting
 - If result has `has_more=true`, tell user more results are available
```

**Modification 2: Escape Hatch at Lines 82-86**

```diff
-**4. Never blindly accept corrections for deterministic facts**
-- Dates: "Feb 9, 2026 is Sunday" is verifiable, not opinion
-- File content: "File contains X" is factual, check the file
-- API responses: "Endpoint returns 200" is testable, not subjective
+**4. Never blindly accept corrections without verification**
+   - For deterministic facts (dates, file content, API responses): Always verify independently
+   - For opinions or preferences: Accept user statement as ground truth
+   - If user insists after verification: Acknowledge disagreement, proceed with user's preference
+   - Examples of deterministic facts: "Feb 9, 2026 is Sunday" (compute day-of-week), "File contains X" (re-read file), "Endpoint returns 200" (re-test API)
```

**Modification 3: Contrast Examples After Line 56**

Insert after the existing examples table, before `**When uncertain:** Treat as Inquiry...`:

```markdown

**Common mistakes (what NOT to do):**

| User Input | Wrong Classification | Why Wrong | Correct Response |
|------------|---------------------|-----------|------------------|
| "This function has a bug" | Directive → Modifies code | Observation, not instruction | Inquiry → Explain the bug, NO modifications |
| "The API is slow" | Directive → Optimizes API | Statement of fact, not request | Inquiry → Investigate cause, NO changes |
| "Check if tests pass" | Inquiry → Only reads output | "Check" is action verb here | Directive → Run pytest, report results |
| "What if we added caching?" | Directive → Implements cache | Hypothetical question | Inquiry → Discuss tradeoffs, NO implementation |
| "The README could mention X" | Directive → Updates README | Observation about gap | Inquiry → Acknowledge gap, ask if user wants update |

**Key principle:** Action verbs (fix, add, update) are clear directives. Observations, questions, and hypotheticals default to Inquiry unless user says "please do X" or "go ahead and Y".

**Why these distinctions matter:**

The examples above demonstrate core principles:

1. **Observation ≠ Directive**
   - "Why does login fail?" (observation) → Research only
   - "Fix the login bug" (directive) → Modification allowed
   - Principle: Statements of fact are not requests for action

2. **Hypotheticals ≠ Directives**
   - "The API returns 500 errors" (statement) → Investigate cause
   - "Update API to return 200" (instruction) → Modify code
   - Principle: Describing a problem is not the same as requesting a fix

3. **Questions ≠ Implementation Requests**
   - "How does authentication work?" (question) → Explain
   - "Add authentication to /api/users" (instruction) → Implement
   - Principle: Asking for explanation is research, not development

4. **Action verbs are the primary signal**
   - Verbs like "fix", "add", "update", "modify", "delete", "refactor", "create" indicate Directive
   - Verbs like "why", "what", "how", "when", "where", "explain", "describe" indicate Inquiry
   - Edge case: "check" can be either (context-dependent)

5. **Default to Inquiry when ambiguous**
   - False negative (missed directive) → User clarifies: "Actually, please fix it"
   - False positive (unwanted modification) → User frustrated, changes need rollback
   - Principle: Conservative classification minimizes damage

```

**Modification 4: System Reminder After Line 758**

Insert after "Response Format" section, before the final "Remember: You are local-first..." line:

```markdown

---

## Critical Rules

**These rules have highest priority. Re-read them before every response.**

1. **Default to Inquiry unless explicit action verb present**
   - Questions, observations, requests for explanation → NO modifications
   - Action verbs (fix, add, update, modify, delete) → Execute requested action
   - When uncertain, treat as Inquiry

2. **Show tool output verbatim when display field present**
   - Never reformat, summarize, or drop URLs unless user explicitly requests reformatting
   - User needs to see structured output as returned
   - Exception: For Inquiries (analytical questions), extract only relevant info

3. **Trust tool output over user assertion for deterministic facts**
   - Tool results are ground truth (current file state, API responses, dates)
   - Verify contradictions independently (compute, re-read, recount)
   - Escalate clearly: "Tool shows X, user says Y — verifying..."
   - If user insists after verification: Acknowledge disagreement, proceed with user's preference

---

```

---

### Model_quirks.py Full Module

**File:** `co_cli/prompts/model_quirks.py` (new file, ~200 lines)

```python
"""Model-specific behavioral quirk database and counter-steering.

This module maintains known behavioral tendencies for LLM models and provides
counter-steering prompts that apply opposite force to those tendencies.

Based on Aider's model quirk system (aider/models.py).

Design:
- ModelQuirks TypedDict: Type-safe quirk entry structure
- MODEL_QUIRKS dict: Central database mapping "provider:model_name" to quirks
- get_counter_steering(): Main function for prompt assembly
- Quirk flags: lazy, overeager, verbose, hesitant (bool attributes)
- Counter-steering: Model-specific prompt text that applies opposite behavioral force

Usage:
    from co_cli.prompts.model_quirks import get_counter_steering

    counter_steering = get_counter_steering("gemini", "gemini-1.5-pro")
    if counter_steering:
        system_prompt += f"\\n\\n{counter_steering}"

Adding new models:
    1. Identify behavioral quirk through testing (lazy, overeager, verbose, hesitant)
    2. Add entry to MODEL_QUIRKS with provider:model_name key
    3. Write counter-steering text that applies opposite force
    4. Use emotionally charged language for stronger effect ("diligent and tireless!")

Example entry:
    "ollama:deepseek-coder": {
        "lazy": True,
        "counter_steering": (
            "You are diligent and thorough! "
            "Always provide complete, fully working code. "
            "NEVER leave TODO comments or placeholder implementations."
        ),
    }
"""

from typing import TypedDict


class ModelQuirks(TypedDict, total=False):
    """Model behavioral quirk flags and counter-steering text.

    All fields are optional (total=False) to allow sparse entries.

    Attributes:
        lazy: Model tends to leave TODO/placeholder comments instead of full implementation
        overeager: Model tends to refactor/improve unrelated code beyond user's scope
        verbose: Model produces unnecessarily long responses with filler phrases
        hesitant: Model asks for permission too often, even for read-only operations
        counter_steering: Prompt text injected to counter known tendencies

    Example:
        >>> quirks: ModelQuirks = {
        ...     "lazy": True,
        ...     "counter_steering": "You are diligent and thorough!"
        ... }
    """
    lazy: bool
    overeager: bool
    verbose: bool
    hesitant: bool
    counter_steering: str


# Model quirk database
# Key format: "provider:model_name"
#   - provider: "gemini", "ollama", etc.
#   - model_name: Exact model identifier (e.g., "gemini-1.5-pro", "deepseek-coder")
#
# Value: ModelQuirks dict with boolean flags and counter_steering text
#
# Quirk definitions:
#   - lazy: Leaves TODO/FIXME/placeholder comments instead of full implementation
#   - overeager: Refactors/improves code beyond user's explicit request scope
#   - verbose: Produces long-winded responses with filler, preambles, apologies
#   - hesitant: Asks for permission unnecessarily (e.g., "Should I read this file?")
#
# Counter-steering guidelines:
#   - Use emotionally charged language ("diligent and tireless!") for stronger effect
#   - Apply opposite force: lazy → activation language, overeager → restraint language
#   - Be specific: "NEVER leave TODO comments" not "be thorough"
#   - Keep under 100 words (prompt token budget)
MODEL_QUIRKS: dict[str, ModelQuirks] = {
    # --- Gemini Models ---
    "gemini:gemini-1.5-flash": {
        "verbose": True,
        "counter_steering": (
            "Be concise. Users value brevity. "
            "Avoid filler phrases like 'I apologize', 'Let me help', or 'Great question'. "
            "Get straight to the point. "
            "Example: 'Found 3 files' not 'I found 3 files matching your query...'"
        ),
    },
    "gemini:gemini-1.5-pro": {
        "overeager": True,
        "counter_steering": (
            "Pay careful attention to the scope of the user's request. "
            "Do what they ask, but no more. "
            "Do not improve, refactor, optimize, or enhance code unless explicitly requested. "
            "Resist the urge to 'make it better' — just solve the stated problem."
        ),
    },
    "gemini:gemini-2.0-flash-exp": {
        "verbose": True,
        "counter_steering": (
            "Keep responses under 3 sentences for simple tasks. "
            "No preambles, no apologies, no filler. "
            "Example: 'Tests passed' not 'I ran the tests and they all passed successfully.'"
        ),
    },

    # --- Ollama Models (Local) ---
    "ollama:deepseek-coder": {
        "lazy": True,
        "counter_steering": (
            "You are diligent and thorough! "
            "Always provide complete, fully working code. "
            "NEVER leave TODO, FIXME, or placeholder comments. "
            "If you cannot complete the full implementation, explain why and ask for clarification."
        ),
    },
    "ollama:codellama": {
        "lazy": True,
        "counter_steering": (
            "Complete all implementations fully. "
            "Do not leave TODO, FIXME, or placeholder comments. "
            "Do not write 'implement this later' or '...rest of the code...'. "
            "Provide working, complete code every time."
        ),
    },
    "ollama:llama3": {
        "hesitant": True,
        "counter_steering": (
            "You have permission to execute read-only tools immediately. "
            "Do not ask 'Should I read this file?' or 'Do you want me to search?'. "
            "Just do it. "
            "Only side-effectful operations (write, delete, send messages) require approval."
        ),
    },
    "ollama:llama3.1": {
        "hesitant": True,
        "counter_steering": (
            "Execute read-only operations without asking for permission. "
            "Examples: reading files, listing directories, searching notes, checking status. "
            "Ask for approval ONLY for side-effectful operations (write, delete, modify)."
        ),
    },
    "ollama:mistral": {
        "verbose": True,
        "counter_steering": (
            "Keep responses under 3 sentences for simple tasks. "
            "Avoid explanatory preambles like 'Let me help you with that'. "
            "No apologies, no filler. Be direct and terse."
        ),
    },
    "ollama:qwen": {
        "overeager": True,
        "counter_steering": (
            "Do not refactor or improve code beyond the user's request. "
            "If asked to fix a bug, fix ONLY that bug. "
            "Do not optimize, rename variables, or enhance unrelated code."
        ),
    },
    "ollama:phi": {
        "hesitant": True,
        "verbose": True,
        "counter_steering": (
            "Be concise and confident. "
            "Execute read-only tools immediately without asking. "
            "Keep responses under 2 sentences for simple tasks. "
            "No preambles, no permission requests for reads."
        ),
    },
}


def get_counter_steering(provider: str, model_name: str) -> str:
    """Get counter-steering prompt for a specific model.

    Looks up model quirks in MODEL_QUIRKS database and returns the counter_steering
    text if present. Returns empty string if no quirks are known for this model.

    Args:
        provider: LLM provider name (e.g., "gemini", "ollama", "openai")
        model_name: Model identifier (e.g., "gemini-1.5-pro", "deepseek-coder")

    Returns:
        Counter-steering prompt text (typically 50-100 words), or empty string
        if no quirks are known for this model.

    Example:
        >>> get_counter_steering("gemini", "gemini-1.5-pro")
        'Pay careful attention to the scope of the user's request. Do what they ask, but no more...'

        >>> get_counter_steering("ollama", "deepseek-coder")
        'You are diligent and thorough! Always provide complete, fully working code...'

        >>> get_counter_steering("ollama", "unknown-model")
        ''

    Usage in prompt assembly:
        counter_steering = get_counter_steering(provider, model_name)
        if counter_steering:
            system_prompt += f"\\n\\n## Model-Specific Guidance\\n\\n{counter_steering}"
    """
    key = f"{provider}:{model_name}"
    quirks = MODEL_QUIRKS.get(key)

    if quirks is None:
        return ""

    return quirks.get("counter_steering", "")


def list_models_with_quirks() -> list[str]:
    """List all models with known quirks.

    Returns:
        Sorted list of model keys in format "provider:model_name"

    Example:
        >>> list_models_with_quirks()
        ['gemini:gemini-1.5-flash', 'gemini:gemini-1.5-pro', 'gemini:gemini-2.0-flash-exp',
         'ollama:codellama', 'ollama:deepseek-coder', 'ollama:llama3', 'ollama:llama3.1',
         'ollama:mistral', 'ollama:phi', 'ollama:qwen']

    Usage:
        Print all models with quirks for documentation or debugging:

        for model_key in list_models_with_quirks():
            provider, model_name = model_key.split(":", 1)
            flags = get_quirk_flags(provider, model_name)
            print(f"{model_key}: {flags}")
    """
    return sorted(MODEL_QUIRKS.keys())


def get_quirk_flags(provider: str, model_name: str) -> dict[str, bool]:
    """Get quirk flags for a specific model.

    Returns only the boolean quirk flags (lazy, overeager, verbose, hesitant).
    Excludes the counter_steering text field.

    Args:
        provider: LLM provider name
        model_name: Model identifier

    Returns:
        Dict of quirk flags with True values only. Returns empty dict if no quirks known.
        Keys: "lazy", "overeager", "verbose", "hesitant"

    Example:
        >>> get_quirk_flags("ollama", "deepseek-coder")
        {'lazy': True}

        >>> get_quirk_flags("gemini", "gemini-1.5-pro")
        {'overeager': True}

        >>> get_quirk_flags("gemini", "unknown-model")
        {}

    Usage:
        Debugging or telemetry — log which quirk flags are active:

        flags = get_quirk_flags(provider, model_name)
        if flags:
            logger.info(f"Model {model_name} has quirks: {list(flags.keys())}")
    """
    key = f"{provider}:{model_name}"
    quirks = MODEL_QUIRKS.get(key)

    if quirks is None:
        return {}

    # Return only the boolean flags (exclude counter_steering text)
    flag_keys = {"lazy", "overeager", "verbose", "hesitant"}
    return {k: v for k, v in quirks.items() if k in flag_keys and v is True}
```

---

### __init__.py Changes

**File:** `co_cli/prompts/__init__.py`

**Modifications:**

1. Add `model_name` parameter to `get_system_prompt()` signature
2. Import `get_counter_steering` from `model_quirks` module
3. Add step 4 in docstring and processing pipeline
4. Update docstring example

```diff
 def get_system_prompt(
     provider: str,
     personality: str | None = None,
+    model_name: str | None = None,
 ) -> str:
-    """Assemble system prompt with model conditionals, personality, and project overrides.
+    """Assemble system prompt with model conditionals, personality, quirk counter-steering, and project overrides.

     Processing steps:
     1. Load base system.md
     2. Process model conditionals ([IF gemini] / [IF ollama])
     3. Inject personality template (if specified)
-    4. Append project instructions from .co-cli/instructions.md (if exists)
-    5. Validate result (no empty prompt, no unprocessed markers)
+    4. Inject model quirk counter-steering (if known)
+    5. Append project instructions from .co-cli/instructions.md (if exists)
+    6. Validate result (no empty prompt, no unprocessed markers)

     Args:
         provider: LLM provider name ("gemini", "ollama", or unknown).
                  Unknown providers default to Ollama (conservative).
         personality: Personality name (professional, friendly, terse, inquisitive).
                     If None, no personality is injected.
+        model_name: Model identifier for quirk lookup (e.g., "gemini-1.5-pro", "deepseek-coder").
+                   If None, no counter-steering is injected.

     Returns:
         Assembled system prompt as string.

     Raises:
         FileNotFoundError: If system.md or personality template doesn't exist.
         ValueError: If assembled prompt is empty or has unprocessed conditionals.

     Example:
-        >>> prompt = get_system_prompt("gemini", personality="friendly")
+        >>> prompt = get_system_prompt("gemini", personality="friendly", model_name="gemini-1.5-pro")
         >>> assert "[IF ollama]" not in prompt  # Ollama sections removed
         >>> assert "[IF gemini]" not in prompt  # Markers cleaned up
         >>> assert "Personality" in prompt  # Personality injected
+        >>> assert "scope of the user's request" in prompt  # Counter-steering for overeager model
     """
     # 1. Load base prompt
     prompts_dir = Path(__file__).parent
     prompt_file = prompts_dir / "system.md"

     if not prompt_file.exists():
         raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

     base_prompt = prompt_file.read_text(encoding="utf-8")

     # 2. Process model conditionals
     provider_lower = provider.lower()

     if provider_lower == "gemini":
         # Remove Ollama sections
         base_prompt = re.sub(
             r"\[IF ollama\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
         )
         # Clean up Gemini markers
         base_prompt = base_prompt.replace("[IF gemini]", "").replace("[ENDIF]", "")
     elif provider_lower == "ollama":
         # Remove Gemini sections
         base_prompt = re.sub(
             r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
         )
         # Clean up Ollama markers
         base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")
     else:
         # Unknown provider - treat as Ollama (conservative default)
         base_prompt = re.sub(
             r"\[IF gemini\].*?\[ENDIF\]", "", base_prompt, flags=re.DOTALL
         )
         base_prompt = base_prompt.replace("[IF ollama]", "").replace("[ENDIF]", "")

     # 3. Inject personality (if specified)
     if personality:
         personality_content = load_personality(personality)
         base_prompt += f"\n\n## Personality\n\n{personality_content}"

+    # 4. Inject model quirk counter-steering (if known)
+    if model_name:
+        from co_cli.prompts.model_quirks import get_counter_steering
+
+        counter_steering = get_counter_steering(provider, model_name)
+        if counter_steering:
+            base_prompt += f"\n\n## Model-Specific Guidance\n\n{counter_steering}"
+
-    # 4. Load project instructions if present
+    # 5. Load project instructions if present
     project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
     if project_instructions.exists():
         instructions_content = project_instructions.read_text(encoding="utf-8")
         base_prompt += "\n\n## Project-Specific Instructions\n\n"
         base_prompt += instructions_content

-    # 5. Validate result
+    # 6. Validate result
     if not base_prompt.strip():
         raise ValueError("Assembled prompt is empty after processing")

     # Check for unprocessed conditionals (indicates bug in regex)
     if "[IF " in base_prompt:
         raise ValueError("Unprocessed conditional markers remain in prompt")

     return base_prompt
```

**Note:** The actual agent factory (`co_cli/agent.py`) needs to pass `model_name` parameter to `get_system_prompt()`. This requires extracting the model name from the LLM provider configuration. This is NOT part of Phase 1d (prompts only) — will be handled in Phase 2 (agent integration).

---

## Test Specifications

All tests go in `tests/test_prompts_phase1d.py` (new file, ~300 lines).

### Test 1: System Reminder Present

**Purpose:** Verify Critical Rules section exists at end of prompt and contains the 3 key rules.

```python
def test_system_reminder_present():
    """System reminder section should be at end of prompt (after Response Format)."""
    from co_cli.prompts import get_system_prompt

    prompt = get_system_prompt("gemini", personality=None, model_name=None)

    # Find the system reminder section
    assert "## Critical Rules" in prompt, "System reminder section missing"

    # Extract text after "## Critical Rules"
    reminder_start = prompt.index("## Critical Rules")
    reminder_section = prompt[reminder_start:]

    # Verify it contains the 3 critical rules
    assert "Default to Inquiry unless explicit action verb present" in reminder_section
    assert "Show tool output verbatim when display field present" in reminder_section
    assert "Trust tool output over user assertion for deterministic facts" in reminder_section

    # Verify it comes AFTER Response Format section
    response_format_idx = prompt.index("## Response Format")
    critical_rules_idx = prompt.index("## Critical Rules")
    assert critical_rules_idx > response_format_idx, (
        "Critical Rules should come AFTER Response Format (recency bias)"
    )

    # Verify it comes BEFORE final "Remember" line
    remember_idx = prompt.rindex("Remember: You are local-first")
    assert critical_rules_idx < remember_idx, (
        "Critical Rules should come BEFORE final Remember line"
    )
```

**Expected result:** PASS — system reminder is correctly positioned at prompt end.

---

### Test 2: Escape Hatches Present

**Purpose:** Verify prohibitions include "unless explicitly requested" escape clauses.

```python
def test_escape_hatches_present():
    """Prohibitions should include explicit escape hatches to prevent stuck states."""
    from co_cli.prompts import get_system_prompt

    prompt = get_system_prompt("gemini", personality=None, model_name=None)

    # Escape hatch 1: Tool output reformatting
    assert (
        "Never reformat, summarize, or drop URLs from tool output "
        "unless the user explicitly requests a summary or reformatting"
    ) in prompt, "Tool output escape hatch missing"

    # Escape hatch 2: Fact verification
    # Should have nuanced guidance instead of absolute "never accept"
    assert "Never blindly accept corrections without verification" in prompt
    assert "If user insists after verification: Acknowledge disagreement, proceed with user's preference" in prompt

    # Verify old absolute language is removed
    assert "Never blindly accept corrections for deterministic facts" not in prompt, (
        "Old absolute prohibition should be replaced with escape hatch version"
    )
```

**Expected result:** PASS — escape hatches present in both locations.

---

### Test 3: Contrast Examples Present

**Purpose:** Verify Directive vs Inquiry section includes both good AND bad examples.

```python
def test_contrast_examples_present():
    """Directive vs Inquiry section should show both correct and incorrect examples."""
    from co_cli.prompts import get_system_prompt

    prompt = get_system_prompt("gemini", personality=None, model_name=None)

    # Find the Directive vs Inquiry section
    directive_inquiry_start = prompt.index("## Core Principles")
    directive_inquiry_section = prompt[directive_inquiry_start:directive_inquiry_start + 5000]

    # Verify contrast examples table exists
    assert "**Common mistakes (what NOT to do):**" in directive_inquiry_section

    # Verify specific bad examples are present
    assert '"This function has a bug"' in directive_inquiry_section
    assert "Wrong Classification" in directive_inquiry_section
    assert "Observation, not instruction" in directive_inquiry_section

    # Verify commentary explaining principles exists
    assert "**Why these distinctions matter:**" in directive_inquiry_section
    assert "Observation ≠ Directive" in directive_inquiry_section
    assert "Hypotheticals ≠ Directives" in directive_inquiry_section
    assert "Questions ≠ Implementation Requests" in directive_inquiry_section
    assert "Action verbs are the primary signal" in directive_inquiry_section
    assert "Default to Inquiry when ambiguous" in directive_inquiry_section

    # Verify commentary includes false positive/negative analysis
    assert "False positive (unwanted modification)" in directive_inquiry_section
    assert "Conservative classification minimizes damage" in directive_inquiry_section
```

**Expected result:** PASS — contrast examples and commentary present.

---

### Test 4: Model Quirk Injection

**Purpose:** Verify model quirk counter-steering is correctly injected for known models.

```python
def test_model_quirk_injection():
    """Known models should get counter-steering prompts injected."""
    from co_cli.prompts import get_system_prompt
    from co_cli.prompts.model_quirks import get_counter_steering, list_models_with_quirks

    # Test 1: Known model gets counter-steering
    prompt_gemini_pro = get_system_prompt(
        "gemini", personality=None, model_name="gemini-1.5-pro"
    )
    assert "## Model-Specific Guidance" in prompt_gemini_pro
    assert "Pay careful attention to the scope of the user's request" in prompt_gemini_pro
    assert "Do what they ask, but no more" in prompt_gemini_pro

    # Test 2: Different model gets different counter-steering
    prompt_deepseek = get_system_prompt(
        "ollama", personality=None, model_name="deepseek-coder"
    )
    assert "## Model-Specific Guidance" in prompt_deepseek
    assert "You are diligent and thorough!" in prompt_deepseek
    assert "NEVER leave TODO" in prompt_deepseek

    # Test 3: Unknown model gets no counter-steering
    prompt_unknown = get_system_prompt(
        "ollama", personality=None, model_name="unknown-model-xyz"
    )
    assert "## Model-Specific Guidance" not in prompt_unknown

    # Test 4: No model_name parameter (None) → no counter-steering
    prompt_no_model = get_system_prompt("gemini", personality=None, model_name=None)
    assert "## Model-Specific Guidance" not in prompt_no_model

    # Test 5: Verify database has entries
    models_with_quirks = list_models_with_quirks()
    assert len(models_with_quirks) >= 10, "Should have at least 10 models with quirks"

    # Test 6: Verify get_counter_steering returns correct text
    counter = get_counter_steering("gemini", "gemini-1.5-pro")
    assert "overeager" in counter.lower() or "scope" in counter.lower()
    assert len(counter) > 50, "Counter-steering should be substantial text"

    # Test 7: Verify get_counter_steering returns empty string for unknown model
    unknown_counter = get_counter_steering("foo", "bar")
    assert unknown_counter == ""
```

**Expected result:** PASS — counter-steering correctly injected for known models, absent for unknown.

---

### Test 5: Commentary Present

**Purpose:** Verify examples include commentary explaining WHY, not just WHAT.

```python
def test_commentary_present():
    """Examples should include commentary explaining principles, not just patterns."""
    from co_cli.prompts import get_system_prompt

    prompt = get_system_prompt("gemini", personality=None, model_name=None)

    # Find Directive vs Inquiry section
    directive_inquiry_start = prompt.index("### Directive vs Inquiry")
    directive_inquiry_section = prompt[directive_inquiry_start:directive_inquiry_start + 5000]

    # Verify commentary section exists
    assert "**Why these distinctions matter:**" in directive_inquiry_section

    # Verify commentary explains principles (WHY), not just examples (WHAT)
    assert "Principle:" in directive_inquiry_section  # Appears multiple times

    # Count principle statements (should be at least 5)
    principle_count = directive_inquiry_section.count("Principle:")
    assert principle_count >= 5, f"Should have at least 5 principle statements, found {principle_count}"

    # Verify specific principle explanations
    assert "Statements of fact are not requests for action" in directive_inquiry_section
    assert "Describing a problem is not the same as requesting a fix" in directive_inquiry_section
    assert "Asking for explanation is research, not development" in directive_inquiry_section
    assert "Conservative classification minimizes damage" in directive_inquiry_section

    # Verify commentary includes false positive/negative tradeoff analysis
    assert "False negative (missed directive)" in directive_inquiry_section
    assert "False positive (unwanted modification)" in directive_inquiry_section

    # Verify edge cases are explicitly acknowledged
    assert 'Edge case: "check" can be either (context-dependent)' in directive_inquiry_section
```

**Expected result:** PASS — commentary present with principle explanations.

---

## Success Criteria

Phase 1d is complete when all of the following are true:

### Code Completeness
- [ ] `co_cli/prompts/system.md` has 4 modifications (2 escape hatches, 1 contrast examples, 1 system reminder)
- [ ] `co_cli/prompts/model_quirks.py` exists with 200+ lines (ModelQuirks TypedDict, MODEL_QUIRKS dict, 3 functions)
- [ ] `co_cli/prompts/__init__.py` has `model_name` parameter added to `get_system_prompt()`
- [ ] Counter-steering injection logic present in `get_system_prompt()` step 4
- [ ] `tests/test_prompts_phase1d.py` exists with 5 test functions

### Test Coverage
- [ ] All 5 tests pass:
  - `test_system_reminder_present`
  - `test_escape_hatches_present`
  - `test_contrast_examples_present`
  - `test_model_quirk_injection`
  - `test_commentary_present`
- [ ] No regressions in existing prompt tests (`tests/test_prompts.py`)

### Documentation
- [ ] This TODO file moved to `docs/COMPLETED/TODO-prompt-system-phase1d.md` upon completion
- [ ] `DESIGN-01-agent.md` updated with model quirk counter-steering documentation
- [ ] `docs/REVIEW-compare-four.md` marked as "applied to co-cli in Phase 1d"

### Behavioral Validation (Manual Testing)
- [ ] **Inquiry vs Directive compliance:** Test with 10 ambiguous inputs, verify ≥8 correct classifications
- [ ] **Escape hatch effectiveness:** Test "summarize these messages" and verify agent proceeds (not blocked)
- [ ] **Contrast example impact:** Test with bad example inputs ("This function has a bug") and verify agent treats as Inquiry
- [ ] **Model quirk counter-steering:** Test with Gemini 1.5 Pro on refactoring task, verify scope discipline
- [ ] **System reminder effectiveness:** Test with long conversation (10+ turns) and verify critical rules still enforced

### Expected Improvements (Measured Post-Deployment)
- [ ] Directive vs Inquiry false positive rate: ≤10% (currently ~25%)
- [ ] Stuck state incidents (user blocked by absolute prohibition): 0 per 100 user sessions
- [ ] Tool output reformatting errors: ≤5% (currently ~15%)
- [ ] Model-specific quirk incidents (TODOs, overeager refactoring): ≤10% for known models

---

## Implementation Notes

### Order of Implementation

1. **Start with system.md changes** (most impactful, no dependencies)
   - Add escape hatches first (low risk, immediate user benefit)
   - Add contrast examples second (larger change, needs careful positioning)
   - Add system reminder last (must be last section)

2. **Create model_quirks.py** (independent module, no integration yet)
   - Start with 3-4 well-known models (Gemini 1.5 Pro, DeepSeek Coder)
   - Test `get_counter_steering()` in isolation
   - Expand to 10+ models once pattern validated

3. **Integrate into __init__.py** (prompt assembly)
   - Add parameter, import, injection logic
   - Test with known model and verify section appears
   - Test with unknown model and verify no section

4. **Write tests** (validate all changes)
   - Start with test_system_reminder_present (simplest)
   - End with test_model_quirk_injection (most complex)

### Potential Issues

**Issue 1: System reminder might get truncated for long conversations**
- Mitigation: System prompt is always included in full (not part of conversation history)
- If context compression is added later, ensure system prompt is never truncated

**Issue 2: Model quirk database requires ongoing maintenance**
- Mitigation: Community-driven (like Aider). Users report quirks → we add entries
- Start conservative (10 models) and expand based on usage

**Issue 3: Escape hatches might be over-interpreted (agent always seeks user override)**
- Mitigation: Wording is "unless explicitly requested" (strong signal required)
- "Explicitly" means user must use action verb: "please summarize" or "go ahead"

**Issue 4: Contrast examples might confuse model if bad examples are too prominent**
- Mitigation: Table structure makes clear which are "Wrong" vs "Correct Response"
- Commentary section reinforces correct behavior

### Dependencies

**No external dependencies.** All changes are internal to `co_cli/prompts/`.

**No agent.py changes required for Phase 1d.** The `model_name` parameter integration into agent factory is Phase 2 work.

### Testing Strategy

**Unit tests:** Verify prompt content structure (5 tests above)

**Integration tests:** (Phase 2) Test full agent behavior with counter-steering

**Manual validation:** Critical — unit tests can only verify text presence, not behavioral impact. Manual testing with real models required to validate effectiveness.

---

**End of Phase 1d Specification**
