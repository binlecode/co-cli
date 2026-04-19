#!/usr/bin/env python3
"""Eval: file tool surface — glob/grep naming and fuzzy patch.

Validates two behavioral hypotheses from the hardening2 delivery:

  H1 — Naming clarity: renaming list_directory→glob and find_in_files→grep
       causes the model to prefer specialist tools over shell fallback for
       file listing and content-search tasks.

  H2 — Fuzzy patch: the patch tool (replacing edit_file) is preferred over
       shell sed/awk for targeted edits and succeeds end-to-end.

Five cases:

  H1:
    glob_over_shell      — file listing task → model calls glob, not shell alone
    grep_over_shell      — content search task → model calls grep, not shell alone
    shell_git_negative   — git task → shell called directly, search_tools NOT searched first

  H2:
    patch_over_shell     — targeted line edit → model calls patch, not shell
    patch_e2e_verified   — model reads file, patches it, file content confirmed changed

Runs against any configured provider.

Usage:
    uv run python evals/eval_file_tools.py
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
from evals._tools import tool_names

from co_cli.agent._core import build_agent, build_tool_registry
from co_cli.config._core import settings
from co_cli.context.orchestrate import run_turn

# ---------------------------------------------------------------------------
# Agent / deps factory (MCP disabled)
# ---------------------------------------------------------------------------


def _build_eval_agent_and_deps() -> tuple[Any, Any]:
    """Build agent and deps with MCP servers disabled to prevent connector noise."""
    config = settings.model_copy(update={"mcp_servers": []})
    reg = build_tool_registry(config)
    agent = build_agent(config=config, tool_registry=reg)
    deps = make_eval_deps()
    deps.tool_index = reg.tool_index
    deps.tool_registry = reg
    return agent, deps


# ---------------------------------------------------------------------------
# H1 cases — naming clarity
# ---------------------------------------------------------------------------


async def run_glob_over_shell() -> dict[str, Any]:
    """Model should call glob (not shell alone) to list Python files."""
    case_id = "glob_over_shell"
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()
    prompt = "List all Python files in the co_cli directory."

    result = await run_turn(
        agent=agent,
        user_input=prompt,
        deps=deps,
        message_history=[],
        frontend=SilentFrontend(),
    )

    calls = tool_names(result.messages)
    has_glob = "glob" in calls
    shell_without_glob = "shell" in calls and not has_glob

    passed = has_glob and not shell_without_glob
    verdict = "PASS" if passed else "FAIL"
    failure = None if passed else f"has_glob={has_glob} shell_without_glob={shell_without_glob}"

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


async def run_grep_over_shell() -> dict[str, Any]:
    """Model should call grep (not shell alone) to search file contents."""
    case_id = "grep_over_shell"
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()
    prompt = "Find all files in the project that contain the text 'CoDeps'."

    result = await run_turn(
        agent=agent,
        user_input=prompt,
        deps=deps,
        message_history=[],
        frontend=SilentFrontend(),
    )

    calls = tool_names(result.messages)
    has_grep = "grep" in calls
    shell_without_grep = "shell" in calls and not has_grep

    passed = has_grep and not shell_without_grep
    verdict = "PASS" if passed else "FAIL"
    failure = None if passed else f"has_grep={has_grep} shell_without_grep={shell_without_grep}"

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


async def run_shell_git_negative() -> dict[str, Any]:
    """shell should be called directly for git — search_tools must NOT precede it."""
    case_id = "shell_git_negative"
    t0 = time.monotonic()

    agent, deps = _build_eval_agent_and_deps()
    prompt = "Run git log --oneline -5 and show me the last 5 commits."

    result = await run_turn(
        agent=agent,
        user_input=prompt,
        deps=deps,
        message_history=[],
        frontend=SilentFrontend(),
    )

    calls = tool_names(result.messages)
    has_shell = "shell" in calls
    search_before_shell = (
        "search_tools" in calls
        and has_shell
        and calls.index("search_tools") < calls.index("shell")
    )

    passed = has_shell and not search_before_shell
    verdict = "PASS" if passed else "FAIL"
    failure = (
        None if passed else f"has_shell={has_shell} search_before_shell={search_before_shell}"
    )

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


# ---------------------------------------------------------------------------
# H2 cases — fuzzy patch
# ---------------------------------------------------------------------------

_FIXTURE_CONTENT = """\
def greet(name: str) -> str:
    greeting = "Hello"
    return f"{greeting}, {name}!"
"""


async def run_patch_over_shell() -> dict[str, Any]:
    """Model should use a native file tool (patch or write_file) to edit — not shell alone."""
    case_id = "patch_over_shell"
    t0 = time.monotonic()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "greet.py").write_text(_FIXTURE_CONTENT, encoding="utf-8")

            agent, deps = _build_eval_agent_and_deps()
            deps.workspace_root = tmp_path

            # Provide file content inline so the model skips exploration and calls patch directly.
            prompt = (
                f"greet.py currently contains:\n```\n{_FIXTURE_CONTENT}```\n"
                "Use the patch tool to change 'greeting = \"Hello\"' to 'greeting = \"Hi\"' in greet.py."
            )

            result = await run_turn(
                agent=agent,
                user_input=prompt,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(),
            )
            calls = tool_names(result.messages)
            content_after = (tmp_path / "greet.py").read_text(encoding="utf-8")
    finally:
        # shell uses CWD (project root), not deps.workspace_root.
        # Remove any file the model may have written there via shell.
        Path("greet.py").unlink(missing_ok=True)

    # Patch must succeed directly — write_file appearing means patch failed its
    # precondition and the model fell back to a full rewrite.
    has_patch = "patch" in calls
    write_file_fallback = "write_file" in calls
    content_changed = 'greeting = "Hi"' in content_after

    passed = has_patch and content_changed and not write_file_fallback
    verdict = "PASS" if passed else "FAIL"
    failure = (
        None
        if passed
        else (
            f"has_patch={has_patch} content_changed={content_changed} "
            f"write_file_fallback={write_file_fallback}"
        )
    )

    return {
        "id": case_id,
        "verdict": verdict,
        "failure": failure,
        "tool_calls": calls,
        "duration_ms": (time.monotonic() - t0) * 1000,
    }


async def run_patch_e2e_verified() -> dict[str, Any]:
    """Model reads file, applies patch, file content confirmed changed on disk."""
    case_id = "patch_e2e_verified"
    t0 = time.monotonic()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            fixture = tmp_path / "greet.py"
            fixture.write_text(_FIXTURE_CONTENT, encoding="utf-8")

            agent, deps = _build_eval_agent_and_deps()
            deps.workspace_root = tmp_path

            # Provide file content inline to reduce exploration; the model still needs
            # to call patch on the actual file so we can verify the on-disk change.
            prompt = (
                f"greet.py currently contains:\n```\n{_FIXTURE_CONTENT}```\n"
                "Use the patch tool to change 'greeting = \"Hello\"' to 'greeting = \"Howdy\"' "
                "in greet.py, then confirm the change was applied."
            )

            result = await run_turn(
                agent=agent,
                user_input=prompt,
                deps=deps,
                message_history=[],
                frontend=SilentFrontend(),
            )

            calls = tool_names(result.messages)
            content_after = fixture.read_text(encoding="utf-8")
    finally:
        Path("greet.py").unlink(missing_ok=True)

    has_patch = "patch" in calls
    write_file_fallback = "write_file" in calls
    content_changed = 'greeting = "Howdy"' in content_after

    # Patch must succeed directly — write_file appearing means patch failed its
    # precondition and the model fell back to a full rewrite.
    passed = has_patch and content_changed and not write_file_fallback
    verdict = "PASS" if passed else "FAIL"
    failure = (
        None
        if passed
        else (
            f"has_patch={has_patch} content_changed={content_changed} "
            f"write_file_fallback={write_file_fallback} "
            f"content_after={content_after!r}"
        )
    )

    return {
        "id": case_id,
        "verdict": verdict,
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
    print("  Eval: File Tool Surface (glob/grep naming + fuzzy patch)")
    print(f"  Provider: {settings.llm.provider}")
    print(f"  Model: {settings.llm.model or 'default'}")
    print("=" * 60)

    cases = [
        ("glob_over_shell", run_glob_over_shell),
        ("grep_over_shell", run_grep_over_shell),
        ("shell_git_negative", run_shell_git_negative),
        ("patch_over_shell", run_patch_over_shell),
        ("patch_e2e_verified", run_patch_e2e_verified),
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
