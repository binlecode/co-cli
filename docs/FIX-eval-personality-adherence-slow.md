# FIX: eval_personality_adherence.py — Runtime and Eval-Purity Contract

**Discovered:** 2026-02-26  
**Context:** `uv run python evals/eval_personality_adherence.py` on local Ollama (`qwen3:30b-a3b-thinking-2507-q8_0-agentic`)  
**Status:** Open

## 1. First-Principles Goal

`eval_personality_adherence.py` should measure one thing:

- Whether the model's **final text response** expresses the target personality traits for that case.

This eval is **not** meant to test:

- Tool selection quality
- Memory recall behavior
- Multi-step tool chains
- Tool execution latency

If those concerns influence pass/fail or dominate runtime, the eval is impure and noisy.

---

## 2. Current Code Flow (as implemented)

### Orchestration

1. Load cases from `evals/p2-personality_adherence.jsonl` (currently **16** cases).
2. Build one real production agent via `get_agent()`.
3. For each case, reuse deps per personality and run N times (default 3).

### Per-run path (`run_single`)

1. Build eval model settings with `make_eval_settings(model_settings, max_tokens=2048)`.
2. Call `agent.run(..., usage_limits=UsageLimits(request_limit=2))`.
3. If output is `DeferredToolRequests`, mark run FAIL (`agent returned tool calls instead of text`).
4. Otherwise score response text with heuristic checks.

### What this means in practice

- Agent is built with **18 tools** available.
- Tool schemas are sent to the model.
- Model may call read-only tools (for example `recall_memory`) even for general CS prompts.
- With `request_limit=2`, tool call on request 1 can execute and still return text on request 2.
- In that case, run is scored as normal text (tool side path is invisible to scoring).

So the current eval can silently include tool-driven runs and extra round-trips.

---

## 3. Mismatch Against First Principles

### Mismatch A: Eval purity

Goal is style-only text quality, but current setup allows tool behavior to affect runtime and outcomes.

### Mismatch B: Unsupported control path in previous proposal

Prior proposal suggested `tool_choice="none"` via `ModelSettings`. In current repo stack (`pydantic-ai 1.59.0`), this is not a supported `ModelSettings` field, so that approach is not a reliable fix path.

### Mismatch C: `request_limit=1` is not a clean proxy for "text-only"

Lowering to `request_limit=1` does not guarantee a neat `DeferredToolRequests` outcome for all tools.
A first-turn normal tool call can instead fail on the second-request boundary with usage-limit errors.

### Mismatch D: Settings pass-through bug in eval helper

`evals/_common.py::make_eval_settings()` currently reads `ModelSettings` using attribute access on a `TypedDict` runtime object, so temperature/top_p/extra_body are dropped unexpectedly. This should be corrected independently so eval settings match intended model behavior.

---

## 4. Expected Behavior Contract (what "correct" looks like)

For P2 personality adherence:

1. One prompt should produce one final text answer.
2. Pass/fail should be determined only by text checks + transient infra handling.
3. Runtime should be dominated by model generation only (not tool dispatch).

This keeps the metric aligned with the objective: personality expression quality.

---

## 5. KPI Definition

### Quality KPIs (gating)

- `overall_accuracy >= threshold` (existing absolute gate, default 0.80)
- Optional strengthening: minimum per-personality floor (for example >= 0.70 each)

### Reliability KPIs

- `transient_error_case_rate` tracked and reported
- `non_transient_error_count = 0`

### Efficiency KPIs (monitoring)

- total wall-clock duration
- p50/p95 per-case duration

Do not hard-gate wall time globally because hardware varies; track trend per machine/model.

---

## 6. Recommended Fix Strategy

### Recommendation

Fix the settings pass-through bug (Mismatch D) so eval runs use the correct model parameters.

### Why this matters

Every eval run on Ollama currently executes with `ModelSettings(max_tokens=2048)` only — temperature, top_p, and `extra_body` (including `num_ctx`) are silently dropped. The eval is not running against the quirks-configured model behavior. This is a correctness bug independent of the tool-calling issue.

---

## 7. Verification Plan

After patching:

1. Run `uv run python evals/eval_personality_adherence.py`.
2. Confirm outputs remain in the same result files.
3. Compare accuracy before/after:
   - If accuracy shifts, attribute to corrected model parameters (temperature, num_ctx), not regression.

---

## 8. Concrete Patch Checklist

1. `evals/_common.py`
   - Fix `make_eval_settings()` to read `TypedDict` values via key access, not attribute access.

