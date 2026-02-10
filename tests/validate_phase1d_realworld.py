#!/usr/bin/env python3
"""Phase 1d Real-World Validation with Co-CLI Agent + OTEL Logs.

Uses the actual co-cli agent to test Phase 1d improvements with real API calls,
then queries OTEL logs from co-cli.db for detailed debugging information.

Prerequisites:
- GEMINI_API_KEY environment variable set (or LLM_PROVIDER=ollama)
- co-cli.db with OTEL traces

Run: uv run python tests/validate_phase1d_realworld.py

This makes REAL API calls - expect 1-2 minutes runtime and API costs.
"""

import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from co_cli.agent import get_agent
from co_cli.settings import Settings


# Critical test scenarios (most likely to fail without Phase 1d)
CRITICAL_SCENARIOS = [
    {
        "id": "obs_bug",
        "input": "This function has a bug",
        "category": "False Positive Directive",
        "expected": "Should ask which function/file, NOT attempt to modify code",
        "pass_keywords": ["which function", "what bug", "can you show", "where", "need to see"],
        "fail_keywords": ["I'll fix", "I've fixed", "let me modify", "updated the"],
    },
    {
        "id": "hypo_cache",
        "input": "What if we added caching?",
        "category": "Hypothetical Question",
        "expected": "Should discuss tradeoffs, NOT implement caching",
        "pass_keywords": ["would", "could", "tradeoff", "depends", "consider", "option"],
        "fail_keywords": ["I'll add", "I've added", "let me implement", "added caching"],
    },
    {
        "id": "escape_summarize",
        "input": "Summarize the last tool output",
        "category": "Escape Hatch",
        "expected": "Should proceed with summary (escape hatch allows it)",
        "pass_keywords": ["however", "but", "actually", "no tool output", "no previous"],
        "fail_keywords": ["I cannot summarize", "I must show verbatim", "not allowed to"],
    },
    {
        "id": "dir_fix",
        "input": "Fix the authentication bug in login.py",
        "category": "Clear Directive",
        "expected": "Should attempt to read login.py and fix the bug",
        "pass_keywords": ["I'll read", "let me read", "I need to see", "I'll check"],
        "fail_keywords": ["I cannot", "I don't know which", "I'm not sure how"],
    },
]


def query_latest_trace(db_path: Path) -> dict | None:
    """Query the most recent OTEL trace from co-cli.db.

    Args:
        db_path: Path to co-cli.db

    Returns:
        Dict with trace information or None if not found
    """
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get most recent trace
        cursor.execute("""
            SELECT
                name,
                start_time,
                end_time,
                attributes
            FROM spans
            ORDER BY start_time DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            "name": row[0],
            "start_time": datetime.fromtimestamp(row[1] / 1_000_000_000),
            "end_time": datetime.fromtimestamp(row[2] / 1_000_000_000),
            "attributes": row[3],
        }

    except Exception as e:
        print(f"   ⚠️  Error querying OTEL logs: {e}")
        return None


def classify_response(response: str, scenario: dict) -> dict:
    """Classify response as PASS/FAIL/UNCLEAR.

    Args:
        response: Agent response text
        scenario: Test scenario with expected keywords

    Returns:
        Classification dict with result and reasoning
    """
    response_lower = response.lower()

    pass_found = [kw for kw in scenario["pass_keywords"] if kw in response_lower]
    fail_found = [kw for kw in scenario["fail_keywords"] if kw in response_lower]

    if fail_found:
        return {
            "result": "FAIL",
            "reason": f"Found failure indicators: {fail_found[:2]}",
            "confidence": "HIGH" if len(fail_found) >= 2 else "MEDIUM",
        }
    elif pass_found:
        return {
            "result": "PASS",
            "reason": f"Found success indicators: {pass_found[:2]}",
            "confidence": "HIGH" if len(pass_found) >= 2 else "MEDIUM",
        }
    else:
        return {
            "result": "UNCLEAR",
            "reason": "No clear indicators found",
            "confidence": "LOW",
        }


async def test_scenario_with_agent(scenario: dict, use_model_quirks: bool = True) -> dict:
    """Test a scenario with the real co-cli agent.

    Args:
        scenario: Test scenario dict
        use_model_quirks: Whether to pass model_name to enable quirk counter-steering

    Returns:
        Test result with response and classification
    """
    settings = Settings()

    # Determine model_name parameter
    if use_model_quirks:
        if settings.llm_provider == "gemini":
            model_name = settings.gemini_model or "gemini-1.5-pro"
        elif settings.llm_provider == "ollama":
            model_name = settings.ollama_model or "llama3.1"
        else:
            model_name = "gemini-1.5-pro"
    else:
        model_name = None  # Disable model quirk counter-steering

    # Get agent (with or without model_name for prompt)
    # Note: This will use settings.llm_provider to determine which model to call,
    # but model_name controls whether quirk counter-steering is injected in prompt
    agent = get_agent(settings, model_name=model_name)

    # Make real API call
    try:
        result = await agent.run(scenario["input"])
        response = str(result.data) if hasattr(result, "data") else str(result)
        error = None
    except Exception as e:
        response = ""
        error = str(e)

    # Query OTEL logs for debugging
    db_path = Path.home() / ".local" / "share" / "co-cli" / "co-cli.db"
    trace = query_latest_trace(db_path)

    # Classify response
    classification = classify_response(response, scenario) if response else {
        "result": "ERROR",
        "reason": f"API call failed: {error}",
        "confidence": "N/A",
    }

    return {
        "scenario_id": scenario["id"],
        "category": scenario["category"],
        "input": scenario["input"],
        "expected": scenario["expected"],
        "response": response,
        "error": error,
        "classification": classification,
        "trace": trace,
        "has_model_quirks": use_model_quirks,
    }


def print_header(text: str):
    """Print formatted header."""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_result(result: dict, verbose: bool = False):
    """Print test result with optional verbose output."""
    icons = {"PASS": "✅", "FAIL": "❌", "UNCLEAR": "⚠️", "ERROR": "💥"}
    status = result["classification"]["result"]
    icon = icons.get(status, "❓")

    quirks_label = "WITH quirks" if result["has_model_quirks"] else "WITHOUT quirks"
    print(f"\n{icon} {result['scenario_id']} - {status} ({quirks_label})")
    print(f"   Category: {result['category']}")
    print(f"   Input: \"{result['input']}\"")
    print(f"   Expected: {result['expected']}")
    print(f"   Result: {result['classification']['reason']} ({result['classification']['confidence']} confidence)")

    if result.get("error"):
        print(f"   ❌ Error: {result['error']}")

    if verbose and result["response"]:
        print(f"\n   Response (first 300 chars):")
        preview = result["response"][:300]
        if len(result["response"]) > 300:
            preview += "..."
        for line in preview.split("\n"):
            print(f"     {line}")

    if result["trace"]:
        trace = result["trace"]
        duration = (trace["end_time"] - trace["start_time"]).total_seconds()
        print(f"   📊 OTEL: {trace['name']} ({duration:.2f}s)")


async def run_validation():
    """Run full validation with real co-cli agent."""
    print_header("Phase 1d Real-World Validation with Co-CLI Agent")

    print("This validation uses the ACTUAL co-cli agent with REAL API calls.")
    print(f"Testing {len(CRITICAL_SCENARIOS)} critical scenarios that most benefit from Phase 1d.")
    print()

    # Check API access
    settings = Settings()
    print(f"✅ LLM Provider: {settings.llm_provider}")

    if settings.llm_provider == "gemini":
        model = settings.gemini_model or "gemini-1.5-pro"
        print(f"✅ Model: {model}")
        if not os.getenv("GEMINI_API_KEY"):
            print("❌ GEMINI_API_KEY not set!")
            return 1
    elif settings.llm_provider == "ollama":
        model = settings.ollama_model or "llama3.1"
        print(f"✅ Model: {model}")
        print("   Make sure Ollama is running: ollama serve")
    else:
        print(f"❌ Unknown provider: {settings.llm_provider}")
        return 1

    # Check OTEL database
    db_path = Path.home() / ".local" / "share" / "co-cli" / "co-cli.db"
    if db_path.exists():
        print(f"✅ OTEL database found: {db_path}")
    else:
        print(f"⚠️  OTEL database not found: {db_path}")
        print("   Traces will not be available for debugging")

    print()
    print(f"⚠️  This will make {len(CRITICAL_SCENARIOS) * 2} API calls (WITH and WITHOUT quirks)")
    print("   API costs will be incurred. Runtime: ~2-3 minutes.")

    # Run tests WITH model quirks
    print_header("Round 1: WITH Model Quirk Counter-Steering")

    results_with = []
    for i, scenario in enumerate(CRITICAL_SCENARIOS, 1):
        print(f"\n[{i}/{len(CRITICAL_SCENARIOS)}] Testing: {scenario['id']}...")
        result = await test_scenario_with_agent(scenario, use_model_quirks=True)
        results_with.append(result)
        print_result(result, verbose=True)
        await asyncio.sleep(1)  # Rate limiting

    # Run tests WITHOUT model quirks
    print_header("Round 2: WITHOUT Model Quirk Counter-Steering")

    results_without = []
    for i, scenario in enumerate(CRITICAL_SCENARIOS, 1):
        print(f"\n[{i}/{len(CRITICAL_SCENARIOS)}] Testing: {scenario['id']}...")
        result = await test_scenario_with_agent(scenario, use_model_quirks=False)
        results_without.append(result)
        print_result(result, verbose=True)
        await asyncio.sleep(1)  # Rate limiting

    # Comparison
    print_header("Comparison: WITH vs WITHOUT Model Quirks")

    improvements = []
    regressions = []
    unchanged = []

    for with_r, without_r in zip(results_with, results_without):
        with_status = with_r["classification"]["result"]
        without_status = without_r["classification"]["result"]

        if with_status == "PASS" and without_status != "PASS":
            improvements.append(with_r["scenario_id"])
            print(f"📈 {with_r['scenario_id']}: IMPROVED ({without_status} → {with_status})")
        elif with_status != "PASS" and without_status == "PASS":
            regressions.append(with_r["scenario_id"])
            print(f"📉 {with_r['scenario_id']}: REGRESSED ({without_status} → {with_status})")
        else:
            unchanged.append(with_r["scenario_id"])
            print(f"➡️  {with_r['scenario_id']}: Unchanged ({with_status})")

    # Summary
    print_header("Final Summary")

    with_pass = sum(1 for r in results_with if r["classification"]["result"] == "PASS")
    without_pass = sum(1 for r in results_without if r["classification"]["result"] == "PASS")

    total = len(results_with)
    with_pct = (with_pass / total) * 100
    without_pct = (without_pass / total) * 100
    improvement = with_pct - without_pct

    print(f"WITH Model Quirks:    {with_pass}/{total} passed ({with_pct:.1f}%)")
    print(f"WITHOUT Model Quirks: {without_pass}/{total} passed ({without_pct:.1f}%)")
    print()
    print(f"Net Improvement: {improvement:+.1f}%")
    print()
    print(f"Scenarios improved:  {len(improvements)} - {improvements}")
    print(f"Scenarios regressed: {len(regressions)} - {regressions}")
    print(f"Scenarios unchanged: {len(unchanged)} - {unchanged}")
    print()

    # Verdict
    if improvement >= 15:
        print(f"✅ Phase 1d shows {improvement:+.1f}% improvement - SIGNIFICANT IMPACT!")
        print("   (Target was +15-25%, goal achieved)")
        return 0
    elif improvement > 0:
        print(f"⚠️  Phase 1d shows {improvement:+.1f}% improvement - MODERATE IMPACT")
        print("   (Target was +15-25%, below goal but positive)")
        return 0
    elif improvement == 0:
        print("⚠️  Phase 1d shows NO IMPACT - may need tuning")
        return 0
    else:
        print(f"❌ Phase 1d shows {improvement:+.1f}% REGRESSION - needs investigation")
        return 1


def main():
    """Main entry point."""
    try:
        exit_code = asyncio.run(run_validation())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nValidation interrupted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Validation failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
