# RESEARCH: Prompt Gaps — Main Flow

_Date: 2026-04-21 (split from `RESEARCH-prompting-co-vs-hermes-gaps.md` + `RESEARCH-llm-call-prompt-gap-review.md §3.1`)_

This doc covers gaps in `co-cli`'s **main-flow prompt architecture** — the base orchestrator agent's system prompt, static rule scaffold, personality layers, runtime addenda, and per-turn preflight injection. This is the prompt the model sees on every normal turn.

**Related research:**
- `RESEARCH-prompt-gaps-llm-tools.md` — gaps in LLM-calling tools (compaction summarizer, knowledge extractor, dream miner, delegation subagents)
- `RESEARCH-prompt-gaps-skill-prompts.md` — gaps in co-cli's skill system and skill body design

## Scope

Reviewed `co-cli` files:

- `co_cli/prompts/_assembly.py`
- `co_cli/agent/_core.py`
- `co_cli/agent/_instructions.py`
- `co_cli/context/_history.py`
- `co_cli/context/orchestrate.py`
- `co_cli/prompts/personalities/_injector.py`
- `co_cli/tools/knowledge/read.py`
- `co_cli/prompts/rules/01_identity.md` through `05_workflow.md`

Peer files reviewed:

- `hermes-agent/agent/prompt_builder.py`
- `hermes-agent/run_agent.py` (`_build_system_prompt()`)
- `hermes-agent/gateway/run.py`
- `hermes-agent/gateway/session.py`
- `hermes-agent/agent/subdirectory_hints.py`
- `hermes-agent/tools/memory_tool.py`
- `hermes-agent/agent/memory_manager.py`
- `fork-claude-code/constants/prompts.ts`
- `codex/codex-rs/protocol/src/prompts/base_instructions/default.md`

## Bottom Line

`co-cli` has the cleaner runtime prompt architecture.

- It keeps the stable prefix small and modular.
- It pushes volatile context into late preflight injections.
- It avoids persisting those injections back into history.
- It is easier to reason about cache behavior and retry behavior.

Hermes and codex have richer operational prompt surfaces.

- They give the model more explicit execution discipline.
- Hermes knows much more about workspace instructions, platform context, model quirks, and skills.
- Hermes has stronger repo-awareness once the agent starts moving through subdirectories.

The core gap is straightforward:

- `co-cli` is architecturally cleaner but under-instructed.
- Hermes is operationally stronger but instruction-heavy and somewhat lossy in what it loads.
- Codex and fork-cc enforce completion and validation more directly than either.

The right direction for `co-cli` is not "copy Hermes prompt stack." The right direction is "keep co's late-injection discipline, but add peer-class execution overlays, workspace instruction handling, and environment awareness in a cache-safe way."

## What The Code Actually Does

### `co-cli`

Static prompt assembly is centralized in `co_cli/prompts/_assembly.py:87-160`.

The static order is:

1. soul seed
2. character memories
3. mindsets
4. numbered rule files (`01_identity.md` through `05_workflow.md`)
5. recency-clearing advisory
6. soul examples
7. critique

That static prompt is attached once in `co_cli/agent/_core.py:129-148`.

Two runtime instruction callbacks are then registered in `co_cli/agent/_core.py:150-152`:

- `add_shell_guidance()` in `co_cli/agent/_instructions.py:8-18`
- `add_category_awareness_prompt()` in `co_cli/agent/_instructions.py:21-24`

Truly volatile data is not put in the static prompt. It is appended during model preflight:

- `_run_model_preflight()` in `co_cli/context/orchestrate.py:544-586`
- `build_recall_injection()` in `co_cli/context/_history.py:915-970`

That preflight injects:

- current date on every model-bound segment
- `personality-context` learned memories on every model-bound segment
- top-3 recalled knowledge artifacts once per new user turn

Importantly, those injections are ephemeral. `co_cli/context/orchestrate.py:551-556` and `580-586` make clear that the extended history is passed into the next segment without mutating the stored history. That is a strong design choice.

### Hermes

Hermes centralizes its base system prompt in `run_agent.py:_build_system_prompt()` at `run_agent.py:3396-3561`.

The actual implemented order is:

1. `SOUL.md` if present, else `DEFAULT_AGENT_IDENTITY`
2. tool-aware guidance blocks for memory / session search / skills
3. Nous subscription capability block
4. tool-use enforcement block when model/config matches
5. model-family overlays for Gemini/Gemma and GPT/Codex
6. optional `system_message`
7. built-in memory snapshot
8. built-in user profile snapshot
9. external memory-provider system block
10. skills index
11. context files
12. timestamp, session ID, model, provider
13. environment hints
14. platform hints

That prompt is intentionally frozen and cached per session in `run_agent.py:8440-8490`, and gateway session reuse exists specifically to preserve prefix caching in `gateway/run.py:629-639` and `8820-8828`.

Hermes then injects additional dynamic context outside the frozen prompt:

- session/platform context via `build_session_context_prompt()` in `gateway/session.py:187-280`
- combined ephemeral per-message prompt in `gateway/run.py:8695-8703`
- external memory prefetch, fenced and appended to the current user message only, in `run_agent.py:8715-8732` and `agent/memory_manager.py:65-80`
- subdirectory context hints appended to tool results, not to the system prompt, via `agent/subdirectory_hints.py:1-224`

### Documented-vs-actual drift

The `co-cli` top-level module comment in `co_cli/prompts/_assembly.py:3-6` says runtime-only layers include project instructions and always-on memories. The actual registered runtime callbacks in `co_cli/agent/_core.py:123-152` are only shell guidance and deferred-tool category awareness. There is no implemented project-instruction loader in the main prompt path, despite the comment implying one.

Hermes `run_agent.py:3404-3411` documents one assembly order; the actual append order in `3413-3559` is materially different. `system_message` is not layer 2 in practice; skills guidance comes before `system_message`; memory comes after tool/model guidance blocks; timestamp/model/provider/environment/platform hints trail the prompt.

## Where `co-cli` Is Better

### 1. Better separation between stable prompt and volatile context

The static prompt is assembled once in `co_cli/prompts/_assembly.py:87-160`. Volatile items are injected later in `co_cli/context/_history.py:915-970`. Those injections are not persisted into stored history (`co_cli/context/orchestrate.py:551-556`).

That gives `co-cli`:

- cleaner provider prefix caching
- cleaner retry semantics
- less accidental prompt drift inside a session
- easier reasoning about what the model "really saw" at any given iteration

Hermes also cares about prefix caching, but it achieves that partly by freezing much more content up front.

### 2. Better compaction honesty

`co-cli` explicitly teaches the model about cleared tool results through `RECENCY_CLEARING_ADVISORY` in `co_cli/prompts/_assembly.py:26-40` and injects it into the static prompt at `143-145`.

That is a good architectural fit with `truncate_tool_results()` in `co_cli/context/_history.py:368-415`. The model is warned about the placeholder behavior before it encounters it.

No equally explicit main-agent advisory exists in Hermes or codex for this compaction artifact class.

### 3. Lower instruction duplication

The main behavioral contract is concentrated in the personality scaffold and rule files (`co_cli/prompts/rules/04_tool_protocol.md:13-57` and `co_cli/prompts/rules/05_workflow.md:18-40`).

Hermes has more coverage but more overlap (identity, memory, session-search, skills, tool-use enforcement, model-family overlays, skills index mandate, platform hints, environment hints). That increases the chance of instruction competition and attention dilution.

## What `co-cli` Has That Peers Lack

These are prompt-level capabilities `co-cli` has that hermes, codex, and fork-cc do not implement, and that should be preserved through any future borrowing.

| Capability | `co-cli` location | Notes |
|---|---|---|
| Intent classification (Directive / Deep Inquiry / Shallow Inquiry) | `co_cli/prompts/rules/05_workflow.md:4-12` | Explicit routing taxonomy with per-class action rules. No equivalent in peers. |
| Anti-sycophancy rule | `co_cli/prompts/rules/01_identity.md:5-9` | "Prioritize technical accuracy over agreement … respectful correction is more valuable than false validation." |
| Source conflict surfacing | `co_cli/prompts/rules/03_reasoning.md:18-28` | "When one tool result contradicts another … surface the conflict explicitly." |
| Two kinds of unknowns | `co_cli/prompts/rules/03_reasoning.md:30-36` | "Before asking a question, determine if the answer is discoverable through tools." |
| Deferred tool discovery | `co_cli/agent/_instructions.py:21-24` + `add_category_awareness_prompt` | Tells the model to call `search_tools` when a capability isn't in the visible set. |
| Recency-clearing advisory | `co_cli/prompts/_assembly.py:26-40` | Explicitly teaches the model about `[tool result cleared…]` placeholders. |
| Memory constraint rules | `co_cli/prompts/rules/02_safety.md:18-27` | Fine-grained "do NOT save" list: workspace paths, transient errors, session-only context, ephemeral task state. |
| Personality depth | `co_cli/prompts/_assembly.py:87-160` | Seed + mindsets (per task-type) + examples + critique. Hermes offers a flat SOUL.md string; codex/fork-cc have no personality layer. |

## Where Peers Are Better

### 1. Workspace instruction handling

Hermes has first-class prompt architecture for workspace instructions:

- startup context loading in `agent/prompt_builder.py:1006-1045`
- context-file safety scanning in `agent/prompt_builder.py:31-73`
- lazy subdirectory hint discovery in `agent/subdirectory_hints.py:1-224`

Codex has a clear AGENTS scope spec in its base instructions.

By contrast, no `co_cli` prompt-path code loads `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, or equivalent project instructions into the main agent prompt.

### 2. Stronger model-family execution overlays

Hermes has explicit model-specific overlays:

- `TOOL_USE_ENFORCEMENT_GUIDANCE` in `agent/prompt_builder.py:173-186`
- `OPENAI_MODEL_EXECUTION_GUIDANCE` in `196-254`
- `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` in `258-276`
- developer-role swap for GPT-5/Codex in `run_agent.py:6715-6726`

`co-cli` has no comparable provider/model-specific prompt layer in the main agent path. This leaves `co-cli` with one generic contract across model families that demonstrably behave differently.

### 3. Stronger session and platform awareness

Hermes gives the agent explicit session context and platform constraints via:

- `gateway/session.py:187-280`
- `run_agent.py:3528-3559`

That includes source platform, user/thread context, session ID, model and provider name, environment hints such as WSL, and platform-specific formatting or capability notes.

`co-cli` only injects the current date plus recalled personality/knowledge context in the preflight path. It does not inject comparable environment or session metadata.

### 4. Stronger execution-discipline contract

Codex and fork-cc both state, in explicit terms, that the agent should keep going until the task is complete, validate its work, and avoid intention-only replies. Hermes adds model-family overlays for GPT/Codex/Gemini to enforce tool use, prerequisite checks, and verification.

`co-cli` implies persistence and completeness in `05_workflow.md` but does not state it as a hard invariant. The most common mode of model failure in coding agents is premature stop — describing an intention rather than executing it. `co-cli`'s implicit completeness rules are too easy to interpret as "suggest and hand off" rather than "keep executing until done."

### 5. First-class planning discipline

Codex and fork-cc both teach the agent when plans are warranted and what a good plan looks like. `co-cli` has a `todo` tool and completion rules, but its main prompt does not teach comparable plan quality or plan decision boundaries.

### 6. Stronger user-facing progress contract

Codex and fork-cc both explicitly teach concise preambles and progress updates as part of the base prompt. `co-cli` has short responsiveness guidance, but the progress-update contract is much thinner.

## Concrete Gaps In `co-cli`

### Gap 0: No explicit execution-discipline and validation contract

Evidence:

- `co_cli/prompts/rules/05_workflow.md` implies persistence and completeness but does not state it as a hard invariant
- No equivalent of codex's "keep going until done, do not stop at a plan" or fork-cc's explicit validation checklist exists in `co_cli/prompts/rules/`
- Hermes enforces this at the model-family level via `TOOL_USE_ENFORCEMENT_GUIDANCE` and `OPENAI_MODEL_EXECUTION_GUIDANCE` (`agent/prompt_builder.py:173-254`)

Why it matters:

- premature stop is the most common failure mode in coding agents
- `co-cli`'s implicit completeness rules are too easy to interpret as "suggest and hand off"

### Gap 1: No workspace-instruction ingestion in the main agent path

Evidence:

- `co_cli/prompts/_assembly.py:87-160` only assembles personality and rules
- `co_cli/agent/_core.py:150-152` only adds shell guidance and deferred-tool awareness
- `co_cli/context/_history.py:937-966` only injects date, personality-context memories, and recalled knowledge

Why it matters:

- repo conventions remain invisible unless the user pastes them manually
- no equivalent of Hermes subdirectory hints exists when the agent moves deeper into a codebase
- this is the most important missing prompt surface relative to real coding-agent workflows

### Gap 2: No model-family execution overlays

Evidence:

- Hermes has explicit overlays in `agent/prompt_builder.py:173-276` and developer-role handling in `run_agent.py:6715-6726`
- `co_cli` has no corresponding prompt path after repo-wide search

Why it matters:

- different model families need different corrective pressure
- the absence is most visible for tool persistence, prerequisite checking, and act-vs-ask defaults

**Audit evidence (2026-04-20):** `REPORT-llm-audit-eval-20260420-162700.md §6` flagged `test_clarify_handled_by_run_turn` — 3 consecutive `tool_call` spans, never reaching `stop`, with the model repeating clarify calls with invalid schema args after the first correct call. `gemini-3.1-pro-preview` is the model in the live eval suite. Hermes injects `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` for this exact family (parallel calls, non-interactive mode, keep going). `co-cli`'s `04_tool_protocol.md` has general tool-use rules but no comparable Gemini-specific corrective pressure.

**Partial fix applied:** `co_cli/tools/user_input.py` — `clarify` docstring strengthened with a CRITICAL block: "Call clarify exactly ONCE … do NOT pass `user_answer` yourself — it is always injected by the system and must be omitted entirely." This is a tool-description patch, not a model-family overlay. The model-family gap at the prompt-architecture level remains open.

### Gap 2b: Weak proactive session-search framing

Hermes has a standalone `SESSION_SEARCH_GUIDANCE` block injected whenever `session_search` is in the toolset (`prompt_builder.py:158-163`): "use session_search before asking the user to repeat themselves." This is prompt-level reinforcement that fires every session.

`co-cli`'s equivalent guidance lives only in the `memory_search` tool docstring (the "USE THIS PROACTIVELY when" block at `co_cli/tools/memory.py:21-33`). That framing reaches the model only if it reads the tool schema, not as a dedicated prompt rule.

**Audit evidence (2026-04-20):** `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` — 5 LLM calls; call 3 pivoted from `memory_search` (correct) to `knowledge_search` (wrong) after getting empty results. The judge flagged "user asked about past session history, not knowledge artifacts." The model had no prompt-level signal reinforcing the tool boundary once the initial query returned nothing.

**Fix applied:** `co_cli/tools/memory.py` — empty-result message now explicitly says "Do NOT switch to `knowledge_search` — that searches knowledge artifacts, not session history" and guides the model toward broader FTS5 queries. This is a tool-result patch. A prompt-level `memory_search` proactive-use rule in `04_tool_protocol.md` would give a stronger, earlier signal.

### Gap 3: No session/platform/environment prompt layer

Evidence:

- Hermes injects this via `gateway/session.py:187-280` and `run_agent.py:3528-3559`
- `co-cli` preflight only adds date plus recalled context in `co_cli/context/_history.py:937-966`

Why it matters:

- the model cannot be told about environment quirks or UI channel constraints through a first-class prompt layer
- that makes `co-cli` less adaptable if it ever broadens beyond a single terminal UX

### Gap 4: No runtime instruction-scope discovery when the working set changes

Hermes solves this with `SubdirectoryHintTracker` in `agent/subdirectory_hints.py:48-224`, appending newly discovered local instruction files to tool results.

`co-cli` has no corresponding mechanism.

Why it matters:

- even if `co-cli` later adds root-level `AGENTS.md` loading, that still would not solve nested instruction scopes
- Hermes is materially better once the agent traverses large repos

## Peer Weaknesses And Tradeoffs

These are reasons not to copy hermes blindly.

### 1. The Hermes frozen prompt is too fat

Hermes freezes identity, tool-aware guidance, model-specific overlays, memory snapshots, user profile, skills index, context files, timestamp, model/provider stamp, environment hints, and platform hints into one session prompt in `run_agent.py:3413-3559`.

Even with prompt caching, this still consumes context window and increases instruction competition.

### 2. Context-file loading is lossy

`build_context_files_prompt()` in `agent/prompt_builder.py:1006-1045` uses a first-match-wins policy: `.hermes.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules`. Only one project context type is loaded at startup. A repo that intentionally uses both `AGENTS.md` and `CLAUDE.md` will not surface both.

### 3. `AGENTS.md` and `CLAUDE.md` are cwd-only at startup

Hermes walks to git root for `.hermes.md` in `agent/prompt_builder.py:92-110`, but `AGENTS.md` and `CLAUDE.md` are only loaded from the current working directory in `944-973`. A nested launch directory can miss root repo instructions until a tool call later triggers subdirectory hints.

### 4. Frozen memory snapshot is intentionally stale mid-session

Built-in memory snapshots are frozen at session load and returned from `tools/memory_tool.py:359-370`. `run_agent.py:8445-8467` explicitly preserves the stored prompt snapshot across turns to maximize cache hits. Memory writes made during the session are not reflected in the system prompt until a rebuild boundary.

## Applied Fixes (2026-04-20 Audit)

| Fix | Location | Addresses | Open work |
|---|---|---|---|
| `clarify` docstring CRITICAL block | `co_cli/tools/user_input.py` | Audit finding: retry spiral with invalid schema args | Gap 2 (model-family overlay) still open |
| `memory_search` empty-result guidance | `co_cli/tools/memory.py` | Audit finding: memory→knowledge drift after empty results | Gap 2b (prompt-level session-search rule) still open |

## Recommended Direction

### P0

1. Add an execution-discipline rule to `co_cli/prompts/rules/` (new `06_execution.md` or extend `05_workflow.md`): keep going until done, validate the result, do not stop at a plan. Addresses Gap 0.
2. Add a workspace-instruction loader for `AGENTS.md`-class files. Addresses Gap 1.
3. Add a cache-safe subdirectory hint mechanism modeled after Hermes, but inject it via tool-result augmentation rather than static prompt growth. Addresses Gap 4.
4. Add model-family execution overlays, especially for Gemini-class models (highest audit-observed risk). Implement as a conditional `@agent.instructions` callback keyed on `config.llm.provider` — do not bake into the static prompt. Addresses Gap 2.

### P1

1. Add a proactive `memory_search` use rule to `04_tool_protocol.md` (§Memory): "Use `memory_search` before asking the user to repeat anything from a past session." Closes Gap 2b at the prompt level, complementing the tool-result fix already applied.
2. Add a lightweight environment/session note layer for terminal-specific realities (e.g. macOS BSD utils, Ollama local endpoint). Keep it late-bound via `@agent.instructions`, not static prefix. Addresses Gap 3.
3. Do not adopt Hermes's full frozen prompt mass.

### P2

1. Add first-class planning discipline to `05_workflow.md`: when plans are warranted, what a good plan looks like.
2. Strengthen the user-facing progress contract (preambles, mid-task updates).

## Final Assessment

`co-cli` does not need Hermes's whole prompt stack. It needs five specific capabilities peers already have:

1. explicit execution-discipline contract (codex / fork-cc pattern)
2. workspace-instruction loading (hermes pattern)
3. subdirectory instruction discovery (hermes pattern)
4. model-family execution overlays (hermes pattern)
5. lightweight environment/session awareness (hermes pattern)

If `co-cli` adds those while preserving its current late-injection discipline, it will likely end up with the stronger overall main-flow prompting architecture.
