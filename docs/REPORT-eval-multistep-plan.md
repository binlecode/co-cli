# REPORT: multistep_plan

## Run 2026-06-10T12:30:28+00:00

**Summary:** 0 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W11.A | FAIL | 102.40s | 100.00s | 86389 | 22.8s / 13376 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 t0_tool_calls=2 t0_steps=0 t1_tool_calls=4 judge.score=1 The agent did not present a multi-step plan before executing tool calls, violating the first pass criterion. t0_jumped_to_tools [slow] 100.0s vs budget 70s |
| W11.B | FAIL | 152.69s | 150.00s | 59975 | 35.3s / 16676 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 t2_tool_calls=0 judge.score=1 The agent executed multiple tool calls immediately instead of presenting a multi-step plan first. [slow] 150.0s vs budget 105s |
| W11.C | FAIL | 46.19s | 41.31s | 46293 | 12.4s / 11910 / 100% | [judge_model=gemini-3.5-flash] rubric=v1 context_referenced=True decision_referenced=True judge.score=10 The agent successfully synthesized key details from both source documents, including the ~10GB/day ingest rate and the 50GB limit trigger. [slow] 41.3s vs budget 35s |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W11.B | 150.00s |
| 2 | W11.A | 100.00s |
| 3 | W11.C | 41.31s |

### Regression vs prior run

- W11.C: verdict PASS → FAIL
- W11.C: model-call ↑ 29.3s → 41.3s (+12.0s)

### Trace files

- **W11.A** — [case_W11.A.jsonl](../evals/_outputs/multistep_plan-20260610T123028Z/case_W11.A.jsonl)
- **W11.B** — [case_W11.B.jsonl](../evals/_outputs/multistep_plan-20260610T123028Z/case_W11.B.jsonl)
- **W11.C** — [case_W11.C.jsonl](../evals/_outputs/multistep_plan-20260610T123028Z/case_W11.C.jsonl)

---

## Run 2026-06-10T04:36:06+00:00

**Summary:** 1 PASS · 2 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W11.A | FAIL | 102.71s | 100.00s | 134769 | 15.1s / 23697 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 t0_tool_calls=9 t0_steps=0 t1_tool_calls=0 judge.score=0 The agent did not provide a multi-step plan before starting execution, violating Criterion 1, and made immediate tool calls. t0_jumped_to_tools [slow] 100.0s vs budget 70s |
| W11.B | FAIL | 152.85s | 150.00s | 125145 | 35.9s / 19263 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 t2_tool_calls=4 judge.score=1 The agent did not present a multi-step plan before executing, immediately resorting to tool calls in its first turn. [slow] 150.0s vs budget 105s |
| W11.C | PASS | 34.10s | 29.27s | 34329 | 5.6s / 11749 / 100% | [judge_model=gemini-3.5-flash] rubric=v1 context_referenced=True decision_referenced=True judge.score=10 The agent successfully synthesized information from both source documents, accurately incorporating the distinctive phrases requested. |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W11.B | 150.00s |
| 2 | W11.A | 100.00s |
| 3 | W11.C | 29.27s |

### Regression vs prior run

- W11.A: model-call ↑ 70.9s → 100.0s (+29.1s)
- W11.C: verdict FAIL → PASS
- W11.C: model-call ↓ 35.2s → 29.3s (-5.9s)

### Trace files

- **W11.A** — [case_W11.A.jsonl](../evals/_outputs/multistep_plan-20260610T043606Z/case_W11.A.jsonl)
- **W11.B** — [case_W11.B.jsonl](../evals/_outputs/multistep_plan-20260610T043606Z/case_W11.B.jsonl)
- **W11.C** — [case_W11.C.jsonl](../evals/_outputs/multistep_plan-20260610T043606Z/case_W11.C.jsonl)

---

## Run 2026-06-10T04:27:57+00:00

**Summary:** 0 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W11.A | FAIL | 74.04s | 70.95s | 47044 | 11.0s / 15444 / 67% | [judge_model=gemini-3.5-flash] rubric=v1 t0_tool_calls=9 t0_steps=2 t1_tool_calls=0 judge.score=1 The agent failed to formulate a multi-step plan or synthesize the sources because the session was interrupted after it could not find the project. t0_jumped_to_tools [slow] 70.9s vs budget 70s |
| W11.B | FAIL | 151.93s | 150.00s | 85090 | 20.5s / 16381 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 t2_tool_calls=0 judge.score=0 The agent did not propose a multi-step plan before executing tool calls. [slow] 150.0s vs budget 105s |
| W11.C | FAIL | 42.29s | 35.17s | 46381 | 11.7s / 11959 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 context_referenced=False decision_referenced=False judge.score=4 The agent successfully synthesized both sources, but the transcript only contains the summary task and lacks the multi-step refactor query to evaluate planning  missing_source [slow] 35.2s vs budget 35s |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W11.B | 150.00s |
| 2 | W11.A | 70.95s |
| 3 | W11.C | 35.17s |

### Regression vs prior run

- W11.A: model-call ↑ 64.9s → 70.9s (+6.0s)
- W11.C: model-call ↓ 50.0s → 35.2s (-14.8s)

### Trace files

- **W11.A** — [case_W11.A.jsonl](../evals/_outputs/multistep_plan-20260610T042757Z/case_W11.A.jsonl)
- **W11.B** — [case_W11.B.jsonl](../evals/_outputs/multistep_plan-20260610T042757Z/case_W11.B.jsonl)
- **W11.C** — [case_W11.C.jsonl](../evals/_outputs/multistep_plan-20260610T042757Z/case_W11.C.jsonl)

---

## Run 2026-06-09T16:49:08+00:00

**Summary:** 0 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W11.A | FAIL | 69.14s | 64.93s | 37154 | 12.4s / 23083 / 67% | [judge_model_same_as_agent] rubric=v1 t0_tool_calls=4 t0_steps=2 t1_tool_calls=0 judge.score=0 The agent failed to provide a multi-step plan, instead getting stuck in file search and failing to identify the correct project context. t0_jumped_to_tools |
| W11.B | FAIL | 153.46s | 150.00s | 64045 | 19.1s / 26542 / 0% | [judge_model_same_as_agent] rubric=v1 t2_tool_calls=0 judge.score=1 The agent failed to provide a numbered plan with explicit steps and immediately executed tool calls instead of pausing for confirmation. [slow] 150.0s vs budget 105s |
| W11.C | FAIL | 51.73s | 50.00s | - | 10.2s / 10676 / 0% | [judge_model_same_as_agent] rubric=v1 context_referenced=False decision_referenced=False judge.score=0 The transcript contains only a user interruption and no agent response, so no plan, checkpoints, or synthesis were provided. missing_source [slow] 50.0s vs budget 35s |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W11.B | 150.00s |
| 2 | W11.A | 64.93s |
| 3 | W11.C | 50.00s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W11.A** — [case_W11.A.jsonl](../evals/_outputs/multistep_plan-20260609T164908Z/case_W11.A.jsonl)
- **W11.B** — [case_W11.B.jsonl](../evals/_outputs/multistep_plan-20260609T164908Z/case_W11.B.jsonl)
- **W11.C** — [case_W11.C.jsonl](../evals/_outputs/multistep_plan-20260609T164908Z/case_W11.C.jsonl)

---

