import asyncio
import os
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.main import create_deps
from co_cli.tools.memory import _load_memories


def _make_deps(session_id: str = "test", personality: str = "finch") -> CoDeps:
    deps = create_deps()
    return replace(deps, config=replace(deps.config, session_id=session_id, personality=personality))


def _extract_tool_calls(result) -> list[str]:
    names: list[str] = []
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.append(part.tool_name)
    return names


def _conversation_trace(result) -> str:
    lines: list[str] = []
    for i, msg in enumerate(result.all_messages()):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content_preview = str(part.content)[:200]
                    lines.append(f"  [{i}] TOOL_RETURN({part.tool_name}): {content_preview}")
                else:
                    content_preview = str(part)[:200]
                    lines.append(f"  [{i}] REQUEST: {content_preview}")
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    args_preview = str(part.args)[:200]
                    lines.append(f"  [{i}] TOOL_CALL: {part.tool_name}({args_preview})")
                elif isinstance(part, TextPart):
                    lines.append(f"  [{i}] TEXT: {part.content[:200]}")
                else:
                    lines.append(f"  [{i}] PART: {type(part).__name__}: {str(part)[:200]}")
    return "\n".join(lines)

@pytest.mark.asyncio
async def test_ollama_memory_decay():
    """Memory decay triggers when save pushes count above limit."""
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    test_root = Path(tempfile.mkdtemp(prefix="co-cli-test-decay-"))
    memory_dir = test_root / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    original_cwd = os.getcwd()

    try:
        os.chdir(test_root)

        limit = 3
        for i in range(1, limit + 1):
            fm = {
                "id": i,
                "created": (datetime.now(timezone.utc) - timedelta(days=30 - i)).isoformat(),
                "tags": ["test"],
            }
            md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\nOld test memory number {i}\n"
            (memory_dir / f"{i:03d}-old-test-memory-{i}.md").write_text(md, encoding="utf-8")

        entries_before = _load_memories(memory_dir)
        assert len(entries_before) == limit

        agent, model_settings, _, _ = get_agent()
        base = _make_deps("test-decay", "finch")
        deps = replace(base, config=replace(base.config, memory_max_count=limit, memory_dir=memory_dir))

        async with asyncio.timeout(60):
            result = await agent.run(
                "I prefer dark mode for all editors. Save this to memory.",
                deps=deps,
                model_settings=model_settings,
                usage_limits=UsageLimits(request_limit=15),
            )

        tool_calls = _extract_tool_calls(result)
        assert isinstance(result.output, DeferredToolRequests), (
            f"Expected DeferredToolRequests for save_memory. "
            f"Tool calls: {tool_calls}\n"
            f"Trace:\n{_conversation_trace(result)}"
        )

        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = True

        result2 = await agent.run(
            deferred_tool_results=approvals,
            message_history=result.all_messages(),
            deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=15),
        )

        entries_after = _load_memories(memory_dir)
        assert len(entries_after) <= limit, (
            f"Decay did not trigger: {len(entries_after)} memories "
            f"exceeds limit of {limit}. "
            f"Files: {[e.path.name for e in entries_after]}\n"
            f"Trace:\n{_conversation_trace(result2)}"
        )

        new_content = " ".join(e.content.lower() for e in entries_after)
        assert "dark mode" in new_content or "dark" in new_content, (
            f"New memory not found in remaining entries: "
            f"{[e.content[:50] for e in entries_after]}"
        )

        remaining_ids = {e.id for e in entries_after}
        original_ids = {1, 2, 3}
        decayed_ids = original_ids - remaining_ids
        assert len(decayed_ids) >= 1, (
            f"No original memories were decayed. Remaining IDs: {remaining_ids}"
        )
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_root, ignore_errors=True)
