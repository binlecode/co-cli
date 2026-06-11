"""UAT eval — Workflow 4: Skills dispatch (judged case only).

Keeps the single judge-backed case (W4.A dispatch_follows_procedure): write a
user skill, dispatch it via ``/<skill> <args>``, apply ``skill_env`` exactly as
``main.py:_apply_command_outcome`` does, drive a real ``run_turn``, and judge
that the response followed each numbered instruction in the skill body. The
structural cases (env restore, skill CRUD, built-in shadowing, deferred-tool
discovery) are covered by pytest under ``tests/`` — see the phase-2 coverage map.

Eval-seeded artifact (deterministic name; reruns overwrite in place):
  - ``~/.co-cli/skills/eval_smoke/SKILL.md`` — written by W4.A; left in place.

Specs: docs/specs/skills.md, tui.md
Mission tenet: operator — procedural capability
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time

from evals._deps import EvalFrontend, make_eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S, TOOL_TURN_BUDGET_S
from evals._trace import record_turn, response_text

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, DelegateToAgent, SlashOutcome
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps
from co_cli.skills.lifecycle import refresh_skills

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(deps: CoDeps, agent, frontend: EvalFrontend) -> CommandContext:
    """Build a CommandContext mirroring main.py:423 with empty history."""
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=agent,
        completer=None,
        frontend=frontend,
    )


# ---------------------------------------------------------------------------
# W4.A — dispatch_user_skill
# ---------------------------------------------------------------------------


async def case_w4_a_dispatch_user_skill(
    deps: CoDeps,
    agent,
    frontend: EvalFrontend,
    run,
) -> CaseResult:
    """W4.A — write a user skill, dispatch it, verify env + arguments reach the agent.

    Writes ``~/.co-cli/skills/eval_smoke/SKILL.md`` with a randomized
    ``CO_EVAL_TOKEN`` value and a two-step body referencing the env var and
    ``$ARGUMENTS``. Drives ``/eval_smoke evaluating_arg1`` through
    ``dispatch()`` — outcome must be ``DelegateToAgent`` carrying both
    ``delegated_input`` (the expanded body) and ``skill_env``. Applies
    ``skill_env`` to ``os.environ`` exactly as ``main.py:_apply_command_outcome``
    does, drives a real ``run_turn``, and asserts the literal token + the
    literal argument both appear in the response. Final gate is an LLM
    judge call rating instruction adherence.
    """
    case_id = "W4.A"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    rand_suffix = secrets.token_hex(4)
    token_value = f"EVALTOKEN_{rand_suffix}"
    skill_path = deps.user_skills_dir / "eval_smoke" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_body = (
        "---\n"
        "name: eval_smoke\n"
        "description: Eval smoke skill — reports CO_EVAL_TOKEN and $ARGUMENTS verbatim.\n"
        "skill-env:\n"
        f"  CO_EVAL_TOKEN: {token_value}\n"
        "---\n"
        "Follow each step exactly:\n"
        '1. Invoke the `shell_exec` tool with command `printf %s "$CO_EVAL_TOKEN"` and include the captured stdout in your response, prefixed with `CO_EVAL_TOKEN=`.\n'
        "2. In your same response, also include the literal text `ARGS=$ARGUMENTS`.\n"
    )
    skill_path.write_text(skill_body, encoding="utf-8")

    refresh_skills(deps)

    reason = ""
    passed = True
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    trace_id = ""

    ctx = _make_ctx(deps, agent, frontend)
    raw_input = "/eval_smoke evaluating_arg1"
    outcome: SlashOutcome = await dispatch(raw_input, ctx)

    if not isinstance(outcome, DelegateToAgent):
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=(f"dispatch returned {type(outcome).__name__}, expected DelegateToAgent"),
            trace_files=[str(trace_file.relative_to(run.outputs_dir))],
        )

    if outcome.skill_name != "eval_smoke":
        passed = False
        reason = f"skill_name = {outcome.skill_name!r}, expected 'eval_smoke'"
    elif outcome.skill_env.get("CO_EVAL_TOKEN") != token_value:
        passed = False
        reason = (
            f"skill_env.CO_EVAL_TOKEN = {outcome.skill_env.get('CO_EVAL_TOKEN')!r}, "
            f"expected {token_value!r}"
        )

    os.environ.update(outcome.skill_env)
    deps.runtime.active_skill_name = outcome.skill_name
    deps.runtime.active_skill_env = dict(outcome.skill_env)

    turn_result = None
    if passed:
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                turn_result, turn_trace = await record_turn(
                    case_id=case_id,
                    turn_index=0,
                    user_input=outcome.delegated_input,
                    run_turn_callable=lambda: run_turn(
                        agent=agent,
                        user_input=outcome.delegated_input,
                        deps=deps,
                        message_history=[],
                        frontend=frontend,
                    ),
                    case_dir_path=trace_file,
                    agent=agent,
                )
            model_call_seconds = turn_trace.model_call_seconds
            token_usage = dict(turn_trace.token_usage)
            trace_id = turn_trace.trace_ids[0] if turn_trace.trace_ids else ""
        except TimeoutError:
            passed = False
            reason = f"run_turn exceeded CALL_TIMEOUT_S ({CALL_TIMEOUT_S}s)"
        except Exception as exc:
            passed = False
            reason = f"run_turn raised {type(exc).__name__}: {exc}"

    if passed and turn_result is not None:
        if turn_result.outcome != "continue":
            passed = False
            reason = f"turn outcome = {turn_result.outcome!r}, expected 'continue'"
        else:
            text = response_text(turn_result)
            if token_value not in text:
                passed = False
                reason = (
                    f"response missing literal CO_EVAL_TOKEN value {token_value!r}; "
                    f"preview={text[:200]!r}"
                )
            elif "evaluating_arg1" not in text:
                passed = False
                reason = (
                    "response missing literal $ARGUMENTS value 'evaluating_arg1'; "
                    f"preview={text[:200]!r}"
                )
            elif model_call_seconds > TOOL_TURN_BUDGET_S:
                passed = False
                reason = f"[slow] {model_call_seconds:.1f}s vs budget {TOOL_TURN_BUDGET_S}.0s"

    if passed and turn_result is not None:
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                verdict = await judge_with_llm(
                    "PASS criteria: did the response follow each numbered instruction "
                    "in the skill body? Step 1 must surface the CO_EVAL_TOKEN value; "
                    "step 2 must surface the $ARGUMENTS value.",
                    turn_result.messages,
                    deps=deps,
                    model=deps.judge_model,
                )
        except TimeoutError:
            passed = False
            reason = f"judge call exceeded CALL_TIMEOUT_S ({CALL_TIMEOUT_S}s)"
        else:
            chip = judge_model_annotation(deps)
            if not verdict.passed:
                passed = False
                reason = f"judge FAIL (score={verdict.score}): {verdict.rationale} {chip}"
            else:
                reason = f"token + args present; judge score={verdict.score} {chip}"

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_id=trace_id,
        trace_files=[str(trace_file.relative_to(run.outputs_dir))],
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> int:
    """Run the W4.A judged case against real CoDeps.

    Ollama warm-up runs outside any ``asyncio.timeout`` per behavioral
    constraint #3.
    """
    await ensure_ollama_warm()
    deps, agent, frontend, stack = await make_eval_deps()
    apply_eval_window(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("skills") as run:
            try:
                cr_a = await case_w4_a_dispatch_user_skill(deps, agent, frontend, run)
            except Exception as exc:
                cr_a = CaseResult(
                    name="W4.A",
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            cases.append(cr_a)
            run.append(cr_a)
            print(
                f"[skills] {cr_a.name}: {'PASS' if cr_a.passed else 'FAIL'} — "
                f"{cr_a.reason or 'ok'}"
            )
    finally:
        await stack.aclose()
    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
