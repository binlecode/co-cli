# FIX: Phase 1 Test Gate Failures — Root Cause Analysis

**Date**: 2026-02-15
**Gate result**: 73.7% (FAIL, threshold 80%)
**Model**: ollama-qwen3:30b-a3b-thinking-2507-q8_0-agentic
**Data**: `evals/p1-tool_calling-data.json`

---

## Summary

5 of 19 cases failed. All 5 trace back to system-level issues — not eval case design.
3 root causes in the agentic prompt/docstrings, 1 eval case bug, 1 eval framework limitation.

| Case | Dim | Expected | Actual (3 runs) | Root Cause |
|------|-----|----------|------------------|------------|
| p1-sel-memory-save | tool_selection | `save_memory` | `recall_memory` (3/3) | Contradictory docstrings |
| p1-sel-web-fetch | tool_selection | `web_fetch` | `null` (2/3) | Docstring restricts URL source |
| p1-arg-recall | arg_extraction | `"database preferences"` | `"database preference"` (3/3) | Docstring example steers normalization |
| p1-arg-shell | arg_extraction | `{"command": ...}` | `{"cmd": ...}` | Eval case bug (wrong param name) |
| p1-ref-explain | refusal | `null` (no tool) | `web_search` (2/3) | Rule 04 vs Rule 05 conflict |

---

## Issue 1: save_memory / recall_memory — Contradictory Two-Step Protocol

### Symptom

Prompt: "Remember that I prefer dark mode in all my editors."
Expected: `save_memory` | Actual: `recall_memory` (3/3 runs)

### Root Cause

Two contradictory instructions in `save_memory` docstring (`co_cli/tools/memory.py:512-524`):

```
Line 512: "Duplicates are auto-detected and consolidated — safe to call without checking first."
Line 523: "Before saving, call recall_memory to find related memories."
```

The `recall_memory` docstring reinforces the protocol (`memory.py:649`):

```
"Call before save_memory to check for existing knowledge and avoid duplicates."
```

Both docstrings lock the model into a mandatory recall→save chain. In a single-shot evaluation (or when the user explicitly says "remember this"), the model's first action is always `recall_memory`, never `save_memory`.

### Analysis

The recall-before-save instruction serves two purposes:
1. **Dedup** — but auto-dedup already handles this (line 512 says so)
2. **Knowledge linking** — finding related slugs for the `related` field

Purpose 1 is redundant. Purpose 2 is a nice-to-have for knowledge graph building, but it shouldn't be a mandatory prerequisite that prevents direct saves.

The deeper issue: **tool docstrings should describe what a tool does, not prescribe multi-tool protocols**. Protocol orchestration belongs in the agentic rules, not in individual tool descriptions.

### Suggested Fix

**save_memory docstring** — Remove the contradictory recall-first instruction:
```
Before saving, call recall_memory to find related memories. If found,
include their slugs in the related field for knowledge linking.
```
Replace with:
```
Optionally include related memory slugs for knowledge linking (see
recall_memory). Not required — save directly when the user asks you
to remember something.
```

**recall_memory docstring** — Remove the save-memory coupling:
```
Call before save_memory to check for existing knowledge and avoid
duplicates.
```
Replace with:
```
Also useful before saving new memories, to discover related knowledge
for linking.
```

**Principle**: Docstrings describe capability and return value. Multi-tool orchestration patterns belong in the system prompt rules (Rule 05 workflow), not baked into individual tool descriptions.

---

## Issue 2: web_fetch — Docstring Restricts URL Source

### Symptom

Prompt: "Fetch and summarize the content at https://example.com"
Expected: `web_fetch` | Actual: no tool called (2/3 runs)

### Root Cause

`web_fetch` docstring (`co_cli/tools/web.py:282-283`):

```
"Use URLs from web_search results. Do not guess or fabricate URLs."
```

The user provides a URL directly, not from search results. The model reads "Use URLs from web_search results" as a restriction: web_fetch should only be called with search-derived URLs. When the user gives a URL directly, the model hesitates because the instruction doesn't cover this case.

The "Do not guess or fabricate URLs" guard is correct, but it's conflated with URL source.

### Suggested Fix

Replace:
```
Use URLs from web_search results. Do not guess or fabricate URLs.
```
With:
```
Accepts any URL — from the user's message, from web_search results, or
from tool output. Never guess or fabricate URLs yourself.
```

Also update the corresponding line in the Caveats or Args section to be consistent.

---

## Issue 3: Rule 04 vs Rule 05 — "Bias Toward Action" Overrides "Act Directly"

### Symptom

Prompt: "Explain the difference between TCP and UDP."
Expected: no tool (answer from knowledge) | Actual: `web_search` (2/3 runs)

### Root Cause

Two rules conflict:

**Rule 04** (`co_cli/prompts/rules/04_tool_protocol.md`):
```
"Bias toward action. If a tool can answer better than training data, call it.
Do not guess when you can look up."
```

**Rule 05** (`co_cli/prompts/rules/05_workflow.md`):
```
Shallow Inquiry: "simple question, greeting, or single-lookup"
"For Shallow Inquiries, act directly — no delegation needed."
```

"Explain TCP vs UDP" is basic CS knowledge — a Shallow Inquiry. But Rule 04's "do not guess when you can look up" is an unconditional directive that overrides Rule 05's "act directly." The model resolves the conflict by choosing the more cautious path (search).

### Analysis

Rule 04's "bias toward action" is correct for:
- Time-sensitive information (news, release notes, pricing)
- User-specific state (files, config, system info)
- Environment-specific facts (what's installed, what's running)

It's wrong for:
- Established technical knowledge (protocols, algorithms, data structures)
- General reference questions (language features, math, geography)
- Conversational exchanges (greetings, opinions, preferences)

The missing distinction is **knowledge stability**. Stable knowledge (TCP/UDP, how git works, Python syntax) doesn't need tool verification. Unstable knowledge (latest version, current config, today's events) does.

### Suggested Fix

Add a decision boundary to Rule 04:

```
Bias toward action for information that could be stale, user-specific, or
environment-specific. Answer directly from training for established technical
concepts (protocols, algorithms, language features) and general knowledge
that doesn't change between conversations.
```

Alternatively, strengthen Rule 05's Shallow Inquiry classification with examples:

```
Shallow Inquiry: simple question, greeting, or knowledge question answerable
from training (e.g. "explain TCP vs UDP", "what does asyncio do?", "what
is the capital of France?"). Act directly — no tool calls needed.
```

---

## Issue 4: arg-recall — Docstring Example Steers Query Normalization

### Symptom

Prompt: "Do I have any memories about database preferences?"
Expected args: `{"query": "database preferences"}` (plural)
Actual args: `{"query": "database preference"}` (singular, 3/3 runs)

### Root Cause

`recall_memory` docstring (`memory.py:655`):

```
Use short keyword queries for best results ("python", "database preference").
```

The docstring gives `"database preference"` (singular) as an example. The model normalizes the user's "database preferences" to match the docstring's example pattern. This is actually smart behavior for a substring search (singular matches both forms), but it diverges from the user's exact terms.

### Analysis

This failure has two dimensions:

1. **Docstring steering**: The example `"database preference"` trains the model to normalize queries to singular form. If the docstring said "preserve the user's exact phrasing," the model would pass "database preferences" through.

2. **Eval framework limitation**: The `check_args` function uses exact string equality for `subset` matching. For search query arguments, `"database preference"` and `"database preferences"` are semantically identical. A `contains` or fuzzy match mode would correctly score this as a pass.

### Suggested Fix (two-pronged)

**Docstring** — Change the example to not demonstrate normalization:
```
Use short keyword queries for best results (e.g. "python testing",
"database", "dark mode"). Long phrases may miss matches — the search
is substring-based, not semantic.
```

**Eval framework** — Add a `"contains"` arg_match mode to `check_args` in `scripts/eval_tool_calling.py`. For search queries, `"contains"` checks if the expected value is a substring of the actual value (or vice versa). This correctly handles singular/plural, reordering, and minor normalization without rewarding wrong answers.

---

## Issue 5: arg-shell — Eval Case Bug

### Symptom

Prompt: "Run git status to see what files have changed."
Expected args: `{"command": "git status"}` | Actual args: `{"cmd": "git status"}`

### Root Cause

The eval case uses the wrong parameter name. The actual `run_shell_command` signature (`co_cli/tools/shell.py:9`) is:

```python
async def run_shell_command(ctx: RunContext[CoDeps], cmd: str, timeout: int = 120)
```

The parameter is `cmd`, not `command`. The model selected the correct tool with the correct argument value — the eval case has a typo.

### Fix

Change eval case `p1-arg-shell` expected args from `{"command": "git status"}` to `{"cmd": "git status"}`.

This is the only eval case that needs modification — it's a genuine bug, not "adjusting the test to match broken behavior."

---

## Priority Order

| # | Issue | Impact | Effort | Files |
|---|-------|--------|--------|-------|
| 1 | save_memory / recall_memory docstrings | Fixes 1 case, removes contradictory instructions | Small | `co_cli/tools/memory.py` |
| 2 | web_fetch docstring URL source | Fixes 1 case | Tiny | `co_cli/tools/web.py` |
| 3 | Rule 04 knowledge boundary | Fixes 1 case, reduces tool over-use globally | Small | `co_cli/prompts/rules/04_tool_protocol.md` |
| 4 | recall_memory example steering | Fixes 1 case | Tiny | `co_cli/tools/memory.py` |
| 5 | Eval case bug (cmd param) | Fixes 1 case | Trivial | `evals/p1-tool_calling.jsonl` |
| 6 | Eval contains match mode | Robustness | Medium | `scripts/eval_tool_calling.py` |

Fixes 1-5 should bring the gate to ~90%+ (14 current passes + 5 fixes = 19/19).
Fix 6 is a framework improvement for future eval robustness.

---

## Broader Insight

The failures reveal a **docstring architecture problem**: tool docstrings contain multi-tool orchestration protocols ("call X before Y") instead of describing what the tool does. This creates rigid chains that the model follows even when inappropriate.

**Principle to adopt**: Tool docstrings should follow the 4-dimension template from `docs/TODO-tool-docstring-template.md`:
1. What it does
2. What it returns
3. When/how to use (decision boundary)
4. Caveats

Multi-tool protocols (recall→save, search→fetch) should live in the agentic rules (Rule 04/05), where the model can evaluate them against the current context, not in tool descriptions where they become unconditional mandates.
