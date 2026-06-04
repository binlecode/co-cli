# REPORT: context-stability

## Run 2026-06-04T13:42:32+00:00

**Summary:** 2 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 2)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 477.76s | 477.74s | 287849 | turns=10 fired_passes=2 anti_thrash_passes=0 overflow=False | anti-thrash gate did NOT engage this run (summarizer kept savings high or middle stayed thin) — bounded-loop invariants verified; TASK-3 owns the deterministic trip |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=2 focus_passes=2 | no FLOOR-budget pass this run (smallest budget=2052)
  pass 0: budget=2052 cap=2668 output_tokens=379 focus=True overshoot=0.18 cap_pressure=0.14 savings_pct=46.8 critical_ctx=True
  pass 1: budget=2094 cap=2723 output_tokens=400 focus=True overshoot=0.19 cap_pressure=0.15 savings_pct=46.6 critical_ctx=True |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 477.74s |

### Regression vs prior run

- CS.A: model-call ↑ 397.7s → 477.7s (+80.1s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260604T134232Z/case_CS.A.jsonl) · `co trace t_b13cb2233a4b1fdc`
- **CS.B** — _(no trace)_

---

## Run 2026-06-04T13:35:36+00:00

**Summary:** 2 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 2)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 397.68s | 397.67s | 284483 | turns=10 fired_passes=2 anti_thrash_passes=0 overflow=False | anti-thrash gate did NOT engage this run (summarizer kept savings high or middle stayed thin) — bounded-loop invariants verified; TASK-3 owns the deterministic trip |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=2 focus_passes=2 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=640 focus=True overshoot=0.32 cap_pressure=0.25 savings_pct=46.5 critical_ctx=True
  pass 1: budget=2092 cap=2720 output_tokens=629 focus=True overshoot=0.30 cap_pressure=0.23 savings_pct=47.6 critical_ctx=True |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 397.67s |

### Regression vs prior run

- CS.A: model-call ↓ 412.5s → 397.7s (-14.8s)
- CS.B: new case (no prior run)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260604T133536Z/case_CS.A.jsonl) · `co trace t_0d087fbd2caeaabd`
- **CS.B** — _(no trace)_

---

## Run 2026-06-04T03:48:58+00:00

**Summary:** 1 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 1)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 412.49s | 412.47s | 284375 | turns=10 fired_passes=2 anti_thrash_passes=0 overflow=False | anti-thrash gate did NOT engage this run (summarizer kept savings high or middle stayed thin) — bounded-loop invariants verified; TASK-3 owns the deterministic trip |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 412.47s |

### Regression vs prior run

- CS.A: model-call ↑ 272.4s → 412.5s (+140.1s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260604T034858Z/case_CS.A.jsonl) · `co trace t_a745784d80db647b`

---

## Run 2026-06-04T03:41:31+00:00

**Summary:** 1 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 1)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 272.42s | 272.41s | 178088 | turns=10 fired_passes=8 anti_thrash_passes=0 overflow=False | anti-thrash gate did NOT engage this run (summarizer kept savings high or middle stayed thin) — bounded-loop invariants verified; TASK-3 owns the deterministic trip |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 272.41s |

### Regression vs prior run

- CS.A: model-call ↓ 400.3s → 272.4s (-127.9s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260604T034131Z/case_CS.A.jsonl) · `co trace t_06c8f67270393486`

---

## Run 2026-06-04T03:05:19+00:00

**Summary:** 1 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 1)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 400.28s | 400.27s | 282907 | turns=10 fired_passes=2 anti_thrash_passes=0 overflow=False | anti-thrash gate did NOT engage this run (summarizer kept savings high or middle stayed thin) — bounded-loop invariants verified; TASK-3 owns the deterministic trip |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 400.27s |

### Regression vs prior run

- CS.A: verdict FAIL → PASS
- CS.A: model-call ↑ 300.0s → 400.3s (+100.3s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260604T030519Z/case_CS.A.jsonl) · `co trace t_6a2039b43409dbd2`

---

## Run 2026-06-04T02:55:10+00:00

**Summary:** 0 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 1)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | FAIL | 300.01s | 299.99s | 124111 | turn 5 returned outcome='error' (possible overflow) |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 299.99s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260604T025510Z/case_CS.A.jsonl) · `co trace t_a36ebd64fccbddd3`

---

