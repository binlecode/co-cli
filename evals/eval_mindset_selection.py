"""Ablation eval — does the always-on ``## Mindsets`` block earn its real estate?

This is a *diagnostic*, not a pass/fail gate. It runs the same user prompts through
prompt arms that differ ONLY in the mindsets block and compares responses pairwise
(``judge_pairwise``), so the verdict is a preference win-rate, not an absolute score.

Arms (everything else — seed, rules, toolset, capabilities — identical):
  A0 — none:          seed + rules, no ``## Mindsets`` block.
  A1 — all-six:       current production prompt (``load_soul_mindsets``).
  A2 — relevant-only: seed + rules + only the mindset(s) matching the case's shape.
  A3 — wrong-only:    seed + rules + a single plausible-but-wrong mindset.

Arms are composed in the eval layer by swapping the orchestrator spec's first
static-instruction builder for an arm-specific one that emits
``seed + <arm mindsets> + build_rules_block()``; the rest of the spec (toolset
guidance, skill manifest, critique, per-turn safety/time, history processors) is
reused via ``build_orchestrator``. No production toggle is added (per
``feedback_no_eval_test_driven_api``); ``build_rules_block`` is the one production
seam (assembly.py), used by production too.

Arm-pairs and what each settles:
  Pair 1 — A1 vs A0: do mindsets change behavior vs. their absence? (the ROI question)
  Pair 2 — A1 vs A2: does flat all-six load dilute the relevant one? (distraction)
  Pair 3 — A2 vs A3: is the *specific* content steering, or would any prose do?
                     (run only when Pair 2 shows A2 > A1 — the pre-authoring gate)

Per-case winner: the two order-swapped judgments must agree, else the case is a tie.
Arm "reliably preferred": wins a clear majority of decisive (non-tie) cases, and the
decisive cases must themselves be a majority. This rule decides every branch so the
decision-tree outcome is reproducible, not a judgment call.

Judge is pinned to ``deps.judge_model`` (records ``[judge_model=...]`` /
``[judge_model_same_as_agent]``). Real everything (``feedback_eval_real_world_data``):
real ``make_eval_deps``, config ``llm.host``, real model; ``ensure_ollama_warm``
called outside any ``asyncio.timeout``. No caps, no test stores.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from evals._deps import EvalFrontend, make_eval_deps
from evals._judge import judge_model_annotation, judge_pairwise
from evals._observability import CaseResult, EvalRun, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._timeouts import CALL_TIMEOUT_S
from evals._trace import record_turn
from pydantic_ai.messages import ModelResponse, TextPart

from co_cli.agent.build import build_orchestrator
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.context.assembly import build_rules_block
from co_cli.deps import CoDeps
from co_cli.personality.prompts import loader as _loader_mod
from co_cli.personality.prompts.loader import load_soul_mindsets, load_soul_seed

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-mindset-selection.md"

_ROLE = "tars"


@dataclass(frozen=True)
class Case:
    """One ablation case: a prompt, the shape(s) it should trigger, and a stance rubric.

    ``task_types`` are the mindset file stems matching the case (one for singles,
    two for composites) — used to build the A2 (relevant-only) block.
    ``wrong_type`` is a plausible-but-wrong stem for the A3 block.
    ``target_stance`` is the behavioral rubric the pairwise judge scores against.
    """

    case_id: str
    prompt: str
    task_types: tuple[str, ...]
    wrong_type: str
    target_stance: str


CASES: tuple[Case, ...] = (
    Case(
        case_id="technical",
        prompt="Add a --dry-run flag to the deploy script so it prints the actions it would take without executing them.",
        task_types=("technical",),
        wrong_type="emotional",
        target_stance=(
            "Treats it as doing the work: moves toward implementing/operating concretely, "
            "proposes the concrete change, and does not pad with theory or hand-holding."
        ),
    ),
    Case(
        case_id="exploration",
        prompt="I'm not sure how our caching layer actually fits together. Where should I even start looking?",
        task_types=("exploration",),
        wrong_type="debugging",
        target_stance=(
            "Maps unknown territory before committing: orients, surveys the structure, and "
            "proposes an investigation path rather than jumping straight to a fix."
        ),
    ),
    Case(
        case_id="debugging",
        prompt="The login endpoint returns a 500 intermittently and I can't tell why.",
        task_types=("debugging",),
        wrong_type="teaching",
        target_stance=(
            "Commits a hypothesis then verifies it; diagnoses the root cause before proposing "
            "fixes; does not patch symptoms or guess scattershot."
        ),
    ),
    Case(
        case_id="teaching",
        prompt="Can you explain how Python's asyncio event loop actually works?",
        task_types=("teaching",),
        wrong_type="technical",
        target_stance=(
            "Explains to build understanding: structures the concept and grounds it, rather "
            "than dumping code or terse facts."
        ),
    ),
    Case(
        case_id="emotional",
        prompt="I've been stuck on this bug for hours and I'm losing it. Nothing I try works.",
        task_types=("emotional",),
        wrong_type="memory",
        target_stance=(
            "Acknowledges the frustration before action — steadies first, then offers a "
            "concrete next step rather than ignoring the emotional state."
        ),
    ),
    Case(
        case_id="memory",
        prompt="Remember that I always want PRs squash-merged, never merge commits.",
        task_types=("memory",),
        wrong_type="exploration",
        target_stance=(
            "Treats it as recording/reconciling a durable preference: confirms what will be "
            "remembered and records it, not just a passing acknowledgment."
        ),
    ),
    Case(
        case_id="debug+teach",
        prompt="Walk me through why this recursion blows the stack, and explain it so I actually understand the underlying cause.",
        task_types=("debugging", "teaching"),
        wrong_type="emotional",
        target_stance=(
            "Both diagnoses the failure cause (committed hypothesis, root cause) AND explains "
            "it pedagogically so the user understands — not one at the expense of the other."
        ),
    ),
    Case(
        case_id="technical+emotional",
        prompt="I'm overwhelmed — please just help me get this migration script written and working.",
        task_types=("technical", "emotional"),
        wrong_type="exploration",
        target_stance=(
            "Briefly acknowledges the overwhelm, then leads with concrete implementation help "
            "— both steadying and doing the work, not pure reassurance or pure code-dump."
        ),
    ),
)


def _response_text(messages) -> str:
    """Concatenate every assistant ``TextPart`` from a TurnResult.messages list."""
    out: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    out.append(part.content)
    return " ".join(out)


def _mindset_block(role: str, task_types: tuple[str, ...]) -> str:
    """Build a ``## Mindsets`` block for a subset of task types (A2/A3 arms).

    Reads the same doctrine files the production loader reads and wraps them in
    the identical ``"## Mindsets\\n\\n" + join`` format, so an arm differs from
    production only in *which* mindsets are present.
    """
    mindsets_dir = Path(_loader_mod.__file__).parent / "souls" / role / "mindsets"
    parts: list[str] = []
    for task_type in task_types:
        mindset_file = mindsets_dir / f"{task_type}.md"
        content = mindset_file.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
    if not parts:
        return ""
    return "## Mindsets\n\n" + "\n\n".join(parts)


def _arm_agent(deps: CoDeps, role: str, mindsets_block: str):
    """Build a real orchestrator agent whose only deviation is the mindsets block.

    Replaces the spec's first static-instruction builder (the seed+mindsets+rules
    provider) with an arm-specific one emitting ``seed + <mindsets_block> + rules``;
    all other builders and the full agent wiring are reused via ``build_orchestrator``.
    """

    def _arm_static_provider(_deps: CoDeps) -> str:
        parts = [load_soul_seed(role)]
        if mindsets_block:
            parts.append(mindsets_block)
        parts.append(build_rules_block())
        return "\n\n".join(parts)

    arm_spec = dataclasses.replace(
        ORCHESTRATOR_SPEC,
        static_instruction_builders=(
            _arm_static_provider,
            *ORCHESTRATOR_SPEC.static_instruction_builders[1:],
        ),
    )
    return build_orchestrator(arm_spec, deps)


async def _arm_response(
    deps: CoDeps,
    frontend: EvalFrontend,
    role: str,
    mindsets_block: str,
    case: Case,
    arm_label: str,
    run: EvalRun,
) -> str:
    """Drive one real turn under an arm; return the response text.

    Per-turn timing/usage is captured by ``record_turn`` into the case trace
    JSONL (``co tail`` / ``co trace`` to inspect) — not returned here, since the
    recorded results are per-arm-pair aggregates with no per-response slot.
    """
    from co_cli.context.orchestrate import run_turn

    agent = _arm_agent(deps, role, mindsets_block)
    trace_file = run.case_trace_path(f"{case.case_id}_{arm_label}")
    trace_file.touch(exist_ok=True)
    async with asyncio.timeout(CALL_TIMEOUT_S):
        turn_result, _ = await record_turn(
            case_id=f"{case.case_id}_{arm_label}",
            turn_index=0,
            user_input=case.prompt,
            run_turn_callable=lambda: run_turn(
                agent=agent,
                user_input=case.prompt,
                deps=deps,
                message_history=[],
                frontend=frontend,
            ),
            case_dir_path=trace_file,
            agent=agent,
        )
    return _response_text(turn_result.messages)


async def _case_winner(
    deps: CoDeps,
    target_stance: str,
    resp_x: str,
    resp_y: str,
) -> str:
    """Order-swapped pairwise reconciliation. Returns 'X', 'Y', or 'tie'.

    Runs (X as A, Y as B) and (Y as A, X as B). X wins only if both runs agree X
    is better; Y wins only if both agree Y; any disagreement is a tie (the
    preference was position-sensitive, i.e. not real).
    """
    v1 = await judge_pairwise(target_stance, resp_x, resp_y, deps=deps, model=deps.judge_model)
    v2 = await judge_pairwise(target_stance, resp_y, resp_x, deps=deps, model=deps.judge_model)
    x_wins = v1.winner == "A" and v2.winner == "B"
    y_wins = v1.winner == "B" and v2.winner == "A"
    if x_wins:
        return "X"
    if y_wins:
        return "Y"
    return "tie"


@dataclass
class _PairOutcome:
    """Aggregate of one arm-pair across cases. ``preferred`` ∈ {x_label, y_label, '≈'}."""

    name: str
    x_label: str
    y_label: str
    per_case: dict[str, str] = field(default_factory=dict)
    preferred: str = "≈"
    x_wins: int = 0
    y_wins: int = 0
    decisive: int = 0
    total: int = 0


def _pair_verdict(name: str, x_label: str, y_label: str, per_case: dict[str, str]) -> _PairOutcome:
    """Apply the reproducible majority rule to a pair's per-case winners."""
    total = len(per_case)
    x_wins = sum(1 for w in per_case.values() if w == "X")
    y_wins = sum(1 for w in per_case.values() if w == "Y")
    decisive = x_wins + y_wins
    preferred = "≈"
    if decisive > total / 2:
        if x_wins > y_wins:
            preferred = x_label
        elif y_wins > x_wins:
            preferred = y_label
    return _PairOutcome(
        name=name,
        x_label=x_label,
        y_label=y_label,
        per_case=per_case,
        preferred=preferred,
        x_wins=x_wins,
        y_wins=y_wins,
        decisive=decisive,
        total=total,
    )


def _pair_case_result(pair: _PairOutcome, verdict: Verdict, note: str) -> CaseResult:
    """Build the recorded result for a pair — aggregate counts + per-case winners.

    Per-case winners (required by Verify-1) are rendered as ``case=arm`` tokens so
    the report row shows which arm won each case, not just the aggregate.
    """
    label_for = {"X": pair.x_label, "Y": pair.y_label}
    per_case = " ".join(
        f"{case_id}={label_for.get(winner, 'tie')}" for case_id, winner in pair.per_case.items()
    )
    reason = (
        f"{pair.x_label} {pair.x_wins} / {pair.y_label} {pair.y_wins} / "
        f"tie {pair.total - pair.decisive} → preferred: {pair.preferred}. {note} [{per_case}]"
    )
    return CaseResult(name=pair.name, verdict=verdict, duration_s=0.0, reason=reason)


async def main() -> int:
    """Run the ablation and write the diagnostic report.

    Ollama warm-up runs outside any ``asyncio.timeout``. Generates A0/A1/A2 per
    case (and A3 only if Pair 2 shows a distraction gap), reconciles pairwise with
    order-swap, applies the majority rule, walks the decision tree, and prepends a
    report. Exit code is 0 — this is a diagnostic, not a gate; the value is the
    branch reached, surfaced for the TL.
    """
    await ensure_ollama_warm()
    deps, _agent, frontend, stack = await make_eval_deps()
    assert deps.config.personality == _ROLE, (
        f"eval pins role={_ROLE!r} but config.personality={deps.config.personality!r}; "
        "the arm provider sets seed/mindsets/rules for _ROLE while the reused critique "
        f"builder reads config.personality — set CO_PERSONALITY={_ROLE} to avoid a mismatch."
    )
    chip = judge_model_annotation(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("mindset_selection") as run:
            a0_block = ""
            a1_block = load_soul_mindsets(_ROLE)

            responses: dict[str, dict[str, str]] = {}
            for case in CASES:
                a2_block = _mindset_block(_ROLE, case.task_types)
                r0 = await _arm_response(deps, frontend, _ROLE, a0_block, case, "A0", run)
                r1 = await _arm_response(deps, frontend, _ROLE, a1_block, case, "A1", run)
                r2 = await _arm_response(deps, frontend, _ROLE, a2_block, case, "A2", run)
                responses[case.case_id] = {"A0": r0, "A1": r1, "A2": r2}
                print(f"[mindset_selection] {case.case_id}: A0/A1/A2 responses captured")

            pair1_cases = {
                case.case_id: await _case_winner(
                    deps,
                    case.target_stance,
                    responses[case.case_id]["A1"],
                    responses[case.case_id]["A0"],
                )
                for case in CASES
            }
            pair1 = _pair_verdict("Pair1 A1-vs-A0", "A1", "A0", pair1_cases)

            pair2_cases = {
                case.case_id: await _case_winner(
                    deps,
                    case.target_stance,
                    responses[case.case_id]["A1"],
                    responses[case.case_id]["A2"],
                )
                for case in CASES
            }
            pair2 = _pair_verdict("Pair2 A1-vs-A2", "A1", "A2", pair2_cases)

            decision = ""
            if pair1.preferred != "A1":
                decision = (
                    "INERT — A1 not reliably preferred over A0: the mindsets block does not "
                    "change behavior vs. its absence. Surface to TL as a doctrine decision "
                    "(cut or rebuild the block)."
                )
                cases.append(
                    _pair_case_result(
                        pair1, Verdict.FAIL, "block inert — does not earn real estate"
                    )
                )
                cases.append(_pair_case_result(pair2, Verdict.SOFT_FAIL, "moot (Pair 1 inert)"))
            else:
                cases.append(
                    _pair_case_result(pair1, Verdict.PASS, "mindsets change behavior vs. absence")
                )
                if pair2.preferred != "A2":
                    decision = (
                        "FLAT_OK — mindsets matter (Pair 1) and flat all-six load is not worse "
                        "than relevant-only (Pair 2). Ship nothing; the router is dead (measured "
                        "null). No authoring needed."
                    )
                    cases.append(
                        _pair_case_result(pair2, Verdict.PASS, "flat load not worse than focused")
                    )
                else:
                    cases.append(
                        _pair_case_result(
                            pair2, Verdict.SOFT_FAIL, "A2 > A1: distraction gap — run Pair 3"
                        )
                    )
                    pair3_cases: dict[str, str] = {}
                    for case in CASES:
                        a3_block = _mindset_block(_ROLE, (case.wrong_type,))
                        r3 = await _arm_response(deps, frontend, _ROLE, a3_block, case, "A3", run)
                        pair3_cases[case.case_id] = await _case_winner(
                            deps, case.target_stance, responses[case.case_id]["A2"], r3
                        )
                    pair3 = _pair_verdict("Pair3 A2-vs-A3", "A2", "A3", pair3_cases)
                    if pair3.preferred == "A2":
                        decision = (
                            "AUTHOR — distraction gap (Pair 2) AND the specific content steers "
                            "(Pair 3: A2 > A3). Proceed to Step 2 (anchors + nudge) to close the "
                            "gap inside the static prefix before reaching for the router."
                        )
                        cases.append(
                            _pair_case_result(
                                pair3, Verdict.PASS, "specific content steers → author"
                            )
                        )
                    else:
                        decision = (
                            "STRUCTURAL — distraction gap (Pair 2) but the lift is prose-presence, "
                            "not the specific content (Pair 3: A2 ≈ A3). Sharpening anchors won't "
                            "help; surface to TL (focus/router territory, not wording)."
                        )
                        cases.append(
                            _pair_case_result(
                                pair3,
                                Verdict.SOFT_FAIL,
                                "prose-presence, not content → no authoring",
                            )
                        )

            cases.insert(
                0,
                CaseResult(
                    name="DECISION",
                    verdict=Verdict.PASS,
                    duration_s=0.0,
                    reason=f"{decision} {chip}",
                ),
            )
            for c in cases:
                run.append(c)
            print(f"\n[mindset_selection] DECISION: {decision} {chip}\n")
            prepend_report(_REPORT_PATH, "mindset_selection", run.iso, cases, run_dir=run.dir)
    finally:
        await stack.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
