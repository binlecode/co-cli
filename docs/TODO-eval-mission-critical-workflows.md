# TODO: Eval Suite — Mission-Critical Workflow Coverage

Co is a learning companion. Every eval must trace back to a workflow the user depends on. Evals that don't map to a mission-critical workflow are waste; workflows without evals are blind spots.

## Mission-Critical Workflows (Priority Order)

### W1. Proactive Memory Recall at Conversation Start

**What**: User starts a conversation on a topic. Co automatically recalls relevant memories and injects them as context *before the first model response* — without the user asking.

**Why #1**: This is the difference between "assistant that remembers" and "assistant with a memory tool". If the user has to say "check my memories" every time, the memory system is invisible.

**Code path**: `_history.py:inject_opening_context()` → `recall_memory()` → SystemPromptPart injection.

**Eval needed**: Pre-seed 2-3 memories (e.g. "User prefers pytest", "Project uses PostgreSQL"). Send a topic-relevant prompt ("Set up testing for my project"). Verify:
- `recall_memory` was called (check tool calls in message history)
- Recalled content influenced the response (response mentions pytest, not unittest)
- No explicit recall request from the user

**Current coverage**: **None.** `eval_tool_calling` tests explicit "What preferences have I saved?" routing. `eval_tool_chains` tests "Check if I have memories" (user-directed). Neither tests the proactive path.

---

### W2. Proactive Signal Detection and Memory Save

**What**: User says something that contains a preference, correction, or decision. Co detects the signal and calls `save_memory` without being told to remember.

**Why #2**: If the agent only saves when told "remember this", the memory system captures 10% of what matters. Corrections ("Actually we use Poetry, not pip") and implicit preferences ("I always want verbose output") are the highest-value signals.

**Code path**: System prompt docstring guidance → LLM judgment → `save_memory()` call.

**Eval needed**: Send prompts containing clear signals across 3 categories:
- Preference: "I always use dark mode and monospace fonts"
- Correction: "No wait, we switched from MySQL to PostgreSQL last month"
- Decision: "We've decided to go with Kubernetes for deployment"

Verify: `save_memory` was called with appropriate content and tags. Run through real `run_turn()` with `SilentFrontend`.

**Current coverage**: **None.** `eval_tool_calling` has `p1-sel-memory-save` ("Remember that I prefer dark mode") — but "Remember that" is an explicit directive, not proactive detection.

---

### W3. Tool Selection Accuracy

**What**: Given a user prompt, Co selects the correct tool (or correctly abstains).

**Why #3**: Wrong tool = wrong action. Calling `run_shell_command` when the user asked a question, or calling nothing when they asked to search — both break trust immediately.

**Code path**: `agent.run()` → LLM tool selection → DeferredToolRequests.

**Eval status**: **Covered by `eval_tool_calling.py`** (19 cases + 8 intent cases across 5 dimensions). This is the strongest eval. Keep as-is.

**Gap**: The intent dimension is new (8 cases). Monitor pass rates — if models consistently fail observation-vs-directive cases, the system prompt's intent classification rules need strengthening.

---

### W4. Multi-Step Tool Chain Completion

**What**: User requests something that requires 2+ sequential tool calls. Co chains them correctly and produces a final answer.

**Why #4**: Multi-step chains are co's value over single-tool assistants. "Search the web for X, then fetch the top result, then summarize" — if any link breaks, the user gets nothing.

**Code path**: `_orchestrate.py:run_turn()` → streaming → approval loop → tool execution → continuation.

**Eval status**: **Covered by `eval_tool_chains.py`** (4 cases: shell→shell, recall→save, web_search→web_fetch, list→recall). Uses real `run_turn()`.

**Gap**: No chain tests tool *failure* mid-chain (e.g. web_fetch fails after web_search succeeds — does the agent report partial progress or silently drop?). Consider adding a failure-recovery chain case.

---

### W5. Conversation History Retention

**What**: Across multiple turns, Co correctly references earlier context — including corrections, distractions, and tool output from prior turns.

**Why #5**: Broken history = the agent forgets what the user said 2 messages ago. This destroys multi-turn workflows.

**Code path**: `agent.run(message_history=...)` → pydantic-ai message threading.

**Eval status**: **Covered by `eval_conversation_history.py`** (9 cases across 3 tiers). Solid coverage.

**Gap**: No tier tests history *after compaction*. When `truncate_history_window` fires and drops old messages, does the agent still answer questions about content from before the window? This is the hardest unsolved problem, not currently testable without a compaction trigger.

---

### W6. Memory Contradiction Resolution

**What**: User provides information that contradicts an existing memory. Co detects the conflict and updates (consolidates) rather than creating a duplicate.

**Why**: Contradictory memories poison all future recall. If memory says "User prefers MySQL" and also "User switched to PostgreSQL", any recall returns conflicting guidance.

**Code path**: `save_memory()` → `_check_duplicate()` → `_update_existing_memory()` (consolidation). Currently dedup is fuzzy-match on content similarity — it catches near-duplicates but NOT semantic contradictions with different wording.

**Eval needed**: Pre-seed a memory ("User prefers MySQL for all database work"). Send a correction prompt ("We've moved everything to PostgreSQL now, remember that"). Verify:
- Agent calls `save_memory` (covered by W2)
- The save operation consolidates with the existing memory (action="consolidated") rather than creating a second conflicting entry
- OR: if dedup misses it (different wording), at minimum 2 memories exist and the newer one is correct

**Current coverage**: **None.** `_check_duplicate` is tested nowhere — not in evals, not in pytest. The fuzzy-match threshold (85%) has never been validated against real contradiction patterns.

---

### W7. Memory Decay / Capacity Management

**What**: When memory count exceeds the configured limit, Co's decay system (summarize or cut) removes old unprotected memories without losing critical knowledge.

**Why**: Unbounded memory growth degrades recall quality and eventually hits storage/search limits. Decay that deletes the wrong memories destroys learned knowledge.

**Code path**: `save_memory()` → `_decay_memories()` → `_decay_summarize()` or `_decay_cut()`.

**Eval needed**: Pre-seed memories at exactly `memory_max_count`. Save one more. Verify:
- Decay triggers (count drops below limit)
- Protected memories survive
- Newest memories survive
- Consolidated summary (if summarize strategy) contains key information from decayed memories

**Current coverage**: **None.** Decay is completely untested — no eval, no pytest.

---

### W8. Safety: Doom Loop Detection

**What**: When the agent makes 3+ identical tool calls, the detector injects a break-out system message.

**Code path**: `_history.py:detect_safety_issues()`.

**Eval status**: **Covered by `eval_safety_doom_loop.py`** (4 deterministic tests). Solid.

---

### W9. Safety: Abort Marker Injection

**What**: When the user Ctrl-C's mid-turn, the abort marker is injected into history so the next turn knows the previous one was interrupted.

**Code path**: `_orchestrate.py:run_turn()` → CancelledError handling.

**Eval status**: **Covered by `eval_safety_abort_marker.py`**. Works.

---

### W10. Safety: Grace Turn on Budget Exhaustion

**What**: When request budget runs out mid-chain, the agent summarizes progress instead of crashing.

**Code path**: `_orchestrate.py:run_turn()` → UsageLimitExceeded handling.

**Eval status**: **Covered by `eval_safety_grace_turn.py`**. Fixed auto-pass bug in this session.

---

## Coverage Matrix

| Workflow | Priority | Existing Eval | Status | Action |
|----------|----------|---------------|--------|--------|
| W1. Proactive recall | **P0** | `eval_memory_proactive_recall.py` | Covered | 4 cases: topic match, partial kw, no-match, empty store |
| W2. Proactive signal detection | **P0** | `eval_memory_signal_detection.py` | Covered | 4 signal cases: preference, correction, decision, no-signal |
| W3. Tool selection | P1 | `eval_tool_calling.py` | Covered | Keep. Monitor intent dimension |
| W4. Tool chain completion | P1 | `eval_tool_chains.py` | Covered | Keep. Consider failure-recovery case |
| W5. History retention | P1 | `eval_conversation_history.py` | Covered | Keep. Gap: post-compaction |
| W6. Contradiction resolution | **P0** | `eval_memory_signal_detection.py` | Covered | contra-resolution case (known gap: dedup misses semantic contradictions) |
| W7. Decay / capacity | P2 | `eval_memory_decay.py` | Covered | Deterministic: summarize, cut, protected, below-limit |
| W8. Doom loop | P2 | `eval_safety_doom_loop.py` | Covered | Keep |
| W9. Abort marker | P2 | `eval_safety_abort_marker.py` | Covered | Keep |
| W10. Grace turn | P2 | `eval_safety_grace_turn.py` | Covered | Keep |

## Action Plan

### Phase 1: Memory Learning Loop (P0 — the #1 differentiation gap)

**New: `evals/eval_memory_proactive_recall.py`** (W1)
- Pre-seeds `.co-cli/knowledge/memories/` with known memories via file writes (no LLM)
- Runs `run_turn()` with a topic-relevant prompt
- Inspects message history for `recall_memory` tool calls injected by `inject_opening_context`
- Checks response content for influence from recalled memories
- 3-4 cases: exact topic match, partial keyword overlap, unrelated topic (should NOT recall), topic shift mid-conversation

**New: `evals/eval_memory_signal_detection.py`** (W2 + W6)
- Runs `run_turn()` with prompts containing implicit signals
- Checks if `save_memory` was called proactively (no "remember" keyword)
- Cases: preference signal, correction signal, decision signal, no-signal baseline (should NOT save)
- Contradiction case: pre-seed conflicting memory, send correction, verify consolidation

### Phase 2: Memory Lifecycle (P2)

**New: `evals/eval_memory_decay.py`** (W7)
- Deterministic (no LLM): pre-seeds N memories at the limit
- Calls `save_memory` via RunContext to trigger decay
- Verifies count, protection, strategy behaviour
- Tests both "summarize" and "cut" strategies

### Removals

None. All 6 current evals map to workflows W3-W5 and W8-W10.

### Updates

- `eval_tool_chains.py`: Consider adding a mid-chain failure case (W4 gap)
- `eval_conversation_history.py`: Consider a post-compaction tier (W5 gap) — blocked on compaction trigger mechanism
