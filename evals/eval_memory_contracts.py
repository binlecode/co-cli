#!/usr/bin/env python3
"""Eval: memory behavioral contracts — write-read cycle, session artifact exclusion, always-on.

Targets the critical flow logic introduced by the memory module refactor
(docs/exec-plans/active/2026-04-13-140000-memory-module-refactor.md):

  render_memory_file  — canonical write path shared by save_insight, update_memory,
                        append_memory. If broken → load_memories silently drops every
                        written file → system loses all memory with no error raised.

  exclude_session_summaries — deduplicated filter applied in _recall_for_context
                        AND search_memories. Both callsites must exclude artifacts.

Three phases:
  Phase 1 — Write-read-recall cycle (render_memory_file → load_memories round-trip)
  Phase 2 — exclude_session_summaries in both callsites (recall + search)
  Phase 3 — Always-on instruction injection (cap of 5 enforced)

Writes: docs/REPORT-eval-memory-contracts.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_contracts.py
"""

import asyncio
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from evals._deps import make_eval_deps, make_eval_settings
from evals._fixtures import seed_memory
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelRequest, SystemPromptPart, ToolReturnPart, UserPromptPart

from co_cli._model_factory import build_model
from co_cli.config._core import settings
from co_cli.memory._extractor import drain_pending_extraction, fire_and_forget_extraction
from co_cli.memory.recall import load_memories

_LLM_MODEL = build_model(settings.llm)
_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-memory-contracts.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deps(memory_dir: Path) -> Any:
    from co_cli.deps import CoDeps, CoSessionState
    from co_cli.tools.shell_backend import ShellBackend

    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=settings,
        memory_dir=memory_dir,
        session=CoSessionState(),
    )


def _seed_session_artifact(memory_dir: Path, content: str) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    fm: dict[str, Any] = {
        "id": "artifact-001",
        "created": datetime.now(UTC).isoformat(),
        "kind": "memory",
        "artifact_type": "session_summary",
        "tags": [],
    }
    text = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / "artifact-session-summary.md"
    path.write_text(text, encoding="utf-8")
    return path


def _seed_always_on(memory_dir: Path, mid: int, content: str) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    fm: dict[str, Any] = {
        "id": mid,
        "created": datetime.now(UTC).isoformat(),
        "kind": "memory",
        "always_on": True,
        "tags": [],
    }
    text = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / f"{mid:03d}-always-on.md"
    path.write_text(text, encoding="utf-8")
    return path


def _find_system_prompt(messages: list[Any], marker: str) -> str | None:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart) and marker in part.content:
                    return part.content
    return None


def _find_tool_return(messages: list[Any], tool_name: str) -> str | None:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == tool_name:
                    content = part.content
                    return content if isinstance(content, str) else str(content)
    return None


def _system_prompt_preview(messages: list[Any]) -> str:
    """Concatenate all SystemPromptPart content received by the model."""
    parts = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    parts.append(part.content[:200])
    return " | ".join(parts)[:400] if parts else "(none)"


async def _run_turn(prompt: str) -> list[Any]:
    from co_cli.agent._core import build_agent
    from co_cli.context.orchestrate import run_turn

    agent = build_agent(config=settings)
    deps = make_eval_deps()
    async with asyncio.timeout(EVAL_TURN_TIMEOUT_SECS):
        result = await run_turn(
            agent=agent,
            user_input=prompt,
            deps=deps,
            message_history=[],
            model_settings=make_eval_settings(),
            frontend=SilentFrontend(),
        )
    return result.messages


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(cases: list[dict[str, Any]], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    provider = settings.llm.provider
    model = settings.llm.model or "default"
    passed = sum(1 for c in cases if c["verdict"] in ("PASS", "SOFT PASS"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {provider} / {model}  ",
        f"**Total runtime:** {total_ms:.0f}ms  ",
        f"**Result:** {passed}/{len(cases)} passed",
        "",
        "### Summary",
        "",
        "| Case | Verdict | Duration |",
        "|------|---------|----------|",
    ]
    for case in cases:
        lines.append(f"| `{case['id']}` | {case['verdict']} | {case['duration_ms']:.0f}ms |")

    lines += ["", "### Step Traces", ""]
    for case in cases:
        lines.append(f"#### `{case['id']}` — {case['verdict']}")
        for step in case["steps"]:
            lines.append(f"- **{step['name']}** ({step['ms']:.0f}ms): {step['detail']}")
        if case.get("failure"):
            lines.append(f"- **Failure:** {case['failure']}")
        if case.get("system_prompt_preview"):
            lines.append(f"- **System prompt received:** `{case['system_prompt_preview']}`")
        lines.append("")

    lines += ["---", ""]
    section = "\n".join(lines)

    if _REPORT_PATH.exists():
        existing = _REPORT_PATH.read_text(encoding="utf-8")
        split = existing.split("\n", 2)
        updated = split[0] + "\n\n" + section + ("\n".join(split[1:]) if len(split) > 1 else "")
    else:
        updated = "# Eval Report: Memory Contracts\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Phase 1 — Write-read-recall cycle
# ---------------------------------------------------------------------------


async def run_write_read_cycle(tmp_dir: Path) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()

    memory_dir = tmp_dir / "write-read" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    deps = _make_deps(memory_dir=memory_dir)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="don't use trailing comments in code")])
    ]

    t = time.monotonic()
    fire_and_forget_extraction(messages, deps=deps, frontend=SilentFrontend(), cursor_start=0)
    launch_ms = (time.monotonic() - t) * 1000
    steps.append(
        {
            "name": "fire_and_forget_extraction launch",
            "ms": launch_ms,
            "detail": f"launch_ms={launch_ms:.1f} ({'non-blocking' if launch_ms < 100 else 'BLOCKED'})",
        }
    )

    t = time.monotonic()
    await drain_pending_extraction(timeout_ms=30_000)
    steps.append(
        {
            "name": "drain_pending_extraction",
            "ms": (time.monotonic() - t) * 1000,
            "detail": "drained",
        }
    )

    t = time.monotonic()
    files_written = len(list(memory_dir.glob("*.md")))
    entries_loaded = len(load_memories(memory_dir))
    steps.append(
        {
            "name": "load_memories",
            "ms": (time.monotonic() - t) * 1000,
            "detail": f"files_written={files_written} entries_loaded={entries_loaded}",
        }
    )

    parse_ok = files_written > 0 and entries_loaded == files_written
    if launch_ms >= 100:
        verdict, failure = "FAIL", f"launch blocked ({launch_ms:.0f}ms)"
    elif files_written == 0:
        verdict, failure = "FAIL", "extractor wrote no file"
    elif not parse_ok:
        verdict, failure = (
            "FAIL",
            f"files={files_written} entries_loaded={entries_loaded} — parse round-trip failed",
        )
    else:
        verdict, failure = "PASS", None

    return {
        "id": "write-read",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "system_prompt_preview": None,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_write_read_recall(tmp_dir: Path) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sys_preview: str = "(none)"

    memory_dir = tmp_dir / "write-recall" / "memory"
    t = time.monotonic()
    seed_memory(
        memory_dir, 1, "User prefers pytest for all testing", days_ago=1, tags=["preference"]
    )
    steps.append(
        {
            "name": "seed_memory",
            "ms": (time.monotonic() - t) * 1000,
            "detail": "1 memory: 'User prefers pytest for all testing' [preference]",
        }
    )

    injection: str | None = None
    with tempfile.TemporaryDirectory() as rundir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(rundir)
            run_memory_dir = Path(rundir) / ".co-cli" / "memory"
            run_memory_dir.mkdir(parents=True)
            for f in memory_dir.glob("*.md"):
                (run_memory_dir / f.name).write_bytes(f.read_bytes())

            t = time.monotonic()
            msgs = await _run_turn("Set up testing for my Python project")
            sys_preview = _system_prompt_preview(msgs)
            steps.append(
                {
                    "name": "run_turn",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "prompt: 'Set up testing for my Python project'",
                }
            )

            injection = _find_system_prompt(msgs, "Relevant memories:")
            keyword_match = "pytest" in (injection or "").lower()
            steps.append(
                {
                    "name": "scan SystemPromptPart for 'Relevant memories:'",
                    "ms": 0,
                    "detail": f"injected={injection is not None} keyword_match={keyword_match}",
                }
            )
        finally:
            os.chdir(orig_cwd)

    verdict = (
        "PASS"
        if (injection is not None and keyword_match)
        else ("SOFT PASS" if injection is not None else "FAIL")
    )
    return {
        "id": "write-recall",
        "verdict": verdict,
        "failure": "no injection" if injection is None else None,
        "steps": steps,
        "system_prompt_preview": sys_preview,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Phase 2 — exclude_session_summaries — both callsites
# ---------------------------------------------------------------------------


async def run_recall_excludes_artifact() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sys_preview: str = "(none)"
    injection: str | None = None

    with tempfile.TemporaryDirectory() as rundir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(rundir)
            memory_dir = Path(rundir) / ".co-cli" / "memory"

            t = time.monotonic()
            _seed_session_artifact(memory_dir, "User prefers pytest for all testing")
            steps.append(
                {
                    "name": "seed session_summary artifact",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "artifact_type=session_summary content='User prefers pytest for all testing'",
                }
            )

            t = time.monotonic()
            msgs = await _run_turn("Set up testing for my Python project")
            sys_preview = _system_prompt_preview(msgs)
            steps.append(
                {
                    "name": "run_turn",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "prompt: 'Set up testing for my Python project'",
                }
            )

            injection = _find_system_prompt(msgs, "Relevant memories:")
            steps.append(
                {
                    "name": "scan SystemPromptPart for 'Relevant memories:'",
                    "ms": 0,
                    "detail": f"injected={injection is not None} (expected False)",
                }
            )
        finally:
            os.chdir(orig_cwd)

    verdict = "PASS" if injection is None else "FAIL"
    return {
        "id": "recall-callsite",
        "verdict": verdict,
        "failure": "artifact leaked into recall injection" if injection is not None else None,
        "steps": steps,
        "system_prompt_preview": sys_preview,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_search_excludes_artifact() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sys_preview: str = "(none)"
    tool_return: str | None = None

    with tempfile.TemporaryDirectory() as rundir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(rundir)
            memory_dir = Path(rundir) / ".co-cli" / "memory"

            t = time.monotonic()
            _seed_session_artifact(memory_dir, "User prefers pytest for all testing")
            steps.append(
                {
                    "name": "seed session_summary artifact",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "artifact_type=session_summary content='User prefers pytest for all testing'",
                }
            )

            t = time.monotonic()
            msgs = await _run_turn(
                "Search my saved memories for the keyword 'pytest' and tell me what you find."
            )
            sys_preview = _system_prompt_preview(msgs)
            steps.append(
                {
                    "name": "run_turn",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "prompt: 'Search my saved memories for pytest'",
                }
            )

            tool_return = _find_tool_return(msgs, "search_memories")
            tool_called = tool_return is not None
            no_results = tool_called and "no memor" in (tool_return or "").lower()
            steps.append(
                {
                    "name": "inspect search_memories ToolReturnPart",
                    "ms": 0,
                    "detail": f"tool_called={tool_called} no_results={no_results} "
                    f"preview={(tool_return or '')[:80]!r}",
                }
            )
        finally:
            os.chdir(orig_cwd)

    tool_called = tool_return is not None
    no_results = tool_called and "no memor" in (tool_return or "").lower()
    if not tool_called:
        verdict, failure = "SOFT PASS", None
    elif no_results:
        verdict, failure = "PASS", None
    else:
        verdict, failure = "FAIL", f"search returned artifact: {(tool_return or '')[:80]}"

    return {
        "id": "search-callsite",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "system_prompt_preview": sys_preview,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Phase 3 — Always-on injection
# ---------------------------------------------------------------------------


async def run_always_on_present() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sys_preview: str = "(none)"
    standing: str | None = None

    with tempfile.TemporaryDirectory() as rundir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(rundir)
            memory_dir = Path(rundir) / ".co-cli" / "memory"

            t = time.monotonic()
            _seed_always_on(memory_dir, 1, "User is a senior data scientist")
            steps.append(
                {
                    "name": "seed always_on memory",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "always_on=True content='User is a senior data scientist'",
                }
            )

            t = time.monotonic()
            msgs = await _run_turn("Hello")
            sys_preview = _system_prompt_preview(msgs)
            steps.append(
                {
                    "name": "run_turn",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "prompt: 'Hello'",
                }
            )

            standing = _find_system_prompt(msgs, "Standing context:")
            keyword_found = "data scientist" in (standing or "").lower()
            steps.append(
                {
                    "name": "scan SystemPromptPart for 'Standing context:'",
                    "ms": 0,
                    "detail": f"present={standing is not None} keyword_found={keyword_found}",
                }
            )
        finally:
            os.chdir(orig_cwd)

    if standing and keyword_found:
        verdict, failure = "PASS", None
    elif standing:
        verdict, failure = "SOFT PASS", None
    else:
        verdict, failure = "FAIL", "no Standing context in system prompt"

    return {
        "id": "always-on-present",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "system_prompt_preview": sys_preview,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


async def run_always_on_cap() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sys_preview: str = "(none)"
    standing: str | None = None

    with tempfile.TemporaryDirectory() as rundir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(rundir)
            memory_dir = Path(rundir) / ".co-cli" / "memory"

            t = time.monotonic()
            for mid in range(1, 9):
                _seed_always_on(memory_dir, mid, f"Always-on fact number {mid}")
            steps.append(
                {
                    "name": "seed 8 always_on memories",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "8 files written, cap should enforce ≤5",
                }
            )

            t = time.monotonic()
            msgs = await _run_turn("Hello")
            sys_preview = _system_prompt_preview(msgs)
            steps.append(
                {
                    "name": "run_turn",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": "prompt: 'Hello'",
                }
            )

            standing = _find_system_prompt(msgs, "Standing context:")
            injected_count = sum(
                1 for i in range(1, 9) if f"Always-on fact number {i}" in (standing or "")
            )
            steps.append(
                {
                    "name": "count injected always_on entries",
                    "ms": 0,
                    "detail": f"present={standing is not None} injected_count={injected_count} cap=5",
                }
            )
        finally:
            os.chdir(orig_cwd)

    if not standing:
        verdict, failure = "FAIL", "no Standing context found"
    elif injected_count <= 5:
        verdict, failure = "PASS", None
    else:
        verdict, failure = "FAIL", f"injected {injected_count} entries, cap is 5"

    return {
        "id": "always-on-cap",
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "system_prompt_preview": sys_preview,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 70)
    print("  Eval: Memory Contracts")
    print("  (write-read cycle · artifact exclusion · always-on)")
    print("=" * 70)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    PHASES = [
        (
            "Phase 1: Write-Read-Recall Cycle",
            [
                (
                    run_write_read_cycle,
                    True,
                    "[write-read] render_memory_file → load_memories round-trip",
                ),
                (
                    run_write_read_recall,
                    True,
                    "[write-recall] seeded memory recalled by inject_opening_context",
                ),
            ],
        ),
        (
            "Phase 2: exclude_session_summaries — Both Callsites",
            [
                (
                    run_recall_excludes_artifact,
                    False,
                    "[recall-callsite] inject_opening_context excludes session_summary",
                ),
                (
                    run_search_excludes_artifact,
                    False,
                    "[search-callsite] search_memories excludes session_summary",
                ),
            ],
        ),
        (
            "Phase 3: Always-On Instruction Injection",
            [
                (
                    run_always_on_present,
                    False,
                    "[always-on-present] always_on memory in Standing context",
                ),
                (run_always_on_cap, False, "[always-on-cap] 8 memories → capped at 5"),
            ],
        ),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for phase_label, cases in PHASES:
            print(f"\n  {phase_label}")
            print("  " + "-" * 50)
            for fn, needs_tmp, label in cases:
                print(f"    {label}", end=" ", flush=True)
                try:
                    case = await (fn(tmp_path) if needs_tmp else fn())
                except Exception as exc:
                    case = {
                        "id": label,
                        "verdict": "ERROR",
                        "failure": str(exc),
                        "steps": [],
                        "system_prompt_preview": None,
                        "duration_ms": 0,
                    }
                all_cases.append(case)
                print(f"{case['verdict']} ({case['duration_ms']:.0f}ms)")
                if case.get("failure"):
                    print(f"      → {case['failure']}")

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for c in all_cases if c["verdict"] in ("PASS", "SOFT PASS"))
    _write_report(all_cases, total_ms)

    print(f"\n{'=' * 70}")
    verdict = "PASS" if passed == len(all_cases) else "FAIL"
    print(f"  Verdict: {verdict} ({passed}/{len(all_cases)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 70}")
    return 0 if passed == len(all_cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
