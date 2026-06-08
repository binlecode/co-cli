# floor-naming-renames

## Context

The `instruction-floor-audit` work surfaced a naming anti-pattern in the prompt-floor code: **names
that imply a complete/effective set while denoting a partial or registry set.** That gap caused three
documented failures — the F6 partial-guard bug, the runtime `static_floor_tokens` under-count, and an
ungrounded design recommendation during planning (a reviewer read `if "memory_search" in tool_index` as
an *availability* check when `tool_index` is the full registry regardless of per-turn visibility).
Fixing the *consequences* (in `instruction-floor-audit`) left the misleading *names* in place, so the
next reader can re-trip on them. This plan removes the names.

This rewrite does two things: **(a)** refines the two originally-planned renames against the now-settled
post-floor-audit surface, and **(b)** extends the detection to the rest of the
prompt-floor / context-assembly / toolset code, which surfaced two facts that change the second rename.

### Verified current state (live grep, post-floor-audit ship)

- `instruction-floor-audit` is shipped (`docs/exec-plans/completed/2026-06-07-112409-instruction-floor-audit.md`;
  commits v0.8.318 / v0.8.319).
- **`static_floor_tokens` is now accurate.** `co_cli/bootstrap/core.py:446-449` sums all three static
  builders (base + toolset-guidance + personality-critique) into `instruction_tokens`. The name is no
  longer a misnomer — **out of scope, no rename** (this self-resolved exactly as the prior draft predicted).
- **`build_static_instructions`** (`co_cli/context/assembly.py:83`): **14 refs / 6 files.** Returns only
  seed + mindsets + rules. The full static literal is assembled in `ORCHESTRATOR_SPEC` from **three**
  builders (`orchestrator.py:54-58`): `_static_instructions_provider`, `_toolset_guidance_provider`,
  `_personality_critique_provider`. The first is named by *category* (the category all three share); the
  other two are named by *content*. The name claims the whole; it builds the base.
- **`tool_index`** (`deps.tool_index`, `deps.py:300`): `dict[str, ToolInfo]`, **122 refs / 42 files**
  (incl. ~17 test files). Holds the *entire registry regardless of visibility* — a DEFERRED tool stays in
  it; visibility is read separately via `ToolInfo.visibility` and enforced by `_tool_visibility_filter`
  (`toolset.py:62-85`). `in tool_index` reads as "available" but means "registered."

### Two findings from the extended detection pass (these reshape the second rename)

1. **`tool_registry` (the prior draft's target) now collides.** `co_cli/tools/agent_tool.py:21-22` already
   defines `TOOL_REGISTRY: list[Callable]` and `TOOL_REGISTRY_BY_NAME: dict[str, Callable]` — the
   decorator-level registries built *before* bootstrap. Renaming `tool_index` → `tool_registry` would
   leave three "registry" symbols (two of them name-keyed dicts) for a reader to disambiguate — strictly
   worse than the problem it solves. **`tool_registry` is rejected as a target.** `tool_catalog` is
   collision-free (0 refs) and reads as "the full enumerated listing," not "the currently-active set."
2. **`skill_index` is the exact sibling of `tool_index`.** `deps.skill_index: dict[str, SkillInfo]`
   (`deps.py:302`), **105 refs / 29 files**, same `<thing>_index : dict[str, <Thing>Info]` shape, same
   `in skill_index` exposure (e.g. `skill_manifest.py`, `skills.py:147`). The misnomer critique applies to
   it identically. Renaming `tool_index` alone introduces an asymmetry (`tool_catalog` next to
   `skill_index`) that is its own small trap. The same applies to the sibling symbols that carry this
   metadata into the fields — `native_index`, `mcp_index`, and the `set_skill_index`/`get_skill_index`
   accessors — so TASK-2 renames the **whole `dict[str, *Info]` map family as one unit**, or none of it.

## Problem & Outcome

**Problem:** Misleading names imply completeness they don't deliver. `build_static_instructions` names the
base as the whole; `tool_index` / `skill_index` name a complete registry as if membership meant
availability. The floor-audit fixed the *behaviors* these names misled into, but left the names — and the
extended pass shows the originally-proposed fix (`tool_registry`) would have *added* a collision.

**Outcome:** `build_static_instructions` → `build_base_instructions` (and its provider wrapper), with a
docstring stating it is one of three static-instruction builders. The `dict[str, *Info]` metadata-map family
(`tool_index`, `skill_index`, `native_index`, `mcp_index`, `set_skill_index`, `get_skill_index`) is renamed
as one consistent unit to a name that signals "full enumerated listing, not availability" (recommend
`*_catalog`) — **or**, if Gate 1 judges the sweep not worth it, left as-is with a clarifying field docstring
(the membership-misread is a *read* problem a docstring also addresses). Pure behavior-preserving renames,
no aliases. `static_floor_tokens` confirmed accurate, untouched.

**Failure cost:** Leaving the names means the next contributor (or agent) re-reads "static instructions" as
the full floor, or `in tool_index` / `in skill_index` as "is callable" — re-introducing exactly the
partial-floor and stale-gate classes the floor-audit just fixed. Renaming `tool_index` → `tool_registry`
(the prior target) would have manufactured a *new* confusion against `TOOL_REGISTRY`. The names are the
root cause; unaddressed, the floor-audit fix is a patch over a mislabeled mechanism.

## Scope

**In:**
- **TASK-1:** Rename `build_static_instructions` → `build_base_instructions` and the wrapper
  `_static_instructions_provider` → `_base_instructions_provider`; docstring states it is the base
  (seed + mindsets + rules), one of three static builders. 14 refs / 6 files.
- **TASK-2 (Gate-1 go/no-go):** Rename the **`dict[str, *Info]` by-name metadata maps** as one consistent
  set: the `CoDeps` fields `tool_index` → `tool_catalog` and `skill_index` → `skill_catalog`, **plus the
  sibling symbols that carry the same metadata into those fields** — `native_index` → `native_tool_catalog`
  (`agent/core.py:30-31`), `mcp_index` → `mcp_tool_catalog` (`agent/mcp.py:178-188`, `bootstrap/core.py:372,378`).
  Both are tool-only `dict[str, ToolInfo]` slices distinguished by source (native vs MCP) that merge into
  `tool_catalog`, so the names keep the `tool_catalog` head and prefix the source — `native_catalog` /
  `mcp_catalog` would drop the subject (tool? skill? both?), the exact incomplete-name smell this plan kills.
  Plus the public accessors `set_skill_index` → `set_skill_catalog` / `get_skill_index` → `get_skill_catalog`
  (`skills/index.py:12,17`, 14 callsites). Update the field/accessor docstrings to state "full enumerated
  listing of all registered tools/skills regardless of per-turn visibility." ~250 sites across **56 files**.
  No logic change.

**Out:**
- **Scope is the `dict[str, *Info]` by-name maps only.** This is *not* "purge all `_index`." The positional/
  offset-integer `_index` locals (`chunk_index`, `line_index`, `start_index`, `part_index`, `token_index`,
  `target_index`, `glob_index`) keep the suffix — it correctly denotes an offset there. The `_index` suffix
  is misleading only for the name→metadata maps.
- **Out-of-scope function *names* may still own in-scope *parameters*.** `build_toolset_guidance` (function
  name) stays, but its `tool_index` parameter (`guidance.py:21`) and the manual `CoDeps(...)` constructor
  copy kwargs (`deps.py:416-417`) become `tool_catalog`/`skill_catalog` — they are field references, caught
  by the grep, and must be edited.
- **`static_floor_tokens`** — verified accurate post-floor-audit (`core.py:446-449` sums all three
  builders). Not a rename. If the precondition check finds it does *not* sum all three, stop and surface —
  this plan does not proceed.
- **`tool_registry` as a target** — rejected: collides with the existing `TOOL_REGISTRY` /
  `TOOL_REGISTRY_BY_NAME` decorator registries (`agent_tool.py:21-22`).
- **Behavioral changes** — zero. Making `in tool_catalog` reads consult per-turn visibility is a behavior
  change, tracked separately (F5 / floor-audit lineage), not a rename.
- **`TOOL_REGISTRY` / `TOOL_REGISTRY_BY_NAME`** — accurately named (they *are* registries of decorated
  functions); not touched.
- **`build_toolset_guidance` / `static_instruction_builders` (spec field)** — accurate as-is; the field
  legitimately names all three builders, and renaming the member to `_base_instructions_provider` resolves
  the category/content mismatch without needing a field rename.
- **Spec text** (`docs/specs/` — `prompt-assembly`, `bootstrap`, `personality`, `compaction`, `01-system`,
  `tools` all reference these names) — updated by `sync-doc` post-delivery, not in any `files:` list.
- **`completed/` exec-plans and `REPORT-*`** — immutable historical artifacts; never edited.

## Behavioral Constraints

- **Zero behavior change.** Pure renames. Each `done_when` proves behavior preserved via the full suite,
  not just that the symbol changed. If a rename forces any logic edit, that is out of scope — stop and
  surface it.
- **No aliases, no compat shims** (`feedback_zero_backward_compat`). Old name gone in the same commit the
  new one lands — no `OldName = NewName` bridge. Renames are hard and immediate.
- **One rename-unit per atomic commit.** TASK-1 and TASK-2 are separate commits so each diff reads "rename
  X→Y, nothing else moved." TASK-2's whole metadata-map family (`tool_index`, `skill_index`,
  `native_index`, `mcp_index`, `set_skill_index`, `get_skill_index`) ships as **one commit** because it is a
  single consistency decision; splitting it would leave `main` in an asymmetric `_catalog`-next-to-`_index`
  state this plan exists to avoid.
- **Settled-surface precondition.** Both tasks require `instruction-floor-audit` shipped (already true), so
  the sweep covers the final callsite set including that plan's `test_instruction_floor_coupling.py` and
  modified `test_instruction_budget.py`.
- **Grounded naming.** Target names are recommendations; the exact identifier is the Gate-1 decision
  (Open Questions). `tool_catalog` / `skill_catalog` confirmed 0 existing refs; `build_base_instructions` /
  `_base_instructions_provider` confirmed 0 existing refs.

## High-Level Design

A rename is a mechanical, behavior-preserving sweep — its value is a diff a reviewer verifies as "no
behavior moved." Each rename-unit is its own task and commit, gated behind the floor-audit ship (satisfied).

- **TASK-1** is small (14 refs / 6 files), high-confidence, and the code already proves the confusion: the
  floor-audit comment at `core.py:442-445` literally calls this builder's output "base instructions" while
  the function is named `build_static_instructions`. Do it.
- **TASK-2** is wide and a *consistent-set* rename (~250 sites / 56 files for the metadata-map family:
  `tool_index`, `skill_index`, `native_index`, `mcp_index`, `set_skill_index`, `get_skill_index`). Gate 1
  approval is its go/no-go: a reviewer weighs whether the clarity payoff justifies a 56-file sweep, and
  picks between (a) the `_catalog` rename of the whole family or (b) keep the names, add clarifying field
  docstrings only. The **docstring kills the membership-misread in both branches**; the rename additionally
  removes the completeness-implies-availability name-smell. If neither is approved, TASK-1 still ships alone.
- The sweep must rename the **whole map family in one commit**, not just the two fields: the
  `_build_native_toolset` return local (`index` → `catalog`), the `create_deps` caller locals
  (`bootstrap/core.py:358,391`), the `native_index`/`mcp_index` slices that merge into the field, and the
  `set_skill_index`/`get_skill_index` accessors. Renaming only the fields would leave `_index`-named
  functions operating on `_catalog` data — the exact asymmetry the consistency argument exists to prevent,
  and one the bare `\btool_index\b\|\bskill_index\b` grep is blind to (`_` is a word char). Visibility is
  still read via `ToolInfo.visibility` and enforced by `_tool_visibility_filter` — unchanged.

## Tasks

### ✓ DONE — TASK-1 — Rename `build_static_instructions` → `build_base_instructions`

- **files:** `co_cli/context/assembly.py`, `co_cli/agent/orchestrator.py`, `co_cli/bootstrap/core.py`,
  `co_cli/personality/prompts/loader.py`, `tests/test_orchestrator_schema_budget.py`,
  `tests/test_instruction_budget.py`
- **Detail:** Rename the function `build_static_instructions` → `build_base_instructions` and the wrapper
  `_static_instructions_provider` → `_base_instructions_provider` (`orchestrator.py:29,30,32`). Update all
  14 references. Update the function docstring to state it builds the **base** (seed + mindsets + rules) and
  is one of three static-instruction builders (with `_toolset_guidance_provider` and
  `_personality_critique_provider`), not the whole static literal. No logic change.
- **done_when:** `grep -rIn --include='*.py' "\bbuild_static_instructions\b\|\b_static_instructions_provider\b" co_cli tests evals`
  returns **zero** matches, AND `scripts/quality-gate.sh full` passes (behavior preserved).
- **success_signal:** N/A (pure rename).
- **prerequisites:** confirm `deps.static_floor_tokens` is computed by summing all three static builders
  (`core.py:446-449`) — proves floor-audit TASK-4 landed and that `static_floor_tokens` needs no rename.

### ✓ DONE — TASK-2 — Rename the metadata-map family `*_index` → `*_catalog` (Gate-1 go/no-go)

- **files:** the 56-file union containing any of the six map symbols
  (`co_cli/deps.py` field defs, `co_cli/agent/` incl. `core.py`+`mcp.py`, `co_cli/bootstrap/`,
  `co_cli/skills/index.py`, `co_cli/tools/`, `co_cli/context/`, `co_cli/main.py`, and the test files;
  enumerate live with
  `grep -rIl --include='*.py' "\btool_index\b\|\bskill_index\b\|\bnative_index\b\|\bmcp_index\b\|\bset_skill_index\b\|\bget_skill_index\b" co_cli tests evals`).
- **Detail:** Rename, in one commit, the whole `dict[str, *Info]` map family:
  - `CoDeps` fields `tool_index` → `tool_catalog`, `skill_index` → `skill_catalog` (`deps.py:300,302`),
    including the manual constructor-copy kwargs at `deps.py:416-417`.
  - the `_build_native_toolset` return local `index` → `catalog` and the `create_deps` caller locals
    (`bootstrap/core.py:358` native, `:391` skill).
  - the tool-only sibling slices that merge into `tool_catalog`: `native_index` → `native_tool_catalog`
    (`agent/core.py:30-31`), `mcp_index` → `mcp_tool_catalog` (`agent/mcp.py:178-188`, `bootstrap/core.py:372,378`).
    Source prefix + `tool_catalog` head, so the subject is explicit (not bare `native_catalog`/`mcp_catalog`).
  - the public accessors `set_skill_index` → `set_skill_catalog`, `get_skill_index` → `get_skill_catalog`
    (`skills/index.py:12,17`) and all 14 callsites.
  - the `build_toolset_guidance(tool_index)` **parameter** (`guidance.py:21`) → `tool_catalog` (the function
    name stays; only the param is a field reference).
  - docstrings describing any of these (`toolset.py:104-117`, `schema_budget.py:14`, `guidance.py`,
    `skill_manifest.py:20-30`) → "full enumerated listing of all registered tools/skills regardless of
    per-turn visibility." No logic change.
  - **Alternative on Gate-1 no-go to the sweep:** do not rename; add a one-line clarifying docstring to both
    `CoDeps` fields ("registered set, not per-turn-visible set"). The `_index` family stays internally
    coherent, and the docstring still kills the membership-misread — a near-zero diff.
- **done_when:** `grep -rIn --include='*.py' "\btool_index\b\|\bskill_index\b\|\bnative_index\b\|\bmcp_index\b\|\bset_skill_index\b\|\bget_skill_index\b" co_cli tests evals`
  returns **zero** matches, AND `scripts/quality-gate.sh full` passes (behavior preserved). *(If the
  docstring-only alternative is chosen, `done_when` is the two field docstrings present + full suite green;
  the grep assertion does not apply.)*
- **success_signal:** N/A (pure rename).
- **prerequisites:** `instruction-floor-audit` shipped (satisfied); **Gate 1 explicit approval** of (i) the
  56-file sweep vs docstring-only alternative, and (ii) the target identifiers (`*_catalog` vs
  alternatives). This task does not auto-proceed — the breadth and the family-pairing are the decision.

## Testing

- `scripts/quality-gate.sh full` is the behavior-preservation proof for both tasks — a pure rename that
  breaks nothing leaves the suite green. No new tests (renames add no behavior).
- Run each task's grep assertion to confirm the old identifier is fully gone (no stragglers in strings,
  comments, or fixtures). Verified upfront: neither `tool_index` nor `skill_index` is used as a string
  literal / dict key / `getattr` target, so the token-boundary grep is a complete safety net.

## Open Questions

- **OQ-1 (TASK-1 target name):** `build_base_instructions` / `_base_instructions_provider` recommended
  (matches the existing `core.py:442` comment, 0 collisions). Alternative: `build_core_instructions`.
  Decide at Gate 1.
- **OQ-2 (TASK-2 — sweep vs docstring-only, and target name):** The real decision. Note the value split up
  front: the **field docstring kills the membership-misread in *either* branch** — `in tool_catalog` is no
  less misreadable than `in tool_index`; "catalog" is not inherently a not-the-active-set signal. So the
  rename's *marginal* value over docstring-only is removing the completeness-implies-availability name-smell
  and matching the `core.py:442` "base instructions" vocabulary — not fixing the misread. Two sub-questions:
  (a) **Scope:** `_catalog` rename of the whole map family (~250 sites / 56 files, name-smell removed at
  churn cost) **vs** docstring-only on the two fields (misread fixed, no churn, `_index` kept). (b) **Name
  (if renaming):** `*_catalog` recommended; `tool_registry` is **rejected** (collides with `TOOL_REGISTRY` /
  `TOOL_REGISTRY_BY_NAME`). TASK-1 ships regardless of this decision.
- **OQ-3 (family pairing):** The design treats the six map symbols (`tool_index`, `skill_index`,
  `native_index`, `mcp_index`, `set_skill_index`, `get_skill_index`) as one rename unit. Gate 1 may elect a
  narrower set, but any partial rename re-creates the `_catalog`-next-to-`_index` asymmetry, so the default
  is all-or-none-of-the-family.

## Final — Team Lead

Plan approved.

### Gate 1 — PO decision (2026-06-07)

Approved. Verified all quantitative claims against live source (TASK-1 14 refs/6 files; `tool_index`
122/42; `skill_index` 105/29; family 56 files/245 sites; `*_catalog` 0 collisions; `TOOL_REGISTRY`
collision basis at `agent_tool.py:21-22`; `static_floor_tokens` sums all three builders at `core.py:446-449`;
registry≠visibility confirmed; grep safety-net complete — no string-key/getattr uses).

Resolutions:
- **OQ-1:** `build_base_instructions` / `_base_instructions_provider` (0 collisions, matches the existing
  `core.py:442` "base instructions" comment).
- **OQ-2 / OQ-3:** **Full `_catalog` sweep** — the complete-system-cleanup branch. Rename the whole six-symbol
  map family in one commit (`tool_index`/`skill_index`/`native_index`/`mcp_index`/`set_skill_index`/`get_skill_index`
  → `*_catalog`). Docstring-only rejected: it caveats the name rather than removing it, leaving the misleading
  identifier in all 245 sites. Target `*_catalog` confirmed; `tool_registry` rejected (collides with `TOOL_REGISTRY`).
  Accepted caveat: `catalog` does not signal not-the-active-set any better than `index` — so the field/accessor
  docstrings ("full enumerated listing … regardless of per-turn visibility") still carry the read-clarity load.

Both tasks GO. Proceeding to `/orchestrate-dev floor-naming-renames`.

## Delivery Summary — 2026-06-08

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep build_static_instructions\|_static_instructions_provider` → 0; full suite green | ✓ pass |
| TASK-2 | `grep` family (6 symbols) → 0; full suite green | ✓ pass |

**Implementation notes (TL took both tasks — cross-cutting renames, one-commit-by-design for TASK-2):**
- **TASK-1:** `build_static_instructions` → `build_base_instructions`, `_static_instructions_provider` →
  `_base_instructions_provider` across 6 files (14 refs). Docstring rewritten to state it's the **base**
  layer, one of three static builders.
- **TASK-2:** full `_catalog` sweep — `tool_index`/`skill_index`/`native_index`/`mcp_index`/`set_skill_index`/
  `get_skill_index` → `tool_catalog`/`skill_catalog`/`native_tool_catalog`/`mcp_tool_catalog`/`set_skill_catalog`/
  `get_skill_catalog` across 56 files (245 sites). Applied via **word-boundary** rename (perl `\b`, not BSD
  sed) — this was load-bearing: `_real_tool_index` (a test-fixture function in `test_flow_deferred_tool_stubs.py`)
  embeds `tool_index` as a substring; a plain substitution would have wrongly renamed it. It is correctly
  **preserved** (the `\b` done_when grep never flagged it). Also renamed the bare `index` local in
  `_build_native_toolset` (`toolset.py`) → `catalog` so no `_index`-named code operates on `_catalog` data.
- **Membership-semantics docstring** added to the `CoDeps` `tool_catalog`/`skill_catalog` fields
  ("full enumerated listing … regardless of per-turn visibility; membership means *registered*, not
  *callable this turn*") — the read-clarity load OQ-2 flagged.

**Tests:** scoped — TASK-1 3 passed; TASK-2 174 passed. **Full suite — 624 passed, 0 failed** (user-requested
full-scope scan, `153.99s`). Lint clean (ruff check + format).

**Doc Sync:** full scope — `/sync-doc` renamed old identifiers across **10 specs** (prompt-assembly,
personality, tools, skills, bootstrap, 01-system, compaction, memory, dream, tui). Source docstrings/comments
updated in-band by the rename + manual field docstrings. Cross-doc index clean.

**Out-of-scope stale refs (flagged, not edited — outside `/sync-doc` remit + this plan's scope):**
`docs/exec-plans/active/2026-05-27-165621-mindset-stance-selection.md` and
`docs/reference/RESEARCH-personality-self-working-style.md` / `RESEARCH-pydantic-ai-sdk-usage.md` still
carry old names. Active plans and RESEARCH docs are not sync-doc targets.

**Staged-file hygiene note for ship:** `docs/specs/tools.md` had a **pre-existing** modification before this
session (unrelated tool-lifecycle edit); the rename sync stacks on top of it in the same file. Per the
plan's "one rename-unit per atomic commit" constraint, ship should produce **two commits** (TASK-1, then
TASK-2 + its doc sync) — orchestrate-dev does not commit.

**Overall: DELIVERED** — both tasks pass `done_when`, full suite green, lint clean, docs synced.

## Implementation Review — 2026-06-08

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `grep build_static_instructions\|_static_instructions_provider` → 0; full suite green | ✓ pass | grep → 0; `bootstrap/core.py:446` `build_base_instructions(config)` summed into floor; `orchestrator.py:55` `_base_instructions_provider` leads the static-builders tuple; `assembly.py:83` docstring states "base layer, one of three" |
| TASK-2 | `grep` family (6 symbols) → 0; full suite green | ✓ pass | grep → 0 (py + specs); `deps.py:300,302` `tool_catalog`/`skill_catalog` fields + membership docstring; `core.py:378` `tool_catalog.update(mcp_tool_catalog)` merge intact; `toolset.py:76,85` visibility still gated on `entry.visibility` (unchanged); 11 accessor callsites resolved to `*_catalog`, 0 orphaned |

### Behavior-preservation checks (rename-specific)
- **False-friend fixture preserved:** `_real_tool_index` (test helper embedding `tool_index` as substring) correctly untouched — count 2. Word-boundary (`perl \b`) rename, not BSD sed, was load-bearing here.
- **No contract renamed:** no string literal / dict-key / span-attribute named `*_catalog` exists — only identifiers changed. Serialization keys (`"tool_approvals"`, `"skill_count"`) intact.
- **Cross-task integration:** 0 orphaned old-name references anywhere in `co_cli`/`tests`/`evals`/`docs/specs`.

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest` (full suite)
- Result: **624 passed, 0 failed**, 1 warning, 153.99s
- Log: `.pytest-logs/<ts>-full.log`. Phases 2–6 applied zero source edits — green result stands.
- Lint: `scripts/quality-gate.sh lint` → PASS (ruff check + format clean, 330 files).

### Behavioral Verification
- `uv run co chat` (EOF boot): ✓ system boots; banner renders "8 skill(s) loaded" via `get_skill_catalog(deps.skill_catalog)`; "Tools: 38  Skills: 8  MCP: 1" — bootstrap → `create_deps` (tool_catalog/skill_catalog/native_tool_catalog/mcp_tool_catalog) → banner → capability check all exercise the renamed fields without error. `(degraded)` is pre-existing/environmental (TEI reranker unavailable), unrelated.
- `success_signal`: N/A both tasks (pure renames).

### Overall: PASS
Pure behavior-preserving rename; full suite green, lint clean, no contract or behavior altered, system boots correctly on the renamed surface. Ship reminder (from delivery): produce **two atomic commits** (TASK-1; TASK-2 + doc sync) and account for the pre-existing `docs/specs/tools.md` edit when staging.
