# Codex Prompt Assembly Flowchart

## From User Request → Final LLM Prompt

### Example Scenario
**User wants:** "Work autonomously on this task, be friendly, and have full file access"

---

## Step 1: User Configuration

```
User starts Codex with settings:
├─ mode: "execute"                    # How should Codex work?
├─ personality: "friendly"            # What tone should it use?
├─ sandbox: "danger-full-access"      # What can it access?
├─ approval: "never"                  # When to ask permission?
└─ model: "gpt-5"                     # Which AI model?
```

---

## Step 2: File Selection (What gets loaded?)

```
┌─────────────────────────────────────────────────────────────┐
│                    CODEX PROMPT FILES                        │
│                     (24 files total)                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  📁 base_instructions/                                       │
│     └─ default.md ✓ ←──────────────────── ALWAYS LOADED    │
│                                                              │
│  📁 collaboration_mode/                                      │
│     ├─ default.md                                           │
│     ├─ execute.md ✓ ←──────────────────── USER SELECTED    │
│     ├─ pair_programming.md                                  │
│     └─ plan.md                                              │
│                                                              │
│  📁 personalities/                                           │
│     ├─ pragmatic.md                                         │
│     └─ friendly.md ✓ ←─────────────────── USER SELECTED    │
│                                                              │
│  📁 permissions/sandbox_mode/                                │
│     ├─ read_only.md                                         │
│     ├─ workspace_write.md                                   │
│     └─ danger_full_access.md ✓ ←────────── USER SELECTED    │
│                                                              │
│  📁 permissions/approval_policy/                             │
│     ├─ never.md ✓ ←────────────────────── USER SELECTED    │
│     ├─ on_failure.md                                        │
│     ├─ on_request.md                                        │
│     ├─ on_request_rule.md                                   │
│     └─ unless_trusted.md                                    │
│                                                              │
│  📁 model_instructions/                                      │
│     └─ gpt-5.2-codex_instructions_template.md ✓ ←─ CONDITIONAL │
│        (Only loaded for GPT-5)                              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 3: Content Assembly (Stack the layers)

```
LAYER 1: Base Instructions (800 lines)
┌─────────────────────────────────────────────────────────┐
│ # You are Codex                                         │
│                                                         │
│ You can execute tasks, use tools, stream responses...  │
│ - Use update_plan tool for complex tasks               │
│ - Keep responses concise                               │
│ - Run tests to validate your work                      │
│ ... (foundation rules)                                 │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

LAYER 2: Execute Mode (120 lines)
┌─────────────────────────────────────────────────────────┐
│ # Collaboration Style: Execute                          │
│                                                         │
│ You execute independently. Do not ask questions.       │
│ When information is missing:                           │
│ - Make a sensible assumption                           │
│ - State the assumption in your final message           │
│ - Continue executing                                   │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

LAYER 3: Friendly Personality (60 lines)
┌─────────────────────────────────────────────────────────┐
│ # Personality                                           │
│                                                         │
│ You optimize for team morale and being supportive.     │
│ - Use "we" and "let's"                                 │
│ - Warm, encouraging, conversational                    │
│ - You are NEVER curt or dismissive                     │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

LAYER 4: Full Access Sandbox (15 lines)
┌─────────────────────────────────────────────────────────┐
│ `sandbox_mode` is `danger-full-access`:                │
│ No filesystem sandboxing - all commands permitted.     │
│ Network access is enabled.                             │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

LAYER 5: Never Ask Approval (20 lines)
┌─────────────────────────────────────────────────────────┐
│ `approval_policy` is `never`:                           │
│ Non-interactive mode - you may NEVER ask for approval. │
│ Work around constraints to solve the task.             │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

LAYER 6: GPT-5 Specific Overrides (150 lines)
┌─────────────────────────────────────────────────────────┐
│ # Model-Specific Instructions                           │
│                                                         │
│ - Don't use emojis                                     │
│ - Use this file reference format: path:line            │
│ - For frontend: avoid default fonts, use gradients     │
│ {{ personality }} ← Injects friendly.md here           │
└─────────────────────────────────────────────────────────┘
```

---

## Step 4: Final Prompt (sent to LLM)

```
┌──────────────────────────────────────────────────────────┐
│         COMPLETE SYSTEM PROMPT (~1,165 lines)            │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  • Core instructions (who you are, how to work)         │
│  • Execute mode (work autonomously, state assumptions)  │
│  • Friendly personality (warm, use "we", encouraging)   │
│  • Full file access (no sandbox restrictions)           │
│  • Never ask approval (solve problems independently)    │
│  • GPT-5 quirks (no emojis, file format, design rules)  │
│                                                          │
└──────────────────────────────────────────────────────────┘
                         ↓
                    SENT TO GPT-5
                         ↓
          🤖 LLM generates response following
             ALL the combined instructions
```

---

## Key Insight: Same Base, Different Combinations

### Configuration A: Autonomous Expert
```
base.md + execute.md + pragmatic.md + full_access.md + never.md
= "Get it done, don't bother me, be direct"
```

### Configuration B: Collaborative Partner
```
base.md + pair_programming.md + friendly.md + workspace_write.md + on_request.md
= "Work with me step-by-step, check before risky actions, be warm"
```

### Configuration C: Planning Phase
```
base.md + plan.md + pragmatic.md + read_only.md + unless_trusted.md
= "Explore and plan only, don't change anything yet, be focused"
```

---

## Visual Decision Tree

```
User Request: "Add authentication to the app"
                         ↓
        ┌────────────────┴────────────────┐
        │ What mode are you in?           │
        └┬────────┬──────────┬────────────┘
         │        │          │
         ↓        ↓          ↓
    EXECUTE    PLAN       PAIR
         │        │          │
         ↓        ↓          ↓
  Make        Explore    Small
  assumptions first,     steps,
  & go!       then       check
              ask        often

    + What personality?
         ↓        ↓
    FRIENDLY  PRAGMATIC
    "Let's"    "You"

    + What can you touch?
         ↓         ↓          ↓
    READ-ONLY  WORKSPACE  FULL-ACCESS

    + When ask permission?
         ↓         ↓         ↓
    NEVER    ON-REQUEST  ALWAYS
```

---

## Real-World Example: Task Execution

### User: "Fix the login bug"

**With execute + pragmatic + full-access + never:**
```
🤖 Codex behavior:
1. Searches for login code → finds bug
2. Fixes it immediately (no approval needed)
3. Runs tests automatically
4. Reports: "Fixed null check in auth.rs:47. Tests pass."
   (Direct, no fluff)
```

**With pair + friendly + workspace + on-request:**
```
🤖 Codex behavior:
1. "Let's take a look at the login flow together!"
2. Finds bug → explains what's wrong
3. "I'd like to fix the null check. Should I go ahead?"
4. After approval: fixes, runs tests
5. "Great! We fixed it and tests are passing. 🎉"
   (Warm, collaborative)
```

---

## Summary: The Assembly Pipeline

```
┌─────────┐    ┌──────────┐    ┌─────────┐    ┌────────┐
│  User   │───▶│  Config  │───▶│  Load   │───▶│ Codex  │
│ Request │    │ Settings │    │  Files  │    │  Acts  │
└─────────┘    └──────────┘    └─────────┘    └────────┘
               mode=execute    base.md         Fixes bug
               personality=    execute.md      No approval
               friendly        friendly.md     Warm tone
               sandbox=full    full_access.md
               approval=never  never.md
```

**The magic:** Change one setting → swap one file → completely different behavior, with ZERO code duplication!

---

## Why This Matters for co-cli

**Current co-cli:** One big prompt with everything mixed together
**Codex approach:** 24 small files, pick and mix

**Benefit:** Want to add a new mode? Just add one new file. Want to tweak personality? Edit one 60-line file, not hunt through 800 lines.

**Practical win:** User can do `co --mode=execute` or `co --mode=pair` and get completely different behavior without you maintaining 2 giant prompt files.
