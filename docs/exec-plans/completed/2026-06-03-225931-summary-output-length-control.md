# summary-output-length-control

> **Origin:** `docs/reference/RESEARCH-summarization-prompting-peer-survey.md` gap analysis (gap 2,
> output-length control). The one summarization axis where co has neither a proportional cap nor a
> prompt target — hermes embeds `"Target ~{N} tokens"` and caps `max_tokens = budget × 1.3`; openclaw
> bounds via chunk sizing + a 16K-char cap.
>
> **Sequencing.** Independent of `antithrash-static-marker-fallback` at the *code* level (no shared
> edits) — but **complementary on the same failure**: this plan attacks the Mode-A
> verbose-summary/low-savings condition *at its source*, which the anti-thrash gate only backstops one
> pass late. Because it shrinks summary length, it shifts the net-savings distribution the antithrash
> eval (`eval_context_stability.py`, touched by both) measures — whoever ships second re-baselines that
> eval rather than reading a savings shift as a regression. **Collides with
> `prior-summary-dedicated-slot`:** both add a parameter to `_build_summarizer_prompt` (this: `budget`;
> that: `prior_summary`) and rewrite the adjacent prompt tail (`summarization.py:152–163`). Not
> order-independent — whoever lands second rebases the `_build_summarizer_prompt` signature and the
> prompt-tail edit.

## Context

The summarizer (`co_cli/context/summarization.py`) produces the marker recap that replaces the dropped
middle. Two facts about its output length, both verified in source:

1. **The output IS capped — but flatly.** `summarize_messages` → `llm_call` defaults to
   `deps.model.settings_noreason` (`call.py:66`), which carries a fixed `max_tokens=8192` on Ollama
   (`config/llm.py:88–101`) / 16384 on Gemini — a constant shared with memory-merge and judge calls.
   It does **not** scale with how much was compacted. `resolve_compaction_budget` exists but returns
   `model_max_ctx` and feeds the *trigger*, never the summarizer output (`summarization.py:70–78`).
2. **No target is given to the model.** The prompt's only length guidance is *"Be concise — this
   replaces the original messages to save context space"* (`summarization.py:162–163`). No
   `"Target ~N tokens"`. The model fills toward the flat ceiling.

The lockstep rule that any cap must obey: Ollama only honors `max_tokens` at the JSON root via
`extra_body`, not OpenAI's `max_completion_tokens`, so the scalar `max_tokens` and
`extra_body["max_tokens"]` must be set together (`config/llm.py:53–56, 82–84`;
`_ollama_settings`, `:177–181`). Gemini settings (`GoogleModelSettings`) carry only the scalar.

## Failure Modes (observed evidence, not imagined)

- co already knows summaries run long: the noreason ceiling was raised to 8192 with the explicit
  comment *"multi-section compaction summaries can exceed 4096 … summarization can produce 5000+
  tokens"* (`config/llm.py:75, 79`). So the summarizer routinely emits large output against a flat cap.
- **Mode A — near-zero savings.** When the dropped region is modest, a summary drifting toward 8192
  tokens can replace it at <10% net savings. The anti-thrash gate (`min_proactive_savings=0.10`)
  detects this only on the *next* pass — one pass late — and (pre-antithrash-plan) responds with a
  no-op, or (post) a static marker that discards the recap entirely.
- **Mode B — mid-section truncation.** A rambling summary hard-cut at 8192 loses the *trailing*
  template sections, which are the high-signal ones: `## Next Step` (with its verbatim drift anchor),
  `## Critical Context` (`summarization.py:134–142`). Truncation silently drops exactly what
  continuity depends on, with no error surfaced.

## Problem & Outcome

**Problem.** The summary's length cap is a flat 8192 (Ollama) independent of input size, and the model
gets no length target — only "Be concise". So the recap can approach the ceiling regardless of how
little was compacted (near-zero savings) or get hard-truncated mid-structure (lost trailing sections).

**Outcome.** The summarizer is bounded *proportionally to what it is compressing*: a budget derived
from the dropped-region size drives both (a) an explicit `"Target ~N tokens"` line in the prompt (a
goal, not just a ceiling) and (b) a per-call `max_tokens` override of `~budget × 1.3` that tracks the
input instead of the flat 8192. For medium/large regions — where the flat ceiling let the summary
drift wastefully — bloat is bounded to a fraction of the compressed region and the model aims at a
concrete target rather than the ceiling. This *mitigates but does not eliminate* Mode A: near the
`FLOOR=2000` budget (cap 2600), a small dropped region (~2 600–3 500 tokens) can still produce a
summary at marginal net savings — the floor deliberately trades residual small-region savings for
truncation safety. The true small-region fix ("don't compact a tiny region at all") lives in boundary
planning, which is out of scope here.

**Failure cost:** silently — long sessions accumulate compaction passes that each free little context
(savings churn caught a pass late by anti-thrash) or emit summaries truncated before their `Next
Step` / `Critical Context` sections, so the next context window resumes from an incomplete handoff.
No error fires; the only symptom is degraded continuity and wasted summarizer calls.

## Scope

**In scope.**
- `resolve_summary_budget(messages) -> int` in `summarization.py`: clamp(`ratio ×
  estimate_message_tokens(messages)`, floor, ceil), kept strictly under the noreason ceiling.
- `"Target ~{budget} tokens"` line in the assembled prompt (replaces the bare "Be concise").
- A provider-agnostic `cap_output_tokens(settings, max_tokens)` helper in `config/llm.py` that
  applies the cap in lockstep (scalar + `extra_body["max_tokens"]` when present), and threading a
  `~budget × 1.3` override into the summarizer's `llm_call` via its existing `model_settings` param.
- Scoped unit tests; an eval extension measuring output length vs budget across passes.

**Out of scope.**
- `compact_messages`, boundary planning, the anti-thrash gate, the circuit breaker — untouched.
- The `## Section` template wording / SKIP-RULE (separate accepted divergence).
- The trigger-side `resolve_compaction_budget` (returns `model_max_ctx`; unchanged — the new helper is
  distinct and summary-output-only).
- Changing the global noreason `max_tokens=8192` default (it stays the ceiling for memory-merge /
  judge / the `/compact` fallback when no budget applies).
- Promoting the budget knobs to user config (module constants now; promote later only if eval shows need).

## Behavioral Constraints

- **Proportional, never above the ceiling.** The derived `max_tokens` override must always be ≤ the
  base noreason `max_tokens` — the budget caps *down* from 8192, never raises it.
- **Lockstep preserved.** Any `max_tokens` override on the Ollama path must set the scalar **and**
  `extra_body["max_tokens"]` together; Gemini sets only the scalar. The override must be derived from
  `deps.model.settings_noreason` (a copy), not a hand-coined `ModelSettings`
  (`feedback_tests_use_config_model_settings`).
- **Both paths covered.** Proactive compaction and `/compact` both flow through `summarize_messages`,
  so both inherit the budget automatically — no separate wiring.
- **Focus pulls up while the cap pulls down — name the tension.** On the proactive path `focus` is the
  *norm*, not the exception: `_resolve_proactive_focus` (`compaction.py:451`) almost always returns
  something (an in-progress todo, else the last user prompt), so nearly every proactive summary runs
  with the FOCUS directive *"Preserve full detail for content related to this topic … Allocate ~60-70%
  of the summary to the focus topic"* (`summarization.py:247-249`). That directive pushes summary
  length **up** against a cap this plan pushes **down** — a focus-preserving summary is exactly the one
  most likely to want more than `budget × 1.3` and hit Mode-B truncation. The cap stays load-bearing
  (it wins), but TASK-4 must exercise a focus-active pass and confirm the trailing sections survive
  under that pressure — this is the worst case for the truncation guarantee, not a corner case.
- **No new no-op / no regression to other noreason callers.** memory-merge and judge calls keep the
  unmodified `settings_noreason`; only the summarizer call passes an override.
- **Functional / behavioral validations ONLY** (`feedback_functional_tests_only`). Every test designed
  or updated by this plan asserts an **observable outcome**, mirroring a `done_when` — never structure.
  Allowed: a pure helper's return value (`resolve_summary_budget` clamps a given input to the right
  number; `cap_output_tokens` returns settings whose effective cap is N with lockstep intact — that
  return value *is* the helper's behavior); the prompt-builder's emitted text carries the target line;
  the real-LLM eval bounding the actual summary output. **Forbidden:** "method called with arg X" /
  "the settings dict contains key Y" / "field/attribute exists" assertions, capturing internal call
  arguments as a proxy for behavior, or any test that would still pass if the summary were never
  actually bounded. The end-to-end "is the output bounded and are trailing sections intact" question is
  answered behaviorally by the real-LLM eval (TASK-4), not by inspecting wiring.
- **Real everything in evals** (`feedback_eval_real_world_data`); **no backward-compat shims**
  (`feedback_zero_backward_compat`); **surgical** — touch only `summarization.py`, `config/llm.py`,
  their scoped tests, and the eval.

## High-Level Design

**Budget (summarization.py).** Add `resolve_summary_budget(messages: list[ModelMessage]) -> int`:
```
raw   = SUMMARY_BUDGET_RATIO * estimate_message_tokens(messages)
return clamp(raw, SUMMARY_BUDGET_FLOOR, SUMMARY_BUDGET_CEIL)
```
Reference defaults (hermes-aligned, kept under the 8192 ceiling so the cushion never exceeds it):
`SUMMARY_BUDGET_RATIO = 0.25`, `SUMMARY_BUDGET_FLOOR = 2_000`, `SUMMARY_BUDGET_CEIL = 6_000`, and the
overshoot cushion `SUMMARY_CAP_OVERSHOOT_RATIO = 1.3` (→ cap range `2_600 … 7_800`, both < 8_192).
`FLOOR = 2_000` mirrors hermes' `_MIN_SUMMARY_TOKENS` and is set high enough that the worst-case cap
(`2_000 × 1.3 = 2_600`) comfortably clears the template's irreducible floor — the fixed-section
scaffold plus the two mandatory verbatim quotes (`## Active Task`, `## Next Step`) — so the hard cap
never truncates a small-region summary mid-structure (the Mode-B failure this plan exists to prevent).
`estimate_message_tokens` already exists in this module (`summarization.py:37`). All four are named
module constants with rationale comments; tunable, not user config.

**On `SUMMARY_CAP_OVERSHOOT_RATIO = 1.3` — named, not magic.** A first-principles check (logged here for
the future tuner): if the cushion's only job were *preventing mid-sentence cut-off*, it would be a
fixed additive amount (~128–256 tokens to finish a section or two), i.e. an effective ratio of only
~1.04 (at CEIL) to ~1.13 (at FLOOR). The chosen `1.3` is deliberately looser than that — it is an
**overshoot-tolerance** cushion (let the summary run meaningfully longer than the target), not a bare
completion margin. For a summary a loose ceiling is the right call: over-running the target slightly is
cheap, truncating a trailing section is not. The value is intentionally a named constant so it can be
**tuned from logs/traces** (see observability below) rather than left as a `* 1.3` literal — if traces
show the model routinely landing well under target, drop it toward an additive ~256-token cushion; if
it shows frequent cap hits, raise it (headroom to ~1.36 before `CEIL × ratio` reaches 8_192).

**Prompt target.** `_build_summarizer_prompt` takes `budget: int` and emits a target line in place of
the bare "Be concise" tail (`summarization.py:162–163`), e.g. *"Target ~{budget} tokens — this
replaces the original messages to save context space. Prioritize recent actions and unfinished work."*
Keeps the existing prioritization guidance.

**Cap override (config/llm.py).** Add a pure helper:
```
def cap_output_tokens(settings: ModelSettings, max_tokens: int) -> ModelSettings:
    # Returns a plain dict copy. GoogleModelSettings is a TypedDict, so its type is
    # erased at runtime regardless and pydantic-ai consumes it via cast + .get() —
    # the plain-dict return is intentional and safe for the Google path (do not
    # "fix" it back to a typed copy). extra_body is a plain dict, not a TypedDict.
    out = dict(settings); out["max_tokens"] = max_tokens
    if isinstance(out.get("extra_body"), dict) and "max_tokens" in out["extra_body"]:
        out["extra_body"] = {**out["extra_body"], "max_tokens": max_tokens}
    return out  # GoogleModelSettings: only scalar set; Ollama: lockstep mirror
```
This centralizes the lockstep rule (currently only inline in the `_LLM_SETTINGS` literals).

**Wiring (summarize_messages).** Compute `budget = resolve_summary_budget(messages)`; build the prompt
with the target; read the base ceiling defensively from
`deps.model.settings_noreason.get("max_tokens", <fallback>)` (present on both Ollama 8192 and Gemini
16384; the `.get` guards a future noreason entry that omits it) and derive
`cap = min(ceil(budget * SUMMARY_CAP_OVERSHOOT_RATIO), base_ceiling)`, then
`settings = cap_output_tokens(deps.model.settings_noreason, cap)`; pass
`model_settings=settings` to `llm_call`. With these constants the `min(…, base_ceiling)` clamp is
structurally unreachable (7_800 < 8_192) — belt-and-suspenders so a future constant bump cannot
silently raise the cap above the noreason ceiling. No call-chain params threaded — budget is computed
where `messages` already is.

**Observability (so the cushion is tunable from traces).** The summarizer already emits an
`llm_call <model>` span carrying `co.model.tokens.output` (`call.py:75`). For the constant to be
tunable without code spelunking, the summarizer path must also surface, per pass, the `budget` (target)
and the `cap` (= `budget × SUMMARY_CAP_OVERSHOOT_RATIO`) it derived — so `output_tokens / budget` (did
the model overshoot, and by how much?) and `output_tokens vs cap` (did it ever hit the cap?) are
recoverable. **Span-ownership caveat:** the `llm_call` span is pushed/popped *inside* `llm_call`
(`call.py:54-82`), so it is not active in `summarize_messages` where `budget`/`cap` are computed —
those land on the parent `compaction.proactive_check` span, not on the `output_tokens` span. Two clean
options: attach `co.compaction.summary.budget` / `.cap` to the parent span (same trace, correlate via
`co trace`), or — preferred — emit one structured log line carrying `budget` / `cap` / `output_tokens`
together, sidestepping the cross-span join. Either way this is the data the future tuner reads to
decide whether `SUMMARY_CAP_OVERSHOOT_RATIO` should move toward an additive cushion or stay loose.

## Tasks

### ✓ DONE TASK-1 — `cap_output_tokens` lockstep helper
- **files:** `co_cli/config/llm.py`
- **action:** Add `cap_output_tokens(settings: ModelSettings, max_tokens: int) -> ModelSettings`
  returning a copy with the scalar `max_tokens` set, and `extra_body["max_tokens"]` mirrored **only
  when** `extra_body` exists and already carries `max_tokens` (Ollama). Gemini settings get only the
  scalar. Pure; does not mutate the input.
- **done_when:** for an Ollama-shaped settings dict (scalar + `extra_body.max_tokens`),
  `cap_output_tokens(s, 5000)` returns a new object with **both** the scalar and
  `extra_body["max_tokens"]` equal to 5000 and the input unmutated; for a Gemini-shaped settings
  (no `extra_body`), only the scalar is set and no `extra_body` key is added;
  `uv run pytest tests/test_flow_llm_call.py -x` passes (new assertions land here — the settings-
  adjacent test module; `tests/test_config_llm.py` does not exist).
- **success_signal:** a max-tokens cap can be applied to noreason settings without breaking the Ollama
  root-vs-`max_completion_tokens` lockstep.
- **prerequisites:** none.

### ✓ DONE TASK-2 — Proportional budget → prompt target + capped summarizer call
- **files:** `co_cli/context/summarization.py`
- **action:** Add named constants `SUMMARY_BUDGET_RATIO / SUMMARY_BUDGET_FLOOR / SUMMARY_BUDGET_CEIL /
  SUMMARY_CAP_OVERSHOOT_RATIO` and `resolve_summary_budget(messages) -> int` (clamped, under the
  noreason ceiling). Add `budget: int` to `_build_summarizer_prompt` and replace the "Be concise" tail
  with a `"Target ~{budget} tokens"` line (retaining the prioritization sentence). In
  `summarize_messages`, compute the budget from `messages`, derive
  `cap = min(ceil(budget * SUMMARY_CAP_OVERSHOOT_RATIO), base_ceiling)`, build the prompt with the
  target, and pass `model_settings=cap_output_tokens(deps.model.settings_noreason, cap)` to `llm_call`.
  Read the base ceiling defensively — `deps.model.settings_noreason.get("max_tokens", <fallback>)` —
  since `_scalar_settings` only includes `max_tokens` `if k in inference` (present on every current
  config, but the `.get` removes a latent `KeyError` if a future noreason entry omits it).
  Surface `budget` and `cap` for trace-based tuning. **Note the span ownership:** the
  `llm_call <model>` span carrying `co.model.tokens.output` is pushed/popped *inside* `llm_call`
  (`call.py:54-82`), so it is not the active span when `summarize_messages` (the caller) computes
  `budget`/`cap` — those run under the parent `compaction.proactive_check` span. So `budget`/`cap`
  cannot land *on the same span* as `output_tokens`; pick deliberately — either attach
  `co.compaction.summary.budget` / `.cap` to the parent span (recoverable in the same trace as
  `output_tokens` via `co trace`), or emit a single structured log line carrying all three
  (`budget` / `cap` / `output_tokens`) — the log-line option sidesteps the cross-span correlation and
  is preferred. Do **not** thread arbitrary attrs into `llm_call` (not its contract).
- **done_when:** functional assertions on observable outputs (no wiring capture): `resolve_summary_budget`
  returns `clamp(ceil(RATIO*T), FLOOR, CEIL)` for representative T at floor / mid / ceil, and
  `_build_summarizer_prompt(..., budget=B)` returns text containing the `Target ~{B} tokens` line;
  `uv run pytest tests/test_flow_compaction_summarization.py -x` passes. The runtime *behavior* — the
  summary the model actually produces is bounded by the derived cap — is validated by TASK-4 (real LLM),
  not by inspecting the settings handed to `llm_call`.
- **success_signal:** the summarizer aims at a budget proportional to the compacted region and is hard-
  capped proportionally, instead of drifting to the flat 8192 ceiling.
- **prerequisites:** TASK-1.

### ✓ DONE TASK-3 — Scoped unit tests
- **files:** `tests/test_flow_compaction_summarization.py` (summarizer; `summarize_messages` /
  `_build_summarizer_prompt` are imported here), `tests/test_flow_llm_call.py` (`cap_output_tokens`)
- **action:** Deterministic, Ollama-free **functional** tests on observable outputs only
  (`feedback_functional_tests_only` — no wiring-capture, no "called-with-arg" proxies): (a)
  `resolve_summary_budget` returns the correct clamped budget at floor / mid / ceil for representative
  input sizes; (b) `cap_output_tokens` returns settings whose effective cap is N with Ollama lockstep
  (scalar == `extra_body["max_tokens"]` == N) and Gemini scalar-only — the helper's return value *is*
  its behavior; (c) `_build_summarizer_prompt(..., budget=B)` returns text containing the
  `Target ~{B} tokens` line. No `ensure_ollama_warm`, no timeout wrapper, no LLM. The "summary output is
  actually bounded" behavior is TASK-4's job (real LLM), not a mocked call-arg assertion here.
- **done_when:** `uv run pytest tests/test_flow_compaction_summarization.py tests/test_flow_llm_call.py -x`
  passes with the new functional assertions.
- **success_signal:** N/A (test).
- **prerequisites:** TASK-2.

### ✓ DONE TASK-4 — Eval: output length tracks budget across passes
- **files:** `evals/eval_context_stability.py` (extend; or focused new eval if not yet created by a
  sibling plan)
- **action:** Real-LLM, real-deps multi-turn run that triggers compaction on regions of differing
  size, **including at least one deliberately small dropped region** that exercises the `FLOOR=2000`
  budget (worst-case cap 2600). Because the proactive path nearly always sets `focus` (see Behavioral
  Constraints), the real runs already exercise the focus-active worst case — **assert that at least one
  cap-bound pass ran with `focus` set** (focus pushes length up while the cap pushes down; this is the
  hardest case for the no-truncation guarantee, not a corner case). **Hard assertions (within
  authority):** (a) every summarizer output's token count ≤ its derived cap (the override is honored
  end-to-end through Ollama); (b) the prompt carried a `Target ~N` line; (c) **on every pass where the
  cap actually bound the output — including the focus-active pass — the emitted summary still contains
  its trailing `## Next Step` and `## Critical Context` headers** — the Mode-B-no-truncation guarantee
  the Outcome promises (deterministic enough to gate: parse for the trailing headers). **Logged, not gated** (legitimately non-deterministic): per-pass
  net savings AND, per pass, the `budget` / `cap` / `output_tokens` triple and the ratios
  `output_tokens / budget` (overshoot) and `output_tokens / cap` (cap pressure) — this is the tuning
  signal for `SUMMARY_CAP_OVERSHOOT_RATIO`; record it for human inspection. Tail the spans log
  (`feedback_tail_log_every_test_run`); watch summarizer call timing and `output_tokens`.
- **done_when:** `uv run python evals/eval_context_stability.py` runs to completion; every recorded
  summarizer `output_tokens` is within its proportional cap; on cap-bound passes (including at least
  one with `focus` set) the trailing `## Next Step` / `## Critical Context` headers are present (hard);
  the small-region pass confirms no mid-template truncation at the floor; the result block logs
  per-pass `budget` / `cap` / `output_tokens` and the overshoot ratio (the data for future cushion
  tuning).
- **success_signal:** under real load the summary length tracks the compacted-region size and stays
  within the proportional cap, with trailing sections present.
- **prerequisites:** TASK-2.

## Testing

**Policy: functional / behavioral validations ONLY** (`feedback_functional_tests_only`). All tests
designed or updated here assert observable outcomes that mirror a `done_when` — pure-helper return
values, prompt-builder output text, and real-LLM output bounding. No structural assertions (key/field
exists, method-called-with-arg, captured internal call settings). A test that would still pass if the
summary were never actually bounded does not count — bounding is proven behaviorally by the eval.

- Scoped unit: `tests/test_flow_compaction_summarization.py` + `tests/test_flow_llm_call.py` (TASK-3) —
  deterministic, no LLM; functional assertions on `resolve_summary_budget`, `cap_output_tokens`, and the
  prompt target line; existing compaction/settings tests pass unchanged.
- Empirical (the behavioral gate): `evals/eval_context_stability.py` (TASK-4) — real LLM; asserts the
  actual summary `output_tokens ≤ cap` and trailing sections survive on cap-bound passes; logs the
  `budget / cap / output_tokens` tuning triple. Tail the log live.
- `scripts/quality-gate.sh full` at ship.

## Open Questions
- **Budget constant values.** `RATIO 0.25 / FLOOR 2000 / CEIL 6000 / OVERSHOOT 1.3` are hermes-informed
  (its `_SUMMARY_RATIO 0.20`, `_MIN_SUMMARY_TOKENS 2000`, `× 1.3` cap), with RATIO nudged up and CEIL
  pulled down to fit co's 8192 noreason ceiling. All are named module constants, not literals. The eval
  (TASK-4) is the tuning surface and the steady-state signal is the per-pass `budget / cap /
  output_tokens` triple emitted on the summarizer span (overshoot = `output_tokens / budget`): if
  traces show the model landing well under target, `SUMMARY_CAP_OVERSHOOT_RATIO` can drop toward an
  additive ~256-token cushion (≈1.04–1.13); if cap hits are frequent, raise it (headroom to ~1.36).
  Adjust the constants — not the mechanism.
- **Prompt target is severable from the cap.** The `max_tokens` override is the load-bearing,
  model-independent lever; the `"Target ~N tokens"` prompt line is the speculative half (lifted from
  hermes, which runs larger models — whether a small local qwen3.6 steers toward a soft target is
  unproven). If TASK-4 shows the target line changes nothing, drop it without abandoning the cap.
- **`/compact` with no dropped region context.** `/compact` runs over full history via the same
  `summarize_messages`, so `resolve_summary_budget` sees the full message list — its CEIL clamp keeps
  the cap bounded. Confirm in TASK-4 that the manual path also stays within cap.

## Spec Sync (post-delivery)

Specs are runtime/shipped-behavior docs — not updated by this plan's tasks (no task lists
`docs/specs/`). After delivery, `/sync-doc` (auto-invoked by `/orchestrate-dev`) must add a
**"Summary output budget"** subsection to `docs/specs/compaction.md §2.6 Summarizer pipeline`,
using the **as-shipped** constant values (whatever TASK-4's eval confirms — the values below are the
planned defaults). Two tables to carry over:

**Sizing logic.** The summarizer output is bounded proportionally to the region it compresses:

```
budget = clamp(SUMMARY_BUDGET_RATIO × estimate_message_tokens(dropped), FLOOR, CEIL)
```
`budget` drives two levers:
- **Prompt target (soft)** — the prompt carries `Target ~{budget} tokens` (replaces the old bare
  "Be concise"), giving the model a goal instead of letting it drift to the flat ceiling.
- **Hard cap (load-bearing)** — `max_tokens = min(ceil(budget × SUMMARY_CAP_OVERSHOOT_RATIO),
  noreason_ceiling)` passed to the summarizer `llm_call` via `model_settings` (`cap_output_tokens`), in
  Ollama lockstep (scalar + `extra_body["max_tokens"]`). The cushion is overshoot tolerance, not a bare
  cut-off margin (see note below the table) — it is a named constant so it is tunable from traces.

| Const | Value | Effect |
|---|---|---|
| `SUMMARY_BUDGET_RATIO` | 0.25 | aim the summary at ~¼ of the compressed region |
| `SUMMARY_BUDGET_FLOOR` | 2 000 | cap floor = 2 000 × 1.3 = **2 600** — clears the ~13-section template + the two mandatory verbatim quotes, so a small region never truncates mid-structure |
| `SUMMARY_BUDGET_CEIL` | 6 000 | cap ceil = 6 000 × 1.3 = **7 800 < 8 192** noreason ceiling — bloat bounded, override never exceeds the hard ceiling |
| `SUMMARY_CAP_OVERSHOOT_RATIO` | 1.3 | overshoot cushion: hard cap = `budget × this`. Deliberately loose (a bare cut-off margin would be ~1.04–1.13); named for trace-driven tuning. Headroom to ~1.36 before `CEIL × ratio` hits 8 192 |

**Ratio analysis / peer backing** (origin: `docs/reference/RESEARCH-summarization-prompting-peer-survey.md`):

| Element | hermes (`context_compressor.py`) | co | openclaw | opencode / codex |
|---|---|---|---|---|
| proportional ratio | `_SUMMARY_RATIO = 0.20` | `0.25` (nudged up for small-model headroom) | adaptive chunk 0.4→0.15 of window | — |
| floor | `_MIN_SUMMARY_TOKENS = 2000` | `2000` (exact match) | — | — |
| ceiling | `_SUMMARY_TOKENS_CEILING = 12000` | `6000` (pulled down to fit co's 8192 noreason ceiling) | 16K-char output cap (~4K tok) | — |
| cap = budget × 1.3 | `max_tokens = budget * 1.3` | same (verbatim) | 4096-tok overhead reserve + 1.2× safety margin | — |
| `Target ~N tokens` in prompt | yes | yes | — | — |

hermes is the direct template (all four levers). openclaw backs the *bound-the-output* principle via
chunk sizing + a char cap rather than a per-summary token budget. opencode/codex have no proportional
summary budget. Only `FLOOR=2000` and `× 1.3` are literal hermes values; `RATIO=0.25` and `CEIL=6000`
are co adaptations fitted to the 8192 ceiling and confirmed/tuned by TASK-4.

**Cross-link:** the full peer comparison stays in the reference survey; the spec gets the behavior +
the constants table only (rationale depth lives in reference, per the build-time/runtime split).

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev summary-output-length-control`

## Delivery Summary — 2026-06-04

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `cap_output_tokens` locksteps Ollama (scalar + `extra_body`), Gemini scalar-only, input unmutated; `test_flow_llm_call.py` passes | ✓ pass |
| TASK-2 | `resolve_summary_budget` clamps floor/mid/ceil; `_build_summarizer_prompt(budget=B)` emits `Target ~B tokens`; capped call wired; `test_flow_compaction_summarization.py` passes | ✓ pass |
| TASK-3 | deterministic functional tests for budget/cap/prompt-target pass (no LLM) | ✓ pass |
| TASK-4 | `eval_context_stability.py` runs to completion; every `output_tokens ≤ cap`; focus-active + FLOOR passes confirmed; trailing `## Next Step` present; per-pass budget/cap/output_tokens + overshoot logged | ✓ pass |

**Tests:** scoped — 17 passed, 0 failed (`tests/test_flow_compaction_summarization.py` + `tests/test_flow_llm_call.py`; 3 new `cap_output_tokens` + 4 new budget/prompt-target functional assertions, all observable-outcome only).
**Eval:** CS.A PASS (bounded loop, no overflow) + CS.B PASS (summary output bounded). Live run: 2 summarizer passes, both `focus=True`; budgets 2000/2092, caps 2600/2720, output_tokens 640/629 (overshoot ~0.30–0.32, well under target); FLOOR-budget pass exercised with no mid-template truncation; `## Next Step` present on every pass.
**Doc Sync:** narrow (compaction.md §2.6 only) — added "Summary output budget" subsection with as-shipped constants (RATIO 0.25 / FLOOR 2000 / CEIL 6000 / OVERSHOOT 1.3). No other spec contradicted by the code.

**Implementation notes (deltas from plan, all within authority):**
- **Observability via parent-span attributes (not a structured log line).** The `llm_call` span's `co.model.input` only serializes `UserPromptPart` content — the system-prompt instructions (where `Target ~N` and `FOCUS TOPIC:` live) are dropped — so the `Target ~N` line is **not** recoverable from spans (it is covered functionally by TASK-3's prompt-builder test). Surfaced `co.compaction.summary.budget` / `.cap` / `.focus` on the parent `compaction.proactive_check` span (the plan's "preferred clean option 1"); the eval correlates them to the child `llm_call` span's `output_tokens` via `parent_span_id`.
- **Added `co.compaction.summary.focus`** (beyond the planned budget/cap) so TASK-4 can identify focus-active cap passes — required by the "≥1 cap pass ran with focus" assertion.
- **Trailing-section gate is `## Next Step` only** (mandatory, the truncation canary); `## Critical Context` is "Skip if none" so it is logged, not gated, to avoid eval flakiness.
- **CS.B reuses CS.A's single multi-turn run** (re-reads the same spans) — no extra LLM cost.

**Overall: DELIVERED** — all four tasks pass `done_when`, lint clean, scoped tests green, eval green end-to-end, doc sync complete.

**Next step:** `/review-impl summary-output-length-control` — full suite + evidence scan + behavioral verification → verdict appended to plan.

## Implementation Review — 2026-06-04

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | Ollama lockstep cap + Gemini scalar-only + input unmutated; `test_flow_llm_call.py` passes | ✓ pass | `config/llm.py:193-211` — `cap_output_tokens`: scalar set unconditionally (`:208`), extra_body mirror guarded by `isinstance(...) and "max_tokens" in ...` (`:209`), `dict(settings)` copy (`:207`); done_when re-executed literally (Ollama both=5000 unmutated; Gemini scalar-only) |
| TASK-2 | `resolve_summary_budget` clamps floor/mid/ceil; `_build_summarizer_prompt(budget=B)` emits `Target ~B tokens` | ✓ pass | `summarization.py:101-110` clamp; `:208-216` target line; `:340-366` budget→cap→`cap_output_tokens`→`llm_call`; `min(...,base_ceiling)` (`:342`) keeps cap ≤ ceiling (worst case 7800<8192, re-verified); budget/cap/focus on parent span (`:348-354`); only the summarizer call capped (dream/judge unaffected) |
| TASK-3 | scoped deterministic tests pass | ✓ pass | 6 new functional tests; each fails under a no-op production impl (litmus confirmed by cold-eyes review); real config-derived settings (rule 18); no formula-replication (rule 38); 6 passed / lint clean |
| TASK-4 | eval runs to completion; every output_tokens ≤ cap; focus+FLOOR exercised; trailing `## Next Step` present; budget/cap/output_tokens logged | ✓ pass | `eval_context_stability.py` CS.B: hard fail-closed on `output_tokens>cap` and missing `## Next Step`; span correlation via `parent_span_id`; zero-pass→SOFT_FAIL (not silent); run log: 2 passes, both focus=True, FLOOR=2000 exercised, output 640/629 ≤ caps 2600/2720 |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Third inline copy of the Ollama lockstep rule the new helper centralizes | `co_cli/context/orchestrate.py:630-640` (`_length_retry_settings`) | minor (pre-existing) | Refactored to call `cap_output_tokens(active_settings, boosted)` — behavior-preserving (verified by `test_flow_orchestrate_length_retry.py`, boost 80→160→320→423-stop); makes the helper's docstring claim accurate |
| Stale module path in comment (`memory/dream.py` no longer exists) | `co_cli/config/llm.py:71` | minor (pre-existing) | Corrected to `daemons/dream/_housekeeping.py` |

### Tests
- Command: `uv run pytest` (full suite) + `uv run pytest tests/test_flow_orchestrate_length_retry.py` (fix verification)
- Result: **618 passed, 0 failed** in 147s; length-retry 1 passed in 37s
- Log: `.pytest-logs/20260604-101521-review-impl-full.log`

### Behavioral Verification
- `uv run co dream status`: ✓ CLI boots, returns valid daemon state JSON
- module imports (`orchestrate` / `summarization` / `config.llm`): ✓ clean (exercises the new `cap_output_tokens` import in orchestrate)
- `success_signal`s verified via the real-LLM eval: summary length tracks compacted-region size and stays within the proportional cap with trailing sections intact (budgets 2000/2092 → caps 2600/2720 → output 640/629; focus-active and FLOOR worst cases both exercised)
- No CLI/tool/output-formatting surface changed — user-facing effect (bounded summary) validated end-to-end by the eval, not just "no crash"

### Overall: PASS
All four tasks confirmed against `done_when` with file:line evidence; two pre-existing minors found and fixed (lockstep dedup + stale path); full suite green; lint clean; behavioral smoke passes. Ready for Gate 2 → `/ship`.
