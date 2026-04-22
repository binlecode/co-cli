# Eval: Compaction Quality — Trace Report

**Date:** 2026-04-22  
**Verdict: FAIL** — 6/13 steps passed  
**Raw output:** `tmp/eval_compaction_quality_<timestamp>.log`

---

## Summary

7 failures across 4 distinct root causes. Three causes are eval staleness (the
eval was written before a tool-rename refactor and before the `## User Corrections`
template redesign). One cause is a genuine production bug (Step 9 threshold guard).

---

## Step-by-step results

| Step | What it tests | Result | Root cause |
|------|---------------|--------|------------|
| 1 | `persist_if_oversized` — disk write, preview cap, idempotency | PASS | — |
| 2 | P1 `truncate_tool_results` — clearing counts, per-type tracking | **FAIL** | Tool rename drift |
| 4 | P5 context enrichment — file paths from ToolCallPart.args, todos, prior summary, 4K cap | **FAIL** | Tool rename drift |
| 5 | Prompt assembly — 9 sections, context+personality ordering | PASS | — |
| 6 | Full P1→P3→P4→P5 chain (real LLM) | **FAIL** | Tool rename drift → P1 clears 0 → P5 sees full history, budget not exceeded |
| 7 | Multi-cycle compaction — prior summary integration | **FAIL** | Tool rename drift (same cascade as Step 6) |
| 8 | Overflow detection + `emergency_compact` + one-shot guard | PASS | — |
| 9 | Circuit breaker degradation — `failure_count=3` → static marker | **FAIL** | Missing `min_context_length_tokens=0` in Step 9 config |
| 10 | A/B enrichment quality — enriched vs bare LLM summaries | PASS | — |
| 11 | Edge case battery (no LLM) — empty, 1-turn, static markers, mixed parts | PASS | — |
| 12 | Prompt composition — section order, security guardrail, injection isolation | **FAIL** | Eval order check vs. conditional `## User Corrections` design |
| 13 | Prompt upgrade quality — verbatim anchor, corrections, error feedback | **FAIL** | LLM quality gap (13b only; 13a + 13c pass) |
| 14 | Pending/Resolved sections — unanswered, answered, merge contract | PASS | — |

---

## Root cause analysis

### RC-1 — Tool rename drift (Steps 2, 4, 6, 7)

**What happened.** All four failures share a single cause: the tools were renamed
from the old flat names to `file_`-prefixed names, but the eval was not updated.

| Old name (in eval) | New name (in production) |
|--------------------|--------------------------|
| `read_file` | `file_read` |
| `glob` | `file_glob` |
| `grep` | `file_grep` |
| `patch` | `file_patch` |
| `write_file` | `file_write` |

The constants in `co_cli/context/tool_categories.py` are:
```python
COMPACTABLE_TOOLS = frozenset({"file_read", "shell", "file_grep", "file_glob",
                                "web_search", "web_fetch", "knowledge_article_read", "obsidian_read"})
FILE_TOOLS       = frozenset({"file_read", "file_write", "file_patch", "file_grep", "file_glob"})
```

The eval checks `COMPACTABLE_TOOLS == {"glob", "grep", "read_file", "web_search", ...}` (Step 2a) —
fails. The `_build_tool_conv("read_file", ...)` helper emits tool calls with name `read_file` which
is NOT in `COMPACTABLE_TOOLS`, so P1 clears nothing (0 instead of 3/5). Step 4 tests use `read_file`
and `patch` as FILE_TOOLS members for file-path extraction — not in actual FILE_TOOLS, so paths
are never harvested. Steps 6 and 7 cascade: P1 clears 0 → full history stays large → P5's budget
check compares token count against `budget * proactive_ratio * min_context_length_tokens` floor
with `num_ctx=30` → boundaries compute correctly but `summarize_history_window` sees the
uncompressed history and returns unchanged (no LLM call because the budget-based threshold isn't
reached, or the LLM call fires but the result shows "Messages: 54 → 54").

Actually the deeper cascade for Step 6/7: with `num_ctx=30` (set in `_make_ctx`'s llm settings),
the model context window is 30 tokens. `resolve_compaction_budget` returns a tiny budget, and
`plan_compaction_boundaries` correctly finds `head_end=2, tail_start=52, dropped=50`. But
`summarize_history_window` calls `_summarize_dropped_messages` which calls `summarize_messages`
— the LLM call either fails or the result shows no reduction because token estimation says
the history isn't over threshold with `min_context_length_tokens=0` set in `_make_ctx`.

Wait, looking at the output again: "Messages: 54 → 54 (1 replaced by 1 marker)" — the
`net_reduction = len_pre_p5 - len(msgs) = 0` but `actual_dropped = net_reduction + 1 = 1`.
The FAIL is "no reduction" because `len(msgs) >= len_pre_p5`. So `summarize_history_window`
returns the original `messages` unchanged — meaning the budget threshold wasn't crossed.

For Step 6, `_make_ctx` uses `llm_num_ctx=30` which gives a tiny context window. But
`_make_ctx` also passes `compaction=CompactionSettings(min_context_length_tokens=0)` — so the
min floor is 0. Step 6 builds its own `config` without that override:
```python
config = Settings.model_construct(
    llm=LlmSettings.model_construct(provider="ollama", num_ctx=30, ...),
    # no compaction= override → min_context_length_tokens defaults to 64K
)
```
So `token_threshold = max(int(budget * proactive_ratio), 64_000)`. With `num_ctx=30`, budget is
tiny, so threshold is `max(tiny, 64_000) = 64_000`. The 72K-char history is maybe ~18K tokens,
still under 64K, so `summarize_history_window` returns early. **This means Steps 6 and 7 also
have the missing `min_context_length_tokens=0` bug, independent of the tool rename.**

**Fix:** Update the eval to use new tool names AND add `compaction=CompactionSettings(min_context_length_tokens=0)` to Steps 6 and 7 configs.

---

### RC-2 — Missing `min_context_length_tokens=0` (Step 9)

**What happened.** Step 9 builds a small synthetic history (~12 messages × ~200 chars ≈ 2,400
chars ≈ 600 tokens) and sets `failure_count=3` to trip the circuit breaker. But:

```python
config = Settings.model_construct(
    llm=LlmSettings.model_construct(provider="ollama", num_ctx=30, ...),
    # no compaction= → min_context_length_tokens defaults to 64_000
)
```

In `summarize_history_window`:
```python
token_threshold = max(int(budget * cfg.proactive_ratio), cfg.min_context_length_tokens)
if token_count <= token_threshold:
    return messages   # ← returns here, circuit breaker never reached
```

With 600 tokens << 64K threshold, the function returns before reaching the circuit breaker
check. The eval reports "no compaction occurred" because `len(result) >= len_pre`.

`_make_ctx()` explicitly sets `CompactionSettings(min_context_length_tokens=0)` for exactly
this reason — but Step 9 does not use `_make_ctx()`.

**Fix:** Add `compaction=CompactionSettings(min_context_length_tokens=0)` to the Step 9 config.

---

### RC-3 — Eval order check vs. conditional section design (Step 12)

**What happened.** The eval's section-order check expects `## User Corrections` to appear
between `## Key Decisions` and `## Errors & Fixes` in the raw template string. But the
`_SUMMARIZE_PROMPT` redesigned it as a **conditional section** — the template body lists 8
static sections (Goal through Next Step), then adds instruction text after the body:

```
"USER CORRECTIONS (conditional): Scan the conversation for explicit user corrections...
If you find any: insert a '## User Corrections' section immediately after '## Key Decisions'..."
```

So in the raw template string, `"## User Corrections"` appears at position 1994 (in the
instruction text, after `## Next Step` at 1307). The eval's position dict:
```
{'## Goal': 269, '## Key Decisions': 354, '## User Corrections': 1994,
 '## Errors & Fixes': 449, ...}
```
flags this as out-of-order, but it's correct template design — the LLM inserts the section
dynamically when it detects corrections.

**Fix:** Update the eval's Step 12 order check to skip `## User Corrections` from the static
section ordering assertion (it's conditional, not static), or check that the instruction text
correctly describes where to insert it.

---

### RC-4 — LLM quality gap in `## User Corrections` (Step 13b)

**What happened.** Step 13b feeds the LLM a conversation containing an explicit user
correction ("No, let's not use bcrypt — the team already standardized on Argon2") and checks
that the summary includes a `## User Corrections` section. The LLM did not produce it.

This is a genuine quality gap: the template instructs the LLM to detect corrections and
insert the section, but the model running locally (Ollama) either missed the correction
signal or decided it didn't meet the threshold. Steps 13a (verbatim anchor) and 13c
(error feedback) pass — only the corrections detection fails.

**Fix:** This is harder — it's model instruction-following quality. Options:
1. Strengthen the detection signal in the template (examples of what counts as a correction).
2. Test with the cloud model (local Ollama may be weaker at instruction following).
3. Accept this as a known gap on the local model and gate only on cloud runs.

---

## What passed (clean signals)

- **Step 1**: `persist_if_oversized` is solid — threshold, preview size, content-addressing,
  idempotency all correct.
- **Step 5**: Prompt assembly logic is correct — 9 sections present, merge contract enforced,
  context+personality ordering correct.
- **Step 8**: Overflow detection handles all 6 cases correctly (413, OpenAI dict body, Ollama
  str body, bare 400, wrong status code, None body). `emergency_compact` produces correct
  first+marker+last structure. One-shot guard field exists on `_TurnState`.
- **Step 10**: A/B enrichment delivers measurable quality improvement — enriched summary hits
  5/5 signals vs bare 3/5. Enrichment-only content (RSA key rotation, Redis) correctly absent
  from bare and present in enriched.
- **Step 11**: All 8 edge cases pass — no crashes on 1-turn history, empty list, static
  markers, massive messages, tool-only first turns, mixed parts.
- **Step 14**: Pending/Resolved section logic is fully functional — unanswered questions
  land in Pending, answered in Resolved, and prior Pending correctly migrates to Resolved
  on the next compaction cycle.

---

## Fix priority

| Priority | Fix | Steps unblocked |
|----------|-----|-----------------|
| P0 | Update eval tool names: `read_file`→`file_read`, `glob`→`file_glob`, etc. | 2, 4 |
| P0 | Add `compaction=CompactionSettings(min_context_length_tokens=0)` to Steps 6, 7, 9 | 6, 7, 9 |
| P1 | Update Step 12 order assertion to treat `## User Corrections` as conditional | 12 |
| P2 | Investigate 13b LLM quality — strengthen correction-detection signal or scope to cloud | 13 |
