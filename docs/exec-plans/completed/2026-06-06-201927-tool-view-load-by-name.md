# tool-view-load-by-name

## Context

co hides low-frequency tools behind `VisibilityPolicyEnum.DEFERRED` (`agent/toolset.py:111` sets
`defer_loading` from it). The SDK (pydantic-ai 1.81.0, pinned in `pyproject.toml:17`) auto-injects a
`ToolSearch` capability (`pydantic_ai/capabilities/_tool_search.py:14`) that wraps the toolset in a
`ToolSearchToolset` (`pydantic_ai/toolsets/_tool_search.py:57`). That wrapper exposes one bridge tool,
**`search_tools(keywords: str)`** — a keyword search matched against tool names + descriptions, returning
≤10 ranked matches, whose return carries `_DISCOVERED_TOOLS_METADATA_KEY` metadata that unlocks the matched
tools into the callable set on the next request.

co separately emits a **per-turn stub block** for *every* deferred tool — name + one-line purpose, grouped
by family (`tools/deferred_prompt.py`, surfaced via `agent/_instructions.py:25`). So in co, **discovery is
already free**: the model sees the exact name of every deferred tool every turn. The keyword-search step is
therefore solving a problem co does not have — the model is forced to invent keywords to load a tool it can
already name. This is also incoherent with co's load-by-name family (`memory_view`, `session_view`,
`skill_view`), and it is a small-model failure surface (crafting matching keywords, then picking the right
one of ≤10 results).

Grounding: this design emerged from the deferred-loading analysis (the deferral cost model — stub floor +
round-trip + plan legibility — and the stub-all-makes-search-redundant observation), whose durable rule is
distilled into `docs/specs/tools.md` § Tool-schema prefill floor; the standalone A1 report was removed
post-consolidation. This plan is **separate from** the A1/A2
tool-schema-floor-reduction work in
`docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md` — that flips `visibility`
flags on existing tools and keeps today's loader; this plan changes the loader *mechanism*.

### Verified integration points (read against the pinned SDK behavior)

- **Auto-injection is suppressible.** `_inject_auto_capabilities` (`agent/__init__.py:2640`) adds the default
  `ToolSearch` only when `has_capability_type(capabilities, ToolSearchCap)` is False. Supplying a
  `ToolSearch` *subclass* instance in `capabilities=[...]` (the agents are built in `agent/build.py:54,111`)
  suppresses the default and lets co's wrapper win — no fork required.
- **`search_tools` is the sole unlock path.** A deferred tool enters `get_tools()` only if its name is in the
  set parsed from prior `search_tools` returns (`_tool_search.py:122-127, 129-142`). No other code path
  unlocks. So a name-addressed loader must emit the *same* discovery metadata to make tools callable.
- **co already hard-codes the loader name in two places** that must move in lockstep with any rename:
  `context/compaction.py:256,264` (`_preserve_deferred_tool_discoveries` matches `tool_name == "search_tools"`
  to carry unlocks across compaction) and the prompt/rules text (`deferred_prompt.py:113`,
  `context/guidance.py:29`, `context/rules/04_tool_protocol.md:62-64`, `agent/_instructions.py:25`).
- **The SDK classes are private.** `ToolSearchToolset`, `ToolSearch`, `_DISCOVERED_TOOLS_METADATA_KEY`,
  `_SEARCH_TOOLS_NAME`, `_SearchTool`, `_parse_discovered_tools` all live in underscore modules
  (`pydantic_ai/toolsets/_tool_search.py`, `capabilities/_tool_search.py`). co pins published 1.81.0 from
  PyPI — the local `~/workspace_genai/pydantic-ai` checkout is research-only and may not match. Any approach
  that imports these is coupled to private internals across SDK upgrades. This is the central design axis
  (see Open Questions / High-Level Design).

## Problem & Outcome

**Problem.** co's deferred-tool loader is keyword-search (`search_tools`), but co stubs every deferred tool
by exact name every turn — so the search/ranking machinery is redundant indirection. The model must produce
keywords matching a tool it can already name, then disambiguate ≤10 results, with no path to address a tool
by the name it sees. This is a poor fit for a small model and incoherent with co's `*_view` load-by-name
family.

**Outcome.** A single loader, `tool_view(name)`, replaces `search_tools`: a normalized-exact name match
unlocks the tool directly (the happy path — copy the name from the stub); on no exact match it falls back to
fuzzy ranking and returns "did you mean" candidates (suggest, never silently auto-load a guess); no match →
do not retry. The model then calls the real tool directly, exactly as today. The keyword engine survives only
as the fuzzy fallback; the interface becomes name-first and family-consistent.

**Failure cost.** The standing cost is *structural, not a bug*: keyword search over a set that is already
fully addressable by name (every deferred tool is stubbed by exact name every turn) is redundant
indirection *by construction* — the model spends a turn crafting search keywords for a tool it can name, can
fail to produce matching tokens (→ "no tools found", possible stall), or load and then call the wrong one of
several ranked hits, and has no path to address a tool by the name it sees. This is a reliability +
coherence improvement; it is justified on that structural redundancy, not on a captured failure (none
observed — the loader works on the happy path; see OQ-1). The cost of getting the *implementation* wrong is
higher and concrete: if the rename desyncs the loader name from `compaction.py`'s hard-coded `"search_tools"`
match, **every unlocked deferred tool is silently revoked on the first compaction** — the model loses access
mid-task with no error; and if the co capability fails to suppress the default `ToolSearch`, **both loaders
ship at once** and co's `tool_view` unlocks may not take (split unlock parser).

## Scope

In scope: replacing the deferred-tool loader bridge with `tool_view(name)` (exact-first, fuzzy-fallback);
keeping the SDK unlock mechanism (discovery metadata); updating the two co-side couplings (compaction
preservation, prompt/rules text); tests; spec sync (post-delivery via sync-doc).

Out of scope:
- The A1/A2 visibility-flag changes (separate plan) — though this plan must keep working as that plan moves
  more tools to DEFERRED.
- Auto-calling the resolved tool (load and call stay separate model steps — the call is the control point and
  args differ per tool).
- Dropping the stub-all design for keyword discovery over a large unstubbed MCP surface (the hermes model) —
  that is the *opposite* direction and only relevant if the stub-all bet breaks (see Behavioral Constraints).

## Behavioral Constraints

- **Stub-all coupling (the validity bet).** `tool_view` is sufficient *only because* co stubs the full
  deferred set, making discovery free. If co ever defers a large surface it cannot fully stub (e.g. a big MCP
  integration), keyword discovery-by-description becomes necessary again and this decision must be revisited.
  State this in the spec; do not silently assume it holds forever.
- **Single loader / suppression verified.** Exactly one deferred-tool loader may ship. co's capability must
  subclass the SDK `ToolSearch` so `_inject_auto_capabilities` does not also append the default — otherwise
  `search_tools` reappears alongside `tool_view` and the SDK's unlock parser (keyed on `"search_tools"`) will
  not honor `tool_view` returns. The integration test must assert `search_tools` is **absent** from the live
  toolset, not merely that `tool_view` is present.
- **Fuzzy suggests, exact loads.** Only a normalized-exact name match may unlock-and-imply a tool. A fuzzy
  near-match returns candidates for a cheap retry; it must never silently unlock a guessed neighbor (a
  hallucinated name resolving to a plausible wrong tool is the failure to prevent).
- **Bounded retries.** Preserve the SDK's "if no tools are found, they do not exist — do not retry" guard so a
  genuinely-absent tool does not loop.
- **No backward-compat shim** (`feedback_zero_backward_compat`): the loader is renamed hard from
  `search_tools` to `tool_view`; no alias kept.
- **Small-model legibility** (`feedback_tool_split_small_model`): one name-addressed verb consistent with the
  `*_view` family; do not reintroduce keyword-crafting as the primary path.

## High-Level Design

> **DELIVERED DESIGN (pivoted during dev — supersedes the capability-based design below).**
> Review feedback rejected coupling to pydantic-ai's capability layer. The delivered
> mechanism is fully SDK-decoupled and simpler:
> - `tool_view(name)` is a **normal `@agent_tool`** (`co_cli/tools/system/tool_view.py`,
>   ALWAYS visibility) — not a capability, not a wrapper toolset. Resolution ladder
>   unchanged (normalized-exact unlock → `difflib` fuzzy suggest → terminal no-retry).
> - **Deferred visibility is co-owned via the existing per-turn filter.** Tools are no
>   longer registered with SDK `defer_loading`; `_tool_visibility_filter`
>   (`agent/toolset.py`) hides a DEFERRED tool (per `tool_index` visibility) until its
>   name is in `deps.runtime.unlocked_tools`. `tool_view` adds it on an exact match.
> - **The SDK `ToolSearch`/`ToolSearchToolset` never engages** (no `defer_loading` tools)
>   and is never imported — zero private coupling, no capability subclass, no guard test.
> - MCP tools dropped `DeferredLoadingToolset`; they're DEFERRED in `tool_index` and
>   hidden by the same filter — one loader for native + MCP.
> - **Unlock state lives in runtime memory, not message history** → survives compaction
>   for free. `_preserve_deferred_tool_discoveries` and its call site were **deleted**;
>   the compaction-preservation coupling no longer exists.
>
> The OQ-2 axis below (how much private SDK surface to subclass) is moot — the answer is
> *none*. The text below is retained as the design history that led here.

One loader tool, name-first with a fuzzy fallback, replacing `search_tools`. The resolution ladder inside
`tool_view(name)`:

1. **Normalized-exact match** (case-insensitive; `_`/`-` normalized) against the deferred-tool names → unlock
   it (emit discovery metadata), return its schema. Happy path.
2. **No exact match → fuzzy-match** names + descriptions → return top-K candidates (`name` + one-liner)
   **without committing**; reply "did you mean: …". Model retries with the right name. On the recommended
   path co owns the toolset, so there is **no SDK engine to reuse** — co writes the matcher. The SDK's
   `_search_tools` is plain substring containment (no ranking, cap 10); a genuine typo
   (`skil_create`) won't substring-match `skill_create`. So the matcher is stdlib `difflib.get_close_matches`
   over the deferred names (true fuzzy, catches typos), not substring containment. No `rg`/subprocess: the
   deferred set is a small in-memory list, not an on-disk corpus.
3. **No fuzzy match →** "no such tool; do not retry."

**Implementation approach — decide at Gate 1 (OQ-2).** **Both viable paths import at least one private SDK
symbol** — suppressing the auto-injected default requires co's capability to subclass the private
`ToolSearch` (`capabilities/_tool_search.py`), because `_inject_auto_capabilities` only skips the default
when `has_capability_type(capabilities, ToolSearch)` is True and there is no public disable flag. So there
is no zero-private-coupling option; the choice is about *how much* private surface co touches:

- **(Recommended) Narrow coupling — co-owned toolset on public base classes.** co's capability subclasses
  the private `ToolSearch` (suppression, unavoidable) but its `get_wrapper_toolset` returns co's own
  `WrapperToolset` (public `pydantic_ai.toolsets.WrapperToolset`/`AbstractToolset`) that adds the `tool_view`
  tool, runs the resolution ladder, and emits a **co-owned discovery-metadata key** read by a **co-owned**
  parse step (co already owns that half in `compaction.py`). Private surface = the one `ToolSearch` capability
  class. The toolset + unlock mechanism are co's, so a pydantic-ai bump can only break the suppression hook
  (caught by the guard test), not the loader's behavior. More code; smaller blast radius.
- **(Alternative, cheaper) Subclass the private `ToolSearchToolset` too.** Additionally reuse
  `ToolSearchToolset` + `_DISCOVERED_TOOLS_METADATA_KEY` + `_SearchTool`, overriding only the bridge tool def
  (name `tool_view`, arg `name`), matching, and `call_tool` dispatch. Less code, but the private surface grows
  to several underscore symbols that can shift shape on an SDK bump.

A third path — upstream a PR making the bridge tool's name/arg/matching configurable — is the cleanest
long-term but adds an external dependency on a merge/release; out of scope for first delivery, noted as
follow-up.

**Either path requires a guard test** (see Testing) that fails loudly if the private SDK surface it depends
on moves. Both supply the capability in `agent/build.py` (suppressing the default `ToolSearch`) and update
`compaction.py`'s preservation to the new loader name (recommended path: + the co-owned metadata key).

## Tasks

### ✓ DONE TASK-1 — `tool_view` loader wrapper + capability
- **files:** `co_cli/agent/` (new wrapper + capability module, e.g. `tool_view.py`), `co_cli/agent/build.py`
  (supply the capability so default `ToolSearch` is suppressed). The capability **must subclass** the SDK
  `ToolSearch` so `_inject_auto_capabilities` does not re-add the default.
- **deliverables (recommended path — co owns the toolset):** (1) `ToolViewCapability(ToolSearch)` overriding
  `get_wrapper_toolset` to return co's toolset; (2) `ToolViewToolset(WrapperToolset)` with `get_tools`
  (partition deferred/visible, raise on `tool_view` name clash, build the bridge tool over the undiscovered
  deferred catalog, union discovered tools in) and `call_tool` (dispatch to the resolution ladder);
  (3) **co's own history-walk unlock parse** — the SDK's `_parse_discovered_tools` lives in
  `ToolSearchToolset.get_tools` and is NOT reused on this path, so co reimplements it: walk `ctx.messages`
  for `tool_view` ToolReturnParts carrying co's metadata key, union those names into the callable set.
  (4) a co-owned discovery-metadata key constant emitted by the ladder and read by this parse.
- **done_when:** driving the real runtime path (build the orchestrator toolset, call `get_tools(ctx)` — not
  grep, not mocks) with a credential-free DEFERRED tool (`skill_create`): the live `get_tools()` keys contain
  `tool_view` and **do not contain `search_tools`** (suppression verified); after a `tool_view`
  call/return cycle for `skill_create` is present in `ctx.messages`, `skill_create` appears in `get_tools()`
  (callable). Driving the model to emit the call is left to evals — the mechanism under test is deterministic.
- **success_signal:** a deferred tool the model addresses by its stubbed name loads in one step without
  keyword crafting, and only one loader is present.
- **prerequisites:** none.

### ✓ DONE TASK-2 — Resolution ladder (exact-first, fuzzy fallback, no silent guess)
- **files:** the wrapper module from TASK-1 (co-written `difflib`-based matcher — no SDK engine reuse on the
  recommended path); a unit test file under `tests/`.
- **done_when:** unit tests over the wrapper's resolution assert all three branches against a synthetic
  deferred set (fabricated names, including one A2-candidate name e.g. `session_search` to demonstrate the
  loader is agnostic to *which* tools are deferred — PO-m-3 cross-plan compat): (a) a normalized-variant name
  (`Skill-Create` / `skill create`) unlocks the canonical `skill_create` (the ToolReturn carries the
  discovery metadata); (b) an unknown-but-close name (a genuine typo, e.g. `skil_create`) returns candidate
  suggestions and the ToolReturn carries **no** discovery metadata (unlocks nothing); (c) a no-overlap name
  returns the no-match/no-retry response and unlocks nothing.
- **success_signal:** a mistyped tool name yields a "did you mean" the model can act on, never a silently
  wrong-enabled tool.
- **prerequisites:** TASK-1.

### ✓ DONE TASK-3 — Re-sync the loader-name couplings (compaction + prompt/rules) + naming sweep
- **files:** `co_cli/context/compaction.py` (`_preserve_deferred_tool_discoveries`),
  `co_cli/tools/deferred_prompt.py`, `co_cli/context/guidance.py`, `co_cli/context/rules/04_tool_protocol.md`,
  `co_cli/agent/_instructions.py`, `co_cli/deps.py` (the `ToolInfo` visibility docstring at :86),
  `tests/test_flow_deferred_tool_stubs.py` (stale module docstring — text only, no assertion change).
- **done_when:** a real run that (1) loads `skill_create` via `tool_view`, then (2) drives a compaction pass,
  asserts `skill_create` is **still callable after compaction** (preservation carries the `tool_view` cycle,
  not the old `search_tools` name); the per-turn stub block + tool-protocol rule instruct loading via
  `tool_view`; **and** a grep for `search_tools` over `co_cli/` returns nothing (the naming sweep — folds in
  former TASK-4 / OQ-3 decision `tool_view`). Specs are synced post-delivery via sync-doc, so they are out of
  this grep's scope.
- **success_signal:** an unlocked deferred tool survives compaction with the new loader name, and no
  co-side reference to the old name remains.
- **prerequisites:** TASK-1.

## Testing

- Unit: the resolution ladder (TASK-2) — exact/normalized hit, fuzzy-suggest-no-unlock, no-match-no-retry.
- Integration (real agent + model): load-by-name unlock (TASK-1) and unlock-survives-compaction (TASK-3) —
  both exercise the runtime path, not grep.
- Regression guard (**mandatory for either OQ-2 path**): a test that fails loudly if the private SDK surface
  the chosen path depends on moves or changes shape — at minimum the `ToolSearch` capability suppression hook
  (recommended path), plus `ToolSearchToolset` / `_DISCOVERED_TOOLS_METADATA_KEY` if the subclassing path is
  chosen. Both paths import ≥1 private symbol, so neither is exempt.
- `scripts/quality-gate.sh full` at ship.

## Open Questions

- **OQ-1 (evidence) — RESOLVED: proceed on first principles, no evidence gate.** The justification is
  structural (keyword search over a fully-name-stubbed set is redundant by construction), not empirical; an
  evidence gate would be near-impossible to satisfy honestly (intermittent small-model keyword stall) and
  would block a sound change. No captured failing run required. (PO C1.)
- **OQ-2 (coupling) — RESOLVED at Gate 1: narrow coupling.** Subclass only the private `ToolSearch`
  capability (the one unavoidable private import — verified: `has_capability_type` uses `isinstance`, so a
  subclass suppresses the auto-injected default), own the toolset on public `WrapperToolset` + a co-owned
  metadata key/parse, with a mandatory guard test. The alternative (subclass `ToolSearchToolset` too) was
  rejected — it widens the private surface and, since the recommended path already writes its own `difflib`
  matcher, the "less code" argument for reuse mostly evaporates. Upstream PR is the long-term path, out of
  first-delivery scope.
- **OQ-3 (naming) — RESOLVED: `tool_view`.** Family-consistency with `memory_view`/`session_view`/`skill_view`
  is the exact coherence this plan buys; the side-effect concern is overstated (those `*_view` loaders are
  already "bring into reach"). Applied via the TASK-3 naming sweep. (PO C1.)

## Final — Team Lead

Plan approved.

**Gate 1 — PASS (verified against pinned SDK 1.81.0 + co code).** Right problem (structural redundancy
confirmed: every DEFERRED tool stubbed by exact name, only keyword `search_tools` loads it). Correct scope
(A1/A2 stays separate, load/call separation preserved). SDK claims verified: suppression via `isinstance`
subclass match, `search_tools` sole unlock path, private symbols present, compaction hardcodes the name at
`compaction.py:256,264`. OQ-2 resolved → narrow coupling. Two corrections folded into HLD/TASK-1/TASK-2:
the matcher is co-written `difflib` (not a reused SDK engine; not `rg`), and the history-walk unlock parse is
an explicit TASK-1 deliverable (compaction only preserves by tool_name string).

## Delivery Summary — 2026-06-06

**Design pivot (mid-dev, on review direction):** the plan's capability-subclass approach was
rejected in favor of a fully SDK-decoupled design — see the DELIVERED DESIGN banner under
High-Level Design. `tool_view` is a normal tool; deferral is co's per-turn visibility filter
keyed on `tool_index` + `runtime.unlocked_tools`; the SDK loader never engages and is never
imported. This deleted three things the plan budgeted for: the capability, the private SDK
import + guard test, and the compaction-preservation coupling (`_preserve_deferred_tool_discoveries`).

| Task | done_when (as delivered) | Status |
|------|--------------------------|--------|
| TASK-1 | `tool_view` present, `search_tools` absent, no `defer_loading` on any tool; unlock via `runtime.unlocked_tools` makes a DEFERRED tool callable | ✓ pass |
| TASK-2 | resolution ladder — normalized-exact unlock / typo suggests-no-unlock / no-match terminal | ✓ pass |
| TASK-3 | loader-name couplings re-synced (compaction coupling deleted, not renamed); prompt/rules/docstrings say `tool_view`; one loader for native + MCP | ✓ pass |

**Files changed (beyond plan's list — flagged):**
- ⚠ `co_cli/agent/mcp.py` — dropped `DeferredLoadingToolset` wrap (it re-stamps `defer_loading`, which would resurrect `search_tools`); MCP now hidden by co's filter. Required for the single-loader invariant.
- ⚠ `co_cli/deps.py` — added `CoRuntimeState.unlocked_tools`.
- ⚠ `co_cli/agent/core.py` — filter rename `_approval_resume_filter` → `_tool_visibility_filter`.
- `co_cli/tools/system/tool_view.py` (new tool), `co_cli/agent/toolset.py` (filter + drop defer_loading + register), `co_cli/context/compaction.py` (delete preservation), `co_cli/tools/deferred_prompt.py`, `co_cli/context/guidance.py`, `co_cli/context/rules/04_tool_protocol.md`, `co_cli/agent/_instructions.py`, `tests/test_tool_view.py` (new), `tests/test_flow_deferred_tool_stubs.py` (docstring).
- `co_cli/agent/build.py` — no net change (capability added then reverted).

**Deviation from TASK-3 grep criterion:** two intentional `search_tools` references remain in `co_cli/`
(`mcp.py:118`, `toolset.py:103` comments) — they document *why the SDK loader is bypassed*, not couplings to it.

**Tests:** scoped — `tests/test_tool_view.py` (6) + regression set (deferred stubs, compaction recovery/history, agent build, approval, capability checks) + bootstrap + integration = all green (60 passed across runs).
**Doc Sync:** fixed — 6 specs (prompt-assembly, core-loop, skills, tools, compaction, self-planning); source docstrings updated in place.

**Overall: DELIVERED** — single name-addressed loader, zero SDK-capability coupling, no compaction coupling; lint clean, scoped tests green, specs synced.

## Implementation Review — 2026-06-06

Stance: issues exist — PASS earned. Reviewed TASK-1/2/3 with three cold-eyes subagents + adversarial reconciliation, emphasis on anti-patterns, module placement, `_prefix` visibility, API shape, dead code, dead/bad tests.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `tool_view` present, `search_tools` absent, no `defer_loading`, unlock makes DEFERRED tool callable | ✓ pass | `tools/system/tool_view.py:52` (@agent_tool ALWAYS); `agent/toolset.py:56` filter gates on `runtime.unlocked_tools`; call path `core.py:59` `combined.filtered(_tool_visibility_filter)`; no `ToolSearch`/`DeferredLoadingToolset` import anywhere in `co_cli/`; `build.py` net-zero diff |
| TASK-2 | resolution ladder: exact-unlock / fuzzy-suggest-no-unlock / no-match-terminal | ✓ pass | `tool_view.py:84-85` exact→`unlocked_tools.add`; `:91-100` fuzzy returns suggestions, no mutate; `:102-104` `tool_error` terminal; `difflib` over names only |
| TASK-3 | couplings re-synced; compaction-preservation deleted; one loader native+MCP | ✓ pass | `_preserve_deferred_tool_discoveries` + call site deleted (`compaction.py`); `mcp.py:118` drops `DeferredLoadingToolset`; prompt/rules/docstrings say `tool_view`; `grep search_tools` → only intentional bypass comments |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale docstring: `compact_messages` still listed `[deferred-tool discoveries]` in assembly | `context/compaction.py:248` | blocking | Removed the dead segment from the docstring |
| **Cross-coupling missed by sweep:** eval W4.E asserted `"search_tools" in tool_names` (tool no longer exists) — broken by this delivery | `evals/eval_skills.py:639` | blocking | Updated to `tool_view`; docstrings re-described to the new mechanism |
| **Eval independence broken:** W4.E reused one bootstrap relying on history-derived discovery; unlock now lives in `runtime.unlocked_tools` → leaks across trials | `evals/eval_skills.py` trial loop | blocking | Added per-trial `deps.runtime.unlocked_tools.clear()`; rewrote the independence note |
| Full-suite fail: ALWAYS schema bucket 20,581 > ceiling 20,200 — new ALWAYS tool `tool_view` (719c, below mean) landed | `tests/test_orchestrator_schema_budget.py:30` | blocking | Re-pinned ceiling 20,200 → 21,000 (deliberate lean loader; the guard working as designed) |
| Redundant test (trivial-delta dup of the hyphen normalization test) | `tests/test_tool_view.py` `test_space_variant…` | minor | Removed; folded the coverage note into the hyphen test |
| Near-tautological assertion mis-readable as structural (a reviewer already proposed deleting the load-bearing line) | `tests/test_tool_view.py:148` | minor | Added comment explaining it's the suppression invariant (no `defer_loading` → SDK loader inert under Agent wrap) |

False-positive (no action): suggestion to mark `tool_view` `is_read_only=True` for peer symmetry — rejected; `tool_view` mutates `runtime.unlocked_tools`, so `is_read_only=False` is the honest label (`is_concurrent_safe=True` is set explicitly; idempotent set-add).

### Out of scope (flagged, not fixed)
- `scripts/dump_tool_index.py` — imports `co_cli.agent._native_toolset` (nonexistent) and prints `info.max_result_size` (no such `ToolInfo` field). **Not in this delivery's diff** — pre-existing breakage from the old `agent/`→`agents/` rename. Fixing correctly needs a domain decision on the intended current API; recommend a separate follow-up.

### Tests
- Command: `uv run pytest -q` (full suite)
- Result: **636 passed, 0 failed** (1 unrelated subprocess-teardown warning) — log `.pytest-logs/20260606-212125-review-impl-rerun.log`. First run caught the schema-budget regression (581 passed, 1 failed → RCA → fix → green).

### Behavioral Verification
- No `co status`/`co health` command exists in this project — full chat is an interactive LLM session (out of automated scope).
- Live-surface check via real bootstrap (`create_deps`): `tool_view` registered, ALWAYS; the per-turn deferred-stub block instructs `tool_view` (not `search_tools`); header reads "Load one by passing its exact name to tool_view, then call it". Toolset assembly (filter + registration + MCP change) constructs without error.
- `success_signal` (model loads a stubbed tool by name in one step, one loader present): verified at the mechanism level (tests); the model-in-the-loop confirmation is W4.E (updated; real-LLM UAT, run at eval time).

### Overall: PASS
All blocking findings fixed (incl. two cross-couplings the co_cli-scoped sweep missed: the W4.E eval rename + its independence break). Full suite green, lint clean, re-scan clean, live surface verified. Ship-ready.
