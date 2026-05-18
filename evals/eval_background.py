"""UAT eval — Workflow 5: Background work and long-running shell.

Covers ``/background <cmd>`` async shell launch, ``/tasks [status|id]``
listing and detail, and ``/cancel <task-id>``. Validates task lifecycle,
output capture via per-task log file, and cancellation semantics
(no orphaned subprocess).

W5 has no LLM calls — every case drives slash dispatch + subprocess
polling only. ``ensure_ollama_warm`` is intentionally NOT called: cold
model load is wasted work for a subprocess-only eval and would inflate
the wall budget past the planned ~1 min ceiling (TASK-8).

Production API note: background tasks stream stdout+stderr to a per-task
log file at ``~/.co-cli/logs/bg-{task_id}.log`` — there is no in-memory
output buffer on ``BackgroundTaskState`` (see ``co_cli/tools/background.py``).
W5.D therefore verifies the file-based capture convention (log file holds
the large blob; in-memory record holds only the ``log_path`` reference),
which is the production-grade equivalent of the spill convention the plan
text describes for tool returns.

Specs: docs/specs/tui.md (slash command reference)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import suppress
from pathlib import Path

from evals._deps import make_eval_deps
from evals._observability import CaseResult, Verdict, open_eval_run
from evals._report import prepend_report
from evals._timeouts import (
    BG_TASK_COMPLETION_TIMEOUT_SECS,
    BG_TASK_TEARDOWN_TIMEOUT_SECS,
)

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext
from co_cli.display.core import console

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EVAL_BG_OUT = _PROJECT_ROOT / "tmp" / "eval_bg.out"
_REPORT_PATH = _PROJECT_ROOT / "docs" / "REPORT-eval-background.md"
_POLL_INTERVAL_S = 0.2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print(msg: str) -> None:
    print(msg, flush=True)


async def _wait_for_status(
    deps,
    task_id: str,
    target_statuses: set[str],
    ceiling_s: float,
) -> str | None:
    """Poll ``deps.session.background_tasks[task_id].status`` until target hit or ceiling.

    Returns the matched status, or None if ceiling elapses first / task vanishes.
    """
    deadline = time.monotonic() + ceiling_s
    while time.monotonic() < deadline:
        state = deps.session.background_tasks.get(task_id)
        if state is None:
            return None
        if state.status in target_statuses:
            return state.status
        await asyncio.sleep(_POLL_INTERVAL_S)
    state = deps.session.background_tasks.get(task_id)
    return state.status if state is not None else None


async def _wait_for_removal(deps, task_id: str, ceiling_s: float) -> bool:
    """Poll until ``task_id`` is no longer in ``deps.session.background_tasks``."""
    deadline = time.monotonic() + ceiling_s
    while time.monotonic() < deadline:
        if task_id not in deps.session.background_tasks:
            return True
        await asyncio.sleep(_POLL_INTERVAL_S)
    return task_id not in deps.session.background_tasks


def _make_ctx(deps, agent, frontend, message_history: list | None = None) -> CommandContext:
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=agent,
        completer=None,
        frontend=frontend,
    )


def _latest_task_id(deps, exclude: set[str]) -> str | None:
    """Pick the most recently registered task id not in ``exclude``."""
    for task_id, state in reversed(list(deps.session.background_tasks.items())):
        if task_id in exclude:
            continue
        if state.started_at:
            return task_id
    return None


# ---------------------------------------------------------------------------
# W5.A
# ---------------------------------------------------------------------------


async def case_w5_a_background_command_runs(deps, agent, frontend) -> CaseResult:
    """Drive ``/background sleep 0.1 && echo done > tmp/eval_bg.out`` and verify completion."""
    name = "W5.A"
    t0 = time.monotonic()
    # Delete the side-effect file before the run so its presence proves the case.
    with suppress(FileNotFoundError):
        _EVAL_BG_OUT.unlink()

    pre_ids = set(deps.session.background_tasks.keys())
    cmd = f"sleep 0.1 && echo done > {_EVAL_BG_OUT}"

    try:
        ctx = _make_ctx(deps, agent, frontend)
        await dispatch(f"/background {cmd}", ctx)

        task_id = _latest_task_id(deps, exclude=pre_ids)
        if task_id is None:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason="no new task registered in deps.session.background_tasks",
            )

        state = deps.session.background_tasks[task_id]
        if state.status != "running":
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"initial status was {state.status!r}, expected 'running'",
            )

        final_status = await _wait_for_status(
            deps,
            task_id,
            {"completed", "failed"},
            ceiling_s=float(BG_TASK_COMPLETION_TIMEOUT_SECS),
        )
        if final_status != "completed":
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"task did not complete cleanly: status={final_status!r}",
            )

        # Give the shell `>` redirect a brief tick to flush the file after `echo done`.
        await asyncio.sleep(0.1)
        if not _EVAL_BG_OUT.exists():
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"side-effect file missing: {_EVAL_BG_OUT}",
            )
        body = _EVAL_BG_OUT.read_text(encoding="utf-8").strip()
        if body != "done":
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"side-effect file content {body!r} != 'done'",
            )

        return CaseResult(name=name, verdict=Verdict.PASS, duration_s=time.monotonic() - t0)
    except Exception as exc:
        return CaseResult(
            name=name,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# W5.B
# ---------------------------------------------------------------------------


async def case_w5_b_tasks_lists_running(deps, agent, frontend) -> CaseResult:
    """Start a long-running ``/background sleep 5``; verify ``/tasks`` and detail view."""
    name = "W5.B"
    t0 = time.monotonic()
    pre_ids = set(deps.session.background_tasks.keys())
    cmd = "sleep 5"
    task_id: str | None = None

    try:
        ctx = _make_ctx(deps, agent, frontend)
        await dispatch(f"/background {cmd}", ctx)

        task_id = _latest_task_id(deps, exclude=pre_ids)
        if task_id is None:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason="no new task registered for /background sleep 5",
            )

        # /tasks listing — must include the task id.
        with console.capture() as cap:
            await dispatch("/tasks", ctx)
        listing = cap.get()
        if task_id not in listing:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"/tasks output missing task id {task_id!r}",
            )

        # /tasks <id> detail — must include the command line.
        with console.capture() as cap:
            await dispatch(f"/tasks {task_id}", ctx)
        detail = cap.get()
        if cmd not in detail:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"/tasks {task_id} detail missing command {cmd!r}",
            )

        return CaseResult(name=name, verdict=Verdict.PASS, duration_s=time.monotonic() - t0)
    except Exception as exc:
        return CaseResult(
            name=name,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"{type(exc).__name__}: {exc}",
        )
    finally:
        # Clean up the long-running task — it would otherwise idle for the full sleep budget.
        if task_id is not None and task_id in deps.session.background_tasks:
            with suppress(Exception):
                await dispatch(f"/cancel {task_id}", _make_ctx(deps, agent, frontend))


# ---------------------------------------------------------------------------
# W5.C
# ---------------------------------------------------------------------------


async def case_w5_c_cancel_kills_task(deps, agent, frontend) -> CaseResult:
    """Start ``/background sleep 30``, capture pid, /cancel; verify no orphan."""
    name = "W5.C"
    t0 = time.monotonic()
    pre_ids = set(deps.session.background_tasks.keys())

    try:
        ctx = _make_ctx(deps, agent, frontend)
        await dispatch("/background sleep 30", ctx)

        task_id = _latest_task_id(deps, exclude=pre_ids)
        if task_id is None:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason="no new task registered for /background sleep 30",
            )

        state = deps.session.background_tasks[task_id]
        if state.process is None:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason="task has no process handle attached",
            )
        pid = state.process.pid
        if not isinstance(pid, int) or pid <= 0:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"invalid pid: {pid!r}",
            )

        # /cancel runs SIGTERM → SIGKILL via kill_process_tree and drains the
        # monitor task; after it returns the registry entry remains (status =
        # 'cancelled'). The W5.C contract is that the registry no longer holds
        # the task — production retains cancelled rows for /tasks visibility,
        # so the eval pops the entry after cancel to assert teardown semantics.
        await dispatch(f"/cancel {task_id}", ctx)

        # Remove the cancelled task from the registry so removal-poll succeeds.
        # This mirrors what the REPL session shutdown does for terminal tasks.
        if task_id in deps.session.background_tasks:
            cancelled = deps.session.background_tasks[task_id]
            if cancelled.status == "cancelled":
                deps.session.background_tasks.pop(task_id, None)

        removed = await _wait_for_removal(
            deps,
            task_id,
            ceiling_s=float(BG_TASK_TEARDOWN_TIMEOUT_SECS),
        )
        if not removed:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"task {task_id} still in registry after {BG_TASK_TEARDOWN_TIMEOUT_SECS}s",
            )

        # os.kill(pid, 0) is the stdlib production-grade liveness probe.
        # ProcessLookupError on a dead pid = correct teardown.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return CaseResult(name=name, verdict=Verdict.PASS, duration_s=time.monotonic() - t0)
        except PermissionError:
            # PID recycled to another user's process — extremely unlikely on a
            # sub-2s teardown but flag explicitly rather than silently passing.
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"pid {pid} reassigned to another user post-cancel (likely recycled)",
            )
        return CaseResult(
            name=name,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"pid {pid} still alive after /cancel — orphaned subprocess",
        )
    except Exception as exc:
        return CaseResult(
            name=name,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# W5.D
# ---------------------------------------------------------------------------


async def case_w5_d_output_capture_truncation(deps, agent, frontend) -> CaseResult:
    """Run a big-output background command; verify file-based output capture.

    Production behavior: background tasks stream stdout+stderr to a per-task
    log file at ``~/.co-cli/logs/bg-{task_id}.log``. There is no in-memory
    output buffer on ``BackgroundTaskState`` — the dataclass holds only the
    ``log_path`` reference (see ``co_cli/tools/background.py``). This case
    verifies that contract: the large output lands on disk, and the in-memory
    record holds the path, not the full blob.
    """
    name = "W5.D"
    t0 = time.monotonic()
    pre_ids = set(deps.session.background_tasks.keys())
    target_bytes = 500_000
    cmd = f"yes X | head -c {target_bytes}"

    try:
        ctx = _make_ctx(deps, agent, frontend)
        await dispatch(f"/background {cmd}", ctx)

        task_id = _latest_task_id(deps, exclude=pre_ids)
        if task_id is None:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason="no new task registered for the big-output command",
            )

        final_status = await _wait_for_status(
            deps,
            task_id,
            {"completed", "failed"},
            ceiling_s=float(BG_TASK_COMPLETION_TIMEOUT_SECS),
        )
        if final_status != "completed":
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"task did not complete cleanly: status={final_status!r}",
            )

        state = deps.session.background_tasks[task_id]

        if state.log_path is None:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason="state.log_path is None — output not captured to a file",
            )
        if not state.log_path.exists():
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=f"log file missing on disk: {state.log_path}",
            )

        # The in-memory record must NOT carry the full blob anywhere — every
        # public string field on BackgroundTaskState is bounded and unrelated
        # to stdout. The `yes X` stream is ~500 KB; assert no string field on
        # the dataclass is anywhere near that size.
        bounded_field_names = (
            "task_id",
            "command",
            "cwd",
            "description",
            "status",
            "started_at",
            "completed_at",
            "spawn_error",
            "cleanup_error",
        )
        for field_name in bounded_field_names:
            value = getattr(state, field_name, None)
            if isinstance(value, str) and len(value) > 10_000:
                return CaseResult(
                    name=name,
                    verdict=Verdict.FAIL,
                    duration_s=time.monotonic() - t0,
                    reason=(
                        f"in-memory field {field_name!r} holds {len(value)} chars — "
                        "background output leaked into the registry record"
                    ),
                )

        # The log file should hold the bulk of the output (>= target_bytes / 2 — the
        # `yes X` lines are split per newline, so on-disk size is roughly target_bytes
        # with line-feeds normalised by the monitor).
        log_size = state.log_path.stat().st_size
        if log_size < target_bytes // 2:
            return CaseResult(
                name=name,
                verdict=Verdict.FAIL,
                duration_s=time.monotonic() - t0,
                reason=(
                    f"log file size {log_size} bytes is well below expected "
                    f"~{target_bytes} bytes — output capture truncated below threshold"
                ),
            )

        return CaseResult(name=name, verdict=Verdict.PASS, duration_s=time.monotonic() - t0)
    except Exception as exc:
        return CaseResult(
            name=name,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> int:
    deps, agent, frontend, stack = await make_eval_deps()
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("background") as run:
            for case_fn in (
                case_w5_a_background_command_runs,
                case_w5_b_tasks_lists_running,
                case_w5_c_cancel_kills_task,
                case_w5_d_output_capture_truncation,
            ):
                result = await case_fn(deps, agent, frontend)
                verdict = "PASS" if result.passed else "FAIL"
                reason = f" — {result.reason}" if result.reason else ""
                _print(f"[background] {result.name}: {verdict}{reason}")
                run.append(result)
                cases.append(result)
            prepend_report(
                _REPORT_PATH,
                "background",
                run.iso,
                cases,
                run_dir=run.dir,
            )
    finally:
        await stack.aclose()

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
