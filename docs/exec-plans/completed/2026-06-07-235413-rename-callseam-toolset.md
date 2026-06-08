# Rename `_RoutingToolset` → `_CallSeamToolset`

## Context

`_RoutingToolset` (`co_cli/agent/toolset.py:140`) is a `WrapperToolset[CoDeps]` whose `call_tool` is the single per-call seam where co stamps the `tool {name}` span + `co.tool.*` attributes, enforces the per-model-request tool-call cap, and spills oversized MCP string results. It is the outermost layer of the orchestrator toolset stack and also wraps the task-agent's plain `FunctionToolset` (`build_task_agent`).

The name is a positional misnomer: **the class does not route.** Native-vs-MCP dispatch is done by the `CombinedToolset` one layer in; `_RoutingToolset.call_tool` only calls `super().call_tool(...)` and adds the three cross-cutting concerns. The class's own docstring already calls it *"Single explicit seam at the routing `call_tool` boundary"* and `docs/specs/tools.md` already calls it *"the single per-call seam"* — the canon vocabulary is **seam**, not routing. `_CallSeamToolset` makes the class name match that established vocabulary and reveal its real role (house rule: *names must reveal the class's role*).

The symbol is package-private to `co_cli/agent/` — imported only by `agent/build.py` and `agent/core.py`. No public-API or cross-package impact; `__init__.py` files are untouched. Per the zero-backward-compat house rule, this is a hard rename with no alias.

### Code Accuracy Verification (grounded against source, 2026-06-07)

Full reference inventory from `grep -rn` over `co_cli/ tests/ docs/`:

**Code (`co_cli/`) — 5 references, all package-private:**
- `co_cli/agent/toolset.py:140` — class definition; module docstring `:1` ("the routing call_tool wrapper")
- `co_cli/agent/build.py:76` (import), `:110` (`toolsets=[_RoutingToolset(toolset)]`)
- `co_cli/agent/core.py:63` (import), `:67` (`return _RoutingToolset(filtered)`); docstring `:59` ("The routing wrapper sits outermost")

**Tests — 5 files reference the symbol directly:**
- `tests/test_flow_spill.py` (5, 18, 86, 117, 125 — imports + instantiates `_RoutingToolset(inner)`)
- `tests/test_flow_tool_call_limit.py` (3, 18, 37, 44)
- `tests/test_flow_model_request_cap.py` (20, 125, 215, 294)
- `tests/test_flow_observability_spans.py` (5, 24, 97, 160)
- `tests/test_flow_usage_tracking.py` (29, 93)

**Specs referencing `_RoutingToolset` (sync-doc surface — NOT plan tasks):** `tools.md`, `compaction.md`, `core-loop.md`, `observability.md`, `agents.md`.

**`assemble_routing_toolset` (builder) — separate decision (see Scope):** referenced in `co_cli/bootstrap/core.py`, `co_cli/bootstrap/schema_budget.py` (comment), `co_cli/agent/core.py`, `tests/test_tool_view.py`, and specs `bootstrap.md`/`01-system.md`/`agents.md`/`tools.md`.

**Excluded from scope (point-in-time historical record):** completed exec-plans under `docs/exec-plans/completed/` and `docs/reference/RESEARCH-*.md` — never retro-edited.

## Problem & Outcome

**Problem:** the class name claims a behavior (routing) it does not perform, sending every reader to the wrong mental model of the toolset stack. The recent spec consolidation hard-codes the contradiction: a class named `Routing` documented as "the single per-call seam."

**Outcome:** the class is named `_CallSeamToolset`, matching the docstring/spec vocabulary and revealing its actual role. All code and tests reference the new name; the suite passes; bootstrap builds the orchestrator and task agents unchanged.

**Failure cost:** none at runtime (pure rename). The cost of *not* doing it is durable: the misleading name silently mis-teaches the architecture to every future reader and every agent that reads this code, and it compounds now that the spec leans on "seam" terminology that the symbol contradicts.

## Scope

**In scope:**
- Rename the class `_RoutingToolset` → `_CallSeamToolset` and every reference in `co_cli/` and `tests/`.
- Fix wrapper-describing prose in code docstrings/comments that says "routing wrapper" / "the routing call_tool wrapper" → "call-seam wrapper" (these describe the class, not the dispatch surface).

**Decision — keep `assemble_routing_toolset` (the builder) unchanged. TL position; flagged for PO.**
The misnomer is specific to the *wrapper class* claiming to route. The *builder* assembles a `CombinedToolset([native, *mcp]).filtered(...)` surface, and `CombinedToolset` genuinely routes a call to the owning member toolset by name. So "routing toolset" accurately names the assembled dispatch surface; the function's role is to assemble that surface and wrap it in the seam. Renaming it would erase an accurate term and triple the blast radius (`bootstrap/core.py`, `schema_budget.py`, `test_tool_view.py`, four specs). End-state reads coherently: **function = what is assembled (a routing surface); class = the wrapper's role (the per-call seam).**

**Out of scope:**
- `docs/specs/*` edits — handled by `/sync-doc` post-delivery (workflow rule: specs never appear in task `files:`). Sync surface enumerated in Testing.
- Completed exec-plans and `docs/reference/RESEARCH-*` — historical record, never retro-edited.
- Local test variable names like `routing = _CallSeamToolset(inner)` — cosmetic; rename opportunistically, not required.

## Behavioral Constraints

1. **Pure rename — zero behavior change.** No edits to `call_tool` body, cap accounting, span attributes, or spill logic.
2. **Hard rename, no alias** (zero-backward-compat house rule). No `_RoutingToolset = _CallSeamToolset` shim.
3. **Atomic across def + all call sites** — the class def and all importers must change in one delivery or imports break.
4. **`__init__.py` untouched** — symbol is and stays package-private to `co_cli/agent/`.

## High-Level Design

Mechanical identifier substitution `_RoutingToolset` → `_CallSeamToolset`, plus targeted prose fixes for wrapper-describing docstrings. No structural change to the toolset stack. The class stays the outermost `WrapperToolset` returned by `assemble_routing_toolset` and applied in `build_task_agent`.

## Tasks

### ✓ DONE TASK-1 — Rename the class and its `co_cli/` references
- **files:** `co_cli/agent/toolset.py`, `co_cli/agent/build.py`, `co_cli/agent/core.py`
- **done_when:** `grep -rn "_RoutingToolset" co_cli/` returns nothing; `uv run co chat <<<'/exit'` bootstraps to "✓ Ready" with the orchestrator built (non-zero tool count) and exit 0.
- **success_signal:** N/A (pure refactor).
- Includes: class def; the module docstring line at `toolset.py:1` — rewrite the whole line, fixing both the wrapper clause ("the routing call_tool wrapper" → "the call-seam call_tool wrapper") **and** the adjacent stale clause "approval-resume filter" → "the per-turn tool-visibility filter" (the symbol is `_tool_visibility_filter`, which gates two rules, not just resume) since the line is already being edited; imports/usages at `build.py:76,110` and `core.py:63,67`; the "routing wrapper sits outermost" prose in `core.py` docstring (`:59`) → "call-seam wrapper".
- Optional (PO-m-1): tighten the `assemble_routing_toolset` docstring first line to foreground that it returns the **seam-wrapped** routing surface, so the function-vs-class story is self-explanatory at the call site. Not required.

### ✓ DONE TASK-2 — Update test references
- **files:** `tests/test_flow_spill.py`, `tests/test_flow_tool_call_limit.py`, `tests/test_flow_model_request_cap.py`, `tests/test_flow_observability_spans.py`, `tests/test_flow_usage_tracking.py`
- **done_when:** `grep -rn "_RoutingToolset" tests/` returns nothing; the five files plus `tests/test_tool_view.py` (exercises the kept `assemble_routing_toolset` builder) pass: `uv run pytest tests/test_flow_spill.py tests/test_flow_tool_call_limit.py tests/test_flow_model_request_cap.py tests/test_flow_observability_spans.py tests/test_flow_usage_tracking.py tests/test_tool_view.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-rename.log`.
- **success_signal:** N/A (pure refactor).
- Includes: imports, `_CallSeamToolset(...)` instantiations, and docstring/comment mentions of the class.
- **prerequisites:** TASK-1.

## Testing

- **Scoped run (TASK-2 done_when):** the five test files that import the symbol, plus `tests/test_tool_view.py` (exercises `assemble_routing_toolset`, confirms the builder-keep decision still wires correctly).
- **Bootstrap smoke (TASK-1 done_when):** `uv run co chat` builds the orchestrator + a task agent through the renamed seam.
- **Full suite:** at `/ship` (safety net), per the standard gate.
- **Post-delivery `/sync-doc` surface** (auto-invoked by `/orchestrate-dev`): update `_RoutingToolset` → `_CallSeamToolset` in `docs/specs/tools.md`, `docs/specs/compaction.md`, `docs/specs/core-loop.md`, `docs/specs/observability.md`, `docs/specs/agents.md`. Leave `assemble_routing_toolset` references in `bootstrap.md`/`01-system.md`/`agents.md`/`tools.md` as-is (builder kept).
- **Dual-token line (PO-m-3):** `docs/specs/tools.md:283` (the `assemble_routing_toolset` Public-Interface row) contains *both* symbols — "…wrapped in `_RoutingToolset`". The class token must change while the builder token stays; do not treat this line as "leave as-is" during the sync-doc pass.

## Open Questions

1. **Builder rename (for PO).** Keep `assemble_routing_toolset` per the Scope rationale, or purge "routing" consistently and rename it (e.g. `assemble_agent_toolset`)? TL position: keep — the builder name is accurate for the dispatch surface it assembles. Resolved unless PO objects on first-principles grounds.

## Final — Team Lead

Plan approved (C1 — both Core Dev and PO returned `Blocking: none`). Adopted minors: line-number cites corrected (`core.py:63/67/59`); TASK-1 also de-stales the `toolset.py:1` module docstring's "approval-resume filter" clause in the same edit; TASK-2 `done_when` adds `tests/test_tool_view.py` to exercise the kept builder; sync surface flags the dual-token line `tools.md:283`. PO confirmed the builder-keep is sound: `_RoutingToolset` (the wrapper) does not route, but `assemble_routing_toolset` assembles the `CombinedToolset` that genuinely dispatches by name — distinct referents, so renaming one and not the other is coherent.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev rename-callseam-toolset`

## Delivery Summary — 2026-06-08

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep -rn "_RoutingToolset" co_cli/` empty; orchestrator builds, non-zero tool count, exit 0 | ✓ pass |
| TASK-2 | `grep -rn "_RoutingToolset" tests/` empty; scoped suite (5 symbol files + `test_tool_view.py`) green | ✓ pass |

**What shipped:** class `_RoutingToolset` → `_CallSeamToolset` across `co_cli/` (def + imports/usages in `toolset.py`/`build.py`/`core.py`) and 5 test files (10 source/test refs total). Folded-in prose de-stales per plan: `toolset.py:1` module docstring (wrapper clause + "approval-resume filter" → "the per-turn tool-visibility filter"); `core.py` `assemble_routing_toolset` docstring ("routing wrapper sits outermost" → "call-seam wrapper", plus PO-m-1 first-line tightening to foreground the routing-surface-vs-call-seam-wrapper distinction). Builder `assemble_routing_toolset` kept unchanged (it assembles the `CombinedToolset` that genuinely routes).

**Tests:** scoped — 30 passed, 0 failed (`tests/test_flow_spill.py`, `test_flow_tool_call_limit.py`, `test_flow_model_request_cap.py`, `test_flow_observability_spans.py`, `test_flow_usage_tracking.py`, `test_tool_view.py`).

**Doc Sync:** fixed — token rename across `tools.md`/`compaction.md`/`observability.md`/`agents.md`/`core-loop.md` (27 refs), including the dual-token line `tools.md:283` (class token changed, builder token kept) and box/tree-diagram alignment compensated for the +1-char token. Stale class-prose fixed beyond the token: `agents.md:3` nav pointer ("per-call routing wrapper" → "per-call call-seam wrapper") and `co_cli/agent/build.py:21` docstring ("routing wrapper" → "call-seam wrapper" — class-prose the TASK-1 delivery missed). All `assemble_routing_toolset` refs and wrapped-surface prose left intact per builder-keep. Flagged (pre-existing, not fixed): `compaction.md:37-38` box right border is one column short of its siblings.

**Note:** TASK-1 `done_when`'s literal `uv run co chat <<<'/exit'` hangs on piped stdin (interactive-TTY artifact — prompt-toolkit needs a real TTY); verified the done_when *substance* deterministically instead (orchestrator builds through `_CallSeamToolset` with 36 native tools, exit 0).

**Overall: DELIVERED**
Pure rename, zero behavior change; lint clean, scoped tests green, docs synced.

**Next step:** `/review-impl rename-callseam-toolset`

## Implementation Review — 2026-06-08

**Stance: issues exist — PASS is earned.** Proportionate inline review (pure mechanical rename, zero behavior change); the full suite is the real gate.

### System-wide cleanup scan (requested before review)
Repo-wide `grep -rn "_RoutingToolset"` across all `*.py`/`*.md` (excl. `.git/`): **zero matches in every live surface** (`co_cli/`, `tests/`, `docs/specs/`). Builder `assemble_routing_toolset` intact everywhere (34 refs). Residual old-token refs existed only in frozen historical records — per explicit user instruction this turn, the two point-in-time records were retro-edited (overriding the never-retro-edit house rule for literal repo-wide zero matches):
- `docs/exec-plans/completed/2026-06-06-211920-drop-capability-api.md` (4 refs → `_CallSeamToolset`)
- `docs/reference/RESEARCH-pydantic-ai-sdk-usage.md` (3 refs → `_CallSeamToolset`; line-citations stay accurate — class def is still `toolset.py:140`)

The active rename plan retains the old name by necessity (it documents the `_RoutingToolset` → `_CallSeamToolset` rename). Final state: zero `_RoutingToolset` repo-wide outside this plan.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `grep -rn "_RoutingToolset" co_cli/` empty; orchestrator builds, non-zero tools, exit 0 | ✓ pass | `toolset.py:140` `class _CallSeamToolset(WrapperToolset[CoDeps])`; imports/usages `core.py:63,67` + `build.py:76,110`; build check → seam=`_CallSeamToolset`, tools=36, `call_tool` callable |
| TASK-2 | `grep -rn "_RoutingToolset" tests/` empty; scoped suite green | ✓ pass | 0 residual in `tests/`; full suite incl. all 5 symbol files + `test_tool_view.py` green |

Visibility boundary verified: `_CallSeamToolset` imported only within `co_cli/agent/` (`core.py`, `build.py`) — package-private contract preserved. `co_cli/agent/__init__.py` untouched. No scope creep (delivery surface = task `files:` + declared sync-doc specs + user-instructed historical docs).

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale class-prose "routing wrapper" describing the renamed class | `co_cli/agent/build.py:21` | minor | Fixed during delivery sync-doc (→ "call-seam wrapper") |
| Stale nav pointer "per-call routing wrapper" | `docs/specs/agents.md:3` | minor | Fixed during delivery sync-doc (→ "per-call call-seam wrapper") |
| Old token in 2 frozen historical records | completed plan + RESEARCH doc | n/a (cleanup) | Retro-edited per explicit user instruction |

No code defects found. Implementation is a correct, behavior-preserving rename.

### Tests
- Command: `uv run pytest -q`
- Result: **624 passed, 0 failed, 1 warning** (160.38s)
- Warning: `PytestUnraisableExceptionWarning` (`BaseSubprocessTransport.__del__`) — pre-existing asyncio subprocess-cleanup artifact, unrelated to this rename.
- Log: `.pytest-logs/20260509-173946-review-impl.log`

### Behavioral Verification
- No CLI command changed; behavioral surface is the toolset construction path that every tool call flows through.
- Orchestrator + native toolset build through `_CallSeamToolset` (36 tools, callable `call_tool` seam) — confirmed via real construction path.
- Seam runtime behavior (span / per-request cap / MCP spill / usage) exercised by `test_flow_spill`, `test_flow_tool_call_limit`, `test_flow_model_request_cap`, `test_flow_observability_spans`, `test_flow_usage_tracking` — all green against real execution.
- Note: `uv run co chat <<<'/exit'` (TASK-1 literal done_when) hangs on piped stdin — interactive-TTY artifact, not a bootstrap failure; verified substance deterministically instead.

### Overall: PASS
Behavior-preserving rename; live surface 100% clean (and historical records cleaned per user instruction); full suite green, lint clean, seam construction + runtime behavior verified. Ready for Gate 2 → `/ship rename-callseam-toolset`.
