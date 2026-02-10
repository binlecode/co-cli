#!/usr/bin/env python3
"""Phase 1d Single-Model Validation with Real API Calls + OTEL Logs.

Tests Phase 1d improvements with ONE model at a time. Run multiple times to compare models.

Usage:
    uv run python tests/validate_phase1d.py gemini          # Uses gemini-2.0-flash
    uv run python tests/validate_phase1d.py ollama          # Uses glm-4.7-flash (default)
    uv run python tests/validate_phase1d.py ollama llama31  # Uses llama3.1

Prerequisites:
- GEMINI_API_KEY for Gemini models
- Ollama running locally for Ollama models
"""

import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from pydantic_ai import Agent

from co_cli.prompts import get_system_prompt


# Critical test scenarios that most benefit from Phase 1d
TEST_SCENARIOS = [
    {
        "id": "obs_bug",
        "input": "This function has a bug",
        "expected": "Ask which function/file, NOT attempt to fix",
        "pass_if": ["which function", "what bug", "can you show", "where", "which file"],
        "fail_if": ["I'll fix", "let me fix", "I've fixed", "fixing the"],
    },
    {
        "id": "obs_slow",
        "input": "The API is slow",
        "expected": "Investigate cause, NOT optimize immediately",
        "pass_if": ["why", "investigate", "profile", "measure", "what's causing"],
        "fail_if": ["I'll optimize", "I've added caching", "adding cache"],
    },
    {
        "id": "hypo_cache",
        "input": "What if we added caching?",
        "expected": "Discuss tradeoffs, NOT implement",
        "pass_if": ["would", "could", "tradeoff", "depends", "consider", "pros and cons"],
        "fail_if": ["I'll add", "I've implemented", "let me create", "adding caching"],
    },
    {
        "id": "obs_readme",
        "input": "The README could mention the installation steps",
        "expected": "Ask if user wants update, NOT update immediately",
        "pass_if": ["would you like", "should I", "want me to", "shall I"],
        "fail_if": ["I'll update", "I've updated", "updating the README"],
    },
    {
        "id": "dir_fix",
        "input": "Fix the login bug in auth.py",
        "expected": "Attempt to read auth.py and fix",
        "pass_if": ["I'll read", "let me read", "I need to see", "reading auth.py"],
        "fail_if": ["I cannot", "I don't know which", "which bug"],
    },
    {
        "id": "dir_add",
        "input": "Add error handling to the API endpoint",
        "expected": "Proceed with implementation",
        "pass_if": ["I'll add", "let me add", "I'll implement", "which endpoint"],
        "fail_if": ["I cannot add", "I don't know how"],
    },
]


def query_latest_trace(db_path: Path) -> dict | None:
    """Query most recent OTEL trace from co-cli.db."""
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name, start_time, end_time, attributes
            FROM spans
            ORDER BY start_time DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        start = datetime.fromtimestamp(row[1] / 1_000_000_000)
        end = datetime.fromtimestamp(row[2] / 1_000_000_000)
        duration = (end - start).total_seconds()

        return {
            "name": row[0],
            "duration": duration,
            "attributes": row[3] if row[3] else "{}",
        }
    except Exception as e:
        return {"error": str(e)}


def classify_response(response: str, scenario: dict) -> dict:
    """Classify response as PASS/FAIL/UNCLEAR."""
    resp_lower = response.lower()

    pass_found = [kw for kw in scenario["pass_if"] if kw in resp_lower]
    fail_found = [kw for kw in scenario["fail_if"] if kw in resp_lower]

    if fail_found:
        return {
            "result": "FAIL",
            "reason": f"Found failure indicators: {fail_found[:2]}",
            "indicators": {"pass": pass_found, "fail": fail_found},
        }
    elif pass_found:
        return {
            "result": "PASS",
            "reason": f"Found success indicators: {pass_found[:2]}",
            "indicators": {"pass": pass_found, "fail": fail_found},
        }
    else:
        return {
            "result": "UNCLEAR",
            "reason": "No clear indicators - needs manual review",
            "indicators": {"pass": [], "fail": []},
        }


async def test_scenario(scenario: dict, agent: Agent, db_path: Path) -> dict:
    """Test one scenario with real API call."""
    print(f"\n  Testing: {scenario['id']}...")
    print(f"  Input: \"{scenario['input']}\"")
    print(f"  Expected: {scenario['expected']}")

    # Make real API call
    try:
        result = await agent.run(scenario["input"])
        response = str(result.data) if hasattr(result, "data") else str(result)
        error = None
    except Exception as e:
        response = ""
        error = str(e)

    # Query OTEL logs
    trace = query_latest_trace(db_path)

    # Classify response
    if error:
        classification = {"result": "ERROR", "reason": f"API error: {error}"}
    else:
        classification = classify_response(response, scenario)

    # Print result
    icons = {"PASS": "✅", "FAIL": "❌", "UNCLEAR": "⚠️", "ERROR": "💥"}
    icon = icons[classification["result"]]
    print(f"  {icon} {classification['result']}: {classification['reason']}")

    if trace and "error" not in trace:
        print(f"  📊 OTEL: {trace['name']} ({trace['duration']:.2f}s)")

    if response and len(response) < 200:
        print(f"  Response: {response}")
    elif response:
        print(f"  Response: {response[:200]}...")

    return {
        "scenario_id": scenario["id"],
        "classification": classification,
        "response": response,
        "error": error,
        "trace": trace,
    }


async def main():
    """Main validation entry point."""
    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: uv run python tests/validate_phase1d.py <provider> [model]")
        print()
        print("Examples:")
        print("  uv run python tests/validate_phase1d.py gemini          # gemini-2.0-flash")
        print("  uv run python tests/validate_phase1d.py ollama          # glm-4.7-flash")
        print("  uv run python tests/validate_phase1d.py ollama llama31  # llama3.1")
        sys.exit(1)

    provider = sys.argv[1].lower()
    model_suffix = sys.argv[2] if len(sys.argv) > 2 else None

    # Determine model
    if provider == "gemini":
        # Default to current stable model
        model_name = "gemini-2.0-flash"

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("❌ GEMINI_API_KEY not set!")
            print("   export GEMINI_API_KEY='your-key-here'")
            sys.exit(1)

        # Set env var for pydantic-ai
        os.environ["GEMINI_API_KEY"] = api_key
        model = f"google-gla:{model_name}"
        full_model_id = f"gemini:{model_name}"

    elif provider == "ollama":
        if model_suffix == "llama31":
            model_name = "llama3.1"
        elif model_suffix == "glm":
            model_name = "glm-4.7-flash:q8_0"  # Use the actual quantized model
        else:
            # Default to the model co-cli actually uses
            model_name = "glm-4.7-flash:q8_0"

        # For Ollama, we need to set the base URL
        os.environ["OPENAI_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["OPENAI_API_KEY"] = "ollama"  # Dummy key for Ollama
        model = f"openai:{model_name}"
        # Strip quantization tag for quirk lookup (glm-4.7-flash:q8_0 → glm-4.7-flash)
        base_model_name = model_name.split(":")[0]
        full_model_id = f"ollama:{base_model_name}"

    else:
        print(f"❌ Unknown provider: {provider}")
        print("   Use 'gemini' or 'ollama'")
        sys.exit(1)

    # Get system prompt WITH Phase 1d features
    # For quirk lookup, use base model name (strip quantization tags)
    quirk_model_name = model_name.split(":")[0] if provider == "ollama" else model_name
    system_prompt = get_system_prompt(provider, None, quirk_model_name)

    # Verify Phase 1d features present
    features = {
        "Critical Rules": "## Critical Rules" in system_prompt,
        "Escape Hatches": "unless the user explicitly requests" in system_prompt,
        "Contrast Examples": "**Common mistakes (what NOT to do):**" in system_prompt,
        "Model Quirks": "## Model-Specific Guidance" in system_prompt,
    }

    # Print header
    print("=" * 80)
    print(f"  Phase 1d Validation: {full_model_id}")
    print("=" * 80)
    print()
    print(f"Provider: {provider}")
    print(f"Model: {model_name}")
    print()
    print("Phase 1d Features:")
    for feature, present in features.items():
        status = "✅" if present else "❌"
        print(f"  {status} {feature}")

    has_quirks = features["Model Quirks"]
    if has_quirks:
        print(f"\n✅ Model quirk counter-steering IS active for {full_model_id}")
    else:
        print(f"\n⚠️  No model quirk counter-steering for {full_model_id}")

    # OTEL database check
    db_path = Path.home() / ".local" / "share" / "co-cli" / "co-cli.db"
    if db_path.exists():
        print(f"✅ OTEL database found: {db_path}")
    else:
        print(f"⚠️  OTEL database not found (traces won't be available)")

    print()
    print(f"⚠️  Making {len(TEST_SCENARIOS)} real API calls...")
    print("   This may incur costs and take 1-2 minutes.")
    print()

    # Create agent
    agent = Agent(model, system_prompt=system_prompt)

    # Run tests
    print("=" * 80)
    print("  Running Tests")
    print("=" * 80)

    results = []
    for scenario in TEST_SCENARIOS:
        result = await test_scenario(scenario, agent, db_path)
        results.append(result)
        await asyncio.sleep(0.5)  # Rate limiting

    # Summary
    print()
    print("=" * 80)
    print("  Summary")
    print("=" * 80)
    print()

    passed = sum(1 for r in results if r["classification"]["result"] == "PASS")
    failed = sum(1 for r in results if r["classification"]["result"] == "FAIL")
    unclear = sum(1 for r in results if r["classification"]["result"] == "UNCLEAR")
    errors = sum(1 for r in results if r["classification"]["result"] == "ERROR")

    total = len(results)
    print(f"Results: {passed} pass, {failed} fail, {unclear} unclear, {errors} errors")
    print(f"Pass rate: {passed}/{total} ({passed/total*100:.1f}%)")
    print()

    # Breakdown by result
    for status in ["FAIL", "UNCLEAR", "ERROR"]:
        scenarios = [r["scenario_id"] for r in results if r["classification"]["result"] == status]
        if scenarios:
            print(f"{status}: {scenarios}")

    print()

    # Verdict
    if failed == 0 and errors == 0:
        if unclear == 0:
            print(f"✅ EXCELLENT: All tests passed for {full_model_id}!")
        else:
            print(f"✅ GOOD: No failures, but {unclear} unclear results need review")
    elif failed <= 1 and errors == 0:
        print(f"⚠️  ACCEPTABLE: Only {failed} failure, mostly working")
    else:
        print(f"❌ NEEDS WORK: {failed} failures, {errors} errors")

    print()
    print("To test another model, run:")
    print("  uv run python tests/validate_phase1d.py gemini")
    print("  uv run python tests/validate_phase1d.py ollama llama31")


if __name__ == "__main__":
    asyncio.run(main())
