# Phase 1d Implementation Issues Analysis

**Date**: 2026-02-09
**Status**: Aggressive Issue Identification (Pre-Validation)

This document identifies potential issues, edge cases, and failure modes in the Phase 1d implementation **before** running validation. The goal is to be aggressive in finding weaknesses so they can be addressed or validated.

---

## Critical Issues (Must Fix Before Production)

### 1. ⚠️ Escape Hatch Language May Be Too Weak

**Location**: `co_cli/prompts/system.md` - Fact Verification section

**Issue**: The escape hatch language for fact verification might not be strong enough:

```markdown
**4. Never blindly accept corrections without verification**
   - If user insists after verification: Acknowledge disagreement, proceed with user's preference
```

**Problem**:
- "Acknowledge disagreement, proceed" is passive and buried in a sub-bullet
- Small models (glm-4.7-flash) might miss this escape hatch
- The example shows verification but doesn't demonstrate the escape hatch in action

**Evidence**:
- `escape_fact_override` scenario: "I know it says 42, but use 50"
- If agent misses the "insists" part, will refuse or argue

**Potential Impact**: User frustration when agent refuses to proceed after explicit insistence

**Suggested Fix**:
```markdown
**4. Verify facts independently, but user preference overrides**
   - Always verify deterministic facts independently first
   - If user insists after verification, **proceed with user's preference**
   - Acknowledge the disagreement briefly, then continue with their value
   - Example: "I calculated 42, but I'll use 50 as you requested."
```

**Validation**: `escape_fact_override` scenario will reveal if this is an issue

---

### 2. ⚠️ "Check" Edge Case Has Conflicting Guidance

**Location**: `co_cli/prompts/system.md:91` - Commentary section

**Issue**: The guidance on "check" is ambiguous:

```markdown
4. **Action verbs are the primary signal**
   - Edge case: "check" can be either (context-dependent)
```

**Problem**:
- "context-dependent" provides no guidance on HOW to determine context
- Agent will inconsistently classify "Check if tests pass" vs "Check the code"
- The Contrast Examples table shows "Check if tests pass" → Directive, but doesn't explain why

**Evidence**:
- `edge_check` scenario: "Check if the tests pass" expects Directive
- But "Check the code for bugs" would be Inquiry
- No guidance on distinguishing these

**Potential Impact**: Inconsistent handling of "check" commands

**Suggested Fix**:
```markdown
Edge case: "check" can be either:
- Directive when followed by actionable result: "Check if tests pass" (run tests)
- Inquiry when followed by vague inspection: "Check the code" (read and explain)
- Heuristic: If "check" has a clear boolean result (pass/fail), treat as Directive
```

**Validation**: `edge_check` scenario + manual testing with variations

---

### 3. ❌ Model Quirk Counter-Steering NOT Active in Production

**Location**: `co_cli/agent.py:82` (per validation guide)

**Issue**: Phase 1d prompt improvements include model-specific counter-steering, but `agent.py` doesn't pass `model_name` parameter:

```python
# Current (Phase 1d):
system_prompt = get_system_prompt(provider_name, personality=settings.personality)

# Needed for model quirks to activate:
system_prompt = get_system_prompt(provider_name, personality=settings.personality, model_name=model_name)
```

**Problem**:
- All 20 validation scenarios assume model quirks are active
- 3 scenarios specifically test Model Quirk Counter-Steering technique
- Without `model_name`, glm-4.7-flash gets NO overeager counter-steering
- Validation results will be artificially poor

**Evidence**:
- `scripts/validate_phase1d.py:252` extracts model name and passes it to `get_system_prompt()`
- But production `agent.py` doesn't do this
- Validation script works around the issue, but production is broken

**Potential Impact**:
- Validation will pass but production will fail
- Small models (glm-4.7-flash, qwen) will be overeager in production
- Large models (gemini-1.5-pro) won't get verbosity counter-steering

**Suggested Fix**: This is documented as "Phase 2" but should be treated as critical blocker

**Validation**: Check if `## Model-Specific Guidance` appears in production prompts

---

## High-Risk Issues (Likely to Cause Failures)

### 4. ⚠️ Hypothetical Detection Relies on Single Word "what if"

**Location**: `co_cli/prompts/system.md:64` - Contrast Examples

**Issue**: Hypothetical detection example:

```markdown
| "What if we added caching?" | Directive → Implements cache | Hypothetical question | Inquiry → Discuss tradeoffs, NO implementation |
```

**Problem**:
- Only shows ONE hypothetical pattern: "What if we X?"
- Other hypothetical patterns not covered:
  - "Maybe we should X?"
  - "Have you considered X?"
  - "Would it make sense to X?"
  - "I'm thinking we could X"
  - "Perhaps we ought to X"

**Evidence**:
- `hypo_cache` scenario: "What if we added caching?" - covered
- `stress_hypothetical` scenario: "Maybe we should use TypeScript?" - NOT covered in examples
- Contrast Examples only show "what if", not "maybe should"

**Potential Impact**: Small models might not recognize non-"what if" hypotheticals

**Suggested Fix**: Add more hypothetical patterns to Contrast Examples:
```markdown
| "Maybe we should use Redis?" | Directive | Tentative suggestion | Inquiry → Discuss pros/cons |
| "Have you considered microservices?" | Directive | Question format | Inquiry → Explain tradeoffs |
```

**Validation**: `stress_hypothetical` scenario will likely FAIL on first run

---

### 5. ⚠️ "The README could mention X" Edge Case Might Confuse Models

**Location**: `co_cli/prompts/system.md:65` - Contrast Examples

**Issue**: The example:

```markdown
| "The README could mention X" | Directive → Updates README | Observation about gap | Inquiry → Acknowledge gap, ask if user wants update |
```

**Problem**:
- "could" is a modal verb that CAN indicate suggestion OR possibility
- In "The README could mention X", it's a suggestion
- But in "The README could be wrong", it's possibility/doubt
- No guidance on distinguishing these

**Evidence**:
- `obs_readme` scenario: "The README could mention the installation steps"
- Expected: Ask if user wants update
- But what about: "The README could be outdated" (observation, not suggestion)?

**Potential Impact**: Inconsistent handling of "could" statements

**Suggested Fix**: Add commentary explaining the distinction:
```markdown
Note: "could" + action (mention, include, add) = suggestion → ask for confirmation
      "could" + state (be wrong, be outdated) = doubt → investigate/verify
```

**Validation**: `obs_readme` scenario + manual testing with "could be" variants

---

### 6. ⚠️ Escape Hatch for "Please Summarize" Might Not Trigger

**Location**: `co_cli/prompts/system.md:164-173` - Tool Output Handling

**Issue**: The escape hatch language:

```markdown
**For Directives (List/Show commands):**
- Never reformat, summarize, or drop URLs from tool output unless the user explicitly requests a summary or reformatting
```

**Problem**:
- Buried in "For Directives" section, but "please summarize" is NOT a directive
- The escape hatch is in the wrong section
- Agent might classify "please summarize" as Inquiry and follow Inquiry rules instead

**Evidence**:
- `escape_summarize` scenario: "Please summarize the last tool output"
- Tool Output Handling section says "unless explicitly requests"
- But Directive vs Inquiry section doesn't mention "please X" as override

**Potential Impact**: Agent refuses to summarize despite "please" escape hatch

**Suggested Fix**: Move escape hatch to a more prominent location or add to Directive vs Inquiry:
```markdown
**Escape Hatch**: When user explicitly requests with "please X" or "can you X",
proceed with that action even if it contradicts the rules above.
```

**Validation**: `escape_summarize` and `escape_reformat` scenarios will reveal this

---

## Medium-Risk Issues (May Cause Inconsistency)

### 7. ⚠️ Default to Inquiry Principle Has Weak Emphasis

**Location**: `co_cli/prompts/system.md:44` - Directive vs Inquiry

**Issue**: The default behavior is stated once:

```markdown
**Default to Inquiry unless explicit action verb present.**
```

**Problem**:
- Only one mention, easily missed by models
- Not repeated in Commentary section
- Not part of "Common mistakes" table
- Small models might forget this rule mid-response

**Evidence**:
- `edge_ambiguous` scenario: "The database schema needs work"
- Expected: Ask what kind of work (default to Inquiry)
- But "needs work" could be interpreted as directive by overeager models

**Potential Impact**: False positive directives when input is ambiguous

**Suggested Fix**: Add to Common Mistakes table:
```markdown
| "The database needs work" | Directive → Modifies schema | Ambiguous statement | Inquiry → Ask what kind of work |
```

And strengthen default rule:
```markdown
**ALWAYS default to Inquiry when uncertain.** Better to ask clarification than to modify code incorrectly.
```

**Validation**: `edge_ambiguous` and `stress_vague` scenarios test this

---

### 8. ⚠️ System Reminder (Critical Rules) Missing

**Location**: `co_cli/prompts/system.md` - End of file

**Issue**: Phase 1d design called for "System Reminder" at END of prompt to exploit recency bias:

From Phase 1d plan:
> **System Reminder** - Critical rules repeated at end (exploits recency bias)

**Problem**:
- Current `system.md` does NOT have a "## Critical Rules" section at the end
- The validation script checks for `"## Critical Rules" in system_prompt`
- This check will FAIL

**Evidence**:
- `scripts/validate_phase1d.py:256`:
  ```python
  "Critical Rules": "## Critical Rules" in system_prompt,
  ```
- Validation guide mentions System Reminder as one of 5 Phase 1d techniques
- But `system.md` doesn't implement it

**Potential Impact**:
- Validation will show "System Reminder: ❌ Not Present"
- Small models won't benefit from recency bias reminder
- Critical rules only appear ONCE at the beginning

**Suggested Fix**: Add `## Critical Rules` section at end of `system.md`:
```markdown
---

## Critical Rules (System Reminder)

**Remember these core principles:**

1. **Default to Inquiry** unless user explicitly uses action verbs (fix, add, update, modify, delete, refactor, create)
2. **Trust tool output** over memory - tools access current state, your memory may be stale
3. **Show tool display verbatim** - never reformat unless explicitly requested
4. **Verify facts independently** - but user preference overrides when they insist
5. **Ask for clarification** when uncertain - false negatives are better than false positives
```

**Validation**: Structural validation will catch this immediately

---

### 9. ⚠️ Keyword Matching Too Broad May Cause False Positives

**Location**: `scripts/validate_phase1d.py` - pass_if keywords

**Issue**: Many scenarios have very broad keywords:

```python
"pass_if": ["which", "what", "where", "can you", "please", "could you", "would you", ...]
```

**Problem**:
- "what" matches "what I'll do is..." (agent proceeding, not asking)
- "which" matches "which I'll implement" (agent acting, not clarifying)
- False PASS results if agent says "I'll fix this, which involves..."

**Evidence**:
- Previous version had 83% UNCLEAR (too specific)
- New version might swing to false PASS (too broad)
- Need balanced keyword lists

**Potential Impact**: Validation reports PASS when agent actually failed

**Suggested Fix**: Use phrase matching instead of single words:
```python
"pass_if": [
    "which function", "which file", "which part",  # Not just "which"
    "what bug", "what error", "what should",       # Not just "what"
    "can you show", "can you clarify",             # Not just "can you"
]
```

**Validation**: Manual review of PASS scenarios is critical

---

### 10. ⚠️ Counter-Steering Text May Be Too Gentle

**Location**: `co_cli/prompts/model_quirks.py:199` - glm-4.7-flash

**Issue**: Counter-steering for glm-4.7-flash (overeager):

```python
"counter_steering": (
    "Be careful not to exceed the scope of the user's request. "
    "If the user states an observation ('This function has a bug') or asks a question "
    "('Why does login fail?'), that is NOT a request to modify code — it's a request "
    "for explanation only. Only modify code when the user explicitly uses action verbs "
    "like 'fix', 'add', 'update', 'modify', 'delete', 'refactor', or 'create'."
),
```

**Problem**:
- "Be careful" is polite but weak
- Small models need EMPHATIC language
- Compare to Aider's counter-steering: "NEVER modify code unless..."

**Evidence**:
- `stress_vague`, `stress_hypothetical`, `stress_observation` scenarios
- Small models are notoriously hard to control
- Gentle language often ignored

**Potential Impact**: Small model scenarios fail despite counter-steering

**Suggested Fix**: Make it more emphatic:
```python
"counter_steering": (
    "⚠️ CRITICAL: You have a tendency to modify code when user only asks questions. "
    "STOP and read carefully: Observations and questions are NOT action requests. "
    "ONLY modify code when user EXPLICITLY says: fix, add, update, modify, delete, refactor, or create. "
    "If uncertain, ASK for clarification - do NOT proceed with modifications."
),
```

**Validation**: All 3 Small Model Stress scenarios will test this

---

## Low-Risk Issues (Minor Inconsistencies)

### 11. ℹ️ Contrast Examples Use "→" Inconsistently

**Location**: `co_cli/prompts/system.md:59-65` - Common mistakes table

**Issue**: Arrow direction and meaning unclear:

```markdown
| User Input | Wrong Classification | Why Wrong | Correct Response |
| "This function has a bug" | Directive → Modifies code | ... | Inquiry → Explain the bug |
```

**Problem**:
- "→" sometimes means "leads to action" (Directive → Modifies code)
- "→" sometimes means "implies classification" (Inquiry → Explain)
- Inconsistent symbol usage might confuse models

**Potential Impact**: Minimal - models likely understand from context

**Suggested Fix**: Use consistent format:
```markdown
| User Input | Wrong: Treats as X, does Y | Why Wrong | Right: Treats as Z, does W |
```

---

### 12. ℹ️ Commentary Principles Section Only Tests ONE Scenario

**Location**: Validation suite

**Issue**: Commentary Principles is one of the 5 Phase 1d techniques, but only ONE scenario tests it:

```
| **Commentary Principles** | Explain the "why" behind rules | inquiry_explain |
```

**Problem**:
- Commentary section explains WHY behind all examples
- But only `inquiry_explain` is tagged as testing this technique
- Insufficient coverage to validate if Commentary principles are effective

**Potential Impact**: Can't determine if Commentary technique is working

**Suggested Fix**: Add more scenarios that require applying principles to NEW cases not in examples

**Validation**: Current suite has this limitation

---

## Edge Cases Not Covered by Test Suite

### 13. ℹ️ Chained Requests Not Tested

**Examples**:
- "This code has a bug. Fix it." (observation + directive)
- "Why does login fail? Also, add logging." (inquiry + directive)
- "Fix the bug and run the tests" (directive + directive)

**Problem**: No scenarios test multiple intents in one input

**Validation**: Consider adding multi-intent scenarios in future

---

### 14. ℹ️ Negations Not Tested

**Examples**:
- "Don't fix this bug yet" (negated directive)
- "Not sure we should add caching" (negated hypothetical)
- "This is NOT a request to refactor" (explicit negation)

**Problem**: No scenarios test negated intents

**Validation**: Consider adding negation scenarios in future

---

### 15. ℹ️ Conditional Requests Not Tested

**Examples**:
- "If the tests pass, deploy to staging" (conditional directive)
- "Fix the bug only if it's in auth.py" (conditional directive)
- "Add caching unless it complicates the code" (conditional directive)

**Problem**: No scenarios test conditional logic

**Validation**: Consider adding conditional scenarios in future

---

## Summary of Issues by Severity

| Severity | Count | Issues |
|----------|-------|--------|
| **Critical** | 3 | Model quirks not active in production, System Reminder missing, Escape hatch language too weak |
| **High** | 3 | Hypothetical detection limited, "check" ambiguous, "please summarize" escape hatch |
| **Medium** | 4 | Default-to-inquiry weak emphasis, keyword matching too broad, counter-steering too gentle, "could" ambiguity |
| **Low** | 2 | Arrow symbol inconsistency, Commentary principles under-tested |
| **Edge Cases** | 3 | Chained requests, negations, conditionals |
| **Total** | 15 | |

## Recommended Actions Before Validation

### Must Fix (Blocking)

1. **Add System Reminder section** at end of `system.md` (Issue #8)
   - Required for structural validation to pass
   - Expected by validation script
   - Core Phase 1d technique

2. **Fix model quirks integration** in `agent.py` (Issue #3)
   - OR accept that production is different from validation
   - OR note this limitation in validation results

### Should Fix (High Impact)

3. **Strengthen escape hatch language** (Issue #1)
   - Add to Directive vs Inquiry section
   - Make it more prominent

4. **Add more hypothetical patterns** to Contrast Examples (Issue #4)
   - Include "maybe should", "have you considered", etc.

5. **Clarify "check" edge case** with heuristic (Issue #2)
   - Explain when "check" is Directive vs Inquiry

### Nice to Fix (Improved Robustness)

6. **Make counter-steering more emphatic** for small models (Issue #10)
7. **Add "could" distinction** to Commentary (Issue #5)
8. **Review keyword matching** for phrase-based instead of word-based (Issue #9)

## Validation Strategy

### Phase 1: Structural Validation

Run validation script to catch:
- Issue #8: System Reminder missing
- Issue #3: Model quirks not present (if running in production mode)

### Phase 2: Run Full Validation

Run with glm-4.7-flash:
```bash
uv run python scripts/validate_phase1d.py ollama
```

**Expected Failures** (before fixes):
- `escape_fact_override` (Issue #1)
- `escape_summarize` / `escape_reformat` (Issue #6)
- `stress_hypothetical` (Issue #4)
- `edge_check` (Issue #2)
- `edge_ambiguous` (Issue #7)
- `stress_vague` (Issue #10)

**Target**: 10-12/20 PASS (50-60%) with current issues

**After fixes**: 14-16/20 PASS (70-80%) target

### Phase 3: Manual Review

For scenarios marked PASS:
- Manually verify agent is actually doing the right thing
- Check for keyword false positives (Issue #9)

### Phase 4: Iterate

Based on failure analysis:
1. Prioritize fixes by severity
2. Re-run validation
3. Track improvement rate
4. Stop when target pass rate achieved

## References

- Phase 1d Implementation: `co_cli/prompts/system.md`
- Model Quirks: `co_cli/prompts/model_quirks.py`
- Prompt Assembly: `co_cli/prompts/__init__.py`
- Validation Script: `scripts/validate_phase1d.py`
- Validation Guide: `docs/PHASE1D-VALIDATION-GUIDE.md`
