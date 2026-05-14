# Exec Plan: Model-Family Overlay — Gemini + Ollama Eval

_Created: 2026-04-28_
_Slug: model-family-overlay_
_Predecessor: `docs/exec-plans/active/2026-04-28-081359-main-flow-prompt-parity.md` (Phase 3 split out)_

## Scope

Two independent workstreams:

1. **Gemini overlay** — port hermes's `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` delta
   (bullets not already in co-cli rule files). Ships from hermes production evidence;
   no new eval required.

2. **Ollama eval** — write `eval_tool_retry_behavior.py` targeting the configured
   Ollama model (detected by model name, not provider). Records evidence for any
   future model-specific overlay. No overlay ships from this eval unless evidence
   warrants it.

## Design

### Gemini overlay

hermes `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` has 7 bullets. The following are
already covered by co-cli's static rule files and must not be duplicated:

| hermes bullet | co-cli coverage |
|---|---|
| Keep going / don't stop at plan | `05_workflow.md § Execution` |
| Non-interactive flags (-y/--yes) | `04_tool_protocol.md § Shell` |
| Parallel tool calls | `04_tool_protocol.md` |
| Verify first / read before edit | `03_reasoning.md` |
| Dependency checks | `03_reasoning.md § Verification` |

Remaining delta (not in co-cli rule files) — these become `GEMINI_OVERLAY`:

- **Absolute paths:** always construct absolute file paths for all file system
  operations; combine project root with relative paths.
- **Conciseness:** keep explanatory text brief — a few sentences, not paragraphs;
  focus on actions and results over narration.

`GEMINI_OVERLAY` is defined in `co_cli/context/guidance.py`.

### Function and wire-in

`build_model_family_guidance(config: Settings) -> str` in `co_cli/context/guidance.py`.
Returns `GEMINI_OVERLAY` for Gemini/Gemma; `""` for all other providers.

```python
def build_model_family_guidance(config: Settings) -> str:
    """Emit model-family-specific corrective pressure."""
    model_name = (config.llm.model or "").lower()
    provider = (config.llm.provider or "").lower()

    if provider == "gemini" or "gemini" in model_name or "gemma" in model_name:
        return GEMINI_OVERLAY

    return ""
```

Wire-in position: `co_cli/agents/core.py` static assembly, after
`build_toolset_guidance`, before `build_category_awareness_prompt`.

```python
tool_guidance = build_toolset_guidance(tool_registry.tool_index)
if tool_guidance:
    static_parts.append(tool_guidance)

model_guidance = build_model_family_guidance(config)  # new
if model_guidance:
    static_parts.append(model_guidance)

category_hint = build_category_awareness_prompt(tool_registry.tool_index)
```

### Ollama eval

`evals/eval_tool_retry_behavior.py` — targets the configured Ollama model (name
from `settings.llm.model`). Gives the agent a task where one tool always returns
an error. Inspects `result.messages` for repeated `(tool_name, args)` pairs across
all `ToolCallPart` entries. Records: model name, provider, retry count per
`(tool_name, args)` pair, verdict.

Threshold: retry count > 1 for the same `(tool_name, args)` = FAIL. A FAIL result
is evidence for a model-name-specific overlay (e.g., `QWEN_OVERLAY`), not a
provider-level `OLLAMA_OVERLAY`. No overlay constant is written from this eval
unless the results clearly warrant one — that decision is deferred to a follow-on
plan.

Writes: `docs/REPORT-eval-tool-retry-behavior.md`.

## Tasks

- [ ] Define `GEMINI_OVERLAY` in `co_cli/context/guidance.py` — absolute paths +
      conciseness bullets only (see delta table above)
- [ ] Implement `build_model_family_guidance(config)` — Gemini branch only;
      returns `""` for all other providers
- [ ] Wire `build_model_family_guidance` into `co_cli/agents/core.py` static
      assembly block
- [ ] Add unit tests: Gemini provider returns non-empty; Ollama provider returns
      `""`; model name containing "gemma" returns non-empty
- [ ] Add static assembly test: model guidance appears in `static_instructions`,
      not registered via `agent.instructions()`
- [ ] Write `evals/eval_tool_retry_behavior.py` targeting the configured Ollama
      model — instrument `(tool_name, args)` repeat count, write verdict and trace
      to `docs/REPORT-eval-tool-retry-behavior.md`
- [ ] Run `scripts/quality-gate.sh full`

## Done When

- `GEMINI_OVERLAY` contains only the 2 delta bullets not covered by existing rule
  files; no duplication of base rules
- `build_model_family_guidance` returns overlay text for Gemini/Gemma, `""` for
  Ollama and everything else
- Model guidance is in static instructions, not a per-turn callback
- Ollama eval report exists with retry-count evidence for the configured model
- The `clarify` CRITICAL block in `co_cli/tools/user_input.py` is left in place

## References

- hermes overlay source: `~/workspace_genai/hermes-agent/agent/prompt_builder.py:270-288`
  (`GOOGLE_MODEL_OPERATIONAL_GUIDANCE`) and `run_agent.py:4794-4805` (injection logic)
- co-cli static assembly: `co_cli/agents/core.py:148-168`
- co-cli toolset guidance: `co_cli/context/guidance.py`
- Predecessor plan: `docs/exec-plans/active/2026-04-28-081359-main-flow-prompt-parity.md`
- Existing partial fix: `co_cli/tools/user_input.py` (clarify CRITICAL block)
