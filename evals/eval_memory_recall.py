#!/usr/bin/env python3
"""Eval: memory recall injection — verify build_recall_injection fires per turn via FTS5 DB.

Pre-seeds memory files on disk, syncs them into a real KnowledgeStore, runs run_turn(),
and checks for SystemPromptPart injection from build_recall_injection. Recall is NOT
visible as a ToolCallPart — the eval scans for SystemPromptPart containing
"Relevant memories:".

Target flow:
    seed_memory (disk) → KnowledgeStore.sync_dir (DB index)
    → run_turn → build_recall_injection → _recall_for_context (FTS5 DB search)
    → SystemPromptPart("Relevant memories: ...")

Also tests the degraded path: when knowledge_store=None, no injection occurs and no
exception is raised.

Writes: docs/REPORT-eval-memory-recall.md (prepends dated section each run).

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_memory_recall.py
"""

import asyncio
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._fixtures import seed_memory
from evals._frontend import SilentFrontend
from evals._timeouts import EVAL_TURN_TIMEOUT_SECS as _EVAL_TURN_TIMEOUT_SECS
from pydantic_ai.messages import ModelRequest, SystemPromptPart

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.context.orchestrate import run_turn
from co_cli.knowledge._store import KnowledgeStore

_TURN_TIMEOUT_SECS = _EVAL_TURN_TIMEOUT_SECS

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-eval-memory-recall.md"


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class RecallCase:
    id: str
    memories: list[dict[str, Any]]
    prompt: str
    expect_injection: bool
    expect_keyword: str | None
    desc: str
    # When True, knowledge_store is None (degraded path test)
    degraded_path: bool = False


CASES: list[RecallCase] = [
    RecallCase(
        id="recall-topic-match",
        memories=[
            {"content": "User prefers pytest for testing", "tags": ["preference"], "days_ago": 3},
            {
                "content": "Project uses PostgreSQL for the database",
                "tags": ["decision"],
                "days_ago": 5,
            },
        ],
        prompt="Set up testing for my Python project",
        expect_injection=True,
        expect_keyword="pytest",
        desc="Topic match → FTS5 injection with keyword",
    ),
    RecallCase(
        id="recall-partial-kw",
        memories=[
            {
                "content": "User prefers vim keybindings in all editors",
                "tags": ["preference"],
                "days_ago": 2,
            },
        ],
        prompt="Configure my editor settings",
        expect_injection=True,
        expect_keyword="vim",
        desc="Keyword match → FTS5 injection",
    ),
    RecallCase(
        id="recall-no-match",
        memories=[
            {
                "content": "User prefers dark mode in all applications",
                "tags": ["preference"],
                "days_ago": 1,
            },
        ],
        prompt="Review this PR and flag any security issues",
        expect_injection=False,
        expect_keyword=None,
        desc="No keyword overlap → no FTS5 match → no injection",
    ),
    RecallCase(
        id="recall-empty-store",
        memories=[],
        prompt="Explain what async/await does in Python",
        expect_injection=False,
        expect_keyword=None,
        desc="Empty DB → no injection",
    ),
    RecallCase(
        id="recall-degraded-path",
        memories=[
            {"content": "User prefers pytest for testing", "tags": ["preference"], "days_ago": 1},
        ],
        prompt="Set up testing for my Python project",
        expect_injection=False,
        expect_keyword=None,
        desc="knowledge_store=None degraded path → no injection, no crash",
        degraded_path=True,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_injection(messages: list[Any]) -> str | None:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart) and "Relevant memories:" in part.content:
                    return part.content
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


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_case(case: RecallCase) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    case_t0 = time.monotonic()
    sys_preview: str = "(none)"
    injection: str | None = None

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            memory_dir = Path(tmpdir) / ".co-cli" / "memory"
            db_path = Path(tmpdir) / "search.db"

            t = time.monotonic()
            for idx, mem in enumerate(case.memories, 1):
                seed_memory(
                    memory_dir,
                    idx,
                    mem["content"],
                    days_ago=mem.get("days_ago", 0),
                    tags=mem.get("tags"),
                )
            seed_detail = (
                "; ".join(f"'{m['content'][:40]}' {m.get('tags', [])}" for m in case.memories)
                if case.memories
                else "empty store"
            )
            steps.append(
                {
                    "name": "seed_memory",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": f"{len(case.memories)} file(s) — {seed_detail}",
                }
            )

            if case.degraded_path:
                # No KnowledgeStore — degraded path: recall returns empty, no crash
                deps = make_eval_deps()
                ks_label = "None (degraded)"
            else:
                # Wire real KnowledgeStore with indexed content
                knowledge_store = KnowledgeStore(config=settings, knowledge_db_path=db_path)
                n_indexed = (
                    knowledge_store.sync_dir("memory", memory_dir) if memory_dir.exists() else 0
                )
                ks_label = f"KnowledgeStore({n_indexed} docs indexed)"
                deps = make_eval_deps(knowledge_store=knowledge_store, knowledge_dir=memory_dir)

            steps.append(
                {
                    "name": "knowledge_store setup",
                    "ms": 0,
                    "detail": ks_label,
                }
            )

            agent = build_agent(config=settings)

            t = time.monotonic()
            try:
                async with asyncio.timeout(_TURN_TIMEOUT_SECS):
                    result = await run_turn(
                        agent=agent,
                        user_input=case.prompt,
                        deps=deps,
                        message_history=[],
                        frontend=SilentFrontend(),
                    )
            finally:
                if not case.degraded_path:
                    knowledge_store.close()

            steps.append(
                {
                    "name": "run_turn",
                    "ms": (time.monotonic() - t) * 1000,
                    "detail": f"prompt: '{case.prompt}'",
                }
            )

            sys_preview = _system_prompt_preview(result.messages)
            injection = _find_injection(result.messages)
            content_match = (
                case.expect_keyword is not None
                and case.expect_keyword.lower() in (injection or "").lower()
            )
            steps.append(
                {
                    "name": "scan SystemPromptPart for 'Relevant memories:'",
                    "ms": 0,
                    "detail": f"injected={injection is not None} "
                    + (
                        f"keyword='{case.expect_keyword}' found={content_match}"
                        if case.expect_keyword
                        else "no keyword expected"
                    ),
                }
            )
        finally:
            os.chdir(orig_cwd)

    failure = None
    if case.expect_injection:
        if not injection:
            verdict, failure = "FAIL", "no injection"
        elif case.expect_keyword and not content_match:
            verdict = "SOFT PASS"
        else:
            verdict = "PASS"
    else:
        if injection:
            verdict, failure = "FAIL", f"unexpected injection: {injection[:80]}"
        else:
            verdict = "PASS"

    return {
        "id": case.id,
        "verdict": verdict,
        "failure": failure,
        "steps": steps,
        "system_prompt_preview": sys_preview,
        "duration_ms": (time.monotonic() - case_t0) * 1000,
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(cases: list[dict[str, Any]], total_ms: float) -> None:
    run_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    passed = sum(1 for c in cases if c["verdict"] in ("PASS", "SOFT PASS"))

    lines: list[str] = [
        f"## Run: {run_ts}",
        "",
        f"**Model:** {settings.llm.provider} / {settings.llm.model or 'default'}  ",
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
        updated = "# Eval Report: Memory Recall\n\n" + section

    _REPORT_PATH.write_text(updated, encoding="utf-8")
    print(f"\n  Report → {_REPORT_PATH.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Memory Recall Injection (FTS5 DB-backed)")
    print("=" * 60)

    all_cases: list[dict[str, Any]] = []
    t0 = time.monotonic()

    for case in CASES:
        print(f"\n  [{case.id}] {case.desc}")
        print(f"    prompt: '{case.prompt}'", end=" ", flush=True)
        try:
            result = await run_case(case)
        except Exception as exc:
            result = {
                "id": case.id,
                "verdict": "ERROR",
                "failure": str(exc),
                "steps": [],
                "system_prompt_preview": None,
                "duration_ms": 0,
            }
        all_cases.append(result)
        print(f"{result['verdict']} ({result['duration_ms']:.0f}ms)")
        if result.get("failure"):
            print(f"    → {result['failure']}")

    total_ms = (time.monotonic() - t0) * 1000
    passed = sum(1 for c in all_cases if c["verdict"] in ("PASS", "SOFT PASS"))
    _write_report(all_cases, total_ms)

    print(f"\n{'=' * 60}")
    verdict = "PASS" if passed == len(all_cases) else "FAIL"
    print(f"  Verdict: {verdict} ({passed}/{len(all_cases)} cases, {total_ms:.0f}ms)")
    print(f"{'=' * 60}")
    return 0 if passed == len(all_cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
