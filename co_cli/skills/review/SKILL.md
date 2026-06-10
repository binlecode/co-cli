---
description: Structured code review — examine changed files for correctness, style, and security issues, with file:line citations for every finding.
argument-hint: "[branch|PR|path]"
user-invocable: true
---

# Review

**Invocation:** `/review [branch|PR|path]`

Perform a structured review of changed code — correctness, style, and security — with every finding cited at `file:line`. The review stops when all blocking issues are either fixed or explicitly accepted, then issues a verdict.

---

## Phase 1 — Load

1. Identify the change set from the argument:
   - If a branch or PR reference is given, run `git diff <base>...<branch> --name-only` to list changed files.
   - If a path is given, read that file directly.
   - If no argument, run `git diff HEAD --name-only` for the working-tree diff.
2. For each changed file, read the full file content (not just the diff) to understand context.
3. Also read `git diff` output for the changed files to see the exact mutations.
4. Note the language, framework, and any relevant config files (e.g. `pyproject.toml`, `tsconfig.json`) so style rules are inferred from the project, not assumed.

## Phase 2 — Evidence

For each changed file, scan for issues across three dimensions:

**Correctness**
- Logic errors: off-by-one, wrong condition, missing branch, incorrect operator.
- Type mismatches or unsafe casts.
- Unhandled error paths: exceptions swallowed, early returns missing, resources not closed.
- Broken imports or references to deleted identifiers.
- Test coverage gaps: changed behavior with no corresponding test change.

**Style**
- Naming inconsistency with the surrounding codebase.
- Dead code introduced (unused variables, unreachable branches).
- Overly complex expressions that could be simplified without behaviour change.
- Missing or incorrect type hints on public functions (Python projects enforce this per CLAUDE.md).
- Comment on the wrong line (trailing instead of above, per project rules).

**Security**
- User input reaching shell commands, SQL, or file paths without sanitisation.
- Secrets or credentials hardcoded or logged.
- Overly permissive file modes or network bindings.
- Dependency version pinned to a known-vulnerable range.

For every issue found, record it as:
```
[BLOCKER|WARN|INFO] file:line — <one-line description>
```
Use BLOCKER for correctness and security issues that must be fixed before ship. WARN for style issues that should be fixed. INFO for observations with no required action.

## Phase 3 — Fix loop

For each BLOCKER finding (in order):
1. Read the surrounding context again if needed.
2. Apply the minimal fix that resolves the issue — do not refactor unrelated code.
3. Verify the fix does not introduce a new issue.
4. Mark the finding resolved and move to the next BLOCKER.

Apply WARN fixes after all BLOCKERs are resolved, unless a WARN is trivial to fix alongside a BLOCKER.

Do not apply INFO findings — surface them in the verdict only.

## Phase 4 — Verdict

Produce a structured verdict:

```
## Review Verdict

**Status:** PASS | PASS WITH NOTES | FAIL

**Fixed:** <N> issues corrected in this session
**Remaining BLOCKERs:** <N> (list if any)
**Warnings:** <N> (list file:line for each)
**Notes:** <INFO findings, if any>
```

- PASS: zero remaining BLOCKERs, all WARNs resolved or explicitly accepted.
- PASS WITH NOTES: zero BLOCKERs, open WARNs or INFO items the author should be aware of.
- FAIL: one or more BLOCKERs remain that were not auto-fixed (e.g. require human decision).

Always cite at least one specific `file:line` in the verdict summary so the author can navigate directly to relevant findings.

## Rules

- Never suppress a finding because it is pre-existing — pre-existing issues found during review must be reported.
- Every finding must have a `file:line` citation; vague findings ("this could be better") are not allowed.
- Do not expand scope: only files in the change set are in-scope for BLOCKERs.
- Doctor recommends; author decides on WARN/INFO items.
