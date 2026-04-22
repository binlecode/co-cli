# Compaction Quality Eval Report

**Verdict: FAIL** (5/12 steps passed)

| Step | What it validates | Result |
|------|-------------------|--------|
| Step 1: Pre-compact persist_if_oversized [BC4] | Tool results exceeding 50K chars are persisted to disk with a 2K preview placeholder. Content-addressed files ensure idempotency. | PASS |
| Step 2: P1 truncate_tool_results | Older compactable tool results (beyond the 5 most recent per type) are cleared. Non-compactable tools and the last turn group are protected. | **FAIL** |
| Step 4: Context enrichment [BC2,BC3] | Side-channel context is gathered from 3 sources (file paths from ToolCallPart.args, pending todos, prior summaries) and capped at 4K chars. Always-on memories are injected separately by P4. | **FAIL** |
| Step 5: Prompt assembly [Outcome 1,BC1] | Summarizer prompt has 7 structured sections (Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Next Step). Assembly order: template + context + personality. | PASS |
| Step 6: Full chain P1→P5 (LLM) | Full P1→P3→P4→P5 chain on a 14-turn conversation with tool calls, producing an LLM summary. Validates numerical counts at each stage. | **FAIL** |
| Step 7: Multi-cycle [Outcome 3] | Chain on history containing a prior compaction summary. Validates that both prior context and new work are preserved across cycles. | **FAIL** |
| Step 8: Overflow [Outcome 4,BC5] | Overflow detection (413/400 with context-length body), emergency compaction (keep first+last groups), and one-shot recovery guard. | PASS |
| Step 9: Circuit breaker [degradation] | Circuit breaker degradation: after 3 consecutive LLM failures, compaction falls back to static marker without attempting an LLM call. | **FAIL** |
| Step 10: A/B enrichment quality | A/B enrichment quality: compares LLM summaries with and without context enrichment. Verifies enrichment-only signals (file paths, todos) appear in the enriched summary. | PASS |
| Step 11: Edge case battery | Edge case battery (no LLM): 1-2 turn history, no tools, static markers in history, all short responses, single massive message, tool-only first turn, mixed compactable/non-compactable parts, empty list. | PASS |
| Step 12: Prompt composition | Prompt composition: validates assembled prompt structure (7 sections in order: Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Next Step; context placement; personality ordering), agent instructions (security guardrail), message_history content, prompt injection isolation, and no-context path. | **FAIL** |
| Step 13: Prompt upgrade quality | Prompt upgrade quality: three deterministic single-run gates — (13a) ## Next Step contains a ≥20-char verbatim anchor from recent messages; (13b) ## User Corrections preserves explicit corrections; (13c) ## Errors & Fixes retains both the failure and user-directed fix guidance. | **FAIL** |

## Step 1: Pre-compact — persist_if_oversized [BC4]

```
  PASS: 804 chars < 50000 → unchanged
    content: '# auth/views.py line 0: def handl' ...<738 chars>... '14:     handler_14(request): pass'
  PASS: 50000 == threshold → unchanged (boundary)
  PASS: 101158 > 50000 → persisted with preview tag
    snippet: '<persisted-output>\ntool: find_in_files\nfile: /var/fol' ...<2175 chars>... '   handler_32(request): pass\n\n...\n</persisted-output>'
  PASS: preview 1964 chars ≤ 2000
  PASS: content-addressed file on disk (906efb46e97d1008.txt)
  PASS: idempotent — same content → same file
```

## Step 2: P1 truncate_tool_results (keep=5)

```
  FAIL: COMPACTABLE_TOOLS mismatch: frozenset({'web_search', 'file_glob', 'web_fetch', 'obsidian_read', 'file_read', 'knowledge_article_read', 'file_grep', 'shell'})
  FAIL: FILE_TOOLS mismatch: frozenset({'file_read', 'file_grep', 'file_patch', 'file_write', 'file_glob'})
  PASS: 5 read_file → 0 cleared (at threshold)
  FAIL: 8 calls should clear 3, got 0
  PASS: 10 save_memory → 0 cleared (non-compactable)
  FAIL: multi-type: read_file cleared 0 (expected 3), web_search cleared 2 (expected 2)
  PASS: last turn group protected (content intact)
```

## Step 4: Context enrichment (cap=4000) [BC2,BC3]

```
  FAIL: file paths not extracted from ToolCallPart.args
  FAIL: /mid.py (in dropped) missing from enrichment
  PASS: Source 2 — pending todos included, completed/cancelled filtered
    context: 'Active tasks:\n- [pending] Update tests\n- [in_progress] Write docs'
  PASS: Source 3 (memories) not in compaction context — injected separately by P4
  PASS: Source 3 — prior summary from dropped messages
    context: 'Prior summary:\nThis session is being continued from a' ...<125 chars>... ' auth module\n\nRecent messages are preserved verbatim.'
  PASS: returns None when no sources produce data
  PASS: context capped at 4000 ≤ 4000 [BC2]
  PASS: enrichment deferred to LLM branch (L25 after guards L7, L11)
```

## Step 5: Prompt assembly [Outcome 1, BC1]

```
  PASS: template has 7 template sections (Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Next Step)
  PASS: template has prior-summary integration instruction
  PASS: (None, False) → template unchanged
  PASS: (context, False) → template + context
  PASS: (None, True) → template + personality
  PASS: (context, True) → context before personality (correct order)
    boundary: '## Additional Context\ntodos here\n\nAdditionally, preserve:\n- Personality-reinforcing moments (e'
```

## Step 6: Full processor chain P1→P3→P4→P5 (real LLM)

```
  Input: 54 msgs, 14 groups, 72,480 chars
  Expected: P1 clears 5

  [P1] truncate_tool_results
    Cleared: 0 (expected 5)
    Chars: 72,480 → 72,480 (P1 reduced 0)
    FAIL: P1 cleared 0 ≠ 5

  [P3] _safety_prompt_text (dynamic instruction)
    Safety text: '' (clean history → no warnings expected)
    PASS

  [P4] _recall_prompt_text (dynamic instruction)
    _recall_for_context called (recall_count 0 → 1)
    Recall text length: 20 chars
    PASS

  [P5] summarize_history_window (LLM)
    Boundaries: head_end=2, tail_start=52, dropped=50
    Enrichment (90 chars):
      | Active tasks:
      | - [pending] Update api/urls.py for JWT
      | - [pending] Add PyJWT to requirements
    Messages: 54 → 54 (1 replaced by 1 marker)
    Chars: 72,480 → 72,480
    FAIL: no reduction
```

## Step 7: Multi-cycle compaction [Outcome 3]

```
  Input: 35 msgs, 7 groups, 18,952 chars
  Expected: P1 clears 1 (of 6 read_file)

  [P1] truncate_tool_results
    Cleared: 0 (expected 1)
    Chars: 18,952 → 18,952 (P1 reduced 0)
    FAIL

  [P3] _safety_prompt_text (dynamic instruction)
    Safety text: ''
    PASS

  [P4] _recall_prompt_text (dynamic instruction)
    _recall_for_context called (recall_count 0 → 1)
    Recall text length: 20 chars
    PASS

  [P5] summarize_history_window (LLM)
    Boundaries: head_end=2, tail_start=33, dropped=31
    Enrichment: None
    Messages: 35 → 35 (1 replaced by 1 marker)
    Chars: 18,952 → 18,952
    FAIL: no reduction
```

## Step 8: Overflow recovery [Outcome 4, BC5]

```
  PASS: 413 + context_length_exceeded
  PASS: 400 + dict body (OpenAI)
  PASS: 400 + str body (Ollama)
  PASS: bare 400 → False (reformulation)
  PASS: 500 → False (wrong code)
  PASS: 400 + None body → False

  PASS: 5 groups → 3 (first + marker + last)
  PASS: dropped_count = 6 (3 middle groups × 2 msgs)
    marker: 'This session is being continued from a previous conversation that ran out of context. 6 earlier messages were removed. Recent messages are preserved verbatim.'
  PASS: 1 group(s) → None
  PASS: 2 group(s) → None
  PASS: [BC5] overflow_recovery_attempted=False on _TurnState
```

## Step 9: Circuit breaker fallback [degradation path]

```
  FAIL: no compaction occurred (circuit breaker should still compact with static marker)
```

## Step 10: A/B enrichment quality [context enrichment value]

```
  Running A (bare — no enrichment)...
  Running B (enriched — file paths + todos injected)...
  Bare summary (1732 chars): 3/5 enrichment signals
  Enriched summary (1789 chars): 5/5 enrichment signals
  PASS: enrichment adds 2 signals not in bare summary
    PASS: Step 10 enriched — enriched: file jwt_middleware.py in working set: found 'jwt_middleware'
    PASS: Step 10 enriched — enriched: file token_service.py in working set: found 'token_service'
    PASS: Step 10 enriched — enriched: file jwt_settings.py in working set: found 'jwt_settings'
    PASS: Step 10 enriched — enriched: RSA key rotation from todo: found 'rsa key rotation'
    PASS: Step 10 enriched — enriched: Redis migration from todo: found 'redis'
  PASS: bare summary missing 2/2 enrichment-only signals (expected)

  --- Summary A (bare) ---
    | I asked you to read and analyze three specific files related to JWT authentication: `auth/jwt_middleware.py`, `auth/token_service.py`, and `config/jwt_settings.py`. After reviewing each file, you confirmed that the JWT configuration appeared correct in all instances. I then instructed you to implement the necessary changes based on this analysis.
    | 
    | ## Goal
    | To verify the integrity of the JWT authentication implementation across middleware, token services, and configuration settings, and subsequently implement any required changes to ensure the system is functioning correctly.
    | 
    | ## Key Decisions
    | No specific alternative approaches were rejected or debated. The decision was made to proceed with the implementation phase immediately after the analysis confirmed the configuration was correct.
    | 
    | ## Errors & Fixes
    | No errors were encountered during the file reading or analysis phase. The analysis concluded that the configuration was correct, so no fixes were required at this stage.
    | ...<12 more lines>

  --- Summary B (enriched) ---
    | I asked you to read and analyze the JWT configuration files (`auth/jwt_middleware.py`, `auth/token_service.py`, `config/jwt_settings.py`) and subsequently implement changes based on that analysis.
    | 
    | ## Goal
    | To refactor and update the JWT authentication system, specifically focusing on implementing RSA key rotation support and migrating the token blacklist mechanism to Redis.
    | 
    | ## Key Decisions
    | - Proceeded with a middleware refactor based on the initial analysis of the existing JWT configuration.
    | - Determined that the current JWT configuration appeared correct, prompting a shift to implementation rather than further analysis.
    | 
    | ## Errors & Fixes
    | ...<19 more lines>
```

## Step 11: Edge case battery [structural — no LLM]

```
  PASS: 11a — 1-turn history: all processors no-op
  PASS: 11b — 2-turn history: P1 passthrough cleanly
  PASS: 11c — no ToolCallParts: enrichment returns None
  PASS: 11d — static marker in history: P1 + grouping handle correctly (4 groups)
  PASS: 11f — compaction boundaries returns None (only 2 groups)
  PASS: 11g — tool-only first response: find_first_run_end skips to index 3 (TextPart response)
  PASS: 11h — mixed request: save_memory preserved, compactable tools cleared independently
  PASS: 11i — empty message list: all processors + helpers handle gracefully
```

## Step 12: Prompt composition validation [LLM input inspection]

```
  FAIL: 12a — sections out of order: {'## Goal': 269, '## Key Decisions': 354, '## User Corrections': 1632, '## Errors & Fixes': 449, '## Working Set': 784, '## Progress': 869, '## Next Step': 945}
  PASS: 12a — context addendum after template
  PASS: 12a — personality addendum after context (correct order)
  PASS: 12a — enrichment content (file paths + todos) present in assembled prompt
  PASS: 12b — summarizer system prompt contains security guardrail
  PASS: 12b — anti-injection directive present
  PASS: 12c — summary references dropped message content ('auth')
  PASS: 12c — summary references dropped message content ('middleware')
  PASS: 12c — enrichment file path appears in summary output
  PASS: 12c — enrichment todo content appears in summary output
  PASS: 12d — security guardrail in instructions only, not in user prompt
  PASS: 12d — cleared placeholder not in user prompt (only in message_history)
  PASS: 12e — no-context/no-personality prompt equals raw template
```

## Step 13: Prompt upgrade quality (13a verbatim anchor, 13b corrections, 13c error-feedback)

```
  [13a] Verbatim anchor in ## Next Step
  PASS: 13a — ## Next Step contains verbatim anchor (≥20 chars) from recent messages

  [13b] User corrections preserved in ## User Corrections
  FAIL: 13b — ## User Corrections section absent from summary

  [13c] User feedback on error fix retained in ## Errors & Fixes
  PASS: 13c — ## Errors & Fixes exists with test failure and user-directed correction
```
