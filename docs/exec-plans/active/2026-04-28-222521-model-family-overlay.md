# Exec Plan: Model-Family Overlay Evidence + Targeted Overlay

_Created: 2026-04-28_
_Slug: model-family-overlay_
_Predecessor: `docs/exec-plans/active/2026-04-28-081359-main-flow-prompt-parity.md` (Phase 3 split out)_

## Problem

co-cli has no model-family-specific prompt overlays. Hermes selects guidance by
model name: `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` for Gemini/Gemma,
`OPENAI_MODEL_EXECUTION_GUIDANCE` for GPT/Codex. The prior audit evidence
(`REPORT-llm-audit-eval-20260420-162700.md`) was misattributed — the retry spiral
came from the Ollama tool-calling path, not Gemini. An overlay must not ship
without family-specific evidence.

## Design

**P1 — First establish model-family-specific evidence.**

The prior plan treated `test_clarify_handled_by_run_turn` as Gemini evidence,
but current test and report evidence show that failure came from the Ollama
tool-calling path. Before adding a Gemini overlay, run or add a model-specific
main-agent/tool-calling eval that exercises the same failure mode against Gemini.

Evidence targets:

- For Gemini/Gemma: malformed tool schema retries, repeated identical tool calls,
  or premature plan-only stops in a Gemini main-agent/tool-calling run.
- For Ollama/Qwen: repeated clarify/tool calls or over-searching empty memory,
  if current audits keep showing that family-specific pressure helps.
- GPT/Codex: not applicable — co-cli's provider is `Literal["ollama", "gemini"]`;
  no OpenAI path exists.

Hermes reference: tool-use enforcement is configurable and auto-applies only
for model-name substrings in `TOOL_USE_ENFORCEMENT_MODELS`
(`prompt_builder.py:188-190`; `run_agent.py:3439-3461`). The model-specific
overlays then branch to Google or OpenAI text (`run_agent.py:3462-3470`). co-cli
should preserve that discipline: family overlays are corrective pressure for
observed model behavior, not a new always-on rule layer.

**P2 — Introduce `build_model_family_guidance(config)` only for a proven family.**

Mirrors hermes `_build_system_prompt()` lines 3461–3470. Inspects
`config.llm.provider` and/or model name to select a family overlay. It should
run at agent construction and append to static instructions, not as a
per-request callback.

The evidence gate is a ship decision, not a runtime flag: if evidence confirms
an overlay for a family, define the constant and return it from this function;
if not, leave the branch absent entirely — no dead constants, no stub returns.

```python
def build_model_family_guidance(config: Settings) -> str:
    """Emit model-family-specific corrective pressure."""
    model_name = (config.llm.model or "").lower()
    provider = (config.llm.provider or "").lower()

    if provider == "gemini" or "gemini" in model_name or "gemma" in model_name:
        return GEMINI_OVERLAY   # define only after evidence confirms
    if provider == "ollama":
        return OLLAMA_OVERLAY   # define only after evidence confirms

    return ""
```

Only the branches with confirmed evidence are included at ship time. If only
Gemini evidence exists, the Ollama branch is absent (and vice versa). Do not
stub empty-string returns for unconfirmed families.

**P3 — Phased rollout — start with only audit-confirmed content.**

Initial PR ships no overlay unless the evidence task confirms a family-specific
issue. GPT branches should not be stubbed unless they return empty with no dead
constants. Avoid speculative prompt bloat.

If Gemini evidence is confirmed, write a `GEMINI_OVERLAY` covering only the
observed failure mode. Do not mirror hermes `GOOGLE_MODEL_OPERATIONAL_GUIDANCE`
bullet-for-bullet — most of its content is already in co-cli's Phase 1 rule
files:

| Hermes GOOGLE_MODEL_OPERATIONAL_GUIDANCE bullet | co-cli coverage |
|---|---|
| Keep going / don't stop at plan | `05_workflow.md § Execution` (Phase 1) |
| Non-interactive flags (-y/--yes) | `04_tool_protocol.md § Shell` (Phase 1) |
| Parallel tool calls | `04_tool_protocol.md` (existing) |
| Verify first / read before edit | `03_reasoning.md` (existing) |
| Dependency checks | `03_reasoning.md § Verification` (Phase 1) |
| Tool-call schema: don't retry same args | `04_tool_protocol.md § Error recovery` (existing) |

The overlay only adds value if Gemini is specifically ignoring rules that Ollama
follows — meaning the eval must show the failure, and the overlay text must be
*differently framed* (more forceful or restructured for Gemini's attention
pattern), not a copy of existing rule text. Duplicating what is already in the
static prefix is noise, not pressure.

Bullets absent from co-cli rule files (and therefore overlay candidates if
confirmed by eval): absolute paths emphasis, conciseness directive.

If the evidence points to Ollama/Qwen instead, write an `OLLAMA_OVERLAY` with
only the observed corrective pressure. The current Ollama signal (memory_search
iterating 5× on cold store, escalating to `memory_list`) is partially addressed
by `MEMORY_GUIDANCE`'s bounded retry rule — an Ollama overlay is only warranted
if the eval shows failure modes that `MEMORY_GUIDANCE` does not reach (e.g.,
tool-call schema retries or plan-only stops in the tool-calling path).

Prompt-reference menu for overlays (co-cli-relevant only):

- Gemini/Gemma: Hermes `GOOGLE_MODEL_OPERATIONAL_GUIDANCE`
  (`prompt_builder.py:256-276`) — use only confirmed deltas not already in
  co-cli Phase 1 rule files (see table above).
- Ollama/Qwen: no hermes equivalent; derive from co-cli eval evidence directly.

**P4 — Wire in `core.py`.**

Injection position: after `build_toolset_guidance`, before the `Agent(...)`
constructor call. This mirrors hermes's assembly order — model overlay sits
adjacent to tool-behavior guidance, before memory/context layers. Do not move
it to the end of `static_parts`.

```python
# core.py — orchestrator path, Block 0 static assembly
static_parts = [build_static_instructions(config)]

tool_guidance = build_toolset_guidance(tool_registry.tool_index)
if tool_guidance:
    static_parts.append(tool_guidance)

model_guidance = build_model_family_guidance(config)  # new — after tool guidance
if model_guidance:
    static_parts.append(model_guidance)

category_hint = build_category_awareness_prompt(tool_registry.tool_index)
if category_hint:
    static_parts.append(category_hint)

static_instructions = "\n\n".join(static_parts)
agent = Agent(..., instructions=static_instructions, ...)

# Block 1: per-turn callbacks — real-time, not cached
agent.instructions(current_time_prompt)  # before safety_prompt (matches current core.py)
agent.instructions(safety_prompt)
```

`build_model_family_guidance` is only imported and called when at least one
overlay constant is defined. If no overlay ships, do not add the call site.

## Tasks

- [ ] Add or run a model-specific main-agent/tool-calling eval for Gemini before adding any Gemini overlay
- [ ] Record the evidence: model/provider, prompt, tool-call sequence, failure mode, and whether it is family-specific
- [ ] If evidence confirms Gemini/Gemma needs an overlay, define `GEMINI_OVERLAY` in `co_cli/context/guidance.py` with only confirmed corrective pressure
- [ ] If evidence instead confirms Ollama/Qwen needs an overlay, define `OLLAMA_OVERLAY` with only confirmed corrective pressure
- [ ] Implement `build_model_family_guidance(config)` only for families with evidence; return empty for all others
- [ ] Wire `build_model_family_guidance` into `core.py` static instruction assembly only if at least one overlay ships
- [ ] Add unit tests for each shipped overlay and a non-matching model returning empty string
- [ ] Add a build-agent/static assembly test proving model guidance is static, not registered as an `agent.instructions` callback
- [ ] Re-run the relevant audit eval and record before/after call-count or retry-pattern delta
- [ ] Run `scripts/quality-gate.sh full`

## Done When

- A model-family overlay is shipped only when backed by model-specific evidence
- `build_model_family_guidance` returns overlay text only for the proven family; empty otherwise
- Model-family guidance is static for the session, not a per-request callback
- Relevant audit eval no longer shows the targeted retry/stop failure, or shows
  a recorded reduction with a clear explanation of residual risk
- Existing prompt-assembly tests still pass
- The `clarify` docstring CRITICAL block in `co_cli/tools/user_input.py` is left
  in place — model-family overlay and tool-description patch are complementary,
  not redundant

## References

- Predecessor plan: `docs/exec-plans/active/2026-04-28-081359-main-flow-prompt-parity.md`
- Hermes prompt builder: `~/workspace_genai/hermes-agent/agent/prompt_builder.py`
  L173–276 (overlays), L144–171 (tool-aware guidance constants)
- Hermes assembler: `~/workspace_genai/hermes-agent/run_agent.py:3396–3470`
- co-cli static assembly: `co_cli/context/assembly.py:build_static_instructions`
- co-cli toolset guidance: `co_cli/context/guidance.py`
- co-cli agent core: `co_cli/agent/core.py`
- Audit report (old, misattributed): `docs/REPORT-llm-audit-eval-20260420-162700.md §6`
- Latest full-suite audit: `docs/REPORT-test-suite-llm-audit-20260428-161324.md`
- Existing partial fix: `co_cli/tools/user_input.py` (clarify CRITICAL block)
