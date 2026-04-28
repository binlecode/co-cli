# RESEARCH: Prompt Gaps — Main Flow

_Date: 2026-04-28 (trimmed to hermes→co parity gaps only; removed co-is-better and peer-weaknesses sections)_

Gaps in `co-cli`'s main-flow prompt architecture vs. hermes/codex/fork-cc peers. Each gap is a concrete missing capability that a peer already implements.

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

## Gaps

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
- this is the most important missing prompt surface relative to real coding-agent workflows

Note: the user-space variant (`~/.co-cli/MEMORY.md` injection) is covered by `docs/exec-plans/active/2026-04-27-000000-user-memory-md.md`. Workspace files (`CLAUDE.md`, `AGENTS.md`) are out of scope for co-cli.

### Gap 2: No model-family execution overlays

Evidence:

- Hermes has explicit overlays in `agent/prompt_builder.py:173-276` and developer-role handling in `run_agent.py:6715-6726`
- `co-cli` has no corresponding prompt path after repo-wide search

Why it matters:

- different model families need different corrective pressure
- the absence is most visible for tool persistence, prerequisite checking, and act-vs-ask defaults

**Audit evidence (2026-04-20):** `REPORT-llm-audit-eval-20260420-162700.md §6` flagged `test_clarify_handled_by_run_turn` — 3 consecutive `tool_call` spans, never reaching `stop`, with the model repeating clarify calls with invalid schema args after the first correct call. `gemini-3.1-pro-preview` is the model in the live eval suite. Hermes injects `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` for this exact family (parallel calls, non-interactive mode, keep going). `co-cli`'s `04_tool_protocol.md` has general tool-use rules but no comparable Gemini-specific corrective pressure.

**Partial fix applied:** `co_cli/tools/user_input.py` — `clarify` docstring strengthened with a CRITICAL block. This is a tool-description patch, not a model-family overlay. The prompt-architecture gap remains open.

### Gap 2b: Weak proactive session-search framing

Hermes has a standalone `SESSION_SEARCH_GUIDANCE` block injected whenever `session_search` is in the toolset (`prompt_builder.py:158-163`): "use session_search before asking the user to repeat themselves." This is prompt-level reinforcement that fires every session.

`co-cli`'s equivalent guidance lives only in the `memory_search` tool docstring (`co_cli/tools/memory.py:21-33`). That framing reaches the model only if it reads the tool schema, not as a dedicated prompt rule.

**Audit evidence (2026-04-20):** `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` — 5 LLM calls; call 3 pivoted from `memory_search` (correct) to `knowledge_search` (wrong) after getting empty results. The model had no prompt-level signal reinforcing the tool boundary once the initial query returned nothing.

**Partial fix applied:** `co_cli/tools/memory.py` — empty-result message now says "Do NOT switch to `knowledge_search`." This is a tool-result patch. A prompt-level rule in `04_tool_protocol.md` would give a stronger, earlier signal.

### Gap 3: No session/platform/environment prompt layer

Evidence:

- Hermes injects this via `gateway/session.py:187-280` and `run_agent.py:3528-3559`
- `co-cli` preflight only adds date plus recalled context in `co_cli/context/_history.py:937-966`

Why it matters:

- the model cannot be told about environment quirks (macOS BSD utils, Ollama local endpoint) through a first-class prompt layer

### Gap 4: No runtime instruction-scope discovery when the working set changes

Hermes solves this with `SubdirectoryHintTracker` in `agent/subdirectory_hints.py:48-224`, appending newly discovered local instruction files to tool results.

`co-cli` has no corresponding mechanism. Out of scope unless root-level workspace file loading is first adopted (see Gap 1 note above).

## Applied Fixes (2026-04-20 Audit)

| Fix | Location | Addresses | Open work |
|---|---|---|---|
| `clarify` docstring CRITICAL block | `co_cli/tools/user_input.py` | Audit finding: retry spiral with invalid schema args | Gap 2 (model-family overlay) still open |
| `memory_search` empty-result guidance | `co_cli/tools/memory.py` | Audit finding: memory→knowledge drift after empty results | Gap 2b (prompt-level session-search rule) still open |

## Recommended Direction

### P0 (by ROI)

1. **Gap 0** — Add an execution-discipline rule to `co_cli/prompts/rules/` (new `06_execution.md` or extend `05_workflow.md`): keep going until done, validate the result, do not stop at a plan. _Highest leverage per effort — one rule file, fixes premature stop across all models._
2. **Gap 2b** — Add a proactive `memory_search` use rule to `04_tool_protocol.md` (§Memory): "Use `memory_search` before asking the user to repeat anything from a past session." _One-liner addition to an existing file; audit-confirmed failure mode._
3. **Gap 2** — Add model-family execution overlays as a conditional `@agent.instructions` callback keyed on `config.llm.provider`. Start with Gemini (highest observed risk from audit). _Moderate effort; audit-confirmed retry spirals._
4. **Gap 3** — Add a lightweight environment/session note callback (OS platform, active provider/model, macOS BSD-util note). Late-bound `@agent.instructions`, not in static prefix. _Low effort; improves model self-awareness on tool behavior._
5. **Gap 1** — User-space `~/.co-cli/MEMORY.md` injection at startup. _Covered by `docs/exec-plans/active/2026-04-27-000000-user-memory-md.md`._

### Out of Scope

- **Gap 4** (subdirectory instruction discovery) — workspace files not loaded at root for co-cli; nothing to discover in subdirs.
- Hermes's 13-platform hint table — co-cli is terminal-only.
- Hermes's skills index in system prompt — co-cli uses deferred tool discovery via `search_tools`.
- Hermes's frozen memory snapshots in system prompt — co-cli's on-demand tool-based loading is better.
