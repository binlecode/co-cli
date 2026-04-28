# Exec Plan: Main-Flow Prompt Parity with Hermes

_Created: 2026-04-28_
_Slug: main-flow-prompt-parity_

## Problem

co-cli's main-flow prompt assembly has confirmed gaps relative to hermes's
`_build_system_prompt()` and per-model overlays. The cross-review (this conversation)
plus the prior audit (`REPORT-llm-audit-eval-20260420-162700.md §6`) confirm three
high-leverage gaps and one architectural smell:

- **G0 — No execution-discipline contract.** `05_workflow.md` implies persistence
  ("continue until all sub-goals are met") but does not state the invariants
  (act-don't-describe, don't-stop-at-plan, validate-before-finishing). Hermes makes
  these hard rules in `TOOL_USE_ENFORCEMENT_GUIDANCE` (`prompt_builder.py:173–186`)
  and `OPENAI_MODEL_EXECUTION_GUIDANCE` `<verification>` block (L238–245).
  Premature stop is the most common failure mode in coding agents.

- **G1 — Static content in per-turn callback (cache smell).**
  `_instructions.py:add_shell_guidance` accepts `ctx` but never reads it; it returns
  a constant string yet runs as a per-turn `@agent.instructions` callback
  (`core.py:153`). Effect: every turn re-emits ~8 lines after the static prefix,
  breaking what would otherwise be a clean prefix-cache boundary. Confirmed by
  reading the function — `ctx` parameter is unused.

- **G2 — No tool-availability gating on tool-specific guidance.** Hermes injects
  `MEMORY_GUIDANCE`, `SESSION_SEARCH_GUIDANCE`, `SKILLS_GUIDANCE` only when the
  corresponding tool is in `valid_tool_names` (`run_agent.py:3426–3434`).
  co-cli's `04_tool_protocol.md § Memory` ships unconditionally. Audit-confirmed
  failure: `test_tool_selection_and_arg_extraction[memory_search_past_sessions]`
  pivoted from `memory_search` (correct) to `knowledge_search` (wrong) on call 3
  after empty results — a prompt-level signal would have prevented the drift.

- **G3 — No model-family overlays.** Hermes selects guidance by model name:
  `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` for Gemini/Gemma, `OPENAI_MODEL_EXECUTION_GUIDANCE`
  for GPT/Codex. co-cli has no equivalent. Audit-confirmed:
  `test_clarify_handled_by_run_turn` showed `gemini-3.1-pro-preview` looping with
  invalid schema args — exactly the failure mode hermes's Gemini overlay
  ("Keep going / non-interactive / parallel calls") prevents.

## Existing Partial Fixes (context)

| Fix | Location | Addresses | Why still incomplete |
|---|---|---|---|
| `clarify` docstring CRITICAL block | `co_cli/tools/user_input.py` | G3 (Gemini retry spiral) | Tool-description patch, not a model-family prompt overlay |
| `memory_search` empty-result message | `co_cli/tools/memory.py` | G2 (memory→knowledge drift) | Tool-result patch reaches the model only after the drift; prompt-level rule fires before |

These are documented so the impl phases below don't accidentally undo them, and so
reviewers know the surface state when reading the new code.

## Scope

Three phases, each shippable as an independent PR. Earlier phases unblock later ones.

- **Phase 1 (R2) — Static rule additions + cache-smell fix.** Bounded, no new mechanisms.
- **Phase 2 (R3) — Toolset-availability gating callback.** New `@agent.instructions`
  callback infrastructure for conditional guidance.
- **Phase 3 (R4) — Model-family overlays.** Reuses Phase 2's callback pattern.

## Sequencing

This plan ships as three independent PRs. Order keeps each PR small and
independently reviewable:

1. **Phase 1 first.** Bounded: one new rule file, two edits, one function
   deletion. Lands in one cycle. Lays the act-don't-describe contract that
   downstream prompt features depend on.

2. **Phase 2 after Phase 1.** Toolset-availability gating callback introduces
   a new `@agent.instructions` mechanism; benefits from `06_execution.md`
   already being in the prompt before the conditional guidance lands.

3. **Phase 3 after Phase 2.** `model_family_prompt` reuses Phase 2's
   `@agent.instructions` callback pattern.

The user-notes plan that previously preceded Phase 2 has been killed
(redundant to the existing memory→knowledge→dream pipeline; see
`2026-04-28-091317-preference-pipeline.md` for the targeted enhancements
that replaced it).

## Out of Scope

- **Workspace-instruction ingestion (CLAUDE.md/AGENTS.md root files).** co-cli
  is not a coding agent; vendor manifests not adopted.
- **User-authored standing-instruction files** (the previously planned
  `~/.co-cli/USER_NOTES.md`). Killed as redundant to the existing
  memory→knowledge→dream pipeline. The remaining concerns from that
  decision are addressed by
  `docs/exec-plans/active/2026-04-28-091317-preference-pipeline.md`.
- **Runtime instruction-scope discovery** (hermes `SubdirectoryHintTracker`).
  Depends on workspace-file ingestion.
- **Session metadata block** (model/provider/session ID at session start). Low
  severity — defer to a separate plan after high-value gaps close.
- **Environment/platform layer** beyond the BSD-utils note already in shell
  guidance. Co-cli is terminal-only; hermes's 13-platform table not applicable.

---

## Phase 1 — Execution discipline + memory rule + shell guidance move

### Design

**P1.1 — Add `co_cli/context/rules/06_execution.md`.**

`_collect_rule_files()` (`assembly.py:42–83`) auto-discovers numbered .md files
and enforces contiguity (currently 01–05; adding 06 keeps contiguity). No code
changes required — the assembler picks the new file up automatically.

Content adapted from hermes (`prompt_builder.py:173–276`), trimmed and de-XML-tagged
to match co-cli's plain markdown rule-file convention:

```markdown
# Execution discipline

## Act, don't describe
When you say you will do something, do it now — make the tool call in this response.
Do not end a turn with a promise of future action ("I'll check that next",
"I would run…"). Every response must either (a) contain tool calls that advance
the task, or (b) deliver the final result. Narrating intentions without acting
is not acceptable.

## Don't stop at a plan
Decomposing a task is a means to execution, not the deliverable.
After decomposing, execute immediately. Do not surface a plan and wait — keep going.

## Prerequisites before acting
Before taking an action, check whether context-gathering or dependency-resolution
steps are needed. Do not skip prerequisite reads or lookups just because the
final action seems obvious.

## Validate before finishing
Before declaring a task done, confirm:
- **Correctness**: does the output satisfy every stated requirement?
- **Grounding**: are factual claims backed by tool outputs, not assumed?
- **Safety**: if the next step has side effects (writes, commands, API calls),
  confirm scope before proceeding.
```

What's adapted vs. dropped from hermes:

- **Adapted:** TOOL_USE_ENFORCEMENT_GUIDANCE (act-don't-describe), GOOGLE_MODEL
  OPERATIONAL "Keep going" line (don't-stop-at-plan), OPENAI `<prerequisite_checks>`
  and `<verification>` blocks (slim version).
- **Dropped:** `<mandatory_tool_use>` math/hash/date list (already covered by
  `03_reasoning.md` "Never assume — verify"); `<missing_context>` block (covered
  by `03_reasoning.md` "two kinds of unknowns"); XML tag wrapping (rule files use
  plain markdown).

**P1.2 — Strengthen `04_tool_protocol.md § Memory` with proactive recall rule.**

Current content (`04_tool_protocol.md:71–75`) says character/user memories are
loaded in the system prompt and discourages turn-start `memory_search`. Append
two sentences that codify the audit-confirmed failure mode:

```markdown
Use `memory_search` before asking the user to repeat anything from a past
session. If `memory_search` returns nothing, surface that explicitly — do
not pivot to `knowledge_search` for memory content; they index different stores.
```

**P1.3 — Move shell guidance into static rules; remove per-turn callback.**

The text in `_instructions.py:add_shell_guidance` (lines 22–36) is a constant
string. Move its content into a new `## Shell` section in `04_tool_protocol.md`
(placed after `## File tools`, before `## Deferred discovery`). Then:

- Delete the `add_shell_guidance` function from `_instructions.py`.
- Remove `agent.instructions(add_shell_guidance)` from `core.py:153`.
- Remove the import of `add_shell_guidance` from `core.py:124`.

Net effect: per-turn instruction overhead shrinks; static prefix grows by the
same content; prefix cache stable for everything after the static prefix.

### Tasks

- [ ] Write `co_cli/context/rules/06_execution.md` with the four sections above
- [ ] Append the proactive `memory_search` rule to `04_tool_protocol.md § Memory`
- [ ] Add new `## Shell` section to `04_tool_protocol.md` with the shell-guidance text
- [ ] Delete `add_shell_guidance` from `co_cli/agent/_instructions.py`
- [ ] Remove `add_shell_guidance` import and `agent.instructions(add_shell_guidance)` line from `co_cli/agent/core.py`
- [ ] Run `scripts/quality-gate.sh full` — confirm lint passes and assembly tests still load 06 rule

### Done When

- `co_cli/context/rules/06_execution.md` exists; `_collect_rule_files()` loads it
  (contiguous 01–06)
- `04_tool_protocol.md` contains the new Memory directive and the new `## Shell`
  section
- `_instructions.py` no longer defines `add_shell_guidance`; `core.py` no longer
  wires it
- `scripts/quality-gate.sh full` passes
- Manual smoke: `uv run co chat`, send a request that should require a tool;
  observe the model executes rather than proposing a plan

---

## Phase 2 — Toolset-availability gating callback

### Design

**P2.1 — Introduce `toolset_guidance_prompt(ctx)` callback in `_instructions.py`.**

Mirrors hermes `_build_system_prompt()` lines 3426–3434 — conditionally emit
guidance blocks based on which tools are loaded. Reads `ctx.deps.tool_index`
(already populated and used by `add_category_awareness_prompt`).

Pseudocode:

```python
def toolset_guidance_prompt(ctx: RunContext[CoDeps]) -> str:
    """Emit tool-specific guidance for tools actually present in the session."""
    parts: list[str] = []
    tool_names = set(ctx.deps.tool_index.keys())

    if "memory_search" in tool_names:
        parts.append(MEMORY_GUIDANCE)
    if "knowledge_search" in tool_names:
        parts.append(KNOWLEDGE_GUIDANCE)
    if "search_tools" in tool_names:
        parts.append(SEARCH_TOOLS_GUIDANCE)

    return "\n\n".join(parts)
```

Each `*_GUIDANCE` constant lives in `_instructions.py` (or a new
`co_cli/context/guidance.py` if the file gets large). Content moves out of
`04_tool_protocol.md`.

**P2.2 — Migrate tool-specific subsections out of rule files.**

The current rule files contain unconditional tool-specific content:
- `04_tool_protocol.md § Memory` → `MEMORY_GUIDANCE`
- `04_tool_protocol.md § Deferred discovery` → already gated via
  `add_category_awareness_prompt`; leave as-is or fold in
- `04_tool_protocol.md § Capability self-check` → `CAPABILITIES_GUIDANCE`
  (gated on `capabilities_check` tool)

Rule files become tool-agnostic. Tool-specific content lives in conditional
callbacks. Trade-off: tool-specific content moves out of the static prefix
into per-turn instructions — but it's already being shipped every turn anyway
inside the rule files; the only difference is conditionality.

**P2.3 — Wire in `core.py`.**

```python
# core.py:153 area, after Phase 1 removed add_shell_guidance
agent.instructions(add_category_awareness_prompt)
agent.instructions(toolset_guidance_prompt)  # new
agent.instructions(date_prompt)
agent.instructions(safety_prompt)
```

### Tasks

- [ ] Define `MEMORY_GUIDANCE`, `KNOWLEDGE_GUIDANCE`, `CAPABILITIES_GUIDANCE` constants
      (and any others identified during impl) in `_instructions.py` (or `context/guidance.py`)
- [ ] Implement `toolset_guidance_prompt(ctx)` reading `ctx.deps.tool_index`
- [ ] Remove `## Memory` and `## Capability self-check` sections from `04_tool_protocol.md`
      (their content moves into the constants above)
- [ ] Wire `toolset_guidance_prompt` in `core.py` build_agent
- [ ] Add a unit test: build agent with `tool_index` containing only `memory_search`,
      assert `toolset_guidance_prompt` returns memory text and not knowledge text
- [ ] Add a unit test: empty `tool_index` returns empty string (no guidance noise)
- [ ] Run `scripts/quality-gate.sh full`

### Done When

- `04_tool_protocol.md` contains no tool-specific subsections that ship unconditionally
- `toolset_guidance_prompt` returns non-empty only when matching tools present
- Existing assembly tests still pass (no changes to `build_static_instructions`)
- New unit tests pass for conditional emission
- Manual smoke: build a session with memory disabled (`config.memory.enabled=False`
  if applicable, or via tool-index manipulation in test), verify memory guidance
  is absent

---

## Phase 3 — Model-family execution overlays

### Design

**P3.1 — Introduce `model_family_prompt(ctx)` callback in `_instructions.py`.**

Mirrors hermes `_build_system_prompt()` lines 3461–3470. Inspects
`ctx.deps.config.llm.provider` and/or model name to select a family overlay.

```python
def model_family_prompt(ctx: RunContext[CoDeps]) -> str:
    """Emit model-family-specific corrective pressure."""
    model_name = (ctx.deps.config.llm.model or "").lower()
    provider = (ctx.deps.config.llm.provider or "").lower()

    if "gemini" in model_name or "gemma" in model_name:
        return GEMINI_OVERLAY
    if "gpt" in model_name or "codex" in model_name:
        return GPT_OVERLAY
    if provider == "ollama":
        return OLLAMA_OVERLAY  # only if a real failure is found; see P3.2

    return ""
```

**P3.2 — Phased rollout of overlays — start with audit-confirmed Gemini only.**

Initial Phase 3 PR ships GEMINI_OVERLAY only. GPT and Ollama overlays are
listed in the design but not implemented unless audit / live evals surface
specific failures for those families. This avoids speculative prompt bloat.

GEMINI_OVERLAY content (adapted from hermes `GOOGLE_MODEL_OPERATIONAL_GUIDANCE`,
trimmed to audit-confirmed failure modes):

```text
# Gemini-family operational rules
- Keep going: work autonomously until the task is fully resolved.
  Do not stop at a plan — execute it.
- Non-interactive flags: when running shell commands, prefer
  -y / --yes / --non-interactive to avoid hanging on prompts.
- Parallel tool calls: when multiple operations are independent
  (reading several files, etc.), make all calls in a single response
  rather than sequentially.
- Tool-call schema: each tool call must include all required arguments
  with valid types. If a call is rejected, do not retry with the same
  args — fix the schema or pick a different tool.
```

The last bullet is the audit-specific addition: it directly addresses the
`test_clarify_handled_by_run_turn` failure mode (3 consecutive `tool_call` spans
repeating clarify calls with invalid schema args).

**P3.3 — Wire in `core.py`.**

```python
# core.py, after toolset_guidance_prompt
agent.instructions(toolset_guidance_prompt)
agent.instructions(model_family_prompt)  # new
agent.instructions(date_prompt)
agent.instructions(safety_prompt)
```

### Tasks

- [ ] Define `GEMINI_OVERLAY` constant in `_instructions.py` (or `context/guidance.py`)
- [ ] Implement `model_family_prompt(ctx)` with Gemini detection only; stub
      branches for GPT and Ollama with explicit `# audit-not-yet-required` comments
- [ ] Wire `model_family_prompt` in `core.py` build_agent
- [ ] Add a unit test: build agent with `config.llm.model="gemini-3.1-pro-preview"`,
      assert overlay text is emitted
- [ ] Add a unit test: build agent with a non-Gemini model, assert empty string
- [ ] Re-run the audit eval `test_clarify_handled_by_run_turn` against Gemini —
      confirm the retry spiral is gone or reduced (existing eval suite, not new)
- [ ] Run `scripts/quality-gate.sh full`

### Done When

- `model_family_prompt` returns Gemini overlay when model name matches; empty otherwise
- Audit eval `test_clarify_handled_by_run_turn` no longer shows 3+ consecutive
  identical-schema-error tool_call spans (or shows reduced count) — record the
  delta in the delivery summary
- Existing prompt-assembly tests still pass
- The `clarify` docstring CRITICAL block in `co_cli/tools/user_input.py` is left
  in place — model-family overlay and tool-description patch are complementary,
  not redundant

---

## References

- Cross-review notes: this conversation's "Systematic Cross-Review" output
- Hermes prompt builder: `~/workspace_genai/hermes-agent/agent/prompt_builder.py`
  L173–276 (overlays), L144–171 (tool-aware guidance constants)
- Hermes assembler: `~/workspace_genai/hermes-agent/run_agent.py:3396–3470`
- co-cli static assembly: `co_cli/context/assembly.py:build_static_instructions`
- co-cli per-turn callbacks: `co_cli/agent/_instructions.py`,
  `co_cli/agent/core.py:118–160`
- co-cli existing rule files: `co_cli/context/rules/01_identity.md` through
  `05_workflow.md`
- Audit report: `REPORT-llm-audit-eval-20260420-162700.md §6` (Gemini retry
  spiral, memory→knowledge drift)
- Existing partial fixes: `co_cli/tools/user_input.py` (clarify CRITICAL block),
  `co_cli/tools/memory.py` (memory_search empty-result guidance)
- Related plan: `docs/exec-plans/active/2026-04-28-091317-preference-pipeline.md`
  — sharpens the existing memory→knowledge→dream pipeline so user preferences
  reliably reach future-session prompts. Replaces the killed user-notes plan
  on first-principles grounds (a user-authored static file is anti-agentic
  when chat-driven curation already covers the surface).
