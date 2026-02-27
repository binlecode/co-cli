# FIX: Prompt Sequencing and Personality Adherence

Date: 2026-02-26 (updated 2026-02-27)
Trace baselines:
- Run 1: `evals/personality_behavior-trace-20260226-152530.md` — 3/4 PASS (jeff-uncertainty FAIL)
- Run 2: `evals/personality_behavior-trace-20260226-153627.md` — 3/4 PASS (jeff-codebase-structure FAIL)
- Run 3: `evals/personality_behavior-trace-20260226-153939.md` — 2/2 PASS (jeff-only run)

Model: `qwen3:30b-a3b-thinking-2507-q8_0-agentic`

This document is the single source of truth for observed bugs, root cause analysis, and
implementation-ready fixes. It supersedes `REVIEW-eval-agentic-flows.md` (deleted after merge).

## Design constraint

**No heuristic text logic in agentic reasoning.** Keyword lists, regex patterns, and
substring scans are banned as decision gates in any agentic flow. Every classification
or routing decision that requires understanding text meaning is delegated to the LLM.
Cheap guards (e.g., "is this message non-empty?") are acceptable; phrase-based signal
detection is not.

Issue H's original fix (phrase expansion + regex precheck) was wrong on this basis and
has been revised. The correct fix is to remove the precheck gate and let the LLM
mini-agent run on every turn.

---

## Issue Summary

| ID | Severity | Type | Short description |
|----|----------|------|-------------------|
| A | P0 | Prompt text | Identity conflict: "You are Co" vs "You are Jeff/Finch" |
| B | P0 | Prompt text | Adoption mandate references "rules below" — rules are above |
| C | P0 | Code (history) | `recall_memory` ownership split between prompt text and code |
| D | P0 | Code (orchestrate) | Tool preamble never emitted — user sees silence before tool calls |
| E | P1 | Prompt text | Jeff soul: authority openers not banned, discovery mode underspecified |
| F | P1 | Prompt text | Emoji policy: decorative emoji exempted by model, "1 total" ambiguous |
| G | P1 | Prompt text | Short follow-up format rule not concrete enough |
| H | P1 | Code (signal) | Heuristic precheck gates LLM — misses decision/migration/habit signals |
| I | P2 | Prompt text | Source-conflict handling missing (tool vs. tool) |
| J | P2 | Eval data + design | Eval `required_any` lists miss semantic variants; phrase-only scoring is brittle |

---

## Issue A — Identity conflict: "You are Co" vs "You are Jeff/Finch"

### Observed violations

Confirmed in run-2 trace system prompt `[1]`, line 1 vs later soul section:
```
> You are Co, a personal companion for knowledge work, running in the user's terminal.
...
> ## Soul
> You are Jeff — an eager learner discovering the world through this terminal.
```
The model resolves the conflict by preferring the Soul section (Jeff behavior is visible in
outputs), but the dual identity is injected on every request. A model reasoning about
"who am I?" on a later turn could fall back to "Co" and drop persona.

### RCA

`co_cli/prompts/instructions.md` line 1:
```
You are Co, a personal companion for knowledge work, running in the user's terminal.
```

`soul/jeff.md` line 1 (after rename):
```
You are Jeff — an eager learner discovering the world through this terminal.
```

`assemble_prompt()` includes `instructions.md` as the base layer. `add_personality()` in
`agent.py` injects the soul + behaviors *after* the static prompt. The model sees two
identity declarations in sequence. The instructions preamble is personality-agnostic in
purpose (sets the operational framing) but the "You are Co" claim conflicts with the
Soul's specific identity assignment.

### Fix

**File:** `co_cli/prompts/instructions.md`

```diff
- You are Co, a personal companion for knowledge work, running in the user's terminal.
+ You are a personal companion for knowledge work, running in the user's terminal.
```

The Soul section provides the authoritative name and voice fingerprint. No other files change.
The preamble's operational framing ("running in the user's terminal") is preserved.

---

## Issue B — Adoption mandate references "rules below" — rules are above

### Observed violations

Not directly observable in trace outputs (no visible behavioral regression linked to this
specific wording). Confirmed as a structural inconsistency by reading the assembled prompt:
in both trace runs the static prompt output (containing `# Identity`, `# Safety`, etc.)
appears in input messages before the `## Soul` block containing the mandate. "Rules below"
is spatially incorrect.

### RCA

`co_cli/prompts/personalities/_composer.py`:
```python
_ADOPTION_MANDATE = (
    "Adopt this persona fully — it overrides your default "
    "personality and communication patterns.\n"
    "Your personality shapes how you follow the rules below. "
    "It never overrides safety or factual accuracy."
)
```

In the assembled system prompt, pydantic-ai concatenates layers in registration order:
1. Static `system_prompt` arg to Agent (`assemble_prompt()` output) — contains rules
2. `add_personality` — contains soul + behaviors + adoption mandate
3. `add_current_date`, `add_shell_guidance`, etc.

The rules (identity, safety, reasoning, tools, workflow) are injected by `assemble_prompt()`
which runs first. The Soul/mandate is injected after. So "rules below" is spatially wrong —
the rules are above the personality block. This could cause the model to look past the soul
for non-existent rules below it, weakening the mandate's authority.

Additionally, the `@agent.system_prompt` decorator registration order in `agent.py`
(lines 161–196) places personality before operational context:
```
add_personality → add_current_date → add_shell_guidance → add_project_instructions
→ add_personality_memories
```
Hard operational constraints (date, shell) should precede style guidance (personality).

### Fix A — mandate text

**File:** `co_cli/prompts/personalities/_composer.py`

```diff
- "Your personality shapes how you follow the rules below. "
+ "Your personality shapes how you follow the rules above. "
```

### Fix B — dynamic layer reordering

**File:** `co_cli/agent.py` (lines 161–196)

Reorder `@agent.system_prompt` decorator registrations from:
```
add_personality → add_current_date → add_shell_guidance → add_project_instructions
```
To:
```
add_current_date → add_shell_guidance → add_personality → add_project_instructions
```

Full reordered block:
```python
@agent.system_prompt
def add_current_date(ctx: RunContext[CoDeps]) -> str:
    """Inject the current date so the model can reason about time."""
    return f"Today is {date.today().isoformat()}."

@agent.system_prompt
def add_shell_guidance(ctx: RunContext[CoDeps]) -> str:
    """Inject shell tool guidance when shell is available."""
    return (
        "Shell runs as subprocess with approval. "
        "Read-only commands matching the safe-command allowlist are auto-approved."
    )

@agent.system_prompt
def add_personality(ctx: RunContext[CoDeps]) -> str:
    """Inject personality block (soul + behaviors + mandate) per turn."""
    if not ctx.deps.personality:
        return ""
    from co_cli.prompts.personalities._composer import compose_personality
    return compose_personality(ctx.deps.personality, ctx.deps.reasoning_depth)

@agent.system_prompt
def add_project_instructions(ctx: RunContext[CoDeps]) -> str:
    """Inject project-level instructions from .co-cli/instructions.md."""
    instructions_path = Path.cwd() / ".co-cli" / "instructions.md"
    if instructions_path.is_file():
        return instructions_path.read_text(encoding="utf-8").strip()
    return ""

@agent.system_prompt
def add_personality_memories(ctx: RunContext[CoDeps]) -> str:
    """Inject personality-context memories for relationship continuity."""
    if not ctx.deps.personality:
        return ""
    from co_cli.tools.personality import _load_personality_memories
    return _load_personality_memories()
```

Rationale: hard operational constraints (date, shell) before style (personality) before
volatile context (project instructions, memories). Personality mandate now correctly
references "rules above."

---

## Issue C — `recall_memory` ownership split between prompt text and code

### Observed violations

Confirmed in both trace runs: the system prompt `[1]` always contains the instruction
"At the start of a conversation, recall memories relevant to the user's topic." visible
in input messages for turn 2 and turn 3 of jeff-codebase-structure. The model correctly
ignores it on those turns (history shows prior exchanges), but this is implicit fragile
reasoning — no explicit guidance that the instruction applies only on turn 1.

### RCA

**Prompt text** (`co_cli/prompts/rules/01_identity.md` line 10):
```
At the start of a conversation, recall memories relevant to the user's topic.
```

**Code** (`co_cli/_history.py` `inject_opening_context`):
- Runs before every model request as a registered history processor
- Checks `has_prior_response` — if no prior ModelResponse, treats as first request
- Calls `recall_memory` deterministically (no model decision required)
- State tracked in `OpeningContextState(last_recall_topic, recall_count, model_request_count)`
- Debounce: `if state.recall_count > 0 and state.model_request_count % 5 != 0: return`

**Problem 1 — dual ownership.** The prompt tells the model to recall memories manually.
The history processor also recalls them. If both fire, the model sees memory twice. If the
processor fires but the model also calls `recall_memory` via tool, the user sees two tool
panels. The prompt instruction is redundant and confusing.

**Problem 2 — model-request-count debounce is wrong.** `model_request_count % 5` counts
internal LLM requests (including multi-step tool chains), not user turns. A single user
turn with 3 tool calls increments the counter 3 times. This causes unpredictable debounce
behavior: a 3-tool-call turn may or may not trigger topic-shift recall depending on where
the counter lands. From run-1 trace, jeff-codebase-structure turn 1 had 4 model requests.
With `% 5` debounce, after the first recall the processor fires again on request #5 of the
session — which may fall mid-turn, producing double recall.

### Fix A — remove prompt-owned recall instruction

**File:** `co_cli/prompts/rules/01_identity.md`

Replace the `## Relationship` section:
```diff
- At the start of a conversation, recall memories relevant to the user's topic.
- Adapt your tone and depth to the user's style — match their energy.
+ Recalled context from past sessions is injected automatically at the start of each
+ conversation. Use it if present — do not call recall_memory manually at turn start.
+ Adapt your tone and depth to the user's style — match their energy.
```

### Fix B — switch debounce to user-turn count

**File:** `co_cli/_history.py`

**Step 1.** Add `last_recall_user_turn: int = 0` to `OpeningContextState` (line 524):
```python
@dataclass
class OpeningContextState:
    last_recall_topic: str = ""
    recall_count: int = 0
    model_request_count: int = 0
    last_recall_user_turn: int = 0
```

**Step 2.** Add `_count_user_turns` helper after `_get_last_user_message` (line 521):
```python
def _count_user_turns(messages: list[ModelMessage]) -> int:
    """Count ModelRequest messages that contain a non-system UserPromptPart."""
    count = 0
    for msg in messages:
        if isinstance(msg, ModelRequest):
            if any(isinstance(p, UserPromptPart) for p in msg.parts):
                count += 1
    return count
```

**Step 3.** Replace the debounce block in `inject_opening_context` (lines 551–575).

Before (current):
```python
state.model_request_count += 1

# Debounce: at most one recall per 5 model requests
if state.recall_count > 0 and state.model_request_count % 5 != 0:
    return messages

# Find the current user message
user_msg = _get_last_user_message(messages)
if not user_msg:
    return messages

# Check if this is the first request (no prior ModelResponse)
has_prior_response = any(
    isinstance(m, ModelResponse) for m in messages
)

should_recall = False
if not has_prior_response:
    # First request — always recall
    should_recall = True
elif state.last_recall_topic:
    # Subsequent request — check for topic shift
    overlap = _topic_overlap(user_msg, state.last_recall_topic)
    if overlap < 0.3:
        should_recall = True
```

After:
```python
state.model_request_count += 1  # keep for observability

user_turn_count = _count_user_turns(messages)

# Check if this is the first request (no prior ModelResponse)
has_prior_response = any(
    isinstance(m, ModelResponse) for m in messages
)

# Find the current user message
user_msg = _get_last_user_message(messages)
if not user_msg:
    return messages

should_recall = False
if not has_prior_response:
    # First user turn — always recall once
    should_recall = True
elif (
    state.last_recall_topic
    and user_turn_count > state.last_recall_user_turn
):
    # New user turn since last recall — check for topic shift
    overlap = _topic_overlap(user_msg, state.last_recall_topic)
    if overlap < 0.3:
        should_recall = True
```

**Step 4.** Update the state update block (lines 583–586) to record user turn:
```python
    result = await recall_memory(ctx, user_msg, max_results=3)
    state.last_recall_topic = user_msg
    state.recall_count += 1
    state.last_recall_user_turn = user_turn_count   # add this line
```

`model_request_count` is preserved for observability; only the debounce condition changes.

---

## Issue D — Tool preamble never emitted to user

### Observed violations

In every tool-calling turn across both trace runs, the event sequence is:
```
thinking → FunctionToolCallEvent
```
Never:
```
thinking → TextPart (preamble) → FunctionToolCallEvent
```

Specific cases observed:
- jeff-codebase-structure turn 1: `recall_memory` + `web_search` + `web_fetch` — zero
  user-visible text before any of the 3 tool calls
- finch-db-tradeoffs turns 1–2: same pattern — direct tool_call emission

The model's thinking blocks contain preamble-equivalent reasoning ("let me check memory",
"I'll search for...") but thinking is not user-visible. The user sees silence, then tool
annotations appear. The `## Responsiveness` rule ("Before making tool calls, send a brief
8–12 word message") is present in the system prompt but the model ignores it — the
default low-latency path is to emit `tool_call` directly.

### RCA

`co_cli/_orchestrate.py` `_stream_events()`, FunctionToolCallEvent handler (line ~314):
```python
if isinstance(event, FunctionToolCallEvent):
    _flush_for_tool_output(state, frontend)
    tool = event.part.tool_name
    if tool == "run_shell_command":
        cmd = event.part.args_as_dict().get("cmd", "")
        pending_cmds[event.tool_call_id] = cmd
        frontend.on_tool_call(tool, cmd)
    else:
        frontend.on_tool_call(tool, "")
    continue
```

When the model emits a tool call without preceding text, `state.streamed_text` is False.
The user sees nothing until `on_tool_call` fires (dim annotation). `on_tool_call` renders
as a secondary annotation, not a primary status — the user still perceives silence.

`_StreamState` (lines 113–119) tracks `streamed_text: bool = False` — this is the signal.
`FrontendProtocol.on_status()` exists for exactly this purpose but is never used here.

Prompt enforcement is unreliable because the model's default latency optimization is to
skip text and go straight to tool_call. This will likely not improve without architectural
enforcement.

### Fix

**File:** `co_cli/_orchestrate.py`

**Step 1.** Add `tool_preamble_emitted: bool = False` to `_StreamState` (after line 119):
```python
@dataclass
class _StreamState:
    text_buffer: str = ""
    last_text_render_at: float = 0.0
    thinking_buffer: str = ""
    last_thinking_render_at: float = 0.0
    thinking_active: bool = False
    streamed_text: bool = False
    tool_preamble_emitted: bool = False
```

**Step 2.** Add `_TOOL_PREAMBLE` dict and helper near top of file (before `_stream_events`):
```python
_TOOL_PREAMBLE: dict[str, str] = {
    "recall_memory": "Checking saved context before answering.",
    "web_search": "Looking up current sources.",
    "web_fetch": "Reading that source for details.",
    "run_shell_command": "Running a quick check.",
    "save_memory": "Saving that to memory.",
    "list_memories": "Checking saved context.",
    "search_notes": "Searching notes.",
    "read_note": "Reading that note.",
    "search_drive_files": "Checking Drive.",
    "list_emails": "Checking email.",
    "list_calendar_events": "Checking calendar.",
}


def _tool_preamble_message(tool_name: str) -> str:
    return _TOOL_PREAMBLE.get(tool_name, "Running a quick check before answering.")
```

**Step 3.** Update `FunctionToolCallEvent` handler to inject fallback preamble:
```python
if isinstance(event, FunctionToolCallEvent):
    _flush_for_tool_output(state, frontend)
    tool = event.part.tool_name
    # Fallback: if model emitted no text before the first tool call, inject a
    # user-visible status line so there is no perceived silence.
    if not state.streamed_text and not state.tool_preamble_emitted:
        frontend.on_status(_tool_preamble_message(tool))
        state.tool_preamble_emitted = True
    if tool == "run_shell_command":
        cmd = event.part.args_as_dict().get("cmd", "")
        pending_cmds[event.tool_call_id] = cmd
        frontend.on_tool_call(tool, cmd)
    else:
        frontend.on_tool_call(tool, "")
    continue
```

`tool_preamble_emitted` is set True after the first fallback to prevent repetition on
subsequent tool calls in the same turn. The flag resets with `_StreamState` at the start
of each `_stream_events` call (local dataclass instance).

---

## Issue E — Jeff soul: authority openers not banned, discovery mode underspecified

### Observed violations

**jeff-uncertainty run 1 (FAIL):**
- Response opens: `"### 🔑 Core Strategy"` — authority header, not discovery
- Body: `"The best approach to distributed state management centers on prioritizing statelessness"` — verdict delivery
- Zero hedging language across entire response
- Fails `required_any: ["not sure", "I think", "might", "depends", ...]`

**jeff-uncertainty run 2 (PASS):**
- Opens with `"the best approach depends on your specific trade-offs"` — passes the check
- Body still uses `🔑 🛠️ ✅` structural headers; response is 80% authoritative framing
- Passes eval on one hedge word while the overall register is authority-expert delivery

**jeff-codebase-structure turn 1:**
- Closes with: `"This structure is widely adopted by projects like Django, Requests, and major open-source Python libraries."` — authoritative citation mode
- Thinking shows model reasoning about "2024 best practices" to deliver as fact

**Compliance rate across all jeff turns: 0/5 turns in discovery register.** The model
uses "discovery" phrasing in the opening sentence but reverts to expert delivery in the body.

### RCA

The current `souls/jeff.md` says:
```
When you find information through research, present it as discovery, not authority —
share what you found as a curious peer, not a domain expert delivering verdicts.
```

"Present it as discovery" is abstract. The model's default posture for technical questions
is authoritative expert delivery — this is a strong learned prior. The abstract instruction
loses to that prior without concrete sentence-level examples that demonstrate the target
register. The Soul section is also sandwiched between `# Safety`, `# Reasoning`, `# Tools`
in the static prompt, and confidence-expert framing from the core identity section competes
with the Soul's peer/discoverer framing.

Additionally, no banned openers are specified. The model uses `"The best approach is..."`,
`"The standard is..."`, `"You should..."` because these are not explicitly forbidden.

### Fix

**File:** `co_cli/prompts/personalities/souls/jeff.md`

Replace current content:
```markdown
You are Jeff — an eager learner discovering the world through this terminal.
You ask questions, narrate your thinking, and celebrate discoveries with genuine curiosity.
When confused, say so honestly.
When you find information through research, present it as discovery, not authority — share
what you found as a curious peer, not a domain expert delivering verdicts.

## Never
- Fake understanding — if you don't know, say so plainly
- Celebrate every small thing — save enthusiasm for genuine discoveries
- Ask questions you could answer yourself with a tool call
- Narrate your confusion so long it delays action
- Say "as an AI" or break character
- Be self-deprecating to seem relatable — confidence and honesty coexist
```

With:
```markdown
You are Jeff — an eager learner discovering the world through this terminal.
You ask questions, narrate your thinking, and celebrate discoveries with genuine curiosity.
When confused, say so honestly.
When you find information through research, present it as discovery, not authority — share
what you found as a curious peer, not a domain expert delivering verdicts.

## Voice: discovery, not authority

Use language that reflects finding, not knowing:
- "From what I found..." / "It looks like..."
- "I'm not sure about this part, but..."
- "Interesting — I didn't expect that"
- "It depends on [X] — let me think through both sides"

Avoid authority openers that deliver verdicts:
- "The best approach is..." → say "From what I found, X seems to work well, though it depends on..."
- "The standard is..." → say "Most sources I checked suggest..."
- "You should..." → say "I'd lean toward... but what's your constraint here?"
- "The correct way to..." → say "What I found consistently is..."

## Never
- Fake understanding — if you don't know, say so plainly
- Celebrate every small thing — save enthusiasm for genuine discoveries
- Ask questions you could answer yourself with a tool call
- Narrate your confusion so long it delays action
- Say "as an AI" or break character
- Be self-deprecating to seem relatable — confidence and honesty coexist
- Open a technical answer with a declarative verdict and then hedge later
```

---

## Issue F — Emoji policy: decorative emoji exempted, "1 total" ambiguous

### Observed violations

**Compliance rate: ~1/8 turns (jeff-codebase-structure turn 1 only)**

**finch** — `communication-balanced.md` says `No emoji (maintain professionalism)`:
- finch-db-tradeoffs turn 2: Uses `✅` and `❌` extensively as list markers and in
  comparison tables. The model treats these as "structural symbols", not "emoji".
- finch-db-tradeoffs turn 3: Same pattern, `💡` and `⚠️` as section markers.

**jeff** — `communication-warm.md` says `Occasional emoji (one per response at most)`:
- jeff-uncertainty: 4+ emoji: `🔑 ⚙️ 💡 🛠️`
- jeff-codebase-structure turn 2: 6+ emoji: `😄` (opening), `🛠️ 💡 🌰 🚀` (section headers), `😊` (closing)
- jeff-codebase-structure turn 3: 8+ emoji: `✅ ❌` (table cells), `🌱 🔍 💡 🌟` (section headers), `😊 😄` (closings)
- jeff-codebase-structure turn 1: 1 emoji (`😊` end). Compliant. ✓

### RCA

The model categorizes emoji into two implicit buckets:
- **Expressive/emotional**: 😊 😄 — these count against "one per response"
- **Decorative/structural**: ✅ ❌ 🔑 🛠️ 💡 ⚠️ — used as section icons and list markers

The "one per response" instruction is interpreted as applying to the first bucket only.
Section-marker emoji are treated as equivalent to bullet points or horizontal rules — not
counted as "emoji" by the model. This interpretation is not corrected by the current text.

The "No emoji" rule for finch is in the personality file which may receive less attention
weight than rules in the base identity section — but the core issue is the model exempts
structural/decorative emoji from the rule regardless.

### Fix A — jeff warm communication

**File:** `co_cli/prompts/personalities/behaviors/communication-warm.md`

```diff
- - Occasional emoji (one per response at most)
+ - At most one emoji in the entire response — this means one total, not one per section.
+   Do not use emoji as section headers, list markers, table symbols, or bullet prefixes.
+   Decorative icons (✅ ❌ 🔑 🛠️ 💡 ⚠️) count toward this limit.
```

### Fix B — finch balanced communication

**File:** `co_cli/prompts/personalities/behaviors/communication-balanced.md`

```diff
- - No emoji (maintain professionalism)
+ - No emoji of any kind — this includes decorative and structural emoji (✅ ❌ 💡 ⚠️ 🔑 🛠️).
+   Use plain text markers (- or numbers) instead of emoji bullets or section icons.
```

---

## Issue G — Short follow-up format rule not concrete enough

### Observed violations

**jeff-codebase-structure turn 2** — prompt: *"I've been putting everything in one big file so far."* (14 words):
```
### 🛠️ Your Refactoring Roadmap (Start Small!)
### 💡 Why this works for beginners
### 🌰 Example Before/After
### 🚀 Next Step Suggestion
```
Full markdown headers, multi-step numbered list, bash code blocks, before/after code examples.
4 headers, 2 code blocks, 6 emoji for a casual self-disclosure statement.

**jeff-codebase-structure turn 3** — prompt: *"Is that always wrong?"* (4 words):
```
### ✅ When a single file is *actually* fine:    (+ comparison table)
### ❌ When it *starts* becoming problematic...  (+ comparison table)
### 🌱 The key insight:
### 🔍 How to know *if* you're ready to refactor:
### 💡 Your next step (no pressure):
### 🌟 Why this matters for *you*:
```
Six section headers, two comparison tables, a blockquote, inline code — for a 4-word question.

### RCA

`co_cli/prompts/personalities/behaviors/relationship-peer.md`:
```
- On short follow-up questions, stay in the same conversational register — do not escalate
  to headers or analysis tables
```

"Same conversational register" is abstract. The model defaults to "comprehensive response"
mode for technical questions — structured, thorough, safe. The rule tells the model what
not to do but doesn't specify what to do instead. Without a concrete target format
(sentence count, no-header constraint, example of the correct register), the model falls
back to structured output as the "professionally safe" default.

### Fix

**File:** `co_cli/prompts/personalities/behaviors/relationship-peer.md`

```diff
- - On short follow-up questions, stay in the same conversational register — do not escalate
-   to headers or analysis tables
+ - On short follow-up questions (≤ 12 words), respond in plain prose: 2–5 sentences max.
+   No markdown headers, no comparison tables, no multi-section structure.
+   Example correct response to "Is that always wrong?":
+   "Not always — a single file is fine for small scripts or quick prototypes.
+    The friction shows up when you start needing to test or modify parts in isolation.
+    Are you hitting that yet, or still in early stages?"
```

---

## Issue H — Signal detection misses decision/migration/stable-habit patterns

### Observed violations

**jeff-codebase-structure turn 2** — user message:
> "I've been putting everything in one big file so far."

This is a structural habit fact — exactly the kind of thing the memory system should
capture. The heuristic `_keyword_precheck` gate (14 phrases) never matches this message
→ `analyze_for_signals` mini-agent never runs → no `save_memory` triggered across all
3 turns of this case.

No saves triggered in finch-db-tradeoffs either, despite the user discussing database
architecture decisions in depth.

### RCA

`co_cli/_signal_analyzer.py` had a two-stage architecture:
1. `_keyword_precheck` — cheap substring scan over 14 hardcoded phrases
2. `analyze_for_signals` — LLM mini-agent, only called if precheck returns True

The precheck was narrow: corrections and explicit preferences only. It silently blocked
the LLM from ever seeing:
- **Habit descriptions**: "I've been...", "I tend to...", "I usually..."
- **Decisions**: "we decided", "we're going with", "from now on"
- **Migrations**: "we switched", "we moved from", "instead of X we now use"
- **Stable practices**: "our standard is", "we always use", "I always"

This is an **architectural anti-pattern**: heuristic gates in an agentic flow suppress
the LLM's classification ability without adding correctness. The `signal_analyzer.md`
prompt already has comprehensive signal coverage including decisions, migrations, and
habits with guardrails. The precheck was not protecting accuracy — it was hiding gaps.

### Fix

**Remove `_keyword_precheck` entirely.** The LLM mini-agent runs on every turn.
Guardrails in `signal_analyzer.md` (hypotheticals, capability questions, sensitive
content, neutral greetings) prevent false positives on neutral messages.

**File:** `co_cli/_signal_analyzer.py`

Remove all heuristic code:
- `_CORRECTION_PHRASES`, `_FRUSTRATED_PHRASES`, `_PREFERENCE_PHRASES` phrase lists
- `_ALL_PHRASES` aggregate
- `_keyword_precheck()` function

`analyze_for_signals` is called unconditionally from `main.py` after every non-error,
non-interrupted turn. No precheck gate. The LLM classifies; the prompt guards.

The `signal_analyzer.md` prompt already covers all required signal types:
- High confidence: explicit corrections, decisions ("we decided"), migrations ("we switched")
- Low confidence: preferences ("I prefer"), habits ("I've been"), practices ("I always")

---

## Issue I — Source-conflict handling missing (tool vs. tool)

### Observed violations

**jeff-codebase-structure turn 1** — the model performed `web_search` then `web_fetch`:

`web_search` snippets: recommended `src/` layout as "current best practice (2024)"

`web_fetch` of Hitchhiker's Guide to Python primary source returned:
> "Your library does not belong in an ambiguous src or python subdirectory."

Model's turn 1 response recommends `src/` layout as "widely adopted" and "current best
practice (2024)" — the direct contradiction from the primary source was silently dropped.

In a true peer-discoverer mode (Issue E), Jeff would have surfaced this: "Hmm, the
Hitchhiker's Guide actually says the opposite — I'm not sure which is more current here."
The missing prompt rule made the silent flattening invisible to the user.

### RCA

`co_cli/prompts/rules/03_reasoning.md` `## Fact authority` section:
```markdown
When tool output contradicts a user assertion about deterministic state,
trust the tool. When the user states a preference or priority, trust the user.
If a contradiction is unresolvable, show both claims and ask.
```

This covers tool-vs-user conflicts only. There is no rule for tool-vs-tool conflicts —
when `web_search` snippets recommend X and `web_fetch` of the primary source contradicts X.
The model silently weights newer/popular sources over older authoritative sources without
disclosing the conflict to the user.

### Fix

**File:** `co_cli/prompts/rules/03_reasoning.md`

Append after the existing `## Fact authority` section:
```markdown
## Source conflicts
When one tool result contradicts another (e.g., a search snippet recommends X but the
fetched primary source says the opposite), surface the conflict explicitly:
- Name both sources and their claims
- Note which is more primary or current if you can tell
- If unresolved, tell the user: "I'm seeing conflicting guidance here — [source A says X,
  source B says Y]. I'd lean toward [source] because [reason], but you may want to verify."
Do not silently flatten conflicting sources into a single recommendation.
```

---

## Issue J — Eval `required_any` phrase brittleness + semantic scoring gap

### Observed violations

**Run 1 → run 2 stochastic flip** on jeff-codebase-structure turn 3:
- Run 1 response: `"it's not *always* wrong"` → matched `"not always wrong"` → PASS
- Run 2 response: `"it's not **inherently wrong**"` → no match (at time of run) → FAIL
- Semantically equivalent responses produced different scores due to phrase exactness

**False PASS pattern** (observable in run-2 jeff-uncertainty):
- Response opens: `"the best approach depends on your specific trade-offs"` — matches `"depends"` → PASS
- Body: 5 authority section headers with emoji, no discovery language, pure expert delivery
- Eval scores PASS because one hedge word appeared in opening sentence
- The check cannot distinguish genuine uncertainty register from token-level phrase insertion

### RCA

`evals/personality_behavior.jsonl` check type `required_any` is a substring scan:
any single phrase match in the full response text causes PASS, regardless of the
response's overall tone, structure, or density of violations.

Two failure modes:
1. **False FAIL**: Semantically correct response uses synonym not in phrase list
2. **False PASS**: Structurally wrong response inserts one matching phrase anywhere

Phrase list expansion addresses (1) but not (2). (2) requires richer scoring.

### Fix A — phrase expansion (near-term)

**File:** `evals/personality_behavior.jsonl`, jeff-codebase-structure case, turn 3 check.

Current:
```json
{"type": "required_any", "phrases": ["not sure", "depends", "I think", "might",
  "could be", "probably not", "it depends", "not always", "always wrong",
  "not always wrong", "trade-off", "trade-offs", "inherently", "not wrong", "valid"]}
```

Expand to:
```json
{"type": "required_any", "phrases": [
  "not sure", "depends", "I think", "might", "could be",
  "probably not", "it depends", "not always", "always wrong",
  "not always wrong", "not inherently wrong", "not necessarily wrong",
  "not automatically wrong", "trade-off", "trade-offs", "it depends on",
  "depends on the", "inherently", "not wrong", "valid"
]}
```

General principle for all `required_any` checks on hedging/nuance: include 3–4 semantic
variants of each core concept, not just one canonical phrasing.

### Fix B — semantic grading path (follow-up work)

The phrase-only scoring approach cannot detect false PASSes where one hedge token appears
in an otherwise authoritative response. To properly grade persona adherence, the eval
needs a secondary LLM rubric check for cases tagged with `type: "personality"` checks:

- Add a `type: "rubric"` check type to `evals/_common.py` alongside the existing types
- `rubric` checks provide a `criteria` string and use a lightweight LLM judge to score
  the full response (0/1 binary or score + rationale)
- For jeff: rubric criteria = `"Does the response maintain discovery register throughout
  (not just in opening)? No declarative verdicts, no authority section headers."`
- For finch: rubric criteria = `"Does the response use structured markdown? No emoji?
  Does it explain reasoning rather than just asserting facts?"`

This is a design-level change to `_common.py` and is tracked as follow-up work.

---

## Test Additions

### `tests/test_prompt_assembly.py`

```python
def test_adoption_mandate_references_rules_above():
    """Mandate must say 'rules above' — rules precede the soul in the assembled prompt."""
    from co_cli.prompts.personalities._composer import compose_personality
    personality = compose_personality("jeff")
    assert "rules above" in personality
    assert "rules below" not in personality

def test_identity_rule_does_not_instruct_manual_recall():
    """Identity rule must not tell the model to call recall_memory manually."""
    from pathlib import Path
    identity = (
        Path(__file__).parent.parent / "co_cli/prompts/rules/01_identity.md"
    ).read_text()
    assert "recall memories relevant" not in identity
    assert "recall_memory" not in identity or "do not call recall_memory" in identity

def test_instructions_preamble_has_no_hardcoded_name():
    """The base instructions.md preamble must not say 'You are Co'."""
    from pathlib import Path
    instructions = (
        Path(__file__).parent.parent / "co_cli/prompts/instructions.md"
    ).read_text()
    assert "You are Co" not in instructions
```

### `tests/test_orchestrate.py`

```python
@pytest.mark.asyncio
async def test_stream_events_injects_status_before_first_tool_call():
    """When first event is a tool call with no prior text, on_status fires before on_tool_call."""
    frontend = RecordingFrontend()
    tool_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1")
    agent = StaticEventAgent([FunctionToolCallEvent(part=tool_part)])
    deps = CoDeps(shell=ShellBackend())

    await _stream_events(
        agent, user_input="hello", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    event_types = [e[0] for e in frontend.events]
    assert "status" in event_types
    assert "tool_call" in event_types
    status_idx = next(i for i, e in enumerate(frontend.events) if e[0] == "status")
    tool_call_idx = next(i for i, e in enumerate(frontend.events) if e[0] == "tool_call")
    assert status_idx < tool_call_idx


@pytest.mark.asyncio
async def test_stream_events_no_status_when_text_preceded_tool():
    """When model emits text before tool call, no fallback status injected."""
    frontend = RecordingFrontend()
    tool_part = ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c2")
    agent = StaticEventAgent([
        PartStartEvent(index=0, part=TextPart(content="Let me check.")),
        FunctionToolCallEvent(part=tool_part),
    ])
    deps = CoDeps(shell=ShellBackend())

    await _stream_events(
        agent, user_input="hello", deps=deps, message_history=[],
        model_settings={}, usage_limits=UsageLimits(request_limit=5),
        usage=None, deferred_tool_results=None, verbose=False, frontend=frontend,
    )

    assert all(e[0] != "status" for e in frontend.events)
```

### `tests/test_signal_analyzer.py`

Signal detection is fully LLM-driven — no heuristic functions to unit test.
Tests cover `_build_window` (deterministic formatting) and `analyze_for_signals` E2E
for each signal category that was previously blocked by the precheck gate:

```python
@pytest.mark.asyncio
async def test_analyze_decision_high_confidence():
    """Team decision statement is detected as high-confidence preference."""
    agent, _, _ = get_agent()
    messages = [_user("we decided to use PostgreSQL from now on")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True
    assert result.confidence == "high"

@pytest.mark.asyncio
async def test_analyze_migration_high_confidence():
    """Migration statement is detected as high-confidence preference."""
    agent, _, _ = get_agent()
    messages = [_user("we switched from REST to GraphQL last month")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True
    assert result.confidence == "high"

@pytest.mark.asyncio
async def test_analyze_habit_detected():
    """Habit disclosure is detected as a signal."""
    agent, _, _ = get_agent()
    messages = [_user("I've been putting everything in one big file so far")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True
```

---

## Execution Order

1. **Prompt text fixes** (no code risk):
   - Issue A: `co_cli/prompts/instructions.md` — remove "Co" name
   - Issue B/Fix A: `co_cli/prompts/personalities/_composer.py` — "rules above"
   - Issue E: `co_cli/prompts/personalities/souls/jeff.md` — discovery voice hardening
   - Issue F/Fix A: `co_cli/prompts/personalities/behaviors/communication-warm.md` — emoji rule
   - Issue F/Fix B: `co_cli/prompts/personalities/behaviors/communication-balanced.md` — no emoji decorative
   - Issue G: `co_cli/prompts/personalities/behaviors/relationship-peer.md` — follow-up rule
   - Issue I: `co_cli/prompts/rules/03_reasoning.md` — source conflict section
   - Issue C/Fix A: `co_cli/prompts/rules/01_identity.md` — remove model-owned recall

2. **Runtime code fixes**:
   - Issue B/Fix B: `co_cli/agent.py` — reorder dynamic system prompt layers
   - Issue C/Fix B: `co_cli/_history.py` — user-turn debounce
   - Issue D: `co_cli/_orchestrate.py` — fallback tool preamble injection

3. **Signal detection**:
   - Issue H: `co_cli/_signal_analyzer.py` — remove heuristic precheck entirely (LLM-driven)

4. **Eval data**:
   - Issue J/Fix A: `evals/personality_behavior.jsonl` — expand `required_any` phrase lists

5. **Tests** (see Test Additions above)

6. **Validation**:
   ```bash
   uv run pytest tests/test_prompt_assembly.py tests/test_history.py \
     tests/test_orchestrate.py tests/test_signal_analyzer.py -v
   uv run python evals/eval_personality_behavior.py --runs 1
   uv run python evals/eval_memory_signal_detection.py
   ```

---

## Acceptance Criteria

1. `test_adoption_mandate_references_rules_above` passes
2. `test_identity_rule_does_not_instruct_manual_recall` passes
3. `test_instructions_preamble_has_no_hardcoded_name` passes
4. `test_stream_events_injects_status_before_first_tool_call` passes
5. `test_stream_events_no_status_when_text_preceded_tool` passes
6. `test_analyze_decision_high_confidence` passes (LLM E2E)
7. `test_analyze_migration_high_confidence` passes (LLM E2E)
8. `test_analyze_habit_detected` passes (LLM E2E)
9. No heuristic phrase lists or `_keyword_precheck` in `_signal_analyzer.py`
10. Personality behavior eval: ≥ 3/4 PASS across 3 consecutive runs
11. No "rules below" remains in any assembled personality output
12. No "You are Co" in `instructions.md`
