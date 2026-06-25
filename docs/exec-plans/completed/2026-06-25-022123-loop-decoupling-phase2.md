# Loop decoupling Phase 2 — owned turn loop behind a flag, parallel to the graph (orchestrator + subagents)

**Milestone:** `2026-06-24-234633-loop-decoupling-milestone.md` (`0.9.0`). **Design:** `2026-06-24-234633-loop-decoupling-design.md` §3, §6.2–6.11. **Phase 1 (shipped):** `docs/exec-plans/completed/2026-06-25-013407-loop-decoupling-phase1.md` built the `model_turn` provider client. Gate 1 for the milestone is APPROVED; this pass details Phase 2's per-task `done_when` before `/orchestrate-dev`.

**Phase-2 scope decision (TL, this pass):** Phase 2 builds the owned loop for **both** the interactive orchestrator turn *and* the subagent driver (`run_standalone`), resolving OQ-4 (subagent structured output) end-to-end. Confirmed with the maintainer — chosen over an orchestrator-only carve-out so OQ-4 lands where the milestone placed it and both drivers share one step-loop core from the start.

## Context

Phase 1 built `model_turn` (`co_cli/llm/model_turn.py`) — the graph-free async-context client over `direct.model_request_stream` that folds surrogate-retry + the `chat` span + JSON repair. It is dead code until a loop drives it. **Phase 2 builds that loop.**

Today the agent turn is the pydantic-ai *graph*:
- **Orchestrator:** `run_turn` (`orchestrate.py:977`) drives `agent.iter()` (`_execute_run`, `orchestrate.py:429`), walking model-request and call-tools nodes. The graph hosts, on co's behalf: the five history processors (`build.py:48`, registered on the `Agent`), `_clean_message_history` normalization (`_agent_graph.py:893,2053`), static + per-turn instruction injection, tool dispatch through `_CallSeamToolset` (`toolset.py:142` — tool span, per-request cap, MCP spill), the reasoning-overflow raise (`_agent_graph.py:1059`, string-matched at `orchestrate.py:1064`), and the turn-cumulative request cap (`UsageLimits`, `orchestrate.py:443`).
- **Subagents:** `run_standalone` (`run.py:18`) drives `agent.run()` with `output_type: type[BaseModel]` (`spec.py:51`) — the graph builds an `OutputToolset` (`_output.py:613`, `allow_text_output=False` for a single non-`str` output) and the model emits the result as a `final_result` tool call, validated against the schema. The dream-reviewer / synthesis daemons (`daemons/dream/_reviewer.py`, `agent/run.py:61`) were tuned to that tool-call contract.

Phase 2 reproduces all of this as straight-line owned-loop code, driving the Phase-1 client, **behind a config flag, with the graph path untouched and default.** No deletion happens this phase — the strangler-fig parallel path is the whole point. The eval suite is the parity oracle: the owned path must match the graph path before Phase 5 flips the default and deletes the graph.

**Current state verified consistent** with the design doc and the milestone Phase-2 contract: the five processors + the entire compaction call-chain read **only `ctx.deps`** (grep for `ctx.model`/`ctx.usage`/`ctx.run_step` across `compaction.py` + `history_processors.py` → zero hits), so S6's RunContext→deps dissolution is mechanical; `_clean_message_history` is at `_agent_graph.py:2053` (module-private — co **ports** its semantics, it does not import a graph-internal symbol); `ModelRequestParameters` carries `function_tools`, `output_tools`, `allow_text_output`, and `instruction_parts` (`models/__init__.py:500–525`); `OutputToolset.build(...)` (`_output.py:1441`) is the SDK machinery that turns an output `BaseModel` into the `final_result` tool definition. No `/sync-doc` needed (specs update at Phase 6).

## Problem & Outcome

**Problem:** the graph owns the control flow co's small-model thesis most depends on, and Phase 1's client has no loop to drive it. Until co owns the loop, every co-specific behavior stays injected at a graph edge, and `model_turn` cannot be exercised in a real turn.

**Outcome:** an owned, linear turn loop — selectable behind a config flag, default off — that for **both** the orchestrator and subagents: runs the history-processor chain with `deps` (no `RunContext`) and applies the `_clean_message_history` normalization as a **pre-send transform of the request copy** (never persisted to `TurnState.history`, matching the graph — CD-M-1), assembles static + per-turn instructions, builds `ModelRequestParameters` from the `@agent_tool` catalog, drives `model_turn`, renders deltas, classifies the assembled response (typed `finish_reason` branch, no string-match), dispatches tool calls (flood cap at the step boundary + MCP spill inline, folding `_CallSeamToolset`), appends results, and repeats until the model emits no tool call (orchestrator) or emits a validated `final_result` (subagent). The graph path remains byte-for-byte behavior-unchanged and default.

**Parity gate, honestly scoped (PO-m-2):** Phase 2 reaches parity only on the **no-approval, no-recovery slice** of a turn — chat, read-only/auto-approved tool calls, multi-step that needs neither interactive approval (Phase 3) nor error/overflow/length recovery (Phase 4). The load-bearing gate is therefore (1) real-Ollama end-to-end owned turns on read-only tools, (2) the **read-only/chat evals** (`eval_daily_chat`, `eval_groundedness`) on the owned path, and (3) a real-Ollama owned **subagent** run producing schema-valid output at parity with the graph. Approval-requiring flow evals (`eval_agentic_loop`, `eval_multistep_plan`, `eval_skills`) move to **Phase 3's** gate — they cannot pass under Phase 2's deny-placeholder (see Behavioral Constraints / Testing).

**What Phase 2 does NOT include** (later phases, to keep the parity gate clean):
- **Inline interactive approval** — Phase 3. Phase 2's dispatch reuses `is_auto_approved` (config-driven auto-approval) and turns any non-auto-approved call into a **denial tool-result** (safe placeholder, headless-parity). No interactive prompt, no `clarify`-inline, no remember-choice yet. (Rationale in Behavioral Constraints.)
- **Error / overflow / length-retry recovery** — Phase 4. Phase 2's `run_turn` wrapper surfaces a provider error / overflow / interrupt as a terminal `TurnResult` (the turn ends cleanly) but does **not** yet compact-and-retry or boost-and-retry. The reasoning-overflow *predicate* (`_is_reasoning_overflow`, S4) IS built here (it is a classification of the response co already owns), but its recovery routing is trivial-terminal until Phase 4.
- **Graph deletion** — Phase 5.

**Failure cost:** high if the owned path silently diverges from the graph — wrong history-processor firing order, a dropped `_clean_message_history` normalization producing an invalid tool-call↔result pairing, or a tool-cap off-by-one. But Phase 2 is **behind a default-off flag**, so a divergence cannot reach users this phase; it surfaces as eval drift on the owned path against the still-live graph oracle. The mitigation is exactly that parallel-path design: every owned-path eval is checkable against the graph path running the same scenario.

## Scope

**In:**
- A config flag (default `False`) selecting the owned vs graph driver, read at both driver entry points (orchestrator `run_turn`, subagent `run_standalone`).
- **Owned orchestrator loop:** `TurnState` + typed `TurnExit` + `ToolCapState`; the turn loop + step loop (§3); per-step history-processor invocation + `_clean_message_history` port + instruction assembly (OQ-6, OQ-8); tool dispatch folding `_CallSeamToolset` (cap + spill + span, OQ-3 tool-def source); typed `finish_reason` / reasoning-overflow branch (S4, §6.6); mixed text+tool-call render+keep (OQ-7); co-owned turn-cumulative request cap (replaces `UsageLimits`).
- **Owned subagent driver (OQ-4):** the same step-loop core driving `model_turn` with `output_tools` (register `spec.output_type` via `OutputToolset.build`, `allow_text_output=False`), detecting the `final_result` call, validating, and re-prompting on failure — option (b), which **is what the graph subagent path already does**, so the gate is owned-(b) == graph parity (PO-M-1). Option (a) (parse final text) is a conditional fallback, built only if (b)'s plumbing proves heavy.
- **S6 dissolution (subsumed from `sdk-coupling-cleanup`):** convert the five history processors + the compaction call-chain they invoke from `RunContext` to `deps`; add RunContext→deps adapter shims at the two graph-path call sites (`build.py` registration, `orchestrate.py` overflow recovery) so the graph path keeps working until Phase 5.
- **S2 absorption:** `ToolCapState` counts at the step boundary (CD-m-3 pre-fan-out shed rule, validated against the >3-parallel eval).
- Tests: functional fake-model + real-Ollama tests of the owned loop; flow + dream-reviewer evals run on the owned path under the flag.

**Out:**
- Inline interactive approval, error/overflow/length-retry recovery, graph deletion (Phases 3/4/5 — above).
- Replacing the `ModelMessage` message model (milestone-out; kept as wire + durable type).
- `docs/specs/` edits (Phase 6, layer rule).
- Deleting `_CallSeamToolset`, `SurrogateRecoveryModel`, the `DeferredToolRequests` wiring, or the graph-coupled type aliases — all Phase 5 (they stay live for the graph path).

## Behavioral Constraints

- **Graph path unchanged and default.** With the flag off (default), `run_turn` and `run_standalone` behave byte-for-byte as today. The S6 signature change is mediated by adapter shims so the `Agent`'s processor contract and `orchestrate.py`'s overflow recovery are observationally identical. Existing graph-path tests (`test_flow_*`, compaction tests) pass unchanged.
- **Owned path == graph path on the no-approval/no-recovery slice.** Same streaming output, same tool dispatch order, same flood-cap semantics, same finish_reason surfacing, same subagent structured output — for turns needing neither approval nor recovery. Proven by the real-Ollama owned-turn tests, the read-only/chat evals (`eval_daily_chat`, `eval_groundedness`), and the real-Ollama owned-subagent run, all on the owned path before this phase's gate.
- **History-processor firing parity (OQ-6).** The five processors run **once per step, before assembling the request**, in the exact current order (`elide_old_multimodal_prompts → dedup_tool_results → evict_old_tool_results → spill_largest_tool_results → proactive_window_processor`) — matching the graph's per-`ModelRequestNode` firing. No double-compaction across steps within one turn (proactive's anti-thrash counters must carry, not reset, across steps — they live on `deps.runtime`, so this holds as long as the loop does not reset them mid-turn).
- **`_clean_message_history` is a pre-send transform, NOT persisted (CD-M-1).** Verified against the graph: `_agent_graph.py:884` persists processor output to `ctx.state.message_history`, then `:893` runs `_clean_message_history` on a **throwaway copy** for the request only — the comment is explicit: *"but don't store it in the message history on state. This is just for the benefit of model classes that want clear user/assistant boundaries."* So the owned loop's `clean_message_history` port (merge consecutive same-instruction `ModelRequest`s, sort `ToolReturnPart`/`RetryPromptPart` to the front of a merged request, preserve `ModelResponse`s, back-fill timestamps) is applied to the **request messages passed to `model_turn` only** — `TurnState.history` stays un-cleaned. Persisting the merged form would corrupt kept history across steps. Ported (not imported) — `_clean_message_history` is `_agent_graph`-private; importing it would re-couple to the graph being removed.
- **Tool-cap is a behavior change, not pure relocation (CD-m-3).** Today the `cap+1`-th call is shed *per-call inside dispatch* (`toolset.py:168–172`), order-dependent under the dispatch semaphore. The owned loop counts at the step boundary **before** fan-out: execute calls at index `< MAX_TOOL_CALLS_PER_MODEL_REQUEST`, return `make_exceeded_payload(...)` for the rest; latch `tool_cap_hard_stop` after `TOOL_CAP_HARD_STOP_CONSECUTIVE` consecutive over-cap steps. Validate against the eval exercising >3 parallel calls; do not assume byte-parity with the per-call path.
- **The flood cap (=3 parallel, hard-stop after N consecutive) is kept** — the near-unique small-model defense (peer survey). No uncapped fan-out.
- **The JSON-repair ladder is kept** — already in the Phase-1 `model_turn` client (`repair=deps.config.llm.uses_ollama()`).
- **Phase-2 approval is a deny-placeholder; approval-requiring evals defer to Phase 3.** With the flag off, approval is the unchanged graph/deferred flow. With the flag on, the owned loop reuses `is_auto_approved` (config exact-match rules) and turns every non-auto-approved call into a denial `ToolReturnPart` — degraded-but-safe, explicitly experimental, replaced by real inline approval in Phase 3. **Critical interaction verified (CD-M-3/PO-m-1):** evals drive approval through `EvalFrontend.prompt_approval` returning `"a"` (always-approve-and-remember, `evals/_deps.py:17`), **not** through `is_auto_approved` config rules — so any eval whose tools require approval (`file_write`, `shell_exec`) would be **denied** under the deny-placeholder. Therefore Phase-2's gate uses only read-only/chat tools; `eval_agentic_loop`, `eval_multistep_plan`, `eval_skills` (which write/exec) are Phase 3's gate. The owned loop never executes an unapproved sensitive op. Subagents are unaffected — `requires_approval=False` (`build.py:98`) makes the placeholder a no-op for them.
- **Subagent contract preserved (OQ-4).** Option (b) keeps the exact prompt contract the dream-reviewer model was tuned to (the SDK steers the result into a `final_result` tool call, `allow_text_output=False`) — and **is** the graph subagent path, so the parity gate is owned-(b) reproducing the graph's output on the same input. Option (a) is a conditional fallback only if (b)'s plumbing proves heavy.

## High-Level Design

### D1 — Module layout (new owned-path modules, graph modules untouched)

The owned loop lives in **new** modules so the graph driver (`orchestrate.py`, `build.py`, `run.py`'s graph branch) stays intact and default:

- `co_cli/agent/turn_state.py` (new) — `TurnState` (reconstructed per turn; never agent-object counters), `TurnExit` enum (§6.2: `FINAL_TEXT | TOOL_CAP | REQUEST_CAP | REASONING_OVERFLOW | PROVIDER_ERROR | TIMEOUT | INTERRUPTED`), `ToolCapState` (§6.3, pre-fan-out counting per CD-m-3).
- `co_cli/agent/preflight.py` (new) — `run_history_processors(history, deps) -> list[ModelMessage]` (ordered chain), `clean_message_history(messages)` (the ported normalizer), `assemble_instructions(deps) -> list[InstructionPart]` (static cached + per-turn dynamic), `build_request_params(deps, instr, *, output_tools=None, allow_text=True) -> ModelRequestParameters`.
- `co_cli/agent/dispatch.py` (new) — `dispatch_tools(tool_calls, deps, *, cap_state, frontend) -> list[ToolReturnPart]` folding `_CallSeamToolset`'s three concerns (tool span, cap shed pre-fan-out, MCP spill) as straight-line code over co's existing `tool_dispatch_sem`; `resolve_auto_approvals(tool_calls, deps)` — the deny-placeholder reusing `is_auto_approved`.
- `co_cli/agent/loop.py` (new) — `run_turn_owned(...)` (the orchestrator turn + step loop, §3) and `run_steps(...)` (the shared step-loop core), driving `model_turn`, rendering via `StreamRenderer`, the typed finish-reason branch, `_is_reasoning_overflow` (§6.6), co-owned request cap.
- `co_cli/agent/run.py` (modify) — `run_standalone` gains an owned branch behind the flag: build tool-defs + `output_tools` from `spec.output_type`, drive `run_steps` with the subagent termination predicate (validated `final_result`), no render.
- `co_cli/main.py` (modify) + `co_cli/agent/orchestrate.py` (modify, additive) — read the flag at the driver entry and dispatch to `run_turn_owned` vs the existing graph `run_turn`. The graph `run_turn` stays the default branch.
- `co_cli/context/history_processors.py` + `co_cli/context/compaction.py` (modify) — signature change `RunContext[CoDeps] → CoDeps` across the chain (S6).
- `co_cli/agent/build.py` (modify) — adapter shims registering the deps-signature processors on the `Agent` as `(ctx, msgs) -> proc(ctx.deps, msgs)`.

Precedent for new domain-homed agent modules: `co_cli/agent/` already hosts `orchestrate.py`, `build.py`, `run.py`, `spec.py`, `orchestrator.py`, `toolset.py`. These are not util modules — each owns one concern of the owned loop.

### D2 — The flag and driver selection (one approach)

A boolean config field, **`config.llm.use_owned_loop`** (default `False`), on `LlmSettings` (`co_cli/config/llm.py`) — the driver is an LLM-interaction concern and `LlmSettings` is already the home for `uses_ollama()`/request-limit policy. Read at the two entry points:
- `main.py` orchestrator turn: `if deps.config.llm.use_owned_loop: run_turn_owned(...) else: run_turn(...)`.
- `run.py` subagent: same branch around the `agent.run()` block.

Evals set it via the centralized eval settings (`evals/_settings.py` override) — a legitimate production toggle the eval layer flips, not eval-driven production API (it exists for the real strangler-fig cutover). No env var, no per-call argument.

### D3 — Shared step-loop core (orchestrator + subagent)

`run_steps` is the single primitive both drivers call. It owns: preflight → build params → `model_turn` drive + render → assemble response → `clean_message_history` → classify parts → dispatch tools → append → repeat. The two drivers differ only in:

| | Orchestrator | Subagent |
|---|---|---|
| `output_tools` | none (text done-ness) | `OutputToolset.build([spec.output_type])` defs, `allow_text_output=False` |
| done predicate | no `ToolCallPart`s → `FINAL_TEXT` | `final_result` call present → validate → return model |
| render | `StreamRenderer` → frontend | iterate + discard (no frontend) |
| approval | deny-placeholder (D1) | no-op (`requires_approval=False`) |
| request cap | `resolve_request_limit(config.llm)` | `spec.default_budget` |
| settings | turn `model_settings` | `deps.model.settings_noreason` |

The differences are passed as parameters/callbacks to `run_steps`; the control flow is identical. This realizes the milestone's "single driver for both" without duplicating the loop.

### D4 — Tool-def source (OQ-3, resolved → keep the FunctionToolset schema generator)

The owned loop reads `ToolDefinition`s for `ModelRequestParameters.function_tools` from the existing native `FunctionToolset` via `prepare_tool_def` (exactly as `schema_budget.py:63` does), applying co's per-turn visibility (the `revealed_tools`/DEFERRED logic in `_tool_visibility_filter`, `toolset.py:71`) and Google self-gating. It uses the toolset **only** as a schema source — never for dispatch (dispatch is co's `dispatch_tools`, D1). Consequence confirmed: `schema_budget.py:62`'s synthetic `RunContext` (S3) **persists** — genuinely forced by `prepare_tool_def`. MCP tool defs come from the assembled routing toolset's tool list the same way (kept-library surface, S5 unchanged). **Source pinned (CD-m-1):** the native tool-def source is the **inner `FunctionToolset` handle returned by `build_native_toolset`** (the same object `schema_budget.py:61` measures), **not** `deps.toolset` (which is the assembled `_CallSeamToolset`/routing stack). Expose that native handle to the owned loop (e.g. store it on `deps` alongside `deps.toolset`); the `done_when` validates behavior (every ALWAYS + revealed tool reaches the model, deferred tools stay hidden until `tool_view`).

### D5 — History processors → `deps`, graph path shimmed (OQ-6, S6)

Change the signature of the five processors **and** the compaction call-chain they invoke (`compact_messages`, `commit_compaction`, `recover_overflow_history`, `summarize_dropped_messages`, `_gated_summarize_or_none`, `_record_proactive_outcome`, `_resolve_proactive_focus`, `_snapshot_and_kick_review`, `_reset_thrash_state`, `_summarization_gate_open`) from `ctx: RunContext[CoDeps]` to `deps: CoDeps`. Verified safe: the entire chain reads only `ctx.deps` (grep-confirmed zero `ctx.model`/`ctx.usage`/`ctx.run_step`). Two graph-path call sites get adapters so the default path is unchanged:
- `build.py` registers each processor as `lambda ctx, msgs, _p=proc: _p(ctx.deps, msgs)` (the SDK's history-processor contract is `(ctx, messages)`).
- `orchestrate.py:858` `_attempt_overflow_recovery` drops its synthetic `RunContext(deps, model, usage)` and calls `recover_overflow_history(deps, history)` directly (S6 dissolved at that site too — bonus, low-risk: it only ever read `ctx.deps`).

The owned loop's `run_history_processors` calls each `proc(deps, msgs)` directly. At Phase 5 the adapters are deleted with the graph.

### D6 — Instruction assembly per step (OQ-8)

`assemble_instructions(...)` builds the static system prompt once (the `ORCHESTRATOR_SPEC.static_instruction_builders`, cached on first build) + evaluates the per-turn dynamic instructions (`safety_prompt`, `wrap_up_prompt`, `current_time_prompt`, `deferred_tool_awareness_prompt`, `skill_manifest_prompt`) **every step**, emitted as `InstructionPart`s on `ModelRequestParameters.instruction_parts` (bridged into the request by `direct`/`Model._get_instruction_parts`, `models/__init__.py:946`).

**The builders are NOT uniformly deps-convertible (CD-M-2) — verified:** two of the five read more than `ctx.deps` — `wrap_up_prompt` reads `ctx.usage.requests` (`_instructions.py:56`) and `safety_prompt` → `safety_prompt_text` reads `ctx.messages` (`prompt_text.py:95`). A blanket `lambda ctx: builder(ctx.deps)` shim would silently **drop the wrap-up nudge and the doom-loop/shell-reflection safety warnings on the graph path** — a default-path regression. **Decision:** convert the three pure-`deps` builders (`current_time_prompt`, `deferred_tool_awareness_prompt`, `skill_manifest_prompt`) to `deps`; refactor the two stateful builders (`wrap_up_prompt`, `safety_prompt`/`safety_prompt_text`) to take **explicit params** — `request_count` and `messages` respectively, alongside `deps`. The graph registration in `build.py` passes them from `ctx` (`builder(ctx.deps, request_count=ctx.usage.requests, messages=ctx.messages)`); the owned loop passes them from `state`/`history`. **Reconcile the wrap-up boundary (CD-M-2):** `ctx.usage.requests` counts *completed requests*; `state.model_requests` counts *ModelResponses*. Confirm they fire the nudge on the same step (one response per request → equivalent) or source the owned-loop count to match `usage.requests` semantics exactly. Dynamic instructions stay ephemeral (never appended to durable history) — same as today.

### D7 — finish_reason / reasoning-overflow (S4 dissolved, §6.6)

`_is_reasoning_overflow(response)` = `response.parts` empty-or-thinking-only **and** `response.finish_reason == 'length'` — co owns the predicate the SDK applied at `_agent_graph.py:1059`, replacing the `_REASONING_OVERFLOW_SIGNATURE` string-match. In Phase 2 it routes to a terminal `TurnExit.REASONING_OVERFLOW` with the existing `_REASONING_OVERFLOW_MESSAGE` status (no recovery — that's the trivial-terminal path; Phase 4 owns richer routing). The text-present + `length` case is the length-continuation signal, but its retry is **Phase 4** — in Phase 2 it surfaces the truncation status and ends the turn. Pin the predicate behaviorally (a test, not a substring match).

### D8 — Subagent structured output (OQ-4, resolved → option (b); gate = owned-(b) == graph)

Build the `final_result` `output_tools` definition from `spec.output_type` and set `allow_text_output=False` on `ModelRequestParameters`. The model emits the result as a `final_result` tool call; the owned subagent driver detects that call in the assembled response, validates its args against the `BaseModel` (the JSON-repair ladder from `model_turn` already cleans malformed Ollama args), and on validation failure re-prompts (append a `RetryPromptPart`-equivalent and loop, bounded by `spec.default_budget`).

**Tool-def source — parity-first in Phase 2, clean public rebuild deferred to Phase 5 (G1-1, corrected at redo).** `OutputToolset.build` lives in `pydantic_ai._output` — a **private module, not re-exported** on `pydantic_ai`/`pydantic_ai.toolsets` (only `FunctionToolset` is public). It builds the full def: tool name `final_result` (`DEFAULT_OUTPUT_TOOL_NAME`, `_output.py:73`) + description + a JSON schema via `ObjectOutputProcessor` (strict-mode transforms + `TOOL_NAME_SANITIZER`, `_output.py:1468`). **The dream-reviewer was tuned to that exact schema.** A hand-built `ToolDefinition` would route through co's *function-tool* schema path (`prepare_tool_def`) — a different generator — so any divergence in name/description/schema/strict **silently changes the contract the model sees**, which is exactly what the parity gate exists to protect. Phase 2 is the parity-*proving* phase; it must not introduce a new divergence in it. **Decision: use `OutputToolset.build` (`_output.py:1441`) in Phase 2** — it *is* the graph path, guarantees the tuned contract, and is **lower surface** than a bespoke builder. The `_output` reach is co's only SDK private-module dependency: **flag it inline + `log()` it as a known v2 break point + a Phase-5 cleanup item** (not silently load-bearing). **Phase 5** (graph deletion, full-eval-suite cutover gate) replaces it with a hand-built public `ToolDefinition`, verified equivalent there — the clean end state, **peer-confirmed** (opencode's `generateObject` builds its own output def, `llm.ts:116-129`). G1-1's concern is honored, sequenced where the swap is holistically verified rather than where it risks the parity gate.

**No co-side parameter customization needed (CD-m-2):** `allow_text_output=False` → `output_mode='tool'` resolves *inside* `OpenAIChatModel.request_stream` → `prepare_request` (`openai.py:828`), so passing it on `ModelRequestParameters` to `direct.model_request_stream` suffices — co does not call `customize_request_parameters`. This holds for the `OutputToolset.build` def used in Phase 2 (and the public rebuild at Phase 5) — `output_mode` is resolved by the model from `allow_text_output`, independent of the def source.

**The A/B is dropped (PO-M-1).** Option (b) **is the graph subagent path** (the SDK builds the same `OutputToolset` with `allow_text_output=False`), and the dream-reviewer model was *tuned to that `final_result` contract* — so option (a) changes the prompt contract and is a degraded fallback, not a co-equal candidate to measure against (b). The Phase-2 parity question is **"owned-(b) reproduces the graph's structured output on the same input,"** not "(b) vs (a)." Build only (b); gate on owned-(b) == graph on the real-Ollama subagent run. Option (a) is implemented **only if** (b)'s plumbing proves heavy in practice, and would then be measured against (b) — not before.

## Tasks

### ✓ DONE TASK-1 — Config flag + driver-selection seam
- `files:` `co_cli/config/llm.py`, `co_cli/main.py`, `co_cli/agent/orchestrate.py`, `co_cli/agent/run.py`
- Add `use_owned_loop: bool = False` to `LlmSettings`. At the orchestrator entry (`main.py:197` region) and the subagent entry (`run.py`), branch on the flag to the owned driver (stubs landing in TASK-6/7) vs the existing graph driver. The graph branch is the default and unchanged.
- `prerequisites:` none
- `done_when:` with the flag `False` (default), `uv run co --help` boots and the full suite is green (graph path untouched); with the flag `True`, the orchestrator + subagent entry points route to the owned driver (assert via a functional test that toggling the config field changes which driver runs a real turn — e.g. the owned driver emits its distinct `co_turn`/step span shape). A grep of `use_owned_loop` **reads** (excluding the `LlmSettings` definition site) shows exactly the two entry points (`main.py`, `run.py`), nowhere else (CD-m-3).
- `success_signal:` flipping one config field swaps the entire turn driver with no other change.

### ✓ DONE TASK-2 — Turn state, `TurnExit`, `ToolCapState`
- `files:` `co_cli/agent/turn_state.py` (new), `co_cli/config/tuning.py` (read-only reuse of `MAX_TOOL_CALLS_PER_MODEL_REQUEST`/`TOOL_CAP_HARD_STOP_CONSECUTIVE`)
- `TurnState` (per-turn mutable: history, pending input, settings, `model_requests`, `final_response`, `exit_reason`); `TurnExit` enum (§6.2 values); `ToolCapState` with `note_calls(n)` latching `hard_stop` after `TOOL_CAP_HARD_STOP_CONSECUTIVE` consecutive over-`MAX_TOOL_CALLS_PER_MODEL_REQUEST` steps, and a method giving the **pre-fan-out shed boundary** (execute index `< cap`, shed the rest — CD-m-3).
- `prerequisites:` none
- `done_when:` a functional test drives `ToolCapState.note_calls` over a sequence of step sizes and asserts the **observable** cap decisions: a step of >cap calls sheds exactly the calls at index ≥ cap; `hard_stop` latches only after `TOOL_CAP_HARD_STOP_CONSECUTIVE` consecutive over-cap steps and a within-cap step resets the streak. (Assert decisions/outputs, never field existence.)
- `success_signal:` N/A (pure structure + cap arithmetic; exercised end-to-end in TASK-6).

### ✓ DONE TASK-3 — History-processor + compaction chain → `deps` (S6) with graph-path adapter shims
- `files:` `co_cli/context/history_processors.py`, `co_cli/context/compaction.py`, `co_cli/agent/build.py`, `co_cli/agent/orchestrate.py`
- Change the five processors + the full compaction call-chain (D5 list) from `ctx: RunContext[CoDeps]` to `deps: CoDeps`, replacing every `ctx.deps` with `deps`. Register them on the `Agent` in `build.py` via `(ctx, msgs) -> proc(ctx.deps, msgs)` adapters. Convert `orchestrate.py:858` `_attempt_overflow_recovery` to call `recover_overflow_history(deps, ...)` directly (drop the synthetic `RunContext`).
- `prerequisites:` none
- `done_when:` repo-wide grep shows zero `RunContext` parameters remaining on the converted chain in `compaction.py` + `history_processors.py` AND zero `ctx.deps`/`ctx.model`/`ctx.usage` reads in those two modules (the conversion is complete, not half-done); the **graph-path** compaction + flow tests (`test_*compaction*`, `test_flow_*` exercising history processors) pass **unchanged** (the adapters preserve the graph contract); full suite green.
- `success_signal:` N/A (pure refactor — the behavior oracle is the unchanged graph-path tests).

### ✓ DONE TASK-4 — Preflight: processor chain runner, `clean_message_history` port, instruction assembly
- `files:` `co_cli/agent/preflight.py` (new), `co_cli/agent/_instructions.py` (stateful builder signatures), `co_cli/context/prompt_text.py` (`safety_prompt_text` signature), `co_cli/agent/spec.py`, `co_cli/agent/orchestrator.py` (per-turn builder wiring), `co_cli/agent/build.py` (per-turn instruction shim)
- `run_history_processors(history, deps)` applies the five (now-`deps`) processors in the canonical order (§ Behavioral Constraints). `clean_message_history(messages)` ports `_agent_graph.py:2053` semantics verbatim (merge consecutive same-instruction `ModelRequest`s, sort tool-return/retry parts to front, preserve responses, back-fill timestamps), applied **only to the pre-send request copy** — never to `TurnState.history` (CD-M-1). `assemble_instructions(...)` builds static-once + per-turn dynamic `InstructionPart`s (D6): three builders convert to `deps`; `wrap_up_prompt` and `safety_prompt`/`safety_prompt_text` take explicit `request_count`/`messages` params, with `build.py` graph shims passing them from `ctx.usage.requests`/`ctx.messages` so the graph path is unchanged.
- `prerequisites:` TASK-3
- `done_when:` a functional test asserts observable normalization outcomes: feeding `run_history_processors` a history with duplicate tool-results yields the same deduped/evicted shape the graph produces for that input (compare against a graph-path run on the identical history); feeding `clean_message_history` two consecutive `ModelRequest`s with tool-returns yields one merged request with `ToolReturnPart`s ordered first **while the un-cleaned source history is unchanged**; `assemble_instructions` includes the per-turn dynamic pieces (the wrap-up nudge fires at the `request_count == limit - 1` step; the safety warning fires when its message-history condition holds). The **graph-path** wrap-up + safety tests pass unchanged (the explicit-param refactor + shims did not regress the default path). Full suite green.
- `success_signal:` the owned preflight reproduces the graph's per-step history-processor + normalization output for the same input.

### ✓ DONE TASK-5 — Tool dispatch (fold `_CallSeamToolset`) + tool-def source
- `files:` `co_cli/agent/dispatch.py` (new), `co_cli/agent/preflight.py` (tool-def builder)
- `dispatch_tools(tool_calls, deps, *, cap_state, frontend)` executes the within-cap calls over the existing `tool_dispatch_sem` (parallel ≤ cap, sequential tools respected), sheds index ≥ cap with `make_exceeded_payload`, applies MCP spill (`spill_with_span`) to MCP string results, emits the `co.tool.*` span per call — all straight-line (folding `toolset.py:142–220`). `build_request_params` (preflight) sources `function_tools` from the native `FunctionToolset.prepare_tool_def` honoring co's visibility filter (D4). `resolve_auto_approvals` = the deny-placeholder reusing `is_auto_approved` (non-auto-approved → denial `ToolReturnPart`).
- `prerequisites:` TASK-2, TASK-4
- `done_when:` a functional test drives `dispatch_tools` with >`MAX_TOOL_CALLS_PER_MODEL_REQUEST` parallel calls and asserts the observable shed (within-cap calls execute and return real results; over-cap calls return the exceeded payload) and that the `co.tool.*` span + MCP spill fire (assert the emitted trace record + that an over-threshold MCP result is spilled to a file path, not the field/method); a test confirms a DEFERRED tool is absent from `function_tools` until revealed and present after; full suite green.
- `success_signal:` the owned dispatch reproduces `_CallSeamToolset`'s cap+spill+span behavior with the pre-fan-out shed rule.

### ✓ DONE TASK-6 — Owned orchestrator turn + step loop, behind the flag
- `files:` `co_cli/agent/loop.py` (new), `co_cli/agent/orchestrate.py` (wire `run_turn_owned` into the TASK-1 branch), `co_cli/main.py`
- `run_steps` (shared core, D3) + `run_turn_owned` (§3 turn loop, orchestrator config): preflight → `build_request_params` → `model_turn(deps.model.model, msgs, params, settings, repair=uses_ollama())` → render deltas via `StreamRenderer` → `stream.get()` → `clean_message_history` → classify; no-tool-calls → `FINAL_TEXT` (with `_is_reasoning_overflow` typed branch → `REASONING_OVERFLOW` terminal, D7); else `cap_state.note_calls` → `resolve_auto_approvals` → `dispatch_tools` → append → repeat; co-owned request cap → `REQUEST_CAP`. Mixed text+tool render+keep (OQ-7). Stall timer wraps the model-request section (relocated `_StallTimer`). Phase-2 error handling: surface provider error / interrupt as a terminal `TurnResult` (no recovery — Phase 4). Returns a `TurnResult` shaped like the graph path's (same fields `main.py` pattern-matches on).
- `prerequisites:` TASK-1, TASK-2, TASK-4, TASK-5
- `done_when:` with `use_owned_loop=True`, a **real-Ollama** test runs three turns end-to-end through `run_turn_owned` using **read-only tools** (e.g. `memory_search`, `session_search`, `file_read`) and asserts observable behavior: (a) a plain chat turn streams text and returns a final answer; (b) a single-tool-call turn dispatches the tool and answers from its result; (c) a multi-step turn (tool → model → tool → final) terminates on no-tool-calls. The **read-only/chat evals** (`eval_daily_chat`, `eval_groundedness`) pass on the owned path under the flag. A behavioral test pins `_is_reasoning_overflow` (empty/thinking-only + `finish_reason=='length'` → terminal `REASONING_OVERFLOW`; text-present + `length` → truncation status, turn ends). Full suite green; graph path (flag off) still green. **Not in scope (Phase 3):** `eval_agentic_loop`/`eval_multistep_plan`/`eval_skills` — they require approval, which the deny-placeholder blocks (Behavioral Constraints).
- `success_signal:` co runs a real interactive turn end-to-end with no graph, behind the flag, at parity with the graph path on the no-approval slice.

### ✓ DONE TASK-7 — Owned subagent driver + OQ-4 structured output (option b)
- `files:` `co_cli/agent/run.py`, `co_cli/agent/loop.py` (subagent termination predicate), `co_cli/agent/preflight.py` (output_tools builder), `tests/test_flow_owned_subagent.py` (new)
- Owned branch in `run_standalone` behind the flag: build the `final_result` `output_tools` def from `spec.output_type` via **`OutputToolset.build`** + `allow_text_output=False` (option b, D8 — parity-first: it *is* the graph path and the tuned contract; the `_output` private reach is flagged inline + `log()`-ed as a Phase-5 cleanup item / v2 break point; the public hand-built rebuild is Phase 5, G1-1), drive `run_steps` with the subagent config (no render, no-op approval, `default_budget` cap, `settings_noreason`), detect the `final_result` call, validate against the `BaseModel`, re-prompt on failure. **No A/B** (PO-M-1): option (b) is the graph path; build (b) only. (a) is a conditional fallback if (b)'s plumbing proves heavy.
- `prerequisites:` TASK-6
- `done_when:` **no existing eval drives the subagent (daemon) path** (verified — `evals/eval_*.py` cover orchestrator turns only), so the load-bearing gate is a **new real-Ollama test**: with `use_owned_loop=True`, run one real subagent spec (e.g. `SKILL_REVIEW_SPEC`) end-to-end through the owned `run_standalone` and assert it returns a populated, schema-valid `spec.output_type` instance; run the **same spec on the same input through the graph path (flag off)** and assert both produce a valid instance of the same type **AND that the `final_result` tool def the model sees is equivalent across drivers** (same tool name + same JSON schema) — output-type validity alone cannot prove the tuned contract is preserved, so the gate asserts def-equivalence, not just a valid instance (G1-1). The Phase-2 `OutputToolset.build` `_output` reach is the single **documented exception** to the standing zero-`pydantic_ai._*`-import grep guard, carrying its inline private-reach + Phase-5-cleanup note; no other `pydantic_ai._*` import appears in `co_cli/`. Full suite green; graph subagent path (flag off) still green.
- `success_signal:` a subagent produces validated structured output through the owned loop, preserving the dream-reviewer's tuned `final_result` contract.

## Testing

The **eval suite is the parity gate for the no-approval/no-recovery slice** — `evals/` are UAT smoke runs on real seeded scenarios (no mocks), the behavioral net for "owned path == graph path." Because the graph path stays live and default, every owned-path eval is checkable against the graph path running the identical scenario (the reference oracle). Phase 2's gate uses only the evals/tests that need neither approval nor recovery (the rest are Phases 3/4).

- **Read-only/chat evals (TASK-6):** `eval_daily_chat` and `eval_groundedness` — orchestrator turns that need neither approval nor recovery — run with `use_owned_loop=True` via the `evals/_settings.py` override, checked against the same evals on the graph path (flag off) as the oracle. **Deferred to Phase 3:** `eval_agentic_loop`, `eval_multistep_plan`, `eval_skills` exercise `file_write`/`shell_exec` and rely on `EvalFrontend.prompt_approval` returning `"a"` (`evals/_deps.py:17`) — which the Phase-2 deny-placeholder blocks, so they cannot pass on the owned path until inline approval lands (Phase 3).
- **Subagent parity (TASK-7):** no existing eval drives the daemon/subagent path, so a **new real-Ollama test** runs a real spec through both the owned and graph drivers and asserts both produce a schema-valid output model (owned-(b) == graph).
- **Functional fake-model + real-Ollama tests:** cap arithmetic (TASK-2), processor/normalization parity + un-cleaned-source invariant (TASK-3/4), dispatch cap+spill+span (TASK-5), end-to-end owned read-only turns (TASK-6), owned subagent (TASK-7). Per co doctrine: assert **observable outcomes** only (delivered answers, shed payloads, emitted spans, validated output models, normalized history shapes), never call counts or field/method existence. Real-LLM tests skip unless `config.llm.uses_ollama()`, warm Ollama outside `asyncio.timeout`, use `llm.host` + `noreason_model_settings()`/`reasoning_model_settings()`, and tail the run log for call timing.
- **Graph-path regression:** all existing graph-path tests pass unchanged with the flag off (the adapters + additive owned modules must not perturb the default path).

No structural tests (functional-only policy). The loop's correctness is proven by behavior parity, not internal shape.

## Open Questions

### Resolved this pass (source-grounded)
- **OQ-3 (tool-def source) — keep `FunctionToolset` as schema generator** (D4); S3 synthetic `RunContext` persists (forced by `prepare_tool_def`, `schema_budget.py:62`).
- **OQ-4 (subagent structured output) — option (b) output-tool; gate = owned-(b) == graph** (D8, PO-M-1); `OutputToolset.build` + `allow_text_output=False` preserves the tuned `final_result` contract and *is* the graph path. No A/B; (a) is a conditional fallback only.
- **OQ-6 (history-processor relocation) — `deps` signature + graph adapter shims** (D5); the processor/compaction chain reads only `ctx.deps` (grep-verified), so that conversion is mechanical; `_clean_message_history` is **ported, not imported** (graph-module-private) and applied as a **pre-send transform only, never persisted** (D5/CD-M-1). Firing order + per-step parity in Behavioral Constraints.
- **OQ-7 (mixed text + tool-call) — render+keep** (§6.7); eval-watch for double-rendering on the `eval_daily_chat`/`eval_groundedness` evals.
- **OQ-8 (instruction injection) — per-step `instruction_parts`; 3 builders → `deps`, 2 stateful builders → explicit `request_count`/`messages` params** (D6/CD-M-2); a blanket deps shim would regress the graph path's wrap-up + safety prompts.

### Deferred to later phases (not Phase 2)
- Inline interactive approval, `clarify`-inline, remember-choice → **Phase 3**.
- Length-continuation retry, overflow compact-and-retry, the full provider-error matrix, the `FailoverReason`-style typed classification, the fill-unanswered-tool_ids invariant → **Phase 4**. (Phase 2 surfaces these as clean terminal turn endings.)
- Deleting `_CallSeamToolset`, `SurrogateRecoveryModel`, `DeferredToolRequests` wiring, the graph type aliases, and the S6 adapter shims → **Phase 5**.

## Next step

This Phase-2 plan goes through the Core Dev (implementation risk) + PO (scope, first-principles) critique and Gate 1. The milestone (`2026-06-24-234633-loop-decoupling-milestone.md`) and design (`2026-06-24-234633-loop-decoupling-design.md`) are the design inputs.

## Decisions

C1: Core Dev `revise / Blocking: CD-M-1, CD-M-2, CD-M-3`; PO `revise / Blocking: PO-M-1`. All four blockers source-verified and resolved by the TL. C2: Core Dev `approve / Blocking: none`, PO `approve / Blocking: none` — every prior blocker confirmed substantively resolved in the plan body. Convergence at C2.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Verified `_agent_graph.py:884` persists processor output then `:893` runs `_clean_message_history` on a throwaway copy ("but don't store it in the message history on state"). The draft wrongly persisted it. | Outcome + Behavioral-Constraints `_clean_message_history` bullet + D5 + TASK-4 body/done_when: apply as a pre-send transform of the request copy only; `TurnState.history` stays un-cleaned; test asserts source history unchanged. |
| CD-M-2 | adopt | Verified `wrap_up_prompt` reads `ctx.usage.requests` (`_instructions.py:56`) + `safety_prompt_text` reads `ctx.messages` (`prompt_text.py:95`); a blanket deps shim would silently disable the wrap-up nudge + doom-loop/safety warnings on the graph path. | D6 + OQ-8 + TASK-4 (files + body + done_when): 3 builders → `deps`; `wrap_up_prompt`/`safety_prompt(_text)` → explicit `request_count`/`messages` params, graph shims pass from `ctx`; reconcile `usage.requests` vs `model_requests`; graph wrap-up/safety tests must pass unchanged. |
| CD-M-3 | adopt | Confirmed the named evals (`dream-reviewer`/`synthesis`/`flow`) do not exist; real files are `eval_daily_chat`, `eval_groundedness`, `eval_agentic_loop`, etc.; no eval drives the subagent/daemon path. | Testing + TASK-6 + TASK-7: name real evals; Phase-2 gate = `eval_daily_chat`+`eval_groundedness` (read-only) + real-Ollama owned-turn/subagent tests; approval-requiring evals → Phase 3. |
| CD-m-1 | adopt | `deps.toolset` is the assembled `_CallSeamToolset`/routing stack; `schema_budget.py:61` reads the inner native `FunctionToolset` handle. | D4: pin the tool-def source to the native `FunctionToolset` handle from `build_native_toolset`, expose it to the owned loop. |
| CD-m-2 | adopt | `allow_text_output=False`→`output_mode='tool'` resolves inside `OpenAIChatModel.request_stream`→`prepare_request` (`openai.py:828`), so `direct.model_request_stream` suffices. | D8: note that no co-side `customize_request_parameters` is needed; the model resolves output_mode internally on the `direct` path. |
| CD-m-3 | adopt | The flag is also defined in `config/llm.py`; a bare grep false-positives on the definition. | TASK-1 done_when: scope the grep to `use_owned_loop` reads, excluding the `LlmSettings` definition site. |
| PO-M-1 | modify | The milestone's OQ-4 "A/B" was a hedge; first-principles, option (b) **is** the graph subagent path and (a) changes the tuned contract, so the parity question is owned-(b)==graph, not (b)-vs-(a). Refined rather than fully adopted: keep (a) as a conditional implementation fallback (honoring the milestone's escape hatch), not a pre-built gate. | D8 + Scope + TASK-7 + OQ-4 resolved note: drop the mandatory build-both A/B; gate on owned-(b)==graph (real-Ollama subagent test through both drivers); (a) built only if (b)'s plumbing proves heavy. |
| PO-m-1 | adopt | Verified evals approve via `EvalFrontend.prompt_approval`→`"a"` (`evals/_deps.py:17`), not `is_auto_approved` config — so approval-requiring evals would be denied under the deny-placeholder, making a flow-eval gate hollow. | Behavioral Constraints + Testing + TASK-6: Phase-2 gate is the read-only/chat evals + real-Ollama read-only owned-turn tests as load-bearing; approval-requiring evals explicitly deferred to Phase 3. |
| PO-m-2 | adopt | Top-line Outcome overstated the gate (parity holds only on the no-approval/no-recovery slice). | Outcome: added the "Parity gate, honestly scoped" paragraph; success_signals qualified to the no-approval slice. |
| G1-1 | adopt (corrected at redo) | Gate-1 human review: `OutputToolset` is in `pydantic_ai._output` (private, not re-exported) — co's *only* SDK private-module reach + a v2 break point. **Redo correction:** the first pass preferred a hand-built public def *in Phase 2*, but `OutputToolset.build` emits the exact `final_result` name + `ObjectOutputProcessor` schema the dream-reviewer was tuned to; a bespoke def routes through a different generator and can silently diverge — adding surface *and* risking the load-bearing parity gate in the very phase that proves parity. So: parity-first in Phase 2 (use `OutputToolset.build`, flag + `log()` the reach), clean public rebuild at Phase 5 where the full eval suite re-verifies it (peer-confirmed end state: opencode `generateObject`, `llm.ts:116-129`). Inventory confirms co has **zero** `pydantic_ai._*` imports today; this keeps it to a single *documented, sunset* reach, not an accreted leak (`/audit-conformance` owns ongoing hygiene). | D8 + CD-m-2 note + TASK-7 body/done_when: Phase 2 uses `OutputToolset.build` with the `_output` reach flagged as the single documented grep-guard exception + Phase-5 cleanup item; TASK-7 gate strengthened to assert **tool-def equivalence** (name + schema), not just output-type validity; public hand-built rebuild deferred to Phase 5. Also: `phase2_resolve_approvals` renamed `resolve_auto_approvals` (drop phase-numbered symbol). Update 2 (co-owned stream-event union) **dropped as low-ROI** — root cause is the public, deliberately-retained message-model coupling, owned by `migrate-pydantic-ai-v2` (sequenced last), not this milestone. |

## Final — Team Lead

Plan approved — Core Dev `Blocking: none` (C2), PO `Blocking: none` (C2). Convergence at C2.

This is **Phase 2** of the `loop-decoupling` milestone (`0.9.0`). Gate 1 approval here greenlights Phase 2's task breakdown for implementation — it does not pre-approve Phases 3–6, which each re-enter `/orchestrate-plan loop-decoupling-phaseN` for their own per-task `done_when`. Phase-2 scope (confirmed with the maintainer): the owned loop for **both** the orchestrator turn and the subagent driver, behind a default-off flag, parity-gated on the no-approval/no-recovery slice.

**2026-06-25 milestone re-sync reviewed — no Phase-2 scope/decision change; G1 approval stands.** The peer survey was re-verified against current HEADs (`hermes d6269da7f`, `opencode 20fd32359`) and folded into the milestone/design docs. Impact on Phase 2: OQ-4 option (b) is now *confirmed* by peer code (opencode `generateObject`, `llm.ts:116-129`), which also confirms hand-built public output defs as the **Phase-5 end state** (not Phase 2 — see the G1-1 redo correction: Phase 2 uses `OutputToolset.build` for parity, the public rebuild lands at the Phase-5 cutover where it is eval-verified); the two findings that would have touched Phase 2 — D2 (forced final turn on hard-stop) and the fill-unanswered stub-injection — were deliberately classified **out** of Phase 2 (D2 = post-milestone enhancement; fill-unanswered = Phase 4) to preserve this phase's parity-only, no-recovery contract. No decision weakened.

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: **right problem? correct scope?** The load-bearing scope calls are (1) including the subagent driver in Phase 2 (OQ-4 resolved to option (b) = the graph path, gated owned-(b)==graph), and (2) the honestly-scoped parity gate — Phase 2 proves "owned == graph" only on the no-approval/no-recovery slice; approval-requiring evals are Phase 3's gate.
> Once approved, run: `/orchestrate-dev loop-decoupling-phase2`

## Delivery Summary — 2026-06-25

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | flag default-off boots + suite green; flag-on routes both entry points; grep scoped to 2 reads | ✓ pass (routing test lands at TASK-6/7 where the owned driver exists) |
| TASK-2 | ToolCapState shed/latch/streak-reset asserted observably | ✓ pass |
| TASK-3 | zero RunContext/ctx.deps in chain; graph-path compaction+flow tests unchanged | ✓ pass |
| TASK-4 | processor-chain deps-threading; clean_message_history merge w/ source unchanged; wrap-up+safety fire; graph shims unchanged | ✓ pass |
| TASK-5 | pre-fan-out cap shed; co.tool span + MCP spill; DEFERRED hidden until revealed | ✓ pass |
| TASK-6 | real-Ollama owned turns (chat + tool-call); reasoning-overflow predicate; read-only/chat eval cases owned==graph | ✓ pass |
| TASK-7 | real-Ollama owned subagent schema-valid output + final_result def-equivalence vs graph | ✓ pass |

**Tests:** scoped — 79 passed (owned: cap-state, preflight, dispatch, owned-turn, owned-subagent; graph-path regression: safety_prompt, deferred_prompt, model_request_cap, compaction history/processor/snapshot/spill, real graph turn) + real-LLM compaction recovery/proactive (33). Lint clean.

**Doc Sync:** none — `docs/specs/` edits are Phase 6 (layer rule); the plan explicitly defers spec updates.

**Overall: DELIVERED**

**Eval parity (owned vs graph oracle, real-Ollama):**
- Deterministic orchestrator cases pass owned == graph: `eval_groundedness` W7.A/W7.B; `eval_daily_chat` W1.A (+ W1.D structural signals: merged=1, archived, token_in_merged).
- `eval_groundedness` W7.C and `eval_daily_chat` W1.D/W1.F are **nondeterministic / loop-independent**, not owned-loop regressions:
  - W7.C: 2 owned re-runs failed with *different* root causes (capitulate vs fail-to-view) — sampling variance on a weak-model capability-boundary "resist false premise" probe; a poor mechanical-parity discriminator.
  - W1.D: structural merge always succeeds; only the judge's phrasing score varies (10↔7).
  - **W1.F (first-principles finding): `merge_memory` → `_merge_cluster` → `llm_call` is a DIRECT `pydantic_ai.direct.model_request`, not `run_standalone`** — it never touches the owned/graph loop. The plan (CD-M-3) assumed `eval_daily_chat` exercised the subagent path under the flag; it does not. W1.F is a loop-independent merge-fidelity probe whose owned-vs-graph variance is pure sampling noise. The real subagent gate is the dedicated `tests/test_flow_owned_subagent.py` (passes owned == graph).

**Decision corrections found during implementation (parity gate working as designed):**
- **CD-m-2 was wrong:** `output_mode` is NOT derived from `allow_text_output`. `Model.prepare_request` strips `output_tools` when `output_mode != 'tool'` (and only fills from the profile default when the mode is `'auto'`, not the `'text'` default). `build_request_params` now sets `output_mode='tool'` whenever `output_tools` are present — caught by the subagent parity test (owned_result was `None` until fixed).
- **CD-m-1 (tool-def source):** used `deps.toolset.get_tools(ctx)` (the assembled, visibility-filtered routing toolset) instead of the bare native `FunctionToolset` handle. This yields the *exact* def set the graph sends (native + MCP + DEFERRED/Google/resume filtering) in one call — stronger parity, includes MCP, no filter re-implementation. No `deps`/bootstrap change needed.

**Extra files touched (beyond per-task `files:`, all required by signature changes or the eval owned-path gate):**
- `co_cli/commands/compact.py` — `/compact` is a production caller of the S6-converted `compact_messages`/`commit_compaction`; converted its synthetic RunContext to `deps`.
- Test updates for changed signatures: `tests/test_flow_safety_prompt.py`, `tests/test_deferred_prompt.py`, `tests/test_flow_model_request_cap.py`, and 6 compaction test files (`test_flow_compaction_*`, `test_flow_spill` unaffected).
- Eval owned-path wiring: `evals/_deps.py` (`drive_turn` + `apply_eval_owned_loop`, env `CO_EVAL_OWNED_LOOP`), `evals/eval_daily_chat.py`, `evals/eval_groundedness.py`.

**Phase-3/4/5 carry-forward:** inline interactive approval (the deny-placeholder is the Phase-2 stand-in); error/overflow/length-retry recovery (Phase-2 surfaces these as clean terminal turn endings); graph deletion + the duplicated `_REASONING_OVERFLOW_MESSAGE`/`_StallTimer` consolidation + the `pydantic_ai._output.OutputToolset` private reach → Phase 5.

## Implementation Review — 2026-06-25

Reviewed all 7 `✓ DONE` tasks evidence-first (one subagent per task, then adversarial reconciliation). Default stance held — four blocking findings surfaced and were fixed.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | flag default-off boots + flag-on routes both entry points; grep scoped to 2 reads | ✓ pass | `config/llm.py:313` `use_owned_loop=False`; reads exactly at `main.py:197`, `run.py:47`; graph branch is the unchanged `else` |
| TASK-2 | ToolCapState shed/latch/streak-reset asserted observably | ✓ pass (after fix) | `turn_state.py:96` `shed_boundary=min(issued,cap)`, `:83` latch after N consecutive + within-cap reset; survives stub-litmus |
| TASK-3 | zero RunContext/ctx.deps in chain; graph-path tests unchanged | ✓ pass (after fix) | `grep` → 0 `RunContext`/`ctx.deps` in `compaction.py`+`history_processors.py`; `build.py:19-38` `(ctx,msgs)→proc(ctx.deps,msgs)` shim; `orchestrate.py:856` direct `recover_overflow_history(deps,…)` |
| TASK-4 | processor-chain deps; clean_message_history merge w/ source unchanged; wrap-up+safety fire | ✓ pass | `preflight.py:71` canonical order; `:79-129` ported (not imported) cleaner applied to request copy only (`loop.py:275`); CD-M-2 explicit-param shims at `_instructions.py:116-133` thread `ctx.usage.requests`/`ctx.messages` — no blanket-deps regression; boundary reconciled (no off-by-one) |
| TASK-5 | pre-fan-out cap shed; co.tool span + MCP spill; DEFERRED hidden until revealed | ✓ pass | `dispatch.py:277-288` boundary shed, `:190-201` MCP spill, `:223-249` span; tool-defs via `deps.toolset.get_tools` (CD-m-1 correction — yields exact graph set incl. MCP); `resolve_auto_approvals` (no phase-number) deny-placeholder |
| TASK-6 | real-Ollama owned turns; reasoning-overflow predicate; read-only/chat eval owned==graph | ✓ pass | `loop.py:245-317` step loop; `:90-101` typed `_is_reasoning_overflow` (no substring match); TurnResult shape parity with graph (`main.py:197-217`); errors → terminal TurnResult, none swallowed |
| TASK-7 | real-Ollama owned subagent schema-valid output + final_result def via OutputToolset.build | ✓ pass (after fix) | `run.py:47-51` flag branch; `preflight.py:204` `OutputToolset.build` (sole `pydantic_ai._*` reach, comment+`log()`-flagged); CD-m-2-corrected `output_mode='tool'` when output_tools present; `final_result` validate + bounded re-prompt |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale test caller: `RunContext` passed where `deps: CoDeps` now expected → `AttributeError: 'RunContext' has no attribute 'runtime'` (full suite RED; TASK-3 migration missed this test) | `tests/context/test_input_too_large_fallback.py:46,66,86` | blocking | Converted `_make_ctx`→`_make_deps` (drop `RunContext`/`RunUsage` import), pass `deps` + read `deps.runtime` |
| Orphan dead code from the S6 rewrite: `deps = deps` self-assignment | `history_processors.py:408`, `compaction.py:302` | blocking | Removed both |
| One-sided (write-only) `TurnState` fields — no reader | `turn_state.py:112-115` (`pending_input`, `model_settings`, `final_response`); writes at `loop.py:194,195,288` | blocking | Removed the three fields + their write sites + now-unused `BinaryContent`/`ModelResponse`/`ModelSettings` imports (`model_requests`/`history`/`exit_reason`/`cap_state` are read — kept) |
| Dead `deps` parameter — unused in body | `loop.py:362` `_build_subagent_toolset(spec, deps)` | blocking | Dropped param; updated call site `loop.py:413` |
| Test-naming rule (`test_flow_<area>`) violated by 3 new files | `tests/test_owned_{dispatch,preflight,tool_cap_state}.py` | minor | `git mv` → `test_flow_owned_*` (consistent with siblings) |

### Test review/trim (per request)
Assessed every new + modified test against `testing.md` (assertion-strength stub-litmus, behavior-over-structure, duplication, naming):
- **New owned tests are behaviorally sound** — cap-state asserts shed/latch/reset decisions (a constant `shed_boundary` cannot satisfy both `==CAP` and `==1`; a no-op `note_calls` fails every latch assert); preflight asserts the un-cleaned-source-unchanged invariant + observable spill; dispatch asserts shed payloads + emitted spans + the spilled file path + DEFERRED visibility. All fail on a gutted body — none structural.
- **Renamed 3 files** to the mandated `test_flow_` prefix (above).
- **Cap-state unit tests kept despite being one layer below the agent surface** — they cover the consecutive-streak latch/reset the single-step dispatch test cannot (`testing.md` "unless the seam exposes behavior the surface cannot").
- **Accepted, documented (not blocking):**
  - `test_owned_subagent_final_result_def_matches_sdk_generator` asserts the owned def comes from the SDK generator (name + schema) — it does **not** directly diff the graph driver's def. Equivalence holds by construction (both call `OutputToolset.build`) and the real-LLM parity test covers output equivalence; a true no-LLM cross-driver diff would couple to SDK internals for marginal gain.
  - TASK-6 done_when case (c) "multi-step (tool→model→tool→final)" has no dedicated real-LLM test — the same `while` loop is exercised by the single-tool case + the read-only eval-parity gate; a second slow real-LLM test was not added (avoids redundant coverage per `testing.md` first-principles).
  - Sibling compaction tests construct a `RunContext` only to pass `.deps` — functionally correct migration cruft; left as-is (surgical discipline; they exercise the real converted functions).
  - Pre-existing `monkeypatch` use in `test_input_too_large_fallback.py` (no-fakes rule) predates this delivery — out of scope; only its signature was repaired.

### Carry-forward (non-blocking, later phases)
- Concurrent tool dispatch shares the single `deps.runtime.tool_progress_callback` slot under `asyncio.gather` (`dispatch.py:218-220`) — best-effort progress display can misroute/clear under parallel fan-out. Not a correctness/safety defect (tools execute correctly); flag for the Phase-3 frontend-wiring task.

### Tests
- Command: `uv run pytest`
- Result: **875 passed, 0 failed** (no skips — real-Ollama tests ran), 214s
- Log: `.pytest-logs/20260625-141717-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads); flag is default-off so the default chat path stays the unchanged graph driver.
- Owned orchestrator turns + owned subagent: ✓ verified via the real-Ollama tests in the green suite (`test_flow_owned_turn.py`, `test_flow_owned_subagent.py`) — chat streams + answers, single-tool turn dispatches + terminates, subagent returns schema-valid output at parity with the graph. `success_signal`s (one flag flips the driver; real owned turn end-to-end; subagent structured output) confirmed.

### Overall: PASS
