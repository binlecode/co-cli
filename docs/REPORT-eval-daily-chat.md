# REPORT: daily_chat

## Run 2026-05-17T02:06:34+00:00

**Summary:** 4 PASS · 0 FAIL · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W1.A | PASS | 27.04s | 25.31s | 38970 | [judge_model_same_as_agent] judge.score=10 The assistant engaged on-topic by summarizing the interrupted session and identifying the incomplete analysis regarding |
| W1.B | PASS | 22.45s | 22.45s | 25840 | file_find |
| W1.C | PASS | 5.94s | 5.94s | 25607 | knowledge_tool+token |
| W1.D | PASS | 0.02s | 0.00s | - | dry_run_clean |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W1.A | 25.31s |
| 2 | W1.B | 22.45s |
| 3 | W1.C | 5.94s |

### Regression vs prior run

- W1.A: verdict FAIL → PASS
- W1.C: verdict FAIL → PASS
- W1.C: model-call ↓ 31.3s → 5.9s (-25.4s)

### Trace files

- **W1.A** — [case_W1.A.jsonl](../evals/_outputs/daily_chat-20260517T020634Z/case_W1.A.jsonl)
- **W1.B** — [case_W1.B.jsonl](../evals/_outputs/daily_chat-20260517T020634Z/case_W1.B.jsonl)
- **W1.C** — [case_W1.C.jsonl](../evals/_outputs/daily_chat-20260517T020634Z/case_W1.C.jsonl)
- **W1.D** — _(no trace)_

---

## Run 2026-05-17T02:02:56+00:00

**Summary:** 2 PASS · 2 FAIL · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W1.A | FAIL | 27.60s | 25.98s | 38898 | [judge_model_same_as_agent] judge.score=4 The response failed to summarize the session as requested, instead providing incomplete metadata about an interrupted ta session_jsonl_delta=0 |
| W1.B | PASS | 21.90s | 21.90s | 25827 | file_find |
| W1.C | FAIL | 31.36s | 31.31s | 69383 | token_missing_in_response |
| W1.D | PASS | 0.02s | 0.00s | - | dry_run_clean |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W1.C | 31.31s |
| 2 | W1.A | 25.98s |
| 3 | W1.B | 21.90s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W1.A** — [case_W1.A.jsonl](../evals/_outputs/daily_chat-20260517T020256Z/case_W1.A.jsonl)
- **W1.B** — [case_W1.B.jsonl](../evals/_outputs/daily_chat-20260517T020256Z/case_W1.B.jsonl)
- **W1.C** — [case_W1.C.jsonl](../evals/_outputs/daily_chat-20260517T020256Z/case_W1.C.jsonl)
- **W1.D** — _(no trace)_

---

