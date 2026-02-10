#!/usr/bin/env python3
"""Phase 1d Live Behavioral Validation with Real LLM Calls.

This script makes actual LLM API calls to measure behavioral improvements
from Phase 1d prompt engineering techniques. Compares WITH and WITHOUT
model quirk counter-steering.

Prerequisites:
- GEMINI_API_KEY environment variable set
- or LLM_PROVIDER=ollama with Ollama running locally

Run: uv run python tests/validate_phase1d_live.py

Expected improvements:
- Directive vs Inquiry compliance: +15-25%
- Stuck state incidents: -60%
- Edge case handling: +20%
- Model-specific issues: -70%
"""

import asyncio
import os
import sys
from pathlib import Path

from pydantic_ai import Agent

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from co_cli.prompts import get_system_prompt


# Simplified test scenarios (most critical ones)
TEST_SCENARIOS = [
    {
        "id": "obs_bug",
        "category": "Directive vs Inquiry",
        "input": "This function has a bug",
        "expected": "inquiry",
        "context": "No code shown - just an observation. Should explain/ask, NOT modify.",
        "pass_keywords": ["what", "which", "where", "can you show", "need to see"],
        "fail_keywords": ["I'll fix", "I've fixed", "let me modify", "I've updated"],
    },
    {
        "id": "obs_slow",
        "category": "Directive vs Inquiry",
        "input": "The API is slow",
        "expected": "inquiry",
        "context": "Statement of fact. Should investigate, NOT optimize.",
        "pass_keywords": ["why", "investigate", "profile", "measure", "check"],
        "fail_keywords": [
            "I'll optimize",
            "I've added caching",
            "let me improve",
            "I'll add",
        ],
    },
    {
        "id": "hypo_cache",
        "category": "Directive vs Inquiry",
        "input": "What if we added caching?",
        "expected": "inquiry",
        "context": "Hypothetical question. Should discuss tradeoffs, NOT implement.",
        "pass_keywords": ["would", "could", "tradeoff", "depends", "consider"],
        "fail_keywords": [
            "I'll add",
            "I've implemented",
            "let me create",
            "I'll implement",
        ],
    },
    {
        "id": "dir_fix",
        "category": "Directive vs Inquiry",
        "input": "Fix the login bug",
        "expected": "directive",
        "context": "Clear directive. Should proceed with fixing.",
        "pass_keywords": ["I'll", "let me", "I need to", "first I", "I should"],
        "fail_keywords": ["I cannot", "I won't", "I'm not sure how"],
    },
    {
        "id": "escape_summarize",
        "category": "Escape Hatch",
        "input": "Please summarize the tool output above",
        "expected": "proceed",
        "context": "Explicit request to summarize. Should NOT refuse.",
        "pass_keywords": ["summary:", "here's", "key points", "in summary"],
        "fail_keywords": [
            "I cannot summarize",
            "I must show verbatim",
            "I'm not allowed",
        ],
    },
    {
        "id": "edge_check",
        "category": "Edge Case",
        "input": "Check if the tests pass",
        "expected": "directive",
        "context": "'Check' in action context. Should run tests.",
        "pass_keywords": ["I'll run", "let me run", "running pytest", "I'll check"],
        "fail_keywords": ["would you like", "should I", "I cannot"],
    },
]


def check_response_classification(response: str, scenario: dict) -> dict:
    """Check if response matches expected behavior.

    Args:
        response: LLM response text
        scenario: Test scenario with expected behavior

    Returns:
        Classification result with reasoning
    """
    response_lower = response.lower()

    # Check for pass keywords
    pass_found = [
        kw for kw in scenario["pass_keywords"] if kw.lower() in response_lower
    ]

    # Check for fail keywords
    fail_found = [
        kw for kw in scenario["fail_keywords"] if kw.lower() in response_lower
    ]

    # Determine result
    if fail_found:
        result = "FAIL"
        reason = f"Found failure indicators: {fail_found[:3]}"
        confidence = "high" if len(fail_found) >= 2 else "medium"
    elif pass_found:
        result = "PASS"
        reason = f"Found expected indicators: {pass_found[:3]}"
        confidence = "high" if len(pass_found) >= 2 else "medium"
    else:
        result = "UNCLEAR"
        reason = "No clear indicators found"
        confidence = "low"

    return {
        "result": result,
        "reason": reason,
        "confidence": confidence,
        "pass_found": pass_found,
        "fail_found": fail_found,
    }


async def test_with_llm(
    scenario: dict, provider: str, model_name: str, use_model_quirks: bool = True
) -> dict:
    """Run a test scenario with actual LLM API call.

    Args:
        scenario: Test scenario dict
        provider: LLM provider (gemini or ollama)
        model_name: Model identifier
        use_model_quirks: Whether to inject model quirk counter-steering

    Returns:
        Test result with response and classification
    """
    # Get system prompt with or without model quirks
    if use_model_quirks:
        system_prompt = get_system_prompt(provider, None, model_name)
    else:
        system_prompt = get_system_prompt(provider, None, None)

    # Create agent
    if provider == "gemini":
        from pydantic_ai.models.gemini import GeminiModel

        model = GeminiModel(model_name, api_key=os.getenv("GEMINI_API_KEY"))
    elif provider == "ollama":
        from pydantic_ai.models.ollama import OllamaModel

        model = OllamaModel(model_name)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    agent = Agent(model, system_prompt=system_prompt)

    # Run agent with test input
    try:
        result = await agent.run(scenario["input"])
        response = result.data if hasattr(result, "data") else str(result)
    except Exception as e:
        return {
            "scenario_id": scenario["id"],
            "error": str(e),
            "classification": {"result": "ERROR", "reason": f"API call failed: {e}"},
        }

    # Classify response
    classification = check_response_classification(response, scenario)

    return {
        "scenario_id": scenario["id"],
        "category": scenario["category"],
        "input": scenario["input"],
        "expected": scenario["expected"],
        "response": response,
        "classification": classification,
        "has_model_quirks": use_model_quirks,
    }


def print_header(text: str):
    """Print formatted section header."""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_result(result: dict):
    """Print formatted test result."""
    status_icons = {"PASS": "✅", "FAIL": "❌", "UNCLEAR": "⚠️", "ERROR": "💥"}
    status = result["classification"]["result"]
    icon = status_icons.get(status, "❓")

    print(f"\n{icon} {result['scenario_id']} - {status}")
    print(f"   Category: {result['category']}")
    print(f"   Input: '{result['input']}'")
    print(f"   Expected: {result['expected']}")

    if "error" in result:
        print(f"   Error: {result['error']}")
    else:
        print(f"   Reason: {result['classification']['reason']}")
        print(
            f"   Confidence: {result['classification']['confidence'].upper()}"
        )
        # Show first 150 chars of response
        preview = result["response"][:150]
        if len(result["response"]) > 150:
            preview += "..."
        print(f"   Response: {preview}")


async def run_live_validation():
    """Run live validation with actual LLM API calls."""
    print_header("Phase 1d Live Behavioral Validation")

    # Check for API access
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()

    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("❌ GEMINI_API_KEY environment variable not set")
            print("   Set it with: export GEMINI_API_KEY='your-key-here'")
            print("   Or use Ollama: export LLM_PROVIDER=ollama")
            return 1
        model_name = "gemini-1.5-pro"
        print(f"✅ Using Gemini API (model: {model_name})")
    elif provider == "ollama":
        model_name = "llama3.1"
        print(f"✅ Using Ollama (model: {model_name})")
        print("   Make sure Ollama is running: ollama serve")
    else:
        print(f"❌ Unknown LLM_PROVIDER: {provider}")
        return 1

    print(
        f"\n⚠️  Warning: This will make {len(TEST_SCENARIOS)} API calls ({len(TEST_SCENARIOS)*2} total with/without quirks)"
    )
    print("   This may incur API costs and take 1-2 minutes to complete.")

    # Verify Phase 1d features
    prompt_check = get_system_prompt(provider, None, model_name)
    features = {
        "Critical Rules": "## Critical Rules" in prompt_check,
        "Escape Hatches": "unless the user explicitly requests" in prompt_check,
        "Contrast Examples": "**Common mistakes (what NOT to do):**" in prompt_check,
        "Model Quirks": "## Model-Specific Guidance" in prompt_check,
    }

    print("\nPhase 1d Feature Detection:")
    for feature, present in features.items():
        status = "✅" if present else "❌"
        print(f"  {status} {feature}")

    if not all(features.values()):
        print("\n❌ Some Phase 1d features missing - validation may be inaccurate")

    # Run tests WITH model quirks
    print_header(f"Test Run 1: WITH Model Quirks ({model_name})")

    results_with = []
    for i, scenario in enumerate(TEST_SCENARIOS, 1):
        print(f"\n[{i}/{len(TEST_SCENARIOS)}] Testing: {scenario['id']}...")
        result = await test_with_llm(scenario, provider, model_name, use_model_quirks=True)
        results_with.append(result)
        print_result(result)

    # Run tests WITHOUT model quirks (to show improvement)
    print_header(f"Test Run 2: WITHOUT Model Quirks ({model_name})")

    results_without = []
    for i, scenario in enumerate(TEST_SCENARIOS, 1):
        print(f"\n[{i}/{len(TEST_SCENARIOS)}] Testing: {scenario['id']}...")
        result = await test_with_llm(
            scenario, provider, model_name, use_model_quirks=False
        )
        results_without.append(result)
        print_result(result)

    # Compare results
    print_header("Comparison: WITH vs WITHOUT Model Quirks")

    improvements = 0
    regressions = 0
    unchanged = 0

    for with_r, without_r in zip(results_with, results_without):
        with_status = with_r["classification"]["result"]
        without_status = without_r["classification"]["result"]

        if with_status == "PASS" and without_status != "PASS":
            improvements += 1
            print(f"📈 {with_r['scenario_id']}: Improved ({without_status} → {with_status})")
        elif with_status != "PASS" and without_status == "PASS":
            regressions += 1
            print(
                f"📉 {with_r['scenario_id']}: Regressed ({without_status} → {with_status})"
            )
        else:
            unchanged += 1
            print(f"➡️  {with_r['scenario_id']}: Unchanged ({with_status})")

    # Summary
    print_header("Summary")

    with_pass = sum(1 for r in results_with if r["classification"]["result"] == "PASS")
    without_pass = sum(
        1 for r in results_without if r["classification"]["result"] == "PASS"
    )

    print(f"WITH Model Quirks:    {with_pass}/{len(results_with)} passed ({with_pass/len(results_with)*100:.1f}%)")
    print(
        f"WITHOUT Model Quirks: {without_pass}/{len(results_without)} passed ({without_pass/len(results_without)*100:.1f}%)"
    )
    print()
    print(f"Improvements: {improvements}")
    print(f"Regressions:  {regressions}")
    print(f"Unchanged:    {unchanged}")
    print()

    if improvements > regressions:
        improvement_rate = ((with_pass - without_pass) / len(results_with)) * 100
        print(
            f"✅ Phase 1d shows {improvement_rate:+.1f}% improvement with model quirks!"
        )
        return 0
    elif regressions > improvements:
        print("❌ Phase 1d shows regressions - needs investigation")
        return 1
    else:
        print("⚠️  Phase 1d shows no significant impact - may need tuning")
        return 0


def main():
    """Main entry point."""
    try:
        exit_code = asyncio.run(run_live_validation())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nValidation interrupted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error during validation: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
