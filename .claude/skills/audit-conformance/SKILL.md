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
| R3 | **Wrapper/bundle bag or premature abstraction** — class/dataclass existing only as a return-value bag or eval one-liner; *or* an interface (ABC/Protocol/base class) / factory whose only concrete implementation is a single type | review.md:39 | classes whose only methods are `__init__`/field access, single caller; ABC/Protocol with one subclass, factory producing one product type |
| R4 | **Wrong module home** — domain logic in a package that doesn't own the concern | review.md:40 | import-graph back-edges; domain code under generic layers (`context/`, `util/`) |
| R5 | **Underscore visibility leak** — `_module`/`_symbol` imported across a package boundary | review.md:41 | the cross-package edge map filtered to private names — NOT a flat grep (same-package `_x` imports are legal) |
| R6 | **Import-time side effect** — module-scope IO, config read, console/tracer build, singleton coupling | review.md:42 | module top-level non-def statements that call/construct |
| R7 | **Optimistic flag** — `state.x = True` set before the operation it asserts commits | review.md:43 | read order of flag-set vs the awaited/returning op |
| R8 | **Backward-compat residue** — alias, compat shim, `_legacy`/`_compat`/`_old`, migration reader | review.md:44 | grep `_legacy\|_compat\|_old`, alias assignments, dual-format readers |
| R9 | **Naming drift** — wrong class suffix, missing numeric unit suffix, abbreviation, bare `created`/`updated` (should be `_at`) | code-conventions.md | section-by-section scan |
| R10 | **Dead code** — helper/symbol with zero non-test callers; stale import; declared dependency with zero import sites | review.md:13 | grep caller count per private symbol; ruff for imports; `pyproject.toml` deps diffed against the import map |
| R11 | **Duplication (DRY)** — near-identical block/logic in ≥2 homes | review.md:38 | clustered logic; same primitive reimplemented |
| R12 | **Swallowed error** — broad `except`/empty handler/log-and-continue on a user-visible path | review.md:16 | grep `except Exception`/bare `except`; read the handler |

`__init__.py` must be docstring-only — a populated one is an R5/R6 finding.

---

## Pass 0 — Scope + import graph

1. Resolve scope from `$ARGUMENTS` (default `co_cli/`). State it in the summary.
2. **Pick up any open plan:** `ls docs/exec-plans/active/*-rules-conformance-cleanup.md`. If one exists, read it — fold new findings in and do not re-list already-tracked violations as new.
3. **Build the import edge map** (no grimp/import-linter in this repo — use AST in `tmp/`). The builder MUST tag each cross-package edge with its **scope** (`MODULE` / `TYPE_CHECKING` / `LOCAL` — walk the AST tracking whether the import node is under an `if TYPE_CHECKING:` block or nested inside a function/method) and a **PRIVATE** flag (imported module path contains `._x` or an imported name starts with `_`).

   Paste the script below verbatim into `tmp/import_edges.py` (manual audit aid — never a CI gate or `tests/` member) and run it:

   ```python
   # tmp/import_edges.py — manual audit aid; NOT a CI gate or tests/ member
   import ast, os, sys
   from pathlib import Path

   ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("co_cli")

   def pkg_of(path: Path) -> str:
       parts = path.with_suffix("").parts
       return ".".join(parts)

   def is_private(name: str) -> bool:
       return any(part.startswith("_") and part not in ("__init__", "__main__")
                  for part in name.split("."))

   def walk_imports(tree: ast.Module):
       """Yield (scope, node) for each import node in the AST."""
       def _walk(nodes, scope):
           for node in nodes:
               if isinstance(node, ast.If):
                   # detect `if TYPE_CHECKING:`
                   test = node.test
                   if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                       isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                   ):
                       yield from _walk(node.body, "TYPE_CHECKING")
                       continue
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                   yield from _walk(node.body, "LOCAL")
                   continue
               if isinstance(node, (ast.Import, ast.ImportFrom)):
                   yield scope, node
               if hasattr(node, "body"):
                   yield from _walk(getattr(node, "body", []), scope)
               if hasattr(node, "orelse"):
                   yield from _walk(getattr(node, "orelse", []), scope)
       yield from _walk(tree.body, "MODULE")

   for py in sorted(ROOT.rglob("*.py")):
       try:
           tree = ast.parse(py.read_text(encoding="utf-8"))
       except SyntaxError:
           continue
       src_pkg = ".".join(py.relative_to(ROOT.parent).with_suffix("").parts)
       src_top = py.relative_to(ROOT.parent).parts[1] if len(py.relative_to(ROOT.parent).parts) > 1 else ""
       for scope, node in walk_imports(tree):
           if isinstance(node, ast.ImportFrom) and node.module:
               mod = node.module
               # resolve relative imports
               if node.level:
                   anchor_parts = list(py.relative_to(ROOT.parent).with_suffix("").parts)
                   up = node.level
                   base = anchor_parts[:-up] if up <= len(anchor_parts) else []
                   mod = ".".join(base + ([mod] if mod else []))
               if not mod.startswith("co_cli"):
                   continue
               dst_top = mod.split(".")[1] if mod.count(".") >= 1 else ""
               names = [a.name for a in node.names]
               private = is_private(mod) or any(n.startswith("_") for n in names)
               for name in names:
                   print(f"{src_pkg}\t{mod}.{name}\t{py}:{node.lineno}\t{scope}\t{'PRIVATE' if private or name.startswith('_') else 'PUBLIC'}\t{name}")
           elif isinstance(node, ast.Import):
               for alias in node.names:
                   if not alias.name.startswith("co_cli"):
                       continue
                   private = is_private(alias.name)
                   print(f"{src_pkg}\t{alias.name}\t{py}:{node.lineno}\t{scope}\t{'PRIVATE' if private else 'PUBLIC'}\t{alias.name}")
   ```

   ```bash
   mkdir -p tmp
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
   # R10 unused deps: collect third-party top-level imports across co_cli/, diff against
   # [project.dependencies] in pyproject.toml. A dep with no matching import is a CANDIDATE
   # to confirm by reading, never an auto-flag — dist name ≠ import name for many packages.
   uv run python tmp/unused_deps.py                                 # emits declared deps with no import site
   ```

   Proof anchors for the three non-obvious filters above: the unbounded `_old` flags the local `t_old` in `tools/files/write.py`; a line-counter `__init__` check false-flags the docstring-only `index`/`session`/`personality.prompts` packages; a bare dist-name diff false-flags packages whose import name differs from the distribution name (`pydantic-ai` → `pydantic_ai`, `python-dotenv` → `dotenv`), so each unmatched dep is resolved to its real import name before it counts.

---

## Pass 1 — Fan-out rule-class audit (read-only subagents)

For a whole-`co_cli/` run, fan out to **read-only** audit subagents — declared tools `Read, Grep, Bash` (no Edit/Write). Group by **rule-class cluster**, not by file, so each agent holds one rule's full mental model across the tree:

- **Agent A — boundaries & visibility:** R4 (consume `tmp/edges.txt`), R5, populated `__init__.py`.
- **Agent B — subtraction:** R1, R2, R3, R10 (one-sided members, redundant state, wrapper bags + one-implementation abstractions, dead code + unused deps).
- **Agent C — lifecycle & errors:** R6, R7, R12 (import-time side effects, optimistic flags, swallowed errors).
- **Agent D — naming & compat:** R8, R9, R11 (compat residue, naming drift, duplication).

Each agent returns **only** a findings table — no narrative, no fixes:

| rule | file:line | what (the offending symbol/edge) | evidence | proposed source fix |
|------|-----------|----------------------------------|----------|---------------------|

**Evidence is mandatory and must be a read, not a grep hit.** For R1: cite the write site AND the absent read site (grep returned 0). For R4: cite the importing line + the layer inversion. For R5: cite the cross-package `from ..._x import` + the defining module. A row without a read-confirmed `file:line` is dropped.

Subagents systematically **over-flag** R1/R3/R10 (a "one-sided" field is often read via `getattr`/serialization; a "single-caller" wrapper may be a deliberate seam; a one-implementation Protocol/ABC may be a published extension contract or a test seam, not a dead abstraction) and **under-flag** R2/R11 (judgment-heavy). Treat every DELETE/REMOVE proposal as a candidate; the orchestrator re-confirms against source before it enters the plan.

---

## Pass 2 — Dedup, prioritize, write the plan

The orchestrator (not a subagent) does this — keeps source-verification and cross-cutting judgment in one place.

1. **Merge + dedup** all four tables. One physical violation = one row even if it trips two rules; note both. **Dead (R10) dominates a lifecycle finding:** before proposing a call-time-defer fix for an R6/R7 row, grep the symbol's reader count — zero readers reclassifies it to R10 and *deletion supersedes the lifecycle remedy* (proven: `_VERSION` flagged R6 import-time side effect was fully dead — deletion was the stronger fix than deferring to call time).
2. **Re-verify each row against source.** Drop any the orchestrator cannot confirm by reading the cited line. Per `feedback_ground_mechanism_claims_in_source`, no membership/visibility/"dead" claim ships unread.
   - **Every DELETE/REMOVE proposal (R1/R3/R10) goes through a blind cold-read subagent**, not just orchestrator self-check. The orchestrator aggregated the findings and carries each one's framing — it confirms what it already believes. Spawn one `Read, Grep` subagent, hand it the merged removal candidates **with verdicts stripped** (claim + `file:line` only), instruct it to default-to-refute and find any reader/caller/test/serialization/re-export that disqualifies "dead." Only survivors enter the plan. (Empirically this flips a large fraction: one round refuted 4 of 9 — a write-only field a test reads, a "dead" function that was a live public re-export, two fields whose source is read but propagated copy is not.)
3. **Prioritize — do not dump the whole backlog into one plan.** A whole-codebase audit on an eroded tree finds many violations; an unscoped 200-item plan never ships. Rank by:
   - **Recurrence class first.** A violation *class* that recurs (R5 underscore leaks ×N, R4 back-edges ×N, stale imports ×N) is the highest-value target — fixing the class structurally (relocate the shared helper) prevents the whole family. Cross-check `git log` for the same fix-commit class repeating, **and `rg` completed-plan verdicts for a repeated manual workaround** (`rg -n "is not a command|no such command|N/A —|skill.*template|had to substitute" docs/exec-plans/completed/`). A workaround repeated across ≥3 delivery verdicts is a rule/template defect forcing accretion — invisible to a `co_cli/` scan and to `git diff`, and a top-priority finding (it graduates an enforcement tier per `feedback_rule_enforcement_tiers`). Proven: a phantom `co status` in a skill template generated ~50 repeated disclaimers before anyone fixed the template.
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
  R3 wrapper / 1-impl abstraction: N
  R4 wrong module home:       N   ← back-edges: [list]
  R5 underscore leak:         N   ← [list pkg._x -> importer]
  R6 import-time side effect: N
  R7 optimistic flag:         N
  R8 backward-compat residue: N
  R9 naming drift:            N
  R10 dead code / unused dep:  N
  R11 duplication:            N
  R12 swallowed error:        N

Recurring classes (git-log corroborated): [class — count, count]
Plan scope this round: <one coherent theme> — K tasks
Deferred backlog: N violations — [classes + counts]
Plan: docs/exec-plans/active/<ts>-rules-conformance-cleanup.md

Next: 👤 Gate 1 (PO + TL approve scope) → /orchestrate-dev rules-conformance-cleanup
```

**Cadence:** run periodically (not per-PR) — the residue accumulates between runs by design. A good trigger is when `git log` shows the same cleanup-commit class repeating, or on a fixed interval. The plan slug is intentionally reused (`rules-conformance-cleanup`); archive the prior one to `completed/` on ship before opening the next.
