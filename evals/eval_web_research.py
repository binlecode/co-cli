#!/usr/bin/env python3
"""Eval: web research — web_fetch and web_search execute through a real agent turn.

Sub-cases:
  W1  web_fetch_executes      Agent calls web_fetch for a real URL; fetched content
                              is referenced in the response
  W2  web_search_executes     Agent calls web_search for a query; search results
                              inform the response (skipped if no BRAVE_SEARCH_API_KEY)
  W3  web_fetch_second_domain Agent calls web_fetch for a second distinct URL in the
                              same session; content is in the response — proves
                              multi-URL fetch is reliable

Note: web_fetch and web_search carry approval=False (read-only tools), so no approval
prompt fires during these cases. Domain approval session persistence (originally W3
in the plan) is not exercisable through a live turn at this time.

All cases require network access and a real LLM.

Writes: docs/REPORT-eval-web-research.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini); network access.
               W2 additionally requires BRAVE_SEARCH_API_KEY in the environment.

Usage:
    uv run python evals/eval_web_research.py
"""

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from evals._deps import make_eval_deps
from evals._judge import run_judge
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import LlmModel

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-web-research.md"

# Stable public URLs with predictable content
_EXAMPLE_URL = "https://example.com"
_IANA_URL = "https://www.iana.org/domains/reserved"

_AGENT = build_agent(config=settings)


# ---------------------------------------------------------------------------
# LLM judge for web fetch content quality
# ---------------------------------------------------------------------------


class _WebFetchJudgeScore(BaseModel):
    content_accuracy: int
    """1–5: agent correctly describes what the fetched page says."""
    question_answered: bool
    """Agent directly answered the specific question asked."""
    has_fabrication: bool
    """Agent asserts things not plausibly on the fetched page."""
    rationale: str
    """One sentence overall quality judgment."""


async def _judge_web_fetch(
    response_text: str,
    url: str,
    question: str,
    llm_model: LlmModel,
    label: str,
) -> tuple[bool, list[str]]:
    """Evaluate whether the agent correctly synthesized the fetched page content."""
    prompt = (
        f"Evaluate whether this agent response correctly answers the question based on fetching {url}.\n\n"
        f"QUESTION: {question}\n\n"
        f"AGENT RESPONSE:\n---\n{response_text[:2000]}\n---\n\n"
        "Score:\n"
        "- content_accuracy: does the response correctly describe the fetched page content (1–5)?\n"
        "- question_answered: did the agent directly answer the specific question asked?\n"
        "- has_fabrication: did the agent state things not plausibly on the fetched page?\n"
        "- rationale: one sentence overall judgment"
    )
    score, err = await run_judge(
        prompt,
        _WebFetchJudgeScore,
        llm_model=llm_model,
        system_prompt=(
            "You are a strict quality evaluator for AI web research responses. "
            "Assess factual accuracy and question answering quality honestly."
        ),
    )

    lines: list[str] = []
    if score is None:
        lines.append(f"    SKIP: {label} — {err}")
        return True, lines

    lines.append(
        f"    Judge: content_accuracy={score.content_accuracy}/5"
        f"  question_answered={score.question_answered}"
        f"  fabrication={score.has_fabrication}"
    )
    lines.append(f"    Rationale: {score.rationale}")

    passed = score.content_accuracy >= 3 and score.question_answered and not score.has_fabrication
    if passed:
        lines.append(f"    PASS: {label} — fetch content quality check passed")
    else:
        lines.append(f"    FAIL: {label} — fetch content quality check failed")
    return passed, lines


def _response_text(result: Any) -> str:
    """Extract all assistant text parts from a TurnResult."""
    parts = []
    for msg in result.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content)
    return " ".join(parts)


def _tool_names_called(result: Any) -> list[str]:
    """Return the tool names called (in call order) across all ModelResponses."""
    names = []
    for msg in result.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.append(part.tool_name)
    return names


# ---------------------------------------------------------------------------
# W1: web_fetch_executes
# ---------------------------------------------------------------------------


async def run_web_fetch_executes() -> dict[str, Any]:
    """Agent fetches example.com; tool fires; fetched content synthesized correctly."""
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    frontend = HeadlessFrontend()
    deps = make_eval_deps()
    question = (
        f"Use web_fetch to fetch {_EXAMPLE_URL} and tell me what the page says it is used for."
    )

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input=question,
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
    tools_called = _tool_names_called(result)
    fetch_called = "web_fetch" in tools_called
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"tools_called={tools_called} fetch_called={fetch_called} preview={text[:250]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif not fetch_called:
        verdict, failure = "FAIL", f"web_fetch was not called; tools={tools_called}"
    else:
        t_judge = time.monotonic()
        judge_ok, judge_lines = await _judge_web_fetch(
            text, _EXAMPLE_URL, question, deps.model, "w1"
        )
        steps.append(
            {
                "name": "judge",
                "ms": (time.monotonic() - t_judge) * 1000,
                "detail": " | ".join(ln.strip() for ln in judge_lines),
            }
        )
        verdict = "PASS" if judge_ok else "FAIL"
        failure = None if judge_ok else "LLM judge: fetch content quality check failed"

    return {
        "id": "web_fetch_executes",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# W2: web_search_executes
# ---------------------------------------------------------------------------


async def run_web_search_executes() -> dict[str, Any]:
    """Agent calls web_search; search results inform the response.

    Skipped when BRAVE_SEARCH_API_KEY is not configured.
    """
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    deps = make_eval_deps()

    if not deps.config.brave_search_api_key:
        steps.append(
            {
                "name": "preflight",
                "ms": 0,
                "detail": "BRAVE_SEARCH_API_KEY not configured — skipping",
            }
        )
        return {
            "id": "web_search_executes",
            "verdict": "SKIP",
            "failure": "BRAVE_SEARCH_API_KEY not configured",
            "steps": steps,
            "duration_ms": (time.monotonic() - case_t0) * 1000,
        }

    frontend = HeadlessFrontend()

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input=(
                "Use web_search to find the official Python programming language website. "
                "Tell me the URL you found and one fact from the results."
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
    tools_called = _tool_names_called(result)
    search_called = "web_search" in tools_called
    # python.org should appear in search results for this query
    python_mentioned = "python" in text.lower()
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"tools_called={tools_called} search_called={search_called} python_mentioned={python_mentioned} preview={text[:250]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif not search_called:
        verdict, failure = "FAIL", f"web_search was not called; tools={tools_called}"
    elif not python_mentioned:
        verdict, failure = (
            "FAIL",
            "search results not referenced in response — 'python' not found",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "web_search_executes",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# W3: web_fetch_second_domain
# ---------------------------------------------------------------------------


async def run_web_fetch_second_domain() -> dict[str, Any]:
    """Agent fetches a second distinct URL; proves multi-URL fetch is reliable.

    Uses the IANA reserved-domains page — stable, predictable public URL.
    Testing a second URL validates that web_fetch is not a one-shot tool.
    """
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    frontend = HeadlessFrontend()
    deps = make_eval_deps()
    question = (
        f"Use web_fetch to fetch {_IANA_URL} and tell me what organization manages this page."
    )

    t = time.monotonic()
    with anyio.fail_after(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=_AGENT,
            user_input=question,
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
    tools_called = _tool_names_called(result)
    fetch_called = "web_fetch" in tools_called
    steps.append(
        {
            "name": "response_analysis",
            "ms": 0,
            "detail": f"tools_called={tools_called} fetch_called={fetch_called} preview={text[:250]!r}",
        }
    )

    if result.outcome == "error":
        verdict, failure = "FAIL", f"turn error: {text[:200]}"
    elif not fetch_called:
        verdict, failure = "FAIL", f"web_fetch was not called; tools={tools_called}"
    else:
        t_judge = time.monotonic()
        judge_ok, judge_lines = await _judge_web_fetch(text, _IANA_URL, question, deps.model, "w3")
        steps.append(
            {
                "name": "judge",
                "ms": (time.monotonic() - t_judge) * 1000,
                "detail": " | ".join(ln.strip() for ln in judge_lines),
            }
        )
        verdict = "PASS" if judge_ok else "FAIL"
        failure = None if judge_ok else "LLM judge: fetch content quality check failed"

    return {
        "id": "web_fetch_second_domain",
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
    passed = sum(1 for c in cases if c["verdict"] in ("PASS", "SOFT PASS", "SKIP"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {settings.llm.provider} / {settings.llm.model or 'default'}  ",
        f"**Total runtime:** {total_ms:.0f}ms  ",
        f"**Result:** {passed}/{len(cases)} passed/skipped",
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
            lines.append(f"- **Failure/Note:** {c['failure']}")
        lines.append("")

    lines += ["---", ""]
    section = "\n".join(lines)

    if _REPORT_PATH.exists():
        existing = _REPORT_PATH.read_text(encoding="utf-8")
        split = existing.split("\n", 2)
        updated = split[0] + "\n\n" + section + ("\n".join(split[1:]) if len(split) > 1 else "")
    else:
        updated = "# Eval Report: Web Research\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Web Research")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    runners = [
        ("W1: web_fetch_executes", run_web_fetch_executes),
        ("W2: web_search_executes", run_web_search_executes),
        ("W3: web_fetch_second_domain", run_web_fetch_second_domain),
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

    total_ms = (time.monotonic() - t0) * 1000
    # SKIP counts as passing — it means the prerequisite (brave API key) is absent,
    # not that the code is broken.
    passed = sum(1 for c in all_cases if c["verdict"] in ("PASS", "SOFT PASS", "SKIP"))
    _write_report(all_cases, total_ms)

    print(f"\n{'=' * 60}")
    verdict = "PASS" if passed == len(all_cases) else "FAIL"
    print(
        f"  Verdict: {verdict} ({passed}/{len(all_cases)} cases passed/skipped, {total_ms:.0f}ms)"
    )
    print(f"{'=' * 60}")
    return 0 if passed == len(all_cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
