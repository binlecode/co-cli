#!/usr/bin/env python3
"""Eval: signal-detector approval path — validate low/high confidence save dispatch.

Tests the post-turn hook integration from main.py (lines 241–261):
  - high-confidence signal → save immediately, prompt_approval NOT called
  - low-confidence signal + user approves ("y") → save called
  - low-confidence signal + user denies ("n") → save discarded
  - no signal → neither prompt_approval nor save called

Uses analyze_for_signals() to get a real SignalResult from the LLM, then runs
the same dispatch logic as main.py with a CapturingFrontend to observe
behavior. Verifies outcomes by inspecting memory files written to disk.

Prerequisites: LLM provider configured (ollama or gemini).

Usage:
    uv run python evals/eval_signal_detector_approval.py
"""

import asyncio
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from pydantic_ai.messages import ModelRequest, UserPromptPart  # noqa: E402

from co_cli._signal_analyzer import analyze_for_signals  # noqa: E402
from co_cli.agent import get_agent  # noqa: E402
from co_cli.tools.memory import _save_memory_impl  # noqa: E402
from evals._common import make_eval_deps  # noqa: E402


# ---------------------------------------------------------------------------
# CapturingFrontend
# ---------------------------------------------------------------------------


class CapturingFrontend:
    """Frontend that records prompt_approval calls and statuses for assertions."""

    def __init__(self, *, approval_response: str = "y"):
        self.statuses: list[str] = []
        self.approval_calls: list[str] = []
        self._approval_response = approval_response

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_call(self, name: str, args_display: str) -> None:
        pass

    def on_tool_result(self, title: str, content: Any) -> None:
        pass

    def on_final_output(self, text: str) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.statuses.append(message)

    def prompt_approval(self, description: str) -> str:
        self.approval_calls.append(description)
        return self._approval_response


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class ApprovalCase:
    id: str
    user_message: str
    approval_response: str
    expect_save: bool
    expect_approval_called: bool
    expect_learned_status: bool
    description: str


CASES: list[ApprovalCase] = [
    ApprovalCase(
        id="high-auto-save",
        user_message="don't use trailing comments in the code",
        approval_response="y",
        # approval_response set but must NOT be called for high-confidence
        expect_save=True,
        expect_approval_called=False,
        expect_learned_status=True,
        description="High confidence → save immediately, no approval prompt",
    ),
    ApprovalCase(
        id="low-approve-y",
        user_message="I prefer shorter responses",
        approval_response="y",
        expect_save=True,
        expect_approval_called=True,
        expect_learned_status=False,
        description="Low confidence + user approves (y) → save called",
    ),
    ApprovalCase(
        id="low-deny-n",
        user_message="I prefer shorter responses",
        approval_response="n",
        expect_save=False,
        expect_approval_called=True,
        expect_learned_status=False,
        description="Low confidence + user denies (n) → save discarded",
    ),
    ApprovalCase(
        id="no-signal",
        user_message="what time is it in Tokyo?",
        approval_response="y",
        # approval_response set but must NOT be called — no signal phrase
        expect_save=False,
        expect_approval_called=False,
        expect_learned_status=False,
        description="No signal → no approval prompt, no save",
    ),
]


# ---------------------------------------------------------------------------
# Dispatch helper — mirrors main.py lines 241–261
# ---------------------------------------------------------------------------


async def _run_dispatch(
    user_message: str,
    model: Any,
    deps: Any,
    frontend: CapturingFrontend,
    memory_dir: Path,
) -> dict[str, Any]:
    """Run signal detection + approval dispatch, matching main.py post-turn hook."""
    messages = [ModelRequest(parts=[UserPromptPart(content=user_message)])]
    files_before = set(memory_dir.glob("*.md"))

    signal = await analyze_for_signals(messages, model)
    if signal.found and signal.candidate and signal.tag:
        if signal.confidence == "high":
            await _save_memory_impl(deps, signal.candidate, [signal.tag], None)
            frontend.on_status(f"Learned: {signal.candidate[:80]}")
        else:
            choice = frontend.prompt_approval(
                f"Worth remembering: {signal.candidate}"
            )
            if choice in ("y", "a"):
                await _save_memory_impl(deps, signal.candidate, [signal.tag], None)

    files_after = set(memory_dir.glob("*.md"))
    return {
        "save_called": len(files_after) > len(files_before),
        "approval_called": len(frontend.approval_calls) > 0,
        "learned_status": any("Learned:" in s for s in frontend.statuses),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print("=" * 60)
    print("  Eval: Signal Detector Approval Path")
    print("=" * 60)
    print()

    agent, _, _ = get_agent()
    model = agent.model

    t0 = time.monotonic()
    passed_count = 0
    total = len(CASES)

    for case in CASES:
        print(f"  [{case.id}] {case.description}")
        print(f'    Prompt: "{case.user_message[:60]}"', end=" ", flush=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                memory_dir = Path(tmpdir) / ".co-cli" / "knowledge" / "memories"
                memory_dir.mkdir(parents=True)

                deps = make_eval_deps(session_id=f"eval-approval-{case.id}")
                frontend = CapturingFrontend(approval_response=case.approval_response)

                try:
                    scores = await _run_dispatch(
                        case.user_message, model, deps, frontend, memory_dir
                    )
                except Exception as exc:
                    print(f"ERROR ({exc})")
                    continue
            finally:
                os.chdir(orig_cwd)

        failures = []
        if scores["save_called"] != case.expect_save:
            failures.append(
                f"save_called={scores['save_called']} (expected {case.expect_save})"
            )
        if scores["approval_called"] != case.expect_approval_called:
            failures.append(
                f"approval_called={scores['approval_called']} "
                f"(expected {case.expect_approval_called})"
            )
        if scores["learned_status"] != case.expect_learned_status:
            failures.append(
                f"learned_status={scores['learned_status']} "
                f"(expected {case.expect_learned_status})"
            )

        if not failures:
            print("PASS")
            passed_count += 1
        else:
            print(f"FAIL ({', '.join(failures)})")

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 60}")
    verdict = "PASS" if passed_count == total else "FAIL"
    print(f"  Verdict: {verdict} ({passed_count}/{total} cases, {elapsed:.1f}s)")
    print(f"{'=' * 60}")
    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
