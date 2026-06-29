"""UAT eval — Phase 5.5 TASK-1: the persona-mode selector A/B (eval-gated decision).

The delegation-interface peer survey found the named-agent / ``subagent_type``
selector is the single most convergent schema element co lacks (4/5 peers have
one; co + hermes are the lone anonymous-generalist camp —
``docs/reference/RESEARCH-delegation-interface-peer-survey.md`` §3). co already
ships the *prose* half: R1 (phase 3.7) carries delegation mode in the ``delegate``
tool description ("State whether the sub-agent should just research or also make
changes, and how to verify" — ``co_cli/tools/system/delegate.py``). So this eval
does NOT measure "structured mode vs nothing"; it measures the only delta that
justifies a new field for co's small model: does a **tuned persona-mode brief,
keyed by a cheap pick**, beat **the small orchestrator authoring mode-prose
unaided**?

The A/B (per scenario, grounded in the seeded ``multistep_research_baseline``
workspace — Helios context + the prior sqlite decision):

  Arm A (R1 prose baseline, NOT nothing — PO-m-2): the small model, shown the real
    ``delegate`` tool guidance (R1 prose) + the user ask, authors the delegated
    ``task`` string *unaided* (no hand-tuned prose). That model-authored task is
    driven through the production ``DELEGATE_AGENT_SPEC`` (base instructions only).

  Arm B (treatment): the same user ask stated plainly (mode stripped), driven
    through an EVAL-LOCAL ``TaskAgentSpec`` (``dataclasses.replace`` of the
    production spec — never a mutation of ``DELEGATE_AGENT_SPEC`` /
    ``_delegate_agent_instructions``, per CD-m-5 / ``feedback_no_eval_test_driven_api``)
    whose ``instructions`` inject the scenario-correct mode's tuned brief. Headline
    isolates *brief quality given a correct pick*.

Both arms drive the real ``run_standalone_owned`` directly (the first eval-layer
direct caller — daemons call ``run_standalone``), forked + ``propagate_approvals=True``
+ the parent frontend, matching the production delegate path
(``co_cli/agent/delegation.py`` — CD-m-3), so the A/B is apples-to-apples.

Readings recorded per run:
  - HEADLINE: pairwise B-vs-A (both orders, disagreement => tie — the documented
    position-bias cancellation in ``evals/_judge.judge_pairwise``).
  - DISQUALIFIER 1 (pick stability + correctness): repeat a combined
    author-task-and-pick call N times; does the model pick the scenario's natural
    mode reliably from the lean menu?
  - DISQUALIFIER 2 (semantic cost): does the picked mode conflict with the task the
    same call authored (a ``task`` / ``subagent_type`` mismatch)?
  - SURFACING (prefill): the token cost of the lean always-on menu, vs an
    affordability budget (enumerate-in-description is paid every turn).
  - OPTIONAL-vs-MANDATORY (no-fit decider, scenario P5.N): on a task fitting neither
    mode, does the model OMIT the field, and does FORCING a mode help/harm/tie vs the
    default? Settles selector cardinality on effectiveness.
  - OVERHEAD A/B (live decision, with-menu vs without-menu): the same delegation
    decision is driven through a real model request twice — once with the production
    `delegate` tool def (menu + `subagent_type`) and once with a control def that strips
    BOTH (the only delta). Isolates the marginal cost of the optional field's *presence*:
    Δ input tokens (the static prefill), Δ output tokens + Δ latency (reasoning overhead),
    and trigger fidelity (the menu must not suppress or distort the delegate decision) —
    proving the optional-with-default field both (a) does not add reasoning overhead beyond
    the small static prefill and (b) is used correctly when present (mode on fit, omit on
    no-fit). This is the with/without proof the dedicated classification call could not give.

The per-case verdict is the per-scenario headline (PASS = B wins or ties; SOFT_FAIL
= A wins — a legitimate measured no-go signal, recorded for review, not a crash).
A run-exception is the only hard FAIL. The go/no-go is then settled by a human from
the printed DECISION SUMMARY and recorded in the survey doc — this eval produces the
readings, it does not author the decision.

Mode set under test (eval-local fixtures — small, closed, co-native, distinctively
knowledge-work; explicitly NOT a generic researcher/editor/verifier coding menu —
``feedback_skill_curation_knowledge_work_positioning``): ``synthesis`` (distill
scattered sources -> condensed brief — co's core knowledge work) and ``critique``
(adversarially stress-test a claim/decision — the co critique/dream lineage). These
are the PO-m-1 illustrative anchors; the eval validates them.

Usage:
    uv run python evals/eval_delegate_persona_mode.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

from evals._deps import eval_deps
from evals._fixtures import load_fixture
from evals._judge import judge_model_annotation, judge_pairwise
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart

from co_cli.agent.delegation import DELEGATE_AGENT_SPEC
from co_cli.agent.loop import InstructionPart, _drive_model_request, run_standalone_owned
from co_cli.agent.preflight import build_request_params, build_tool_defs
from co_cli.context.tokens import estimate_text_tokens
from co_cli.deps import fork_deps
from co_cli.llm.call import llm_call
from co_cli.tools.deferred_prompt import build_deferred_tool_awareness_prompt
from co_cli.tools.system.delegate import delegate

_FIXTURE_NAME = "multistep_research_baseline"

# How many times to repeat the combined author-and-pick call per scenario to read
# pick stability. Small (UAT smoke): shows whether the pick is reliable, not a
# statistical distribution.
_PICK_REPEATS = 3

# The lean always-on selection menu (one when-to-use line per mode, claude-code
# `prompt.ts:43` shape). This is the string that would ride the `delegate`
# description on EVERY turn — its token cost is the surfacing prefill reading.
# Affordable inline iff the small closed set stays cheap (else surfacing forces a
# deferred discovery tool — survey :130). ~100 tokens added to one always-on tool
# description is negligible against the multi-thousand-token static prompt; the
# deferred-discovery alternative is only warranted if the set grows large.
_PREFILL_BUDGET_TOKENS = 100


@dataclass(frozen=True)
class _PersonaMode:
    """An eval-local persona-mode fixture: the lean menu line + the rich on-use brief.

    ``when_to_use`` is the always-on menu line (prefill-paid). ``brief`` is the rich
    persona instruction injected into the delegated agent's ``instructions`` ONLY on
    the turn the mode is used (the two-tier surface — lean menu / rich on-use brief).
    """

    name: str
    when_to_use: str
    brief: str


# Small, closed, co-native set. Knowledge-work personas, NOT a coding-shop menu.
_MODES: dict[str, _PersonaMode] = {
    "synthesis": _PersonaMode(
        name="synthesis",
        when_to_use=(
            "gather scattered sources and distill them into one condensed, decision-ready brief"
        ),
        brief=(
            "Work as a synthesis specialist. Read widely across the sources available "
            "to you, reconcile what they say, and condense it into a tight, "
            "decision-ready brief. Attribute each material claim to the source it came "
            "from, and never invent a detail that no source supports — if the sources "
            "disagree or leave a gap, say so plainly rather than papering over it. "
            "Favour the few load-bearing facts a decision actually turns on over an "
            "exhaustive recap."
        ),
    ),
    "critique": _PersonaMode(
        name="critique",
        when_to_use=(
            "stress-test a claim, decision, or artifact and surface its weakest "
            "points and failure modes"
        ),
        brief=(
            "Work as an adversarial reviewer. Your job is to stress-test the claim, "
            "decision, or artifact in front of you — not to agree with it. Surface the "
            "strongest objections, the assumptions that could be wrong, and the "
            "concrete conditions under which it fails. Separate fatal flaws from minor "
            "nits, ground every objection in the actual sources rather than generic "
            "caution, and close on the single most important risk the reader must weigh."
        ),
    ),
}


def _lean_menu() -> str:
    """The lean always-on selection menu — one when-to-use line per mode."""
    lines = ["Optionally set `subagent_type` to focus the sub-agent's mode:"]
    lines += [f"- {m.name}: {m.when_to_use}" for m in _MODES.values()]
    lines.append("Omit it to use the default general sub-agent.")
    return "\n".join(lines)


# The R1 prose the orchestrator reads when authoring a delegated task today —
# sourced from the live `delegate` tool docstring so Arm A's baseline never drifts
# from shipped behavior.
_R1_GUIDANCE = (delegate.__doc__ or "").strip()


@dataclass(frozen=True)
class _Scenario:
    """One knowledge-work A/B scenario grounded in the seeded fixture.

    ``user_ask`` is what the orchestrator sees (Arm A authors a delegated task from
    it). ``plain_task`` is the same work stated with NO mode-prose (Arm B's input;
    the mode lives in the injected brief instead). ``intended_mode`` is the mode the
    scenario naturally calls for — the pick-correctness target.
    """

    case_id: str
    intended_mode: str
    user_ask: str
    plain_task: str
    stance: str


_SCENARIOS: list[_Scenario] = [
    _Scenario(
        case_id="P5.S",
        intended_mode="synthesis",
        user_ask=(
            "I need a single condensed brief on where project Helios's datastore "
            "stands and why. The background is in the 'project_helios_context' and "
            "'decision_use_sqlite' memory artifacts. Hand this off to a sub-agent."
        ),
        plain_task=(
            "Read the 'project_helios_context' and 'decision_use_sqlite' memory "
            "artifacts, then produce a single condensed brief on where project "
            "Helios's datastore stands and the reasoning behind it."
        ),
        stance=(
            "A tight, decision-ready brief that faithfully distills BOTH seeded "
            "sources (Helios's workload/storage and the prior sqlite decision plus "
            "its revisit threshold) into the few load-bearing facts, attributes "
            "claims to their source, and invents no detail."
        ),
    ),
    _Scenario(
        case_id="P5.C",
        intended_mode="critique",
        user_ask=(
            "I want the prior decision to stay on sqlite for Helios stress-tested — "
            "where is it weakest and what would force a change? The decision is in the "
            "'decision_use_sqlite' memory artifact (Helios's workload is in "
            "'project_helios_context'). Hand this off to a sub-agent."
        ),
        plain_task=(
            "Read the 'decision_use_sqlite' memory artifact (and "
            "'project_helios_context' for Helios's workload), then stress-test the "
            "decision to stay on sqlite: surface its weakest points, the assumptions "
            "that could be wrong, and the concrete conditions that would force a change."
        ),
        stance=(
            "An adversarial stress-test that surfaces the decision's weakest points "
            "and concrete failure conditions grounded in the seeded sources (e.g. the "
            "workload threshold that triggers revisiting sqlite), separates fatal from "
            "minor, and closes on the most important risk — not a neutral summary."
        ),
    ),
]


_AUTHOR_PICK_SYSTEM = """You are the main agent deciding how to hand a subtask to a \
sub-agent via the `delegate` tool. You are given the tool's own guidance, a selection \
menu of optional sub-agent modes, and a user request. Do two things and return ONE \
compact JSON object on a single line:

  {"task": "<the self-contained task string you would pass to delegate>",
   "mode": "<synthesis|critique|none>"}

Rules:
- The `task` must be self-contained (the sub-agent cannot see this conversation) and
  follow the delegate guidance, including stating whether the sub-agent should research
  or also make changes and how to verify.
- `mode` is your pick from the menu (or "none" for the default general sub-agent).
- Output nothing else — no preamble, no markdown, no commentary.
"""

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class _AuthorPick:
    """One author-and-pick result: the authored Arm A task + the model's mode pick."""

    task: str
    mode: str


def _parse_author_pick(raw: str) -> _AuthorPick:
    """Pull a single ``{task, mode}`` object; coerce mode to a known mode or 'none'."""
    match = _JSON_OBJ_RE.search(raw.strip())
    if match:
        try:
            data = json.loads(match.group(0))
            mode = str(data.get("mode", "none")).strip().lower()
            if mode not in _MODES:
                mode = "none"
            task = str(data.get("task", "")).strip()
            if task:
                return _AuthorPick(task=task, mode=mode)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return _AuthorPick(task="", mode="none")


async def _author_and_pick(deps: Any, scenario: _Scenario) -> _AuthorPick:
    """One real-model author-and-pick call: Arm A's task + the orchestrator's mode pick."""
    prompt = (
        f"DELEGATE TOOL GUIDANCE:\n{_R1_GUIDANCE}\n\n"
        f"MODE MENU:\n{_lean_menu()}\n\n"
        f"USER REQUEST:\n{scenario.user_ask}\n\n"
        "Return JSON now."
    )
    async with asyncio.timeout(CALL_TIMEOUT_S):
        raw = await llm_call(deps, prompt, instructions=_AUTHOR_PICK_SYSTEM)
    return _parse_author_pick(raw)


def _arm_b_spec(mode: _PersonaMode) -> Any:
    """An EVAL-LOCAL spec: the production delegate spec with the mode brief injected.

    ``dataclasses.replace`` returns a NEW frozen ``TaskAgentSpec`` — it never mutates
    ``DELEGATE_AGENT_SPEC`` nor touches ``_delegate_agent_instructions`` (CD-m-5). The
    eval-local ``instructions`` builder reproduces the production base brief verbatim
    (an eval fixture mirroring ``delegation.py`` — kept frozen here so the A/B does not
    drift if the production briefs change) and composes the mode brief BEFORE the live
    deferred-tool stubs, so the stub block is preserved, not displaced (CD-m-2)."""

    def build(deps: Any) -> str:
        base = (
            "You are a focused agent handling one delegated subtask for the main agent. "
            "You have the same full tool surface as the main agent: read and search, and act — "
            "run commands, write and patch files, and the rest. Some tools are not loaded up "
            "front; to use one, pass its exact name to tool_view to load it, then call it. "
            "Sensitive actions are gated by user approval; if an action is denied, adapt and "
            "continue with what you can do. When done, call the final_result tool with a single "
            "concise `summary` that distills the outcome into what the main agent needs to "
            "continue. You have no user channel — do not ask questions. Keep the summary "
            "self-contained: the main agent sees only your summary, never your intermediate "
            "tool calls or their results."
        )
        persona = f"For this subtask, adopt a specific working mode. {mode.brief}"
        head = f"{base}\n\n{persona}"
        stubs = build_deferred_tool_awareness_prompt(
            deps.tool_catalog, deps.runtime.revealed_tools
        )
        if stubs:
            return f"{head}\n\n{stubs}"
        return head

    return dataclasses.replace(DELEGATE_AGENT_SPEC, instructions=build)


async def _drive_arm(deps: Any, frontend: Any, spec: Any, task: str) -> str:
    """Drive one arm through the real owned delegate path; return the delegated summary.

    Mirrors ``delegate_to_agent`` (``delegation.py``): fork with its own dispatch
    semaphore, ``propagate_approvals=True``, the parent frontend, the parent model
    settings — so both arms exercise the production drive (CD-m-3)."""
    agent_deps = fork_deps(deps, share_dispatch_sem=False)
    async with asyncio.timeout(CALL_TIMEOUT_S * 4):
        result = await run_standalone_owned(
            spec,
            agent_deps,
            task,
            settings=deps.model.settings,
            propagate_approvals=True,
            frontend=frontend,
        )
    if result is None:
        return "(no result — delegated agent exhausted its budget)"
    return result.summary


async def _pairwise_both_orders(
    deps: Any, stance: str, summary_a: str, summary_b: str
) -> tuple[str, str]:
    """B-vs-A pairwise in both orders; collapse disagreement to a tie.

    Returns ``(outcome, detail)`` where outcome ∈ {"B", "A", "tie"} from B's
    perspective. Running both orders cancels position bias (``judge_pairwise``)."""
    # Order 1: A=summary_a, B=summary_b → "B" means our Arm B won.
    v1 = await judge_pairwise(stance, summary_a, summary_b, deps=deps, model=deps.judge_model)
    # Order 2: A=summary_b, B=summary_a → "A" means our Arm B won.
    v2 = await judge_pairwise(stance, summary_b, summary_a, deps=deps, model=deps.judge_model)
    b_won_1 = v1.winner == "B"
    b_won_2 = v2.winner == "A"
    a_won_1 = v1.winner == "A"
    a_won_2 = v2.winner == "B"
    detail = f"order1={v1.winner} order2={v2.winner}"
    if b_won_1 and b_won_2:
        return "B", detail
    if a_won_1 and a_won_2:
        return "A", detail
    return "tie", detail


async def _run_scenario(deps: Any, frontend: Any, scenario: _Scenario) -> CaseResult:
    """Run one scenario's full A/B + disqualifier readings → a CaseResult."""
    case_t0 = time.monotonic()
    reason_parts: list[str] = [judge_model_annotation(deps), f"mode={scenario.intended_mode}"]
    case_verdict = Verdict.FAIL

    try:
        load_fixture(_FIXTURE_NAME, deps)

        # Disqualifiers: repeat the combined author-and-pick call. Run 0's task feeds
        # Arm A; all runs' picks feed pick stability + correctness; task-vs-pick
        # agreement on each run is the semantic-cost reading.
        author_picks: list[_AuthorPick] = []
        for _ in range(_PICK_REPEATS):
            author_picks.append(await _author_and_pick(deps, scenario))
        picks = [ap.mode for ap in author_picks]
        pick_correct = sum(1 for p in picks if p == scenario.intended_mode)
        pick_stable = len(set(picks)) == 1
        reason_parts.append(
            f"picks={picks} correct={pick_correct}/{_PICK_REPEATS} stable={pick_stable}"
        )

        arm_a_task = author_picks[0].task or scenario.plain_task

        # HEADLINE: Arm A = production spec + model-authored R1-laden task; Arm B =
        # plain task + scenario-correct mode brief (eval-selected to isolate brief
        # quality from pick quality — pick quality is the disqualifier above).
        summary_a = await _drive_arm(deps, frontend, DELEGATE_AGENT_SPEC, arm_a_task)
        spec_b = _arm_b_spec(_MODES[scenario.intended_mode])
        summary_b = await _drive_arm(deps, frontend, spec_b, scenario.plain_task)
        reason_parts.append(f"len(A)={len(summary_a)} len(B)={len(summary_b)}")

        outcome, judge_detail = await _pairwise_both_orders(
            deps, scenario.stance, summary_a, summary_b
        )
        reason_parts.append(f"headline=B_{outcome} ({judge_detail})")

        # PASS = the structured brief won or held even (the field helps / does not
        # hurt on this scenario). SOFT_FAIL = the unaided R1 prose won — a legitimate
        # measured no-go signal for this scenario, recorded for review (not a crash).
        if outcome in ("B", "tie"):
            case_verdict = Verdict.PASS
        else:
            case_verdict = Verdict.SOFT_FAIL
            reason_parts.append("R1_prose_won")
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=scenario.case_id,
        verdict=case_verdict,
        duration_s=duration,
        reason=" ".join(reason_parts).strip(),
    )


# A NO-FIT scenario: a mechanical read→write→verify task that matches NEITHER mode.
# It is the decisive case for optional-vs-mandatory: optional lets the model omit the
# field; mandatory would force a synthesis/critique brief onto clerical work. Grounded in
# the seeded workspace (operates on a real seeded artifact), per eval-real-use-case rule.
_NOFIT_SCENARIO = _Scenario(
    case_id="P5.N",
    intended_mode="none",
    user_ask=(
        "Hand this off to a sub-agent: read the 'decision_use_sqlite' memory artifact, write "
        "its full contents verbatim into a new workspace file named 'decision_backup.md', then "
        "read that file back and confirm its line count matches the original."
    ),
    plain_task=(
        "Read the 'decision_use_sqlite' memory artifact, write its full contents verbatim into "
        "a new workspace file named 'decision_backup.md', then read that file back and confirm "
        "the line count matches the original. Report the line count and whether they match."
    ),
    stance=(
        "Correctly and cleanly completes the mechanical task — copies the artifact verbatim to "
        "the named file and verifies the line count — and reports the outcome plainly. It must "
        "NOT impose synthesis (distilling/condensing the content) or critique (stress-testing "
        "the decision) framing the task never asked for; that framing is a defect here."
    ),
)


async def _run_nofit_decision(deps: Any, frontend: Any) -> CaseResult:
    """The optional-vs-mandatory decider, on a task that fits NEITHER mode.

    Two readings, both on effectiveness (not safety):
      (1) PICK DISCIPLINE — under the optional menu, does the model OMIT the field
          (pick 'none') when nothing fits? A spurious pick is a mis-fire.
      (2) FORCING HARM — drive the same no-fit task three ways: default (no mode),
          forced synthesis, forced critique; pairwise each forced arm vs default on the
          no-fit stance. If default ties-or-beats both, forcing a mode does not help and
          may hurt — so mandatory would degrade the no-fit slice of the task distribution.

    Verdict: PASS when optional is justified on effectiveness — the model omits on no-fit
    AND/OR forcing degrades-or-doesn't-help (so optional routes modes to fit-cases and the
    default to no-fit, while mandatory could only match or harm). SOFT_FAIL flags the one
    complicating outcome: the model mis-fires (spurious pick) AND forcing harms — optional
    then needs a mitigation (tighter menu, or an explicit no-mode signal).
    """
    case_t0 = time.monotonic()
    reason_parts: list[str] = [judge_model_annotation(deps), "nofit"]
    case_verdict = Verdict.FAIL

    try:
        load_fixture(_FIXTURE_NAME, deps)

        # (1) pick discipline — does the model omit when nothing fits?
        picks = [
            (await _author_and_pick(deps, _NOFIT_SCENARIO)).mode for _ in range(_PICK_REPEATS)
        ]
        omits = sum(1 for p in picks if p == "none")
        pick_omit = omits == _PICK_REPEATS
        reason_parts.append(f"picks={picks} omit={omits}/{_PICK_REPEATS}")

        # (2) forcing harm — default vs each forced mode on the no-fit task.
        plain = _NOFIT_SCENARIO.plain_task
        default_summary = await _drive_arm(deps, frontend, DELEGATE_AGENT_SPEC, plain)
        forced: dict[str, str] = {}
        for name in _MODES:
            forced[name] = await _drive_arm(deps, frontend, _arm_b_spec(_MODES[name]), plain)

        # outcome from the FORCED arm's perspective vs default (B=forced, A=default).
        harm_flags: list[str] = []
        forcing_helps = False
        for name, summary in forced.items():
            outcome, detail = await _pairwise_both_orders(
                deps, _NOFIT_SCENARIO.stance, default_summary, summary
            )
            reason_parts.append(f"{name}_vs_default=forced_{outcome} ({detail})")
            if outcome == "A":
                harm_flags.append(name)
            elif outcome == "B":
                forcing_helps = True
        forcing_harms = bool(harm_flags)

        # Effectiveness verdict.
        if pick_omit and not forcing_helps:
            case_verdict = Verdict.PASS
            reason_parts.append("optional_justified: omits on no-fit; forcing adds nothing")
        elif not pick_omit and forcing_harms:
            case_verdict = Verdict.SOFT_FAIL
            reason_parts.append(
                "optional_risk: spurious pick AND forcing harms — needs mitigation"
            )
        else:
            case_verdict = Verdict.PASS
            reason_parts.append("optional_holds: routing+forcing readings do not favour mandatory")
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=_NOFIT_SCENARIO.case_id,
        verdict=case_verdict,
        duration_s=duration,
        reason=" ".join(reason_parts).strip(),
    )


# A fixed, controlled orchestrator instruction for the overhead A/B — identical across
# both conditions so the ONLY delta is the delegate tool def (with vs without the menu).
_OVERHEAD_INSTRUCTION = (
    "You are the main agent. When a subtask is multi-step, hand it to a sub-agent with "
    "the delegate tool; do small one-shot actions yourself."
)


def _nomenu_delegate_def(with_def: Any) -> Any:
    """The control tool def: the production `delegate` def with the persona-mode menu AND
    the `subagent_type` param stripped — the only delta from `with_def`, so the A/B
    isolates the field's marginal cost. The menu is the description's trailing paragraph
    (Args is excluded from `.description`), so it is cut from its marker to the end."""
    desc = with_def.description or ""
    marker = "Optionally set `subagent_type`"
    if marker in desc:
        desc = desc[: desc.index(marker)].rstrip()
    schema = dict(with_def.parameters_json_schema or {})
    props = dict(schema.get("properties", {}))
    props.pop("subagent_type", None)
    schema["properties"] = props
    return dataclasses.replace(with_def, description=desc, parameters_json_schema=schema)


def _pick_from_calls(calls: list[ToolCallPart]) -> tuple[bool, str | None]:
    """From a response's tool calls, return (delegated?, subagent_type-or-None)."""
    for c in calls:
        if c.tool_name != "delegate":
            continue
        args = c.args
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}
        mode = args.get("subagent_type") if isinstance(args, dict) else None
        return True, (mode or None)
    return False, None


async def _one_decision(
    deps: Any, user_prompt: str, tool_def: Any
) -> tuple[bool, str | None, int, float]:
    """Drive ONE real model request (orchestrator instruction + a delegation-worthy ask)
    with a single function tool; return (delegated?, picked-mode, output_tokens, latency_s).

    Faithful live decision: real model, real `_drive_model_request`, the production tool
    def (or its menu-stripped control) as the only function tool, text output allowed (the
    model may decline to delegate). This is the with/without comparison the dedicated
    classification call (`_author_and_pick`) could not provide."""
    params = build_request_params(
        instruction_parts=[InstructionPart(content=_OVERHEAD_INSTRUCTION, dynamic=False)],
        function_tools=[tool_def],
        output_tools=None,
        allow_text_output=True,
    )
    history = [ModelRequest(parts=[UserPromptPart(content=user_prompt)])]
    stall = deps.config.llm.run_stall_timeout_secs
    t0 = time.monotonic()
    async with asyncio.timeout(CALL_TIMEOUT_S):
        response, usage = await _drive_model_request(
            deps, history, params, deps.model.settings, None, stall
        )
    latency = time.monotonic() - t0
    calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
    delegated, mode = _pick_from_calls(calls)
    return delegated, mode, int(usage.output_tokens or 0), latency


async def _cell(deps: Any, user_prompt: str, tool_def: Any) -> dict[str, Any]:
    """Average one (probe, condition) cell over ``_PICK_REPEATS`` real requests."""
    runs = [await _one_decision(deps, user_prompt, tool_def) for _ in range(_PICK_REPEATS)]
    delegated = sum(1 for d, _, _, _ in runs if d)
    modes = [m for _, m, _, _ in runs]
    mean_out = sum(o for _, _, o, _ in runs) / len(runs)
    mean_lat = sum(t for _, _, _, t in runs) / len(runs)
    return {"delegated": delegated, "modes": modes, "mean_out": mean_out, "mean_lat": mean_lat}


async def _run_overhead_ab(deps: Any) -> CaseResult:
    """The with-menu vs without-menu overhead proof, on the real delegation decision.

    Two probes (a critique-fit ask and the no-fit ask), each driven both ways. Proves the
    optional field BOTH (a) adds no reasoning overhead beyond the small static prefill —
    Δ output tokens / Δ latency ≈ 0 and trigger fidelity holds (the menu neither suppresses
    nor spuriously induces delegation) — AND (b) is used correctly when present: the model
    sets the right mode on the fit probe and omits it on the no-fit probe.
    """
    case_t0 = time.monotonic()
    reason_parts: list[str] = ["overhead_ab"]
    case_verdict = Verdict.FAIL

    try:
        load_fixture(_FIXTURE_NAME, deps)
        with_def = next(d for d in await build_tool_defs(deps) if d.name == "delegate")
        nomenu_def = _nomenu_delegate_def(with_def)
        prefill_delta = estimate_text_tokens(with_def.description or "") - estimate_text_tokens(
            nomenu_def.description or ""
        )

        probes = [
            ("fit", _SCENARIOS[1].user_ask, "critique"),
            ("nofit", _NOFIT_SCENARIO.user_ask, None),
        ]
        fidelity_ok = True
        usage_ok = True
        for label, prompt, want_mode in probes:
            wm = await _cell(deps, prompt, with_def)
            wo = await _cell(deps, prompt, nomenu_def)
            d_out = wm["mean_out"] - wo["mean_out"]
            d_lat = wm["mean_lat"] - wo["mean_lat"]
            # Trigger fidelity (GATED): the menu must not suppress delegation vs the
            # control — the field's presence must not break the core delegate decision.
            fidelity = wm["delegated"] >= wo["delegated"]
            # Correct use when present (GATED): sets the right mode on fit, omits on no-fit.
            if want_mode is None:
                use_ok = all(m is None for m in wm["modes"])
            else:
                use_ok = all(m == want_mode for m in wm["modes"])
            # Generation/latency overhead (REPORTED, not gated): d_out/d_lat on 3 noisy
            # repeats conflate field-emission, task-authoring variance, and run noise — a
            # human reads the magnitude, the eval does not pretend to threshold it.
            fidelity_ok = fidelity_ok and fidelity
            usage_ok = usage_ok and use_ok
            reason_parts.append(
                f"{label}: deleg with/wo={wm['delegated']}/{wo['delegated']} "
                f"d_out={d_out:+.0f}tok d_lat={d_lat:+.1f}s modes_with={wm['modes']} use_ok={use_ok}"
            )
        reason_parts.append(f"prefill_delta≈{prefill_delta}tok (the only clean overhead isolate)")

        if fidelity_ok and usage_ok:
            case_verdict = Verdict.PASS
            reason_parts.append(
                "field presence preserves the delegate decision + is used correctly; "
                "generation overhead is REPORTED above (not free — read d_out/d_lat)"
            )
        else:
            case_verdict = Verdict.SOFT_FAIL
            reason_parts.append(
                f"signal: fidelity_ok={fidelity_ok} use_ok={usage_ok} — review (d_out/d_lat above)"
            )
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name="P5.O",
        verdict=case_verdict,
        duration_s=duration,
        reason=" ".join(reason_parts).strip(),
    )


def _print_decision_summary(cases: list[CaseResult], prefill_tokens: int) -> None:
    """Print the human-readable DECISION SUMMARY the go/no-go is settled from."""
    print("\n" + "=" * 72)
    print("DECISION SUMMARY — persona-mode selector (Phase 5.5 TASK-1)")
    print("=" * 72)
    for c in cases:
        print(f"  [{c.name}] {c.verdict.name}: {c.reason}")
    print(
        f"  SURFACING: lean menu prefill ≈ {prefill_tokens} tokens "
        f"(budget {_PREFILL_BUDGET_TOKENS}; "
        f"{'affordable inline' if prefill_tokens <= _PREFILL_BUDGET_TOKENS else 'OVER — prefer deferred discovery tool'})"
    )
    headline_b = sum(1 for c in cases if "headline=B_B" in c.reason)
    headline_tie = sum(1 for c in cases if "headline=B_tie" in c.reason)
    headline_a = sum(1 for c in cases if "headline=B_A" in c.reason)
    print(f"  HEADLINE tally — B wins: {headline_b}  ties: {headline_tie}  A wins: {headline_a}")
    nofit = next((c for c in cases if c.name == _NOFIT_SCENARIO.case_id), None)
    if nofit is not None:
        print(f"  OPTIONAL-vs-MANDATORY (no-fit decider): {nofit.verdict.name} — {nofit.reason}")
    overhead = next((c for c in cases if c.name == "P5.O"), None)
    if overhead is not None:
        print(f"  OVERHEAD (with-menu vs without): {overhead.verdict.name} — {overhead.reason}")
    print("=" * 72 + "\n")


async def main() -> int:
    """Drive the persona-mode A/B end-to-end, print the decision summary, return exit code."""
    await ensure_ollama_warm()

    prefill_tokens = estimate_text_tokens(_lean_menu())

    async with eval_deps() as (deps, frontend), open_eval_run("delegate_persona_mode") as run:
        apply_eval_window(deps)
        cases: list[CaseResult] = []
        for scenario in _SCENARIOS:
            case = await _run_scenario(deps, frontend, scenario)
            run.append(case)
            print(f"[delegate_persona_mode] {case.name}: {case.verdict.name} — {case.reason}")
            cases.append(case)

        nofit_case = await _run_nofit_decision(deps, frontend)
        run.append(nofit_case)
        print(
            f"[delegate_persona_mode] {nofit_case.name}: {nofit_case.verdict.name} — {nofit_case.reason}"
        )
        cases.append(nofit_case)

        overhead_case = await _run_overhead_ab(deps)
        run.append(overhead_case)
        print(
            f"[delegate_persona_mode] {overhead_case.name}: "
            f"{overhead_case.verdict.name} — {overhead_case.reason}"
        )
        cases.append(overhead_case)

    _print_decision_summary(cases, prefill_tokens)
    return 0 if all(c.verdict != Verdict.FAIL for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
