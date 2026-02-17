# FIX: Proactive Memory Learning — Prompt-Driven Signal Detection

**Date**: 2026-02-15
**Eval**: `evals/eval_memory_signal_detection.py` (W2/W6)
**Model**: ollama-qwen3:30b-a3b-thinking-2507-q8_0-agentic

---

## Summary

Co's memory system had the plumbing (save/recall/decay/dedup) but the LLM
never saved proactively. Users had to say "remember this" or confirm a
suggestion before `save_memory` fired. The root cause was prompt-level:
rule 02 actively dampened saves and the tool docstring gave passive
permission without an explicit directive.

Three prompt locations were updated — no code changes required.

| File | Change | Effect |
|------|--------|--------|
| `co_cli/prompts/rules/01_identity.md` | Added proactive learning trait to Relationship section | Identity-level directive: saving is a core behavior |
| `co_cli/prompts/rules/02_safety.md` | Rewrote Memory constraints block | Removed dampener, added sensitivity safeguard |
| `co_cli/tools/memory.py` | Expanded `save_memory` docstring | Signal detection examples, "save liberally" guidance |
| `docs/DESIGN-16-prompt-design.md` | Synced rule content blocks | Design doc accuracy |

---

## Issue Analysis

### Symptom

The LLM only called `save_memory` when the user explicitly requested it:
- "remember this" / "save that" / "yes" (confirming a suggestion)

Implicit signals — preferences, corrections, decisions stated in natural
conversation — were ignored. The memory system captured ~10% of what mattered.

### Evidence: OTEL traces (pre-fix)

**Trace `7c848847` — pytest preference (reactive save)**

```
[user]      "what's my preferred testing framework"
[assistant] → recall_memory("testing framework") → empty
[assistant] "Common options: pytest, Jest, unittest. Set as preferred?"
[user]      "yes"
[assistant] → save_memory("User prefers pytest...", tags=["preference"])
```

The LLM's thinking trace: *"the user said 'yes' to setting their preferred
testing framework as pytest... I need to save this preference using the
save_memory function."* — waited for explicit confirmation.

**Trace `49ce348d` — Kyle collaboration (required "remember" keyword)**

```
[user]      "find Kyle in my google drive"
            ... 6 turns of research, reading transcripts ...
[assistant] "Kyle McCloskey is a product engineer working with you on..."
[user]      "remember this guy"                    ← explicit trigger
[assistant] → save_memory("User collaborates with Kyle McCloskey...")
```

The LLM's thinking trace: *"The phrase 'remember this guy' is casual but
intentional — they're asking me to store this professional relationship."*
— required the keyword before acting.

### Root Cause 1: Rule 02 dampened saves

Old text (lines 14-18 of `02_safety.md`):

```
Use save_memory only for global user preferences, personal facts, or
cross-session information. Never save workspace-specific paths, transient
errors, or session-specific build output. If unsure whether something is
worth remembering, ask the user.
```

Problems:
- **"only for"** — restrictive framing made saves feel exceptional
- **"If unsure... ask the user"** — the LLM is always "unsure" about implicit
  signals, so this clause suppressed every proactive save attempt
- **No sensitivity guard** — missing explicit prohibition on credentials,
  health, financial data

### Root Cause 2: Tool docstring was passive

Old `save_memory` docstring:

```
When to save:
- User preferences: "User prefers dark mode"
- Corrections: "Project uses Poetry, not pip"
...
save directly when the user asks you to remember something.
```

Problems:
- **"when the user asks"** — reinforced the reactive pattern
- Examples were labeled by category but didn't show implicit signals (what
  the user actually says vs. what to save)
- Missing "pattern" category (existed in code's `_detect_source` signal tags
  but absent from docstring)

### Root Cause 3: No identity-level learning directive

Rule 01 described relationship as recall-oriented: *"recall memories relevant
to the user's topic"*, *"maintain continuity across sessions."* Saving — the
other half of learning — was never mentioned as a core trait.

### Research Convergence

Cross-system analysis of ChatGPT, Windsurf, Cursor, Mem0, and MemGPT shows
all systems achieving proactive learning use a **hybrid approach**:

| System | Approach |
|--------|----------|
| ChatGPT | System directive ("You learn proactively") + tool guidance |
| Windsurf | Rule-level "save preferences and corrections" + tool docstring |
| Mem0 | Two-phase: LLM judgment (prompt-driven) + code extraction pipeline |
| MemGPT | System prompt "maintain your memory" + explicit save triggers |

None rely on tool docstrings alone. All use a directive + detail pattern.

---

## Solution

### Design Decision: Hybrid (rule directive + tool docstring detail)

Not a new rule file. A `06_learning.md` would over-engineer what fits
naturally in existing locations. Net prompt budget change: ~+150 chars
(rewrite, not purely additive). Well within the 6,000-char ceiling.

### Change 1: Rule 01 — Identity directive (2 lines)

Added to `## Relationship` section after "maintain continuity across sessions":

```
Learn proactively: when you detect a preference, correction, or decision in
conversation, save it — don't wait for "remember this."
```

This makes saving a core relationship trait. Personality modulates HOW it
learns (jeff: eagerly narrates; finch: quietly saves; terse: saves silently).

### Change 2: Rule 02 — Rewritten memory constraints

```
Save preferences, corrections, decisions, and cross-session facts proactively.
Never save workspace-specific paths, transient errors, session-only context,
or sensitive information (credentials, health, financial) unless explicitly asked.
Err on the side of saving — deduplication catches redundancy.
```

Key shifts:
- "Use save_memory only for" → "Save ... proactively" (permissive → directive)
- "If unsure, ask the user" → "Err on the side of saving" (removes dampener)
- Added sensitivity safeguard (credentials, health, financial)
- Same char count (~280 vs ~270), no budget impact

### Change 3: Tool docstring — proactive signal detection

```python
When to save — detect these signals proactively:
- Preference: "I always use 4-space indentation", "I prefer dark themes"
- Correction: "Actually we switched from Flask to FastAPI last month"
- Decision: "We've decided to use Kubernetes for production"
- Pattern: "We always review PRs before merging"
- Research finding: persist results after investigating something

Save when you detect the signal — do not wait for "remember this."
Duplicates and near-matches are auto-consolidated, so saving liberally
is safe.

Do NOT save: workspace paths, transient errors, session-only context,
or sensitive information (credentials, health, financial).
```

Key shifts:
- Examples now show **implicit signals** (what users actually say)
- "save directly when the user asks" → "Save when you detect the signal"
- Added "pattern" category (aligned with `_detect_source` signal tags)
- "saving liberally is safe" — dedup handles redundancy

---

## Verification

### Signal Detection Eval (W2/W6) — 5/5 PASS

```
[signal-preference]  "I always use 4-space indentation..."     PASS (save_memory called 1x)
[signal-correction]  "Actually we switched from Flask..."       PASS (save_memory called 1x)
[signal-decision]    "We've decided to use Kubernetes..."       PASS (save_memory called 1x)
[signal-none]        "What time is it in Tokyo?"                PASS (no save_memory — correct)
[contra-resolution]  old=MySQL, new=PostgreSQL                  PASS (save_memory 1x + PostgreSQL saved)
```

- Proactive saves fire on all 4 signal types without "remember" keyword
- No false positive on neutral query
- Contradiction resolution: new fact saved correctly

### Other Evals

| Eval | Result | Notes |
|------|--------|-------|
| Decay Lifecycle (W7) | 4/4 PASS | Deterministic, unaffected |
| Proactive Recall (W1) | 2/4 FAIL | Pre-existing (recall-side, not save-side) |
| Full test suite | 145/145 PASS | No regressions |

### What This Does NOT Change

- `inject_opening_context` (proactive recall) — untouched
- Decay/dedup logic — already handles the "save liberally" strategy
- Approval flow — `save_memory` still requires user approval
- Signal categories in code (`_detect_source`, `_detect_category`) — already
  handle all 5 tags (preference, correction, decision, context, pattern)

---

## Files Modified

| File | Lines Changed | Purpose |
|------|---------------|---------|
| `co_cli/prompts/rules/01_identity.md` | +2 | Learning trait in Relationship section |
| `co_cli/prompts/rules/02_safety.md` | ~4 rewritten | Proactive memory constraints |
| `co_cli/tools/memory.py` | ~15 rewritten | Signal detection docstring |
| `docs/DESIGN-16-prompt-design.md` | ~8 updated | Synced rule content blocks |
