# compaction-summary-structured-output

## Problem

The compaction summarizer in `co_cli/context/summarization.py` (`_SUMMARIZE_PROMPT`) uses a 12-section markdown template with "Skip if none" instructions. Local models (qwen3.5:35b-a3b-q4_k_m-agentic) intermittently violate the contract by emitting `## Pending User Asks\nNone\n` instead of omitting empty sections.

Evidence — `tests/test_flow_compaction_summarization.py::test_summarize_messages_from_scratch_returns_structured_text` has failed across multiple unrelated runs (logs 20260512, 20260514, 20260522 ship). The test catches a real defect: placeholder text leaks into the session's compacted context and survives onto disk.

The prompt is already maximally explicit — there's a `SKIP RULE` block with three repetitions of "do NOT write 'None.' / 'N/A' / placeholder". More prompt tuning won't fix small-model compliance reliably.

## Reference parity

- **hermes-agent** (`trajectory_compressor.py:582-597`) uses a freeform numbered-list prompt with no fixed `##` sections — sidesteps the problem entirely. Post-processing is one line: `_ensure_summary_prefix`.
- **openclaw** delegates to provider-controlled prompts via `registerCompactionProvider()`. The built-in path is internal; it does not expose a templated `##` contract to the LLM.
- Neither peer maintains a templated-markdown contract because LLM-compliance cost on local/small models is too high.

co-cli intentionally keeps the structured-markdown UX (humans scan `/compact` output by section; the merge logic transitions items between `## Pending User Asks` and `## Resolved Questions`). The right way to keep that UX while eliminating LLM compliance flakiness is **structured-output enforcement at the protocol layer** — not prompt tuning, not code-side regex stripping.

## Goal

Replace freeform-prompt + LLM-emits-markdown with Pydantic `output_type` + LLM-emits-validated-JSON + code-renders-markdown. The LLM emits JSON conforming to the schema (function calling / JSON mode); code renders markdown deterministically by skipping `None`/empty fields. Markdown output is byte-identical for the dense case and structurally clean for the sparse case.

## Design

### CompactionSummary schema

```python
class CompletedAction(BaseModel):
    action: str = Field(description="ACTION target — outcome. Be specific: file paths, line numbers, commands, exact outcomes.")
    tool: str = Field(description="Actual tool name from the conversation, e.g. file_edit, file_read, shell_exec. Do NOT invent.")


class ResolvedQuestion(BaseModel):
    question: str
    answer: str = Field(description="One sentence.")


class CompactionSummary(BaseModel):
    active_task: str = Field(
        description="CRITICAL. Copy the user's most recent request using their exact words. Quote them verbatim. If no outstanding task exists, write 'None.'"
    )
    goal: str
    constraints_preferences: str | None = Field(
        default=None,
        description="User constraints, preferences, and specs. None if no explicit constraints mentioned.",
    )
    key_decisions: str = Field(description="Decisions made and why. Rejected alternatives if relevant.")
    user_corrections: list[str] | None = Field(
        default=None,
        description="Verbatim quotes of explicit user corrections — messages where the user overrode or rejected a prior choice. None if no such corrections in the transcript.",
    )
    errors_fixes: str = Field(
        description="Errors and resolutions. When the user redirected after a failure, record both the failed attempt and their guidance."
    )
    completed_actions: list[CompletedAction]
    in_progress: str = Field(description="Work actively under way at compaction time.")
    remaining_work: str = Field(description="Work not yet started — framed as context, not as instructions to execute.")
    working_set: str = Field(description="Files read/edited/created. URLs fetched. Active tools.")
    pending_user_asks: list[str] | None = Field(
        default=None, description="Unanswered questions — verbatim or near-verbatim. None if no unanswered questions."
    )
    resolved_questions: list[ResolvedQuestion] | None = Field(
        default=None, description="Q&A pairs answered during the session. None if none."
    )
    next_step: str = Field(
        description="Immediate next action. MUST include a verbatim 1–2 line quote from the most recent user or assistant message as a drift anchor. No paraphrase."
    )
    critical_context: str | None = Field(
        default=None,
        description="Exact values that cannot be reconstructed: error strings, config values, line numbers, command outputs. None if nothing of this kind appeared.",
    )
```

### Prompt simplification

The new prompt drops the entire `SKIP RULE` block, the per-section format examples, and the integration rules (Pending → Resolved transition rules). Those become enforced by the schema (`Optional` fields) and the rendering logic. The prompt keeps only:

- High-level intent ("Distill the conversation history into a structured handoff summary")
- Strong directives on the two hard-to-schema-enforce items: `active_task` verbatim, `next_step` verbatim quote
- Tool-name honesty directive ("do NOT invent tool names")
- Integration rules for prior-summary merge (Pending → Resolved transitions live in the prompt — but the LLM emits typed lists, so they map cleanly)

Expected length: ~30 lines vs the current ~70 lines. Less surface for the LLM to violate.

### Rendering layer

```python
def render_summary_markdown(summary: CompactionSummary) -> str:
    """Render a CompactionSummary into the canonical markdown format.

    Skips None and empty-list fields entirely — no '## Section\nNone' leakage.
    """
    parts: list[str] = []

    def section(title: str, body: str | None) -> None:
        if body and body.strip():
            parts.append(f"## {title}\n{body.strip()}\n")

    def list_section(title: str, items: list[str] | None) -> None:
        if items:
            parts.append(f"## {title}\n" + "\n".join(f"- {item}" for item in items) + "\n")

    section("Active Task", summary.active_task)
    section("Goal", summary.goal)
    section("Constraints & Preferences", summary.constraints_preferences)
    section("Key Decisions", summary.key_decisions)
    list_section("User Corrections", summary.user_corrections)
    section("Errors & Fixes", summary.errors_fixes)
    if summary.completed_actions:
        body = "\n".join(
            f"{i}. {a.action} [tool: {a.tool}]"
            for i, a in enumerate(summary.completed_actions, start=1)
        )
        parts.append(f"## Completed Actions\n{body}\n")
    section("In Progress", summary.in_progress)
    section("Remaining Work", summary.remaining_work)
    section("Working Set", summary.working_set)
    list_section("Pending User Asks", summary.pending_user_asks)
    if summary.resolved_questions:
        body = "\n".join(f"- Q: {q.question} → A: {q.answer}" for q in summary.resolved_questions)
        parts.append(f"## Resolved Questions\n{body}\n")
    section("Next Step", summary.next_step)
    section("Critical Context", summary.critical_context)

    return "\n".join(parts)
```

### Call-site change

`summarize_messages` switches from `llm_call(deps, prompt, instructions=...)` (string return) to a structured-output variant — call shape depends on what's available in `co_cli/llm/call.py`. Likely options:

1. Pydantic-ai supports `output_type=CompactionSummary` directly on an Agent — wrap the call in a one-shot Agent with no tools.
2. Use the OpenAI-compatible JSON mode in `llm_call` if the layer exposes it.

Either way the function signature for `summarize_messages` is unchanged (`-> str`); it returns `render_summary_markdown(typed_result)`.

## Tasks

- [ ] **T1.** Add `CompletedAction`, `ResolvedQuestion`, `CompactionSummary` models to `co_cli/context/summarization.py`. Field descriptions are the contract surfaced to the LLM via the schema — write them carefully.

- [ ] **T2.** Implement `render_summary_markdown(summary: CompactionSummary) -> str`. Sections render in fixed order. None / empty-list fields are omitted entirely — by construction, no `## Header\nNone` output is possible.

- [ ] **T3.** Rewrite `_SUMMARIZE_PROMPT` to be schema-driven. Drop the entire `SKIP RULE` block, per-section format examples, integration-rule prose. Keep: high-level intent, `active_task` verbatim directive, `next_step` verbatim-quote requirement, tool-name honesty directive, prior-summary merge rules (Pending → Resolved transitions still need LLM-side instruction even though the typed lists make the mapping easier).

- [ ] **T4.** Change `summarize_messages` to invoke the LLM with `output_type=CompactionSummary`. Verify the right invocation against `co_cli/llm/call.py` and pydantic-ai's structured-output support. May require introducing a `llm_call_typed[T](..., output_type: type[T]) -> T` helper or wrapping in a one-shot Agent. Returns `render_summary_markdown(typed_result)`.

- [ ] **T5.** Update tests:
  - Keep `test_summarize_messages_from_scratch_returns_structured_text` (LLM-driven; verifies the round-trip produces correct markdown). The filler-placeholder assertion becomes impossible-by-construction; remove that assertion.
  - Add `test_render_summary_markdown_skips_none_fields` — deterministic, no LLM. Construct a sparse `CompactionSummary`; assert the rendered markdown omits `## Constraints & Preferences`, `## Pending User Asks`, etc.
  - Add `test_render_summary_markdown_omits_empty_lists` — deterministic. Empty list = no section header.
  - Keep `test_summarize_messages_iterative_incorporates_new_turns` — verifies the prior-summary merge still works through the typed pipeline.

- [ ] **T6.** Compatibility check: run a quick local probe against the configured Ollama model (qwen3.6 default; qwen3.5:35b-a3b-q4_k_m-agentic for older configs). Confirm structured output succeeds end-to-end. If the model rejects the schema (unlikely but possible for ≤8B models), document the minimum supported model in `docs/specs/`.

- [ ] **T7.** Spec sync — update `docs/specs/context.md` (or wherever the compaction summary format is documented) to reflect that the markdown shape is now derived from a typed schema. Mention the rendering invariant ("no empty sections possible").

## Risks

- **Small-model JSON compliance.** Pydantic schema with 14 fields, several optional, two list-of-object types. qwen3.5+ handles this fine in practice; smaller models may not. Mitigation: keep schema flat (no nested unions, no enums), document minimum model.

- **`next_step` verbatim quote.** Schema cannot enforce "this string contains a verbatim quote from the transcript". Stays as a prompt-level directive; if the model omits the quote, the field is still populated but with paraphrased content. Acceptable degradation — same risk as today.

- **Pydantic-ai call shape.** `summarize_messages` currently uses `llm_call(...)` which returns a string. Structured-output requires either a typed variant or a one-shot Agent. Implementation detail of T4; not a design risk.

- **Prior-summary merge transitions.** Today the prompt has explicit rules for moving items from a prior `## Pending User Asks` into `## Resolved Questions` when answered. With typed lists this becomes cleaner — the LLM sees `pending_user_asks: list[str]` and `resolved_questions: list[ResolvedQuestion]` — but the LLM still needs the merge rule in the prompt. Verified by `test_summarize_messages_iterative_incorporates_new_turns`.

## Files

- `co_cli/context/summarization.py` — schema, prompt rewrite, call, rendering helper
- `tests/test_flow_compaction_summarization.py` — assertions adjusted; deterministic rendering tests added
- `docs/specs/context.md` (or applicable spec) — spec sync

## Out of scope

- **Batch dream-cycle merge prompt** (`co_cli/memory/dream.py:_DREAM_MERGE_PROMPT` if it exists) — same anti-pattern (LLM emits templated markdown), separate plan.
- **Switching LLM providers or models** — provider-agnostic. Works with any pydantic-ai-supported provider.
- **Refactoring how compacted summaries are rendered into context for the next turn** — the output is still markdown; downstream consumers see the same shape.

## Cross-references

- Plan 1.5 (`2026-05-22-105500-plan1.5-dream-daemon-decouple.md`) — shipped 2026-05-22; surfaced this issue when its full-suite safety-net run hit the flaky summarization test.
- Reference repos: `hermes-agent/trajectory_compressor.py`, `openclaw/docs/concepts/compaction.md` — both deliberately avoid templated-markdown contracts with LLMs.
