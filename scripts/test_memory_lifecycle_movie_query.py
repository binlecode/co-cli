#!/usr/bin/env python3
"""Test: memory lifecycle via movie query — natural prompt, observe behavior.

Runs a live agent conversation with a natural user prompt. The prompt does
NOT explicitly ask Co to save — proactive memory detection should trigger
save_memory autonomously. Exercises:
  1. Co researches the movie online (web_search, web_fetch)
  2. Co synthesizes and presents what it learned
  3. Co proactively detects memory-worthy content and calls save_memory
  4. Optionally: approve the save and show the memory file on disk

Saves a timestamped markdown report to scripts/.

Environment defaults (override via env vars):
  LLM_PROVIDER=ollama
  OLLAMA_MODEL=qwen3:30b-a3b-thinking-2507-q8_0-agentic
  OLLAMA_NUM_CTX=262144

Usage:
    uv run python scripts/test_memory_lifecycle_movie_query.py
    uv run python scripts/test_memory_lifecycle_movie_query.py --approve
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Set env defaults BEFORE co_cli imports — the Settings singleton is created
# at import time via agent.py → config.settings lazy attribute, so env vars
# must be in place before any co_cli module is imported.
_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
    "OLLAMA_NUM_CTX": "262144",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from pydantic_ai import DeferredToolRequests, DeferredToolResults  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits  # noqa: E402

from co_cli.agent import get_agent  # noqa: E402
from co_cli.config import get_settings, WebPolicy  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.sandbox import SubprocessBackend  # noqa: E402


# ---------------------------------------------------------------------------
# Trace printer / collector
# ---------------------------------------------------------------------------


def print_trace(messages: list, lines: list[str] | None = None) -> None:
    """Print a human-readable conversation trace and optionally collect lines."""
    def _out(text: str) -> None:
        print(text)
        if lines is not None:
            lines.append(text)

    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content = str(part.content)
                    preview = content[:500] + "..." if len(content) > 500 else content
                    _out(f"\n  [{i}] TOOL_RETURN({part.tool_name}):")
                    for line in preview.split("\n"):
                        _out(f"       {line}")
                elif isinstance(part, RetryPromptPart):
                    _out(f"\n  [{i}] RETRY({part.tool_name}): {part.content}")
                else:
                    kind = type(part).__name__
                    text = str(part)[:300]
                    _out(f"\n  [{i}] {kind}: {text}")

        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    args = part.args
                    if hasattr(args, "args_dict"):
                        args_str = json.dumps(args.args_dict, indent=2)
                    else:
                        args_str = str(args)
                    preview = args_str[:500] + "..." if len(args_str) > 500 else args_str
                    _out(f"\n  [{i}] TOOL_CALL: {part.tool_name}")
                    for line in preview.split("\n"):
                        _out(f"       {line}")
                elif isinstance(part, TextPart):
                    _out(f"\n  [{i}] TEXT: {part.content[:500]}")


def extract_tool_calls(messages: list) -> list[str]:
    """Extract ordered tool call names from agent messages."""
    names: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.append(part.tool_name)
    return names


def extract_final_text(messages: list) -> str:
    """Extract the last text response from the agent."""
    last_text = ""
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    last_text = part.content
    return last_text


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

_REPORT_DIR = Path(__file__).parent


def write_report(
    *,
    prompt: str,
    provider: str,
    model: str,
    personality: str,
    approve: bool,
    tool_sequence: list[str],
    trace_lines: list[str],
    final_text: str,
    memory_saved: bool,
    memory_file: str | None,
    memory_content: str | None,
    elapsed: float,
    error: str | None = None,
) -> Path:
    """Write a markdown report to scripts/."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = _REPORT_DIR / f"test_memory_lifecycle_movie_query-report.md"

    lines: list[str] = []
    w = lines.append

    w("# Memory Lifecycle Test — Movie Query Report")
    w("")
    w(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"**Provider**: {provider}")
    w(f"**Model**: {model}")
    w(f"**Personality**: {personality}")
    w(f"**Auto-approve**: {approve}")
    w(f"**Elapsed**: {elapsed:.1f}s")
    w("")

    w("## Prompt")
    w("")
    w(f"> {prompt}")
    w("")

    if error:
        w("## Error")
        w("")
        w(f"```\n{error}\n```")
        w("")

    # Tool sequence analysis
    w("## Tool Call Sequence")
    w("")
    if tool_sequence:
        for i, name in enumerate(tool_sequence, 1):
            w(f"{i}. `{name}`")
    else:
        w("(no tool calls)")
    w("")

    # Lifecycle stage analysis
    w("## Lifecycle Stage Analysis")
    w("")
    has_search = any("web_search" in t for t in tool_sequence)
    has_fetch = any("web_fetch" in t for t in tool_sequence)
    has_save = any("save_memory" in t for t in tool_sequence)
    has_recall = any("recall_memory" in t for t in tool_sequence)
    has_personality = any("load_personality" in t or "load_aspect" in t for t in tool_sequence)

    stages = [
        ("Web research (web_search)", has_search),
        ("Content retrieval (web_fetch)", has_fetch),
        ("Memory recall (recall_memory)", has_recall),
        ("Proactive memory save (save_memory)", has_save),
        ("Context loading (load_personality/load_aspect)", has_personality),
    ]
    for label, present in stages:
        status = "PASS" if present else "SKIP"
        w(f"- **{status}**: {label}")
    w("")

    w(f"- **Memory persisted to disk**: {'YES' if memory_saved else 'NO'}")
    w("")

    # Verdict
    w("## Verdict")
    w("")
    if error:
        w("**FAIL** — agent errored before completing the lifecycle.")
    elif has_search and has_save:
        w("**PASS** — full lifecycle: research + proactive memory save.")
    elif has_search and not has_save:
        w("**PARTIAL** — research completed but no proactive memory save triggered.")
    else:
        w("**FAIL** — agent did not perform web research.")
    w("")

    # Final response
    w("## Agent Response (final text)")
    w("")
    if final_text:
        # Truncate for readability
        display = final_text[:2000]
        if len(final_text) > 2000:
            display += f"\n\n... (truncated, {len(final_text)} chars total)"
        w(display)
    else:
        w("(no text response)")
    w("")

    # Memory file
    if memory_file:
        w("## Memory File")
        w("")
        w(f"**Path**: `{memory_file}`")
        w("")
        if memory_content:
            w("```markdown")
            w(memory_content.rstrip())
            w("```")
        w("")

    # Conversation trace
    w("## Conversation Trace")
    w("")
    w("<details>")
    w("<summary>Full trace (click to expand)</summary>")
    w("")
    w("```")
    for tl in trace_lines:
        w(tl)
    w("```")
    w("")
    w("</details>")
    w("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    approve = "--approve" in sys.argv
    settings = get_settings()

    # Web tools auto-execute (no approval prompt) for this demo
    demo_web_policy = WebPolicy(search="allow", fetch="allow")
    agent, model_settings, _ = get_agent(web_policy=demo_web_policy)

    # Resolve provider + model for display
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        model_name = settings.ollama_model
    elif provider == "gemini":
        model_name = settings.gemini_model
    else:
        model_name = provider
    personality = settings.personality or "finch"

    deps = CoDeps(
        sandbox=SubprocessBackend(),
        session_id="demo-movie-lifecycle",
        personality=personality,
        brave_search_api_key=settings.brave_search_api_key,
        web_policy=demo_web_policy,
        web_fetch_allowed_domains=settings.web_fetch_allowed_domains,
        web_fetch_blocked_domains=settings.web_fetch_blocked_domains,
        web_http_max_retries=settings.web_http_max_retries,
        web_http_backoff_base_seconds=settings.web_http_backoff_base_seconds,
        web_http_backoff_max_seconds=settings.web_http_backoff_max_seconds,
        web_http_jitter_ratio=settings.web_http_jitter_ratio,
        memory_max_count=settings.memory_max_count,
        memory_dedup_window_days=settings.memory_dedup_window_days,
        memory_dedup_threshold=settings.memory_dedup_threshold,
        memory_decay_strategy=settings.memory_decay_strategy,
        memory_decay_percentage=settings.memory_decay_percentage,
    )

    prompt = (
        "Go online and learn about the movie Finch, "
        "then tell me about it — make it interesting."
    )

    print("=" * 70)
    print("  Memory Lifecycle Test — Co Learns About a Movie")
    print("=" * 70)
    print(f"\n  Provider:  {provider}")
    print(f"  Model:     {model_name}")
    print(f"  Personality: {personality}")
    print(f"  Prompt:    {prompt}")
    print(f"  Approve save: {approve}")
    print()

    # --- Run agent ---
    t0 = time.monotonic()
    trace_lines: list[str] = []
    error_msg: str | None = None
    all_messages: list = []
    final_text = ""
    memory_saved = False
    memory_file: str | None = None
    memory_content: str | None = None

    try:
        print("--- Conversation Trace ---")
        result = await agent.run(
            prompt,
            deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=25),
        )

        all_messages = result.all_messages()
        print_trace(all_messages, trace_lines)

        # --- Tool call summary ---
        tool_calls = extract_tool_calls(all_messages)

        print("\n--- Tool Call Sequence ---")
        for i, name in enumerate(tool_calls):
            print(f"  {i + 1}. {name}")

        # --- Handle deferred approvals (save_memory) ---
        print("\n--- Result ---")
        if isinstance(result.output, DeferredToolRequests):
            for call in result.output.approvals:
                print(f"  Deferred: {call.tool_name} (id={call.tool_call_id})")

            if approve:
                print("\n--- Approving deferred tools ---")
                approvals = DeferredToolResults()
                for call in result.output.approvals:
                    approvals.approvals[call.tool_call_id] = True

                result2 = await agent.run(
                    deferred_tool_results=approvals,
                    message_history=result.all_messages(),
                    deps=deps,
                    model_settings=model_settings,
                    usage_limits=UsageLimits(request_limit=5),
                )

                all_messages = result2.all_messages()
                # Collect tool calls from the approval round too
                tool_calls.extend(extract_tool_calls(all_messages))

                output = result2.output if isinstance(result2.output, str) else str(result2.output)
                print(f"  Agent: {output[:500]}")
                final_text = output
                memory_saved = True

                # Show memory file on disk
                memory_dir = Path.cwd() / ".co-cli" / "knowledge" / "memories"
                if memory_dir.exists():
                    files = sorted(memory_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if files:
                        newest = files[0]
                        memory_file = str(newest)
                        memory_content = newest.read_text(encoding="utf-8")
                        print(f"\n--- Memory File: {newest.name} ---")
                        print(memory_content)
            else:
                print("\n  (pass --approve to execute the save and show the memory file)")
                final_text = extract_final_text(all_messages)
        else:
            output = result.output[:500] if isinstance(result.output, str) else str(result.output)
            print(f"  Output: {output}")
            final_text = result.output if isinstance(result.output, str) else str(result.output)

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        print(f"\n--- ERROR ---\n  {error_msg}")
        tool_calls = extract_tool_calls(all_messages)

    elapsed = time.monotonic() - t0

    # --- Write report ---
    report_path = write_report(
        prompt=prompt,
        provider=provider,
        model=model_name,
        personality=personality,
        approve=approve,
        tool_sequence=tool_calls,
        trace_lines=trace_lines,
        final_text=final_text,
        memory_saved=memory_saved,
        memory_file=memory_file,
        memory_content=memory_content,
        elapsed=elapsed,
        error=error_msg,
    )

    print(f"\n--- Report saved to {report_path} ---")
    print()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
