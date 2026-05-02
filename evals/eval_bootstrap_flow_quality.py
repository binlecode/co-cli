#!/usr/bin/env python3
"""Eval: bootstrap flow — production startup boundary through the welcome banner.

Validates the canonical startup path described in ``docs/specs/bootstrap.md``:

  create_deps() -> restore_session() -> init_session_index()
  -> display_welcome_banner()

Also validates that every recorded degradation in deps.degradations has a
corresponding startup status emission (degradation-signals case).

The eval uses the real configured system, records the emitted startup statuses,
checks ordering and end-state invariants at the REPL boundary, and writes a
dated Markdown report to ``docs/REPORT-eval-bootstrap.md``.

Usage:
    uv run python evals/eval_bootstrap_flow.py
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals._timeouts import EVAL_E2E_BOOTSTRAP_TIMEOUT_SECS

from co_cli.bootstrap.banner import display_welcome_banner
from co_cli.bootstrap.core import create_deps, init_session_index, restore_session
from co_cli.commands.registry import BUILTIN_COMMANDS, build_completer_words
from co_cli.commands.skills import get_skill_registry
from co_cli.config.core import get_settings
from co_cli.deps import CoDeps, ToolSourceEnum
from co_cli.display.core import console
from co_cli.display.headless import HeadlessFrontend

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-bootstrap-flow-quality.md"
_REPORT_HEADER = "# Eval Report: Bootstrap Flow Quality"
_CURRENT_SECTION_MARKER = "**Bootstrap timeout:**"


@dataclass(frozen=True)
class EvalStep:
    name: str
    ms: float
    detail: str


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    verdict: str
    duration_ms: float
    steps: list[EvalStep]
    failure: str | None = None


def _load_compatible_report_sections() -> list[str]:
    """Return prior report sections that match the current bootstrap report schema."""
    if not _REPORT_PATH.exists():
        return []

    existing = _REPORT_PATH.read_text(encoding="utf-8")
    if not existing.startswith(_REPORT_HEADER):
        return []

    split = existing.split("\n\n", 1)
    body = split[1] if len(split) > 1 else ""
    raw_sections = [section.strip() for section in body.split("\n\n---\n\n") if section.strip()]
    compatible_sections: list[str] = []
    for section in raw_sections:
        if _CURRENT_SECTION_MARKER not in section:
            continue
        if "`create-deps`" not in section or "`banner-boundary`" not in section:
            continue
        if "`mcp_state`" not in section:
            continue
        compatible_sections.append(section)
    return compatible_sections


def _first_status_index(statuses: list[str], fragment: str) -> int | None:
    """Return the first status index containing fragment, or None when absent."""
    for idx, status in enumerate(statuses):
        if fragment in status:
            return idx
    return None


def _count_mcp_tools(deps: CoDeps) -> int:
    """Count MCP tools in the merged tool index."""
    return sum(1 for tool in deps.tool_index.values() if tool.source is ToolSourceEnum.MCP)


def _capture_banner_text(deps: CoDeps) -> str:
    """Render the startup banner and return the captured console output."""
    with console.capture() as capture:
        display_welcome_banner(deps)
    return capture.get()


def _make_skip_case(case_id: str, reason: str) -> EvalCaseResult:
    """Build a skip result with a single explanatory step."""
    return EvalCaseResult(
        case_id=case_id,
        verdict="SKIP",
        duration_ms=0.0,
        steps=[EvalStep(name="precondition", ms=0.0, detail=reason)],
        failure=reason,
    )


def _write_report(
    *,
    cases: list[EvalCaseResult],
    status_timeline: list[tuple[float, str]],
    log_messages: list[str],
    total_ms: float,
) -> None:
    """Write the bootstrap eval report, prepending the newest compatible run."""
    config = get_settings()
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(1 for case in cases if case.verdict == "PASS")
    degraded = (
        "yes"
        if any(msg.startswith("  Knowledge degraded") for _, msg in status_timeline)
        else "no"
    )

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {config.llm.provider} / {config.llm.model or 'default'}  ",
        f"**Bootstrap timeout:** {EVAL_E2E_BOOTSTRAP_TIMEOUT_SECS}s for `create_deps()`  ",
        f"**Workspace:** {Path.cwd()}  ",
        f"**Total runtime:** {total_ms:.0f}ms  ",
        f"**Result:** {passed}/{len(cases)} passed  ",
        f"**Knowledge degraded:** {degraded}",
        "",
        "### Summary",
        "",
        "| Case | Verdict | Duration |",
        "|------|---------|----------|",
    ]
    for case in cases:
        lines.append(f"| `{case.case_id}` | {case.verdict} | {case.duration_ms:.0f}ms |")

    lines += ["", "### Case Details", ""]
    for case in cases:
        lines.append(f"#### `{case.case_id}` — {case.verdict}")
        for step in case.steps:
            lines.append(f"- **{step.name}** ({step.ms:.0f}ms): {step.detail}")
        if case.failure:
            lines.append(f"- **Failure:** {case.failure}")
        lines.append("")

    lines += ["### Startup Status Timeline", ""]
    if status_timeline:
        for elapsed_ms, msg in status_timeline:
            lines.append(f"- `+{elapsed_ms:.0f}ms` `{msg}`")
    else:
        lines.append("- No startup statuses captured.")

    lines += ["", "### Bootstrap Logs", ""]
    if log_messages:
        for message in log_messages:
            lines.append(f"- `{message}`")
    else:
        lines.append("- No bootstrap logs captured.")

    section = "\n".join(lines)
    prior_sections = _load_compatible_report_sections()
    all_sections = [section, *prior_sections]
    updated = _REPORT_HEADER + "\n\n" + "\n\n---\n\n".join(all_sections) + "\n"
    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report -> {_REPORT_PATH.relative_to(Path.cwd())}")


async def _run_create_deps_case(
    frontend: HeadlessFrontend,
    stack: AsyncExitStack,
) -> tuple[EvalCaseResult, CoDeps | None]:
    """Run create_deps() and validate the assembled runtime contract."""
    case_t0 = time.monotonic()
    steps: list[EvalStep] = []
    deps: CoDeps | None = None

    try:
        step_t0 = time.monotonic()
        async with asyncio.timeout(EVAL_E2E_BOOTSTRAP_TIMEOUT_SECS):
            deps = await create_deps(frontend, stack)
        steps.append(
            EvalStep(
                name="create_deps",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=(
                    f"backend={deps.config.knowledge.search_backend} "
                    f"tools={len(deps.tool_index)} "
                    f"mcp_tools={_count_mcp_tools(deps)} "
                    f"skills={len(deps.skill_commands)} "
                    f"degradations={len(deps.degradations)}"
                ),
            )
        )

        step_t0 = time.monotonic()
        skill_registry = get_skill_registry(deps.skill_commands)
        completer_words = build_completer_words(deps.skill_commands)
        steps.append(
            EvalStep(
                name="registry_state",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=(
                    f"tool_registry={'yes' if deps.tool_registry is not None else 'no'} "
                    f"mcp_tools={_count_mcp_tools(deps)} "
                    f"model_skills={len(skill_registry)} "
                    f"completer_words={len(completer_words)}"
                ),
            )
        )

        step_t0 = time.monotonic()
        mcp_configured = len(deps.config.mcp_servers or {})
        mcp_failures = {k[4:]: v for k, v in deps.degradations.items() if k.startswith("mcp.")}
        mcp_connected = mcp_configured - len(mcp_failures)
        mcp_detail = (
            f"configured={mcp_configured} connected={mcp_connected} "
            f"failed={len(mcp_failures)} tools={_count_mcp_tools(deps)}"
        )
        if mcp_failures:
            failure_list = " ".join(f"{srv}:{err}" for srv, err in mcp_failures.items())
            mcp_detail += f" failures=[{failure_list}]"
        steps.append(
            EvalStep(
                name="mcp_state",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=mcp_detail,
            )
        )

        failures: list[str] = []
        if deps.model is None:
            failures.append("create_deps() returned no foreground model")
        if deps.tool_registry is None:
            failures.append("create_deps() returned no tool registry")
        if not deps.tool_index:
            failures.append("merged tool_index is empty")
        if len(completer_words) < len(BUILTIN_COMMANDS):
            failures.append("completer words dropped built-in slash commands")
        if deps.config.knowledge.search_backend == "grep" and deps.memory_store is not None:
            failures.append("grep backend should not keep a MemoryStore handle")

        if failures:
            verdict = "FAIL"
            failure = "; ".join(failures)
        else:
            verdict = "PASS"
            failure = None
    except TimeoutError:
        verdict = "FAIL"
        failure = f"create_deps() exceeded {EVAL_E2E_BOOTSTRAP_TIMEOUT_SECS}s timeout"
    except Exception as exc:
        verdict = "FAIL"
        failure = f"create_deps() raised {exc.__class__.__name__}: {exc}"

    return (
        EvalCaseResult(
            case_id="create-deps",
            verdict=verdict,
            duration_ms=(time.monotonic() - case_t0) * 1000,
            steps=steps,
            failure=failure,
        ),
        deps if verdict == "PASS" else None,
    )


def _run_degradation_signals_case(
    deps: CoDeps | None,
    frontend: HeadlessFrontend,
) -> EvalCaseResult:
    """Verify every recorded degradation has a corresponding startup status emission."""
    if deps is None:
        return _make_skip_case("degradation-signals", "Skipped because create-deps failed")

    case_t0 = time.monotonic()
    steps: list[EvalStep] = []
    failures: list[str] = []

    step_t0 = time.monotonic()
    knowledge_degraded = "knowledge" in deps.degradations
    if knowledge_degraded and _first_status_index(frontend.statuses, "Knowledge degraded") is None:
        failures.append(
            f"knowledge degradation recorded "
            f"({deps.degradations['knowledge']!r}) but no 'Knowledge degraded' status emitted"
        )
    steps.append(
        EvalStep(
            name="knowledge_signal",
            ms=(time.monotonic() - step_t0) * 1000,
            detail=(
                f"degraded={knowledge_degraded} "
                f"entry={deps.degradations.get('knowledge', 'none')!r}"
            ),
        )
    )

    step_t0 = time.monotonic()
    mcp_failures = {k[4:]: v for k, v in deps.degradations.items() if k.startswith("mcp.")}
    for server_name in mcp_failures:
        if _first_status_index(frontend.statuses, f"MCP server {server_name!r}") is None:
            failures.append(
                f"MCP server {server_name!r} degradation recorded but no matching status emitted"
            )
    steps.append(
        EvalStep(
            name="mcp_signals",
            ms=(time.monotonic() - step_t0) * 1000,
            detail=f"mcp_failures={len(mcp_failures)} servers={list(mcp_failures) or 'none'}",
        )
    )

    step_t0 = time.monotonic()
    total = len(deps.degradations)
    steps.append(
        EvalStep(
            name="degradation_count",
            ms=(time.monotonic() - step_t0) * 1000,
            detail=f"total={total} keys={list(deps.degradations) or 'none'}",
        )
    )

    verdict = "FAIL" if failures else "PASS"
    return EvalCaseResult(
        case_id="degradation-signals",
        verdict=verdict,
        duration_ms=(time.monotonic() - case_t0) * 1000,
        steps=steps,
        failure="; ".join(failures) if failures else None,
    )


def _run_session_case(
    deps: CoDeps | None,
    frontend: HeadlessFrontend,
) -> EvalCaseResult:
    """Run restore_session() and init_session_index() and validate ordering/state."""
    if deps is None:
        return _make_skip_case("restore-session-index", "Skipped because create-deps failed")

    case_t0 = time.monotonic()
    steps: list[EvalStep] = []

    try:
        step_t0 = time.monotonic()
        current_session_path = restore_session(deps, frontend)
        restore_status = "restored" if current_session_path.exists() else "new"
        steps.append(
            EvalStep(
                name="restore_session",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=f"path={current_session_path} state={restore_status}",
            )
        )

        step_t0 = time.monotonic()
        init_session_index(deps, current_session_path, frontend)
        if deps.memory_store is None:
            index_detail = "memory_store=None"
        else:
            search_results = deps.memory_store.search("bootstrap")
            index_detail = f"memory_store=ready search_results={len(search_results)}"
        steps.append(
            EvalStep(
                name="init_session_index",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=index_detail,
            )
        )

        failures: list[str] = []
        if deps.session.session_path != current_session_path:
            failures.append("deps.session.session_path does not match restore_session() result")

        session_status_fragment = (
            "Session restored" if current_session_path.exists() else "Session new"
        )
        session_status_idx = _first_status_index(frontend.statuses, session_status_fragment)
        knowledge_status_idx = _first_status_index(frontend.statuses, "Knowledge ")

        if session_status_idx is None:
            failures.append(f"missing startup status for '{session_status_fragment}'")
        if knowledge_status_idx is None:
            failures.append("missing knowledge startup status before session restore")
        elif session_status_idx is not None and knowledge_status_idx >= session_status_idx:
            failures.append("knowledge sync/status did not occur before session restore status")
        if (
            deps.memory_store is None
            and _first_status_index(frontend.statuses, "Session index unavailable") is None
        ):
            failures.append("session index unavailable but no degradation status was emitted")

        if failures:
            verdict = "FAIL"
            failure = "; ".join(failures)
        else:
            verdict = "PASS"
            failure = None
    except Exception as exc:
        verdict = "FAIL"
        failure = f"session restore/index raised {exc.__class__.__name__}: {exc}"

    return EvalCaseResult(
        case_id="restore-session-index",
        verdict=verdict,
        duration_ms=(time.monotonic() - case_t0) * 1000,
        steps=steps,
        failure=failure,
    )


def _run_banner_case(deps: CoDeps | None) -> EvalCaseResult:
    """Render the welcome banner and validate the REPL-boundary surface."""
    if deps is None:
        return _make_skip_case("banner-boundary", "Skipped because create-deps failed")

    case_t0 = time.monotonic()
    steps: list[EvalStep] = []

    try:
        step_t0 = time.monotonic()
        banner_text = _capture_banner_text(deps)
        required_fragments = [
            "Ready",
            "Model:",
            "Knowledge:",
            "Tools:",
            Path.cwd().name,
            deps.config.llm.provider,
        ]
        if deps.config.llm.model:
            required_fragments.append(deps.config.llm.model)

        missing = [fragment for fragment in required_fragments if fragment not in banner_text]
        steps.append(
            EvalStep(
                name="display_welcome_banner",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=f"chars={len(banner_text)} missing={len(missing)}",
            )
        )

        if missing:
            verdict = "FAIL"
            failure = f"banner output missing expected fragments: {', '.join(missing)}"
        else:
            verdict = "PASS"
            failure = None
    except Exception as exc:
        verdict = "FAIL"
        failure = f"display_welcome_banner() raised {exc.__class__.__name__}: {exc}"

    return EvalCaseResult(
        case_id="banner-boundary",
        verdict=verdict,
        duration_ms=(time.monotonic() - case_t0) * 1000,
        steps=steps,
        failure=failure,
    )


async def main() -> None:
    """Run the bootstrap flow eval and write the report."""
    print("=== Bootstrap Flow Eval ===\n")

    config = get_settings()
    preflight_error = config.llm.validate_config()
    if preflight_error:
        cases = [_make_skip_case("create-deps", preflight_error)]
        _write_report(cases=cases, status_timeline=[], log_messages=[], total_ms=0.0)
        return

    frontend = HeadlessFrontend(verbose=True)
    log_messages: list[str] = []
    total_t0 = time.monotonic()
    deps: CoDeps | None = None

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = self.format(record)
            log_messages.append(message)
            print(f"    LOG: {message}")

    log_handler = ListHandler()
    log_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    bootstrap_logger = logging.getLogger("co_cli.bootstrap")
    bootstrap_logger.addHandler(log_handler)
    bootstrap_logger.setLevel(logging.INFO)

    cases: list[EvalCaseResult] = []
    try:
        async with AsyncExitStack() as stack:
            create_case, deps = await _run_create_deps_case(frontend, stack)
            cases.append(create_case)

            degradation_case = _run_degradation_signals_case(deps, frontend)
            cases.append(degradation_case)

            session_case = _run_session_case(deps, frontend)
            cases.append(session_case)

            banner_case = _run_banner_case(deps)
            cases.append(banner_case)
    finally:
        if deps is not None:
            deps.shell.cleanup()
        bootstrap_logger.removeHandler(log_handler)

    _write_report(
        cases=cases,
        status_timeline=frontend.status_timeline,
        log_messages=log_messages,
        total_ms=(time.monotonic() - total_t0) * 1000,
    )


if __name__ == "__main__":
    asyncio.run(main())
