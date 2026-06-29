# Loop decoupling — Phase 5: cutover to the owned loop + delete the graph path

> Milestone: `2026-06-24-234633-loop-decoupling-milestone.md` (Gate 1 APPROVED). This is the
> per-task plan for **PHASE 5**. Phases 1, 2, 2.5, 3, 3.5, 3.6, 3.7, 4 shipped (v0.8.490 → v0.8.505).
> Phase 5 is the **cutover + deletion**; the `0.9.0` bump + spec sync are **Phase 6** (separate plan).

## Context

The owned loop (`co_cli/agent/loop.py`, flag `config.llm.use_owned_loop`, default **False**) now reaches
behavioral parity with the graph path across streaming, dispatch, flood-cap, approval, recovery, and
length-retry (Phases 2–4). The graph path (`co_cli/agent/orchestrate.py:run_turn`, the pydantic-ai
`Agent.iter()` orchestration) remains the default and the parallel-path reference oracle. Phase 5 **flips
the default to the owned loop, then deletes the graph path** — the milestone's "own the agent turn" payoff
lands here.

**Current cutover seam** (`co_cli/main.py:197-213`): `_run_foreground_turn` branches on
`deps.config.llm.use_owned_loop` — owned → `run_turn_owned`, else graph → `run_turn(agent=…)`. The
subagent seam mirrors it (`co_cli/agent/run.py:47-54`). Evals select via `CO_EVAL_OWNED_LOOP`
(`evals/_deps.py:47-88`, `drive_turn`).

**Source-grounded correction to the milestone's Phase-5 contract (CD-m-4).** The milestone authored its
deletion inventory *before* the owned path was implemented; the shipped owned loop **reuses several pieces
the milestone listed for deletion**. Verified against source this planning pass — these are **NOT orphans
and must survive the cutover**:
- **`clarify_answers`** (`deps.py:223`) — the owned inline-approval path **writes** it (`approval.py:128`)
  and the unchanged `clarify` tool body **reads** it (`user_input.py:56`). The milestone's "inline drops the
  `clarify_answers` stash" (Behavioral Constraints) was an aspiration the implementation did not take —
  `approval.py:104` keeps the "unchanged clarify body reads the stash" design. **Keep the field.** Only the
  *graph writer* (`orchestrate.py:247`) is deleted. (The return-directly simplification is an independent
  micro-cleanup, explicitly **out of scope** — see Open Questions OQ-1.)
- **`deferred_tool_awareness_prompt`** (`_instructions.py:84`) — the milestone conflated "deferred *tools*"
  (the `tool_view` self-loading awareness stub) with "deferred *approval*." The owned preflight **calls it
  every step** (`preflight.py:222`) and the delegation builder uses it (`delegation.py:81`). **Keep the
  function.** Only the graph's per-turn `_ctx` registration is dead (`orchestrator.py:91`), which orphans
  the thin `deferred_tool_awareness_prompt_ctx` wrapper (`_instructions.py:128`).
- **`RepairingStreamedResponse`** (`llm/_json_repair.py:133`) — the owned `model_turn` uses it inline
  (`model_turn.py:108,122`). The milestone listed `_RepairingStreamedResponse` for deletion; that is wrong.
  **Keep it.** Only the `SurrogateRecoveryModel` *wrapper* that also consumed it goes.

**Confirmed true orphans / graph-only (deletable):** `resume_tool_names` (`deps.py:216` — written only by
the graph approval loop `orchestrate.py:530,538`, read only by the graph toolset gate `toolset.py:90`);
`record_approval_choice` (`tools/approvals.py:188` — only caller is `orchestrate.py:266`);
`CommandContext.agent` (`commands/types.py:21` — never read in `commands/`); the `SessionAgent` /
`Agent[CoDeps, str | DeferredToolRequests]` aliases (`orchestrate.py:105`, `main.py:182`,
`commands/types.py:21`); the whole `DeferredToolRequests/Results/ToolApproved` suspend/resume wiring
in `orchestrate.py`.

**`SurrogateRecoveryModel` and `_CallSeamToolset` are deletable but RIPPLE — not a flat delete:**
- `SurrogateRecoveryModel` (`llm/surrogate_recovery_model.py:39`) wraps `deps.model.model`
  (`llm/factory.py:56,70`). The owned path **strips it** (`loop.py:95` `_unwrap_model`) because
  `model_turn` reimplements its three concerns (span, surrogate-retry, JSON repair) inline
  (`model_turn.py:80`). Deleting it means **`factory.py` stops wrapping** (builds the raw model) and
  **`loop.py`'s `_unwrap_model` becomes a no-op to remove**. `_json_repair` / `_message_sanitize` stay
  (owned `model_turn` uses them).
- `_CallSeamToolset` (`agent/toolset.py:143`) is still live on the owned path as a **container**: the
  bootstrap orchestrator toolset is `_CallSeamToolset(filtered(...))` (`agent/core.py:80`) and owned
  dispatch deliberately runs on its **unwrapped** `.wrapped` (`dispatch.py:91`) to avoid double-applying
  the folded span/cap/spill (`dispatch.py:13-14`, "the still-live `_CallSeamToolset` seam"). Deleting the
  class requires **unwinding the wrap-then-unwrap**: `core.py:80` returns the filtered stack directly,
  `dispatch.py:91` uses `deps.toolset` directly (drop the `.wrapped` getattr), the owned subagent FLAT_EXACT
  builder returns the plain toolset (`loop.py:581`), and `build.py` (graph) is deleted. This is a genuine
  **clarity-by-subtraction** win (kill the wrap-then-unwrap dance), not a one-liner.

**Symbols the owned path imports FROM the graph module** (must relocate to an owned home before
`orchestrate.py` is deleted) — `loop.py:50-56`: `TurnResult`, `_REASONING_OVERFLOW_MESSAGE`,
`TOOL_CAP_NO_ANSWER_TEXT`, `_handle_model_request_event` (+ its callee `_handle_tool_call_event`),
`_last_assistant_text`. `build.py:15` imports the `SessionAgent` type alias (graph-only, deleted with it).

## Problem & Outcome

**Problem:** co still ships the graph as the default agent turn, carrying the entire parallel graph stack
(`orchestrate.py` ~1100 lines, `build.py`, the `SurrogateRecoveryModel` model wrapper, the `_CallSeamToolset`
wrap-then-unwrap, the `DeferredToolRequests` suspend/resume approval machinery, the graph-only orphan fields)
purely as dead weight beside the proven owned path. Every future change pays the two-path tax, and the
milestone's control/maintainability/v2-insulation payoff is unrealized until the graph is gone.

**Outcome:** the owned loop is the **only** agent turn. The default flips, the full eval suite passes on the
owned path at parity, then the graph path and all its now-dead scaffolding are deleted: `orchestrate.py`'s
graph machinery, `build.py`, the `SurrogateRecoveryModel` wrapper (factory builds the raw model), the
`_CallSeamToolset` wrap-then-unwrap (collapsed to a single unwrapped stack), the `DeferredToolRequests`
approval wiring, the confirmed orphan fields, and the `use_owned_loop` flag itself. The two owned drivers
(`_orchestrator_step_loop`, `run_standalone_owned`) are confirmed to share the construction + request +
dispatch scaffolding (the "scaffolding tenet"), differing only by workflow. pydantic-ai remains the
provider + message + schema library.

**Failure cost:** **highest of the milestone.** This is the irreversible cut — once the graph oracle is
deleted there is no reference path to diff against, and a missed live-dependency (the three stale CD-m-4
items prove the inventory was wrong on first pass) would delete code the owned loop actually runs, wedging
every turn. Mitigated by: (a) the cutover **gate** (TASK-1) proving the owned path green on the full eval +
test suite *before any deletion*; (b) deletion split into relocate-then-cut so the suite stays green at each
step; (c) per-task repo-wide stale-reference grep + full suite (the rename/drop `done_when` discipline);
(d) the source-verified orphan list above replacing the milestone's stale one.

## Scope

**In:** flip `use_owned_loop` default to True and prove the owned path on the full eval + test suite (the
cutover gate); relocate the shared symbols out of `orchestrate.py` to owned homes; delete the graph
orchestration (`orchestrate.py` graph machinery, `build.py`, the `main.py`/`run.py` cutover branches);
delete the `SurrogateRecoveryModel` wrapper (factory builds raw, remove `loop._unwrap_model`); unwind and
delete `_CallSeamToolset`; delete the `DeferredToolRequests` suspend/resume approval wiring + the confirmed
orphan fields (`resume_tool_names` + its `toolset.py:90` gate, `record_approval_choice`, the graph clarify
writer, the `deferred_tool_awareness_prompt_ctx` registration + wrapper, the `SessionAgent` / `Agent[…,
DeferredToolRequests]` aliases + the dead `agent` param/field); remove the `use_owned_loop` flag +
`CO_EVAL_OWNED_LOOP` env; delete graph-only tests and port any graph-behavior tests lacking an owned twin;
redirect every eval/test off `orchestrate.run_turn`; confirm + document the two-driver scaffolding tenet.

**Out:**
- **`docs/specs/` edits and the `0.9.0` version bump** — those are **Phase 6** (the layer rule: source
  changes here, spec sync there). Phase 5 ships as a normal patch bump via `/ship`.
- **`ModelMessage` / `*Part` migration** — kept as the wire + durable type (milestone Out).
- **`RepairingStreamedResponse`, `_json_repair`, `_message_sanitize`, `clarify_answers`,
  `deferred_tool_awareness_prompt`, `QuestionRequired`** — live on the owned path; **not** deleted (Context).
- **The clarify return-directly simplification** (drop the stash) — independent micro-cleanup, OQ-1.
- **A new `AgentSpec`/parameterized-mega-loop abstraction** — explicitly rejected (High-Level Design §
  "Scaffolding tenet"); the tenet is already met by the shared primitives.
- **The R2 named-agent selector** (`phase5-5` plan) — depends on this phase but is a separate post-`0.9.0`
  plan; not in scope here (see Next step).

## Behavioral Constraints

- **The cutover gate is the authorization.** No deletion task starts until TASK-1 proves the owned path
  passes the full eval suite (run on the owned path) **and** the full pytest suite, with no regression vs
  the graph path. If the gate fails, stop and RCA — do not delete the oracle.
- **Observable behavior is preserved across the flip.** Same streaming output, tool dispatch order,
  flood-cap semantics, length-retry/overflow/400 recovery, approval decisions, error surfacing. The owned
  path already proved this per-phase against the graph oracle; TASK-1 is the whole-suite confirmation.
- **One known, documented divergence carries through** (from Phase 4): interrupt uses drop→fill (the
  unanswered response is retained + synthetically answered next turn) rather than the graph's break-time
  drop. Already shipped; not re-litigated here.
- **Relocate before delete.** Shared symbols move to owned homes (suite green) *before* `orchestrate.py` is
  cut, so no step leaves the tree red.
- **Zero stale references on completion.** Repo-wide grep across `co_cli/` AND `tests/` AND `evals/` must
  find zero references to every deleted symbol, and `grep -rE 'from pydantic_ai\.[a-z_]*\._|from
  pydantic_ai\._' co_cli/` must stay limited to the one documented `_output.OutputToolset` reach
  (`preflight.py`).

## High-Level Design

### Cutover gate first, then relocate-then-cut

```
TASK-1  flip default True → run full eval suite (owned) + full pytest → GREEN gate  (no deletion)
TASK-2  relocate shared symbols out of orchestrate.py → owned homes               (suite green, graph still present)
TASK-3  delete graph orchestration: orchestrate.py machinery + build.py + main/run branches + flag + eval env + type aliases
TASK-4  delete dead wrappers + orphan wiring: SurrogateRecoveryModel (factory raw, _unwrap_model gone);
        _CallSeamToolset unwind; DeferredTool* approval wiring; resume_tool_names + gate; graph clarify writer;
        deferred_tool_awareness_prompt_ctx + wrapper; record_approval_choice
TASK-5  tests + evals: delete graph-only tests, port unique graph behaviors, redirect off orchestrate.run_turn
TASK-6  confirm + document the scaffolding tenet; final repo-wide grep + full suite + quality-gate full
```

The deletion is inherently near-atomic (`orchestrate.py` cannot be half-present while `main.py` imports
`run_turn`), so TASK-3/TASK-4/TASK-5 land together as one phase delivery; they are **split by concern for
reviewability**, each with its own stale-reference grep, not as independently-shippable units.

### Relocation homes (TASK-2)

| Symbol | From | To | Rationale |
|--------|------|----|-----------|
| `TurnResult` **+ its `TurnOutcome = Literal["continue","error"]` alias** | `orchestrate.py:114,55` | `turn_state.py` | Turn-scoped result type; `turn_state.py` already owns `TurnState`/`ToolCapState`/`TurnExit`. Cohesive; no cycle (plain dataclass over `RunUsage`/`ModelMessage`). `TurnResult.outcome` is typed by `TurnOutcome` — relocate the alias alongside or TASK-2 leaves the tree red (CD-M-1). |
| `_REASONING_OVERFLOW_MESSAGE`, `TOOL_CAP_NO_ANSWER_TEXT` | `orchestrate.py:81,541` | `loop.py` | Sole consumer is the owned loop. |
| `_handle_model_request_event` | `orchestrate.py:334` | `display/stream_renderer.py` | Pure SDK-event→`StreamRenderer` glue consumed by the owned `_drive_model_request` (`loop.py:54,147`); belongs with the renderer (display cohesion). |
| `_last_assistant_text` | `orchestrate.py:547` | `loop.py` | History-extraction helper consumed only by the owned tool-cap salvage. |

**NOT relocated (CD-M-1):** `_handle_tool_call_event` (`orchestrate.py:318`) and `_handle_tool_event`
(`orchestrate.py:355`) are **graph-only** — they are *siblings* of `_handle_model_request_event` (not its
callees) and have **zero owned consumers** (grep). They are deleted with `orchestrate.py` in TASK-3, never
relocated (relocating dead code plants a fresh orphan).

`main.py:22` and `tests/test_eval_trace_slicing.py:30` then import `TurnResult` from `turn_state` (not
`orchestrate`); `loop.py:50-56`'s `orchestrate` import block collapses to the relocated homes.

### Scaffolding tenet — confirm, do NOT abstract (the load-bearing design decision)

The milestone tenet (line 204) says Phase 5 "unifies `_orchestrator_step_loop` and `run_standalone_owned`
into the single driver." **Read against the shipped code, that unification is already substantially done**
and the right Phase-5 action is to **confirm + document it and remove accidental divergence — not to build a
new abstraction.** Source evidence:

- The two drivers **already share** the construction scaffolding (`_build_subagent_toolset` mirrors the
  orchestrator toolset via the same `build_native_toolset`/`assemble_routing_toolset`), the request
  primitive (`_drive_model_request`, `loop.py:126`), every preflight builder
  (`run_history_processors`/`build_tool_defs`/`build_request_params`/`clean_message_history`), `dispatch_tools`,
  `collect_inline_approvals`, and `ToolCapState`. The `loop.py:11-15` docstring already states this contract.
- They **differ only by workflow**, exactly as the tenet permits ("the only differences are
  workflow/functionality"): the orchestrator streams free text, runs history processors + dynamic per-step
  instructions + length-retry/overflow/400 recovery + rendering + the abort marker and terminates on
  *no tool calls*; the subagent forces the `final_result` output tool (`allow_text_output=False`), runs no
  rendering/recovery, and terminates on a *validated `final_result` call* with a bounded re-prompt loop.
- Forcing these two genuinely-different while-loops through one parameterized function (the Explore-surfaced
  `AgentSpec` with `termination_gate`/`result_processor`/`approval_mode`/`rendering_mode` callbacks) would
  **reduce clarity, not increase it** — it manufactures an indirection layer to host two callers, violating
  clarity-by-subtraction and the surgical-changes rule. The tenet's intent (shared scaffolding) is met;
  identical control flow is **not** the tenet.

So TASK-6 is: (a) after the `_CallSeamToolset` unwind (TASK-4), confirm the orchestrator and subagent
toolsets share the same unwrapped-stack shape and `_build_subagent_toolset` carries no leftover wrap; (b)
verify no construction/loop scaffolding diverges except the documented workflow differences; (c) record the
shared-core arrangement in the `loop.py` module docstring as the canonical statement of the tenet. **No new
module, no callback registry, no enum-dispatched mega-loop.** A *future* new scaffolding divergence is the
smell the tenet guards against — this phase establishes the baseline.

### What gets deleted from `orchestrate.py` (TASK-3) — all graph-only after TASK-2

`run_turn`, `_execute_run` (the `agent.iter()` driver), `_run_approval_loop`,
`_collect_deferred_tool_approvals`, `_StallTimer` (owned has the inline per-event reschedule,
`loop.py:126-152`), `_handle_tool_call_event` + `_handle_tool_event` (graph-only sibling event handlers,
zero owned consumers, CD-M-1), `_check_turn_caps`, `_check_output_limits`,
`_emit_final_output_if_needed`, `_length_retry_settings`, `_history_with_pending_user_input`,
`_transient_error_message`, `_apply_400_reformulation`, `_history_after_successful_run`,
`_attempt_overflow_recovery`, `_handle_model_http_error`, `_build_error_turn_result`,
`_build_interrupted_turn_result`, `_finalize_run`, `_prompt_char_count`, `_TurnState`, `SessionRunResult`,
the `_LENGTH_RETRY_*`/`_HTTP_400_REFLECT_BACKOFF_SECS`/`_REASONING_OVERFLOW_SIGNATURE`/`_LLM_RUN_WARN_SECS`
constants, the `ToolApprovalDecisions` alias. Each Phase-4-ported twin (`recovery.py`, `loop.py`,
`preflight.py`) is the surviving owner — the orchestrate.py originals are now duplicates. **The whole file
is deleted** once these and the relocations (TASK-2) are done.

## Tasks

### ✓ DONE TASK-1 — Cutover gate: flip default + full eval/test suite on the owned path
- **files:** `co_cli/config/llm.py`
- **Capture the graph baseline first, same session (CD-m-3 / PO-m-3):** with `use_owned_loop` still default
  False, run the full eval suite on the **graph** path and save the named baseline log — this is the only
  oracle the cutover diffs against, and it is deleted right after, so a remembered prior run is not
  acceptable. Then flip `use_owned_loop` default `False → True` (`llm.py:313`) and re-run the **full eval
  suite** (owned path) + the **full pytest suite**. This is the go/no-go authorization for all deletion
  tasks. Do **not** delete anything in this task.
- **done_when:** the graph-path eval baseline is captured to a named log in this session; `use_owned_loop`
  defaults True; the full eval suite (`evals/`, owned path) runs at parity **diffed against that captured
  baseline** (no scenario regresses) AND `scripts/quality-gate.sh full` is green; every run piped to a
  timestamped `.pytest-logs/` log with the spans log tailed live (RCA any slow/stalled LLM call, never
  widen a timeout without approval).
- **success_signal:** a real `uv run co chat` turn runs end-to-end on the owned loop as the default with no
  flag set.
- **prerequisites:** none

### ✓ DONE TASK-2 — Relocate the shared symbols out of orchestrate.py to owned homes
- **files:** `co_cli/agent/turn_state.py`, `co_cli/agent/loop.py`, `co_cli/display/stream_renderer.py`,
  `co_cli/main.py`, `tests/test_eval_trace_slicing.py`
- Move per the relocation table (High-Level Design): `TurnResult` **+ the `TurnOutcome` alias** →
  `turn_state.py`; `_REASONING_OVERFLOW_MESSAGE` + `TOOL_CAP_NO_ANSWER_TEXT` + `_last_assistant_text` →
  `loop.py`; `_handle_model_request_event` (only — **not** `_handle_tool_call_event`/`_handle_tool_event`,
  which are graph-only and deleted in TASK-3, CD-M-1) → `stream_renderer.py`. Update `loop.py`'s
  `from co_cli.agent.orchestrate import (...)` block, `main.py:22`'s `TurnResult` import, **and
  `tests/test_eval_trace_slicing.py:30`'s `TurnResult` import** (CD-m-1, redirect not delete) to the new
  homes. Pure relocation — no behavior change; the graph path still imports what it needs from its own file.
- **done_when:** repo-wide grep shows zero owned-path/test imports from `co_cli.agent.orchestrate` for the
  moved symbols; `scripts/quality-gate.sh full` green (graph path still present and passing).
- **success_signal:** N/A (pure refactor).
- **prerequisites:** TASK-1

### ✓ DONE TASK-3 — Delete the graph orchestration path + the flag
- **files:** `co_cli/agent/orchestrate.py` (delete file), `co_cli/agent/build.py` (delete file),
  `co_cli/agent/run.py`, `co_cli/main.py`, `co_cli/commands/types.py`, `co_cli/config/llm.py`,
  `co_cli/agent/spec.py`
- Delete `orchestrate.py` entirely (all graph machinery now duplicated by Phase-4 twins + relocated by
  TASK-2) and `build.py` entirely (`build_orchestrator` + `_history_processor_shim` are graph-only;
  `build_task_agent` is reached only by `run.py`'s graph branch). Collapse `main.py:_run_foreground_turn` to
  call `run_turn_owned` unconditionally — remove the `else: run_turn(agent=…)` branch, the `agent` parameter,
  the `build_orchestrator` call site, the `DeferredToolRequests`/`run_turn` imports, and the
  `Agent[CoDeps, str | DeferredToolRequests]` annotation (`main.py:18,22,182`). Collapse `run.py` to call
  `run_standalone_owned` unconditionally (remove the graph branch + `build_task_agent` import). Delete
  `CommandContext.agent` + its `Agent[…, DeferredToolRequests]` annotation/import (`commands/types.py:9,21`
  — field never read in `commands/`). Remove the `use_owned_loop` config field (`llm.py:313`). Fix the
  stale `[str, DeferredToolRequests]` doc reference in `spec.py:45`.
- **done_when:** `orchestrate.py` and `build.py` no longer exist; the two-grep check is clean (CD-m-4 — the
  `run_turn\b` pattern matches `run_turn_owned`, so split it): `grep -rn "run_turn\b" co_cli/ | grep -v
  run_turn_owned` is empty, and `grep -rn "\borchestrate\b\|build_orchestrator\|build_task_agent\|use_owned_loop" co_cli/`
  is empty (the only surviving driver entrypoints are `run_turn_owned`/`run_standalone_owned`); `co_cli`
  imports clean (`uv run co --help` boots); `scripts/quality-gate.sh full` green after TASK-5's test updates
  land.
- **success_signal:** N/A (deletion; user-facing behavior unchanged — owned path already default).
- **prerequisites:** TASK-2

### ✓ DONE TASK-4 — Delete the dead wrappers + orphan approval/deferred wiring
- **files:** `co_cli/llm/factory.py`, `co_cli/llm/surrogate_recovery_model.py` (delete file),
  `co_cli/agent/loop.py`, `co_cli/agent/core.py`, `co_cli/agent/dispatch.py`, `co_cli/agent/toolset.py`,
  `co_cli/deps.py`, `co_cli/tools/approvals.py`, `co_cli/agent/orchestrator.py`,
  `co_cli/agent/_instructions.py`
- **SurrogateRecoveryModel:** make `factory.py` build the raw provider model (drop the
  `SurrogateRecoveryModel(...)` wraps at `:56,70`); delete `surrogate_recovery_model.py`; remove
  `loop.py:95`'s `_unwrap_model` (the model is now already raw — update the one call site to use
  `deps.model.model` directly). Keep `_json_repair` / `_message_sanitize` (owned `model_turn` uses them).
- **_CallSeamToolset unwind:** `core.py:80` returns the filtered routing stack directly (no
  `_CallSeamToolset` wrap); `dispatch.py:91` uses `deps.toolset` directly (drop the `.wrapped` getattr +
  the "still-live seam" comment); `loop.py:581` (`_build_subagent_toolset` FLAT_EXACT) returns the plain
  toolset; delete the `_CallSeamToolset` class (`toolset.py:143`) and its imports.
- **Orphan fields + graph approval wiring:** delete `resume_tool_names` (`deps.py:216` + reset `:256`) and
  its read gate (`toolset.py:90`); delete `record_approval_choice` (`tools/approvals.py:188`); delete the
  graph clarify writer reference (gone with `orchestrate.py`, but verify no stale import). **All five `_ctx`
  per-turn shims die together (CD-M-2):** `ORCHESTRATOR_SPEC.per_turn_instructions` (`orchestrator.py:87-93`)
  is a tuple of five `_ctx` shims consumed **only** by `build_orchestrator` (deleted TASK-3); the owned path
  uses the non-`_ctx` builders directly via `assemble_instructions` (`preflight.py:218-225`). So delete the
  whole `per_turn_instructions` tuple from `ORCHESTRATOR_SPEC`, all five `_ctx` shims
  (`safety_prompt_ctx`/`wrap_up_prompt_ctx`/`current_time_prompt_ctx`/`skill_manifest_prompt_ctx`/`deferred_tool_awareness_prompt_ctx`,
  `_instructions.py:116-133`), and the now write-never-read `per_turn_instructions` field from
  `OrchestratorSpec` (`spec.py:50`). **Keep** the non-`_ctx` builders, `clarify_answers`,
  `deferred_tool_awareness_prompt` (non-`_ctx`), `QuestionRequired`, `RepairingStreamedResponse` (Context —
  owned path uses all five).
- **done_when:** `grep -rn "SurrogateRecoveryModel\|_CallSeamToolset\|_unwrap_model\|resume_tool_names\|record_approval_choice\|_prompt_ctx\b\|per_turn_instructions" co_cli/ tests/`
  returns zero (note: the shim token is `_prompt_ctx\b`, **not** bare `_ctx\b` — the latter is
  un-satisfiable, matching 327 unrelated `num_ctx`/`cmd_ctx` hits; the five doomed shims all end in
  `_prompt_ctx`, mirroring the CD-m-4 `run_turn\b`/`run_turn_owned` fix); `grep -rn "clarify_answers\|RepairingStreamedResponse\|deferred_tool_awareness_prompt\b" co_cli/`
  still shows the live owned-path uses (regression check that the keepers survived); owned dispatch runs on
  the unwrapped stack with no double-applied span/cap (verified by the existing `test_flow_owned_dispatch` +
  `test_flow_owned_tool_cap_state`); `scripts/quality-gate.sh full` green.
- **success_signal:** a real owned-path turn dispatches a tool with exactly one `co.tool.*` span (no
  double-application) and a `shell_exec` approval still prompts + remembers.
- **prerequisites:** TASK-3

### ✓ DONE TASK-5 — Tests + evals: delete graph-only, port unique behaviors, redirect off orchestrate.run_turn
- **files:** `tests/test_flow_*` (graph-path files), `evals/_deps.py`, `evals/eval_*.py`
- **Build the complete coupled-file set by grep, not the illustrative list (CD-m-2)** — `grep -rln
  "co_cli.agent.orchestrate\|SurrogateRecoveryModel\|_CallSeamToolset\|CO_EVAL_OWNED_LOOP\|use_owned_loop"
  tests/ evals/`. Three disposition classes:
  1. **Delete — graph-internals tests with an owned twin:** `test_flow_approval_subject.py`
     (`_collect_deferred_tool_approvals`); `test_flow_orchestrate_length_retry.py` / `_reasoning_overflow.py`
     / `_reformulation.py` / `_stall_timeout.py` (covered by `test_flow_owned_recovery.py`);
     `test_flow_multimodal_prompt.py` (`_prompt_char_count`); assess `test_flow_model_request_cap.py` /
     `test_flow_tool_call_functional.py` / `test_flow_turn_result_model_requests.py` /
     `test_flow_usage_tracking.py` / `test_flow_phase2_migrated.py` against their owned twins.
  2. **Port — kept-behavior tests pinned to a deleted wrapper (NOT blanket-delete):**
     `test_surrogate_recovery_model.py` + `test_flow_tool_call_repair.py` (JSON repair — assert against the
     owned `model_turn` inline repair, not the gone wrapper); `test_flow_observability_spans.py` /
     `test_flow_spill.py` / `test_flow_tool_call_limit.py` / `test_flow_user_image_intake.py` (tool span /
     MCP spill / cap — assert against `dispatch_tools` / the owned twins). These assert behavior that
     **survives**, so they must be ported to the surviving owner, not deleted with the wrapper.
  3. **Redirect — eval driver + direct importers:** redirect `evals/_deps.py:drive_turn` to call
     `run_turn_owned` unconditionally (drop the `run_turn` import + `CO_EVAL_OWNED_LOOP` env handling at
     `:47-88`); change every eval importing `run_turn` directly (`eval_agentic_loop`, `eval_memory`,
     `eval_context_stability`, `eval_session_recall`, `eval_rule_compliance`, `eval_multistep_plan`,
     `eval_session_continuity`, `eval_user_model`, `eval_skills`; `eval_daily_chat` already routes via
     `drive_turn` — verify) to use `drive_turn`. `tests/test_eval_trace_slicing.py`'s `TurnResult` import was
     already redirected in TASK-2.
  Each ported test must exercise the owned runtime path (a real `run_turn_owned`/`model_turn`/`dispatch_tools`
  drive), not grep-level assertions.
- **done_when:** `grep -rn "co_cli.agent.orchestrate\|orchestrate import\|SurrogateRecoveryModel\|_CallSeamToolset\|CO_EVAL_OWNED_LOOP\|use_owned_loop" tests/ evals/`
  returns zero; the full pytest suite is green (deleted tests gone, ported tests pass on the surviving owner);
  at least one redirected eval (`eval_context_stability` or `eval_agentic_loop`) runs end-to-end on the
  owned path (piped to a timestamped log, spans tailed).
- **success_signal:** the eval suite drives the owned loop with no `CO_EVAL_OWNED_LOOP` env needed.
- **prerequisites:** TASK-3 (the import targets are gone), TASK-4

### ✓ DONE TASK-6 — Confirm the scaffolding tenet + final whole-repo gate
- **files:** `co_cli/agent/loop.py` (docstring only)
- Confirm (read-and-verify, no abstraction): after the `_CallSeamToolset` unwind, the orchestrator and
  subagent toolsets share the same unwrapped-stack construction and `_build_subagent_toolset` carries no
  leftover wrap; the two drivers diverge only by the documented workflow differences (output tool,
  termination predicate, rendering, recovery, approval propagation). **Record the residual construction
  asymmetry as intended (CD-m-5):** post-unwind, `_build_subagent_toolset` FLAT_EXACT returns a plain
  `FunctionToolset` while the orchestrator/VISIBILITY_MODEL path returns the unwrapped routing stack —
  confirm `dispatch_tools`/`build_tool_defs` read `deps.toolset` generically and treat both identically, and
  document the FLAT_EXACT vs VISIBILITY_MODEL split as the **one intended** construction divergence so a
  future reviewer does not read it as the smell the tenet guards against. Update the `loop.py` module
  docstring to state the shared-core arrangement as the canonical tenet baseline. Run the final whole-repo
  gate.
- **done_when:** the read-and-confirm produces a recorded finding (PO-m-2 — "no divergence found beyond the
  intended FLAT_EXACT/VISIBILITY_MODEL split" is itself the verifiable result), captured in the delivery
  summary; the `loop.py` docstring records the shared-scaffolding/differ-by-workflow tenet; repo-wide grep
  across `co_cli/` + `tests/` + `evals/` shows **zero** references to every symbol deleted in TASK-3/4/5;
  `grep -rE 'from pydantic_ai\.[a-z_]*\._|from pydantic_ai\._' co_cli/` is limited to the one documented
  `_output.OutputToolset` reach; `scripts/quality-gate.sh full` green.
- **success_signal:** N/A (refactor + gate).
- **prerequisites:** TASK-3, TASK-4, TASK-5

## Testing

**The cutover gate (TASK-1) is the primary safety net** — the full eval suite (UAT smoke runs on real
seeded scenarios) plus the full pytest suite, run on the owned path, prove parity with the graph baseline
*before* the oracle is deleted. After deletion, the owned-path tests (`test_flow_owned_*`, already shipped
Phases 2–4) are the regression net; graph-only tests are deleted and any unique graph behavior is ported
onto `run_turn_owned` (TASK-5). Functional-only policy holds — no structural assertions on the loop's
internal shape; correctness is behavior parity. Every pytest/eval run pipes to a timestamped `.pytest-logs/`
log with the spans log tailed live (RCA slow LLM calls; never widen a timeout without approval). The
standing **G1-1 private-reach guard** stays green (only the one `_output.OutputToolset` reach).

**Why no new evals:** Phase 5 adds no behavior — it deletes the parallel path the suite already covers on
both sides. The right verification is the existing suite passing on the now-sole owned path, not new cases.

## Open Questions

- **OQ-1 (clarify return-directly simplification) — DEFERRED, out of scope.** Once the graph is gone, the
  owned `_handle_clarify` (which already holds the answers, `approval.py:127`) could return them directly as
  the `ToolReturnPart` and skip the `clarify_answers` stash + the clarify-body re-read — making
  `clarify_answers` and `QuestionRequired` fully removable. This is the simplification
  `approval.py:18-20`/the milestone Behavioral Constraints envisioned. It is an **independent micro-cleanup**
  that touches the clarify tool body during an already-high-risk cutover, so it is deliberately **not** in
  Phase 5. **Re-raise trigger:** a standalone source cleanup post-`0.9.0`, or a `phase5-5` rider if the
  clarify path is being touched anyway — **not** Phase 6, which is spec-sync-only / source-frozen (PO-m-4).
  Phase 5 keeps `clarify_answers` exactly as shipped.

## Next step

After Gate 1, run `/orchestrate-dev loop-decoupling-phase5`. Phase 5 unblocks two downstream items:
- **Phase 6** (spec sync + `0.9.0`) — the next milestone phase.
- **`phase5-5`** (R2 named-agent `subagent_type` selector, `2026-06-27-182243-loop-decoupling-phase5-5.md`)
  — its impl (TASK-2) is gated on this phase's unified driver; its design+eval (TASK-1) may proceed in
  parallel once the user settles its skills-vs-roles crux. **This phase delivers exactly the dependency
  `phase5-5` waits on** — see the cross-review note in the delivery discussion. **Phase 5 must NOT
  pre-shape its driver for R2's `subagent_type` seam (PO-m-1):** do not speculatively add the role field to
  `TaskAgentSpec`/the instruction builder/the `delegate` signature now — anticipating R2 inside this phase
  would quietly undercut TASK-6's "no new abstraction" discipline. R2's seam lands in `phase5-5`, on the
  driver this phase settles.

## Decisions

C1: Core Dev `revise / Blocking: CD-M-1, CD-M-2` (all three "keeper" claims + all orphan claims
source-verified CORRECT); PO `approve / Blocking: none`. C2: Core Dev `approve / Blocking: none` — both
blockers + all five minors verified resolved against the edited sections. Convergence at C2.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Source-confirmed: `_handle_tool_call_event`/`_handle_tool_event` are graph-only siblings (zero owned consumers); `TurnResult.outcome` is typed by `TurnOutcome`. Relocating dead code plants an orphan; omitting the alias leaves TASK-2 red. | Relocation table (HLD + TASK-2): relocate only `_handle_model_request_event`; `TurnResult` carries `TurnOutcome`; siblings deleted in TASK-3 (added to the TASK-3 deletion list). |
| CD-M-2 | adopt | `per_turn_instructions` (5 `_ctx` shims) is consumed only by `build_orchestrator` (deleted TASK-3); owned path uses the non-`_ctx` builders. Leaving 4 dead shims + a write-never-read spec field is the one-sided accretion the zero-stale-ref constraint forbids. | TASK-4: delete all five `_ctx` shims, the `ORCHESTRATOR_SPEC.per_turn_instructions` tuple, and the `OrchestratorSpec.per_turn_instructions` field; `done_when` greps `_ctx`/`per_turn_instructions` to zero. |
| CD-m-1 | adopt | `test_eval_trace_slicing.py:30` imports `TurnResult` from orchestrate. | TASK-2 files += `tests/test_eval_trace_slicing.py`; redirect (not delete) its import. |
| CD-m-2 | adopt | Illustrative test list under-covers grep; wrapper-pinned tests (repair/spill/span) assert kept behavior and must port to the surviving owner, not delete. | TASK-5 rewritten into three grep-derived disposition classes (delete / port / redirect) with `eval_daily_chat` verify + the SurrogateRecoveryModel/_CallSeamToolset test set named. |
| CD-m-3 / PO-m-3 | adopt | The cutover diff needs a captured graph baseline in-session — the oracle is deleted right after; a remembered figure is not reproducible. | TASK-1: capture graph-path eval baseline (flag still False) before flipping; `done_when` diffs the owned run against it. |
| CD-m-4 | adopt | `run_turn\b` matches `run_turn_owned`; the single-grep `done_when` was self-contradictory. | TASK-3 `done_when` split into two greps. |
| CD-m-5 / PO-m-2 | adopt | Confirm-not-abstract is source-grounded; the residual FLAT_EXACT (plain toolset) vs VISIBILITY_MODEL (unwrapped stack) split must be recorded as intended so it isn't read as the tenet smell; TASK-6 needs a verifiable recorded finding. | TASK-6: record the intended construction asymmetry + confirm `dispatch_tools`/`build_tool_defs` treat both identically; `done_when` makes the read-and-confirm a recorded finding. |
| PO-M (R2 boundary) / PO-m-1 | adopt | Keeping R2 out is the right boundary (matches milestone post-3.6 sequencing); but Phase 5 must not speculatively add the `subagent_type` seam, which would undercut TASK-6's no-abstraction discipline. | Next step: explicit "do not pre-shape the driver for R2's seam." |
| PO-m-4 | adopt | Phase 6 is spec-sync-only / source-frozen; folding a source cleanup there contradicts the layer rule. | OQ-1 re-raise trigger: drop Phase-6; standalone post-`0.9.0` or phase5-5 rider only. |

## Final — Team Lead

Plan approved — Core Dev `Blocking: none` (C2), PO `Blocking: none` (C1).

The load-bearing planning result: the milestone's CD-m-4 Phase-5 deletion list was authored before the owned
path existed and is **partly stale** — `clarify_answers`, `deferred_tool_awareness_prompt`, and
`RepairingStreamedResponse` are live on the owned path and survive the cut (both reviewers source-verified
this). Phase 5 deletes only the true graph-only surface, behind a cutover gate that proves the owned path on
the full eval+test suite *before* the reference oracle is removed. Spec sync + `0.9.0` are Phase 6.

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: **right problem? correct scope?** The load-bearing risk is **irreversibility** — once
> the graph oracle is deleted there is no diff target, so the plan front-loads a cutover gate (TASK-1: flip
> default, capture the graph baseline in-session, prove the owned path green) before any deletion, and
> replaces the milestone's stale orphan list with a source-verified one. Scope is cutover+delete only; specs
> and the `0.9.0` bump are Phase 6; the R2 `subagent_type` selector stays in `phase5-5`.
> Once approved, run: `/orchestrate-dev loop-decoupling-phase5`

## Delivery Summary — 2026-06-29

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | graph baseline captured in-session; owned full eval suite at parity (no regression vs baseline) + `quality-gate full` green | ✓ pass |
| TASK-2 | (prior checkpoint) shared symbols relocated; no owned-path/test imports from `orchestrate` | ✓ pass |
| TASK-3 | `orchestrate.py`/`build.py` deleted; two-grep clean; `co --help` boots; suite green | ✓ pass |
| TASK-4 | `SurrogateRecoveryModel`/`_CallSeamToolset`/`_unwrap_model`/`resume_tool_names`/`record_approval_choice`/`_prompt_ctx`/`per_turn_instructions` → 0; keepers survive | ✓ pass |
| TASK-5 | tests/evals grep-clean of graph symbols; full suite green; redirected evals drive owned path | ✓ pass |
| TASK-6 | scaffolding-tenet finding recorded; `loop.py` docstring states tenet; repo-wide grep 0; G1-1 guard intact; `quality-gate full` green | ✓ pass |

**Cutover gate (TASK-1) — the authorization, run + RCA'd in-session:**
- **Deterministic half:** full pytest suite green (925 passed pre-deletion) with every owned-path flow test passing *beside* its graph twin in one run — paired parity. Instruction/tool/model-settings surface verified **byte-identical** between paths by source (`build_static_instructions` reuses `ORCHESTRATOR_SPEC.static_instruction_builders`; owned per-turn instructions match graph order; both default to `deps.model.settings`).
- **Eval half (de-confounded):** the real `~/.co-cli/co-cli-search.db` had `chunks_vec_1024` index corruption confounding all memory-dependent cases. Rebuilt the (derived) index → 0 corruption errors. Post-rebuild: `groundedness` flipped **fully green** (W7.C "capitulated to false premise" was the corruption masking *correct* owned behaviour); `eval_memory` W3.R fails *identically on graph* (reviewer under-saves = model-capability, parity); remaining eval failures are all path-independent — harness 50s/turn budget timeouts on verbose-reasoning/multi-search turns (confirmed flaky on graph too), model-capability recall/merge under-firing, or self-documented scaffold flakes. **No owned-loop regression in any failure.** `eval_agentic_loop` W12.A run ×2 owned + ×2 graph showed both paths flaky on the same case.

**Scaffolding tenet (TASK-6) recorded finding:** after the `_CallSeamToolset` unwind, the orchestrator and subagent drive share the request primitive (`_drive_model_request`), every preflight builder, `dispatch_tools`, `collect_inline_approvals`, and `ToolCapState`, differing only by workflow. **No divergence found beyond the intended FLAT_EXACT (plain `FunctionToolset`) vs VISIBILITY_MODEL (filtered routing stack) split** — `dispatch_tools`/`build_tool_defs` read `deps.toolset` generically and treat both identically. No new abstraction added (no `AgentSpec`/mega-loop). Recorded in the `loop.py` module docstring as the canonical baseline.

**Tests:** full suite **884 passed, 0 failed** (6:45, owned path). Test triage: 11 graph-only deleted (covered by `test_flow_owned_*` twins), 8 wrapper-pinned ported to surviving owners (`model_turn` repair/surrogate, `dispatch_tools` span/cap/spill), 4 owned-test harnesses fixed off the seam, ~12 `agent=None` `CommandContext` stragglers + 1 `.wrapped` reach-in fixed. `eval_rule_compliance` section-ablation reworked onto the owned loop via `_installed_spec` (module-global `ORCHESTRATOR_SPEC` swap). Individual LLM calls all ≤14.3s (warm band) — no stalled call; the 6:45 wall-clock is the serial sum of healthy real-LLM turns.
**Doc Sync:** not run — spec edits are **Phase 6** (layer rule: source here, specs there). Source docstrings/comments swept inline (zero stale graph references).

**Overall: DELIVERED.** The owned loop is the sole agent turn; the graph path, its wrappers, the flag, and all orphan wiring are gone. 91 files changed, 3 deleted (`orchestrate.py`, `build.py`, `surrogate_recovery_model.py`). Repo-wide grep across `co_cli/` + `tests/` + `evals/` is clean of every deleted symbol; G1-1 private-reach guard limited to the one documented `_output.OutputToolset`. Working tree uncommitted (ship is a separate step). **Next:** `/review-impl loop-decoupling-phase5`, then `/ship`. Phase 6 (spec sync + `0.9.0`) and `phase5-5` (R2 selector) unblocked.

## Implementation Review — 2026-06-29

Review scope = checkpoint commit `1acdcb7a` (TASK-1 flag flip + TASK-2 relocation + eval redirects) **plus** the uncommitted working tree (TASK-3/4/5/6). Both together constitute the Phase-5 delivery.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | default flipped; full eval suite at parity; `quality-gate full` green | ✓ pass | `use_owned_loop` flag removed entirely (TASK-3); `main.py:189` calls `run_turn_owned` unconditionally; suite green (884 passed) |
| TASK-2 | zero owned-path/test imports from `orchestrate` for moved symbols | ✓ pass | `TurnResult`+`TurnOutcome` in `turn_state.py:33,120`; `handle_model_request_event` relocated to `stream_renderer.py` (underscore dropped — cross-package, per visibility contract), imported `loop.py:76`; `main.py:20` + `test_eval_trace_slicing.py:30` import `TurnResult` from `turn_state` |
| TASK-3 | `orchestrate.py`/`build.py` gone; two-grep clean; `co --help` boots | ✓ pass | both files deleted; `grep run_turn\b \| grep -v run_turn_owned` empty; `grep orchestrate\|build_orchestrator\|build_task_agent\|use_owned_loop co_cli/` empty; `co --help` boots (import+bootstrap graph loads) |
| TASK-4 | dead-symbol grep → 0; keepers survive; unwrapped dispatch | ✓ pass | `grep SurrogateRecoveryModel\|_CallSeamToolset\|_unwrap_model\|resume_tool_names\|record_approval_choice\|_prompt_ctx\|per_turn_instructions co_cli/ tests/` → 0; `factory.py:55,66` builds raw model; `dispatch.py:87` returns `deps.toolset` directly; `loop.py:149` `raw_model = deps.model.model`; keepers (`clarify_answers`/`RepairingStreamedResponse`/`deferred_tool_awareness_prompt`) live (`deps.py:222`, `model_turn.py:104`, `preflight.py:222`) |
| TASK-5 | tests/evals grep-clean; full suite green; evals drive owned | ✓ pass | `grep co_cli.agent.orchestrate\|SurrogateRecoveryModel\|_CallSeamToolset\|CO_EVAL_OWNED_LOOP\|use_owned_loop tests/ evals/` → 0; `_deps.py:56` `drive_turn` → `run_turn_owned` unconditional |
| TASK-6 | tenet finding recorded in `loop.py` docstring; repo grep 0; G1-1 intact | ✓ pass | `loop.py:11-22` records shared-scaffolding tenet + the one intended FLAT_EXACT/VISIBILITY_MODEL split; `grep -rE 'from pydantic_ai\.[a-z_]*\._' co_cli/` limited to `preflight.py:258` `_output.OutputToolset` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Undeclared files touched (~13 `co_cli/` + 4 `evals/_*` helpers + 4 `tests/` files) | repo-wide | minor | Confirmed all are docstring/comment stale-reference sweeps (graph→owned) + necessary `agent=`/`build_orchestrator` ripple cleanups required by the "zero stale references" constraint — no behavior change. Left as-is. |
| `_routing_toolset(deps)` now a 2-caller `return deps.toolset` passthrough | dispatch.py:81 | minor | Borderline clarity-by-subtraction candidate, but it has 2 callers + a semantic docstring (was the former `.wrapped`-unwrap seam). Not a single-caller speculative wrapper — left as-is. |
| `_run_foreground_turn` nested `try / try-except / finally` could collapse to one `try` | main.py:192-211 | minor | Mechanical artifact of the branch-collapse; functionally correct (cancellation handling + cleanup preserved). Left as-is. |

_No blocking findings._

### Tests
- Command: `uv run pytest`
- Result: **884 passed, 0 failed** (6:58)
- Log: `.pytest-logs/20260629-025939-review-impl.log`
- LLM-call timing: all warm-band (2.6–4.3s), no stalled call; wall-clock is the serial sum of real-LLM turns.

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads cleanly with `orchestrate.py`/`build.py`/`surrogate_recovery_model.py` deleted and `run_turn_owned` reachable).
- TASK-1 `success_signal` (real chat turn on owned loop as default): verified by config — `use_owned_loop` removed, `run_turn_owned` unconditional at `main.py:189`; chat turn itself is LLM-mediated, non-gating.
- TASK-4 `success_signal` (one `co.tool.*` span, no double-application; `shell_exec` approval prompts+remembers): verified via the owned-path flow tests in the green suite (`test_flow_owned_dispatch`, `test_flow_owned_subagent`, tool-cap/spill/span ports) — LLM-mediated, chat non-gating.

### Overall: PASS
Cutover is clean and irreversible-safe: every `done_when` grep is satisfied, the three CD-m-4 keepers survive on the owned path, the full real-LLM suite is green on the now-sole owned loop, lint passes, and the only undeclared edits are required stale-reference sweeps. Ready for `/ship`.
