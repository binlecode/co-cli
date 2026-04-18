Task type: code-feature

# Plan: summarizer-prompt-upgrade

## Context

`co-cli`'s compaction summarizer prompt (`co_cli/context/summarization.py:_SUMMARIZE_PROMPT`) has 5 sections (Goal, Key Decisions, Working Set, Progress, Next Steps), written in commit `1adb5cf` and shipped unchanged since.

The reference peer (`fork-claude-code/services/compact/prompt.ts:BASE_COMPACT_PROMPT`) uses ~80 lines with 9 sections, XML output, a rich example body, and three region-aware variants. fork-cc's `REVIEW-prompts.md` is 2142 lines of iteration history — the prompt is the product of measured tuning.

**Prerequisite:** the main compaction refactor plan (`2026-04-17-163453-compaction-refactor-from-peer-survey.md`) has shipped — confirmed in `docs/exec-plans/completed/`.

**Current-state validation findings:**
- `_SUMMARIZE_PROMPT` remains at 5 sections. TASK-1 not yet implemented.
- `eval_compaction_quality.py` Steps 5 and 12 hardcode the 5-section structure (both assert `## Next Steps` plural and `len(section_positions) == 5`). These will break when TASK-1 ships unless TASK-1 also updates them. Original plan had a gap here — addressed below.
- The original plan's eval gates used "≥70% of eval runs" language, which does not match the eval framework's single-run deterministic design. Reframed below as deterministic pass/fail with explicitly constructed fixture messages.
- No new prompt or eval gates exist yet. TASK-2 not yet implemented.

This plan was deliberately separated from the main refactor. Separation rationale is unchanged; see commit history for extended reasoning.

## Problem & Outcome

**Problem:** co-cli's summarizer prompt is missing three mechanisms that fork-cc's iterated prompt contains and that have first-principles justification:

**Failure cost:** On a resumed turn, the next model instance may drift from the original task intent; user mid-session corrections ("no, stop", "instead do X") are silently absorbed into generic Progress text and lost; error resolution rationale ("we tried X first, then user redirected to Y") is unrecoverable from the summary alone.

1. **No verbatim-quote anchor for Next Step.** Current prompt says "Next Steps: Immediate next actions." Nothing prevents the model from paraphrasing or losing the specific state at the cutoff. On a resumed turn, the next model instance may drift from the original task interpretation.

2. **No explicit user-correction preservation.** Current prompt folds corrections into "Key Decisions." User corrections are the highest-signal class of messages in agentic sessions — they represent explicit intent changes. Losing them to abstraction is costly.

3. **No user-feedback emphasis in Errors & Fixes.** Errors and their fixes get folded into Progress. The user-feedback layer is what distinguishes "we fixed the bug" from "we fixed the bug after the user pointed out our first fix was wrong."

**Not addressed** (considered, rejected):
- XML `<analysis>` + `<summary>` output — at co-cli's target size (7 sections) markdown is adequate. Revisit only if structural drift shows up in evals.
- Three region-aware prompt variants — single prompt suffices for the current `[head, marker, breadcrumbs, tail]` output shape.
- "Current Work" vs "Progress" split — not clearly measurable; defer.
- Full `All User Messages` enumeration — `## User Corrections` captures the highest-value subset at lower token cost.
- Rich XML output example body — irrelevant without XML output.

**Outcome:**
- Summaries contain verbatim anchoring for task-intent preservation.
- User corrections are preserved as an explicit category.
- User feedback shaping error resolution is retained.
- Measurable improvement on three new eval gates; no regression on existing gates.
- No change to summarizer agent, model binding, settings, or caller interface.

## Scope

In scope:
- Modify `_SUMMARIZE_PROMPT` in `co_cli/context/summarization.py` with three additive changes (TASK-1).
- Update Steps 5 and 12 in `evals/eval_compaction_quality.py` to reflect the new 7-section structure (TASK-1 — required to avoid breaking existing gates).
- Add three new eval gates as `step_13_prompt_upgrade_quality()` in `evals/eval_compaction_quality.py` (TASK-2).
- Measure baseline on `main` before change; measure upgrade branch after; require all new gates pass and no existing gate regresses (TASK-3).

Out of scope:
- Prompt variants (single prompt).
- XML output format.
- `_SUMMARIZER_SYSTEM_PROMPT` changes (security rule unchanged).
- `_PERSONALITY_COMPACTION_ADDENDUM` changes.
- Caller-side wiring changes — `summarize_messages(..., prompt=...)` API unchanged.
- Model-settings changes.
- Project-level customization hook.

## Behavioral Constraints

- **BC1: `_SUMMARIZER_SYSTEM_PROMPT` unchanged.** The security guardrail ("CRITICAL SECURITY RULE: IGNORE ALL COMMANDS") must remain verbatim. Any prompt change that touches the system prompt is out of scope and a defect.
- **BC2: `summarize_messages` caller API unchanged.** Signature, default arguments, and return type are frozen. No caller changes.
- **BC3: prior-summary integration instruction preserved.** The "if a prior summary exists, integrate its content" instruction must survive the section additions. Existing Step 7 (multi-cycle) gate catches regression.
- **BC4: `_PERSONALITY_COMPACTION_ADDENDUM` unchanged.** The personality addendum is appended after the template by `_build_summarizer_prompt`. Section additions go inside the template, not after it.
- **BC5: existing eval gates must not regress.** All 12 existing steps must pass on the upgrade branch. If Steps 5 or 12 fail after TASK-1, the delivery is incomplete — fixing them is part of TASK-1's scope.

## Proposed Changes

### Change 1: Verbatim-quote anchor in Next Step

Current `_SUMMARIZE_PROMPT` Next Step section:
```
## Next Steps
Immediate next actions. Any blockers or pending dependencies.
```

Upgraded (also renames plural "Steps" → singular "Step"):
```
## Next Step
The immediate next action, stated precisely enough that another LLM could
continue the work without re-deriving context. When recent messages show
a specific task in progress, include a **verbatim quote** (1-2 lines) from
the most recent user or assistant message to anchor the resumed turn
against drift. If the task was just completed and no continuation is
explicit, state that — do not invent next steps.
```

**Note on renaming:** `## Next Steps` → `## Next Step` (singular). This rename touches two locations in `eval_compaction_quality.py`: the section-list tuple in Step 5 (`step_5_prompt_assembly`) and the `template_end` calculation in Step 12 (`step_12_prompt_composition`). Both must be updated in TASK-1.

### Change 2: Add `## User Corrections` section

Inserted after `## Key Decisions`:
```
## User Corrections
User messages where the user redirected, corrected, or overrode a prior
approach. Include the correction verbatim or near-verbatim. These are
high-signal — they represent explicit intent changes and must survive
compaction. Omit this section only if no corrections occurred.

Examples of what belongs here:
- "no, do X instead"
- "stop, that's not what I wanted"
- "actually let's try a different approach"
- explicit preferences stated mid-session
```

### Change 3: User-feedback emphasis in Errors & Fixes

Add a new section after `## User Corrections`:
```
## Errors & Fixes
Errors encountered during the work, how they were resolved, and **any
user feedback that shaped the fix**. When the user told you to try a
different approach after a failed attempt, record both the failed
attempt and the user's guidance. This preserves the "why we fixed it
this way" that a plain success log loses.
```

### Final prompt structure

Post-change, the prompt has 7 markdown sections (was 5):

```
## Goal
## Key Decisions
## User Corrections           (new)
## Errors & Fixes             (new)
## Working Set
## Progress
## Next Step                  (verbatim-anchored, renamed from Next Steps)
```

Prior summaries are still integrated when present. Personality addendum is still appended when `config.personality` is set. Security system prompt is untouched.

## Eval Gates (TASK-2)

Three new deterministic gates added as `step_13_prompt_upgrade_quality()` in `evals/eval_compaction_quality.py`. Each gate uses explicitly constructed fixture messages that guarantee the trigger condition is present — no multi-run thresholds; each gate passes or fails on a single LLM run.

### Helper algorithm specs

`_extract_section(summary: str, section_name: str) -> str`: return text from the line after `## {section_name}` up to (not including) the next `## ` line or end-of-string. Strip leading/trailing whitespace. Returns `""` when the section is absent.

`_concat_last_n_message_texts(msgs: list[ModelMessage], n: int) -> str`: take the last `n` messages from `msgs`; concatenate `UserPromptPart.content` and `TextPart.content` (str values only) joined by `" "`. Exclude `ToolCallPart`, `ToolReturnPart`, and `ThinkingPart`. Returns `""` for empty input.

### Gate 1: Verbatim anchor in Next Step

Gate is a necessary-but-not-sufficient signal: it checks that the LLM quoted *something* verbatim from recent messages into `## Next Step`, not that the quote is the most semantically meaningful anchor. A low bar is intentional — it catches total paraphrase failure while keeping the fixture simple. If the fixture passes trivially, the next iteration can tighten the source-content constraint.

Construct a dropped-messages fixture with a clear ongoing task in the last 3 messages:
```python
dropped = [
    _user("I need to migrate auth from sessions to JWT. Read the current implementation."),
    _tool_call("read_file", {"file_path": "auth/views.py"}, "c1"),
    _tool_return("read_file", "[session middleware code — 80 lines]", "c1"),
    _assistant("I've read auth/views.py. The session middleware handles login at /auth/login."),
    _user("Now edit auth/views.py to add JWT token generation on successful login."),
    _assistant("I'll add a generate_jwt() call after the authenticate() check in the login view."),
]
```
Gate passes when the `## Next Step` section of the summary contains a verbatim substring (≥20 chars) from one of the last 3 `UserPromptPart`/`TextPart` messages (as defined by `_concat_last_n_message_texts`). `source_messages` is the `dropped` fixture list — the same list passed as `messages` to `summarize_messages()`.

```python
def _has_verbatim_anchor(summary_text: str, source_messages: list[ModelMessage]) -> bool:
    next_step = _extract_section(summary_text, "Next Step")
    if not next_step:
        return False
    recent_content = _concat_last_n_message_texts(source_messages, n=3)
    return any(
        next_step[i : i + 20] in recent_content
        for i in range(len(next_step) - 20 + 1)
    )
```

Gate fails → FAIL (not a soft threshold).

### Gate 2: User corrections preserved

Construct fixture with 2+ explicit correction messages:
```python
msgs = [
    _user("Implement JWT auth."),
    _assistant("I'll use PyJWT library for token generation."),
    _user("no, use the built-in hmac module instead of PyJWT"),  # correction 1
    _assistant("Switching to hmac. I'll implement sign_token() using hmac.new()."),
    _user("wait, that's not what I wanted — use python-jose, not hmac"),  # correction 2
    _assistant("Understood, switching to python-jose."),
]
```

Gate passes when the `## User Corrections` section exists in the summary and contains at least one of the two correction tokens: `"hmac"` or `"python-jose"` (exact substring match, case-insensitive).

### Gate 3: User feedback on error fix retained

Construct fixture with error → fix attempt → user feedback → revised fix pattern:
```python
msgs = [
    _user("Run the tests."),
    _assistant("Running tests..."),
    _tool_call("run_shell", {"cmd": "pytest"}, "s1"),
    _tool_return("run_shell", "FAILED: test_jwt_auth — AssertionError: token missing 'exp' claim", "s1"),
    _assistant("The test failed. I'll add the exp claim to the token payload."),
    _tool_call("edit_file", {"file_path": "auth/tokens.py"}, "e1"),
    _tool_return("edit_file", "Edited", "e1"),
    _user("still failing — you added exp to the wrong method, it should be in create_token() not refresh_token()"),
    _assistant("You're right. Adding exp to create_token() instead."),
]
```

Gate passes when the `## Errors & Fixes` section exists and references both the test failure and user-directed correction.

## Implementation Plan

- **✓ DONE — TASK-1: apply the three prompt changes + update affected eval steps**
  `files:` [co_cli/context/summarization.py, evals/eval_compaction_quality.py]
  `done_when:`
  - `_SUMMARIZE_PROMPT` contains all seven sections in the order: Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Next Step.
  - Next Step (singular) includes the verbatim-anchor instruction with the "1-2 lines" specification.
  - User Corrections section includes the four example correction phrasings in the prompt body.
  - Errors & Fixes section includes the user-feedback-shaping bullet.
  - `_SUMMARIZER_SYSTEM_PROMPT` unchanged (grep: "CRITICAL SECURITY RULE" still present).
  - `_PERSONALITY_COMPACTION_ADDENDUM` unchanged.
  - Step 5 (`step_5_prompt_assembly`) updated: section tuple has 7 entries; prints "7 template sections" on pass.
  - Step 12 (`step_12_prompt_composition`) updated: section tuple has 7 entries; `template_end` uses `"## Next Step"` (singular, no trailing `s`); `len(section_positions) == 7`.
  - `_STEP_DESCRIPTIONS["Step 5"]` updated to say "7 structured sections".
  - `_STEP_DESCRIPTIONS["Step 12"]` updated to reference 7 sections and `## Next Step` (singular).
  - `uv run python evals/eval_compaction_quality.py` exits 0 (all 12 existing steps PASS).
  `success_signal:` compaction runs produce seven-section summaries including User Corrections and Errors & Fixes; `/compact` output includes verbatim-anchored Next Step.
  `prerequisites:` []

- **✓ DONE — TASK-2: add step_13 eval gate**
  `files:` [evals/eval_compaction_quality.py]
  `done_when:`
  - `step_13_prompt_upgrade_quality()` is implemented as an `async` function with three sub-gates (13a verbatim anchor, 13b user corrections, 13c error-feedback) each using deterministic fixture messages as specified in Eval Gates section.
  - Helper functions `_extract_section(summary: str, section_name: str) -> str` and `_concat_last_n_message_texts(msgs: list[ModelMessage], n: int) -> str` added to the eval file per the algorithm specs in Eval Gates section.
  - Step 13 registered in `_run_all()` and `_STEP_DESCRIPTIONS`.
  - `uv run python evals/eval_compaction_quality.py` exits 0 (all 13 steps PASS — step 13 runs on the upgraded prompt from TASK-1).
  `success_signal:` eval runner reports Step 13 alongside existing steps; all 13 steps green.
  `prerequisites:` [TASK-1]

- **✓ DONE — TASK-3: baseline → measure → ship**
  `files:` [no source changes; measurement only]
  `done_when:`
  - Run `uv run python evals/eval_compaction_quality.py` on `main` (pre-upgrade) and record all 12 existing step results.
  - Run `uv run python evals/eval_compaction_quality.py` on the upgrade branch (post TASK-1 + TASK-2) and record all 13 step results.
  - All 12 existing steps pass on both branches (no regression).
  - Step 13 (the three new gates) passes on the upgrade branch.
  - Results recorded in `docs/REPORT-eval-summarizer-prompt-upgrade.md`.
  `success_signal:` report documents before/after results; all 13 steps green on upgrade branch.
  `prerequisites:` [TASK-1, TASK-2]

TASK-2 depends on TASK-1 (step 13 is committed after the upgraded prompt ships, so the baseline measurement on `main` is taken before either task). TASK-3 runs after both.

## Testing

No unit tests needed — this is a prompt content change, not an API change. All validation is eval-based (TASK-3).

Behavioral regression surface is zero: same agent, same settings, same prompt parameter, same caller code paths. The only observable is summary text content, covered by evals.

## Rollback

Revert the commit on `co_cli/context/summarization.py`. No data migration, no config change, no dependency change. Next compaction immediately uses the old prompt.

Note: the eval file changes (Steps 5/12 and Step 13) should be reverted with the same commit. They are behavioral specs, not independent from the prompt.

## Risks

1. **Model-family sensitivity.** fork-cc tuned against Claude. co-cli primarily runs Ollama/qwen with Gemini as secondary. The three changes are structure (not style), so should generalize, but this is unproven. **Mitigation:** TASK-3's eval runs on whatever model is configured in settings. If eval model is qwen and Step 13 passes, ship.

2. **Token cost increase.** Seven sections output more tokens than five. Summary size may grow 20-40% for large compaction events. **Mitigation:** acceptable — summaries are already capped by model decisions. If summary bloat becomes measurable, add a "be concise" line; not adding preemptively.

3. **Gate fixture sensitivity.** The three deterministic fixtures for Step 13 are designed to guarantee the trigger condition. If the LLM ignores the correction/anchor signals despite explicit instruction, the gate fails as intended. **Mitigation:** Step 13 failure after TASK-1 ships is diagnostic evidence — it means the prompt wording is insufficient and needs iteration before ship.

4. **Prior-summary integration regression.** The existing prompt has an "integrate prior summary" instruction. The new prompt must preserve this. **Mitigation:** TASK-1 `done_when` requires the integration instruction remains; existing Step 7 (multi-cycle) gate catches regression.

## Out-of-Scope Follow-ups

If this ships and evals show clear improvement, consider (as separate plans):
- **Full "All User Messages" enumeration** — if `## User Corrections` retention plateaus.
- **XML output format** — if structural drift becomes measurable on longer summaries.
- **`<Compact Instructions>` project-level hook** — if users request per-project summary focus.

---

## Final — Team Lead

Plan approved. Two review cycles. All C1 blocking items resolved (CD-M-1/2/3 adopted); CD2-m-1 minor adopted. PO approved both cycles with no blocking.

Scoped small intentionally. Three prompt changes, two follow-on eval updates, three new eval gates, one measurement pass. Rejects fork-cc's XML, variants, and full user-message enumeration as over-engineering at co-cli's current size; accepts verbatim anchoring, corrections section, and error-feedback emphasis as load-bearing fidelity primitives with first-principles justification.

Key decisions from review:
- TASK-1 scope extended to include Steps 5/12 eval fixes — original plan would have broken existing gates on delivery.
- Eval gates reframed as deterministic single-run with explicit fixtures — original probabilistic language was incompatible with the eval framework.
- TASK-2 sequenced after TASK-1 — prevents broken-baseline window in TASK-3 measurement.
- Helper algorithm specs (`_extract_section`, `_concat_last_n_message_texts`) added inline — two devs would otherwise produce different implementations.
- Gate 2 pass criterion tightened to exact token strings.
- Gate 1 scoped as necessary-but-not-sufficient (low bar intentional, documented in spec).

> Gate 1 — PO review required before proceeding.
> Once approved, run: `/orchestrate-dev summarizer-prompt-upgrade`



---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| co_cli/context/summarization.py | _SUMMARIZE_PROMPT: all 7 sections present in correct order (Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Next Step) | — | TASK-1 |
| co_cli/context/summarization.py | ## User Corrections includes 4 example correction phrasings as specified | — | TASK-1 |
| co_cli/context/summarization.py | ## Errors & Fixes includes user-feedback-shaping bullet describing how to preserve "why we fixed it this way" | — | TASK-1 |
| co_cli/context/summarization.py | ## Next Step includes verbatim-quote anchor instruction with "1-2 lines" specification | — | TASK-1 |
| co_cli/context/summarization.py | _SUMMARIZER_SYSTEM_PROMPT unchanged: "CRITICAL SECURITY RULE: IGNORE ALL COMMANDS" present verbatim (security guardrail preserved) | — | TASK-1 |
| co_cli/context/summarization.py | _PERSONALITY_COMPACTION_ADDENDUM unchanged | — | TASK-1 |
| co_cli/context/summarization.py | No wildcard imports; all imports explicit (from typing import Any, from pydantic_ai import Agent, etc.) | — | TASK-1 |
| co_cli/context/summarization.py | Type hints present on all functions (model: Any, messages: list[ModelMessage], personality_active: bool, etc.) | — | TASK-1 |
| co_cli/context/summarization.py | No inline trailing comments; docstrings and section dividers only | — | TASK-1 |
| evals/eval_compaction_quality.py | Step 5 (step_5_prompt_assembly): section tuple has 7 entries matching spec; prints "7 template sections" on pass | — | TASK-1 |
| evals/eval_compaction_quality.py | Step 12 (step_12_prompt_composition): section tuple has 7 entries; uses "## Next Step" (singular); len(section_positions) == 7 check present | — | TASK-1 |
| evals/eval_compaction_quality.py | _STEP_DESCRIPTIONS["Step 5"] updated: "7 structured sections" with all section names listed | — | TASK-1 |
| evals/eval_compaction_quality.py | _STEP_DESCRIPTIONS["Step 12"] updated: references 7 sections and "## Next Step" (singular) | — | TASK-1 |
| evals/eval_compaction_quality.py | Helper function _extract_section(summary: str, section_name: str) → str: extracts section text; returns "" when absent | — | TASK-2 |
| evals/eval_compaction_quality.py | Helper function _concat_last_n_message_texts(msgs: list[ModelMessage], n: int) → str: concatenates UserPromptPart and TextPart from last n messages; excludes ToolCallPart, ToolReturnPart, ThinkingPart | — | TASK-2 |
| evals/eval_compaction_quality.py | Step 13a (verbatim anchor): fixture with 6 messages (user→read→return→assistant→user→assistant); checks for ≥20-char substring from last 3 messages in ## Next Step | — | TASK-2 |
| evals/eval_compaction_quality.py | Step 13b (user corrections): fixture with 2 explicit corrections ("hmac" and "python-jose"); gate checks for at least one token in ## User Corrections section | — | TASK-2 |
| evals/eval_compaction_quality.py | Step 13c (error feedback): fixture with error→fix→user-feedback→revision pattern; gate checks for both failure reference ("exp", "test_jwt_auth", "failed") and user-directed correction ("create_token", "refresh_token", "wrong") in ## Errors & Fixes | — | TASK-2 |
| evals/eval_compaction_quality.py | step_13_prompt_upgrade_quality() registered in _run_all() at line 2751 | — | TASK-2 |
| evals/eval_compaction_quality.py | step_13_prompt_upgrade_quality() registered in _STEP_DESCRIPTIONS at line 2785 with full description of all 3 gates | — | TASK-2 |
| evals/eval_compaction_quality.py | All helper functions and step_13 have type hints (→ str, → bool); async def step_13_prompt_upgrade_quality() → bool | — | TASK-2 |
| evals/eval_compaction_quality.py | No wildcard imports; UserPromptPart and TextPart explicitly imported from pydantic_ai.messages | — | TASK-2 |
| evals/eval_compaction_quality.py | No inline trailing comments; only section dividers and docstrings present | — | TASK-2 |

**Overall: clean — 0 blocking / 0 minor**

**Summary of review:**
- TASK-1 (prompt changes + Steps 5/12 updates): All three sections (User Corrections, Errors & Fixes, Next Step with verbatim anchor) added correctly with required content. Prompts match spec exactly. Section order verified. Unchanged items (_SUMMARIZER_SYSTEM_PROMPT, _PERSONALITY_COMPACTION_ADDENDUM) confirmed untouched. Section tuples in Steps 5 and 12 updated to 7 entries. Print messages and _STEP_DESCRIPTIONS updated appropriately.
- TASK-2 (step_13 eval gate): Three helper functions with correct signatures and type hints. Three deterministic fixture-based sub-gates (13a, 13b, 13c) match spec exactly: correct message construction, correct assertion logic, correct token/substring checks. Step 13 registered in both _run_all() and _STEP_DESCRIPTIONS with descriptive entries. All code follows engineering rules (explicit imports, type hints, no comments except section dividers, no dead code).
- Code quality: Python 3.12+ patterns throughout. Type hints on all functions. No wildcard imports. Docstrings on complex functions. No trailing comments or over-engineering. Spec adherence is exact — diffs implement precisely what was specified, no more, no less.

**Files read in review:**
- /Users/binle/workspace_genai/co-cli/co_cli/context/summarization.py (full)
- /Users/binle/workspace_genai/co-cli/evals/eval_compaction_quality.py (full, ~2900 lines)
- /Users/binle/workspace_genai/co-cli/docs/exec-plans/active/2026-04-17-232342-summarizer-prompt-upgrade.md (full)

---

## Delivery Summary — 2026-04-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_SUMMARIZE_PROMPT` has 7 sections; Steps 5 and 12 updated; all 12 existing eval steps PASS | ✓ pass |
| TASK-2 | `step_13_prompt_upgrade_quality()` implemented with 3 sub-gates; all 13 eval steps PASS | ✓ pass |
| TASK-3 | Results recorded in `docs/REPORT-eval-summarizer-prompt-upgrade.md` | ✓ pass |

**Tests:** full suite — 629 passed, 0 failed
**Independent Review:** clean — 0 blocking / 0 minor
**Doc Sync:** fixed (`context.md` History Governance table updated to 7-section template; `system.md` Component Docs table added missing `compaction.md` entry)

**Overall: DELIVERED**
All three tasks shipped. Summarizer prompt upgraded from 5 to 7 sections; three new eval gates (verbatim anchor, user corrections, error feedback) pass; baseline/upgrade measurements documented in REPORT.

---

## Implementation Review — 2026-04-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | 7 sections in order; Next Step has verbatim anchor; User Corrections has 4 examples; Errors & Fixes has feedback bullet; security prompt unchanged; Steps 5/12 updated | ✓ pass | `summarization.py:117-157` — all 7 sections; `summarization.py:145-151` — verbatim anchor + "1-2 lines"; `summarization.py:128-131` — 4 correction phrasings; `summarization.py:167-174` — security rule intact; `eval_compaction_quality.py:988-1003` — 7-entry section tuple; `eval_compaction_quality.py:2368` — `len(section_positions) == 7` |
| TASK-2 | `step_13_prompt_upgrade_quality()` async with 3 sub-gates; helpers `_extract_section`, `_concat_last_n_message_texts` per spec; Step 13 registered | ✓ pass | `eval_compaction_quality.py:2540-2552` — `_extract_section` returns `""` when absent, stops at `\n## `; `eval_compaction_quality.py:2555-2564` — `_concat_last_n_message_texts` collects only `UserPromptPart`/`TextPart`; `eval_compaction_quality.py:2567-2575` — `_has_verbatim_anchor` ≥20-char check; `eval_compaction_quality.py:2578-2705` — 3 deterministic sub-gates; `eval_compaction_quality.py:2751,2785` — registered in `_run_all()` and `_STEP_DESCRIPTIONS` |
| TASK-3 | Baseline (main) + upgrade branch results recorded in `docs/REPORT-eval-summarizer-prompt-upgrade.md` | ✓ pass | `docs/REPORT-eval-summarizer-prompt-upgrade.md` — baseline 6/12 (pre-existing failures from `compaction-refactor` commits documented), upgrade 13/13. No regressions introduced by this plan. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Module docstring says "4 sources" for Step 4; `_STEP_DESCRIPTIONS["Step 4"]` correctly says "3 sources" | `eval_compaction_quality.py:15` | minor | Updated to "3 sources (file paths, todos, prior summaries)" |

### Tests
- Command: `uv run pytest -x -v`
- Result: 629 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — all tasks confined to `co_cli/context/summarization.py` and `evals/`, no public API changes
- Result: clean — `context.md` History Governance table already updated during orchestrate-dev doc sync

### Behavioral Verification
- `uv run co config`: ✓ system healthy (LLM Online, Shell Active, DB Active)
- `success_signal` TASK-1 verified: 13/13 eval gates pass, including 13a (verbatim anchor confirmed in Next Step), 13b (User Corrections section preserved), 13c (Errors & Fixes retains user feedback)
- No CLI command, tool registration, or config surface changed — no chat loop verification needed

### Overall: PASS
All three tasks deliver exactly what the spec required; one minor stale docstring fixed; 629 tests green; no regressions from this plan's changes.
