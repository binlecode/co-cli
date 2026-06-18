# Summary-Fidelity Verify-and-Retry

## Context

co's compaction summarizer (`co_cli/context/summarization.py`) preserves critical
identifiers (file paths, error strings, line numbers, verbatim user corrections)
**by prompt instruction only** — the `## Critical Context` section says "preserve
exact values" and the `SUMMARY_CAP_OVERSHOOT_RATIO` guards against mid-section
truncation. The only post-summary guard today is the **no-progress size check**
in `compaction.py` (`_record_proactive_outcome` / `proactive_window_processor`
~`:692-706`): did the pass shrink the token count? That is a *quantity* axis. There
is no *fidelity* (content-survival) check.

Peer reference: openclaw's quality-audit retry loop
(`compaction-safeguard-quality.ts:220-246`) — it extracts identifiers via regex
from the source and, in strict mode, verifies every one appears in the summary,
re-running with a quality-feedback prompt if not. (See
`docs/reference/RESEARCH-summarization-prompting-peer-survey.md` §2.)

Current state is consistent and plannable:
- `summarize_messages` (`summarization.py:351-420`) is the single chokepoint —
  used by **both** the proactive window processor and `/compact`. It already
  computes `serialized = serialize_messages(...)` (the redacted source text),
  `budget`, and `settings`, then makes one `llm_call`.
- `serialize_messages` (`:270-311`) already applies `redact_text` to every part,
  so the source text the check would read is **already credential-redacted** —
  secrets are `[REDACTED]` before any extraction, so they can never be extracted
  as "identifiers."
- `eval_summarizer_fidelity.py` already measures contract compliance on the
  configured Ollama model over 3 synthesized transcripts (variant B verdict:
  COMPLIANT at PASS_THRESHOLD 0.8). It has deterministic scorers
  (`_paths_survive`, `_quote_is_verbatim`, `_tool_names_grounded`) we mirror.
- Summarizer **input** is redacted (`:396` serialized turns, `:403` prior summary);
  the **output** is returned raw from `llm_call` (`:415`). hermes also redacts
  output (`context_compressor.py:1402`); co does not.

## Problem & Outcome

**Problem.** When the summarizer drops a file path, error string, line number, or
verbatim user correction from the recap, that token is gone permanently —
compaction rewrites the live transcript in place and the original turns are
dropped (`compact_messages` assembles `head | marker | tail`; the dropped middle
is discarded). The agent can no longer cite the exact path/error and may redo
work or hallucinate. The fidelity eval's 0.8 threshold *tolerates* a 1-in-5
stochastic miss on the configured model — that tolerated miss, in an in-place
compactor where the dropped turns are unrecoverable, is the load-bearing
justification. (A future model swap, Ollama→Gemini uncalibrated, would widen the
tail — secondary motivation, not the primary reason.) Why not just raise the eval
threshold or strengthen the prompt instead? Prompt-only preservation (`_SUMMARIZE_PROMPT`
"preserve exact values") is exactly the status quo being backstopped, and the eval's 0.8
threshold governs the *CI verdict*, not runtime loss — neither closes the silent-loss
window on a live in-place compaction, so a runtime mechanism is the proportionate (not
redundant) response.

**Failure cost.** Silent, unrecoverable loss of an exact identifier (path / error
string / line ref / verbatim correction) inside an otherwise-plausible summary.
No signal today — the no-progress guard passes (the summary *did* shrink) and the
agent proceeds on a recap that quietly lost a load-bearing value.

**Outcome.** After the summarizer returns, deterministically check whether the
high-signal identifiers present in the (already-redacted) source survived into the
summary. If too many are missing, re-prompt the summarizer **exactly once** with
the missing items as feedback, then accept whichever result preserved more. The
guard is a content backstop orthogonal to the anti-thrash, no-progress, and
circuit-breaker mechanisms — it never falls back to a static marker and never
loops. Plus: redact the accepted summary text on the way out (output-side parity
with hermes) — this output redaction is independently justified defense-in-depth and
would be correct even if the verify-retry were never built; removing the fidelity guard
does not imply removing the redaction.

## Scope

**In:**
- A deterministic identifier extractor + survival check over the serialized source
  and the produced summary (no LLM judge).
- A single bounded retry inside `summarize_messages` with missing-identifier
  feedback; accept-best-of-two; degrade-safe if the retry raises.
- Output-side `redact_text` on the accepted summary.
- OTEL span attributes for extracted / missing / retry-fired / accepted-which.
- Unit tests for the pure check + the bounded-retry behavior; an extension to
  `eval_summarizer_fidelity.py` measuring retry lift on the real model.

**Out:**
- Any LLM-based fidelity judging (would compound cost and fight anti-thrash).
- Config knobs for the threshold/policy (constant + trace-tunable comment, matching
  `SUMMARY_CAP_OVERSHOOT_RATIO`'s style — no per-user policy surface).
- Touching the no-progress / anti-thrash / circuit-breaker logic in `compaction.py`.
- Chunking / multi-stage summarization (separate gap, not this plan).
- Specs (`docs/specs/compaction.md`) — updated by `sync-doc` post-delivery. This is
  observable runtime behavior (a content backstop + an extra conditional LLM call), not
  a doc tidy: at Gate 2, confirm `sync-doc` actually adds the fidelity-guard behavior and
  the +1-call-in-miss-case cost to `compaction.md`, not a no-op sync.

## Behavioral Constraints

- **Orthogonal to existing guards.** The fidelity guard must not read or write
  `consecutive_low_yield_proactive_compactions`, `compaction_skip_count`, the
  circuit-breaker state, or the no-progress escalation. It operates entirely inside
  `summarize_messages`, below `_gated_summarize_or_none`, and returns a string just
  as before. A fidelity miss is **not** a summarizer failure — it never increments
  the skip counter and never triggers a static-marker fallback.
- **At most one extra LLM call.** The retry fires at most once per compaction. Worst
  case is +1 summarizer call only when the first summary drops identifiers above
  threshold. No loop, no recursion.
- **Degrade-safe.** If the retry call raises (timeout, breaker), return the first
  (non-empty) summary — never lose a good-enough recap to a failed retry.
- **Redaction ordering.** Extract identifiers from the already-redacted `serialized`
  source; check membership against the **raw** summary; redact the chosen summary
  **last**, on return. Secrets are `[REDACTED]` in the source, so they are never
  extraction candidates and a redacted secret never reads as a "missing identifier"
  (no spurious retry).
- **Tolerance, not strict.** Unlike openclaw's strict "every identifier," co retries
  only when the missing fraction exceeds a constant threshold — over-strict matching
  causes retry-thrash on benign paraphrase. Threshold is a commented constant,
  tunable from traces.
- **Realtime-local philosophy preserved.** No provider-reported usage is read; the
  check is pure string membership over local text.

## High-Level Design

The verify-and-retry is a **shared private helper** `_verify_and_retry(deps,
serialized, first_summary, run_call)` where `run_call(feedback: str | None)` is a
thunk that performs one summarizer `llm_call` with identical settings/system-prompt
(the `feedback` arg appends the missing-identifier line to the task prompt). Both
`summarize_messages` (production) and the eval's `_summarize_with_template` invoke
the helper, passing their own `run_call` thunk — so the eval exercises the *real*
retry while keeping its A/B template lever (CD-M-1). The helper:

```
def _verify_and_retry(deps, serialized, first_summary, run_call):
    required = _extract_identifiers(serialized)              # set[str], high-signal
    if not required:                                         # zero-id short-circuit
        return first_summary                                 # _accept_better never called
    missing = required - _present_in(first_summary)
    if len(missing) / len(required) <= FIDELITY_MISS_THRESHOLD:
        return first_summary
    span: fidelity_retry_fired = True
    try:
        retry = await run_call(_feedback(missing))           # top-N missing only
        return _accept_better(first_summary, retry, required) # fewer-missing wins; tie→first
    except Exception:
        log + span(fidelity_retry_error); return first_summary   # degrade-safe
```

`summarize_messages` then wraps: `summary = await _verify_and_retry(...)` followed
by `return redact_text(summary, patterns)` (output-side redaction, last).

- `run_call` factors out the existing `llm_call(...)` invocation so the retry reuses
  identical settings/system-prompt **and the identical output cap** (the goal is
  preservation-priority, not more room). The feedback list is bounded to the top
  `FIDELITY_FEEDBACK_MAX_IDENTIFIERS` missing identifiers (a named constant beside
  `FIDELITY_MISS_THRESHOLD`) so a long list cannot itself blow the cap; retry cap pressure
  is surfaced as a span attribute. This is a refactor of the current single call into
  a thunk, not a new API on `summarize_messages` (no test-driven signature change).
- `_extract_identifiers` is conservative and high-signal: file/dir paths
  (`co_cli/...`, `*.py`), `path:line` refs, URLs, hex hashes, quoted `'…'`/`"…"`
  command/correction spans, and unquoted error-string tokens pinned to a concrete
  regex — `\b\w+(Error|Exception)\b` plus literal `Traceback` — so a free-floating
  error message named load-bearing in the Problem statement is captured, not only
  quoted/path-attached ones (the vague "Traceback-adjacent lines" framing is dropped:
  no window/anchor rule, not implementable). Mirrors the eval's `_paths_survive` / `_quote_is_verbatim`
  intent. Over-extraction → spurious retries (cost); under-extraction → misses — tuned
  conservative, trace-observable.
- `_present_in` is byte-substring membership (same as the eval's verbatim check).

## Tasks

### ✓ DONE TASK-1 — Deterministic identifier extractor + survival check
- **files:** `co_cli/context/summarization.py` (new private helpers
  `_extract_identifiers`, `_present_in`/survival check, `FIDELITY_MISS_THRESHOLD`)
- **done_when:** `uv run pytest tests/context/test_summary_fidelity_check.py` passes —
  given a source containing `co_cli/auth.py:42`, an **unquoted** error string, and a
  quoted correction, a summary that drops the path and error yields a missing-fraction
  over threshold (needs-retry True); a summary that keeps all yields needs-retry False;
  and a source with **zero** extractable identifiers yields needs-retry False (the
  short-circuit, so the retry never fires on identifier-free transcripts).
- **success_signal:** A summary that silently drops a file path or error string is
  detectably flagged as low-fidelity by a pure local check.
- **prerequisites:** none

### ✓ DONE TASK-2 — Shared `_verify_and_retry` helper + output redaction in `summarize_messages`
- **files:** `co_cli/context/summarization.py`
- **done_when:** `uv run pytest tests/context/test_summary_fidelity_retry.py` passes.
  The retry-behavior assertions run against `_verify_and_retry` driven by a **real
  in-test `run_call` thunk** (a plain async function that counts its own invocations and
  returns crafted strings — production code, no `llm_call` monkeypatch, per
  testing.md:18): given a drops-identifiers `first_summary` then a keeps-identifiers
  retry, the thunk is invoked exactly once (one retry) and the keeps-identifiers result
  is returned; when both drop identifiers the thunk is invoked exactly once and a
  non-empty summary is returned (accept-best, no second retry); when the thunk raises,
  the original `first_summary` is returned (degrade-safe); when the source has zero
  identifiers the thunk is **never** invoked (short-circuit). Output redaction is
  asserted separately on a **real `summarize_messages` call** (real model) with a
  **non-empty** `observability.redact_patterns`: a matching token in the produced
  summary body comes back `[REDACTED]` (so the assertion can't pass vacuously).
- **success_signal:** A first-pass summary that drops identifiers is repaired by one
  re-prompt without touching the anti-thrash / no-progress path, and the returned
  summary is credential-redacted.
- **prerequisites:** TASK-1
- **note:** the verify-and-retry body lives in a shared private helper
  `_verify_and_retry(deps, serialized, first_summary, run_call)` (per High-Level
  Design) so TASK-3 can exercise the same retry on the real-model eval path. The retry
  reuses the identical output cap; the feedback list is bounded to the top
  `FIDELITY_FEEDBACK_MAX_IDENTIFIERS` missing identifiers (a named constant beside
  `FIDELITY_MISS_THRESHOLD`).

### ✓ DONE TASK-3 — Real-model retry-lift measurement in the fidelity eval
- **files:** `evals/eval_summarizer_fidelity.py`
- **done_when:** `uv run python evals/eval_summarizer_fidelity.py` runs against the
  configured model with `_summarize_with_template` routing its summarizer call through
  the shared `_verify_and_retry` helper (passing its own template-swapped `run_call`
  thunk — the A/B lever is preserved). Because the helper returns only the final pick,
  the eval must **capture the first-pass `first_summary` separately** and score it and
  the helper's return as two properties, so the run JSONL can record, per transcript,
  the single-pass identifier survival rate AND how often the retry fired and lifted a
  sample from fail→pass; the printed verdict still resolves COMPLIANT/REVISE.
- **success_signal:** The eval quantifies, on real model output, how much the retry
  recovers identifier survival versus the single-pass baseline.
- **prerequisites:** TASK-2
- **note:** `_verify_and_retry` returns **raw** (unredacted) text — output redaction
  lives only in the `summarize_messages` caller, never inside the shared helper.
  Adding redaction inside the helper would double-redact in production and diverge the
  eval from the unredacted baseline it measures.

## Testing

- Unit (functional, no LLM): TASK-1 exercises the pure extract+survival decision over
  crafted source/summary pairs (the feature's observable decision). TASK-2 exercises
  the bounded-retry behavior by driving `_verify_and_retry` with a **real in-test
  `run_call` thunk** (production code that counts its own invocations and returns
  crafted strings — no `llm_call` monkeypatch, per testing.md:18) — asserts retry count,
  accept-best selection, degrade-safe on retry exception, and the zero-id short-circuit.
  Output redaction is asserted separately on a **real `summarize_messages` call**
  (real model) with a non-empty `redact_patterns`. Mirrors `done_when`.
- Eval (real data, real LLM): TASK-3 extends the existing fidelity harness — real
  configured model, real compaction assembly, no caps/stores/cleanup.
- No structural assertions (no "method called", "field exists"); every test asserts an
  observable functional outcome (needs-retry decision, call count, returned text).

## Open Questions

- **Identifier extraction precision.** The conservative regex set (paths, `path:line`,
  URLs, hashes, quoted spans) is the first cut. Whether it over-fires on transcripts
  dense with incidental numbers/quotes is a tuning question answered from TASK-3 trace
  data (`fidelity.identifiers_extracted` vs `fidelity_retry_fired` rate), not guessable
  up front. Resolution: ship conservative, tune the constant from the eval run.
- **Accept-best tie-break.** When both passes miss the *same* count, keep the first
  (avoid paying for a lateral move). Selection is **count-only** — identifier
  criticality weighting (a dropped path vs. an incidental hash) is explicitly out of
  scope; weights would reintroduce the tuning complexity the conservative-regex
  decision deliberately avoids (PO-m-3).

## Decisions

Condensed ledger across all critique cycles (verbose per-cycle critique bodies removed; rows retained as the design + overdesign-avoidance record). No issue was rejected — all adopted.

| Cycle | Issue ID | Decision | Rationale | Change |
|-------|----------|----------|-----------|--------|
| C1 | CD-M-1 | adopt | Eval must exercise the real retry, not a divergent copy. | Verify-and-retry factored into a shared `_verify_and_retry(deps, serialized, first_summary, run_call)` helper; eval routes through it via its own template-swapped `run_call` thunk (A/B lever preserved). |
| C1 | CD-m (×4) | adopt | Bound feedback + reuse cap; make short-circuit explicit; capture unquoted errors; non-vacuous redaction test. | Top-N bounded feedback + cap-reuse; zero-identifier short-circuit made explicit and tested; unquoted-error-string extraction; non-empty `redact_patterns` in the redaction test. |
| C1 | PO-m (×2) | adopt | Sharpen motivation + tie-break scope. | Model-swap demoted to secondary motivation; count-only tie-break, criticality weighting declared out of scope. |
| C2 | CD-M-1 | adopt | testing.md:18 forbids monkeypatching `llm_call`; the `_verify_and_retry(run_call)` factoring already enables a policy-clean test via a real in-test thunk. | TASK-2 `done_when` rewritten: retry-count / accept-best / degrade-safe / zero-id assertions driven by a real in-test async `run_call` thunk (production code, no `llm_call` monkeypatch); output-redaction asserted on a real `summarize_messages` call (real model) — a `redact_patterns` token returns `[REDACTED]`. |
| C2 | CD-m-1 | adopt | "bare error-string token" had no regex shape; "Traceback-adjacent lines" not implementable without an anchor. | High-Level Design `_extract_identifiers`: unquoted-error class pinned to `\b\w+(Error\|Exception)\b` plus literal `Traceback`; "adjacent lines" dropped. |
| C2 | CD-m-2 | adopt | Routing through the helper returns only the final pick; lift cannot be derived from one string. | TASK-3 `done_when`: eval captures the first-pass `first_summary` separately (scores it and the helper return as two properties) to compute single-pass survival vs fail→pass lift. |
| C2 | CD-m-3 | adopt | Prevents adding redaction inside the shared helper (would double-redact in production, diverge the eval baseline). | TASK-3 note: helper returns raw; output redaction stays in the `summarize_messages` caller. |
| C2 | CD-m-4 | adopt | "top-N missing" never named N; constant-style consistency with `SUMMARY_CAP_OVERSHOOT_RATIO`. | N named as constant `FIDELITY_FEEDBACK_MAX_IDENTIFIERS` beside `FIDELITY_MISS_THRESHOLD` (High-Level Design + TASK-2 note). |
| C2 | PO-m-1 | adopt | Output redaction is independently justified (hermes parity), correct even without the fidelity guard. | Outcome paragraph names the output redaction as independently-justified co-resident parity; removing the fidelity guard does not imply removing the redaction. |
| C2 | PO-m-2 | adopt | The "just raise the threshold/prompt" challenge is the likeliest Gate-1 objection. | Problem paragraph: prompt-only preservation is the status quo being backstopped; the eval threshold governs CI verdicts, not runtime loss — a runtime mechanism is proportionate, not redundant. |
| C2 | PO-m-3 | adopt | New runtime behavior + extra-call cost is observable agent behavior, not a doc tidy. | Scope "Out" spec line: at Gate 2 confirm `sync-doc` adds the fidelity-guard behavior + the +1-call-in-miss-case cost to `compaction.md`, not a no-op sync. |
| C3 | (verification) | — | C3 re-ran Core Dev only (PO was Blocking: none in C2). | CD-M-1 confirmed substantively resolved; all four C2 minors confirmed applied in the plan body; stale "monkeypatched `llm_call` seam" prose in the Testing section corrected. |

## Final — Team Lead

Plan approved. Converged C3: both Core Dev and PO return Blocking: none. C1 factored the
verify-and-retry into a shared `_verify_and_retry` helper (eval exercises the real retry).
C2 caught a testing-policy violation (TASK-2 monkeypatched `llm_call`, forbidden by
testing.md:18) — fixed by driving the pure helper with a real in-test `run_call` thunk and
asserting output redaction on a real `summarize_messages` call — plus four minor
specificity fixes (pinned unquoted-error regex, separate eval lift baseline, helper-returns-raw
note, named `FIDELITY_FEEDBACK_MAX_IDENTIFIERS` constant) and three PO clarifications
(independently-justified output redaction, threshold-vs-runtime rebuttal, Gate-2 sync-doc
confirmation). C3 confirmed all resolved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev summary-fidelity-verify-retry`

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `tests/context/test_summary_fidelity_check.py` passes — drop-path/error → needs-retry True, keep-all → False, zero-id → False | ✓ pass |
| TASK-2 | `tests/context/test_summary_fidelity_retry.py` passes — retry-once/accept-best/degrade-safe/zero-id via real in-test thunk; output redaction on a real `summarize_messages` call | ✓ pass |
| TASK-3 | `uv run python evals/eval_summarizer_fidelity.py` runs through `_verify_and_retry`, captures first-pass separately, records single-pass survival + retries-fired + fail→pass lift; verdict COMPLIANT | ✓ pass |

**Tests:** scoped — 8 passed, 0 failed (3 pure check + 4 pure retry + 1 real-model redaction @3.3s).
**Eval:** COMPLIANT (threshold 0.8) on `qwen3.6:35b-a3b-agentic`, 5 samples/transcript.
**Doc Sync:** fixed — `compaction.md` §2.6 fidelity verify-and-retry subsection + pipeline diagram + §2.9 degradation row + §2.10 two-sided redaction bullet (the behavior + the +1-call-in-miss-case cost, per Scope "Out" Gate-2 confirmation — not a no-op sync).

**Implementation notes:**
- New constants `FIDELITY_MISS_THRESHOLD = 0.34`, `FIDELITY_FEEDBACK_MAX_IDENTIFIERS = 12` (commented, trace-tunable, beside `SUMMARY_CAP_OVERSHOOT_RATIO`).
- **Signature deviation from the plan sketch:** `_verify_and_retry(serialized, first_summary, run_call)` drops the `deps` arg the High-Level Design sketched — `deps` had no read/write site inside the helper (span via `current_span()`, log via module logger), so including it would be a one-sided param violating the no-one-sided-members rule (Step-4 self-review). Both callers (production + eval) omit it. Behavior contract unchanged.
- `summarize_messages` refactored its single `llm_call` into a `run_call(feedback)` thunk; `first_summary = run_call(None)` → `_verify_and_retry(...)` → `redact_text(...)` on return.

**Eval observation for review/tuning (not a defect):** `tool_heavy` transcript fired `retries_fired=5/5` with zero fail→pass lift — the conservative extractor over-fires on JSON-dense tool-arg/quoted spans the summary legitimately compresses. This is exactly the over-extraction-cost signal the Open Questions flagged as resolved-from-trace-data (tune `FIDELITY_MISS_THRESHOLD` / extraction precision from eval runs); ship conservative.

**Overall: DELIVERED**
All three tasks passed `done_when`, lint clean, scoped tests green, eval COMPLIANT, doc sync applied.

## Implementation Review — 2026-06-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | drop-path/error → needs-retry True, keep-all → False, zero-id → False | ✓ pass | `summarization.py:411-432` `_extract_identifiers`/`_retry_warranted`; `tests/context/test_summary_fidelity_check.py` 3/3 pass — short-circuit on empty `required` confirmed at `:425` |
| TASK-2 | retry-once/accept-best/degrade-safe/zero-id via real thunk + output redaction on real call | ✓ pass | `_verify_and_retry` `summarization.py:445-484`; output redaction `:566` `redact_text(summary, ...)` last; `test_summary_fidelity_retry.py` 5/5 (incl. real-model redaction asserts `[REDACTED]` present + `Active Task` absent, non-vacuous since header is model-generated, prompt `:166`) |
| TASK-3 | eval routes through helper, captures first-pass separately, records survival + retries-fired + fail→pass lift; COMPLIANT | ✓ pass | `eval_summarizer_fidelity.py:115-163` returns `(first, final)`; `:447-491` scores `single_pass_rates`/`final_rates`/`lift_counts`; verdict driven by final pick; eval imports clean |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `run_call` param untyped — violates "type hints everywhere"; both other callable params in `context/` are typed (`history_processors.py:98`, `_tool_result_markers.py:105`) | summarization.py:445 | blocking | Added `Callable[[str \| None], Awaitable[str]]` + `from collections.abc import Awaitable, Callable` |
| Signature drops `deps` vs plan sketch | summarization.py:445 | n/a (correct) | Confirmed correct — `deps` had no read/write site inside helper; including it would be a one-sided param. Documented in delivery notes. |
| Scope-creep in working tree: `compaction.py` (`skills.review_enabled`→`memory.review_enabled`), `memory.py`, `main.py`, several `test_flow_*`, specs | (various) | minor | NOT this task — belongs to concurrent active plans sharing the dirty tree. This task's declared files (`summarization.py`, `eval_summarizer_fidelity.py`, `tests/context/`, `compaction.md`) verified in isolation. Flagged for staging hygiene at ship. |

### Tests
- Command: `uv run pytest tests/context/ tests/test_flow_compaction_summarization.py` (affected surface; full suite deliberately not run — tree contaminated by unrelated in-progress plans)
- Result: 19 passed, 0 failed (incl. real-model `summarize_messages` flow where a retry fired end-to-end, spans=4 @21.88s — within budget, no stall)
- Log: `.pytest-logs/*-review-affected.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads after the type-annotation fix)
- Fidelity retry / redaction: LLM-mediated — verified via scoped real-model tests and `test_flow_compaction_summarization` (retry fired in a real compaction flow); chat REPL non-gating
- `success_signal` (all 3): verified — low-fidelity summary is flagged by pure local check (TASK-1), repaired by one re-prompt + credential-redacted (TASK-2 real-model test), eval quantifies retry lift vs baseline (TASK-3, COMPLIANT)
- Gate-2 sync-doc confirmation: ✓ `compaction.md` §2.6 fidelity subsection + pipeline diagram + §2.9 degradation row + §2.10 two-sided redaction — substantive, not a no-op

### Overall: PASS
One blocking convention finding (untyped callable param) auto-fixed; affected-surface tests green, lint clean, boot verified, sync-doc confirmed. Full suite skipped by design (concurrent unrelated work in tree) — note staged-file hygiene before `/ship`.
