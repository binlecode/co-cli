---
title: Personality Evals
nav_order: 4
---

# Personality Evals

## 1. What & How

Two evals validate that the personality system produces measurably distinct, consistent output across roles. Both run the real agent against golden JSONL cases — no mocks, no stubs. Heuristic check functions score natural language responses; majority-vote across N runs smooths LLM non-determinism; an absolute gate enforces the quality threshold.

```
golden JSONL cases  (p2 / p3)
  │
  ├── personality CoDeps    ← make_eval_deps(personality=role)
  ├── agent.run()           ← real agent, real LLM, 2048 token cap
  │
  ├── P2: single-turn       score response against checks
  └── P3: multi-turn        score each turn; run passes only if all turns pass
          │
          ├── check functions  → list of failure descriptions (or none)
          ├── majority-vote    → CaseResult.majority_pass
          ├── per-personality  → dim_stats (accuracy per role)
          └── absolute gate    → exit 0 (PASS) or 1 (FAIL)
                │
                ├── {stem}-data.json    detailed results per run
                └── {stem}-result.md   human-readable report
```

**P2 (adherence)** — single-turn: does a single response exhibit the role's surface characteristics?
**P3 (cross-turn)** — multi-turn: does personality hold across a 3-turn conversation as history grows?

P3 fills the gap P2 cannot cover: it validates that `@agent.system_prompt` fires on every `agent.run()` call and that model behavior does not drift as conversation history accumulates.

---

## 2. Core Logic

### Scoring pipeline

Both evals share the same check dispatch table and majority-vote mechanism.

```
for each case:
    for run in range(N):
        response = agent.run(prompt, deps=personality_deps)
        if DeferredToolRequests → fail (prompts should elicit text only)
        failures = score_response(text, checks)
        RunResult(passed = len(failures) == 0)

    CaseResult.majority_pass = pass_count > len(valid_runs) / 2
    transient errors (rate limit, timeout, connection) → excluded from vote

dim_stats = per-personality pass rate across all scorable cases
absolute gate: overall_accuracy >= threshold (default 0.80) → exit 0 else 1
```

Transient errors are detected by pattern-matching the exception message (`rate limit`, `429`, `timeout`, `connection`, `temporarily unavailable`) and excluded from the vote — they do not count as failures.

### Check types

Six heuristic check functions. Each returns `None` (pass) or a failure description string.

| Check type | Params | Passes when |
|------------|--------|-------------|
| `max_sentences` | `n: int` | response has ≤ n sentences |
| `min_sentences` | `n: int` | response has ≥ n sentences |
| `forbidden` | `phrases: list[str]` | none of the phrases appear (case-insensitive, strips markdown `*_`) |
| `required_any` | `phrases: list[str]` | at least one phrase appears (case-insensitive, strips markdown `*_`) |
| `no_preamble` | `phrases: list[str]` | response does not start with any phrase |
| `has_question` | *(none)* | response contains `?` |

Sentence counting strips code blocks before splitting on `.!?` followed by whitespace. For fragment-style responses (no sentence-ending punctuation), counts non-empty lines instead.

Markdown emphasis (`*` and `_`) is stripped before `forbidden` and `required_any` matching so `"not *always* wrong"` does not bypass a forbidden check on `"always"`.

### P2 — Single-turn adherence

Case schema (JSONL): `id`, `personality`, `prompt`, `checks[]`.

```
for each case:
    deps = make_eval_deps(personality=case.personality)   ← cached per role
    result = agent.run(case.prompt, deps=deps,
                       usage_limits=UsageLimits(request_limit=2),
                       model_settings=make_eval_settings(max_tokens=2048))
    score against case.checks → RunResult
```

`request_limit=2` allows one text response; `max_tokens=2048` caps thinking budget (qwen3 thinking chains can reach 32K tokens — the cap keeps each case under ~60s locally).

20 cases across 4 roles (terse, finch, jeff, inquisitive) in `p2-personality_adherence.jsonl`.

### P3 — Multi-turn cross-turn consistency

Case schema (JSONL): `id`, `personality`, `turns[]`, `checks_per_turn[][]` — one checks list per turn.

```
for each case:
    history = []
    for turn_idx, (prompt, checks) in enumerate(turns × checks_per_turn):
        result = agent.run(prompt, message_history=history,
                           usage_limits=UsageLimits(request_limit=4))
        if DeferredToolRequests → fail turn; keep history unchanged, continue
        history = result.all_messages()     ← accumulates across turns
        score against checks → TurnRun(passed)
    RunResult.passed = all(turn.passed for turn in turn_runs)
```

`request_limit=4` accommodates up to 2 tool calls + text per turn (multi-turn conversations frequently trigger `recall_memory` before responding — `request_limit=2` was too tight).

If the model returns `DeferredToolRequests`, history is not advanced (pydantic-ai errors on unresolved tool calls in history). The turn is marked failed; subsequent turns continue with the prior history.

A complete run passes only when **all turns** pass their checks. This is intentionally strict — cross-turn consistency means every turn must hold, not just a majority.

### Shared infrastructure (`evals/_common.py`)

| Function | Purpose |
|----------|---------|
| `make_eval_deps(session_id, personality)` | Builds `CoDeps` with the specified personality role |
| `make_eval_settings(model_settings, max_tokens)` | Applies `max_tokens` cap on top of agent model settings |
| `detect_model_tag()` | Auto-detects model label from config for result tagging |

---

## 3. Config

Eval parameters are CLI flags — no settings.json involvement.

| Flag | Default | Description |
|------|---------|-------------|
| `--runs` | `3` | Runs per case (odd recommended for majority vote) |
| `--threshold` | `0.80` | Absolute pass rate gate (0.0–1.0) |
| `--personality` | *(all)* | Filter to a single role |
| `--case-id` | *(all)* | Filter to a single case ID |
| `--model-tag` | *(auto)* | Label for this run; auto-detected from config if omitted |
| `--save` | *(auto)* | Override output JSON path |

---

## 4. Files

| File | Purpose |
|------|---------|
| `evals/eval_personality_adherence.py` | P2 runner — single-turn adherence, heuristic checks, majority-vote, absolute gate |
| `evals/eval_personality_cross_turn.py` | P3 runner — multi-turn consistency, per-turn scoring, all-turns-must-pass |
| `evals/_common.py` | Shared: `make_eval_deps`, `make_eval_settings`, `detect_model_tag` |
| `evals/p2-personality_adherence.jsonl` | 20 golden cases across 4 roles (`id`, `personality`, `prompt`, `checks`) |
| `evals/p3-personality_cross_turn.jsonl` | Cross-turn cases (`id`, `personality`, `turns`, `checks_per_turn`) |
| `evals/p2-personality_adherence-data.json` | Detailed run results (auto-generated) |
| `evals/p2-personality_adherence-result.md` | Human-readable report (auto-generated) |
| `evals/p3-personality_cross_turn-data.json` | Detailed run results (auto-generated) |
| `evals/p3-personality_cross_turn-result.md` | Human-readable report (auto-generated) |
