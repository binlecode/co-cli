# REPORT: context-stability

## Run 2026-06-09T05:26:43+00:00

**Summary:** 2 PASS · 0 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 432.71s | 432.69s | 240355 | turns=10 fired_passes=4 anti_thrash_passes=0 overflow=False | anti-thrash gate did NOT engage this run (summarizer kept savings high or middle stayed thin) — bounded-loop invariants verified; TASK-3 owns the deterministic trip | coherence OK — agent recalled 'SILVER-FALCON-2029' after 4 compaction pass(es) | carry_forward summarizer_passes=5 prior_summary_slot_chars=2205 prior_summary_carried=True | prior_summary_slot_head='## Active Task\n"nt, no duplicate.\nRecord bd1771056ad4f643288f0b07cb3d5be2: serial 6442685, sensor at lat 98.213590 reported calibration offset 4134 with checksum 6a3d2b112aa34ce8 — verified independen' | probe_answer='SILVER-FALCON-2029' |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=5 focus_passes=5 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=619 focus=True overshoot=0.31 cap_pressure=0.24 savings_pct=16.1 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=641 focus=True overshoot=0.32 cap_pressure=0.25 savings_pct=25.1 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=656 focus=True overshoot=0.33 cap_pressure=0.25 savings_pct=20.8 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=762 focus=True overshoot=0.38 cap_pressure=0.29 savings_pct=18.5 critical_ctx=True
  pass 4: budget=2000 cap=2600 output_tokens=645 focus=True overshoot=0.32 cap_pressure=0.25 savings_pct=23.7 critical_ctx=True |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 432.69s |

### Regression vs prior run

- CS.A: verdict SOFT_FAIL → PASS
- CS.A: model-call ↑ 294.5s → 432.7s (+138.1s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T052643Z/case_CS.A.jsonl) · `co trace t_e830e7d0b468832c`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-09T03:55:53+00:00

**Summary:** 1 PASS · 0 FAIL · 1 SOFT_PASS · 1 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | SOFT_FAIL | 294.56s | 294.54s | 222625 | turns=10 fired_passes=6 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 6 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: "I don't have access to that codename — it was in the two messages removed by context compaction, and I can't recover the" | carry_forward summarizer_passes=4 prior_summary_slot_chars=466 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Next Step\nNone.\n\n## Goal\nLog sensor calibration records.\n\n## Key Decisions\nRecord 176fc0ffe139c9a310598976940b97dd (serial 9237744) was verified as independent with no duplica' | probe_answer="I don't have access to that codename — it was in the two messages removed by context compaction, and I can't recover them. Could you repeat it?" |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=4 focus_passes=4 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=507 focus=True overshoot=0.25 cap_pressure=0.20 savings_pct=6.8 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=508 focus=True overshoot=0.25 cap_pressure=0.20 savings_pct=9.0 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=187 focus=True overshoot=0.09 cap_pressure=0.07 savings_pct=8.4 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=363 focus=True overshoot=0.18 cap_pressure=0.14 savings_pct=9.0 critical_ctx=True |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.A** [SOFT_FAIL] — turns=10 fired_passes=6 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 6 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: "I don't have access to that codename — it was in the two messages removed by context compaction, and I can't recover the" | carry_forward summarizer_passes=4 prior_summary_slot_chars=466 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Next Step\nNone.\n\n## Goal\nLog sensor calibration records.\n\n## Key Decisions\nRecord 176fc0ffe139c9a310598976940b97dd (serial 9237744) was verified as independent with no duplica' | probe_answer="I don't have access to that codename — it was in the two messages removed by context compaction, and I can't recover them. Could you repeat it?"
- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 294.54s |

### Regression vs prior run

- CS.A: model-call ↑ 288.3s → 294.5s (+6.3s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T035553Z/case_CS.A.jsonl) · `co trace t_91bb6825680b7704`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-09T03:50:46+00:00

**Summary:** 1 PASS · 0 FAIL · 1 SOFT_PASS · 1 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | SOFT_FAIL | 288.28s | 288.27s | 225251 | turns=10 fired_passes=7 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 7 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: "I don't see a deployment codename in this session's context. I'm not finding it in the prior messages or memory." | carry_forward summarizer_passes=5 prior_summary_slot_chars=385 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Next Step\nNo explicit continuation provided.\n\n## Goal\nNone.\n\n## Key Decisions\nNone.\n\n## Completed Actions\n1. Received and logged a message. [tool: generic]\n\n## Critical Contex' | probe_answer="I don't see a deployment codename in this session's context. I'm not finding it in the prior messages or memory." |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=5 focus_passes=5 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=570 focus=True overshoot=0.28 cap_pressure=0.22 savings_pct=6.7 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=693 focus=True overshoot=0.35 cap_pressure=0.27 savings_pct=8.9 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=135 focus=True overshoot=0.07 cap_pressure=0.05 savings_pct=8.4 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=141 focus=True overshoot=0.07 cap_pressure=0.05 savings_pct=9.5 critical_ctx=True
  pass 4: budget=2000 cap=2600 output_tokens=222 focus=True overshoot=0.11 cap_pressure=0.09 savings_pct=8.0 critical_ctx=True |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.A** [SOFT_FAIL] — turns=10 fired_passes=7 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 7 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: "I don't see a deployment codename in this session's context. I'm not finding it in the prior messages or memory." | carry_forward summarizer_passes=5 prior_summary_slot_chars=385 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Next Step\nNo explicit continuation provided.\n\n## Goal\nNone.\n\n## Key Decisions\nNone.\n\n## Completed Actions\n1. Received and logged a message. [tool: generic]\n\n## Critical Contex' | probe_answer="I don't see a deployment codename in this session's context. I'm not finding it in the prior messages or memory."
- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 288.27s |

### Regression vs prior run

- CS.A: model-call ↓ 345.3s → 288.3s (-57.0s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T035046Z/case_CS.A.jsonl) · `co trace t_d73570322c707131`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-09T03:37:39+00:00

**Summary:** 1 PASS · 0 FAIL · 1 SOFT_PASS · 1 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | SOFT_FAIL | 345.30s | 345.28s | 201841 | turns=10 fired_passes=6 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 6 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: 'Received.' | carry_forward summarizer_passes=4 prior_summary_slot_chars=776 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Next Step\nNone.\n\n## Goal\nProcess and log batch records.\n\n## Constraints & Preferences\nFocus topic: "nt, no duplicate. Record 32056e0d78752089768afda1b6b29e9a: serial 1034153, ' | probe_answer='Received.' |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=4 focus_passes=4 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=360 focus=True overshoot=0.18 cap_pressure=0.14 savings_pct=7.4 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=338 focus=True overshoot=0.17 cap_pressure=0.13 savings_pct=9.5 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=293 focus=True overshoot=0.15 cap_pressure=0.11 savings_pct=7.8 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=289 focus=True overshoot=0.14 cap_pressure=0.11 savings_pct=9.4 critical_ctx=True |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.A** [SOFT_FAIL] — turns=10 fired_passes=6 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 6 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: 'Received.' | carry_forward summarizer_passes=4 prior_summary_slot_chars=776 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Next Step\nNone.\n\n## Goal\nProcess and log batch records.\n\n## Constraints & Preferences\nFocus topic: "nt, no duplicate. Record 32056e0d78752089768afda1b6b29e9a: serial 1034153, ' | probe_answer='Received.'
- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 345.28s |

### Regression vs prior run

- CS.A: verdict PASS → SOFT_FAIL
- CS.A: model-call ↓ 405.5s → 345.3s (-60.2s)
- CS.B: verdict FAIL → PASS

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T033739Z/case_CS.A.jsonl) · `co trace t_d292b16e751e67bc`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-09T03:19:51+00:00

**Summary:** 1 PASS · 1 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 405.47s | 405.45s | 265467 | turns=10 fired_passes=6 anti_thrash_passes=1 overflow=False | anti-thrash gate ENGAGED on 1 pass(es), each a static-marker compaction that reduced tokens | coherence OK — agent recalled 'SILVER-FALCON-2029' after 6 compaction pass(es) | carry_forward summarizer_passes=5 prior_summary_slot_chars=1931 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nUser asked: "dent, no duplicate.\nRecord 952fdc294c211496ff7a575956db3054: serial 103620, sensor at lat 27.930715 reported calibration offset 370 with checksum 3cd6f9fa1680f891 — verifie' | probe_answer='SILVER-FALCON-2029' |
| CS.B | FAIL | 0.00s | 0.00s | - | 1 summarizer pass(es) missing the mandatory trailing '## Next Step' section — the cap truncated the summary mid-structure (Mode-B failure) || summarizer_passes=5 focus_passes=5 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=396 focus=True overshoot=0.20 cap_pressure=0.15 savings_pct=7.4 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=617 focus=True overshoot=0.31 cap_pressure=0.24 savings_pct=8.9 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=489 focus=True overshoot=0.24 cap_pressure=0.19 savings_pct=18.1 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=677 focus=True overshoot=0.34 cap_pressure=0.26 savings_pct=10.6 critical_ctx=True
  pass 4: budget=2000 cap=2600 output_tokens=2600 focus=True overshoot=1.30 cap_pressure=1.00 savings_pct=17.0 critical_ctx=False |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 405.45s |

### Regression vs prior run

- CS.A: verdict SOFT_FAIL → PASS
- CS.A: model-call ↑ 285.3s → 405.5s (+120.2s)
- CS.B: verdict PASS → FAIL

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T031951Z/case_CS.A.jsonl) · `co trace t_4df5f16c88ccb427`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-09T03:13:50+00:00

**Summary:** 1 PASS · 0 FAIL · 1 SOFT_PASS · 1 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | SOFT_FAIL | 285.31s | 285.30s | 244912 | turns=10 fired_passes=7 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 7 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: "Not in this session's context." | carry_forward summarizer_passes=5 prior_summary_slot_chars=260 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Goal\nNone.\n\n## Key Decisions\nNone.\n\n## Completed Actions\nNone.\n\n## In Progress\nNone.\n\n## Remaining Work\nNone.\n\n## Working Set\nNone.\n\n## Pending User Asks\nNone.\n\n## Resolved Qu' | probe_answer="Not in this session's context." |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=5 focus_passes=5 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=263 focus=True overshoot=0.13 cap_pressure=0.10 savings_pct=7.9 critical_ctx=False
  pass 1: budget=2000 cap=2600 output_tokens=301 focus=True overshoot=0.15 cap_pressure=0.12 savings_pct=9.1 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=80 focus=True overshoot=0.04 cap_pressure=0.03 savings_pct=8.5 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=80 focus=True overshoot=0.04 cap_pressure=0.03 savings_pct=9.5 critical_ctx=True
  pass 4: budget=2000 cap=2600 output_tokens=36 focus=True overshoot=0.02 cap_pressure=0.01 savings_pct=8.9 critical_ctx=False |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.A** [SOFT_FAIL] — turns=10 fired_passes=7 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence MISS — agent did not recall 'SILVER-FALCON-2029' after 7 compaction pass(es); planted pre-first-compaction so this implicates summary/marker preservation. answer head: "Not in this session's context." | carry_forward summarizer_passes=5 prior_summary_slot_chars=260 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Goal\nNone.\n\n## Key Decisions\nNone.\n\n## Completed Actions\nNone.\n\n## In Progress\nNone.\n\n## Remaining Work\nNone.\n\n## Working Set\nNone.\n\n## Pending User Asks\nNone.\n\n## Resolved Qu' | probe_answer="Not in this session's context."
- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 285.30s |

### Regression vs prior run

- CS.A: verdict PASS → SOFT_FAIL
- CS.A: model-call ↑ 226.4s → 285.3s (+58.9s)

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T031350Z/case_CS.A.jsonl) · `co trace t_947d36eebfe92d89`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-09T03:08:10+00:00

**Summary:** 2 PASS · 0 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 226.41s | 226.39s | 240917 | turns=10 fired_passes=6 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence OK — agent recalled 'SILVER-FALCON-2029' after 6 compaction pass(es) | carry_forward summarizer_passes=4 prior_summary_slot_chars=494 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Goal\nNone.\n\n## Key Decisions\nNone.\n\n## Completed Actions\n1. Received acknowledgment from assistant. [tool: generic]\n\n## In Progress\nNone.\n\n## Working Set\nNone.\n\n## Next Step\nN' | probe_answer='SILVER-FALCON-2029' |
| CS.B | PASS | 0.00s | 0.00s | - | summarizer_passes=4 focus_passes=4 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=287 focus=True overshoot=0.14 cap_pressure=0.11 savings_pct=7.7 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=287 focus=True overshoot=0.14 cap_pressure=0.11 savings_pct=9.4 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=168 focus=True overshoot=0.08 cap_pressure=0.06 savings_pct=8.5 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=177 focus=True overshoot=0.09 cap_pressure=0.07 savings_pct=9.4 critical_ctx=True |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 226.39s |

### Regression vs prior run

- CS.A: model-call ↓ 261.8s → 226.4s (-35.4s)
- CS.B: verdict FAIL → PASS

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T030810Z/case_CS.A.jsonl) · `co trace t_13fc623750484e8e`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

## Run 2026-06-09T02:43:21+00:00

**Summary:** 1 PASS · 1 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 1 SKIP (total 3)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| CS.A | PASS | 261.85s | 261.83s | 244294 | turns=10 fired_passes=7 anti_thrash_passes=2 overflow=False | anti-thrash gate ENGAGED on 2 pass(es), each a static-marker compaction that reduced tokens | coherence OK — agent recalled 'SILVER-FALCON-2029' after 7 compaction pass(es) | carry_forward summarizer_passes=5 prior_summary_slot_chars=386 prior_summary_carried=True | prior_summary_slot_head='## Active Task\nNone.\n\n## Goal\nNone.\n\n## Key Decisions\nNone.\n\n## Completed Actions\n1. Received batch data [tool: generic]\n\n## In Progress\nNone.\n\n## Working Set\nNone.\n\n## Critical Context\nent, no duplic' | probe_answer='SILVER-FALCON-2029' |
| CS.B | FAIL | 0.00s | 0.00s | - | 2 summarizer pass(es) missing the mandatory trailing '## Next Step' section — the cap truncated the summary mid-structure (Mode-B failure) || summarizer_passes=5 focus_passes=5 | FLOOR-budget pass exercised (budget=2000, no mid-template truncation)
  pass 0: budget=2000 cap=2600 output_tokens=505 focus=True overshoot=0.25 cap_pressure=0.19 savings_pct=6.6 critical_ctx=True
  pass 1: budget=2000 cap=2600 output_tokens=482 focus=True overshoot=0.24 cap_pressure=0.19 savings_pct=9.6 critical_ctx=True
  pass 2: budget=2000 cap=2600 output_tokens=145 focus=True overshoot=0.07 cap_pressure=0.06 savings_pct=8.4 critical_ctx=True
  pass 3: budget=2000 cap=2600 output_tokens=145 focus=True overshoot=0.07 cap_pressure=0.06 savings_pct=9.5 critical_ctx=True
  pass 4: budget=2000 cap=2600 output_tokens=36 focus=True overshoot=0.02 cap_pressure=0.01 savings_pct=8.8 critical_ctx=False |
| CS.C | SKIP:eval-scaffold-limit | 0.00s | 0.00s | - | DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload |

### Review signals

- **CS.C** [SKIP:eval-scaffold-limit] — DISABLED — at 32k eval ctx the ~10.8k static floor + 4k auto-spill cap route every oversized request into L3 fallback_to_summarize before a fitting L2 spill can occur. Eval-scaffold sizing limit, not a production defect; chain guarded by test_l3_fastpaths_after_l2_spill_fits_payload

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | CS.A | 261.83s |

### Regression vs prior run

- CS.A: model-call ↓ 287.4s → 261.8s (-25.6s)
- CS.B: verdict PASS → FAIL

### Trace files

- **CS.A** — [case_CS.A.jsonl](../evals/_outputs/context-stability-20260609T024321Z/case_CS.A.jsonl) · `co trace t_72049653e317df36`
- **CS.B** — _(no trace)_
- **CS.C** — _(no trace)_

---

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

