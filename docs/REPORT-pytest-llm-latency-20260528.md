# REPORT: pytest LLM-call latency analysis — 2026-05-28

**Date:** 2026-05-28
**Source:** `~/.co-cli/logs/co-cli-spans.jsonl` (structured-log spans, kind=`model`), aggregated over the day's pytest runs
**Trigger:** Investigation into why the full `uv run pytest -v` suite takes ~10 minutes (597s) to run 625 tests
**Backend:** Ollama serving `qwen3.6:35b-a3b-agentic` (Qwen 3.6, 35B-A3B MoE — Active 3B params)
**Hardware:** Apple Silicon (M-series) local inference

---

## 1. Executive Summary

The pytest suite's ~10-minute runtime is dominated by ~15 functional/integration tests that hit a real Ollama model end-to-end (per `feedback_eval_real_world_data.md` — no mocks). The remaining ~610 tests run sub-second each.

**The single biggest cost is cold KV-cache prefill, not generation.** Each test fixture builds a fresh `CoDeps` and agent, so the first model call in a test pays full prefill of a ~13.4k-token prompt — taking **30–40 seconds** when llama.cpp's KV cache cannot reuse the previous request's prefix. Subsequent calls within the same test (tool-call resume loops) ride the warm cache at ~2 seconds.

Three orthogonal levers govern call latency:

1. **Cold prefill** — bimodal: ~330 tok/s (cache miss) vs. ~6,000 tok/s (cache hit). 20× difference for the same input size.
2. **Generation rate** — bimodal: ~70 tok/s (no reasoning) vs. ~15 tok/s effective (reasoning mode, dragged by hidden thinking tokens).
3. **Input prompt size** — median 13.4k tokens, of which the system prompt is only ~7k. The rest is tool schemas + per-turn data.

The prompt-static-trim plan (shipped 2026-05-28) stabilizes ~6,727 tokens of the static prefix across turns within a session — a real win for interactive `co chat` use, but **invisible to pytest** because per-test fixtures break cache continuity at the test boundary.

---

## 2. Methodology

### 2.1 Data source

The structured-log span file at `~/.co-cli/logs/co-cli-spans.jsonl` is a JSON-lines append log written by `co_cli/observability/file_logging.py`. Each `kind=model` record carries:

| Field | Meaning |
|---|---|
| `duration_ms` | wall-clock duration of the chat-completion call |
| `attributes["co.model.name"]` | model identifier (`qwen3.6:35b-a3b-agentic`) |
| `attributes["co.model.tokens.input"]` | input token count (system prompt + tool schemas + history + user msg) |
| `attributes["co.model.tokens.output"]` | output token count (visible text — does NOT include reasoning/thinking tokens) |
| `attributes["co.model.finish_reason"]` | `stop`, `tool_call`, etc. |
| `attributes["co.model.input"]` / `["co.model.output"]` | full request/response payloads (truncated where spilled) |

The data covers **today's pytest runs** filtered by `ts.startswith("2026-05-28")`:
- The exploratory single-file runs at the start of review-impl
- The first full-suite run that failed at `test_typed_ahead_enqueues_and_drains_under_real_turn`
- The second full-suite run that failed at `test_build_status_snapshot_empty_session_path_produces_dash`
- The final clean full-suite run (625 passed in 597.67s)

Total: **121 model-call spans** over the day; spans logged from 3 separate full-suite invocations + several scoped runs.

### 2.2 Methodology caveats

- **The `out_tok` count excludes reasoning tokens.** Qwen 3.6's `<think>...</think>` chain-of-thought emits invisible tokens that consume generation time but never appear in `co.model.tokens.output`. This systematically under-counts output for reasoning-enabled calls — visible as a depressed "effective tok/s" rate for those calls.
- **No direct prefill-vs-generation split is logged.** We infer it indirectly by comparing same-prompt-size calls with different finish modes (short-output calls are prefill-dominated; long-output calls expose generation rate). Per-call timestamps for first-token-emitted would be needed for a clean split.
- **Spans aggregate across multiple pytest runs today.** The 1,542s total wall time exceeds the 597s of the final clean run — earlier runs hit some of the same tests redundantly. We do not deduplicate by trace_id because the goal is per-call performance characterization, not run-time accounting.

### 2.3 Analysis script

The full analysis pipeline lives at `tmp/llm_perf.py` (scratch — not committed). It:
1. Streams the spans JSONL, filters to today's `kind=model` records, skips `function::stream_fn` internal wrappers (32 records, all ~0ms framework overhead).
2. Produces input-token, output-token, and effective-throughput distributions with percentiles.
3. Buckets calls by output size: `out_tok ≤ 50` (prefill-bound, exposes prefill rate), `out_tok > 200` (generation-bound, exposes generation rate after subtracting estimated prefill).
4. Applies a linear separation `dur ≈ prefill(in_tok) + generation(out_tok)` using an assumed warm-prefill rate of 1,800 tok/s to estimate per-component cost.

---

## 3. Raw data — call distribution

### 3.1 Headline numbers

```
Real model calls today (excl. stream_fn wrappers): 121
Sum of all durations:                              1,542 s
Sum of input tokens:                               1,199,085
Sum of output tokens:                              10,793
Input:Output ratio:                                ~111:1
```

For comparison, the April 15 baseline (`REPORT-llm-runtime-statistics.md`) showed I:O ratio of ~45:1 on `qwen3.5:35b-a3b-think`. The current ~111:1 reflects that test fixtures use small user prompts and short expected outputs — they exercise prompt-handling and tool-routing logic, not long-form generation.

### 3.2 Input-token distribution (per call)

```
min  : 50
p25  : 50
p50  : 13,254
p75  : 13,423
p90  : 13,851
max  : 14,595
```

Bimodal distribution:
- **Tiny calls (50 tokens)** — these are likely warm-up pings (`ensure_ollama_warm`) or healthchecks; ~20 calls in this band.
- **Real test calls (13.2k–14.6k tokens)** — every functional test call clusters tightly around 13.4k input tokens. The narrow range (13.2k–13.9k for the bulk of calls) confirms the system prompt + tool schemas are the dominant component; the user message + accumulated history varies by only a few hundred tokens across tests.

**Decomposition of the 13.4k input tokens:**
- System prompt (rules + soul + per-turn instructions assembled): ~7,113 tokens (per the prompt-static-trim plan's measurement)
- Tool schemas (JSON schemas for every registered tool): estimated **~5,500–6,000 tokens**
- User message + conversation history: estimated **~300–500 tokens** per call
- Tool result content (when present): up to spill threshold (~4k chars ≈ 1k tokens)

The tool-schema share is striking — it's nearly as large as the entire system prompt. Every turn re-serializes every registered tool's full JSON schema; this content is byte-stable across turns (tool registry is immutable mid-session) but still occupies prefix bytes that must hit the KV cache to avoid re-prefill.

### 3.3 Output-token distribution (per call)

```
min  : 1
p25  : 12
p50  : 33
p75  : 112
p90  : 320
max  : 535
```

Most calls emit < 100 tokens. The tail (max 535) is from tests that exercise the agent's response-formatting paths (e.g., `test_summarize_messages_from_scratch_returns_structured_text`). This matters because **output_tokens excludes reasoning tokens** — a call that reports `out_tok=33` may have produced 200–400 internal `<think>` tokens that account for most of its duration.

### 3.4 Duration buckets

```
   0–  1s:  32 calls
   1–  5s:  21 calls
   5– 10s:  13 calls
  10– 20s:  22 calls
  20– 30s:  14 calls
  30– 60s:  19 calls
  60+  s:   0 calls
```

Two clusters:
- **Sub-5s (53 calls, 44%)** — warm-up calls + KV-cache-warm follow-up calls within a test.
- **20–60s (33 calls, 27%)** — cold-prefill calls (first call after fixture setup) + reasoning-heavy generation calls.

The middle band (5–20s) is mixed: medium-output calls without reasoning, or short-output calls that still hit some cache-miss cost.

---

## 4. The bimodal prefill phenomenon

### 4.1 Same input, very different duration

The clearest signal in the data is what happens when you sort short-output calls (`out_tok ≤ 50`) by input size — calls with **near-identical input** (13.2k tokens, ±50) span an order of magnitude in duration:

| in_tok | out_tok | duration | implied prefill rate | cluster |
|---|---|---|---|---|
| 13,185 | 22 | **39.59 s** | **333 tok/s** | COLD |
| 13,185 | 30 | **39.57 s** | **333 tok/s** | COLD |
| 13,190 | 50 | 25.50 s | 517 tok/s | COLD |
| 13,190 | 48 | 27.15 s | 486 tok/s | COLD |
| 13,190 | 48 | 37.20 s | 355 tok/s | COLD |
| 13,190 | 48 | 33.81 s | 390 tok/s | COLD |
| 13,190 | 50 | 34.33 s | 384 tok/s | COLD |
| 13,200 | 24 | **1.60 s** | **8,227 tok/s** | WARM |
| 13,200 | 33 | 1.86 s | 7,084 tok/s | WARM |
| 13,205 | 48 | 2.10 s | 6,287 tok/s | WARM |
| 13,205 | 46 | 2.20 s | 6,007 tok/s | WARM |
| 13,205 | 50 | 2.14 s | 6,182 tok/s | WARM |
| 13,205 | 49 | 2.32 s | 5,685 tok/s | WARM |
| 13,254 | 38 | 5.04 s | 2,630 tok/s | partial-hit |
| 13,259 | 21 | 3.72 s | 3,562 tok/s | partial-hit |

The "implied prefill rate" column treats the whole call duration as prefill (overestimates prefill cost slightly because generation isn't zero, but for `out_tok ≤ 50` the generation contribution is small — at 60 tok/s that's < 1s).

**The 20× ratio between 333 tok/s and 8,227 tok/s on the same model and same input length is not a model performance characteristic — it's a KV cache lookup.** llama.cpp's autoregressive decoder maintains an implicit cache of the previous request's prefix; when the next request's leading bytes match exactly, the cache replays those positions instead of recomputing them. Any byte difference at position N causes re-prefill from position N forward.

### 4.2 Partial-hit cluster

The 2,630 and 3,562 tok/s entries are partial hits — leading prefix matches, then diverges partway through. This happens when:
- Two requests share the same static system prompt + tool schemas but differ in conversation history (different `session_id`, different user messages, different tool-call sequences).
- The cache replays the matched prefix and re-prefills only the divergent tail.

The math: if 70% of the 13.2k prefix matched and the remaining 30% re-prefilled at 333 tok/s while the matched 70% replayed at ~10k tok/s effective, you'd see an aggregate of ~2,500–3,500 tok/s. The data fits.

### 4.3 Why this matters for pytest

Every pytest fixture builds a fresh `CoDeps` via `_make_deps(tmp_path)`. The system prompt + tool schemas are byte-stable across fixtures (same TARS personality, same SETTINGS_NO_MCP toolset). But:
- **`session_id`** is fresh per fixture (it's a UUID). If `session_id` appears anywhere in the prompt, every fixture diverges at that token.
- **User messages** differ per test.
- **`current_time_prompt`** changes per call (current time string).
- **Conversation history accumulates** as the test progresses through multiple model calls.

The first model call in each test pays the cold prefill. Subsequent calls within that test ride the warm cache (or partial-hit cache) for the static portion and pay tail re-prefill for the growing history.

---

## 5. Generation-rate analysis

### 5.1 Same input, different output behavior

Filtering to calls with `out_tok > 200` (where generation cost is non-trivial), and applying the linear split `dur = prefill_est + gen_est` with prefill_est = `in_tok / 1800`:

| in_tok | out_tok | duration | est. prefill | est. gen | gen rate |
|---|---|---|---|---|---|
| 13,791 | 442 | 12.75 s | 7.7 s | **5.1 s** | **87 tok/s** ← no reasoning |
| 13,973 | 402 | 13.82 s | 7.8 s | 6.1 s | 66 tok/s |
| 13,791 | 535 | 15.43 s | 7.7 s | 7.8 s | 69 tok/s |
| 14,595 | 413 | 18.69 s | 8.1 s | 10.6 s | 39 tok/s |
| 13,971 | 398 | 18.83 s | 7.8 s | 11.1 s | 36 tok/s |
| 13,851 | 468 | 22.72 s | 7.7 s | 15.0 s | 31 tok/s |
| 13,713 | 363 | 33.82 s | 7.6 s | 26.2 s | **14 tok/s** ← heavy reasoning |
| 13,497 | 397 | 33.55 s | 7.5 s | 26.0 s | 15 tok/s |
| 13,969 | 437 | 35.41 s | 7.8 s | 27.6 s | 16 tok/s |
| 13,851 | 487 | 34.62 s | 7.7 s | 26.9 s | 18 tok/s |

Two clusters:
- **High rate (60–90 tok/s)** — non-reasoning calls. The model emits ~400–500 visible tokens at native MoE speed.
- **Low rate (14–40 tok/s)** — reasoning calls. The visible output count is unchanged, but the model emits substantial hidden `<think>` content first, so wall-clock per *visible* token rises.

A 14 tok/s effective rate on a model that achieves 87 tok/s for visible-only generation implies the hidden thinking content is **~6× the visible output**. For an `out_tok = 397` call running at 26 seconds of generation, the model likely generated:
- Visible: 397 tokens
- Hidden thinking: ~2,000 tokens (estimated, 26s × 90 native tok/s − 397 visible)

This is consistent with reasoning models' typical behavior — extensive chain-of-thought before a short answer.

### 5.2 Worst-case effective throughput

The slowest call observed today:
```
in=13,409  out=14  dur=34.2s  →  0.4 tok/s effective output rate
```

This call had only 14 visible output tokens but took 34.2s. After estimated prefill (7.4s), generation is 26.8s. Producing 14 visible tokens in 26.8s implies ~2,400 hidden thinking tokens at ~90 tok/s native rate. This is a reasoning call that ground out almost no visible answer — probably a "decide which tool to call" hop where the model deliberated extensively before emitting a short tool-call directive.

---

## 6. Aggregate decomposition — where the time goes

Applying the linear split across all 121 calls:

```
Total call wall time:    1,542.4 s
Total input tokens:      1,199,085
Total output tokens:     10,793

If warm prefill = 1,800 tok/s:
  Implied prefill time:   666.2 s  (43% of total)
  Implied generation:     876.3 s  (57% of total)
  → effective gen rate:   12.3 tok/s
```

But this assumes ALL prefill is warm at 1,800 tok/s. The bimodal data says ~half of cold calls run at 333 tok/s (5× slower). Adjusting:
- 60 cold-prefill calls × 7.5s prefill at 333 tok/s ≈ 450s
- 60 warm-prefill calls × 1.5s prefill at 8,000 tok/s ≈ 90s
- Total prefill: **~540s (35% of total)**
- Remaining generation: ~1,000s (65%) at 11 tok/s effective (reasoning-dragged)

Either way: **prefill is at minimum 35% of total LLM wall time across the suite**, and individual cold-prefill calls account for 30–40s spikes that dominate per-test latency.

---

## 7. Why the prompt-static-trim plan is invisible to pytest

The prompt-static-trim plan (shipped this session, completed `docs/exec-plans/active/2026-05-27-232827-prompt-static-trim.md`) moves the skill manifest and tool-category awareness off the static prefix and onto per-turn dynamic instructions. The net effect:

- **Static prefix becomes byte-stable across turns within a session**, regardless of `skill_index` / `tool_index` mutations.
- Mid-session `skill_manage(action='create')` no longer invalidates the cached ~6,727-token prefix.

This is a **real win for interactive `co chat`** — a user creating a new skill mid-conversation pays 0 bytes of prefix invalidation for subsequent turns.

But pytest sees none of this because:

1. **Every test starts a fresh fixture** with a new `CoDeps`. The cache from the previous test's last call doesn't matter — the new test's first call's prefix is different (different `session_id`, different first-message context).
2. **Tests don't mutate `skill_index` mid-run**, so the pre-trim variability the plan removes was never exercised by tests anyway.
3. **Per-turn instructions (current_time_prompt, manifest, tool-category awareness) now appear post-static** — they were inside the cached prefix before; now they're outside. Each test's first call still pays the same total prefill cost; the cache hit/miss boundary just moved.

The plan's measurable value is in real sessions where state evolves. pytest doesn't simulate that.

---

## 8. Where the leverage is — recommendations

In order of expected impact:

### 8.1 Tool-schema size (highest leverage)

Tool schemas account for an estimated 5–6k tokens of every 13.4k-token prompt — **~40% of the input prefix**. Cutting this in half would drop median prompt size to ~10.5k tokens, with linear knock-on to prefill cost.

Concrete moves to investigate:
- **Trim docstrings on tools.** Every tool's full docstring is serialized into its JSON schema description. Some tools have docstrings hundreds of tokens long that the model doesn't strictly need at every turn — the per-call hint is the function signature; the docstring is reference material.
- **Audit which tools are actually used.** The native toolset (`build_native_toolset`) registers every implemented tool. Tests using `SETTINGS_NO_MCP` skip MCP, but native tools (memory, session, files, shell, code execution, sub-agents, background tasks) all stay registered even when the test only exercises one path.
- **Consider deferred-only registration for less-common tools.** The `VisibilityPolicyEnum.DEFERRED` machinery already exists for this — currently used for `task_start`, `code_execute`, etc. Tools registered as `DEFERRED` are not in the always-visible schema list; the model discovers them through `search_tools`. Expanding this set would shrink the always-on schema.

**Validation step:** measure `len(json.dumps(toolset.schema))` for the current default toolset. The prompt-static-trim plan's `tmp/measure_prompt.py` script could be extended to print this.

### 8.2 Reasoning-mode tests (medium leverage)

The reasoning-vs-non-reasoning split shows reasoning calls cost 2–6× more wall-clock per visible token. For tests that only assert on observable agent behavior (e.g., "tool X was called", "response contained string Y"), reasoning is overhead — the model would arrive at the same observable outcome via a non-reasoning path.

Concrete move:
- Audit which tests use `reasoning_model_settings()` vs `noreason_model_settings()`. Tests that don't specifically exercise the reasoning path could be downgraded to non-reasoning, cutting their LLM cost roughly in half.
- The memory note `feedback_tests_use_config_model_settings.md` already enforces using the helper functions; this is about choosing between them per test.

**Caveat:** `feedback_call_timeout_no_cold_start.md` and `feedback_llm_call_timing.md` both warn against papering over LLM perf problems with timeouts — the right fix is in the prompt / model settings, not the test budget.

### 8.3 Per-module warm session (low leverage, high risk)

A pytest fixture scoped per-module instead of per-test would let multiple tests in the same module share an Ollama session, amortizing the first-call cold prefill.

This is **not recommended** because:
- It violates per-test isolation (one test's failure could pollute the next test's state).
- It would mask the very prefix-invalidation cost the prompt-static-trim plan addressed.
- The integration tests already require fresh fixtures to validate startup invariants (`build_orchestrator` initial state, `create_deps` lifecycle).

The right place for warm-session optimization is the production REPL, not the test fixture.

### 8.4 Fail-fast pytest (already in place)

The suite uses `--maxfail=1` (per the failure-stop behavior seen during this review). This caps wall-clock cost when something breaks — a hard failure aborts the run at the first red instead of churning through 600 more tests. Memory note `Test runs (orchestrate-dev and general)` documents this as policy:
- Fail fast: run pytest with `-x`
- Fix the failing test before resuming (`pytest -x --lf` to rerun from last failure)
- Never run the full suite past a known failure

This is the right operational behavior given the cost profile documented above — every test minute matters when the floor is ~10 minutes.

---

## 9. Reproducing this analysis

The full pipeline is in `tmp/llm_perf.py` (scratch, not committed). Core steps:

```python
import json
from pathlib import Path

log = Path.home() / ".co-cli/logs/co-cli-spans.jsonl"
today = "2026-05-28"

calls = []
with log.open() as f:
    for line in f:
        rec = json.loads(line)
        if rec.get("kind") != "model" or not rec.get("ts", "").startswith(today):
            continue
        if rec.get("name", "").startswith("function::"):
            continue  # skip stream_fn wrapper spans
        a = rec.get("attributes", {})
        calls.append({
            "dur_ms": rec.get("duration_ms", 0),
            "in_tok": a.get("co.model.tokens.input", 0),
            "out_tok": a.get("co.model.tokens.output", 0),
            "finish": a.get("co.model.finish_reason", "?"),
        })

# Bucket prefill-bound calls (out_tok <= 50, in_tok > 1000)
prefill_bucket = [c for c in calls if c["out_tok"] <= 50 and c["in_tok"] > 1000]
for c in sorted(prefill_bucket, key=lambda c: c["in_tok"]):
    rate = c["in_tok"] / (c["dur_ms"] / 1000)
    print(f"in={c['in_tok']}  out={c['out_tok']}  dur={c['dur_ms']/1000:.2f}s  rate={rate:.0f} tok/s")
```

The same span file feeds `co tail` and `co trace <trace_id>` for live exploration:

```bash
uv run co tail --detail       # stream new spans with per-record input/output
uv run co trace <trace_id>    # snapshot tree of one trace
```

---

## 10. Open questions

1. **Why is prefill so slow at 333 tok/s when the model can sustain 6,000+ tok/s warm?** This is a 20× difference for an MoE model that should have flat prefill cost. Hypothesis: cold prefill triggers expert routing initialization that the warm cache skips entirely. Verifying would require Ollama-side perf counters (which aren't exposed via the chat API).

2. **What fraction of the 5–6k token tool-schema budget is essential vs. removable?** The current registration emits every tool's full schema unconditionally. A docstring-length audit (`for tool in toolset: print(tool.name, len(tool.schema['description']))`) would reveal top contributors.

3. **Is the `function::stream_fn` span informative?** 32 of 121 model spans are wrappers logged with name `function::stream_fn` and `co.model.name=function::stream_fn`. They have ~0ms duration and no token attributes — likely framework-level streaming-result spans inside pydantic-ai's adapter. We skip them in this analysis, but a deeper audit could confirm they're noise vs. signal.

4. **Could a per-session warm-up call (one cold prefill, then all warm) work as a pytest opt-in fixture?** Not for the full suite, but a per-module fixture (`@pytest.fixture(scope="module")`) that does one warm-up call before the module's first real test would amortize the cold prefill across N tests in that module. Trade-off: weakens isolation. Open as a future micro-optimization.

---

## 11. Related artifacts

- **Plan:** `docs/exec-plans/completed/2026-05-27-232827-prompt-static-trim.md` (after `/ship`) — moved skill manifest and tool-category awareness off the static prefix; addresses interactive `co chat` cache stability but invisible to pytest per §7.
- **Prior baseline:** `docs/REPORT-llm-runtime-statistics.md` (2026-04-15) — OTel-era baseline with 3,329 calls; relative metrics (I:O ratio, output character distribution) are comparable.
- **Cache-aware prefill model:** `docs/specs/prompt-assembly.md` §2.3 — describes static/dynamic split and the `cache_control` semantics for Anthropic; llama.cpp / Ollama follows the same byte-prefix-reuse contract without an explicit marker.
- **Memory policy:** `feedback_eval_real_world_data.md`, `feedback_call_timeout_no_cold_start.md`, `feedback_llm_call_timing.md`, `feedback_tests_use_config_model_settings.md` — collectively constrain the test-LLM contract: real data, real models, per-call timeouts that exclude cold start, model settings from config.

---

## 12. Conclusion

The 10-minute pytest runtime is a structural consequence of three compounding factors: large input prompts (~13.4k tokens, dominated by tool schemas), bimodal prefill cost (cold ≫ warm by 20×), and reasoning-mode generation overhead. Per-test fixture churn defeats KV-cache reuse across test boundaries, so each test pays at least one cold prefill at ~330 tok/s — about 30 seconds.

The prompt-static-trim plan addresses a related but distinct problem (in-session prefix mutation), with no measurable impact on pytest. Where pytest runtime can genuinely improve is upstream of the per-call cost — primarily by shrinking the tool-schema component of the prompt, and secondarily by auditing reasoning-mode tests for necessity.

The current behavior is not a regression; it's the expected cost of a real-LLM, real-data, real-fixture-isolation test policy. Anything faster than ~10 minutes for 625 tests would require giving up one of those three properties.
