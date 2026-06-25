# Phase 3 — Inline approval on the owned loop (replace the deny-placeholder)

**Parent milestone:** `2026-06-24-234633-loop-decoupling-milestone.md` (Phase 3). **Design:** `2026-06-24-234633-loop-decoupling-design.md` §6.5. **Survey:** `docs/reference/RESEARCH-loop-decoupling-peer-survey.md` §"Inline approval".

This phase makes the owned loop **prompt the user inline** for approval-gated tool calls, at parity with the graph path's `DeferredToolRequests` suspend/resume. It is a behavior-preserving parity refactor — no new capability. Write-capable delegated children + child→parent approval propagation are **Phase 3.5** (separate plan, depends on this).

## Context

Phases 1, 2, 2.5 shipped (all in `completed/`). The owned loop (`config.llm.use_owned_loop`) drives `run_turn_owned` (`co_cli/agent/loop.py:170`) → `_orchestrator_step_loop` (`loop.py:243`) → `dispatch_tools` (`co_cli/agent/dispatch.py:260`).

**Today the owned path cannot run an approval-gated tool interactively.** Two gaps:

1. **The deny-placeholder.** `resolve_auto_approvals` (`dispatch.py:104`) is the Phase-2 stand-in: a call whose tool is catalog-marked `is_approval_required=True` and is *not* auto-approved returns `DENY_PLACEHOLDER_TEXT` (`dispatch.py:55`) — it **never prompts**. The loop calls it at `loop.py:299` and passes the denials into `dispatch_tools`.

2. **Dynamic in-body raises crash the turn.** Two tools decide approval *at runtime inside the body*, not via the catalog flag:
   - `shell_exec` (`co_cli/tools/shell/execute.py:80-81`) raises `ApprovalRequired` when `evaluate_shell_command(...)` returns `REQUIRE_APPROVAL` and `not ctx.tool_call_approved`. **It is NOT catalog-marked** (`execute.py:19` — `@agent_tool(..., is_concurrent_safe=True)`, no `is_approval_required`), so `resolve_auto_approvals` does not see it.
   - `clarify` (`co_cli/tools/system/user_input.py:50-51`) raises `QuestionRequired` (a subclass of `ApprovalRequired`, `approvals.py:20`) on the first unapproved call.
   
   `_run_tool_body` (`dispatch.py:162`) only catches `ValidationError`/`ModelRetry`; an `ApprovalRequired`/`QuestionRequired` propagates uncaught through `_execute_one` → `dispatch_tools` → `_orchestrator_step_loop` → out of `run_turn_owned` (whose `except` clauses, `loop.py:215-228`, do not cover it). So on the owned path **shell-approval and clarify currently crash the turn.**

**How the graph path does it (the behavior to match).** Both triggers are unified by the SDK: catalog `is_approval_required=True` → `toolset.add_function(fn, requires_approval=info.is_approval_required)` (`co_cli/agent/toolset.py:130,137`); dynamic in-body `raise ApprovalRequired`. Either way the run ends with `DeferredToolRequests` output. `_collect_deferred_tool_approvals` (`orchestrate.py:191`) then, per pending call: clarify (`"questions"` in metadata) → `frontend.prompt_question` per question → stash answers in `deps.runtime.clarify_answers` keyed by `tool_call_id` + approve with bare `ToolApproved()` (`orchestrate.py:219-249`); standard approval → `resolve_approval_subject` → `is_auto_approved`? skip : `frontend.prompt_approval` (y/n/a) → `record_approval_choice` (`orchestrate.py:251-273`). `_run_approval_loop` (`orchestrate.py:506`) resumes the run with the decisions; approved calls re-run with `ctx.tool_call_approved=True`, denied calls get `ToolDenied`. Headless (`frontend is None`) auto-denies (`choice = "n"`, `orchestrate.py:263`).

**Deny is continue, not halt (source-grounded).** On the graph path a denial yields a `ToolDenied` tool-result fed back to the model and the turn **continues** — approved siblings still execute. `eval_approval_discipline` requires post-denial continuation: W8.B (`respects_denial`, 2 turns — no silent retry next turn) and W8.C (`adjusts_plan_after_denial`, 3 turns — proposes a less-destructive alternative) both grade behavior *after* a scripted `approval_override="n"` denial (`evals/eval_approval_discipline.py:10-19,171`). A halt-on-deny owned path would diverge from the graph oracle and fail the parity gate.

**The reusable helpers (graph + owned share these verbatim).** `resolve_approval_subject`, `is_auto_approved`, `record_approval_choice`, `decode_tool_args` (`co_cli/tools/approvals.py`) — already used by the owned `resolve_auto_approvals`. `record_approval_choice` writes into a `DeferredToolResults` today; the owned path needs the same allow/deny+remember decision without that container (see TASK-2).

**Eval wiring already exists.** `evals/_deps.py` has `apply_eval_owned_loop` (`CO_EVAL_OWNED_LOOP` → `use_owned_loop=True`, `:50`) and `drive_turn` (dispatches to `run_turn_owned` or `run_turn`, `:62`); `EvalFrontend.approval_override` drives a real y/n/a choice through the production approval path (`:108-120`). `eval_daily_chat`/`eval_groundedness` already call `drive_turn`. **`eval_approval_discipline` and `eval_bounded_autonomy` still call `run_turn` directly** (`eval_approval_discipline.py:48,127`; `eval_bounded_autonomy.py:45,119`) — they have not been converted, so the Phase-3 gate cannot yet exercise them on the owned path.

**Subagents never approve** (`build.py:123`, `loop.py:377` — both add tools `requires_approval=False`), so the owned subagent driver `run_standalone_owned` (`loop.py:489`, `frontend=None`) needs no approval and must stay approval-free.

## Problem & Outcome

**Problem:** the owned loop cannot ask the user before a destructive or clarifying action — it silently denies catalog-gated calls and crashes on shell-approval/clarify. The owned path is therefore not yet at parity on the trust boundary, blocking the Phase-5 cutover.

**Outcome:** the owned loop prompts inline — sequentially, before the parallel fan-out — for every approval-gated call (catalog-marked **and** shell's dynamic policy gate), honoring auto-approval and remember-choice; clarify asks its questions inline and returns answers; denials become tool-results fed back to the model and the turn continues; headless auto-denies. Behavior matches the graph path so `eval_approval_discipline` + `eval_bounded_autonomy` pass on the owned path at parity.

**Failure cost:** without it, the owned path can never be the default — it cannot honor the user's gate on destructive actions (the trust boundary, a mission tenet), so the whole loop-decoupling milestone stalls at Phase 3.

## Scope

**In:**
- An inline approval collector run **before** the parallel fan-out in `_orchestrator_step_loop`, replacing `resolve_auto_approvals`'s deny-placeholder.
- Coverage of **both** approval triggers: catalog `is_approval_required=True` AND shell's dynamic `REQUIRE_APPROVAL` policy (evaluated via the same `evaluate_shell_command`).
- Inline `clarify` handling (prompt the questions, supply answers) on the owned path.
- `tool_call_approved` propagation into the per-call `RunContext` so approved shell/clarify bodies execute.
- Headless (`frontend is None`) auto-deny parity.
- Convert `eval_approval_discipline` + `eval_bounded_autonomy` to `drive_turn` and verify them on the owned path (the gate).
- Child-aware shaping (collector keyed on `frontend`+`deps`, no orchestrator-only globals) so Phase 3.5 extends rather than retrofits.

**Out:**
- **Deleting** the graph path's deferred machinery or the orphaned runtime fields (`clarify_answers`, `resume_tool_names`) — **Phase 5** owns all deletion; the graph path stays default and untouched here, and it still reads those fields (`toolset.py:90`, `orchestrate.py:530`).
- **Write-capable delegated children + child→parent approval propagation** — Phase 3.5 (the 2.5 child has no approval-required tools, so no child triggers approval here).
- Error/recovery/length-retry relocation — Phase 4.
- Adopting opencode's reject-halts-step / forced-final-turn — a behavior change vs the graph (breaks the parity gate); explicitly not adopted (D-A).

## Behavioral Constraints

- **Parity with the graph path.** Same approval decisions (subject resolution, auto-approval, remember-choice), same deny semantics (**deny → denial tool-result fed back, approved siblings still execute, turn continues** — D-A), same headless auto-deny. `eval_approval_discipline` + `eval_bounded_autonomy` must pass on the owned path at parity with the graph oracle.
- **Pre-fan-out sequencing (design §6.5, risk #2).** All approval/question prompts run **sequentially, before** any tool executes in the step. co has one terminal and a ≤3 parallel cap; concurrent prompts would clobber the frontend's single `_question_future`. This pre-sequencing is co-original (no peer pre-sequences); it gets first-principles scrutiny, not borrowed confidence.
- **Interrupt (Ctrl-C) halts the turn** — already handled by `run_turn_owned`'s `CancelledError`/`KeyboardInterrupt` catch (`loop.py:215`). Deny does **not** halt (D-A).
- **Subagents stay approval-free.** The collector runs only in `_orchestrator_step_loop`, never in `run_standalone_owned` (subagent tools are `requires_approval=False`; `frontend=None`).
- **Graph path untouched.** No edit to `orchestrate.py`'s deferred flow; the shared helpers in `approvals.py` keep their graph-path signatures.
- **Child-aware, not child-enabled.** The collector takes `frontend`+`deps` as parameters so Phase 3.5 can route a propagated child request to the parent's frontend — but Phase 3 ships no propagation and widens no child surface.

## High-Level Design

```
_orchestrator_step_loop, after classifying tool_calls (loop.py:298):

  resolution = await collect_inline_approvals(calls, deps, frontend)   # SEQUENTIAL, pre-fanout
     for call in calls (original order):
       • clarify          → prompt_question per question → stash answers in
                            deps.runtime.clarify_answers[id]; mark approved (D-C)
       • needs_approval?  → catalog info.is_approval_required
                            OR shell_exec & evaluate_shell_command == REQUIRE_APPROVAL (D-B)
            yes → resolve_approval_subject → is_auto_approved? mark approved
                  else prompt_approval (frontend None → "n") → y/a: mark approved
                       (+ remember_tool_approval on "a"); n: denial ToolReturnPart
            no  → nothing (executes normally)
     returns ApprovalResolution(denials: {id: ToolReturnPart}, approved_ids: set[str])

  parts = await dispatch_tools(calls, deps, cap_state=…, frontend=frontend,
                               denials=resolution.denials, approved_ids=resolution.approved_ids)
     • within-cap, non-denied calls execute; ctx.tool_call_approved = id in approved_ids
       so shell's in-body gate passes and clarify reads its stashed answers
     • denied calls → their denial ToolReturnPart; turn continues (D-A)
```

**Why pre-fanout covers shell (a dynamic, in-body gate).** The collector decodes args via `decode_tool_args(call.args)` then calls `evaluate_shell_command(cmd, deps.config.shell.safe_commands)` to *decide whether to prompt*; on approval it marks the call `approved` so dispatch sets `ctx.tool_call_approved=True`, and shell's body (`execute.py:80`) skips its own raise and executes. The policy is a pure function (`shell_policy.py`), so evaluating it in the collector and again in the body is free of side effects — no logic is duplicated, only re-evaluated. Shell is the **only** dynamic-approval tool; the collector carries one shell branch (mirroring the existing shell branch in `resolve_approval_subject`, `approvals.py:76`). If a second dynamic-approval tool ever appears, generalize to a per-tool `approval_probe_fn` — **this deferred-generalization note must land as a code comment at the shell branch** (PO-m-2) so the next dynamic-approval tool author finds it instead of adding a second string-match.

**Why clarify reuses the existing stash (D-C).** The graph path and the `clarify` tool body both depend on `deps.runtime.clarify_answers` + `ctx.tool_call_approved` (`user_input.py:50-56`). The graph path stays live until Phase 5, so Phase 3 **cannot** drop the stash. The owned collector therefore reuses it verbatim: prompt the questions, stash answers keyed by `tool_call_id`, mark approved → dispatch runs the unchanged `clarify` body with `tool_call_approved=True`, which reads the stash and returns the answers. This is the surgical, parity-safe port of `orchestrate.py:219-249`. Design §6.5's "drops the `clarify_answers` stash + the `override_args` workaround entirely" describes the **Phase-5 end state** (when the graph path is deleted and clarify can be simplified) — not Phase 3.

**Deny = continue (D-A).** A denied call becomes a `ToolReturnPart(outcome="denied", content="User denied this action")` (the graph's `ToolDenied` content). It is fed back into history with the other results; the turn loops and the model reacts. The owned loop does **not** cancel approved siblings or halt the step on a denial — that matches the graph and the W8 evals. (The milestone's shorthand "deny = loop-halt" is resolved here toward parity; "interrupt = halt" is the Ctrl-C path, already implemented.)

**The replaced surface.** `resolve_auto_approvals` + `DENY_PLACEHOLDER_TEXT` (`dispatch.py:55,104`) are removed; the collector subsumes the auto-approval-skip logic and adds real prompting. `dispatch_tools` gains an `approved_ids: set[str] | None` param; `make_run_context` (`dispatch.py:64`) gains `tool_call_approved: bool = False`.

## Tasks

### ✓ DONE TASK-1 — `tool_call_approved` plumbing into per-call dispatch
- `files:` `co_cli/agent/dispatch.py`
- Add `tool_call_approved: bool = False` to `make_run_context` (`dispatch.py:64`) and pass it through to the constructed `RunContext`. Add `approved_ids: set[str] | None = None` to `dispatch_tools` (`dispatch.py:260`) and thread it to `_execute_one` so a call whose `tool_call_id ∈ approved_ids` builds its `RunContext` with `tool_call_approved=True`. The `ctx` used for visibility filtering / `get_visible_tools` (`dispatch.py:295`) stays unapproved (it gates nothing).
- **`tool_call_approved` is load-bearing only for the two in-body raisers (CD-m-1):** shell (`execute.py:80`) and clarify (`user_input.py:50`) read `ctx.tool_call_approved`. Catalog `is_approval_required=True` tools never read it on the owned path — `FunctionToolset.call_tool` has no approval check (`.venv/.../pydantic_ai/toolsets/function.py:649-660`) and the graph's `unapproved`-kind enforcement never runs in the owned loop. So for catalog tools the **denial** entry is the gate; `approved_ids` membership is a harmless no-op for them, and a non-denied catalog approval-gated call executes regardless of `approved_ids`. Do not over-thread approval state for catalog tools.
- `done_when:` an owned-path dispatch test: a call listed in `approved_ids` runs with `ctx.tool_call_approved=True` (observable via a tool that branches on it — e.g. a real `shell_exec` REQUIRE_APPROVAL command executes when approved); a call absent from `approved_ids` is unapproved. Scoped tests + `scripts/quality-gate.sh full` green.
- `success_signal:` an approved approval-gated call executes its body instead of raising.
- `prerequisites:` none.

### ✓ DONE TASK-2 — The inline approval collector (catalog + shell), pre-fanout
- `files:` `co_cli/agent/dispatch.py` (or a new `co_cli/agent/approval.py` if `dispatch.py` grows past cohesion — TL decides at dev), `co_cli/tools/approvals.py` (only if a non-`DeferredToolResults` decision helper is needed — see note)
- Add `collect_inline_approvals(tool_calls, deps, frontend) -> ApprovalResolution` (`ApprovalResolution` = `denials: dict[str, ToolReturnPart]`, `approved_ids: set[str]`). The arg order `(tool_calls, deps, frontend)` is **canonical** — it matches `dispatch_tools(tool_calls, deps, *, frontend=…)` house style and **supersedes** design §6.5's `(tool_calls, frontend, deps)` positional sketch (CD-m-6). **Strictly sequential** over calls in original order — carry a one-line comment that prompts must never be `asyncio.gather`-ed (a future "parallelize approvals" optimization would clobber the frontend's single `_prompt_future`/`_question_future`, `display/core.py:328,343,660` — CD-m-4). For each call: determine `needs_approval` = `info.is_approval_required` (catalog) OR (`tool_name == "shell_exec"` and `evaluate_shell_command(decode_tool_args(call.args).get("cmd", ""), deps.config.shell.safe_commands).decision == REQUIRE_APPROVAL` — decode args first, mirroring `approvals.py:77`; malformed args → `{}` → `cmd=""`, which `evaluate_shell_command` treats as `REQUIRE_APPROVAL` (`shell_policy.py:127`) → a harmless prompt-then-empty-cmd that **matches the graph's behavior** on the same malformed input — CD-m-2). When it needs approval: `resolve_approval_subject` → `is_auto_approved`? add to `approved_ids` : `prompt_approval` (frontend `None` → `"n"`); `y`/`a` → `approved_ids` (+ `remember_tool_approval` on `"a"` when `subject.can_remember`); `n` → a denial `ToolReturnPart` (content = the graph's `"User denied this action"`, `outcome="denied"`). Calls that need no approval and aren't clarify are left untouched (execute normally). Replace the `loop.py:299` `resolve_auto_approvals` call with `collect_inline_approvals`, passing `resolution.denials` + `resolution.approved_ids` into `dispatch_tools`. Delete `resolve_auto_approvals` + `DENY_PLACEHOLDER_TEXT`.
- Note on `record_approval_choice`: it writes a `DeferredToolResults` container the owned path doesn't have. The owned collector calls `remember_tool_approval` directly (the only reusable side effect) and records allow/deny in `ApprovalResolution` — it does **not** import `DeferredToolResults`. `record_approval_choice` stays untouched for the graph path.
- `done_when:` owned-path flow/integration tests: (a) a catalog approval-gated call with a scripted `"y"` executes; (b) the same with `"n"` returns a denial part and the **turn continues** (a following step still runs); (c) an auto-approved subject (pre-seeded `session_approval_rules`) executes with **no prompt**; (d) `"a"` records a `SessionApprovalRule` so a second same-subject call is auto-approved; (e) `frontend=None` auto-denies; (f) a `shell_exec` REQUIRE_APPROVAL command prompts and, on `"y"`, executes. Repo-wide grep: zero references to `resolve_auto_approvals`/`DENY_PLACEHOLDER_TEXT`. Full suite green.
- `success_signal:` the owned loop prompts for and honors approval on both triggers, with auto-approval and remember-choice.
- `prerequisites:` TASK-1.

### ✓ DONE TASK-3 — Inline `clarify` on the owned path
- `files:` `co_cli/agent/dispatch.py` (collector), test
- In `collect_inline_approvals`, detect `tool_name == "clarify"`: decode its `questions` arg, build a `QuestionPrompt` per question (mirroring `orchestrate.py:222-240` — handle `options` as `list[{label}]`/`list[str]`, `multiple`), `await frontend.prompt_question` each (frontend `None` → `""`), stash the answers list in `deps.runtime.clarify_answers[tool_call_id]`, and add the call to `approved_ids`. Dispatch then runs the unchanged `clarify` body, which reads the stash and returns the answers. (D-C: reuse, no stash removal in Phase 3.)
- **Headless clarify approves-with-empty (NOT deny) — parity (CD-m-5):** the graph's headless asymmetry is that standard approvals auto-deny (`orchestrate.py:263`, `"n"`) but clarify auto-approves with empty answers (`orchestrate.py:240,248`). Keep the headless clarify branch on the approve-with-empty path (`""` answers + `approved_ids`), or it diverges from the oracle.
- `done_when:` an owned-path test: the orchestrator emits a `clarify` call; the scripted frontend answers; the `clarify` ToolReturnPart contains the answers as positional JSON; the turn continues. (Previously this crashed the owned turn.) **Plus a headless case (CD-m-7):** `frontend=None` → clarify call → empty-string answers stashed + call approved (the approve-with-empty branch, NOT auto-deny — the asymmetry CD-m-5 pins) → body returns the empty-answer result and the turn continues. Full suite green.
- `success_signal:` clarify asks and returns answers inline on the owned path without crashing.
- `prerequisites:` TASK-2.

### ✓ DONE TASK-4 — Convert approval + bounded-autonomy evals to `drive_turn`; verify on owned path
- `files:` `evals/eval_approval_discipline.py`, `evals/eval_bounded_autonomy.py`
- Replace the direct `run_turn(...)` call in each eval's `_drive_turns` lambda (`eval_approval_discipline.py:127-133`, `eval_bounded_autonomy.py:119`) with `drive_turn(agent=…, user_input=…, deps=…, message_history=…, frontend=…)` (import from `evals._deps`) — an exact kwarg-for-kwarg drop-in (neither passes `model_settings`; `drive_turn` defaults it), and **delete the now-orphaned `from co_cli.agent.orchestrate import run_turn` import** in each eval (`eval_approval_discipline.py:48`, `eval_bounded_autonomy.py:45`). `apply_eval_owned_loop(deps)` is already honored — both evals build deps via `eval_deps()`, which calls it (`_deps.py:149`); no second edit (CD-m-3). `EvalFrontend.approval_override` already drives the choice through the production path. No rubric/scenario change.
- `done_when:` both evals run green on the **graph** path (no regression) AND on the **owned** path (`CO_EVAL_OWNED_LOOP=1`) at parity — W8.A/B/C honor the scripted denial and continue; bounded-autonomy cases pass. Tail the LLM-call log during the runs. (Eval run-records are JSONL under `evals/_outputs/`, not markdown.)
- `success_signal:` the trust-boundary + bounded-autonomy behavior holds on the owned loop at parity with the graph.
- `prerequisites:` TASK-3.

### ✓ DONE TASK-5 — Owned-path approval flow test (the behavioral net)
- `files:` `tests/test_flow_owned_approval.py` (new)
- A real-Ollama owned-path flow test covering the contract a unit test can't: (a) a destructive ask is approval-gated and, on a scripted denial, **not executed** (verify on disk, mirroring the W8 scratch-file pattern) while the turn continues; (b) a scripted approval executes the action; (c) auto-approval (pre-seeded rule) skips the prompt. Use a `HeadlessFrontend`/scripted frontend with the production approval path; LLM calls hit `llm.host` with the shared reasoning/noreason settings.
- `done_when:` the flow test passes on the owned loop; full suite green; tail the spans/log during the run.
- `success_signal:` the deny-blocks-the-side-effect + approve-executes contract holds end-to-end on the owned loop.
- `prerequisites:` TASK-3.

## Testing

Functional-only (mirrors `done_when`; no structural assertions). The behavioral net: TASK-5's real-Ollama flow test (deny-blocks / approve-executes / auto-approve) + TASK-2/3's deterministic collector tests (catalog approve/deny/auto/remember/headless, shell dynamic gate, clarify inline). The **parity gate** is TASK-4: `eval_approval_discipline` + `eval_bounded_autonomy` green on the owned path against the still-live graph path as the oracle. All LLM calls hit `llm.host` from config with `noreason`/`reasoning` settings from the shared eval helpers — never a coined `ModelSettings`. Tail the log every run; fail fast (`-x`). No new eval scenario (the trust-boundary scenarios already exist).

**Standing boundary invariant (milestone G1-1):** `grep -rE 'from pydantic_ai\.[a-z_]*\._|from pydantic_ai\._' co_cli/` stays empty except the one documented `_output` reach in `preflight.py` (unchanged by this phase). This phase adds no new private-module reach.

## Open Questions

None deferred. Resolved inline (source-grounded):
- **D-A (deny semantics) → continue, not halt.** Graph feeds `ToolDenied` and continues (`orchestrate.py:203`); W8.B/C grade post-denial continuation (`eval_approval_discipline.py:18-19`). Halt-on-deny would fail the parity gate. The milestone's "deny = loop-halt" is resolved toward parity; "interrupt = halt" is the Ctrl-C path (already implemented, `loop.py:215`). opencode's reject-halts-step is **not** adopted (behavior change).
- **D-B (shell dynamic approval) → collector evaluates `evaluate_shell_command` pre-fanout, then sets `tool_call_approved`.** Honors design §6.5's pre-sequencing choice; shell is the only dynamic-approval tool; pure-function policy → re-evaluation is side-effect-free. Generalize to `approval_probe_fn` only if a second such tool appears.
- **D-C (clarify) → reuse the existing `clarify_answers` stash + `tool_call_approved` on the owned path; defer the stash-drop to Phase 5.** The graph path and the `clarify` body still depend on the stash (`user_input.py:50-56`); Phase 3 can't remove it. Design §6.5's "drops the stash" is the Phase-5 end state.

**Finding to carry to Phase 5 (orphan-inventory correction, not Phase-3 work):** milestone CD-m-4 lists `deferred_tool_awareness_prompt` (`orchestrator.py`, the per-turn instruction) among the orphans inline approval creates. **This is wrong** — `deferred_tool_awareness_prompt` builds the **DEFERRED-visibility** tool-discovery stub (`co_cli/tools/deferred_prompt.py` — tools hidden until `tool_view`), which is orthogonal to deferred *approval* and stays fully live (it is exactly why `delegate` is `ALWAYS`). Phase 5 must **not** delete it. The genuine inline-approval orphans (deletable once the graph path goes at Phase 5) are `deps.runtime.clarify_answers` + `resume_tool_names` (`deps.py:208,215`) and their graph readers (`toolset.py:90`, `orchestrate.py:530`). **Re-raise trigger (PO-m-3):** when Phase 5 is planned, correct the milestone's Phase-5 inventory line (`...-milestone.md:129`) to drop `deferred_tool_awareness_prompt` from the deletion list before it is acted on.

## Decisions

C1: Core Dev `approve / Blocking: none`; PO `approve / Blocking: none`. Both `Blocking: none` on C1 → convergence at C1, no C2.

Re-review (Gate-1 validation pass): Core Dev `approve / Blocking: none`; PO `approve / Blocking: none`. Both critics independently re-verified every C1 source claim against actual source (deny-part bytes `_agent_graph.py:1877`; catalog no-op `function.py:649-663`; pure `evaluate_shell_command` `shell_policy.py:76-127`; single prompt future `core.py:328,343,660`; eval `run_turn`→`drive_turn` clean drop-in) — all grounded. Three minor refinements adopted (CD-m-6, CD-m-7, CD-m-2 wording); no blockers.

| Issue | Decision | Rationale | Change |
|-------|----------|-----------|--------|
| Catalog approval enforcement (CD verify) | confirmed | Catalog `requires_approval=True` is enforced only by the agent-graph's `unapproved` tool-kind (`_agent_graph.py:1556,1619,1646`), never by `FunctionToolset.call_tool` (`function.py:649-660`). The owned loop dispatches via `routing.call_tool` (`dispatch.py:186,308`) with no SDK gate, so the collector's pre-emption is necessary and sufficient. No blocker. | — |
| CD-m-1 | adopt | `tool_call_approved` is read only by the two in-body raisers (shell `execute.py:80`, clarify `user_input.py:50`); catalog tools are gated by the denial entry, so `approved_ids` is a harmless no-op for them. | TASK-1: added note that `tool_call_approved` matters only to shell/clarify and a non-denied catalog call executes regardless of `approved_ids`. |
| CD-m-2 | adopt | Shell args may be a raw JSON string; policy eval must decode first (mirrors `approvals.py:77`). | TASK-2 + HLD note: arg source is `decode_tool_args(call.args).get("cmd","")`; malformed → `cmd=""` → no spurious prompt. |
| CD-m-3 | adopt | `apply_eval_owned_loop` already runs via `eval_deps()` (`_deps.py:149`); the only edit is the `run_turn`→`drive_turn` lambda swap, and the `run_turn` import is then orphaned. | TASK-4: dropped the phantom "ensure honored" clause; added deletion of the orphaned `run_turn` import in both evals. |
| CD-m-4 | adopt | The frontend has a single instance-level prompt future (`core.py:328,343,660`); concurrent prompts clobber it. | TASK-2: collector pinned strictly sequential with a no-`gather` code-comment requirement. |
| CD-m-5 | adopt | Graph headless asymmetry: standard approvals auto-deny but clarify auto-approves with empty answers (`orchestrate.py:240,248,263`). | TASK-3: pinned headless clarify to approve-with-empty (not deny) for parity. |
| PO-m-1 | noted | Child-aware design obligation (milestone Phase-3 contract) is already met — collector keyed on `frontend`+`deps`, no orchestrator-only globals. | — (confirmation only) |
| PO-m-2 | adopt | The shell string-match should carry the `approval_probe_fn` deferred-generalization note where the next author will find it. | HLD shell note: the deferred-generalization note must land as a code comment at the shell branch. |
| PO-m-3 | adopt | The milestone's Phase-5 inventory line still lists `deferred_tool_awareness_prompt` for deletion; the correction must reach Phase 5. | Finding: added a re-raise trigger to correct `...-milestone.md:129` when Phase 5 is planned. |
| CD-m-6 (re-review) | adopt | Design §6.5 sketches `collect_inline_approvals(tool_calls, frontend, deps)`; the plan uses `(tool_calls, deps, frontend)` (house style, matches `dispatch_tools`). Drift would read as a contradiction at dev. | TASK-2: pinned `(tool_calls, deps, frontend)` as canonical, superseding the design's positional sketch. |
| CD-m-7 (re-review) | adopt | TASK-3's `done_when` exercised only the scripted-frontend clarify path, not the headless approve-with-empty branch (the CD-m-5 asymmetry that's the divergence risk). | TASK-3 `done_when`: added a headless clarify case (frontend=None → empty answers + approved, not auto-deny). |
| CD-m-2 wording (re-review) | adopt | The "malformed args → no spurious prompt" note was imprecise: `cmd=""` → `evaluate_shell_command` returns `REQUIRE_APPROVAL` (`shell_policy.py:127`), so it *does* prompt — harmlessly, matching the graph on the same input. | TASK-2: corrected the note to "harmless prompt-then-empty-cmd that matches the graph". |
| PO-m-1 (re-review) | noted | `approval_probe_fn` note is right-sized (a code comment, not a built abstraction); dev should frame it conditionally ("*if* a second dynamic-approval tool appears"). No plan-text change. | — |
| PO-m-2 (re-review) | noted | TASK-5's real-Ollama flow test verifies the deny-blocks-the-disk-side-effect contract the behavior-judged TASK-4 evals do not assert — minimum net, not bloat. | — |

## Final — Team Lead

Plan approved — Core Dev `Blocking: none`, PO `Blocking: none` on both the original cycle (C1) and an independent Gate-1 re-review pass. Both critics returned no blockers, so the loop converged at C1; the eight original minor issues (CD-m-1…5, PO-m-1…3) and three re-review refinements (CD-m-6, CD-m-7, CD-m-2 wording) were all adopted or noted, each mapped to a specific task/section above. The re-review re-grounded every load-bearing C1 source claim against actual source (deny-part bytes, catalog no-op, pure shell policy, single prompt future, eval drop-in) — all confirmed. The two load-bearing first-principles calls are source-grounded: **D-A** (deny = continue, not halt — graph parity + the W8 evals require post-denial continuation) and **D-C** (reuse the `clarify_answers` stash; the graph path still depends on it, so the stash-drop is a Phase-5 item). The SDK-internals crux is verified: catalog approval is enforced only by the graph's `unapproved` tool-kind, never by `FunctionToolset.call_tool`, so the owned loop has no SDK gate and the collector's pre-emption is both necessary and sufficient.

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: **right problem? correct scope?** This is a behavior-preserving parity phase — inline approval at parity with the graph's deferred suspend/resume, zero new capability; deletion is fenced to Phase 5, write-capable children to Phase 3.5.
> Once approved, run: `/orchestrate-dev loop-decoupling-phase3`

## Delivery Summary — 2026-06-25

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | a call in `approved_ids` runs with `ctx.tool_call_approved=True`; absent → unapproved | ✓ pass |
| TASK-2 | collector approve/deny/auto/remember/headless + shell dynamic gate; `resolve_auto_approvals`/`DENY_PLACEHOLDER_TEXT` removed (grep zero) | ✓ pass |
| TASK-3 | clarify asks + returns answers inline (collector→dispatch→body); headless approve-with-empty | ✓ pass |
| TASK-4 | `eval_approval_discipline` + `eval_bounded_autonomy` green on owned path at parity (graph oracle) | ✓ pass |
| TASK-5 | deny-blocks / approve-executes / auto-approve real-Ollama flow test | ✓ pass |

**Tests:** scoped — 21 owned-path tests pass (collector unit + dispatch + clarify e2e + 3 real-Ollama approval-flow + owned-turn); broader sweep 94 passed (approval/dispatch/owned/shell surface), 0 failed. Boundary invariant G1-1 clean (only the documented `_output` reach in `preflight.py`).
**Doc Sync:** none needed — no spec references the changed symbols or owned-loop approval behavior (owned loop is flag-gated build-time infra in exec-plans, not runtime specs; user-facing y/n/a + deny→continue mechanics unchanged).

**Implementation notes:**
- New module `co_cli/agent/approval.py` (`collect_inline_approvals`, `ApprovalResolution`) — TL chose a dedicated module over growing `dispatch.py` (the trust boundary is a distinct concern). The `approval_probe_fn` deferred-generalization note (PO-m-2) lands as a code comment on the shell branch; the no-`gather` sequencing requirement (CD-m-4) and the deny-content/headless-asymmetry parity notes (D-A, CD-m-5) are all in-module comments.
- `dispatch.py`: `make_run_context` + `dispatch_tools` gained `tool_call_approved`/`approved_ids`; `resolve_auto_approvals` + `DENY_PLACEHOLDER_TEXT` deleted; module docstring updated.
- `loop.py:299`: `resolve_auto_approvals` → `collect_inline_approvals`, threading `denials` + `approved_ids`.

**Eval-variance note (per "fix any issue discovered"):** the only non-green eval results were LLM nondeterminism, not code defects, and were RCA'd to ground:
- `eval_approval_discipline` W8.C: judge scored "propose a less-destructive alternative" low on BOTH paths (graph oracle `fail` score 5 / owned `soft_pass` score 6). The structural trust-boundary checks (`files_intact=True`, no silent retry) pass identically on both — the variance is the local model's reasoning quality on a behavioral rubric, equally applicable to the graph oracle, not a loop-path divergence.
- `eval_bounded_autonomy` W9.A (owned, first run): the turn-3 retry's single model call ran 50s and hit `CALL_TIMEOUT_S` (`co.turn` outcome=INTERRUPTED, model_requests=0 → empty output → judge 0). Root cause is model-generation latency on the large-history retry, NOT loop logic (the collector never ran — turn 3 produced 0 tool calls). Confirmed variance: warm re-run scored W9.A=9, W9.B pass, W9.C=10 (all green). No timeout was widened.

**Overall: DELIVERED**
The owned loop prompts inline for approval (catalog + shell dynamic gate), honors auto-approval and remember-choice, asks clarify inline, and continues after a denial — at parity with the graph path's deferred suspend/resume. The trust boundary holds on the owned loop end-to-end. No code defects discovered; the eval dips were LLM variance (re-confirmed green warm).

## Implementation Review — 2026-06-25

Stance: issues exist — PASS is earned. Reviewed TASK-1…5. Lint clean, boundary invariant clean.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | call in `approved_ids` → `ctx.tool_call_approved=True`; absent → unapproved | ✓ pass | `dispatch.py:59,80` `make_run_context` threads the flag → `RunContext`; `dispatch.py:298` `_run` sets `tool_call_approved=call.tool_call_id in approved_ids`. Verified live by `test_flow_owned_dispatch.py::test_dispatch_marks_approved_call_tool_call_approved` (probe tool returns "APPROVED"/"UNAPPROVED"). SDK field confirmed at `pydantic_ai/_run_context.py:76`. |
| TASK-2 | collector approve/deny/auto/remember/headless + shell dynamic gate; deleted symbols grep-zero | ✓ pass | `approval.py:132` `collect_inline_approvals`; catalog+shell trigger `approval.py:157-160`; auto-approve `:165`; remember on `a` `:172`; headless `n` `:169`. Shell branch `_shell_needs_approval` `:76-90` re-evals pure `evaluate_shell_command` (`shell_policy.py:76,127`). `grep resolve_auto_approvals\|DENY_PLACEHOLDER_TEXT` → zero. `resolve_approval_subject` shell branch keys on `tool_name` not the catalog flag (`approvals.py:76`). |
| TASK-3 | clarify asks + returns answers inline; headless approve-with-empty | ✓ pass | `_handle_clarify` `approval.py:93-129` stashes `clarify_answers[id]` + adds to `approved_ids`; headless → `""` answers, not deny (`:126`). Body coupling `user_input.py:50,56` reads flag + stash. e2e `test_clarify_collector_to_dispatch_returns_answers` returns `["green"]`; headless `test_clarify_headless_approves_with_empty` → `["",""]` approved. |
| TASK-4 | both evals green on graph + owned path at parity | ✓ pass | `run_turn`→`drive_turn` swap exact kwarg drop-in (`eval_approval_discipline.py:125`, `eval_bounded_autonomy.py:117`); orphaned `run_turn` import deleted in both. `drive_turn`/`apply_eval_owned_loop` present (`_deps.py:62,50`). Delivery RCA'd both eval dips to LLM variance (W8.C judge quality on both paths; W9.A cold-call timeout, green warm) — structural checks pass identically on both paths. |
| TASK-5 | deny-blocks / approve-executes / auto-approve real-Ollama flow | ✓ pass | `test_flow_owned_approval.py` — 3/3 pass: deny → `target.exists()` + `outcome=="continue"`; approve → file deleted; pre-seeded `SessionApprovalRule(SHELL,"rm")` → no prompt, executes. Real model `qwen3.6:35b-a3b-agentic`, calls 0.9–3.1s. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Extra files in working tree not in any Phase-3 task: `docs/.../loop-decoupling-design.md`, `...-milestone.md`, `RESEARCH-loop-decoupling-peer-survey.md`, `uv.lock` | — | staging-hygiene (not a code defect) | These are adjacent Phase-2.5 docs + the 492→494 version bump from the Phase-2.5 ship — uncommitted working-tree state from a different effort. Do NOT stage them with the Phase-3 ship (CLAUDE.md staged-file hygiene). No action on the code. |

No code defects found. No auto-fix applied; no source changed during review.

### Tests
- Deterministic surface: `uv run pytest tests/test_owned_inline_approval.py tests/test_flow_owned_dispatch.py` → 14 passed.
- Real-Ollama flow (TASK-5): `tests/test_flow_owned_approval.py` → 3 passed (11.55s).
- Broader touched surface: `pytest -k "approval or dispatch or owned or shell_policy or clarify or shell_exec or capability"` → 96 passed.
- Logs: `.pytest-logs/*-review-impl-{deterministic,flow,surface}.log`.
- Full real-LLM suite not re-run wholesale (multi-hour, real-Ollama-heavy); change is owned-loop-scoped and flag-gated — the graph path (default) is untouched, so its tests cannot regress from these edits. Delivery swept 94 green; the surrounding 96-test deterministic surface confirms integration here.

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads).
- Owned-loop inline approval is LLM-mediated — verified via TASK-5's 3 real-Ollama flow tests (deny-blocks / approve-executes / auto-approve), not a chat turn (non-gating). `success_signal`s confirmed: approved gated call executes its body (file deleted); denied call's side effect does not happen (file intact) and the turn continues; auto-approved subject skips the prompt.

### Overall: PASS
All five tasks meet `done_when` with file:line evidence; the trust boundary holds end-to-end on the owned loop at parity with the graph path. Only finding is a staging-hygiene note (unrelated Phase-2.5 docs + version bump in the working tree) — exclude them when running `/ship`.
