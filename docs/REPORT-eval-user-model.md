# REPORT: user_model

## Run 2026-06-09T21:26:02+00:00

**Summary:** 0 PASS · 2 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W10.A | FAIL | 21.89s | 18.89s | 56773 | 4.8s / 11765 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 judge.score=3 The agent asked which language was preferred, violating the rule that seeded preferences should obviate this question, and the response was overly verbose. |
| W10.B | FAIL | 17.11s | 14.70s | 45195 | 4.6s / 11380 / 0% | [judge_model=gemini-3.5-flash] rubric=v1 judge.score=4 The agent failed to revert to the default Python preference on the neutral third turn after a one-shot Go override. |
| W10.C | SOFT_PASS | 36.36s | 0.00s | - | - | aged pref_terse to ~95d (mtime) pref_terse preserved (SOFT_PASS) |

### Review signals

- **W10.C** [SOFT_PASS] — aged pref_terse to ~95d (mtime) pref_terse preserved (SOFT_PASS)

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W10.A | 18.89s |
| 2 | W10.B | 14.70s |

### Regression vs prior run

- W10.A: model-call ↓ 27.4s → 18.9s (-8.5s)
- W10.B: model-call ↑ 12.3s → 14.7s (+2.4s)

### Trace files

- **W10.A** — [case_W10.A.jsonl](../evals/_outputs/user_model-20260609T212602Z/case_W10.A.jsonl)
- **W10.B** — [case_W10.B.jsonl](../evals/_outputs/user_model-20260609T212602Z/case_W10.B.jsonl)
- **W10.C** — _(no trace)_

---

## Run 2026-06-09T16:47:49+00:00

**Summary:** 0 PASS · 2 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Perf (p95/ctx/goal) | Reason |
|------|---------|----------|--------------|--------|---------------------|--------|
| W10.A | FAIL | 29.44s | 27.38s | 44814 | 10.1s / 11463 / 0% | [judge_model_same_as_agent] rubric=v1 judge.score=4 The agent failed the terse preference criterion by providing a verbose, multi-section response with headers and bullet points instead of a direct, concise answe |
| W10.B | FAIL | 14.02s | 12.29s | 34085 | 4.6s / 11356 / 0% | [judge_model_same_as_agent] rubric=v1 judge.score=3 The one-shot Go override persisted into the third turn instead of reverting to the seeded Python default, violating criterion 2. |
| W10.C | SOFT_PASS | 32.62s | 0.00s | - | - | aged pref_terse to ~95d (mtime) pref_terse preserved (SOFT_PASS) |

### Review signals

- **W10.C** [SOFT_PASS] — aged pref_terse to ~95d (mtime) pref_terse preserved (SOFT_PASS)

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W10.A | 27.38s |
| 2 | W10.B | 12.29s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W10.A** — [case_W10.A.jsonl](../evals/_outputs/user_model-20260609T164749Z/case_W10.A.jsonl)
- **W10.B** — [case_W10.B.jsonl](../evals/_outputs/user_model-20260609T164749Z/case_W10.B.jsonl)
- **W10.C** — _(no trace)_

---

