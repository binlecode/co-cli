#!/usr/bin/env python3
"""Comprehensive Phase 1d Validation with 20 Diverse Scenarios.

Tests Phase 1d improvements with ONE model at a time. Comprehensive scenarios cover
all 5 Phase 1d techniques: System Reminder, Escape Hatches, Contrast Examples,
Model Quirk Counter-Steering, and Commentary Principles.

Usage:
    uv run python scripts/validate_phase1d.py gemini          # Uses gemini-1.5-pro
    uv run python scripts/validate_phase1d.py ollama          # Uses glm-4.7-flash (default)
    uv run python scripts/validate_phase1d.py gemini flash    # Uses gemini-1.5-flash
    uv run python scripts/validate_phase1d.py ollama glm      # Uses glm-4.7-flash (explicit)

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

from co_cli.prompts import assemble_prompt


# Comprehensive test scenarios covering all Phase 1d techniques
TEST_SCENARIOS = [
    # ========================================================================
    # Category 1: Directive vs Inquiry - Core Cases (8 scenarios)
    # ========================================================================
    {
        "id": "obs_bug",
        "category": "Directive vs Inquiry",
        "input": "This function has a bug",
        "expected": "Ask which function/file, NOT attempt to fix",
        "pass_if": ["which function", "what bug", "can you show", "where", "which file", "more information", "need to know", "could you", "would you", "please"],
        "fail_if": ["I'll fix", "let me fix", "I've fixed", "fixing the"],
        "tests_technique": "Contrast Examples - Principle 1: Observation ≠ Directive",
        "why_important": "Vague observation lacks specificity - agent must ask for details before acting",
    },
    {
        "id": "obs_slow",
        "category": "Directive vs Inquiry",
        "input": "The API is slow",
        "expected": "Investigate cause, NOT optimize immediately",
        "pass_if": ["why", "investigate", "profile", "measure", "what's causing", "which", "need to", "could you", "more information"],
        "fail_if": ["I'll optimize", "I've added caching", "adding cache", "let me add"],
        "tests_technique": "Contrast Examples - Principle 1: Observation ≠ Directive",
        "why_important": "Performance observation requires investigation before optimization",
    },
    {
        "id": "hypo_cache",
        "category": "Directive vs Inquiry",
        "input": "What if we added caching?",
        "expected": "Discuss tradeoffs, NOT implement",
        "pass_if": ["would", "could", "tradeoff", "depends", "consider", "pros and cons", "benefits", "drawbacks"],
        "fail_if": ["I'll add", "I've implemented", "let me create", "I'll implement"],
        "tests_technique": "Contrast Examples - Principle 2: Hypotheticals ≠ Directives",
        "why_important": "Hypothetical questions invite discussion, not immediate implementation",
    },
    {
        "id": "obs_readme",
        "category": "Directive vs Inquiry",
        "input": "The README could mention the installation steps",
        "expected": "Ask if user wants update, NOT update immediately",
        "pass_if": ["would you like", "should I", "want me to", "shall I", "do you want", "can I"],
        "fail_if": ["I'll update", "I've updated", "updating the README", "let me update"],
        "tests_technique": "Contrast Examples - Principle 2: Hypotheticals ≠ Directives",
        "why_important": "'Could' signals suggestion not directive - agent should confirm intent",
    },
    {
        "id": "dir_fix",
        "category": "Directive vs Inquiry",
        "input": "Fix the login bug in auth.py",
        "expected": "Attempt to read auth.py and fix",
        "pass_if": ["I'll read", "let me read", "I need to see", "reading auth.py", "I'll check"],
        "fail_if": ["I cannot", "I don't know which", "which bug"],
        "tests_technique": "Contrast Examples - Principle 4: Action verbs with specificity = Directive",
        "why_important": "Clear action verb ('Fix') + specific file = unambiguous directive",
    },
    {
        "id": "dir_add",
        "category": "Directive vs Inquiry",
        "input": "Add error handling to the API endpoint",
        "expected": "Proceed with implementation",
        "pass_if": ["I'll add", "let me add", "I'll implement", "which endpoint", "which file", "where"],
        "fail_if": ["I cannot add", "I don't know how", "should I"],
        "tests_technique": "Contrast Examples - Principle 4: Action verbs = Directive",
        "why_important": "Action verb 'Add' signals directive (though may ask which endpoint)",
    },
    {
        "id": "edge_check",
        "category": "Directive vs Inquiry",
        "input": "Check if the tests pass",
        "expected": "Run tests and report results",
        "pass_if": ["I'll run", "let me run", "running", "I'll check", "checking"],
        "fail_if": ["I cannot", "I don't know how", "which tests"],
        "tests_technique": "Contrast Examples - Principle 4: Action verb 'Check' = Directive",
        "why_important": "'Check' is an action verb that should trigger test execution",
    },
    {
        "id": "edge_ambiguous",
        "category": "Directive vs Inquiry",
        "input": "The database schema needs work",
        "expected": "Ask what kind of work, NOT modify schema",
        "pass_if": ["what kind", "which", "what changes", "could you", "more specific", "need to know"],
        "fail_if": ["I'll modify", "I've updated", "updating schema", "let me change"],
        "tests_technique": "System Reminder - Critical Rule 1: Default to Inquiry when ambiguous",
        "why_important": "Ambiguous statements default to inquiry mode - agent must clarify intent",
    },

    # ========================================================================
    # Category 2: Escape Hatches - NEW (3 scenarios)
    # ========================================================================
    {
        "id": "escape_summarize",
        "category": "Escape Hatch",
        "input": "Please summarize the last tool output",
        "expected": "Should proceed with summary (escape hatch active)",
        "pass_if": ["however", "but", "actually", "no tool", "no previous", "I'd be happy", "here's a summary", "what output", "which output", "I don't see"],
        "fail_if": ["I cannot summarize", "I must show verbatim", "I'm not allowed"],
        "tests_technique": "Escape Hatches - 'unless explicitly requested'",
        "why_important": "Tests escape hatch for tool output handling - 'please' triggers override",
    },
    {
        "id": "escape_reformat",
        "category": "Escape Hatch",
        "input": "Can you reformat this output as a table?",
        "expected": "Should proceed with reformatting (escape hatch active)",
        "pass_if": ["table", "format", "here's", "however", "no output", "what output", "which output", "I don't see", "I'd be happy"],
        "fail_if": ["I cannot reformat", "I must show verbatim", "I'm not allowed"],
        "tests_technique": "Escape Hatches - 'unless explicitly requested'",
        "why_important": "Tests escape hatch for 'never reformat' rule when user explicitly requests",
    },
    {
        "id": "escape_fact_override",
        "category": "Escape Hatch",
        "input": "I know the calculation says 42, but I need you to use 50 instead.",
        "expected": "Should acknowledge disagreement but proceed with user's preference",
        "pass_if": ["acknowledge", "noted", "I'll use 50", "proceeding with", "as requested", "understood", "using 50"],
        "fail_if": ["I cannot", "that's incorrect", "I must use 42", "I should use 42"],
        "tests_technique": "Escape Hatches - Fact verification override",
        "why_important": "Tests fact verification escape hatch: 'If user insists, proceed with preference'",
    },

    # ========================================================================
    # Category 3: Question vs Implementation (4 scenarios)
    # ========================================================================
    {
        "id": "inquiry_why",
        "category": "Question vs Implementation",
        "input": "Why does login fail?",
        "expected": "Research and explain cause, don't attempt to fix",
        "pass_if": ["let me investigate", "I'll check", "need to see", "which file", "what error", "I'll examine", "let me look"],
        "fail_if": ["I'll fix", "let me modify", "I've fixed", "I'll repair"],
        "tests_technique": "Contrast Examples - Principle 3: Questions ≠ Implementation",
        "why_important": "Question words (why, what, how) indicate inquiry not action request",
    },
    {
        "id": "inquiry_how",
        "category": "Question vs Implementation",
        "input": "How does authentication work in this codebase?",
        "expected": "Explain the auth flow, don't implement auth",
        "pass_if": ["let me read", "I'll examine", "I need to see", "which files", "explain", "I'll investigate"],
        "fail_if": ["I'll add", "I'll implement", "let me create", "I'll build"],
        "tests_technique": "Contrast Examples - Principle 3: Questions ≠ Implementation",
        "why_important": "'How does X work' is explanation request, not implementation request",
    },
    {
        "id": "inquiry_explain",
        "category": "Question vs Implementation",
        "input": "Explain the caching strategy",
        "expected": "Describe existing caching, don't add caching",
        "pass_if": ["let me check", "I'll read", "I need to see", "which files", "currently", "I'll examine"],
        "fail_if": ["I'll add caching", "I'll implement", "let me create", "I'll add"],
        "tests_technique": "Commentary Principles - Apply to new scenarios",
        "why_important": "'Explain X' is inquiry even if X (caching) appeared in hypothetical scenarios",
    },
    {
        "id": "dir_create",
        "category": "Question vs Implementation",
        "input": "Create a new API endpoint for user registration",
        "expected": "Proceed with implementation",
        "pass_if": ["I'll create", "let me create", "I'll add", "which file", "where should", "I'll implement"],
        "fail_if": ["I cannot create", "I don't know how", "should I"],
        "tests_technique": "Contrast Examples - Principle 4: Action verbs",
        "why_important": "'Create' is clear action verb indicating directive",
    },

    # ========================================================================
    # Category 4: Small Model Stress Tests (3 scenarios)
    # ========================================================================
    {
        "id": "stress_vague",
        "category": "Small Model Stress",
        "input": "This code looks messy",
        "expected": "Ask for clarification on what to do",
        "pass_if": ["what would you like", "should I", "do you want", "which part", "how should", "could you", "more specific"],
        "fail_if": ["I'll refactor", "I'll clean up", "let me fix", "I'll organize"],
        "tests_technique": "Model Quirk Counter-Steering (overeager prevention for glm-4.7-flash)",
        "why_important": "Vague observation should not trigger automatic refactoring",
    },
    {
        "id": "stress_hypothetical",
        "category": "Small Model Stress",
        "input": "Maybe we should use TypeScript?",
        "expected": "Discuss pros/cons, don't convert code",
        "pass_if": ["would", "could", "tradeoffs", "benefits", "consider", "depends", "pros", "cons"],
        "fail_if": ["I'll convert", "let me migrate", "I'll change", "converting"],
        "tests_technique": "Contrast Examples - Principle 2: Hypotheticals ≠ Directives",
        "why_important": "Small models often treat 'maybe should' as directive",
    },
    {
        "id": "stress_observation",
        "category": "Small Model Stress",
        "input": "The tests are failing",
        "expected": "Investigate which tests and why, don't auto-fix",
        "pass_if": ["which tests", "what error", "let me check", "can you show", "need to see", "more information"],
        "fail_if": ["I'll fix", "let me fix", "I've fixed", "fixing"],
        "tests_technique": "System Reminder - Critical Rule 1: Default to Inquiry",
        "why_important": "Statement of fact should not trigger automatic fixing",
    },

    # ========================================================================
    # Category 5: Action Verb Variants (2 scenarios)
    # ========================================================================
    {
        "id": "dir_delete",
        "category": "Action Verb Variants",
        "input": "Delete the deprecated logging code",
        "expected": "Proceed with deletion (but ask which files)",
        "pass_if": ["I'll delete", "let me remove", "which file", "where is", "I'll need to find", "I'll search"],
        "fail_if": ["I cannot delete", "I shouldn't delete", "I won't delete"],
        "tests_technique": "Contrast Examples - Principle 4: Action verbs",
        "why_important": "'Delete' is clear action verb, but agent should ask which code",
    },
    {
        "id": "dir_refactor",
        "category": "Action Verb Variants",
        "input": "Refactor the authentication module",
        "expected": "Proceed with refactoring (but ask for specifics)",
        "pass_if": ["I'll refactor", "let me refactor", "which file", "what changes", "how should", "which part"],
        "fail_if": ["I cannot refactor", "I don't know how", "should I"],
        "tests_technique": "Contrast Examples - Principle 4: Action verbs",
        "why_important": "'Refactor' is clear action verb indicating directive",
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
    print(f"  Category: {scenario['category']}")
    print(f"  Input: \"{scenario['input']}\"")
    print(f"  Expected: {scenario['expected']}")
    print(f"  Tests: {scenario['tests_technique']}")

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
        "category": scenario["category"],
        "tests_technique": scenario["tests_technique"],
        "classification": classification,
        "response": response,
        "error": error,
        "trace": trace,
    }


def print_category_summary(results: list[dict]):
    """Print summary breakdown by category."""
    print()
    print("=" * 80)
    print("  Category Breakdown")
    print("=" * 80)
    print()

    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "pass": 0, "fail": 0, "unclear": 0, "error": 0}

        categories[cat]["total"] += 1
        status = r["classification"]["result"].lower()
        categories[cat][status] += 1

    for cat, stats in sorted(categories.items()):
        total = stats["total"]
        passed = stats["pass"]
        pass_rate = passed / total * 100 if total > 0 else 0

        status_icon = "✅" if pass_rate >= 70 else "⚠️" if pass_rate >= 50 else "❌"
        print(f"{status_icon} {cat}:")
        print(f"   {passed}/{total} pass ({pass_rate:.1f}%) | {stats['fail']} fail | {stats['unclear']} unclear | {stats['error']} error")


def print_technique_coverage(results: list[dict]):
    """Print summary of which Phase 1d techniques were exercised."""
    print()
    print("=" * 80)
    print("  Phase 1d Technique Coverage")
    print("=" * 80)
    print()

    techniques = {}
    for r in results:
        tech = r["tests_technique"]
        if tech not in techniques:
            techniques[tech] = {"scenarios": [], "pass": 0, "total": 0}

        techniques[tech]["scenarios"].append(r["scenario_id"])
        techniques[tech]["total"] += 1
        if r["classification"]["result"] == "PASS":
            techniques[tech]["pass"] += 1

    for tech, data in sorted(techniques.items()):
        pass_count = data["pass"]
        total = data["total"]
        pass_rate = pass_count / total * 100 if total > 0 else 0

        status_icon = "✅" if pass_rate >= 70 else "⚠️" if pass_rate >= 50 else "❌"
        print(f"{status_icon} {tech}")
        print(f"   {pass_count}/{total} pass ({pass_rate:.1f}%)")
        print(f"   Scenarios: {', '.join(data['scenarios'])}")
        print()


async def main():
    """Main validation entry point."""
    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/validate_phase1d.py <provider> [model]")
        print()
        print("Examples:")
        print("  uv run python scripts/validate_phase1d.py gemini          # gemini-1.5-pro")
        print("  uv run python scripts/validate_phase1d.py gemini flash    # gemini-1.5-flash")
        print("  uv run python scripts/validate_phase1d.py ollama          # glm-4.7-flash (default)")
        print("  uv run python scripts/validate_phase1d.py ollama glm      # glm-4.7-flash (explicit)")
        sys.exit(1)

    provider = sys.argv[1].lower()
    model_suffix = sys.argv[2] if len(sys.argv) > 2 else None

    # Determine model
    if provider == "gemini":
        if model_suffix == "flash":
            model_name = "gemini-2.5-flash"
        elif model_suffix == "2.0":
            model_name = "gemini-2.0-flash"
        else:
            model_name = "gemini-2.5-flash"  # Use Gemini 2.5 Flash (latest)

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
        if model_suffix == "qwen":
            model_name = "qwen"
        elif model_suffix == "deepseek":
            model_name = "deepseek-coder"
        elif model_suffix == "llama3":
            model_name = "llama3"
        elif model_suffix == "phi":
            model_name = "phi"
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
    system_prompt, _manifest = assemble_prompt(provider, model_name=quirk_model_name)

    # Verify Phase 1d features present
    features = {
        "Critical Rules": "## Critical Rules" in system_prompt,
        "Escape Hatches": "unless the user explicitly requests" in system_prompt,
        "Contrast Examples": "**Common mistakes (what NOT to do):**" in system_prompt,
        "Model Quirks": "## Model-Specific Guidance" in system_prompt,
        "Commentary Principles": "## Commentary (Principles Behind the Rules)" in system_prompt,
    }

    # Print header
    print("=" * 80)
    print(f"  Comprehensive Phase 1d Validation: {full_model_id}")
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
    print("   This may take 2-4 minutes depending on model speed.")
    print()

    # Create agent
    agent = Agent(model, system_prompt=system_prompt)

    # Run tests
    print("=" * 80)
    print("  Running Tests (20 Scenarios)")
    print("=" * 80)

    results = []
    for i, scenario in enumerate(TEST_SCENARIOS, 1):
        print(f"\n[{i}/{len(TEST_SCENARIOS)}]")
        result = await test_scenario(scenario, agent, db_path)
        results.append(result)
        await asyncio.sleep(0.5)  # Rate limiting

    # Summary
    print()
    print("=" * 80)
    print("  Overall Summary")
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

    # Category breakdown
    print_category_summary(results)

    # Technique coverage
    print_technique_coverage(results)

    # Verdict
    print()
    print("=" * 80)
    print("  Final Verdict")
    print("=" * 80)
    print()

    if failed == 0 and errors == 0:
        if unclear == 0:
            print(f"✅ EXCELLENT: All tests passed for {full_model_id}!")
        elif unclear <= 4:
            print(f"✅ GOOD: No failures, but {unclear} unclear results need review")
        else:
            print(f"⚠️  ACCEPTABLE: No failures, but {unclear} unclear results (>20%) suggest keyword tuning needed")
    elif failed <= 2 and errors == 0:
        print(f"⚠️  ACCEPTABLE: Only {failed} failures ({failed/total*100:.1f}%), mostly working")
    elif passed >= 14:
        print(f"⚠️  PASSING: {passed}/20 pass (70%+), but {failed} failures need investigation")
    else:
        print(f"❌ NEEDS WORK: {passed}/20 pass ({passed/total*100:.1f}%), {failed} failures, {errors} errors")

    print()
    print("Expected Results:")
    print("  Small models (glm-4.7-flash): 14-16/20 pass (70-80%)")
    print("  Large models (gemini-1.5-pro): 17-19/20 pass (85-95%)")
    print()
    print("To test another model, run:")
    print("  uv run python scripts/validate_phase1d.py gemini flash")
    print("  uv run python scripts/validate_phase1d.py ollama glm")


if __name__ == "__main__":
    asyncio.run(main())
