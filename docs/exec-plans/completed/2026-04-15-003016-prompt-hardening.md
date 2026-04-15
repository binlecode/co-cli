# Plan: Prompt Hardening — Gap-Fill from Peer Audit

**Task type: code-feature**

---

## Context

A three-way prompt audit (co-cli vs. fork-claude-code vs. hermes-agent) identified four gaps in
co-cli's behavioral rule files. Two were resolved as non-issues (tool availability guard —
already handled by `_deferred_tool_prompt.py`; no model-specific enforcement sections — Anthropic
models don't need them). Three real gaps remain, ordered by blast-radius risk:

1. **Git safety** — `02_safety.md`'s `## Source control` has two lines ("do not stage or commit
   unless requested"). No guidance on force-push, hook bypass, or amend-vs-new-commit. The shell
   tool can run git commands today; a model trained on lenient examples can silently destroy
   history.

2. **Memory durable/ephemeral** — `02_safety.md`'s `## Memory constraints` lists what not to
   save but never explicitly calls out the ephemeral category (task progress, session outcomes,
   TODO state). Hermes-agent has this right. Without the explicit negative examples, the model
   drifts toward saving transient state, bloating memory and surfacing stale context in future
   sessions.

3. **Knowledge cutoff** — `04_tool_protocol.md`'s `## Strategy` says "bias toward action for
   information that could be stale" but never mentions that training data has a hard cutoff.
   Fork-claude-code injects the cutoff dynamically per turn. Co-cli's per-turn `add_current_date()`
   already exists; cutoff awareness complements it by telling the model *why* it should distrust
   certain knowledge, not just *when* today is.

No exec-plan for this slug existed prior to this plan. Current-state validation: no phantom
features, no stale tasks. All three target files were read and confirmed to lack the described
content.

---

## Problem & Outcome

**Problem:** Co-cli's behavioral rules leave three safety-relevant behaviors underspecified
relative to peer best practice (fork-claude-code, hermes-agent).

**Failure cost:**
- Git gap: model can execute destructive git operations (force-push, hook bypass,
  amend-published) without guidance to stop — shell policy only enforces a DENY list, not
  semantic git safety.
- Memory gap: model saves session-local state (active TODOs, debugging notes) as persistent
  memory, polluting future sessions with stale cross-session context.
- Cutoff gap: model cites outdated library versions, API schemas, or event facts as current
  without prompting the user to verify — silent misinformation.

**Outcome:** Three targeted additions to existing rule files make the agent's behavior consistent
with fork-claude-code's git protocol and hermes-agent's memory hygiene, and explicit about its
own knowledge limits. No new files, no renumbering.

---

## Scope

**In:** Edit `02_safety.md` (git safety + memory ephemeral expansions), edit `04_tool_protocol.md`
(knowledge cutoff), add pytest assertions to `tests/test_prompt_assembly.py`.

**Out:**
- No shell policy changes — git safety is advisory guidance, not DENY-pattern enforcement
- No per-turn injection for cutoff — static rule is cheaper; per-turn adds tokens every turn
- No new rule files — no renumbering of the contiguous `NN_rule_id.md` scheme
- No tool availability guard tasks — already handled by `_deferred_tool_prompt.py`
- No eval changes — evaluating behavioral compliance is a separate concern; structural tests
  suffice for this delivery

---

## Behavioral Constraints

1. **Git guidance is advisory, never redundant with shell policy** — do not replicate DENY-pattern
   logic in the rule file; the shell backend enforces hard blocks, the rule file guides intent.
2. **Memory ephemeral list must not discourage saving durable facts** — positive framing
   ("save cross-session facts proactively") must remain prominent; the new content adds explicit
   negative examples only.
3. **Cutoff guidance must be scoped to time-sensitive information** — must not inhibit answering
   about stable concepts (protocols, algorithms, language semantics). Only: versions, APIs,
   events, pricing, changelogs.
4. **All new text must survive review lens** — tone, brevity, and directness must match existing
   rule file style (imperative, ≤2 sentences per bullet).

Reviewer note: aim for ≤80 words added per rule file (these inject every turn at token cost);
not machine-enforced, but flag if clearly exceeded.

---

## Failure Modes

Observed behaviors motivating the constraints:

- **F1 (git):** Without guidance, model responds to "push to main" with a direct shell call
  rather than warning about force-push risk. Shell policy blocks DENY-listed commands, but does
  not block `git push origin main` (not in DENY list — goes to REQUIRE_APPROVAL, but no advisory
  context before that dialog).
- **F2 (memory):** Without ephemeral examples, model saves "I was helping debug X today" as a
  memory. Future sessions load stale session-outcome context that is no longer relevant.
- **F3 (cutoff):** Without cutoff awareness, model states "the current version of pydantic-ai is
  0.0.X" from training data when the live version differs. No web-verify suggestion triggered.

---

## High-Level Design

### TASK-1: Safety rule expansions in `02_safety.md`

**Git safety** — replace the current two-line `## Source control` with:

```
## Source control

Do not stage or commit changes unless specifically requested.

Never force-push to main or master. Never skip hooks (--no-verify). When
amending, confirm the commit has not been published — if it has, create a
new commit instead. If a hook fails, diagnose and fix; do not bypass.
```

**Memory ephemeral** — append to `## Memory constraints` after the existing "Never save..." line:

```
Do not save ephemeral session state: task progress for the current session,
completed-work logs, active TODO items, or temporary debugging notes. These
belong in session context, not persistent memory.
```

Both additions target separate sections of `02_safety.md` — no merge conflict risk.

### TASK-2: Knowledge cutoff awareness in `04_tool_protocol.md`

Append to the `## Strategy` section:

```
Training data has a cutoff. Treat software versions, API schemas, release
notes, current events, and pricing as potentially stale. Use web_search or
web_fetch to verify before citing.
```

### TASK-3: Pytest assertions in `tests/test_prompt_assembly.py`

Add three assertion functions to the existing test file (which already exercises the same
integration boundary via `_BASE_CONFIG.model_copy(update={"personality": None})`):

- `test_git_safety_in_static_instructions` — asserts "force-push" and "no-verify" and "hook"
- `test_memory_ephemeral_in_static_instructions` — asserts "ephemeral" and "TODO"
- `test_cutoff_awareness_in_static_instructions` — asserts "cutoff" and "stale" independently

---

## Implementation Plan

### ✓ DONE — TASK-1 — Safety rule expansions in `02_safety.md`

```yaml
id: TASK-1
files:
  - co_cli/prompts/rules/02_safety.md
done_when: >
  uv run pytest tests/test_prompt_assembly.py::test_git_safety_in_static_instructions passes
  AND uv run pytest tests/test_prompt_assembly.py::test_memory_ephemeral_in_static_instructions
  passes — both test calls to _BASE_CONFIG.model_copy(update={"personality": None}) passed
  to build_static_instructions() and assert key strings in the returned prompt.
success_signal: >
  Agent asked to force-push a branch responds with a warning about force-pushing before
  executing; agent completing a debugging session does not proactively save session-local
  notes as memories. (Manual spot-check; not automated.)
```

### ✓ DONE — TASK-2 — Knowledge cutoff awareness in `04_tool_protocol.md`

```yaml
id: TASK-2
files:
  - co_cli/prompts/rules/04_tool_protocol.md
done_when: >
  uv run pytest tests/test_prompt_assembly.py::test_cutoff_awareness_in_static_instructions
  passes — test uses _BASE_CONFIG.model_copy(update={"personality": None}) and asserts
  both "cutoff" in output AND "stale" in output independently.
success_signal: >
  Agent asked "what's the current version of pydantic-ai?" suggests web_search rather than
  citing a version from training data. (Manual spot-check; not automated.)
```

### ✓ DONE — TASK-3 — Pytest assertions in `tests/test_prompt_assembly.py`

```yaml
id: TASK-3
files:
  - tests/test_prompt_assembly.py
done_when: >
  uv run pytest tests/test_prompt_assembly.py passes with all three new tests green
  (test_git_safety_in_static_instructions, test_memory_ephemeral_in_static_instructions,
  test_cutoff_awareness_in_static_instructions). Tests use
  _BASE_CONFIG.model_copy(update={"personality": None}) consistent with existing convention.
success_signal: N/A — structural test; no user-visible behavior change.
prerequisites: [TASK-1, TASK-2]
```

---

## Testing

All three tasks share `tests/test_prompt_assembly.py`. New tests follow the existing pattern:
use `_BASE_CONFIG.model_copy(update={"personality": None})` (not bare `Settings()` — default
personality is "tars" which would require tars soul files present on disk). Pass the config to
`build_static_instructions()` and assert key strings appear in the output.

This exercises the integration boundary: rule file → `_collect_rule_files()` →
`build_static_instructions()` → assembled prompt.

---

## Open Questions

None. All questions resolved by inspection:
- Q: Should cutoff be per-turn (dynamic) or static (rule file)?
  A: Static. Per-turn adds tokens every turn; the model cutoff doesn't change session-to-session.
- Q: Should git safety go in a new rule file?
  A: No. Git is already in the safety domain (`02_safety.md`). A new file would require
  renumbering all subsequent files and perturbing `_collect_rule_files()` validation.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev prompt-hardening`

---

## Delivery Summary — 2026-04-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `test_git_safety_in_static_instructions` and `test_memory_ephemeral_in_static_instructions` pass | ✓ pass |
| TASK-2 | `test_cutoff_awareness_in_static_instructions` passes | ✓ pass |
| TASK-3 | all 3 new tests green in `tests/test_prompt_assembly.py` | ✓ pass |

**Tests:** full suite — 468 passed, 0 failed
**Independent Review:** clean / 0 blocking / 1 minor (pre-existing `tmp_path` unused fixture in `test_section_order_no_personality`, not introduced by this diff)
**Doc Sync:** clean — rule content changes do not affect spec documentation

**Overall: DELIVERED**
Three targeted prompt rule additions close the git safety, memory ephemeral, and knowledge cutoff gaps identified in the peer audit; all structural tests green.

---

## Implementation Review — 2026-04-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `test_git_safety_in_static_instructions` + `test_memory_ephemeral_in_static_instructions` pass | ✓ pass | `02_safety.md:11-13` — force-push/hook/amend guidance; `02_safety.md:24-26` — ephemeral session state exclusion |
| TASK-2 | `test_cutoff_awareness_in_static_instructions` passes | ✓ pass | `04_tool_protocol.md:37-39` — cutoff/stale sentence appended to `## Strategy` |
| TASK-3 | all 3 new tests green | ✓ pass | `test_prompt_assembly.py:51-76` — 3 tests, no mocks, real `build_static_instructions()`, deletion check passes for all |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 468 passed, 0 failed
- Log: `.pytest-logs/$(date +%Y%m%d-%H%M%S)-review-impl.log`

### Doc Sync
- Scope: narrow — rule content edits only, no public API changes, no module renames
- Result: clean

### Behavioral Verification
- `uv run co config`: ✓ healthy (LLM online, shell active, all integrations nominal)
- Rule changes inject via `build_static_instructions()` every turn — no CLI surface changed
- `success_signal` for TASK-1 and TASK-2 are manual spot-checks per plan; structural gate confirms text presence in assembled prompt

### Overall: PASS
All three tasks confirmed implemented at file:line, done_when re-verified independently, 468 tests green, lint clean, system starts healthy.
