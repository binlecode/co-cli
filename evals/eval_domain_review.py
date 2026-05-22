"""UAT eval — per-prompt domain review extraction quality.

Runs the dream daemon's memory and skill reviewers against a synthetic fixture
session and asserts per-domain extraction behavior:

  DR.A — memory review extracts persona/preference facts (memory item written)
  DR.B — skill review extracts technique/correction patterns (no memory persona writes)

Both cases use ``process_review`` from ``co_cli/daemons/dream/_reviewer.py``
and a small synthetic fixture session containing:
- a user preference statement
- an agent learning a skill technique
- general conversation filler

Per ``feedback_eval_real_world_data``: real model calls, real stores, no mocks.
Per ``feedback_call_timeout_no_cold_start``: per-call asyncio.timeout covers
warm-model latency only; ensure_ollama_warm runs outside any timeout block.

Specs: docs/specs/dream.md, memory.md
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from evals._observability import CaseResult, EvalRun, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from co_cli.config.core import USER_DIR
from co_cli.daemons.dream._deps import build_codeps_for_daemon
from co_cli.daemons.dream._reviewer import process_review
from co_cli.session.persistence import append_messages

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORT_PATH = _PROJECT_ROOT / "docs" / "REPORT-eval-domain-review.md"

# Fixture session file — deterministic name so reruns overwrite in place.
_FIXTURE_SESSION_ID = "fixture-domain-review-0"

# Per-call asyncio.timeout ceiling (generous — warm-model latency only).
# Domain reviewers may drive multiple tool turns internally.
_REVIEW_TIMEOUT_S: int = 120


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _build_fixture_session(sessions_dir: Path) -> Path:
    """Write a 6-message synthetic session JSONL covering preference, technique, and filler.

    Deterministic path: reruns overwrite in place (truncate + rewrite).
    Uses pydantic-ai ModelMessage types so the schema stays current.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{_FIXTURE_SESSION_ID}.jsonl"
    if path.exists():
        path.unlink()

    turns: list[tuple[str, str]] = [
        (
            "Please always keep your replies concise — I don't like long answers.",
            "Understood, I'll keep responses brief from now on.",
        ),
        (
            "When I ask you to refactor code, always suggest adding type hints as part of the change.",
            "Good call — I'll include type hint suggestions in every refactor.",
        ),
        (
            "What's the capital of France?",
            "Paris.",
        ),
    ]

    messages: list[ModelMessage] = []
    for user_text, assistant_text in turns:
        messages.append(ModelRequest(parts=[UserPromptPart(content=user_text)]))
        messages.append(ModelResponse(parts=[TextPart(content=assistant_text)]))

    append_messages(path, messages)
    return path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _count_memory_items(memory_dir: Path) -> int:
    """Count .md files in memory_dir that look like memory items (not _archive/)."""
    if not memory_dir.exists():
        return 0
    return sum(1 for p in memory_dir.glob("*.md") if p.is_file())


def _print(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# DR.A — memory review extracts persona / preference facts
# ---------------------------------------------------------------------------


async def case_dr_a_memory_review_extracts_persona(
    run: EvalRun,
) -> CaseResult:
    """DR.A — memory reviewer writes at least one new memory item from the fixture.

    The fixture transcript contains two clear preference/persona statements
    (terse replies, type-hint technique). The memory reviewer should recognize
    at least one as worth persisting and call memory_manage(create).

    Assertion: memory_dir .md count increases after the review run.
    No skill-write side-effect assertion here — skill_manage is not in
    MEMORY_REVIEW_SPEC.tool_names, so it can't fire.
    """
    case_id = "DR.A"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    deps = build_codeps_for_daemon(USER_DIR)

    session_path = _build_fixture_session(deps.sessions_dir)
    count_before = _count_memory_items(deps.memory_dir)

    passed = True
    reason = ""
    try:
        async with asyncio.timeout(_REVIEW_TIMEOUT_S):
            await process_review(
                deps=deps,
                domain="memory",
                session_id=_FIXTURE_SESSION_ID,
                persisted_message_count=6,
            )
    except TimeoutError:
        passed = False
        reason = f"asyncio.timeout({_REVIEW_TIMEOUT_S}s) fired before memory review completed"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"
    else:
        count_after = _count_memory_items(deps.memory_dir)
        if count_after <= count_before:
            passed = False
            reason = (
                f"memory review produced no new memory items "
                f"(before={count_before}, after={count_after}); "
                "expected at least one persona/preference write"
            )
        else:
            reason = (
                f"memory review wrote {count_after - count_before} new item(s) "
                f"(before={count_before}, after={count_after})"
            )
    finally:
        # Leave session file in place — no cleanup, per feedback_eval_real_world_data.
        _ = session_path  # suppress unused-variable warning

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


# ---------------------------------------------------------------------------
# DR.B — skill review runs without error; no memory persona bleed
# ---------------------------------------------------------------------------


async def case_dr_b_skill_review_no_memory_persona_bleed(
    run: EvalRun,
) -> CaseResult:
    """DR.B — skill reviewer runs without error and does not write persona memory items.

    The fixture transcript contains a technique hint (type hints on refactor).
    The skill reviewer may create or patch a skill entry. What it must NOT do
    is write persona/preference items — memory_manage is not in
    SKILL_REVIEW_SPEC.tool_names, so persona writes are architecturally blocked.

    Assertions:
      1. process_review(domain='skill') completes without raising.
      2. memory_dir .md count does not increase (no memory writes from skill domain).
    """
    case_id = "DR.B"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    deps = build_codeps_for_daemon(USER_DIR)

    # Rebuild the fixture session in case DR.A's session file was not found.
    _build_fixture_session(deps.sessions_dir)
    count_before = _count_memory_items(deps.memory_dir)

    passed = True
    reason = ""
    try:
        async with asyncio.timeout(_REVIEW_TIMEOUT_S):
            await process_review(
                deps=deps,
                domain="skill",
                session_id=_FIXTURE_SESSION_ID,
                persisted_message_count=6,
            )
    except TimeoutError:
        passed = False
        reason = f"asyncio.timeout({_REVIEW_TIMEOUT_S}s) fired before skill review completed"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"
    else:
        count_after = _count_memory_items(deps.memory_dir)
        if count_after > count_before:
            passed = False
            reason = (
                f"skill review wrote {count_after - count_before} memory item(s) "
                f"(before={count_before}, after={count_after}); "
                "persona bleed detected — skill domain must not write memory items"
            )
        else:
            reason = (
                f"skill review completed without error; "
                f"no memory persona bleed (count stable at {count_after})"
            )

    return CaseResult(
        name=case_id,
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        duration_s=time.monotonic() - t0,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> int:
    """Run DR.A and DR.B against the real daemon reviewer and emit the REPORT.

    ensure_ollama_warm runs outside any asyncio.timeout per
    feedback_call_timeout_no_cold_start. Failures in one case do not abort
    the run — each case captures its own verdict; exit code is non-zero iff
    any case failed.
    """
    await ensure_ollama_warm()

    cases: list[CaseResult] = []
    try:
        async with open_eval_run("domain_review") as run:
            ordered_cases = (
                case_dr_a_memory_review_extracts_persona,
                case_dr_b_skill_review_no_memory_persona_bleed,
            )
            for case_fn in ordered_cases:
                try:
                    cr = await case_fn(run)
                except Exception as exc:
                    cr = CaseResult(
                        name=case_fn.__name__,
                        verdict=Verdict.FAIL,
                        duration_s=0.0,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                cases.append(cr)
                run.append(cr)
                label = cr.verdict.value.upper()
                _print(f"[domain_review] {cr.name}: {label} — {cr.reason or 'ok'}")

            prepend_report(
                _REPORT_PATH,
                "domain_review",
                run.iso,
                cases,
                run_dir=run.dir,
            )
    except Exception as exc:
        _print(f"[domain_review] run failed: {type(exc).__name__}: {exc}")
        return 1

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
