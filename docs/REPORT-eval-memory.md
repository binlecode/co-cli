# REPORT: memory

## Run 2026-05-17T02:40:13+00:00

**Summary:** 6 PASS · 0 FAIL · 0 SOFT_FAIL · 0 SKIP (total 6)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | PASS | 38.29s | 38.29s | 38526 | agent saved eval-w3-fact-def678b5.md; FTS rows=1 |
| W3.B | PASS | 29.13s | 27.70s | 39178 | W3.A hit at rank 4/8; snippet contains STG_DEPLOY_42 |
| W3.C | PASS | 9.99s | 9.99s | 25809 | 1 session_search call(s); at least one returned hits |
| W3.D | PASS | 0.04s | 0.00s | - | /memory list enumerates 'eval-w3-fact-def678b5' among 40 artifacts |
| W3.E | PASS | 0.01s | 0.00s | - | file removed; FTS rows for eval-w3-fact-def678b5.md cleared |
| W3.F | PASS | 1.79s | 0.00s | - | both tokens preserved across survivors+archive; extracted=0 merged=1 decayed=0 errors=0 |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.A | 38.29s |
| 2 | W3.B | 27.70s |
| 3 | W3.C | 9.99s |

### Regression vs prior run

- W3.A: verdict FAIL → PASS
- W3.A: model-call ↑ 35.5s → 38.3s (+2.7s)
- W3.B: verdict FAIL → PASS
- W3.B: model-call ↓ 36.3s → 27.7s (-8.6s)
- W3.E: verdict FAIL → PASS

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260517T024013Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260517T024013Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260517T024013Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260517T024013Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260517T024013Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260517T024013Z/case_W3.F.jsonl)

---

## Run 2026-05-17T02:37:08+00:00

**Summary:** 3 PASS · 3 FAIL · 0 SOFT_FAIL · 0 SKIP (total 6)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | FAIL | 35.54s | 35.54s | 25562 | IsADirectoryError: [Errno 21] Is a directory: '.' |
| W3.B | FAIL | 38.17s | 36.29s | 39585 | top hit stem='eval_W1_seed' != expected 'eval-w3-fact-a43d9228'; all hits: ['eval_W1_seed', 'deploy-id-6b1c1eb5', 'user-s-role-and-focus-dba415f0', 'eval-w3-fact-a43d9228', 'deploy-id-6f5fc4f9', 'deploy-id-deploy-77-8aeb5f9c', 'deploy-id-20064e6e', 'deploy-id-67698c4d'] |
| W3.C | PASS | 10.77s | 10.77s | 25823 | 1 session_search call(s); at least one returned hits |
| W3.D | PASS | 0.04s | 0.00s | - | /memory list enumerates 'eval-w3-fact-a43d9228' among 39 artifacts |
| W3.E | FAIL | 3.40s | 0.00s | - | knowledge_search for 'STG_DEPLOY_42' still returns hits after forget — stale index entry survived |
| W3.F | PASS | 1.94s | 0.00s | - | both tokens preserved across survivors+archive; extracted=0 merged=1 decayed=0 errors=0 |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.B | 36.29s |
| 2 | W3.A | 35.54s |
| 3 | W3.C | 10.77s |

### Regression vs prior run

- W3.A: model-call ↓ 46.7s → 35.5s (-11.2s)
- W3.B: verdict PASS → FAIL
- W3.B: model-call ↑ 34.7s → 36.3s (+1.6s)
- W3.C: verdict FAIL → PASS
- W3.C: model-call ↓ 50.0s → 10.8s (-39.2s)

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260517T023708Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260517T023708Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260517T023708Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260517T023708Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260517T023708Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260517T023708Z/case_W3.F.jsonl)

---

## Run 2026-05-17T02:28:08+00:00

**Summary:** 3 PASS · 3 FAIL · 0 SOFT_FAIL · 0 SKIP (total 6)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | FAIL | 46.74s | 46.74s | 25547 | [slow] 46.7s vs budget 35.0s |
| W3.B | PASS | 36.69s | 34.74s | 39260 | top hit 'eval-w3-fact-f3d93438' contains STG_DEPLOY_42; 7 total results |
| W3.C | FAIL | 50.01s | 50.00s | - | agent did not call session_search; tools used: [] |
| W3.D | PASS | 0.03s | 0.00s | - | /memory list enumerates 'eval-w3-fact-f3d93438' among 28 artifacts |
| W3.E | FAIL | 0.01s | 0.00s | - | artifact file eval-w3-fact-f3d93438.md still on disk after /memory forget |
| W3.F | PASS | 209.39s | 0.00s | - | both tokens preserved across survivors+archive; extracted=11 merged=2 decayed=0 errors=0 |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.C | 50.00s |
| 2 | W3.A | 46.74s |
| 3 | W3.B | 34.74s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260517T022808Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260517T022808Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260517T022808Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260517T022808Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260517T022808Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260517T022808Z/case_W3.F.jsonl)

---

