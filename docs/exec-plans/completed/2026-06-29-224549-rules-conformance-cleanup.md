# Rules-conformance cleanup — post loop-decoupling whole-tree audit

**Triggered by:** `/audit-conformance` whole-codebase sweep (2026-06-29), run after the loop-decoupling milestone (Phases 1→5.6) as its planned mechanical-remainder pass. Emphasis surface: the loop-refactor region (`co_cli/agent/`, `deps.py`, `llm/`).

## Context

The audit scanned all 211 `co_cli/` modules against R1–R12 (`.agent_docs/review.md` *Clarity by Subtraction* + `code-conventions.md`), with a 1000-edge AST import map (MODULE/TYPE_CHECKING/LOCAL/PRIVATE tagged) and four read-only rule-class subagents (boundaries, subtraction, lifecycle, naming/compat/dup).

**Headline: the tree is conformant.** The high-churn loop-refactor surface carries **no residue** beyond what Phase 5.6 already removed — every `CoDeps`/`CoRuntimeState`/`CoSessionState`/`ToolCapState` field is two-sided (R1 = 0), the orchestrator/subagent driver sharing is the by-design scaffolding tenet (R11 = 0), and the absorbed in-loop error handling is all re-raise or documented best-effort/fail-open (R12 = 0). Zero cross-package underscore leaks (R5), zero populated `__init__.py`, zero import-time side effects (R6), zero optimistic flags (R7), zero backward-compat residue (R8).

Only **three** read-confirmed findings survived, none in the loop surface:

1. **R4 — `tools → commands` package inversion.** `co_cli/tools/system/capabilities.py:63` (function-scope) imports `BUILTIN_COMMANDS` from `co_cli.commands.registry` to list slash commands in the capabilities surface (`capabilities.py:71`). `tools` is a leaf; `commands` is the top CLI layer, so the package edge is inverted. **Two corrections to the original audit framing (source-verified):** (a) the import target `co_cli.commands.registry` is itself a declared **leaf** (imports only stdlib + `commands.types` + `skill_types`, never reaches `tools`), so a top-level import would **not** cycle — the function-scope import is cargo-cult, not a load-bearing circular-import workaround. (b) the real coupling is that `BUILTIN_COMMANDS` is an empty module-level dict until `commands/core.py`'s import-time side-effects populate it (verified 0→17 entries). `capabilities_check` is the sanctioned runtime self-knowledge tool, so *reading the command catalog is correct* — but it should read it **downward** off `CoDeps`, not import the CLI registry **upward**. The `bootstrap → commands` edges (`bootstrap/core.py:416`, `bootstrap/banner.py:5`) are **not** counted — bootstrap is a composition root that legitimately wires commands into the app.

   **NB — no existing `deps.capabilities` surface.** An earlier audit draft routed this fix through "`deps.capabilities` / `CoCapabilityState`, the existing bootstrap-set capability registry." Source-verified false: `CoDeps` has no `capabilities` field and there is no `CoCapabilityState` class — the registries are two flat fields, `tool_catalog: dict[str, ToolInfo]` and `skill_catalog: dict[str, SkillInfo]` (`deps.py:324,331`). The fix therefore adds a **new flat field** mirroring those (see TASK-1); it does **not** build the grouped capability-state object (that would touch every `tool_catalog`/`skill_catalog` reader — a refactor far beyond a conformance pass).

2. **R10 — two unused direct dependencies.** `ollama>=0.6.1` (`pyproject.toml:25`) is never imported — co reaches Ollama via `pydantic_ai.providers.ollama.OllamaProvider` (`llm/factory.py:10`), an OpenAI-compatible client that does not need the `ollama` package. `google-auth-httplib2>=0.3.0` (`pyproject.toml:13`) is never imported directly; `uv tree` confirms it is a transitive of `google-api-python-client` (dedup `(*)` marker), so the direct declaration is redundant. Both removals tree-verified safe.

3. **R9 — one naming-unit drift (minor).** `tei_rerank_batch_size` (`config/memory.py:67`) is an item count, not a byte size; bare `_size` is ambiguous per `code-conventions.md`. Cosmetic; deferred.

**Refuted candidates (recorded so the next audit does not re-litigate):** `agent → tools` (dispatch.py:40-41, `format_for_display`/`make_exceeded_payload`) is a **forward** edge (agent is above tools) — not an inversion. `tools → agent` (delegate.py:12 → `delegate_to_agent`) is the **by-design** delegation coupling (a tool must invoke the delegation driver). `context → dream_queue` (compaction.py:59) targets a **foundational** module (imports only config+fileio). `google-genai` is **used** (`index/_providers.py:36,39`). All `except Exception` handlers in the loop/compaction surface are re-raise or documented best-effort.

## Problem & Outcome

**Problem:** the only genuine modularization defect the audit found is one leaf→top import inversion (`tools→commands`), kept alive by a lazy-import workaround; plus two dead direct dependencies and one cosmetic naming drift.

**Outcome:** `capabilities_check` reads the command catalog through a new downward `CoDeps.command_catalog` field populated at bootstrap (the upward import is gone); the two unused direct deps are dropped; the tree is left with no read-confirmed R1–R12 violation except the deferred cosmetic R9.

**Failure cost:** low and slow — this is accretion-prevention, not a live defect. The inversion's risk is that the function-scope upward import normalizes leaf→top reaches, and the next "tool needs to know about X CLI thing" copies the pattern. Removing it now keeps the boundary clean before it recurs.

## Scope

**In:**
- **TASK-1 (R4):** relocate the *command catalog read* off the upward `commands.registry` import onto a new downward `CoDeps.command_catalog: dict[str, str]` (name→description) field, eliminating the `tools→commands` package edge + the function-scope import. `co_cli/deps.py` (add the flat field, mirroring `tool_catalog`/`skill_catalog`), `co_cli/bootstrap/core.py` (populate it from `BUILTIN_COMMANDS` at bootstrap, where `bootstrap→commands` is already a legitimate composition-root edge), `co_cli/tools/system/capabilities.py` (read `ctx.deps.command_catalog`). The field stores `dict[str, str]`, **not** `SlashCommand` objects — storing the type would make `deps` import a `commands` type (`deps→commands`, a worse inversion since `deps` is foundational).
- **TASK-2 (R10):** remove `ollama` and `google-auth-httplib2` from `pyproject.toml` `[project.dependencies]`; `uv lock` to re-resolve.

**Out:**
- **R9 `tei_rerank_batch_size` rename** → `## Deferred backlog` (cosmetic, touches config + env-map + `index/store.py` + `_retrieval.py`; low priority, not worth coupling to a boundary refactor).
- **The `tools→agent` delegation inversion** — by-design; revisit only if a delegation-interface plan reopens it. Not a cleanup target.
- **Any loop-surface change** — the audit found it conformant; nothing to fix.
- **Guard / fitness / structural tests** — forbidden (`testing.md`); the back-edge's absence is proven by the import map, not a test.
- **`docs/specs/` edits** — none implied; if the capability surface doc needs a note it syncs post-delivery via `/sync-doc`.

## Behavioral Constraints

- **No behavior change.** `capabilities_check` must still list exactly the same slash commands; the dep removals must not change any import. Full suite green + `uv run co --help` boot smoke are the proof.
- **Fix at source; structural over detection.** TASK-1 eliminates the inversion by re-homing the read, not by allowlisting it.
- **Scope guard: flat field only.** TASK-1 adds one flat `CoDeps.command_catalog` field — it must **not** grow into a grouped `CoCapabilityState` object that absorbs `tool_catalog`/`skill_catalog` (that touches every reader of those fields and is out of scope). If the flat-field approach turns out to need a public-API restructure beyond adding the field + its bootstrap population, stop and escalate at the ledger gate (mirrors `/review-impl` Phase 4) rather than forcing it.

## Tasks

### ✓ DONE TASK-1 — Remove the `tools→commands` inversion via a new `CoDeps.command_catalog` field
- **files:** `co_cli/deps.py`, `co_cli/bootstrap/core.py`, `co_cli/tools/system/capabilities.py`
- Add a flat `command_catalog: dict[str, str] = field(default_factory=dict)` to `CoDeps` (name→description), mirroring the existing `tool_catalog`/`skill_catalog` fields. Populate it at bootstrap from `BUILTIN_COMMANDS` (`bootstrap/core.py` already imports it at `:416`) — `{name: cmd.description for name, cmd in BUILTIN_COMMANDS.items()}` — where `bootstrap→commands` is a legitimate composition-root edge. Change `capabilities.py:_build_command_surface_lines` to take `deps` and read `deps.command_catalog` instead of importing `BUILTIN_COMMANDS`. Delete the function-scope `from co_cli.commands.registry import BUILTIN_COMMANDS`. Store `dict[str, str]`, not `SlashCommand`, so `deps` never imports a `commands` type.
- **done_when:** `rg -n "from co_cli.commands" co_cli/tools/` returns zero hits; `capabilities_check` output still lists the same slash commands (verified in behavioral check); full suite green.
- **success_signal:** `uv run co --help` boots and the capabilities self-knowledge surface still enumerates the slash commands — no user-visible change.
- **prerequisites:** none.

### ✓ DONE TASK-2 — Drop the two unused direct dependencies
- **files:** `pyproject.toml`, `uv.lock`
- Remove `"ollama>=0.6.1"` and `"google-auth-httplib2>=0.3.0"` from `[project.dependencies]`; run `uv lock` to re-resolve (both remain available transitively / unneeded — tree-verified). Do not touch any source.
- **done_when:** neither appears in `[project.dependencies]`; `uv sync` succeeds; full suite green; `uv run co --help` boots; the Google tools still import (`uv run python -c "import co_cli.tools.google.auth"`).
- **success_signal:** clean dependency set; no runtime import failure for the Ollama provider or Google tools.
- **prerequisites:** none.

## Testing

No new tests (subtraction/boundary pass; guard/fitness tests forbidden — `testing.md`, `project_architecture_erosion_tension`). Behavior-preservation proof = the **existing full suite green** + the **import-map re-run** (TASK-1's back-edge is gone) + the **`uv run co --help` boot smoke** + the catalog-still-lists-commands behavioral check. Run piped to a timestamped `.pytest-logs/` log, spans tailed; RCA any failure to root cause.

## Deferred backlog

- **R9 — `tei_rerank_batch_size` → `tei_rerank_batch_items`** (`config/memory.py:67`, `:35` default const, `index/store.py:192`, `index/_retrieval.py:561`, env map). Cosmetic unit-suffix drift; 1 finding. Pick up in a later naming-only pass or fold into the next config touch.

## Next step

Gate 1 (PO + TL) **revised and re-confirmed 2026-06-29**: the `deps.capabilities`/`CoCapabilityState`-exists premise was source-falsified; TASK-1 rewritten to add a new flat `CoDeps.command_catalog` field (A1), scope-guarded against growing into a grouped capability-state object. TASK-2 verified clean. Gate 1 PASS → `/orchestrate-dev rules-conformance-cleanup`.

## Delivery Summary — 2026-06-29

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `rg "from co_cli.commands" co_cli/tools/` → 0 hits; `capabilities_check` lists same slash commands | ✓ pass |
| TASK-2 | neither dep in `[project.dependencies]`; `uv sync` ok; `co --help` boots; Google tools import | ✓ pass |

**Files changed:** `co_cli/deps.py` (new flat `command_catalog: dict[str, str]` field + `fork_deps` propagation), `co_cli/bootstrap/core.py` (populate from `BUILTIN_COMMANDS` at `CoDeps(...)`), `co_cli/tools/system/capabilities.py` (`_build_command_surface_lines(deps)` reads `deps.command_catalog`; upward import deleted), `pyproject.toml` + `uv.lock` (dropped `ollama`, `google-auth-httplib2`), `tests/test_flow_capability_checks.py` (`_make_deps` populates `command_catalog` as bootstrap does; removed now-redundant in-test `commands.core` import).

**Tests:** scoped — 10 passed, 0 failed (`test_flow_capability_checks`, `test_flow_fork_deps`, `test_instruction_floor_coupling`, `test_instruction_budget`, `test_orchestrator_schema_budget`). One initial failure (`test_capabilities_surfaces_slash_commands`) RCA'd to the test helper bypassing bootstrap; fixed in the test layer (production signature unchanged).
**Doc Sync:** skipped — no `docs/specs/` change implied (internal boundary refactor, no user-visible behavior change; plan scope excludes spec edits).

**Overall: DELIVERED**
Both tasks passed `done_when`; the `tools→commands` package inversion is gone (catalog read downward off `deps.command_catalog`), the two dead direct deps are dropped (`ollama` fully removed from the lock, `google-auth-httplib2` retained transitively), lint clean, scoped tests green.

## Implementation Review — 2026-06-29

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `rg "from co_cli.commands" co_cli/tools/` → 0 hits; `capabilities_check` lists same commands | ✓ pass | `deps.py:333` flat `command_catalog: dict[str,str]` (two-sided: written `bootstrap/core.py:464` + `deps.py:466` fork, read `capabilities.py:62`); upward import deleted; sole caller `capabilities.py:175` passes `ctx.deps`; edge direction now downward |
| TASK-2 | neither dep in `[project.dependencies]`; `uv sync` ok; `co --help` boots; Google tools import | ✓ pass | `pyproject.toml` both lines removed; `uv.lock` re-resolved (`ollama` gone, `google-auth-httplib2` transitive of `google-api-python-client`) |

### Issues Found & Fixed
No blocking issues found. Adversarial greps confirmed: zero orphaned no-arg `_build_command_surface_lines()` calls, zero remaining `BUILTIN_COMMANDS` readers in `co_cli/tools/`, single caller updated. No mocks/fakes, no global state, no new abstraction.

Noted (non-blocking): `tests/test_flow_capability_checks.py` is an extra file beyond TASK-1's declared `files:`, but is a justified test-layer fix (the `_make_deps` helper bypasses bootstrap, so it must populate `command_catalog` as bootstrap does) — recorded in the Delivery Summary. The `_make_deps` change yields a strong assertion (the slash-commands test fails if the catalog read returns empty). `docs/exec-plans/.../loop-decoupling-milestone.md` shows in the working tree but is a pre-existing uncommitted change unrelated to this plan — must not be staged at ship.

### Tests
- Command: `uv run pytest`
- Result: 794 passed, 0 failed
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- TASK-1 `success_signal`: ✓ capabilities surface enumerates the same 17 slash commands downward off `deps.command_catalog` (verified via `test_capabilities_surfaces_slash_commands` in the green suite + direct catalog count check)
- TASK-2 `success_signal`: ✓ `OllamaProvider` + `co_cli.llm.factory` + `co_cli.tools.google.auth` all import cleanly with both direct deps removed
- No LLM-mediated behavior under test — chat REPL non-gating, not exercised.

### Overall: PASS
Both tasks fully implemented and source-verified; full suite green; the package inversion is structurally removed (not allowlisted) and the dead deps dropped — ready to ship.
