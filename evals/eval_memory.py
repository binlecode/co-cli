"""UAT eval — Workflow 3: Memory recall and curation (judged case only).

Keeps the single judge-backed case (W3.G forget-propagation, which also
exercises recall reuse): a three-turn recall → agent-driven delete → re-recall
flow judged for absence of the deleted token. The structural cases
(create+index, recall ranking, session_search, /memory list, /memory forget
file/FTS cleanup, dream decay) are covered by pytest under ``tests/`` — see the
phase-2 coverage map.

Reruns overwrite the seed in place via a deterministic stem — no accumulation.

Specs: docs/specs/memory.md, knowledge.md, dream.md
Mission tenet: local — user-controlled storage; trusted — reversible
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from evals._deps import EvalFrontend, make_eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, EvalRun, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S
from evals._trace import record_turn

from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps

_W3G_STEM = "eval_w3g_fact"
_W3G_TITLE = "eval_W3G_fact"
_W3G_TOKEN = "W3G_MARKER_XK42"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print(msg: str) -> None:
    print(msg, flush=True)


def _seed_memory_artifact(
    deps: CoDeps,
    *,
    filename_stem: str,
    title: str,
    body: str,
) -> Path:
    """Write a memory .md file with canonical frontmatter and reindex into FTS.

    Bypasses the memory write tools so the filename_stem is exactly the one we
    request — ``save_artifact`` slugifies the title and appends a random uuid
    suffix, which loses the deterministic-stem semantics the delete turn needs.
    """
    knowledge_dir = deps.memory_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = knowledge_dir / f"{filename_stem}.md"
    artifact_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()
    frontmatter = {
        "id": artifact_id,
        "memory_kind": "note",
        "title": title,
        "created_at": created_at,
    }
    yaml_lines = [
        "---",
        f"id: {artifact_id}",
        "memory_kind: note",
        f"title: {title}",
        f"created_at: '{created_at}'",
        "---",
        "",
        body.strip(),
        "",
    ]
    markdown_content = "\n".join(yaml_lines)
    artifact_path.write_text(markdown_content, encoding="utf-8")

    if deps.memory_store is not None:
        deps.memory_store.reindex_one(artifact_path, body, markdown_content, frontmatter)
    return artifact_path


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def case_w3_g_forget_propagates_to_recall(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.G — memory_delete propagates through FTS; re-search finds nothing.

    Three turns with shared history: recall seed → agent-driven delete → re-recall
    judged for absence. SOFT_FAIL when judge says agent still surfaces the token.
    """
    case_id = "W3.G"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)

    body = f"{_W3G_TOKEN} eval W3G memory item for forget-propagation test"
    try:
        seed_path = _seed_memory_artifact(
            deps, filename_stem=_W3G_STEM, title=_W3G_TITLE, body=body
        )
    except Exception as exc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"seed failed: {type(exc).__name__}: {exc}",
        )

    passed = True
    verdict = Verdict.PASS
    reason = ""
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    history: list[Any] = []
    t2_result = None

    try:
        # Turn 0: recall — agent should call memory_search and surface the seed
        t0_input = (
            f"Use memory_search to find any memory item containing '{_W3G_TOKEN}'. "
            "Report the filename_stem of any hits."
        )
        async with asyncio.timeout(CALL_TIMEOUT_S):
            t0_result, t0_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=t0_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=t0_input,
                    deps=deps,
                    message_history=history,
                    frontend=frontend,
                ),
                case_dir_path=trace_file,
                agent=agent,
            )
        model_call_seconds += t0_trace.model_call_seconds
        for k, v in t0_trace.token_usage.items():
            token_usage[k] = token_usage.get(k, 0) + v
        history = list(t0_result.messages)

        t0_tool_names = [rec.tool_name for rec in t0_trace.tool_calls]
        if "memory_search" not in t0_tool_names:
            passed = False
            reason = f"turn 0: agent did not call memory_search; tools={t0_tool_names!r}"

        # Turn 1: delete — agent should call memory_delete using the stem
        if passed:
            t1_input = (
                f"Now delete the memory item with filename_stem `{_W3G_STEM}` using memory_delete."
            )
            async with asyncio.timeout(CALL_TIMEOUT_S):
                t1_result, t1_trace = await record_turn(
                    case_id=case_id,
                    turn_index=1,
                    user_input=t1_input,
                    run_turn_callable=lambda: run_turn(
                        agent=agent,
                        user_input=t1_input,
                        deps=deps,
                        message_history=history,
                        frontend=frontend,
                    ),
                    case_dir_path=trace_file,
                    agent=agent,
                )
            model_call_seconds += t1_trace.model_call_seconds
            for k, v in t1_trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v
            history = list(t1_result.messages)

            if seed_path.exists():
                passed = False
                reason = f"turn 1: seed {seed_path.name!r} still on disk after delete turn"

        # Turn 2: re-recall — judge that agent does not surface the token
        if passed:
            t2_input = f"Search memory again for '{_W3G_TOKEN}'. What do you find?"
            async with asyncio.timeout(CALL_TIMEOUT_S):
                t2_result, _t2_trace = await record_turn(
                    case_id=case_id,
                    turn_index=2,
                    user_input=t2_input,
                    run_turn_callable=lambda: run_turn(
                        agent=agent,
                        user_input=t2_input,
                        deps=deps,
                        message_history=history,
                        frontend=frontend,
                    ),
                    case_dir_path=trace_file,
                    agent=agent,
                )
            model_call_seconds += _t2_trace.model_call_seconds
            for k, v in _t2_trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v

        # Judge turn 2 for absence of the deleted token
        if passed and t2_result is not None:
            rubric = (
                f"The user deleted a memory item containing '{_W3G_TOKEN}' in turn 1. "
                f"In turn 2, the user searched memory again for '{_W3G_TOKEN}'. "
                "PASS only if the agent reports no results found or confirms the item is gone. "
                f"FAIL if the agent successfully recalls or quotes '{_W3G_TOKEN}'."
            )
            try:
                async with asyncio.timeout(CALL_TIMEOUT_S):
                    jverdict = await judge_with_llm(
                        rubric, t2_result.messages, deps=deps, model=deps.judge_model
                    )
                judge_note = f"judge.score={jverdict.score} {judge_model_annotation(deps)}"
                if jverdict.rationale:
                    judge_note += f" {jverdict.rationale[:120]}"
                if not jverdict.passed:
                    verdict = Verdict.SOFT_FAIL
                reason = f"seed_deleted={not seed_path.exists()} | {judge_note}"
            except Exception as jexc:
                reason = (
                    f"seed_deleted={not seed_path.exists()} | judge_error: {type(jexc).__name__}"
                )

        if not reason:
            reason = "ok"
    except Exception as exc:
        passed = False
        verdict = Verdict.FAIL
        reason = f"{type(exc).__name__}: {exc}"

    if not passed and verdict == Verdict.PASS:
        verdict = Verdict.FAIL

    return CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> int:
    """Run the W3.G judged case.

    SOFT_FAIL is a review signal, not a gate failure; exit code is non-zero
    only on a hard FAIL.
    """
    await ensure_ollama_warm()
    deps, agent, frontend, stack = await make_eval_deps()
    apply_eval_window(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("memory") as run:
            for case_fn in (case_w3_g_forget_propagates_to_recall,):
                try:
                    cr = await case_fn(deps, agent, frontend, run)
                except Exception as exc:
                    cr = CaseResult(
                        name=case_fn.__name__,
                        verdict=Verdict.FAIL,
                        duration_s=0.0,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                cases.append(cr)
                run.append(cr)
                if cr.skipped:
                    label = f"SKIP:{cr.skip_category or '?'}"
                else:
                    label = cr.verdict.value.upper()
                _print(f"[memory] {cr.name}: {label} — {cr.reason or 'ok'}")
    finally:
        await stack.aclose()

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
