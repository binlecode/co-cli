# context-stability-sizing-control

> **Status: PARTIAL DELIVERY — pre-Gate-1 for the remainder.** ISSUE-1 (proportional tail,
> `tail_fraction 0.20→0.10`) and ISSUE-1.5 (floor-aware trigger estimate) are **shipped**; ISSUE-4a's
> instruction-block guard already exists (via the `rules-block-trim-finish` sibling plan). This plan
> now tracks the remaining dynamics issues (2, 3, 5), the tool-schema reduction (4b), and the
> loop-stability eval. Baseline numbers below reflect the post-delivery shipped state.

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
| Request-level tool spill | `enforce_request_size`, `spill_ratio 0.50` | 32,768 tok (`deps.spill_threshold_tokens`) |
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
  an uncapped live tool-output path. ISSUE-2 — beyond parity: no peer fixes the anti-thrash no-op.
  ISSUE-3 — no peer reference; co's spill→summarize ladder is co-specific, tune via the eval. ISSUE-4b —
  co's per-tool schemas are already the leanest surveyed; the lever is deferral, not docstring squeeze.
- **Architecture confirmation.** Co's per-request compaction cadence already does (more aggressively)
  what openclaw's mid-turn precheck does — this plan adds **no new cadence**, only threshold tuning.

## Problem & Outcome

**Problem.** Gap-prone compaction logic still makes total context unstable: a single gate
(anti-thrash) can disable compaction entirely, spill and summarize have no separating margin, and a
single tool read can inject an unbounded inline chunk. The fixed floor — the highest-leverage latency
lever — is now guarded on its instruction half but tool schemas remain over-eager. Together these
produce middle-section dilution and growth-to-overflow under realistic multi-turn load. (The tail
disproportion and the floor-blind trigger are now fixed — see Delivered.)

**Outcome.** Compaction degrades gracefully (never to a no-op); spill reliably runs before LLM
summarization; large tool reads cannot single-handedly dominate the middle; tool schemas keep
shrinking; and a loop-stability test proves the window stays bounded — and the agent stays coherent —
under a long multi-turn conversation.

**Failure cost:** silently, the agent loop grows context past 32,768 in long multi-turn sessions until
the model returns a context-overflow error; the reactive last-resort recovery
(`_attempt_overflow_recovery`) then dumps the entire pre-tail in one unbounded cut — destroying recent
reasoning the user was mid-task on — or, if that also fails, the turn dies with "Context overflow —
unrecoverable." No alarm fires before the cliff; the only visible symptom beforehand is degraded
answer quality from a diluted middle.

**Latency dimension (not just stability).** ISSUE-1.5 (shipped) made the trigger floor-aware. The
remaining fixes are also a per-turn latency lever: an LLM summarization pass dominates a turn's
wall-clock. ISSUE-2 (static-marker fallback) and ISSUE-3 (spill-first band) avoid/defer the
summarization call; ISSUE-4b (schema reduction) cuts latency on *every* prefill-bound call.

## High-Level Design

Both fronts are localized — no new abstractions:
- **Dynamics (ISSUE-2/3/5).** All ratio knobs live in `co_cli/config/compaction.py`, consumed by
  `co_cli/context/compaction.py` (proactive trigger + boundaries) and
  `co_cli/context/history_processors.py` (spill/evict/dedup), plus the per-tool spill threshold in
  `co_cli/tools/`. Adjust the ISSUE-3 band, the ISSUE-2 gate behavior (static-marker fallback), and the
  ISSUE-5 emission cap. (The tail re-size and the floor-aware trigger estimate are already shipped.)
- **Floor (ISSUE-4).** Reduce tool schemas via deferral (ISSUE-4b). The instruction-half guard
  (ISSUE-4a) already exists; no source change to rules.
- **Validation.** A new loop-stability eval exercises the dynamics the synthetic-fixture unit tests do
  not — it gates the combined behavior and the shipped `tail_fraction 0.10` coherence.

Each issue below carries its own implementation block (Files / Action / done_when / success_signal) so
the analysis and the work that resolves it stay together. Cross-cutting validation follows the issues.

## Issues, Fixes & Implementation

### ISSUE-2 — Anti-thrash gate disables compaction entirely (no-op → growth)
Compaction has two ways to trim the conversation. The **expensive** way (LLM summarization) asks the model
to summarize the dropped region — it costs a full model call but keeps the *meaning* of what was cut. The
**cheap** way (static marker) just drops the region and leaves a stub — free and instant, but it discards the
meaning. "Thrashing" is when the loop keeps paying for the *expensive* way while getting almost nothing back:
pass after pass fires, each costs an LLM call, each frees <10% (the OS sense — all effort spent on overhead,
no real progress).

Detecting that is the right instinct; the bug is the **response**. `co_cli/context/compaction.py` (the
anti-thrash gate, currently a bare `return messages`): when
`consecutive_low_yield_proactive_compactions >= proactive_thrash_window (2)`,
`proactive_window_processor` **returns messages unchanged**. It doesn't stop paying for the *expensive* trim
— it stops trimming **at all**. Text/reasoning context then grows unbounded toward 64k, bounded only by
reactive `_attempt_overflow_recovery` (fires *after* the model errors). (Tool-return bloat is still capped by
`evict_old_tool_results` keep-recent-5; text/reasoning is not.)

**Logic (verified source).** The counter is maintained in `_record_proactive_outcome`: after each fired pass
it computes `savings = (token_count - tokens_after) / token_count` (now on a floor-inclusive basis after
ISSUE-1.5) and, if `savings < cfg.min_proactive_savings` (0.10), increments
`consecutive_low_yield_proactive_compactions`, else resets it to 0. On the *next* request the anti-thrash
gate sees the counter `>= proactive_thrash_window` and bare-`return messages` — no boundary plan, no marker,
no token reduction.

Walk the two reasons a pass is repeatedly low-yield — **neither makes the no-op the right move:**
- **Verbose summary, real droppable region (the no-op is *harmful*).** The region was large but the LLM
  summary came back nearly as large, so net savings <10%. The right move is to drop the region and insert a
  static marker (no summary) — which saves *more* than the verbose-summary version. The no-op throws that
  saving away.
- **Middle genuinely exhausted (the no-op is *redundant*).** Almost nothing left to drop. But this case is
  **already handled upstream** — `plan_compaction_boundaries` returns `None` and `proactive_window_processor`
  skips before the anti-thrash gate matters.

So there is **no scenario where the no-op is correct**: it is either redundant (exhausted middle — the
boundary-`None` guard already skips) or harmful (verbose summary — should static-marker). Contrast the
**circuit breaker** (`_COMPACTION_BREAKER_TRIP=3`, `_COMPACTION_BREAKER_PROBE_EVERY=10`): after summarizer
*failures* it still emits a `static_marker` (compacts, no LLM) — it never no-ops. The two gates are
independent (Appendix F); only anti-thrash leaves the hole.

**Fix.** Demote the counter from a *compaction kill-switch* to an *LLM-budget guard*: it should decide only
"pay for the expensive LLM summary, or use the free static marker" — never "stop trimming." When the gate
trips, **fall back to a static-marker compaction** (drop the region, insert a marker — no LLM call), reusing
the circuit-breaker's existing `static_marker` path, so compaction is never a full no-op. "Whether to compact
at all" is already answered upstream by the threshold and the boundary-`None` guard, so the counter has no
business gating it. (*How eagerly* to retry the expensive path after falling back — bounce back every pass
vs. stay on the cheap path and re-probe occasionally — is a tuning choice owned by this task and the eval.)

**Peer framing — this is BEYOND parity, not catch-up.** Among surveyed peers, only hermes-agent has an
anti-thrash heuristic at all (same shape: skip if the last 2 compressions each saved <10%), and **it
shares co's exact no-op hole** — it skips rather than forcing a cheap compaction, leaning on a 600s
cooldown to recover. Openclaw and codex have no anti-thrash gate. So no surveyed peer solves ISSUE-2;
co's static-marker-on-thrash fix would be **novel**.

**Implementation.**
- **Files:** `co_cli/context/compaction.py`, `tests/test_flow_compaction_proactive.py`.
- **Action:** Replace the `anti_thrash_gate` early-return-unchanged with a static-marker compaction pass.
  **Mechanism (CD-M-1):** the existing surface cannot force the static path — when the gate trips, the model
  is present and `compaction_skip_count < _COMPACTION_BREAKER_TRIP`, so `_summarization_gate_open` returns
  `(True, _)` and `compact_messages` would still fire the LLM via `_gated_summarize_or_none`. Add a
  `summarize: bool = True` parameter to `compact_messages` that, when `False`, skips
  `_gated_summarize_or_none` and passes `summary_text=None` (static marker), reusing the rest of the
  assembly intact (marker, `todo_snapshot`, deferred-tool discoveries, tail). In the anti-thrash branch,
  plan boundaries and call `compact_messages(..., summarize=False)`, then `commit_compaction`.
- **prerequisites:** none.
- **done_when:** the existing test `test_anti_thrash_gate_skips_compaction_after_consecutive_low_yield`
  (which currently asserts `result is messages` / `compaction_applied_this_turn is False` — the old no-op)
  is **rewritten** to assert the static marker was inserted, the dropped region removed, message tokens
  reduced, no summarizer LLM call made, and `compaction_applied_this_turn is True`;
  `uv run pytest tests/test_flow_compaction_proactive.py -x` passes.
- **success_signal:** in the loop-stability eval, the anti-thrash gate engaging never yields a pass that
  returns messages unchanged — every triggered pass reduces token count.

### ISSUE-3 — Zero band between spill and summarize (`spill_ratio == compaction_ratio`)
Both default 0.50, so cheap tool-return spill and expensive LLM summarization fire at the **same**
32,768 point; only processor order gives spill first crack.

**Logic (verified source).** Both layers share the `int(ratio × model_max_ctx)` basis: L2's
`deps.spill_threshold_tokens = int(spill_ratio × model_max_ctx)` = 32,768 (`deps.py`), L3's
`token_threshold = int(budget × compaction_ratio)` = 32,768 (`compaction.py`). Equal thresholds
mean the only thing that gives spill first crack is **processor order** in the chain
(`enforce_request_size` before `proactive_window_processor`). Critically, L2 spills **only**
string-content `ToolReturnPart`s (`_collect_tool_return_candidates`, `history_processors.py`) — so a
**text/reasoning-heavy** middle has zero spill candidates and goes straight to L3. The band only helps
tool-output pressure; that is exactly why the eval needs a separate text-heavy phase to exercise ISSUE-2.

**Fix.** Lower `spill_ratio` below `compaction_ratio` (e.g. spill 0.40 / compaction 0.50) so spill
runs with margin and can pull total under the summarize trigger before LLM summarization fires. The
validator already enforces `spill_ratio <= compaction_ratio`; this just makes the default a real band.

**Peer framing — no external reference; tune empirically.** No surveyed peer spills tool output to disk
at all (opencode truncates in place at 2K, openclaw at 8K head-only) — the recoverable spill→summarize
**two-tier ladder is co-specific**. So there is **no peer-derived number** for the band width; the
0.40-vs-0.45 `spill_ratio` choice is owned entirely by the eval.

**Implementation.**
- **Files:** `co_cli/config/compaction.py`, `tests/test_flow_compaction_processor_chain.py`.
- **Action:** Set `spill_ratio` default below `compaction_ratio` (proposed 0.40 / 0.50); keep the
  validator invariant (`spill_ratio <= compaction_ratio`) and update the `spill_ratio` field docstring.
- **prerequisites:** none.
- **done_when:** resolved `spill_threshold_tokens < compaction trigger` (26,214 < 32,768 at 0.40/0.50);
  a chain test that constructs **tool-return (spillable) pressure specifically** — `enforce_request_size`
  spills only string-content `ToolReturnPart`s, so a generic-token fixture would not exercise the band —
  shows spill fires and resolves pressure before proactive summarization within the band;
  `uv run pytest tests/test_flow_compaction_processor_chain.py -x` passes.
- **success_signal:** in the loop-stability eval, spill-to-disk passes precede LLM-summarization passes
  under tool-output pressure, not the reverse.

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
next request's `enforce_request_size` force-spills it. A single read can dominate the middle between
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
Validates the combined behavior of ISSUE-2/3/5 and the shipped tail+floor work; not tied to a single issue.
- **Files:** `evals/eval_context_stability.py` (NEW).
- **Action:** Drive a long multi-turn conversation (real LLM, real tools, large tool outputs) past the
  trigger and assert: no context-overflow error, bounded number of compaction passes, post-pass total
  stays below the trigger, anti-thrash never produces a no-op. Include **two distinct pressure phases**:
  (a) tool-output-heavy load (exercises spill / ISSUE-3 / ISSUE-5), and (b) a **reasoning/text-heavy phase
  that engages the anti-thrash gate** — a text middle has no spillable `ToolReturnPart` candidates, so this
  is the only phase that actually exercises ISSUE-2's previously-unreproduced no-op→growth path. Real
  everything (per `feedback_eval_real_world_data`).
- **prerequisites:** ISSUE-2, ISSUE-3, ISSUE-5 fixes required. (ISSUE-1 + ISSUE-1.5 already shipped; this
  eval is also the coherence gate for the shipped `tail_fraction 0.10` — see Open Questions.)
- **done_when:** `uv run python evals/eval_context_stability.py` runs to completion with the loop bounded
  (no overflow error, every triggered pass reduces tokens, post-pass total below trigger), the anti-thrash
  phase (b) confirms a static-marker pass reduced tokens rather than no-op'ing, **AND** the agent still
  completes the multi-turn task correctly after compaction — a coherence assertion (e.g. the agent recalls
  a fact stated before the first compaction; a **soft, real-LLM single-run gate per CD-m-4**, not
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
- ISSUE-3 band width (0.40 vs 0.45 spill_ratio) — tune against the loop-stability eval. **No peer reference
  exists** (co's spill→summarize ladder is co-specific; no peer spills to disk), so this number is owned
  entirely by the eval — do not seek a peer-derived value.
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
| `spill_ratio` | 0.50 | `deps.spill_threshold_tokens = int(spill_ratio * model_max_ctx)` (`deps.py`), used in `history_processors.py` | 32,768 tok |
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
3. **`enforce_request_size`** — at `spill_threshold_tokens` (32,768), force-spill the largest unspilled
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
  `proactive_window_processor` **returns messages unchanged** — the no-op. This is ISSUE-2: unlike the
  breaker, it has no static-marker fallback, so text/reasoning context (not bounded by layer 2) can grow
  to overflow. **No surveyed peer fixes this** — the ISSUE-2 fix is beyond-parity.

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
