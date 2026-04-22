# Compaction Quality Eval Report

**Verdict: FAIL** (10/13 steps passed)

| Step | What it validates | Result |
|------|-------------------|--------|
| Step 1: Pre-compact persist_if_oversized [BC4] | Tool results exceeding 50K chars are persisted to disk with a 2K preview placeholder. Content-addressed files ensure idempotency. | PASS |
| Step 2: P1 truncate_tool_results | Older compactable tool results (beyond the 5 most recent per type) are cleared. Non-compactable tools and the last turn group are protected. | PASS |
| Step 4: Context enrichment [BC2,BC3] | Side-channel context is gathered from 3 sources (file paths from ToolCallPart.args, pending todos, prior summaries) and capped at 4K chars. Always-on memories are injected separately by P4. | PASS |
| Step 5: Prompt assembly [Outcome 1,BC1] | Summarizer prompt has 9 structured sections (Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Pending User Asks, Resolved Questions, Next Step). Assembly order: template + context + personality. Merge contract: explicit pending→resolved transitions verified. | PASS |
| Step 6: Full chain P1→P5 (LLM) | Full P1→P3→P4→P5 chain on a 14-turn conversation with tool calls, producing an LLM summary. Validates numerical counts at each stage. | **FAIL** |
| Step 7: Multi-cycle [Outcome 3] | Chain on history containing a prior compaction summary. Validates that both prior context and new work are preserved across cycles. | PASS |
| Step 8: Overflow [Outcome 4,BC5] | Overflow detection (413/400 with context-length body), emergency compaction (keep first+last groups), and one-shot recovery guard. | PASS |
| Step 9: Circuit breaker [degradation] | Circuit breaker degradation: after 3 consecutive LLM failures, compaction falls back to static marker without attempting an LLM call. | PASS |
| Step 10: A/B enrichment quality | A/B enrichment quality: compares LLM summaries with and without context enrichment. Verifies enrichment-only signals (file paths, todos) appear in the enriched summary. | PASS |
| Step 11: Edge case battery | Edge case battery (no LLM): 1-2 turn history, no tools, static markers in history, all short responses, single massive message, tool-only first turn, mixed compactable/non-compactable parts, empty list. | PASS |
| Step 12: Prompt composition | Prompt composition: validates assembled prompt structure (9 sections in order: Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Pending User Asks, Resolved Questions, Next Step; context placement; personality ordering), agent instructions (security guardrail), message_history content, prompt injection isolation, and no-context path. | **FAIL** |
| Step 13: Prompt upgrade quality | Prompt upgrade quality: three deterministic single-run gates — (13a) ## Next Step contains a ≥20-char verbatim anchor from recent messages; (13b) ## User Corrections preserves explicit corrections; (13c) ## Errors & Fixes retains both the failure and user-directed fix guidance. | **FAIL** |
| Step 14: Pending/Resolved sections (functional) | Pending/Resolved sections — functional LLM validation: (14a) unanswered question appears in ## Pending User Asks; (14b) answered question appears in ## Resolved Questions and not in Pending; (14c) merge contract — prior pending item answered in new block migrates to ## Resolved Questions. | PASS |

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
  PASS: COMPACTABLE_TOOLS = ['file_glob', 'file_grep', 'file_read', 'knowledge_article_read', 'obsidian_read', 'shell', 'web_fetch', 'web_search']
  PASS: FILE_TOOLS = ['file_glob', 'file_grep', 'file_patch', 'file_read', 'file_write']
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

## Step 4: Context enrichment (cap=4000) [BC2,BC3]

```
  PASS: Source 1 — file paths from ToolCallPart.args
    context: 'Files touched: /app/models.py, /app/views.py'
  PASS: Source 1 scoped to dropped only (Gap M)
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
  PASS: template has 9 template sections (Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Pending User Asks, Resolved Questions, Next Step)
  PASS: template has prior-summary integration instruction with explicit merge contract
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
    Cleared: 5 (expected 5)
    Chars: 72,480 → 58,200 (P1 reduced 14,280)
    PASS

  [P3] _safety_prompt_text (dynamic instruction)
    Safety text: '' (clean history → no warnings expected)
    PASS

  [P4] _recall_prompt_text (dynamic instruction)
    _recall_for_context called (recall_count 0 → 1)
    Recall text length: 20 chars
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
  Expected: P1 clears 1 (of 6 read_file)

  [P1] truncate_tool_results
    Cleared: 1 (expected 1)
    Chars: 18,952 → 17,497 (P1 reduced 1,455)
    PASS

  [P3] _safety_prompt_text (dynamic instruction)
    Safety text: ''
    PASS

  [P4] _recall_prompt_text (dynamic instruction)
    _recall_for_context called (recall_count 0 → 1)
    Recall text length: 20 chars
    PASS

  [P5] summarize_history_window (LLM)
    Boundaries: head_end=2, tail_start=33, dropped=31
    Enrichment (126 chars):
      | Files touched: admin/views.py, auth/permissions.py, auth/tokens.py, settings.py, tests/test_auth.py, tests/test_integration.py
    Messages: 35 → 5 (31 replaced by 1 marker)
    Chars: 17,497 → 3,259
    PASS: compacted
    PASS: marker count (31) = actual dropped (31)
    PASS: prior content preserved (JWT/PyJWT)
    PASS: new work preserved (tests/URLs)
    PASS: multi-cycle integration — both prior and new preserved
    PASS: Step 7 — prior: JWT migration goal: found 'jwt'
    PASS: Step 7 — prior: PyJWT library choice: found 'pyjwt'
    PASS: Step 7 — new: test files updated: found 'test_auth'
    PASS: Step 7 — new: api/urls.py updated: found 'api/urls'
    PASS: Step 7 — new: dual-auth middleware: found 'dual-auth'
    PASS: Step 7 — new: Redis blacklist: found 'redis'
    PASS: Step 7 — new: rate limiting: found 'rate limit'
    PASS: semantic validation 7/7 (≥5 required)

    Summary (3197 chars):
      | This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion (31 messages).
      | 
      | I asked you to refactor the authentication module from session-based to JWT-based, specifically requesting the use of PyJWT for greater control.
      | 
      | ## Goal
      | Refactor the auth module to use JWTs instead of sessions, utilizing PyJWT directly for implementation control. The system must support a zero-downtime migration strategy.
      | 
      | ## Key Decisions
      | - **Library**: Using PyJWT directly for granular control over token claims and validation.
      | - **Migration Strategy**: Implementing a dual-auth middleware that checks for JWT first, falling back to session authentication for backwards compatibility during the transition.
      | - **Token Configuration**: Access tokens (15-min TTL, HS256, claims: user_id, email, role); Refresh tokens (7-day TTL, HttpOnly cookies).
      | - **Security**: Redis-based token blacklist, rate limiting (5 req/min), and RFC 6750 compliant error responses.
      | 
      | ## Errors & Fixes
      | No errors or failed attempts were encountered during this session. The user provided direct instructions to update tests, URLs, and clean up admin files without requiring pivots or corrections.
      | 
      | ## Working Set
      | - **Files Read**: `tests/test_auth.py`, `tests/test_integration.py`, `api/urls.py`, `settings.py`, `admin/views.py`, `auth/tokens.py`, `auth/permissions.py`.
      | - **Files Edited**: `tests/test_auth.py`, `tests/test_integration.py`, `api/urls.py`, `admin/views.py`.
      | - **Tools Used**: `file_read`, `edit_file`, `find_in_files`.
      | 
      | ## Progress
      | - **Completed**:
      |   - Updated `tests/test_auth.py` with comprehensive JWT flow tests (valid flow, expiration, malformed tokens, concurrency, blacklist, roles, scope, CORS).
      |   - Updated `tests/test_integration.py` with identical coverage.
      |   - Updated `api/urls.py` to route JWT endpoints.
      |   - Cleaned up `admin/views.py` and identified session references in `settings.py`.
      |   - Implemented JWT middleware logic (dual-auth, Redis blacklist, key rotation grace periods, clock skew handling).
      | - **In Progress**: None. All requested tasks have been marked as completed.
      | 
      | ## Pending User Asks
      | None.
      | 
      | ## Resolved Questions
      | Q: How should the middleware handle migration? → A: Use a dual-auth approach checking JWT first, falling back to session auth.
      | Q: What are the token specifications? → A: Access tokens (15m, HS256, specific claims); Refresh tokens (7d, HttpOnly).
      | Q: How are edge cases handled? → A: Key rotation (5m grace), clock skew (30s leeway), concurrent refresh (Redis lock).
      | 
      | ## Next Step
      | The refactoring task is complete. The immediate next step is to run the full test suite to verify the new JWT implementation and the dual-auth middleware behavior.
      | 
      | ## Additional Context
      | - **Files Touched**: `admin/views.py`, `auth/permissions.py`, `auth/tokens.py`, `settings.py`, `tests/test_auth.py`, `tests/test_integration.py`.
      | - **Personality/Tone**: The interaction was strictly professional and task-oriented. No specific personality preferences or emotional exchanges were noted. The user provided concise, directive commands ("Update tests", "Update URLs", "Find session refs").
      | 
      | Recent messages are preserved verbatim.

  Chain: 35 msgs/18,952ch → 5 msgs/3,259ch
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
  PASS: static marker used (no LLM call)
  PASS: failure_count incremented to 4 (circuit breaker skip tracking)
  PASS: messages reduced 12 → 5
```

## Step 10: A/B enrichment quality [context enrichment value]

```
  Running A (bare — no enrichment)...
  Running B (enriched — file paths + todos injected)...
  Bare summary (1222 chars): 3/5 enrichment signals
  Enriched summary (1367 chars): 5/5 enrichment signals
  PASS: enrichment adds 2 signals not in bare summary
    PASS: Step 10 enriched — enriched: file jwt_middleware.py in working set: found 'jwt_middleware'
    PASS: Step 10 enriched — enriched: file token_service.py in working set: found 'token_service'
    PASS: Step 10 enriched — enriched: file jwt_settings.py in working set: found 'jwt_settings'
    PASS: Step 10 enriched — enriched: RSA key rotation from todo: found 'rsa key rotation'
    PASS: Step 10 enriched — enriched: Redis migration from todo: found 'redis'
  PASS: bare summary missing 2/2 enrichment-only signals (expected)

  --- Summary A (bare) ---
    | ## Goal
    | The user aims to implement changes related to JWT authentication configuration after reviewing the relevant middleware, token service, and settings files.
    | 
    | ## Key Decisions
    | No explicit decisions or rejected alternatives were recorded in the provided conversation history; the user proceeded directly to implementation after confirming the configuration looked correct.
    | 
    | ## Errors & Fixes
    | No errors were encountered or resolved during this session.
    | 
    | ## Working Set
    | ...<16 more lines>

  --- Summary B (enriched) ---
    | ## Goal
    | I am refactoring the JWT authentication system to improve security and scalability. The immediate focus is implementing a middleware refactor, followed by adding RSA key rotation support and migrating the token blacklist to Redis.
    | 
    | ## Key Decisions
    | - Proceeded with a middleware refactor based on the analysis of `auth/jwt_middleware.py`, `auth/token_service.py`, and `config/jwt_settings.py`.
    | - Confirmed that the existing JWT configuration logic is correct across all three files, validating the foundation for the refactor.
    | 
    | ## Errors & Fixes
    | No errors or failures were encountered during this session. The analysis confirmed the existing configuration was sound.
    | 
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
  PASS: 12a — 8 static template sections present and in order
  FAIL: 12a — ## User Corrections conditional instruction missing from template
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

## Step 14: Pending/Resolved sections (functional LLM)

```
  [14a] Unanswered question → ## Pending User Asks
  PASS: 14a — ## Pending User Asks present with unanswered TTL question
    Pending section: '- "What TTL should we use for blacklisted tokens?"'

  [14b] Answered question → ## Resolved Questions, not in Pending
  PASS: 14b — ## Resolved Questions present with answered algorithm question
    Resolved section: '- Q: Which hashing algorithm should we u' ...<82 chars>... ' internal services with a shared secret.'
  PASS: 14b — answered question absent from ## Pending User Asks

  [14c] Merge contract — prior ## Pending item migrates to ## Resolved Questions
  PASS: 14c — TTL question migrated to ## Resolved Questions, absent from Pending
  PASS: 14c — TTL question absent from ## Pending User Asks (correctly resolved)
    Resolved section: 'Q: What Redis TTL should we use for blacklisted tokens? → A: 15 minutes for access tokens and 7 days for refresh tokens.'
    Pending section:  'None.'
```
