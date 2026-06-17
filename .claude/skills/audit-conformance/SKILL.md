---
name: audit-conformance
description: Periodic whole-codebase audit against the .agent_docs coding rules. Inventories accreted violations (boundaries, one-sided members, underscore leaks, dead code, DRY, naming) with file:line + rule citation, then emits a scoped rules-conformance-cleanup exec-plan. The Lane-2 counterpart to /review-impl — fixes accretion that diff-scoped review is blind to. Never adds guard tests.
argument-hint: "[package-or-path scope, default whole co_cli/]"
---

# audit-conformance

**Invocation:** `/audit-conformance [scope]` (default scope: all of `co_cli/`)

**Mission:** `/review-impl` catches violations a *change* introduces; it is structurally blind to slow whole-codebase accretion (`.agent_docs/review.md` → *Two review scopes*). This is the other scope: judgment-scan the whole tree against the `.agent_docs` coding rules, inventory every violation with `file:line` + the exact rule cited, and emit a scoped cleanup plan. This is the feedback loop that keeps the codebase conformant — review feeds regulation.

**Why this exists:** co's cleanup/refactor commits are ~42% of all commits and *accelerating*; global invariants (clean boundaries, stable surface, DRY) erode because they are defended only at the diff. Twenty good-local refactors leave a residue surface nobody owns. This skill owns the surface across time.

**Hard doctrine — read before producing output:**
- **Never propose a guard test / fitness function.** `.agent_docs/testing.md` forbids structural tests (they pass against a gutted body); `review.md` *Code Regulation Model* rejects them explicitly. The output is a *cleanup plan that fixes code at the source*, never a test that freezes the violation behind an allowlist. Eliminating a violation class structurally (relocate a shared helper so the back-edge cannot exist) is stronger than detecting it.
- **Ground every finding in source.** Each row cites `file:line` and the exact rule (`review.md:NN` or `code-conventions.md` section). Never assert a violation from a name or call-shape without reading the implementing line. No "looks like" findings.
- **This skill does not edit code.** It produces an inventory + an exec-plan. Fixes run through the normal flow: Gate 1 → `/orchestrate-dev` → `/review-impl` → `/ship`.

**Produces:** `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-rules-conformance-cleanup.md` (the canonical recurring slug) + a terminal inventory summary.

---

## The rule set (what gets scanned)

Every finding maps to one of these. Source of truth: `.agent_docs/review.md` *Clarity by Subtraction* + `.agent_docs/code-conventions.md`.

| # | Rule | Source | Primary detector |
|---|------|--------|------------------|
| R1 | **One-sided member** — field/param/flag with only a write site OR only a read site | review.md:37 | grep both sites per field/flag; one missing = dead |
| R2 | **Redundant same-lifecycle state** — two flags/paths written + cleared together = one concept | review.md:38 | read flag mutation sites; co-set/co-cleared pairs |
| R3 | **Wrapper/bundle bag** — class/dataclass existing only as a return-value bag or eval one-liner | review.md:39 | classes whose only methods are `__init__`/field access, single caller |
| R4 | **Wrong module home** — domain logic in a package that doesn't own the concern | review.md:40 | import-graph back-edges; domain code under generic layers (`context/`, `util/`) |
| R5 | **Underscore visibility leak** — `_module`/`_symbol` imported across a package boundary | review.md:41 | the cross-package edge map filtered to private names — NOT a flat grep (same-package `_x` imports are legal) |
| R6 | **Import-time side effect** — module-scope IO, config read, console/tracer build, singleton coupling | review.md:42 | module top-level non-def statements that call/construct |
| R7 | **Optimistic flag** — `state.x = True` set before the operation it asserts commits | review.md:43 | read order of flag-set vs the awaited/returning op |
| R8 | **Backward-compat residue** — alias, compat shim, `_legacy`/`_compat`/`_old`, migration reader | review.md:44 | grep `_legacy\|_compat\|_old`, alias assignments, dual-format readers |
| R9 | **Naming drift** — wrong class suffix, missing numeric unit suffix, abbreviation, bare `created`/`updated` (should be `_at`) | code-conventions.md | section-by-section scan |
| R10 | **Dead code** — helper/symbol with zero non-test callers; stale import | review.md:13 | grep caller count per private symbol; ruff for imports |
| R11 | **Duplication (DRY)** — near-identical block/logic in ≥2 homes | review.md:38 | clustered logic; same primitive reimplemented |
| R12 | **Swallowed error** — broad `except`/empty handler/log-and-continue on a user-visible path | review.md:16 | grep `except Exception`/bare `except`; read the handler |

`__init__.py` must be docstring-only — a populated one is an R5/R6 finding.

---

## Pass 0 — Scope + import graph

1. Resolve scope from `$ARGUMENTS` (default `co_cli/`). State it in the summary.
2. **Pick up any open plan:** `ls docs/exec-plans/active/*-rules-conformance-cleanup.md`. If one exists, read it — fold new findings in and do not re-list already-tracked violations as new.
3. **Build the import edge map** (no grimp/import-linter in this repo — use AST in `tmp/`). The builder MUST tag each cross-package edge with its **scope** (`MODULE` / `TYPE_CHECKING` / `LOCAL` — walk the AST tracking whether the import node is under an `if TYPE_CHECKING:` block or nested inside a function/method) and a **PRIVATE** flag (imported module path contains `._x` or an imported name starts with `_`):

   ```bash
   mkdir -p tmp
   # tmp/import_edges.py: walk co_cli/, AST-parse, emit:  src_pkg -> dst_pkg \t file:line \t scope \t private \t symbol
   uv run python tmp/import_edges.py > tmp/edges.txt
   ```

   Dry-run proved why the tags are mandatory: an untagged builder reports `display→main` and `display→tools` as back-edges when both are TYPE_CHECKING-only (forward-ref annotations), inflating R4. Filter:
   - **R4** considers **MODULE-scope edges only**. TYPE_CHECKING and LOCAL edges are reported in a separate low-priority bucket (a type-only or lazy edge is a weak coupling, not a runtime inversion).
   - **R5** = any cross-package edge (MODULE or TYPE_CHECKING) with PRIVATE set — derive it from this map, never from a flat grep.

   Layer order from `docs/specs/01-system.md`: `index` (infra) < `memory`/`session` (domain) < `tools` < `agent`/loop < `commands`/CLI; `config`/`observability` are foundational cross-cutting (imported by any layer — not an inversion). A lower-layer module importing a higher one (other than through `deps`/`main` composition roots) is an R4 back-edge candidate.

4. **Cheap grep/AST sweeps** seed candidate lists (each confirmed by reading source in Pass 1, never flagged from the sweep alone):

   ```bash
   rg -n "_legacy|_compat|\b_old\b|\bdeprecated\b" co_cli/           # R8 — \b_old\b excludes locals like t_old
   rg -n "except Exception|except:" co_cli/                          # R12 candidates
   rg -n "\bcreated\b *[:=]|\bupdated\b *[:=]" co_cli/               # R9 timestamp suffix
   # populated __init__ (R5/R6): AST, not grep — a multi-line docstring is not code.
   # flag a __init__.py only if ast.get_docstring()-stripped body has any non-docstring statement.
   uv run python tmp/check_inits.py                                  # emits only truly-populated __init__.py
   ```

   R8's `\b_old\b` matters: a bare `_old\b` flags the local `t_old` in `tools/files/write.py`. The `__init__` check must use `ast` — a line-counter counts docstring body lines as code and false-flags docstring-only packages (proven on `index`/`session`/`personality.prompts`).

---

## Pass 1 — Fan-out rule-class audit (read-only subagents)

For a whole-`co_cli/` run, fan out to **read-only** audit subagents — declared tools `Read, Grep, Bash` (no Edit/Write). Group by **rule-class cluster**, not by file, so each agent holds one rule's full mental model across the tree:

- **Agent A — boundaries & visibility:** R4 (consume `tmp/edges.txt`), R5, populated `__init__.py`.
- **Agent B — subtraction:** R1, R2, R3, R10 (one-sided members, redundant state, wrapper bags, dead code).
- **Agent C — lifecycle & errors:** R6, R7, R12 (import-time side effects, optimistic flags, swallowed errors).
- **Agent D — naming & compat:** R8, R9, R11 (compat residue, naming drift, duplication).

Each agent returns **only** a findings table — no narrative, no fixes:

| rule | file:line | what (the offending symbol/edge) | evidence | proposed source fix |
|------|-----------|----------------------------------|----------|---------------------|

**Evidence is mandatory and must be a read, not a grep hit.** For R1: cite the write site AND the absent read site (grep returned 0). For R4: cite the importing line + the layer inversion. For R5: cite the cross-package `from ..._x import` + the defining module. A row without a read-confirmed `file:line` is dropped.

Subagents systematically **over-flag** R1/R3/R10 (a "one-sided" field is often read via `getattr`/serialization; a "single-caller" wrapper may be a deliberate seam) and **under-flag** R2/R11 (judgment-heavy). Treat every DELETE/REMOVE proposal as a candidate; the orchestrator re-confirms against source before it enters the plan.

---

## Pass 2 — Dedup, prioritize, write the plan

The orchestrator (not a subagent) does this — keeps source-verification and cross-cutting judgment in one place.

1. **Merge + dedup** all four tables. One physical violation = one row even if it trips two rules; note both.
2. **Re-verify each row against source.** Drop any the orchestrator cannot confirm by reading the cited line. Per `feedback_ground_mechanism_claims_in_source`, no membership/visibility/"dead" claim ships unread.
3. **Prioritize — do not dump the whole backlog into one plan.** A whole-codebase audit on an eroded tree finds many violations; an unscoped 200-item plan never ships. Rank by:
   - **Recurrence class first.** A violation *class* that recurs (R5 underscore leaks ×N, R4 back-edges ×N, stale imports ×N) is the highest-value target — fixing the class structurally (relocate the shared helper) prevents the whole family. Cross-check `git log` for the same fix-commit class repeating.
   - **Then severity:** boundary/visibility inversions (R4/R5) and swallowed user-visible errors (R12) over cosmetic naming (R9).
   - **Cap the plan** at one coherent refactoring theme (e.g. "collapse the `index`→`agent` back-edges + the helpers that force them"). Defer the rest to a `## Deferred backlog` section in the plan, with counts.
4. **Write `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-rules-conformance-cleanup.md`** using the standard exec-plan sections (`.agent_docs/spec-conventions.md`). Each task:
   - names the rule + `file:line`,
   - states the **structural** fix (relocate / collapse / delete / rename), not a detection,
   - `done_when` = a behavioral/observable criterion (suite green + the back-edge no longer exists in the import map), **never** "a guard test passes".
   - Carries the `no behavior change` expectation where it applies (most subtraction refactors are behavior-preserving + green suite + synced specs).
5. **If a deletion would orphan a production helper**, chain it into the same task — do not leave it for lint to discover.

---

## Verdict + terminal summary

This skill has no PASS/FAIL — its output is an inventory + a plan that enters **Gate 1** for human approval before any code changes.

```
## audit-conformance — <date>

Scope: <path>          Import edges scanned: N
Violations found: N total (read-confirmed; M grep candidates dropped on read)
  R1 one-sided member:        N
  R2 redundant state:         N
  R3 wrapper bag:             N
  R4 wrong module home:       N   ← back-edges: [list]
  R5 underscore leak:         N   ← [list pkg._x -> importer]
  R6 import-time side effect: N
  R7 optimistic flag:         N
  R8 backward-compat residue: N
  R9 naming drift:            N
  R10 dead code:              N
  R11 duplication:            N
  R12 swallowed error:        N

Recurring classes (git-log corroborated): [class — count, count]
Plan scope this round: <one coherent theme> — K tasks
Deferred backlog: N violations — [classes + counts]
Plan: docs/exec-plans/active/<ts>-rules-conformance-cleanup.md

Next: 👤 Gate 1 (PO + TL approve scope) → /orchestrate-dev rules-conformance-cleanup
```

**Cadence:** run periodically (not per-PR) — the residue accumulates between runs by design. A good trigger is when `git log` shows the same cleanup-commit class repeating, or on a fixed interval. The plan slug is intentionally reused (`rules-conformance-cleanup`); archive the prior one to `completed/` on ship before opening the next.
