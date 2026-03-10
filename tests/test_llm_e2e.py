import asyncio
import os
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits

from co_cli.agent import get_agent
from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.main import create_deps
from co_cli.tools.memory import _load_memories


@pytest.mark.asyncio
async def test_agent_e2e_gemini():
    """Test a full round-trip to Gemini.
    Requires LLM_PROVIDER=gemini and GEMINI_API_KEY set.
    """
    if os.getenv("LLM_PROVIDER") != "gemini":
        return  # Not targeting Gemini this run

    agent, model_settings, _, _ = get_agent()
    try:
        async with asyncio.timeout(60):
            result = await agent.run("Reply with exactly 'OK'.", model_settings=model_settings)
        assert "OK" in result.output
    except TimeoutError:
        pytest.fail("Gemini E2E timed out after 30s — is the API reachable?")
    except Exception as e:
        pytest.fail(f"Gemini E2E failed: {e}")


def test_gemini_api_key_overrides_env():
    """Regression: settings gemini_api_key must overwrite a pre-existing GEMINI_API_KEY env var."""
    original_env = os.environ.get("GEMINI_API_KEY")
    original_key = settings.gemini_api_key
    original_provider = settings.llm_provider
    try:
        # Simulate a stale env var and a settings-configured key
        os.environ["GEMINI_API_KEY"] = "stale-key-from-env"
        settings.gemini_api_key = "settings-key-wins"
        settings.llm_provider = "gemini"

        get_agent()

        assert os.environ["GEMINI_API_KEY"] == "settings-key-wins"
    finally:
        # Restore original state
        settings.gemini_api_key = original_key
        settings.llm_provider = original_provider
        if original_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = original_env


def _make_deps(session_id: str = "test", personality: str = "finch") -> CoDeps:
    """Create full CoDeps from settings, overriding session-specific fields."""
    deps = create_deps()
    return replace(deps, config=replace(deps.config, session_id=session_id, personality=personality))


def _extract_tool_calls(result) -> list[str]:
    """Extract tool names called during an agent run from conversation messages."""
    names: list[str] = []
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.append(part.tool_name)
    return names


def _conversation_trace(result) -> str:
    """Build a human-readable trace of the full conversation for debugging.

    Shows each message with role, tool calls (with args), tool returns,
    and text parts. Useful in assertion messages to understand what happened.
    """
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
async def test_agent_e2e_ollama():
    """Test a full round-trip to Ollama.
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return  # Not targeting Ollama this run

    agent, model_settings, _, _ = get_agent()
    deps = _make_deps("test-e2e")
    try:
        async with asyncio.timeout(60):
            result = await agent.run(
                "Reply with exactly 'OK'.",
                deps=deps,
                model_settings=model_settings,
            )
        assert "OK" in result.output
    except TimeoutError:
        pytest.fail("Ollama E2E timed out after 30s — is Ollama running?")
    except Exception as e:
        pytest.fail(f"Ollama E2E failed: {e}")


@pytest.mark.asyncio
async def test_ollama_memory_gravity():
    """Model retrieves memory through the agent toolchain and references it.

    The agent-facing memory retrieval tool is search_memories. This test
    verifies that a unique memory can be found and reflected in the model
    response via real tool execution.
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    memory_dir = Path.cwd() / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Write a test memory with a unique keyword no other memory would contain
    test_id = 999
    test_content = "User prefers zygomorphic-widget framework for all integration tests"
    test_file = memory_dir / f"{test_id:03d}-zygomorphic-widget-preference.md"

    fm = {
        "id": test_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": ["preference", "testing"],
    }
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{test_content}\n"
    test_file.write_text(md, encoding="utf-8")

    try:
        # Confirm memory exists before agent run
        entries_before = _load_memories(memory_dir)
        before = [e for e in entries_before if e.id == test_id]
        assert len(before) == 1, f"Test memory {test_id} not found after write"

        agent, model_settings, _, _ = get_agent()
        deps = _make_deps("test-memory-gravity")

        # Ask the model to search memories — unique keyword ensures our file matches.
        async with asyncio.timeout(60):
            result = await agent.run(
                "Search my saved memories for 'zygomorphic-widget'.",
                deps=deps,
                model_settings=model_settings,
            )

        tool_calls = _extract_tool_calls(result)
        assert "search_memories" in tool_calls, (
            f"Model did not call search_memories. Tool calls: {tool_calls}"
        )

        # Verify response references the memory content
        output = result.output if isinstance(result.output, str) else ""
        assert "zygomorphic" in output.lower(), (
            f"Response doesn't reference the test memory: {output!r}"
        )

    finally:
        if test_file.exists():
            test_file.unlink()


@pytest.mark.asyncio
async def test_ollama_autonomous_memory_save():
    """Model detects a preference signal and autonomously calls save_memory.

    The save_memory docstring tells the agent to detect preference/correction/
    decision signals. This test states a clear preference — no mention of
    tools — and verifies the model recognizes it as worth persisting.
    save_memory requires approval → result must be DeferredToolRequests.
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    agent, model_settings, _, _ = get_agent()
    deps = _make_deps("test-autonomous-save")

    # State a clear preference — no mention of save_memory or any tool
    async with asyncio.timeout(60):
        result = await agent.run(
            "I always prefer pytest over unittest for Python testing. "
            "Keep this in mind for our future conversations.",
            deps=deps,
            model_settings=model_settings,
        )

    # save_memory requires approval → must return DeferredToolRequests
    assert isinstance(result.output, DeferredToolRequests), (
        f"Expected DeferredToolRequests (save_memory needs approval), "
        f"got {type(result.output).__name__}. "
        f"Model may not have detected the preference signal."
    )

    # Verify save_memory is among the deferred calls
    calls = list(result.output.approvals)
    tool_names = [c.tool_name for c in calls]
    assert "save_memory" in tool_names, (
        f"save_memory not in deferred tool calls: {tool_names}"
    )

    # Verify the tool captured the preference in its args
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == "save_memory":
                    args_str = str(part.args).lower()
                    assert "pytest" in args_str, (
                        f"save_memory args don't capture the preference: {part.args}"
                    )
                    break



@pytest.mark.asyncio
async def test_ollama_memory_decay():
    """Memory decay triggers when save pushes count above limit.

    Pre-fills an isolated memory directory at the limit, then the model
    saves one more. After approving the deferred save_memory, verifies that
    decay removed/consolidated the oldest memories to stay within the limit.

    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    # Isolated temp project root so we don't touch real memories
    test_root = Path(tempfile.mkdtemp(prefix="co-cli-test-decay-"))
    memory_dir = test_root / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    original_cwd = os.getcwd()

    try:
        os.chdir(test_root)

        # Pre-fill memory dir to the limit (3 memories)
        limit = 3
        for i in range(1, limit + 1):
            fm = {
                "id": i,
                "created": (datetime.now(timezone.utc) - timedelta(days=30 - i)).isoformat(),
                "tags": ["test"],
            }
            md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\nOld test memory number {i}\n"
            (memory_dir / f"{i:03d}-old-test-memory-{i}.md").write_text(md, encoding="utf-8")

        # Verify pre-fill
        entries_before = _load_memories(memory_dir)
        assert len(entries_before) == limit

        agent, model_settings, _, _ = get_agent()
        base = _make_deps("test-decay", "finch")
        deps = replace(base, config=replace(base.config, memory_max_count=limit, memory_dir=memory_dir))

        # Ask model to save a preference — triggers save_memory
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

        # Approve the save — this executes save_memory which triggers decay
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

        # After save + decay, total should stay at or below the limit
        # save_memory adds 1 (total=4), then decay removes oldest to get back to limit
        entries_after = _load_memories(memory_dir)
        assert len(entries_after) <= limit, (
            f"Decay did not trigger: {len(entries_after)} memories "
            f"exceeds limit of {limit}. "
            f"Files: {[e.path.name for e in entries_after]}\n"
            f"Trace:\n{_conversation_trace(result2)}"
        )

        # Verify the new memory was saved (dark mode preference)
        new_content = " ".join(e.content.lower() for e in entries_after)
        assert "dark mode" in new_content or "dark" in new_content, (
            f"New memory not found in remaining entries: "
            f"{[e.content[:50] for e in entries_after]}"
        )

        # Verify at least one old memory was decayed/consolidated
        remaining_ids = {e.id for e in entries_after}
        original_ids = {1, 2, 3}
        decayed_ids = original_ids - remaining_ids
        assert len(decayed_ids) >= 1, (
            f"No original memories were decayed. Remaining IDs: {remaining_ids}"
        )
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_root, ignore_errors=True)
