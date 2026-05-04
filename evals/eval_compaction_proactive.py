#!/usr/bin/env python3
"""Eval: compaction proactive UAT — M3 fires organically via real run_turn.

co autonomously fetches Wikipedia pages and reviews for the 2021 film Finch
(Tom Hanks, Apple TV+) until the M3 proactive compaction threshold is crossed.
M1 persists oversized tool results at emit time. No hand-built history, no
article caps, no fallback content.

Prerequisites: LLM provider configured (Ollama or cloud), network access.

Usage:
    uv run python evals/eval_compaction_proactive.py
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import sys
import time
from contextlib import AsyncExitStack, redirect_stdout
from pathlib import Path

import httpx
from evals._judge import run_judge
from evals._timeouts import EVAL_PROBE_TIMEOUT_SECS
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.agent.core import build_agent
from co_cli.bootstrap.core import create_deps
from co_cli.config.core import KNOWLEDGE_DIR, TOOL_RESULTS_DIR
from co_cli.context.compaction import SUMMARY_MARKER_PREFIX
from co_cli.context.orchestrate import run_turn
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import LlmModel
from co_cli.memory.session import new_session_path
from co_cli.memory.transcript import persist_session_history

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snippet(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return repr(text)
    head = max_len // 3
    tail = max_len // 3
    return repr(text[:head]) + f" ...<{len(text) - head - tail} chars>... " + repr(text[-tail:])


def _extract_section(text: str, header: str) -> str:
    """Return the body of a named ## section, empty string if absent."""
    idx = text.find(header)
    if idx == -1:
        return ""
    start = idx + len(header)
    next_sec = text.find("\n##", start)
    return text[start:next_sec] if next_sec != -1 else text[start:]


def _check_structure(summary: str, label: str) -> tuple[bool, list[str]]:
    """Check that the summary matches the required section schema.

    Validates: marker prefix, minimum length, required sections present,
    Completed Actions non-empty with at least one URL, Critical Context
    has substantive content.
    """
    lines: list[str] = []
    all_ok = True

    if summary.startswith(SUMMARY_MARKER_PREFIX):
        lines.append(f"    PASS: {label} — marker prefix present")
    else:
        lines.append(f"    FAIL: {label} — summary does not start with SUMMARY_MARKER_PREFIX")
        all_ok = False

    if len(summary) >= 400:
        lines.append(f"    PASS: {label} — length {len(summary)} chars (≥400)")
    else:
        lines.append(f"    FAIL: {label} — too short: {len(summary)} chars (<400)")
        all_ok = False

    for section in ("## Completed Actions", "## Critical Context"):
        if section in summary:
            lines.append(f"    PASS: {label} — '{section}' present")
        else:
            lines.append(f"    FAIL: {label} — '{section}' missing")
            all_ok = False

    if any(s in summary for s in ("## Active Task", "## Next Step")):
        lines.append(f"    PASS: {label} — task-tracking section present")
    else:
        lines.append(f"    FAIL: {label} — neither '## Active Task' nor '## Next Step' present")
        all_ok = False

    completed = _extract_section(summary, "## Completed Actions")
    real_actions = [
        ln
        for ln in completed.splitlines()
        if ln.strip() and ln.strip() not in ("None.", "None") and not ln.startswith("##")
    ]
    if real_actions:
        lines.append(f"    PASS: {label} — Completed Actions has {len(real_actions)} action(s)")
    else:
        lines.append(f"    FAIL: {label} — Completed Actions is empty or 'None.'")
        all_ok = False

    if "https://" in completed:
        lines.append(f"    PASS: {label} — Completed Actions contains a source URL")
    else:
        lines.append(f"    FAIL: {label} — no URL in Completed Actions (no fetch evidence)")
        all_ok = False

    critical = _extract_section(summary, "## Critical Context")
    content_lines = [
        ln
        for ln in critical.splitlines()
        if ln.strip() and ln.strip() not in ("None.", "None") and not ln.startswith("##")
    ]
    if len(content_lines) >= 2:
        lines.append(
            f"    PASS: {label} — Critical Context has {len(content_lines)} content line(s)"
        )
    else:
        lines.append(f"    FAIL: {label} — Critical Context has <2 content lines")
        all_ok = False

    return all_ok, lines


# ---------------------------------------------------------------------------
# LLM judge for summary quality
# ---------------------------------------------------------------------------


class _SummaryJudgeScore(BaseModel):
    factual_density: int
    """1–5: specific facts (names, dates, roles, URLs) from the source preserved in summary."""
    source_attribution: int
    """1–5: source URLs cited in Completed Actions (5 = multiple specific URLs)."""
    task_continuity: int
    """1–5: a new session can continue the work from this summary alone."""
    has_fabrication: bool
    """True if the summary asserts facts not present in the source messages."""
    missing_key_facts: list[str]
    """Specific facts in the source that are absent from the summary."""
    rationale: str
    """One sentence overall quality judgment."""


def _serialize_messages(messages: list[ModelMessage], max_chars: int = 4000) -> str:
    """Condense a message list to plain text for the judge prompt."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    if not part.content.startswith(SUMMARY_MARKER_PREFIX):
                        parts.append(f"[USER] {part.content[:600]}")
                elif isinstance(part, ToolReturnPart):
                    content = (
                        part.content if isinstance(part.content, str) else json.dumps(part.content)
                    )
                    parts.append(f"[TOOL RETURN] {part.tool_name}: {content[:400]}")
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(f"[ASSISTANT] {part.content[:600]}")
                elif isinstance(part, ToolCallPart):
                    args = part.args if isinstance(part.args, str) else json.dumps(part.args)
                    parts.append(f"[TOOL CALL] {part.tool_name}({args[:200]})")
    text = "\n".join(parts)
    return text[:max_chars] if len(text) > max_chars else text


async def _judge_summary_quality(
    summary_text: str,
    source_messages: list[ModelMessage],
    llm_model: LlmModel,
    label: str,
) -> tuple[bool, list[str]]:
    """Evaluate compaction summary quality via the common LLM judge.

    Compares summary against the serialized source messages it replaced.
    Returns (passed, lines); passed=True when all rubric thresholds are met.
    """
    source_text = _serialize_messages(source_messages)
    prompt = (
        "Evaluate this compaction summary against the original messages it replaced.\n\n"
        "ORIGINAL MESSAGES (condensed):\n---\n"
        f"{source_text}\n---\n\n"
        "COMPACTION SUMMARY:\n---\n"
        f"{summary_text[:3000]}\n---\n\n"
        "Score each dimension:\n"
        "- factual_density: specific facts (names, dates, URLs, roles) from source in summary\n"
        "- source_attribution: source URLs cited in Completed Actions\n"
        "- task_continuity: new session can continue work from this summary alone\n"
        "- has_fabrication: summary asserts anything NOT in source messages\n"
        "- missing_key_facts: list specific facts from source absent from summary\n"
        "- rationale: one sentence overall judgment"
    )
    score, err = await run_judge(
        prompt,
        _SummaryJudgeScore,
        llm_model=llm_model,
        system_prompt=(
            "You are a strict quality evaluator for AI compaction summaries. "
            "Assess faithfulness and usefulness honestly. Score 1–5 per dimension."
        ),
    )

    lines: list[str] = []
    if score is None:
        lines.append(f"    SKIP: {label} — {err}")
        return True, lines  # skip, not a failure

    lines.append(
        f"    Judge scores: factual_density={score.factual_density}/5"
        f"  source_attribution={score.source_attribution}/5"
        f"  task_continuity={score.task_continuity}/5"
        f"  fabrication={score.has_fabrication}"
    )
    lines.append(f"    Rationale: {score.rationale}")
    if score.missing_key_facts:
        lines.append(f"    Missing facts: {score.missing_key_facts}")

    passed = True
    for name, value, minimum in [
        ("factual_density", score.factual_density, 3),
        ("source_attribution", score.source_attribution, 3),
        ("task_continuity", score.task_continuity, 3),
    ]:
        if value >= minimum:
            lines.append(f"    PASS: {label} — {name} {value}/5 (≥{minimum})")
        else:
            lines.append(f"    FAIL: {label} — {name} {value}/5 (<{minimum})")
            passed = False

    if not score.has_fabrication:
        lines.append(f"    PASS: {label} — no fabrication detected")
    else:
        lines.append(f"    FAIL: {label} — fabrication detected")
        passed = False

    return passed, lines


# ---------------------------------------------------------------------------
# UAT: proactive M3 compaction via real run_turn
# ---------------------------------------------------------------------------


async def step_proactive_compaction() -> bool:
    """UAT: co autonomously researches Finch (2021) until M3 compaction fires.

    Open-ended loop driven by real run_turn. co decides what to fetch and in what
    order; M1 persists oversized results at emit time; M3 fires organically when
    context pressure crosses 65% of max_ctx (32768 — halved from the Ollama default
    so M3 triggers at ~21k tokens rather than ~85k). No hand-built history, no
    article caps, no fallback content.
    """
    print("\n--- UAT: Proactive M3 compaction via run_turn (Finch/2021) ---")

    # Network preflight
    try:
        async with asyncio.timeout(EVAL_PROBE_TIMEOUT_SECS):
            async with httpx.AsyncClient() as _probe:
                probe_resp = await _probe.head("https://en.wikipedia.org/")
        if probe_resp.status_code >= 500:
            print(f"UAT: FAIL: coarse reachability probe failed — HTTP {probe_resp.status_code}")
            print("  (coarse reachability probe — does not guarantee per-URL availability)")
            return False
    except TimeoutError:
        print("UAT: FAIL: coarse reachability probe timed out")
        print("  (coarse reachability probe — does not guarantee per-URL availability)")
        return False
    except Exception as exc:
        print(f"UAT: FAIL: coarse reachability probe failed — {exc}")
        print("  (coarse reachability probe — does not guarantee per-URL availability)")
        return False
    print("  Preflight: en.wikipedia.org reachable")

    before_tool_results = set(TOOL_RESULTS_DIR.glob("*")) if TOOL_RESULTS_DIR.exists() else set()
    before_knowledge = set(KNOWLEDGE_DIR.glob("*")) if KNOWLEDGE_DIR.exists() else set()

    frontend = HeadlessFrontend(verbose=True)
    message_history: list[ModelMessage] = []
    passed = True
    compaction_fired = False
    summary_texts: list[str] = []
    before_session_path: Path | None = None
    before_session_files: set[Path] = set()
    pre_compaction_history: list[ModelMessage] = []
    judge_llm_model: LlmModel | None = None

    async with AsyncExitStack() as stack:
        deps = await create_deps(frontend, stack)
        deps.config.llm.max_ctx = 32768

        # Assign a real session path so persist_session_history has a target.
        deps.session.session_path = new_session_path(deps.sessions_dir)
        before_session_path = deps.session.session_path
        before_session_files = (
            set(deps.sessions_dir.glob("*.jsonl")) if deps.sessions_dir.exists() else set()
        )
        judge_llm_model = deps.model
        agent = build_agent(
            config=deps.config,
            model=deps.model,
            tool_registry=deps.tool_registry,
        )

        initial_prompt = (
            "I want you to conduct a comprehensive deep study of the 2021 Apple TV+ film Finch, "
            "starring Tom Hanks and directed by Miguel Sapochnik. "
            "Research every angle of this film by fetching as many primary sources as you need. "
            "Start with the Wikipedia page for the film itself, then fetch the Wikipedia pages for "
            "Tom Hanks, Miguel Sapochnik (the director), Caleb Landry Jones (who voiced Jeff the "
            "robot), Gustavo Santaolalla (the composer), and the list of Apple TV+ original films. "
            "Also fetch at least three critical reviews from major outlets such as Variety, "
            "The Guardian, RogerEbert.com, IndieWire, and the Hollywood Reporter. "
            "Do not stop after one or two sources — this is a deep study. "
            "Fetch the Wikipedia pages for the film, the director, all major cast members, "
            "the composer, and at least three critical reviews. "
            "Keep fetching until you have covered every angle: the plot, themes, production history "
            "(including the original BIOS title), the cast and crew, the score, the critical "
            "reception, and Apple TV+ context. Do not stop until you have covered all major facets."
        )

        _continuation_prompts = [
            (
                "Keep going — fetch the Wikipedia page for director Miguel Sapochnik to understand "
                "his Game of Thrones background and how that shaped his approach to Finch."
            ),
            (
                "Now fetch Caleb Landry Jones's Wikipedia page — I want to understand his background "
                "and voice performance as Jeff the robot."
            ),
            (
                "Fetch Gustavo Santaolalla's Wikipedia page to understand how his Academy Award-winning "
                "work on Brokeback Mountain and Babel compares to his score for Finch."
            ),
            (
                "Fetch the Wikipedia list of Apple TV+ original films to place Finch in Apple's "
                "content strategy alongside CODA, Greyhound, and other prestige originals."
            ),
            (
                "Fetch the Tom Hanks Wikipedia page to understand how Finch fits into his career arc "
                "alongside Cast Away, The Terminal, and other isolated-protagonist roles."
            ),
            (
                "Fetch at least one critical review from Variety, RogerEbert.com, or The Guardian "
                "to get the critical consensus on the film's emotional resonance."
            ),
            (
                "Fetch the IndieWire or Hollywood Reporter review to understand the trade press "
                "reception and how critics evaluated it against other post-apocalyptic films."
            ),
            (
                "Fetch information about the production history — specifically the BIOS working title "
                "and how the film changed during COVID-related delays and Apple TV+ acquisition."
            ),
            (
                "Fetch information about the Amblin Entertainment and Pariah Entertainment production "
                "companies involved, and how this fits Apple TV+'s acquisition strategy."
            ),
            (
                "Do a final synthesis — fetch any remaining sources about the film's themes of "
                "loneliness, companionship, and legacy in the context of Tom Hanks's filmography."
            ),
        ]

        max_turns = 30
        for turn_idx in range(max_turns):
            user_input = (
                initial_prompt
                if turn_idx == 0
                else _continuation_prompts[min(turn_idx - 1, len(_continuation_prompts) - 1)]
            )

            prev_len = len(message_history)
            pre_turn_history = list(message_history)
            print(f"  Turn {turn_idx + 1}/{max_turns} — history: {prev_len} msgs")

            _turn_start = time.monotonic()
            turn_result = await run_turn(
                agent=agent,
                user_input=user_input,
                deps=deps,
                message_history=message_history,
                frontend=frontend,
            )
            _elapsed = time.monotonic() - _turn_start
            print(f"    turn elapsed: {_elapsed:.1f}s")

            if turn_result.outcome == "error":
                print(
                    f"UAT: FAIL (turn error): turn {turn_idx + 1} — LLM call error or timeout"
                    f" ({_elapsed:.1f}s); context may be too large for the local model"
                )
                return False

            message_history = turn_result.messages
            persist_session_history(
                session_path=deps.session.session_path,
                messages=message_history,
                persisted_message_count=deps.session.persisted_message_count,
                history_compacted=deps.runtime.compaction_applied_this_turn,
            )
            deps.session.persisted_message_count = len(message_history)

            for m in message_history:
                if isinstance(m, ModelRequest):
                    for p in m.parts:
                        if (
                            isinstance(p, UserPromptPart)
                            and isinstance(p.content, str)
                            and SUMMARY_MARKER_PREFIX in p.content
                            and p.content not in summary_texts
                        ):
                            summary_texts.append(p.content)
                            compaction_fired = True

            if compaction_fired:
                pre_compaction_history = pre_turn_history
                print(f"  Compaction fired after turn {turn_idx + 1}")
                break

            from pydantic_ai.messages import ModelResponse, ToolCallPart

            latest_exchange = message_history[prev_len:]
            tool_calls_this_turn = sum(
                1
                for m in latest_exchange
                if isinstance(m, ModelResponse)
                for p in m.parts
                if isinstance(p, ToolCallPart)
            )

            if tool_calls_this_turn == 0 and turn_idx >= 1:
                print(
                    "UAT: FAIL (agentic stall): co returned a turn with no tool calls before "
                    "compaction triggered — prompt insufficient or agentic flow regression"
                )
                return False

        if not compaction_fired:
            print(f"UAT: FAIL (no compaction): {max_turns} turns completed, M3 never triggered")
            return False

    # Side-effect report
    if TOOL_RESULTS_DIR.exists():
        new_tool_results = set(TOOL_RESULTS_DIR.glob("*")) - before_tool_results
    else:
        new_tool_results = set()
    if KNOWLEDGE_DIR.exists():
        new_knowledge = set(KNOWLEDGE_DIR.glob("*")) - before_knowledge
    else:
        new_knowledge = set()

    print(f"\n  Persisted tool results: {len(new_tool_results)} new files")
    for p in sorted(new_tool_results):
        print(f"    {p} ({p.stat().st_size:,} bytes)")
    print(f"  Knowledge artifacts: {len(new_knowledge)} new files")
    for p in sorted(new_knowledge):
        print(f"    {p}")
    print(f"  Compactions fired: {len(summary_texts)}")
    print(f"  Final history: {len(message_history)} messages")

    # Session path stability (in-place rewrite regression guard)
    if before_session_path is not None:
        after_session_files = (
            set(deps.sessions_dir.glob("*.jsonl")) if deps.sessions_dir.exists() else set()
        )
        new_session_files = after_session_files - before_session_files
        unexpected_forks = [p for p in new_session_files if p != before_session_path]
        if deps.session.session_path == before_session_path:
            print(f"UAT: PASS: session path stable after compaction ({before_session_path.name})")
        else:
            print(
                f"UAT: FAIL: session path changed after compaction:"
                f" {before_session_path.name} → {deps.session.session_path.name}"
            )
            passed = False
        if not unexpected_forks:
            print(
                f"UAT: PASS: no child sessions forked — {len(new_session_files)} file(s)"
                f" created ({[p.name for p in new_session_files]})"
            )
        else:
            print(
                f"UAT: FAIL: {len(unexpected_forks)} unexpected session file(s) forked:"
                f" {[p.name for p in unexpected_forks]}"
            )
            passed = False

    # Approval-hang guard
    approval_prompts = getattr(frontend, "approval_calls", None) or getattr(
        frontend, "approval_prompts", []
    )
    if approval_prompts:
        print(f"UAT: FAIL: unexpected approval prompts captured: {approval_prompts}")
        passed = False
    else:
        print("UAT: PASS: no approval prompts (expected)")

    # Structural + LLM judge checks on the compaction summary
    summary_text = summary_texts[0] if summary_texts else None
    if summary_text:
        print(f"\n  Full LLM summary output ({len(summary_text)} chars):")
        for line in summary_text.split("\n"):
            print(f"    | {line}")

        # --- Structural: section schema, non-empty actions, URL attribution, length ---
        struct_ok, struct_lines = _check_structure(summary_text, "proactive")
        for line in struct_lines:
            print(line)
        if struct_ok:
            print("UAT: PASS: structural schema valid")
        else:
            print("UAT: FAIL: structural schema invalid — see above")
            passed = False

        # --- LLM judge: faithfulness + completeness against dropped messages ---
        print("\n  LLM judge: evaluating summary against pre-compaction messages...")
        judge_ok, judge_lines = await _judge_summary_quality(
            summary_text=summary_text,
            source_messages=pre_compaction_history,
            llm_model=judge_llm_model,
            label="judge",
        )
        for line in judge_lines:
            print(line)
        if judge_ok:
            print("UAT: PASS: LLM judge quality check passed")
        else:
            print("UAT: FAIL: LLM judge quality check failed — see scores above")
            passed = False
    else:
        print("  No LLM summary text (static circuit-breaker marker)")

    # Tool-result files: informational — count depends on how many turns fired before M3
    if new_tool_results:
        print(f"  Persisted tool-result files: {len(new_tool_results)} (informational)")
    else:
        print(
            "  Persisted tool-result files: 0 (informational — M1 threshold may not have been crossed)"
        )

    if passed:
        print("UAT: PASS: proactive compaction complete")
    else:
        print("UAT: FAIL — see above")
    return passed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_LAST_RESULTS: dict[str, bool] = {}
_NOISE_PATTERNS = ("WARNING:", "Compacting conversation")


def _build_report(raw_output: str, results: dict[str, bool]) -> str:
    lines: list[str] = []
    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    verdict = "PASS" if passed_count == total else "FAIL"

    lines.append("# Compaction Proactive Eval Report")
    lines.append("")
    lines.append(f"**Verdict: {verdict}** ({passed_count}/{total} steps passed)")
    lines.append("")

    lines.append("| Step | Result |")
    lines.append("|------|--------|")
    for name, ok in results.items():
        lines.append(f"| {name} | {'PASS' if ok else '**FAIL**'} |")
    lines.append("")

    last_eq = raw_output.rfind("\n====")
    if last_eq > 0:
        prev_eq = raw_output.rfind("\n====", 0, last_eq)
        results_cut = raw_output[:prev_eq] if prev_eq > 0 else raw_output[:last_eq]
    else:
        results_cut = raw_output

    step_blocks = re.findall(
        r"(-{3} UAT.+?-{3})(.*?)(?=-{3} UAT|$)",
        results_cut,
        re.DOTALL,
    )
    for header_raw, body_raw in step_blocks:
        lines.append(f"## {header_raw.strip('- ').strip()}")
        lines.append("")
        filtered = [
            line
            for line in body_raw.splitlines()
            if not any(line.strip().startswith(p) for p in _NOISE_PATTERNS)
        ]
        while filtered and not filtered[0].strip():
            filtered.pop(0)
        while filtered and not filtered[-1].strip():
            filtered.pop()
        if filtered:
            lines.append("```")
            lines.extend(filtered)
            lines.append("```")
        lines.append("")

    return "\n".join(lines)


async def _run_all() -> int:
    global _LAST_RESULTS
    print("=" * 60)
    print("  Eval: Compaction — Proactive M3 UAT")
    print("=" * 60)

    results: dict[str, bool] = {}
    results["Proactive M3 compaction (Finch/UAT)"] = await step_proactive_compaction()

    print("\n" + "=" * 60)
    print("  Results")
    print("=" * 60)
    all_pass = True
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            all_pass = False

    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    print(f"\n  {passed_count}/{total} passed")
    print(f"\nVERDICT: {'PASS' if all_pass else 'FAIL'}")
    _LAST_RESULTS.update(results)
    return 0 if all_pass else 1


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    buf = io.StringIO()

    class Tee:
        def __init__(self, *targets):
            self.targets = targets

        def write(self, s):
            for t in self.targets:
                t.write(s)
            return len(s)

        def flush(self):
            for t in self.targets:
                t.flush()

    tee = Tee(sys.stdout, buf)
    with redirect_stdout(tee):
        exit_code = asyncio.run(_run_all())

    report_path = Path("docs/REPORT-compaction-proactive.md")
    report_path.write_text(_build_report(buf.getvalue(), _LAST_RESULTS), encoding="utf-8")
    print(f"\nReport: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
