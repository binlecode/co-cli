#!/usr/bin/env python3
"""Eval: session history context — persist → load transcript → run turn with prior context.

Validates that message history survives the persistence round-trip and that a
subsequent agent turn can reference content from the restored history.

    persist_session_history → load_transcript     → message count + content integrity
    persist_session_history → load_transcript     → run_turn with restored history
                                                  → agent references prior content

The context-in-turn case is the critical path: if session history doesn't survive
persist→restore, the agent has no memory of prior turns within the same session.

Writes: docs/REPORT-eval-session-history-context.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_session_history_context.py
"""

import asyncio
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._fixtures import build_message_history
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.memory.session import new_session_path
from co_cli.memory.transcript import load_transcript, persist_session_history

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-session-history-context.md"

# Unique marker with no risk of appearing in LLM training or fixtures
_SENTINEL = "zyxwquartz-session-history-eval-unique"


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def run_persist_load_roundtrip(tmp_dir: Path) -> dict[str, Any]:
    """persist_session_history writes JSONL; load_transcript deserializes exact message count."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "persist-load" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    messages = build_message_history(
        [
            ("user", f"My preferred language is {_SENTINEL}-lang."),
            ("assistant", f"Understood. I will use {_SENTINEL}-lang."),
            ("user", "What is 2 + 2?"),
            ("assistant", "2 + 2 = 4."),
        ]
    )

    t = time.monotonic()
    session_path = new_session_path(sessions_dir)
    written_path = persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=messages,
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist_session_history",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"wrote {len(messages)} messages to {written_path.name}",
        }
    )

    t = time.monotonic()
    loaded = load_transcript(written_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(loaded)} expected={len(messages)}",
        }
    )

    # Verify first user message content survived round-trip
    first_user_text = ""
    for msg in loaded:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    first_user_text = part.content
                    break
        if first_user_text:
            break
    steps.append(
        {
            "name": "content integrity check",
            "ms": 0,
            "detail": f"first_user_msg={first_user_text[:60]!r} sentinel_present={_SENTINEL in first_user_text}",
        }
    )

    if len(loaded) != len(messages):
        verdict, failure = (
            "FAIL",
            f"message count mismatch: wrote {len(messages)}, loaded {len(loaded)}",
        )
    elif _SENTINEL not in first_user_text:
        verdict, failure = (
            "FAIL",
            f"sentinel not found in restored first message: {first_user_text[:80]!r}",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "persist-load-roundtrip",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_incremental_append(tmp_dir: Path) -> dict[str, Any]:
    """Subsequent turns append-only; persisted_message_count prevents re-writing old messages."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "incremental" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = new_session_path(sessions_dir)

    turn1 = build_message_history(
        [
            ("user", "First turn message."),
            ("assistant", "First turn response."),
        ]
    )
    turn2 = build_message_history(
        [
            ("user", "First turn message."),
            ("assistant", "First turn response."),
            ("user", f"Second turn with {_SENTINEL}-append-marker."),
            ("assistant", "Second turn response."),
        ]
    )

    t = time.monotonic()
    persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=turn1,
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist turn 1 (2 messages)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": "persisted_message_count=0 → appends all 2",
        }
    )

    t = time.monotonic()
    persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=turn2,
        persisted_message_count=len(turn1),  # only append the 2 new messages
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist turn 2 (4 messages, count=2)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"persisted_message_count={len(turn1)} → appends only 2 new",
        }
    )

    t = time.monotonic()
    loaded = load_transcript(session_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(loaded)} expected={len(turn2)}",
        }
    )

    # Verify no duplicates: each message appears exactly once
    user_texts = [
        part.content
        for msg in loaded
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, UserPromptPart)
    ]
    has_duplicate = len(user_texts) != len(set(user_texts))
    has_sentinel = any(_SENTINEL in t for t in user_texts)
    steps.append(
        {
            "name": "duplicate check",
            "ms": 0,
            "detail": f"user_messages={user_texts} has_duplicate={has_duplicate} has_sentinel={has_sentinel}",
        }
    )

    if len(loaded) != len(turn2):
        verdict, failure = "FAIL", f"expected {len(turn2)} messages, loaded {len(loaded)}"
    elif has_duplicate:
        verdict, failure = (
            "FAIL",
            "duplicate messages found — incremental append wrote redundant data",
        )
    elif not has_sentinel:
        verdict, failure = "FAIL", "sentinel from turn 2 not found in loaded transcript"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "incremental-append",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_restored_history_in_context(tmp_dir: Path) -> dict[str, Any]:
    """Restored history is used as context; agent can reference content from prior turns."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "context-turn" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Prior history: user stated a unique preference; assistant acknowledged it
    prior_history = [
        ModelRequest(
            parts=[UserPromptPart(content=f"My secret project codename is {_SENTINEL}-codename.")]
        ),
        ModelResponse(
            parts=[TextPart(content=f"Got it, your project codename is {_SENTINEL}-codename.")],
            model_name="eval-history",
        ),
    ]

    t = time.monotonic()
    session_path = new_session_path(sessions_dir)
    persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=prior_history,
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist prior history",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"wrote {len(prior_history)} messages",
        }
    )

    t = time.monotonic()
    restored = load_transcript(session_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(restored)} messages",
        }
    )

    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_dir / "context-turn")
        deps = make_eval_deps()
        agent = build_agent(config=settings)

        t = time.monotonic()
        try:
            async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
                result = await run_turn(
                    agent=agent,
                    user_input="What codename did I mention earlier?",
                    deps=deps,
                    message_history=restored,
                    frontend=SilentFrontend(),
                )
        finally:
            pass
        turn_ms = (time.monotonic() - t) * 1000
        steps.append(
            {
                "name": "run_turn (with restored history)",
                "ms": turn_ms,
                "detail": "asked about prior codename",
            }
        )

        # Extract the assistant's final text response
        assistant_text = ""
        for msg in result.messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, TextPart):
                        assistant_text += part.content

        keyword = f"{_SENTINEL}-codename"
        found_keyword = keyword.lower() in assistant_text.lower()
        steps.append(
            {
                "name": "response analysis",
                "ms": 0,
                "detail": f"sentinel_in_response={found_keyword} preview={assistant_text[:120]!r}",
            }
        )

        if len(restored) != len(prior_history):
            verdict, failure = (
                "FAIL",
                f"history not fully restored: {len(restored)}/{len(prior_history)}",
            )
        elif not found_keyword:
            # SOFT PASS: model may paraphrase but still use context; check for partial match
            partial = _SENTINEL.split("-")[0] in assistant_text.lower()
            if partial:
                verdict, failure = "SOFT PASS", None
            else:
                verdict, failure = (
                    "FAIL",
                    "restored history not used as context — sentinel not found in response",
                )
        else:
            verdict, failure = "PASS", None
    finally:
        os.chdir(orig_cwd)

    return {
        "id": "restored-history-in-context",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_empty_history_new_session(tmp_dir: Path) -> dict[str, Any]:
    """Empty history persists nothing; load_transcript returns []. No crash."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sessions_dir = tmp_dir / "empty-history" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = new_session_path(sessions_dir)

    t = time.monotonic()
    written_path = persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=[],
        persisted_message_count=0,
        history_compacted=False,
        reason="eval",
    )
    steps.append(
        {
            "name": "persist_session_history (empty messages)",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"file_exists={written_path.exists()}",
        }
    )

    t = time.monotonic()
    loaded = load_transcript(written_path)
    steps.append(
        {
            "name": "load_transcript",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"loaded={len(loaded)} (expected 0)",
        }
    )

    if len(loaded) != 0:
        verdict, failure = "FAIL", f"expected 0 messages, got {len(loaded)}"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "empty-history-new-session",
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
        updated = "# Eval Report: Session History Context\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Session History Context")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    # Non-LLM cases run first (fast); LLM case last
    non_llm_runners = [
        ("persist-load-roundtrip", run_persist_load_roundtrip),
        ("incremental-append", run_incremental_append),
        ("empty-history-new-session", run_empty_history_new_session),
    ]
    llm_runners = [
        ("restored-history-in-context", run_restored_history_in_context),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        print("\n  Phase 1: Persistence integrity (no LLM)")
        print("  " + "-" * 44)
        for label, fn in non_llm_runners:
            print(f"    [{label}]", end=" ", flush=True)
            try:
                result = await fn(tmp_path)
            except Exception as exc:
                result = {
                    "id": label,
                    "verdict": "ERROR",
                    "failure": str(exc),
                    "steps": [],
                    "duration_ms": 0,
                }
            all_cases.append(result)
            print(f"{result['verdict']} ({result['duration_ms']:.0f}ms)")
            if result.get("failure"):
                print(f"      → {result['failure']}")

        print("\n  Phase 2: Context availability in agent turn (LLM)")
        print("  " + "-" * 44)
        for label, fn in llm_runners:
            print(f"    [{label}]", end=" ", flush=True)
            try:
                result = await fn(tmp_path)
            except Exception as exc:
                result = {
                    "id": label,
                    "verdict": "ERROR",
                    "failure": str(exc),
                    "steps": [],
                    "duration_ms": 0,
                }
            all_cases.append(result)
            print(f"{result['verdict']} ({result['duration_ms']:.0f}ms)")
            if result.get("failure"):
                print(f"      → {result['failure']}")

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
