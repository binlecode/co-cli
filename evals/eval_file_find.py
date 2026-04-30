#!/usr/bin/env python3
"""Eval: file_find LLM tool call correctness.

Validates that the LLM constructs correct file_find calls for common
file discovery tasks.  Each case targets a specific hypothesis about
the tool's docstring and argument design.

Five cases:

  H1: file_find_over_shell
      "List all Python files" → model calls file_find, not shell alone

  H2: recursive_pattern_used
      "Find all Python files in this project" → model uses ** in pattern

  H3: scoped_to_subdirectory
      "List Python files inside the tests directory" → model scopes
      path or pattern to tests/

  H4: e2e_fixture_files_found
      Known fixture workspace → model calls file_find with a recursive
      pattern; all fixture .py filenames appear in the tool return value

  H5: file_search_not_file_find_for_content
      "Find files containing 'utilities'" → model calls file_search,
      not file_find (validates the "When NOT to use" guidance)

Usage:
    uv run python evals/eval_file_find.py
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._frontend import SilentFrontend
from evals._observability import init_eval_observability
from evals._tools import extract_tool_calls, tool_names
from pydantic_ai.messages import ToolReturnPart

from co_cli.agent.core import build_agent, build_tool_registry
from co_cli.config.core import settings
from co_cli.context.orchestrate import run_turn

# ---------------------------------------------------------------------------
# Fixture workspace
# ---------------------------------------------------------------------------

_FIXTURE_FILES: dict[str, str] = {
    "src/agent.py": "# agent module\n",
    "src/utils.py": "# utilities\n",
    "tests/test_agent.py": "# tests for agent\n",
    "README.md": "# Project\n",
}

_FIXTURE_PY_NAMES = {"agent.py", "utils.py", "test_agent.py"}


def _write_fixture(root: Path) -> None:
    for rel, content in _FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


# ---------------------------------------------------------------------------
# Shared agent / deps factory
# ---------------------------------------------------------------------------


def _build(workspace: Path) -> tuple[Any, Any]:
    config = settings.model_copy(update={"mcp_servers": []})
    reg = build_tool_registry(config)
    agent = build_agent(config=config, tool_registry=reg)
    deps = make_eval_deps()
    deps.tool_index = reg.tool_index
    deps.tool_registry = reg
    deps.workspace_root = workspace
    return agent, deps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_find_args(messages: list[Any]) -> list[dict[str, Any]]:
    """Return args dicts for every file_find call in the message history."""
    return [args for name, args in extract_tool_calls(messages) if name == "file_find"]


def _tool_return_texts(messages: list[Any], tool_name: str) -> list[str]:
    """Collect all return-value strings for a named tool."""
    texts: list[str] = []
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == tool_name:
                texts.append(str(part.content))
    return texts


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def run_file_find_over_shell() -> dict[str, Any]:
    """Model should call file_find (not shell alone) for a file listing task."""
    case_id = "file_find_over_shell"
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_fixture(root)
        agent, deps = _build(root)

        result = await run_turn(
            agent=agent,
            user_input="List all Python files in this workspace.",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    calls = tool_names(result.messages)
    has_file_find = "file_find" in calls
    shell_only = "shell" in calls and not has_file_find

    passed = has_file_find and not shell_only
    return _result(
        case_id,
        passed,
        f"has_file_find={has_file_find} shell_only={shell_only}" if not passed else None,
        calls,
        t0,
    )


async def run_recursive_pattern_used() -> dict[str, Any]:
    """Model should use a ** pattern when asked to find files across subdirectories."""
    case_id = "recursive_pattern_used"
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_fixture(root)
        agent, deps = _build(root)

        result = await run_turn(
            agent=agent,
            user_input="Find all Python files in this project, including inside subdirectories.",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    calls = tool_names(result.messages)
    ff_args = _file_find_args(result.messages)
    has_file_find = bool(ff_args)
    has_recursive = any("**" in str(a.get("pattern", "")) for a in ff_args)

    passed = has_file_find and has_recursive
    return _result(
        case_id,
        passed,
        f"has_file_find={has_file_find} has_recursive={has_recursive} "
        f"patterns={[a.get('pattern') for a in ff_args]}"
        if not passed
        else None,
        calls,
        t0,
    )


async def run_scoped_to_subdirectory() -> dict[str, Any]:
    """Model should scope path or pattern to tests/ when asked for files there."""
    case_id = "scoped_to_subdirectory"
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_fixture(root)
        agent, deps = _build(root)

        result = await run_turn(
            agent=agent,
            user_input="List only the Python files inside the tests directory.",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    calls = tool_names(result.messages)
    ff_args = _file_find_args(result.messages)
    has_file_find = bool(ff_args)
    scoped = any(
        "tests" in str(a.get("path", "")) or "tests" in str(a.get("pattern", "")) for a in ff_args
    )

    passed = has_file_find and scoped
    return _result(
        case_id,
        passed,
        f"has_file_find={has_file_find} scoped={scoped} args={ff_args}" if not passed else None,
        calls,
        t0,
    )


async def run_e2e_fixture_files_found() -> dict[str, Any]:
    """Correct args yield correct file list: all fixture .py names in tool return."""
    case_id = "e2e_fixture_files_found"
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_fixture(root)
        agent, deps = _build(root)

        result = await run_turn(
            agent=agent,
            user_input="List all Python files in this workspace.",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    calls = tool_names(result.messages)
    ff_args = _file_find_args(result.messages)
    has_file_find = bool(ff_args)
    has_recursive = any("**" in str(a.get("pattern", "")) for a in ff_args)

    returns = _tool_return_texts(result.messages, "file_find")
    combined = "\n".join(returns)
    missing = [name for name in _FIXTURE_PY_NAMES if name not in combined]

    passed = has_file_find and has_recursive and not missing
    return _result(
        case_id,
        passed,
        f"has_recursive={has_recursive} missing_files={missing} "
        f"patterns={[a.get('pattern') for a in ff_args]}"
        if not passed
        else None,
        calls,
        t0,
    )


async def run_file_search_not_file_find_for_content() -> dict[str, Any]:
    """Model should call file_search (not file_find) when asked to search file contents."""
    case_id = "file_search_not_file_find_for_content"
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_fixture(root)
        agent, deps = _build(root)

        result = await run_turn(
            agent=agent,
            user_input="Find all files in this workspace that contain the text 'utilities'.",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    calls = tool_names(result.messages)
    has_file_search = "file_search" in calls
    file_find_without_search = "file_find" in calls and not has_file_search

    passed = has_file_search and not file_find_without_search
    return _result(
        case_id,
        passed,
        f"has_file_search={has_file_search} file_find_without_search={file_find_without_search}"
        if not passed
        else None,
        calls,
        t0,
    )


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


def _result(
    case_id: str,
    passed: bool,
    failure: str | None,
    calls: list[str],
    t0: float,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "verdict": "PASS" if passed else "FAIL",
        "failure": failure,
        "tool_calls": calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    init_eval_observability()
    print("=" * 60)
    print("  Eval: file_find LLM tool call correctness")
    print(f"  Provider: {settings.llm.provider}")
    print(f"  Model: {settings.llm.model or 'default'}")
    print("=" * 60)

    cases = [
        ("file_find_over_shell", run_file_find_over_shell),
        ("recursive_pattern_used", run_recursive_pattern_used),
        ("scoped_to_subdirectory", run_scoped_to_subdirectory),
        ("e2e_fixture_files_found", run_e2e_fixture_files_found),
        ("file_search_not_file_find_for_content", run_file_search_not_file_find_for_content),
    ]

    all_results: list[dict[str, Any]] = []
    t0 = time.monotonic()

    for case_id, runner in cases:
        print(f"\n  [{case_id}]", end=" ", flush=True)
        try:
            result = await runner()
        except Exception as exc:
            result = {
                "id": case_id,
                "verdict": "ERROR",
                "failure": str(exc),
                "tool_calls": [],
                "duration_ms": 0,
            }

        all_results.append(result)
        tool_summary = ", ".join(result["tool_calls"]) if result["tool_calls"] else "(none)"
        print(f"tool_calls: {tool_summary} → {result['verdict']} ({result['duration_ms']:.0f}ms)")
        if result.get("failure"):
            print(f"    failure: {result['failure']}")
        if result["verdict"] != "PASS":
            print("\n  [FAIL-FAST] stopping after first non-pass")
            break

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for r in all_results if r["verdict"] == "PASS")
    overall = "PASS" if passed == len(all_results) else "FAIL"

    print(f"\n{'=' * 60}")
    print(f"  Verdict: {overall} ({passed}/{len(all_results)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 60}")

    return 0 if passed == len(all_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
