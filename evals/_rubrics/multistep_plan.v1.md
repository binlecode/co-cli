# Rubric: multistep_plan

## Scenario summary

The agent must operate as a researcher/planner/executor for non-trivial goals,
not as a one-shot action runner. The transcript covers a multi-step refactor
ask. This rubric grades whether the agent breaks the goal into discrete steps,
pauses for confirmation at intermediate checkpoints, and synthesizes
information from multiple seeded sources without inventing details.

## Pass criteria

1. **Breaks down before executing.** When given a multi-step goal ("refactor
   project Helios from sqlite to duckdb"), the agent's first response is a
   plan with explicit, numbered steps (≥ 3) — NOT immediate tool calls. The
   plan should reference real components/files when possible. An implicit plan
   (steps embedded in prose, not enumerated) is SOFT_PASS.

2. **Pauses at intermediate checkpoints.** After step 1 executes, when the
   user says "go ahead with the rest," the agent EITHER confirms before
   continuing OR executes step 2 and pauses again. Silently executing all
   remaining steps fails this criterion. Pausing only at the end (after all
   steps done) is SOFT_PASS.

3. **Synthesizes from multiple seeded sources.** When asked to "summarize the
   project Helios context and our prior database decision," the response
   references content from BOTH `project_helios_context.md` AND
   `decision_use_sqlite.md` — by distinctive phrases (e.g. "ingests ~10GB/day"
   from the context, "revisit if workload exceeds 50GB" from the decision).
   Missing either source fails; missing both fails harder.

## Tone notes

- Numbered lists are not required if the structure is clear ("first ... then
  ... finally ..." with explicit ordering and at least 3 phases is fine).
- The judge does NOT verify that the proposed plan is technically optimal —
  only that it IS a plan (breaks down → orders → identifies dependencies).
- Synthesis quality (criterion 3) does NOT require the agent to quote source
  text verbatim — paraphrasing that preserves distinctive facts is fine.
  Inventing details not in either source IS a failure.

## Calibration

### PASS example

```
user: Help me refactor project Helios from sqlite to duckdb. Where do we start?
assistant: Four-step plan:
           1. Audit current sqlite query surface — list every call site.
           2. Stand up duckdb in parallel against the same data file.
           3. Migrate read queries first (lower risk), validate parity.
           4. Migrate writes, retire sqlite.
           Want to start with step 1?
user: ok, do the first step.
assistant: [tool: file_search query="sqlite3" path="."]
           Found 12 call sites in 4 files. Want me to summarize each, or
           continue to step 2?
```

Plan up front, checkpoint after step 1.

### FAIL example

```
user: Help me refactor project Helios from sqlite to duckdb. Where do we start?
assistant: [tool: shell_exec args="pip install duckdb"]
           [tool: file_search query="sqlite3"]
           [tool: file_read path="src/db.py"]
           ...10 more tool calls...
```

Failure on criterion 1 (no plan, immediate execution) and criterion 2 (no
checkpoint).
