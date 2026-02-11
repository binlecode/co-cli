# DESIGN-15: Proactive Memory Detection

**Purpose:** Agent autonomously detects memory-worthy information (preferences, corrections, decisions) through linguistic pattern recognition and saves memories without explicit "remember X" commands.

**Approach:** Pure prompt engineering—the agent reasons about when to call `save_memory` based on signal detection patterns in the system prompt and tool docstrings. No hardcoded logic; all intelligence lives in prompt design.

---

## Architecture Overview

```
User Input
    ↓
    "I prefer async/await"  ← Preference signal
    ↓
┌────────────────────────────────────────┐
│ Agent (LLM Reasoning)                  │
│                                        │
│ 1. Reads system prompt                 │
│    - Sees "Memory & Knowledge Mgmt"    │
│    - Pattern table: "I prefer" =       │
│      Preference signal                 │
│                                        │
│ 2. Reads tool docstrings               │
│    - save_memory: "Use when user       │
│      states preference"                │
│                                        │
│ 3. Pattern matching                    │
│    - Input: "I prefer async/await"     │
│    - Match: "I prefer" → Preference    │
│    - Decision: Call save_memory        │
│                                        │
│ 4. Tool call generation                │
│    - content: "User prefers..."        │
│    - tags: ["preference", "python"]    │
└────────────────────────────────────────┘
    ↓
DeferredToolRequests (requires_approval=True)
    ↓
Approval Prompt: "Save memory 5? [y/n/a]"
    ↓
User approves → Tool executes → File written
    ↓
.co-cli/knowledge/memories/005-user-prefers-async-await.md
---
id: 5
created: 2026-02-10T...
tags: [preference, python]
source: detected         ← Auto-detected, not explicit
auto_category: preference ← Extracted from tags
---

User prefers async/await over callbacks in Python
```

**Key Insight:** The agent doesn't have code that says `if "I prefer" in input: call save_memory()`. Instead, the LLM **reasons** about signals by:
1. Pattern matching user language against examples in prompts
2. Understanding tool purpose from enhanced docstrings
3. Inferring appropriate action (call save_memory with correct tags)

This is **language understanding**, not rule-following.

---

## Core Logic

### 1. Signal Detection Patterns

**Location:** `co_cli/prompts/system.md` lines 411-480

**Design:** Markdown table showing input → signal → action mapping

```markdown
| User Says | Signal Type | Approach | Example Tags |
|-----------|-------------|----------|--------------|
| "I prefer async/await" | Preference | Call save_memory | ["preference", "python"] |
| "Actually, we use TypeScript" | Correction | Call save_memory | ["correction", "languages"] |
| "We decided to use Postgres" | Decision | Call save_memory | ["decision", "database"] |
| "Our API base is example.com" | Context | Call save_memory | ["context", "api"] |
| "We always lint before commit" | Pattern | Call save_memory | ["pattern", "workflow"] |
```

**Why a table?**
- **Visual pattern matching**: LLM sees structured input-output pairs
- **Parallel structure**: User language → Classification → Action → Expected tags
- **Dense format**: Less verbose than bullet lists
- **Easy scanning**: Model can quickly match user input to patterns

**Pattern categories:**

| Signal Type | Trigger Phrases | Semantic Meaning | Tag Convention |
|-------------|-----------------|------------------|----------------|
| **Preference** | "I prefer", "I like", "I favor", "I use" | User's tool/style choices | `["preference", domain]` |
| **Correction** | "Actually", "No wait", "That's wrong", "I meant" | User correcting information | `["correction", domain]` |
| **Decision** | "We decided", "We chose", "We implemented" | Team/project decisions | `["decision", domain]` |
| **Context** | Factual statements about team/project/environment | Background information | `["context", domain]` |
| **Pattern** | "We always", "When we [do X]", "Never [do Y]" | Recurring workflows/constraints | `["pattern", domain]` |

**Linguistic signal lists:**

Explicit phrase collections guide pattern matching:

- **Preferences:** "I prefer", "I like", "I favor", "I use"
- **Corrections:** "Actually", "No wait", "That's wrong", "I meant"
- **Decisions:** "We decided", "We chose", "We implemented"
- **Patterns:** "We always", "When we [do X]", "Never [do Y]"

The LLM uses these as **fuzzy matching templates**, not exact string matches. Similar phrasings (e.g., "I really like", "Actually that's not right") trigger the same signal recognition.

### 2. Negative Guidance (Anti-Patterns)

**Equally important:** What NOT to save

```markdown
Don't save:
- Speculation: "Maybe we should...", "I think...", "Could we..."
- Questions: "Should we use X?", "What if we tried Y?"
- Transient conversation details (only relevant to current session)
- Information already in context files
- Uncertain statements lacking confidence
```

**Why this matters:**
- **Prevents false positives**: Clarifies boundary between memory-worthy and transient
- **Contrast learning**: Showing negative cases helps LLM distinguish patterns
- **Speculation filter**: Critical—"Maybe we should use Redis" should NOT be saved as a decision

**Design decision:** Negative guidance is as important as positive patterns. Without it, models tend to over-trigger (especially older models like GPT-4).

### 3. Tool Docstring Enhancement

**Location:** `co_cli/tools/memory.py` lines 132-170, 232-265

**Pattern:** Extended docstrings with structured "when to use" sections

**save_memory docstring structure:**

```python
"""Save a memory for cross-session persistence.

Use this when the user shares important, actionable information:
- Preferences: "I prefer X" → tags=["preference", domain]
- Corrections: "Actually, we use Y" → tags=["correction", domain]
- Decisions: "We chose Z" → tags=["decision", domain]
- Context: "Our team has N people" → tags=["context"]
- Patterns: "We always do X" → tags=["pattern", domain]

Do NOT save:
- Speculation or hypotheticals ("Maybe", "I think")
- Transient conversation details
- Information already in context files
- Questions ("Should we?")

[... parameter docs ...]

Example:
    User: "I prefer pytest over unittest"
    Call: save_memory(ctx, "User prefers pytest over unittest",
                      tags=["preference", "testing", "python"])
"""
```

**Why this works:**
- **Pydantic-AI extracts docstrings** as tool descriptions → LLM sees this when deciding which tool to call
- **"Use this when" section** guides tool selection (when to choose save_memory vs other tools)
- **Tag conventions** show expected structure → LLM learns to use correct first tag (signal type)
- **Concrete example** grounds abstract guidance

**recall_memory docstring structure:**

```python
"""Search memories using keyword search.

Use this proactively when:
- User mentions a preference/decision from past conversations
- Starting work where prior context would be helpful
- User explicitly asks about past information

[... parameter docs ...]

Example:
    User: "Write tests for the API"
    Call: recall_memory(ctx, "testing python", max_results=3)
    → Finds: "User prefers pytest over unittest"
    → Use this to write pytest tests
"""
```

**Key addition:** "Use this **proactively**" tells agent to recall memories without being asked, when context would benefit.

### 4. Process Flow (Not Rigid Steps)

**Location:** System prompt lines 465-470

```markdown
Typical flow:
1. Recognize signal pattern in user message
2. Extract concise, standalone fact (< 200 chars preferred)
3. Call save_memory(content="...", tags=["signal_type", "domain", ...])
4. User approves via prompt: "Save memory N? [y/n/a]"
5. Continue conversation
```

**Design choices:**
- **"Typical flow" not "Process"**: Suggests pattern without forcing
- **"Recognize" not "Detect"**: Softer language (pattern-matching, not rule-checking)
- **Concrete example**: Shows actual function call syntax
- **Approval step included**: Sets expectation that user will see prompt

**Why process-based?**
- Shows agent HOW to use tools (steps to follow)
- Agent infers tool calls from process steps (e.g., step 3 → "I need to call save_memory")
- More flexible than rigid IF-THEN rules

### 5. Tone Calibration (Production-Grade)

**Research finding:** Modern models (Opus 4.5, Gemini 2.0 Flash, GLM 4.7) are **tone-sensitive**. Aggressive language causes overtriggering.

**Intensity markers removed:**

| Before (Overtriggers) | After (Balanced) |
|-----------------------|------------------|
| "**proactively**" (bold) | "When you recognize" |
| "Save **immediately**" | "Call save_memory" |
| "CRITICAL: You MUST..." | (removed entirely) |
| "ALWAYS call..." | "Call when you recognize..." |
| "❌ Don't save" | "Don't save" |

**Why this matters:**
- **Balanced tone** → model reasons about when to use tools
- **Aggressive tone** → model uses tools reflexively (lower quality)
- **Reference:** Claude Code's `prompt-snippets.md` (Opus 4.5 migration guide) documents this phenomenon

**Tone principles:**
- Declarative over imperative ("here are patterns" not "you must do X")
- Pattern-focused over command-focused
- Examples over rules
- Suggestion over enforcement

### 6. Example Interactions

**Location:** System prompt lines 472-479

```markdown
Example interaction:
```
User: "I prefer pytest over unittest for testing"
You: [Detect preference signal]
     [Call save_memory("User prefers pytest over unittest", tags=["preference", "testing", "python"])]
     [After approval] ✓ Saved memory 5. I'll use pytest for your tests going forward.
```
```

**Purpose:**
- Shows **complete flow**: User input → Signal detection → Tool call → Response
- Demonstrates **tag structure**: Signal type first, then domain tags
- Models **natural response**: Acknowledge save, state what it means for future behavior

**Why examples work:**
- Grounds abstract patterns in concrete scenarios
- Shows multi-step reasoning (detect → call → acknowledge)
- Demonstrates expected output format

### 7. Metadata Auto-Detection

**Location:** `co_cli/tools/memory.py` lines 68-106

**Helper functions:**

```python
def _detect_source(tags: list[str] | None) -> str:
    """Detect if memory was auto-saved (detected) or explicitly requested."""
    if not tags:
        return "user-told"

    signal_tags = {"preference", "correction", "decision", "context", "pattern"}
    return "detected" if any(t in signal_tags for t in tags) else "user-told"


def _detect_category(tags: list[str] | None) -> str | None:
    """Extract primary category from tags."""
    if not tags:
        return None

    categories = ["preference", "correction", "decision", "context", "pattern"]
    for category in categories:
        if category in tags:
            return category

    return None
```

**Logic:**
1. **Source detection**: If tags include signal types → `source: detected` (agent recognized pattern)
2. **Category extraction**: First matching signal tag becomes `auto_category`

**Frontmatter output:**

```yaml
---
id: 5
created: 2026-02-10T10:30:00+00:00
tags: [preference, testing, python]
source: detected          # ← Indicates proactive detection
auto_category: preference # ← Primary signal type
---
```

**Why metadata matters:**
- **Provenance tracking**: Did agent detect this, or user explicitly save?
- **Filtering**: User can query "show me all corrections" or "list my preferences"
- **Audit trail**: User can see what the agent auto-saved
- **Future features**: Can build preference dashboard, auto-delete low-confidence memories, etc.

### 8. Approval Flow Integration

**No changes to approval mechanism** (uses existing DeferredToolRequests pattern)

**Flow:**

```
Agent decides: "I should save this preference"
    ↓
Generates: ToolRequest(tool_name="save_memory", args={"content": "...", "tags": [...]})
    ↓
save_memory has requires_approval=True
    ↓
pydantic-ai returns: DeferredToolRequests (not executing yet)
    ↓
Chat loop (co_cli/main.py): await run_turn(...)
    ↓
_orchestrate.py: handle_deferred_approval() detects DeferredToolRequests
    ↓
_approval.py: prompt_approval() → User sees: "Save memory 5? [y/n/a]"
    ↓
User: y → Tool executes
User: n → Tool skipped, agent continues
User: a → Auto-approve mode enabled (YOLO)
```

**Design decision:** Keep existing approval gate. Proactive detection is **opt-in at execution time**, not automatic persistence.

**Why preserve approval:**
- **Safety**: User can reject incorrect detections
- **Transparency**: User sees what agent considers memory-worthy
- **Learning signal**: Rejections teach user what agent detected (calibration feedback)

---

## Design Decisions & Trade-offs

### 1. Prompt Engineering vs Code Logic

**Decision:** 100% prompt-based, zero hardcoded rules

**Rationale:**
- Modern LLMs (Opus 4.5, Gemini 2.0 Flash, GLM 4.7) excel at **pattern recognition**
- Hardcoded rules (if "I prefer" in text) are brittle (miss variations, synonyms)
- Prompt patterns are **flexible** (LLM handles "I really prefer", "I'd rather use", etc.)
- Easier to iterate (change prompt, test immediately—no code deploy)

**Trade-off:**
- ✅ **Pro:** Works across model updates, handles edge cases naturally
- ⚠️ **Con:** Behavior varies slightly by model (Gemini vs Ollama may detect differently)

**Mitigation:** Test with multiple models (GLM 4.7 Flash, Gemini 2.0 Flash confirmed working)

### 2. Pattern Table vs Bullet Lists

**Decision:** Use markdown table for signal patterns

**Rationale:**
- **Visual structure**: Input-output pairs side-by-side
- **Parallel format**: Every row has same structure (user says / signal / action / tags)
- **Easier scanning**: LLM can quickly match user input to table row

**Trade-off:**
- ✅ **Pro:** Dense, structured, easy to scan
- ⚠️ **Con:** Less narrative-friendly (harder to read in prose)

**Mitigation:** Add narrative sections (linguistic signals, process flow) alongside table

### 3. Negative Guidance Required

**Decision:** Include "Don't save" section with equal prominence

**Rationale:**
- Without negative guidance, models over-trigger (especially on speculation, questions)
- Contrast learning helps LLM distinguish boundaries
- Research shows negative examples are critical for precision

**Example:**
- ❌ Without negative guidance: "Maybe we should use Redis" → agent saves as decision
- ✅ With negative guidance: Agent recognizes "Maybe" = speculation → doesn't save

**Trade-off:**
- ✅ **Pro:** Prevents false positives, improves precision
- ⚠️ **Con:** Adds prompt length

**Mitigation:** Keep negative guidance concise (5 bullet points)

### 4. Tag Convention: Signal Type First

**Decision:** First tag is always signal type (`["preference", "python"]`)

**Rationale:**
- Enables `_detect_category()` to extract primary category
- Standard format for agent to follow
- Enables filtering: "show me all preferences" or "list corrections"

**Trade-off:**
- ✅ **Pro:** Consistent structure, enables metadata extraction
- ⚠️ **Con:** Agent must remember convention

**Mitigation:** Show examples in every prompt section and docstring

### 5. Metadata Auto-Detection vs Explicit Fields

**Decision:** Detect `source` and `auto_category` from tags (not explicit parameters)

**Rationale:**
- Simpler agent interface (no extra parameters)
- Tags already encode signal type
- DRY principle (don't ask agent to specify both tags and category)

**Trade-off:**
- ✅ **Pro:** Simpler tool signature, no redundancy
- ⚠️ **Con:** Requires consistent tag convention

**Mitigation:** Document tag convention extensively in prompts

### 6. Tone Calibration: Softening vs Clarity

**Decision:** Remove intensity markers (bold, caps, "immediately") for production-grade tone

**Rationale:**
- Research shows modern models overtrigger with aggressive tone
- Balanced tone → better reasoning
- "Call save_memory" is clear without being forceful

**Trade-off:**
- ✅ **Pro:** Better agent reasoning, fewer false positives
- ⚠️ **Con:** Less forceful might → occasional misses?

**Testing:** GLM 4.7 Flash and Gemini 2.0 Flash both detect correctly with balanced tone

### 7. Process Flow: "Typical" not "Required"

**Decision:** Frame as "Typical flow" not "Process" or "Required steps"

**Rationale:**
- Allows agent flexibility
- "Typical" suggests pattern without forcing
- Agent can adapt to context

**Trade-off:**
- ✅ **Pro:** More flexible, respects agent reasoning
- ⚠️ **Con:** Less deterministic (might deviate from pattern)

**Mitigation:** Process flow is clear enough that deviation is rare

---

## Processing Flows

### Flow 1: Preference Detection → Save

```
User: "I prefer async/await over callbacks"
    ↓
Agent reasoning:
  1. Read system prompt: Pattern table shows "I prefer" → Preference signal
  2. Read save_memory docstring: "Use when user shares preferences"
  3. Match: "I prefer" detected in user input
  4. Decision: Call save_memory
  5. Extract content: "User prefers async/await over callbacks in Python code"
  6. Select tags: ["preference", "python", "async"]
    ↓
Tool call generated:
  save_memory(
    content="User prefers async/await over callbacks in Python code",
    tags=["preference", "python", "async"]
  )
    ↓
DeferredToolRequests (requires_approval=True)
    ↓
User sees: "Save memory 5? [y/n/a]"
    ↓
User: y
    ↓
save_memory executes:
  1. _next_memory_id() → 5
  2. _slugify("User prefers...") → "user-prefers-async-await-over-callbacks-in-python"
  3. frontmatter = {
       id: 5,
       created: ISO timestamp,
       tags: ["preference", "python", "async"],
       source: _detect_source(tags) → "detected",
       auto_category: _detect_category(tags) → "preference"
     }
  4. Write: .co-cli/knowledge/memories/005-user-prefers-async-await....md
    ↓
Tool returns: {"display": "✓ Saved memory 5: ...", "memory_id": 5, ...}
    ↓
Agent responds: "✓ Saved memory 5. I'll use async/await patterns in future code."
```

### Flow 2: Correction Detection → Save

```
User: "Actually, we use TypeScript not JavaScript"
    ↓
Agent reasoning:
  1. Read system prompt: "Actually" → Correction signal
  2. Match: Correction pattern detected
  3. Decision: Call save_memory with correction tag
    ↓
Tool call:
  save_memory(
    content="The user uses TypeScript, not JavaScript",
    tags=["correction", "languages", "typescript"]
  )
    ↓
[Same approval + save flow as above]
    ↓
Frontmatter includes:
  source: detected
  auto_category: correction
```

### Flow 3: Speculation → No Save

```
User: "Maybe we should try using Redis for caching"
    ↓
Agent reasoning:
  1. Read system prompt: Negative guidance shows "Maybe we should" = Speculation
  2. Check: "Don't save: Speculation"
  3. Decision: Do NOT call save_memory
    ↓
Agent responds: "Redis is a good option for caching. Would you like me to show you
                how to set it up, or are you still exploring options?"
    ↓
[No tool call generated]
```

### Flow 4: Proactive Recall

```
User: "Write tests for the new API endpoint"
    ↓
Agent reasoning:
  1. Task: Write tests
  2. Context check: User might have testing preferences
  3. Read recall_memory docstring: "Use proactively when starting work"
  4. Decision: Call recall_memory
    ↓
Tool call:
  recall_memory(query="testing python", max_results=3)
    ↓
Search results:
  - Memory 2: "User prefers pytest over unittest"
  - Memory 8: "Use pytest fixtures for test setup"
    ↓
Agent uses memories:
  "I'll write pytest tests, since you prefer pytest over unittest. Here's the test file..."
    ↓
[Writes test file using pytest framework]
```

### Flow 5: User Deletes Memory

```
User: /forget 5
    ↓
_cmd_forget handler:
  1. Parse: memory_id = 5
  2. Find: .co-cli/knowledge/memories/005-*.md
  3. Delete: matching_files[0].unlink()
    ↓
Console: "✓ Deleted memory 5: 005-user-prefers-async-await-over-callbacks-in-python.md"
    ↓
[File removed from disk]
```

---

## Error Handling

### 1. Invalid Signal Detection

**Scenario:** Agent incorrectly detects signal (e.g., question as preference)

**Handling:**
- Approval gate catches incorrect detections
- User rejects: `n` → Memory not saved
- Agent continues without saving

**No code-level error** (agent reasoning issue, not exception)

### 2. Missing Metadata Fields

**Scenario:** Old memory files without `auto_category` field

**Handling:**
- `_detect_category()` returns `None` if no tags
- `list_memories()` checks `m.get("auto_category")` → defaults to empty string
- Display shows: `**001** (2026-02-10)  : Summary` (no brackets)

**Backwards compatible**

### 3. Invalid Memory ID in /forget

**Scenario:** User types `/forget abc` or `/forget 999`

**Handling:**

```python
try:
    memory_id = int(args.strip())
except ValueError:
    console.print("[bold red]Invalid memory ID:[/bold red] {args}")
    return None

matching_files = list(memory_dir.glob(f"{memory_id:03d}-*.md"))
if not matching_files:
    console.print(f"[bold red]Memory {memory_id} not found[/bold red]")
    return None
```

**User-friendly error messages**

### 4. Malformed Frontmatter

**Scenario:** User manually edits memory file, breaks YAML

**Handling:**
- `validate_memory_frontmatter()` raises `ValueError`
- Caught in `list_memories()` and `_search_memories()`:
  ```python
  try:
      validate_memory_frontmatter(frontmatter)
  except ValueError:
      continue  # Skip invalid memories
  ```
- Invalid memories skipped, not crash

### 5. Over-Triggering

**Scenario:** Agent saves too aggressively

**Mitigation:**
- Negative guidance in prompt ("Don't save speculation")
- Balanced tone (no intensity markers)
- Approval gate (user can reject)

**If occurs:** Adjust prompt (add more negative examples)

---

## Security & Privacy

### 1. Approval Gate Preserved

**Security:** All `save_memory` calls require user approval

- Prevents agent from silently persisting sensitive data
- User sees content before save
- User can reject

**Implementation:** `requires_approval=True` on tool registration

### 2. Local Storage Only

**Privacy:** All memories stored in `.co-cli/knowledge/memories/` (local filesystem)

- No cloud sync
- No external API calls
- User controls storage location
- Git-friendly (can commit or gitignore)

### 3. No Secrets in Memories

**Guidance in prompt:** "Don't save" section explicitly warns against storing credentials

**If agent tries:** Approval prompt shows content → user rejects

**Best practice:** Store metadata about WHERE secrets are, not secrets themselves

Example:
- ✅ "Stripe API key is in .env as STRIPE_SECRET_KEY"
- ❌ "Stripe API key is sk_live_abc123..."

### 4. Tag Sanitization

**Current:** No sanitization (tags accepted as-is from agent)

**Risk:** Agent could generate malicious tags (XSS if tags displayed in web UI)

**Mitigation:** Co is CLI-only, tags displayed in terminal (no XSS risk)

**Future:** If web UI added, sanitize tags before display

---

## Testing Strategy

### 1. Manual Signal Detection Tests

**Validated patterns:**
- ✅ Preference: "I prefer async/await" → calls save_memory with ["preference", "python"]
- ✅ Correction: "Actually, we use TypeScript" → calls save_memory with ["correction", "languages"]
- ⏳ Decision: "We decided to use Postgres" (pending test)
- ⏳ Speculation (negative): "Maybe we should try React" (should NOT save)
- ⏳ Question (negative): "Should we use Redis?" (should NOT save)

**Test environment:**
- Model: GLM 4.7 Flash (via Ollama)
- Location: `uv run co chat`

### 2. Metadata Validation Tests

**Test cases:**
- Tags include signal type → `source: detected`, `auto_category: preference`
- No tags → `source: user-told`, `auto_category: None`
- Old memory without auto_category → displays without brackets

**Test location:** `tests/test_memory_tools.py` (existing file, needs expansion)

### 3. /forget Command Tests

**Test cases:**
- Valid ID → file deleted
- Invalid ID (non-numeric) → error message
- Missing ID (999) → error message
- Empty memories directory → graceful message

**Test location:** Integration tests (manual or new test file)

### 4. Integration Tests

**Test cases:**
- Save → list → verify category shows
- Save → recall → verify content found
- Save → forget → recall → verify gone

**Test location:** `tests/test_proactive_memory.py` (to be created)

### 5. Model Compatibility Tests

**Test across models:**
- ✅ GLM 4.7 Flash (Ollama) - Working
- ⏳ Gemini 2.0 Flash - Pending test
- ⏳ Claude Opus 4.5 - Pending test (if available)

**Goal:** Verify prompt patterns work across different LLMs

---

## Configuration

No new configuration settings added. Uses existing:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm_provider` | `LLM_PROVIDER` | `"ollama"` | Model provider (affects reasoning quality) |
| `ollama_model` | `OLLAMA_MODEL` | `"llm"` | Ollama model name |
| `gemini_model` | `GEMINI_MODEL` | `"gemini-2.0-flash-exp"` | Gemini model name |

**Note:** Signal detection quality depends on model capability. Recommended:
- ✅ GLM 4.7 Flash or newer
- ✅ Gemini 2.0 Flash or newer
- ✅ Claude Opus 4.5 or newer

---

## Files Modified

| File | Purpose | Changes |
|------|---------|---------|
| **Prompt Engineering** |||
| `co_cli/prompts/system.md` | System prompt | Added "Memory & Knowledge Management" section (+70 lines) |
| `co_cli/tools/memory.py` | Tool docstrings | Enhanced save_memory, recall_memory docstrings (+30 lines) |
| **Supporting Code** |||
| `co_cli/tools/memory.py` | Metadata detection | Added `_detect_source()`, `_detect_category()` helpers (+40 lines) |
| `co_cli/tools/memory.py` | save_memory function | Updated frontmatter to include source, auto_category (+5 lines) |
| `co_cli/tools/memory.py` | list_memories function | Updated display format to show category (+3 lines) |
| `co_cli/_frontmatter.py` | Validation | Added auto_category to schema (+3 lines) |
| **User Affordances** |||
| `co_cli/_commands.py` | Slash commands | Added `/forget` command (+40 lines) |
| **Documentation** |||
| `docs/TODO-proactive-memory-implementation.md` | Tracking | Implementation progress tracking (new file) |
| `docs/DESIGN-15-proactive-memory-detection.md` | Design | This document (new file) |

**Total:**
- **Prompt engineering**: ~100 lines (core intelligence)
- **Supporting code**: ~90 lines (infrastructure)

---

## Future Enhancements

### Phase 2: Advanced Signal Detection

**Implicit preference detection:**
- When user corrects agent repeatedly: "No, use X" → Infer preference for X
- When user chooses one option over another consistently → Detect preference

**Example:**
```
Agent: "Should I use unittest or pytest?"
User: "pytest"
[Later]
Agent: "I'll write tests with unittest"
User: "Use pytest"
→ Agent detects: Strong preference for pytest → Saves automatically
```

### Phase 3: Confidence Scoring

**Add confidence field to frontmatter:**
```yaml
confidence: high | medium | low
```

**Criteria:**
- High: Explicit preference ("I prefer X")
- Medium: Implied preference (repeated corrections)
- Low: Inferred from context

**Use case:** Filter low-confidence memories, prompt user to confirm

### Phase 4: Memory Summarization

**Problem:** 200+ memories → grep search slows down

**Solution:** Periodic summarization
- Group related memories
- Extract key preferences
- Store in context.md as "Learned Preferences" section

**Example:**
```markdown
# Learned Preferences (from 50 memories)

## Python Development
- Prefers async/await over callbacks
- Uses pytest exclusively for testing
- Prefers type hints on all functions
```

### Phase 5: Conflict Detection

**Problem:** User states conflicting preferences

**Example:**
- Memory 5: "User prefers JavaScript"
- New input: "Actually, I prefer TypeScript"

**Solution:**
- Detect conflict (both about language preference)
- Prompt: "Memory 5 says JavaScript, but you just said TypeScript. Update memory 5?"
- If yes: Replace memory 5 content, add updated_at field

### Phase 6: Memory Dashboard

**New command:** `/memories` (interactive TUI)

**Features:**
- Browse by category (filter by preference/correction/decision)
- Search by keyword
- Bulk delete
- Edit memory content inline
- View memory timeline

---

## References

### Research Documents

- `docs/REVIEW-rundown-phase1c-knowledge-system.md` - Knowledge system walkthrough
- `docs/VERIFICATION-phase1c-demo-results.md` - Phase 1c verification (merged into demo doc)
- `scripts/demo_knowledge_roundtrip.md` - Complete demo & verification results

### Related Design Docs

- `docs/DESIGN-14-knowledge-system.md` - Storage/retrieval infrastructure
- `docs/DESIGN-01-agent.md` - Agent initialization, tool registration
- `docs/DESIGN-02-chat-loop.md` - Approval flow, slash command dispatch

### Peer System Research

**Gemini-CLI** (`~/workspace_genai/gemini-cli/`):
- `packages/core/src/utils/memoryDiscovery.ts` - Tiered memory loading
- `packages/core/src/tools/memoryTool.ts` - Signal detection patterns

**Claude Code** (`~/workspace_genai/claude-code/`):
- `plugins/plugin-dev/skills/agent-development/references/system-prompt-design.md` - Process-based prompting
- `plugins/claude-opus-4-5-migration/skills/claude-opus-4-5-migration/references/prompt-snippets.md` - Tone intensity guidance

---

## Appendix: Prompt Text

### System Prompt Section (Full Text)

```markdown
## Memory & Knowledge Management

Co has memory tools to persist information across sessions. When you recognize memory-worthy signals in the conversation, call save_memory without waiting for explicit "remember X" commands.

### save_memory — Persist facts, preferences, and decisions

**Recognition patterns:**

| User Says | Signal Type | Approach | Example Tags |
|-----------|-------------|----------|--------------|
| "I prefer async/await" | Preference | Call save_memory | `["preference", "python"]` |
| "Actually, we use TypeScript" | Correction | Call save_memory | `["correction", "languages"]` |
| "We decided to use Postgres" | Decision | Call save_memory | `["decision", "database"]` |
| "Our API base is example.com" | Context | Call save_memory | `["context", "api"]` |
| "We always lint before commit" | Pattern | Call save_memory | `["pattern", "workflow"]` |

**Linguistic signals:**
- **Preferences:** "I prefer", "I like", "I favor", "I use"
- **Corrections:** "Actually", "No wait", "That's wrong", "I meant"
- **Decisions:** "We decided", "We chose", "We implemented"
- **Context:** Factual statements about team, project, environment
- **Patterns:** "We always", "When we [do X]", "Never [do Y]"

**Don't save:**
- Speculation: "Maybe we should...", "I think...", "Could we..."
- Questions: "Should we use X?", "What if we tried Y?"
- Transient conversation details (only relevant to current session)
- Information already in context files (`.co-cli/knowledge/context.md`)
- Uncertain statements lacking confidence

**Typical flow:**
1. Recognize signal pattern in user message
2. Extract concise, standalone fact (< 200 chars preferred)
3. Call `save_memory(content="...", tags=["signal_type", "domain", ...])`
4. User approves via prompt: "Save memory N? [y/n/a]"
5. Continue conversation

**Example interaction:**
```
User: "I prefer pytest over unittest for testing"
You: [Detect preference signal]
     [Call save_memory("User prefers pytest over unittest", tags=["preference", "testing", "python"])]
     [After approval] ✓ Saved memory 5. I'll use pytest for your tests going forward.
```

### recall_memory — Search saved memories

**Use when:**
- User asks about past preferences: "What do I usually prefer?"
- Starting work where user preferences might apply (writing code, suggesting tools)
- User references something you should know: "Remember what I said about X?"
- Context would benefit from retrieving past decisions

**Typical flow:**
1. Identify relevant query terms from conversation context
2. Call `recall_memory(query="relevant keywords", max_results=5)`
3. Use retrieved memories to inform your response

**Example:**
```
User: "Write tests for the new API endpoint"
You: [Check for testing preferences]
     [Call recall_memory("testing python", max_results=3)]
     [Finds: "User prefers pytest over unittest"]
     I'll write pytest tests, since you prefer pytest. Here's the test file...
```

### list_memories — Show all saved memories

**Use when:**
- User explicitly asks: "Show me what you remember", "List my memories"
- User wants to review or audit saved information
- User asks about memory management: "What have you saved?"
```

---

**Document Status:** Complete, synchronized with implementation
**Last Updated:** 2026-02-10
**Implementation Status:** ✅ Phases 1-3 complete, Phase 4 (testing) in progress
