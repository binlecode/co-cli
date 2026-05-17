# REPORT: session-continuity

## Run 2026-05-17T02:24:43+00:00

**Summary:** 6 PASS · 0 FAIL · 0 SOFT_FAIL · 0 SKIP (total 6)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W2.A | PASS | 0.00s | 0.00s | - | - |
| W2.B | PASS | 17.09s | 17.09s | 12711 | - |
| W2.C | PASS | 0.00s | 0.00s | - | - |
| W2.D | PASS | 52.05s | 52.03s | 78697 | - |
| W2.E | PASS | 101.86s | 80.95s | 190794 | N=10 compaction_ratio=0.50 len 28→5 |
| W2.F | PASS | 20.68s | 20.68s | - | len 5→5 markers=1 (stable within ±1) |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W2.E | 80.95s |
| 2 | W2.D | 52.03s |
| 3 | W2.F | 20.68s |

### Regression vs prior run

- W2.D: model-call ↑ 16.2s → 52.0s (+35.8s)
- W2.E: verdict FAIL → PASS
- W2.E: model-call ↑ 21.3s → 81.0s (+59.6s)
- W2.F: verdict FAIL → PASS

### Trace files

- **W2.A** — _(no trace)_
- **W2.B** — [case_W2.B.jsonl](../evals/_outputs/session-continuity-20260517T022443Z/case_W2.B.jsonl)
- **W2.C** — _(no trace)_
- **W2.D** — [case_W2.D.jsonl](../evals/_outputs/session-continuity-20260517T022443Z/case_W2.D.jsonl)
- **W2.E** — [case_W2.E.jsonl](../evals/_outputs/session-continuity-20260517T022443Z/case_W2.E.jsonl)
- **W2.F** — _(no trace)_

---

## Run 2026-05-17T02:21:08+00:00

**Summary:** 4 PASS · 2 FAIL · 0 SOFT_FAIL · 0 SKIP (total 6)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W2.A | PASS | 0.00s | 0.00s | - | - |
| W2.B | PASS | 18.01s | 18.01s | 12718 | - |
| W2.C | PASS | 0.00s | 0.00s | - | - |
| W2.D | PASS | 16.21s | 16.19s | 52163 | - |
| W2.E | FAIL | 104.80s | 21.32s | 52717 | inflation+compact failed: BlockingIOError: [Errno 35] write could not complete without blocking |
| W2.F | FAIL | 0.00s | 0.00s | - | W2.E produced no post-compact history; cannot assert idempotence |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W2.E | 21.32s |
| 2 | W2.B | 18.01s |
| 3 | W2.D | 16.19s |

### Regression vs prior run

- W2.B: model-call ↑ 16.7s → 18.0s (+1.3s)
- W2.D: verdict FAIL → PASS
- W2.D: model-call ↓ 28.3s → 16.2s (-12.1s)
- W2.E: verdict PASS → FAIL
- W2.E: model-call ↓ 636.9s → 21.3s (-615.5s)
- W2.F: verdict PASS → FAIL

### Trace files

- **W2.A** — _(no trace)_
- **W2.B** — [case_W2.B.jsonl](../evals/_outputs/session-continuity-20260517T022108Z/case_W2.B.jsonl)
- **W2.C** — _(no trace)_
- **W2.D** — [case_W2.D.jsonl](../evals/_outputs/session-continuity-20260517T022108Z/case_W2.D.jsonl)
- **W2.E** — [case_W2.E.jsonl](../evals/_outputs/session-continuity-20260517T022108Z/case_W2.E.jsonl)
- **W2.F** — _(no trace)_

---

## Run 2026-05-17T02:07:54+00:00

**Summary:** 5 PASS · 1 FAIL · 0 SOFT_FAIL · 0 SKIP (total 6)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W2.A | PASS | 0.00s | 0.00s | - | - |
| W2.B | PASS | 16.68s | 16.67s | 12720 | - |
| W2.C | PASS | 0.00s | 0.00s | - | - |
| W2.D | FAIL | 28.33s | 28.33s | 38857 | load_transcript returned empty for 2026-05-17-T020754Z-284cbca5.jsonl |
| W2.E | PASS | 686.17s | 636.85s | 437063 | N=10 compaction_ratio=0.50 len 40→3 |
| W2.F | PASS | 7.55s | 7.55s | - | len 3→3 markers=1 (stable within ±1) |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W2.E | 636.85s |
| 2 | W2.D | 28.33s |
| 3 | W2.B | 16.67s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W2.A** — _(no trace)_
- **W2.B** — [case_W2.B.jsonl](../evals/_outputs/session-continuity-20260517T020754Z/case_W2.B.jsonl)
- **W2.C** — _(no trace)_
- **W2.D** — [case_W2.D.jsonl](../evals/_outputs/session-continuity-20260517T020754Z/case_W2.D.jsonl)
- **W2.E** — [case_W2.E.jsonl](../evals/_outputs/session-continuity-20260517T020754Z/case_W2.E.jsonl)
- **W2.F** — _(no trace)_

---

