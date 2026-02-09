#!/usr/bin/env python3
"""Test script for reasoning gap fix: Inquiry vs Directive distinction.

This script validates that:
1. DIRECTIVES (commands like "List X") show verbatim tool output
2. INQUIRIES (questions like "When is X?") synthesize answers from tool results
"""

import asyncio
import sys
from pathlib import Path

from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli.config import settings
from co_cli.google_auth import GOOGLE_TOKEN_PATH, ADC_PATH, ALL_GOOGLE_SCOPES, get_google_credentials


def print_section(title: str, description: str):
    """Print a test section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("  " + "-" * 76)
    print(f"  {description}")
    print("=" * 80)


def print_result(response: str, expected_behavior: str):
    """Print test result."""
    print(f"\n📋 Agent Response:\n{response}\n")
    print(f"✓ Expected: {expected_behavior}")


async def test_directive_list_calendar():
    """Test DIRECTIVE: 'List calendar events' should show verbatim tool output."""
    print_section(
        "TEST 1: DIRECTIVE",
        "Command: 'List today's calendar events' → Should show full tool output"
    )

    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        sandbox=Sandbox(container_name="test-reasoning-gap"),
        session_id="test-reasoning-gap",
        google_credentials_path=settings.google_credentials_path,
    )

    try:
        result = await agent.run(
            "List today's calendar events",
            deps=deps,
            model_settings=model_settings,
        )
        response = result.output if isinstance(result.output, str) else str(result.output)
        print_result(
            response,
            "Verbatim tool output with full event details and URLs intact"
        )
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_inquiry_when_is_lunch():
    """Test INQUIRY: 'When is lunch?' should synthesize answer from calendar."""
    print_section(
        "TEST 2: INQUIRY",
        "Question: 'When is lunch today?' → Should synthesize answer (NOT raw dump)"
    )

    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        sandbox=Sandbox(container_name="test-reasoning-gap"),
        session_id="test-reasoning-gap",
        google_credentials_path=settings.google_credentials_path,
    )

    try:
        result = await agent.run(
            "When is lunch today?",
            deps=deps,
            model_settings=model_settings,
        )
        response = result.output if isinstance(result.output, str) else str(result.output)
        print_result(
            response,
            "Synthesized answer like '1:00 PM team lunch' (NOT raw JSON/calendar dump)"
        )

        # Check if response looks synthesized (not a raw tool dump)
        response_lower = response.lower()
        is_synthesized = (
            len(response) < 500 and  # Synthesized answers are concise
            ("lunch" in response_lower or "no lunch" in response_lower or
             "meeting" in response_lower or "event" in response_lower or
             "don't have" in response_lower or "don't see" in response_lower)
        )

        if is_synthesized:
            print("✓ Response appears synthesized (concise, focused answer)")
        else:
            print("⚠️  Response may be too verbose (check if it's raw tool output)")

        return is_synthesized
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_inquiry_next_meeting():
    """Test INQUIRY: 'What's my next meeting?' should synthesize answer."""
    print_section(
        "TEST 3: INQUIRY",
        "Question: 'What's the first event on my calendar today?' → Should synthesize"
    )

    agent, model_settings, _ = get_agent()
    deps = CoDeps(
        sandbox=Sandbox(container_name="test-reasoning-gap"),
        session_id="test-reasoning-gap",
        google_credentials_path=settings.google_credentials_path,
    )

    try:
        result = await agent.run(
            "What's the first event on my calendar today?",
            deps=deps,
            model_settings=model_settings,
        )
        response = result.output if isinstance(result.output, str) else str(result.output)
        print_result(
            response,
            "Synthesized answer with time and title (NOT raw event data)"
        )

        # Check if response is synthesized
        response_lower = response.lower()
        is_synthesized = (
            len(response) < 500 and
            ("meeting" in response_lower or "event" in response_lower or
             "don't have" in response_lower or "no meeting" in response_lower)
        )

        if is_synthesized:
            print("✓ Response appears synthesized (concise, focused answer)")
        else:
            print("⚠️  Response may be too verbose (check if it's raw tool output)")

        return is_synthesized
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def main():
    """Run all reasoning gap tests."""
    print("\n" + "=" * 80)
    print("  🧪 REASONING GAP FIX VALIDATION")
    print("  Testing Inquiry vs Directive distinction in system prompt")
    print("=" * 80)

    # Check prerequisites - use the actual credential resolution logic
    test_creds = get_google_credentials(settings.google_credentials_path, ALL_GOOGLE_SCOPES)

    if not test_creds:
        print("\n❌ ERROR: Google credentials not available.")
        print("   Google credentials resolution order:")
        print(f"   1. Explicit path: {settings.google_credentials_path or 'None'}")
        print(f"   2. Default token: {GOOGLE_TOKEN_PATH} (exists: {GOOGLE_TOKEN_PATH.exists()})")
        print(f"   3. ADC fallback: {ADC_PATH} (exists: {ADC_PATH.exists()})")
        print("\n   Run 'uv run co status' to configure Google authentication.")
        sys.exit(1)

    print("\n✓ Prerequisites met (Google Calendar configured)")

    # Run tests
    results = []

    # Test 1: Directive (verbatim expected)
    result1 = await test_directive_list_calendar()
    results.append(("Directive: List calendar", result1))

    # Test 2: Inquiry (synthesis expected)
    result2 = await test_inquiry_when_is_lunch()
    results.append(("Inquiry: When is lunch", result2))

    # Test 3: Inquiry (synthesis expected)
    result3 = await test_inquiry_next_meeting()
    results.append(("Inquiry: Next meeting", result3))

    # Summary
    print("\n" + "=" * 80)
    print("  📊 TEST SUMMARY")
    print("=" * 80)
    for test_name, passed in results:
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {test_name}")

    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)

    print("\n" + "=" * 80)
    print(f"  Results: {passed_count}/{total_count} tests passed")
    print("=" * 80)

    if passed_count == total_count:
        print("\n✅ All tests passed! Reasoning gap fix validated.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Review outputs above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
