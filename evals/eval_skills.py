"""UAT eval — Workflow 4: Skills dispatch + model selection.

W4.A (dispatch_follows_procedure, judged): write a user skill, dispatch it via
``/<skill> <args>``, apply ``skill_env`` exactly as ``main.py:_apply_command_outcome``
does, drive a real ``run_turn_owned``, and judge that the response followed each numbered
instruction in the skill body. This exercises slash-dispatch **mechanics**.

W4.B (skill_selection_mutual_exclusivity, behavioral): the ``pdf`` and ``office``
bundled skills are both ``user-invocable: false`` — the model's only entry path is
selecting them from the ``<available_skills>`` manifest and loading them with
``skill_view(name)`` (per the skill-protocol rule). W4.B drives real turns over
representative prompts and asserts the model selects the right skill from the manifest:
a PDF prompt selects ``pdf`` (not ``office``); a deck / spreadsheet prompt selects
``office`` (not ``pdf``); a bare web URL selects neither (that is ``web_fetch``).
The observable is which skill name reaches ``skill_view`` — the descriptions are the only
selection signal, so this is the durable regression gate on their mutual exclusivity.

W4.R (skill_reviewer_encodes_correction, judged): drives the per-session skill
reviewer (``process_review(domain="skill")``) over a seeded transcript and judges
the encoded skill is faithful to the one durable correction.

W4.M (skill_merge_preserves_distinct_steps, judged): the skill-side analogue of
daily_chat W1.F. Seeds two near-duplicate user skills (shared boilerplate body,
one distinct final step each), runs the housekeeping ``merge_skills`` consolidation,
and judges that BOTH distinct steps survive in the umbrella — the lossless-merge gate.

The structural cases (env restore, skill CRUD, built-in shadowing, deferred-tool
discovery) and the deterministic housekeeping mechanics (clustering threshold,
canonical-anchor pick, sibling archival, skill decay) are covered by pytest under
``tests/`` — see the phase-2 coverage map. These evals own only the model-driven
halves a unit test cannot assert.

Eval-seeded skill fixtures (eval-owned names) are torn down on run completion so
they never linger in the user's real ``~/.co-cli/skills/`` slash-command surface:
  - ``eval_smoke/`` — written by W4.A.
  - ``eval_merge_{a,b}_<rand>/`` (+ ``.archive/``) — written by W4.M.
  - the reviewer-encoded deploy skill — created by W4.R.
W4.A/W4.M fixtures are purged in ``main()``'s finally; W4.R removes its own
reviewer-created skill inline (its name is model-chosen).

Specs: docs/specs/skills.md, tui.md
Mission tenet: operator — procedural capability
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evals._deps import EvalFrontend, drive_turn, make_eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S, DREAM_CYCLE_BUDGET_S, TOOL_TURN_BUDGET_S
from evals._trace import TurnTrace, record_turn, response_text
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, DelegateToAgent, SlashOutcome
from co_cli.daemons.dream._housekeeping import merge_skills
from co_cli.daemons.dream._reviewer import process_review
from co_cli.daemons.dream.state import HousekeepingState
from co_cli.deps import CoDeps
from co_cli.session.filename import session_filename
from co_cli.session.persistence import append_messages
from co_cli.skills.lifecycle import refresh_skills

_SKILL_REVIEWER_FIXTURE_UUID8 = "c4d5e6f7"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(deps: CoDeps, frontend: EvalFrontend) -> CommandContext:
    """Build a CommandContext mirroring main.py:423 with empty history."""
    return CommandContext(
        message_history=[],
        deps=deps,
        completer=None,
        frontend=frontend,
    )


# ---------------------------------------------------------------------------
# W4.A — dispatch_user_skill
# ---------------------------------------------------------------------------


async def case_w4_a_dispatch_user_skill(
    deps: CoDeps,
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
    does, drives a real ``run_turn_owned``, and asserts the literal token + the
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
        "user-invocable: true\n"
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

    ctx = _make_ctx(deps, frontend)
    raw_input = "/eval_smoke evaluating_arg1"
    outcome: SlashOutcome = await dispatch(raw_input, ctx)

    if not isinstance(outcome, DelegateToAgent):
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=(f"dispatch returned {type(outcome).__name__}, expected DelegateToAgent"),
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
                    prior_message_count=0,
                    run_turn_callable=lambda: drive_turn(
                        user_input=outcome.delegated_input,
                        deps=deps,
                        message_history=[],
                        frontend=frontend,
                    ),
                    case_dir_path=trace_file,
                )
            model_call_seconds = turn_trace.model_call_seconds
            token_usage = dict(turn_trace.token_usage)
            trace_id = turn_trace.trace_ids[0] if turn_trace.trace_ids else ""
        except TimeoutError:
            passed = False
            reason = f"run_turn_owned exceeded CALL_TIMEOUT_S ({CALL_TIMEOUT_S}s)"
        except Exception as exc:
            passed = False
            reason = f"run_turn_owned raised {type(exc).__name__}: {exc}"

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
        reason=reason,
    )


# ---------------------------------------------------------------------------
# W4.B — skill_selection_mutual_exclusivity
# ---------------------------------------------------------------------------


def _selected_skills(turn_trace: TurnTrace) -> set[str]:
    """The set of skill names the agent loaded via ``skill_view`` during a turn.

    Reads the captured ``skill_view`` tool calls and parses the ``name`` argument
    — the manifest-driven selection the model made, the only entry path for a
    ``user-invocable: false`` skill.
    """
    selected: set[str] = set()
    for call in turn_trace.tool_calls:
        if call.tool_name != "skill_view":
            continue
        try:
            args = json.loads(call.args) if call.args else {}
        except (TypeError, ValueError):
            args = {}
        name = args.get("name") if isinstance(args, dict) else None
        if isinstance(name, str) and name:
            selected.add(name)
    return selected


_SELECTION_PROMPTS = [
    (
        "pdf",
        "Please summarize the quarterly report saved at ~/reports/q3-report.pdf.",
        {"pdf"},
        {"office"},
    ),
    (
        "pptx",
        "Summarize the slide deck at ~/decks/q3-review.pptx for me.",
        {"office"},
        {"pdf"},
    ),
    (
        "xlsx",
        "What's in the spreadsheet at ~/data/budget.xlsx?",
        {"office"},
        {"pdf"},
    ),
    (
        "url",
        "Summarize this web page for me: https://example.com/quarterly-update",
        set(),
        {"pdf", "office"},
    ),
]


async def case_w4_b_skill_selection(
    deps: CoDeps,
    frontend: EvalFrontend,
    run,
) -> CaseResult:
    """W4.B — assert pdf↔office mutual exclusivity in model skill selection.

    Drives one real turn per prompt in ``_SELECTION_PROMPTS`` (fresh history each),
    captures the ``skill_view`` selections, and fails on the first prompt whose
    selection includes a forbidden skill or omits a required one. No judge — the
    selection is directly observable.
    """
    case_id = "W4.B"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    passed = True
    reason = ""
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    trace_id = ""

    for index, (label, prompt, must_include, must_exclude) in enumerate(_SELECTION_PROMPTS):
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                _, turn_trace = await record_turn(
                    case_id=case_id,
                    turn_index=index,
                    user_input=prompt,
                    prior_message_count=0,
                    run_turn_callable=lambda p=prompt: drive_turn(
                        user_input=p,
                        deps=deps,
                        message_history=[],
                        frontend=frontend,
                    ),
                    case_dir_path=trace_file,
                )
        except TimeoutError:
            passed = False
            reason = f"[{label}] run_turn_owned exceeded CALL_TIMEOUT_S ({CALL_TIMEOUT_S}s)"
            break
        except Exception as exc:
            passed = False
            reason = f"[{label}] run_turn_owned raised {type(exc).__name__}: {exc}"
            break

        model_call_seconds += turn_trace.model_call_seconds
        for key, value in turn_trace.token_usage.items():
            token_usage[key] = token_usage.get(key, 0) + value
        if turn_trace.trace_ids and not trace_id:
            trace_id = turn_trace.trace_ids[0]

        selected = _selected_skills(turn_trace)
        wrongly_selected = must_exclude & selected
        missing = must_include - selected
        if wrongly_selected or missing:
            passed = False
            reason = (
                f"[{label}] prompt {prompt!r}: selected {sorted(selected) or 'none'}; "
                f"expected to include {sorted(must_include) or 'none'} and exclude "
                f"{sorted(must_exclude)}"
            )
            break

    if passed:
        reason = (
            f"all {len(_SELECTION_PROMPTS)} prompts selected the right skill "
            "(pdf↔office mutual exclusivity holds)"
        )

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_id=trace_id,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Skill-reviewer cognition — process_review(domain="skill")
# ---------------------------------------------------------------------------


def _make_correction_token() -> str:
    """Per-run-unique marker that is the substance of the corrected deploy step.

    Unique per run so a skill body left by a prior run can never satisfy the
    structural gate — only this run's reviewer output carries this token. It is
    embedded as a concrete command the user demands, not an opaque marker, so a
    faithful reviewer must reproduce it verbatim when encoding the correction.
    """
    return f"verify-{uuid4().hex[:12]}"


def _seed_skill_reviewer_transcript(sessions_dir: Path) -> tuple[Path, str]:
    """Write a real JSONL transcript carrying one clear, reusable user correction.

    The user corrects HOW the deploy class of task is handled: a required first
    step (run ``make <token>``) the assistant kept skipping. The per-run token is
    the substance of the corrected step, so a faithful skill update must embed it.
    Signal lives in user/assistant text only (the reviewer serializes with
    ``include_tool_results=False``). Mirrors ``eval_memory._seed_reviewer_transcript``:
    real ``ModelRequest``/``ModelResponse`` via ``append_messages``, deterministic
    fixture uuid8 with stale-purge, per-run token.

    Returns the transcript path and the per-run correction token.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for stale in sessions_dir.glob(f"*-{_SKILL_REVIEWER_FIXTURE_UUID8}.jsonl"):
        stale.unlink()
    token = _make_correction_token()
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="Go ahead and deploy the staging build.")]),
        ModelResponse(parts=[TextPart(content="Done — I pushed the staging build out.")]),
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "You skipped the smoke check again. Stop doing that. From now on, "
                        f"every single deploy must run `make {token}` as the very first step "
                        "before anything else — if that check fails, abort the deploy. This "
                        "is a hard rule for all future deploys, not just this one."
                    )
                )
            ]
        ),
        ModelResponse(
            parts=[
                TextPart(
                    content=(
                        f"Understood — I'll run `make {token}` first on every deploy from now "
                        "on and abort if it fails."
                    )
                )
            ]
        ),
        ModelRequest(
            parts=[UserPromptPart(content="Thanks. Different topic — what's the weather like?")]
        ),
        ModelResponse(
            parts=[TextPart(content="I can't check live weather, but happy to help otherwise.")]
        ),
    ]
    created_at = datetime.now(UTC)
    path = sessions_dir / session_filename(created_at, _SKILL_REVIEWER_FIXTURE_UUID8)
    append_messages(path, messages)
    return path, token


def _user_skill_bodies(user_skills_dir: Path) -> dict[Path, str]:
    """Map each user ``<name>/SKILL.md`` path to its current body text."""
    if not user_skills_dir.exists():
        return {}
    return {p: p.read_text(encoding="utf-8") for p in user_skills_dir.glob("*/SKILL.md")}


async def case_skill_reviewer_encodes_correction(
    deps: CoDeps,
    frontend: EvalFrontend,
    run,
) -> CaseResult:
    """Skill-reviewer cognition — process_review(domain="skill") encodes a correction.

    Drives the real skill reviewer end-to-end against a seeded transcript carrying
    one clear, reusable user correction about how deploys are handled. Structural
    gate: the per-run token appears in a user skill body that did not contain it
    before (created OR patched — the prompt prefers updating an existing skill).
    Judged: the skill faithfully encodes the correction and does not fabricate a
    procedure absent from the transcript.

    ``agent``/``frontend`` are unused (this case calls ``process_review`` directly,
    not ``run_turn_owned``) but kept for the ``case_fn(deps, frontend, run)``
    tuple-dispatch signature.
    """
    case_id = "W4.R"
    t0 = time.monotonic()

    before = _user_skill_bodies(deps.user_skills_dir)
    try:
        seed_path, token = _seed_skill_reviewer_transcript(deps.sessions_dir)
    except Exception as exc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"seed failed: {type(exc).__name__}: {exc}",
        )

    try:
        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            await process_review(deps, "skill", seed_path.stem, persisted_message_count=None)
    except Exception as exc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"reviewer failed: {type(exc).__name__}: {exc}",
        )

    after = _user_skill_bodies(deps.user_skills_dir)
    encoded = [
        (path, body)
        for path, body in after.items()
        if token in body and token not in before.get(path, "")
    ]
    if not encoded:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=(
                f"structural gate: no user skill body newly carries token {token!r}; "
                f"skills={[p.parent.name for p in after]}"
            ),
        )

    skill_path, skill_body = encoded[0]

    # Tear down skills the reviewer newly created this run (eval residue that would
    # otherwise pollute the user's /help command surface). Only dirs absent before
    # the run are removed — a pre-existing skill the reviewer merely patched is left
    # intact. The judge below reads the captured ``skill_body`` string, not the file.
    import shutil

    for created in set(after) - set(before):
        shutil.rmtree(created.parent, ignore_errors=True)

    rubric = (
        "A skill maintainer read a short conversation and wrote/patched the skill below. "
        "The conversation contained exactly ONE durable correction: the user demanded that "
        f"every deploy must run `make {token}` as the first step and abort if it fails. The "
        "rest was transient chatter — a one-off weather question — that should NOT be encoded. "
        "PASS if the skill faithfully encodes the deploy correction (the required first step). "
        "FAIL if it fabricates a procedure not in the conversation, distorts the correction, or "
        "encodes the transient chatter. Judge faithfulness only; a faithful skill may legitimately "
        "scope or condense what it keeps."
    )
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            jverdict = await judge_with_llm(
                rubric,
                [{"role": "assistant", "content": skill_body}],
                deps=deps,
                model=deps.judge_model,
            )
    except Exception as jexc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"skill={skill_path.parent.name} | judge_error: {type(jexc).__name__}",
        )

    verdict = Verdict.PASS if jverdict.passed else Verdict.SOFT_FAIL
    reason = (
        f"skill={skill_path.parent.name} judge.score={jverdict.score} "
        f"{judge_model_annotation(deps)}"
    )
    if jverdict.rationale:
        reason += f" {jverdict.rationale[:120]}"
    return CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=time.monotonic() - t0,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# W4.M — skill merge (housekeeping consolidation)
# ---------------------------------------------------------------------------

_MERGE_SHARED_BODY = (
    "This skill documents the backend service deploy release procedure. "
    "Always pull the latest main branch before starting. "
    "Build the release artifact with the standard build command. "
    "Verify the build output before continuing the deploy. "
    "Tag the release commit so the rollout is traceable. "
    "Finally, run the {distinct} before finishing the deploy."
)


def _purge_merge_skills(user_skills_dir: Path) -> None:
    """Remove prior-run eval_merge_* skills (live + archived) to avoid stale interference."""
    import shutil

    for parent in (user_skills_dir, user_skills_dir / ".archive"):
        if not parent.exists():
            continue
        for path in parent.glob("eval_merge_*"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)


def _purge_eval_skill_fixtures(user_skills_dir: Path) -> None:
    """Remove eval-owned skill fixtures (live + archived) so they never linger.

    W4.A writes ``eval_smoke/`` and W4.M writes ``eval_merge_{a,b}_<rand>/``. Both
    default to ``user-invocable: true`` and so would surface in the user's ``/help``
    slash-command list across unrelated sessions. Run from ``main()``'s finally to
    keep the real ``~/.co-cli/skills/`` command surface clean after every run.
    (The W4.R reviewer-encoded skill is torn down inline by that case, since its
    name is model-chosen and not known here.)
    """
    import shutil

    for parent in (user_skills_dir, user_skills_dir / ".archive"):
        if not parent.exists():
            continue
        for pattern in ("eval_smoke", "eval_merge_*"):
            for path in parent.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)


def _write_merge_skill(user_skills_dir: Path, name: str, distinct_step: str) -> Path:
    """Write a user skill whose body shares a boilerplate block + one distinct final step."""
    skill_dir = user_skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = _MERGE_SHARED_BODY.format(distinct=distinct_step)
    text = (
        "---\n"
        f"name: {name}\n"
        "description: Eval merge fixture — backend deploy release procedure.\n"
        "---\n\n"
        f"{body}\n"
    )
    path = skill_dir / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


async def case_w4_m_skill_merge_preserves_distinct_steps(
    deps: CoDeps,
    frontend: EvalFrontend,
    run,
) -> CaseResult:
    """W4.M — housekeeping skill merge consolidates near-duplicates losslessly.

    The skill-side analogue of daily_chat W1.F. Seeds two user skills with a large
    shared boilerplate body (clears the token-Jaccard gate by construction, so the
    merge LLM is reached) but each carrying ONE genuinely distinct final step. Runs
    ``merge_skills`` and judges that BOTH distinct steps survive in the consolidated
    umbrella skill — a regression that drops one as "redundant" fails the judge.

    Partition note: the deterministic half — clustering threshold, canonical-anchor
    pick, sibling archival, refresh — is pytest-owned (test_skill_housekeeping.py).
    This eval owns ONLY the LLM consolidation quality (lossless umbrella), which a
    unit test cannot assert.

    ``agent``/``frontend`` are unused (calls ``merge_skills`` directly) but kept for
    the runner signature.
    """
    case_id = "W4.M"
    t0 = time.monotonic()
    suffix = secrets.token_hex(3)
    name_a = f"eval_merge_a_{suffix}"
    name_b = f"eval_merge_b_{suffix}"
    step_a = "staging smoke suite"
    step_b = "production approval check"

    try:
        _purge_merge_skills(deps.user_skills_dir)
        _write_merge_skill(deps.user_skills_dir, name_a, step_a)
        _write_merge_skill(deps.user_skills_dir, name_b, step_b)
        refresh_skills(deps)

        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            merged_count = await merge_skills(deps, HousekeepingState())
    except Exception as exc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"{type(exc).__name__}: {exc}",
        )

    live = {p.parent.name for p in deps.user_skills_dir.glob("*/SKILL.md")}
    survivors = [n for n in (name_a, name_b) if n in live]
    reason_parts = [f"merged={merged_count} survivors={survivors}"]

    # Structural gate: the cluster fused — exactly one of the pair remains live
    # (the anchor, rewritten), the other was archived.
    if not (merged_count >= 1 and len(survivors) == 1):
        reason_parts.append("structural gate: pair did not consolidate to a single umbrella")
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=" ".join(reason_parts),
        )

    umbrella_body = (deps.user_skills_dir / survivors[0] / "SKILL.md").read_text(encoding="utf-8")
    rubric = (
        "Two near-duplicate skills documenting the same backend deploy procedure were just run "
        "through a consolidation pass into the single umbrella skill below. The two originals were "
        "identical EXCEPT for their final step: one required running the STAGING SMOKE SUITE, the "
        "other required a PRODUCTION APPROVAL CHECK. These are two genuinely distinct steps.\n"
        "PASS only if BOTH distinct final steps are present in the consolidated skill below — "
        "whether kept as two listed steps or folded into one combined procedure.\n"
        "FAIL if either step was dropped (only staging OR only production survives), or a step "
        "absent from the originals was invented.\n"
        "Ignore boilerplate and wording — judge only whether both distinct steps survive."
    )
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            jverdict = await judge_with_llm(
                rubric,
                [{"role": "assistant", "content": umbrella_body}],
                deps=deps,
                model=deps.judge_model,
            )
    except Exception as jexc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"{' '.join(reason_parts)} | judge_error: {type(jexc).__name__}",
        )

    reason_parts.append(f"judge.score={jverdict.score} {judge_model_annotation(deps)}")
    if jverdict.rationale:
        reason_parts.append(jverdict.rationale[:120])
    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if jverdict.passed else Verdict.SOFT_FAIL,
        duration_s=time.monotonic() - t0,
        reason=" ".join(reason_parts),
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
    deps, frontend, stack = await make_eval_deps()
    apply_eval_window(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("skills") as run:
            try:
                cr_a = await case_w4_a_dispatch_user_skill(deps, frontend, run)
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
                cr_b = await case_w4_b_skill_selection(deps, frontend, run)
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
                cr_r = await case_skill_reviewer_encodes_correction(deps, frontend, run)
            except Exception as exc:
                cr_r = CaseResult(
                    name="W4.R",
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            cases.append(cr_r)
            run.append(cr_r)
            label = (
                "SOFT_FAIL"
                if cr_r.verdict is Verdict.SOFT_FAIL
                else ("PASS" if cr_r.passed else "FAIL")
            )
            print(f"[skills] {cr_r.name}: {label} — {cr_r.reason or 'ok'}")
            try:
                cr_m = await case_w4_m_skill_merge_preserves_distinct_steps(deps, frontend, run)
            except Exception as exc:
                cr_m = CaseResult(
                    name="W4.M",
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            cases.append(cr_m)
            run.append(cr_m)
            m_label = (
                "SOFT_FAIL"
                if cr_m.verdict is Verdict.SOFT_FAIL
                else ("PASS" if cr_m.passed else "FAIL")
            )
            print(f"[skills] {cr_m.name}: {m_label} — {cr_m.reason or 'ok'}")
    finally:
        _purge_eval_skill_fixtures(deps.user_skills_dir)
        await stack.aclose()
    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
