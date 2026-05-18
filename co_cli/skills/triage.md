---
description: Bug report triage — reproduce, isolate, and categorize a reported issue, then draft a structured ticket with reproduction steps.
argument-hint: "[bug description or error]"
user-invocable: true
---

# Triage

**Invocation:** `/triage [bug description or error]`

Reproduce, isolate, and categorize a reported issue, then produce a structured ticket ready to hand off to an implementer.

---

## Phase 1 — Reproduce

Attempt to reproduce the issue from the report.

1. Read the bug description or error message provided as the argument. If none, ask for it before proceeding.
2. Identify the minimum reproduction path: the fewest steps that trigger the failure.
3. Run the reproduction steps if possible (execute a command, call a function, trigger a workflow).
4. Record the actual output exactly — include stack traces, error messages, and exit codes verbatim.
5. Record the expected output: what should have happened instead?

If reproduction fails:
- Note the environment delta (OS, Python version, config, missing fixture) that likely explains why.
- Do not stop — proceed with the rest of triage using the report's stated behaviour as ground truth.

## Phase 2 — Isolate

Narrow the failure to a specific root cause.

1. Identify the component or module where the failure originates. Read the relevant source files.
2. Trace the call path from the user-facing entry point to the failure site.
3. State the root cause in one sentence: "X fails because Y when Z."
4. Distinguish the root cause from symptoms: the error message is a symptom; the missing guard or wrong assumption is the root cause.
5. Check for related issues: search the codebase for similar patterns that might be affected by the same root cause.

If the root cause cannot be determined with certainty, state what is known and what additional information would confirm it.

## Phase 3 — Categorize

Assign standard triage metadata.

- **Severity:** Critical (data loss or security) / High (core feature broken, no workaround) / Medium (feature degraded, workaround exists) / Low (cosmetic or edge case)
- **Component:** The package or module where the fix will land (e.g. `co_cli/memory/`, `co_cli/tools/`)
- **Type:** Bug / Regression / Performance / UX / Security
- **Regression:** Yes/No — did this work in a prior version? If yes, identify the commit or version where it broke if possible.
- **Workaround:** Describe any known workaround, or state "None."

## Phase 4 — File

Draft a structured ticket.

```
## Bug Report

**Title:** <verb phrase describing the failure, ≤ 72 chars>

**Severity:** <from Phase 3>
**Component:** <from Phase 3>
**Type:** <from Phase 3>

**Description:**
<One paragraph: what fails, under what conditions, what the impact is.>

**Reproduction steps:**
1. <step>
2. <step>
...

**Expected:** <what should happen>
**Actual:** <what happens — include error message or stack trace>

**Root cause (if known):** <one sentence from Phase 2>

**Suggested fix:** <optional — only if the fix is clear from isolation>

**Workaround:** <from Phase 3>
```

Present the ticket to the user and ask if it should be filed (e.g. via `memory_manage` or an issue tracker tool). Do not file without confirmation.

## Rules

- Never modify source files during triage — triage is read-only.
- If reproduction is impossible, say so explicitly rather than assuming the report is wrong.
- Root cause and symptom must be distinguished — conflating them produces unfixable tickets.
