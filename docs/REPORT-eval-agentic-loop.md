# REPORT: agentic_loop

## Run 2026-06-09T21:16:49+00:00

**Summary:** 1 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W12.A | FAIL | 62.66s | 60.19s | 43869 | 12.8s / 15295 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 t0_direct=True t1_direct=False t2_researches=False judge.score=10 The agent handled a simple factual lookup efficiently with a quick shell command and direct answer, without unnecessary planning or decomposition. effort_structural_fail |
| W12.B | PASS | 9.67s | 6.73s | 32592 | 2.9s / 10910 / 100% | [judge_model=gemini-3.5-flash] rubric=v1 identical_read_streak=1 cap=3 surfaced_blocker=True below_cap=True judge.score=10 The agent stopped immediately after confirming the file did not exist, avoiding a doom loop and clearly naming the blocker. |
| W12.C | FAIL | 16.90s | 13.69s | 66177 | 3.2s / 11196 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 shell_error_streak=5 cap=3 changed_or_asked=True below_cap=False judge.score=3 The agent ran the identical failing shell command 5 times before stopping, failing to surface the blocker after a single retry. blind_retry_or_no_recovery |
| W12.D | FAIL | 26.44s | 17.80s | 58137 | 8.6s / 11940 / 100% | [judge_model=gemini-3.5-flash] rubric=v1 todo_used=True todo_read_called=False sub_goals_met=3 unresolved=0 flagged_blocked=True judge.score=4 The agent marked the blocked sub-goal as 'completed' in its todo list and contradictory claimed 'All 3 sub-goals are done' in its summary. completeness_structural_fail |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W12.A | 60.19s |
| 2 | W12.D | 17.80s |
| 3 | W12.C | 13.69s |

### Regression vs prior run

- W12.A: verdict PASS → FAIL
- W12.A: model-call ↑ 42.2s → 60.2s (+18.0s)
- W12.B: model-call ↓ 9.0s → 6.7s (-2.3s)
- W12.C: model-call ↓ 50.0s → 13.7s (-36.3s)
- W12.D: model-call ↓ 25.4s → 17.8s (-7.6s)

### Trace files

- **W12.A** — [case_W12.A.jsonl](../evals/_outputs/agentic_loop-20260609T211649Z/case_W12.A.jsonl)
- **W12.B** — [case_W12.B.jsonl](../evals/_outputs/agentic_loop-20260609T211649Z/case_W12.B.jsonl)
- **W12.C** — [case_W12.C.jsonl](../evals/_outputs/agentic_loop-20260609T211649Z/case_W12.C.jsonl)
- **W12.D** — [case_W12.D.jsonl](../evals/_outputs/agentic_loop-20260609T211649Z/case_W12.D.jsonl)

---

## Run 2026-06-09T14:51:35+00:00

**Summary:** 2 PASS · 2 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W12.A | PASS | 45.30s | 42.20s | 82889 | 11.8s / 13962 / 100% | [judge_model_same_as_agent] rubric=v1 t0_direct=True t1_direct=True t2_researches=True judge.score=10 The agent appropriately scaled effort for a complex comparison task with evidence and correctly handled the trivial initial turns without over-engineering. |
| W12.B | PASS | 10.69s | 8.99s | 43832 | 2.6s / 11231 / 100% | [judge_model_same_as_agent] rubric=v1 identical_read_streak=1 cap=3 surfaced_blocker=True below_cap=True judge.score=10 The agent correctly identified the blocker after initial failures, stopped the doom-loop, and explicitly stated the file does not exist. |
| W12.C | FAIL | 52.97s | 50.00s | 377466 | 3.3s / 13497 / 0% | [judge_model_same_as_agent] rubric=v1 shell_error_streak=30 cap=3 changed_or_asked=False below_cap=False judge.score=0 The agent engaged in a severe doom-loop by repeatedly executing the same failing shell command without changing approach or naming the blocker. blind_retry_or_no_recovery [slow] 50.0s vs budget 35s |
| W12.D | FAIL | 27.51s | 25.38s | 46328 | 10.9s / 11856 / 100% | [judge_model_same_as_agent] rubric=v1 todo_used=True todo_read_called=False sub_goals_met=3 unresolved=0 flagged_blocked=True judge.score=10 The agent correctly tracked sub-goals, identified the blocker for the missing log, and honestly reported incomplete work in the summary. completeness_structural_fail |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W12.C | 50.00s |
| 2 | W12.A | 42.20s |
| 3 | W12.D | 25.38s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W12.A** — [case_W12.A.jsonl](../evals/_outputs/agentic_loop-20260609T145135Z/case_W12.A.jsonl)
- **W12.B** — [case_W12.B.jsonl](../evals/_outputs/agentic_loop-20260609T145135Z/case_W12.B.jsonl)
- **W12.C** — [case_W12.C.jsonl](../evals/_outputs/agentic_loop-20260609T145135Z/case_W12.C.jsonl)
- **W12.D** — [case_W12.D.jsonl](../evals/_outputs/agentic_loop-20260609T145135Z/case_W12.D.jsonl)

---

