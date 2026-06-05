"""UAT eval — Workflow 4: Skills dispatch + lifecycle.

Covers user-skill dispatch via ``/<skill> <args>`` (body expansion through
``run_turn`` with ``skill_env`` applied to the process environment),
post-dispatch env restoration via ``cleanup_skill_run_state``, the
skill write surface (skill_create / skill_patch / skill_delete), the
built-in-shadowing safety boundary that prevents a user skill from
intercepting a built-in slash command, and (W4.E) the deferred-tool
discovery diagnostic — a guarded SOFT_PASS-only case that activates only
when ``skill_create`` is DEFERRED.

Per-case structure mirrors W1-W6: real CoDeps via ``make_eval_deps()``,
real ``~/.co-cli/`` workspace, dispatch via ``co_cli.commands.core.dispatch``
and direct skill-write tool calls with a manually constructed
``RunContext[CoDeps]``, JSONL run record under ``evals/_outputs/``, dated
section prepended to ``docs/REPORT-eval-skills.md``.

Eval-seeded artifacts (deterministic names; reruns overwrite in place):
  - ``~/.co-cli/skills/eval_smoke.md`` — written by W4.A; **left in place**.
  - ``~/.co-cli/skills/eval_w4_lifecycle.md`` — created and deleted by W4.C.
  - ``~/.co-cli/skills/help.md`` — W4.D's shadowing attempt; **always cleaned
    up** at case end (its presence would brick ``/help`` on future sessions).

Specs: docs/specs/skills.md, tui.md
Mission tenet: operator — procedural capability
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from pathlib import Path

from evals._deps import EvalFrontend, make_eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S, TOOL_TURN_BUDGET_S
from evals._trace import record_turn, response_text
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, DelegateToAgent, LocalOnly, SlashOutcome
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.skills.lifecycle import cleanup_skill_run_state, refresh_skills
from co_cli.tools.system.skills import skill_create, skill_delete, skill_patch

_REPORT_PATH = Path("docs/REPORT-eval-skills.md")


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


def _make_run_context(deps: CoDeps, agent) -> RunContext[CoDeps]:
    """Construct a minimal RunContext for direct @agent_tool invocation.

    The skill-write tools read ctx.deps, ctx.tool_name (for spill thresholds),
    and nothing else; tool_index is the only ctx.deps surface touched in the
    success path. RunUsage() is a fresh zero-usage accumulator — irrelevant
    to the tools' structural assertions.
    """
    return RunContext(
        deps=deps,
        model=agent.model,
        usage=RunUsage(),
        tool_name="skill_create",
    )


def _skill_w4_lifecycle_body() -> str:
    """Return frontmatter + body for the W4.C lifecycle skill.

    Plain-text content so the security scan never trips; description must be
    non-empty per _validate_skill_content (skills.py:99).
    """
    return (
        "---\n"
        "description: Eval W4.C lifecycle skill — created, patched, and deleted by the eval.\n"
        "---\n"
        "\n"
        "# Eval W4 lifecycle\n"
        "\n"
        "Initial body — patched in W4.C.\n"
    )


def _help_shadowing_body() -> str:
    """Frontmatter + body for the W4.D shadowing-attempt skill named `help`."""
    return (
        "---\n"
        "description: Eval W4.D shadowing attempt — must not override the built-in /help.\n"
        "---\n"
        "\n"
        "# Help shadow\n"
        "\n"
        "If this body reaches the agent, built-in shadowing is broken.\n"
    )


# ---------------------------------------------------------------------------
# W4.A — dispatch_user_skill
# ---------------------------------------------------------------------------


async def case_w4_a_dispatch_user_skill(
    deps: CoDeps,
    agent,
    frontend: EvalFrontend,
    run,
) -> tuple[CaseResult, dict[str, str | None]]:
    """W4.A — write a user skill, dispatch it, verify env + arguments reach the agent.

    Writes ``~/.co-cli/skills/eval_smoke.md`` with a randomized
    ``CO_EVAL_TOKEN`` value and a two-step body referencing the env var and
    ``$ARGUMENTS``. Drives ``/eval_smoke evaluating_arg1`` through
    ``dispatch()`` — outcome must be ``DelegateToAgent`` carrying both
    ``delegated_input`` (the expanded body) and ``skill_env``. Applies
    ``skill_env`` to ``os.environ`` exactly as ``main.py:_apply_command_outcome``
    does, drives a real ``run_turn``, and asserts the literal token + the
    literal argument both appear in the response. Final gate is an LLM
    judge call rating instruction adherence.

    Returns the ``CaseResult`` plus ``saved_env`` (the pre-dispatch
    snapshot) so W4.B can restore via ``cleanup_skill_run_state``.
    """
    case_id = "W4.A"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    rand_suffix = secrets.token_hex(4)
    token_value = f"EVALTOKEN_{rand_suffix}"
    skill_path = deps.user_skills_dir / "eval_smoke.md"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
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
    saved_env: dict[str, str | None] = {}
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    trace_id = ""

    ctx = _make_ctx(deps, agent, frontend)
    raw_input = "/eval_smoke evaluating_arg1"
    outcome: SlashOutcome = await dispatch(raw_input, ctx)

    if not isinstance(outcome, DelegateToAgent):
        return (
            CaseResult(
                name=case_id,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=(f"dispatch returned {type(outcome).__name__}, expected DelegateToAgent"),
                trace_files=[str(trace_file.relative_to(run.dir.parent))],
            ),
            saved_env,
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

    saved_env = {k: os.environ.get(k) for k in outcome.skill_env}
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

    return (
        CaseResult(
            name=case_id,
            verdict=Verdict.PASS if passed else Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            model_call_seconds=model_call_seconds,
            token_usage=token_usage,
            trace_id=trace_id,
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
            reason=reason,
        ),
        saved_env,
    )


# ---------------------------------------------------------------------------
# W4.B — env_restored_after_dispatch
# ---------------------------------------------------------------------------


async def case_w4_b_env_restored_after_dispatch(
    deps: CoDeps,
    saved_env: dict[str, str | None],
    run,
) -> CaseResult:
    """W4.B — ``cleanup_skill_run_state`` restores env + clears active_skill_name.

    Runs immediately after W4.A and consumes its ``saved_env`` snapshot.
    The cleanup helper takes ``(saved_env, deps)`` per
    ``co_cli/skills/lifecycle.py:51``. Asserts ``CO_EVAL_TOKEN`` is no
    longer in ``os.environ`` (the W4.A snapshot captured the pre-dispatch
    absence as None → cleanup pops the key) and ``deps.runtime.active_skill_name``
    is None.
    """
    case_id = "W4.B"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    cleanup_skill_run_state(saved_env, deps)

    reason = ""
    passed = True

    if "CO_EVAL_TOKEN" in os.environ:
        passed = False
        reason = "CO_EVAL_TOKEN still in os.environ after cleanup — env leaked across turns"
    elif deps.runtime.active_skill_name is not None:
        passed = False
        reason = f"active_skill_name = {deps.runtime.active_skill_name!r}, expected None"
    else:
        reason = "env restored and active_skill_name cleared"

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
        reason=reason,
    )


# ---------------------------------------------------------------------------
# W4.C — skill_create_patch_delete
# ---------------------------------------------------------------------------


async def case_w4_c_skill_create_patch_delete(
    deps: CoDeps,
    agent,
    run,
) -> CaseResult:
    """W4.C — drive skill_create / skill_patch / skill_delete on a fresh skill.

    Uses the deterministic name ``eval_w4_lifecycle`` (created and deleted
    by this case in a single pass; reruns recreate-and-delete cleanly). The
    tool is invoked directly via a manually constructed RunContext —
    ``@agent_tool`` returns the function unchanged, so no agent loop is
    required.
    """
    case_id = "W4.C"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    name = "eval_w4_lifecycle"
    ctx = _make_run_context(deps, agent)
    skill_path = deps.user_skills_dir / f"{name}.md"

    if skill_path.exists():
        skill_path.unlink()
        refresh_skills(deps)

    reason = ""
    passed = True

    create_result = await skill_create(
        ctx,
        name=name,
        content=_skill_w4_lifecycle_body(),
    )
    if (create_result.metadata or {}).get("error"):
        passed = False
        reason = f"create failed: {create_result.return_value}"
    elif not skill_path.exists():
        passed = False
        reason = "create returned success but file missing on disk"

    mtime_after_create = skill_path.stat().st_mtime if passed and skill_path.exists() else 0.0

    if passed:
        await asyncio.sleep(1.05)
        patch_result = await skill_patch(
            ctx,
            name=name,
            old_string="Initial body — patched in W4.C.",
            new_string="Patched body — verified by W4.C mtime check.",
        )
        if (patch_result.metadata or {}).get("error"):
            passed = False
            reason = f"patch failed: {patch_result.return_value}"
        elif not skill_path.exists():
            passed = False
            reason = "patch returned success but file missing on disk"
        else:
            mtime_after_patch = skill_path.stat().st_mtime
            if mtime_after_patch <= mtime_after_create:
                passed = False
                reason = f"patch did not bump mtime ({mtime_after_create} → {mtime_after_patch})"

    if passed:
        delete_result = await skill_delete(ctx, name=name)
        if (delete_result.metadata or {}).get("error"):
            passed = False
            reason = f"delete failed: {delete_result.return_value}"
        elif skill_path.exists():
            passed = False
            reason = "delete returned success but file still on disk"

    if passed:
        reason = "create + patch + delete all observed on disk"

    if skill_path.exists():
        skill_path.unlink(missing_ok=True)
        refresh_skills(deps)

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
        reason=reason,
    )


# ---------------------------------------------------------------------------
# W4.D — builtin_shadowing_blocked
# ---------------------------------------------------------------------------


async def case_w4_d_builtin_shadowing_blocked(
    deps: CoDeps,
    agent,
    frontend: EvalFrontend,
    run,
) -> CaseResult:
    """W4.D — a user skill named ``help`` must not override the built-in /help.

    Two PASS paths:
      (a) ``skill_create(name='help', ...)`` is rejected
          by the production code — case PASSes without touching disk.
      (b) The create succeeds (skill_create's validator imposes no reserved-
          name check today). Refresh the index and dispatch ``/help``; the
          outcome must be ``LocalOnly`` (built-in handler ran) — NOT
          ``DelegateToAgent`` (which would mean the user skill shadowed).

    Cleanup: any ``help.md`` written to the user skills dir is removed
    before returning, regardless of the path taken — leaving it in place
    would brick ``/help`` on every future session.
    """
    case_id = "W4.D"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    help_path = deps.user_skills_dir / "help.md"

    reason = ""
    passed = True

    try:
        ctx = _make_run_context(deps, agent)
        create_result = await skill_create(
            ctx,
            name="help",
            content=_help_shadowing_body(),
        )
        rejected = bool((create_result.metadata or {}).get("error"))

        if rejected:
            if help_path.exists():
                passed = False
                reason = "skill_create rejected the create but help.md was still written"
            else:
                reason = f"create rejected by production code: {create_result.return_value}"
        else:
            refresh_skills(deps)
            slash_ctx = _make_ctx(deps, agent, frontend)
            help_outcome = await dispatch("/help", slash_ctx)
            if isinstance(help_outcome, DelegateToAgent):
                passed = False
                reason = (
                    "user skill /help shadowed the built-in — dispatch returned DelegateToAgent"
                )
            elif not isinstance(help_outcome, LocalOnly):
                passed = False
                reason = f"/help returned {type(help_outcome).__name__}, expected LocalOnly"
            else:
                reason = "built-in /help ran; user skill did not shadow"
    finally:
        if help_path.exists():
            help_path.unlink(missing_ok=True)
        refresh_skills(deps)

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
        reason=reason,
    )


# ---------------------------------------------------------------------------
# W4.E — deferred skill_create discovery (gated re-flip diagnostic)
# ---------------------------------------------------------------------------


def _eval_w4e_skill_body(name: str) -> str:
    """Valid skill markdown for the discovery probe — passed verbatim to the model.

    Pre-formed so the trial isolates *discovery* of the DEFERRED skill_create tool
    from content-generation quality: the only variable under test is whether the
    model finds skill_create via search_tools and creates with the given content.
    """
    return (
        "---\n"
        f"description: Eval W4.E discovery probe {name} — clears stale pytest logs.\n"
        "---\n"
        f"\n# {name}\n\n"
        f"**Invocation:** /{name}\n\n"
        "## Phase 1 — Locate\n\n"
        "Find log files older than 7 days under .pytest-logs/.\n\n"
        "## Phase 2 — Remove\n\n"
        "Delete the located files.\n"
    )


async def case_w4_e_discovery(
    deps: CoDeps,
    agent,
    frontend: EvalFrontend,
    run,
    trials: int = 3,
) -> CaseResult:
    """W4.E — does the model discover DEFERRED skill_create via search_tools and create?

    Diagnostic for the deferred-tool-stubs bet: with skill_create flipped to DEFERRED
    and the per-tool awareness stubs live, run ``trials`` independent discovery attempts.
    Each trial drives one ``run_turn`` with a fresh ``message_history=[]`` asking the
    model to save a procedure as a skill. A trial is a HIT when, in one turn, the model:
    (1) calls ``search_tools`` (discovery), (2) calls ``skill_create`` (loaded + invoked
    the deferred tool), (3) leaves the skill on disk in the user skills dir, and (4) does
    NOT fall back to ``file_write`` (the FM-3 cwd-pollution failure mode).

    Independence note: the SDK's ToolSearchToolset derives "already discovered" state
    from the message history (``_parse_discovered_tools`` walks ``ctx.messages``), NOT
    from toolset-instance state. A fresh ``message_history=[]`` per trial therefore
    re-defers skill_create every trial, so reusing one bootstrap is genuinely independent
    — and avoids the cross-task MCP teardown crash that per-trial ``make_eval_deps()``
    bootstraps trigger (the plan's stated 'fresh deps per trial' rationale is moot).

    Gate: HITS ≥ ceil(2/3 · trials) → keep skill_create DEFERRED; below → revert to
    ALWAYS. The gate result is recorded in ``reason`` for the human re-flip decision;
    the verdict is always ``SOFT_PASS`` so a stochastic miss never flips the eval's
    process exit code (``CaseResult.passed`` is True only for PASS/SOFT_PASS, so
    SOFT_FAIL would redden the run — avoided here by design).

    Also captures the measured stub-prompt char length from the live bootstrap.
    """
    case_id = "W4.E"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    skill_info = deps.tool_index.get("skill_create")
    if skill_info is None or skill_info.visibility != VisibilityPolicyEnum.DEFERRED:
        return CaseResult(
            name=case_id,
            verdict=Verdict.SOFT_PASS,
            duration_s=time.monotonic() - t0,
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
            reason=(
                "inert: skill_create is ALWAYS, not DEFERRED — the deferred-discovery "
                "path has no target. Re-flip skill_create to DEFERRED to re-test FM-2/3 "
                "(stubs alone were insufficient; binding constraint is the loader UX)."
            ),
        )

    from co_cli.tools.deferred_prompt import build_deferred_tool_awareness_prompt

    stub_chars = len(build_deferred_tool_awareness_prompt(deps.tool_index))

    threshold = -(-2 * trials // 3)
    hits = 0
    trial_notes: list[str] = []
    total_model_seconds = 0.0
    token_usage: dict[str, int] = {}

    for i in range(trials):
        name = f"eval_w4e_discovery_{i}"
        skill_path = deps.user_skills_dir / f"{name}.md"
        try:
            if skill_path.exists():
                skill_path.unlink()
                refresh_skills(deps)

            prompt = (
                f"I just worked out a reliable multi-step procedure for clearing stale "
                f"pytest logs. Save it as a reusable skill named {name!r} and create it "
                f"on disk now. Use exactly this content:\n\n{_eval_w4e_skill_body(name)}"
            )
            tool_names: list[str] = []
            try:
                async with asyncio.timeout(CALL_TIMEOUT_S):
                    _turn_result, turn_trace = await record_turn(
                        case_id=case_id,
                        turn_index=i,
                        user_input=prompt,
                        run_turn_callable=lambda p=prompt: run_turn(
                            agent=agent,
                            user_input=p,
                            deps=deps,
                            message_history=[],
                            frontend=frontend,
                        ),
                        case_dir_path=trace_file,
                        agent=agent,
                    )
                total_model_seconds += turn_trace.model_call_seconds
                for k, v in turn_trace.token_usage.items():
                    token_usage[k] = token_usage.get(k, 0) + v
                tool_names = [tc.tool_name for tc in turn_trace.tool_calls]
            except TimeoutError:
                trial_notes.append(f"t{i}=timeout")
                continue
            except Exception as exc:
                trial_notes.append(f"t{i}=err({type(exc).__name__})")
                continue

            searched = "search_tools" in tool_names
            managed = "skill_create" in tool_names
            on_disk = skill_path.exists()
            polluted = "file_write" in tool_names
            hit = searched and managed and on_disk and not polluted
            if hit:
                hits += 1
            trial_notes.append(
                f"t{i}={'HIT' if hit else 'miss'}"
                f"[search={int(searched)},manage={int(managed)},"
                f"disk={int(on_disk)},fw={int(polluted)}]"
            )
        finally:
            if skill_path.exists():
                skill_path.unlink(missing_ok=True)
                refresh_skills(deps)

    gate_pass = hits >= threshold
    reason = (
        f"discovery {hits}/{trials} (gate {'PASS' if gate_pass else 'FAIL'}, "
        f"need ≥{threshold}) — {'keep DEFERRED' if gate_pass else 'revert to ALWAYS'}; "
        f"stub_prompt={stub_chars}c; {', '.join(trial_notes)}"
    )

    return CaseResult(
        name=case_id,
        verdict=Verdict.SOFT_PASS,
        duration_s=time.monotonic() - t0,
        model_call_seconds=total_model_seconds,
        token_usage=token_usage,
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> int:
    """Run W4.A through W4.E against real CoDeps and emit the REPORT.

    Ollama warm-up runs outside any ``asyncio.timeout`` per behavioral
    constraint #3. Each case captures its own verdict; a failure in one
    case does not abort the run. W4.B reads ``saved_env`` from W4.A's
    return tuple — that ordering is a contract (W4.B is the env-restore
    boundary case for the W4.A dispatch).
    """
    await ensure_ollama_warm()
    deps, agent, frontend, stack = await make_eval_deps()
    apply_eval_window(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("skills") as run:
            saved_env: dict[str, str | None] = {}
            try:
                cr_a, saved_env = await case_w4_a_dispatch_user_skill(deps, agent, frontend, run)
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

            try:
                cr_b = await case_w4_b_env_restored_after_dispatch(deps, saved_env, run)
            except Exception as exc:
                cr_b = CaseResult(
                    name="W4.B",
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            cases.append(cr_b)
            run.append(cr_b)
            print(
                f"[skills] {cr_b.name}: {'PASS' if cr_b.passed else 'FAIL'} — "
                f"{cr_b.reason or 'ok'}"
            )

            try:
                cr_c = await case_w4_c_skill_create_patch_delete(deps, agent, run)
            except Exception as exc:
                cr_c = CaseResult(
                    name="W4.C",
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            cases.append(cr_c)
            run.append(cr_c)
            print(
                f"[skills] {cr_c.name}: {'PASS' if cr_c.passed else 'FAIL'} — "
                f"{cr_c.reason or 'ok'}"
            )

            try:
                cr_d = await case_w4_d_builtin_shadowing_blocked(deps, agent, frontend, run)
            except Exception as exc:
                cr_d = CaseResult(
                    name="W4.D",
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            cases.append(cr_d)
            run.append(cr_d)
            print(
                f"[skills] {cr_d.name}: {'PASS' if cr_d.passed else 'FAIL'} — "
                f"{cr_d.reason or 'ok'}"
            )

            try:
                cr_e = await case_w4_e_discovery(deps, agent, frontend, run)
            except Exception as exc:
                cr_e = CaseResult(
                    name="W4.E",
                    verdict=Verdict.SOFT_PASS,
                    duration_s=0.0,
                    reason=f"diagnostic errored: {type(exc).__name__}: {exc}",
                )
            cases.append(cr_e)
            run.append(cr_e)
            print(
                f"[skills] {cr_e.name}: {'SOFT_PASS' if cr_e.passed else 'SOFT_FAIL'} — "
                f"{cr_e.reason or 'ok'}"
            )

            prepend_report(
                _REPORT_PATH,
                "skills",
                run.iso,
                cases,
                run_dir=run.dir,
            )
    finally:
        await stack.aclose()
    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
