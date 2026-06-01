# tool-guidance-dedup

> **Child 2 of** `2026-05-28-141854-prefill-trim.md` (canonical reference — measured
> baseline, governing principle, behavioral constraints). Ship **last** (after child 3): this is
> the adherence-critical, philosophy-laden work — the one child that could regress tool routing, so
> it goes after the safe params win is banked, and it carries both the routing eval validation and
> the cumulative **schema-budget guard** (which locks the final ALWAYS bucket, hence it lives in
> whatever ships last).

## Context

The parent established the governing principle: **one canonical home per piece of guidance** —
tool-specific routing lives at the tool's docstring (locality of reference at the decision point;
a 3B-active model binds nearby guidance more reliably than a rule recalled from 6k tokens away),
cross-tool framing and prominent safety injunctions live in rules. Verified duplication:
`04_tool_protocol.md` "## File tools"/"## Shell" sections ≈ `shell_exec`'s docstring
(`co_cli/tools/shell/execute.py:24-32`), which is the **canonical home and is NOT trimmed**.

This child removes that rule duplication AND trims the routing/web/file tool docstrings (desc +
params together, per tool — one docstring, one edit). The overlap set (`web_fetch`, `web_search`,
`file_read`) is edited in both the rule and the docstring here, so the canonical-home seam guard
is contained entirely within this plan.

## Problem & Outcome

**Problem.** Tool-routing guidance is duplicated between rule `04` and tool docstrings (split
signal for a small model), and the routing/web/file docstrings carry `Returns:` enumerations and
internally-enforced caveats the model derives from the result.

**Outcome.** Rule `04` keeps cross-tool framing only; `03` absorbs the stale-data verification
cue; routing/web/file docstrings trimmed to one when-to-use clause + load-bearing injunctions,
with params `Args:` tightened. Expected ~−175 tok (rule) + ~−700–1,000 tok (these docstrings).
Tool routing validated via `eval_mindset_selection.py` — this is the empirical check on the
tool-home principle.

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

## High-Level Design

### Rule de-duplication
`co_cli/context/rules/04_tool_protocol.md`:
- Remove "## File tools" (61–73) and most of "## Shell" (80–87) — duplicates of `shell_exec`'s
  docstring.
- **Keep** the absolute-paths rule (cross-tool; no single docstring home) — fold into
  `## Strategy` or rename `## Paths`.
- **Keep** `## Responsiveness`, `## Strategy`, `## Execute, don't promise`, `## Error recovery`,
  `## Deferred discovery`, `## Memory` pointer.

`co_cli/context/rules/03_reasoning.md:6`: strengthen to name `web_search`/`web_fetch` as the
stale-data verification path, so that cue is not lost when removed from `04`.

### Routing/web/file docstring trim (desc + params per tool)
`web_fetch`, `web_search`, `file_read`, `file_patch`. Drop `Returns:` enumerations and enforced
caveats; tighten `Args:` to noun-phrase + constraint; keep one when-to-use clause + injunctions.
(`file_read` is in `co_cli/tools/files/read.py:538-area`; `file_patch` in
`co_cli/tools/files/write.py`.) **`file_search` is NOT in this plan** — it is params-dominated
(393 desc / 1,134 params), so it moved to child 3 (the params-audit owner). Note `file_search`
shares `files/read.py` with `file_read`: **child 3 ships first**, so this plan re-pulls `read.py`
before editing `file_read`, so the two edits are sequential, not conflicting.

## Tasks

### TASK-1 — rule↔docstring de-duplication

**Files:** `co_cli/context/rules/04_tool_protocol.md`, `co_cli/context/rules/03_reasoning.md`.

**done_when:**
- `grep -n "file_read instead\|DENY-pattern\|BSD utilities" co_cli/context/rules/04_tool_protocol.md`
  returns 0.
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

### TASK-4 — cumulative schema-budget guard (moved here from child 3 — ships last)

**Prerequisites:** TASK-1–TASK-3, **and child 3 landed** (the guard ceiling must reflect both
children's trims; child 2 ships last, so this is the lock-it-at-the-end step).

**Files:** `tests/test_orchestrator_schema_budget.py` (NEW; sibling to `test_flow_prompt_assembly.py`).

**Action:** Build deps via `co_cli.bootstrap.core.create_deps`; unwrap the toolset to each
`ToolDefinition` via `prepare_tool_def`; sum `name + description + minified-params-JSON`;
cross-reference visibility via `deps.tool_index[name].visibility`. Assertions:
- ALWAYS-bucket total ≤ measured post-(child 3 + child 2) value + ~400-char headroom.
- Each ToolDefinition has a non-empty description.
- `len(tools) >= 24` (registry is 25 — was 27; `code_execute` + `reason` removed — guard against
  further accidental drops, not at the exact boundary).
- Max per-ALWAYS-tool ≤ a measured ceiling.

**done_when:** `uv run pytest tests/test_orchestrator_schema_budget.py -x` passes; the asserted
ceiling reflects the full cumulative trim (children 3 + 2).

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
  data/memory/todo/clarify tools and the budget guard (child 3); rules `05`/`06`/`07` (child 4).
- `shell_exec` trim (canonical home). Memory-tool consolidation (user direction).

## Open Questions

- Tool-home-over-rule-home on qwen3.6 — answered empirically by TASK-3's eval; documented in the
  Delivery Summary.

## Delivery Summary — TBD
