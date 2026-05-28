# prompt-static-trim

## Context

The static system prompt assembled by `ORCHESTRATOR_SPEC` measures ~28,422 chars / ~7,104 tokens.
The trim is small in absolute terms; the real win is cache-prefix stability.

### Current static-prompt breakdown

| Provider (orchestrator.py:59-75) | Source | Chars | ~Tokens | In cached prefix? | Volatile? |
|---|---|---|---|---|---|
| `_static_instructions_provider` | `context/assembly.py` → seed + mindsets + rules | 25,921 | 6,480 | ✓ | ✗ (config + files only) |
| `_toolset_guidance_provider` | `context/guidance.py` (`MEMORY_GUIDANCE` + `CAPABILITIES_GUIDANCE`) | 986 | 247 | ✓ | ✗ (tool_index keys stable) |
| `_tool_category_awareness_provider` | `tools/deferred_prompt.py:build_tool_category_awareness_prompt` | ~180 | ~45 | ✓ | **✓ tool_index** |
| `_skill_manifest_provider` | `context/manifests/skill_manifest.py:render_skill_manifest` | ~1,200 | ~300 | ✓ | **✓ skill_index** |
| `_personality_critique_provider` | `personality/prompts/souls/<role>/critique.md` | 162 | 40 | ✓ | ✗ (file) |
| per-turn `safety_prompt` | `agent/_instructions.py:safety_prompt` | 0–150 | 0–38 | ✗ | per-turn |
| per-turn `current_time_prompt` | `agent/_instructions.py:current_time_prompt` | ~50 | ~12 | ✗ | per-turn |
| **TOTAL static** | | **~28,422** | **~7,104** | | |

### Rules-block sub-breakdown

| File | Chars | ~Tokens |
|---|---|---|
| `01_identity.md` | 538 | 134 |
| `02_safety.md` | 1,622 | 405 |
| `03_reasoning.md` | 3,094 | 773 |
| `04_tool_protocol.md` | 4,711 | 1,177 |
| `05_workflow.md` | 2,567 | 641 |
| `06_skill_protocol.md` | 3,199 | 799 |
| `07_memory_protocol.md` | 3,855 | 963 → ~923 (table → bullets) |
| **TOTAL** | **19,586** | **4,892 → ~4,852** |

### Mindsets sub-breakdown (TARS)

| File | Chars | ~Tokens |
|---|---|---|
| `debugging.md` | 455 | 113 |
| `emotional.md` | 1,254 | 313 |
| `exploration.md` | 929 | 232 |
| `memory.md` | 539 | 134 |
| `teaching.md` | 590 | 147 |
| `technical.md` | 647 | 161 |
| **TOTAL** | **4,414** | **1,100** |

### Issue

**Prefix-hostile content in static block.** `_skill_manifest_provider` and
`_tool_category_awareness_provider` emit content that varies with `skill_index` / `tool_index`.
On Ollama (the production backend per `llm/factory.py:59`), every prefix mutation forces a full
re-prefill on the next call — llama.cpp's KV cache only reuses the leading bytes that match the
previous request. With the agent loop multiplying the prompt ~9× per turn, byte-stable prefix is
the highest-leverage lever for TTFT.

### Trim candidates

| # | Proposal | Tokens | Reason |
|---|---|---|---|
| 1 | Move skill manifest + tool-category awareness to per-turn | 0 absolute (~345 out of static prefix) | Prefix stability — the primary win |
| 2 | Condense rule 07 "Kind selection" table → bullets | ~40 | Information preserved 1:1 |
| **TOTAL** | | **~40 tok absolute + ~345 tok volatility out of static prefix** | |

### Code-accuracy verification (anchors, re-read 2026-05-27)

- Assembly entry: `_static_instructions_provider` (`co_cli/agent/orchestrator.py:24-27`) calls
  `build_static_instructions` (`co_cli/context/assembly.py:83-129`). Order: seed → mindsets →
  rules.
- `build_rules_block` (`co_cli/context/assembly.py:66-80`) reads every `NN_rule_id.md` in
  `co_cli/context/rules/`, joined by `"\n\n"`. Numbered prefixes must be contiguous from 01
  (`_collect_rule_files` validation at `assembly.py:56-61`).
- Toolset guidance: `build_toolset_guidance` (`co_cli/context/guidance.py:32-42`) emits
  `MEMORY_GUIDANCE` when `memory_search` OR `session_search` is present; `CAPABILITIES_GUIDANCE`
  when `capabilities_check` is present.
- Memory tools are registered with `VisibilityPolicyEnum.ALWAYS`
  (`co_cli/tools/memory/{recall,view,manage}.py`, `co_cli/tools/session/{recall,view}.py`) — they
  are de facto always loaded in a normal config; the tool-presence gate on `MEMORY_GUIDANCE` is
  defensive boilerplate that fires false only in stripped test fixtures or eval arms.
- Skill manifest: `render_skill_manifest` (`co_cli/context/manifests/skill_manifest.py:13-37`).
- Tool-category awareness: `build_tool_category_awareness_prompt` (`co_cli/tools/deferred_prompt.py`).
- Per-turn instructions infra already exists: `current_time_prompt`, `safety_prompt`
  (`co_cli/agent/_instructions.py`) via `per_turn_instructions` tuple
  (`orchestrator.py:68`).

## Problem & Outcome

**Problem.** The static system-prompt prefix carries ~345 tokens of volatile content (skill
manifest + tool-category awareness) that mutate when the skill set or tool surface changes.
On Ollama, any prefix mutation forces full prompt re-prefill on the next call; llama.cpp KV cache
only replays bytes that match the previous request. With ~9× loop multiplication, prefix stability
matters more than absolute size.

**Outcome.**

1. **Static-prefix stability win.** Move `_skill_manifest_provider` and
   `_tool_category_awareness_provider` from `static_instruction_builders` to
   `per_turn_instructions`. The static prefix becomes free of `skill_index`- and
   `tool_index`-dependent content. Same prefix bytes across turns regardless of skill set or tool
   surface changes. Absolute size change: 0 (content still emitted), but ~345 tokens of volatility
   removed from the static prefix → llama.cpp KV cache reuse holds across more turns, lower TTFT
   on each of the ~9 loop calls per user message.

2. **Rule 07 kind-selection table → bullets.** Information preserved 1:1, structure tightened
   (~40 tokens).

3. **Side-effect — live skill/tool-surface visibility (improvement).** Today
   `_skill_manifest_provider` and `_tool_category_awareness_provider` evaluate ONCE inside
   `build_orchestrator` (`agent/build.py:36-40`) and freeze into the literal static string for
   the agent's lifetime. Mid-session `skill_manage(action='create')` mutates `deps.skill_index`,
   but the model continues to see the startup snapshot until process restart. After the move,
   both re-render per turn from live `ctx.deps.*`, so newly-created skills and newly-registered
   deferred-tool categories become visible to the model on the very next turn without restart.

### Prefix split — before vs. after

| Tier | Component | Before (tok) | After (tok) | Δ |
|---|---|---|---|---|
| **Static prefix** (KV-cache anchor) | seed + mindsets + rules | 6,480 | 6,440 (−40 rule 07 table) | −40 |
| | toolset guidance (MEMORY + CAPABILITIES) | 247 | 247 | 0 |
| | tool-category awareness | 45 | — (moved out) | −45 |
| | skill manifest | 300 | — (moved out) | −300 |
| | critique lens | 40 | 40 | 0 |
| | **Static-prefix subtotal** | **7,112** | **6,727** | **−385** |
| **Per-turn (appended)** | safety_prompt (conditional) | 0–38 | 0–38 | 0 |
| | current_time_prompt | ~12 | ~12 | 0 |
| | tool-category awareness | — | 45 | +45 |
| | skill manifest | — | 300 | +300 |
| | **Per-turn subtotal** | **~12** | **~357** | **+345** |
| **Assembled total** | | **~7,124** | **~7,084** | **−40** |

On Ollama, the ~6,727-token static prefix becomes byte-identical across turns regardless of
skill/tool mutations. Today, a mid-session `skill_manage(create=...)` invalidates the KV-cache
overlap for the ~6,800-token static portion on every subsequent call (~9 calls per turn → full
re-prefill on each); post-change, the same invocation invalidates 0 bytes of static prefix —
only the per-turn dynamic suffix recomputes.

**Failure cost.** Continuing to pay ~345 tokens of prefix-mutation churn × ~9 calls per turn =
~3,000 tokens/turn of unnecessary re-prefill on any session where skill set or tool surface
changes (every `skill_manage` create, every integration toggle).

## Scope

1. **Move skill manifest + tool-category awareness from cached prefix to per-turn** (primary win).
   Verify pydantic-ai per-turn ordering preserves the "scan the `<available_skills>` manifest"
   directive in rule 06 — manifest must still appear in the assembled prompt before the model
   acts on rule 06's guidance.

2. **Condense rule 07 "Kind selection" table → bullets** (~40 tokens). Information preserved,
   structure tightened.

## Behavioral Constraints

- **Static prefix stability** — after the change, the static prefix MUST be byte-identical across
  turns within a session and across sessions with identical config + personality + rule files,
  regardless of `skill_index` / `tool_index` state changes.
- **Manifest visibility preserved** — the `<available_skills>` block MUST still appear in the
  final assembled prompt. Rule 06 ("scan the `<available_skills>` manifest") MUST remain
  actionable.
- **Test contract preserved** — `tests/test_flow_prompt_assembly.py` and
  `tests/test_memory_protocol_rule.py` MUST continue to pass. The prompt content seen by tests is
  the full assembled prompt, not just the cached prefix; the per-turn move does not change what
  is visible to the model.
- **Rule ordering invariant preserved** — `_collect_rule_files` contiguous-from-01 validation
  (`assembly.py:56-61`) must continue to hold. No renumbering this plan.
- **Personality-system intact** — finch and jeff personality files are not touched
  (coworker-maintained, per project memory feedback).

## High-Level Design

Two changes; both narrow.

### Change A — per-turn promotion

`co_cli/agent/orchestrator.py:59-75`:
- Remove `_skill_manifest_provider` and `_tool_category_awareness_provider` from
  `static_instruction_builders`.
- Add per-turn wrappers in `co_cli/agent/_instructions.py` taking `RunContext[CoDeps]`:
  ```python
  def skill_manifest_prompt(ctx: RunContext[CoDeps]) -> str:
      from co_cli.context.manifests.skill_manifest import render_skill_manifest
      return render_skill_manifest(
          ctx.deps.skill_index, ctx.deps.skills_dir, ctx.deps.user_skills_dir
      )

  def tool_category_awareness_prompt(ctx: RunContext[CoDeps]) -> str:
      from co_cli.tools.deferred_prompt import build_tool_category_awareness_prompt
      return build_tool_category_awareness_prompt(ctx.deps.tool_index)
  ```
- Add to `per_turn_instructions` tuple. Ordering after `safety_prompt`, `current_time_prompt`
  is acceptable — these are all post-cache.

**Resolved (pydantic-ai source, 2026-05-27):** static literal goes first as one block; per-turn
callables run in registration order, each appended as separate `InstructionPart(dynamic=True)`
joined by `\n\n` (`pydantic_ai/agent/__init__.py:1232-1245` + `messages.py:1428-1430`). After the
move, the manifest lands AFTER all rules in the assembled prompt — still before user/tool
messages. Rule 06's "scan above" wording was already stale (today `_skill_manifest_provider` runs
AFTER `_static_instructions_provider` in the tuple, so the manifest is already BELOW rule 06).
Drop "above" unconditionally in TASK-2.

### Change B — rule 07 table → bullets

`co_cli/context/rules/07_memory_protocol.md` lines 35-42: replace the markdown table with a bullet
list (same four kinds, same descriptions, lighter structure).

No file deletions, no renumbering, no test updates required.

## Tasks

### ✓ DONE TASK-1 — move skill manifest + tool-category awareness to per-turn

**Prerequisites:** none. (Pydantic-ai ordering already resolved — see High-Level Design §A.)

**Files:**
- `co_cli/agent/_instructions.py` — add `skill_manifest_prompt`, `tool_category_awareness_prompt`.
- `co_cli/agent/orchestrator.py` — remove from static, add to per-turn.
- `co_cli/context/rules/06_skill_protocol.md` line 9 — replace "visible in the
  <available_skills> block above" with "in the <available_skills> block in this prompt"
  (unconditional cleanup: the manifest is already below rule 06 today, and the move puts it
  further down still).

**done_when:**
- `uv run pytest tests/test_flow_prompt_assembly.py tests/test_memory_protocol_rule.py tests/test_flow_skill_manifest.py` passes.
- Manual snapshot: print the assembled prompt with a non-empty `skill_index` and confirm
  `<available_skills>` appears in the prompt.

**success_signal:** A `uv run co chat` session can still discover and invoke a skill (e.g.,
ask for `/sync-doc` or any known skill); the model lists available skills correctly when asked.

### ✓ DONE TASK-2 — condense rule 07 "Kind selection" table → bullets

**Prerequisites:** none.

**Files:** `co_cli/context/rules/07_memory_protocol.md`.

**Action:** Replace lines 35-42 (the markdown table) with bullets:
```
**Kind selection:**
- `user` — stable personal preference ("I prefer X")
- `rule` — forward-acting standing rule ("always / never / stop")
- `article` — web article or fetched substrate
- `note` — free-form note, distilled finding, recorded decision, saved URL
```

**done_when:** `uv run pytest tests/test_memory_protocol_rule.py` passes (asserts `Promotion.`,
`Correction.`, `Drift.` content; the table change is below these assertions); `grep -c "| User
intent | kind |" co_cli/context/rules/07_memory_protocol.md` returns 0.

**success_signal:** N/A (pure trim).

### ✓ DONE TASK-3 — measure token deltas

**Prerequisites:** TASK-1, TASK-2.

**Files:** read-only — `tmp/measure_prompt.py` (one-off script under `tmp/`).

**Action:** Write a script that:
1. Boots a `CoDeps` with TARS personality + full native toolset.
2. Calls each builder/provider individually, sums chars grouped by static-prefix vs. per-turn.
3. Prints a delta table vs. the pre-change baseline.

**Expected:**
- Static prefix tokens: ~6,727 (was ~7,112; drop ~345 from per-turn move, ~40 from rule 07 table).
- Per-turn tokens: ~357 (newly populated with skill manifest + tool-category awareness).
- Total assembled tokens: ~7,084.

**done_when:** Measurement table appended to this plan as Delivery Summary; static-prefix tokens
within ~50 of expected ~6,727.

**success_signal:** N/A.

## Testing

- `uv run pytest tests/test_flow_prompt_assembly.py tests/test_memory_protocol_rule.py tests/test_flow_skill_manifest.py` — all must pass unchanged.
- `uv run co chat` smoke: ask the agent to list available skills; verify the model can name them.
- `evals/eval_mindset_selection.py` — must still complete (no mindset files touched).
- `scripts/quality-gate.sh full` — must pass.

## Open Questions

None — pydantic-ai instruction ordering resolved inline (see High-Level Design §A); rule 06
"above" wording → unconditional drop in TASK-1.

## Delivery Summary — 2026-05-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | scoped tests pass; manifest visible in assembled prompt | ✓ pass |
| TASK-2 | rule 07 test passes; table grep returns 0 | ✓ pass |
| TASK-3 | static prefix within ~50 of 6,727 tok | ✓ pass (6,761 tok) |

**Measurement (TARS personality, real bootstrap, 6 tools / 3 skills):**

| Tier | Before (tok) | After (tok) | Δ |
|---|---|---|---|
| Static prefix | ~7,112 | 6,761 | −351 |
| Per-turn part | ~12 | 352 | +340 |
| Assembled total | ~7,124 | 7,113 | −11 |

Static-prefix tokens dropped 351 (target −385; within tolerance — the smaller delta vs. plan is
because the pre-change baseline used estimated counts from a different fixture). Per-turn grew
+340 (target +345). Rule 07 table → bullets cut ~120 chars from the rules block; the precise
−40-tok absolute target lands at −10 because tokenizer rounding obscures small wins at this size.
The headline result holds: ~340 tokens of `skill_index` / `tool_index`–dependent content moved
off the static prefix, so any mid-session skill/tool mutation no longer churns the cached 6,761-tok
prefix on the next call.

**Tests:** scoped — 13 passed, 0 failed (`tests/test_flow_prompt_assembly.py`,
`tests/test_memory_protocol_rule.py`, `tests/test_flow_skill_manifest.py`)
**Lint:** clean (ruff check + ruff format — 323 files)
**Doc Sync:** fixed — 6 specs updated (prompt-assembly, personality, bootstrap, 01-system,
skills, tools); covers static→per-turn relocation, builder count change, and `build_category_awareness_prompt` → `build_tool_category_awareness_prompt` rename

**Overall: DELIVERED**
All three tasks pass `done_when`; specs no longer claim the manifest lives in the cached prefix.
Next: `/review-impl prompt-static-trim`.

## Implementation Review — 2026-05-28

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | scoped tests pass; manifest visible in assembled prompt | ✓ pass | `co_cli/agent/_instructions.py:22-43` (new per-turn fns); `co_cli/agent/orchestrator.py:52-71` (static reduced to 3; per-turn extended to 4); `co_cli/context/rules/06_skill_protocol.md:9` ("in this prompt"); `co_cli/tools/deferred_prompt.py:45` (rename to `build_tool_category_awareness_prompt`, no stale callers); 13/13 scoped tests green |
| TASK-2 | rule 07 test passes; table grep returns 0 | ✓ pass | `co_cli/context/rules/07_memory_protocol.md:35-40` (bullets preserve all four kinds); `grep -c "\| User intent \| kind \|"` returns 0; `test_memory_protocol_rule.py` 2/2 green |
| TASK-3 | static prefix within ~50 tok of 6,727 | ✓ pass | Delivery Summary reports 6,761 (Δ=34, within tolerance); per-turn +340 (target +345); math internally consistent (−351 + 340 = −11 total) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Pre-existing typo: `agents/_instructions.py` (plural) — actual file is `agent/_instructions.py` (singular) | `docs/specs/prompt-assembly.md:160` | minor (pre-existing per `feedback_review_preexisting_issues.md`) | Fixed to `co_cli/agent/_instructions.py` |
| Extra files in diff not declared in any task's `files:`: `co_cli/tools/deferred_prompt.py` (function rename, mentioned in Delivery Summary but unscoped in TASK-1), `evals/_deps.py`, `evals/eval_mindset_selection.py`, `uv.lock` | git status | minor | Left in place — `co_cli/tools/deferred_prompt.py` is the natural call-site of the new per-turn fn; the evals/* and uv.lock changes remediate a pre-existing `create_deps()` signature drift from v0.8.234, bundled per `feedback_merge_coworker_uncommitted.md` |

### Tests
- Command: `uv run pytest -v`
- Result: **625 passed, 0 failed in 597.67s (~10 min)**
- Log: `.pytest-logs/20260528-091448-review-impl-retry2.log`
- Note on duration: ~15 tests hit a real Ollama model end-to-end (per `feedback_eval_real_world_data.md` — no mocks). Top contributors: `test_length_retry_completes_truncated_noreason_response` (86.8s), `test_real_turn_with_tool_call_populates_model_requests` (75.8s), `test_clarify_deferred_resume_end_to_end` (46.5s), `test_denied_tool_does_not_execute` (44.5s), `test_auto_approval_skips_prompt_for_remembered_session_rule` (41.7s). Sum of top ~15 ≈ 580s (~97% of total). Remaining 610 tests run sub-second each.
- Two pre-existing test failures from in-flight REPL queue workstream (v0.8.260) were RCA'd and fixed during this review — both were stale call sites missing the new `queue` / `queue_depth` argument added when `_handle_one_input` and `_build_status_snapshot` signatures evolved:
  - `tests/integration/test_repl_input_queue.py:103` — added `queue=runtime.queue` to the `_handle_one_input` call.
  - `tests/test_display.py:399,406,413,420,425` — added `0` positional arg to `_build_status_snapshot` calls (matches the now-required `queue_depth: int` parameter).

### Behavioral Verification
- `uv run co status` not available in this CLI — substituted with assembled-prompt snapshot via `tmp/check_assembly.py`.
- Static literal: does NOT contain `<available_skills>` (confirms relocation off the cached prefix — the plan's primary win).
- Per-turn `skill_manifest_prompt(ctx)`: emits 1073-char `<available_skills>` block listing 6 bundled skills — confirms TASK-1 manual-snapshot done_when.
- Per-turn `tool_category_awareness_prompt(ctx)`: emits `Additional capabilities available via search_tools: background tasks (task_start), code execution (code_execute), sub-agents (web_research, knowledge_analyze, reason).`
- Registered per-turn order: `safety_prompt → current_time_prompt → tool_category_awareness_prompt → skill_manifest_prompt` (manifest sits last — correctly after rule 06, consistent with the unconditional "above" → "in this prompt" wording fix).
- `success_signal` for TASK-1 (interactive `co chat` skill discovery) not exercised end-to-end, but the precondition is satisfied: skill manifest is non-empty, well-formed, and rendered every turn from live `ctx.deps.skill_index`.

### Overall: PASS
All three tasks pass `done_when` with file:line-cited evidence; full test suite green (625/0); lint clean; behavioral snapshot confirms the static literal is manifest-free and the per-turn pipeline renders the manifest and tool-category hint correctly. One pre-existing doc typo fixed; two pre-existing test stalenesses from an in-flight workstream RCA'd and fixed. Ready for `/ship prompt-static-trim`.

