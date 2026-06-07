# REPORT: skills

## Run 2026-06-07T13:43:10+00:00

**Summary:** 4 PASS · 0 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 5)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | PASS | 19.50s | 18.53s | 22156 | token + args present; judge score=10 [judge_model_same_as_agent] |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.07s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |
| W4.E | SOFT_PASS | 51.57s | 51.56s | 104628 | discovery 3/3 (gate PASS, need ≥2) — keep DEFERRED; stub_prompt=1832c; t0=HIT[load=1,manage=1,disk=1,fw=0], t1=HIT[load=1,manage=1,disk=1,fw=0], t2=HIT[load=1,manage=1,disk=1,fw=0] |

### Review signals

- **W4.E** [SOFT_PASS] — discovery 3/3 (gate PASS, need ≥2) — keep DEFERRED; stub_prompt=1832c; t0=HIT[load=1,manage=1,disk=1,fw=0], t1=HIT[load=1,manage=1,disk=1,fw=0], t2=HIT[load=1,manage=1,disk=1,fw=0]

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.E | 51.56s |
| 2 | W4.A | 18.53s |

### Regression vs prior run

- W4.A: model-call ↓ 34.0s → 18.5s (-15.4s)
- W4.E: model-call ↓ 149.1s → 51.6s (-97.5s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260607T134310Z/case_W4.A.jsonl) · `co trace t_5b6fff2f75ba8b5f`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260607T134310Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260607T134310Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260607T134310Z/case_W4.D.jsonl)
- **W4.E** — [case_W4.E.jsonl](../evals/_outputs/skills-20260607T134310Z/case_W4.E.jsonl)

---

## Run 2026-06-03T13:00:33+00:00

**Summary:** 4 PASS · 0 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 5)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | PASS | 35.46s | 33.97s | 23633 | token + args present; judge score=10 [judge_model_same_as_agent] |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.07s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |
| W4.E | SOFT_PASS | 149.12s | 149.11s | 86619 | discovery 1/3 (gate FAIL, need ≥2) — revert to ALWAYS; stub_prompt=1501c; t0=miss[search=1,manage=0,disk=1,fw=0], t1=miss[search=1,manage=0,disk=1,fw=0], t2=HIT[search=1,manage=1,disk=1,fw=0] |

### Review signals

- **W4.E** [SOFT_PASS] — discovery 1/3 (gate FAIL, need ≥2) — revert to ALWAYS; stub_prompt=1501c; t0=miss[search=1,manage=0,disk=1,fw=0], t1=miss[search=1,manage=0,disk=1,fw=0], t2=HIT[search=1,manage=1,disk=1,fw=0]

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.E | 149.11s |
| 2 | W4.A | 33.97s |

### Regression vs prior run

- W4.A: model-call ↑ 31.6s → 34.0s (+2.4s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260603T130033Z/case_W4.A.jsonl) · `co trace t_e1dca9dd19d25c82`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260603T130033Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260603T130033Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260603T130033Z/case_W4.D.jsonl)
- **W4.E** — [case_W4.E.jsonl](../evals/_outputs/skills-20260603T130033Z/case_W4.E.jsonl)

---

## Run 2026-06-03T12:57:08+00:00

**Summary:** 4 PASS · 0 FAIL · 1 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 5)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | PASS | 33.03s | 31.61s | 23619 | token + args present; judge score=10 [judge_model_same_as_agent] |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.07s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |
| W4.E | SOFT_PASS | 150.02s | 150.00s | 73574 | discovery 0/3 (gate FAIL, need ≥2) — revert to ALWAYS; stub_prompt=1501c; t0=miss[search=1,manage=0,disk=1,fw=0], t1=miss[search=1,manage=0,disk=1,fw=0], t2=miss[search=1,manage=0,disk=1,fw=0] |

### Review signals

- **W4.E** [SOFT_PASS] — discovery 0/3 (gate FAIL, need ≥2) — revert to ALWAYS; stub_prompt=1501c; t0=miss[search=1,manage=0,disk=1,fw=0], t1=miss[search=1,manage=0,disk=1,fw=0], t2=miss[search=1,manage=0,disk=1,fw=0]

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.E | 150.00s |
| 2 | W4.A | 31.61s |

### Regression vs prior run

- W4.A: model-call ↓ 38.4s → 31.6s (-6.7s)
- W4.E: model-call ↑ 143.5s → 150.0s (+6.5s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260603T125708Z/case_W4.A.jsonl) · `co trace t_e57148fc8574bbe3`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260603T125708Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260603T125708Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260603T125708Z/case_W4.D.jsonl)
- **W4.E** — [case_W4.E.jsonl](../evals/_outputs/skills-20260603T125708Z/case_W4.E.jsonl)

---

## Run 2026-05-18T19:26:16+00:00

**Summary:** 4 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | PASS | 37.45s | 35.98s | 25393 | token + args present; judge score=10 [judge_model_same_as_agent] |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.06s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.01s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 35.98s |

### Regression vs prior run

- W4.A: verdict FAIL → PASS
- W4.A: model-call ↓ 41.4s → 36.0s (-5.4s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260518T192616Z/case_W4.A.jsonl) · `co trace t_78b44f02a0f5ad87`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260518T192616Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260518T192616Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260518T192616Z/case_W4.D.jsonl)

---

## Run 2026-05-18T19:24:31+00:00

**Summary:** 3 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | FAIL | 41.37s | 41.36s | 25410 | [slow] 41.4s vs budget 35.0s |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.07s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 41.36s |

### Regression vs prior run

- W4.A: model-call ↑ 37.5s → 41.4s (+3.8s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260518T192431Z/case_W4.A.jsonl) · `co trace t_9688b2ddafffddf2`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260518T192431Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260518T192431Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260518T192431Z/case_W4.D.jsonl)

---

## Run 2026-05-18T19:23:05+00:00

**Summary:** 3 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | FAIL | 37.54s | 37.54s | 25422 | [slow] 37.5s vs budget 35.0s |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.07s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 37.54s |

### Regression vs prior run

- W4.A: verdict PASS → FAIL
- W4.A: model-call ↑ 34.4s → 37.5s (+3.1s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260518T192305Z/case_W4.A.jsonl) · `co trace t_c5314ea05d1c0dd1`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260518T192305Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260518T192305Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260518T192305Z/case_W4.D.jsonl)

---

## Run 2026-05-18T18:11:09+00:00

**Summary:** 4 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | PASS | 35.84s | 34.44s | 25454 | token + args present; judge score=10 [judge_model_same_as_agent] |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.07s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 34.44s |

### Regression vs prior run

_(no changes vs prior run)_

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260518T181109Z/case_W4.A.jsonl) · `co trace t_ce3fbbafd95379fd`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260518T181109Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260518T181109Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260518T181109Z/case_W4.D.jsonl)

---

## Run 2026-05-18T16:19:05+00:00

**Summary:** 4 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | PASS | 35.96s | 34.65s | 25481 | token + args present; judge score=10 [judge_model_same_as_agent] |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.06s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.01s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 34.65s |

### Regression vs prior run

- W4.A: verdict FAIL → PASS
- W4.A: model-call ↑ 18.2s → 34.7s (+16.5s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260518T161905Z/case_W4.A.jsonl) · `co trace t_22cd20d74f1c4d1c`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260518T161905Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260518T161905Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260518T161905Z/case_W4.D.jsonl)

---

## Run 2026-05-18T16:17:19+00:00

**Summary:** 3 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | FAIL | 18.19s | 18.19s | 12781 | response missing literal CO_EVAL_TOKEN value 'EVALTOKEN_9b01b95b'; preview='$printf %s "$CO_EVAL_TOKEN"\nCO_EVAL_TOKEN=sk-eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9...\n\nARGS=evaluating_arg1' |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.07s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 18.19s |

### Regression vs prior run

- W4.A: model-call ↓ 34.0s → 18.2s (-15.8s)

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260518T161719Z/case_W4.A.jsonl) · `co trace t_189b8a67ba66f202`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260518T161719Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260518T161719Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260518T161719Z/case_W4.D.jsonl)

---

## Run 2026-05-18T02:20:26+00:00

**Summary:** 3 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | FAIL | 33.99s | 33.99s | 25526 | response missing literal CO_EVAL_TOKEN value 'EVALTOKEN_1d62d21b'; preview='CO_EVAL_TOKEN=\nARGS=evaluating_arg1' |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | PASS | 1.06s | 0.00s | - | create + patch + delete all observed on disk |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 33.99s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260518T022026Z/case_W4.A.jsonl) · `co trace t_5d190129abd8f56f`
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260518T022026Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260518T022026Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260518T022026Z/case_W4.D.jsonl)

---

## Run 2026-05-16T23:53:14+00:00

**Summary:** 2 PASS · 2 FAIL · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| W4.A | FAIL | 34.44s | 34.44s | 25477 | response missing literal CO_EVAL_TOKEN value 'EVALTOKEN_68e44a01'; preview='CO_EVAL_TOKEN: *(empty)*\nevaluating_arg1: *(empty)*' |
| W4.B | PASS | 0.00s | 0.00s | - | env restored and active_skill_name cleared |
| W4.C | FAIL | 0.00s | 0.00s | - | create failed: Invalid skill name 'eval_W4_lifecycle'. Name must be lowercase letters, digits, hyphens, or underscores; max 64 chars. |
| W4.D | PASS | 0.02s | 0.00s | - | built-in /help ran; user skill did not shadow |

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | W4.A | 34.44s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **W4.A** — [case_W4.A.jsonl](../evals/_outputs/skills-20260516T235314Z/case_W4.A.jsonl)
- **W4.B** — [case_W4.B.jsonl](../evals/_outputs/skills-20260516T235314Z/case_W4.B.jsonl)
- **W4.C** — [case_W4.C.jsonl](../evals/_outputs/skills-20260516T235314Z/case_W4.C.jsonl)
- **W4.D** — [case_W4.D.jsonl](../evals/_outputs/skills-20260516T235314Z/case_W4.D.jsonl)

---

