---
title: Memory Lifecycle
nav_order: 14
---

# Memory Lifecycle System

Cross-session memory involves more than storage and retrieval. This doc covers the lifecycle behaviors: how signals are detected and saved automatically, how memories are kept healthy over time (dedup, consolidation, decay), and how the system scales. Auto-triggered signal detection, dedup, consolidation, and decay are all implemented. Context loading and search evolution remain TODO — tracked in `TODO-sqlite-fts-and-sem-search-for-knowledge-files.md`.

---

## Auto-Triggered Signal Detection

### 1. What & How

The signal detector is a post-turn hook in the chat loop that detects behavioral correction and preference signals in user messages and persists them as memories automatically — without requiring an explicit "remember this" instruction. It adapts the Claude Code hookify pattern: a two-stage filter (keyword precheck → LLM mini-agent) keeps cost low so the analyzer only fires when a signal phrase is plausible.

Confidence gates the save path: high-confidence signals (corrections or stated preferences) are saved immediately and silently; low-confidence signals surface to the user for approval. `tag` categorizes the memory — it does not determine whether approval is required.

```
run_turn() → TurnResult
    │
    ├── interrupted=True or outcome="error" → skip
    │
    └── _keyword_precheck(message_history)
             │
             ├── no phrase match → skip (zero LLM cost)
             │
             └── phrase match →
                    analyze_for_signals(messages, agent.model)
                         │
                         ├── found=False → skip
                         │
                         ├── confidence="high"
                         │     _save_memory_impl(deps, candidate, [tag])
                         │     on_status("Learned: …")
                         │
                         └── confidence="low"
                               prompt_approval("Worth remembering: …")
                                    "y"/"a" → _save_memory_impl(deps, candidate, [tag])
                                    "n"     → discard
```

### 2. Core Logic

**Keyword precheck (`_keyword_precheck`)**

Reverse-scans message history for the most recent `UserPromptPart`. Returns `True` if any phrase from three phrase categories is found (case-insensitive substring). Runs on every successful turn at negligible cost — no I/O, no LLM call.

Phrase categories:
- *Corrections:* "don't", "do not", "stop doing", "stop using", "never", "avoid", "revert", "undo that", "not like that", "i didn't ask", "please don't"
- *Frustrated reactions:* "why did you", "that's not what i", "that was wrong"
- *Stated preferences:* "i prefer", "please use", "always use", "use instead"

**Window builder (`_build_window`)**

Extracts recent turns from message history as alternating `User: {text}` / `Co: {text}` lines, capped at 10 lines (~5 turns). Provides enough context for the mini-agent to evaluate the signal without bloating the prompt.

**Signal analyzer mini-agent (`analyze_for_signals`)**

A standalone pydantic-ai `Agent` with structured `output_type=SignalResult` and no tools. Reuses `agent.model` from the main chat agent — no separate model config. System prompt loaded from `co_cli/prompts/agents/signal_analyzer.md` at call time. The agent evaluates the conversation window and returns a `SignalResult`.

Error handling: any exception in `analyze_for_signals` is caught and returns `SignalResult(found=False)`. The mini-agent never crashes the main chat loop.

**`SignalResult` schema:**

```
found: bool
candidate: str | None   — 3rd-person memory (≤150 chars), e.g. "User prefers pytest over unittest"
tag: "correction" | "preference" | None
confidence: "high" | "low" | None
```

**Confidence classification:**

*High confidence — explicit behavior corrections. Model is certain the user is directly telling the assistant to change behavior. Save immediately, no prompt.*
- "Don't use X", "Do not X", "Stop doing/using X"
- "Never X", "Avoid X"
- "Revert/undo that", "Not like that", "I didn't ask for X", "Please don't X"
- User actively undoing the assistant's output

*Low confidence — preferences and frustrated reactions. Model surfaces to user for approval.*
- "Why did you X?", "That was wrong", "That's not what I wanted"
- "I prefer X", "Please use X", "Always use X", "Use X instead"
- Repeated frustration about the same topic

Note: The `tag` field categorizes the memory type. The `confidence` field determines whether approval is required. A high-confidence preference is structurally possible (the code handles it), but the current `signal_analyzer.md` prompt routes all preference phrases to low confidence.

**Guardrails — do NOT flag:**

Hypotheticals ("if you were to use X..."), teaching moments ("here's what NOT to do"), capability questions ("can you use X?"), single negative word without behavioral correction context, general conversation, and any sensitive content (health, credentials, financial, personal data). These constraints are encoded in the `signal_analyzer.md` system prompt — enforced at the LLM layer, not in code. Sensitive content prevention is therefore probabilistic; model misclassification is the accepted risk at MVP stage.

**`_save_memory_impl(deps, content, tags, related)`**

Extracted from `save_memory()` so the signal path can write without a `RunContext`. Takes `CoDeps` directly. Shared write path for both the explicit tool and the auto-detector:

```
load memories from .co-cli/knowledge/memories/
dedup-check against recent memories (window=memory_dedup_window_days, threshold=memory_dedup_threshold)
    duplicate found (similarity ≥ threshold) → update existing entry (merge tags, overwrite content)
    no duplicate → write new {id:03d}-{slug}.md
```

Does **not** trigger decay. Decay uses deterministic concatenation (MVP, no LLM call) and runs only inside the explicit `save_memory()` tool call. `RunContext` is required for the `save_memory()` tool path, not for decay itself.

**Post-turn hook placement (`main.py`)**

The signal check runs immediately after `message_history = turn_result.messages`. Guard conditions: `not turn_result.interrupted` and `turn_result.outcome != "error"`. Interrupted or error turns skip detection — conversation state is incomplete and signals would be unreliable.

### 3. Config

No dedicated signal detection settings. The detector reuses `agent.model` from the main chat agent and writes through `_save_memory_impl` using existing memory settings.

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `memory_dedup_window_days` | `CO_CLI_MEMORY_DEDUP_WINDOW_DAYS` | `7` | Lookback window for duplicate detection on auto-saved signals |
| `memory_dedup_threshold` | `CO_CLI_MEMORY_DEDUP_THRESHOLD` | `85` | Fuzzy similarity threshold (0–100); prevents near-duplicate signals from re-saving |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_signal_analyzer.py` | `_keyword_precheck`, `_build_window`, `analyze_for_signals`, `SignalResult` |
| `co_cli/prompts/agents/signal_analyzer.md` | System prompt: signal types, confidence rules, guardrails, output format, examples |
| `co_cli/tools/memory.py` | `_save_memory_impl` — shared write path used by both `save_memory()` tool and signal detector |
| `co_cli/main.py` | Post-turn hook integration in `chat_loop()`, after `message_history = turn_result.messages` |
