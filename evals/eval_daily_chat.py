"""UAT eval — Workflow 1: Daily chat (multi-turn conversation).

Drives the default REPL path: ``uv run co chat`` → multi-turn dialogue →
``run_turn_owned`` with carried ``message_history`` → tool loop with approval gates
→ reasoning display → dream-cycle merge. Each conversational case runs
2-3 turns so context retention and tool chaining — the parts of agent
behavior that single-turn evals can't see — are actually exercised.

Cases:
  W1.A  multi_turn_coherence        3-turn ask → follow-up → recap. Judge rubric on coherence + voice.
  W1.D  dream_propagates_to_recall  Seed pair → dream merge → merged artifact in active store.
  W1.F  merge_preserves_distinct_facts  Lexically-similar/distinct pair clusters; both facts survive the merge.

Specs: docs/specs/core-loop.md, prompt-assembly.md, dream.md.
Mission tenet: for knowledge work — synthesis + voice; trusted — inspectable

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

from evals._deps import drive_turn, eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import (
    CALL_TIMEOUT_S,
    DREAM_CYCLE_BUDGET_S,
    TURN_BUDGET_S,
)
from evals._trace import record_turn, response_text
from pydantic_ai.messages import (
    ModelResponse,
    ToolCallPart,
)

from co_cli.daemons.dream._housekeeping import merge_memory
from co_cli.daemons.dream.state import HousekeepingState
from co_cli.memory.frontmatter import render_frontmatter
from co_cli.memory.item import MemoryKindEnum


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


def _tool_calls_from(messages: list[Any]) -> list[ToolCallPart]:
    """Extract ToolCallParts in call order across the given assistant messages."""
    calls: list[ToolCallPart] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append(part)
    return calls


def _aggregate_trace_stats(slices: list[_TurnSlice]) -> tuple[float, dict[str, int]]:
    """Sum ``model_call_seconds`` and ``token_usage`` across turns."""
    total_seconds = sum(getattr(s.trace, "model_call_seconds", 0.0) for s in slices)
    totals: dict[str, int] = {}
    for s in slices:
        for k, v in (getattr(s.trace, "token_usage", None) or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    return total_seconds, totals


_DREAM_A_STEM = "eval_w1d_pair_a"
_DREAM_B_STEM = "eval_w1d_pair_b"
_DREAM_SHARED_TOKEN = "EVAL_W1D_KEY_NX7"

# Pair bodies: nearly identical so token_jaccard >= 0.75 triggers dream-cycle merge.
# Unique tokens: {eval_w1d_key_nx7, staging, deploy, identifier, note, eval, pipeline, alpha/beta}
# Intersection=7, Union=9, Jaccard≈0.78 >= 0.75 threshold.
_DREAM_A_BODY = f"{_DREAM_SHARED_TOKEN} staging deploy identifier note eval pipeline alpha"
_DREAM_B_BODY = f"{_DREAM_SHARED_TOKEN} staging deploy identifier note eval pipeline beta"


def _purge_dream_pair(memory_dir: Path) -> None:
    """Remove dream-pair seed files from memory_dir to prevent stale-seed interference."""
    for stem in (_DREAM_A_STEM, _DREAM_B_STEM):
        target = memory_dir / f"{stem}.md"
        target.unlink(missing_ok=True)


def _seed_dream_pair(memory_dir: Path) -> tuple[Path, Path]:
    """Write two near-identical memory items whose token Jaccard >= 0.75.

    Returns ``(path_a, path_b)`` so the caller can assert they were archived.
    """
    memory_dir.mkdir(parents=True, exist_ok=True)

    def _write(stem: str, body: str) -> Path:
        path = memory_dir / f"{stem}.md"
        frontmatter_dict = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, stem)),
            "memory_kind": MemoryKindEnum.NOTE.value,
            "title": stem,
            "created_at": datetime.now(UTC).isoformat(),
        }
        path.write_text(render_frontmatter(frontmatter_dict, body), encoding="utf-8")
        return path

    return _write(_DREAM_A_STEM, _DREAM_A_BODY), _write(_DREAM_B_STEM, _DREAM_B_BODY)


_DISTINCT_A_STEM = "eval_w1f_distinct_a"
_DISTINCT_B_STEM = "eval_w1f_distinct_b"


def _purge_distinct_pair(memory_dir: Path) -> None:
    """Remove W1.F distinct-pair seed files to prevent stale-seed interference."""
    for stem in (_DISTINCT_A_STEM, _DISTINCT_B_STEM):
        (memory_dir / f"{stem}.md").unlink(missing_ok=True)


def _seed_distinct_pair(memory_dir: Path) -> tuple[Path, Path, str]:
    """Write two items that clear the Jaccard gate yet carry two DISTINCT facts.

    Built exactly like the W1.D pair so the deterministic Jaccard gate *guarantees*
    clustering by construction — the model is reached regardless of whether it then
    fuses or keeps the pair distinct (``merge_memory``'s int return cannot tell those
    apart, so clustering is established here, not at runtime). The pair diverges from
    W1.D only in payload: each body carries a genuinely different fact (one about
    backup timing, one about certificate renewal), not an alpha/beta variant.

    The per-run token is folded into the *substance* of each fact (``backups<run>``,
    ``certificate<run>``) rather than carried as a standalone filler marker — the
    merge prompt explicitly drops filler ("keep it short") but preserves facts, so a
    run token embedded in a fact survives a lossless fuse and stays locatable.

    Token accounting (lowercased, single-char + STOPWORDS dropped, set-based):
      shared (15): {production, server, maintenance, runbook, scheduled, recurring,
                    operations, team, standard, procedure, documented, internal,
                    reference, notes, baseline}
      unique A (2): {backups<run>, sunday}   unique B (2): {certificate<run>, march}
      Jaccard = 15 / (15 + 4) = 0.789 >= 0.75 threshold.

    Returns ``(path_a, path_b, run)`` — ``run`` (embedded in both fact phrases)
    locates this run's surviving/consolidated bodies among unrelated store items.
    """
    memory_dir.mkdir(parents=True, exist_ok=True)
    run = uuid.uuid4().hex[:8]
    shared = (
        "production server maintenance runbook scheduled recurring operations team "
        "standard procedure documented internal reference notes baseline"
    )
    body_a = f"{shared} backups{run} sunday"
    body_b = f"{shared} certificate{run} march"

    def _write(stem: str, body: str) -> Path:
        path = memory_dir / f"{stem}.md"
        frontmatter_dict = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, stem)),
            "memory_kind": MemoryKindEnum.NOTE.value,
            "title": stem,
            "created_at": datetime.now(UTC).isoformat(),
        }
        path.write_text(render_frontmatter(frontmatter_dict, body), encoding="utf-8")
        return path

    return _write(_DISTINCT_A_STEM, body_a), _write(_DISTINCT_B_STEM, body_b), run


async def _drive_turns(
    *,
    case_id: str,
    deps: Any,
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
                prior_message_count=prior_len,
                run_turn_callable=(
                    lambda h=history, ui=user_input: drive_turn(
                        user_input=ui,
                        deps=deps,
                        message_history=h,
                        frontend=frontend,
                    )
                ),
                case_dir_path=case_dir_path,
            )
        history = list(result.messages)
        new_msgs = history[prior_len:]
        slices.append(
            _TurnSlice(
                result=result,
                trace=trace,
                new_messages=new_msgs,
                assistant_text=response_text(result),
                tool_calls=_tool_calls_from(new_msgs),
            )
        )
    return slices


async def _case_w1_a_multi_turn_coherence(
    deps: Any,
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
        reason=" ".join(reason_parts).strip(),
    )


async def _case_w1_d_dream_propagates_to_recall(
    deps: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.D — dream cycle merges seed pair; agent recalls merged artifact via shared token."""
    case_id = "W1.D"
    case_t0 = time.monotonic()
    reason_parts: list[str] = [judge_model_annotation(deps)]
    case_verdict = Verdict.FAIL
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}

    try:
        _purge_dream_pair(deps.memory_dir)
        path_a, path_b = _seed_dream_pair(deps.memory_dir)

        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            hk_state = HousekeepingState()
            merged_count = await merge_memory(deps, hk_state)

        merged_positive = merged_count > 0
        a_archived = not path_a.exists()
        b_archived = not path_b.exists()
        # Merge consolidates the cluster into a fresh item and archives ALL originals
        # (both seeds gone), rather than folding one into the other. The collapse is
        # proven by both originals archiving and the shared token surviving in the
        # new consolidated item.
        both_archived = a_archived and b_archived

        active_files = [
            p for p in deps.memory_dir.glob("*.md") if p not in (path_a, path_b) and p.is_file()
        ]
        token_in_merged = any(
            _DREAM_SHARED_TOKEN in p.read_text(encoding="utf-8") for p in active_files
        )

        reason_parts.append(
            f"merged={merged_count} archived_a={a_archived} "
            f"archived_b={b_archived} token_in_merged={token_in_merged}"
        )

        if not (merged_positive and both_archived and token_in_merged):
            duration = time.monotonic() - case_t0
            return CaseResult(
                name=case_id,
                verdict=Verdict.FAIL,
                duration_s=duration,
                model_call_seconds=model_call_seconds,
                token_usage=token_usage,
                reason=" ".join(reason_parts).strip(),
            )

        # Structural gate passed — now drive judged agent turn.
        slices = await _drive_turns(
            case_id=case_id,
            deps=deps,
            frontend=frontend,
            case_dir_path=run.case_trace_path(case_id),
            inputs=[
                f"Search your memory for anything related to '{_DREAM_SHARED_TOKEN}' "
                "and summarize what you find in one sentence."
            ],
        )
        model_call_seconds, token_usage = _aggregate_trace_stats(slices)

        rubric = (
            f"PASS only if the agent's response mentions '{_DREAM_SHARED_TOKEN}' by citing "
            "a single merged memory artifact — not two separate items with different bodies.\n"
            f"FAIL if the agent says it found nothing, cites two distinct '{_DREAM_SHARED_TOKEN}' "
            "artifacts separately (duplicate citation of the archived sibling), or does not use "
            "a memory tool.\n"
            "Score 6-7 if the token appears but the agent hedges or the merge context is ambiguous."
        )
        final_history = slices[-1].result.messages if slices else []
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric, final_history, deps=deps, model=deps.judge_model
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])

        if verdict.passed:
            case_verdict = Verdict.PASS
        elif verdict.score >= 6:
            # SOFT_FAIL on borderline miss — per spec § 2 verdict taxonomy.
            case_verdict = Verdict.SOFT_FAIL
        else:
            case_verdict = Verdict.FAIL
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=" ".join(reason_parts).strip(),
    )


async def _case_w1_f_merge_preserves_distinct_facts(
    deps: Any,
    frontend: Any,
    run: Any,
) -> CaseResult:
    """W1.F — merge over-merge guard: a lexically-similar pair carrying two DISTINCT facts.

    The complement of W1.D (which proves a genuine duplicate pair fuses). Here the
    seed pair clears the Jaccard gate *by construction* (so the model is reached),
    but carries two genuinely distinct facts. Judges that BOTH facts survive the
    pass — whether the model kept two items or fused them losslessly. A regression
    that conflates distinct facts (dropping one as "redundant") fails the judge.
    """
    case_id = "W1.F"
    case_t0 = time.monotonic()
    reason_parts: list[str] = [judge_model_annotation(deps)]
    case_verdict = Verdict.FAIL

    try:
        _purge_distinct_pair(deps.memory_dir)
        _path_a, _path_b, run = _seed_distinct_pair(deps.memory_dir)

        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            merged_count = await merge_memory(deps, HousekeepingState())

        # Locate this run's surviving bodies by the run token folded into each fact —
        # location-agnostic across the merge's possible outcomes (two items kept, a
        # new consolidated item, or an in-place anchor merge). The run token is unique
        # per run, so this never picks up unrelated store items or prior-run residue.
        candidates = [
            p
            for p in deps.memory_dir.glob("*.md")
            if p.is_file() and run in p.read_text(encoding="utf-8")
        ]
        reason_parts.append(f"merged={merged_count} surviving_bodies={len(candidates)}")

        if not candidates:
            reason_parts.append("structural: no surviving body carries this run's facts")
            duration = time.monotonic() - case_t0
            return CaseResult(
                name=case_id,
                verdict=Verdict.FAIL,
                duration_s=duration,
                reason=" ".join(reason_parts).strip(),
            )

        bodies = "\n\n---\n\n".join(p.read_text(encoding="utf-8") for p in candidates)
        rubric = (
            "Two memory notes were just run through a consolidation pass. One note recorded "
            "that BACKUPS run on SUNDAY; the other that a CERTIFICATE renews in MARCH. These "
            "are two genuinely distinct facts, not variants of one.\n"
            "PASS only if BOTH facts are present across the surviving memory item(s) below — "
            "whether kept as two separate items or fused into one combined item.\n"
            "FAIL if either fact was dropped (e.g. only the backup fact OR only the certificate "
            "fact survives), or a fact absent from the originals was invented.\n"
            "Ignore filler and boilerplate wording — judge only whether both distinct facts survive."
        )
        async with asyncio.timeout(CALL_TIMEOUT_S):
            verdict = await judge_with_llm(
                rubric,
                [{"role": "assistant", "content": bodies}],
                deps=deps,
                model=deps.judge_model,
            )
        reason_parts.append(f"judge.score={verdict.score}")
        if verdict.rationale:
            reason_parts.append(verdict.rationale[:160])
        case_verdict = Verdict.PASS if verdict.passed else Verdict.SOFT_FAIL
    except Exception as exc:
        case_verdict = Verdict.FAIL
        reason_parts.append(f"exception: {type(exc).__name__}: {exc}")

    duration = time.monotonic() - case_t0
    return CaseResult(
        name=case_id,
        verdict=case_verdict,
        duration_s=duration,
        reason=" ".join(reason_parts).strip(),
    )


async def main() -> int:
    """Drive W1.A / W1.D / W1.F end-to-end, write trace, return exit code."""
    await ensure_ollama_warm()

    async with eval_deps() as (deps, frontend), open_eval_run("daily_chat") as run:
        apply_eval_window(deps)
        cases: list[CaseResult] = []

        for runner in (
            _case_w1_a_multi_turn_coherence,
            _case_w1_d_dream_propagates_to_recall,
            _case_w1_f_merge_preserves_distinct_facts,
        ):
            try:
                case = await runner(deps, frontend, run)
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

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
