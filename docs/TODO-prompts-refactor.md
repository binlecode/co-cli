# TODO: Fix Reasoning Gap

**Goal:** Allow the agent to answer analytical questions by relaxing the strict "verbatim" rule, aligned with peer system best practices.
## Current Problem

**"Verbatim strict mode"** blocks synthesis — agent can't answer "When is lunch?" from calendar data, just dumps raw output.

## Peer System Convergence (5/5 systems)

From `docs/REVIEW-prompts-peer-systems.md` — what **all 5 peer systems** agree on:

1. **Be concise / terse** — Codex, Gemini, OpenCode, Claude Code, Aider
2. **Keep going until resolved** — Codex, Gemini, OpenCode, Claude Code (4/5, Aider is human-in-loop)
3. **Respect existing conventions** — All 5 systems
4. **Validate with tests** — Codex, Gemini, OpenCode, Claude Code (4/5, Aider is tool-less)
5. **Plan/research before execute** — All 5 systems

### Key Borrowable Patterns

- **Gemini's Directive vs Inquiry distinction** — Prevents file edits when user is just asking a question
- **Codex's preamble messages** — 8-12 word updates before tool calls ("I've explored the repo; now checking the API routes")
- **Gemini's "High-Signal Output"** — Focus on intent and technical rationale. Avoid filler, apologies, tool-use narration
- **OpenCode's "professional objectivity"** — "Respectful correction is more valuable than false agreement"
- **Aider's lazy_prompt** — "You NEVER leave comments describing code without implementing it"

## Implementation Tasks

- [x] **Read current prompt:** Review `co_cli/prompts/system.md` baseline
- [x] **Update Tool Output section:**
  - **Removed:** "Never reformat, summarize, or drop URLs" strict rule
  - **Added:** Distinguish **List/Show** (verbatim with URLs) vs **Find/Analyze** (synthesis)
  - **Added:** "High-Signal Output" — focus on intent, avoid narration
- [x] **Add Inquiry vs Directive distinction:**
  - **Inquiry:** Questions ("When is lunch?", "What's the status?") → Synthesize answers
  - **Directive:** Commands ("List emails", "Show calendar") → Verbatim tool output
- [x] **Add preamble guidance:** 8-12 word updates before tool calls (Codex pattern)
- [x] **Add professional objectivity:** Respectful correction over false agreement (OpenCode pattern)
- [x] **Add "Keep going" principle:** Complete requests thoroughly before yielding (4/5 peer systems)
- [x] **Validation:** Automated test script created and executed (2/3 passing)
  - Script: `test_reasoning_gap.py`
  - RCA: `docs/RCA-test3-verbose-inquiry.md`
  - Results: ✓ Directive works, ✓ Inquiry synthesis works, ⚠️ Model inconsistency on complex inquiries

### Test Results (from `test_reasoning_gap.py`)

**✓ Test 1 - DIRECTIVE:** "List today's calendar events"
- Result: Full formatted output with URLs preserved
- Status: **PASS** - Verbatim tool output maintained

**✓ Test 2 - INQUIRY:** "When is lunch today?"
- Result: "No lunch event scheduled today"
- Status: **PASS** - Concise synthesis, no raw dump

**⚠️ Test 3 - INQUIRY:** "What's my next meeting?"
- Result: Multi-paragraph summary instead of "Next meeting: [X] at [Y]"
- Status: **KNOWN ISSUE** - Model inconsistency (see RCA)
- Root cause: Ollama glm-4.7-flash:q8_0 doesn't consistently follow "1-2 sentences" constraint

## Success Criteria

1. **Lunch Time Test passes** — "When is lunch?" → "1:00 PM team lunch" (not raw JSON dump)
2. **Tool output preserved** — "List emails" still shows verbatim display with URLs
3. **No test regressions** — All existing agent tests still pass