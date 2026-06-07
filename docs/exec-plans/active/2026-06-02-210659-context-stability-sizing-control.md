# context-stability-sizing-control

> **Status: pre-Gate-1 for the remainder.** All compaction-dynamics work this plan originally tracked has
> shipped (proportional tail + floor-aware trigger v0.8.294; anti-thrash static-marker fallback v0.8.296;
> drop-reported realtime trigger v0.8.302; L2 spill tail-protection v0.8.307; read/view emission-cap dedup
> v0.8.308). **Two levers remain open** — both below.

## First-principle goal

The agent runs a small local model on a fixed 64k window operated conservatively at `0.50` (a 32,768-token
proactive trigger). Two quantities decide whether long multi-turn sessions stay healthy:

1. **The fixed prefill floor** — static instructions + ALWAYS tool schemas (~11.4k tok) ride on *every*
   request. It is uncompactable, so it is subtracted from the working middle the agent reasons in on every
   turn, and it is a direct prefill-latency tax (trivial turns are prefill-bound). Shrinking it raises
   effective headroom after *every* compaction and cuts latency on *every* call.
2. **The working middle's stability** — total context must stay provably below the trigger across a long
   conversation *and* the agent must stay coherent (recall what it established before a compaction), all
   within the chosen `0.50` ceiling, **without enlarging the window**.

The shipped work closed the compaction-*logic* gaps. What remains is (1) reducing the schema half of the
fixed floor — its instruction half is already guarded by `tests/test_instruction_budget.py` — and (2)
proving empirically that the loop stays bounded **and coherent** under sustained load.

## Remaining tasks

### TASK A1 — Audit the ALWAYS tool-schema floor → report — ✓ DONE

Delivered: **`docs/REPORT-always-tool-schema-audit.md`** (from a real `measure_always_schema_budget` run,
`tmp/a1_schema_report.py`, `stack=None`). Findings: live ALWAYS bucket = **19,862 chars (~4,965 tok)** across
**22 tools** (plan's prior "24" was stale). Report carries the ranked char-cost list and a **first-principles
size × criticality tiering** (the deferral cost model — stub floor + round-trip + plan-legibility — grounded
in `deferred_prompt.py`, with a per-tool 2×2 placement) cross-checked against hermes/openclaw/opencode tool
surfaces **and against mined per-tool usage frequency** (`tmp/mine_tool_frequency.py` over
`~/.co-cli/sessions/`). **Recommended deferral set = 4**: `session_search`, `session_view`, `skill_patch`,
`skill_edit` → projected **16,505 chars**, clearing the <19,000 target with +2,495 margin, no docstring
squeeze. `web_fetch`/`web_search` were initially defer candidates but **revised to KEEP** (the mined corpus
shows them as the most-used tools + hermes keeps both in CORE); the memory-write family also stays (only
`memory_create` has payoff, splitting the family is incoherent). Both are documented next levers, not A2.
(Usage corpus is thin/stale — 42 calls — so it grounds the low-use defers and flags web, not more.)

### TASK A2 — Reduce the ALWAYS tool-schema floor (was part of ISSUE-4b)

Consumes TASK A1's report to shrink the schema half of the fixed floor. Deferral is the primary lever
(deferred tools leave the ALWAYS bucket the measure helper counts); docstring tightening is unnecessary at
the recommended set (kept optional).

- **prerequisites:** TASK A1 (✓ — Tier-2 set is the deferral worklist).
- **Files:** `co_cli/tools/session/recall.py` (`session_search`), `co_cli/tools/session/view.py`,
  `co_cli/tools/system/skills.py` (`skill_patch`/`skill_edit`); `tests/test_orchestrator_schema_budget.py`.
- **Impl:**
  1. Flip the 4 recommended tools to `visibility=VisibilityPolicyEnum.DEFERRED` (aligns `skill_patch`/
     `skill_edit` with their already-DEFERRED `skill_create`/`skill_delete` siblings).
  2. Do **not** defer `web_fetch`/`web_search` (revised to KEEP — usage mining + hermes-CORE) or the
     memory-write family (resolved KEEP); both are future levers only if a current corpus / floor squeeze
     justifies them.
  3. Re-pin `ALWAYS_BUCKET_CEILING` and `PER_ALWAYS_TOOL_CEILING` to the new measured values via
     `measure_always_schema_budget`. `deps.static_floor_tokens` is measured live at bootstrap from the same
     helper, so the runtime trigger floor auto-updates — no separate re-pin there.
- **done_when:** measured ALWAYS bucket is **below 19,000 chars** (from 19,862 — a real reduction via
  deferral, not a re-pin to the current measurement); both ceiling constants re-pinned to the new
  measurement; `uv run pytest tests/test_orchestrator_schema_budget.py -x` passes against the lowered pins;
  per-call prefill on a trivial turn measures below the current ~11.4k-tok floor.

### TASK B — Gate `tail_fraction 0.10` coherence; resolve CS.C status

`tail_fraction 0.10` shipped (v0.8.294), halving the preserved recent reasoning chain (13,107 → 6,554 tok).
That tail may be intentional small-model coherence headroom. The loop-stability eval
(`evals/eval_context_stability.py`) proves the loop is **bounded** (CS.A/CS.B run), but its module docstring
explicitly declares **"No coherence probe — deliberately out of scope"** — so `tail_fraction 0.10` is
currently validated for boundedness only, **never for coherence**. Separately, the tool-output case **CS.C is
authored but disabled** (`_CS_C_ENABLED = False`): under the 32k eval window the ~10.8k floor + 16,384 L3
trigger + 4k auto-spill cap route every oversized request to `fallback_to_summarize` before a fitting L2
aggregate spill can occur, so the spill-before-summarize chain is guarded meanwhile by the unit test
`test_l3_fastpaths_after_l2_spill_fits_payload` (`tests/test_flow_compaction_proactive.py`).

- **Files:** `evals/eval_context_stability.py` (`case_cs_a_text_pressure_bounded` — extend with a recall
  probe).
- **Impl:**
  1. **Coherence gate (primary).** Coherence does *not* depend on CS.C — it needs a recall assertion on a
     case that actually compacts. CS.A already drives text-heavy turns past the trigger and fires proactive
     passes. State a distinctive fact in an early turn (before the first compaction), then in a later turn
     (after ≥1 fired pass) ask the agent to restate it; assert the answer carries the fact. Soft real-LLM
     single-run gate — a bounded-but-incoherent run then fails the eval.
  2. **On regression** revert `tail_fraction` toward a higher value (one-line knob in
     `co_cli/config/compaction.py`); re-run until the recall probe passes, and pin the chosen value with the
     eval result as evidence.
  3. **CS.C disposition.** Either (a) accept the unit-test guard as the substitute and record CS.C as a
     documented SKIP (recommended — lowest cost; the precondition is environment-blocked, not a code gap), or
     (b) address the window/floor/cap sizings so the precondition is reachable and flip `_CS_C_ENABLED`.
- **done_when:** `uv run python evals/eval_context_stability.py` runs to completion with the loop bounded
  (no overflow error, every fired pass reduces tokens, post-pass total below trigger) **and** the CS.A recall
  probe confirms the agent restates a pre-compaction fact after compaction; the chosen `tail_fraction` is
  pinned with the eval result; CS.C disposition (a or b) is recorded in the Delivery Summary.

## Behavioral constraints

- **Conservative small-model bias preserved** — the `0.50` operational ceiling exists for qwen3 coherence; do
  not raise it without eval evidence. These fixes improve stability *within* the ceiling, not by enlarging the
  window.
- **Real everything in evals** (`feedback_eval_real_world_data`): real deps/LLM/tools, no caps or test stores.
- **No backward-compat shims** (`feedback_zero_backward_compat`): change defaults directly; settings.json
  overrides remain the escape hatch.
- **Surgical**: TASK A1 changes no source (report + `tmp/` only); TASK A2 touches only the tool decorators /
  schema-budget test; TASK B touches only the eval / tail knob.

## Testing

- `tests/test_orchestrator_schema_budget.py` (re-pinned by TASK A2).
- `evals/eval_context_stability.py` (coherence probe added by TASK B) — the empirical loop-bound + coherence gate.
- `scripts/quality-gate.sh full` at ship.

## Live thresholds (reference — production window)

`model_max_ctx = 65,536` (`llm.max_ctx`; Ollama `num_ctx = 65_536`). All ratio knobs:
`co_cli/config/compaction.py`.

| Control | Site | Resolved |
|---|---|---|
| Proactive summarize + L2 spill trigger | `compaction_ratio` / `spill_ratio` = 0.50 | 32,768 tok |
| Trigger basis | `effective_request_tokens` = `static_floor_tokens + estimate_message_tokens` | realtime-local only (no provider `reported`, v0.8.302) |
| Preserved tail | `tail_fraction` = 0.10 | 6,554 tok (≈20% of the trigger) — **TASK B may revert** |
| Fixed floor | static instructions (5,838 tok, guarded) + ALWAYS schemas (~4,950 tok) | ~10,788 tok — **TASK A2 lowers the schema half** |
| Per-tool-result spill | `tool_io.SPILL_THRESHOLD_CHARS` | 4,000 chars (`file_read`/`*_view` = ∞, land inline) |

## Out of scope

- Enlarging the operational window beyond `0.50` (separate eval-gated decision).
- Server-side / provider-delegated compaction (`feedback_context_management_self_contained`).
- Mid-turn / pre-emptive compaction cadence — `proactive_window_processor` already runs per LLM request,
  more aggressively than openclaw's optional precheck; this plan tunes thresholds, it adds no cadence.
- Peer-survey cross-session / long-loop / multi-agent items (dream daemon + future subagent axis).
