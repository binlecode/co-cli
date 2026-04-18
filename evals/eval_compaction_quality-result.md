# Compaction Quality Eval Report

**Verdict: FAIL** (6/12 steps passed)

| Step | What it validates | Result |
|------|-------------------|--------|
| Step 1: Pre-compact persist_if_oversized [BC4] | Tool results exceeding 50K chars are persisted to disk with a 2K preview placeholder. Content-addressed files ensure idempotency. | PASS |
| Step 2: P1 truncate_tool_results | Older compactable tool results (beyond the 5 most recent per type) are cleared. Non-compactable tools and the last turn group are protected. | **FAIL** |
| Step 3: P2 compact_assistant_responses [BC6,BC7] | Large assistant TextPart/ThinkingPart content in older messages is capped at 2500 chars with proportional head(20%)/tail(80%) truncation (aligned with gemini-cli). Tool args and returns are untouched. | PASS |
| Step 4: Context enrichment [BC2,BC3] | Side-channel context is gathered from 4 sources (file paths from ToolCallPart.args, pending todos, always-on memories, prior summaries) and capped at 4K chars. | **FAIL** |
| Step 5: Prompt assembly [Outcome 1,BC1] | Summarizer prompt has 5 structured sections (Goal, Key Decisions, Working Set, Progress, Next Steps). Assembly order: template + context + personality. | PASS |
| Step 6: Full chain P1→P5 (LLM) | Full P1-P5 chain on a 14-turn conversation with tool calls, producing an LLM summary. Validates numerical counts at each stage. | **FAIL** |
| Step 7: Multi-cycle [Outcome 3] | Chain on history containing a prior compaction summary. Validates that both prior context and new work are preserved across cycles. | **FAIL** |
| Step 8: Overflow [Outcome 4,BC5] | Overflow detection (413/400 with context-length body), emergency compaction (keep first+last groups), and one-shot recovery guard. | **FAIL** |
| Step 9: Circuit breaker [degradation] | Circuit breaker degradation: after 3 consecutive LLM failures, compaction falls back to static marker without attempting an LLM call. | **FAIL** |
| Step 10: A/B enrichment quality | A/B enrichment quality: compares LLM summaries with and without context enrichment. Verifies enrichment-only signals (file paths, todos) appear in the enriched summary. | PASS |
| Step 11: Edge case battery | Edge case battery (no LLM): 1-2 turn history, no tools, static markers in history, all short responses, single massive message, tool-only first turn, mixed compactable/non-compactable parts, empty list. | PASS |
| Step 12: Prompt composition | Prompt composition: validates assembled prompt structure (section order, context placement, personality ordering), agent instructions (security guardrail), message_history content, prompt injection isolation, and no-context path. | PASS |

## Step 1: Pre-compact — persist_if_oversized [BC4]

```
  PASS: 804 chars < 50000 → unchanged
    content: '# auth/views.py line 0: def handl' ...<738 chars>... '14:     handler_14(request): pass'
  PASS: 50000 == threshold → unchanged (boundary)
  PASS: 101158 > 50000 → persisted with preview tag
    snippet: '<persisted-output>\ntool: find_in_files\nfile: /var/fol' ...<2200 chars>... 'h_results.log line 33:     handle\n</persisted-output>'
  PASS: preview 2000 chars ≤ 2000
  PASS: content-addressed file on disk (906efb46e97d1008.txt)
  PASS: idempotent — same content → same file
```

## Step 2: P1 truncate_tool_results (keep=5)

```
  FAIL: COMPACTABLE_TOOLS mismatch: frozenset({'run_shell_command', 'glob', 'grep', 'web_search', 'read_file', 'read_article', 'read_note', 'web_fetch'})
  FAIL: FILE_TOOLS mismatch: frozenset({'glob', 'patch', 'read_file', 'write_file', 'grep'})
  PASS: 5 read_file → 0 cleared (at threshold)
  PASS: 8 read_file → 3 cleared (8 - 5 = 3)
    cleared: '[tool result cleared — older than 5 most recent calls]'
    cleared: '[tool result cleared — older than 5 most recent calls]'
    cleared: '[tool result cleared — older than 5 most recent calls]'
    kept:    'result 7'
  PASS: 10 save_memory → 0 cleared (non-compactable)
  PASS: multi-type: 8 read_file → 3 cleared, 7 web_search → 2 cleared (independent per-type tracking)
  PASS: last turn group protected (content intact)
```

## Step 3: P2 compact_assistant_responses (cap=2500) [BC6,BC7]

```
  PASS: OLDER_MSG_MAX_CHARS = 2500
  PASS: 54920 char TextPart → 2500 chars with marker
    head: "I've analyzed auth/views.py. Here's what I found:\n\nThe module follows Django's c"
    tail: ...'eed to be careful about the CSRF token replacement and the middleware ordering.\n'
  PASS: ThinkingPart capped to 2500
  PASS: last turn group untouched ('The middleware chain is now fully migrated. Tests are green.'...)
  PASS: ToolCallPart.args preserved (file_path=/critical.py)
  PASS: ToolReturnPart untouched (39679 char file content intact)
  PASS: _truncate_proportional head=36, tail=145 (20%/80%)
    head: "I've analyzed auth/middleware.py. He"...
    tail: ...'out the CSRF token replacement and the middleware ordering.\n'
  PASS: output never exceeds max_chars (tested down to 1)
```

## Step 4: Context enrichment (cap=4000) [BC2,BC3]

```
  FAIL: file paths not extracted from ToolCallPart.args
  PASS: Source 1 scoped to dropped only (Gap M)
  PASS: Source 2 — pending todos included, completed/cancelled filtered
    context: 'Active tasks:\n- [pending] Update tests\n- [in_progress] Write docs'
  FAIL: always-on memory not loaded
  FAIL: prior summary not extracted from dropped messages
  PASS: returns None when no sources produce data
  PASS: context capped at 4000 ≤ 4000 [BC2]
  FAIL: cannot locate enrichment (line None), registry guard (line None), or breaker (line None) in source
```

## Step 5: Prompt assembly [Outcome 1, BC1]

```
  PASS: template has 5 sections (Goal, Key Decisions, Working Set, Progress, Next Steps)
  PASS: template has prior-summary integration instruction
  PASS: (None, False) → template unchanged
  PASS: (context, False) → template + context
  PASS: (None, True) → template + personality
  PASS: (context, True) → context before personality (correct order)
    boundary: '## Additional Context\ntodos here\n\nAdditionally, preserve:\n- Personality-reinforcing moments (e'
```

## Step 6: Full processor chain P1→P2→P3→P4→P5 (real LLM)

```
  Input: 54 msgs, 14 groups, 72,480 chars
  Expected: P1 clears 5, P2 caps 13

  [P1] truncate_tool_results
    Cleared: 5 (expected 5)
    Chars: 72,480 → 58,200 (P1 reduced 14,280)
    PASS

  [P2] compact_assistant_responses
    Truncated: 13 (expected 13)
    Chars: 58,200 → 54,957 (P2 reduced 3,243)
    PASS

  [P3] detect_safety_issues
    Injections: 0 (clean history → no safety warnings)
    PASS

  [P4] append_recalled_memories
    _recall_for_context called (recall_count 0 → 1)
    Injections: 1 (memory injected)
    PASS

  [P5] summarize_history_window (LLM)
    Boundaries: head_end=2, tail_start=52, dropped=50
    Enrichment (287 chars):
      | Files touched: auth/backends.py, auth/constants.py, auth/decorators.py, auth/middleware.py, auth/permissions.py, auth/serializers.py, auth/signals.py, auth/tokens.py, auth/utils.py, auth/views.py
      | 
      | Active tasks:
      | - [pending] Update api/urls.py for JWT
      | - [pending] Add PyJWT to requirements
    FAIL: timed out
```

## Step 7: Multi-cycle compaction [Outcome 3]

```
  Input: 35 msgs, 7 groups, 18,952 chars
  Expected: P1 clears 1 (of 6 read_file), P2 caps 4

  [P1] truncate_tool_results
    Cleared: 1 (expected 1)
    Chars: 18,952 → 17,497 (P1 reduced 1,455)
    PASS

  [P2] compact_assistant_responses
    Truncated: 4 (expected 4)
    Chars: 17,497 → 17,269 (P2 reduced 228)
    PASS

  [P3] detect_safety_issues
    Injections: 0
    PASS

  [P4] append_recalled_memories
    _recall_for_context called (recall_count 0 → 1)
    Injections: 1 (memory injected)
    PASS

  [P5] summarize_history_window (LLM)
    Boundaries: head_end=2, tail_start=33, dropped=31
    Enrichment (126 chars):
      | Files touched: admin/views.py, auth/permissions.py, auth/tokens.py, settings.py, tests/test_auth.py, tests/test_integration.py
    Messages: 36 → 6 (31 replaced by 1 marker)
    Chars: 17,269 → 2,811
    PASS: compacted
    FAIL: expected exactly 1 marker, found 0
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
  FAIL: no marker found
  PASS: 1 group(s) → None
  PASS: 2 group(s) → None
  PASS: [BC5] overflow_recovery_attempted=False on _TurnState
```

## Step 9: Circuit breaker fallback [degradation path]

```
  FAIL: no static marker found
  FAIL: failure_count changed to 4 (should stay 3)
  PASS: messages reduced 12 → 5
```

## Step 10: A/B enrichment quality [context enrichment value]

```
  Running A (bare — no enrichment)...
  Running B (enriched — file paths + todos injected)...
  Bare summary (1240 chars): 3/5 enrichment signals
  Enriched summary (1344 chars): 5/5 enrichment signals
  PASS: enrichment adds 2 signals not in bare summary
    PASS: Step 10 enriched — enriched: file jwt_middleware.py in working set: found 'jwt_middleware'
    PASS: Step 10 enriched — enriched: file token_service.py in working set: found 'token_service'
    PASS: Step 10 enriched — enriched: file jwt_settings.py in working set: found 'jwt_settings'
    PASS: Step 10 enriched — enriched: RSA key rotation from todo: found 'rsa key rotation'
    PASS: Step 10 enriched — enriched: Redis migration from todo: found 'redis'
  PASS: bare summary missing 2/2 enrichment-only signals (expected)

  --- Summary A (bare) ---
    | ## Goal
    | Implement specific changes to the JWT authentication system across the application, ensuring the middleware, token service, and configuration settings are aligned and functional.
    | 
    | ## Key Decisions
    | No explicit decisions or rejected alternatives were recorded in the conversation history. The immediate directive was to implement changes following a review of the relevant files.
    | 
    | ## Working Set
    | - **Files Reviewed:** `auth/jwt_middleware.py`, `auth/token_service.py`, `config/jwt_settings.py`.
    | - **Status:** All reviewed files were analyzed, and the system confirmed the JWT configuration appears correct based on the initial inspection.
    | - **Tools:** File reading tools were used to inspect the codebase.
    | ...<9 more lines>

  --- Summary B (enriched) ---
    | I asked you to review the JWT implementation across `auth/jwt_middleware.py`, `auth/token_service.py`, and `config/jwt_settings.py`, and then implement the necessary changes.
    | 
    | ## Goal
    | Refactor the existing JWT authentication system to improve security and scalability, specifically targeting RSA key rotation and token invalidation mechanisms.
    | 
    | ## Key Decisions
    | - Proceeded with a middleware refactor based on the analysis of the three configuration files.
    | - Selected Redis as the backend for the new token blacklist implementation.
    | 
    | ## Working Set
    | ...<16 more lines>
```

## Step 11: Edge case battery [structural — no LLM]

```
  PASS: 11a — 1-turn history: all processors no-op
  PASS: 11b — 2-turn history: processors passthrough cleanly
  PASS: 11c — no ToolCallParts: enrichment returns None
  PASS: 11d — static marker in history: processors + grouping handle correctly (4 groups)
  PASS: 11e — all responses under 2.5K: P2 is pure no-op (0 truncated)
  PASS: 11f — single 60K message: P2 caps to 2500
  PASS: 11f — compaction boundaries returns None (only 2 groups)
  PASS: 11g — tool-only first response: find_first_run_end skips to index 3 (TextPart response)
  PASS: 11h — mixed request: save_memory preserved, compactable tools cleared independently
  PASS: 11i — empty message list: all processors + helpers handle gracefully
```

## Step 12: Prompt composition validation [LLM input inspection]

```
  PASS: 12a — 5 template sections present and in order
  PASS: 12a — context addendum after template
  PASS: 12a — personality addendum after context (correct order)
  PASS: 12a — enrichment content (file paths + todos) present in assembled prompt
  PASS: 12b — agent instructions contain security guardrail
  PASS: 12b — anti-injection directive present
  PASS: 12c — summary references dropped message content ('auth')
  PASS: 12c — summary references dropped message content ('middleware')
  PASS: 12c — enrichment file path appears in summary output
  PASS: 12c — enrichment todo content appears in summary output
  PASS: 12d — security guardrail in instructions only, not in user prompt
  PASS: 12d — cleared placeholder not in user prompt (only in message_history)
  PASS: 12e — no-context/no-personality prompt equals raw template
```
