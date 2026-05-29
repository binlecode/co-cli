# deferred-tool-stubs

> **Child 1b of** `2026-05-28-141854-prefill-schema-trim.md`. Supersedes the withdrawn child 1
> (`skill-manage-defer`), whose smoke proved the real blocker is *discoverability of deferred
> tools*, not the model's raw ability to use them. This plan fixes discoverability for **every**
> deferred tool, then re-tests the `skill_manage` DEFERRED flip on top of it.

## Context

The parent measured tool schemas at ~47% of every cold prefill; DEFERRED removes a tool's full
schema and loads it lazily via `search_tools`. Child 1 flipped `skill_manage` to DEFERRED and
gated on a discovery smoke. The smoke (`tmp/discovery_smoke.py`, driven through `run_turn` + a real
approval loop) **failed**: independent discovery ≈ 1/2, ~177s even on success, and a failure mode
where the model fires `search_tools` repeatedly then falls back to `file_write` with a bare
relative path — leaving a stray skill `.md` in the launch directory.

**Root cause found.** The static-prompt hint for deferred tools,
`build_tool_category_awareness_prompt` (`co_cli/tools/deferred_prompt.py`), is:
1. **A hardcoded allowlist** — `_NATIVE_TOOL_CATEGORIES` lists only `task_*`, `web_research`,
   `knowledge_analyze`. `skill_manage` is **absent**, so when it went DEFERRED it had **zero**
   awareness signal in the prompt. Any *future* deferred tool falls into the same gap silently.
2. **Category-level, not tool-level** — it emits domains ("background tasks (task_start)"), not a
   per-tool name + when-to-use. The model is left to guess names and form `search_tools` keywords.

### Current-state facts (verified against source)

- `tool_category_awareness_prompt` (`co_cli/agent/_instructions.py:22`) is a **per-turn** builder,
  placed *post-static* on purpose (v0.8.266) so mid-session integration registration is reflected
  next turn without invalidating the cached static prefix. **This placement must be preserved.**
- It emits: `"Additional capabilities available via search_tools: background tasks (task_start),
  sub-agents (web_research, knowledge_analyze), <integration labels>."`
- `ToolInfo` (`co_cli/deps.py:99`) carries `name`, `description`, `visibility`, `integration`,
  `source` — enough to auto-generate `name + one-liner` stubs. It carries **no parameter/arg
  metadata** (arg names would require unwrapping the `ToolDefinition`).
- Deferred tools in a full bootstrap: ~7 native (`task_start/status/list/cancel`, `web_research`,
  `knowledge_analyze`, + `skill_manage` if re-flipped) plus config-gated integrations (obsidian 3,
  google 7) and MCP (context7 2) — only those *registered* appear in `tool_index`.
- **No test** references `build_tool_category_awareness_prompt` or the awareness prompt today.

## Failure Modes (observed, from `tmp/discovery_smoke.py`)

- **FM-1 — silent omission.** A deferred tool not in the hardcoded allowlist (`skill_manage`) gets
  no prompt signal; the model only learns it exists if it independently calls `capabilities_check`.
- **FM-2 — discovery thrash.** Even when the model knows the tool exists, it loops `search_tools`
  (×3 observed) and stalls; turns hit the 180s ceiling.
- **FM-3 — wrong-tool fallback + cwd pollution.** On giving up, the model substitutes `file_write`
  with a bare relative path, writing a stray skill file into the user's working directory.

## Problem & Outcome

**Problem.** Deferred tools lack a complete, per-tool, always-present discovery signal. The
existing hint is a hardcoded category allowlist that is both incomplete (omits `skill_manage` and
any new deferred tool) and too coarse (no name/purpose), so small models thrash or fail to
discover.

**Failure cost.** Without this: every DEFERRED promotion is a coin-flip on a 3B-active model —
capabilities silently become unreachable, turns stall, and the model pollutes the user's cwd with
fallback `file_write`s. The DEFERRED lever (the parent's highest-leverage prefill trim) stays
unusable beyond the few tools the allowlist happens to name.

**Outcome.** Replace the hardcoded category allowlist with **auto-generated per-tool stubs**
(`name + one-line purpose`) built from `tool_index` — complete by construction, no list to forget.
Add the missing test coverage. Then re-test the `skill_manage` DEFERRED flip on top of the stubs;
re-flip only if discovery clears the bar.

**The bet (legible at Gate 1).** The predecessor's failure had two coupled symptoms: FM-1 (the tool
was silently omitted from the prompt) and FM-2/3 (thrash + cwd pollution). This plan directly fixes
**only FM-1**, betting that *awareness* was the dominant blocker. TASK-3 is the falsification test:
if discovery still fails with complete stubs live, the binding constraint is the `search_tools`→call
*loader mechanic* (FM-2/3), not awareness — and `skill_manage` stays ALWAYS while that becomes a
separate problem. TASK-1+TASK-2 deliver standalone value (complete-by-construction awareness + a
regression guard) **regardless of how that bet resolves.**

## Scope

In: the deferred-awareness prompt builder, its test, a repeatable discovery eval, and a gated
`skill_manage` re-flip. Out: trimming any schema, touching ALWAYS tools, arg-level stub metadata,
changing the static/per-turn split, or promoting any *other* tool to DEFERRED.

## Behavioral Constraints

- **Keep the builder per-turn** (post-static). Do not move stubs into the cached static prefix —
  mid-session integration registration must still reflect next turn (v0.8.266 invariant).
- **One-liner rule (exact).** Take the **first non-empty line** of `description`, strip it, and
  truncate to **100 chars** with an ellipsis if longer. Never emit full/multi-line descriptions —
  that re-imports the schema cost the DEFERRED flip removed. (The test asserts no embedded newline
  survives and length ≤ 100.)
- **Empty-description fallback.** MCP tools may have an empty `description` (`mcp.py:146`). When the
  first non-empty line is blank, emit the **tool name only** (no dangling `` `name`: ``).
- **Keep the builder's name.** Do not rename `build_tool_category_awareness_prompt` /
  `tool_category_awareness_prompt` — body change only (avoids cross-module + spec churn).
- **Preserve the empty-set contract.** The rewritten body must still return `""` when no DEFERRED
  tools exist — the per-turn instruction slot relies on the empty-string return today.
- **Complete by construction.** Derive the list from `tool_index` DEFERRED entries; no hardcoded
  per-tool allowlist. Integration-gating falls out naturally (only registered tools are indexed).
- **Keep the `search_tools` framing** — the prompt must still tell the model to load a tool via
  `search_tools` before calling it.
- **`skill_manage` stays ALWAYS — standing decision, not a pending gate** (see "Standing Decision"
  at the end). No other visibility changes.
- **Coworker-maintained assets** (`souls/`, `_profiles/`, `evals/judges/`, `memories/`) untouched.

## High-Level Design

Rewrite the **body** of `build_tool_category_awareness_prompt` to iterate `tool_index`, select
`visibility == DEFERRED`, and emit one line per tool: `` - `<name>`: <one-liner> ``, under a heading
that names `search_tools` as the loader. Drop `_NATIVE_TOOL_CATEGORIES` / `_REPS` /
`_INTEGRATION_TOOL_CATEGORIES`. Optionally group by `integration` label for readability (native
first, then per-integration), but the name+one-liner pairs are the payload.

**Keep the function name** (`build_tool_category_awareness_prompt` and the per-turn caller
`tool_category_awareness_prompt`) — renaming triggers cross-module churn (`orchestrator.py` importer)
and ~5 spec-doc references under a zero-backward-compat rule, for no behavioral gain. The per-turn
slot in `_instructions.py` is unchanged; only its docstring shifts (category → per-tool).

**Cost framing (marginal delta, not vs zero).** The new line *replaces* today's ~30-40 tok category
line. Typical TARS bootstrap (~7 native deferred) ≈ **~175 tok/turn**; all-integrations ceiling
≈ ~475 tok/turn. This is a per-turn (uncached) cost, but it's ~20× smaller than the multi-thousand-
token schemas DEFERRED removes from the **cold** prefix (the costliest tokens at ~333 tok/s) — a net
win. Actual length is measured in TASK-3, not assumed.

## Tasks

### ✓ DONE TASK-1 — auto-generate per-tool deferred stubs

**Files:** `co_cli/tools/deferred_prompt.py` (body rewrite; drop the hardcoded dicts),
`co_cli/agent/_instructions.py` (docstring of `tool_category_awareness_prompt` only — name kept,
no caller change).

**done_when:** The TASK-2 test passes — built from a real bootstrap `tool_index`, the prompt
contains a `name + one-liner` line for **every** DEFERRED tool and **no** ALWAYS tool, one-liners
obey the exact rule (first non-empty line, ≤100 chars, no newline), empty-description tools emit
name-only, and the `search_tools` directive is present. (No schema moves; the ALWAYS/DEFERRED
bucket invariant is asserted in TASK-2 from `tool_index`, not via a scratch script.)

**success_signal:** The model sees every deferred tool by name and purpose in every turn's prompt —
shipped value **independent of** TASK-3's re-flip outcome.

**prerequisites:** none.

### ✓ DONE TASK-2 — test coverage for the stub prompt (closes the gap)

**Files:** `tests/test_flow_deferred_tool_stubs.py` (NEW).

**done_when:** `uv run pytest tests/test_flow_deferred_tool_stubs.py -x` passes — builds the prompt
from a real `create_deps` `tool_index` and asserts: completeness (every DEFERRED tool named),
exclusion (no ALWAYS tool), one-liner rule (no embedded newline, length ≤100), empty-description
fallback (synthetic empty-desc `ToolInfo` → name-only line), the ALWAYS/DEFERRED bucket invariant
(membership read from `tool_index`), and presence of the `search_tools` directive.

**success_signal:** Re-introducing a hardcoded omission, dumping full descriptions, or moving a
schema between buckets fails CI.

**prerequisites:** TASK-1.

### ✓ DONE TASK-3 — re-test skill_manage DEFERRED on top of stubs (gated re-flip)

> **Resolved:** gate FAILED 0/3 → `skill_manage` reverted to ALWAYS, now a **standing decision**
> (do not re-flip absent a loader-UX fix — see "Standing Decision" at the end). The gated-experiment
> language below is the original intent; the outcome is authoritative.

**Files:** `co_cli/tools/system/skills.py` (`skill_manage` decorator), `evals/eval_skills.py`
(add `case_w4_e_discovery` — the repeatable home for the smoke, replacing the throwaway
`tmp/discovery_smoke.py`).

**Design notes for the case:**
- `case_w4_e_discovery` runs its **own loop** of N≥3 trials, calling `make_eval_deps()` **once per
  trial** (fresh deps → fresh `ToolSearchToolset`, so each trial is an independent discovery test;
  the W4.A–D single-bootstrap structure does not apply here). Each trial drives one `run_turn` with
  a clear "save this procedure as a skill named X / create it now" prompt.
- Approval is already handled: `EvalFrontend.prompt_approval` returns `"a"` and `skill_manage`'s
  subject sets `can_remember=True`, so the discover→approve→create path auto-approves. Do not
  reimplement approval.
- The case prints a **diagnostic verdict**; use `Verdict.SOFT_PASS`/`SOFT_FAIL` so a stochastic
  miss is recorded but does **not** flip the eval's process exit code to nonzero (`main()` returns
  nonzero only when `all(c.passed)` is False; `SOFT_FAIL` is excluded from `passed`). This is a UAT
  diagnostic, not a hard CI gate.
- Capture and record the **measured** stub-prompt char length from the live bootstrap (confirms the
  ~175 tok typical figure).

**done_when:** With stubs live, `case_w4_e_discovery` runs N≥3 independent trials; **PASS = ≥2/3**
discover `skill_manage` via `search_tools` → approve → create on disk, with **no** `file_write`
fallback to cwd. If PASS, `skill_manage` stays DEFERRED and `tools.md` count is re-synced via
`/sync-doc`; if FAIL, revert to ALWAYS and record that stubs alone are insufficient (binding
constraint = loader UX, FM-2/3).

**success_signal:** Skill creation through the deferred path is reliable (≥2/3) and pollution-free.

**prerequisites:** TASK-1, TASK-2.

## Testing

- `scripts/quality-gate.sh full` (lint + full pytest).
- `tests/test_flow_deferred_tool_stubs.py` — the new completeness/exclusion guard.
- `evals/eval_skills.py` `case_w4_e_discovery` — the N≥3 discovery gate (TASK-3); a UAT
  diagnostic, not a hard CI assertion (discovery is stochastic on a 3B-active model).
- `tmp/audit_tool_schemas.py` — optional cross-check that no schema moved (the tracked TASK-2 test
  is the authoritative invariant guard).
- **`/sync-doc` follow-up (post-delivery):** the behavior description (category-level → per-tool
  stubs) drifts `prompt-assembly.md`, `tools.md`, `bootstrap.md`, `personality.md`. The function
  *name* is unchanged so refs stay valid; only the prose needs updating. Per convention specs are
  not in any `files:` — handled by `/sync-doc` after dev.

## Open Questions

- **Per-turn token cost (marginal).** The stub list *replaces* today's ~30-40 tok category line.
  Typical TARS (~7 native deferred) ≈ ~175 tok/turn; all-integrations ceiling ≈ ~475. Uncached
  (per-turn) but ~20× smaller than the cold-prefix schemas DEFERRED removes — net win, measured in
  TASK-3, decided not assumed.
- **Is the stub enough?** The smoke showed thrash *even after* `capabilities_check` named the tool
  (FM-2). Stubs make discovery cheaper to *decide*; whether they fix the `search_tools`→call
  *mechanic* is empirical — TASK-3 is the answer. If discovery still fails with stubs live, the
  bottleneck is the loader UX, not awareness, and `skill_manage` stays ALWAYS.
- **Grouping & arg names.** Flat list vs grouped-by-integration (readability vs tokens)? Include
  bare arg names (requires `ToolDefinition` unwrap, not in `ToolInfo`) or name+one-liner only?
  Default: name+one-liner, grouping only if it costs nothing.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev deferred-tool-stubs`

## Delivery Summary — 2026-05-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | TASK-2 test passes from a real bootstrap `tool_index`; per-tool stub for every DEFERRED tool, none ALWAYS, one-liner rule obeyed, empty-desc → name-only, `search_tools` directive present | ✓ pass |
| TASK-2 | `pytest tests/test_flow_deferred_tool_stubs.py` green — completeness, exclusion, one-liner cap, empty-desc fallback, truncation, first-line, search_tools directive, empty-set contract | ✓ pass (8 tests) |
| TASK-3 | run N≥3 independent discovery trials; PASS = ≥2/3 → keep DEFERRED, else revert to ALWAYS + record | ✓ pass — **gate FAILED 0/3 → reverted to ALWAYS** |

**Scope deviations from plan (TL calls, approved mid-dev):**
1. **Function renamed** `tool_category_awareness_prompt` → `deferred_tool_awareness_prompt` (and `build_` form). The plan's "keep the name" constraint assumed no behavioral gain from renaming; the rewrite *removed the category concept entirely* (flat per-tool list), making the old name actively misleading. Renamed across `_instructions.py`, `orchestrator.py` (importer), the test, and 5 specs.
2. **Stub granularity** = name + one-liner (kept per user decision), flat list, no category grouping.
3. **W4.E uses one shared bootstrap, not per-trial `make_eval_deps()`.** The plan's "fresh deps per trial for independence" premise is inaccurate: the SDK's `ToolSearchToolset._parse_discovered_tools` derives discovery state from `ctx.messages`, so a fresh per-trial `message_history=[]` already guarantees independence. Per-trial bootstrap additionally triggered a fatal cross-task MCP teardown (`RuntimeError: exit cancel scope in a different task`); shared bootstrap avoids it.
4. **Verdict mechanics correction.** The plan's note "`SOFT_FAIL` is excluded from `passed`" is wrong — `CaseResult.passed` is True only for `{PASS, SOFT_PASS}`, so `SOFT_FAIL` *does* redden the exit code. W4.E always returns `SOFT_PASS` and encodes the real gate result in `reason`.
5. **W4.E is guarded.** Since `skill_manage` reverted to ALWAYS, the case self-skips (inert `SOFT_PASS`) when `skill_manage` is not DEFERRED — preserved as the durable repeatable home that auto-reactivates on any future re-flip, instead of a misleading permanent 0/N.

**TASK-3 finding (the bet, resolved).** Awareness was **not** the binding constraint. With complete per-tool stubs live (`skill_manage` listed by name + purpose, measured ~965 char prompt in full bootstrap), discovery was **0/3**:
- t0, t1: model never even called `search_tools`.
- t2: called `search_tools` **and** `skill_manage`, but no skill landed on disk (partly disrupted by a `ValueError: ...different Context` harness artifact).
- Model reasoning verbatim: *"skill_manage is listed in the available_skills manifest but not in my available tools"* and *"Let me use search_tools to find it, or just use file_write to create the skill"* — textbook FM-2 (thrash) + FM-3 (file_write fallback temptation).

**Conclusion:** the binding constraint is the `search_tools`→load→call **loader UX (FM-2/3)**, not awareness — exactly the plan's falsification branch. `skill_manage` stays ALWAYS. TASK-1+TASK-2 ship standalone value (complete-by-construction per-tool awareness + regression guard) regardless. Fixing the loader mechanic is a separate problem; a lead worth chasing is the model's confusion between the `<available_skills>` manifest and the tool surface.

**Tests:** scoped — 15 passed (8 new deferred-stub + 7 prompt-assembly/capability), 0 failed. Lint clean.
**Doc Sync:** fixed — 5 specs (prompt-assembly, tools, personality, bootstrap, 01-system): identifier rename + category→per-tool prose.

**Overall: DELIVERED**
All three tasks met their `done_when`. The gated re-flip resolved negatively per design (0/3 < 2/3 → reverted to ALWAYS); the awareness + regression-guard value (TASK-1/2) is shipped and `skill_manage` is unchanged from baseline.

## Implementation Review — 2026-05-28

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | per-tool stub for every DEFERRED tool, none ALWAYS, one-liner rule (first non-empty line, ≤100, no newline), empty-desc → name-only, `search_tools` directive | ✓ pass | `deferred_prompt.py:45-59` iterates `sorted(tool_index)`, skips non-DEFERRED at `:47`, emits `` - `name`: one-liner `` / name-only at `:52-53`; one-liner trunc `:27-28` `line[:99] + "…"` = exactly 100 (no off-by-one); empty-set `""` at `:54-55`; header w/ `search_tools` `:56-59`. Per-turn placement preserved: `orchestrator.py:59-64` `per_turn_instructions` (not static); `_instructions.py:22-31` re-reads `tool_index` per call. Hardcoded dicts gone; repo grep for `_NATIVE_TOOL_CATEGORIES`/`_INTEGRATION_TOOL_CATEGORIES`/`_REPS`/`tool_category_awareness` → 0 hits. Rename consistent across `_instructions.py`, `orchestrator.py`, test, eval, 5 specs. |
| TASK-2 | `pytest tests/test_flow_deferred_tool_stubs.py` green — completeness, exclusion, one-liner cap, empty-desc, truncation, first-line, search_tools, empty-set | ✓ pass (8 tests) | All 8 done_when bullets mapped to real assertions; built from real `build_native_toolset(SETTINGS)` index (`:20-22`); no mocks/patching (synthetic `ToolInfo` is genuine dataclass test data); catches all 3 named regression classes (hardcoded omission, full-desc dump, bucket move). |
| TASK-3 | N≥3 independent trials; PASS=≥2/3 → keep DEFERRED else revert+record | ✓ pass — gate FAILED 0/3 → reverted | `skills.py:305-309` `skill_manage` = `VisibilityPolicyEnum.ALWAYS` (reverted, no DEFERRED token in file). `eval_skills.py`: N=3 loop `:610`, per-trial `message_history=[]` `:635` (independence), self-skip guard reads live `deps.tool_index["skill_manage"].visibility` `:586-598`, returns `SOFT_PASS` unconditionally w/ gate in `reason` `:669-677`, measures stub-prompt length `:600-602`, HIT detection requires search_tools+skill_manage+on_disk+no-file_write `:651-655`. `Verdict`/`CaseResult.passed` = `{PASS, SOFT_PASS}` confirmed `_observability.py:80-83`. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale `main()` docstring: "Run W4.A through W4.D" — body runs W4.E | `evals/eval_skills.py:692` | minor | Updated to "W4.A through W4.E" |
| Stale module docstring: omits the W4.E discovery diagnostic | `evals/eval_skills.py:1-8` | minor | Added W4.E coverage line |
| Suite failure: `test_real_turn_with_tool_call_populates_model_requests` — accumulator(10) ≠ direct_count(9) on `interrupted=True` | `tests/test_flow_turn_result_model_requests.py:79` | blocking (suite-red) | **Pre-existing, orthogonal to this plan.** RCA: `_build_interrupted_turn_result` (orchestrate.py:511-517) deliberately drops the trailing ToolCall-bearing ModelResponse, so a trimmed interrupted turn legitimately holds fewer ModelResponses than the (correct) accumulator. Production correct-by-design; test invariant was wrong for the interrupt path. Fixed test to branch on `turn.interrupted` (`>=` when interrupted, `==` otherwise). Triggered stochastically by the 3B model looping the shell tool 10× then hitting the request cap — not caused by the stub-prompt text change. |

### Tests
- Command: `uv run pytest -x -q` (full suite)
- Result: **648 passed, 0 failed** (`.pytest-logs/*-review-impl-full2.log`); scoped re-verify 10 passed (`.pytest-logs/*-fix-verify.log`)
- Lint: `scripts/quality-gate.sh lint` → PASS (ruff check + format)

### Behavioral Verification
- No `co status` command (CLI surface: chat/tail/trace/dream); the user-facing surface for this change is per-turn prompt assembly, verified directly.
- Built the deferred-stub prompt from a real `build_native_toolset` index: **633 chars**, header with `search_tools` directive, one `` `name`: one-liner `` per deferred tool (`knowledge_analyze`, `task_cancel`, `task_list`, `task_start`, `task_status`, `web_research`), zero ALWAYS tools, all one-liners single-line ≤100 chars. `skill_manage` correctly absent (ALWAYS).
- `success_signal` verified — TASK-1: the model sees every deferred tool by name + purpose in the per-turn prompt; TASK-3: `skill_manage` reverted to ALWAYS (not in the deferred list), W4.E self-skips inert.

### Scope notes (not attributable to this plan)
- The working tree carries many unrelated in-progress changes (`toolset.py`, `commands/history.py`, `tools/agents/delegation.py`, `tools/code/*` deletions, `display.py`, `agents.md` REASON_SPEC removal, `evals/_deps.py` async prompts, other exec-plans, `uv.lock`). These predate this review and belong to other work streams — flagged for staged-file hygiene before `/ship`.

### Overall: PASS
All three tasks satisfy their `done_when` with file:line evidence; the gated re-flip resolved negatively by design and `skill_manage` is cleanly ALWAYS. Two stale docstrings fixed; one pre-existing, orthogonal interrupt-path test fragility fixed at root (test invariant, not production). Full suite green, lint clean, behavioral signal confirmed. Ship gate: ensure only deferred-tool-stubs files are staged.

## Standing Decision — `skill_manage` stays ALWAYS (2026-05-28)

`skill_manage` is **ALWAYS by deliberate design**, not merely because TASK-3's gate failed once.
Do **not** re-attempt the DEFERRED flip absent a fix to the loader mechanic (FM-2/3). Rationale,
independent of the 0/3 smoke result:

1. **Context window is not the constraint — latency is.** The full runtime prefill is ~13.9k of the
   64k window (~21%), leaving ~50k headroom. There is no space pressure forcing `skill_manage`'s
   ~560 tok out of the prompt. DEFERRED here only buys *cold-prefill latency*, and the ALWAYS
   schema is KV-cached after the first turn — so the steady-state saving is small.
2. **The defer-tax lands at the worst moment.** `skill_manage` is rare, deliberate, high-intent
   ("save this as a skill"), and already approval-gated. Deferring adds a `search_tools` round-trip
   plus a real failure probability exactly when the user has explicitly requested the action — the
   one place not to add friction or a coin-flip.
3. **Its failure mode is harmful, not just slow.** When discovery fails the model falls back to
   `file_write` with a bare relative path, polluting the user's cwd (FM-3) — a correctness
   regression, not a latency blip.
4. **It is the sole action-dispatch deferred candidate** → the only tool with the semantic-gap risk
   (capability lives in the `action` enum, hidden when deferred). Maximum fragility for a cold-only,
   cached, ~560-tok payoff.

The 64k budget, when it matters, is governed by history / tool-result / reasoning growth — managed
by compaction + spill — not by evicting one cached tool schema. So the window argues *for* keeping
`skill_manage` ALWAYS, not against. The dormant W4.E case stays as a guard (self-skips while ALWAYS)
but is **not** a standing invitation to re-flip.
