# REPORT: context-stability

## Run 2026-06-07T15:33:04+00:00

**Summary:** 2 PASS · 0 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 287.40s | 287.39s | 270390 | turns=10 fired_passes=7 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence OK — agent recalled 'SILVER-FALCON-2029' after 7 compaction pass(es) |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=5 focus_passes=5 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=623 focus=True overshoot=0.31 cap_pressure=0.24 savings_pct=6.9 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=653 focus=True overshoot=0.33 cap_pressure=0.25 savings_pct=9.1 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=521 focus=True overshoot=0.26 cap_pressure=0.20 savings_pct=6.8 critical_ctx=False
  pass 3: budget=2000 cap=2600 output_tokens=527 focus=True overshoot=0.26 cap_pressure=0.20 savings_pct=9.2 critical_ctx=False
  pass 4: budget=2000 cap=2600 output_tokens=515 focus=True overshoot=0.26 cap_pressure=0.20 savings_pct=6.7 critical_ctx=True |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 287.39s |

### Regression vs prior run

- CS.A: model-call ↑ 154.4s → 287.4s (+133.0s)
- CS.C: new case (no prior run)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260607T153304Z/case_CS.A.jsonl) · `co trace t_af8fda412b2e0ab4`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-05T03:53:09+00:00

**Summary:** 2 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 154.41s | 154.39s | 193848 | turns=10 fired_passes=6 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=4 focus_passes=4 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=274 focus=True overshoot=0.14 cap_pressure=0.11 savings_pct=6.9 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=355 focus=True overshoot=0.18 cap_pressure=0.14 savings_pct=8.8 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=333 focus=True overshoot=0.17 cap_pressure=0.13 savings_pct=7.3 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=322 focus=True overshoot=0.16 cap_pressure=0.12 savings_pct=9.1 critical_ctx=True |
| CS.C | FAIL | 412.48s | 409.16s | 652812 | L2 never spilled across 16 turns — tool-output accumulation never crossed the spill trigger (raise _CS_C_MAX_TURNS or artifact size) |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.C | 409.16s |
| 2 | CS.A | 154.39s |

### Regression vs prior run

- CS.A: verdict FAIL → PASS
- CS.A: model-call ↓ 331.9s → 154.4s (-177.5s)
- CS.C: new case (no prior run)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260605T035309Z/case_CS.A.jsonl) · `co trace t_1702b924ea819eb6`
- **CS.B** — _(no trace)_
- **CS.C** — [case_CS.C.jsonl](../evals/_outputs/context-stability-20260605T035309Z/case_CS.C.jsonl) · `co trace t_2936d2d2cb49f9a5`

---

## Run 2026-06-05T00:40:37+00:00

**Summary:** 1 PASS · 2 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | FAIL | 331.94s | 331.93s | 212843 | post-pass total 16396 not below trigger 16384 (skip_reason=None) || turns=10 fired_passes=8 anti_thrash_passes=0 overflow=False |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=8 focus_passes=8 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=207 focus=True overshoot=0.10 cap_pressure=0.08 savings_pct=11.9 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=324 focus=True overshoot=0.16 cap_pressure=0.12 savings_pct=13.4 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=358 focus=True overshoot=0.18 cap_pressure=0.14 savings_pct=14.3 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=378 focus=True overshoot=0.19 cap_pressure=0.15 savings_pct=13.7 critical_ctx=True
  pass 4: budget=2000 cap=2600 output_tokens=388 focus=True overshoot=0.19 cap_pressure=0.15 savings_pct=13.6 critical_ctx=True
  pass 5: budget=2000 cap=2600 output_tokens=421 focus=True overshoot=0.21 cap_pressure=0.16 savings_pct=13.5 critical_ctx=True
  pass 6: budget=2000 cap=2600 output_tokens=458 focus=True overshoot=0.23 cap_pressure=0.18 savings_pct=13.5 critical_ctx=True
  pass 7: budget=2000 cap=2600 output_tokens=487 focus=True overshoot=0.24 cap_pressure=0.19 savings_pct=13.4 critical_ctx=True |
| CS.C | FAIL | 430.75s | 425.70s | 612823 | L2 never spilled across 16 turns — tool-output accumulation never crossed the spill trigger (raise _CS_C_MAX_TURNS or artifact size) |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.C | 425.70s |
| 2 | CS.A | 331.93s |

### Regression vs prior run

- CS.A: verdict PASS → FAIL
- CS.A: model-call ↓ 477.7s → 331.9s (-145.8s)
- CS.C: new case (no prior run)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260605T004037Z/case_CS.A.jsonl) · `co trace t_e1d45f3b071aa984`
- **CS.B** — _(no trace)_
- **CS.C** — [case_CS.C.jsonl](../evals/_outputs/context-stability-20260605T004037Z/case_CS.C.jsonl) · `co trace t_8917316532c95776`

---

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

