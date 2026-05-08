# Co CLI — Compaction System


Covers how co-cli keeps context bounded under pressure. Prompt assembly and history processors live in [prompt-assembly.md](prompt-assembly.md); transcript persistence (in-place rewrite on compaction) lives in [memory.md](memory.md); one-turn orchestration and overflow detection in [core-loop.md](core-loop.md); tool emission contracts in [tools.md](tools.md).

## 1. Functional Architecture

**Cycle terminology.** A *user turn* runs from one `UserPromptPart` to the final response; it contains one or more *LLM requests* (`ModelRequestNode` → one HTTP call to the model). A turn that drives K tool-call batches fires K+1 LLM requests; a no-tool turn fires 1.

**Trigger cadence vs. scope.** Five history processors fire before every `ModelRequestNode` (pydantic-ai's pre-flight hook). They never skip a request. What differs between mechanisms is *scope* — which slice of the message list each one acts on. See the **Scope** column.

| Mechanism | Scope | When | Gate logic | Target | Effect | LLM? |
|---|---|---|---|---|---|---|
| **`spill_if_oversized`** | per tool result | At tool emit time | `len(display) > spill_threshold_chars` (per-tool, default `SPILL_THRESHOLD_CHARS`); `force=True` overrides threshold | Single tool result | Content written to disk; `<persisted-output>` placeholder in context | No |
| **`dedup_tool_results`** | housekeeping | Every `ModelRequestNode` | last `UserPromptPart` exists (boundary); per-part: `is_dedup_candidate` (string ≥ 200 chars, in `COMPACTABLE_TOOLS`); latest-id target in `durable_call_ids` (will survive eviction) | Duplicate returns in pre-tail region | Identical `(tool, content-hash)` pairs collapsed to back-reference pointing at latest `tool_call_id` | No |
| **`evict_old_tool_results`** | housekeeping | Every `ModelRequestNode` | last `UserPromptPart` exists; per-part: tool in `COMPACTABLE_TOOLS` AND not in 5-most-recent `keep_ids` per tool name | `COMPACTABLE_TOOLS` returns in pre-tail older than the 5 most-recent per tool name | Content replaced with a semantic marker (name, key args, size/outcome signal); non-compactable tools pass through untouched | No |
| **`enforce_request_size`** | full message list | Every `ModelRequestNode`, after `evict_old_tool_results`, before `proactive_window_processor` | `total > deps.spill_threshold_tokens` (where `total = max(estimate_message_tokens, latest_response_input_tokens)`); ≥ 1 non-already-spilled candidate | All string `ToolReturnPart`s across the message list | Largest unspilled returns force-spilled to disk, largest-first, until aggregate ≤ threshold | No |
| **`proactive_window_processor`** | multi-user-turn | Every `ModelRequestNode` | `token_count > compaction_ratio × budget`; `consecutive_low_yield_proactive_compactions < proactive_thrash_window`; `plan_compaction_boundaries` returns non-`None`; circuit breaker not tripped (else static marker, no LLM); `ctx.deps.model is not None` (else static marker) | Middle user-turn groups (head and tail always preserved) | Middle replaced by LLM summary marker; head / marker / breadcrumbs / tail assembled | Yes (static marker fallback) |
| **`sanitize_surrogate_codepoints`** | housekeeping | Every `ModelRequestNode`, after `proactive_window_processor` | None — unconditional sweep (no-op when no surrogates present) | All messages | Lone surrogates (U+D800–U+DFFF) replaced with U+FFFD | No |
| **Overflow recovery** | multi-user-turn | Provider HTTP 400/413 | `is_context_overflow` true (HTTP 413 unconditional; 400 with overflow evidence); `turn_state.overflow_recovery_attempted` False (one-shot per turn) | All `ToolReturnPart`s, then middle user-turn groups if still over budget | Strip every tool return to a semantic marker; if stripped fits budget, return; else run planner + `apply_compaction` on the stripped history | Yes (static marker fallback) |
| **`/compact`** | multi-user-turn | User command | None — user-invoked | Full history, bounds `(0, n, n)` | Same assembly as `proactive_window_processor` via `apply_compaction` | Yes (static marker fallback) |

All three LLM-capable paths (`proactive_window_processor`, overflow recovery, `/compact`) share one summarizer, one assembly helper (`apply_compaction`), and one marker format — static vs. summary markers differ only in whether a summary string is available.

**Why per-request cadence.** Context pressure rises mid-turn — a single tool round can append a 50K-char shell output plus several `file_read` results, pushing the next request over budget before the user speaks again. Per-turn cadence would catch overflow only reactively via HTTP 400/413.

**Effective work ≠ uniform trigger.** Per the **Gate logic** column above: `sanitize_surrogate_codepoints` runs unconditionally; `dedup_tool_results` / `evict_old_tool_results` act whenever eligible parts exist (cheap); `enforce_request_size` fast-paths whenever `total ≤ deps.spill_threshold_tokens`; `proactive_window_processor` fast-paths until its own threshold trips. The two threshold-gated processors are intentionally tiered — spill (cheap) targets ≤ `spill_ratio × budget` (default 0.50); proactive (LLM) only fires when spill exhausted candidates and total still exceeds `compaction_ratio × budget` (default 0.50, gating proactive's `>`-check).

**Manual entry:** `/compact [focus]` — full-history replacement via `apply_compaction` with bounds `(0, n, n)`. Inherits the proactive path's degradation policy (no-model / circuit-breaker / failure all fall back to a static marker). `[focus]` threads to `summarize_messages` for topic emphasis.

### Diagram 1: Overall compaction pipeline

End-to-end flow: `UserPromptPart` → `ModelRequestNode` pre-flight hook (the **MRN** subgraph — five `history_processors`) → model HTTP → `CallToolsNode` (the **CTN** subgraph — per-result `spill_if_oversized` only); overflow recovery branches off HTTP 400/413. See §2.3 for per-stage message transformation; §2.4 for `enforce_request_size`; §2.5 for `proactive_window_processor` internals.

```mermaid
flowchart TD
    UPN(["UserPromptPart\n(turn start)"])
    FINAL(["FinalResult"])
    RTYPE{"response type?"}

    subgraph MRN["ModelRequestNode — history_processors chain → HTTP"]
        direction LR
        C0["dedup_tool_results"] --> C1["evict_old_tool_results"] --> CE["enforce_request_size"] --> C2["proactive_window_processor"] --> C3["sanitize_surrogate_codepoints"] --> HTTP["model.request()\nHTTP → model"]
    end

    subgraph CTN["CallToolsNode — execute_tools (per-result spill_if_oversized only)"]
        direction LR
        TCALL["model response:\nToolCallPart(s)"] --> EXEC["tool executes"] --> M1["spill_if_oversized"] --> HIST["ToolReturnPart →\nbatch parts"] --> MORE{"more tool calls?"}
        MORE -->|yes| EXEC
        MORE -->|"no — batch complete"| NEXT["next ModelRequestNode"]
    end

    subgraph OVF["overflow recovery — HTTP 400/413"]
        direction LR
        OV1["recover_overflow_history"] --> OV2["strip_all_tool_returns"]
        OV2 --> OV3{"fits budget?"}
        OV3 -->|yes| OV4["retry"]
        OV3 -->|no| OV5["plan_compaction_boundaries"] --> OV6{"bounds valid?"}
        OV6 -->|yes| OV7["apply_compaction"] --> OV4
        OV6 -->|no| OV8["terminal error"]
    end

    UPN --> C0
    HTTP -->|"200 OK"| RTYPE
    HTTP -->|"400/413"| OV1
    OV3 --> C0
    RTYPE -->|ToolCallPart| TCALL
    RTYPE -->|TextPart| FINAL
    NEXT -.->|"re-enter MRN"| C0

    classDef hook fill:#ede0f5,stroke:#9c6fc7
    classDef term fill:#d4edda,stroke:#74c476
    class C0,C1,CE,C2,C3,M1 hook
    class UPN,FINAL term
```

### Diagram 2: Message-list shape before, during, and after marker insertion

Four `ModelRequestNode` snapshots across a user turn that crosses the compaction threshold mid-turn — Request #1 (fast path) → Request #2 sampled before and after `proactive_window_processor` fires → Request #3 (post-compaction fast path). `SystemPrompt` is SDK-injected fresh on every request and is never part of the compacted history.

```text
①  Request #1  —  fast path  (token_count ≤ compaction_ratio × budget)
─────────────────────────────────────────────────────────────────────
  SystemPrompt                                       ← SDK-injected, never compacted
  ModelRequest    UserPromptPart  (1st turn)         ┐
  ModelResponse   TextPart        (1st turn)         ┘ pinned head
  ModelRequest / ModelResponse    prior turns        ← dedup/evict-cleared in pre-tail
  ModelRequest    UserPromptPart  (current)          ← protected tail

      │  model returns ToolCall1; tool runs
      ▼

②a Request #2  —  BEFORE compaction  (token_count exceeds threshold; processor about to fire)
─────────────────────────────────────────────────────────────────────
  SystemPrompt
  ModelRequest    UserPromptPart  (1st turn)         ┐ pinned head
  ModelResponse   TextPart        (1st turn)         ┘
  ModelRequest / ModelResponse    prior turns        ← dedup/evict-cleared, still present
  ModelRequest    UserPromptPart  (current)          ┐
  ModelResponse   ToolCallPart    (file_read)        │ current turn
  ModelRequest    ToolReturnPart  (spill_if_oversized if oversized)  ┘

      │  proactive_window_processor: plan → summarize → assemble
      │  middle prior turns dropped; head | marker | metadata | tail substituted
      ▼

②b Request #2  —  AFTER compaction  (sent to model with compacted history)
─────────────────────────────────────────────────────────────────────
  SystemPrompt
  ModelRequest    UserPromptPart  (1st turn)         ┐ pinned head
  ModelResponse   TextPart        (1st turn)         ┘
  ModelRequest    UserPromptPart  (COMPACTION MARKER)    ← summary or static
  ModelRequest    UserPromptPart  (todo snapshot)        ┐ metadata
  ModelRequest    ToolReturnPart  (search breadcrumbs)   ┘ (carried over)
  ModelRequest    UserPromptPart  (current)          ┐
  ModelResponse   ToolCallPart    (file_read)        │ current turn
  ModelRequest    ToolReturnPart  (result1)          ┘

      │  model returns ToolCall2; tool runs
      ▼

③  Request #3  —  fast path  (marker shrank token_count below threshold; carried forward)
─────────────────────────────────────────────────────────────────────
  SystemPrompt
  ModelRequest    UserPromptPart  (1st turn)         ┐ pinned head
  ModelResponse   TextPart        (1st turn)         ┘
  ModelRequest    UserPromptPart  (COMPACTION MARKER)    ← persists across requests
  ModelRequest    UserPromptPart  (todo snapshot)
  ModelRequest    ToolReturnPart  (search breadcrumbs)
  ModelRequest    UserPromptPart  (current)          ┐
  ModelResponse   ToolCallPart    (file_read)        │
  ModelRequest    ToolReturnPart  (result1)          │ two tools resolved
  ModelResponse   ToolCallPart    (file_search)      │
  ModelRequest    ToolReturnPart  (result2)          ┘

      │  model returns final text
      ▼  user turn complete
```

## 2. Core Logic

### 2.1 L0 admission cap — `MAX_TOOL_CALLS_PER_MODEL_TURN`

The first defense in co-cli's four-layer budget stack — L0 caps how many tool calls a single `ModelResponse` can issue. Layers L1-L3 (covered in §2.2 / §2.4 / §2.5 and §2.7) reshape context after content arrives; L0 short-circuits at admission, so calls beyond the cap never execute and never produce a `ToolReturnPart` for the lower layers to handle.

| Layer | Mechanism | Scope | Role |
|---|---|---|---|
| **L0** | `MAX_TOOL_CALLS_PER_MODEL_TURN = 6` | per `ModelResponse` | Admission control — preventive |
| **L1** | `spill_if_oversized` | per `ToolReturnPart` | Per-result emit-time persistence — reactive |
| **L2** | `enforce_request_size` (history processor at `ModelRequestNode` entry) | full message list | Per-request size cap — force-spill largest tool returns until total fits — reactive |
| **L3** | `proactive_window_processor` (+ `recover_overflow_history`) | full history window | Window-scale LLM compaction — reactive, fallback when L2 spill alone can't fit |

**Constant.** `MAX_TOOL_CALLS_PER_MODEL_TURN = 6` in `co_cli/agent/tool_call_limit.py`; non-configurable. Sized so 6 non-spilling (≤ 4K char) tool returns aggregate inside the per-request spill threshold.

**Trigger surface.** `CoToolLifecycle.before_tool_execute` — runs inside `CallToolsNode`, before each tool handler is invoked, on every `ToolCallPart` in the model response.

**Counter mechanics.**
- `ctx.run_step` increments once per `ModelRequestNode`, so all tool calls from one assistant message share the same `run_step`.
- When `run_step` changes, `runtime.tool_calls_in_model_turn` resets to 0.
- Each call increments the counter before the cap check.

**Enforcement** (`co_cli/tools/lifecycle.py:187-197`):

```python
if ctx.run_step != runtime.tool_call_limit_run_step:
    runtime.tool_call_limit_run_step = ctx.run_step
    runtime.tool_calls_in_model_turn = 0
runtime.tool_calls_in_model_turn += 1

if runtime.tool_calls_in_model_turn > MAX_TOOL_CALLS_PER_MODEL_TURN:
    return json.dumps(make_exceeded_payload(runtime.tool_calls_in_model_turn))
return await handler(args)
```

The first 6 calls execute normally. Calls 7+ are **rejected without running** — the rejection payload is returned as the tool's "result," appended to message history as a `ToolReturnPart`, and seen by the model on the next turn:

```json
{
  "error": "max_tool_calls_per_turn_exceeded",
  "max": 6,
  "issued": <N>,
  "guidance": "Issued <N> tool calls in one model turn; cap is 6. Pick the 6 most important calls and try again."
}
```

**Observability.** `CoToolLifecycle.after_node_run` emits one `tool_budget.enforce_tool_call_limit` span per `CallToolsNode` exit with attributes `tool_calls.limit / issued / allowed / rejected / limit_exceeded` (see `docs/specs/observability.md`).

**Why L0 isn't in the §1 mechanism table.** That table tracks context-modifying passes — transformations of the `ModelMessage` list before it goes to the model. L0 doesn't transform the message list; it short-circuits at admission and returns a structured rejection. Conceptually it's part of the budget stack but not part of compaction proper.

**Relationship to L1-L3.** L0 is preventive; L1-L3 are reactive. Even with L0 in place:
- A malformed tool returning a 1MB blob still needs L1 (`spill_if_oversized`) to size-cap.
- Multi-batch accumulation across one user turn still needs L2 (`enforce_request_size`) to aggregate-cap the upcoming request.
- Long conversations with non-tool-return pressure still need L3 (`proactive_window_processor`) to window-cap.

L0 just bounds the *worst-case fan-out per turn* so the lower layers can be sized for realistic load instead of pathological model output.

### 2.2 `spill_if_oversized` — Emit-time persistence

Spills any single tool result exceeding its per-tool threshold to `.co-cli/tool-results/<sha16>.txt` and replaces the content with a `<persisted-output>` placeholder before the result enters history. Content-addressed (SHA-256 prefix); written once, never rewritten.

**Trigger:** `len(display) > threshold` inside `tool_output()`.

| Tool | `spill_threshold_chars` | Note |
|---|---|---|
| Default | `SPILL_THRESHOLD_CHARS` (4,000) | module constant in `tool_io.py` |
| `file_read` | `math.inf` | never spills — prevents spill→read→spill recursion |

Placeholder shape — multi-line `<persisted-output>` block carrying a size preamble, `tool:` / `file:` lines, a `file_read` retrieval hint, and a `preview:` of the first `TOOL_RESULT_PREVIEW_CHARS` (1,500) chars (newline-aware truncation past halfway point).

### 2.3 `dedup_tool_results` / `evict_old_tool_results` — Prepass recency clearing

Two sync processors in order; no LLM calls. A third sync processor (`sanitize_surrogate_codepoints`) runs after `proactive_window_processor` — see end of this section.

**`dedup_tool_results`** — collapses identical returns outside the protected tail before recency clearing. For each compactable return whose `(tool_name, sha256(content))` key matches a more recent return of the same tool, replaces content with a 1-line back-reference to the latest `tool_call_id`. Eligibility: string content ≥ 200 chars; non-string and non-compactable tools pass through. (`co_cli/context/_dedup_tool_results.py`)

**`evict_old_tool_results`** — protects the last `UserPromptPart` onward; only acts on tools in `COMPACTABLE_TOOLS` (`file_read`, `shell`, `file_search`, `file_find`, `web_search`, `web_fetch`, `obsidian_read`) — non-compactable tools (writes, approvals, memory ops) pass through untouched regardless of count. For each selected tool, keeps the 5 most-recent returns in the pre-tail region (counted independently per tool name); replaces older returns with a semantic marker (`semantic_marker()` in `co_cli/context/_tool_result_markers.py`). Marker carries tool name, 1-3 key args from `ToolCallPart.args` (looked up via a `tool_call_id` index), and a size/outcome signal — e.g. `[shell] ran \`uv run pytest\` → exit 0, 47 lines`, `[file_read] src/foo.py (full, 1,200 chars)`. Non-string content falls back to `_CLEARED_PLACEHOLDER`. `tool_name` and `tool_call_id` preserved in the replacement part.

**Worked example — message list state at each stage.** Conversation with duplicate `file_read` returns and six `shell` runs in the pre-tail region. The `═══` line marks the protected-tail boundary; everything below it passes through `dedup_tool_results` and `evict_old_tool_results` untouched. (Round-budget enforcement on the latest batch is not part of this prepass — see §2.4.)

```text
INPUT  (raw history, before any processor)
─────────────────────────────────────────────────────────────────────
  head
  TR(file_read)  abc.py                       ← duplicate of fd2
  TR(file_read)  abc.py                       ← latest copy
  TR(shell)      run 1                        ┐
  TR(shell)      run 2                        │
  TR(shell)      run 3                        │  pre-tail
  TR(shell)      run 4                        │
  TR(shell)      run 5                        │
  TR(shell)      run 6                        ┘
  ═══ UserPromptPart (current turn) ═══════════════════════════
  TR(shell)      run 7

      │
      │  dedup_tool_results
      │    rule: collapse identical (tool_name, content-hash) in pre-tail
      │    finding: fd1 and fd2 share the same content
      ▼

AFTER dedup_tool_results
─────────────────────────────────────────────────────────────────────
  head
  → see fd2  (back-reference, content cleared)
  TR(file_read)  abc.py                       ← latest, kept
  TR(shell)      run 1
  TR(shell)      run 2 .. run 6
  ═══ UserPromptPart ═══════════════════════════════════════════
  TR(shell)      run 7

      │
      │  evict_old_tool_results
      │    rule: keep COMPACTABLE_KEEP_RECENT = 5 most-recent per tool name
      │    finding: 6 shell returns in pre-tail; run 1 is the oldest
      ▼

AFTER evict_old_tool_results
─────────────────────────────────────────────────────────────────────
  head
  → see fd2  (back-reference)
  TR(file_read)  abc.py                       ← 1 of 1, kept
  [shell] run 1 → exit 0, N lines             ← semantic marker (older than 5)
  TR(shell)      run 2 .. run 6               ← 5 most-recent, kept
  ═══ UserPromptPart ═══════════════════════════════════════════
  TR(shell)      run 7
```

**`sanitize_surrogate_codepoints`.** Runs after `proactive_window_processor` — last in the pipeline. Replaces lone Unicode surrogate code points (U+D800–U+DFFF) with U+FFFD in all `ModelRequest` and `ModelResponse` parts. Applies to string content on `UserPromptPart`, `SystemPromptPart`, `RetryPromptPart`, `ToolReturnPart`, `TextPart`, `ThinkingPart`, and `ToolCallPart.args`. Guards against `UnicodeEncodeError` inside the OpenAI SDK caused by byte-token reasoning models that occasionally emit lone surrogates.

### 2.4 `enforce_request_size` — Per-request size control (history processor)

L2's per-request cap. Operates on the **full message list** at `ModelRequestNode` entry — not on a single batch. Implemented as a sync history processor in `co_cli/context/history_processors.py` alongside `dedup_tool_results`, `evict_old_tool_results`, and `sanitize_surrogate_codepoints`. Replaces a prior post-tool-exec hook design (`_enforce_request_budget`) which only saw the just-produced batch and over-fired on small histories while under-firing on multi-batch accumulation.

**Chain placement.** Fires after `dedup_tool_results` and `evict_old_tool_results` (so cheap reductions happen first — no point spilling content the next processor would have deduped) and before `proactive_window_processor` (which fast-paths whenever spill brought total under `compaction_ratio × budget`, sparing the LLM call).

**Scope.** The full message list visible at MRN entry. Walks every `ModelRequest` and collects every string `ToolReturnPart`, regardless of region (no protected tail at this stage — recency protection is `evict_old_tool_results`'s job).

**Skip cases:**

| Condition | `skip_reason` | Behavior |
|---|---|---|
| `total ≤ deps.spill_threshold_tokens` | `below_threshold` | Fast path; no rewrite. Span emitted with `tokens_before == tokens_after`. |
| No string `ToolReturnPart`s in history | `no_candidates` | No rewrite. |
| All candidates already persisted (content starts with `PERSISTED_OUTPUT_TAG`) | `all_spilled` | No rewrite. |
| Spill exhausted candidates but aggregate still > threshold | `fallback_to_summarize` | Returns the (possibly partially-rewritten) message list; `proactive_window_processor` runs next and decides whether to fire LLM summarization. |
| Spill brought aggregate ≤ threshold | `""` (empty) | Rewritten message list returned. |
| Spill `OSError` on a candidate | (per-candidate, counted in `spill_errors`) | That candidate skipped (`new == old`); loop continues. |

**Algorithm.**

1. `total = max(estimate_message_tokens(messages), latest_response_input_tokens(messages))` — folds local char-based estimate with the most recent provider-reported input-token count to bias toward earlier spilling.
2. If `total ≤ deps.spill_threshold_tokens`, fast-path.
3. Walk every `ModelRequest`, collect all `ToolReturnPart`s with string content as candidates. Filter spillable: those whose content does not start with `PERSISTED_OUTPUT_TAG`.
4. Sort spillable largest-first by `len(content)`.
5. Force-spill via `spill_if_oversized(content, deps.tool_results_dir, tool_name, force=True)` until aggregate ≤ threshold or candidates exhaust. Track replacements by `id(part)`. The loop accumulator is tracked in local-char space (`starting_tokens = estimate_message_tokens(messages)`); `max(local, reported)` is the trigger only, not the loop baseline. After spill, `effective_after = max(local_after, reported)` reconstructs the conservative post-spill estimate for the terminal span and `fallback_to_summarize` gate.
6. Apply rewrites via `_rewrite_tool_returns(messages, len(messages), replacement_for=lambda p: spilled.get(id(p)))` — only messages with rewritten parts are rebuilt; unchanged messages pass through verbatim.

**Span.** `tool_budget.enforce_request_size` — emitted by tracer `co-cli.tool_budget` on every call. Attributes: `budget.context_window_tokens`, `request.threshold_tokens / tokens_before / local_tokens / reported_tokens / tokens_after / candidates_count / spillable_count / spilled_count / spill_errors / spill_fired (bool) / skip_reason`.

**Side effect.** Writes `deps.runtime.current_request_tokens_after_spill = aggregate` on every path (including fast paths). Read by `proactive_window_processor` for OTEL — diagnostic only, no logic branches on it.

**Threshold.** `deps.spill_threshold_tokens = int(spill_ratio × model_max_ctx)`, computed once at bootstrap and cached on `CoDeps`. The `compaction.spill_ratio` knob is validated `≤ compaction_ratio` so post-spill aggregate falls below proactive's trigger and proactive fast-paths.

**Worked example.** Multi-batch turn: history contains three `ModelRequest`s carrying tool returns of 24K chars (≈ 6K tokens) each, plus a small head. Threshold = 6K tokens.

```text
BEFORE enforce_request_size (full message list)
─────────────────────────────────────────────────────────────────────
  ModelRequest    UserPromptPart  (turn start)
  ModelResponse   ToolCallPart    shell
  ModelRequest    ToolReturnPart  shell run 1   [24K chars ≈ 6K tokens]
  ModelResponse   ToolCallPart    shell
  ModelRequest    ToolReturnPart  shell run 2   [24K chars ≈ 6K tokens]
  ModelResponse   ToolCallPart    shell
  ModelRequest    ToolReturnPart  shell run 3   [24K chars ≈ 6K tokens]

      aggregate ≈ 18K tokens > 6K threshold → spill_fired=True
      spillable largest-first: run 1 == run 2 == run 3 (any order)

AFTER enforce_request_size
─────────────────────────────────────────────────────────────────────
  ModelRequest    UserPromptPart  (turn start)
  ModelResponse   ToolCallPart    shell
  ModelRequest    ToolReturnPart  shell run 1   <persisted-output>   ← spilled
  ModelResponse   ToolCallPart    shell
  ModelRequest    ToolReturnPart  shell run 2   <persisted-output>   ← spilled
  ModelResponse   ToolCallPart    shell
  ModelRequest    ToolReturnPart  shell run 3   [24K chars]          ← spill stopped: aggregate ≤ 6K
```

### 2.5 `proactive_window_processor` — Window compaction

**Full path overview.** Trigger → gates → planner → assembly → feedback. The prose subsections below cover each step in detail.

```text
proactive_window_processor — full path

  ─ STEP 1: token counting (combine local + provider-reported) ─
    local_estimate    = estimate_message_tokens(messages)
    reported_estimate = (
        0  if compaction_applied_this_turn
        else post_compaction_token_estimate (stale guard)
          or latest_response_input_tokens(messages)
    )
    token_count = max(local_estimate, reported_estimate)
                    ↑ biases toward earlier compaction

  ─ STEP 2: gates (cheap, fail fast) ─────────────────────────────
    if token_count ≤ compaction_ratio × budget
        → return messages              (FAST PATH — below threshold)
    if consecutive_low_yield ≥ proactive_thrash_window
        → emit banner once; return     (ANTI-THRASH gate)

  ─ STEP 3: boundary planner (plan_compaction_boundaries) ────────
    budget       = resolve_compaction_budget(deps)    (= model_max_ctx)
    head_end     = find_first_run_end(messages) + 1
    groups       = group_by_turn(messages)            e.g., [G0, G1, G2, G3]
    tail_budget  = tail_fraction × budget             e.g., 0.20 × 32K ≈ 6.5K

    if len(groups) < _MIN_RETAINED_TURN_GROUPS + 1 (= 2)
        → return None                  (nothing to drop)

    walk groups from end, accumulate tokens:
        G3:  3K  →  acc=3K, len=1 ≥ min=1, fits 6.5K → keep
        G2:  2K  →  acc=5K, len=2,        fits 6.5K → keep
        G1:  4K  →  acc=9K, len=2,        > 6.5K   → STOP

    tail_start    = G2.start_index
    dropped_range = G1
    head_range    = [0 .. head_end] + G0

    last-group guarantee:
        if G3 alone = 10K > 6.5K, len=1 ≥ min=1 → keep anyway

  ─ STEP 4: assembly (apply_compaction) ──────────────────────────
    summary = _gated_summarize_or_none(messages[head_end:tail_start])
    result  = head | marker | [todo_snapshot] | [search breadcrumbs] | tail

  ─ STEP 5: post-compaction feedback (anti-thrash counter) ───────
    savings = (tokens_before − tokens_after) / tokens_before   (local-only)
    if savings ≥ min_proactive_savings
        consecutive_low_yield = 0      (re-arm)
    else
        consecutive_low_yield += 1     (steps toward thrash gate)
```

**Stale-suppression guard.** STEP 1's `post_compaction_token_estimate` branch only clears once a `ModelResponse` with `input_tokens > 0` exists in `messages[message_count_at_last_compaction:]` — message-count delta alone is unreliable since post-compaction tool returns or user messages may have been appended without a fresh LLM call.

**Token counting:**
- `estimate_message_tokens(messages)` — `total_chars // CHARS_PER_TOKEN` over text parts and JSON-serialized `ToolCallPart.args`. Args are included because `evict_old_tool_results` clears return content only, never call args.
- `latest_response_input_tokens(messages)` — most recent `ModelResponse.usage.input_tokens`; zeroed when `compaction_applied_this_turn` (provider count is stale).
- `_effective_token_count` — `max(local, reported)`; biases toward earlier compaction.

**Budget resolution.** `resolve_compaction_budget(deps)` returns `deps.model_max_ctx` (Ollama probe capped by `config.llm.max_ctx`, set at bootstrap).

**Boundary planner invariant.** `_MIN_RETAINED_TURN_GROUPS = 1` is non-configurable: the last turn group is retained unconditionally even when its tokens alone exceed `tail_fraction × budget`. Since `group_by_turn` splits at every `UserPromptPart`, this also guarantees `tail_start ≤ latest_user_idx` — active-user anchoring needs no explicit step.

Edge cases: returns `None` when `len(groups) < 2` or when the backward walk produces `tail_start ≤ head_end` (head and tail would overlap).

**Compaction assembly** (`apply_compaction`) — shared by `proactive_window_processor`, overflow recovery, and `/compact`:

Summarizes `messages[head_end:tail_start]` via `_gated_summarize_or_none`, then assembles:
`head | marker | [todo_snapshot] | [search breadcrumbs] | tail`

Raw `summary_text` is stored in `ctx.deps.runtime.previous_compaction_summary` before `build_compaction_marker` adds the `[CONTEXT COMPACTION — REFERENCE ONLY]` prefix; on failure the field is left untouched. Sets `compaction_applied_this_turn = True`, `post_compaction_token_estimate = estimate_message_tokens(result)`, and `message_count_at_last_compaction = len(result)`. After the turn, `_finalize_turn()` in `co_cli/main.py` reads `compaction_applied_this_turn` and passes it as `history_compacted=True` to `persist_session_history()`, which overwrites the transcript in place instead of appending.

Summarizer surface:
- `_summarization_gate_open(ctx)` — `False` when `ctx.deps.model is None` or circuit breaker tripped (and not at probe cadence).
- `summarize_dropped_messages(ctx, dropped, *, focus, previous_summary=None)` — pure LLM call; raises on failure.
- `_gated_summarize_or_none(...)` — gate + announce + summarizer + validity check + fallback; returns `None` on any failure path (exception, empty/whitespace-only return, or gate closed).

**Marker** — `ModelRequest` / `UserPromptPart` with a `[CONTEXT COMPACTION — REFERENCE ONLY]` prefix, N-message count, `summary_text`, and a verbatim-tail trailer instructing the model to treat the summary as reference, not active instructions. Detection via `startswith(SUMMARY_MARKER_PREFIX)` — shared constant used by both builder and detector.

**Breadcrumb preservation** (`_preserve_search_tool_breadcrumbs`): reconstructs paired `search_tools` call/return cycles from `dropped`. Scans `ModelResponse` parts for `search_tools` `ToolCallPart`s (indexed by `tool_call_id`), then for each matching `ToolReturnPart` in `ModelRequest` parts, emits a fresh `ModelResponse([call_part])` + `ModelRequest([return_part])` pair. Orphan returns with no paired call are dropped rather than emitted as broken shapes. Only `search_tools` cycles are preserved; all other tool results are dropped from the breadcrumb list.

**Anti-thrashing** (proactive path only): savings = `(tokens_before − tokens_after) / tokens_before`. Below `min_proactive_savings` increments `consecutive_low_yield_proactive_compactions`; at or above resets it to 0.

**Circuit breaker:**

| `compaction_skip_count` | Behavior |
|---|---|
| `0–2` (healthy) | Attempt summarizer. Valid summary → reset to 0. Failure or empty return → static marker, increment. |
| `≥ 3` (tripped) | Skip summarizer; static marker; increment. Probe once every `_COMPACTION_BREAKER_PROBE_EVERY` (10) skips — probe success resets to 0. |

`ctx.deps.model is None` (sub-agent without configured model) is also a bypass.

### 2.6 Enrichment helper

`gather_compaction_context` collects signal that survives `dedup_tool_results` / `evict_old_tool_results` / `enforce_request_size`. Called from `summarize_dropped_messages` so every LLM-capable compaction path inherits it.

| Source | Function | Why it survives |
|---|---|---|
| File paths from `ToolCallPart.args` (`FILE_TOOLS`) | `_gather_file_paths(dropped)` | `evict_old_tool_results` clears return content only, not call args. |
| Session todos | `_gather_session_todos` | Orthogonal to message history. |
| Prior summaries in dropped range | `_gather_prior_summaries` | Detected via `SUMMARY_MARKER_PREFIX`; skipped when `previous_compaction_summary` is set (iterative branch embeds it directly as `PREVIOUS SUMMARY:`). |

Output: single `str`; each source capped independently (file paths 1,500 chars, todos 1,500 chars, prior summaries 2,000 chars); joined result bounded at 4,000 chars. Returns `None` when no sources yield content.

### 2.7 Overflow recovery

Single-tier strip-then-summarize. Inlined in `run_turn`'s overflow branch (no separate orchestrator helper).

1. **Trigger:** `ModelHTTPError` classified by `_http_error_classifier.is_context_overflow`: HTTP 413 unconditionally; HTTP 400 with explicit overflow evidence in `error.message`, `error.code`, or `error.metadata.raw`. Overflow phrases recognized from OpenAI, Ollama, Gemini, vLLM, AWS Bedrock.
2. **Rate limit:** gated by `turn_state.overflow_recovery_attempted` — one-shot per turn.

**Algorithm** (`recover_overflow_history`, cascade flow shown in Diagram 1's `OVF` subgraph):

1. **Strip** every `ToolReturnPart` to a per-tool semantic marker via `strip_all_tool_returns`. No `COMPACTABLE_TOOLS` filter, no recency cap, no boundary protection — every tool return, including writes / approvals / memory ops, collapses to a one-line stub. Pairing is preserved (only `.content` is rewritten; `tool_name` and `tool_call_id` survive). Idempotent: returns whose content is already a marker (`is_cleared_marker(content)` true) pass through unchanged so re-running over an EVICT-stripped history does not degrade the size signal.
2. **Budget gate.** If `estimate_message_tokens(stripped) <= budget`, return the stripped history directly (no LLM call) and set `compaction_applied_this_turn` via `_mark_compaction_applied`.
3. **Summarize.** Else run `plan_compaction_boundaries(stripped, budget, tail_fraction)` + `apply_compaction(announce=False)` on the stripped history. Returns `None` when the planner cannot find valid bounds (`len(groups) < 2`, or `tail_start <= head_end`) — caller drives the terminal error path.
4. **Reset thrash.** Both return paths call `_reset_thrash_state(ctx)` — recovery proves the system needed to compact, so the next proactive run is unblocked. `apply_compaction` itself does NOT reset thrash state (proactive callers must keep their own gate).

**Message-list shape at each path:**

```text
PATH 1 — strip-only-fits (budget met after strip)
─────────────────────────────────────────────────────────────────────
  SystemPrompt
  ModelRequest    UserPromptPart   (1st turn)
  ModelResponse   TextPart / ToolCallPart
  ModelRequest    ToolReturnPart   ← content rewritten to "[tool_name] …"
  ...                              ← every ToolReturnPart stripped
  ModelRequest    UserPromptPart   (pending — preserved at tail)

      │  retry once with stripped history
      ▼  user turn continues


PATH 2 — strip + summarize (budget still exceeded after strip)
─────────────────────────────────────────────────────────────────────
  SystemPrompt
  ModelRequest    UserPromptPart   (1st turn)         ┐ pinned head
  ModelResponse   TextPart         (1st turn)         ┘
  ModelRequest    UserPromptPart   (COMPACTION MARKER)    ← summary or static
  ModelRequest    UserPromptPart   (todo snapshot)
  ModelRequest    ToolReturnPart   (search breadcrumbs)
  ModelRequest / ModelResponse    tail turns          ← retained, returns stripped

      │  retry once
      ▼  user turn continues
```

On a non-`None` result, `run_turn` sets `current_history = compacted`, clears pending input, emits `"Context overflow — compacting and retrying..."`, and retries. Terminal error (`"Context overflow — unrecoverable."`) on `None` or on a second overflow in the same turn.

### 2.8 Summarizer

`summarize_messages(deps, messages, *, personality_active, context, focus, previous_summary=None)` — `llm_call()` with no tools; agent constructed per call; `deps.model.settings_noreason`. System prompt enforces treating history as data, not instructions.

**Two-branch prompt:**
- **From-scratch** (`previous_summary is None`): `_SUMMARIZE_PROMPT` directly.
- **Iterative update** (`previous_summary` set): `_build_iterative_template(previous_summary)` prepends `PREVIOUS SUMMARY:` and PRESERVE / ADD / MOVE / REMOVE discipline before the shared template.

**Cross-compaction feedback loop** — `previous_compaction_summary` is read and written inside `apply_compaction` before every summarizer call, threading the prior summary across compaction boundaries:

```mermaid
flowchart LR
    c1["Compaction N\nruntime.previous_compaction_summary = None\n→ from-scratch branch"]:::head
    write1["runtime.previous_compaction_summary\n= summary_text_N\n(written only on success)"]:::tail
    c2["Compaction N+1\nruntime.previous_compaction_summary = summary_text_N\n→ iterative-update branch"]:::head
    write2["runtime.previous_compaction_summary\n= summary_text_N+1\n(written only on success)"]:::tail
    fail["summarizer fails:\nfield left untouched — next\ncompaction reuses summary_text_N"]:::prior
    c1 --> write1 --> c2 --> write2
    write1 -.->|"on failure"| fail

    classDef head fill:#e8f4f8,stroke:#6baed6
    classDef tail fill:#d4edda,stroke:#74c476
    classDef prior fill:#f5f5f5,stroke:#9e9e9e,stroke-dasharray:3 3
```

**Template sections:** `## Active Task`, `## Goal`, `## Key Decisions`, `## User Corrections`†, `## Errors & Fixes`, `## Completed Actions`, `## In Progress`, `## Remaining Work`, `## Working Set`, `## Pending User Asks`†, `## Resolved Questions`†, `## Next Step` (verbatim drift anchor), `## Critical Context`†. (†omitted when empty)

### 2.9 Base system prompt advisory

`RECENCY_CLEARING_ADVISORY` — static, cacheable `## Tool result recency` section; no per-turn interpolation; `5` sourced from `COMPACTABLE_KEEP_RECENT` at module-load time:

> "## Tool result recency
>
> Tool results may be automatically cleared from context to free space. The 5 most recent results per tool type are always kept. Note important information from tool results in your response — the original output may be cleared on later turns."

### 2.10 Trigger cadence and self-stabilization

`proactive_window_processor` fires before every `ModelRequestNode` but produces at most one summarizer call per pressure event per turn:
- After a successful compaction, `token_count` drops below threshold — subsequent passes hit the fast path.
- A static-marker compaction also shrinks context — same result.
- `plan_compaction_boundaries` returning `None` (head/tail overlap) leaves messages unchanged.

### 2.11 Error handling and degradation

| Failure mode | Fallback |
|---|---|
| Summarizer raises OR returns empty/whitespace | Static marker; warning logged; `compaction_skip_count += 1` |
| `compaction_skip_count >= 3` | Circuit breaker tripped — static markers used; LLM probed once every 10 skips |
| `ctx.deps.model is None` (sub-agent context) | Static marker without LLM attempt |
| `plan_compaction_boundaries` returns `None` (proactive) | Return messages unchanged; provider may then reject → overflow path |
| `plan_compaction_boundaries` returns `None` (overflow, after strip) | `recover_overflow_history` returns `None`; caller emits `"Context overflow — unrecoverable."` |
| Second overflow in same turn | Terminal error (gated by `overflow_recovery_attempted`) |

### 2.12 Security

- Summarizer system prompt contains a CRITICAL SECURITY RULE treating history as data, not instructions.
- Emit-time persisted files are content-addressed by SHA-256; filenames leak no semantics.
- Tool-result files live under `.co-cli/tool-results/` (project-local). Cleanup is manual; warning surfaced when directory > 100 MB via `check_tool_results_size`.

## 3. Config

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `llm.max_ctx` | — | `32768` | Ceiling on the Ollama-probed context window; `deps.model_max_ctx = min(probe, max_ctx)`. Used as the compaction budget. |

**Compaction tuning** (`CompactionSettings` in `co_cli/config/compaction.py`):

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `compaction.compaction_ratio` | `CO_COMPACTION_RATIO` | `0.50` | Fraction of budget above which `proactive_window_processor` fires |
| `compaction.tail_fraction` | `CO_COMPACTION_TAIL_FRACTION` | `0.20` | Fraction of budget targeted for the preserved tail |
| `compaction.spill_ratio` | `CO_COMPACTION_SPILL_RATIO` | `0.50` | Fraction of context window above which `enforce_request_size` force-spills tool returns. Validated `≤ compaction_ratio` so post-spill aggregate falls below proactive's trigger and proactive fast-paths. |
| `compaction.min_proactive_savings` | `CO_COMPACTION_MIN_PROACTIVE_SAVINGS` | `0.10` | Minimum token savings fraction to count a proactive compaction as effective (anti-thrashing) |
| `compaction.proactive_thrash_window` | `CO_COMPACTION_PROACTIVE_THRASH_WINDOW` | `2` | Consecutive low-yield proactive compactions before anti-thrashing gate activates |

**Non-configurable module constants:**

| Constant | Value | Purpose |
|---|---|---|
| `_MIN_RETAINED_TURN_GROUPS` | `1` | Hardcoded correctness invariant — last turn group always retained |
| `COMPACTABLE_KEEP_RECENT` | `5` | `evict_old_tool_results`: most-recent returns per tool to keep |
| `_COMPACTION_BREAKER_TRIP` | `3` | Consecutive failures that trip the circuit breaker |
| `_COMPACTION_BREAKER_PROBE_EVERY` | `10` | Skips between probe attempts when circuit breaker is tripped |

**Spill / request-budget constants** (module-level; not user-configurable):

| Constant | Source | Value | Purpose |
|---|---|---|---|
| `SPILL_THRESHOLD_CHARS` | `co_cli/tools/tool_io.py` | `4,000` | `spill_if_oversized` default per-tool emit-time spill threshold |
| `TOOL_RESULT_PREVIEW_CHARS` | `co_cli/tools/tool_io.py` | `1,500` | Preview chars included in the `<persisted-output>` placeholder |
| `CHARS_PER_TOKEN` | `co_cli/context/tokens.py` | `4` | Fast chars→tokens proxy used by `enforce_request_size` aggregate estimate |

**L2 spill threshold** (`deps.spill_threshold_tokens`) — computed at bootstrap as `int(spill_ratio * model_max_ctx)` and cached on `CoDeps`; not directly configurable (controlled via `compaction.spill_ratio` and `llm.max_ctx`).

**Per-tool `spill_if_oversized` overrides** (set via `@agent_tool(spill_threshold_chars=...)` in each tool's own file):

| Tool | `spill_threshold_chars` | Notes |
|---|---|---|
| Default | `None` (→ `SPILL_THRESHOLD_CHARS`) | falls through to module constant |
| `file_read` | `math.inf` | never spills |

## 4. Files

| File | Role |
|---|---|
| `co_cli/config/compaction.py` | `CompactionSettings` — ratios, thresholds, and anti-thrashing knobs. |
| `co_cli/context/compaction.py` | Public entry surface: `proactive_window_processor` processor, overflow recovery, `apply_compaction`, summarizer gate, re-exports. |
| `co_cli/context/_compaction_boundaries.py` | Boundary planner: `TurnGroup`, `group_by_turn`, `plan_compaction_boundaries`, active-user anchoring. |
| `co_cli/context/_compaction_markers.py` | Marker builders, `gather_compaction_context` enrichment helper, `SUMMARY_MARKER_PREFIX`. |
| `co_cli/context/_dedup_tool_results.py` | `dedup_tool_results` helpers: content hash, eligibility predicate (`is_dedup_candidate`), back-reference builder (`build_dedup_part`). |
| `co_cli/context/history_processors.py` | Pure history processors (registered): `dedup_tool_results`, `evict_old_tool_results`, `enforce_request_size`, `sanitize_surrogate_codepoints`. Recovery helper (unregistered): `strip_all_tool_returns`. |
| `co_cli/tools/lifecycle.py` | `CoToolLifecycle` capability: dedup tool calls (`before_node_run`), per-call cap brake (`wrap_tool_execute`), L0 tool-call-limit span (`after_node_run`), JSON repair (`before_tool_validate`), path normalization (`before_tool_execute`), audit/span enrichment (`after_tool_execute`). |
| `co_cli/context/_tool_result_markers.py` | `semantic_marker` per-tool format and `is_cleared_marker` predicate. |
| `co_cli/context/summarization.py` | `summarize_messages`, token estimator, budget resolver, and prompt templates. |
| `co_cli/context/tokens.py` | `CHARS_PER_TOKEN` shared constant. |
| `co_cli/context/_http_error_classifier.py` | `is_context_overflow` — provider overflow detection for 400/413. |
| `co_cli/context/orchestrate.py` | `run_turn` overflow dispatch and anti-thrash gate reset. |
| `co_cli/main.py` | `_finalize_turn()` — session persistence bridge; reads `compaction_applied_this_turn` and calls `persist_session_history(history_compacted=True)` to rewrite the transcript. |
| `co_cli/tools/categories.py` | `COMPACTABLE_TOOLS`, `FILE_TOOLS`, `PATH_NORMALIZATION_TOOLS`. |
| `co_cli/tools/tool_io.py` | `spill_if_oversized`: `spill_if_oversized`, `tool_output`, `check_tool_results_size`; `SPILL_THRESHOLD_CHARS`, `TOOL_RESULT_PREVIEW_CHARS`. |
| `co_cli/tools/files/read.py` | `file_read` per-tool `spill_threshold_chars=math.inf` override (never spills). |
| `co_cli/agent/tool_call_limit.py` | `MAX_TOOL_CALLS_PER_MODEL_TURN`, `MaxToolCallsExceededPayload`, `make_exceeded_payload`. |
| `co_cli/config/llm.py` | `max_ctx` (Ollama probe ceiling). |
| `co_cli/context/assembly.py` | Prompt assembly: `build_static_instructions`; static `RECENCY_CLEARING_ADVISORY` recency-clearing paragraph. |
| `co_cli/context/rules/` | Base system prompt rule files (identity, safety, reasoning, tool protocol, workflow). |
| `evals/eval_compaction_proactive.py` | Proactive compaction end-to-end eval. |
| `evals/eval_compaction_multi_cycle.py` | Multi-cycle compaction fidelity eval. |

## 5. Test Gates

| Property | Test file |
|---|---|
| `spill_if_oversized`: spill path: oversized result spilled to disk, placeholder format confirmed | `tests/test_flow_spill_threshold.py` |
| `spill_if_oversized` constant values pinned: `SPILL_THRESHOLD_CHARS`, `TOOL_RESULT_PREVIEW_CHARS` | `tests/test_flow_spill_threshold.py` |
| `spill_if_oversized` threshold boundary: below 4000 passes through unchanged; above 4000 spills | `tests/test_flow_spill_threshold.py` |
| `spill_if_oversized` `force=True`: force-spills even below threshold when above preview size | `tests/test_flow_spill_threshold.py` |
| `spill_if_oversized` OTEL: `tool_budget.spill_tool_result` span emitted; tracer name `co-cli.tool_budget` | `tests/test_flow_spill_otel.py` |
| `enforce_request_size`: below-threshold fast path; largest-first spill across full message list; cross-batch accumulation (multiple `ModelRequest`s); already-spilled exclusion; OTEL span `tool_budget.enforce_request_size` | `tests/test_flow_enforce_request_size.py` |
| `enforce_request_size` cached threshold: `spill_threshold_tokens` from `CoDeps` used without recompute | `tests/test_flow_enforce_request_size.py` |
| L0 tool-call cap: `MAX_TOOL_CALLS_PER_MODEL_TURN` constant pinned; allow up to cap; reject above cap with JSON payload | `tests/test_flow_tool_call_limit.py` |
| L0 run_step counter: resets on `ctx.run_step` transition | `tests/test_flow_tool_call_limit.py` |
| `dedup_tool_results`: identical return collapses to back-reference; short content and distinct content pass through | `tests/test_flow_history_processors.py` |
| `evict_old_tool_results`: clears oldest when over keep limit; keeps all at limit; protects last-turn returns | `tests/test_flow_history_processors.py` |
| `evict_old_tool_results` + `is_cleared_marker`: cleared markers recognized; recent returns left untouched | `tests/test_flow_history_processors.py` |
| `group_by_turn` correctly partitions multi-turn message list into turn groups | `tests/test_flow_history_processors.py` |
| `proactive_window_processor` below-threshold fast path: messages object returned unchanged, no compaction | `tests/test_flow_compaction_proactive.py` |
| `proactive_window_processor` above-threshold compaction: result shorter than input, compaction marker present | `tests/test_flow_compaction_proactive.py` |
| `proactive_window_processor` anti-thrashing gate: skips compaction after consecutive low-yield passes | `tests/test_flow_compaction_proactive.py` |
| Circuit breaker cadence: counts 0–2 open, 3–12 closed, 13 probe, 14–22 closed, 23 probe | `tests/test_flow_compaction_proactive.py` |
| Circuit breaker counter resets to 0 after a successful (non-empty) LLM compaction | `tests/test_flow_compaction_proactive.py` |
| Boundary planner: valid `(head_end, tail_start, dropped_count)` for 3-turn history | `tests/test_flow_compaction_boundaries.py` |
| Boundary planner: returns `None` when only 1 turn group (nothing to drop) | `tests/test_flow_compaction_boundaries.py` |
| Boundary planner: oversized last-group retained unconditionally even over tail budget | `tests/test_flow_compaction_boundaries.py` |
| `find_first_run_end` anchors at first `TextPart` response, skips tool-only responses | `tests/test_flow_compaction_boundaries.py` |
| `estimate_message_tokens` scales with content length; returns 0 for empty list | `tests/test_flow_compaction_summarization.py` |
| `resolve_compaction_budget` returns `deps.model_max_ctx` | `tests/test_flow_compaction_summarization.py` |
| Summarizer from-scratch branch: returns non-empty structured text | `tests/test_flow_compaction_summarization.py` |
| Summarizer iterative branch: output incorporates prior summary and new turns | `tests/test_flow_compaction_summarization.py` |
| Overflow recovery — strip-only-fits: oversized tool returns rewritten to per-tool markers; message count preserved; pending user turn preserved | `tests/test_flow_compaction_recovery.py` |
| Overflow recovery — strip+summary path: emits static marker (model=None gate); recovered history shorter than input | `tests/test_flow_compaction_recovery.py` |
| Overflow recovery — terminal: single-turn history returns `None` when planner cannot find bounds | `tests/test_flow_compaction_recovery.py` |
| Overflow recovery — pairing: every `tool_call_id` in `ToolCallPart`s matches a `ToolReturnPart` (both paths) | `tests/test_flow_compaction_recovery.py` |
| `strip_all_tool_returns` idempotent on already-marked content | `tests/test_flow_compaction_recovery.py` |
| `is_context_overflow`: 413 is unconditional; 400 requires overflow evidence in body | `tests/test_flow_http_error_classifier.py` |
| `/clear` resets all compaction runtime fields to initial state | `tests/test_flow_slash_commands.py` |
