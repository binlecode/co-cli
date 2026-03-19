#!/usr/bin/env python3
"""Personality eval trace report — runs two P2 cases through the real agent,
collects OTel spans from SQLite, and writes a detailed trace report.

Cases:
  - finch-explains-why (personality=finch)
  - jeff-uncertainty (personality=jeff)

Output: evals/trace-report-finch-jeff.md

Usage:
    uv run python scripts/trace_report_personality.py
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Resolve evals/ package from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic_ai import DeferredToolRequests
from pydantic_ai.usage import UsageLimits

from co_cli.agent import build_agent
from co_cli.config import DATA_DIR, settings
from co_cli.deps import CoConfig
from evals._common import (
    EvalCase,
    TurnTrace,
    _check_display,
    _check_result,
    _md_cell,
    analyze_turn_spans,
    bootstrap_telemetry,
    build_timeline,
    collect_spans_for_run,
    detect_model_tag,
    load_cases,
    make_eval_deps,
    make_eval_settings,
    score_response,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EVALS_DIR = Path(__file__).parent.parent / "evals"
_JSONL_PATH = _EVALS_DIR / "personality_behavior.jsonl"
_REPORT_PATH = _EVALS_DIR / "trace-report-finch-jeff.md"
_DB_PATH = str(DATA_DIR / "co-cli.db")

_TARGET_CASE_IDS = ("finch-explains-why", "jeff-uncertainty")
_TRACE_MAX_TOKENS = 2048
_TRACE_REQUEST_LIMIT = 4


# ---------------------------------------------------------------------------
# Local case+trace wrapper
# ---------------------------------------------------------------------------


@dataclass
class _CaseRunTrace:
    case: EvalCase
    turn_trace: TurnTrace


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


async def run_case(
    case: EvalCase,
    agent: Any,
    model_settings: Any,
    provider: Any,
) -> _CaseRunTrace:
    """Run a single eval case (first turn only), collect spans, return trace."""
    deps = make_eval_deps(
        session_id=f"trace-report-{case.personality}",
        personality=case.personality,
    )
    eval_settings = make_eval_settings(model_settings, max_tokens=_TRACE_MAX_TOKENS)
    prompt = case.turns[0]
    checks = case.checks_per_turn[0]

    print(f"\n  Running {case.id} (personality={case.personality}) ...")
    print(f"  Prompt: {prompt!r}")

    start_ns = time.time_ns()
    t0 = time.monotonic()

    try:
        result = await agent.run(
            prompt,
            deps=deps,
            model_settings=eval_settings,
            usage_limits=UsageLimits(request_limit=_TRACE_REQUEST_LIMIT),
        )
        wall_time_s = time.monotonic() - t0

        provider.force_flush()
        await asyncio.sleep(0.2)

        response_text = ""
        if not isinstance(result.output, DeferredToolRequests):
            response_text = str(result.output)

        spans = collect_spans_for_run(start_ns, _DB_PATH)
        print(f"  Collected {len(spans)} spans from SQLite")

        if not spans:
            failed_checks = score_response(response_text, checks)
            turn_trace = TurnTrace(
                spans=[],
                root_span=None,
                model_requests=[],
                tool_spans=[],
                timeline=[],
                wall_time_s=wall_time_s,
                response_text=response_text,
                failed_checks=failed_checks,
                error=None,
            )
            return _CaseRunTrace(case=case, turn_trace=turn_trace)

        turn_trace = analyze_turn_spans(prompt, checks, spans, wall_time_s)

        # If span analysis didn't find a response text, use result.output
        if not turn_trace.response_text and response_text:
            turn_trace.response_text = response_text
            turn_trace.failed_checks = score_response(response_text, checks)

        verdict = "PASS" if not turn_trace.failed_checks else "FAIL"
        print(f"  Result: {verdict}  |  wall_time={wall_time_s:.1f}s")
        if turn_trace.failed_checks:
            print(f"  Failed checks: {turn_trace.failed_checks}")

        return _CaseRunTrace(case=case, turn_trace=turn_trace)

    except Exception as exc:
        wall_time_s = time.monotonic() - t0
        provider.force_flush()
        error_msg = f"{type(exc).__name__}: {exc}"
        print(f"  ERROR: {error_msg}")
        turn_trace = TurnTrace(
            spans=[],
            root_span=None,
            model_requests=[],
            tool_spans=[],
            timeline=[],
            wall_time_s=wall_time_s,
            response_text="",
            failed_checks=[],
            error=error_msg,
        )
        return _CaseRunTrace(case=case, turn_trace=turn_trace)


# ---------------------------------------------------------------------------
# Markdown report writer
# ---------------------------------------------------------------------------


def write_report(case_traces: list[_CaseRunTrace], model_tag: str) -> None:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines: list[str] = []
    w = lines.append

    w("# Personality Eval Trace Report")
    w("")
    w(f"Generated: {timestamp}")
    w(f"Model: {model_tag}")
    w("")
    w("---")

    for ct in case_traces:
        case = ct.case
        rt = ct.turn_trace
        verdict = "PASS" if not rt.failed_checks else "FAIL"
        failed_str = f"  |  failed_checks: {rt.failed_checks}" if rt.failed_checks else ""

        w("")
        w(f"## Case: {case.id} — {case.personality}")
        w("")
        w(f'**Prompt:** "{case.turns[0]}"')
        w(f"**Result:** {verdict}{failed_str}")
        w(f"**Total wall time:** {rt.wall_time_s:.1f}s")
        w("")

        if rt.error:
            w("**Error:**")
            w("")
            w(f"```\n{rt.error}\n```")
            w("")
            w("---")
            continue

        # Timeline
        w("### Timeline")
        w("")
        w("| Elapsed (ms) | Duration (ms) | Span | Detail |")
        w("|---|---|---|---|")

        if rt.root_span and rt.timeline:
            for trow in rt.timeline:
                w(
                    f"| {trow.elapsed_ms:,} | {trow.duration_ms} | "
                    f"{_md_cell(trow.span_name)} | {_md_cell(trow.detail)} |"
                )
        else:
            w("| — | — | (no spans collected) | — |")
        w("")

        # Per-model-request sections
        prev_input_tokens = 0
        for req in rt.model_requests:
            w(f"### Model Request {req.request_index}")
            w("")

            token_delta = ""
            if req.request_index > 1 and prev_input_tokens > 0:
                delta = req.input_tokens - prev_input_tokens
                sign = "+" if delta >= 0 else ""
                token_delta = f" ({sign}{delta:,} vs prior request)"
            w(f"- **Input tokens:** {req.input_tokens:,}{token_delta}")
            w(f"- **Output tokens:** {req.output_tokens:,}")
            w(f"- **Finish reason:** {req.finish_reason}")

            if req.thinking_excerpt:
                w(f"- **Thinking excerpt:** {req.thinking_excerpt}")
            else:
                w("- **Thinking excerpt:** none")

            for tc in req.tool_calls:
                tc_name = tc.get("name") or tc.get("tool_name", "unknown")
                tc_args_raw = tc.get("arguments") or tc.get("args", "{}")
                if isinstance(tc_args_raw, str):
                    try:
                        tc_args = json.loads(tc_args_raw)
                    except (json.JSONDecodeError, TypeError):
                        tc_args = {"raw": tc_args_raw}
                elif isinstance(tc_args_raw, dict):
                    tc_args = tc_args_raw
                else:
                    tc_args = {}
                w(f"- **Tool call emitted:** `{tc_name}({json.dumps(tc_args)})`")

            prev_input_tokens = req.input_tokens
            w("")

        # Tool sections
        for ts in rt.tool_spans:
            w(f"### Tool: {ts.tool_name}")
            w("")
            w(f"- **Arguments:** `{json.dumps(ts.arguments)}`")
            dur_str = f"{int(ts.duration_ms)}ms" if ts.duration_ms is not None else "unknown"
            w(f"- **Duration:** {dur_str}")
            w(f"- **Result:** `{ts.result_preview}`")
            w("")

        # Final response text
        w("### Response text")
        w("")
        if rt.response_text:
            w(rt.response_text)
        else:
            w("(no text response captured in span attributes)")
        w("")

        # Scoring table — uses turn_trace.failed_checks (derived from span response)
        w("### Scoring")
        w("")
        w("| Check | Type | Result |")
        w("|---|---|---|")
        for check in case.checks_per_turn[0]:
            display = _check_display(check)
            check_type = check.get("type", "")
            result_str = _check_result(check, rt.failed_checks)
            w(f"| {display} | {check_type} | {result_str} |")
        w("")
        w("---")

    with open(_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nReport written to {_REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("=" * 70)
    print("  Personality Eval Trace Report")
    print("=" * 70)

    # Bootstrap telemetry before agent creation
    provider = bootstrap_telemetry(_DB_PATH)

    model_tag = detect_model_tag()
    print(f"\n  Model: {model_tag}")

    # Load target cases
    all_cases = load_cases(_JSONL_PATH)
    target_cases = [c for c in all_cases if c.id in _TARGET_CASE_IDS]

    if not target_cases:
        print(f"ERROR: could not find cases {_TARGET_CASE_IDS} in {_JSONL_PATH}")
        sys.exit(1)

    # Sort to ensure deterministic order: finch first, jeff second
    target_cases.sort(key=lambda c: _TARGET_CASE_IDS.index(c.id))
    print(f"  Cases: {[c.id for c in target_cases]}")

    # Create agent once (shared across cases)
    # TODO: source model_settings from make_eval_settings()
    agent, tool_names, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
    print(f"  Agent tools: {len(tool_names)}")

    # Run cases sequentially to keep span windows clean
    case_traces: list[_CaseRunTrace] = []
    for case in target_cases:
        ct = await run_case(case, agent, make_eval_settings(), provider)
        case_traces.append(ct)

    # Write report
    write_report(case_traces, model_tag)


if __name__ == "__main__":
    asyncio.run(main())
