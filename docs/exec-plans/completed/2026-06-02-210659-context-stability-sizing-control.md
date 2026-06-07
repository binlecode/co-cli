# context-stability-sizing-control

> **Status: all tasks ✓ DONE — ready to ship + archive.** **A1** (ALWAYS tool-schema audit) and **A2**
> (defer 4 tools; bucket 20,581 → 17,224 chars) shipped in **v0.8.314** (`b5667fbc`). **B** (coherence gate +
> CS.C disposition) is delivered: the rewritten `eval_context_stability.py` now gates coherence and **passes
> at `tail_fraction 0.10`** (fact recalled after 7 compaction passes) — no revert needed. The production-logic
> trace done during B surfaced 7 compaction bugs, captured in `docs/ISSUE-compaction-production-logic.md` for
> a **separate** fix plan (out of scope here).

## First-principle goal

The agent runs a small local model on a fixed 64k window operated conservatively at `0.50` (a 32,768-token
proactive trigger). Two quantities decide whether long multi-turn sessions stay healthy:

1. **The fixed prefill floor** — static instructions + ALWAYS tool schemas ride on *every* request,
   uncompactable and a direct prefill-latency tax. **Addressed:** instruction half guarded by
   `tests/test_instruction_budget.py`; schema half cut to 17,224 chars by A2.
2. **The working middle's stability** — total context must stay provably below the trigger across a long
   conversation *and* the agent must stay coherent (recall what it established before a compaction), all
   within the `0.50` ceiling, **without enlarging the window**. The loop is proven *bounded*; it is **not
   yet proven coherent** — that is TASK B.

## Task

### TASK B — Gate `tail_fraction 0.10` coherence; resolve CS.C status — ✓ DONE

`tail_fraction 0.10` shipped, sizing the preserved recent reasoning chain at 6,554 tok (≈20% of the
production trigger). The loop-stability eval (`evals/eval_context_stability.py`) proves the loop is
**bounded** (CS.A/CS.B run), but its module docstring explicitly declares **"No coherence probe —
deliberately out of scope"** (line 42) — so `tail_fraction 0.10` is validated for boundedness only, **never
for coherence**. Separately, the tool-output case **CS.C is authored but disabled** (`_CS_C_ENABLED = False`,
line 119): under the 32k eval window the floor + 16,384 L3 trigger + auto-spill cap route every oversized
request to `fallback_to_summarize` before a fitting L2 aggregate spill can occur. That spill-before-summarize
chain is meanwhile guarded by the unit test `test_l3_fastpaths_after_l2_spill_fits_payload`
(`tests/test_flow_compaction_proactive.py:415`).

- **Files:** `evals/eval_context_stability.py` (`case_cs_a_text_pressure_bounded`, line 274 — extend with a
  recall probe); `co_cli/config/compaction.py` (`tail_fraction` knob, line 39 — only if a regression forces
  a revert).
- **Impl:**
  1. **Coherence gate (primary).** Coherence does *not* depend on CS.C — it needs a recall assertion on a
     case that actually compacts. CS.A already drives text-heavy turns past the trigger and fires proactive
     passes. State a distinctive fact in an early turn (before the first compaction), then in a later turn
     (after ≥1 fired pass) ask the agent to restate it; assert the answer carries the fact. Soft real-LLM
     single-run gate — a bounded-but-incoherent run then fails the eval.
  2. **On regression** revert `tail_fraction` toward a higher value (one-line knob in
     `co_cli/config/compaction.py`); re-run until the recall probe passes; pin the chosen value with the eval
     result as evidence.
  3. **CS.C disposition.** Either (a) accept the unit-test guard as the substitute and record CS.C as a
     documented SKIP (recommended — lowest cost; the precondition is environment-blocked, not a code gap), or
     (b) re-size the eval window/floor/cap so the precondition is reachable and flip `_CS_C_ENABLED`.
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
- **Surgical**: B touches only the eval / `tail_fraction` knob.

## Testing

- `evals/eval_context_stability.py` (coherence probe added by B) — the empirical loop-bound + coherence gate.
- `scripts/quality-gate.sh full` at ship.

## Live thresholds (reference — production window)

`model_max_ctx = 65,536` (`llm.max_ctx`; Ollama `num_ctx = 65_536`). All ratio knobs in
`co_cli/config/compaction.py`.

| Control | Site | Resolved |
|---|---|---|
| Proactive summarize + L2 spill trigger | `compaction_ratio` / `spill_ratio` = 0.50 | 32,768 tok |
| Trigger basis | `effective_request_tokens` = `static_floor_tokens + estimate_message_tokens` | realtime-local only (no provider `reported`) |
| Preserved tail | `tail_fraction` = 0.10 | 6,554 tok — **B pinned 0.10** (coherence gate passes; no revert) |
| Fixed floor | static instructions (guarded) + ALWAYS schemas (17,224 chars / ~4,306 tok — A2 shipped) | floor reduced; schema half no longer the open lever |
| Per-tool-result spill | `tool_io.SPILL_THRESHOLD_CHARS` | 4,000 chars (`file_read`/`*_view` = ∞, land inline) |

## Out of scope

- Enlarging the operational window beyond `0.50` (separate eval-gated decision).
- Further ALWAYS-floor deferral (`web_fetch`/`web_search`, memory-write family) — resolved KEEP in A1;
  documented next levers only, not this plan.
- Server-side / provider-delegated compaction (`feedback_context_management_self_contained`).
- Mid-turn / pre-emptive compaction cadence — `proactive_window_processor` already runs per LLM request;
  this plan tunes thresholds, it adds no cadence.
- Peer-survey cross-session / long-loop / multi-agent items (dream daemon + future subagent axis).

## Shipped (this plan)

- **A1** — ALWAYS tool-schema audit → `docs/REPORT-always-tool-schema-audit.md`. Recommended deferral set of
  4 tools; `web_fetch`/`web_search` + memory-write family resolved KEEP.
- **A2** (v0.8.314, `b5667fbc`) — deferred `session_search`, `session_view`, `skill_edit`, `skill_patch`
  (ALWAYS → DEFERRED); ALWAYS schema bucket 20,581 → 17,224 chars; `ALWAYS_BUCKET_CEILING` 21,000 → 17,700;
  spec + `.agent_docs` doc-sync. Verified live (eval_memory W3.C, eval_skills W4.E); no dream-daemon
  regression. Full delivery detail in commit `b5667fbc` and CHANGELOG `[0.8.314]`.

## Delivery Summary — TASK B (2026-06-07)

Rewrote `evals/eval_context_stability.py` to add the **coherence gate** (the half the eval previously
declared out of scope) and reconcile it with current code. The probe plants a distinctive fact
(`SILVER-FALCON-2029`) in an early turn (pre-first-compaction, so it lands in the compactable middle), drives
the bounded-loop pressure turns, then asks the agent to restate it after ≥1 pass fired; a bounded-but-
incoherent run grades **SOFT_FAIL** (fails the run, marked soft for single-run LLM variance). Boundedness /
overflow stay HARD. House style reconciled (`run.append` centralized in `main()`); proven span-readers
preserved verbatim.

**Result — real-LLM run (exit 0), all green at `tail_fraction 0.10`:**
- **CS.A PASS** — `turns=10 fired_passes=7 anti_thrash_passes=2 overflow=False`; anti-thrash gate engaged on
  2 passes, each a static-marker that reduced tokens; **coherence OK** — agent recalled `SILVER-FALCON-2029`
  after 7 compaction passes.
- **CS.B PASS** — 5 summarizer passes, all focus, FLOOR-budget (2000) exercised, no mid-template truncation.
- **CS.C SKIPPED** — documented eval-scaffold sizing limit (32k window + ~10.8k floor + 4k auto-spill cap
  block the precondition); production chain correct and guarded by `test_l3_fastpaths_after_l2_spill_fits_payload`.

**`tail_fraction` decision:** **pinned 0.10** — coherence passes, no revert. The eval itself is now the pin
(it gates coherence on every run). **CS.C disposition:** option (a) — documented SKIP, the unit test is the
substitute (the precondition is eval-environment-blocked, not a code gap).

**Production-logic findings (out of scope, separate plan):** the adversarial trace done while rewriting the
eval surfaced **7 compaction bugs**, all verified cold and captured with concrete fixes in
`docs/ISSUE-compaction-production-logic.md`. Highest value: ISSUE-2 (no convergence guard) + ISSUE-3 (prior
summary-marker survival is prompt-only — did **not** regress this run, now guarded by the coherence probe).
ISSUE-1 (tail-budget floor inconsistency) should fold into that plan since it redefines the `tail_fraction`
lever. None fixed here (no eval-driven production changes).

**Changes:** `evals/eval_context_stability.py` (rewrite). `docs/ISSUE-compaction-production-logic.md` (new).
`docs/REPORT-eval-context-stability.md` (auto-prepended by the run).
