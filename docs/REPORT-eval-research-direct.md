# REPORT: research_direct

## Run 2026-06-02T20:22:07+00:00

**Summary:** 4 PASS · 0 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| DECISION | PASS | 0.00s | 0.00s | - | PROVEN (for focused research) — 3/3 cases completed full research via the atomic web_search → web_fetch → synthesize loop with ZERO delegation. The normal agentic flow conducts and finishes multi-step research on its own; the dropped web_research subagent is not required for these tasks. Standing guard that research stays in the atomic loop with no delegation tool. [judge_model_same_as_agent] |
| release-notes | PASS | 72.87s | 72.87s | 59199 | search=2 fetch=4 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant correctly identified the latest version (0.28.1) and summarized changes using fetched GitHub data, providing real source URLs without refusal or fabrication. [judge_model_same_as_agent] |
| doc-compare | PASS | 192.57s | 192.57s | 131872 | search=0 fetch=8 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answers the comparison with specific details grounded in fetched documentation and includes real source URLs. [judge_model_same_as_agent] |
| current-fact | PASS | 70.22s | 70.22s | 58085 | search=1 fetch=3 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answers the question with specific version details and features, grounds the response in fetched content, and includes a real source URL. [judge_model_same_as_agent] |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | doc-compare | 192.57s |
| 2 | release-notes | 72.87s |
| 3 | current-fact | 70.22s |

### Regression vs prior run

- release-notes: model-call ↓ 160.2s → 72.9s (-87.3s)
- doc-compare: verdict SOFT_FAIL → PASS
- doc-compare: model-call ↑ 120.0s → 192.6s (+72.6s)
- current-fact: model-call ↓ 96.1s → 70.2s (-25.9s)

### Trace files

- **DECISION** — _(no trace)_
- **release-notes** — [case_release-notes.jsonl](../evals/_outputs/research_direct-20260602T202207Z/case_release-notes.jsonl) · `co trace t_ae114bf071bb71c4`
- **doc-compare** — [case_doc-compare.jsonl](../evals/_outputs/research_direct-20260602T202207Z/case_doc-compare.jsonl) · `co trace t_1c8c9f311c67b06b`
- **current-fact** — [case_current-fact.jsonl](../evals/_outputs/research_direct-20260602T202207Z/case_current-fact.jsonl) · `co trace t_b3eb9ecdd6f5fa1d`

---

## Run 2026-06-02T19:28:35+00:00

**Summary:** 3 PASS · 0 FAIL · 0 SOFT_PASS · 1 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| DECISION | PASS | 0.00s | 0.00s | - | PROVEN (for focused research) — 2/3 cases completed full research via the atomic web_search → web_fetch → synthesize loop with ZERO web_research delegation. The normal agentic flow conducts and finishes multi-step research on its own; the subagent is not required for these tasks. BOUNDARY: 1/3 case(s) — the heaviest multi-page task — ERRORED on an LLM-call timeout from context bloat: the atomic loop pulled multiple large pages into the main context until a model call timed out. This is exactly the failure mode web_research's context isolation prevents, so delegation earns its keep for heavy multi-source research even though it is unnecessary for focused research. Surface to TL as evidence for the keep/drop decision on web_research. [judge_model_same_as_agent] |
| release-notes | PASS | 160.16s | 160.16s | 151012 | search=2 fetch=7 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answered the question with specific version details, summarized notable changes from fetched sources, and included real URLs. [judge_model_same_as_agent] |
| doc-compare | SOFT_FAIL | 120.00s | 120.00s | - | search=5 fetch=9 delegate=0 — turn ERRORED mid-research (120s segment wall-clock timeout: serial generate→fetch rounds over many large pages exhausted the single segment budget). No delegation; the atomic loop progressed but did not complete. This is the failure mode delegation's context isolation + smaller per-call prompts mitigate. [judge_model_same_as_agent] |
| current-fact | PASS | 96.07s | 96.07s | 139286 | search=2 fetch=5 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answered the question with specific version details and features, grounded in fetched content, and included a real source URL. [judge_model_same_as_agent] |

### Review signals

- **doc-compare** [SOFT_FAIL] — search=5 fetch=9 delegate=0 — turn ERRORED mid-research (120s segment wall-clock timeout: serial generate→fetch rounds over many large pages exhausted the single segment budget). No delegation; the atomic loop progressed but did not complete. This is the failure mode delegation's context isolation + smaller per-call prompts mitigate. [judge_model_same_as_agent]

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | release-notes | 160.16s |
| 2 | doc-compare | 120.00s |
| 3 | current-fact | 96.07s |

### Regression vs prior run

- release-notes: model-call ↑ 109.1s → 160.2s (+51.0s)
- doc-compare: verdict FAIL → SOFT_FAIL
- doc-compare: model-call ↓ 240.0s → 120.0s (-120.0s)
- current-fact: model-call ↓ 105.5s → 96.1s (-9.4s)

### Trace files

- **DECISION** — _(no trace)_
- **release-notes** — [case_release-notes.jsonl](../evals/_outputs/research_direct-20260602T192835Z/case_release-notes.jsonl) · `co trace t_e18e025d4591984b`
- **doc-compare** — [case_doc-compare.jsonl](../evals/_outputs/research_direct-20260602T192835Z/case_doc-compare.jsonl) · `co trace t_93baa2d0f243f6f8`
- **current-fact** — [case_current-fact.jsonl](../evals/_outputs/research_direct-20260602T192835Z/case_current-fact.jsonl) · `co trace t_2f59e276389c14d4`

---

## Run 2026-06-02T19:19:18+00:00

**Summary:** 3 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| DECISION | PASS | 0.00s | 0.00s | - | INCONCLUSIVE — no delegation, but only 2/3 cases produced a clean grounded synthesis and a hard failure occurred. Inspect the failing cases. [judge_model_same_as_agent] |
| release-notes | PASS | 109.14s | 109.14s | 133668 | search=4 fetch=7 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answered the question with specific version details and notable changes, grounded in fetched data, and included real source URLs. [judge_model_same_as_agent] |
| doc-compare | FAIL | 240.00s | 240.00s | 201585 | search=0 fetch=7 delegate=0 — never searched; no research attempted [judge_model_same_as_agent] |
| current-fact | PASS | 105.46s | 105.46s | 92015 | search=4 fetch=4 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answered the question with specific version and features, grounded in fetched content, and included a valid source URL. [judge_model_same_as_agent] |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | doc-compare | 240.00s |
| 2 | release-notes | 109.14s |
| 3 | current-fact | 105.46s |

### Regression vs prior run

- release-notes: verdict FAIL → PASS
- release-notes: model-call ↓ 125.0s → 109.1s (-15.9s)
- doc-compare: model-call ↑ 120.0s → 240.0s (+120.0s)
- current-fact: verdict FAIL → PASS
- current-fact: model-call ↑ 93.4s → 105.5s (+12.1s)

### Trace files

- **DECISION** — _(no trace)_
- **release-notes** — [case_release-notes.jsonl](../evals/_outputs/research_direct-20260602T191918Z/case_release-notes.jsonl) · `co trace t_fd81663262ea9a66`
- **doc-compare** — [case_doc-compare.jsonl](../evals/_outputs/research_direct-20260602T191918Z/case_doc-compare.jsonl) · `co trace t_bf29ae2435f39077`
- **current-fact** — [case_current-fact.jsonl](../evals/_outputs/research_direct-20260602T191918Z/case_current-fact.jsonl) · `co trace t_0b99d0995ca602f2`

---

## Run 2026-06-02T19:10:32+00:00

**Summary:** 1 PASS · 3 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| DECISION | PASS | 0.00s | 0.00s | - | INCONCLUSIVE — no delegation, but only 0/3 cases produced a clean grounded synthesis and a hard failure occurred. Inspect the failing cases. [judge_model_same_as_agent] |
| release-notes | FAIL | 125.04s | 125.04s | 90882 | search=0 fetch=0 delegate=0 — never searched; no research attempted [judge_model_same_as_agent] |
| doc-compare | FAIL | 120.00s | 120.00s | - | search=0 fetch=0 delegate=0 — turn ERRORED before any search [judge_model_same_as_agent] |
| current-fact | FAIL | 93.35s | 93.35s | 170336 | search=0 fetch=0 delegate=0 — never searched; no research attempted [judge_model_same_as_agent] |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | release-notes | 125.04s |
| 2 | doc-compare | 120.00s |
| 3 | current-fact | 93.35s |

### Regression vs prior run

- release-notes: verdict PASS → FAIL
- release-notes: model-call ↑ 120.0s → 125.0s (+5.0s)
- current-fact: verdict PASS → FAIL
- current-fact: model-call ↑ 62.1s → 93.4s (+31.2s)

### Trace files

- **DECISION** — _(no trace)_
- **release-notes** — [case_release-notes.jsonl](../evals/_outputs/research_direct-20260602T191032Z/case_release-notes.jsonl) · `co trace t_ffaf3c5aa7b8641f`
- **doc-compare** — [case_doc-compare.jsonl](../evals/_outputs/research_direct-20260602T191032Z/case_doc-compare.jsonl) · `co trace t_10061487cd7b26ce`
- **current-fact** — [case_current-fact.jsonl](../evals/_outputs/research_direct-20260602T191032Z/case_current-fact.jsonl) · `co trace t_5fac585db17ec2e7`

---

## Run 2026-06-02T18:58:53+00:00

**Summary:** 3 PASS · 1 FAIL · 0 SOFT_PASS · 0 SOFT_FAIL · 0 SKIP (total 4)

### Cases

| Case | Verdict | Duration | Model-call s | Tokens | Reason |
|------|---------|----------|--------------|--------|--------|
| DECISION | PASS | 0.00s | 0.00s | - | INCONCLUSIVE — no delegation observed, but only 2/3 cases produced a clean grounded synthesis. The atomic loop ran without delegation but did not clear the quality bar on a majority; re-run or inspect soft cases. [judge_model_same_as_agent] |
| release-notes | PASS | 120.01s | 120.01s | 138180 | search=2 fetch=4 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answered the question with specific version details, summarized notable changes grounded in fetched data, and included real source URLs. [judge_model_same_as_agent] |
| doc-compare | FAIL | 120.00s | 120.00s | - | search=0 fetch=0 delegate=0 — never searched; no research attempted [judge_model_same_as_agent] |
| current-fact | PASS | 62.12s | 62.12s | 124346 | search=3 fetch=6 delegate=0 — atomic search→fetch→synthesize completed without delegation; judge: The assistant directly answers the question with specific version and features, grounds the response in fetched content, and includes real source URLs. [judge_model_same_as_agent] |

### Review signals

_(no review signals this run)_

### Slow ops (top 3)

| Rank | Case | Model-call s |
|------|------|--------------|
| 1 | release-notes | 120.01s |
| 2 | doc-compare | 120.00s |
| 3 | current-fact | 62.12s |

### Regression vs prior run

_(no prior run on disk)_

### Trace files

- **DECISION** — _(no trace)_
- **release-notes** — [case_release-notes.jsonl](../evals/_outputs/research_direct-20260602T185853Z/case_release-notes.jsonl) · `co trace t_b93eef6f8e921316`
- **doc-compare** — [case_doc-compare.jsonl](../evals/_outputs/research_direct-20260602T185853Z/case_doc-compare.jsonl) · `co trace t_3551e6ac2ff8a729`
- **current-fact** — [case_current-fact.jsonl](../evals/_outputs/research_direct-20260602T185853Z/case_current-fact.jsonl) · `co trace t_a65ec542b7b296b6`

---

