# Per-Model Prompt Calibration — decide whether the orchestrator's behavioral rules need a calibration tuned to the configured local model, measured against a persona-off baseline (measure-first)

Task type: measurement gate (instruction-following ablation on the configured model) → conditional per-model prompt variant, eval-gated. DEPENDED ON `summarizer-fidelity-measure` (shared root question: does the local model honor co's prompt contracts) — that plan is now DELIVERED (2026-06-17, verdict REVISE), so the build gate is satisfied.

## Context

co assembles ONE static instruction prefix regardless of which model is configured. With personality
now disabled by default (`DEFAULT_PERSONALITY = None`, `co_cli/config/core.py:53`), that prefix is the
functional core only:
- `build_base_instructions` → behavioral rules `01–07` (`co_cli/context/rules/`, ~4,448 tok)
- `build_toolset_guidance(tool_catalog)` (`co_cli/context/guidance.py`)
- ALWAYS tool-schema budget (measured into `static_floor_tokens`, `bootstrap/core.py:485-490`)
plus per-turn dynamic layers (time, conditional safety, deferred-tool stubs, skill manifest —
`co_cli/agent/_instructions.py`).

The rules are written as **descriptive prose** (e.g. `03_reasoning.md` 491 words, `07_memory_protocol.md`
669 words — the two heaviest, and reasoning + memory are exactly the two areas the project reports as
struggling). The peer pattern diverges sharply: opencode routes each model family to a hand-tuned
prompt (`~/workspace_genai/opencode/.../session/system.ts:25-39`, a `switch` on model id → 8 prompt
files), because the *mechanism* of compliance differs per model — Gemini responds to a hard line cap +
few-shot length demos, Claude to task-structure + anti-sycophancy
(`docs/reference/RESEARCH-opencode-context-conciseness-architecture.md` §2). co writes once and assumes
uniform strong instruction-following.

The configured model is local (`qwen3.6:35b-a3b-agentic`, MoE). Whether its instruction-following
profile matches the frontier assumption baked into co's descriptive rules is **unmeasured**. This plan
does not assume it doesn't — it measures, using the persona-off baseline that is now the default.

### Dependency on `summarizer-fidelity-measure` (RESOLVED — verdict REVISE)
That plan answered the same root question for the compaction summarizer (a 13-section contract) and is
now DELIVERED (`docs/exec-plans/completed/2026-06-17-144704-summarizer-fidelity-measure.md`, commit
`0d0120e0`). **Measured verdict: REVISE** — the configured model `qwen3.6:35b-a3b-agentic` did NOT honor
the demanding template: it defaulted to keep-every-section, ignored the omit-empty SKIP RULE, and drifted
on the verbatim active-task anchor. The fix that flipped it to COMPLIANT was exactly the prose-contract →
explicit-positioned-sections recalibration this plan proposes for the rules.

This is the leading indicator the plan was waiting on, and it points toward building: the model degrades
on a descriptive prompt contract, which is direct evidence the descriptive-rule style needs
model-specific calibration here as well. The bar for proceeding with TASK-1 is therefore LOW (a COMPLIANT
verdict would have raised it; the actual REVISE verdict does not).

### Code-state verification (claims checked against HEAD)
- Static prefix builders are joined in `build_orchestrator` (`co_cli/agent/build.py`) /
  `co_cli/agent/orchestrator.py`; `_personality_critique_provider` returns `None` when
  `personality=None` (`orchestrator.py:43`) — baseline is rules+toolset only, verified this session.
- There is NO model-id routing anywhere in prompt assembly — `grep` confirms `config.personality` and
  `tool_catalog` are the only branches; model identity does not influence prompt text.
- `evals/_settings.py` (EVAL_MAX_CTX + override from `load_config`), `_deps.py` (`make_eval_deps`),
  `_timeouts.py` are the centralized eval substrate (`feedback_evals_centralized_settings`).
- Persona-off is the default now, so an A/B against a known baseline needs no special config flip.

## Problem & Outcome

**Problem.** co's main prompt may be miscalibrated for the configured local model — descriptive rules
that a frontier model follows but a 35B-a3b model under-applies (skipped reasoning discipline, ignored
recall protocol). If so, the reported reasoning/recall struggles are partly a prompt-calibration
problem, not only a recall-engine or design problem.

**Failure cost:** two ways to lose. (1) Assume the rules work and keep editing prose → invisible
under-compliance persists. (2) Rewrite rules / add per-model routing speculatively → carry a routing
mechanism and a second prompt variant (maintenance + token cost) with no evidence it helps, violating
co's lean-prompt advantage and `feedback_eval_real_world_data` discipline.

**Outcome (measure-first).**
1. **A measured verdict (decisive deliverable).** On the configured model, do the load-bearing rules
   (reasoning discipline in `03`, recall cascade in `07`) actually change agent behavior on
   representative tasks vs an ablated prompt? Recorded as a reproducible JSONL run. "Rules are honored —
   change nothing" is a valuable, shippable finding. (Scope set to `03`/`07` — the two heaviest rules and
   the two reported struggle areas; `04 tool_protocol` is excluded from the ablation set per G1: no
   reported struggle-signal and including it doubles run cost without evidence it matters.)
2. **Only if non-compliance is measured — a calibrated revision** scoped to the failing rules: convert
   the specific descriptive rule(s) the model ignores into few-shot demonstration (the opencode/gemini
   pattern), measured to confirm lift. Per-model *routing* is adopted ONLY if a second configured model
   is in play and the calibrations genuinely conflict — otherwise the single prompt is recalibrated in
   place (co has effectively one configured model; routing is premature until it doesn't).

## Scope

### In scope
- An eval harness ablating load-bearing rules against the configured model on representative tasks,
  scoring observable behavior change (does removing the recall cascade in `07` measurably reduce
  `memory_search` use before answering; does removing reasoning discipline in `03` change tool-call
  planning quality).
- Conditional, evidence-scoped recalibration of the specific rules that fail (prose → few-shot demo).

### Out of scope
- The summarizer prompt — owned by `summarizer-fidelity-measure`.
- Re-introducing personality / persona calibration (orthogonal; persona stays off by default).
- Building a model-id routing `switch` unless a measured calibration conflict between two configured
  models forces it (explicitly deferred, not adopted).
- Any `docs/specs/` edit as a task.

## Behavioral Constraints
- All eval data real (`feedback_eval_real_world_data`); centralized eval settings only
  (`feedback_evals_centralized_settings`); config `llm.host` + `reasoning_model_settings()` /
  `noreason_model_settings()` per call type (`feedback_tests_use_config_model_settings`).
- Editing any rule `.md` trips the instruction-floor guards (budget ceiling + F5 no-deferred-tool
  signature) — run them during dev; keep `tool_name(` call syntax out of rule prose
  (`feedback_instruction_floor_guards_on_rule_edits`).
- Few-shot demonstrations must teach behavior, not bloat — net token delta is tracked; a calibrated
  rule that grows the floor without measured lift is rejected.
- Tail the log every run; long LLM calls are RCA-first, never timeout bumps
  (`feedback_long_llm_call_rca_first`, `feedback_tail_log_every_test_run`).

## Tasks

**✓ DONE — TASK-1 — Rule-compliance ablation harness (always)**
- files: `evals/eval_rule_compliance.py` (new; throwaway-grade), `evals/_settings.py` / `_deps.py` (reuse)
- done_when: `uv run python evals/eval_rule_compliance.py` runs the real agent on representative tasks
  (one recall-dependent: answer needs a prior memory; one reasoning-dependent: multi-step tool plan)
  under two prompt arms — full rules vs the target rule ablated — against the configured model, and
  records a JSONL run scoring observable behavior deltas (e.g. did the agent call `memory_search` before
  answering; did it follow the literal→pattern→honest-miss recall cascade from `07`; tool-call plan
  quality). The record states the **verdict per rule**: HONORED (model behavior changes when rule
  present → keep as-is) or IGNORED (no behavior delta → candidate for few-shot recalibration).
- scoring: deterministic signals first (span presence — did `memory_search` fire before the answer; did
  the recall cascade order appear) with explicit pass thresholds; any LLM-judge dimension (plan quality)
  must carry a stated threshold up front or it does not count toward the verdict. One rule ablated at a
  time, all other rules held fixed (so a delta is attributable to the ablated rule, not offset shift).
- success_signal: a measured, reproducible map of which load-bearing rules actually steer the configured
  model — no production code touched.
- prerequisites: none — `summarizer-fidelity-measure` verdict (REVISE) is known and recorded in Context as the leading indicator

**— NOT TRIGGERED — TASK-2 — Evidence-scoped rule recalibration (conditional: TASK-1 = IGNORED for ≥1 rule)**
- files: the specific `co_cli/context/rules/0N_*.md` the model ignores, `tests/` behavioral test as
  applicable
- done_when: the ignored rule(s) are rewritten from descriptive prose to few-shot demonstration of the
  desired behavior (opencode/gemini pattern); the TASK-1 harness is re-run and records the
  HONORED/IGNORED delta post-revision in the same JSONL format, showing the recalibrated rule now steers
  behavior; net static-floor token delta is recorded and within the instruction-floor budget.
- success_signal: the previously-ignored rule measurably changes the configured model's behavior after
  recalibration, without busting the prompt budget.
- prerequisites: TASK-1 (IGNORED verdict for the rule)

## Testing
- TASK-1 / re-runs are evals (UAT smoke), artifacts under `evals/_outputs/`.
- TASK-2 behavioral pytest (if added) asserts observable behavior change, never rule-text structure
  (`feedback_functional_tests_only`); fail-fast `-x`; pipe to `.pytest-logs/`; tail the log.

## Open Questions
1. Representative-task selection — must be discriminating (a task where the rule plausibly matters),
   not generic. Draft 2 tasks max per rule to keep the run cheap.
2. Behavior scoring — deterministic where possible (did `memory_search` fire before the answer = span
   presence) vs LLM judge for plan quality. Prefer deterministic signals.
3. If TASK-1 finds ALL load-bearing rules honored, this plan closes at TASK-1 with a "no calibration
   needed" finding and the refocus narrows to recall (`recall-degradation-visibility`).

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | real agent, 2 discriminating tasks × 2 prompt arms on the configured model → JSONL per-rule HONORED/IGNORED verdict | ✓ pass |
| TASK-2 | conditional rule recalibration (prose → few-shot) | — not triggered (no IGNORED verdict) |

**Harness:** `evals/eval_rule_compliance.py` (throwaway-grade). Ablation seam: a custom `OrchestratorSpec`
swaps the base-instructions builder for a rules block with one rule file removed (persona off → base layer
= rules only), reusing the real toolset guidance, per-turn instructions, and history processors. A startup
guard asserts the full-arm assembly is byte-equal to `build_rules_block` so a delta is never an artifact.
Deterministic scoring only (tool-call span presence), one rule ablated at a time, N=4 samples/arm.

**Measured verdict (model `qwen3.6:35b-a3b-agentic`, run `evals/_outputs/rule-compliance-20260617-222956-run.jsonl`):**

| Rule | full fire-rate | ablated fire-rate | Δ | verdict |
|------|----------------|-------------------|---|---------|
| `03_reasoning` (verify arithmetic via tool) | 1.00 | 0.25 | +0.75 | HONORED |
| `07_memory_protocol` (recall before answering) | 0.75 | 0.25 | +0.50 | HONORED |

**Overall: ALL-HONORED — no calibration needed.** Both load-bearing rules measurably steer the configured
model; removing either drops the demanded behavior to a 0.25 floor. Per Open Question 3, the plan closes
at TASK-1 and the prompting refocus narrows to recall (`recall-degradation-visibility`).

Measurement note (RCA discipline): the first probe pair saturated both arms at 1.00 — a non-discriminating
artifact (explicit-cue prompts: "compute the hash", "check what you've saved" command the behavior directly).
Fixed two ways before the verdict: (1) harness now labels a zero-delta at a saturated ceiling/floor
NON-DISCRIMINATING rather than IGNORED; (2) probes rewritten to implicit prompts so the ablated arm can
plausibly skip the behavior. The HONORED verdict above is from the corrected, discriminating run.

Reconciliation with `summarizer-fidelity-measure` (REVISE): not a contradiction. The model honors *light
behavioral* rules (03/07) but not the summarizer's *demanding 13-section omit-empty* contract — descriptive
prose suffices for behavioral nudges, not for heavy structured templates.

**Tests:** none — TASK-1 is an eval (UAT smoke); TASK-2 (which would add a behavioral pytest) did not trigger.
**Doc Sync:** clean — eval-only change, no shared module / public API / schema / spec touched.

**Overall: DELIVERED.** Measure-first gate worked end to end: TASK-1 measured ALL-HONORED on the configured
model, so the conditional recalibration (TASK-2) is correctly not built.

## Status
DELIVERED 2026-06-17 — TASK-1 measured ALL-HONORED (`03` Δ+0.75, `07` Δ+0.50) on `qwen3.6:35b-a3b-agentic`;
TASK-2 not triggered. Build gate had been satisfied by `summarizer-fidelity-measure` (REVISE). Ready for
`/review-impl`.
