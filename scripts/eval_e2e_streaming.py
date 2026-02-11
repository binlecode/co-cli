"""E2E test: verify streaming output via run_stream_events().

Two test functions:

  1. test_text_streaming — bare agent (no tools), verifies text deltas arrive
  2. test_markdown_rendering — exercises the Live + Markdown path from
     _stream_agent_run(), verifies Rich renders without errors

Runs against whichever LLM_PROVIDER is configured (gemini or ollama).

Prerequisites:
  - LLM provider configured (gemini_api_key or ollama running)
  - Set LLM_PROVIDER env var if not using the default (gemini)

Usage:
    uv run python scripts/e2e_streaming.py
"""

import asyncio
import io
import sys
import time

from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, TextPartDelta
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from co_cli.config import settings
from co_cli.main import _RENDER_INTERVAL


def _make_bare_agent() -> tuple[Agent, ModelSettings | None]:
    """Create a minimal agent with no tools — pure text streaming test."""
    provider_name = settings.llm_provider.lower()
    model_settings: ModelSettings | None = None

    if provider_name == "gemini":
        api_key = settings.gemini_api_key
        model_name = settings.gemini_model
        if not api_key:
            raise ValueError("gemini_api_key required when llm_provider is 'gemini'")
        import os
        os.environ["GEMINI_API_KEY"] = api_key
        model = f"google-gla:{model_name}"

    elif provider_name == "ollama":
        base_url = f"{settings.ollama_host}/v1"
        provider = OpenAIProvider(base_url=base_url, api_key="ollama")
        model = OpenAIChatModel(model_name=settings.ollama_model, provider=provider)
        model_settings = ModelSettings(temperature=0.7, top_p=1.0, max_tokens=256)

    else:
        raise ValueError(f"Unknown llm_provider: '{provider_name}'")

    agent: Agent[None, str] = Agent(model, output_type=str)
    return agent, model_settings


async def test_text_streaming():
    """Verify that text deltas stream incrementally."""
    agent, model_settings = _make_bare_agent()

    prompt = "Count from 1 to 5, one number per line."
    deltas: list[str] = []
    delta_times: list[float] = []
    result = None

    print(f"[TEST] Provider: {settings.llm_provider}")
    print(f"[TEST] Prompt: {prompt!r}")
    print(f"[TEST] Streaming response:\n")

    t0 = time.monotonic()

    async for event in agent.run_stream_events(
        prompt, model_settings=model_settings,
        usage_limits=UsageLimits(request_limit=5),
    ):
        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            chunk = event.delta.content_delta
            deltas.append(chunk)
            delta_times.append(time.monotonic() - t0)
            print(chunk, end="", flush=True)

        elif isinstance(event, AgentRunResultEvent):
            result = event.result

    print("\n")  # end streamed output

    # ── Assertions ──────────────────────────────────────────────────
    ok = True

    # 1. Got text deltas
    if len(deltas) == 0:
        print("[FAIL] No text deltas received — streaming not working")
        ok = False
    else:
        print(f"[PASS] Received {len(deltas)} text deltas")

    # 2. Multiple deltas (not a single all-at-once chunk)
    if len(deltas) >= 2:
        print(f"[PASS] Multiple deltas — output was progressive ({len(deltas)} chunks)")
    else:
        print(f"[WARN] Only {len(deltas)} delta(s) — provider may not support fine-grained streaming")

    # 3. Got final result
    if result is None:
        print("[FAIL] No AgentRunResultEvent received")
        ok = False
    else:
        print(f"[PASS] AgentRunResultEvent received — result.output is {type(result.output).__name__}")

    # 4. Streamed text matches result.output
    streamed = "".join(deltas)
    if result and isinstance(result.output, str):
        if streamed.strip() == result.output.strip():
            print("[PASS] Streamed text matches result.output exactly")
        elif streamed.strip() in result.output.strip() or result.output.strip() in streamed.strip():
            print("[PASS] Streamed text approximately matches result.output")
        else:
            print("[WARN] Streamed text differs from result.output")
            print(f"       Streamed ({len(streamed)} chars): {streamed[:100]!r}...")
            print(f"       Output   ({len(result.output)} chars): {result.output[:100]!r}...")

    # 5. Timing spread — deltas should arrive over time, not all at once
    if len(delta_times) >= 2:
        span = delta_times[-1] - delta_times[0]
        if span > 0.1:
            print(f"[PASS] Deltas spread over {span:.2f}s — genuinely progressive")
        else:
            print(f"[WARN] All deltas arrived within {span:.3f}s — may be buffered")

    elapsed = time.monotonic() - t0
    print(f"\n[INFO] Total time: {elapsed:.2f}s")

    return ok


async def test_markdown_rendering():
    """Verify that Live + Markdown rendering works end-to-end.

    Exercises the same code path as _stream_agent_run(): accumulate deltas
    into a buffer, render via rich.Live(Markdown(...)), verify no errors and
    non-empty output.
    """
    agent, model_settings = _make_bare_agent()

    # Prompt that produces Markdown-rich output (headings, bold, lists)
    prompt = (
        "List 3 benefits of Python. Use a markdown heading, "
        "then a numbered list with **bold** keywords."
    )

    print(f"[TEST] Provider: {settings.llm_provider}")
    print(f"[TEST] Prompt: {prompt!r}")
    print()

    # Capture Rich output to a string buffer to verify rendering
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, width=80)

    text_buffer = ""
    result = None
    live: Live | None = None
    last_render = 0.0
    render_count = 0
    live_started = False
    live_stopped_cleanly = False

    try:
        async for event in agent.run_stream_events(
            prompt, model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=5),
        ):
            if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                text_buffer += event.delta.content_delta
                now = time.monotonic()
                if now - last_render >= _RENDER_INTERVAL:
                    if live is None:
                        live = Live(
                            Markdown(text_buffer), console=test_console,
                            auto_refresh=False,
                        )
                        live.start()
                        live_started = True
                    else:
                        live.update(Markdown(text_buffer))
                        live.refresh()
                    render_count += 1
                    last_render = now
                continue

            if isinstance(event, AgentRunResultEvent):
                result = event.result

        # Final render
        if live:
            live.update(Markdown(text_buffer))
            live.refresh()
            live.stop()
            live = None
            live_stopped_cleanly = True
            render_count += 1
    finally:
        if live:
            try:
                live.stop()
            except Exception:
                pass

    rendered = buf.getvalue()

    # ── Assertions ──────────────────────────────────────────────────
    ok = True

    # 1. Live was started and stopped cleanly
    if live_started:
        print("[PASS] rich.Live started successfully")
    else:
        print("[FAIL] rich.Live never started — no text deltas received")
        ok = False

    if live_stopped_cleanly:
        print("[PASS] rich.Live stopped cleanly (no crash)")
    else:
        print("[FAIL] rich.Live did not stop cleanly")
        ok = False

    # 2. Multiple renders happened (progressive, not one-shot)
    if render_count >= 2:
        print(f"[PASS] {render_count} Markdown renders — progressive display")
    else:
        print(f"[WARN] Only {render_count} render(s)")

    # 3. Rendered output is non-empty
    if rendered.strip():
        print(f"[PASS] Rendered output is non-empty ({len(rendered)} chars)")
    else:
        print("[FAIL] Rendered output is empty")
        ok = False

    # 4. Markdown was actually parsed (look for ANSI escape codes from styling)
    has_ansi = "\x1b[" in rendered
    if has_ansi:
        print("[PASS] Output contains ANSI styling — Markdown was rendered")
    else:
        print("[WARN] No ANSI codes in output — Markdown may not have rendered styles")

    # 5. Got final result
    if result and isinstance(result.output, str):
        print(f"[PASS] AgentRunResultEvent received — {len(result.output)} chars")
    else:
        print("[FAIL] No valid result")
        ok = False

    # Show a snippet of the rendered output
    print(f"\n[INFO] Rendered output preview (first 300 chars):")
    print(rendered[:300])

    return ok


async def _run_all():
    print("=" * 60)
    print("E2E Streaming Test")
    print("=" * 60)

    all_ok = True

    print("\n--- Test 1: Text Delta Streaming ---\n")
    ok = await test_text_streaming()
    all_ok = all_ok and ok

    print("\n--- Test 2: Markdown Rendering via Live ---\n")
    ok = await test_markdown_rendering()
    all_ok = all_ok and ok

    return all_ok


def main():
    all_ok = asyncio.run(_run_all())

    print()
    if all_ok:
        print("[RESULT] ALL PASSED")
        return 0
    else:
        print("[RESULT] SOME FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
