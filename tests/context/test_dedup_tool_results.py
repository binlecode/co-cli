"""Functional tests for dedup_tool_results — hash-based dedup of identical tool returns."""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.config._compaction import CompactionSettings
from co_cli.context._compaction import (
    _find_last_turn_start,
    dedup_tool_results,
    truncate_tool_results,
)
from co_cli.context._dedup_tool_results import (
    build_dedup_part,
    dedup_key,
    is_dedup_candidate,
)
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend


def _processor_ctx() -> RunContext:
    """Minimal RunContext for sync history processors — no LLM call."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(
            compaction=CompactionSettings(min_context_length_tokens=0),
        ),
    )
    return RunContext(deps=deps, model=None, usage=RunUsage())


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def _user_msg(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant_msg(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_turn(
    tool_name: str,
    args: dict,
    call_id: str,
    content: object,
) -> list:
    """Single tool turn: user prompt, assistant call, tool return, assistant ack."""
    return [
        _user_msg(f"turn using {tool_name}"),
        ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id)]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=call_id)]
        ),
        _assistant_msg(f"ack {call_id}"),
    ]


def _tail() -> list:
    """Final protected tail: user prompt + assistant response."""
    return [_user_msg("final"), _assistant_msg("done")]


def _extract_returns(messages: list, tool_name: str) -> list[ToolReturnPart]:
    return [
        part
        for msg in messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


# ---------------------------------------------------------------------------
# Unit tests — is_dedup_candidate
# ---------------------------------------------------------------------------


def test_is_dedup_candidate_accepts_compactable_string_over_threshold():
    part = ToolReturnPart(tool_name="file_read", content="x" * 200, tool_call_id="c0")
    assert is_dedup_candidate(part) is True


def test_is_dedup_candidate_rejects_non_compactable_tool():
    part = ToolReturnPart(tool_name="save_memory", content="x" * 500, tool_call_id="c0")
    assert is_dedup_candidate(part) is False


def test_is_dedup_candidate_rejects_short_content():
    part = ToolReturnPart(tool_name="file_read", content="x" * 199, tool_call_id="c0")
    assert is_dedup_candidate(part) is False


def test_is_dedup_candidate_rejects_non_string_content():
    part = ToolReturnPart(
        tool_name="file_read",
        content=[{"type": "text", "text": "x" * 500}],
        tool_call_id="c0",
    )
    assert is_dedup_candidate(part) is False


# ---------------------------------------------------------------------------
# Unit tests — dedup_key
# ---------------------------------------------------------------------------


def test_dedup_key_same_tool_same_content_matches():
    a = ToolReturnPart(tool_name="file_read", content="x" * 500, tool_call_id="c0")
    b = ToolReturnPart(tool_name="file_read", content="x" * 500, tool_call_id="c1")
    assert dedup_key(a) == dedup_key(b)


def test_dedup_key_different_content_differs():
    a = ToolReturnPart(tool_name="file_read", content="x" * 500, tool_call_id="c0")
    b = ToolReturnPart(tool_name="file_read", content="y" * 500, tool_call_id="c1")
    assert dedup_key(a) != dedup_key(b)


def test_dedup_key_different_tool_same_content_differs():
    a = ToolReturnPart(tool_name="file_read", content="x" * 500, tool_call_id="c0")
    b = ToolReturnPart(tool_name="file_search", content="x" * 500, tool_call_id="c1")
    assert dedup_key(a) != dedup_key(b)


# ---------------------------------------------------------------------------
# Unit tests — build_dedup_part
# ---------------------------------------------------------------------------


def test_build_dedup_part_preserves_tool_call_id_and_tool_name():
    original = ToolReturnPart(tool_name="file_read", content="x" * 500, tool_call_id="old_cid")
    replacement = build_dedup_part(original, latest_call_id="new_cid")
    assert replacement.tool_name == "file_read"
    assert replacement.tool_call_id == "old_cid"


def test_build_dedup_part_content_references_latest_call_id_and_tool_name():
    original = ToolReturnPart(tool_name="file_read", content="x" * 500, tool_call_id="old_cid")
    replacement = build_dedup_part(original, latest_call_id="new_cid")
    assert isinstance(replacement.content, str)
    assert "Duplicate tool output" in replacement.content
    assert "file_read" in replacement.content
    assert "new_cid" in replacement.content


# ---------------------------------------------------------------------------
# Integration tests — dedup_tool_results processor
# ---------------------------------------------------------------------------


_CONTENT_A = "alpha " * 100  # 600 chars, safely above threshold


def test_dedup_identical_file_reads_collapses_earlier_to_back_refs():
    """3 identical file_read returns → 1 full + 2 back-refs; latest is the most recent."""
    msgs: list = []
    for idx in range(3):
        msgs.extend(_tool_turn("file_read", {"path": "foo.py"}, f"fr{idx}", _CONTENT_A))
    msgs.extend(_tail())

    result = dedup_tool_results(_processor_ctx(), msgs)
    returns = _extract_returns(result, "file_read")

    assert len(returns) == 3
    full = [r for r in returns if r.content == _CONTENT_A]
    back_refs = [
        r
        for r in returns
        if isinstance(r.content, str) and r.content.startswith("[Duplicate tool output")
    ]
    assert len(full) == 1
    assert len(back_refs) == 2
    # The latest (fr2) must be the surviving full copy.
    assert full[0].tool_call_id == "fr2"
    # Back-refs must preserve their original call_ids and reference the latest.
    assert {r.tool_call_id for r in back_refs} == {"fr0", "fr1"}
    assert all("call_id=fr2" in r.content for r in back_refs)


def test_dedup_different_content_passes_through():
    """Returns that differ by a single byte must all survive unchanged."""
    msgs: list = []
    for idx in range(3):
        content = _CONTENT_A + f"-suffix-{idx}"
        msgs.extend(_tool_turn("file_read", {"path": "foo.py"}, f"fr{idx}", content))
    msgs.extend(_tail())

    result = dedup_tool_results(_processor_ctx(), msgs)
    returns = _extract_returns(result, "file_read")

    assert len(returns) == 3
    for idx, ret in enumerate(returns):
        assert ret.content == _CONTENT_A + f"-suffix-{idx}"


def test_dedup_protects_last_turn():
    """Identical returns inside the last user turn must pass through intact."""
    msgs: list = []
    # Older turn (will be scanned by dedup but there's no earlier identical — passes through).
    msgs.extend(_tool_turn("file_read", {"path": "foo.py"}, "fr0", _CONTENT_A))
    # Final user turn containing two identical file_read returns — both protected.
    msgs.append(_user_msg("read twice in this turn"))
    for idx in range(2):
        cid = f"tail{idx}"
        msgs.append(
            ModelResponse(parts=[ToolCallPart(tool_name="file_read", args={}, tool_call_id=cid)])
        )
        msgs.append(
            ModelRequest(
                parts=[ToolReturnPart(tool_name="file_read", content=_CONTENT_A, tool_call_id=cid)]
            )
        )
    msgs.append(_assistant_msg("done"))

    result = dedup_tool_results(_processor_ctx(), msgs)

    # Every return from the protected tail keeps full content.
    boundary = _find_last_turn_start(result) or 0
    tail_returns = [
        part
        for msg in result[boundary:]
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(tail_returns) == 2
    assert all(r.content == _CONTENT_A for r in tail_returns)


def test_dedup_skips_content_below_threshold():
    """Identical returns < 200 chars pass through unchanged (marker would not save tokens)."""
    short = "short payload"  # well under 200 chars
    msgs: list = []
    for idx in range(3):
        msgs.extend(_tool_turn("file_read", {"path": "foo.py"}, f"fr{idx}", short))
    msgs.extend(_tail())

    result = dedup_tool_results(_processor_ctx(), msgs)
    returns = _extract_returns(result, "file_read")
    assert len(returns) == 3
    assert all(r.content == short for r in returns)


def test_dedup_skips_non_string_content():
    """Multimodal (non-string) identical returns pass through — cannot be hashed safely."""
    multimodal: object = [{"type": "text", "text": _CONTENT_A}]
    msgs: list = []
    for idx in range(3):
        msgs.extend(_tool_turn("file_read", {"path": "foo.py"}, f"fr{idx}", multimodal))
    msgs.extend(_tail())

    result = dedup_tool_results(_processor_ctx(), msgs)
    returns = _extract_returns(result, "file_read")
    assert len(returns) == 3
    assert all(r.content == multimodal for r in returns)


def test_dedup_skips_non_compactable_tools():
    """save_memory identical returns must pass through (non-compactable — not in dedup gate)."""
    msgs: list = []
    for idx in range(3):
        msgs.extend(_tool_turn("save_memory", {}, f"sm{idx}", _CONTENT_A))
    msgs.extend(_tail())

    result = dedup_tool_results(_processor_ctx(), msgs)
    returns = _extract_returns(result, "save_memory")
    assert len(returns) == 3
    assert all(r.content == _CONTENT_A for r in returns)


def test_dedup_different_tools_same_content_do_not_collapse():
    """Identical content across different tools must NOT dedup (key includes tool name)."""
    msgs: list = []
    msgs.extend(_tool_turn("file_read", {"path": "x"}, "fr0", _CONTENT_A))
    msgs.extend(_tool_turn("file_search", {"pattern": "x", "path": "."}, "fg0", _CONTENT_A))
    msgs.extend(_tool_turn("web_fetch", {"url": "http://x"}, "wf0", _CONTENT_A))
    msgs.extend(_tail())

    result = dedup_tool_results(_processor_ctx(), msgs)
    # Each tool's return should still carry full content.
    for tool_name in ("file_read", "file_search", "web_fetch"):
        returns = _extract_returns(result, tool_name)
        assert len(returns) == 1
        assert returns[0].content == _CONTENT_A


def test_dedup_then_truncate_pipeline_collapses_kept_window():
    """End-to-end: 10 identical file_read → 1 full + 9 one-line stubs.

    Dedup runs first: fr0..fr8 become back-refs pointing to fr9, fr9 keeps
    full content. Truncate then applies recency (COMPACTABLE_KEEP_RECENT=5):
    fr5..fr9 are "kept" (fr5..fr8 remain as back-refs; fr9 remains full);
    fr0..fr4 are older-than-5 and get rewritten to semantic markers on top
    of their current (back-ref) content. Net outcome: exactly one full copy
    survives; every other return is a short stub.
    """
    msgs: list = []
    for idx in range(10):
        msgs.extend(_tool_turn("file_read", {"path": "foo.py"}, f"fr{idx}", _CONTENT_A))
    msgs.extend(_tail())

    ctx = _processor_ctx()
    after_dedup = dedup_tool_results(ctx, msgs)
    after_truncate = truncate_tool_results(ctx, after_dedup)

    returns = _extract_returns(after_truncate, "file_read")
    assert len(returns) == 10

    full = [r for r in returns if r.content == _CONTENT_A]
    assert len(full) == 1
    assert full[0].tool_call_id == "fr9"

    # Every non-latest return is a 1-line stub (dedup back-ref or truncate semantic marker).
    stubs = [r for r in returns if r.content != _CONTENT_A]
    assert len(stubs) == 9
    assert all(isinstance(r.content, str) and len(r.content) < 200 for r in stubs)

    # fr5..fr8 are kept by truncate's recency window → still dedup back-refs.
    # fr0..fr4 are older-than-5 → rewritten to semantic markers on top of back-refs.
    by_call_id = {r.tool_call_id: r for r in returns}
    for cid in ("fr5", "fr6", "fr7", "fr8"):
        assert by_call_id[cid].content.startswith("[Duplicate tool output"), cid
    for cid in ("fr0", "fr1", "fr2", "fr3", "fr4"):
        assert by_call_id[cid].content.startswith("[file_read]"), cid


def test_dedup_empty_messages_returns_unchanged():
    """Degenerate input: empty messages → empty output, no error."""
    result = dedup_tool_results(_processor_ctx(), [])
    assert result == []


def test_dedup_no_user_prompt_returns_unchanged():
    """When boundary is 0 (no UserPromptPart) the processor is a no-op."""
    msgs = [_assistant_msg("only assistant")]
    result = dedup_tool_results(_processor_ctx(), msgs)
    assert result == msgs
