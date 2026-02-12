Start from the symptom. Reproduce it before theorizing.
Check the obvious first: typos, wrong variable, stale state, missing import.
Form one hypothesis at a time. Use a tool to test it. If disproven, say so and move on.
Read the actual error message — parse the traceback, line number, and exception type before guessing.
When multiple causes are plausible, rank by likelihood and test the most likely first.
After fixing, verify the original symptom is gone and no new failures were introduced.
If stuck after 3 hypotheses, step back: re-read the code path end-to-end, or ask the user for more context.
