# Loop decoupling Phase 1 — thin provider client `model_turn` (graph path untouched)

**Milestone:** `2026-06-24-234633-loop-decoupling-milestone.md` (`0.9.0`). **Design:** `2026-06-24-234633-loop-decoupling-design.md` §6.1. Gate 1 for the milestone is APPROVED; this pass details Phase 1's per-task `done_when` before `/orchestrate-dev`.

## Context

The milestone owns the agent turn by driving `pydantic_ai.direct.model_request_stream` directly and demoting pydantic-ai to a provider+message library. Phase 1 builds the **single call that replaces the graph's model request** — a `co_cli/llm/` async-context client `model_turn` — and nothing else. The owned loop (Phase 2), inline approval (Phase 3), and error/recovery relocation (Phase 4) all sit *on top of* this client. It is the foundation, so it ships and is proven in isolation first.

Today co's three model-request-boundary concerns live in `SurrogateRecoveryModel` (`co_cli/llm/surrogate_recovery_model.py`), a `WrapperModel` subclass the graph invokes via `agent.iter()`:
1. **Surrogate recovery** — catch `UnicodeEncodeError` lone surrogates, re-sanitize messages, retry once (`surrogate_recovery_model.py:232-238` request, `:276-290` stream).
2. **`chat` span** — push `kind="model"` on entry, close with output/token attrs (`:219-226`, `:194-203`).
3. **JSON arg repair** — gated to Ollama, repair each string `ToolCallPart.args` on the assembled response before pydantic validation; on the stream path via `_RepairingStreamedResponse` proxy on `.get()` (`:144-181`, `:242-243`).

Phase 1 lifts these three concerns into a standalone client function that drives `direct.model_request_stream` itself (no `WrapperModel`, no graph), folding `SurrogateRecoveryModel`. **The wrapper is NOT deleted in Phase 1** — it stays wired into the factory (`factory.py:56,70`) so the graph path (still default) keeps working. The wrapper is deleted at Phase 5.

**Current state verified consistent** with the design doc (the three concerns are exactly as §6.1 describes; `direct.model_request_stream` present in installed pydantic-ai 1.92.0 at `direct.py:167`, returns an async-context `StreamedResponse` whose `.get()` returns the assembled `ModelResponse` at `models/__init__.py:1133`). No `/sync-doc` needed.

## Problem & Outcome

**Problem:** the model-request boundary's three co-specific concerns are reachable only as a `WrapperModel` the *graph* invokes. The owned loop has no graph, so it needs a graph-free entry point to the same three concerns.

**Outcome:** `co_cli/llm/model_turn.py` exposes `model_turn(...)` — an async context manager that drives `direct.model_request_stream`, applies surrogate-retry + the `chat` span + (gated) JSON repair inline, and yields a streamed response whose `.get()` returns the assembled, repaired `ModelResponse`. The three repair/proxy/span primitives are extracted into package-private homes that both the new client and the still-live wrapper share (no duplication). Unit + one real-Ollama integration test prove the client in isolation. The graph path is byte-for-byte unchanged in behavior and remains the default.

**Failure cost:** Phase 1 is additive (the client is dead code until Phase 2 wires it in), so a bug here cannot reach users this phase — but a faithfulness gap (e.g. repair not landing on `.get()`, or surrogate retry mis-scoped) would silently propagate into the owned loop and surface only as eval drift at Phase 2/5, far from its cause. The gate is faithful relocation proven by tests against the same fakes the wrapper uses today.

## Scope

**In:**
- New `co_cli/llm/model_turn.py`: `model_turn` async-context client over `direct.model_request_stream`, with surrogate-retry + `chat` span inline + repair gating.
- New `co_cli/llm/_json_repair.py`: relocate `repair_json_args`, `repair_response`, `RepairingStreamedResponse` (+ their module-private helpers) out of `surrogate_recovery_model.py`.
- Refactor `surrogate_recovery_model.py` to import the relocated repair primitives and the shared span-close helper from their new homes — behavior identical.
- Update the one test that imports the relocated symbols (`tests/test_flow_tool_call_repair.py`).
- Tests: functional fake-model tests of `model_turn` (surrogate retry, repair-on-`.get()`, span); one real-Ollama streamed integration test.

**Out:**
- Rebasing `llm_call` onto the client (deferred — see High-Level Design D5). The helper path stays on `direct.model_request` unchanged.
- Any change to `factory.py`'s wrapping, `deps.model`, the graph driver (`orchestrate.py`), or the wrapper's deletion — all later phases.
- The typed co-event union (design §4) — Phase 1 yields the SDK's `StreamedResponse` and the caller iterates SDK part-events; co-typed events are a Phase-2 render concern (design §4 lean: pass through SDK part-events).
- `docs/specs/` edits — Phase 6 (layer rule).

## Behavioral Constraints

- **Graph path unchanged.** `SurrogateRecoveryModel` keeps identical observable behavior after the primitive extraction; the existing `test_surrogate_recovery_model.py` and `test_flow_tool_call_repair.py` pass unchanged (only the import path in the latter moves). The graph remains the default driver.
- **Faithful three-concern parity.** `model_turn` must reproduce the wrapper's `request_stream` semantics exactly: surrogate retry only around stream *open* (not after open — a post-open consumer `UnicodeEncodeError` propagates, no retry, per `surrogate_recovery_model.py:276-278`); span ERROR-popped on any exception; repair applied on `.get()` only when `repair=True`.
- **Repair gating matches today.** `repair=True` for Ollama, `False` for Gemini — same as `repair_tool_args` set at `factory.py:61` (Ollama) and its absence at `factory.py:70` (Gemini).

## High-Level Design (§6.1 of the design doc, resolved to one approach)

### D1 — Module layout (extract, don't duplicate)

The repair logic and the span-close helper are needed by **both** the soon-deleted wrapper and the new client. Duplicating them would drift; leaving them in `surrogate_recovery_model.py` and importing into the new client would make the *new* code depend on the *to-be-deleted* module (wrong direction for Phase 5). So **extract to package-private homes; both import from there**:

- `co_cli/llm/_json_repair.py` (new, package-private module) — `repair_json_args`, `repair_response`, `RepairingStreamedResponse`, plus the existing module-private helpers (`_try_parse`, `_balance_brackets`, `_CLOSE_FOR`/`_OPEN_FOR`/`_TRAILING_COMMA`, `_JSON_REPAIR_MAX_TRIM_STEPS`). Leading-underscore removed from the three public names (they cross module boundaries within the package; the module itself carries the package-private contract).
- `co_cli/llm/model_turn.py` (new, public module) — `model_turn` + the shared span-close-attr builder (`model_span_close_attributes`) and the stream-span-close helper (`close_model_span`), relocated from the wrapper.
- `co_cli/llm/surrogate_recovery_model.py` (refactored) — imports `repair_response`/`RepairingStreamedResponse` from `_json_repair` and `model_span_close_attributes`/`close_model_span` from `model_turn`; the `request`/`request_stream` method bodies are otherwise unchanged.

Precedent: `co_cli/llm/` already hosts `_message_sanitize.py` (`surrogate_recovery_model.py:33`) as a package-private model-boundary primitive. This is not a new util module — it is domain-homed boundary code.

The span-close helpers (`model_span_close_attributes`, `close_model_span`) are placed in `model_turn.py` rather than a neutral home symmetric with `_json_repair.py`: the `chat` span is a model-turn-boundary concern the client owns going forward (after Phase 5 deletes the wrapper, the client is its sole owner). The wrapper imports them in the interim. This is a shared primitive that may re-home if a third consumer appears; not worth a separate module in Phase 1 (per PO-m-2).

### D2 — `model_turn` signature and body

```python
@asynccontextmanager
async def model_turn(
    model: Model,
    messages: list[ModelMessage],
    model_request_parameters: ModelRequestParameters,
    model_settings: ModelSettings | None,
    *,
    repair: bool,
) -> AsyncIterator[StreamedResponse]:
    push_span(f"chat {model.model_name}", kind="model", attributes={...input...})
    spanned_stream = None
    try:
        opened = False
        try:
            async with direct.model_request_stream(
                model, messages, model_settings=model_settings,
                model_request_parameters=model_request_parameters,
            ) as stream:
                opened = True
                spanned_stream = RepairingStreamedResponse(stream) if repair else stream
                yield spanned_stream
        except UnicodeEncodeError:
            if opened:
                raise                          # post-open consumer error — no retry
            sanitized = sanitize_surrogate_codepoints_messages(messages)
            async with direct.model_request_stream(model, sanitized, ...) as stream:
                spanned_stream = RepairingStreamedResponse(stream) if repair else stream
                yield spanned_stream
    except BaseException as exc:
        pop_span(status="ERROR", status_msg=str(exc)); raise
    close_model_span(spanned_stream)
```

This is the wrapper's `request_stream` body (`surrogate_recovery_model.py:248-294`) with `self.wrapped.request_stream(..., run_context)` replaced by `direct.model_request_stream(..., model_request_parameters=...)` — **`run_context` is dropped** (it was a graph artifact; `direct` does not take it). `repair` replaces the instance `self.repair_tool_args`. The model is passed in (the client is not a model subclass).

`direct.model_request_stream` re-wraps the passed model via `instrument_model` (`direct.py:298-303`), whereas the wrapper calls `self.wrapped.request_stream` directly. This adds **no** wrapping in co's case because `Agent._instrument_default` is `False` and co never calls `logfire.instrument_pydantic_ai` — so `instrument_model` returns the model unwrapped (CD-m-2). A future contributor who enables logfire would touch the `model_turn` path but not the legacy wrapper path; noted so the asymmetry is not a silent surprise.

`model` is the **raw provider model** (`OpenAIChatModel`/`GoogleModel`), not the `SurrogateRecoveryModel` wrapper — otherwise the wrapper's concerns would double-apply. In Phase 1 the only caller is the test; it sources the raw model (see Testing). Phase 2's factory work decides how the owned loop sources the raw model — out of scope here.

### D3 — repair gating

`repair: bool` keyword, caller-supplied (`deps.config.llm.uses_ollama()` once wired). Mirrors `repair_tool_args=True` for Ollama (`factory.py:61`) and unset (False) for Gemini (`factory.py:70`). Idempotent on valid JSON, so the gate is cleanliness not correctness (per `surrogate_recovery_model.py:14`).

### D4 — stream-only (no non-stream method)

`model_turn` is streaming-only; the wrapper's non-stream `.request()` path has no owned-loop consumer (the loop always streams to render). The non-stream `model_span_close_attributes` helper is still extracted because `.request()` uses it and `model_turn`'s `close_model_span` uses it too.

### D5 — do NOT rebase `llm_call` in Phase 1

The milestone listed the `llm_call` rebase as *optional*. Decision: **defer.** `llm_call` is non-streaming (`call.py:64`, `direct.model_request`); rebasing onto the streaming client would change a default-path helper (compaction summarizer, dream merges, eval judges) from non-stream to stream under the hood mid-Phase-1, against the "graph path untouched" intent. The client is validated end-to-end by its own real-Ollama integration test instead. The client being unused-until-Phase-2 is the expected strangler-fig shape.

**Known pre-existing duplication (mention, do not fix — PO-m-3):** `call.py:80-89` builds its own inline span-close attribute dict with the same keys as the extracted `model_span_close_attributes`. Out of Phase 1 scope (surgical-changes rule; D5 defers `llm_call` entirely). When `llm_call` is eventually rebased (post-Phase-1), it should consume the shared `model_span_close_attributes` rather than keep its inline copy — flagged here so it is not silently re-duplicated.

## Tasks

### ✓ DONE TASK-1 — Extract repair primitives to `co_cli/llm/_json_repair.py`
- `files:` `co_cli/llm/_json_repair.py` (new), `co_cli/llm/surrogate_recovery_model.py`, `tests/test_flow_tool_call_repair.py`
- Move `_repair_json_args`→`repair_json_args`, `_repair_response`→`repair_response`, `_RepairingStreamedResponse`→`RepairingStreamedResponse`, and their module-private helpers (`_try_parse`, `_balance_brackets`, the regex/const module-level names) into the new module. Refactor `surrogate_recovery_model.py` to import `repair_response`/`RepairingStreamedResponse` from it. Update `tests/test_flow_tool_call_repair.py` imports to the new module + names.
- `done_when:` repo-wide grep shows zero references to `_repair_json_args`/`_repair_response`/`_RepairingStreamedResponse` (old underscore names) anywhere in `co_cli/` or `tests/`; AND the relocated module-private helpers/constants (`_try_parse`, `_balance_brackets`, `_CLOSE_FOR`, `_OPEN_FOR`, `_TRAILING_COMMA`, `_JSON_REPAIR_MAX_TRIM_STEPS`) no longer have a definition left behind in `surrogate_recovery_model.py` (grep that file for each — zero, so an incomplete cut surfaces); `tests/test_flow_tool_call_repair.py` and `tests/test_surrogate_recovery_model.py` pass against the relocated symbols; full suite green.
- `success_signal:` N/A (pure relocation/refactor).

### ✓ DONE TASK-2 — Build the `model_turn` client
- `files:` `co_cli/llm/model_turn.py` (new), `co_cli/llm/surrogate_recovery_model.py`
- Implement `model_turn` per D2. Relocate `model_span_close_attributes` (the close-attr builder, currently `_model_span_close_attributes`) and `close_model_span` (currently `_close_model_span`) into `model_turn.py`; refactor the wrapper's `request`/`request_stream` to import them. Wrapper behavior unchanged.
- `prerequisites:` TASK-1
- `done_when:` a fake-model test (reusing the `_FakeModel`/`_FakeStream` pattern from `test_surrogate_recovery_model.py`, driven through `direct.model_request_stream`) exercises the runtime path and asserts **observable outcomes only — never call counts, "method fired", or internal shape**:
  - (a) **clean input** → `model_turn` yields a stream whose `.get()` returns the model's response (the answer is delivered).
  - (b) **lone-surrogate input** → `.get()` still returns a response AND the content delivered to the provider on the successful attempt has the surrogate codepoint replaced — the recovery's *observable effect* (clean content reaches the model), not "retried once".
  - (c) **input that fails sanitization on both attempts** → the `UnicodeEncodeError` surfaces from `model_turn` to the caller (terminal, not silently swallowed).
  - (d) **consumer `UnicodeEncodeError` raised after the stream opened** → it surfaces unchanged (no silent recovery of a post-open error).
  - (e) **`repair=True`** → the assembled `.get()`'s `ToolCallPart.args` parses as valid JSON for a malformed-args fake response; **`repair=False`** → the args are returned verbatim (the repair gate's observable output).
  - (f) **span artifact** (via `tracing.setup_log`, the `test_flow_observability_spans.py` pattern) → after a turn the spans log contains a `kind="model"` record with the output/token attributes; on the raising path it contains a `status="ERROR"` record. (Assert the emitted trace record, not that `push_span`/`pop_span` were called.)
  All pass.
- `success_signal:` driving `model_turn` against a fake model yields the same surrogate-retry + repair behavior the wrapper produces today.

### ✓ DONE TASK-3 — Real-Ollama streamed integration test for `model_turn`
- `files:` `tests/test_flow_model_turn.py` (new)
- A real-LLM test (no mocks, per eval/test doctrine): **skip the test unless `config.llm.uses_ollama()`** (the `.wrapped` reach-in below assumes the wrapped Ollama provider, and warming + repair-gating are Ollama-specific — make the assumption explicit rather than silent). Build the raw provider model from config (`build_model(config.llm).model.wrapped` — the wrapper exposes `.wrapped`; this reach-in is test-only and disappears at Phase 5 when the factory returns a raw model), ensure Ollama warm outside any `asyncio.timeout`, drive `model_turn(raw_model, [user prompt], ModelRequestParameters(), noreason_settings, repair=config.llm.uses_ollama())`, iterate the stream, and assert text deltas arrive AND `.get()` returns an assembled `ModelResponse` with non-empty text. Use `llm.host` from config and `noreason_model_settings()` (per the config-model-settings test rule); tail the run log to watch call timing.
- `prerequisites:` TASK-2
- `done_when:` the integration test makes one real streamed call through `model_turn` against the configured Ollama model and asserts observable behavior: text deltas are received during iteration AND `.get()` returns an assembled `ModelResponse` whose text is non-empty and consistent with the streamed deltas (the streamed text is contained in / equals the assembled text). Test passes against warm Ollama.
- `success_signal:` `model_turn` streams a real model response end-to-end with no wrapper involved.

## Testing

**Functional-only discipline (per `feedback_functional_tests_only` / `.agent_docs/testing.md`):** every assertion is an *observable outcome*, never structure. Concretely:
- **Assert:** the recovered response is delivered; the surrogate is replaced in the content the provider receives; a fatal `UnicodeEncodeError` surfaces to the caller; repaired tool-call args parse as valid JSON (or pass verbatim when `repair=False`); the emitted spans log carries the `kind="model"` / `status="ERROR"` records; the real stream yields deltas whose text matches the assembled `.get()`.
- **Do NOT assert:** `request_stream`/`push_span`/`pop_span` "was called", raw call counts as the sole check, or that a module/attribute/field exists. (The legacy `test_surrogate_recovery_model.py` leans on `len(...calls)`; the new `model_turn` tests assert the *effect* of recovery instead — clean content reached the model — which is the functional behavior the count was a proxy for.)

The seam is exercised with fakes because you cannot make a real model reliably emit a lone surrogate or malformed JSON — this is still functional behavior assertion (observable I/O + trace artifacts), not structural. The end-to-end reality of the client is proven by one real-Ollama streamed integration test (TASK-3), consistent with co's real-data test doctrine. The graph path's continued correctness is proven by the unchanged `test_surrogate_recovery_model.py` passing after the extraction.

## Open Questions

None. All Phase-1 decisions (module layout, signature, repair gating, stream-only, defer `llm_call` rebase) are resolved against source above.

## Decisions

C1: Core Dev `approve / Blocking: none`; PO `approve / Blocking: none`. Convergence at C1 — no C2 needed. Core Dev verified all three load-bearing pydantic-ai internals claims against installed 1.92.0 source (`direct.model_request_stream` takes no `run_context`, `StreamedResponse.get()` returns the assembled response, the streamed request fires on `__aenter__` so the surrogate `opened`-flag logic is faithful) and confirmed the moved-symbol ripple set is complete.

| Issue | Decision | Rationale | Change |
|-------|----------|-----------|--------|
| CD-m-1 / PO-m-1 | adopt | `_message_sanitize` is imported only by the wrapper (`surrogate_recovery_model.py:33`), not `call.py` — the precedent sentence overstated the evidence; the extraction conclusion stands regardless. | D1 precedent reworded: `co_cli/llm/` hosts `_message_sanitize.py` as a package-private boundary primitive (dropped the false `call.py` half). |
| CD-m-2 | adopt | `direct.model_request_stream` re-wraps via `instrument_model`, but co keeps instrumentation off (`Agent._instrument_default=False`, no `instrument_pydantic_ai` call), so it returns the model unwrapped — parity holds on an unstated assumption. | D2: added a line noting `direct` adds no model wrapping because co leaves pydantic-ai instrumentation off. |
| CD-m-3 | adopt | TASK-1's grep checked only the three renamed public names; a stale leftover *definition* of a module-private helper/constant in the wrapper would pass undetected. | TASK-1 `done_when` += grep `surrogate_recovery_model.py` for zero leftover definitions of `_try_parse`/`_balance_brackets`/the four constants. |
| CD-m-4 | adopt | TASK-3's `.model.wrapped` reach-in + warming + repair-gating silently assume the configured provider is wrapped Ollama. | TASK-3: skip unless `config.llm.uses_ollama()` — assumption made explicit. |
| PO-m-2 | modify | Span helpers homed in `model_turn.py` (asymmetric with `_json_repair.py`) is acceptable: the `chat` span is a model-turn-boundary concern the client owns going forward (sole owner after Phase 5). A separate module is not worth it in Phase 1. | D1: added a line stating the rationale + that it may re-home if a third consumer appears (kept in `model_turn.py`, not moved). |
| PO-m-3 | adopt | Pre-existing inline span-close dict in `call.py:80-89` duplicates the extracted `model_span_close_attributes`; out of Phase 1 scope but should be named so it is not silently re-duplicated. | D5: added a mention-not-fix note to consume `model_span_close_attributes` when `llm_call` is later rebased. |

## Final — Team Lead

Plan approved — Core Dev `Blocking: none` (C1), PO `Blocking: none` (C1). Convergence at C1.

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev loop-decoupling-phase1`

## Delivery Summary — 2026-06-25

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | zero old-underscore refs + no leftover private defs in wrapper; relocated tests pass | ✓ pass |
| TASK-2 | fake-model tests (a)–(f) assert observable outcomes; all pass | ✓ pass |
| TASK-3 | one real streamed call through `model_turn` vs configured Ollama; deltas consistent with `.get()` | ✓ pass |

**Files changed:**
- `co_cli/llm/_json_repair.py` (new) — relocated `repair_json_args`, `repair_response`, `RepairingStreamedResponse` + module-private helpers/constants (underscore dropped on the three cross-module names).
- `co_cli/llm/model_turn.py` (new) — `model_turn` async-context client over `direct.model_request_stream` (surrogate-retry around open + `chat` span + gated repair); homes `model_span_close_attributes` / `close_model_span`.
- `co_cli/llm/surrogate_recovery_model.py` — imports the relocated repair + span primitives; method bodies otherwise unchanged.
- `tests/test_flow_tool_call_repair.py` — import path/names updated to `_json_repair`.
- `tests/test_flow_model_turn.py` (new) — 8 fake-model functional tests + 1 real-Ollama streamed integration test. (Extra file beyond TASK-2's `files:` — it is TASK-3's listed file; the fake-model tests share it as the natural home for `model_turn` tests.)

**Tests:** scoped — 31 passed, 0 failed (`test_flow_tool_call_repair`, `test_surrogate_recovery_model`, `test_flow_model_turn`, `test_flow_observability_spans`). Real-Ollama integration warm call ~0.33s.
**Doc Sync:** clean — no sync (additive client, no public API rename; `docs/specs/` deferred to Phase 6 per plan scope).

**Note:** the `done_when` "full suite green" clause for TASK-1 is left to `/review-impl` (Phase 3 forbids running the full suite here); all diff-scoped touched tests are green.

**Overall: DELIVERED**
All three tasks passed their `done_when`; the graph path stays byte-for-byte unchanged in behavior (wrapper tests pass unmodified) and the new client is dead-code-until-Phase-2 as designed.

**Next step:** `/review-impl loop-decoupling-phase1` — full suite + evidence scan + behavioral verification → verdict appended to plan.

## Implementation Review — 2026-06-25

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | zero old-underscore refs + no leftover private defs in wrapper; relocated tests pass | ✓ pass | `_json_repair.py:66,112,133` define de-underscored public names; module-private helpers/constants keep `_` (`:82,88` + `:28-31,42`); grep for old underscore names + leftover defs in wrapper → exit 1 (none); `surrogate_recovery_model.py:30` imports from `_json_repair`; no stale imports left (`json`/`re`/`replace`/`ToolCallPart`/`serialize_response` all gone). 21 relocated-test asserts green. |
| TASK-2 | fake-model tests (a)–(f) assert observable outcomes | ✓ pass | `model_turn.py`: surrogate retry only around open via `opened` flag (`:99,107,111`), post-open re-raise (`:110-112`), span ERROR-pop on any exc (`:124-126`), repair on `.get()` only when `repair=True` (`:108` + `_json_repair.py:160-163`), `run_context` dropped. **Coupling confirmed vs installed pydantic-ai 1.92.0**: signature `direct.py:167-174`, returns async CM `direct.py:174/223` + `models/__init__.py:658`, `.get()` assembles `models/__init__.py:1133`, request fires on `__aenter__` `models/openai.py:833`, `instrument_model` returns unwrapped when off `instrumented.py:69-75`. 8 fake-model tests green. |
| TASK-3 | one real streamed call vs configured Ollama; deltas consistent with `.get()` | ✓ pass | `test_flow_model_turn.py:313-347` — skips unless `uses_ollama()` (`:322`), `ensure_ollama_warm` outside `asyncio.timeout` (`:329` vs `:331`), raw model via `.model.wrapped` (`:325`), config-sourced `noreason_model_settings()` (`:326`), timeout from `_timeouts.py` (`:331`). Real warm call 0.83s, 3 tokens streamed. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Delta-arrival not independently asserted — `streamed_text in assembled_text` is vacuously true when `streamed_text==""`, so the test passed even with zero deltas, violating done_when's "text deltas received during iteration" (Important behavior — core of a streaming client) | `test_flow_model_turn.py:344` | blocking | Added `assert streamed_text.strip(), "text deltas must arrive during iteration"`; re-ran — passes (3 tokens streamed). |

### Tests
- Command: `uv run pytest -v`
- Result: 856 passed, 0 failed
- Log: `.pytest-logs/20260625-*-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads with the two new modules + refactored wrapper; exit 0)
- Observability (span helpers relocated): ✓ verified via `test_flow_observability_spans.py` passing in the full suite — `chat` span tree emits at parity (manual `co tail` non-gating; needs a live session to emit spans)
- `success_signal`: TASK-1 N/A (pure relocation); TASK-2 ✓ fake-model tests reproduce the wrapper's surrogate-retry + repair behavior; TASK-3 ✓ `model_turn` streams a real Ollama response end-to-end with no wrapper involved (0.83s).

### Overall: PASS
Faithful three-concern relocation proven against the same fakes the wrapper uses plus a real-Ollama stream; graph path unchanged (wrapper tests pass unmodified); one weak-assertion gap in the integration test found and strengthened to match its `done_when`.
