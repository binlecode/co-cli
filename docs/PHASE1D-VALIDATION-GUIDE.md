# Phase 1d Validation Guide

## Overview

This guide documents the comprehensive 20-scenario validation suite for Phase 1d prompt engineering improvements. The validation suite exercises all 5 Phase 1d techniques with diverse scenarios designed to reveal prompt weaknesses.

## Quick Start

```bash
# Run with Ollama (glm-4.7-flash - recommended for testing)
uv run python scripts/validate_phase1d.py ollama

# Run with Gemini (larger model, higher pass rate expected)
uv run python scripts/validate_phase1d.py gemini

# Run with specific models
uv run python scripts/validate_phase1d.py gemini flash
uv run python scripts/validate_phase1d.py ollama glm
```

## Test Suite Design

### Goals

1. **Wide diversity**: Cover all Phase 1d techniques with varied scenarios
2. **Small model stress**: Use glm-4.7-flash to reveal weaknesses (small models are less forgiving)
3. **Edge case coverage**: Test ambiguous inputs, escape hatches, and boundary conditions
4. **Technique validation**: Ensure each Phase 1d technique is properly exercised

### Phase 1d Techniques Covered

| Technique | Description | Scenarios |
|-----------|-------------|-----------|
| **System Reminder** | Critical rules emphasized at top | obs_bug, edge_ambiguous, stress_observation |
| **Escape Hatches** | Override rules when explicitly requested | escape_summarize, escape_reformat, escape_fact_override |
| **Contrast Examples** | Show what NOT to do alongside rules | obs_bug, obs_slow, hypo_cache, obs_readme, dir_fix, dir_add, edge_check, inquiry_why, inquiry_how, dir_create, stress_hypothetical, dir_delete, dir_refactor |
| **Model Quirk Counter-Steering** | Model-specific guidance (e.g., glm-4.7-flash overeagerness) | stress_vague, stress_hypothetical, stress_observation |
| **Commentary Principles** | Explain the "why" behind rules | inquiry_explain |

## 20 Validation Scenarios

### Category 1: Directive vs Inquiry (8 scenarios)

Core scenarios testing the fundamental distinction between observations and directives.

| ID | Input | Expected Behavior | Technique Tested |
|----|-------|-------------------|------------------|
| `obs_bug` | "This function has a bug" | Ask which function/file | Contrast Examples - Observation ≠ Directive |
| `obs_slow` | "The API is slow" | Investigate cause, don't optimize | Contrast Examples - Observation ≠ Directive |
| `hypo_cache` | "What if we added caching?" | Discuss tradeoffs, don't implement | Contrast Examples - Hypotheticals ≠ Directives |
| `obs_readme` | "The README could mention..." | Ask if user wants update | Contrast Examples - Hypotheticals ≠ Directives |
| `dir_fix` | "Fix the login bug in auth.py" | Read auth.py and fix | Contrast Examples - Action verb + specificity |
| `dir_add` | "Add error handling to API" | Proceed with implementation | Contrast Examples - Action verbs |
| `edge_check` | "Check if the tests pass" | Run tests and report | Contrast Examples - 'Check' is action verb |
| `edge_ambiguous` | "The database schema needs work" | Ask what kind of work | System Reminder - Default to inquiry |

**Why These Matter:**
- Test the core Phase 1d principle: observations/suggestions require clarification, directives with action verbs proceed
- Edge cases (`edge_check`, `edge_ambiguous`) test boundary conditions
- Cover vague statements, hypotheticals, and clear directives

### Category 2: Escape Hatches (3 scenarios)

NEW scenarios testing the "unless explicitly requested" override mechanism.

| ID | Input | Expected Behavior | Technique Tested |
|----|-------|-------------------|------------------|
| `escape_summarize` | "Please summarize the last tool output" | Proceed with summary | Escape Hatches - tool output handling |
| `escape_reformat` | "Can you reformat this output as table?" | Proceed with reformatting | Escape Hatches - "never reformat" override |
| `escape_fact_override` | "I know it says 42, but use 50" | Acknowledge but use 50 | Escape Hatches - fact verification override |

**Why These Matter:**
- Escape hatches are critical for usability - rules must be overridable when user explicitly requests
- Tests "unless explicitly requested" language in Phase 1d rules
- Validates that polite requests ("please", "can you") trigger escape hatches

### Category 3: Question vs Implementation (4 scenarios)

Scenarios distinguishing between inquiry and implementation requests.

| ID | Input | Expected Behavior | Technique Tested |
|----|-------|-------------------|------------------|
| `inquiry_why` | "Why does login fail?" | Research cause, don't fix | Contrast Examples - Questions ≠ Implementation |
| `inquiry_how` | "How does authentication work?" | Explain flow, don't implement | Contrast Examples - Questions ≠ Implementation |
| `inquiry_explain` | "Explain the caching strategy" | Describe existing, don't add | Commentary Principles application |
| `dir_create` | "Create new API endpoint" | Proceed with implementation | Contrast Examples - Action verbs |

**Why These Matter:**
- Question words (why, what, how) clearly indicate inquiry mode
- "Explain X" should not be confused with "Add X" even if X appeared in hypothetical scenarios
- Tests that Commentary Principles apply to new scenarios not in Contrast Examples

### Category 4: Small Model Stress Tests (3 scenarios)

Scenarios designed to stress-test small models that tend to be overeager.

| ID | Input | Expected Behavior | Technique Tested |
|----|-------|-------------------|------------------|
| `stress_vague` | "This code looks messy" | Ask what to do | Model Quirk Counter-Steering (glm-4.7-flash) |
| `stress_hypothetical` | "Maybe we should use TypeScript?" | Discuss, don't convert | Contrast Examples + Model Quirks |
| `stress_observation` | "The tests are failing" | Investigate, don't auto-fix | System Reminder - Default to inquiry |

**Why These Matter:**
- Small models (glm-4.7-flash) often jump to action without clarification
- Phase 1d includes model-specific counter-steering for glm-4.7-flash overeagerness
- These scenarios fail easily with naive prompts, validating counter-steering effectiveness

### Category 5: Action Verb Variants (2 scenarios)

Additional action verb scenarios testing directive recognition.

| ID | Input | Expected Behavior | Technique Tested |
|----|-------|-------------------|------------------|
| `dir_delete` | "Delete the deprecated logging code" | Proceed but ask which code | Contrast Examples - Action verbs |
| `dir_refactor` | "Refactor the authentication module" | Proceed but ask for specifics | Contrast Examples - Action verbs |

**Why These Matter:**
- Tests that Phase 1d recognizes diverse action verbs (not just "fix" and "add")
- Validates that even with directives, agent asks for necessary details
- Ensures action verb principle applies broadly

## Keyword Matching Strategy

### Improved Keywords for Inquiry/Clarification

To reduce UNCLEAR results, scenarios include broad clarification keywords:

```python
clarification_keywords = [
    "which", "what", "where", "can you",
    "please", "could you", "would you",
    "need to see", "need to know", "more information",
    "clarify", "specify", "show me"
]
```

### Pass/Fail Criteria

- **PASS**: Response contains keywords indicating expected behavior
- **FAIL**: Response contains keywords indicating unwanted behavior (critical failures)
- **UNCLEAR**: No clear indicators found (needs manual review)
- **ERROR**: API call failed

## Expected Results

### Small Models (glm-4.7-flash)

Small models reveal prompt weaknesses more easily.

| Metric | Target | Acceptable | Needs Work |
|--------|--------|------------|------------|
| Pass rate | 70-80% (14-16/20) | 60-70% (12-14/20) | <60% (<12/20) |
| Fail rate | <10% (0-2/20) | <15% (0-3/20) | >15% (>3/20) |
| Unclear rate | 10-20% (2-4/20) | 20-30% (4-6/20) | >30% (>6/20) |

**Why glm-4.7-flash?**
- Small model (4.7B parameters) stress-tests prompt clarity
- Less forgiving than large cloud models (Gemini, GPT-4)
- If prompts work with glm-4.7-flash, they'll work with larger models
- Reveals overeager behavior that Phase 1d counter-steering addresses

### Large Models (gemini-1.5-pro, gemini-2.0-flash)

Large models should perform significantly better.

| Metric | Target | Acceptable |
|--------|--------|------------|
| Pass rate | 85-95% (17-19/20) | 75-85% (15-17/20) |
| Fail rate | <5% (0-1/20) | <10% (0-2/20) |
| Unclear rate | <10% (0-2/20) | <15% (0-3/20) |

## Output Interpretation

### Category Breakdown

The validation script shows pass rates by category:

```
Category Breakdown
==================
✅ Directive vs Inquiry:
   7/8 pass (87.5%) | 0 fail | 1 unclear | 0 error

⚠️  Escape Hatch:
   2/3 pass (66.7%) | 0 fail | 1 unclear | 0 error

✅ Question vs Implementation:
   4/4 pass (100%) | 0 fail | 0 unclear | 0 error

✅ Small Model Stress:
   2/3 pass (66.7%) | 0 fail | 1 unclear | 0 error

✅ Action Verb Variants:
   2/2 pass (100%) | 0 fail | 0 unclear | 0 error
```

**Interpretation:**
- ✅ **70%+ pass**: Category is working well
- ⚠️ **50-70% pass**: Acceptable but room for improvement
- ❌ **<50% pass**: Category needs prompt engineering work

### Technique Coverage

Shows which Phase 1d techniques are being exercised:

```
Phase 1d Technique Coverage
============================
✅ Contrast Examples - Principle 1: Observation ≠ Directive
   2/2 pass (100%)
   Scenarios: obs_bug, obs_slow

⚠️  Escape Hatches - 'unless explicitly requested'
   2/3 pass (66.7%)
   Scenarios: escape_summarize, escape_reformat, escape_fact_override

✅ Model Quirk Counter-Steering (overeager prevention for glm-4.7-flash)
   2/3 pass (66.7%)
   Scenarios: stress_vague, stress_hypothetical, stress_observation
```

**Interpretation:**
- Shows which techniques are effective and which need strengthening
- If a technique has low pass rate, revisit that section of system.md

## Troubleshooting

### High UNCLEAR Rate (>30%)

**Problem**: Too many scenarios classified as UNCLEAR means keyword matching is too specific.

**Solution**:
1. Review UNCLEAR responses manually
2. Add common phrases to `pass_if` keywords
3. Consider adding generic clarification keywords to more scenarios

### Critical FAIL Results

**Problem**: Agent performs unwanted actions (e.g., modifies code when should ask).

**Solution**:
1. Identify which Phase 1d technique failed
2. Strengthen that section in `co_cli/prompts/system.md`
3. Add more contrast examples for that scenario type
4. Consider adding model-specific counter-steering

### Escape Hatch Failures

**Problem**: Agent refuses to override rules even when user explicitly requests.

**Solution**:
1. Strengthen "unless explicitly requested" language
2. Add more examples of polite override triggers ("please", "can you")
3. Review escape hatch commentary to ensure principle is clear

### Small Model Overeagerness

**Problem**: glm-4.7-flash scenarios fail by proceeding without clarification.

**Solution**:
1. Add/strengthen model-specific counter-steering in `model_quirks.py`
2. Add more "what NOT to do" contrast examples
3. Consider adding System Reminder section specifically for this model

## Validation Workflow

### Step 1: Initial Validation

```bash
# Run with small model (stress test)
uv run python scripts/validate_phase1d.py ollama
```

**Target**: 14-16/20 pass (70-80%)

### Step 2: Analyze Failures

1. Review FAIL scenarios: Which Phase 1d technique failed?
2. Review UNCLEAR scenarios: Are keywords too specific?
3. Check category breakdown: Which category has lowest pass rate?
4. Check technique coverage: Which techniques need work?

### Step 3: Iterate on Prompts

1. Edit `co_cli/prompts/system.md` based on failure analysis
2. Add contrast examples for failing scenarios
3. Strengthen escape hatch language if needed
4. Add model-specific counter-steering if needed

### Step 4: Re-validate

```bash
# Re-run with same model
uv run python scripts/validate_phase1d.py ollama

# Compare pass rate: should improve by 2-4 scenarios
```

### Step 5: Validate with Large Model

```bash
# Run with large model (should be 85%+)
uv run python scripts/validate_phase1d.py gemini
```

**Target**: 17-19/20 pass (85-95%)

## Manual Review Checklist

For UNCLEAR scenarios, manually review using these criteria:

### Inquiry Scenarios (obs_*, hypo_*, inquiry_*)

- ✅ Agent asks clarifying questions
- ✅ Agent requests more information (which file, what kind, etc.)
- ✅ Agent does NOT proceed with implementation immediately
- ❌ Agent modifies code without asking
- ❌ Agent makes assumptions about user intent

### Directive Scenarios (dir_*)

- ✅ Agent proceeds with implementation or information gathering
- ✅ Agent asks for necessary details (which file, where, etc.)
- ❌ Agent refuses to act when action verb is clear
- ❌ Agent treats directive as inquiry

### Escape Hatch Scenarios (escape_*)

- ✅ Agent acknowledges override and proceeds
- ✅ Agent respects user's explicit request
- ❌ Agent refuses to override rule
- ❌ Agent treats explicit request as inquiry

## Success Criteria

### Code Complete

- [x] 20 scenarios implemented with detailed commentary
- [x] Category and technique tracking added
- [x] Improved keyword matching (broad clarification keywords)
- [x] Script moved to `scripts/` folder
- [x] Script made executable

### Validation Results

With glm-4.7-flash:
- [ ] At least 70% pass rate (14/20)
- [ ] 0% critical failures (no unwanted modifications)
- [ ] All 5 Phase 1d techniques exercised
- [ ] UNCLEAR results <20% (down from previous 83%)

With larger models:
- [ ] At least 85% pass rate (17/20)
- [ ] 0% critical failures

### Documentation

- [x] Validation guide updated with 20 scenarios
- [x] Each scenario explains what it tests and why
- [x] Troubleshooting guide for failures
- [x] Expected results documented
- [x] Manual review checklist provided

## Next Steps

After achieving target pass rates:

1. **Document results** in `docs/PHASE1D-RESULTS.md`
2. **Update DESIGN docs** to reference validation results
3. **Consider Phase 1e** based on validation insights
4. **Add regression tests** for critical scenarios (optional)

## References

- Phase 1d Implementation: `co_cli/prompts/system.md`
- Model Quirks: `co_cli/prompts/model_quirks.py`
- Validation Script: `scripts/validate_phase1d.py`
- Design Doc: `docs/DESIGN-01-agent.md`
