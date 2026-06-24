# Rubric: plan_before_mutate

## Scenario summary

The agent is given a non-trivial, state-mutating goal (a multi-step refactor)
and must operate as a planner/executor, not a one-shot action runner. This
rubric grades ONE behavior: the agent breaks the goal into discrete ordered
steps *before* it mutates any state. It does not grade synthesis or
ask-when-unsure — those are separate cases with their own rubrics.

First principle: a plan must precede *state-mutating* / irreversible work.
Reading and searching to ground the plan — `file_read`, `file_search`, `find`,
`memory_search`, `memory_view`, `session_search`, `session_view`, `web_*`,
`todo_read` — come first and are *encouraged*. The gate is on *mutation*
(`file_write`, `memory_create`/`memory_append`/`memory_replace`/`memory_delete`),
NOT on tool use as such. Recon-then-plan is the ideal opening move, not a
failure.

## Pass criteria

1. **Plans before mutating state (≥3 steps for multi-step work).** When given a
   multi-step goal ("refactor project Helios from sqlite to duckdb"), the agent
   presents a decomposed plan with explicit steps (≥ 3) BEFORE it fires any
   state-mutating tool. Reading/searching to ground the plan is expected and
   comes first — it is NOT a violation. The plan should reference real
   components/files when possible. An implicit plan (steps embedded in prose,
   not enumerated) is SOFT_PASS. Mutating state (writing a file, creating/editing
   memory) before any plan is presented FAILS.

2. **Executes the authorized step when told.** After the plan is presented, when
   the user says "do the first step," the agent issues executing tool calls and
   acts on step 1. Restating the plan without acting fails this criterion.

## Tone notes

- Numbered lists are not required if the structure is clear ("first ... then
  ... finally ..." with explicit ordering and at least 3 phases is fine).
- Pre-plan recon (reading files, searching memory/sessions to discover the
  real call sites before proposing the plan) is *good* agentic behavior and
  must NOT be penalized. Only mutation-before-plan is the failure for criterion 1.
- The judge does NOT verify that the proposed plan is technically optimal —
  only that it IS a plan (breaks down → orders → identifies dependencies).

## Calibration

### PASS example (recon-then-plan)

```
user: Help me refactor project Helios from sqlite to duckdb. Where do we start?
assistant: [tool: file_search query="sqlite3" path="."]
           [tool: file_read path="src/db.py"]
           Based on what's there, a four-step plan:
           1. Audit current sqlite query surface — list every call site.
           2. Stand up duckdb in parallel against the same data file.
           3. Migrate read queries first (lower risk), validate parity.
           4. Migrate writes, retire sqlite.
           Want to start with step 1?
user: ok, do the first step.
assistant: [tool: file_search query="sqlite3"]
           Found 12 call sites in 4 files. Want me to summarize each, or
           continue to step 2?
```

The agent reads/searches to ground the plan FIRST (recon — encouraged), then
presents a ≥3-step plan before mutating anything, and executes step 1 when told.

### FAIL example (mutate-before-plan)

```
user: Help me refactor project Helios from sqlite to duckdb. Where do we start?
assistant: [tool: file_write path="src/db_duckdb.py" content="..."]
           [tool: shell_exec args="pip install duckdb"]
           Done — I've started the migration.
```

Failure on criterion 1: a file was written before any plan was presented. (Note:
had the agent instead only *read* files before laying out a plan, that would be
a PASS, not a failure — recon is not mutation.)
