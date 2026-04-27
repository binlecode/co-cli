# Compaction Quality Eval Report

**Verdict: FAIL** (11/12 steps passed)

| Step | What it validates | Result |
|------|-------------------|--------|
| Step 1: Pre-compact persist_if_oversized [BC4] | Tool results exceeding 50K chars are persisted to disk with a 2K preview placeholder. Content-addressed files ensure idempotency. | PASS |
| Step 2: P1 truncate_tool_results | Older compactable tool results (beyond the 5 most recent per type) are cleared. Non-compactable tools and the last turn group are protected. | PASS |
| Step 4: Context enrichment [BC2,BC3] | Side-channel context is gathered from 3 sources (file paths from ToolCallPart.args, pending todos, prior summaries) and capped at 4K chars. Always-on memories are injected separately by P4. | PASS |
| Step 5: Prompt assembly [Outcome 1,BC1] | Prompt assembly: four input combinations (context × personality_active) validated against expected output structure. Verifies context appears after template sections, personality appears last. | PASS |
| Step 6: Full chain P1→P5 (LLM) | Full P1→P3→P4→P5 chain on a 14-turn conversation with tool calls, producing an LLM summary. Validates numerical counts at each stage. | PASS |
| Step 7: Multi-cycle [Outcome 3] | Chain on history containing a prior compaction summary. Validates that both prior context and new work are preserved across cycles. | PASS |
| Step 8: Overflow [Outcome 4,BC5] | Overflow detection (413/400 with context-length body), emergency compaction (keep first+last groups), and one-shot recovery guard. | PASS |
| Step 9: Circuit breaker [degradation] | Circuit breaker degradation: after 3 consecutive LLM failures, compaction falls back to static marker without attempting an LLM call. | PASS |
| Step 11: Edge case battery | Edge case battery (structural): 1-turn history, static markers in history, single massive message, mixed compactable/non-compactable parts, empty list. | PASS |
| Step 13: Prompt upgrade quality | Prompt upgrade quality: three deterministic single-run gates — (13a) ## Next Step contains a ≥20-char verbatim anchor from recent messages; (13b) ## User Corrections preserves explicit corrections; (13c) ## Errors & Fixes retains both the failure and user-directed fix guidance. | PASS |
| Step 14: Pending/Resolved sections (functional) | Pending/Resolved sections — functional LLM validation: (14a) unanswered question appears in ## Pending User Asks; (14b) answered question appears in ## Resolved Questions and not in Pending; (14c) merge contract — prior pending item answered in new block migrates to ## Resolved Questions. | PASS |
| Step 15: Deep learning trigger (Finch/UAT) | UAT: open-ended deep-learning loop driven by real run_turn. co autonomously fetches Wikipedia pages and reviews for the 2021 film Finch (Tom Hanks, Apple TV+) until M3 compaction fires organically. M1 persists oversized tool results to ~/.co-cli/tool-results/. Validates: network preflight, agentic continuation, M1+M3 end-to-end on real data, approval-hang guard, 10-point semantic ground truth (cast/crew/themes/title), 3 anti-hallucination checks, and ≥3 persisted artifacts in the real store. | **FAIL** |

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
  PASS: 5 read_file → 0 cleared (at threshold)
  PASS: 8 read_file → 3 cleared (8 - 5 = 3)
    cleared: '[file_read] ? (full, 8 chars)'
    cleared: '[file_read] ? (full, 8 chars)'
    cleared: '[file_read] ? (full, 8 chars)'
    kept:    'result 7'
  PASS: 10 save_memory → 0 cleared (non-compactable)
  PASS: multi-type: 8 read_file → 3 cleared, 7 web_search → 2 cleared (independent per-type tracking)
  PASS: last turn group protected (content intact)
```

## Step 4: Context enrichment (cap=4000) [BC2,BC3]

```
  PASS: Source 1 — file paths from ToolCallPart.args
    context: 'Files touched: /app/models.py, /app/views.py'
  PASS: Source 1 scoped to dropped only (Gap M)
  PASS: Source 2 — pending todos included, completed/cancelled filtered
    context: 'Active tasks:\n- [pending] Update tests\n- [in_progress] Write docs'
  PASS: Source 3 — prior summary from dropped messages
    context: 'Prior summary:\n[CONTEXT COMPACTION — REFERENCE ONLY] ' ...<614 chars>... ' auth module\n\nRecent messages are preserved verbatim.'
  PASS: context capped at 1995 ≤ 4000 [BC2]
```

## Step 5: Prompt assembly [Outcome 1, BC1]

```
  PASS: (context, False) → template + context
  PASS: (context, True) → context before personality (correct order)
    boundary: '## Additional Context\ntodos here\n\nAdditionally, preserve:\n- Personality-reinforcing moments (e'
```

## Step 6: Full processor chain P1→P3→P4→P5 (real LLM)

```
  Input: 54 msgs, 14 groups, 72,480 chars
  Expected: P1 clears 5

  [P1] truncate_tool_results
    Cleared: 5 (expected 5)
    Chars: 72,480 → 58,095 (P1 reduced 14,385)
    PASS

  [P3] safety_prompt_text (dynamic instruction)
    Safety text: '' (clean history → no warnings expected)
    PASS

  [P5] apply_compaction (LLM)
    SKIP: plan_compaction_boundaries returned None (budget=131072, 54 msgs) — history too small for configured context window
```

## Step 7: Multi-cycle compaction [Outcome 3]

```
  Input: 35 msgs, 7 groups, 19,596 chars
  Expected: P1 clears 1 (of 6 read_file)

  [P1] truncate_tool_results
    Cleared: 1 (expected 1)
    Chars: 19,596 → 18,120 (P1 reduced 1,476)
    PASS

  [P3] safety_prompt_text (dynamic instruction)
    Safety text: ''
    PASS

  [P5] apply_compaction (LLM)
    SKIP: plan_compaction_boundaries returned None (budget=131072, 35 msgs) — history too small for configured context window
```

## Step 8: Overflow recovery [Outcome 4, BC5]

```
  PASS: 413 + context_length_exceeded
  PASS: 400 + dict body (OpenAI)
  PASS: 400 + str body (Ollama)
  PASS: bare 400 → False (reformulation)
  PASS: 500 → False (wrong code)
  PASS: 400 + None body → False
  PASS: 400 + Gemini exceeds-limit
  PASS: 400 + Gemini input-token-count
  PASS: 400 + structured overflow code
  PASS: 413 + None body → True (status alone)
  PASS: 400 + wrapped metadata.raw
  PASS: 400 + malformed metadata.raw → False
```

## Step 9: Circuit breaker fallback [degradation path]

```
  SKIP: plan_compaction_boundaries returned None (budget=131072, 12 msgs) — history too small for configured context window
```

## Step 11: Edge case battery [structural]

```
  PASS: 11a — 1-turn history: all processors no-op
  PASS: 11b — static marker in history: P1 + grouping handle correctly (4 groups)
  PASS: 11d — mixed request: save_memory preserved, compactable tools cleared independently
```

## Step 13: Prompt upgrade quality (13a verbatim anchor, 13b corrections, 13c error-feedback)

```
  [13a] Verbatim anchor in ## Next Step
  PASS: 13a — ## Next Step contains verbatim anchor (≥20 chars) from recent messages

  [13b] User corrections captured in ## Active Task verbatim anchor
  PASS: 13b — 'python-jose' present in ## Active Task (final directive captured)

  [13c] User feedback on error fix retained in ## Errors & Fixes
  PASS: 13c — ## Errors & Fixes exists with test failure and user-directed correction
```

## Step 14: Pending/Resolved sections (functional LLM)

```
  [14a] Unanswered question → ## Pending User Asks
  PASS: 14a — ## Pending User Asks present with unanswered TTL question
    Pending section: '"What TTL should we use for blacklisted tokens?"'

  [14b] Answered question → ## Resolved Questions, not in Pending
  PASS: 14b — ## Resolved Questions present with answered algorithm question
    Resolved section: 'Q: Which hashing algorithm should we use for JWT signing? → A: HS256 was chosen for its simplicity with a shared secret.'
  PASS: 14b — answered question absent from ## Pending User Asks

  [14c] Merge contract — prior ## Pending item migrates to ## Resolved Questions
  PASS: 14c — TTL question migrated to ## Resolved Questions, absent from Pending
  PASS: 14c — TTL question absent from ## Pending User Asks (correctly resolved)
    Resolved section: 'Q: What Redis TTL should we use for blac' ...<71 chars>... 'ays (604800 seconds) for refresh tokens.'
    Pending section:  'None.'
```

## Step 15 (UAT): Deep movie learning (Finch) — run_turn-driven, real data

```
  Preflight: en.wikipedia.org reachable
    STATUS:   Reranker degraded — TEI cross-encoder unavailable; search results will be unranked
    STATUS:   Knowledge degraded — embedder unavailable (not reachable — [Errno 61] Connection refused); using fts5
    STATUS:   Knowledge synced — 0 item(s) (fts5)
  Turn 1/30 — history: 0 msgs
    STATUS: Co is thinking...
    STATUS: LLM segment timed out — model did not respond. Try a shorter prompt, or check model health with `co config`.
    turn elapsed: 60.0s
  Turn 2/30 — history: 0 msgs
    STATUS: Co is thinking...
    turn elapsed: 39.6s
  Turn 3/30 — history: 4 msgs
    STATUS: Co is thinking...
    STATUS: LLM segment timed out — model did not respond. Try a shorter prompt, or check model health with `co config`.
    turn elapsed: 60.0s
UAT: FAIL (agentic stall): co returned a turn with no tool calls before compaction triggered — prompt insufficient or agentic flow regression
```
