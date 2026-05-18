"""UAT eval — Workflow 1: Daily chat (multi-turn conversation).

Drives the default REPL path: ``uv run co chat`` → multi-turn dialogue →
``run_turn`` with carried ``message_history`` → tool loop with approval gates
→ reasoning display → dream-cycle smoke. Each conversational case runs
2-3 turns so context retention and tool chaining — the parts of agent
behavior that single-turn evals can't see — are actually exercised.

Cases:
  W1.A  multi_turn_coherence    3-turn ask → follow-up → recap. Judge rubric on coherence + voice.
  W1.B  tool_chain              2-turn: list files → read a listed file.
  W1.C  recall_reuse            2-turn: memory_view a seed → follow-up uses the recalled content.
  W1.D  dream_callable_smoke    Boundary: ``run_dream_cycle(dry_run=True)`` callable, no lock leaks.

Specs: docs/specs/core-loop.md, prompt-assembly.md, dream.md.

Usage:
    uv run python evals/eval_daily_chat.py
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._timeouts import (
    CALL_TIMEOUT_S,
    DREAM_CYCLE_BUDGET_S,
    TOOL_TURN_BUDGET_S,
    TURN_BUDGET_S,
)
from evals._trace import record_turn, scan_artifact_paths
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from co_cli.context.orchestrate import run_turn
from co_cli.memory.artifact import ArtifactKindEnum
from co_cli.memory.dream import run_dream_cycle
from co_cli.memory.frontmatter import render_frontmatter
from co_cli.tools.memory.manage import memory_manage

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-daily-chat.md"

_SEED_STEM = "eval_W1_seed"
_SEED_TOKEN = "MNEMONIC_TOKEN_42"
_SEED_BODY = f"{_SEED_TOKEN} my staging deploy id reminder."


@dataclass(frozen=True)
class _TurnSlice:
    """Per-turn view of a multi-turn drive.

    ``assistant_text`` and ``tool_calls`` cover ONLY the messages added during
    this turn — not the cumulative history. The cumulative history lives on
    ``result.messages`` and is what the next turn carries forward.
    """

    result: Any
    trace: Any
    new_messages: list[Any]
    assistant_text: str
    tool_calls: list[ToolCallPart]


def _assistant_text_from(messages: list[Any]) -> str:
    """Concatenate ``TextPart.content`` across the given assistant messages."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content or "")
    return " ".join(parts)


def _tool_calls_from(messages: list[Any]) -> list[ToolCallPart]:
    """Extract ToolCallParts in call order across the given assistant messages."""
    calls: list[ToolCallPart] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append(part)
    return calls


def _used_shell_command(calls: list[ToolCallPart], cmd: str) -> bool:
    """True if any ``shell_exec`` call's args mention the given command substring."""
    for tc in calls:
        if tc.tool_name != "shell_exec":
            continue
        args_repr = tc.args if isinstance(tc.args, str) else repr(tc.args)
        if cmd in args_repr.lower():
            return True
    return False


def _aggregate_trace_stats(slices: list[_TurnSlice]) -> tuple[float, dict[str, int]]:
    """Sum ``model_call_seconds`` and ``token_usage`` across turns."""
    total_seconds = sum(getattr(s.trace, "model_call_seconds", 0.0) for s in slices)
    totals: dict[str, int] = {}
    for s in slices:
        for k, v in (getattr(s.trace, "token_usage", None) or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    return total_seconds, totals


def _seed_knowledge_artifact(knowledge_dir: Path) -> Path:
    """Write the W1.C seed artifact to disk under a deterministic filename.

    The path is fixed (``eval_W1_seed.md``) so reruns overwrite in place per
    Behavioral Constraint #12 (no accumulation). Body is < 100 chars and opens
    with the distinctive token so the recall snippet (capped at 100 chars in
    ``co_cli/tools/memory/recall.py``) actually carries the token.
    """
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = knowledge_dir / f"{_SEED_STEM}.md"
    frontmatter = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, _SEED_STEM)),
        "kind": "knowledge",
        "artifact_kind": ArtifactKindEnum.USER.value,
        "title": _SEED_STEM,
        "created": datetime.now(UTC).isoformat(),
    }
    artifact_path.write_text(
        render_frontmatter(frontmatter, _SEED_BODY),
        encoding="utf-8",
    )
    return artifact_path


def _dream_lock_keys(deps: Any) -> list[str]:
    """Return any resource-lock keys mentioning 'dream' (lower-cased substring)."""
    locks = getattr(getattr(deps, "resource_locks", None), "_locks", None)
    if not isinstance(locks, dict):
        return []
    return [k for k in locks if "dream" in str(k).lower()]


async def _drive_turns(
    *,
    case_id: str,
    deps: Any,
    agent: Any,
    frontend: Any,
    case_dir_path: Path,
    inputs: list[str],
) -> list[_TurnSlice]:
    """Drive N user turns carrying ``message_history`` forward.

    Each turn gets its own ``CALL_TIMEOUT_S`` budget. ``record_turn`` writes
    every turn under the same case JSONL with a distinct ``turn_index``. The
    returned slices expose per-turn assistant text and tool calls so checks
    can target a specific turn rather than the cumulative history.
    """
    history: list[Any] = []
    slices: list[_TurnSlice] = []
    for i, user_input in enumerate(inputs):
        prior_len = len(history)
        async with asyncio.timeout(CALL_TIMEOUT_S):
            result, trace = await record_turn(
                case_id=case_id,
                turn_index=i,
                user_input=user_input,
                run_turn_callable=(
                    lambda h=history, ui=user_input: run_turn(
                        agent=agent,
                        user_input=ui,
                        deps=deps,
                        message_history=h,
                        frontend=frontend,
                    )
                ),
                case_dir_path=case_dir_path,
                agent=agent,
            )
        history = list(result.messages)
        new_msgs = history[prior_len:]
        slices.append(
            _TurnSlice(
                result=result,
                trace=trace,
                new_messages=new_msgs,
                assistant_text=_assistant_text_from(new_msgs),
                tool_calls=_tool_calls_from(new_msgs),
            )
        )
    return slices


async def _case_w1_a_multi_turn_coherence(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.A — 3-turn coherence + voice.

    Turn 0 opens a technical thread; turn 1 is a follow-up that only makes
    sense in context; turn 2 forces a recap. The judge rubric PASSes only if
    every turn is substantive, turn 1 builds on turn 0 instead of restarting,
    turn 2 actually summarizes the prior discussion concretely, and the voice
    stays consistent across turns.

    Single-turn rubrics can't catch context drift, hallucinated recaps, or
    persona resets — this case is here precisely for those failure modes.
    """
    case_id = "W1.A"
    case_t0 = time.monotonic()
    inputs = [
        "Hi — I'm weighing sqlite vs duckdb for a 50GB analytics workload. Help me think it through.",
        "What about query latency for ad-hoc analytical reads?",
        "Summarize the tradeoffs we just discussed in 3 bullet points.",
    ]
    reason_parts: list[str] = [judge_model_annotation(deps)]
    passed = False
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}

    try:
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            agent=agent,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=inputs,
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        outcomes = [getattr(s.result, "outcome", None) for s in slices]
        all_continue = all(o == "continue" for o in outcomes)
        per_turn_text = [s.assistant_text.strip() for s in slices]
        all_nonempty = all(t for t in per_turn_text)

        budget = TURN_BUDGET_S * len(inputs)
        budget_ok = model_call_seconds <= budget
        if not budget_ok:
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")

        rubric = (
            "You are reviewing a 3-turn user/assistant conversation. PASS only if ALL hold:\n"
            "(a) every assistant turn is substantive and on-topic — not empty, error, or refusal;\n"
            "(b) turn 2 builds on turn 1's context (the sqlite/duckdb framing) and does NOT "
            "restart or ask 'what were we discussing';\n"
            "(c) turn 3 concretely summarizes the prior two turns (mentions sqlite/duckdb "
            "tradeoffs by name) rather than producing a generic database-comparison answer;\n"
            "(d) voice is consistent across turns — no jarring tonal shift or persona reset.\n"
            "FAIL on: empty turn, refusal, off-topic drift, generic summary that ignores prior "
            "turns, or context regression."
        )
        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        passed = bool(all_continue and all_nonempty and verdict.passed and budget_ok)
        if not all_continue:
            reason_parts.append(f"outcomes={outcomes!r}")
        if not all_nonempty:
            empty_idx = [i for i, t in enumerate(per_turn_text) if not t]
            reason_parts.append(f"empty_turns={empty_idx!r}")
    except Exception as exc:
        passed = False
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
    )


async def _case_w1_b_tool_chain(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.B — 2-turn tool chain.

    Turn 0 asks for a directory listing (expects ``file_find`` or ``shell_exec ls``).
    Turn 1 asks to read a specific file by name (expects ``file_read`` or
    ``shell_exec cat/head``). PASS requires both turns picked an appropriate
    tool — the second turn proves the agent can carry on after a tool result
    and pick a different tool for a different ask.
    """
    case_id = "W1.B"
    case_t0 = time.monotonic()
    inputs = [
        "list files in the current directory",
        "now show me the contents of pyproject.toml",
    ]
    reason_parts: list[str] = []
    passed = False
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}

    try:
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            agent=agent,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=inputs,
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        t0_calls = slices[0].tool_calls
        t1_calls = slices[1].tool_calls
        t0_names = [tc.tool_name for tc in t0_calls]
        t1_names = [tc.tool_name for tc in t1_calls]

        t0_tool_ok = "file_find" in t0_names or _used_shell_command(t0_calls, "ls")
        t1_tool_ok = (
            "file_read" in t1_names
            or "file_view" in t1_names
            or _used_shell_command(t1_calls, "cat")
            or _used_shell_command(t1_calls, "head")
        )

        # Tool-effect verification: the response must reflect actual tool output,
        # not a hallucinated listing / file body. Without this floor, an agent
        # could emit ceremonial tool calls and confabulate the rest.
        t0_text = slices[0].assistant_text
        t1_text = slices[1].assistant_text
        t0_effect_ok = any(
            stem in t0_text for stem in ("pyproject.toml", "README.md", "CLAUDE.md")
        )
        t1_effect_ok = "[project]" in t1_text

        t0_ok = t0_tool_ok and t0_effect_ok
        t1_ok = t1_tool_ok and t1_effect_ok

        if t0_ok and t1_ok:
            passed = True
            reason_parts.append(f"t0={t0_names!r} t1={t1_names!r} effects=ok")
        else:
            if not t0_tool_ok:
                reason_parts.append(f"t0_no_listing tools={t0_names!r}")
            elif not t0_effect_ok:
                reason_parts.append(f"t0_no_listed_file_in_response text={t0_text[:120]!r}")
            if not t1_tool_ok:
                reason_parts.append(f"t1_no_read tools={t1_names!r}")
            elif not t1_effect_ok:
                reason_parts.append(f"t1_no_pyproject_marker text={t1_text[:120]!r}")

        budget = TOOL_TURN_BUDGET_S * len(inputs)
        if model_call_seconds > budget:
            passed = False
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")
    except Exception as exc:
        passed = False
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
    )


async def _case_w1_c_recall_reuse(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.C — 2-turn recall + reuse.

    Turn 0 forces a knowledge-channel read of the seeded artifact and expects
    the token verbatim. Turn 1 asks a follow-up that only the seed content
    can answer — proving the recalled snippet persisted into working memory
    rather than vanishing after turn 0. Turn 1 may or may not re-tool; the
    check is that the answer references the seed content correctly.
    """
    case_id = "W1.C"
    case_t0 = time.monotonic()
    reason_parts: list[str] = []
    passed = False
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}

    try:
        _seed_knowledge_artifact(deps.memory_dir)
        deps.memory_store.sync_dir(deps.memory_dir)

        inputs = [
            (
                f"Use the `memory_view` tool to read the artifact whose filename_stem is "
                f"`{_SEED_STEM}`, then quote its body verbatim in your response."
            ),
            "In one sentence, what did that snippet say the token referred to?",
        ]
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            agent=agent,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=inputs,
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        t0_names = [tc.tool_name for tc in slices[0].tool_calls]
        t0_knowledge = any(n in {"memory_view", "memory_search"} for n in t0_names)
        t0_token = _SEED_TOKEN in slices[0].assistant_text

        t1_text_lower = slices[1].assistant_text.lower()
        t1_uses_seed = (
            "staging" in t1_text_lower
            or "deploy" in t1_text_lower
            or _SEED_TOKEN.lower() in t1_text_lower
        )

        t0_ok = t0_knowledge or t0_token
        if t0_ok and t1_uses_seed:
            passed = True
            reason_parts.append(f"t0_knowledge={t0_knowledge} t0_token={t0_token} t1_seed=True")
        else:
            reason_parts.append(
                f"t0_knowledge={t0_knowledge} t0_token={t0_token} "
                f"t1_seed={t1_uses_seed} t1_text={slices[1].assistant_text[:120]!r}"
            )

        budget = TURN_BUDGET_S * len(inputs)
        if model_call_seconds > budget:
            passed = False
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {budget:.0f}s")
    except Exception as exc:
        passed = False
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
    )


async def _case_w1_d_dream_smoke(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.D — boundary: ``run_dream_cycle(dry_run=True)`` is callable, leaves no locks or mutations."""
    case_id = "W1.D"
    case_t0 = time.monotonic()
    reason_parts: list[str] = []
    passed = False

    try:
        scan_roots = [deps.memory_dir]
        before = scan_artifact_paths(scan_roots)
        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            await run_dream_cycle(deps, memory_manage, dry_run=True)
        after = scan_artifact_paths(scan_roots)

        lock_leaks = _dream_lock_keys(deps)
        no_lock_leak = len(lock_leaks) == 0
        no_mutation = before == after

        if no_lock_leak and no_mutation:
            passed = True
            reason_parts.append("dry_run_clean")
        else:
            if not no_lock_leak:
                reason_parts.append(f"dream_lock_leak={lock_leaks!r}")
            if not no_mutation:
                added = sorted(set(after) - set(before))
                removed = sorted(set(before) - set(after))
                reason_parts.append(f"knowledge_diff added={added!r} removed={removed!r}")
    except Exception as exc:
        passed = False
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=duration,
        model_call_seconds=0.0,
        token_usage={},
        trace_files=[],
        reason=" ".join(reason_parts).strip(),
    )


async def main() -> int:
    """Drive W1.A-W1.D end-to-end, write trace + REPORT, return exit code."""
    await ensure_ollama_warm()

    async with eval_deps() as (deps, agent, frontend), open_eval_run("daily_chat") as run:
        cases: list[CaseResult] = []

        for runner in (
            _case_w1_a_multi_turn_coherence,
            _case_w1_b_tool_chain,
            _case_w1_c_recall_reuse,
            _case_w1_d_dream_smoke,
        ):
            try:
                case = await runner(deps, agent, frontend, run)
            except Exception as exc:
                case = CaseResult(
                    name=runner.__name__,
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason=f"runner_crash: {type(exc).__name__}: {exc}",
                )
            run.append(case)
            verdict = "PASS" if case.passed else "FAIL"
            print(f"[daily_chat] {case.name}: {verdict} — {case.reason or 'ok'}")
            cases.append(case)

        prepend_report(
            _REPORT_PATH,
            "daily_chat",
            run.iso,
            cases,
            run_dir=run.dir,
        )

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
