"""UAT eval — Workflow 6: Trust and visibility controls.

Drives the user-facing slash surface for session approval-rule management
(`/approvals list|clear`) and the unknown-slash safety boundary
(`/this_is_not_a_command` must never reach the LLM). No turns are driven —
every assertion is structural against `deps.session.session_approval_rules`
and `deps.runtime.turn_usage`.

Per-case structure mirrors W1-W5: real CoDeps via `make_eval_deps()`, real
`~/.co-cli/` workspace, dispatch via the production `commands.core.dispatch`
entrypoint, JSONL run record under `evals/_outputs/`, dated section
prepended to `docs/REPORT-eval-trust-visibility.md`.

Specs: docs/specs/tui.md (slash-command reference)
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from evals._deps import EvalFrontend, make_eval_deps
from evals._observability import CaseResult, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, LocalOnly
from co_cli.deps import ApprovalKindEnum, CoDeps, SessionApprovalRule


def _make_ctx(deps: CoDeps, agent, frontend: EvalFrontend) -> CommandContext:
    """Build a CommandContext mirroring main.py:423 with empty history."""
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=agent,
        completer=None,
        frontend=frontend,
    )


async def case_w6_a_approvals_list_clear(
    deps: CoDeps,
    agent,
    frontend: EvalFrontend,
    run,
) -> CaseResult:
    """W6.A — `/approvals list` + `/approvals clear` operate on session rules.

    Clears `session_approval_rules`, inserts one known SHELL rule directly
    (production-path: the list is the storage), drives `/approvals list` and
    asserts the handler returned `LocalOnly` without crashing and the rule
    is still present. Then drives `/approvals clear` and asserts the list
    is empty. Leaves rules empty on exit — `EvalFrontend.prompt_approval`
    will re-populate as needed for downstream cases.
    """
    case_id = "W6.A"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    reason = ""
    passed = True
    try:
        deps.session.session_approval_rules.clear()
        rule = SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="echo")
        deps.session.session_approval_rules.append(rule)

        ctx = _make_ctx(deps, agent, frontend)

        list_outcome = await dispatch("/approvals list", ctx)
        if not isinstance(list_outcome, LocalOnly):
            passed = False
            reason = f"/approvals list returned {type(list_outcome).__name__}, expected LocalOnly"
        elif len(deps.session.session_approval_rules) != 1:
            passed = False
            reason = (
                f"after /approvals list, rule count = "
                f"{len(deps.session.session_approval_rules)}, expected 1"
            )
        else:
            clear_outcome = await dispatch("/approvals clear", ctx)
            if not isinstance(clear_outcome, LocalOnly):
                passed = False
                reason = (
                    f"/approvals clear returned {type(clear_outcome).__name__}, expected LocalOnly"
                )
            elif len(deps.session.session_approval_rules) != 0:
                passed = False
                reason = (
                    f"after /approvals clear, rule count = "
                    f"{len(deps.session.session_approval_rules)}, expected 0"
                )
            else:
                reason = "list+clear behaved per spec"
    finally:
        deps.session.session_approval_rules.clear()

    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=time.monotonic() - t0,
        reason=reason,
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def case_w6_b_unknown_slash_local_only(
    deps: CoDeps,
    agent,
    frontend: EvalFrontend,
    run,
) -> CaseResult:
    """W6.B — unknown slash returns LocalOnly and never burns tokens.

    Snapshots `deps.runtime.turn_usage` before and after dispatching
    `/this_is_not_a_command`. The production handler prints "Unknown
    command" to console and returns `LocalOnly()` — it must not invoke
    the LLM, so turn_usage stays unchanged.
    """
    case_id = "W6.B"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    reason = ""
    passed = True

    usage_before = deps.runtime.turn_usage
    ctx = _make_ctx(deps, agent, frontend)
    outcome = await dispatch("/this_is_not_a_command", ctx)
    usage_after = deps.runtime.turn_usage

    if not isinstance(outcome, LocalOnly):
        passed = False
        reason = f"unknown slash returned {type(outcome).__name__}, expected LocalOnly"
    elif usage_after is not usage_before:
        passed = False
        reason = (
            f"turn_usage changed (before={usage_before!r}, after={usage_after!r}) — "
            "unknown slash reached the LLM"
        )
    else:
        reason = "unknown slash short-circuited locally; turn_usage unchanged"

    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=time.monotonic() - t0,
        reason=reason,
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def main() -> int:
    """Run W6.A and W6.B against real CoDeps and emit the REPORT.

    Ollama warm-up happens outside any `asyncio.timeout` for consistency
    with the W1-W5 harness pattern, even though W6 drives no LLM calls.
    """
    await ensure_ollama_warm()
    deps, agent, frontend, stack = await make_eval_deps()
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("trust_visibility") as run:
            for fn in (
                case_w6_a_approvals_list_clear,
                case_w6_b_unknown_slash_local_only,
            ):
                try:
                    cr = await fn(deps, agent, frontend, run)
                except Exception as exc:
                    cr = CaseResult(
                        name=fn.__name__,
                        passed=False,
                        duration_s=0.0,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                cases.append(cr)
                run.append(cr)
                verdict = "PASS" if cr.passed else "FAIL"
                print(f"[trust_visibility] {cr.name}: {verdict} — {cr.reason or 'ok'}")
            prepend_report(
                Path("docs/REPORT-eval-trust-visibility.md"),
                "trust_visibility",
                run.iso,
                cases,
                run_dir=run.dir,
            )
    finally:
        await stack.aclose()
    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
