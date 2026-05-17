# REPORT: skills

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

