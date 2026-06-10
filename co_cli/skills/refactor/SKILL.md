---
description: Guided module refactor — scope the change, verify test coverage, apply the refactor incrementally, and confirm the suite stays green.
argument-hint: "[module or file path]"
user-invocable: true
---

# Refactor

**Invocation:** `/refactor [module or file path]`

Scope a refactor, establish a safety net of tests, apply the change incrementally, and verify the suite stays green at each step. No public API is changed without an explicit decision.

---

## Phase 1 — Scope

Define what changes and what must not change.

1. Read the target module or file. If no path is given, ask for it.
2. State the refactor goal in one sentence (e.g. "extract `_parse_frontmatter` into its own module", "rename `FooHelper` to `FooService`", "collapse three duplicated list-comprehensions into a shared function").
3. List the identifiers (functions, classes, constants) that will move, rename, or change signature.
4. List the callers of each affected identifier by searching the codebase. These are the blast radius.
5. Declare the invariant: what must be identical before and after?
   - Public API surface (function signatures visible to callers outside the package).
   - Observable behaviour (same inputs produce same outputs).
   - File paths and config keys that other components reference.
6. Flag any ambiguity or risk before proceeding. If the blast radius is larger than expected, report it and ask for confirmation.

## Phase 2 — Safety

Verify test coverage before touching any code.

1. Run the existing test suite scoped to the affected module: `uv run pytest tests/ -x -q -k <module_name>`.
2. Check coverage: are the identifiers being changed exercised by at least one test?
3. For any affected identifier with no test coverage, add a minimal behavioural test first — before the refactor. The test must assert the current behaviour, not the desired post-refactor behaviour.
4. Run the targeted test suite again to confirm all new tests pass on the current code.
5. Record the baseline: "N tests pass, 0 failures" — this is the green baseline the refactor must preserve.

Do not begin Phase 3 if the baseline is not green.

## Phase 3 — Refactor

Apply changes incrementally, one logical step at a time.

Ordering:
- Add new destination (new module, renamed identifier) first.
- Update all callers to use the new destination.
- Delete the old source last (only after all callers are updated).

For each step:
1. Make the minimal edit for that step.
2. Verify imports are consistent — no wildcard imports, no circular imports.
3. Run the linter: `scripts/quality-gate.sh lint --fix`.
4. If a step introduces a lint error that cannot be auto-fixed, resolve it before continuing.

Keep each step small enough that a single `git diff` is readable in one screen. If a step grows large, split it.

Do not combine the refactor with unrelated improvements (dead code removal, style fixes in untouched lines, logic changes). Those belong in a separate commit.

## Phase 4 — Verify

Confirm the suite stays green and the invariant holds.

1. Run the full test suite: `uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-refactor.log`.
2. If any test fails, classify the failure:
   - **Legitimate regression**: the refactor broke behaviour — fix it before proceeding.
   - **Test coupled to implementation detail**: the test asserts an internal that changed. Evaluate whether the internal coupling was intentional. If not, update the test to assert behaviour, not structure.
3. Confirm the public API is unchanged: grep for every affected identifier in the caller list from Phase 1 and verify each call site compiles and is logically equivalent.
4. Produce a summary:

```
## Refactor Summary

**Goal:** <one sentence from Phase 1>
**Files changed:** <list>
**Identifiers moved/renamed:** <list>
**Tests added:** <N>
**Suite result:** <N passed, 0 failed>
**API invariant:** preserved | CHANGED (explain)
```

## Rules

- Never change observable behaviour during a refactor — that is a feature change, not a refactor.
- Safety net (Phase 2) is mandatory — do not skip it even for "obvious" renames.
- Delete old code only after all callers are updated and the suite is green.
- One refactor per session — do not bundle multiple unrelated refactors.
