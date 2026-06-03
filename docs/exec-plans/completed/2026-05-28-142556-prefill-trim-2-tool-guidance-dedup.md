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

### Current-state baseline (re-measured 2026-06-02, `tmp/audit_tool_schemas.py`)

The ALWAYS bucket moved again since the 2026-06-01 measurement, because three surface changes from
the `tool-surface-small-model-audit` plan have now all shipped and re-shaped the registry:

- `memory_manage` (1 tool) was **split into `memory_create`/`memory_append`/`memory_replace`/`memory_delete`** (4 ALWAYS tools, +3 net).
- `skill_manage` (1 tool) was **split into `skill_create`/`skill_edit`/`skill_patch`/`skill_delete`** (Task 3b, +3 net) — landed after the prior baseline.
- `knowledge_analyze` was **deleted** (−1, it was DEFERRED).
- `file_patch` **V4A multi-file mode was removed** (Task 3c, Option B) — the docstring shrank
  dramatically and `file_patch` is now a single monomorphic single-file-replace tool.

Measured now (re-measured 2026-06-02 post-Google-tools): **35 tools registered**; **ALWAYS bucket =
22,589 chars (~5,647 tok)**; DEFERRED = 10,194 chars. (The registry grew 28 → 35 because the v0.8.280
Google least-privilege tools — `google_calendar_search`/`google_calendar_list`/`google_drive_search`/
`google_drive_read`/`google_gmail_search` — landed after the prior refresh; they are all **DEFERRED**,
so the ALWAYS bucket moved only +119 chars while the DEFERRED bucket ballooned ~6k chars.) **The TASK-4
guard ceiling must re-baseline on this current measured number.** Child 2's
remaining edit targets are `web_fetch` (desc 1,019), `web_search` (desc 808, post-drop), and `file_read`
(desc 626/params 898). **`file_patch` is NO LONGER a target** — post-3c it measures desc 477/params
787 (rank #9, below the desc>600 / params>800 trim heuristic), so its docstring was effectively
trimmed by the V4A removal. The current #1 desc is `shell_exec` (1,317 chars), but that is the
**untouched canonical routing home** by design — not a trim target.

## Problem & Outcome

**Problem.** Tool-routing guidance is duplicated between rule `04` and tool docstrings (split
signal for a small model), and the routing/web/file docstrings carry `Returns:` enumerations and
internally-enforced caveats the model derives from the result.

**Outcome.** Rule `04` keeps cross-tool framing only; `03` absorbs the stale-data verification
cue; routing/web/file docstrings trimmed to one when-to-use clause + load-bearing injunctions,
with params `Args:` tightened. Expected ~−175 tok (rule) + ~−700–1,000 tok (these docstrings).
Tool routing validated via `eval_mindset_selection.py` — this is the empirical check on the
tool-home principle.

**Saving target (post-3c refresh).** The parent's child-2 projection assumed four docstring targets
including `file_patch`. Task 3c's V4A removal already shrank `file_patch` below the trim heuristic, so
child 2's docstring scope is now **three tools** (`web_fetch`/`web_search`/`file_read`). The rule-side
saving (~−175 tok) is unchanged; the docstring saving is correspondingly smaller (~−500–700 tok over
three tools instead of four) because the largest target already self-trimmed. The remaining three
routing docstrings are untouched and still carry `Returns:` enumerations and enforced caveats worth
trimming.

## Behavioral Constraints

(Inherits all parent Behavioral Constraints.) Load-bearing for this child:
- **Prerequisite — sequenced after `drop-web-research-add-fetch-extraction`
  (`2026-06-02-160319`, another team, in progress).** That plan removes the `web_research` tool
  and the `web_search`↔`web_research` steer clause from `web_search`'s docstring, and adds
  trafilatura content extraction to `web_fetch` (code, not its docstring). This child MUST start
  from the **post-drop tree**: do **not** preserve or re-add any `web_research` steer (it is gone
  upstream), and re-measure `web_search`/`web_fetch` against the post-drop docstrings before
  trimming. The drop only removes a DEFERRED tool, so the ALWAYS bucket (TASK-4's subject) is
  unaffected; only the registry count and the DEFERRED bucket shrink.
- **Canonical-home seam guard.** Any guidance removed from rule `04` MUST survive in exactly one
  docstring that is NOT trimmed away here. Explicit per-tool check for `web_fetch`, `web_search`,
  `file_read`.
- **Conservative trim, NOT hermes-brevity** — keep routing/when-to-use cues; the model is small.
- Preserve injunctions, verbatim from the **current** docstring (do not paraphrase): `web_fetch`'s
  "Accepts any URL — from the user's message, from web_search results, or from tool output. Never
  guess or fabricate URLs yourself." (note it includes the "from tool output" clause — keep it).
  (`file_patch`'s "Requires `file_read` … first" injunction is out of scope — that tool is no longer
  trimmed here; the injunction lives in its post-3c docstring and stays.)
- **Keep `web_fetch`'s Shell-fallback cue** (`fetch.py:133–135`: retry blocked/403/Cloudflare fetches
  with `curl -sL <url>`). It is a when-to-use/fallback routing cue, NOT a `Returns:` enumeration or
  passive caveat — condense it, do not drop it.
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
`web_fetch`, `web_search`, `file_read`. Drop `Returns:` enumerations and enforced caveats; tighten
`Args:` to noun-phrase + constraint; keep one when-to-use clause + injunctions. (`file_read` is in
`co_cli/tools/files/read.py`.) **`file_search` is NOT in this plan** — it was child 3's (params-audit
owner). **Child 3 has shipped**, so the prior read.py sequencing hazard is resolved: `file_search` is
already trimmed and `file_read` (same module) is now child 2's to edit freely — no concurrent-edit
conflict remains.

**`file_patch` dropped from scope — resolved by Task 3c.** The earlier draft proposed splitting
`file_patch` into `file_patch` (single-file replace) + `file_apply_patch` (V4A). **That split did
NOT happen.** Task 3c of `tool-surface-small-model-audit` shipped **Option B** instead: V4A multi-file
mode was removed entirely and `file_patch` stayed a single monomorphic single-file-replace tool
(`file_apply_patch` never existed). The V4A removal already shrank `file_patch`'s docstring below the
trim heuristic (desc 477/params 787, rank #9), so there is no docstring left for child 2 to trim and
no signature-clobber hazard. **`file_patch` is out of child 2's scope.**

## Tasks

### ✓ DONE TASK-1 — rule↔docstring de-duplication

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

### ✓ DONE TASK-2 — routing/web/file docstring trim

**Prerequisites:** TASK-1 (for the seam-guard overlap check); **`drop-web-research-add-fetch-extraction`
shipped** — `web_search.py`/`fetch.py` are at their post-drop state before this task edits them.

**Files:** `co_cli/tools/web/fetch.py`, `co_cli/tools/web/search.py`, `co_cli/tools/files/read.py`
(`file_read` only — `file_search` is child 3's). **`co_cli/tools/files/write.py` (`file_patch`) is no
longer in scope** — Task 3c already shrank it below the trim heuristic.

**Action:** Trim desc (drop `Returns:`/enforced caveats) and params `Args:` per tool; for
`web_fetch`/`web_search`/`file_read` verify the cue removed from rule `04` survives here. For
`web_search` specifically: the `web_research` steer clause is already **removed upstream** by the drop
plan — do NOT preserve or re-add it; trim the post-drop docstring as-is (its desc has shrunk
accordingly, so re-measure before trimming). For `web_fetch` specifically: preserve the fabricate-URLs
injunction verbatim (incl. "from tool output") and keep the Shell-fallback cue (`fetch.py:133–135`)
condensed — both are routing-load-bearing, not trimmable caveats. (The drop plan changed `web_fetch`'s
**code**, not its docstring, so the docstring is still child 2's to trim — but confirm the line refs
post-drop.)

**done_when:**
- `uv run pytest tests/ -k "web or file" -x` passes.
- `uv run python tmp/audit_tool_schemas.py` shows these tools' totals reduced (no fixed-ratio
  gate — adherence over char-parity).

**success_signal:** `co chat` turn touching web_fetch + file_read + file_patch selects the right
tools; read-before-patch honored.

### ✓ DONE TASK-3 — routing-adherence eval + sweep

**Prerequisites:** TASK-1, TASK-2.

**Action:**
1. `uv run python evals/eval_mindset_selection.py` — validate tool/skill routing did not regress
   (the empirical check on the tool-home-over-rule-home claim). If routing regresses, restore the
   cross-tool cue to a rule (de-duplicated — one home) and re-run.
2. `mkdir -p .pytest-logs && uv run pytest -x 2>&1 | tee
   .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-guidance-dedup.log`

**done_when:** Eval completes without routing regression; full suite green.

### ✓ DONE TASK-4 — cumulative schema-budget guard (ships last)

**Prerequisites:** TASK-1–TASK-3. (Child 3's prerequisite is satisfied — it has shipped — so the
ceiling is now set from a single re-measurement *after* child 2's TASK-1–3 land, taken from the
current registry which already includes child 3's trims, both the memory and skill splits, the
`knowledge_analyze` removal, and the Task 3c V4A removal.)

**Files:** `tests/test_orchestrator_schema_budget.py` (NEW; sibling to `test_flow_prompt_assembly.py`).

**Action:** Build deps via `co_cli.bootstrap.core.create_deps`; unwrap the toolset
(FilteredToolset → CombinedToolset → FunctionToolset.tools) and call each tool's
`prepare_tool_def(ctx)` to get its `ToolDefinition` — **mirror the proven unwrap in
`tmp/audit_tool_schemas.py`**, which already walks this exact chain. Sum
`name + description + minified-params-JSON`; cross-reference visibility via
`deps.tool_index[name].visibility`. Assertions:
- ALWAYS-bucket total ≤ measured post-child-2-trim value + ~400-char headroom. **Baseline before
  child 2's trims: 22,589 chars (re-measured 2026-06-02, post-Google-tools), reflecting both splits,
  the 3c V4A removal, the `web_research` drop, AND the v0.8.280 Google tools.** The Google tools and
  the `web_research` drop do **not** materially change this ALWAYS number — the new Google tools are
  all DEFERRED, and `web_research` was DEFERRED; the ALWAYS bucket moved only +119 chars (22,470 →
  22,589) while the DEFERRED bucket grew to 10,194 chars. Re-measure after TASK-1–3 and pin the
  ALWAYS ceiling to that number.
- Each ToolDefinition has a non-empty description.
- `len(tools) >= 27` (registry is **35** now — the floor is far below current and is a guard against
  accidental drops, NOT a pin of the exact boundary; the 28 → 35 growth is the five DEFERRED
  v0.8.280 Google tools — `google_calendar_search`/`google_calendar_list`/`google_drive_search`/
  `google_drive_read`/`google_gmail_search` — none of which enter the ALWAYS bucket). Leave the
  floor at `>= 27`; do not raise it to track the deferred-tool count.
- Max per-ALWAYS-tool ≤ a measured ceiling. Post-3c the top ALWAYS tool by total is `file_search`
  (~2,100 chars; child 3's, already trimmed), with `shell_exec` (~1,956, the untouched canonical
  home) next — `file_patch` is no longer the max (it dropped to ~1,274). Set the per-tool cap from
  the post-trim max, with headroom.

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
- `shell_exec` trim (canonical home). `file_patch` (Task 3c's V4A removal already shrank it below the
  trim heuristic — see the resolved coordination note in High-Level Design). Consolidating the memory
  or skill tools back into one (both surfaces are now four monomorphic tools by deliberate design).

## Open Questions

- Tool-home-over-rule-home on qwen3.6 — answered empirically by TASK-3's eval; documented in the
  Delivery Summary.

## Delivery Summary — 2026-06-02

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grep markers 0 / cue relocated ≥1 / assembly test pass / rules block down | ✓ pass |
| TASK-2 | web/file tests pass / trimmed tool totals reduced | ✓ pass |
| TASK-3 | eval completes without routing regression / full suite green | ✓ eval pass (no regression); full suite deferred to review-impl |
| TASK-4 | schema-budget guard passes; ALWAYS ceiling pinned | ✓ pass |

**Measured outcomes**
- **Rules block (TASK-1):** `04` "## File tools"/"## Shell" removed (dup of `shell_exec` canonical home); absolute-paths kept as `## Paths`; stale-data cue relocated to `03 ## Verification`; "Track convergence" dropped per child-4 coordination. Net −1,486 chars (~−371 tok). `grep -nE "^## File tools|DENY-pattern|BSD utilities"` → 0; `grep web_fetch|web_search` in `03` → ≥1.
- **Docstrings (TASK-2):** `web_fetch` 1,626→1,013, `web_search` 1,398→914, `file_read` 1,533→1,029 chars. Injunctions preserved verbatim (fabricate-URLs incl. "from tool output", Shell-fallback, file_search-first). **ALWAYS tool bucket 22,589 → 20,988 chars (−1,601).**
- **Routing eval (TASK-3):** `eval_mindset_selection.py` completed all 8 cases; agent routed `file_search`/`file_read`/web tools correctly throughout — **no routing regression**. (Verdict `INERT` concerns the mindsets block — a separate doctrine matter, untouched here. Trailing crash was an MCP/anyio teardown race after the verdict, not an eval-logic failure.) Before/after diff of 382 common LLM tests: **zero span-count regressions** — identical model-request rounds pre/post trim; compaction calls marginally faster.
- **Schema-budget guard (TASK-4):** `tests/test_orchestrator_schema_budget.py` (NEW). Pins ALWAYS bucket ≤ 21,400 (measured 20,988 + headroom), per-tool ≤ 2,300 (max `file_search` 2,111), tool floor ≥ 27 (live 35), non-empty descriptions. Hardened to `stack=None` (headless, MCP-free) for determinism in the ship gate.

**Tests:** scoped — 71 passed, 0 failed (prompt_assembly + web/file + schema_budget). Partial full suite reached 58% / 382 passed / 0 failed (incl. real-LLM compaction + repl tests) before being stopped on user instruction; full-suite completion deferred to review-impl per skill ownership.
**Doc Sync:** clean — no spec references removed rule sections or old docstrings (narrow scope, no changes).

**Open Question answered (tool-home-over-rule-home on qwen3.6):** Empirically supported — relocating tool-routing guidance to docstrings (canonical homes) with de-duplicated rules produced no routing regression across the eval's 8 cases and no change in tool-call structure across 382 LLM tests.

**Overall: DELIVERED**
All four tasks pass their done_when (TASK-3's eval half green with no routing regression; full-suite half deferred to review-impl). Lint clean, scoped tests green, no doc drift. A separate context-stability investigation (tail_fraction proportionality, anti-thrash gate fallback, spill/compaction band, tool-schema budget) surfaced during review and is recommended as a follow-up plan — out of scope here.

## Final — Team Lead

Plan approved. Both Core Dev and PO returned `Blocking: none` on Cycle C1 — converged in one cycle.
Minor issues processed: CD-m-1 (adopted — TASK-4 `done_when` now records the measured ALWAYS total
inline and forbids an un-pinned ceiling), PO-m-1 (adopted — Problem & Outcome reaffirms the child-2
saving target), PO-m-2 (adopted — coordination note states the child-2-first default when the
file_patch split is unapproved), CD-m-2 (rejected — informational only; the plan already isolates the
two relevant injunctions and does not over-scope). All refreshed factual claims (26 tools, ALWAYS
22,211 chars / 21 tools, file_patch #1 at 2,121, rule line refs, corrected grep, unwrap chain) were
independently verified live by Core Dev.

## Plan Refresh — 2026-06-02 (pre-Gate-1 re-verification)

Re-verified against the live tree after Task 3b (`skill_manage` split) and Task 3c (`file_patch` V4A
removal) shipped from `tool-surface-small-model-audit`. Drift found and corrected in this plan:

- **Registry:** 26 → **29 tools**; ALWAYS bucket 22,211 → **22,470 chars** (`tmp/audit_tool_schemas.py`).
- **`file_patch` dropped from TASK-2 scope.** It is no longer the #1 tool — Task 3c's V4A removal shrank
  it to desc 477/params 787 (rank #9), below the trim heuristic. The earlier "split vs trim" coordination
  note is resolved: the split never happened (Option B removed V4A; `file_apply_patch` never existed).
- **TASK-4 numbers re-baselined:** ALWAYS ceiling 22,470; `len(tools) >= 28`; per-tool max example moved
  off `file_patch` (now `file_search` ~2,100 / `shell_exec` ~1,956).
- Rule-`04` line references (File tools 61–73, Shell 75–87, stale-data cue 71–73, Track-convergence 37–41)
  re-checked against the current file — **still accurate**. The `04` "## File tools" block additionally
  carries two stale references (`file_find` — no such tool; a `glob` filter — the param is `path`) that
  TASK-1's deletion of that block sweeps away as a side effect.

TASK-1 (rule dedup) is unaffected by the drift; TASK-2 shrinks from four docstrings to three. Plan is
Gate-1-ready against the current tree.

## Plan Refresh — 2026-06-02 (sequenced after `drop-web-research-add-fetch-extraction`)

This child now **follows the delivery** of `2026-06-02-160319-drop-web-research-add-fetch-extraction.md`
(another team, in progress). That plan drops the `web_research` tool + its in-turn delegation machinery
and adds trafilatura content extraction to `web_fetch`. Impact on this child:

- **Hard ordering:** this child starts from the **post-drop tree**. Both plans edit `web/search.py` and
  `web/fetch.py` — they must not run concurrently; the drop ships first.
- **Steer inversion (supersedes the earlier Gate-1 nit).** The `web_search`↔`web_research` reciprocal
  steer (added by `tool-surface` T5-web) is **removed upstream** by the drop. TASK-2 must NOT preserve
  or re-add it — earlier review guidance to "preserve the steer" is void. `web_search`'s desc shrinks
  when the steer clause is removed, so re-measure before trimming.
- **`web_fetch`:** the drop changes `web_fetch`'s **code** (extraction), not its docstring — the
  docstring stays this child's to trim; re-confirm the Shell-fallback line ref post-drop.
- **Counts:** registry **29 → 28** (`web_research −1`); DEFERRED bucket shrinks (~−1,268 chars);
  **ALWAYS bucket unchanged** (`web_research` was DEFERRED). TASK-4 floor updated `>= 28` → `>= 27`;
  the ALWAYS ceiling re-measure is unaffected by the drop and still taken after TASK-1–3.
- TASK-1 (rules `03`/`04`) is entirely unaffected by the drop.

## Plan Refresh — 2026-06-02 (Gate-1 re-verification, post-Google-tools)

Re-verified live against the current tree at Gate 1. Prerequisites confirmed shipped:
`drop-web-research-add-fetch-extraction` and child 3 are both in `completed/`; `web_research` is gone
from `web/search.py` (steer removed — do not re-add); trafilatura extraction is in `web/fetch.py`.

Central dedup claim re-confirmed: rule `04`'s "## File tools"/"## Shell" duplicate `shell_exec`'s
docstring (`execute.py:21-56`) almost line-for-line (file_read/file_search routing, DENY-pattern +
approval, diagnose-and-retry, macOS BSD utils). The stale-data cue (`04:71-73`) is NOT in shell_exec —
relocation to `03 ## Verification` (TASK-1) is correct, not deletion. Rule-04 line refs still accurate
(File tools 61-73, Shell 75-87, Track-convergence 37-41, stale `file_find`/`glob` at 65/68).

Drift found and corrected (numbers only — no scope change to TASK-1/2/3):

- **Registry 28 → 35; ALWAYS 22,470 → 22,589 chars; DEFERRED → 10,194 chars.** Cause: the v0.8.280
  Google least-privilege tools landed after the prior refresh. All five are **DEFERRED**, so the ALWAYS
  bucket (TASK-4's subject) moved only +119 chars and the trim targets are unaffected.
- **TASK-4 baseline re-pinned to 22,589 chars**; the `len(tools) >= 27` floor is unchanged (35 ≫ 27 —
  intentionally loose) but its rationale was corrected from the stale "registry is 28".
- `web_search` desc measured 808 (was 796) post-drop — still above heuristic, still a valid target.
- Shell-fallback cue moved to `fetch.py:158-159` (was 133-135 — trafilatura shifted it); fabricate-URLs
  injunction at `fetch.py:156` (verbatim, incl. "from tool output"). Confirm both line refs during dev.

Per-tool max examples (`file_search` 2,111, `shell_exec` 1,966) verified accurate — unchanged.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev prefill-trim-2-tool-guidance-dedup`

## Implementation Review — 2026-06-02

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | grep markers → 0; web cue in `03` → ≥1; assembly test pass | ✓ pass | `grep -nE "^## File tools\|DENY-pattern\|BSD utilities" 04` exits 1 (0 matches); `03_reasoning.md:14-16` carries relocated web_search/web_fetch cue under `## Verification`; `tests/test_flow_prompt_assembly.py` 5 passed |
| TASK-1 | seam guard — removed `04` content survives in canonical home | ✓ pass | shell_exec docstring `execute.py:26-46` retains file_read/file_search routing + DENY-pattern + non-zero-exit + BSD utils; absolute-paths kept in `04` as `## Paths` (04:55-58); "Track convergence" canonical home `05_workflow.md:27-29` |
| TASK-2 | web/file tests pass; trimmed totals reduced | ✓ pass | `pytest -k "web or file"` 65 passed; audit re-run: web_fetch 1,013 / web_search 914 / file_read 1,029 (audit total), ALWAYS bucket 22,589→20,988 |
| TASK-2 | injunctions survive verbatim | ✓ pass | `fetch.py:154-155` fabricate-URLs verbatim incl. "from tool output"; `fetch.py:157-158` Shell-fallback (curl -sL) condensed-kept; `web_research` absent from `search.py`; `read.py:411` file_search-first cue kept; griffe Args parse for all 3 |
| TASK-3 | eval no routing regression | ✓ pass | `eval_mindset_selection.py` 8/8 correct routing; 0 span-count regressions across 382 LLM tests (delivery) |
| TASK-4 | schema-budget guard passes; ceiling pinned | ✓ pass | `tests/test_orchestrator_schema_budget.py` 1 passed; real deps via `create_deps` (line 74, `stack=None`); 4 assertion classes (lines 100/102/113-117/119); pinned literals with inline measured comments (lines 31-38), not self-referential; live ALWAYS 20,988 ≤ 21,400, max file_search 2,111 ≤ 2,300, 35 tools ≥ 27 |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Reported docstring char endpoints (819/697/836 raw `__doc__`) appeared to mismatch delivery (1,013/914/1,029) | — | false-positive | Two different metrics — delivery numbers are audit `total` (name+desc+minified-params-JSON), confirmed by re-running `tmp/audit_tool_schemas.py`. No defect. |
| Extra changed file `docs/exec-plans/active/2026-05-28-150239-vision-input.md` not in any task's `files:` | — | minor | Different plan, concurrent work — not staged by this review. Flag for staged-file hygiene at ship. |

_No blocking findings — no fixes applied._

### Tests
- Lint: `scripts/quality-gate.sh lint` → PASS (ruff check + format, 326 files).
- Scoped (re-run live this review): `test_flow_prompt_assembly` 5 passed; `-k "web or file"` 65 passed; `test_orchestrator_schema_budget` 1 passed. 0 failed.
- Full suite: started fail-fast (`uv run pytest -x`), reached 57 passed / 0 failed, then **stopped on user instruction ("skip suite")**. Not run to completion in this review. Delivery recorded a prior partial full run to 382 passed / 0 failed (incl. real-LLM compaction + repl).

### Behavioral Verification
- No `co status` command exists in this CLI (commands: chat/tail/trace/dream/google) — skill default N/A.
- Bootstrap + tool-schema generation: verified live — TASK-4 test builds real deps and prepares all 35 tool defs without error.
- `success_signal` (routing): verified via TASK-3 `eval_mindset_selection.py` — web_fetch/file_read/file_search routing correct across 8 cases, no regression; read-before-patch surface unchanged (file_patch out of scope, docstring untouched).

### Overall: PASS
All four tasks meet their done_when with file:line evidence; seam guard holds (no guidance lost — every removed cue has exactly one canonical home); injunctions preserved verbatim; schema-budget guard pinned and non-self-referential; lint clean; scoped tests green. Full suite was not run to completion (skipped per user); ship gate (`/ship`) runs the full suite as the safety net. One staged-file-hygiene note: `vision-input.md` (unrelated plan) is modified in the working tree — confirm it is not staged at ship.
