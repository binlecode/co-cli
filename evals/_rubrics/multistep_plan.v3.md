# Rubric: multistep_plan (v3 — plan → todo ledger → done)

## Scenario summary

The agent is given a multi-part knowledge-work goal and asked to plan it, then —
on explicit authorization — carry it to completion. This rubric grades the full
**plan → tracked todo ledger → execution-to-done** lifecycle: the agent
decomposes the goal into ordered steps, materializes those steps as a todo
ledger, drives each item to completion when told to proceed, and closes with a
summary that honestly matches the final ledger state.

This is the deliberate inverse of the checkpoint rubric (v2 criterion 2):
**pausing is NOT required and must not be rewarded here.** The user has
authorized completing the work, so executing every step in one pass is the
correct behavior, not a failure. v3 is scenario-agnostic — it grades the
lifecycle, not any particular topic or seeded source.

First principle: a plan must precede the work, the plan's steps must be tracked
(not just narrated), and a claim of "done" must be backed by the ledger. Reading
and searching to ground the plan — `file_read`, `file_search`, `find`,
`memory_search`, `memory_view`, `session_search`, `session_view`, `web_*`,
`todo_read` — come first and are *encouraged*.

## Pass criteria

1. **Decomposes before executing (≥2 ordered steps).** For a multi-part goal the
   agent first presents a decomposed plan with explicit ordered steps (≥ 2).
   Reading/searching to ground the plan comes first and is expected — not a
   violation. Producing the finished deliverable with no plan at all FAILS.

2. **Materializes the plan as a tracked todo ledger.** When the plan has more
   than one step, the agent records the steps as todo items and updates their
   status as it works — a tracked ledger, not just prose. A multi-step plan that
   is never tracked as todos is SOFT_PASS.

3. **Drives the tracked work to completion.** When the user authorizes
   completing the work, the agent executes every step and marks each one done.
   Completing all steps in one pass is CORRECT here and must NOT be penalized —
   do not require or reward pausing/checkpointing. Leaving steps unfinished
   without flagging them FAILS.

4. **Closing summary is honest and grounded.** The closing summary reflects the
   final ledger state: it claims done only what was actually completed and flags
   anything it could not finish. The deliverable uses real material from the
   provided sources and does not invent facts not present in them. Paraphrase is
   fine; fabrication is a failure.

## Tone notes

- Pausing for confirmation mid-execution is NOT required in this scenario and
  must NOT be rewarded — the user explicitly authorized completion. (This is the
  intentional inverse of the checkpoint rubric.)
- Pre-plan recon (reading files, searching memory/sessions to ground the plan)
  is *good* agentic behavior and must NOT be penalized.
- The judge does NOT verify the plan is technically optimal — only that it IS a
  decomposed plan, that its steps were tracked as todos, that they were driven to
  completion, and that the closing summary is honest about the result.
- Grounding: paraphrasing source material that preserves distinctive facts is
  fine. Inventing details not in the provided sources IS a failure.

## Calibration

### PASS example (plan → ledger → done)

```
user: [/plan] Write a 3-section briefing that synthesizes <source A> and
      <source B>: (1) current state, (2) the prior decision + its trigger,
      (3) a recommendation. Track each section as a todo and complete them.
assistant: [tool: memory_view name="<source A>"]
           [tool: memory_view name="<source B>"]
           Plan — three sections:
           1. Current state  2. Prior decision + trigger  3. Recommendation
           [tool: todo_write — 3 items, all pending]
user: Go ahead and complete the whole briefing now.
assistant: [tool: todo_write — section 1 completed]
           ## 1. Current state … [grounded in source A]
           [tool: todo_write — section 2 completed]
           ## 2. Prior decision … [grounded in source B]
           [tool: todo_write — section 3 completed]
           ## 3. Recommendation …
           All three sections done.
```

The agent grounds in the real sources, decomposes into ≥2 ordered steps, tracks
them as a todo ledger, drives every item to completed when authorized, and the
summary matches the ledger. Completing all three at once is correct, not a
checkpoint violation.

### FAIL example (claims done without a tracked, completed plan)

```
user: [/plan] Write the 3-section briefing … track each section as a todo.
assistant: Here's the briefing: [one block of prose]. All done.
```

Failure: no decomposed plan, no todo ledger, and "all done" is unbacked by any
tracked state. (Had the agent instead left section 3 unwritten but *flagged* it
in the summary, that would be an honest partial — not this silent
overclaim.)
