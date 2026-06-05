# REPORT: memory

## Run 2026-06-03T13:13:09+00:00

**Summary:** 6 PASS · 0 FAIL · 0 SOFT_PASS · 1 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | PASS | 40.25s | 40.25s | 23867 | agent saved eval-w3-fact-02c77a70.md; FTS rows=1 |
| W3.B | PASS | 21.76s | 21.37s | 24089 | W3.A hit at rank 1/5; snippet contains STG_DEPLOY_42 |
| W3.C | PASS | 8.80s | 8.80s | 23922 | 1 session_search call(s); at least one returned hits |
| W3.D | PASS | 0.01s | 0.00s | - | /memory list enumerates 'eval-w3-fact-02c77a70' among 9 artifacts |
| W3.E | PASS | 0.00s | 0.00s | - | file removed; FTS rows for eval-w3-fact-02c77a70.md cleared |
| W3.F | PASS | 1.73s | 0.00s | - | both tokens preserved across survivors+archive; merged=1 |
| W3.G | SOFT_FAIL | 83.26s | 80.48s | 86299 | seed_deleted=True | judge.score=0 [judge_model_same_as_agent] The agent reported that search results for 'W3G_MARKER_XK42' still exist in archived files, failing the requirement to r |

### Review signals

- **W3.G** [SOFT_FAIL] — seed_deleted=True | judge.score=0 [judge_model_same_as_agent] The agent reported that search results for 'W3G_MARKER_XK42' still exist in archived files, failing the requirement to r

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 80.48s |
| 2 | W3.A | 40.25s |
| 3 | W3.B | 21.37s |

### Regression vs prior run

- W3.A: verdict FAIL → PASS
- W3.A: model-call ↓ 50.0s → 40.3s (-9.8s)
- W3.B: verdict FAIL → PASS
- W3.B: model-call ↓ 50.0s → 21.4s (-28.6s)
- W3.C: model-call ↓ 28.9s → 8.8s (-20.1s)
- W3.G: verdict FAIL → SOFT_FAIL
- W3.G: model-call ↑ 50.0s → 80.5s (+30.5s)

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260603T131309Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260603T131309Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260603T131309Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260603T131309Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260603T131309Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260603T131309Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260603T131309Z/case_W3.G.jsonl)

---

## Run 2026-06-03T13:07:44+00:00

**Summary:** 4 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | FAIL | 50.00s | 50.00s | 11858 | no memory_create tool call found; tools used: [] |
| W3.B | FAIL | 50.00s | 50.00s | - | agent did not call memory_search; tools used: [] |
| W3.C | PASS | 28.88s | 28.88s | 23965 | 1 session_search call(s); at least one returned hits |
| W3.D | PASS | 0.01s | 0.00s | - | /memory list enumerates 'eval-w3-fact-b7e5b778' among 8 artifacts |
| W3.E | PASS | 0.00s | 0.00s | - | file removed; FTS rows for eval-w3-fact-b7e5b778.md cleared |
| W3.F | PASS | 3.64s | 0.00s | - | both tokens preserved across survivors+archive; merged=1 |
| W3.G | FAIL | 50.00s | 50.00s | - | turn 0: agent did not call memory_search; tools=[] |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 50.00s |
| 2 | W3.A | 50.00s |
| 3 | W3.B | 50.00s |

### Regression vs prior run

- W3.A: verdict PASS → FAIL
- W3.A: model-call ↑ 39.5s → 50.0s (+10.5s)
- W3.B: verdict PASS → FAIL
- W3.B: model-call ↑ 22.7s → 50.0s (+27.3s)
- W3.C: model-call ↑ 8.7s → 28.9s (+20.2s)
- W3.G: verdict SOFT_FAIL → FAIL
- W3.G: model-call ↓ 122.2s → 50.0s (-72.2s)

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260603T130744Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260603T130744Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260603T130744Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260603T130744Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260603T130744Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260603T130744Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260603T130744Z/case_W3.G.jsonl)

---

## Run 2026-06-03T13:03:53+00:00

**Summary:** 6 PASS · 0 FAIL · 0 SOFT_PASS · 1 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | PASS | 39.47s | 39.47s | 23643 | agent saved eval-w3-fact-b290c0f9.md; FTS rows=1 |
| W3.B | PASS | 22.98s | 22.65s | 24075 | W3.A hit at rank 1/5; snippet contains STG_DEPLOY_42 |
| W3.C | PASS | 8.70s | 8.70s | 23908 | 1 session_search call(s); at least one returned hits |
| W3.D | PASS | 0.01s | 0.00s | - | /memory list enumerates 'eval-w3-fact-b290c0f9' among 8 artifacts |
| W3.E | PASS | 0.00s | 0.00s | - | file removed; FTS rows for eval-w3-fact-b290c0f9.md cleared |
| W3.F | PASS | 1.75s | 0.00s | - | both tokens preserved across survivors+archive; merged=1 |
| W3.G | SOFT_FAIL | 129.56s | 122.16s | 60800 | seed_deleted=True | judge.score=0 [judge_model_same_as_agent] The transcript ends after the deletion step and does not show the required second search for 'W3G_MARKER_XK42' to verify |

### Review signals

- **W3.G** [SOFT_FAIL] — seed_deleted=True | judge.score=0 [judge_model_same_as_agent] The transcript ends after the deletion step and does not show the required second search for 'W3G_MARKER_XK42' to verify

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 122.16s |
| 2 | W3.A | 39.47s |
| 3 | W3.B | 22.65s |

### Regression vs prior run

- W3.A: model-call ↓ 41.0s → 39.5s (-1.5s)
- W3.B: model-call ↓ 24.5s → 22.7s (-1.8s)
- W3.C: model-call ↓ 9.9s → 8.7s (-1.2s)
- W3.G: model-call ↑ 89.0s → 122.2s (+33.2s)

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260603T130353Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260603T130353Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260603T130353Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260603T130353Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260603T130353Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260603T130353Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260603T130353Z/case_W3.G.jsonl)

---

## Run 2026-06-02T01:50:39+00:00

**Summary:** 7 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | PASS | 41.55s | 41.55s | 25661 | agent saved eval-w3-fact-40b54c8f.md; FTS rows=1 |
| W3.B | PASS | 30.14s | 30.14s | 38802 | W3.A hit at rank 1/1; snippet contains STG_DEPLOY_42 |
| W3.C | PASS | 9.30s | 9.29s | 25645 | 1 session_search call(s); at least one returned hits |
| W3.D | PASS | 0.00s | 0.00s | - | /memory list enumerates 'eval-w3-fact-40b54c8f' among 1 artifacts |
| W3.E | PASS | 0.00s | 0.00s | - | file removed; FTS rows for eval-w3-fact-40b54c8f.md cleared |
| W3.F | PASS | 1.78s | 0.00s | - | both tokens preserved across survivors+archive; merged=1 |
| W3.G | PASS | 69.20s | 67.55s | 77250 | seed_deleted=True | judge.score=10 [judge_model_same_as_agent] The agent correctly reported no results after the memory item was deleted, confirming the deletion was successful. |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 67.55s |
| 2 | W3.A | 41.55s |
| 3 | W3.B | 30.14s |

### Regression vs prior run

- W3.A: verdict FAIL → PASS
- W3.A: model-call ↓ 50.0s → 41.5s (-8.5s)
- W3.B: verdict FAIL → PASS
- W3.C: model-call ↓ 36.0s → 9.3s (-26.7s)
- W3.D: verdict FAIL → PASS
- W3.E: verdict FAIL → PASS
- W3.G: model-call ↓ 116.0s → 67.5s (-48.4s)

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260602T015039Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260602T015039Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260602T015039Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260602T015039Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260602T015039Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260602T015039Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260602T015039Z/case_W3.G.jsonl)

---

## Run 2026-06-02T01:45:27+00:00

**Summary:** 3 PASS · 4 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | FAIL | 50.00s | 50.00s | - | no memory_create tool call found; tools used: [] |
| W3.B | FAIL | 0.00s | 0.00s | - | W3.A artifact missing — case ordering broken |
| W3.C | PASS | 36.01s | 36.01s | 25644 | 1 session_search call(s); at least one returned hits |
| W3.D | FAIL | 0.00s | 0.00s | - | W3.A artifact missing — case ordering broken |
| W3.E | FAIL | 0.00s | 0.00s | - | W3.A artifact missing — case ordering broken |
| W3.F | PASS | 1.79s | 0.00s | - | both tokens preserved across survivors+archive; merged=1 |
| W3.G | PASS | 119.18s | 115.99s | 77262 | seed_deleted=True | judge.score=10 [judge_model_same_as_agent] The agent correctly reported no results found after the memory item was deleted, satisfying the pass criteria. |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 115.99s |
| 2 | W3.A | 50.00s |
| 3 | W3.C | 36.01s |

### Regression vs prior run

- W3.C: verdict FAIL → PASS
- W3.C: model-call ↑ 12.7s → 36.0s (+23.3s)
- W3.G: model-call ↓ 143.0s → 116.0s (-27.0s)

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260602T014527Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260602T014527Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260602T014527Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260602T014527Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260602T014527Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260602T014527Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260602T014527Z/case_W3.G.jsonl)

---

## Run 2026-06-02T01:42:42+00:00

**Summary:** 2 PASS · 5 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | FAIL | 50.00s | 50.00s | - | no memory_create tool call found; tools used: [] |
| W3.B | FAIL | 0.00s | 0.00s | - | W3.A artifact missing — case ordering broken |
| W3.C | FAIL | 12.69s | 12.69s | 25546 | all session_search returns indicated zero hits — current session not surfaced |
| W3.D | FAIL | 0.00s | 0.00s | - | W3.A artifact missing — case ordering broken |
| W3.E | FAIL | 0.00s | 0.00s | - | W3.A artifact missing — case ordering broken |
| W3.F | PASS | 2.82s | 0.00s | - | both tokens preserved across survivors+archive; merged=1 |
| W3.G | PASS | 148.25s | 143.04s | 77315 | seed_deleted=True | judge.score=10 [judge_model_same_as_agent] The agent correctly reported no results found after the memory item was deleted, satisfying the pass criteria. |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 143.04s |
| 2 | W3.A | 50.00s |
| 3 | W3.C | 12.69s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260602T014242Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260602T014242Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260602T014242Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260602T014242Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260602T014242Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260602T014242Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260602T014242Z/case_W3.G.jsonl)

---

## Run 2026-06-02T01:38:56+00:00

**Summary:** 4 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | PASS | 37.71s | 37.71s | 25683 | agent saved eval-w3-fact-0f5c22c6.md; FTS rows=1 |
| W3.B | PASS | 22.56s | 22.56s | 25625 | W3.A hit at rank 1/1; snippet contains STG_DEPLOY_42 |
| W3.C | FAIL | 8.48s | 8.48s | 25553 | all session_search returns indicated zero hits — current session not surfaced |
| W3.D | FAIL | 0.00s | 0.00s | - | production load_artifacts did not surface 'eval-w3-fact-0f5c22c6'; found 0 artifacts total |
| W3.E | FAIL | 0.00s | 0.00s | - | artifact file eval-w3-fact-0f5c22c6.md still on disk after /memory forget |
| W3.F | PASS | 1.73s | 0.00s | - | both tokens preserved across survivors+archive; merged=1 |
| W3.G | PASS | 91.01s | 86.72s | 90877 | seed_deleted=True | judge.score=10 [judge_model_same_as_agent] The agent correctly reported no results for the deleted memory item in turn 2, satisfying the pass criteria. |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 86.72s |
| 2 | W3.A | 37.71s |
| 3 | W3.B | 22.56s |

### Regression vs prior run

- W3.A: model-call ↑ 36.1s → 37.7s (+1.6s)
- W3.G: model-call ↑ 50.2s → 86.7s (+36.5s)

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260602T013856Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260602T013856Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260602T013856Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260602T013856Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260602T013856Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260602T013856Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260602T013856Z/case_W3.G.jsonl)

---

## Run 2026-06-02T00:58:14+00:00

**Summary:** 4 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | PASS | 36.08s | 36.08s | 25613 | agent saved eval-w3-fact-166a8a6c.md; FTS rows=1 |
| W3.B | PASS | 22.50s | 22.50s | 25624 | W3.A hit at rank 1/1; snippet contains STG_DEPLOY_42 |
| W3.C | FAIL | 0.00s | 0.00s | - | index_session failed: AttributeError: 'MemoryStore' object has no attribute 'index_session' |
| W3.D | FAIL | 0.00s | 0.00s | - | production load_artifacts did not surface 'eval-w3-fact-166a8a6c'; found 0 artifacts total |
| W3.E | FAIL | 0.00s | 0.00s | - | artifact file eval-w3-fact-166a8a6c.md still on disk after /memory forget |
| W3.F | PASS | 0.00s | 0.00s | - | both tokens preserved across survivors+archive; merged=0 |
| W3.G | PASS | 51.84s | 50.18s | 77327 | seed_deleted=True | judge.score=10 [judge_model_same_as_agent] The agent correctly reported no results for the deleted memory item in turn 2, confirming the deletion was effective. |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.G | 50.18s |
| 2 | W3.A | 36.08s |
| 3 | W3.B | 22.50s |

### Regression vs prior run

- W3.A: verdict FAIL → PASS
- W3.B: verdict FAIL → PASS
- W3.F: verdict FAIL → PASS
- W3.G: verdict FAIL → PASS

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260602T005814Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260602T005814Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260602T005814Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260602T005814Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260602T005814Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260602T005814Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260602T005814Z/case_W3.G.jsonl)

---

## Run 2026-06-02T00:51:46+00:00

**Summary:** 0 PASS · 7 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 7)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W3.A | FAIL | 18.80s | 0.00s | - | OperationalError: table docs has no column named created_at |
| W3.B | FAIL | 23.01s | 23.01s | 25688 | production memory_search for 'STG_DEPLOY_42' returned 0 results |
| W3.C | FAIL | 0.00s | 0.00s | - | index_session failed: AttributeError: 'MemoryStore' object has no attribute 'index_session' |
| W3.D | FAIL | 0.00s | 0.00s | - | production load_artifacts did not surface 'eval-w3-fact-4230461f'; found 0 artifacts total |
| W3.E | FAIL | 0.00s | 0.00s | - | artifact file eval-w3-fact-4230461f.md still on disk after /memory forget |
| W3.F | FAIL | 0.00s | 0.00s | - | seed_dup_pair failed: OperationalError: table docs has no column named created_at |
| W3.G | FAIL | 0.00s | 0.00s | - | seed failed: OperationalError: table docs has no column named created_at |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W3.B | 23.01s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W3.A** — [case_W3.A.jsonl](../evals/_outputs/memory-20260602T005146Z/case_W3.A.jsonl)
- **W3.B** — [case_W3.B.jsonl](../evals/_outputs/memory-20260602T005146Z/case_W3.B.jsonl)
- **W3.C** — [case_W3.C.jsonl](../evals/_outputs/memory-20260602T005146Z/case_W3.C.jsonl)
- **W3.D** — [case_W3.D.jsonl](../evals/_outputs/memory-20260602T005146Z/case_W3.D.jsonl)
- **W3.E** — [case_W3.E.jsonl](../evals/_outputs/memory-20260602T005146Z/case_W3.E.jsonl)
- **W3.F** — [case_W3.F.jsonl](../evals/_outputs/memory-20260602T005146Z/case_W3.F.jsonl)
- **W3.G** — [case_W3.G.jsonl](../evals/_outputs/memory-20260602T005146Z/case_W3.G.jsonl)

---

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

