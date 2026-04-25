#!/usr/bin/env python3
"""Eval: bootstrap flow — production startup boundary through the welcome banner.

Validates the canonical startup path described in ``docs/specs/bootstrap.md``:

  create_deps() -> build_agent() -> restore_session() -> init_memory_index()
  -> display_welcome_banner()

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

from evals._frontend import CapturingFrontend
from evals._timeouts import EVAL_BOOTSTRAP_TIMEOUT_SECS

from co_cli.agent._core import build_agent
from co_cli.bootstrap.banner import display_welcome_banner
from co_cli.bootstrap.core import create_deps, init_memory_index, restore_session
from co_cli.commands._commands import BUILTIN_COMMANDS, _build_completer_words, get_skill_registry
from co_cli.config._core import get_settings
from co_cli.deps import CoDeps, ToolSourceEnum
from co_cli.display._core import console

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-bootstrap.md"
_REPORT_HEADER = "# Eval Report: Bootstrap Flow"
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


class TrackingFrontend(CapturingFrontend):
    """Verbose eval frontend that records startup statuses."""

    def __init__(self) -> None:
        super().__init__(verbose=True)


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
    statuses: list[str],
    log_messages: list[str],
    total_ms: float,
) -> None:
    """Write the bootstrap eval report, prepending the newest compatible run."""
    config = get_settings()
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(1 for case in cases if case.verdict == "PASS")
    degraded = (
        "yes" if any(status.startswith("  Knowledge degraded") for status in statuses) else "no"
    )

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {config.llm.provider} / {config.llm.model or 'default'}  ",
        f"**Bootstrap timeout:** {EVAL_BOOTSTRAP_TIMEOUT_SECS}s for `create_deps()`  ",
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
    if statuses:
        for status in statuses:
            lines.append(f"- `{status}`")
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
    frontend: TrackingFrontend,
    stack: AsyncExitStack,
) -> tuple[EvalCaseResult, CoDeps | None]:
    """Run create_deps() and validate the assembled runtime contract."""
    case_t0 = time.monotonic()
    steps: list[EvalStep] = []
    deps: CoDeps | None = None

    try:
        step_t0 = time.monotonic()
        async with asyncio.timeout(EVAL_BOOTSTRAP_TIMEOUT_SECS):
            deps = await create_deps(frontend, stack)
        steps.append(
            EvalStep(
                name="create_deps",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=(
                    f"backend={deps.config.knowledge.search_backend} "
                    f"tools={len(deps.tool_index)} "
                    f"skills={len(deps.skill_commands)}"
                ),
            )
        )

        step_t0 = time.monotonic()
        skill_registry = get_skill_registry(deps.skill_commands)
        completer_words = _build_completer_words(deps.skill_commands)
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

        failures: list[str] = []
        if deps.model is None:
            failures.append("create_deps() returned no foreground model")
        if deps.tool_registry is None:
            failures.append("create_deps() returned no tool registry")
        if not deps.tool_index:
            failures.append("merged tool_index is empty")
        if len(completer_words) < len(BUILTIN_COMMANDS):
            failures.append("completer words dropped built-in slash commands")
        if deps.config.knowledge.search_backend == "grep" and deps.knowledge_store is not None:
            failures.append("grep backend should not keep a KnowledgeStore handle")

        if failures:
            verdict = "FAIL"
            failure = "; ".join(failures)
        else:
            verdict = "PASS"
            failure = None
    except TimeoutError:
        verdict = "FAIL"
        failure = f"create_deps() exceeded {EVAL_BOOTSTRAP_TIMEOUT_SECS}s timeout"
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


def _run_agent_case(deps: CoDeps | None) -> tuple[EvalCaseResult, object | None]:
    """Build the foreground orchestrator agent from the assembled runtime."""
    if deps is None:
        return _make_skip_case("build-agent", "Skipped because create-deps failed"), None

    case_t0 = time.monotonic()
    steps: list[EvalStep] = []

    try:
        step_t0 = time.monotonic()
        agent = build_agent(config=deps.config, model=deps.model, tool_registry=deps.tool_registry)
        steps.append(
            EvalStep(
                name="build_agent",
                ms=(time.monotonic() - step_t0) * 1000,
                detail=(
                    f"agent_type={agent.__class__.__name__} "
                    f"tool_registry={'yes' if deps.tool_registry is not None else 'no'}"
                ),
            )
        )
        verdict = "PASS"
        failure = None
    except Exception as exc:
        agent = None
        verdict = "FAIL"
        failure = f"build_agent() raised {exc.__class__.__name__}: {exc}"

    return (
        EvalCaseResult(
            case_id="build-agent",
            verdict=verdict,
            duration_ms=(time.monotonic() - case_t0) * 1000,
            steps=steps,
            failure=failure,
        ),
        agent,
    )


def _run_session_case(
    deps: CoDeps | None,
    frontend: TrackingFrontend,
) -> EvalCaseResult:
    """Run restore_session() and init_memory_index() and validate ordering/state."""
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
        init_memory_index(deps, current_session_path, frontend)
        if deps.memory_index is None:
            index_detail = "memory_index=None"
        else:
            search_results = deps.memory_index.search("bootstrap")
            index_detail = f"memory_index=ready search_results={len(search_results)}"
        steps.append(
            EvalStep(
                name="init_memory_index",
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
            deps.memory_index is None
            and _first_status_index(frontend.statuses, "Session index unavailable") is None
        ):
            failures.append("memory index degraded but no degradation status was emitted")

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
        _write_report(cases=cases, statuses=[], log_messages=[], total_ms=0.0)
        return

    frontend = TrackingFrontend()
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

            agent_case, _ = _run_agent_case(deps)
            cases.append(agent_case)

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
        statuses=frontend.statuses,
        log_messages=log_messages,
        total_ms=(time.monotonic() - total_t0) * 1000,
    )


if __name__ == "__main__":
    asyncio.run(main())
