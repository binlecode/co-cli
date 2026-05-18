# Rubric: user_model

## Scenario summary

The agent has access to a pre-seeded user model: preference artifacts in
knowledge (`pref_terse`, `pref_python`, `pref_pst`) and prior session
transcripts where these preferences came up naturally. After a session
rotation (`/new`), the agent should adapt to these preferences without being
re-told. This rubric grades whether the agent honors the durable user model
across the rotation boundary and whether one-shot overrides leak forward.

## Pass criteria

1. **Honors seeded preferences across rotation.** When asked an open-ended
   question post-rotation, the agent's response reflects the seeded
   preferences:
   - Code examples default to Python (per `pref_python`).
   - Response is terse — no preamble, no multi-paragraph fluff (per `pref_terse`).
   - Time references default to PST / America/Los_Angeles (per `pref_pst`).
   At least 2 of the 3 must be honored for PASS; 1 of 3 is SOFT_PASS.

2. **One-shot override doesn't persist.** When the user explicitly asks for a
   non-default treatment ("give me the Go version"), the agent switches for
   that turn only. The next neutral question reverts to the seeded default.
   A persistent switch (subsequent turns stay in Go without re-prompt) fails.

3. **Voice/tone consistency.** Across all turns, the assistant register stays
   consistent — no swing between terse and verbose. The agent doesn't drop
   the seeded preferences as a response to friction (correction, clarifying
   question).

## Tone notes

- The judge does NOT need to verify the seeded preferences are correct — they
  are. The judge is verifying the agent USES them.
- "Verbose" is judged by structure (multi-paragraph, headers, bulleted
  rationale before answer) not raw character count. A 3-line clear answer is
  fine; a 3-line answer with 2 paragraphs of preamble is not.
- If the agent asks which language is preferred when the user has not
  overridden, that's a FAIL for criterion 1 — the seeded `pref_python` should
  obviate the question.

## Calibration

### PASS example

```
[after /new rotation with user_model_baseline fixture loaded]
user: show me how to read a CSV.
assistant: ```python
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

Python by default, terse, one-shot Go override didn't persist.

### FAIL example

```
[after /new rotation]
user: show me how to read a CSV.
assistant: There are several ways to read a CSV in different languages. Which
           language would you like? Common options include Python, JavaScript,
           Go, Java, or Ruby. Each has different tradeoffs...
```

Failure on criterion 1 (didn't honor `pref_python`, didn't honor `pref_terse`).
