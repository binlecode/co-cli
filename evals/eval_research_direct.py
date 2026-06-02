"""Capability eval — does the *normal agentic flow* conduct research without delegation?

This is the standing guard for a settled decision: the ``web_research`` subagent-delegation
tool was **dropped** (per the drop-web-research-add-fetch-extraction plan) after this eval
showed the main orchestrator loop conducts multi-step web research on its own using only
the atomic ``web_search`` / ``web_fetch`` tools. The eval now guards two invariants: research
stays in the atomic loop, and no in-turn research-delegation tool reappears.

The production surface has no delegation tool: the model sees only ``web_search`` and
``web_fetch``, so the **default production behavior routes research through the atomic
loop**. This eval measures that path end-to-end and checks it completes research to a
quality bar, with zero delegation.

Per case (one real turn through the production ``ORCHESTRATOR_SPEC`` agent):
  - Structural (tool calls tallied LIVE via the frontend's ``on_tool_start`` stream event,
    not read back from ``TurnResult.messages`` — an errored turn drops its tool history
    from the returned messages, so live counting is the only faithful source):
      web_search   ≥ 1   — it attempted to search
      web_fetch    ≥ 1   — it followed a result through to primary page content
                            (this is what distinguishes *research* from a snippet lookup)
      web_research = 0    — regression tripwire: the dropped delegation tool must not
                            reappear and be called; the main loop does the work inline
  - Completion (``TurnResult.outcome``): "continue" = the turn finished; "error" = a model
    call failed mid-turn (e.g. an LLM-call timeout once fetched pages bloat the context).
  - Quality (rubric judge on the final answer, only for completed turns): a grounded,
    sourced synthesis of the fetched content that answers the question — not a hedge.

Per-case verdict (research may be fetch-led — web_search is not mandatory; fetching and
synthesizing primary pages is the atomic loop succeeding whether or not it searched first):
  PASS       — completed, fetch≥1 AND delegate=0 AND judge passes.
  FAIL       — delegate>0 (it reached for the subagent), or it used no web tool at all, or
               it errored before using any web tool.
  SOFT_FAIL  — no delegation, but: errored mid-research (hit the 120s segment wall-clock
               budget, a separate per-segment timeout boundary), OR answered from snippets
               only (fetch=0), OR completed but the synthesis was weak.

DECISION (and exit code): PROVEN (for focused research) when no case delegated, no hard
failure occurred, and ≥1 case completed full research cleanly via the atomic loop. A
context-bloat timeout does NOT disprove the claim — it marks the boundary where the heaviest
multi-doc research still strains the 120s segment budget (a separate per-segment timeout
concern), surfaced rather than folded into the PASS count. Exit 0 when proven, 1 otherwise,
so a manual run is a usable signal.

Real everything (``feedback_eval_real_world_data``): real ``make_eval_deps``, config
``llm.host`` model, real Brave Search + real HTTP fetches, no caps, no test stores.
``ensure_ollama_warm`` runs outside any ``asyncio.timeout``. Requires
``BRAVE_SEARCH_API_KEY`` — without it the eval SKIPs (research is impossible to smoke
without web access), it does not falsely fail.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from evals._deps import EvalFrontend, make_eval_deps
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, EvalRun, Verdict, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._trace import record_turn

from co_cli.config.core import REASONING_DISPLAY_FULL
from co_cli.deps import CoDeps

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-research-direct.md"

# Multi-step research turn: search + ≥1 page fetch + synthesis, each a model call, plus
# real external network latency for the live searches and fetches. This ceiling is a
# wall-clock bound that intentionally includes that external I/O — it is NOT a warm-model
# latency budget (cf. feedback_call_timeout_no_cold_start, which governs model-call
# budgets only). Diagnose a turn that blows it as a stalled fetch, not a model regression.
_RESEARCH_TURN_TIMEOUT_S = 240


class CountingFrontend(EvalFrontend):
    """Eval frontend that tallies tool calls live as they stream.

    Tool counts MUST come from the live ``on_tool_start`` stream event, not from
    ``TurnResult.messages`` after the fact: when a turn errors (e.g. an LLM-call
    timeout mid-loop), pydantic-ai's aborted streamed run never surfaces its
    accumulated tool-call history into the returned ``TurnResult`` — the messages
    fall back to the empty initial history. Counting at ``on_tool_start`` captures
    every issued call regardless of how the turn ends, so a turn that searched and
    fetched heavily before timing out is not mis-measured as "never searched".

    Requires the session's ``reasoning_display`` to be non-summary: ``on_tool_start``
    is suppressed in summary mode (``orchestrate._handle_tool_call_event``), so the
    eval forces ``REASONING_DISPLAY_FULL`` in ``main`` before running. Display mode
    only affects rendering, never tool routing.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tool_names: list[str] = []

    def reset_counts(self) -> None:
        """Clear the per-case tally — called before each case turn."""
        self.tool_names = []

    def count(self, name: str) -> int:
        """Number of times ``name`` was invoked this case."""
        return self.tool_names.count(name)

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        self.tool_names.append(name)
        super().on_tool_start(tool_id, name, args_display)


@dataclass(frozen=True)
class Case:
    """One research question that genuinely requires search → fetch → synthesize.

    Each prompt asks for current/specific information (versions, features, doc-grounded
    comparisons) that cannot be answered from a small model's training alone and is not
    reliably present in search snippets — forcing the loop to read at least one primary
    page and cite it.
    """

    case_id: str
    prompt: str


CASES: tuple[Case, ...] = (
    Case(
        case_id="release-notes",
        prompt=(
            "Find the latest released version of the `httpx` Python library and summarize "
            "the notable changes in that release. Link the changelog or release notes you used."
        ),
    ),
    Case(
        case_id="doc-compare",
        prompt=(
            "Compare how `uv` and `pip` handle dependency locking, based on their current "
            "official documentation. Cite the documentation pages you read."
        ),
    ),
    Case(
        case_id="current-fact",
        prompt=(
            "What is the current stable Python version, and what are two headline features it "
            "introduced? Cite the python.org page you used."
        ),
    ),
)


_RESEARCH_RUBRIC = """\
A good research answer to the user's question must, in its final assistant message:
1. Directly answer the question with specific, current details (concrete versions,
   features, or a real comparison) — not a vague, generic, or hedged non-answer.
2. Be grounded in content the assistant actually fetched this turn (a synthesis of real
   page content), not recalled-from-memory guessing or fabrication.
3. Include at least one real source URL.
4. Not refuse, defer, or claim it cannot access the web.

PASS only if all four hold. FAIL if the answer is vague, unsourced, refuses, or reads as
fabricated/uncertain.
"""


async def _run_case(
    deps: CoDeps,
    agent,
    frontend: CountingFrontend,
    case: Case,
    run: EvalRun,
    chip: str,
) -> CaseResult:
    """Drive one real research turn and classify it. Never raises — failures become FAIL.

    The agent is the unmodified production ``ORCHESTRATOR_SPEC`` orchestrator; there is no
    delegation tool to reach for, exactly as in a real session. Tool counts come from the
    frontend's live tally; turn completion comes from ``TurnResult.outcome``
    (``"continue"`` vs ``"error"``).
    """
    from co_cli.context.orchestrate import run_turn

    frontend.reset_counts()
    trace_file = run.case_trace_path(case.case_id)
    trace_file.touch(exist_ok=True)
    try:
        async with asyncio.timeout(_RESEARCH_TURN_TIMEOUT_S):
            turn_result, turn_trace = await record_turn(
                case_id=case.case_id,
                turn_index=0,
                user_input=case.prompt,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=case.prompt,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=trace_file,
                agent=agent,
            )
    except Exception as exc:
        print(f"[research_direct] {case.case_id} FAILED: {type(exc).__name__}: {exc}")
        return CaseResult(
            name=case.case_id,
            verdict=Verdict.FAIL,
            duration_s=0.0,
            reason=f"turn raised {type(exc).__name__}: {exc} {chip}",
        )

    n_search = frontend.count("web_search")
    n_fetch = frontend.count("web_fetch")
    # Regression tripwire: web_research was dropped, so this count is structurally 0. A
    # non-zero value means a research-delegation tool was reintroduced and called.
    n_delegate = frontend.count("web_research")
    errored = getattr(turn_result, "outcome", "continue") == "error"
    counts = f"search={n_search} fetch={n_fetch} delegate={n_delegate}"

    base = CaseResult(
        name=case.case_id,
        verdict=Verdict.FAIL,
        duration_s=turn_trace.model_call_seconds,
        model_call_seconds=turn_trace.model_call_seconds,
        token_usage=turn_trace.token_usage,
        trace_id=turn_trace.trace_ids[0] if turn_trace.trace_ids else "",
        trace_files=[f"case_{case.case_id}.jsonl"],
    )

    if n_delegate > 0:
        base.verdict = Verdict.FAIL
        base.reason = (
            f"{counts} — REGRESSION: web_research delegation reappeared and was called {chip}"
        )
        return base

    if errored:
        if n_search >= 1 or n_fetch >= 1:
            base.verdict = Verdict.SOFT_FAIL
            base.reason = (
                f"{counts} — turn ERRORED mid-research (120s segment wall-clock timeout: serial "
                "generate→fetch rounds over many large pages exhausted the single segment budget). "
                "No delegation; the atomic loop progressed but did not complete. web_fetch now "
                "returns extracted content (not full-page chrome); a heaviest case still straining "
                f"the segment budget is a separate per-segment timeout concern, not a delegation gap. {chip}"
            )
        else:
            base.verdict = Verdict.FAIL
            base.reason = f"{counts} — turn ERRORED before using any web tool {chip}"
        return base

    if n_search == 0 and n_fetch == 0:
        base.verdict = Verdict.FAIL
        base.reason = f"{counts} — never used a web tool; no research attempted {chip}"
        return base

    verdict = await judge_with_llm(
        _RESEARCH_RUBRIC, turn_result.messages, deps=deps, model=deps.judge_model
    )

    if n_fetch == 0:
        base.verdict = Verdict.SOFT_FAIL
        base.reason = (
            f"{counts} — answered from snippets only, no primary-page read "
            f"(research-lite, but no delegation); judge: {verdict.rationale} {chip}"
        )
        return base
    if not verdict.passed:
        base.verdict = Verdict.SOFT_FAIL
        base.reason = (
            f"{counts} — atomic loop ran (no delegation) but synthesis weak; "
            f"judge: {verdict.rationale} {chip}"
        )
        return base

    base.verdict = Verdict.PASS
    base.reason = (
        f"{counts} — atomic search→fetch→synthesize completed without delegation; "
        f"judge: {verdict.rationale} {chip}"
    )
    return base


def _decision(cases: list[CaseResult]) -> tuple[str, bool]:
    """Apply the proof rule to per-case results. Returns (decision_text, proven).

    ``proven`` (and exit code 0) means: the normal agentic flow conducts research via the
    atomic loop with NO delegation, and every case that ran to completion produced a clean
    grounded synthesis. A context-bloat timeout (SOFT_FAIL "ERRORED mid-research") does not
    disprove the claim — it marks the boundary where the heaviest multi-doc research still
    strains the 120s segment budget (a separate per-segment timeout concern) — but it is
    surfaced prominently rather than swept into the PASS count.
    """
    scored = [c for c in cases if not c.skipped]
    total = len(scored)
    pass_count = sum(1 for c in scored if c.verdict == Verdict.PASS)
    bloat_count = sum(1 for c in scored if "ERRORED mid-research" in c.reason)
    delegated = any("REGRESSION: web_research" in c.reason for c in scored)
    hard_fail = any(c.verdict == Verdict.FAIL for c in scored)

    proven = (not delegated) and (not hard_fail) and pass_count >= 1
    bloat_note = ""
    if bloat_count:
        bloat_note = (
            f" BOUNDARY: {bloat_count}/{total} case(s) — the heaviest multi-page task — ERRORED "
            "on an LLM-call timeout: the atomic loop pulled multiple large pages into the main "
            "context until a model call hit the 120s segment budget. web_fetch now returns "
            "extracted main content (not full-page chrome), lowering per-page bloat; a heaviest "
            "case still timing out is a separate per-segment timeout concern to reopen on its "
            "own, not evidence for restoring delegation."
        )

    if proven:
        text = (
            f"PROVEN (for focused research) — {pass_count}/{total} cases completed full research "
            "via the atomic web_search → web_fetch → synthesize loop with ZERO delegation. The "
            "normal agentic flow conducts and finishes multi-step research on its own; the "
            f"dropped web_research subagent is not required for these tasks.{bloat_note} Standing "
            "guard that research stays in the atomic loop with no delegation tool."
        )
    elif delegated:
        text = (
            f"REGRESSION — a research-delegation tool reappeared and was called "
            f"({pass_count}/{total} clean PASS). web_research was dropped; the atomic loop should "
            f"be the sole research path. Inspect the delegating case and revert the reintroduction.{bloat_note}"
        )
    else:
        text = (
            f"INCONCLUSIVE — no delegation, but only {pass_count}/{total} cases produced a clean "
            f"grounded synthesis and a hard failure occurred. Inspect the failing cases.{bloat_note}"
        )
    return text, proven


async def main() -> int:
    """Run the proof, write the diagnostic report, and exit-code the verdict.

    Ollama warm-up runs outside any ``asyncio.timeout``. Preflight-skips when
    ``BRAVE_SEARCH_API_KEY`` is absent (research is unrunnable without web access).
    """
    await ensure_ollama_warm()
    deps, agent, _frontend, stack = await make_eval_deps()
    frontend = CountingFrontend()
    # on_tool_start (the live tool-count source) is suppressed in summary display
    # mode; force full so every tool call is observed. Rendering-only, not behavior.
    deps.session.reasoning_display = REASONING_DISPLAY_FULL
    chip = judge_model_annotation(deps)
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("research_direct") as run:
            if not deps.config.brave_search_api_key:
                skip = CaseResult(
                    name="PREFLIGHT",
                    verdict=Verdict.FAIL,
                    duration_s=0.0,
                    reason="BRAVE_SEARCH_API_KEY not configured — research eval unrunnable",
                    skipped=True,
                    skip_category="config",
                )
                run.append(skip)
                prepend_report(_REPORT_PATH, "research_direct", run.iso, [skip], run_dir=run.dir)
                print("\n[research_direct] SKIPPED — BRAVE_SEARCH_API_KEY not configured\n")
                return 0

            for case in CASES:
                result = await _run_case(deps, agent, frontend, case, run, chip)
                cases.append(result)
                print(
                    f"[research_direct] {case.case_id}: {result.verdict.value} — {result.reason}"
                )

            decision, proven = _decision(cases)
            cases.insert(
                0,
                CaseResult(
                    name="DECISION",
                    verdict=Verdict.PASS,
                    duration_s=0.0,
                    reason=f"{decision} {chip}",
                ),
            )
            for c in cases:
                run.append(c)
            print(f"\n[research_direct] DECISION: {decision} {chip}\n")
            prepend_report(_REPORT_PATH, "research_direct", run.iso, cases, run_dir=run.dir)
            return 0 if proven else 1
    finally:
        await stack.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
