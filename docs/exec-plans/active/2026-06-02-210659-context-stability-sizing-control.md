# context-stability-sizing-control

> **Status: PARTIAL DELIVERY — pre-Gate-1 for the remainder.** ISSUE-1 (proportional tail,
> `tail_fraction 0.20→0.10`) and ISSUE-1.5 (floor-aware trigger estimate) are **shipped**; ISSUE-4a's
> instruction-block guard already exists (via the `rules-block-trim-finish` sibling plan); **ISSUE-2
> (anti-thrash static-marker fallback) was extracted to its own plan —
> `docs/exec-plans/active/2026-06-03-220905-antithrash-static-marker-fallback.md` — and is no longer
> tracked here.** This plan now tracks the remaining dynamics issues (3, 5), the tool-schema reduction
> (4b), and the loop-stability eval. Baseline numbers below reflect the post-delivery shipped state.

## Context

The 64k operational window (`num_ctx`/`max_ctx` = 65,536) is utilized in the agentic loop via a
proactive trigger and tool-return spill that both fire at `0.50 × 65,536 = 32,768` tokens. The
tail+floor work (ISSUE-1, ISSUE-1.5) has widened the compressible middle within that budget; the
remaining issues attack the compaction *logic* that the sizing fixes left untouched — a gate that can
disable compaction entirely, a missing spill/summarize margin, and an uncapped live tool-read path.

### Baseline (post-delivery)

Resolved thresholds against the current loop:

| Control | Site | Resolved |
|---|---|---|
| Per-tool-result spill | `tool_io.SPILL_THRESHOLD_CHARS` | 4,000 chars (`file_read`/`*_view` = ∞) |
| Request-level tool spill | `spill_largest_tool_results`, `spill_ratio 0.50` | 32,768 tok (`deps.spill_threshold_tokens`) |
| Proactive summarize | `proactive_window_processor`, `compaction_ratio 0.50` | 32,768 tok |
| **Trigger local estimate** | `effective_request_tokens` (**floor-aware, shipped**) | `static_floor_tokens + estimate_message_tokens` |
| Preserved tail target | `tail_fraction 0.10` (**shipped, was 0.20**) | 6,554 tok (≈20% of the 32,768 trigger) |
| Output cap / turn | reasoning `max_tokens` | 4,096 tok |
| Parallel tool calls | `MAX_TOOL_CALLS_PER_MODEL_REQUEST` | 3 |

**Fixed floor present on every call (~11,438 tok, measured post rules-trim):**

| Component | tokens |
|---|---|
| static instructions (seed + mindsets + rules) | 5,838 |
| ALWAYS tool schemas | 4,950 |
| **`static_floor_tokens` (counted by the floor-aware trigger)** | **10,788** |
| per-turn parts (current_time, deferred-tool-awareness, skill manifest; excluded from trigger v1) | ~650 |

Trivial turns are **prefill-bound** (latency tracks input size, not output), so floor reduction
(ISSUE-4b) remains a direct latency lever.

**Operational-budget partition (against the 32,768 trigger), post-delivery:**

| Region | tokens | % of trigger | Compactable? |
|---|---|---|---|
| Fixed floor (`static_floor_tokens`) | 10,788 | 32.9% | No |
| Protected tail (`tail_fraction 0.10`) | 6,554 | 20.0% | No |
| **Compressible middle** | **~15,426** | **~47.1%** | Yes |

The shipped tail+floor work lifted the compressible middle from ~23.5% to ~47% — the remaining issues
keep the compaction machinery from squandering that headroom.

### Delivered (shipped ahead of the remaining plan)

- **ISSUE-1 — proportional tail.** `tail_fraction` default lowered `0.20 → 0.10` in
  `co_cli/config/compaction.py` (the preferred shape — knob value, not formula re-base). Tail is now
  ~20% of the operational budget (was 40%). Coherence still pending the loop-stability eval (see Open
  Questions).
- **ISSUE-1.5 — floor-aware trigger estimate.** New bootstrap-measured `deps.static_floor_tokens`
  (static instructions + ALWAYS schemas, measured live via `co_cli/bootstrap/schema_budget.py`); new
  `effective_request_tokens(deps, messages)` makes the L2/L3 trigger local floor-inclusive; savings
  and `commit_compaction` now use a floor-inclusive `tokens_after`. Specs (`compaction.md`,
  `core-loop.md`) synced. Closes the within-turn undercount (stale/zeroed/missing report).
- **ISSUE-4a — instruction-block guard.** `tests/test_instruction_budget.py` already exists (landed
  via `rules-block-trim-finish`); this plan reads that single guard rather than adding a second.

### Peer-survey alignment (`docs/reference/RESEARCH-context-management-peer-survey.md`)

Cross-checked against the peer survey (hermes-agent, openclaw, opencode, codex):

- **Different axis from the survey's open gaps.** The survey's cross-session / long-loop / multi-agent
  items are a separate axis; this plan is entirely **within-session request-window dynamics** (see Out
  of scope).
- **Per-issue peer position** (detail in each ISSUE block): ISSUE-5 — co is the lone peer outlier with
  an uncapped live tool-output path. ISSUE-3 (now the drop-reported plan) — co is the lone chain-shaped
  peer carrying a provider-reported floor (`max(local, reported)`) in its compaction trigger; hermes and
  openclaw drive the same spill→summarize chain off realtime-local only. ISSUE-4b — co's per-tool schemas are already the leanest surveyed; the
  lever is deferral, not docstring squeeze. (ISSUE-2's beyond-parity anti-thrash fix moved to its
  extracted plan.)
- **Architecture confirmation.** Co's per-request compaction cadence already does (more aggressively)
  what openclaw's mid-turn precheck does — this plan adds **no new cadence**, only threshold tuning.

## Problem & Outcome

**Problem.** Gap-prone compaction logic still makes total context unstable: spill and summarize have no
separating margin, and a single tool read can inject an unbounded inline chunk. The fixed floor — the
highest-leverage latency lever — is now guarded on its instruction half but tool schemas remain
over-eager. Together these produce middle-section dilution and growth-to-overflow under realistic
multi-turn load. (The tail disproportion and the floor-blind trigger are now fixed — see Delivered; the
anti-thrash no-op is handled by the extracted ISSUE-2 plan.)

**Outcome.** Spill reliably runs before LLM summarization; large tool reads cannot single-handedly
dominate the middle; tool schemas keep shrinking; and a loop-stability test proves the window stays
bounded — and the agent stays coherent — under a long multi-turn conversation.

**Failure cost:** silently, the agent loop grows context past 32,768 in long multi-turn sessions until
the model returns a context-overflow error; the reactive last-resort recovery
(`_attempt_overflow_recovery`) then dumps the entire pre-tail in one unbounded cut — destroying recent
reasoning the user was mid-task on — or, if that also fails, the turn dies with "Context overflow —
unrecoverable." No alarm fires before the cliff; the only visible symptom beforehand is degraded
answer quality from a diluted middle.

**Latency dimension (not just stability).** ISSUE-1.5 (shipped) made the trigger floor-aware. The
remaining fixes are also a per-turn latency lever: an LLM summarization pass dominates a turn's
wall-clock. ISSUE-3 (now the drop-reported plan — realtime-only trigger) suppresses the avoidable
summarization call after a spill fits the payload; ISSUE-4b (schema reduction) cuts
latency on *every* prefill-bound call.

## High-Level Design

Both fronts are localized — no new abstractions:
- **Dynamics (ISSUE-3/5).** All ratio knobs live in `co_cli/config/compaction.py`, consumed by
  `co_cli/context/compaction.py` (proactive trigger + boundaries) and
  `co_cli/context/history_processors.py` (spill/evict/dedup), plus the per-tool spill threshold in
  `co_cli/tools/`. ISSUE-3 was split out and superseded by the drop-reported plan (remove `reported`
  from both triggers — no ratio change); this plan keeps the ISSUE-5 emission cap. (The tail re-size and
  the floor-aware trigger estimate are already shipped; the anti-thrash gate behavior is the extracted
  ISSUE-2 plan.)
- **Floor (ISSUE-4).** Reduce tool schemas via deferral (ISSUE-4b). The instruction-half guard
  (ISSUE-4a) already exists; no source change to rules.
- **Validation.** A new loop-stability eval exercises the dynamics the synthetic-fixture unit tests do
  not — it gates the combined behavior and the shipped `tail_fraction 0.10` coherence.

Each issue below carries its own implementation block (Files / Action / done_when / success_signal) so
the analysis and the work that resolves it stay together. Cross-cutting validation follows the issues.

## Issues, Fixes & Implementation

### ISSUE-2 — Anti-thrash gate disables compaction entirely (no-op → growth) — SHIPPED ELSEWHERE
Extracted and shipped: `docs/exec-plans/completed/2026-06-03-220905-antithrash-static-marker-fallback.md`.

### ISSUE-3 — split out and superseded; see `docs/exec-plans/active/2026-06-04-130800-drop-reported-realtime-trigger.md` (root cause was `reported` in the trigger `max()`, not the spill/summarize band).

### ISSUE-4 — The fixed floor is the highest-leverage lever; instruction half guarded, schemas are not
The floor (~11,438 tok) is ~33% of the trigger and present in every post-compaction state, so reducing
it raises effective headroom on every call and after every compaction. It has two uncompactable halves:

| Half | tokens | Regression guard? |
|---|---|---|
| **Instruction block** (seed + mindsets + rules) | **5,838** | **Yes — `tests/test_instruction_budget.py`** (ISSUE-4a, delivered via `rules-block-trim-finish`) |
| Tool schemas (ALWAYS-visibility) | 4,950 | `tests/test_orchestrator_schema_budget.py` |

**ISSUE-4a — instruction-block guard: SATISFIED.** `tests/test_instruction_budget.py` already exists. It
measures `build_static_instructions(deps.config)` (seed + mindsets + rules), pins a ceiling
(`INSTRUCTION_BLOCK_CEILING = 23_750` chars / ~5,838 tok measured), and fails if the block grows past it —
the instruction-half counterpart to `test_orchestrator_schema_budget.py`. This plan reads that single guard
rather than adding a second rules-only test. No further action. (Rules are guarded against bloat, not
trimmed for size — always-on protocol is a deliberate small-model affordance.)

**ISSUE-4b — tool deferral is under-used.** Only ~5 of ~35 native tools are DEFERRED; the rest are
ALWAYS-visible. A peer comparison (hermes-agent, openclaw) found co's per-tool schemas already the leanest
of the three (~175 tok/tool vs hermes' ~400, which ships all tools always-visible) — so the highest-yield
lever is **deferring more ALWAYS tools**, not squeezing docstrings that are already tight.

**Implementation (ISSUE-4b).**
- **Files:** ALWAYS-visibility tool modules selected by re-auditing the ALWAYS/DEFERRED schema buckets,
  `tests/test_orchestrator_schema_budget.py`.
- **Action:** Audit ALWAYS-visibility tools and **move DEFERRED-able ones behind ToolSearch first** (the
  highest-yield lever), then tighten any remaining oversized schemas; lower the guard's
  `ALWAYS_BUCKET_CEILING` (currently `20_200`, measured ~19,800) and `PER_ALWAYS_TOOL_CEILING` to the new
  measured values. **Re-pin via the shared `measure_always_schema_budget` helper** (ISSUE-1.5 added it; the
  guard already reads it) — because `deps.static_floor_tokens` is measured live, the trigger floor
  auto-updates, so no separate re-pin there.
- **prerequisites:** none.
- **done_when:** measured ALWAYS bucket is **below 19,000 chars** (from ~19,800 — a re-pin to the current
  measurement is not acceptable; a real reduction is required) and both ceiling constants are re-pinned to
  the new measurement; `uv run pytest tests/test_orchestrator_schema_budget.py -x` passes against the
  lowered pins.
- **success_signal:** per-call prefill on a trivial turn measures below the current ~11.4k-tok floor.

### ISSUE-5 — `file_read`/`*_view` exempt from per-emission spill (`spill_threshold_chars = ∞`)
A large read lands fully inline (up to `_READ_MAX_LINES=2000 × _READ_MAX_LINE_CHARS=2000`); only the
next request's `spill_largest_tool_results` force-spills it. A single read can dominate the middle between
passes.

**Fix.** Replace `∞` with a high-but-finite per-emission spill threshold for `file_read` (and the
`*_view` tools), so a single oversized read spills to disk at emission rather than injecting a large
inline block. Keep the threshold high enough that normal ranged reads stay inline.

**Peer validation (clearest "co is behind" case).** **Every** surveyed peer caps *live* tool output
(openclaw `TOOL_RESULT_MAX_CHARS=8000` head-only, opencode `TOOL_OUTPUT_MAX_CHARS=2000` in-place);
co's `∞` exemption makes it the **lone outlier with an uncapped live-output path**. The fix brings co
to parity *and* preserves co's unique edge — co spills to disk (**recoverable** behind a placeholder +
preview) where peers **truncate** (lossy). So this is parity on bounding plus a retained advantage.

**Implementation.**
- **Files:** `co_cli/tools/files/read.py`, `co_cli/tools/memory/view.py`, `co_cli/tools/session/view.py`,
  `co_cli/tools/system/skills.py`, `co_cli/tools/tool_io.py`, `tests/test_flow_files_read.py`.
- **Action:** Replace `spill_threshold_chars=math.inf` (at `read.py:397`, `memory/view.py:22`,
  `session/view.py:25`, `system/skills.py:37`) with a high finite threshold; verify normal ranged reads
  stay inline and an oversized read spills at emission.
- **prerequisites:** none.
- **done_when:** an oversized `file_read` returns a persisted-output reference (spilled); a normal ranged
  read returns inline; `uv run pytest tests/test_flow_files_read.py -x` passes.
- **success_signal:** a single full-file `file_read` no longer injects a >threshold inline block into the
  middle in the loop-stability eval.

### Loop-stability eval — cross-cutting validation
Validates the combined behavior of ISSUE-3/5 and the shipped tail+floor work; not tied to a single issue.
- **Files:** `evals/eval_context_stability.py` (EXTEND — the file is **created by the extracted ISSUE-2
  plan** with a text/reasoning-heavy phase; this plan adds the tool-output-heavy phase below).
- **Action:** Add a **tool-output-heavy** pressure phase to the eval: drive a long multi-turn
  conversation (real LLM, real tools, large tool outputs) past the trigger and assert no
  context-overflow error, bounded number of compaction passes, post-pass total stays below the trigger,
  and that spill-to-disk passes precede LLM-summarization passes under tool-output pressure (drop-reported
  trigger / ISSUE-5 emission cap). A tool-output-heavy middle has spillable `ToolReturnPart` candidates, so this
  phase exercises spill before summarize — the opposite of the extracted plan's text-heavy phase. Real
  everything (per `feedback_eval_real_world_data`).
- **prerequisites:** the drop-reported plan
  (`docs/exec-plans/active/2026-06-04-130800-drop-reported-realtime-trigger.md`, which superseded ISSUE-3)
  and ISSUE-5 fixes required. The extracted ISSUE-2 plan must ship first (it
  creates the eval file and lands the anti-thrash fix this eval's bounded-loop assertion relies on).
  (ISSUE-1 + ISSUE-1.5 already shipped; this eval is also the **hard coherence gate** for the shipped
  `tail_fraction 0.10` — the extracted plan deliberately logs coherence rather than gating, leaving the
  revert lever here — see Open Questions.)
- **done_when:** `uv run python evals/eval_context_stability.py` runs to completion with the loop bounded
  (no overflow error, every triggered pass reduces tokens, post-pass total below trigger), the
  tool-output phase confirms spill precedes summarization under tool-output pressure, **AND** the agent
  still completes the multi-turn task correctly after compaction — a coherence assertion (e.g. the agent
  recalls a fact stated before the first compaction; a **soft, real-LLM single-run gate**, not
  machine-deterministic), so a bounded-but-incoherent loop fails the eval; result block documented in the
  Delivery Summary.

## Behavioral Constraints

- **Conservative small-model bias preserved** — the 0.50 operational ceiling exists for qwen3.6
  coherence; do not raise it without eval evidence. These fixes improve stability *within* the chosen
  ceiling, not by enlarging the window.
- **Real everything in evals** (`feedback_eval_real_world_data`): the loop-stability eval uses real
  deps/LLM/tools.
- **No backward-compat shims** (`feedback_zero_backward_compat`): change defaults directly; settings.json
  overrides remain the escape hatch.
- **Surgical**: touch only the sizing/compaction modules named per issue.

## Testing
- Scoped: `tests/test_flow_compaction_*.py`, `tests/test_flow_files_read.py`,
  `tests/test_orchestrator_schema_budget.py` (re-pinned by ISSUE-4b).
- `evals/eval_context_stability.py` — the empirical loop-bound gate.
- `scripts/quality-gate.sh full` at ship.

## Out of scope
- Enlarging the operational window beyond 0.50 (separate eval-gated decision).
- Server-side/provider-delegated compaction (`feedback_context_management_self_contained`).
- Provider-reported vs local token-estimate reconciliation (the `max(local, reported)` trigger basis is
  intentional; ISSUE-1.5 made the local half floor-inclusive, which is the shipped resolution).
- **Mid-turn / pre-emptive compaction cadence** (openclaw's `preemptive-compaction.ts`). No new cadence
  mechanism is needed: co's `proactive_window_processor` already runs **per LLM request** — between tool
  batches *within* a turn — which is *more* aggressive than openclaw's optional precheck. This plan tunes
  the existing cadence's thresholds, it does not add a cadence.
- **The peer-survey cross-session / long-loop / multi-agent items** (background-loop isolation,
  multi-agent provenance, and hermes-style per-session distillation — the last of which co rejects by
  design). A *different axis* — belongs to the dream daemon and any future subagent work.

## Open Questions
- ISSUE-3 (drop-reported plan) — resolved there: ratios stay 0.50/0.50, no band; the fix is removing
  `reported` from both triggers so a spill deterministically suppresses an unnecessary summarize. See
  `docs/exec-plans/active/2026-06-04-130800-drop-reported-realtime-trigger.md`.
- **ISSUE-1 coherence (now an eval validation item, not a pre-ship gate):** `tail_fraction 0.10` shipped
  ahead of the eval, halving the preserved recent reasoning chain (13,107→6,554 tok). That tail may be
  *intentional* small-model coherence headroom. The loop-stability eval must gate on coherence-preserved,
  not just bounded; **if it regresses, revert `tail_fraction` to a higher value** (the change is a one-line
  knob revert). **Peer evidence weighing in:** hermes-agent runs a tail = 20%-of-threshold in production
  and, like co, relies on the carried-forward summary marker as cross-compaction memory — evidence that a
  ~20%-of-operational-budget tail is coherent *when a summary marker backs it*. This lowers (not
  eliminates) the risk; the eval still decides whether to keep 0.10.

---

## Reference — code sites & logic

### A. Resolved thresholds — exact code sites

All ratio knobs: `co_cli/config/compaction.py` (`CompactionSettings`). Budget baseline:
`co_cli/context/summarization.py` `resolve_compaction_budget(deps)` → returns `deps.model_max_ctx`
(no ratio applied; callers apply their own). Live `model_max_ctx = 65,536` (`llm.max_ctx`; Ollama
Modelfile `num_ctx = 65_536`).

| Knob | Default | Site that applies it | Resolved |
|---|---|---|---|
| `compaction_ratio` | 0.50 | `compaction.py` `token_threshold = int(budget * cfg.compaction_ratio)` | 32,768 tok |
| `spill_ratio` | 0.50 | `deps.spill_threshold_tokens = int(spill_ratio * model_max_ctx)` (`deps.py`), used in `history_processors.py` | 32,768 tok (stays 0.50 — the drop-reported plan needs no band) |
| `tail_fraction` | **0.10** | `_compaction_boundaries.py` `tail_budget = tail_fraction * budget` | **6,554 tok** |
| `static_floor_tokens` | — (bootstrap-measured) | `bootstrap/core.py`; added to the local estimate via `effective_request_tokens` | ~10,788 tok |
| `min_proactive_savings` | 0.10 | low-yield test in `_record_proactive_outcome` | — |
| `proactive_thrash_window` | 2 | anti-thrash gate trip count (`compaction.py`) | — |
| `SPILL_THRESHOLD_CHARS` | 4,000 | `tool_io.py`, per-emission spill | 4,000 chars |
| per-tool `spill_threshold_chars` | ∞ | `file_read`, `memory_view`, `session_view`, `skill_view` | never per-emission spill (ISSUE-5) |
| `MAX_TOOL_CALLS_PER_MODEL_REQUEST` | 3 | `tool_call_limit.py` | 3 |
| output cap (reasoning) | 4,096 | `llm.py` `reasoning.max_tokens` | 4,096 tok/turn |
| output cap (noreason/summarize) | 8,192 | `llm.py` `noreason.max_tokens` | 8,192 tok |

### B. Floor composition (current, measured post rules-trim)

- static instructions 23,352 chars / 5,838 tok = seed + mindsets + rules (guarded by
  `test_instruction_budget.py`, ceiling 23,750 chars)
- ALWAYS tool schemas (name+desc+minified-params) ~19,800 chars / ~4,950 tok (guarded by
  `test_orchestrator_schema_budget.py`, ceiling 20,200 chars; ISSUE-4b drives this down)
- `static_floor_tokens` (trigger-counted, measured at bootstrap) = 5,838 + 4,950 ≈ **10,788 tok**
- per-turn parts ~650 tok (current_time + deferred-tool-awareness + skill manifest; excluded from
  `static_floor_tokens` in v1)
- floor on every call ≈ 10,788 + 650 ≈ **11,438 tok**

### C. The 5-layer size-defense chain (current loop)

Processor order (`Agent(history_processors=[...])`, `co_cli/context/history_processors.py`), cheap →
expensive, each request:
1. **`dedup_tool_results`** — collapse identical-content tool returns to back-references.
2. **`evict_old_tool_results`** — content-clear compactable tool returns beyond `COMPACTABLE_KEEP_RECENT
   = 5` per tool name. Bounds *tool-return* bloat only.
3. **`spill_largest_tool_results`** — at `spill_threshold_tokens` (32,768), force-spill the largest unspilled
   `ToolReturnPart`s to disk (`force=True` bypasses the 4,000-char per-tool threshold). Trigger basis
   `max(static_floor_tokens + estimate_message_tokens, last_reported_input_tokens)` (floor-aware after
   ISSUE-1.5).
4. **`proactive_window_processor`** — at `compaction_ratio × budget` (32,768), LLM summarization of the
   dropped region into a marker. Gated (see F). Local trigger estimate is floor-inclusive via
   `effective_request_tokens`.
5. **`_attempt_overflow_recovery`** (`orchestrate.py`) — reactive last resort *after* the model returns a
   context-overflow error: `recover_overflow_history` frees the entire pre-tail (no recency cap, no
   boundary protection). One attempt per turn; else "Context overflow — unrecoverable."

### D. The two distinct gates (do not conflate)

- **Circuit breaker** (`_COMPACTION_BREAKER_TRIP = 3`, `_COMPACTION_BREAKER_PROBE_EVERY = 10`): after 3
  summarizer *failures*, falls back to a **static marker** (still compacts, no LLM); probes every 10
  skips. **This path does NOT no-op.**
- **Anti-thrash gate**: after `proactive_thrash_window = 2` consecutive *low-yield*
  (<`min_proactive_savings` 0.10) passes — counter maintained in `_record_proactive_outcome` —
  `proactive_window_processor` **returns messages unchanged** — the no-op. Unlike the breaker, it has no
  static-marker fallback, so text/reasoning context (not bounded by layer 2) can grow to overflow. This
  no-op is fixed by the **extracted ISSUE-2 plan**
  (`docs/exec-plans/active/2026-06-03-220905-antithrash-static-marker-fallback.md`); the description here
  is the current (pre-fix) loop state for reference.

### E. Trigger-basis — floor-aware (ISSUE-1.5, shipped)

`proactive_window_processor` uses `token_count = max(effective_request_tokens(deps, messages), reported)`.
`effective_request_tokens = deps.static_floor_tokens + estimate_message_tokens(messages)`;
`estimate_message_tokens` counts **only the message list** (history) — system instructions + tool
schemas are not in `messages`, which is why the floor is added explicitly. `reported`
(`last_reported_input_tokens`) is the full input the model saw, **including** the floor.

The provider report is floor-inclusive and current **cross-turn**, but stale/zeroed/missing
**within-turn** (in-turn growth before the next response updates it; after a within-turn compaction
zeroes it; or a response with no usage). In those windows the floor-blind local would have undercounted
live size by up to one floor (~11k); ISSUE-1.5 closed that by adding the bootstrap-measured
`static_floor_tokens` to the local estimate. Savings (`_record_proactive_outcome`) and the
`commit_compaction` overwrite both use the floor-inclusive `tokens_after`, so savings is no longer
overstated. Specs `compaction.md` §1.5/§2.5 and `core-loop.md` document the shipped behavior.

### F. Compaction boundary planning (`_compaction_boundaries.py`)

`plan_compaction_boundaries(messages, budget, tail_fraction)` → `(head_end, tail_start,
dropped_count)`: head = first run end + 1 (initial group, minimal); tail accumulates recent turn
groups until `acc_tokens > tail_fraction * budget` (now 0.10 × 65,536 = 6,554) unless under
`_MIN_RETAINED_TURN_GROUPS`; dropped = everything between → summarized into the marker. Aborts (returns
None) when `tail_start <= head_end`. The tail budget multiplies the **full** `budget` (65,536); at
`tail_fraction 0.10` the preserved tail is ~20% of the 32,768 operational trigger (ISSUE-1 fix).
