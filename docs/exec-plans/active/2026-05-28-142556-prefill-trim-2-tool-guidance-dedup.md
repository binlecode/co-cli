# tool-guidance-dedup

> **Child 2 of** `2026-05-28-141854-prefill-trim.md` (canonical reference — measured
> baseline, governing principle, behavioral constraints). This is the adherence-critical,
> philosophy-laden work — the one child that could regress tool routing — and it carries both the
> routing eval validation and the cumulative **schema-budget guard** (which locks the final ALWAYS
> bucket). **Child 3 has shipped** (`completed/2026-05-28-142557-prefill-trim-3-data-tool-schema-trim.md`),
> so its sequencing prerequisite is satisfied; child 2 is now the last unshipped family child
> touching tool schemas (child 4 trims rules only).

## Context

The parent established the governing principle: **one canonical home per piece of guidance** —
tool-specific routing lives at the tool's docstring (locality of reference at the decision point;
a 3B-active model binds nearby guidance more reliably than a rule recalled from 6k tokens away),
cross-tool framing and prominent safety injunctions live in rules. Verified duplication:
`04_tool_protocol.md` "## File tools"/"## Shell" sections ≈ `shell_exec`'s docstring
(`co_cli/tools/shell/execute.py`), which is the **canonical home and is NOT trimmed**.

This child removes that rule duplication AND trims the routing/web/file tool docstrings (desc +
params together, per tool — one docstring, one edit). The overlap set (`web_fetch`, `web_search`,
`file_read`) is edited in both the rule and the docstring here, so the canonical-home seam guard
is contained entirely within this plan.

### Current-state baseline (re-measured 2026-06-01, `tmp/audit_tool_schemas.py`)

The ALWAYS bucket moved since child 3 shipped, because two surface changes from the
`tool-surface-small-model-audit` plan landed afterward and re-shaped the registry:

- `memory_manage` (1 tool) was **split into `memory_create`/`memory_append`/`memory_replace`/`memory_delete`** (4 ALWAYS tools, +3 net).
- `knowledge_analyze` was **deleted** (−1, it was DEFERRED).

Measured now: **26 tools registered** (was 25 in the parent baseline); **ALWAYS bucket = 22,211 chars
(~5,552 tok) across 21 ALWAYS tools**; DEFERRED = 4,043 chars across 5 tools. Note the ALWAYS bucket
is *higher* than child 3's post-trim 21,941 chars — the memory split re-inflated it (4 schemas where
there was 1). **The TASK-4 guard ceiling must re-baseline on this current measured number, not on
child 3's figure.** Child 2's own edit targets (`file_patch` desc 1,025/params 1,086 — still the #1
tool; `web_fetch` desc 1,019; `web_search` desc 796; `file_read` desc 626/params 898) are all
untrimmed and unstarted — the core scope holds.

## Problem & Outcome

**Problem.** Tool-routing guidance is duplicated between rule `04` and tool docstrings (split
signal for a small model), and the routing/web/file docstrings carry `Returns:` enumerations and
internally-enforced caveats the model derives from the result.

**Outcome.** Rule `04` keeps cross-tool framing only; `03` absorbs the stale-data verification
cue; routing/web/file docstrings trimmed to one when-to-use clause + load-bearing injunctions,
with params `Args:` tightened. Expected ~−175 tok (rule) + ~−700–1,000 tok (these docstrings).
Tool routing validated via `eval_mindset_selection.py` — this is the empirical check on the
tool-home principle.

**Saving target reaffirmed (post-refresh).** The parent's child-2 projection (~−175 rule + ~−700–
1,000 docstrings) still holds in absolute terms: the memory split that re-inflated the ALWAYS bucket
touched *memory* schemas, not child 2's targets (`file_patch`/`web_fetch`/`web_search`/`file_read`),
which remain at their pre-trim sizes. If anything the value case is stronger — the bucket now sits
*above* child 3's post-trim figure, so trimming these four routing tools matters more, not less.

## Behavioral Constraints

(Inherits all parent Behavioral Constraints.) Load-bearing for this child:
- **Canonical-home seam guard.** Any guidance removed from rule `04` MUST survive in exactly one
  docstring that is NOT trimmed away here. Explicit per-tool check for `web_fetch`, `web_search`,
  `file_read`.
- **Conservative trim, NOT hermes-brevity** — keep routing/when-to-use cues; the model is small.
- Preserve injunctions: `file_patch` "Requires `file_read` on each affected file before patching";
  `web_fetch` "Use URLs from search results or the user's message; do not fabricate URLs".
- Keep `shell_exec` untouched (canonical routing home). Keep griffe `Args:` formatting.
- Rule ordering invariant (`assembly.py:56-61`) holds — no renumbering.
- **Superseded inherited constraint.** The parent's "memory tool surface unchanged — three separate
  tools" constraint is now obsolete: the memory surface is four tools (`memory_create`/`append`/
  `replace`/`delete`). This does not enlarge child 2's edit scope — memory tools are NOT trimmed
  here — but the TASK-4 guard counts them in the ALWAYS bucket and asserts on per-tool ceilings, so
  the four memory tools are part of the measured baseline, not an exception.

## High-Level Design

### Rule de-duplication
`co_cli/context/rules/04_tool_protocol.md` (current line refs):
- Remove "## File tools" (61–73) and most of "## Shell" (75–87) — duplicates of `shell_exec`'s
  docstring.
- **Keep** the absolute-paths rule (cross-tool; no single docstring home) — currently the opening
  paragraph of "## Shell" (77–79); fold into `## Strategy` or rename `## Paths`.
- **Keep** `## Responsiveness`, `## Strategy`, `## Execute, don't promise`, `## Error recovery`,
  `## Deferred discovery`, `## Memory` pointer.
- The **stale-data verification cue** ("Training data has a cutoff … Use web_search or web_fetch to
  verify") currently lives *inside* "## File tools" (71–73), not in rule `03`. It must survive the
  removal of "## File tools" — relocate it to rule `03` (next item) rather than letting it vanish.

`co_cli/context/rules/03_reasoning.md`: rule 03 was restructured into a `## Verification` section
whose opening already states the precedence principle ("Tool output for deterministic state …
takes precedence over training data", ~lines 4–6) and lists "any current fact that may have changed
since training" among things to verify. **Add the web_search/web_fetch path here** — name them as
the stale-data verification tools — so the cue removed from `04` has exactly one home. (Original
plan said "strengthen 03:6"; the file has since gained the `## Verification` heading, so the edit
lands in that section, not a bare line 6.)

### Routing/web/file docstring trim (desc + params per tool)
`web_fetch`, `web_search`, `file_read`, `file_patch`. Drop `Returns:` enumerations and enforced
caveats; tighten `Args:` to noun-phrase + constraint; keep one when-to-use clause + injunctions.
(`file_read` is in `co_cli/tools/files/read.py`; `file_patch` in `co_cli/tools/files/write.py`.)
**`file_search` is NOT in this plan** — it was child 3's (params-audit owner). **Child 3 has
shipped**, so the prior read.py sequencing hazard is resolved: `file_search` is already trimmed and
`file_read` (same module) is now child 2's to edit freely — no concurrent-edit conflict remains.

**Coordination — `file_patch` split (draft plan).** The `tool-surface-small-model-audit` plan
(`active/2026-05-29-234336-…`, Task 3c, **not yet Gate-1 approved**) proposes splitting `file_patch`
into `file_patch` (single-file replace) + `file_apply_patch` (V4A). Child 2 only *trims*
`file_patch`'s docstring; it does not split. If the split lands first, child 2 trims both resulting
docstrings; if child 2 lands first, the split inherits the trimmed wording. Resolve ordering at
Gate 1 — do not let a docstring trim and a signature split clobber each other (same file,
`files/write.py`). **Default if the split is not yet Gate-1 approved:** child 2 proceeds and trims
the current single `file_patch` docstring; the split, if/when approved, inherits the trimmed prose.
There is no deadlock — child 2 is approval-ready and surgical, so it does not wait on the draft.

## Tasks

### TASK-1 — rule↔docstring de-duplication

**Files:** `co_cli/context/rules/04_tool_protocol.md`, `co_cli/context/rules/03_reasoning.md`.

**Action:** Remove "## File tools" and most of "## Shell" from `04` (keep absolute-paths + error-
recovery + deferred-discovery + memory pointer); relocate the stale-data web_search/web_fetch cue
from `04`'s "## File tools" block into `03`'s `## Verification` section as the named verification
path.

**done_when:**
- `grep -nE "^## File tools|DENY-pattern|BSD utilities" co_cli/context/rules/04_tool_protocol.md`
  returns 0. (NB: the original `"file_read instead"` pattern never matched — a backtick sits between
  the words in the source — so it would false-pass; use the heading + shell-dup markers instead.)
- `grep -n "web_fetch\|web_search" co_cli/context/rules/03_reasoning.md` returns ≥1 (cue relocated,
  not lost).
- `uv run pytest tests/test_flow_prompt_assembly.py -x` passes.
- `uv run python tmp/measure_prompt.py` shows the rules block down ~175 tok.

### TASK-2 — routing/web/file docstring trim

**Prerequisites:** TASK-1 (for the seam-guard overlap check).

**Files:** `co_cli/tools/web/fetch.py`, `co_cli/tools/web/search.py`, `co_cli/tools/files/read.py`
(`file_read` only — `file_search` is child 3's), `co_cli/tools/files/write.py` (`file_patch`).

**Action:** Trim desc (drop `Returns:`/enforced caveats) and params `Args:` per tool; for
`web_fetch`/`web_search`/`file_read` verify the cue removed from rule `04` survives here.

**done_when:**
- `uv run pytest tests/ -k "web or file" -x` passes.
- `uv run python tmp/audit_tool_schemas.py` shows these tools' totals reduced (no fixed-ratio
  gate — adherence over char-parity).

**success_signal:** `co chat` turn touching web_fetch + file_read + file_patch selects the right
tools; read-before-patch honored.

### TASK-3 — routing-adherence eval + sweep

**Prerequisites:** TASK-1, TASK-2.

**Action:**
1. `uv run python evals/eval_mindset_selection.py` — validate tool/skill routing did not regress
   (the empirical check on the tool-home-over-rule-home claim). If routing regresses, restore the
   cross-tool cue to a rule (de-duplicated — one home) and re-run.
2. `mkdir -p .pytest-logs && uv run pytest -x 2>&1 | tee
   .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-guidance-dedup.log`

**done_when:** Eval completes without routing regression; full suite green.

### TASK-4 — cumulative schema-budget guard (ships last)

**Prerequisites:** TASK-1–TASK-3. (Child 3's prerequisite is satisfied — it has shipped — so the
ceiling is now set from a single re-measurement *after* child 2's TASK-1–3 land, taken from the
current registry which already includes child 3's trims, the memory split, and the
`knowledge_analyze` removal.)

**Files:** `tests/test_orchestrator_schema_budget.py` (NEW; sibling to `test_flow_prompt_assembly.py`).

**Action:** Build deps via `co_cli.bootstrap.core.create_deps`; unwrap the toolset
(FilteredToolset → CombinedToolset → FunctionToolset.tools) and call each tool's
`prepare_tool_def(ctx)` to get its `ToolDefinition` — **mirror the proven unwrap in
`tmp/audit_tool_schemas.py`**, which already walks this exact chain. Sum
`name + description + minified-params-JSON`; cross-reference visibility via
`deps.tool_index[name].visibility`. Assertions:
- ALWAYS-bucket total ≤ measured post-child-2-trim value + ~400-char headroom. **Baseline before
  child 2's trims: 22,211 chars / 21 ALWAYS tools (2026-06-01).** Re-measure after TASK-1–3 and pin
  the ceiling to that number — do NOT reuse child 3's stale 21,941 (the memory split re-inflated the
  bucket above it).
- Each ToolDefinition has a non-empty description.
- `len(tools) >= 25` (registry is **26** now — was 25 in the parent baseline; `memory_manage`→4
  tools `+3`, `knowledge_analyze` removed `−1`). Floor is one below current to guard against
  accidental drops, not pin the exact boundary.
- Max per-ALWAYS-tool ≤ a measured ceiling (current top ALWAYS tool is `file_patch` at 2,121 chars
  — set the per-tool cap from the post-trim max, with headroom).

**done_when:** `uv run pytest tests/test_orchestrator_schema_budget.py -x` passes; the asserted
ceiling reflects the post-child-2 re-measurement (which already includes child 3 + the memory split).
Re-run `tmp/audit_tool_schemas.py` *after* TASK-1–3 land and record the measured ALWAYS total as an
inline comment in the test next to the ceiling constant, so the pinned number is auditable, not a
bare magic value. Pin the ceiling and run the guard before any `scripts/quality-gate.sh full` ship
gate — the guard must never run against an un-pinned (self-referential) ceiling.

**success_signal:** A deliberately re-bloated docstring or a new ALWAYS tool fails the guard.

## Testing

- `scripts/quality-gate.sh full`.
- `eval_mindset_selection.py` — the adherence gate (TASK-3).
- `tests/test_orchestrator_schema_budget.py` — the cumulative regression lock (TASK-4).
- `co chat` multi-tool smoke (TASK-2 signal).

## Coordinate with child 4 (rules-block-trim)

Child 4 trims rules `05`/`06`/`07`; this plan trims `03`/`04`. Disjoint files, no hard ordering.
One cross-file overlap to resolve at Gate 1: the "attempts not progressing = blocked, surface it,
retrying is a loop" cue is stated in both `04`'s `## Strategy` ("Track convergence" paragraph —
this plan's file) and `05`'s `## Execution`/`## Completeness` (child 4's file). Agreed canonical
home: keep the goal-level convergence cue in `05`; **this plan additionally drops the near-identical
"Track convergence" paragraph from `04`'s `## Strategy`**, leaving `04 ## Error recovery` with only
the tool-call-level "don't repeat the exact failed call" retry guidance. Add this to TASK-1's `04`
edits.

## Out of scope

- `skill_manage` visibility/discovery (child 1b — `2026-05-28-164327-deferred-tool-stubs.md`);
  data/memory/todo/clarify docstring trims (child 3, shipped); rules `05`/`06`/`07` (child 4). The
  cumulative schema-budget guard is **in scope here** (TASK-4) — it ships with the last family child.
- `shell_exec` trim (canonical home). Splitting `file_patch` (that is the `tool-surface` plan's
  Task 3c, not this one — see the coordination note in High-Level Design). Consolidating the memory
  tools back into one (the surface is now four monomorphic tools by deliberate design).

## Open Questions

- Tool-home-over-rule-home on qwen3.6 — answered empirically by TASK-3's eval; documented in the
  Delivery Summary.

## Delivery Summary — TBD

## Final — Team Lead

Plan approved. Both Core Dev and PO returned `Blocking: none` on Cycle C1 — converged in one cycle.
Minor issues processed: CD-m-1 (adopted — TASK-4 `done_when` now records the measured ALWAYS total
inline and forbids an un-pinned ceiling), PO-m-1 (adopted — Problem & Outcome reaffirms the child-2
saving target), PO-m-2 (adopted — coordination note states the child-2-first default when the
file_patch split is unapproved), CD-m-2 (rejected — informational only; the plan already isolates the
two relevant injunctions and does not over-scope). All refreshed factual claims (26 tools, ALWAYS
22,211 chars / 21 tools, file_patch #1 at 2,121, rule line refs, corrected grep, unwrap chain) were
independently verified live by Core Dev.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev prefill-trim-2-tool-guidance-dedup`
