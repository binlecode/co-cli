# Plan: Retire LLM Listwise Reranker — TEI-Only

**Task type:** feature-retirement (eval-gated)

**Sequence:** spin-out #2 of 3 (medium, eval-gated, breaking config — independent of
the other two and of the recall-path cleanup; tackle after #1 lands or in parallel
with #1).

**Status:** stub — Gate 1 not yet started. Drafted as a spin-out from
`docs/exec-plans/active/2026-05-02-090658-knowledge-recall-path-cleanup.md` (originally
TASK-19 / O2). Expand via `/orchestrate-plan reranker-retire-llm-listwise` when ready
to execute.

## Context

The recall path supports four reranker providers: TEI cross-encoder, Ollama LLM listwise,
Gemini LLM listwise, and "none" (pass-through). The LLM-listwise stack is fragile —
JSON-shape guessing on model output, silent fallback to identity order on parse failure
— and is dominated by TEI on every metric in the prior reranker comparison.

Bundling this retirement into the recall-path cleanup plan was rejected at Gate 1 because
it is a **feature retirement with a breaking config change**, not internal cleanup. It
deserves its own plan, its own eval gate, and its own CHANGELOG entry visible at
approval time.

## Problem & Outcome

**Problem:** Two reranker providers exist with low value (LLM listwise) but high
maintenance surface — fragile output parsing, separate config schema (`LlmModelSettings`,
`_RERANKER_DEFAULT_MODEL` map), bootstrap LLM probe (`check_reranker_llm`,
`_check_gemini_key`), separate eval (`eval_reranker_comparison.py`).

**Outcome:** Reranker layer is one provider deep (TEI) or off. Failure mode is observable
(TEI URL probe in bootstrap). No silent identity-order returns from JSON-parse failures.
LLM-listwise scaffolding deleted across `_reranker.py`, `memory_store.py`, `knowledge.py`,
`bootstrap/`, `tools/system/capabilities.py`, and `evals/`.

## Scope

**In scope:**
- `co_cli/memory/_reranker.py` — DELETE entire module.
- `co_cli/memory/memory_store.py` — remove `_llm_rerank`, `_call_reranker_llm`,
  `_generate_rerank_scores`, `_rerank_llm_fn` field + builder block in `__init__`,
  `_llm_reranker` field + provider-resolution branch. `_rerank_results` collapses to
  TEI-or-pass-through. Drop `from co_cli.memory._reranker import build_llm_reranker`
  import.
- `co_cli/config/knowledge.py` — remove `LlmModelSettings`, `_RERANKER_DEFAULT_MODEL`,
  `llm_reranker` field on `KnowledgeSettings`.
- `co_cli/bootstrap/check.py` — remove `check_reranker_llm`, `_check_gemini_key`. Update
  any caller list.
- `co_cli/bootstrap/core.py` — remove `config.knowledge.llm_reranker = None` line.
- `co_cli/tools/system/capabilities.py` — `_resolve_reranker` collapses to:
  `"tei" if cross_encoder_reranker_url else "none"`.
- `evals/eval_reranker_comparison.py` — rewrite as TEI-vs-none, or delete entirely.
  Decision goes in the REPORT.
- `CHANGELOG.md` — note breaking config change: `llm_reranker` setting in `settings.json`
  is removed; users must delete the field.

**Out of scope:**
- Backwards-compat shim for users with `llm_reranker: {...}` in `settings.json` —
  intentional hard break, documented in CHANGELOG.

## Behavioral Constraints

- **Eval gate is the prerequisite.** Run `uv run python evals/eval_reranker_comparison.py`
  (or read the most recent archived run) and confirm TEI ≥ LLM listwise on the headline
  metric. Capture in `docs/REPORT-reranker-tei-only-<date>.md`. **If TEI does not
  dominate, stop and revisit.**
- TEI must remain a soft-fail provider — if `cross_encoder_reranker_url` is unset or the
  endpoint is down, recall must still work (pass-through ordering).
- Pydantic validation for the existing `llm_reranker:` field becomes an error after this
  plan ships. Users must delete the field from `settings.json`. CHANGELOG must call this
  out at the top of the next release notes.

## High-Level Design

1. **Eval gate** — confirm TEI dominance, write REPORT.
2. **Module deletion** — `_reranker.py` removed; references stripped from `memory_store.py`.
3. **Config schema** — `LlmModelSettings` + `_RERANKER_DEFAULT_MODEL` + `llm_reranker`
   field deleted from `KnowledgeSettings`.
4. **Bootstrap probe** — LLM reranker checks deleted; TEI URL probe (already exists)
   remains.
5. **Capabilities resolver** — `_resolve_reranker` collapses to two-state.
6. **Eval rewrite** — `eval_reranker_comparison.py` becomes TEI-vs-none, or is deleted.
7. **CHANGELOG** — breaking config change announced.

## Implementation Plan

(To be expanded via `/orchestrate-plan` — this stub records intent and scope, not the
task breakdown.)

## Open Questions

- Are there any users in the wild (other than `binle`) with `llm_reranker:` set in their
  `settings.json`? If yes, consider a one-release soft-deprecate window before the hard
  removal. If solo-user (likely), hard-remove immediately.

## Final — Team Lead

Stub created. Awaiting expansion via `/orchestrate-plan reranker-retire-llm-listwise`
and Gate 1 review.
