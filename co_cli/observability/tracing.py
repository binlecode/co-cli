"""Structured-log tracing for co-cli.

Spans are tracked via a contextvar stack; each span emits a JSON record on close
to the ``co_cli.observability.spans`` logger (``propagate=False``, dedicated
rotating handler). The ``@trace`` decorator wraps sync/async functions;
``current_span()`` returns a proxy that mutates the active span's attributes
and events. Replaces ``opentelemetry-sdk`` for co-cli's local-only use case.

Public surface:
- ``setup_log`` — configure the rotating JSON-line handler and redaction patterns
- ``set_session_context`` / ``clear_session_context`` — bind a session_id to the contextvar
- ``new_trace`` — start a fresh ``trace_id`` (spans on the stack keep their own id)
- ``current_trace_id`` — read the trace_id bound to the contextvar (None if unset)
- ``current_span`` — proxy over the top-of-stack span
- ``trace`` — decorator with optional ``new_trace=True`` to reset the trace_id
  before pushing the decorated function's span
- ``push_span`` / ``pop_span`` — explicit span management for capability hooks
- ``run_with_context`` — propagate ContextVars across ``loop.run_in_executor``
"""

import functools
import inspect
import json
import logging
import logging.handlers
import os
import re
import secrets
import time
from collections.abc import Callable
from contextvars import ContextVar, copy_context
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOGGER_NAME = "co_cli.observability.spans"
_DEBUG_LOGGER = logging.getLogger("co_cli.observability.tracing")

_SESSION_ID: ContextVar[str | None] = ContextVar("co_session_id", default=None)
_TRACE_ID: ContextVar[str | None] = ContextVar("co_trace_id", default=None)
_SPAN_STACK: ContextVar[tuple[dict, ...]] = ContextVar("co_span_stack", default=())

_COMPILED_PATTERNS: list[re.Pattern[str]] = []


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def setup_log(
    log_path: Path,
    *,
    max_size_mb: int = 50,
    backup_count: int = 5,
    redact_patterns: list[str] | None = None,
) -> None:
    """Configure the rotating JSON-line spans handler. Idempotent."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = os.path.abspath(log_path)
    for existing in logger.handlers:
        if (
            isinstance(existing, logging.handlers.RotatingFileHandler)
            and existing.baseFilename == target
        ):
            break
    else:
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    global _COMPILED_PATTERNS
    _COMPILED_PATTERNS = [re.compile(p) for p in (redact_patterns or [])]


def set_session_context(session_id: str) -> None:
    _SESSION_ID.set(session_id)


def clear_session_context() -> None:
    _SESSION_ID.set(None)


def new_trace() -> str:
    """Generate a fresh ``trace_id`` and bind it to the contextvar.

    Spans already on the stack keep their own ``trace_id``. Spans pushed after
    this call adopt the new one. Intended to be called between user turns
    (when the stack is empty) or via ``@trace(new_trace=True)`` so the
    decorated function's own span gets the fresh id.
    """
    trace_id = _new_id("t")
    _TRACE_ID.set(trace_id)
    return trace_id


def current_trace_id() -> str | None:
    """Return the trace_id bound to the current contextvar, or None if unset.

    After a ``@trace(new_trace=True)``-decorated function returns, the trace_id
    set during its invocation remains in the contextvar (``pop_span`` clears
    the span stack but does not reset the trace id). Callers like evals can
    read this immediately after the call to learn which trace just ran.
    """
    return _TRACE_ID.get()


class _Span:
    """Proxy that mutates the top-of-stack span dict in place."""

    __slots__ = ("_data",)

    def __init__(self, data: dict) -> None:
        self._data = data

    def set_attribute(self, key: str, value: Any) -> None:
        self._data["attributes"][key] = value

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        self._data["events"].append(
            {"ts": _now_iso(), "name": name, "attributes": attributes or {}}
        )

    def set_status(self, status: str, msg: str | None = None) -> None:
        self._data["status"] = status
        if msg is not None:
            self._data["status_msg"] = msg


class _NoOpSpan:
    """Returned when the stack is empty — debug-logs each call to surface
    latent 'where did this go?' bugs without breaking the call site."""

    __slots__ = ()

    def set_attribute(self, key: str, value: Any) -> None:
        _DEBUG_LOGGER.debug("current_span() called with empty stack: set_attribute(%r)", key)

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        _DEBUG_LOGGER.debug("current_span() called with empty stack: add_event(%r)", name)

    def set_status(self, status: str, msg: str | None = None) -> None:
        _DEBUG_LOGGER.debug("current_span() called with empty stack: set_status(%r)", status)


_NO_OP_SPAN = _NoOpSpan()


def current_span() -> _Span | _NoOpSpan:
    stack = _SPAN_STACK.get()
    if not stack:
        return _NO_OP_SPAN
    return _Span(stack[-1])


def push_span(name: str, *, kind: str = "co", attributes: dict | None = None) -> dict:
    """Push a span onto the stack. Returns the span dict (for direct access by
    capability hooks). The decorator and ``ObservabilityCapability.before_*``
    are the intended callers — business code uses ``@trace`` or
    ``current_span().add_event(...)`` instead."""
    parent_stack = _SPAN_STACK.get()
    parent_span_id = parent_stack[-1]["span_id"] if parent_stack else None
    trace_id = _TRACE_ID.get()
    if trace_id is None:
        trace_id = _new_id("t")
        _TRACE_ID.set(trace_id)

    span = {
        "trace_id": trace_id,
        "span_id": _new_id("s"),
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": kind,
        "start_ts": _now_iso(),
        "_start_perf": time.perf_counter(),
        "attributes": dict(attributes) if attributes else {},
        "events": [],
        "status": "OK",
        "status_msg": None,
    }
    _SPAN_STACK.set((*parent_stack, span))
    return span


def pop_span(
    *,
    status: str = "OK",
    status_msg: str | None = None,
    attributes: dict | None = None,
) -> None:
    """Pop the top-of-stack span, apply final attributes/status, emit the record."""
    stack = _SPAN_STACK.get()
    if not stack:
        _DEBUG_LOGGER.debug("pop_span() called with empty stack — no-op")
        return
    span = stack[-1]
    _SPAN_STACK.set(stack[:-1])
    if attributes:
        span["attributes"].update(attributes)
    if status != "OK":
        span["status"] = status
    if status_msg is not None:
        span["status_msg"] = status_msg
    _emit(span)


def _emit(span: dict) -> None:
    duration_ms = (time.perf_counter() - span["_start_perf"]) * 1000.0
    record = {
        "ts": _now_iso(),
        "schema_version": 1,
        "session_id": _SESSION_ID.get(),
        "trace_id": span["trace_id"],
        "span_id": span["span_id"],
        "parent_span_id": span["parent_span_id"],
        "name": span["name"],
        "kind": span["kind"],
        "start_ts": span["start_ts"],
        "duration_ms": round(duration_ms, 3),
        "status": span["status"],
        "status_msg": _redact_str(span["status_msg"]) if span["status_msg"] else None,
        "attributes": _redact_dict(span["attributes"]),
        "events": [
            {
                "ts": e["ts"],
                "name": e["name"],
                "attributes": _redact_dict(e["attributes"]),
            }
            for e in span["events"]
        ],
    }
    logging.getLogger(_LOGGER_NAME).info(json.dumps(record, default=str))


def _redact_str(value: str) -> str:
    if not _COMPILED_PATTERNS:
        return value
    for pattern in _COMPILED_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def _redact_value(value: Any) -> Any:
    """Redact strings; if a string parses as JSON, walk the parsed tree fully."""
    if isinstance(value, str):
        redacted = _redact_str(value)
        try:
            parsed = json.loads(redacted)
        except (json.JSONDecodeError, ValueError):
            return redacted
        if isinstance(parsed, str | int | float | bool) or parsed is None:
            return redacted
        return json.dumps(_redact_value(parsed))
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def _redact_dict(d: dict) -> dict:
    return {k: _redact_value(v) for k, v in d.items()}


def _top_is(span: dict) -> bool:
    stack = _SPAN_STACK.get()
    return bool(stack) and stack[-1] is span


def trace(name: str | None = None, *, new_trace: bool = False) -> Callable:  # noqa: C901 — sync/async branches kept inline; complexity is intrinsic to dual-path decoration
    """Decorate a sync or async function to emit a span on each call.

    Args:
        name: Span name. Defaults to ``co.<module_basename>.<func_name>``.
        new_trace: If True, call the module-level :func:`new_trace` BEFORE pushing
            the span so the decorated function's own span carries the fresh
            ``trace_id``. Used at the top of each user turn (``co.turn``) to make
            every turn a fresh trace.
    """
    start_new_trace = new_trace
    new_trace_fn = globals()["new_trace"]

    def decorator(func: Callable) -> Callable:
        span_name = name or f"co.{func.__module__.rsplit('.', 1)[-1]}.{func.__name__}"

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if start_new_trace:
                    new_trace_fn()
                my_span = push_span(span_name)
                try:
                    result = await func(*args, **kwargs)
                except BaseException as exc:
                    if _top_is(my_span):
                        pop_span(status="ERROR", status_msg=str(exc))
                    raise
                if _top_is(my_span):
                    pop_span()
                return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if start_new_trace:
                new_trace_fn()
            my_span = push_span(span_name)
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                if _top_is(my_span):
                    pop_span(status="ERROR", status_msg=str(exc))
                raise
            if _top_is(my_span):
                pop_span()
            return result

        return sync_wrapper

    return decorator


def run_with_context(fn: Callable, *args: Any, **kwargs: Any) -> Callable[[], Any]:
    """Capture the current context now; return a 0-arg callable for the executor.

    Use to bridge ``loop.run_in_executor`` calls where the worker thread would
    otherwise lose the span/trace/session context:

        await loop.run_in_executor(None, tracing.run_with_context(worker, arg))

    The context snapshot happens at the call site (the async task), so the
    worker thread sees the ContextVar values that were in effect there.
    """
    ctx = copy_context()

    def _bound() -> Any:
        return ctx.run(fn, *args, **kwargs)

    return _bound


__all__ = [
    "clear_session_context",
    "current_span",
    "current_trace_id",
    "new_trace",
    "pop_span",
    "push_span",
    "run_with_context",
    "set_session_context",
    "setup_log",
    "trace",
]
