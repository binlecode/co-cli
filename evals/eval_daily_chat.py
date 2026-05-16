"""UAT eval — Workflow 1: Daily chat / one-shot task.

Covers the default REPL path: ``uv run co chat`` → prompt → ``run_turn`` →
personality + recall injection → tool loop with approval gates → reasoning
display → JSONL persist → dream-cycle smoke.

Cases:
  W1.A  happy_path_qualified_response   single-turn + LLM judge for on-topic + voice.
  W1.B  tool_choice_quality              prompt that should pick ``file_find``.
  W1.C  recall_used_in_response          seeded knowledge token surfaces in response.
  W1.D  dream_callable_smoke (failure)   dream cycle dry-run is callable, no lock leaks.

Specs: docs/specs/core-loop.md, prompt-assembly.md, dream.md.

Usage:
    uv run python evals/eval_daily_chat.py
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import eval_deps
from evals._judge import judge_with_llm
from evals._observability import CaseResult, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._timeouts import CALL_TIMEOUT_S, DREAM_CYCLE_BUDGET_S, TURN_BUDGET_S
from evals._trace import record_turn, scan_artifact_paths
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from co_cli.context.orchestrate import run_turn
from co_cli.memory.artifact import ArtifactKindEnum
from co_cli.memory.dream import run_dream_cycle
from co_cli.memory.frontmatter import render_frontmatter
from co_cli.tools.memory.manage import knowledge_manage

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-daily-chat.md"

_SEED_STEM = "eval_W1_seed"
_SEED_TOKEN = "MNEMONIC_TOKEN_42"
_SEED_BODY = f"{_SEED_TOKEN} my staging deploy id reminder."

_JUDGE_NOTE = "[judge_model_same_as_agent]"


def _response_text(result: Any) -> str:
    """Concatenate all assistant text parts emitted across the turn."""
    parts: list[str] = []
    for msg in getattr(result, "messages", None) or []:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content or "")
    return " ".join(parts)


def _tool_calls(result: Any) -> list[ToolCallPart]:
    """Walk the turn's messages → list of ToolCallPart entries in call order."""
    calls: list[ToolCallPart] = []
    for msg in getattr(result, "messages", None) or []:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append(part)
    return calls


def _session_line_count(path: Path) -> int:
    """Best-effort line count of the active session JSONL — 0 if absent."""
    if not path or not Path(path).exists():
        return 0
    try:
        return sum(1 for _ in Path(path).open("r", encoding="utf-8"))
    except OSError:
        return 0


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


async def _case_w1_a_happy_path(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.A — single turn, judge rubric on on-topic + voice."""
    case_id = "W1.A"
    case_t0 = time.monotonic()
    session_path = deps.session.session_path
    lines_before = _session_line_count(session_path)
    user_input = "hi, summarize my last session"
    reason_parts: list[str] = [_JUDGE_NOTE]
    passed = False
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}

    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            result, trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=user_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=run.case_trace_path(case_id),
                agent=agent,
            )
        model_call_seconds = trace.model_call_seconds
        token_usage = dict(trace.token_usage)

        outcome_ok = getattr(result, "outcome", None) == "continue"
        lines_after = _session_line_count(session_path)
        jsonl_grew = (lines_after - lines_before) >= 2
        budget_ok = model_call_seconds <= TURN_BUDGET_S
        if not budget_ok:
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {TURN_BUDGET_S}.0s")

        rubric = (
            "Did the response engage with the prompt on-topic, in the agent's voice "
            "(per soul seed)?"
        )
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(rubric, result.messages, deps=deps)
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:120])

        passed = bool(outcome_ok and verdict.passed and jsonl_grew and budget_ok)
        if not outcome_ok:
            reason_parts.append(f"outcome={getattr(result, 'outcome', None)!r}")
        if not jsonl_grew:
            reason_parts.append(f"session_jsonl_delta={lines_after - lines_before}")
    except Exception as exc:
        passed = False
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
    )


async def _case_w1_b_tool_choice(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.B — 'list files in the current directory' should pick file_find (or ls fallback)."""
    case_id = "W1.B"
    case_t0 = time.monotonic()
    user_input = "list files in the current directory"
    reason_parts: list[str] = []
    passed = False
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}

    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            result, trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=user_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=run.case_trace_path(case_id),
                agent=agent,
            )
        model_call_seconds = trace.model_call_seconds
        token_usage = dict(trace.token_usage)

        tool_calls = _tool_calls(result)
        tool_names = [tc.tool_name for tc in tool_calls]
        used_file_find = "file_find" in tool_names
        used_shell_ls = False
        for tc in tool_calls:
            if tc.tool_name != "shell_exec":
                continue
            args_repr = tc.args if isinstance(tc.args, str) else repr(tc.args)
            if "ls" in args_repr.lower():
                used_shell_ls = True
                break

        if used_file_find:
            passed = True
            reason_parts.append("file_find")
        elif used_shell_ls:
            passed = True
            reason_parts.append("[shell_fallback]")
        else:
            reason_parts.append(f"no_listing_tool tools={tool_names!r}")

        if model_call_seconds > TURN_BUDGET_S:
            passed = False
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {TURN_BUDGET_S}.0s")
    except Exception as exc:
        passed = False
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        trace_files=[str(run.case_trace_path(case_id).name)],
        reason=" ".join(reason_parts).strip(),
    )


async def _case_w1_c_recall(
    deps: Any,
    agent: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.C — seed an artifact, drive a recall-triggering turn, expect token verbatim."""
    case_id = "W1.C"
    case_t0 = time.monotonic()
    reason_parts: list[str] = []
    passed = False
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}

    try:
        _seed_knowledge_artifact(deps.knowledge_dir)
        # Re-index the knowledge dir so the FTS chunks_fts table sees the new seed.
        deps.memory_store.sync_dir("knowledge", deps.knowledge_dir)

        user_input = f"What do you remember from {_SEED_STEM}?"
        async with asyncio.timeout(CALL_TIMEOUT_S):
            result, trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=user_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=run.case_trace_path(case_id),
                agent=agent,
            )
        model_call_seconds = trace.model_call_seconds
        token_usage = dict(trace.token_usage)

        response_text = _response_text(result)
        token_present = _SEED_TOKEN in response_text
        if token_present:
            passed = True
            reason_parts.append("token_in_response")
        else:
            reason_parts.append("token_missing_in_response")

        if model_call_seconds > TURN_BUDGET_S:
            passed = False
            reason_parts.append(f"[slow] {model_call_seconds:.1f}s vs budget {TURN_BUDGET_S}.0s")
    except Exception as exc:
        passed = False
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        passed=passed,
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
    """W1.D — boundary: run_dream_cycle(dry_run=True) is callable, leaves no locks or mutations."""
    case_id = "W1.D"
    case_t0 = time.monotonic()
    reason_parts: list[str] = []
    passed = False

    try:
        scan_roots = [deps.knowledge_dir]
        before = scan_artifact_paths(scan_roots)
        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            await run_dream_cycle(deps, knowledge_manage, dry_run=True)
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
        passed=passed,
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
            _case_w1_a_happy_path,
            _case_w1_b_tool_choice,
            _case_w1_c_recall,
            _case_w1_d_dream_smoke,
        ):
            try:
                case = await runner(deps, agent, frontend, run)
            except Exception as exc:
                case = CaseResult(
                    name=runner.__name__,
                    passed=False,
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
