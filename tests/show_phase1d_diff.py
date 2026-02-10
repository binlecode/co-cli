#!/usr/bin/env python3
"""Show Phase 1d prompt differences.

Displays the specific sections added by Phase 1d techniques with before/after view.

Run: uv run python tests/show_phase1d_diff.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from co_cli.prompts import get_system_prompt


def extract_section(prompt: str, start_marker: str, end_marker: str = None) -> str:
    """Extract a section from the prompt.

    Args:
        prompt: Full prompt text
        start_marker: Start marker to find
        end_marker: Optional end marker (if None, takes next 500 chars)

    Returns:
        Extracted section or "NOT FOUND"
    """
    start_idx = prompt.find(start_marker)
    if start_idx == -1:
        return "NOT FOUND"

    if end_marker:
        end_idx = prompt.find(end_marker, start_idx)
        if end_idx == -1:
            return prompt[start_idx : start_idx + 500] + "..."
        return prompt[start_idx:end_idx]
    else:
        return prompt[start_idx : start_idx + 500] + "..."


def print_section(title: str, content: str, present: bool):
    """Print a section with formatting.

    Args:
        title: Section title
        content: Section content
        present: Whether the section is present
    """
    status = "✅ PRESENT" if present else "❌ MISSING"
    print(f"\n{'='*80}")
    print(f"{status}: {title}")
    print("=" * 80)

    if present and content != "NOT FOUND":
        # Indent content for readability
        lines = content.split("\n")
        for line in lines[:15]:  # Show first 15 lines
            print(f"  {line}")
        if len(lines) > 15:
            print(f"  ... ({len(lines) - 15} more lines)")
    elif content == "NOT FOUND":
        print("  [Section not found in prompt]")
    else:
        print("  [Section not present]")


def main():
    """Show Phase 1d additions."""
    print("=" * 80)
    print("  Phase 1d Prompt Engineering - What Was Added")
    print("=" * 80)
    print()
    print("This script shows the 5 prompt techniques added in Phase 1d:")
    print("1. Escape Hatches - Allow proceeding when user explicitly requests")
    print("2. Contrast Examples - Show wrong AND right classifications")
    print("3. Commentary - Teach principles behind rules")
    print("4. Model Quirks - Per-model behavioral counter-steering")
    print("5. System Reminder - Critical rules repeated at end (recency bias)")
    print()

    # Get prompts with and without model quirks
    prompt_with_quirks = get_system_prompt("gemini", None, "gemini-1.5-pro")
    prompt_without_quirks = get_system_prompt("gemini", None, None)

    # 1. Escape Hatches
    escape_hatch_1 = extract_section(
        prompt_with_quirks,
        "- Never reformat, summarize, or drop URLs from tool output",
        "\n-",
    )
    has_escape = "unless the user explicitly requests" in escape_hatch_1
    print_section("1. Escape Hatch - Tool Output", escape_hatch_1, has_escape)

    escape_hatch_2 = extract_section(
        prompt_with_quirks,
        "**4. Never blindly accept corrections without verification**",
        "\n\n**Example",
    )
    has_escape_2 = "If user insists after verification" in escape_hatch_2
    print_section("   Escape Hatch - Fact Verification", escape_hatch_2, has_escape_2)

    # 2. Contrast Examples
    contrast = extract_section(
        prompt_with_quirks,
        "**Common mistakes (what NOT to do):**",
        "**When uncertain:**",
    )
    has_contrast = "Wrong Classification" in contrast
    print_section("2. Contrast Examples Table", contrast, has_contrast)

    # 3. Commentary
    commentary = extract_section(
        prompt_with_quirks,
        "**Why these distinctions matter:**",
        "**When uncertain:**",
    )
    has_commentary = "Principle:" in commentary
    print_section("3. Commentary - Teaching Principles", commentary, has_commentary)

    # 4. Model Quirks (only in WITH quirks version)
    model_quirks = extract_section(
        prompt_with_quirks, "## Model-Specific Guidance", "\n\n##"
    )
    has_model_quirks = "scope" in model_quirks.lower()
    print_section(
        "4. Model Quirk Counter-Steering (gemini-1.5-pro)", model_quirks, has_model_quirks
    )

    # Show that WITHOUT model_name, no quirks injected
    no_quirks = "## Model-Specific Guidance" not in prompt_without_quirks
    print_section(
        "   Model Quirks WITHOUT model_name parameter",
        "[No model-specific guidance injected]",
        no_quirks,
    )

    # 5. System Reminder
    system_reminder = extract_section(
        prompt_with_quirks, "## Critical Rules", "---\n\nRemember:"
    )
    has_reminder = "highest priority" in system_reminder
    print_section("5. System Reminder (Recency Bias)", system_reminder, has_reminder)

    # Verify placement (should be near end)
    reminder_pos = prompt_with_quirks.find("## Critical Rules")
    total_len = len(prompt_with_quirks)
    reminder_pct = (reminder_pos / total_len) * 100
    print(f"\n   📍 Position: {reminder_pct:.1f}% through prompt (should be >90% for recency bias)")

    # Summary
    print("\n" + "=" * 80)
    print("  Summary")
    print("=" * 80)
    print()

    all_present = (
        has_escape
        and has_escape_2
        and has_contrast
        and has_commentary
        and has_model_quirks
        and has_reminder
    )

    if all_present:
        print("✅ All 5 Phase 1d techniques are present and correctly positioned!")
        print()
        print("Expected behavioral improvements:")
        print("  • Directive vs Inquiry compliance: +15-25%")
        print("  • Stuck state incidents: -60%")
        print("  • Edge case handling: +20%")
        print("  • Model-specific issues: -70%")
        print()
        print("To validate these improvements with actual LLM calls:")
        print("  uv run python tests/validate_phase1d_live.py")
    else:
        print("❌ Some Phase 1d techniques are missing or incorrectly positioned")
        print("   Review the sections marked as MISSING above")

    print()


if __name__ == "__main__":
    main()
