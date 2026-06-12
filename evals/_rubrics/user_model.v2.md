# Rubric: user_model

## Scenario summary

The agent has a pre-seeded user model: preference items in memory (`pref_terse`,
`pref_python`, `pref_pst`) plus prior session transcripts where these surfaced.
co's memory is **recall-on-demand** — items are not auto-injected into the prompt;
the agent reaches them only by calling a recall tool (`memory_search` / `memory_view`,
or `session_search` / `session_view`). After a session rotation (`/new`) the
preferences are no longer in the conversation, so the agent must **consult memory and
then reuse what it finds**. This rubric grades that recall→reuse path: did the agent
retrieve the preferences and apply them, and does a one-shot override revert cleanly.

This is deliberately NOT a test of auto-applied preferences — co has no always-on
preference layer, so "the agent should just know" is out of scope. What is in scope is
whether the durable user model is real and usable *when the agent consults it*.

## Pass criteria

1. **Recalls, then reuses.** When the user asks the agent to draw on what it knows
   about them, the agent consults memory and its answer then reflects what it found:
   - Code examples default to Python (per `pref_python`).
   - Response is terse — no preamble, no multi-paragraph fluff (per `pref_terse`).
   - Time references default to PST / America/Los_Angeles (per `pref_pst`).
   Once the preferences are recalled into context, at least 2 of the 3 must be
   honored for PASS; 1 of 3 is SOFT_PASS. (The structural check separately confirms
   a recall tool was actually called — a confident answer that never consulted
   memory is not a pass for this rubric.)

2. **One-shot override doesn't persist.** When the user explicitly asks for a
   non-default treatment ("give me the Go version"), the agent switches for that turn
   only. The next neutral question reverts to the recalled default. A persistent
   switch (subsequent turns stay in Go without re-prompt) fails.

3. **Voice/tone consistency.** Across all turns the assistant register stays
   consistent — no swing between terse and verbose. The agent doesn't drop the
   recalled preferences in response to friction (correction, clarifying question).

## Tone notes

- The judge does NOT verify the seeded preferences are correct — they are. The judge
  verifies the agent RETRIEVED and USED them.
- "Verbose" is judged by structure (multi-paragraph, headers, bulleted rationale
  before the answer), not raw character count. A 3-line clear answer is fine; a
  3-line answer with 2 paragraphs of preamble is not.
- If the agent recalls the preferences and then still asks which language to use,
  that's a FAIL for criterion 1 — the recalled `pref_python` should obviate the
  question.
- If the agent answers without consulting memory at all, criterion 1 fails on the
  recall step regardless of how the answer happens to look.

## Calibration

### PASS example

```
[after /new rotation with user_model_baseline loaded]
user: check what you've saved about how I like to work, then show me how to read a CSV.
assistant: [calls memory_search "preferences"] → finds pref_python, pref_terse, pref_pst
           ```python
           import csv
           with open("data.csv") as f:
               for row in csv.reader(f):
                   print(row)
           ```
user: actually, give me the Go version.
assistant: ```go
           f, _ := os.Open("data.csv")
           r := csv.NewReader(f)
           records, _ := r.ReadAll()
           ```
user: now show me how to read JSON.
assistant: ```python
           import json
           with open("data.json") as f:
               data = json.load(f)
           ```
```

Recalled the preferences, applied them (Python, terse), one-shot Go override didn't persist.

### FAIL example

```
[after /new rotation]
user: check what you've saved about how I like to work, then show me how to read a CSV.
assistant: There are several ways to read a CSV in different languages. Which language
           would you like? Common options include Python, JavaScript, Go, Java...
```

Failure on criterion 1: never consulted memory, so it didn't recall `pref_python` /
`pref_terse` and fell back to a generic verbose answer.
