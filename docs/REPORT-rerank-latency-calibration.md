# REPORT: TEI Reranker Latency — Root Cause & Truncation Calibration

**Date:** 2026-06-10
**Trigger:** `eval_multistep_plan` case W11.C hit its 50s turn budget and was scored a
stall (2026-06-09). This report records the investigation that found the cause, the
hypotheses it falsified, and the calibration that fixed it.
**Outcome:** per-candidate reranker input is truncated to `rerank_text_char_budget`
(default **512 chars**), cutting the worst-case rerank call from ~14s to ~2s with
negligible ranking-quality loss. Config plumbed in `co_cli/config/memory.py`; applied
in `co_cli/index/_retrieval.py::_fetch_reranker_texts`.

Hardware: single Apple Silicon box, 128 GB unified memory. Reranker:
`bge-reranker-v2-m3` (cross-encoder) on TEI/Metal, port 8282. Chat model:
`qwen3.6:35b-a3b-agentic` on ollama (24 GB GPU-resident).

---

## 1. The original incident

W11.C ran 50.00s of model-call time and was cut off (empty assistant output). The
trace (`t_1001b702d684728a`) decomposed the turn as **sequential** ops — no concurrency:

| t+ | op | dur |
|---|---|---|
| 0.0s | chat qwen3.6:35b | 11.2s |
| 11.2s | memory_search → user pass (empty, skips embed+rerank) | 0.14s |
| 11.3s | memory_search → **waterfall pass (embed+rerank)** | **14.87s** |
| 26.2s | memory_search → user pass | 0.15s |
| 26.3s | memory_search → **waterfall pass (embed+rerank)** | **13.33s** |
| 39.7s | chat qwen3.6:35b (cut off at budget) | 12.0s |

Two `index.search` rerank passes at ~14s + ~13s, plus slow chat calls, blew the budget.

## 2. Hypotheses falsified

Each was tested with a controlled probe; all were rejected:

| Hypothesis | Test | Result |
|---|---|---|
| Reranker cold-start curve | fresh `pkill`+relaunch, time first calls | first call **1.23s**, all ~1.1s — no curve. TEI `/health` only returns 200 once the model is GPU-resident |
| Idle-eviction | warm, idle 90s / 240s, re-probe | still ~1.1–1.3s — no re-colding |
| Multiple evals running concurrently | timestamps of all eval runs in the window | none overlapped; prior eval finished ~90s before W11.C started |
| In-eval rerank concurrency | trace span timeline | all ops sequential (async tools do blocking `httpx.post`, serializing on the event loop) |
| ollama↔TEI co-residency / memory pressure | rerank latency with 35B unloaded vs idle-resident vs **actively generating** | **0.94 / 0.93 / 0.95s** — identical. On 128 GB there is no eviction; Metal shares the GPU fine. TEI resident set ≈ 5.2 GB (reranker 3.62 + embedder 1.53) |

## 3. Root cause: payload size

The cross-encoder runs one transformer forward pass per `(query, text)` pair, so
latency scales with `candidate_count × tokens_per_candidate`. Sweep at 50 candidates
(the batch cap), warm, isolated:

| 50 texts × | ~tokens total | latency |
|---|---|---|
| 300 chars | 3.7K | 1.16s |
| 1000 chars | 12.5K | 3.98s |
| 2000 chars | 25K | 9.57s |
| 4000 chars | 50K | 20.4s |
| 8000 chars | 100K | 47.0s |

The 6/9 waterfall passes (14.87s / 13.33s) match ~50 real-memory candidates at
~2.5–3 KB each. `_fetch_reranker_texts` sent full chunk content untruncated; live-DB
chunks reach p90 = 2417 / max = 2700 chars. The "couldn't reproduce" episodes earlier
in the investigation used 300-char probe texts (1.1s) — the wrong payload size.

## 4. Chunk-size distribution (live DB, 95 chunks)

| stat | chars | vs. 512 budget |
|---|---|---|
| mean | 923 | 0.6× (below average) |
| **median** | **168** | **3.0× (generous)** |
| p75 | 1784 | 0.3× |
| p90 | 2417 | 0.2× |
| max | 2700 | 0.2× |

Right-skewed: mean (923) ≫ median (168), inflated by a minority of large chunks (partly
eval-fixture contamination). **54% of chunks (51/95) are ≤ 512 → never truncated.** The
46% that exceed it are the long tail.

## 5. Calibration: 512 vs 768 vs 1024

Clean run, full-spectrum pool (50 chunks spanning the size distribution, avg 923 chars),
warm, no concurrent load. Quality = fidelity to the untruncated reranker (no relevance
labels needed; full-content rerank is the reference ordering).

| Budget | Speed (median/6) | Quality vs full | Tail recall (match >1024c) |
|---|---|---|---|
| **512c** | **2.63s** | top-1 100% · top-5 97% | 0/4 |
| 768c | 3.29s | top-1 100% · top-5 97% | 0/4 |
| 1024c | 4.42s | top-1 100% · top-5 98% | 0/4 |
| full (ref) | 14.50s | — (gold) | 1/4 |

**512 is Pareto-optimal.** Across 512/768/1024 quality is flat (100% top-1, 97–98%
top-5), so 768/1024 only add latency. The one degraded case — an exact keyword buried
past the budget in a long chunk — is **not** recovered by 768/1024 (0/4 at all three)
and barely by full fidelity (1/4); these were near-duplicate synthetic fixtures that
confuse the cross-encoder regardless of input length. Crucially, FTS5 still recalls
those chunks into the candidate pool, so it is a rerank-ordering nuance, not lost search.

An earlier adversarial probe (50 longest near-duplicate chunks + tail-only-token
queries) showed large rank drops (1→36) and was the basis for an interim "non-trivial
loss" reading; that setup was doubly pathological (the `checksum` query was rank 8 even
at full fidelity) and is superseded by the representative measurement above.

## 6. Verdict

Default `rerank_text_char_budget = 512`. For the agent's actual workload — semantic
memory recall — the quality loss is negligible while latency drops ~7×. End-to-end
re-run of `eval_multistep_plan` confirmed W11.C's `index.search` fell from ~14s to
1.3–2.4s with the case scoring 10/10 (both sources synthesized). The budget is
config-plumbed (`CO_MEMORY_RERANK_TEXT_CHAR_BUDGET`) for atypical corpora.

Not the right lever for the buried-keyword case: candidate-count reduction (hurts
recall) or a larger budget (latency, marginal). A future option is query-aware
windowing (rerank the window around the match rather than the head), out of scope here.
