# REPORT: context-stability

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

