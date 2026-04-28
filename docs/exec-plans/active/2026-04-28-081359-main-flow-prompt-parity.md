# Exec Plan: Main-Flow Prompt Parity with Hermes

_Created: 2026-04-28_
_Slug: main-flow-prompt-parity_

## Problem

co-cli's main-flow prompt assembly has confirmed gaps relative to hermes's
`_build_system_prompt()` and per-model overlays. Review against current source
shows the original plan overstated one gap and used stale evidence for two others.
The remaining high-leverage gaps are:

- **G0 — Execution-discipline contract exists but is fragmented.**
  Current `04_tool_protocol.md` already has `## Execute, don't promise`
  (act-don't-describe) and `## Error recovery` (no unchanged retry);
  `05_workflow.md` already requires multi-step execution and completeness checks;
  `03_reasoning.md` already requires verification before claims. The real gap is
  not absence of the contract, but that "do not stop at a plan" and
  "validate before finishing" are split across files and partly implicit.
  Phase 1 must dedupe and sharpen the existing rule files rather than blindly
  adding a mostly-overlapping sixth rule.

- **G1 — Static content in per-turn callback (cache smell).**
  `_instructions.py:add_shell_guidance` accepts `ctx` but never reads it; it returns
  a constant string yet runs as a per-turn `@agent.instructions` callback
  (`core.py:153`). Effect: every turn re-emits ~8 lines after the static prefix,
  breaking what would otherwise be a clean prefix-cache boundary. Confirmed by
  reading the function — `ctx` parameter is unused.

- **G2 — Tool-specific guidance still ships unconditionally.** Hermes injects
  tool-specific guidance only when matching tools are available. co-cli's
  `04_tool_protocol.md § Memory` and `§ Capability self-check` ship
  unconditionally, even though the correct runtime source of truth is
  `ctx.deps.tool_index`. Current source also has no `knowledge_search` tool
  (`memory_search` replaced the old knowledge/session split), so Phase 2 must
  gate only real tools: `memory_search`, `capabilities_check`, and deferred-tool
  categories derived from `VisibilityPolicyEnum.DEFERRED`.

- **G3 — Session-static guidance is implemented as dynamic callbacks.**
  Hermes builds tool guidance, skills guidance, model overlays, timestamp, model,
  provider, context files, and environment hints into a cached session prompt
  (`run_agent.py:3399-3561`). co-cli currently uses per-request
  `@agent.instructions` for constant shell guidance and date, and the original
  Phase 2/3 design would add more session-static callbacks. Phase 2/3 should
  prefer static assembly helpers called during `build_agent()` unless the content
  genuinely varies during a session.

- **G4 — No model-family overlays, but the Gemini evidence is not established.**
  Hermes selects guidance by model name: `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` for
  Gemini/Gemma, `OPENAI_MODEL_EXECUTION_GUIDANCE` for GPT/Codex. co-cli has no
  equivalent. However, the cited `test_clarify_handled_by_run_turn` retry spiral
  in `REPORT-llm-audit-eval-20260420-162700.md` ran in the Ollama tool-calling
  flow, not the Gemini flow. Phase 3 must first add or run model-specific evidence
  for the target family; it should not ship a Gemini overlay solely from the old
  report.

- **G5 — Skills and metadata/context layers are not parity-mapped.**
  Hermes injects mandatory skill-loading guidance when skills tools are present
  (`prompt_builder.py:777-799`; `run_agent.py:3499-3515`), project context files
  with injection scanning and truncation (`prompt_builder.py:32-75`,
  `prompt_builder.py:878-1045`; `run_agent.py:3517-3526`), and session metadata
  (`run_agent.py:3528-3537`). co-cli currently treats workspace instruction
  ingestion and session metadata as out of scope, which may be correct, but they
  must remain explicitly recorded as deferred parity gaps for overall main-prompt
  parity.

## Existing Partial Fixes (context)

| Fix | Location | Addresses | Why still incomplete |
|---|---|---|---|
| `clarify` docstring CRITICAL block | `co_cli/tools/user_input.py` | G4 (clarify retry risk) | Tool-description patch, not a model-family prompt overlay; current evidence does not prove a Gemini-specific failure |
| `memory_search` empty-result message | `co_cli/tools/memory/recall.py` | G2 (empty-result retry behavior) | Latest audit shows repeated `memory_search` broadening on a cold store; prompt-level rule should bound retry count and surface no results |
| Current rule-file execution language | `03_reasoning.md`, `04_tool_protocol.md`, `05_workflow.md` | G0 | Covers most behavior already; Phase 1 should consolidate missing pressure, not duplicate sections |

These are documented so the impl phases below don't accidentally undo them, and so
reviewers know the surface state when reading the new code.

## Scope

Three phases, each shippable as an independent PR. Earlier phases unblock later ones.

- **Phase 1 (R2) — Static rule cleanup + cache-smell fix.** Bounded, no new mechanisms.
- **Phase 2 (R3) — Toolset-availability gated static assembly.** New static
  guidance helper called from `build_agent()` using the session `tool_index`.
- **Phase 3 (R4) — Model-family overlay evidence + overlay only if justified.**
  Reuses Phase 2's static assembly pattern only after model-specific evidence exists.

## Sequencing

This plan ships as three independent PRs. Order keeps each PR small and
independently reviewable:

1. **Phase 1 first.** Bounded: sharpen existing rule files and remove one
   constant per-turn callback. Lands in one cycle. Keeps the static prefix
   cleaner without adding a new mechanism.

2. **Phase 2 after Phase 1.** Toolset-availability gating introduces static
   assembly helpers; benefits from Phase 1's cleaned-up execution rules already
   being in the prompt before conditional guidance lands.

3. **Phase 3 after Phase 2.** First establish which model family actually needs
   extra pressure, then add `build_model_family_guidance` only for that family.

The user-notes plan that previously preceded Phase 2 has been killed
(redundant to the existing memory→knowledge→dream pipeline; see
`2026-04-28-091317-preference-pipeline.md` for the targeted enhancements
that replaced it).

## Deferred Parity Gaps

These are real Hermes main-prompt layers but remain out of scope for this plan:

- **Workspace/context-file ingestion:** Hermes loads `.hermes.md`, `AGENTS.md`,
  `CLAUDE.md`, and `.cursorrules` with threat scanning and truncation
  (`prompt_builder.py:32-75`, `prompt_builder.py:878-1045`). co-cli is not
  adopting vendor workspace manifests in this plan. If co-cli later becomes more
  coding-agent-oriented, this needs a separate design with injection isolation,
  precedence, and cache behavior.
- **Session metadata block:** Hermes injects conversation start time, session ID,
  model, and provider into the cached prompt (`run_agent.py:3528-3537`). co-cli
  currently injects only today's date per request. Phase 1 must add an explicit
  date/cache decision, but full session metadata is deferred.
- **Environment/platform layer:** Hermes has environment hints and platform
  formatting hints (`run_agent.py:3551-3559`). co-cli is terminal-only for this
  plan; only shell/platform command guidance is in scope.
- **Full skills management parity:** Hermes has model-callable skill tools and a
  mandatory skill-loading prompt (`prompt_builder.py:777-799`). co-cli has a
  skill system, but exposing skill discovery/loading to the model is a broader
  tool-surface decision. Phase 2 should record the gap and avoid pretending
  memory/capability guidance is complete skills parity.

## Out of Scope

- **Workspace-instruction ingestion (CLAUDE.md/AGENTS.md root files).** co-cli
  is not adopting vendor manifests in this plan; see Deferred Parity Gaps.
- **User-authored standing-instruction files** (the previously planned
  `~/.co-cli/USER_NOTES.md`). Killed as redundant to the existing
  memory→knowledge→dream pipeline. The remaining concerns from that
  decision are addressed by
  `docs/exec-plans/active/2026-04-28-091317-preference-pipeline.md`.
- **Runtime instruction-scope discovery** (hermes `SubdirectoryHintTracker`).
  Depends on workspace-file ingestion.
- **Session metadata block** (model/provider/session ID at session start).
  Deferred except for the Phase 1 date/cache decision.
- **Environment/platform layer** beyond the BSD-utils note already in shell
  guidance. Co-cli is terminal-only; hermes's 13-platform table not applicable.

---

## Phase 1 — Execution-rule cleanup + memory retry bound + shell guidance move

### Design

**P1.0 — Preserve the current five-rule partition, but tighten ownership.**

Do not add `06_execution.md` unless the edited `05_workflow.md` becomes too
dense to read. The current five buckets are the right shape:

- `01_identity.md` — personality/stance only.
- `02_safety.md` — secrets, git, approvals, and memory privacy boundaries.
- `03_reasoning.md` — verification, source authority, deterministic-state
  tool-use examples, and missing-context policy.
- `04_tool_protocol.md` — general tool mechanics, parallel/sequential calls,
  error recovery, file/shell/deferred discovery.
- `05_workflow.md` — intent classification, plan-then-execute, completeness,
  validation-before-finish.

Rule-file edits should move content toward those ownership boundaries rather
than adding a sixth rule as an overflow bucket.

**P1.1 — Sharpen existing execution rules; do not add a duplicate rule file by default.**

Current source already covers most execution discipline:

- `03_reasoning.md § Verification` says deterministic state must be verified with tools.
- `04_tool_protocol.md § Execute, don't promise` says every response must either
  call tools or deliver a final result.
- `04_tool_protocol.md § Error recovery` says never retry an identical failed call.
- `05_workflow.md § Execution` and `§ Completeness` say to continue through all
  sub-goals and verify every stated sub-goal before ending.

Phase 1 should add only the missing pressure:

- In `05_workflow.md § Execution`, explicitly say planning is not the deliverable:
  after decomposing, execute immediately unless the user explicitly asked only
  for a plan or review.
- In `05_workflow.md § Completeness`, add a short validation checklist:
  correctness, grounding in tool output, requested formatting/schema, side-effect
  scope/safety, and any unresolved blocker.
- Keep the existing `04_tool_protocol.md § Execute, don't promise` rather than
  duplicating it in a new `06_execution.md`.

If implementation finds the edited sections becoming hard to read, a new
`06_execution.md` is acceptable, but then the overlapping text must be removed
from `04_tool_protocol.md` / `05_workflow.md` and docs/tests must be updated for
the `01`-`06` rule set.

Hermes reference: `TOOL_USE_ENFORCEMENT_GUIDANCE` states "do not describe what
you would do" and "Never end your turn with a promise of future action"
(`prompt_builder.py:173-185`); `OPENAI_MODEL_EXECUTION_GUIDANCE` says not to
stop early when another tool call would materially improve the result and to
verify before finalizing (`prompt_builder.py:197-245`).

Also add Hermes's "act on obvious defaults" and prerequisite/dependency checks
where they fit the co-cli rule partition:

- `03_reasoning.md § Two kinds of unknowns`: when a question has an obvious
  default interpretation, act on it instead of asking for clarification. Only
  ask when ambiguity genuinely changes which tool/action should run.
- `03_reasoning.md § Verification`: before imports/library usage or framework
  assumptions, check dependency files such as `pyproject.toml`, `package.json`,
  `requirements.txt`, `Cargo.toml`, etc. when present.

Hermes reference: `<act_dont_ask>` and `<prerequisite_checks>`
(`prompt_builder.py:221-236`).

**P1.2 — Fix continuity wording and memory retry pressure.**

Current content says character/user memories are loaded in the system prompt,
discourages turn-start `memory_search`, and correctly tells the model to use
`memory_search` before asking the user to repeat past-session facts.

`01_identity.md` currently says "Remember past interactions" without saying how.
That can invite hallucinated continuity. Reword it to maintain continuity using
loaded context and `memory_search` when needed, then let `04_tool_protocol.md`
own the operational memory behavior until Phase 2 moves it into gated guidance.

The latest audit (`REPORT-test-suite-llm-audit-20260428.md`) shows the remaining
failure mode is not pivoting to `knowledge_search` (that tool no longer exists);
it is over-broadening `memory_search` four times on a cold store. Add:

```markdown
If `memory_search` returns no results, make at most one broader retry when a
clear broader query exists. After that, surface the miss explicitly instead of
continuing to search variations.
```

Hermes reference: `SESSION_SEARCH_GUIDANCE` says to use session search when the
user references a past conversation before asking them to repeat themselves
(`prompt_builder.py:158-162`). `OPENAI_MODEL_EXECUTION_GUIDANCE` says empty or
partial results should be retried with a different strategy before giving up,
but co-cli's current memory audit shows this must be bounded
(`prompt_builder.py:197-203`; `REPORT-test-suite-llm-audit-20260428.md`).

**P1.3 — Resolve persistence-policy conflict between memory and deep inquiry.**

`02_safety.md` says to save stable preferences, corrections, decisions, and
cross-session facts proactively. `05_workflow.md` says Deep Inquiries should not
persist state until an explicit Directive. Clarify that durable memory curation
for stable user preferences/corrections is exempt from "do not persist task
state"; temporary task progress, active TODOs, completed-work logs, and transient
debugging notes remain prohibited.

Hermes reference: `MEMORY_GUIDANCE` prioritizes durable facts and recurring
corrections, while forbidding task progress, session outcomes, completed-work
logs, and temporary TODO state (`prompt_builder.py:144-156`).

**P1.4 — Add concrete deterministic-state examples to reasoning.**

`03_reasoning.md` says "Never assume — verify," but Hermes makes the common
failure modes concrete: time/date/timezone, system state, file contents, git
state, and current facts must use tools (`prompt_builder.py:207-219`). Add a
co-cli-specific examples list under `03_reasoning.md § Verification`.

Do not blindly import Hermes's "all arithmetic must use a tool" rule. Current
co-cli functional coverage expects simple arithmetic like `17×23` to be answered
directly without a tool. A narrower rule is better: use tools for non-trivial,
high-stakes, bulk, or exact arithmetic/hashes/encodings; direct mental arithmetic
is acceptable when obviously simple.

**P1.5 — Move shell guidance into static rules; remove per-turn callback.**

The text in `_instructions.py:add_shell_guidance` (lines 22–36) is a constant
string. Move its content into a new `## Shell` section in `04_tool_protocol.md`
(placed after `## File tools`, before `## Deferred discovery`). Then:

- Delete the `add_shell_guidance` function from `_instructions.py`.
- Remove `agent.instructions(add_shell_guidance)` from `core.py:153`.
- Remove the import of `add_shell_guidance` from `core.py:124`.

Net effect: per-turn instruction overhead shrinks; static prefix grows by the
same content; prefix cache remains stable for the shell guidance.

Add Hermes's non-interactive command pressure while moving the text: when running
commands that may prompt, prefer flags such as `-y`, `--yes`, or
`--non-interactive` when the command supports them. Hermes reference:
`GOOGLE_MODEL_OPERATIONAL_GUIDANCE` (`prompt_builder.py:258-275`).

**P1.6 — Decide the date/cache behavior explicitly.**

Current co-cli injects `Today is YYYY-MM-DD` through `date_prompt`, a per-request
`@agent.instructions` callback. Hermes freezes conversation-start time in the
cached system prompt (`run_agent.py:3528-3537`). Phase 1 must make an explicit
choice:

- Preferred for cache parity: freeze a session-start date/time string during
  `build_agent()` and include it in static instructions.
- Acceptable alternative: move date to a tail history injection if the date must
  vary by request.
- Avoid: leaving a per-request instruction callback for date without documenting
  why this cache variance is acceptable.

Full model/provider/session-ID metadata stays deferred, but the date callback
must not remain an unexamined cache-smell after this plan.

### Tasks

- [ ] Repartition wording without adding `06_execution.md` unless the five-file structure becomes hard to read
- [ ] Reword `01_identity.md` continuity line to avoid hallucinated memory; reference loaded context and `memory_search`
- [ ] Edit `05_workflow.md § Execution` to state "don't stop at a plan" without duplicating `04_tool_protocol.md`
- [ ] Edit `05_workflow.md § Completeness` with a short validation-before-finish checklist: correctness, grounding, requested formatting/schema, side-effect scope/safety, blocker status
- [ ] Clarify in `05_workflow.md` that proactive durable memory saves are exempt from the Deep Inquiry "do not persist state" rule
- [ ] Add deterministic-state examples to `03_reasoning.md § Verification` (time/date/timezone, system state, file contents, git state, current facts)
- [ ] Keep simple mental arithmetic allowed; require tools for non-trivial/exact/high-stakes calculations, hashes, encodings, and checksums
- [ ] Add obvious-default guidance to `03_reasoning.md § Two kinds of unknowns`
- [ ] Add dependency-file prerequisite guidance to `03_reasoning.md § Verification`
- [ ] Append the bounded empty-result retry rule to `04_tool_protocol.md § Memory`
- [ ] Add new `## Shell` section to `04_tool_protocol.md` with the shell-guidance text plus non-interactive flag guidance
- [ ] Delete `add_shell_guidance` from `co_cli/agent/_instructions.py`
- [ ] Remove `add_shell_guidance` import and `agent.instructions(add_shell_guidance)` line from `co_cli/agent/core.py`
- [ ] Resolve date/cache behavior: freeze date/time into static instructions or move date to a tail injection; remove or justify `date_prompt`
- [ ] Add or update a static prompt assembly test that asserts the edited rule text is present in `build_static_instructions()`
- [ ] If a new `06_execution.md` is introduced, update `docs/specs/personality.md`, `co_cli/personality/prompts/loader.py`, and tests/doc references from `01`-`05` to `01`-`06`
- [ ] Update `docs/specs/prompt-assembly.md` and `docs/specs/core-loop.md` if `date_prompt` or static instruction assembly changes
- [ ] Run `scripts/quality-gate.sh full`

### Done When

- Rule ownership matches the five-file partition above; no operational memory rule remains in `01_identity.md`
- `05_workflow.md` contains explicit don't-stop-at-plan and validation-before-finish pressure
- `05_workflow.md` no longer blocks durable preference/correction memory saves during Deep Inquiry
- `03_reasoning.md` contains concrete deterministic-state tool-use examples without forcing tools for trivial arithmetic
- `03_reasoning.md` contains obvious-default and dependency-file prerequisite guidance
- `04_tool_protocol.md` contains the bounded Memory retry directive and the new `## Shell`
  section
- `_instructions.py` no longer defines `add_shell_guidance`; `core.py` no longer
  wires it
- Date/cache behavior is explicitly resolved and reflected in code/specs
- Static prompt assembly coverage confirms the edited rule content is loaded
- `scripts/quality-gate.sh full` passes
- Manual smoke: `uv run co chat`, send a request that should require a tool;
  observe the model executes rather than proposing a plan

---

## Phase 2 — Toolset-availability gated static assembly

### Design

**P2.1 — Introduce `build_toolset_guidance(tool_index)` static assembly helper.**

Mirrors hermes `_build_system_prompt()` lines 3426–3434 — conditionally emit
guidance blocks based on which tools are loaded. It should run at agent
construction from `tool_registry.tool_index`, not as a per-request
`@agent.instructions` callback, because the native/MCP tool index is session
static after bootstrap. Important:
`knowledge_search` is not a current tool; `memory_search` covers both T2
artifacts and T1 sessions. `search_tools` is SDK-provided deferred-discovery
surface, not a native `ToolInfo` entry, so deferred-discovery gating must derive
from deferred tools in `tool_index`, not from `"search_tools" in tool_names`.

Pseudocode:

```python
def build_toolset_guidance(tool_index: dict[str, ToolInfo]) -> str:
    """Emit tool-specific guidance for tools actually present in the session."""
    parts: list[str] = []
    tool_names = set(tool_index.keys())

    if "memory_search" in tool_names:
        parts.append(MEMORY_GUIDANCE)
    if "capabilities_check" in tool_names:
        parts.append(CAPABILITIES_GUIDANCE)

    return "\n\n".join(parts)
```

Each `*_GUIDANCE` constant should live in a static prompt module such as
`co_cli/context/guidance.py` (preferred over `_instructions.py`, which should
remain for true per-request instruction callbacks). Content moves out of
`04_tool_protocol.md`.

**P2.2 — Migrate tool-specific subsections out of rule files.**

The current rule files contain unconditional tool-specific content:
- `04_tool_protocol.md § Memory` → `MEMORY_GUIDANCE`
- `04_tool_protocol.md § Deferred discovery` → leave static if it remains
  tool-agnostic, or fold into the existing `add_category_awareness_prompt`
  path that emits only when deferred categories exist
- `04_tool_protocol.md § Capability self-check` → `CAPABILITIES_GUIDANCE`
  (gated on `capabilities_check` tool)

Rule files become tool-agnostic. Tool-specific content lives in conditional
static assembly. Trade-off: the static prefix becomes different for different
tool configurations, but remains stable within one session. Do not introduce
dynamic callbacks for content that is constant and universally applicable.

Hermes reference: `_build_system_prompt()` conditionally appends
`MEMORY_GUIDANCE`, `SESSION_SEARCH_GUIDANCE`, and `SKILLS_GUIDANCE` only when
`memory`, `session_search`, or `skill_manage` are in `valid_tool_names`
(`run_agent.py:3425-3434`). co-cli should mirror the principle but map it to the
current tool surface: `memory_search`, `capabilities_check`, and deferred
categories from `ToolInfo.visibility == DEFERRED`. Do not introduce a
`knowledge_search` guidance block; current tests assert that tool is absent.

**P2.2b — Keep deferred discovery guidance available without lying about `search_tools`.**

`search_tools` is SDK-provided when deferred tools exist, not a native
`ToolInfo` entry. Therefore:

- The existing `add_category_awareness_prompt()` path is the right home for
  category-level deferred discovery hints.
- If static `## Deferred discovery` remains in `04_tool_protocol.md`, it should
  be generic enough not to become wrong when no deferred tools are present.
- If it moves into conditional guidance, gate it by checking whether any
  `ctx.deps.tool_index` entry has `visibility == VisibilityPolicyEnum.DEFERRED`,
  not by checking `"search_tools" in tool_names`.

**P2.3 — Record skills prompt parity without pretending it is solved.**

Hermes injects mandatory skill-loading guidance when `skills_list`,
`skill_view`, or `skill_manage` are available (`run_agent.py:3499-3515`) and the
prompt tells the model to load matching skills before replying and update bad
skills before finishing (`prompt_builder.py:777-799`). co-cli has a skill
system, but the current main-agent tool surface does not include model-callable
skill discovery/loading tools. Phase 2 should add a short documented deferred
gap, not a fake static skills prompt that names unavailable tools.

If implementation discovers existing co-cli model-callable skill tools, add a
real gated `SKILLS_GUIDANCE`; otherwise explicitly leave skills parity deferred
to a separate tool-surface plan.

**P2.4 — Wire in `core.py` as static instructions.**

```python
# core.py:131 area, after build_static_instructions(config)
static_parts = [build_static_instructions(config)]
tool_guidance = build_toolset_guidance(tool_registry.tool_index)
if tool_guidance:
    static_parts.append(tool_guidance)
static_instructions = "\n\n".join(static_parts)

# Per-request callbacks remain only for genuinely dynamic content:
agent.instructions(date_prompt)
agent.instructions(safety_prompt)
```

If Phase 1 removes or replaces `date_prompt`, update this snippet accordingly.

### Tasks

- [ ] Define `MEMORY_GUIDANCE` and `CAPABILITIES_GUIDANCE` constants in `co_cli/context/guidance.py` or an equivalent static prompt module
- [ ] Implement `build_toolset_guidance(tool_index)` reading the session `tool_registry.tool_index`
- [ ] Do not reference `knowledge_search`; it is intentionally absent from the current tool surface
- [ ] Remove `## Memory` and `## Capability self-check` sections from `04_tool_protocol.md`
      (their content moves into the constants above)
- [ ] Preserve the bounded empty-result retry rule from Phase 1 inside `MEMORY_GUIDANCE`; do not delete it when removing `## Memory`
- [ ] Keep deferred-discovery guidance valid: either leave `## Deferred discovery` static or gate it by checking for any `ToolInfo.visibility == DEFERRED`
- [ ] If deferred-discovery guidance is gated, do not check for `"search_tools"` in `tool_index`; derive it from deferred `ToolInfo` entries
- [ ] Record skills prompt parity as deferred unless model-callable skill discovery/loading tools are available in current source
- [ ] Wire `build_toolset_guidance` into `core.py` static instruction assembly; do not add a per-request callback for session-static guidance
- [ ] Add a unit test: helper with `tool_index` containing only `memory_search`,
      assert it returns memory text and not capabilities text
- [ ] Add a unit test: `tool_index` containing only `capabilities_check` returns capabilities text and not memory text
- [ ] Add a unit test: empty `tool_index` returns empty string (no guidance noise)
- [ ] Add a static assembly/build-agent test proving tool guidance appears in static instructions and is not registered as an `agent.instructions` callback
- [ ] Run `scripts/quality-gate.sh full`

### Done When

- `04_tool_protocol.md` contains no tool-specific subsections that ship unconditionally
- `build_toolset_guidance` returns non-empty only when matching tools present
- No `knowledge_search` prompt text or tests are introduced
- The Phase 1 bounded memory retry rule survives the Phase 2 migration
- Toolset guidance is static for the session, not a new per-request callback
- Skills prompt/tool parity is either implemented with real available tools or explicitly deferred
- Existing assembly tests still pass (no changes to `build_static_instructions`)
- New unit tests pass for conditional emission
- Manual smoke or test: build with a controlled `tool_index`, verify memory and
  capabilities guidance appear only when their matching tools are present

---

## Phase 3 — Model-family overlay evidence, then targeted overlay

### Design

**P3.1 — First establish model-family-specific evidence.**

The prior plan treated `test_clarify_handled_by_run_turn` as Gemini evidence,
but current test and report evidence show that failure came from the Ollama
tool-calling path. Before adding a Gemini overlay, run or add a model-specific
main-agent/tool-calling eval that exercises the same failure mode against Gemini.

Evidence targets:

- For Gemini/Gemma: malformed tool schema retries, repeated identical tool calls,
  or premature plan-only stops in a Gemini main-agent/tool-calling run.
- For Ollama/Qwen: repeated clarify/tool calls or over-searching empty memory,
  if current audits keep showing that family-specific pressure helps.
- For GPT/Codex: no overlay until an actual co-cli failure mode is observed.

Hermes reference: tool-use enforcement is configurable and auto-applies only
for model-name substrings in `TOOL_USE_ENFORCEMENT_MODELS`
(`prompt_builder.py:188-190`; `run_agent.py:3439-3461`). The model-specific
overlays then branch to Google or OpenAI text (`run_agent.py:3462-3470`). co-cli
should preserve that discipline: family overlays are corrective pressure for
observed model behavior, not a new always-on rule layer.

**P3.2 — Introduce `build_model_family_guidance(config)` only for a proven family.**

Mirrors hermes `_build_system_prompt()` lines 3461–3470. Inspects
`config.llm.provider` and/or model name to select a family overlay. It should
run at agent construction and append to static instructions, not as a
per-request callback.

```python
def build_model_family_guidance(config: Settings) -> str:
    """Emit model-family-specific corrective pressure."""
    model_name = (config.llm.model or "").lower()
    provider = (config.llm.provider or "").lower()

    if _gemini_overlay_is_supported_by_evidence and (
        provider == "gemini" or "gemini" in model_name or "gemma" in model_name
    ):
        return GEMINI_OVERLAY
    if _ollama_overlay_is_supported_by_evidence and provider == "ollama":
        return OLLAMA_OVERLAY

    return ""
```

**P3.3 — Phased rollout of overlays — start with only audit-confirmed content.**

Initial Phase 3 PR ships no overlay unless the Phase 3 evidence task confirms
a family-specific issue. GPT branches should not be stubbed unless they return
empty with no dead constants. Avoid speculative prompt bloat.

If Gemini evidence is confirmed, use a short `GEMINI_OVERLAY` adapted from
hermes `GOOGLE_MODEL_OPERATIONAL_GUIDANCE`, trimmed to the confirmed failure:

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

The last bullet is valid only if the Gemini evidence task reproduces schema
retry behavior. If the evidence points to Ollama/Qwen instead, write an
Ollama/Qwen overlay with only the observed corrective pressure.

Prompt-reference menu for possible overlays:

- General tool-use enforcement: Hermes `TOOL_USE_ENFORCEMENT_GUIDANCE`
  (`prompt_builder.py:173-186`) maps to co-cli's existing
  `04_tool_protocol.md § Execute, don't promise`; use only deltas not already
  covered by Phase 1.
- OpenAI/GPT/Codex overlay: Hermes `OPENAI_MODEL_EXECUTION_GUIDANCE`
  (`prompt_builder.py:196-254`) contains tool persistence, mandatory tool-use
  examples, act-don't-ask, prerequisites, verification, and missing-context
  handling. Most of this belongs in static co-cli rules unless a GPT/Codex eval
  proves model-specific reinforcement is needed.
- Gemini/Gemma overlay: Hermes `GOOGLE_MODEL_OPERATIONAL_GUIDANCE`
  (`prompt_builder.py:256-276`) contains absolute paths, verify-first,
  dependency checks, conciseness, parallel tool calls, non-interactive commands,
  and keep-going. Most universal parts should be static rule text; only
  confirmed Gemini-specific pressure should remain in a Gemini overlay.

**P3.4 — Wire in `core.py`.**

```python
# core.py, after build_toolset_guidance(...)
model_guidance = build_model_family_guidance(config)
if model_guidance:
    static_parts.append(model_guidance)

# Per-request callbacks remain only for genuinely dynamic content:
agent.instructions(date_prompt)
agent.instructions(safety_prompt)
```

### Tasks

- [ ] Add or run a model-specific main-agent/tool-calling eval for Gemini before adding any Gemini overlay
- [ ] Record the evidence: model/provider, prompt, tool-call sequence, failure mode, and whether it is family-specific
- [ ] If evidence confirms Gemini/Gemma needs an overlay, define `GEMINI_OVERLAY` in `_instructions.py` (or `context/guidance.py`) with only confirmed corrective pressure
- [ ] If evidence instead confirms Ollama/Qwen needs an overlay, define `OLLAMA_OVERLAY` with only confirmed corrective pressure
- [ ] Implement `build_model_family_guidance(config)` only for families with evidence; return empty for all others
- [ ] Wire `build_model_family_guidance` into `core.py` static instruction assembly only if at least one overlay ships
- [ ] Add unit tests for each shipped overlay and a non-matching model returning empty string
- [ ] Add a build-agent/static assembly test proving model guidance is static, not registered as an `agent.instructions` callback
- [ ] Re-run the relevant audit eval and record before/after call-count or retry-pattern delta
- [ ] Run `scripts/quality-gate.sh full`

### Done When

- A model-family overlay is shipped only when backed by model-specific evidence
- `build_model_family_guidance` returns overlay text only for the proven family; empty otherwise
- Model-family guidance is static for the session, not a per-request callback
- Relevant audit eval no longer shows the targeted retry/stop failure, or shows
  a recorded reduction with a clear explanation of residual risk
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
- Hermes full main-prompt assembly continuation: `~/workspace_genai/hermes-agent/run_agent.py`
  L3474–3561 (memory/user profile, skills prompt, context files, timestamp/model/provider,
  environment and platform hints)
- Hermes skills prompt: `~/workspace_genai/hermes-agent/agent/prompt_builder.py`
  L760–805
- Hermes context-file loading and injection scanning:
  `~/workspace_genai/hermes-agent/agent/prompt_builder.py` L32–75 and L878–1045
- co-cli existing rule files: `co_cli/context/rules/01_identity.md` through
  `05_workflow.md`
- Audit report: `REPORT-llm-audit-eval-20260420-162700.md §6` (old retry
  spiral and old memory→knowledge drift evidence; review current source before
  treating these as still-current)
- Latest full-suite audit: `REPORT-test-suite-llm-audit-20260428.md` (current
  memory behavior is repeated `memory_search` broadening on a cold store, not
  `knowledge_search` drift)
- Existing partial fixes: `co_cli/tools/user_input.py` (clarify CRITICAL block),
  `co_cli/tools/memory/recall.py` (memory_search empty-result guidance)
- Related plan: `docs/exec-plans/active/2026-04-28-091317-preference-pipeline.md`
  — sharpens the existing memory→knowledge→dream pipeline so user preferences
  reliably reach future-session prompts. Replaces the killed user-notes plan
  on first-principles grounds (a user-authored static file is anti-agentic
  when chat-driven curation already covers the surface).
