---
title: "15 — Proactive Memory Detection"
parent: Infrastructure
nav_order: 6
---

# Design: Proactive Memory Detection

## 1. What & How

The agent autonomously detects memory-worthy information (preferences, corrections, decisions) through linguistic pattern recognition in prompts and tool docstrings. Pure prompt engineering — no hardcoded rules. The agent **reasons** about signals, not follows `if/then` rules.

```
User: "I prefer async/await"
  → Agent reads system prompt signal patterns
  → Matches "I prefer" → Preference signal
  → Calls save_memory("User prefers async/await", tags=["preference", "python"])
  → Approval prompt: "Save memory 5? [y/n/a]"
  → User approves → File written
```

**Interface to lifecycle:** DESIGN-15 ends at the `save_memory(content, tags)` call. Everything after — dedup, consolidation, decay, protection — is handled by DESIGN-14 Memory Lifecycle Management.

## 2. Core Logic

### Signal Detection Patterns

Located in `co_cli/prompts/system.md`. Markdown table showing input → signal → action:

| Signal Type | Trigger Phrases | Tag Convention |
|-------------|-----------------|----------------|
| **Preference** | "I prefer", "I like", "I favor", "I use" | `["preference", domain]` |
| **Correction** | "Actually", "No wait", "That's wrong", "I meant" | `["correction", domain]` |
| **Decision** | "We decided", "We chose", "We implemented" | `["decision", domain]` |
| **Context** | Factual statements about team/project/environment | `["context", domain]` |
| **Pattern** | "We always", "When we [do X]", "Never [do Y]" | `["pattern", domain]` |

The LLM uses these as **fuzzy matching templates** — similar phrasings trigger the same recognition.

### Negative Guidance

Equally important — what NOT to save:

- **Speculation:** "Maybe we should...", "I think...", "Could we..."
- **Questions:** "Should we use X?", "What if we tried Y?"
- **Transient details:** Only relevant to current session
- **Already known:** Information in context files
- **Uncertain statements:** Lacking confidence

Without negative guidance, models over-trigger (especially on speculation and questions).

### Tool Docstring Design

`save_memory` and `recall_memory` docstrings include structured "when to use" sections with signal examples, negative guidance, tag conventions, and concrete examples. Pydantic-AI extracts these as tool descriptions — the LLM sees them when deciding tool calls.

`recall_memory` docstring says "Use this **proactively**" — the agent recalls memories without being asked when context would benefit.

### Tone Calibration

Modern models are tone-sensitive. Balanced tone produces better reasoning than aggressive language:

- Declarative over imperative ("here are patterns" not "you must do X")
- Pattern-focused over command-focused
- Examples over rules
- No intensity markers (bold, caps, "immediately", "CRITICAL")

### Metadata Auto-Detection

`_detect_source(tags)` and `_detect_category(tags)` derive `source` and `auto_category` from the tags the agent provides. If tags include signal types (preference, correction, etc.) → `source: "detected"`. First matching signal tag becomes `auto_category`. Keeps tool signature simple (no extra parameters).

### Approval Flow

Uses existing DeferredToolRequests pattern — no changes needed. `save_memory` has `requires_approval=True`. User sees content before save and can approve (`y`), reject (`n`), or auto-approve (`a`).

## 3. Config

No new configuration. Signal detection is prompt-only. Model capability affects detection quality — recommended: Gemini 2.0 Flash+, GLM 4.7 Flash+, Claude Opus 4.5+.

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/system.md` | Signal patterns, negative guidance, example interactions |
| `co_cli/tools/memory.py` | Enhanced docstrings + `_detect_source()`, `_detect_category()` helpers |
| `co_cli/_commands.py` | `/forget` slash command |
