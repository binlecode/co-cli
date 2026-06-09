# Deferred-tool reveal — single source of truth (`revealed_tools`)

## Context

co defers low-frequency tools: a DEFERRED tool's full schema is withheld from the prompt and replaced by a one-line stub; the model loads it on demand via `tool_view`, which records the reveal so a per-turn visibility filter surfaces the full schema next turn.

"Is this deferred tool currently revealed" is held in `CoRuntimeState.unlocked_tools: set[str]` (`co_cli/deps.py:228`), written by `tool_view` (`co_cli/tools/system/tool_view.py:85`) and read by `_tool_visibility_filter` (`co_cli/agent/toolset.py:76`). This runtime home is correct and peer-aligned: a HEAD survey of hermes (immutable registry, stateless rebuild), openclaw (immutable `baseToolDefinitions` + separate session-scoped catalog maps, reveal state reassigned not mutated), and opencode (`Object.freeze`'d tools, fresh `materialize()` per turn) shows **3/3 keep tool definitions immutable and hold reveal/active state in a separate runtime structure**. None mutates a registry entry to flip an "active/revealed" flag. co's invariants encode the same discipline: `ToolInfo` is "set once at registration, never mutated" (`deps.py:101`); `tool_catalog` membership means "registered, not callable this turn" (`deps.py:299-302`); `fork_deps` shares `tool_catalog` by reference precisely because runtime state is fork-fresh (`deps.py:394`).

The defect is not the runtime set — it is that **two consumers read different sources.** `_tool_visibility_filter` reads `unlocked_tools`; but `build_deferred_tool_awareness_prompt` (`co_cli/tools/deferred_prompt.py:101`) reads **only** `ToolInfo.visibility` and never consults the reveal set. So once a deferred tool is revealed, the filter surfaces its full schema **and** the stub generator keeps emitting its "load it via `tool_view`" stub every turn — a redundant stub alongside the already-present schema, and a self-contradicting instruction to load a tool the model can already call.

## Problem & Outcome

**Problem:** Reveal state has one correct home (`runtime`) but two consumers, and one consumer (`deferred_prompt`) ignores it — producing a redundant, contradictory stub for every revealed deferred tool for the rest of the session. The field name `unlocked_tools` also under-describes (it is specifically the *revealed* deferred-tool set).

**Outcome:** Both consumers read the one runtime reveal set, so they agree by construction; a revealed deferred tool stops emitting a stub; the field is renamed `revealed_tools` to name its true membership. No data-model removal, no catalog mutation, no fork/budget/compaction change — co stays on the peer-standard immutable-registry discipline.

**Failure cost:** Without the fix, every revealed deferred tool wastes per-turn prompt tokens on a redundant stub and feeds the small model a "load X" instruction for a tool already callable — eroding the exact prompt-coherence and floor budget that deferral exists to protect, and the cost compounds as more tools are revealed within a session.

## Scope

In:
- Rename `CoRuntimeState.unlocked_tools` → `revealed_tools` and **sweep the full "unlock" → "reveal" terminology** across all dev-facing identifiers, comments, and docstrings in code + tests — not just the field reads. Confirmed surface: `deps.py` (field + comment 222-227), `toolset.py` (read 76 + docstrings 65,100), `tool_view.py` (write 85 + module docstring/ladder comments 12,13,19,21), `tests/test_tool_view.py` (module docstring 5,7,9; three function names `*_unlocks_*`/`*_unlocking`/`*_unlocks_nothing` → `*_reveals_*`; docstrings 80,94,105,126,155,158; asserts 89,100,110,148,164).
- **Preserve model-facing vocabulary unchanged:** the `tool_view` `@agent_tool` function docstring and the `"Loaded …"` success return already use the "load"/"view" action verb (not "unlock") — leave them. Distinction is deliberate: model-facing **action** = "load via `tool_view`"; internal **state** = `revealed_tools`. No prompt/return text changes (behavior-preserving).
- Make `build_deferred_tool_awareness_prompt` consult the reveal set so a revealed deferred tool is not stubbed; thread the set in from the instruction call site.
- Preserve the awareness-prompt empty-string contract, including the new "all deferred tools revealed → empty" case.

Out (explicit):
- Removing the reveal set / mutating `tool_catalog` / changing `ToolInfo` (rejected — peer outlier, breaks three invariants, forces copy-on-fork).
- Removing deferral, the `_normalize`/difflib matcher, or any ALWAYS↔DEFERRED reclassification.
- Persisting reveals across sessions/processes.
- Spec/reference doc edits as task work (tracked below; specs handled post-delivery by `sync-doc`).

### Post-delivery doc sync (not task `files:` — comprehensiveness tracker)

The rename + the stub-skips-revealed behavior must propagate so no stale `unlocked_tools` / "unlock" reference survives system-wide:
- **`sync-doc` (specs, auto-invoked by `orchestrate-dev`):** `docs/specs/tools.md` (78, 81, 106, 122, 359), `docs/specs/pydantic-ai-integration.md` (124, 356), `docs/specs/compaction.md` (559-561). Each must reflect the rename **and** that the awareness stub now skips revealed tools.
- **Reference docs (manual — `sync-doc` does not touch `docs/reference/`):** `docs/reference/RESEARCH-context-management-peer-survey.md` (16, 73, 79) and `RESEARCH-tools-gaps-co-vs-hermes.md` — accuracy touch for the field name. Lower priority (background artifacts), but in-scope for "comprehensive."
- **Do NOT touch** `docs/exec-plans/completed/*` (historical records — the prior tool-view plan's `unlocked_tools` references are correct history).

## Behavioral Constraints

- Filter semantics for ALWAYS tools and for **un**revealed DEFERRED tools are unchanged (only the field name changes there).
- Reveal state stays in `runtime` → still survives compaction with no history coupling, and `fork_deps` still gives each forked agent a fresh empty reveal set (no cross-agent leak).
- `tool_view` exact-name + `_normalize` folding + difflib "did you mean" behavior unchanged; only the write target field is renamed and the success message reworded.
- The `resume_tool_names` resume gate in `_tool_visibility_filter` is untouched.
- `measure_always_schema_budget` / `static_floor_tokens` are bootstrap-computed from ALWAYS tools before any reveal — a runtime reveal must not retroactively change the measured floor (unaffected: catalog/visibility are not mutated).
- Awareness prompt returns `""` when there is nothing to stub — including when every DEFERRED tool is already revealed (the per-turn instruction slot relies on the empty contract).

## High-Level Design

Single source of truth = the one runtime reveal set; both consumers read it.

1. **Field** — `CoRuntimeState.revealed_tools: set[str]` (renamed from `unlocked_tools`), comment updated to describe membership ("DEFERRED tools the model has revealed via `tool_view`"). Stays in `runtime`; fork-fresh; not reset per turn.
2. **Writer** — `tool_view` adds the canonical name to `revealed_tools`; success message reworded to "revealed/now callable" phrasing.
3. **Consumer A (filter)** — `_tool_visibility_filter`: predicate unchanged in meaning — hide iff `visibility == DEFERRED and name not in revealed_tools`.
4. **Consumer B (stub generator)** — `build_deferred_tool_awareness_prompt(tool_catalog, revealed_tools)` gains a second parameter (`set[str]`, kept a plain collection so the formatter stays pure — no `deps`/`runtime` reach-in) and skips any tool whose name is in `revealed_tools`. The `deferred_tool_awareness_prompt` instruction (`_instructions.py:31`) passes `ctx.deps.runtime.revealed_tools`.
5. **Empty contract** — with the skip applied, "no deferred tools" and "all deferred tools revealed" both naturally yield the existing `""` return.

Naming note: `revealed_tools` over `active_tools` — ALWAYS tools are active/callable every turn but are never members, so `active_tools` would misname the set's membership; `revealed_tools` denotes exactly "deferred tools that have been revealed" and reads cleanly against the filter predicate. The rename is coupled to TASK-2, not cosmetic: the new stub-generator predicate reads literally as "skip if revealed" (`name in revealed_tools`), so the field name carries the fix's intent at the second consumer.

## Tasks

### ✓ DONE TASK-1 — Rename `unlocked_tools` → `revealed_tools` + full "unlock"→"reveal" terminology sweep (behavior-preserving)
- files: `co_cli/deps.py`, `co_cli/agent/toolset.py`, `co_cli/tools/system/tool_view.py`, `tests/test_tool_view.py`
- Rename the field + dev comment (`deps.py:222-227`); the filter read + keyed-on docstring (`toolset.py:65,76,100`); the write site + module docstring + resolution-ladder comments (`tool_view.py:12,13,19,21,85`); and in `tests/test_tool_view.py` the module docstring (5,7,9), the **three function names** (`test_normalized_exact_match_unlocks_canonical`→`…_reveals_canonical`, `test_typo_suggests_without_unlocking`→`…_without_revealing`, `test_no_overlap_name_is_terminal_and_unlocks_nothing`→`…_and_reveals_nothing`), the docstrings (80,94,105,126,155,158), and the asserts (89,100,110,148,164). **Leave model-facing text** — the `tool_view` `@agent_tool` function docstring and the `"Loaded …"` success return (already "load"/"view" verb, not "unlock"). No semantic change.
- done_when: `grep -rin "unlock" co_cli/ tests/` returns no hits (broadened to all-case "unlock" so the sweep is comprehensive, not just the field name), `grep -rn "unlocked_tools" evals/` returns no hits (no-hit guard), and `uv run pytest tests/test_tool_view.py -x` passes (the renamed function names + asserts run green).
- success_signal: N/A (pure refactor).
- prerequisites: none.

### ✓ DONE TASK-2 — Stub generator honors the reveal set (single-source fix)
- files: `co_cli/tools/deferred_prompt.py`, `co_cli/agent/_instructions.py`, `tests/test_deferred_prompt.py` (new file — does not yet exist)
- Add a `revealed_tools: set[str]` parameter to `build_deferred_tool_awareness_prompt`; skip any DEFERRED tool whose name is in it. **No new empty-output branch:** the per-name skip happens in the existing loop *before* the `if not general and not families: return ""` guard (`deferred_prompt.py:109`), so an all-revealed catalog falls through to `""` naturally — do not add a separate all-revealed early-return. Update `_instructions.py:31` to pass `ctx.deps.runtime.revealed_tools`.
- done_when: (a) builder boundary — `build_deferred_tool_awareness_prompt` over a catalog with two DEFERRED tools where one name is in `revealed_tools` omits the revealed tool's stub and keeps the unrevealed tool's stub; all-revealed input → `""`. (b) wired runtime path — calling `deferred_tool_awareness_prompt(ctx)` with a `RunContext` whose `deps.runtime.revealed_tools` holds a revealed DEFERRED name omits that tool's stub (proves the `_instructions.py:31` threading). `uv run pytest tests/test_deferred_prompt.py -x` passes.
- success_signal: A deferred tool that has been loaded via `tool_view` no longer emits a redundant "load it via `tool_view`" stub on subsequent turns.
- prerequisites: TASK-1.

## Testing

- TASK-1: `tests/test_tool_view.py` (renamed function names + docstrings + asserts) green — confirms the rename is behavior-preserving for the reveal write + filter path.
- TASK-2: new `tests/test_deferred_prompt.py` — (a) revealed DEFERRED tool omitted from stubs while unrevealed one remains; (b) all-revealed → `""`; (c) wired path via `deferred_tool_awareness_prompt(ctx)`. Asserts observable prompt output, not field structure.
- **Tests-cleanup note:** `test_tool_view.py` is already functional/behavioral and has no dead, duplicate, or structural cruft to purge — cleanup here is terminology consistency only (the `defer_loading`-flag assertion at line 146 guards a real "SDK loader stays inert" invariant and is retained). The new `test_deferred_prompt.py` must assert observable prompt output only (mirror `done_when`), never field/attribute existence.
- Ship gate: `scripts/quality-gate.sh full` green.

## Open Questions

None blocking. Pre-answered from source:
- *Pass the set or the whole `deps`/`runtime` into the formatter?* — Pass `revealed_tools: set[str]`. `build_deferred_tool_awareness_prompt` is a pure formatter already taking only `tool_catalog`; threading a plain collection keeps it free of `deps` reach-in.
- *Any consumer of reveal state beyond the filter + stub generator?* — No. Repo-wide grep shows `unlocked_tools` read only in `toolset.py` (filter) and written only in `tool_view.py`; no third consumer.

## Final — Team Lead

Plan approved. Converged C2 — both reviewers `Blocking: none` (PO approved C1; Core Dev's lone C1 blocker, a phantom `evals/eval_skills.py` rename target, removed and re-verified C2).

**Gate-1 comprehensiveness hardening (post-converge, per PO directive "comprehensive system cleanup, tests cleanup"):** a system-wide `grep -rin "unlock"` revealed the rename surface was under-stated — TASK-1 now sweeps full "unlock"→"reveal" terminology (incl. 3 test function names + docstrings, with model-facing "load"/"view" text deliberately preserved), `done_when` broadened to `grep -rin "unlock"` → no hits; a Post-delivery doc-sync tracker enumerates the 3 spec files (`tools.md`, `pydantic-ai-integration.md`, `compaction.md`) for `sync-doc` and 2 `docs/reference/` files for manual touch; Testing records that `test_tool_view.py` needs terminology-only cleanup (no dead/structural tests to purge). No design change — same two tasks, wider sweep.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev deferred-reveal-single-source`

## Delivery Summary — 2026-06-09

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep -rin unlock co_cli/ tests/` no hits; `grep -rn unlocked_tools evals/` no hits; `pytest tests/test_tool_view.py` green | ✓ pass |
| TASK-2 | builder omits revealed stub / keeps unrevealed / all-revealed → `""`; wired path via `deferred_tool_awareness_prompt(ctx)`; `pytest tests/test_deferred_prompt.py` green | ✓ pass |

**Tests:** scoped — 10 passed, 0 failed (`test_tool_view.py` 5, `test_deferred_prompt.py` 4 incl. wired path, `test_flow_deferred_tool_stubs.py` 1).
**Doc Sync:** fixed — specs (`tools.md`, `pydantic-ai-integration.md`, `compaction.md`) renamed `unlocked_tools`→`revealed_tools` + `unlock`→`reveal`, and `tools.md` load-flow gained a note that the stub generator reads `revealed_tools` so a revealed tool stops stubbing; reference docs (`RESEARCH-context-management-peer-survey.md`, `RESEARCH-tools-gaps-co-vs-hermes.md`) given the manual field-name/term touch. System-wide `grep -rin "unlock"` is clean across code, tests, evals, specs, and tracked reference docs (only the active plan retains the old name, by design).

**Extra file (beyond task `files:`):** `tests/test_flow_deferred_tool_stubs.py` — existing single-arg caller of `build_deferred_tool_awareness_prompt`; updated to pass `set()` (nothing revealed), preserving its "every DEFERRED tool stubbed" contract.

**Overall: DELIVERED**
Both tasks passed `done_when`, lint clean, scoped tests green, doc sync clean. Single source of truth achieved: filter and stub generator both read `runtime.revealed_tools`; a revealed deferred tool no longer emits a redundant `tool_view` stub.

## Implementation Review — 2026-06-09

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `grep -rin "unlock" co_cli/ tests/` → exit 1 (no hits); `grep -rn "unlocked_tools" evals/` → exit 1; `pytest tests/test_tool_view.py -x` → 5 passed | ✓ pass | Field renamed `deps.py:227`; filter read `toolset.py:76`; write site `tool_view.py:85`; module docstring + ladder `tool_view.py:12,19-22`; model-facing text preserved (`tool_view.py:59` "Load…", `:87` "Loaded…"); test fns/asserts `test_tool_view.py:80,94,105,90,101,111,149,165` |
| TASK-2 | builder omits revealed stub / keeps unrevealed / all-revealed → `""`; wired path via `deferred_tool_awareness_prompt(ctx)`; `pytest tests/test_deferred_prompt.py -x` → 4 passed | ✓ pass | Param `deferred_prompt.py:82`; skip `:110` is inside the loop and before the empty guard `:118` (one `return ""` at `:119`); no separate all-revealed branch; threading `_instructions.py:31-32` passes `ctx.deps.runtime.revealed_tools`; call path `deferred_tool_awareness_prompt → build_deferred_tool_awareness_prompt` confirmed; all 5 call sites pass two args |

Adversarial pass re-read all six cited claims cold — every claim CONFIRMED-PASS, zero downgrades.

### Issues Found & Fixed
No issues found. No blocking findings; auto-fix loop empty. `tests/test_flow_deferred_tool_stubs.py` (declared extra file in delivery) correctly updated to the two-arg signature with `set()`; no other caller breaks.

### Tests
- Command: `uv run pytest -q`
- Result: 652 passed, 0 failed, 1 warning (169.96s)
- Log: `.pytest-logs/20260609-141537-review-impl.log`

### Behavioral Verification
- `uv run co status`: n/a — no `status` subcommand in this CLI (commands: chat, tail, trace, dream, google); system imports + bootstrap exercised below instead.
- `build_native_toolset()` boots with renamed field; real catalog has 17 DEFERRED tools.
- **`success_signal` verified** (TASK-2): against the production catalog, revealing `google_calendar_list` drops its stub from the awareness prompt while its sibling `google_calendar_search` is retained; all-17-revealed → `""`. A loaded deferred tool emits no redundant `tool_view` stub on subsequent turns — exactly the user-observable outcome.

### Overall: PASS
Rename is behavior-preserving and system-wide complete; the single-source fix removes the redundant stub for revealed deferred tools; full suite green, lint clean, success_signal confirmed against the real catalog. Ready to ship.
