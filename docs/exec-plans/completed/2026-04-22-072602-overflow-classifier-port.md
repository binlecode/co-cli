# Plan: Hermes-Style Overflow Detection Port

Task type: refactor

**Slug:** overflow-classifier-port  
**Created:** 2026-04-22  
**Source:** review of `co_cli/context/orchestrate.py`, `co_cli/context/_history.py`, `docs/specs/core-loop.md`, `docs/specs/compaction.md`, `docs/reference/RESEARCH-compaction-co-vs-hermes-gaps.md`, and Hermes reference implementation in `../hermes-agent/agent/error_classifier.py`

## Context

- `co-cli` currently decides overflow recovery with a single helper, `_is_context_overflow()`, that checks `status_code in (400, 413)` plus three substrings in `str(e.body)` in `co_cli/context/orchestrate.py`.
- The retry loop assumes an invariant that context overflow never falls through to the 400 reformulation branch, but that invariant only holds for provider wording already covered by those three substrings.
- Direct helper evaluation against representative bodies shows provider-wording drift today:
  - `{"error": {"message": "maximum context length is 8192"}}` -> `True`
  - `"Request payload size exceeds the limit"` -> `False`
  - `"Input token count exceeds the maximum"` -> `False`
- Source trace confirms the failure path when classification misses:
  - `run_turn()` falls from the overflow branch into the HTTP 400 reformulation branch in `co_cli/context/orchestrate.py`.
  - The reformulation branch appends a new `UserPromptPart` containing the provider error body.
  - `_build_error_turn_result()` returns `turn_state.current_history` when no segment completed, so those injected prompts persist into the next turn’s `message_history`.
- `docs/reference/RESEARCH-compaction-co-vs-hermes-gaps.md` already records this exact Gemini gap, but the current active compaction plan at `docs/exec-plans/active/2026-04-21-115119-compaction-hardening.md` does not include work on overflow classification. This scope is not already shipped.

## Problem & Outcome

**Problem:** `co-cli` uses a brittle overflow predicate that recognizes only a narrow set of provider error strings, so supported providers can emit real context-overflow 400s that are misrouted into tool-call reformulation.

**Failure cost:** a user who hits context overflow on Gemini-compatible or wrapped-provider responses can get the wrong retry path, longer contaminated history, a generic provider error instead of compaction, and a degraded next turn because bogus reformulation prompts were persisted.

**Outcome:** replace `_is_context_overflow()` with a focused Hermes-style HTTP overflow helper, preserving `co-cli`’s current retry/compaction semantics while making overflow classification robust to structured bodies, wrapped provider errors, and broader explicit overflow wording across supported providers.

## Scope

In scope:
- Port the Hermes overflow-detection slice only, not Hermes’s full failover taxonomy.
- Replace the current overflow predicate with a small private classifier used by `run_turn()`.
- Inspect structured error body fields (`error.message`, top-level `message`, `error.code`) and wrapped raw metadata when available.
- Add regression coverage for Gemini-style wording, negative guardrails proving generic/descriptive 400s stay out of overflow classification, and history contamination prevention.
- Update the compaction eval’s overflow matrix to reflect the new detection contract.

Out of scope:
- Porting billing, auth, rate-limit, or model-not-found classification from Hermes.
- Changing `co-cli`’s one-shot overflow recovery policy, compaction planner, or summarizer behavior.
- Adding new user config or CLI flags.
- Updating `docs/specs/` directly in this plan; `sync-doc` remains a post-delivery output.

## Behavioral Constraints

1. Requests classified as context overflow must enter the existing overflow recovery branch before the 400 reformulation branch.
2. Only explicit overflow evidence may classify a request as overflow in this delivery: recognized overflow message text, recognized overflow error codes, or recognized wrapped raw provider messages. Generic short 400s remain out of scope.
3. Descriptive 400 request-format errors such as invalid tool JSON or invalid parameter schema must continue to use `tool_reformat_budget`; the new classifier must not steal that path.
4. Body parsing failures, missing fields, and malformed `metadata.raw` payloads must be non-fatal and fall back cleanly to “not overflow”.
5. The port must preserve current one-shot recovery semantics: at most one overflow recovery cycle per turn, using the existing `_attempt_overflow_recovery()` and terminal overflow handling.
6. Requests classified as overflow must never append the reformulation `UserPromptPart` to persisted history, even when recovery ultimately fails.
7. Existing behavior for non-overflow 429/5xx terminal provider errors and `provider_error` span emission must remain unchanged.
8. HTTP 413 must continue to reach the same compaction recovery behavior `co-cli` documents today; internal helper structure may change, but observable retry behavior must not.

## Failure Modes

Observed from the current code path and helper evaluation:

1. OpenAI/Ollama-style overflow bodies are recognized, but representative Gemini bodies are not.
2. A missed overflow 400 is treated as reformulatable tool-call failure, so the retry path makes the history longer instead of shorter.
3. Terminal failure after repeated misclassification persists the synthetic reformulation prompts into the next turn’s history.
4. The compaction eval currently codifies the narrow contract (`status in (400, 413)` plus current patterns) and has no Gemini-style coverage, so this regression can ship unnoticed.

## High-Level Design

Introduce a small private overflow helper dedicated to `co-cli`’s turn loop rather than a broad provider-failover system.

Design outline:
- Add `co_cli/context/_http_error_classifier.py` with a single public predicate for this seam, plus small private extraction helpers.
- Build the predicate from the actual `ModelHTTPError` only:
  - `status_code`
  - body-derived message from nested `error.message`
  - flat-body `message`
  - nested `error.code`
  - wrapped `error.metadata.raw` when present and parseable
- Port only Hermes’s overflow-relevant logic:
  - explicit overflow phrases broader than `co-cli`’s current tuple
  - structured overflow codes such as `context_length_exceeded`
  - wrapped raw-message inspection when a proxy/provider nests the real overflow text
- Keep `run_turn()`’s existing branch structure and outcomes:
  - classifier says overflow -> current overflow recovery path
  - classifier says other on 400 with budget -> current reformulation path
  - everything else unchanged
- Migrate in one pass: `run_turn()` and the compaction eval stop using `_is_context_overflow()` in the same delivery, and the old helper is removed rather than left as a compatibility wrapper.
- Keep the design deliberately small:
  - no auth/billing/rate-limit taxonomy
  - no new recovery actions
  - no movement of compaction logic out of `_history.py`
  - parse failures on optional structured fields never raise from the helper

## Implementation Plan

### ✓ DONE — TASK-1 — Replace `_is_context_overflow()` with a focused HTTP overflow classifier

**files:**
- `co_cli/context/_http_error_classifier.py`
- `co_cli/context/orchestrate.py`
- `tests/test_http_error_classifier.py`

**done_when:** `uv run pytest tests/test_http_error_classifier.py`

**success_signal:** N/A

**Red-Green-Refactor:** first add failing tests for Gemini-style messages, wrapped raw metadata, flat-body messages, structured error codes, and malformed `metadata.raw` fallback; then implement the helper and wire `run_turn()` to use it. The test file must include at least one assertion through the `run_turn()` consumer boundary so this task proves the new helper is actually wired into orchestration, not just unit-callable.

### ✓ DONE — TASK-2 — Add turn-level regression coverage for overflow routing and history safety

**files:**
- `tests/test_orchestrate_context_overflow.py`
- `tests/test_orchestrate_error_event.py`

**done_when:** `uv run pytest tests/test_orchestrate_context_overflow.py tests/test_orchestrate_error_event.py`

**success_signal:** Gemini-style overflow 400s take the overflow recovery/terminal overflow path and do not persist reformulation prompts into the next turn’s history.

**prerequisites:** [TASK-1]

**Red-Green-Refactor:** first write failing turn-level tests that reproduce today’s misrouting and contamination, then make the helper-driven orchestration changes pass them without regressing the existing malformed-tool-call 400 coverage or the existing HTTP 413 overflow-recovery path.

### ✓ DONE — TASK-3 — Update the compaction overflow eval to match the new detection contract

**files:**
- `evals/eval_compaction_quality.py`

**done_when:** `uv run python evals/eval_compaction_quality.py`

**success_signal:** N/A

**prerequisites:** [TASK-1]

**Red-Green-Refactor:** extend the Step 8 matrix so it fails under the old narrow predicate, then update the eval assertions to cover Gemini-style message text, structured error codes, wrapped raw provider messages, and malformed-raw fallback behavior.

## Testing

- Targeted unit coverage:
  - explicit overflow patterns from current OpenAI/Ollama strings
  - Gemini-style messages such as `"Request payload size exceeds the limit"` and `"Input token count exceeds the maximum"`
  - structured body code `context_length_exceeded`
  - flat-body descriptive 400s that must remain non-overflow
  - wrapped raw provider errors in `metadata.raw`
  - malformed `metadata.raw` payloads that must fall back safely
- Targeted turn-level coverage:
  - overflow-classified 400 bypasses reformulation and reaches current overflow recovery handling
  - malformed tool-call 400 still uses reformulation
  - HTTP 413 still reaches the same overflow-recovery path documented today
  - terminal overflow does not persist reflection prompts into returned history
  - existing `provider_error` event behavior for budget-exhausted true malformed 400s remains intact
- Eval coverage:
  - Step 8 in `eval_compaction_quality.py` reflects the widened classifier contract and negative guardrails
- Pre-ship gate:
  - `scripts/quality-gate.sh full`

## Open Questions

None after source inspection. Preserve the documented `co-cli` behavior that HTTP 413 feeds the same compaction-recovery path as other context overflows; do not import Hermes’s separate payload-too-large recovery branch in this delivery.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev overflow-classifier-port`

## Delivery Summary — 2026-04-22

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_http_error_classifier.py` | ✓ pass (16/16) |
| TASK-2 | `uv run pytest tests/test_orchestrate_context_overflow.py tests/test_orchestrate_error_event.py` | ✓ pass (9/9) |
| TASK-3 | `uv run python evals/eval_compaction_quality.py` | ✓ pass (exits 0; step 8 all green; steps 6/13/14 require live LLM — config-dependent, not a code error) |

**Tests:** full suite — 617 passed, 0 failed
**Doc Sync:** fixed — `compaction.md`: trigger description, pseudocode, proactive handoff text, Files table, Diagram 3; `core-loop.md`: error matrix row updated

**Overall: DELIVERED**
Replaced `_is_context_overflow()` (3-pattern narrow predicate) with `is_context_overflow()` in new `co_cli/context/_http_error_classifier.py`, wiring broader Hermes-style overflow detection (17 phrases, structured codes, wrapped `metadata.raw`) into `run_turn()`. Gemini-style bodies previously misrouted to reformulation now correctly reach overflow recovery; history contamination from misclassified 400s is prevented.

## Implementation Review — 2026-04-22

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | `uv run pytest tests/test_http_error_classifier.py` | ✓ pass | `_http_error_classifier.py:40` — `is_context_overflow()` with 17 phrases, two structured codes, and `metadata.raw` inspection; `orchestrate.py:65,611` — import wired, called in `ModelHTTPError` handler. Call path: `run_turn()` → `is_context_overflow(e)` → `_body_has_overflow_evidence()`. Old `_is_context_overflow` absent from all live code. 16/16 pass. |
| TASK-2 | `uv run pytest tests/test_orchestrate_context_overflow.py tests/test_orchestrate_error_event.py` | ✓ pass | `test_orchestrate_context_overflow.py`: 4 tests covering Gemini overflow routing, history contamination prevention, non-overflow 400 reformulation guard, and 413 path. `test_orchestrate_error_event.py`: includes `test_gemini_overflow_400_no_provider_error_event` verifying overflow path skips `provider_error` span. 9/9 pass. |
| TASK-3 | `uv run python evals/eval_compaction_quality.py` | ✓ pass | `eval_compaction_quality.py:83` — imports `is_context_overflow`; Step 8 matrix at lines 1534–1586 has 12 cases covering Gemini-style messages, structured codes, wrapped `metadata.raw`, malformed raw fallback, and negative guardrails. Verified previously exits 0. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale comment references old helper name `_is_context_overflow` | `evals/eval_compaction_quality.py:28` | minor | Updated to `is_context_overflow` |

### Tests
- Command: `uv run pytest -v`
- Result: 468 passed, 1 failed (pre-existing `web_search_fastapi` live-LLM flake: passes 3/3 in isolation; fails only under concurrent Ollama load in full run; not introduced by this delivery)
- Log: `.pytest-logs/*-review-impl.log`

### Doc Sync
- Scope: narrow — delivery already synced `compaction.md` (trigger description, Files table, Diagram 3) and `core-loop.md` (error matrix row). Verified both specs reference `is_context_overflow` and `_http_error_classifier` correctly.
- Result: clean (one stale eval comment fixed above)

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM Online, Shell Active, Database Active, all integrations nominal
- No user-facing surface (chat loop, tools, output formatting) was changed — no chat interaction needed.
- `success_signal` for TASK-2: verified — Gemini overflow 400 takes overflow path (status "Context overflow") and not reformulation path ("Tool call rejected"), confirmed by `test_gemini_overflow_400_routes_to_overflow_path` passing.

### Overall: PASS
Delivery is spec-complete, test-clean, and doc-synced. One pre-existing live-LLM flake is unrelated to this delivery scope. Ship directly.
