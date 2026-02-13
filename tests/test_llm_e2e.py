import os
import shutil
import tempfile
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
from co_cli.config import get_settings
from co_cli.deps import CoDeps
from co_cli.sandbox import DockerSandbox, SubprocessBackend
from co_cli.tools.memory import _load_all_memories


@pytest.mark.asyncio
async def test_agent_e2e_gemini():
    """Test a full round-trip to Gemini.
    Requires LLM_PROVIDER=gemini and GEMINI_API_KEY set.
    """
    if os.getenv("LLM_PROVIDER") != "gemini":
        return  # Not targeting Gemini this run

    agent, model_settings, _ = get_agent()
    try:
        result = await agent.run("Reply with exactly 'OK'.", model_settings=model_settings)
        assert "OK" in result.output
    except Exception as e:
        pytest.fail(f"Gemini E2E failed: {e}")


def test_gemini_api_key_overrides_env():
    """Regression: settings gemini_api_key must overwrite a pre-existing GEMINI_API_KEY env var."""
    from co_cli.config import settings

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
    """Create minimal CoDeps for E2E tests."""
    return CoDeps(
        sandbox=SubprocessBackend(),
        session_id=session_id,
        personality=personality,
    )


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

    agent, model_settings, _ = get_agent()
    deps = _make_deps("test-e2e")
    try:
        result = await agent.run(
            "Reply with exactly 'OK'.",
            deps=deps,
            model_settings=model_settings,
        )
        assert "OK" in result.output
    except Exception as e:
        pytest.fail(f"Ollama E2E failed: {e}")


@pytest.mark.asyncio
async def test_ollama_tool_calling():
    """Ollama model selects the correct tool and emits valid args.

    Validates that quantized models can produce structured tool-call JSON.
    This is the gate test for switching between quant levels (e.g. q8_0 → q4_k_m).
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return  # Not targeting Ollama this run

    agent, model_settings, _ = get_agent()
    deps = _make_deps("test-tool-call")

    result = await agent.run(
        "Run this shell command: echo hello",
        deps=deps,
        model_settings=model_settings,
    )

    # run_shell_command requires approval → must return DeferredToolRequests
    assert isinstance(result.output, DeferredToolRequests), (
        f"Expected tool call, got text: {result.output!r}"
    )

    calls = list(result.output.approvals)
    assert len(calls) >= 1, "No tool calls in DeferredToolRequests"
    assert calls[0].tool_name == "run_shell_command", (
        f"Wrong tool selected: {calls[0].tool_name}"
    )


@pytest.mark.asyncio
async def test_ollama_context_tool_personality():
    """LLM calls load_personality tool and response reflects the loaded content.

    Verifies the context tool pipeline: agent sees load_personality in its
    tool list, calls it, receives personality content, and uses it in response.
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return  # Not targeting Ollama this run

    agent, model_settings, tool_names = get_agent()
    assert "load_personality" in tool_names

    deps = _make_deps("test-context-personality", personality="jeff")

    # Ask for information only obtainable via the tool — the role name
    # and piece names are dynamic, so the model must call load_personality.
    result = await agent.run(
        "Call the load_personality tool now. "
        "Report the exact role name and list of pieces_loaded it returns.",
        deps=deps,
        model_settings=model_settings,
    )

    # Verify the agent actually called load_personality during the conversation
    messages = result.all_messages()
    tool_calls_made = []
    tool_returns_received = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls_made.append(part.tool_name)
        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_returns_received.append(part.tool_name)

    assert "load_personality" in tool_calls_made, (
        f"Agent did not call load_personality. Tool calls: {tool_calls_made}"
    )
    assert "load_personality" in tool_returns_received, (
        "load_personality tool return not found in conversation"
    )

    # Verify response reflects the tool's return — role should be "jeff"
    output = result.output if isinstance(result.output, str) else ""
    assert "jeff" in output.lower(), (
        f"Response doesn't mention role 'jeff': {output!r}"
    )


@pytest.mark.asyncio
async def test_ollama_autonomous_personality():
    """Model proactively loads personality without explicit tool instruction.

    The system prompt says "At the start of each session, always load your
    personality character piece to establish your voice." This test sends a
    normal conversational message — no mention of tools — and verifies the
    model follows the system prompt instruction autonomously.
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    agent, model_settings, _ = get_agent()
    deps = _make_deps("test-autonomous-personality", personality="jeff")

    # Normal greeting — no mention of tools or load_personality
    result = await agent.run(
        "Hello, what's your name?",
        deps=deps,
        model_settings=model_settings,
    )

    tool_calls = _extract_tool_calls(result)

    # The model should have proactively loaded personality per system prompt
    assert "load_personality" in tool_calls, (
        f"Model did not autonomously call load_personality. "
        f"Tool calls: {tool_calls}. "
        f"System prompt instructs: 'always load your personality character piece'."
    )

    # Response should reflect Jeff's identity from the loaded personality
    output = result.output if isinstance(result.output, str) else ""
    assert "jeff" in output.lower(), (
        f"Response doesn't reflect Jeff personality: {output!r}"
    )


@pytest.mark.asyncio
async def test_ollama_memory_gravity():
    """recall_memory through the full agent pipeline touches pulled memories.

    Verifies gravity end-to-end: a memory file starts with no updated
    timestamp, the model calls recall_memory and finds it, then the file's
    updated timestamp is set by gravity — proving the touch happened through
    the live agent toolchain, not just a unit-level function call.
    Requires LLM_PROVIDER=ollama and Ollama server running.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    memory_dir = Path.cwd() / ".co-cli" / "knowledge" / "memories"
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
        # Confirm no updated timestamp before agent run
        entries_before = _load_all_memories(memory_dir)
        before = [e for e in entries_before if e.id == test_id]
        assert len(before) == 1, f"Test memory {test_id} not found after write"
        assert before[0].updated is None, "Test memory should have no updated timestamp initially"

        agent, model_settings, _ = get_agent()
        deps = _make_deps("test-memory-gravity")

        # Ask the model to search memories — unique keyword ensures our file matches
        result = await agent.run(
            "Search my saved memories for 'zygomorphic-widget' using recall_memory.",
            deps=deps,
            model_settings=model_settings,
        )

        tool_calls = _extract_tool_calls(result)
        assert "recall_memory" in tool_calls, (
            f"Model did not call recall_memory. Tool calls: {tool_calls}"
        )

        # Verify response references the memory content
        output = result.output if isinstance(result.output, str) else ""
        assert "zygomorphic" in output.lower(), (
            f"Response doesn't reference the test memory: {output!r}"
        )

        # Verify gravity: the memory file should now have an updated timestamp
        entries_after = _load_all_memories(memory_dir)
        after = [e for e in entries_after if e.id == test_id]
        assert len(after) == 1, f"Test memory {test_id} not found after recall"
        assert after[0].updated is not None, (
            "Gravity failed — memory was not touched (updated timestamp is still None)"
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

    agent, model_settings, _ = get_agent()
    deps = _make_deps("test-autonomous-save")

    # State a clear preference — no mention of save_memory or any tool
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
async def test_ollama_web_research_and_save():
    """Model researches a topic online and saves findings to memory.

    Tests the full autonomous multi-step chain:
    1. Model uses web tools (web_search or web_fetch) to research the movie Finch
    2. Model processes the fetched content
    3. Model calls save_memory to persist what it learned

    save_memory requires approval → final result is DeferredToolRequests.
    The conversation history should contain the web tool calls that executed
    before the save was deferred.

    Requires LLM_PROVIDER=ollama, Ollama server running, and internet access.
    """
    if os.getenv("LLM_PROVIDER") != "ollama":
        return

    _settings = get_settings()
    agent, model_settings, _ = get_agent()

    # Full deps with web credentials so web_search can work if Brave key exists
    deps = CoDeps(
        sandbox=SubprocessBackend(),
        session_id="test-web-research",
        personality="finch",
        brave_search_api_key=_settings.brave_search_api_key,
        web_policy=_settings.web_policy,
    )

    # Natural prompt — no mention of specific tools
    result = await agent.run(
        "Go online and learn from Wikipedia about the movie Finch. "
        "Save a short summary of what you learn to memory.",
        deps=deps,
        model_settings=model_settings,
        usage_limits=UsageLimits(request_limit=25),
    )

    # Collect all tool calls from the conversation
    tool_calls = _extract_tool_calls(result)

    # Model should have used at least one web tool
    web_tools_used = [t for t in tool_calls if t in ("web_search", "web_fetch")]
    assert len(web_tools_used) >= 1, (
        f"Model did not use any web tools. Tool calls: {tool_calls}"
    )

    # save_memory should be called → result is DeferredToolRequests
    assert isinstance(result.output, DeferredToolRequests), (
        f"Expected DeferredToolRequests (save_memory needs approval), "
        f"got {type(result.output).__name__}. Tool calls: {tool_calls}"
    )

    # Verify save_memory is in the deferred calls
    calls = list(result.output.approvals)
    deferred_names = [c.tool_name for c in calls]
    assert "save_memory" in deferred_names, (
        f"save_memory not in deferred tool calls: {deferred_names}. "
        f"All tool calls: {tool_calls}"
    )

    # Verify save_memory args contain movie information
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == "save_memory":
                    args_str = str(part.args).lower()
                    assert "finch" in args_str, (
                        f"save_memory args don't mention 'Finch': {part.args}\n"
                        f"Conversation trace:\n{_conversation_trace(result)}"
                    )
                    break

    # Inspect: web tool must have executed before save_memory in the sequence
    web_idx = None
    save_idx = None
    for idx, name in enumerate(tool_calls):
        if name in ("web_search", "web_fetch") and web_idx is None:
            web_idx = idx
        if name == "save_memory":
            save_idx = idx
    assert web_idx is not None and save_idx is not None, (
        f"Missing web or save tool call. Trace:\n{_conversation_trace(result)}"
    )
    assert web_idx < save_idx, (
        f"Web tool call (idx={web_idx}) must precede save_memory (idx={save_idx}). "
        f"Tool sequence: {tool_calls}"
    )


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
    memory_dir = test_root / ".co-cli" / "knowledge" / "memories"
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
        entries_before = _load_all_memories(memory_dir)
        assert len(entries_before) == limit

        agent, model_settings, _ = get_agent()
        deps = CoDeps(
            sandbox=SubprocessBackend(),
            session_id="test-decay",
            personality="finch",
            memory_max_count=limit,
        )

        # Ask model to save a preference — triggers save_memory
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
        entries_after = _load_all_memories(memory_dir)
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
