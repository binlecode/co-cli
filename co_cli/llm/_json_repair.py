"""Syntactic JSON arg repair for tool calls — package-private model-boundary primitive.

Two layers, shared by the still-live ``SurrogateRecoveryModel`` wrapper and the
owned-loop ``model_turn`` client:

- ``repair_json_args`` / ``repair_response`` — purely syntactic repair applied to
  each string ``ToolCallPart.args`` on a ``ModelResponse`` BEFORE pydantic
  validation. Idempotent on valid JSON, so gating is cleanliness, not correctness.
- ``RepairingStreamedResponse`` — a thin proxy that repairs the assembled
  ``StreamedResponse.get()`` (the surface the graph and the owned loop both
  validate from).

Domain-homed boundary code, not a util module: ``co_cli/llm/`` already hosts
``_message_sanitize.py`` as a package-private model-boundary primitive.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import StreamedResponse

_CLOSE_FOR: dict[str, str] = {"{": "}", "[": "]"}
_OPEN_FOR: dict[str, str] = {v: k for k, v in _CLOSE_FOR.items()}
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_JSON_REPAIR_MAX_TRIM_STEPS = 50
"""Max trailing-delimiter trim passes in JSON arg repair (bounds the trim loop)."""


def _try_parse(s: str) -> str | None:
    try:
        return json.dumps(json.loads(s, strict=False))
    except json.JSONDecodeError:
        return None


def _balance_brackets(s: str) -> str:
    """Append missing closing brackets by tracking the open-bracket stack."""
    stack: list[str] = []
    in_str = False
    escape_next = False
    for ch in s:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_str:
            escape_next = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in _CLOSE_FOR:
            stack.append(ch)
        elif ch in _OPEN_FOR and stack and stack[-1] == _OPEN_FOR[ch]:
            stack.pop()
    return s + "".join(_CLOSE_FOR[o] for o in reversed(stack))


def repair_json_args(raw: str) -> str:
    """Apply syntactic repair passes to a malformed tool-call arguments string.

    Purely syntactic — no value inference or schema awareness. Returns a valid
    JSON string on success, or '{}' if all passes fail (so pydantic validation
    can raise ModelRetry rather than crashing the session).
    """
    if not raw or not raw.strip():
        return "{}"
    s = raw.strip()

    if s == "None":
        return "{}"

    # Control-char escape — strict=False accepts literal tabs/newlines;
    # re-serialise to produce a spec-compliant string.
    result = _try_parse(s)
    if result is not None:
        return result

    # Trailing-comma strip (common in quantized-model output)
    s = _TRAILING_COMMA.sub(r"\1", s)
    result = _try_parse(s)
    if result is not None:
        return result

    # Balance unclosed brackets, then re-strip trailing commas that now
    # precede the appended closer.
    s = _TRAILING_COMMA.sub(r"\1", _balance_brackets(s))
    result = _try_parse(s)
    if result is not None:
        return result

    # Trim excess trailing closing delimiters (bounded — see _JSON_REPAIR_MAX_TRIM_STEPS).
    for _ in range(_JSON_REPAIR_MAX_TRIM_STEPS):
        s = s.rstrip()
        if not s or s[-1] not in ("}", "]"):
            break
        s = s[:-1]
        result = _try_parse(s)
        if result is not None:
            return result

    return "{}"


def repair_response(response: ModelResponse) -> ModelResponse:
    """Return a copy of ``response`` with each string ``ToolCallPart.args`` repaired.

    Repair is idempotent on already-valid JSON, so unchanged parts are preserved
    by identity and the response is rebuilt only when something actually changed.
    """
    new_parts = []
    changed = False
    for part in response.parts:
        if isinstance(part, ToolCallPart) and isinstance(part.args, str):
            repaired = repair_json_args(part.args)
            if repaired != part.args:
                new_parts.append(replace(part, args=repaired))
                changed = True
                continue
        new_parts.append(part)
    if not changed:
        return response
    return replace(response, parts=new_parts)


class RepairingStreamedResponse:
    """StreamedResponse proxy that repairs tool-call args on the assembled ``get()``.

    Explicit read-surface contract — what the agent graph actually touches:
    - ``get()`` — the graph validates and dispatches tools from the assembled
      ``StreamedResponse.get()`` (``_agent_graph.py`` ``_streaming_handler``, :637),
      so repairing here lands the fix before pydantic validation.
    - ``__aiter__`` / ``usage()`` — delegate to the wrapped stream verbatim.

    ``__getattr__`` stays as the catch-all for every other member the graph or
    instrumentation reads (``model_name``, ``timestamp``, ``provider_*``,
    ``close_stream``, and the stream passed to ``_build_agent_stream``). It is
    deliberately NOT replaced by an enumerated member list — the SDK reads the
    streamed response beyond the hot members above, so enumerate-and-drop would
    ``AttributeError`` on any unlisted access. Subclassing ``StreamedResponse`` is
    not viable: its ``get()``/``usage()`` read ``self._parts_manager``/``self._usage``,
    which are populated from raw provider chunks the inner stream has already
    consumed — a subclass could fill them only by privately coupling to the inner.
    """

    def __init__(self, inner: StreamedResponse) -> None:
        self._inner = inner
        self._cached: ModelResponse | None = None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._inner.__aiter__()

    def get(self) -> ModelResponse:
        if self._cached is None:
            self._cached = repair_response(self._inner.get())
        return self._cached

    def usage(self) -> Any:
        return self._inner.usage()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
