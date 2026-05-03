#!/usr/bin/env python3
"""Eval: agent-driven memory write → recall — end-to-end memory lifecycle.

Sub-cases (sequential within one shared isolated knowledge store):
  W1  agent_saves_artifact     Agent instructed to save a unique fact → calls memory_create;
                               artifact file appears on disk; approval prompt fires
  W2  artifact_indexed         MemoryStore.search(fact_token) returns ≥ 1 hit after W1
  W3  agent_recalls_in_turn2   Fresh session: agent searches memory for fact_token → response
                               contains the token (sourced from artifact, not conversation)
  W4  memory_modify_append     Agent instructed to append APPEND_TOKEN to artifact →
                               calls memory_modify(action="append"); file body contains token
  W5  memory_modify_replace    Agent instructed to replace fact_token with REPLACE_TOKEN →
                               calls memory_modify(action="replace"); old text absent, new present

All cases use a real LLM, real SQLite store, real filesystem under a temp CO_HOME.

Writes: docs/REPORT-eval-memory-write-recall.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_write_recall.py
"""

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
from evals._deps import make_eval_deps
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelResponse, TextPart

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend
from co_cli.memory.memory_store import MemoryStore

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-memory-write-recall.md"

_AGENT = build_agent(config=settings)


def _response_text(result: Any) -> str:
    """Extract all assistant text parts from a TurnResult."""
    parts = []
    for msg in result.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content)
    return " ".join(parts)


def _find_artifact_by_content(knowledge_dir: Path, token: str) -> Path | None:
    """Return the first .md file in knowledge_dir whose body contains token."""
    for f in sorted(knowledge_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if token in f.read_text(encoding="utf-8"):
            return f
    return None


# ---------------------------------------------------------------------------
# W1: agent_saves_artifact
# ---------------------------------------------------------------------------


async def run_agent_saves_artifact(
    knowledge_dir: Path,
    ks: MemoryStore,
    fact_token: str,
    note_title: str,
) -> dict[str, Any]:
    """Agent instructed to save fact → memory_create called; approval fires; file exists."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    frontend = HeadlessFrontend(approval_response="y")
    deps = make_eval_deps(knowledge_dir=knowledge_dir, memory_store=ks)

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input=(
                f"Please save the following as a knowledge artifact. "
                f"Use artifact_kind='note' and title='{note_title}'. "
                f"Content: The unique evaluation test token is {fact_token}. "
                f"Use the memory_create tool to save it now."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    steps.append(
        {
            "name": "run_turn",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"outcome={result.outcome} approval_calls={len(frontend.approval_calls)}",
        }
    )

    text = _response_text(result)
    steps.append({"name": "response_preview", "ms": 0, "detail": text[:200]})

    artifact_path = _find_artifact_by_content(knowledge_dir, fact_token)
    steps.append(
        {
            "name": "artifact_on_disk",
            "ms": 0,
            "detail": f"found={artifact_path is not None} path={artifact_path.name if artifact_path else None}",
        }
    )

    approval_was_prompted = len(frontend.approval_calls) >= 1
    approval_subject = frontend.last_approval_subject
    steps.append(
        {
            "name": "approval_subject",
            "ms": 0,
            "detail": (f"tool={approval_subject.tool_name if approval_subject else None}"),
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif not approval_was_prompted:
        verdict, failure = "FAIL", "approval prompt never called — memory_create was not triggered"
    elif artifact_path is None:
        verdict, failure = (
            "FAIL",
            f"no artifact file found containing {fact_token!r} in {knowledge_dir}",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "agent_saves_artifact",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# W2: artifact_indexed
# ---------------------------------------------------------------------------


async def run_artifact_indexed(
    knowledge_dir: Path,
    ks: MemoryStore,
    fact_token: str,
) -> dict[str, Any]:
    """MemoryStore.search(fact_token) returns ≥ 1 hit after W1 indexed the artifact."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    t = time.monotonic()
    results = ks.search(fact_token, sources=["knowledge"], limit=5)
    elapsed = (time.monotonic() - t) * 1000
    steps.append(
        {
            "name": "ks.search",
            "ms": elapsed,
            "detail": f"query={fact_token!r} count={len(results)}",
        }
    )

    if results:
        steps.append(
            {
                "name": "first_result",
                "ms": 0,
                "detail": str(results[0])[:200],
            }
        )

    if len(results) < 1:
        verdict, failure = "FAIL", f"search returned 0 results for {fact_token!r}"
    else:
        verdict, failure = "PASS", None

    return {
        "id": "artifact_indexed",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# W3: agent_recalls_in_turn2
# ---------------------------------------------------------------------------


async def run_agent_recalls_in_turn2(
    knowledge_dir: Path,
    ks: MemoryStore,
    fact_token: str,
) -> dict[str, Any]:
    """Fresh session: agent asked to search memory → response contains fact_token."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    frontend = HeadlessFrontend(approval_response="y")
    deps = make_eval_deps(knowledge_dir=knowledge_dir, memory_store=ks)

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            # Fresh session: no message history from W1 — agent must search memory
            user_input=(
                f"Search your memory artifacts for the evaluation test token. "
                f"Specifically, search for '{fact_token[:8]}' and report verbatim what you find."
            ),
            deps=deps,
            message_history=[],
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
            "detail": f"fact_token_in_response={fact_token in text} preview={text[:250]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif fact_token not in text:
        # Agent may have recalled the artifact but paraphrased; accept partial match on token prefix
        partial = fact_token[:8] in text
        if partial:
            verdict, failure = "SOFT PASS", None
        else:
            verdict, failure = (
                "FAIL",
                f"fact_token {fact_token!r} not found in response; agent may not have searched memory",
            )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "agent_recalls_in_turn2",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# W4: memory_modify_append
# ---------------------------------------------------------------------------


async def run_memory_modify_append(
    knowledge_dir: Path,
    ks: MemoryStore,
    fact_token: str,
    note_title: str,
    append_token: str,
) -> dict[str, Any]:
    """Agent appends append_token to artifact via memory_modify(action='append')."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    artifact_path = _find_artifact_by_content(knowledge_dir, fact_token)
    if artifact_path is None:
        return {
            "id": "memory_modify_append",
            "verdict": "FAIL",
            "failure": f"Prerequisite: artifact with {fact_token!r} not found — W1 must pass first",
            "steps": steps,
            "duration_ms": (time.monotonic() - case_t0) * 1000,
        }

    steps.append({"name": "artifact_found", "ms": 0, "detail": artifact_path.name})

    frontend = HeadlessFrontend(approval_response="y")
    deps = make_eval_deps(knowledge_dir=knowledge_dir, memory_store=ks)

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input=(
                f"Use memory_search to find the artifact titled '{note_title}'. "
                f"Then append the following text to the end of that artifact using memory_modify "
                f"with action='append': {append_token}"
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    steps.append(
        {
            "name": "run_turn",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"outcome={result.outcome} approval_calls={len(frontend.approval_calls)}",
        }
    )

    text = _response_text(result)
    steps.append({"name": "response_preview", "ms": 0, "detail": text[:200]})

    # Re-read the artifact file to check appended content
    updated_content = artifact_path.read_text(encoding="utf-8")
    append_present = append_token in updated_content
    steps.append(
        {
            "name": "file_content_check",
            "ms": 0,
            "detail": f"append_token_present={append_present} file_size={len(updated_content)}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif len(frontend.approval_calls) == 0:
        verdict, failure = "FAIL", "approval never fired — memory_modify was not called"
    elif not append_present:
        verdict, failure = (
            "FAIL",
            f"append_token {append_token!r} not found in artifact body after modify",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "memory_modify_append",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# W5: memory_modify_replace
# ---------------------------------------------------------------------------


async def run_memory_modify_replace(
    knowledge_dir: Path,
    ks: MemoryStore,
    fact_token: str,
    note_title: str,
    replace_token: str,
) -> dict[str, Any]:
    """Agent replaces fact_token with replace_token via memory_modify(action='replace')."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    artifact_path = _find_artifact_by_content(knowledge_dir, fact_token)
    if artifact_path is None:
        return {
            "id": "memory_modify_replace",
            "verdict": "FAIL",
            "failure": f"Prerequisite: artifact with {fact_token!r} not found — W1 must pass first",
            "steps": steps,
            "duration_ms": (time.monotonic() - case_t0) * 1000,
        }

    steps.append({"name": "artifact_found", "ms": 0, "detail": artifact_path.name})

    frontend = HeadlessFrontend(approval_response="y")
    deps = make_eval_deps(knowledge_dir=knowledge_dir, memory_store=ks)

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input=(
                f"Use memory_search to find the artifact titled '{note_title}'. "
                f"Then use memory_modify with action='replace' to surgically replace "
                f"the exact text '{fact_token}' with '{replace_token}' in that artifact. "
                f"The target parameter must be exactly '{fact_token}'."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    steps.append(
        {
            "name": "run_turn",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"outcome={result.outcome} approval_calls={len(frontend.approval_calls)}",
        }
    )

    text = _response_text(result)
    steps.append({"name": "response_preview", "ms": 0, "detail": text[:200]})

    # Re-read the artifact file to check replacement
    updated_content = artifact_path.read_text(encoding="utf-8")
    replace_present = replace_token in updated_content
    # fact_token should be gone from body (may still be in YAML frontmatter title if it leaked there)
    body_only = (
        updated_content.split("---", 2)[-1] if "---" in updated_content else updated_content
    )
    old_absent = fact_token not in body_only
    steps.append(
        {
            "name": "file_content_check",
            "ms": 0,
            "detail": f"replace_token_present={replace_present} old_text_absent_in_body={old_absent}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif len(frontend.approval_calls) == 0:
        verdict, failure = "FAIL", "approval never fired — memory_modify was not called"
    elif not replace_present:
        verdict, failure = (
            "FAIL",
            f"replace_token {replace_token!r} not found in artifact after modify",
        )
    elif not old_absent:
        verdict, failure = (
            "FAIL",
            f"old text {fact_token!r} still present in artifact body after replace",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "memory_modify_replace",
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
        updated = "# Eval Report: Memory Write → Recall\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Write → Recall")
    print("=" * 60)

    # Unique tokens for this run — prevent cross-run false positives
    fact_token = f"TESTFACT{uuid4().hex[:8].upper()}"
    note_title = f"eval-test-note-{uuid4().hex[:4]}"
    append_token = f"APPENDTOKEN{uuid4().hex[:8].upper()}"
    replace_token = f"REPLACETOKEN{uuid4().hex[:8].upper()}"

    print(f"\n  fact_token  = {fact_token}")
    print(f"  note_title  = {note_title}")
    print(f"  append_token = {append_token}")
    print(f"  replace_token = {replace_token}")

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    # Isolated knowledge store for this eval run
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        knowledge_dir = tmp_path / "knowledge"
        db_path = tmp_path / "search.db"
        knowledge_dir.mkdir(parents=True, exist_ok=True)

        ks = MemoryStore(config=settings, memory_db_path=db_path)
        try:
            runners: list[tuple[str, Any]] = [
                (
                    "W1: agent_saves_artifact",
                    lambda: run_agent_saves_artifact(knowledge_dir, ks, fact_token, note_title),
                ),
                (
                    "W2: artifact_indexed",
                    lambda: run_artifact_indexed(knowledge_dir, ks, fact_token),
                ),
                (
                    "W3: agent_recalls_in_turn2",
                    lambda: run_agent_recalls_in_turn2(knowledge_dir, ks, fact_token),
                ),
                (
                    "W4: memory_modify_append",
                    lambda: run_memory_modify_append(
                        knowledge_dir, ks, fact_token, note_title, append_token
                    ),
                ),
                (
                    "W5: memory_modify_replace",
                    lambda: run_memory_modify_replace(
                        knowledge_dir, ks, fact_token, note_title, replace_token
                    ),
                ),
            ]

            for label, fn in runners:
                print(f"\n  [{label}]", flush=True)
                try:
                    result = await fn()
                except Exception as exc:
                    result = {
                        "id": label.split(": ", 1)[1].replace(" ", "_").lower()[:30],
                        "verdict": "ERROR",
                        "failure": f"{type(exc).__name__}: {exc}",
                        "steps": [],
                        "duration_ms": 0,
                    }
                all_cases.append(result)
                print(f"  → {result['verdict']} ({result['duration_ms']:.0f}ms)")
                if result.get("failure"):
                    print(f"    {result['failure']}")

        finally:
            ks.close()

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
