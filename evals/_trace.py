"""Per-turn trace capture for eval-driven ``run_turn`` calls.

Every ``run_turn`` driven by an eval is wrapped with :func:`record_turn`,
which captures prompt-snapshot hashes, full message history, tool
calls/returns, thinking, token usage, model latency, and the co trace id
into ``evals/_outputs/<eval>-<ts>/case_<id>.jsonl``.

The trace id comes from co's structured-log tracing
(``co_cli.observability.tracing.current_trace_id``) — ``run_turn`` is
decorated with ``@trace("co.turn", new_trace=True)``, so each turn runs
under a fresh trace_id that's still bound to the contextvar when the call
returns. Use ``co tail`` to follow new records as they land or
``co trace <trace_id>`` to render the snapshot tree for one turn.

Long fields are truncated to ~4 KB inline; truncation is marked so the trace
file size stays bounded across runs. Thinking capture is opt-in via the
``EVAL_VERBOSE_TRACE=1`` env var (defaults off to keep trace size predictable;
the eval driver flips it on automatically when a case FAILs).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from co_cli.observability.tracing import current_trace_id

_INLINE_MAX_CHARS = 4096
_THINKING_ENV = "EVAL_VERBOSE_TRACE"


@dataclass
class ToolCallRecord:
    """One tool-call observation extracted from a turn's message history."""

    tool_name: str
    args: str
    result: str
    truncated: bool = False


@dataclass
class TurnTrace:
    case_id: str
    turn_index: int
    user_input: str
    assistant_text: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    thinking: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    model_call_seconds: float = 0.0
    trace_ids: list[str] = field(default_factory=list)
    prompt_snapshot: dict[str, str] = field(default_factory=dict)
    error: str = ""


def _truncate(s: str | None) -> tuple[str, bool]:
    if not s:
        return "", False
    if len(s) <= _INLINE_MAX_CHARS:
        return s, False
    return s[:_INLINE_MAX_CHARS] + "…", True


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def response_text(turn_result: Any) -> str:
    """The agent's final response text — pydantic-ai's canonical ``AgentRunResult.output``.

    Reads ``turn_result.output`` (str in practice; the value the production REPL
    renders) instead of reconstructing from message ``TextPart``s. qwen3.6's
    length-retry / thinking-budget path doesn't always land the final text as a
    clean ``TextPart`` in ``all_messages()``, so a message-walk reads empty even
    when ``.output`` resolves. Returns "" for a non-str / ``None`` output
    (``output`` is ``str | DeferredToolRequests`` in practice); never raises.
    """
    output = getattr(turn_result, "output", None)
    return output if isinstance(output, str) else ""


def _extract_messages(messages: list[Any]) -> tuple[str, list[ToolCallRecord], str | None]:
    """Walk pydantic-ai messages → (assistant_text, tool_calls, thinking)."""
    assistant_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls_by_id: dict[str, ToolCallRecord] = {}

    for msg in messages:
        parts = getattr(msg, "parts", None) or []
        for part in parts:
            cls_name = type(part).__name__
            if cls_name == "TextPart":
                assistant_parts.append(getattr(part, "content", "") or "")
            elif cls_name == "ThinkingPart":
                thinking_parts.append(getattr(part, "content", "") or "")
            elif cls_name == "ToolCallPart":
                tool_call_id = getattr(part, "tool_call_id", "") or ""
                args = getattr(part, "args", "")
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args, default=str)
                    except (TypeError, ValueError):
                        args = repr(args)
                args_trunc, _ = _truncate(args)
                tool_calls_by_id[tool_call_id] = ToolCallRecord(
                    tool_name=getattr(part, "tool_name", "") or "",
                    args=args_trunc,
                    result="",
                )
            elif cls_name == "ToolReturnPart":
                tool_call_id = getattr(part, "tool_call_id", "") or ""
                content = getattr(part, "content", "")
                if not isinstance(content, str):
                    try:
                        content = json.dumps(content, default=str)
                    except (TypeError, ValueError):
                        content = repr(content)
                result_trunc, truncated = _truncate(content)
                rec = tool_calls_by_id.get(tool_call_id)
                if rec is None:
                    rec = ToolCallRecord(
                        tool_name=getattr(part, "tool_name", "") or "",
                        args="",
                        result=result_trunc,
                        truncated=truncated,
                    )
                    tool_calls_by_id[tool_call_id] = rec
                else:
                    rec.result = result_trunc
                    rec.truncated = truncated

    assistant_text = "".join(assistant_parts)
    thinking = "".join(thinking_parts) if thinking_parts else None
    return assistant_text, list(tool_calls_by_id.values()), thinking


def _extract_usage(usage: Any) -> dict[str, int]:
    """Pull prompt/completion/total tokens from a pydantic-ai ``RunUsage`` object."""
    if usage is None:
        return {}
    out: dict[str, int] = {}
    for src, dst in (
        ("input_tokens", "prompt"),
        ("output_tokens", "completion"),
        ("total_tokens", "total"),
    ):
        val = getattr(usage, src, None)
        if isinstance(val, int):
            out[dst] = val
    if "total" not in out and "prompt" in out and "completion" in out:
        out["total"] = out["prompt"] + out["completion"]
    return out


def _prompt_snapshot_from_agent(agent: Any) -> dict[str, str]:
    """Best-effort static-prompt hash without instrumenting agent internals."""
    out: dict[str, str] = {}
    instructions = getattr(agent, "_instructions", None)
    if isinstance(instructions, str) and instructions:
        out["static_hash"] = _hash_text(instructions)
    return out


async def record_turn(
    *,
    case_id: str,
    turn_index: int,
    user_input: str,
    run_turn_callable: Any,
    case_dir_path: Path,
    agent: Any | None = None,
) -> tuple[Any, TurnTrace]:
    """Drive one ``run_turn`` callable and persist its ``TurnTrace``.

    ``run_turn_callable`` is a zero-arg async callable returning a
    ``TurnResult`` (the eval composes the kwargs it needs and passes a thunk).

    Returns ``(turn_result, turn_trace)``. Always appends one line to
    ``case_<id>.jsonl`` even on exception — caller can inspect both the
    raised exception and the partial trace.
    """
    case_dir_path.parent.mkdir(parents=True, exist_ok=True)
    trace_file = case_dir_path

    snapshot = _prompt_snapshot_from_agent(agent) if agent is not None else {}
    verbose_thinking = os.environ.get(_THINKING_ENV) == "1"

    error_msg = ""
    turn_result: Any = None
    assistant_text = ""
    tool_calls: list[ToolCallRecord] = []
    thinking: str | None = None
    usage_dict: dict[str, int] = {}
    trace_ids: list[str] = []

    t0 = time.monotonic()
    try:
        turn_result = await run_turn_callable()
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        model_call_seconds = time.monotonic() - t0
        # ``run_turn`` is decorated with ``@trace("co.turn", new_trace=True)`` —
        # by the time we read here, the trace id of the just-completed turn is
        # still bound to the contextvar (``pop_span`` clears the stack, not the
        # trace id). Read once; subsequent eval-orchestration code stays on
        # the same trace until the next turn starts a new one.
        tid = current_trace_id()
        if tid:
            trace_ids.append(tid)
        if turn_result is not None:
            msgs = getattr(turn_result, "messages", None) or []
            assistant_text, tool_calls, thinking_raw = _extract_messages(msgs)
            if not assistant_text:
                assistant_text = response_text(turn_result)
            if verbose_thinking:
                thinking = thinking_raw
            usage_dict = _extract_usage(getattr(turn_result, "usage", None))

        assistant_trunc, _ = _truncate(assistant_text)
        thinking_trunc, _ = _truncate(thinking) if thinking else ("", False)
        trace_obj = TurnTrace(
            case_id=case_id,
            turn_index=turn_index,
            user_input=user_input,
            assistant_text=assistant_trunc,
            tool_calls=tool_calls,
            thinking=thinking_trunc if thinking else None,
            token_usage=usage_dict,
            model_call_seconds=model_call_seconds,
            trace_ids=trace_ids,
            prompt_snapshot=snapshot,
            error=error_msg,
        )
        with trace_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace_obj), default=str) + "\n")

    return turn_result, trace_obj


def capture_artifact_diff(
    paths_before: dict[str, tuple[int, float]],
    paths_after: dict[str, tuple[int, float]],
) -> str:
    """Render a textual diff of {path: (size, mtime)} maps for a case.

    Inputs come from :func:`scan_artifact_paths` taken before/after a case
    body. Output is short multi-line markdown-ish text suitable for sidecar
    ``case_<id>-artifact-diff.txt`` and inclusion in a case's ``reason`` for review.
    """
    lines: list[str] = []
    before_keys = set(paths_before)
    after_keys = set(paths_after)

    for added in sorted(after_keys - before_keys):
        size, _ = paths_after[added]
        lines.append(f"+ {added} ({size}B)")
    for removed in sorted(before_keys - after_keys):
        size, _ = paths_before[removed]
        lines.append(f"- {removed} ({size}B was)")
    for shared in sorted(before_keys & after_keys):
        b_size, b_mtime = paths_before[shared]
        a_size, a_mtime = paths_after[shared]
        if (b_size, b_mtime) != (a_size, a_mtime):
            lines.append(f"~ {shared} ({b_size}B → {a_size}B)")

    return "\n".join(lines) if lines else "(no changes)"


def scan_artifact_paths(roots: list[Path]) -> dict[str, tuple[int, float]]:
    """Walk one or more directories and return {relative_path: (size, mtime)}.

    Cheap ``os.scandir`` walk — file metadata only, no content reads.
    """
    out: dict[str, tuple[int, float]] = {}
    for root in roots:
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for path in root_resolved.rglob("*"):
            if path.is_file():
                try:
                    st = path.stat()
                except OSError:
                    continue
                rel = path.relative_to(root_resolved.parent)
                out[str(rel)] = (st.st_size, st.st_mtime)
    return out
