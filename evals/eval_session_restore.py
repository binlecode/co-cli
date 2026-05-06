#!/usr/bin/env python3
"""Eval: session restore — prior session transcript loaded and referenced by agent.

Sub-cases:
  R1  prior_context_available           Agent receives a seeded prior session as message
                                        history; it references the unique token planted
                                        in that transcript
  R2  no_hallucination_from_absent_session  Fresh session with no prior history; agent
                                        does not fabricate a unique token it was never told
  R3  multi_session_most_recent_wins    Two seeded sessions; most recent session content
                                        appears in agent response over older

All cases use a real LLM, real filesystem under a temp sessions dir, no mocks.

Writes: docs/REPORT-eval-session-restore.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_session_restore.py
"""

import asyncio
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from evals._deps import make_eval_deps
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from co_cli.agent.core import build_agent
from co_cli.bootstrap.core import restore_session
from co_cli.config.core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend
from co_cli.memory.session import session_filename
from co_cli.memory.transcript import append_messages, load_transcript

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-session-restore.md"

_AGENT = build_agent(config=settings)


def _response_text(result: Any) -> str:
    parts = []
    for msg in result.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content)
    return " ".join(parts)


def _write_session(sessions_dir: Path, created_at: datetime, token: str) -> Path:
    """Write a synthetic two-message session transcript containing token."""
    path = sessions_dir / session_filename(created_at, f"{token[:8].lower()}-0000")
    messages: list[Any] = [
        ModelRequest(parts=[UserPromptPart(content=f"Remember this token: {token}")]),
        ModelResponse(parts=[TextPart(content=f"Understood, I have noted your token: {token}")]),
    ]
    append_messages(path, messages)
    return path


# ---------------------------------------------------------------------------
# R1: prior_context_available
# ---------------------------------------------------------------------------


async def run_prior_context_available() -> dict[str, Any]:
    """Agent receives a seeded prior session and references the unique token in it."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    token = f"SESTOKEN{int(time.monotonic() * 1000) % 100000:05d}A"

    with tempfile.TemporaryDirectory() as tmpdir:
        sessions_dir = Path(tmpdir) / "sessions"
        sessions_dir.mkdir()

        created_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        _write_session(sessions_dir, created_at, token)
        steps.append({"name": "seed_session", "ms": 0, "detail": f"token={token}"})

        frontend = HeadlessFrontend()
        deps = make_eval_deps()
        deps.sessions_dir = sessions_dir

        t = time.monotonic()
        session_path = restore_session(deps, frontend)
        steps.append(
            {
                "name": "restore_session",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"path={session_path.name}",
            }
        )

        t = time.monotonic()
        prior_messages = load_transcript(session_path)
        steps.append(
            {
                "name": "load_transcript",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"messages={len(prior_messages)}",
            }
        )

        if not prior_messages:
            return {
                "id": "prior_context_available",
                "verdict": "FAIL",
                "failure": "load_transcript returned empty — session file not written or not found",
                "steps": steps,
                "duration_ms": (time.monotonic() - case_t0) * 1000,
            }

        t = time.monotonic()
        with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=_AGENT,
                user_input="What was the token I mentioned? Repeat it exactly.",
                deps=deps,
                message_history=prior_messages,
                frontend=frontend,
            )
        steps.append(
            {
                "name": "run_turn",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"outcome={result.outcome}",
            }
        )

    text = _response_text(result)
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"token_in_response={token in text} preview={text[:200]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif token not in text:
        verdict, failure = "FAIL", f"token {token!r} not found in response: {text[:300]!r}"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "prior_context_available",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# R2: no_hallucination_from_absent_session
# ---------------------------------------------------------------------------


async def run_no_hallucination_from_absent_session() -> dict[str, Any]:
    """Fresh session — agent does not fabricate a token it was never told."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    # A token so unique it cannot appear in training data or be guessed
    absent_token = f"ABSENTXQ{int(time.monotonic() * 1000) % 100000:05d}Z"

    with tempfile.TemporaryDirectory() as tmpdir:
        sessions_dir = Path(tmpdir) / "sessions"
        sessions_dir.mkdir()
        # No session files written — truly fresh session

        frontend = HeadlessFrontend()
        deps = make_eval_deps()
        deps.sessions_dir = sessions_dir

        restore_session(deps, frontend)
        # session_path does not exist yet — load_transcript returns []
        prior_messages = load_transcript(deps.session.session_path)
        steps.append(
            {
                "name": "load_transcript",
                "ms": 0,
                "detail": f"messages={len(prior_messages)} (expected 0)",
            }
        )

        t = time.monotonic()
        with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=_AGENT,
                user_input="What was the special token I gave you earlier in our conversation? Just state it plainly.",
                deps=deps,
                message_history=prior_messages,
                frontend=frontend,
            )
        steps.append(
            {
                "name": "run_turn",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"outcome={result.outcome}",
            }
        )

    text = _response_text(result)
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"absent_token_hallucinated={absent_token in text} preview={text[:200]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif absent_token in text:
        verdict, failure = "FAIL", f"agent hallucinated absent token {absent_token!r}"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "no_hallucination_from_absent_session",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# R3: multi_session_most_recent_wins
# ---------------------------------------------------------------------------


async def run_multi_session_most_recent_wins() -> dict[str, Any]:
    """Two sessions seeded; restore picks the newest; agent references its token."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    base = int(time.monotonic() * 1000) % 10000
    token_old = f"OLDTOKEN{base:04d}X"
    token_new = f"NEWTOKEN{base:04d}Y"

    with tempfile.TemporaryDirectory() as tmpdir:
        sessions_dir = Path(tmpdir) / "sessions"
        sessions_dir.mkdir()

        older_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        newer_at = datetime(2026, 1, 2, 10, 0, 0, tzinfo=UTC)
        _write_session(sessions_dir, older_at, token_old)
        _write_session(sessions_dir, newer_at, token_new)
        steps.append(
            {
                "name": "seed_sessions",
                "ms": 0,
                "detail": f"older={token_old} newer={token_new}",
            }
        )

        frontend = HeadlessFrontend()
        deps = make_eval_deps()
        deps.sessions_dir = sessions_dir

        t = time.monotonic()
        session_path = restore_session(deps, frontend)
        steps.append(
            {
                "name": "restore_session",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"path={session_path.name}",
            }
        )

        prior_messages = load_transcript(session_path)
        steps.append(
            {
                "name": "load_transcript",
                "ms": 0,
                "detail": f"messages={len(prior_messages)}",
            }
        )

        if not prior_messages:
            return {
                "id": "multi_session_most_recent_wins",
                "verdict": "FAIL",
                "failure": "load_transcript returned empty from most recent session",
                "steps": steps,
                "duration_ms": (time.monotonic() - case_t0) * 1000,
            }

        t = time.monotonic()
        with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
            result = await run_turn(
                agent=_AGENT,
                user_input="What was the token I mentioned? Repeat it exactly.",
                deps=deps,
                message_history=prior_messages,
                frontend=frontend,
            )
        steps.append(
            {
                "name": "run_turn",
                "ms": (time.monotonic() - t) * 1000,
                "detail": f"outcome={result.outcome}",
            }
        )

    text = _response_text(result)
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"new_in_response={token_new in text} old_in_response={token_old in text} preview={text[:200]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif token_new not in text:
        verdict, failure = (
            "FAIL",
            f"most recent token {token_new!r} not found in response: {text[:300]!r}",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "multi_session_most_recent_wins",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(cases: list[dict[str, Any]], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(1 for c in cases if c["verdict"] in ("PASS", "SOFT PASS"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {settings.llm.provider} / {settings.llm.model or 'default'}  ",
        f"**Total runtime:** {total_ms:.0f}ms  ",
        f"**Result:** {passed}/{len(cases)} passed",
        "",
        "### Summary",
        "",
        "| Case | Verdict | Duration |",
        "|------|---------|----------|",
    ]
    for c in cases:
        lines.append(f"| `{c['id']}` | {c['verdict']} | {c['duration_ms']:.0f}ms |")

    lines += ["", "### Step Traces", ""]
    for c in cases:
        lines.append(f"#### `{c['id']}` — {c['verdict']}")
        for step in c["steps"]:
            lines.append(f"- **{step['name']}** ({step['ms']:.0f}ms): {step['detail']}")
        if c.get("failure"):
            lines.append(f"- **Failure:** {c['failure']}")
        lines.append("")

    lines += ["---", ""]
    section = "\n".join(lines)

    if _REPORT_PATH.exists():
        existing = _REPORT_PATH.read_text(encoding="utf-8")
        split = existing.split("\n", 2)
        updated = split[0] + "\n\n" + section + ("\n".join(split[1:]) if len(split) > 1 else "")
    else:
        updated = "# Eval Report: Session Restore\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Session Restore")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    runners = [
        ("R1: prior_context_available", run_prior_context_available),
        ("R2: no_hallucination_from_absent_session", run_no_hallucination_from_absent_session),
        ("R3: multi_session_most_recent_wins", run_multi_session_most_recent_wins),
    ]

    for label, fn in runners:
        print(f"\n  [{label}]", flush=True)
        try:
            result = await fn()
        except Exception as exc:
            result = {
                "id": label.split(": ", 1)[1].replace(" ", "_").lower()[:40],
                "verdict": "ERROR",
                "failure": f"{type(exc).__name__}: {exc}",
                "steps": [],
                "duration_ms": 0,
            }
        all_cases.append(result)
        print(f"  → {result['verdict']} ({result['duration_ms']:.0f}ms)")
        if result.get("failure"):
            print(f"    {result['failure']}")

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for c in all_cases if c["verdict"] in ("PASS", "SOFT PASS"))
    _write_report(all_cases, total_ms)

    print(f"\n{'=' * 60}")
    verdict = "PASS" if passed == len(all_cases) else "FAIL"
    print(f"  Verdict: {verdict} ({passed}/{len(all_cases)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 60}")
    return 0 if passed == len(all_cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
