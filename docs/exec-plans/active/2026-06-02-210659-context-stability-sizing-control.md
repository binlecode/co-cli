# context-stability-sizing-control

> **Status: pre-Gate-1 for the remainder.** The compaction-*logic* work this plan originally tracked has
> shipped. Two levers remain open, both below: **A2** (shrink the ALWAYS tool-schema floor) and **B** (prove
> the loop stays bounded *and coherent* under sustained load). TASK A1 (the audit feeding A2) is done.

## First-principle goal

The agent runs a small local model on a fixed 64k window operated conservatively at `0.50` (a 32,768-token
proactive trigger). Two quantities decide whether long multi-turn sessions stay healthy:

1. **The fixed prefill floor** — static instructions + ALWAYS tool schemas ride on *every* request. It is
   uncompactable, so it is subtracted from the working middle the agent reasons in on every turn, and it is a
   direct prefill-latency tax (trivial turns are prefill-bound). Shrinking it raises effective headroom after
   *every* compaction and cuts latency on *every* call.
2. **The working middle's stability** — total context must stay provably below the trigger across a long
   conversation *and* the agent must stay coherent (recall what it established before a compaction), all
   within the chosen `0.50` ceiling, **without enlarging the window**.

The shipped work closed the compaction-logic gaps. What remains is (1) reducing the schema half of the fixed
floor — its instruction half is already guarded by `tests/test_instruction_budget.py` — and (2) proving
empirically that the loop stays bounded **and coherent** under sustained load.

## Remaining tasks

### TASK A1 — Audit the ALWAYS tool-schema floor → report — ✓ DONE

Delivered **`docs/REPORT-always-tool-schema-audit.md`** (run date 2026-06-06). It carries the ranked
per-tool char-cost list, a first-principles size×criticality tiering (deferral cost model from
`deferred_prompt.py`: stub floor + search-tools round-trip + plan-legibility), and a usage-frequency
cross-check. **Recommended deferral set = 4**: `session_search`, `session_view`, `skill_patch`,
`skill_edit`. `web_fetch`/`web_search` and the memory-write family were considered and **revised to KEEP**
(usage mining + hermes-CORE parity) — documented next levers, not A2.

> **Drift note for A2:** the report measured the bucket at **19,862 chars / 22 ALWAYS tools**. Since then
> v0.8.310 added the ALWAYS `tool_view` loader (+719 chars), so the **current live bucket is ~20,581 chars**
> and the test pin was raised to `21_000`. A2 must re-measure fresh and re-pin to the post-deferral value —
> not trust the report's absolute numbers. The *recommended deferral set is unaffected* (the 4 tools and
> their ~3,357 combined chars are unchanged).

### TASK A2 — Reduce the ALWAYS tool-schema floor — ✓ DONE

Consumes A1's recommendation to shrink the schema half of the fixed floor. Deferral is the only lever needed
(deferred tools leave the ALWAYS bucket the measure helper counts); docstring tightening is unnecessary at
the recommended set.

- **prerequisites:** TASK A1 (✓).
- **Files:**
  - `co_cli/tools/session/recall.py` (`session_search`, currently `ALWAYS` at ~line 122)
  - `co_cli/tools/session/view.py` (`session_view`, currently `ALWAYS` at ~line 19)
  - `co_cli/tools/system/skills.py` (`skill_edit` ~line 331, `skill_patch` ~line 359, both `ALWAYS`)
  - `tests/test_orchestrator_schema_budget.py` (re-pin `ALWAYS_BUCKET_CEILING`)
- **Impl:**
  1. Flip the 4 tools to `visibility=VisibilityPolicyEnum.DEFERRED`. This aligns `skill_patch`/`skill_edit`
     with their already-DEFERRED `skill_create`/`skill_delete` siblings.
  2. Do **not** defer `web_fetch`/`web_search` or the memory-write family (resolved KEEP in A1).
  3. Re-measure the live bucket with `measure_always_schema_budget(deps)` and re-pin `ALWAYS_BUCKET_CEILING`
     to the new value + the existing ~400-char headroom convention. `PER_ALWAYS_TOOL_CEILING` is unaffected
     (the deferred tools are not the per-tool max; `file_search`/`shell_exec` still are). `MIN_TOOL_COUNT` is
     a drop-guard floor, not a count pin — leave it unless it would trip.
  4. `deps.static_floor_tokens` is measured live at bootstrap from the same helper, so the runtime trigger
     floor auto-updates — no separate re-pin there.
- **done_when:** measured ALWAYS bucket is **below 19,000 chars** (a real reduction via deferral from the
  ~20,581 live bucket, not a re-pin to the current measurement — projected ~17,200 after the 4 defers);
  `ALWAYS_BUCKET_CEILING` re-pinned to the new measurement; `uv run pytest
  tests/test_orchestrator_schema_budget.py -x` passes against the lowered pin; the 4 deferred tools resolve
  via the `tool_view` loader when called.

### TASK B — Gate `tail_fraction 0.10` coherence; resolve CS.C status

`tail_fraction 0.10` shipped, sizing the preserved recent reasoning chain at 6,554 tok (≈20% of the
production trigger). The loop-stability eval (`evals/eval_context_stability.py`) proves the loop is
**bounded** (CS.A/CS.B run), but its module docstring explicitly declares **"No coherence probe —
deliberately out of scope"** — so `tail_fraction 0.10` is validated for boundedness only, **never for
coherence**. Separately, the tool-output case **CS.C is authored but disabled** (`_CS_C_ENABLED = False`):
under the 32k eval window the floor + 16,384 L3 trigger + auto-spill cap route every oversized request to
`fallback_to_summarize` before a fitting L2 aggregate spill can occur. That spill-before-summarize chain is
meanwhile guarded by the unit test `test_l3_fastpaths_after_l2_spill_fits_payload`
(`tests/test_flow_compaction_proactive.py`).

- **Files:** `evals/eval_context_stability.py` (`case_cs_a_text_pressure_bounded` — extend with a recall
  probe); `co_cli/config/compaction.py` (`tail_fraction` knob, only if a regression forces a revert).
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
- **Surgical**: A2 touches only the 4 tool decorators + the schema-budget pin; B touches only the eval / tail
  knob.

## Testing

- `tests/test_orchestrator_schema_budget.py` (re-pinned by A2).
- `evals/eval_context_stability.py` (coherence probe added by B) — the empirical loop-bound + coherence gate.
- `scripts/quality-gate.sh full` at ship.

## Live thresholds (reference — production window)

`model_max_ctx = 65,536` (`llm.max_ctx`; Ollama `num_ctx = 65_536`). All ratio knobs in
`co_cli/config/compaction.py`.

| Control | Site | Resolved |
|---|---|---|
| Proactive summarize + L2 spill trigger | `compaction_ratio` / `spill_ratio` = 0.50 | 32,768 tok |
| Trigger basis | `effective_request_tokens` = `static_floor_tokens + estimate_message_tokens` | realtime-local only (no provider `reported`) |
| Preserved tail | `tail_fraction` = 0.10 | 6,554 tok (≈20% of the trigger) — **B may revert** |
| Fixed floor | static instructions (guarded) + ALWAYS schemas (17,224 chars / ~4,306 tok — **A2 done**) | schema half lowered 20,581 → 17,224 chars |
| Per-tool-result spill | `tool_io.SPILL_THRESHOLD_CHARS` | 4,000 chars (`file_read`/`*_view` = ∞, land inline) |

## Out of scope

- Enlarging the operational window beyond `0.50` (separate eval-gated decision).
- Server-side / provider-delegated compaction (`feedback_context_management_self_contained`).
- Mid-turn / pre-emptive compaction cadence — `proactive_window_processor` already runs per LLM request;
  this plan tunes thresholds, it adds no cadence.
- Peer-survey cross-session / long-loop / multi-agent items (dream daemon + future subagent axis).

## Delivery Summary — TASK A2 (2026-06-07)

Deferred the A1-recommended 4 tools (`session_search`, `session_view`, `skill_edit`, `skill_patch`),
unifying all four skill-write tools as DEFERRED. Per the scope decision this session, the move
deliberately reverses the `skills.md` "skill_edit/skill_patch always-loaded for drift-fix" rationale —
the drift-fix path now takes a `tool_view` round-trip, accepted as worth a smaller per-turn prefill.

**Result:** ALWAYS schema bucket **20,581 → 17,224 chars** (live-measured), 19 ALWAYS tools, all 4 target
tools confirmed out of the bucket; `tool_count` unchanged at 36 (deferral hides, does not drop). Clears
the <19,000 target with +1,776 margin, no docstring squeeze. `deps.static_floor_tokens` auto-updates at
bootstrap from the same `measure_always_schema_budget` helper.

**Changes:**
- `co_cli/tools/session/recall.py`, `co_cli/tools/session/view.py`, `co_cli/tools/system/skills.py` —
  4 decorators flipped `ALWAYS → DEFERRED`.
- `tests/test_orchestrator_schema_budget.py` — `ALWAYS_BUCKET_CEILING` re-pinned `21_000 → 17_700`
  (+~400 headroom), comment history updated.
- Doc-sync: `docs/specs/skills.md` (§Path 3 — all 4 skill-write tools DEFERRED), `docs/specs/tools.md`
  (count `19·17`, expanded DEFERRED list, row-32 visibility annotation, row-30 header `Interaction &
  Planning`). `docs/specs/compaction.md` reviewed — no change (mechanism-level prose, no baked literals).

**Verification:**
- Re-measure: `total_chars=17,224 < 19,000`; `session_search`/`session_view`/`skill_edit`/`skill_patch`
  absent from `per_tool_chars`; max still `file_search` (2,111).
- `uv run pytest tests/test_orchestrator_schema_budget.py -x` — 2 passed against the lowered pin.
- 67 unit tests pass (4 flow tests + `test_tool_view` + `test_stub_names_exactly_match_deferred_set`).
- **Behavioral (real LLM):** `eval_skills.py` — W4.C create+patch+delete all observed on disk; W4.E
  discovery 3/3 HIT with `load=1` each trial ("keep DEFERRED"). `eval_memory.py` — W3.C 2 `session_search`
  calls, loader emitted "Loaded `session_search`. It is now callable". The `tool_view` round-trip fires for
  both deferred families. All W3/W4 cases PASS.
- `scripts/quality-gate.sh lint` — PASS. Full suite deferred to `/ship`.

## Implementation Review — 2026-06-07

Reviewed: TASK A1 (report-only), TASK A2 (deferral). Stance: issues exist — PASS is earned.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| A2 | Bucket < 19,000 chars (real reduction) | ✓ pass | Live `measure_always_schema_budget`: `total_chars=17,224` (from 20,581); 4 targets absent from `per_tool_chars` |
| A2 | `ALWAYS_BUCKET_CEILING` re-pinned | ✓ pass | `tests/test_orchestrator_schema_budget.py:32` = `17_700` (from `21_000`), comment history accurate |
| A2 | Schema-budget test passes on lowered pin | ✓ pass | `pytest …schema_budget.py -x` → 2 passed |
| A2 | 4 tools resolve via `tool_view` | ✓ pass | `recall.py:122`, `view.py:19`, `skills.py:331`/`:359` all `DEFERRED`; eval_memory W3.C + eval_skills W4.E confirm live load |
| A2 | Doc-sync accurate | ✓ pass | `skills.md:52` (all 4 skill-write DEFERRED), `tools.md:37` (19·17, DEFERRED list totals 17), `tools.md:32` visibility annotated |
| A2 | No dream-daemon regression (adversarial) | ✓ pass | `build_task_agent` (`build.py:79-111`) passes tools by name; never applies `_tool_visibility_filter` — deferral invisible to `SKILL_REVIEW_SPEC` reviewers |
| A1 | Audit report delivered | ✓ pass | `docs/REPORT-always-tool-schema-audit.md` exists; per-tool table re-confirmed exact this session (errata block records the +tool_view drift) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Pre-existing: DEFERRED described as "discovered via search_tools" (co uses co-owned `tool_view`, no SDK `search_tools`) | `.agent_docs/tools.md:57` | minor (pre-existing) | Corrected to the `tool_view` loader mechanism |
| Pre-existing: tool registration cites non-existent path `co_cli/agents/_native_toolset.py` + wrong mechanism | `.agent_docs/tools.md:53` | minor (pre-existing) | Corrected to `@agent_tool`→`TOOL_REGISTRY`, module import in `co_cli/agent/toolset.py` |
| Scope: 2 eval REPORT files modified, not in any task `files:` | `docs/REPORT-eval-{memory,skills}.md` | non-blocking | Expected — auto-prepended by eval harness during behavioral verification |

### Tests
- Command: `uv run pytest -x -q`
- Result: **625 passed, 0 failed** (164.87s)
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- `co status` is not a command in this CLI; the user-facing surface A2 changed (deferred tool discovery → `tool_view` load → use) was verified via real-LLM evals — the appropriate surface. eval_memory W3.C: loader emitted "Loaded `session_search`. It is now callable", 2 successful calls. eval_skills W4.E: discovery 3/3 HIT, `load=1` each. Bootstrap health confirmed (19 ALWAYS / 36 total via live `create_deps`).

### Overall: PASS
A2 meets every `done_when` with live-re-measured evidence, the change is surgical, docs are synced and accurate, the only integration risk (dream daemon) is refuted by call-path evidence, the full suite is green, and the deferred round-trip is behaviorally confirmed. Two pre-existing `.agent_docs/tools.md` inaccuracies were fixed in passing. Ready to ship.
