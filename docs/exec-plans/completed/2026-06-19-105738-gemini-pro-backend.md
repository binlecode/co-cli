# Support Gemini 3.1 Pro as an additional backend (selectable alongside the default Ollama/qwen)

Task type: backend enablement — add a large cloud reasoning model as a *selectable* provider/model option. Config-tier addition on existing plumbing + one real design decision (context-window budget) + live validation. Ollama/qwen stays the default; the weak-model reflex program continues for the local path.

## Context — research/scope pass (2026-06-19)

**Existing Gemini support.** co already has a Gemini provider: `provider: Literal["ollama","gemini"]` (`co_cli/config/llm.py:225`), `_gemini_settings()` → `GoogleModelSettings`, `GEMINI_API_KEY` resolution, `validate_config()`, and reasoning/noreason mode mapping. But `_LLM_SETTINGS["gemini"]` only carries **flash-tier** models: `gemini-3-flash-preview` (default), `gemini-2.5-flash`, `gemini-2.5-flash-lite`. No pro/large entry exists. So this milestone is a **config-tier addition on working plumbing**, not new architecture.

**Target model (verified live, not assumed).** The user said "gemini-3-pro"; the current pro-tier API id is **`gemini-3.1-pro-preview`** (the dev guide lists `gemini-3-pro` only as the image variant `gemini-3-pro-image-preview`). Specs:
- **Input context: 1,000,000 tokens.** Output: **up to 64k** (65,536).
- `thinking_level` ∈ `minimal | low | medium | high` (default `high`). Mirrors the `gemini-3-flash-preview` knob (which co sets to `MINIMAL` for noreason).
- Multimodal (image/video/audio input).
- Long-context **pricing cliff at >200K tokens** (all tokens billed at long-context rate above that); GA pricing effective 2026-07-01.
- Sources: [Gemini 3 dev guide](https://ai.google.dev/gemini-api/docs/gemini-3), [models](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing).

**Integration facts (grounded in source).**
- Model-key resolution is `self.model.split(":")[0]` (`llm.py:255,279`). Gemini ids have no `:`, so the `_LLM_SETTINGS` key must be the **exact** full id `gemini-3.1-pro-preview`. No variant-stripping needed.
- `validate_config` requires the model-key to exist in `_LLM_SETTINGS` AND to have a `reasoning` entry to be usable as the main agent (`llm.py:281-284`). gemini-3.1-pro supports reasoning (thinking_level high) → it gets both `reasoning` + `noreason` blocks, so it is a full main-agent backend (unlike 2.5-flash, which is noreason-only).
- The `num_ctx` floor/ceiling probe checks are **Ollama-only** (`ollama_num_ctx()` returns None for Gemini; the Modelfile probe is skipped). For Gemini, `max_context_tokens` is purely the compaction reference, not a probed ceiling.

**The context-window decision is now SETTLED BY SHIPPED CODE (Plan A landed after this plan was drafted).** This plan originally framed `max_context_tokens` as the one open design decision. Plan A (`model-profile-01-seam`, shipped) resolved it:
- `_default_context_from_profile` (`llm.py:284-288`) sets `max_context_tokens = profile_max_context_tokens(resolve_model_profile(self))` whenever the field is not explicitly configured. `resolve_model_profile` (`llm.py:52-58`) returns `FRONTIER` for any non-Ollama provider, and `profile_max_context_tokens(FRONTIER)` returns **`FRONTIER_MAX_CONTEXT_TOKENS = 524_288`** (`llm.py:36,61-69`) — half the 1M window, clamped by `compaction_ratio=0.50`.
- So **selecting gemini-3.1-pro automatically inherits the 524k FRONTIER budget, centrally, with zero new code.** The original OQ-1 proposal ("raise to ~200K under the pricing cliff") is **superseded**: Plan A explicitly deferred the pricing-cliff cost-clamp (its OQ-3), so the cliff is not an active lever and 524k stands. OQ-1 is closed (see Open Questions).
- The Ollama-only `num_ctx` ceiling/floor probe is already gated off the gemini path: `ollama_num_ctx()` returns `None` for non-Ollama (`llm.py:312-317`). gemini-pro boots without tripping it.

**Consequence for scope:** TASK-2 collapses from "implement context-budget handling" to "confirm gemini-pro inherits 524k via the profile resolver + assert it in a config test." No new budget code.

**Other verified current-state facts:**
- `thinking_config` casing in shipped config is **UPPERCASE** (`"MINIMAL"`, `llm.py:154`) — the illustrative lowercase in High-Level Design below is corrected to `"HIGH"`/`"MINIMAL"`.
- `_gemini_settings` (`llm.py:222-228`) maps a `thinking_config` dict → `google_thinking_config` generically, so adding a `reasoning` thinking_config for pro just works.
- Gemini model ids appear outside `_LLM_SETTINGS`/`DEFAULT_LLM_MODELS` at three known sites, all harmless to the new pro entry: `tests/test_flow_llm_call.py:116` (`gemini-3-flash-preview`), `evals/_settings.py:68` (`EVAL_JUDGE_MODEL = "gemini-3.5-flash"` — a judge id, deliberately NOT in `_LLM_SETTINGS`; pre-existing, out of scope), and `tests/test_flow_model_profile.py:34` (bare `provider="gemini"`). TASK-1's grep should EXPECT these hits, not assert a single reference.

**Decisions resolved at Gate 1 (this cycle):** target id = `gemini-3.1-pro-preview` (OQ-3); main reasoning `thinking_level = HIGH`, noreason `MINIMAL` (OQ-2).

## Problem & Outcome

**Problem.** Users who want a strong cloud reasoning backend can only select flash-tier Gemini today; there is no large/pro option, and the context budget is hard-tuned to the local model.

**Failure cost.** Without it: co is locked to the weak-local model or fast-but-shallow flash for any task needing a strong reasoner; the recently-diagnosed weak-model failures (recall, thrash) have no strong-model escape hatch. Over-scoping risk: rebuild context/compaction to be fully model-aware, or wire vision/multimodal, when the milestone is "make pro selectable."

**Outcome.** `provider=gemini, model=gemini-3.1-pro-preview` is a working, validated main-agent backend: boots, passes config validation, drives reasoning + noreason turns with tool calls, on a context budget that is at least safe (and ideally tuned to its window). Ollama/qwen remains the untouched default.

**Shippable contract:** the config entry + the context-budget handling for the Gemini-pro selection + a live smoke proving a real turn (reasoning + tool call) — clearing validation with no regression to the Ollama default path.

## Behavioral Constraints
- **Do not change the default backend.** `DEFAULT_LLM_PROVIDER`/`DEFAULT_LLM_MODELS` stay Ollama/qwen. gemini-3.1-pro is opt-in via config/env only.
- **No hardcoded `~/.co-cli`**; use config constants. Centralized settings only — knobs live in `_LLM_SETTINGS`, never coined inline (`feedback_evals_centralized_settings`, `feedback_tests_use_config_model_settings`).
- **Exact model id** `gemini-3.1-pro-preview` as the `_LLM_SETTINGS` key (matches `model.split(":")[0]`).
- **Vision is OUT of scope** — gemini-3.1-pro is multimodal, but co's image plumbing is unbuilt (`reference_configured_model_vision`); vision is its own plan. Backend = text + tool-calling.
- Tests/repros hit `llm.host`/provider from config and use `reasoning_model_settings()` / `noreason_model_settings()` — never coin `ModelSettings`/`GoogleModelSettings` inline.
- Live Gemini calls need `GEMINI_API_KEY` (in `~/env-secrets/`, never the repo). Watch call timing; RCA-first on slow/stalled calls.

## Scope

### In scope
- `_LLM_SETTINGS["gemini"]["gemini-3.1-pro-preview"]` — `reasoning` block (thinking_level for depth; temp/top_p; max_tokens ≤ 65,536) + `noreason` block (thinking_level `minimal`; lower max_tokens), mirroring `gemini-3-flash-preview`.
- Context-budget confirmation: gemini-pro inherits the shipped FRONTIER 524k budget via Plan A's profile resolver (`_default_context_from_profile`). No new budget code — TASK-2 asserts the inheritance and the unchanged Ollama path.
- `validate_config` confirmation (api_key present + reasoning entry → usable as main model); no Ollama probe on the Gemini path.
- Live smoke: boot with `provider=gemini, model=gemini-3.1-pro-preview`, drive a reasoning turn + a noreason helper call, each making a tool call.

### Out of scope (deferred with rationale)
- **Vision/multimodal** — image plumbing unbuilt; separate plan.
- **Making gemini-pro the default** — explicitly an *additional option* (user choice).
- **Model-aware auto-derivation of `max_context_tokens`** beyond what OQ-1 settles — a general refactor; only do the minimum the selection needs.
- **Per-model prompt calibration** (strong-Gemini vs weak-local prompt) — that is the `per-model-prompt-calibration` plan's job; this milestone is backend enablement, not prompt tuning. (The reflex program continues independently for the local path.)
- **Cost guardrails / long-context-pricing alerts** — noted (>200K cliff), not built here. Because gemini-pro runs on the shipped 524k FRONTIER budget, a turn can cross the >200K cliff with no user-visible signal — this is an **accepted known cost characteristic of opting into pro**, to be recorded as such in the delivery summary (a decision on record, not implicit).
- Eval runs against Gemini — evals stay on the configured model via centralized settings; no eval change unless a later decision routes evals to Gemini.

## High-Level Design

One config addition + one budget decision + validation/smoke. No new modules; `build_model`/`_gemini_settings`/`llm_call` already handle the Gemini path generically.

```python
# _LLM_SETTINGS["gemini"], new entry. Casing UPPERCASE to match shipped config
# (llm.py:154). thinking_level resolved: HIGH (reasoning) / MINIMAL (noreason).
"gemini-3.1-pro-preview": {
    "reasoning": {            # main agent
        "temperature": 1.0, "top_p": 0.95, "max_tokens": 65536,
        "thinking_config": {"thinking_level": "HIGH"},
    },
    "noreason": {             # helper calls (summarizer, judge, memory-merge)
        "temperature": 0.7, "top_p": 0.8, "max_tokens": 16384,
        "thinking_config": {"thinking_level": "MINIMAL"},
    },
},
```
Note: shipped `gemini-3-flash-preview` omits `thinking_config` on its `reasoning` block (relies on the model default). For pro we set it explicitly to `HIGH` so the depth knob is pinned and visible, not implicit.

## Tasks

✓ DONE **TASK-1 — Add the `gemini-3.1-pro-preview` inference entry + validate**
- files: `co_cli/config/llm.py`
- done_when: `_LLM_SETTINGS["gemini"]["gemini-3.1-pro-preview"]` has `reasoning` + `noreason` blocks (thinking_level + temp/top_p/max_tokens, output ≤ 65,536), mirroring the flash-preview shape; with `provider=gemini, model=gemini-3.1-pro-preview, GEMINI_API_KEY` set, `validate_config()` returns no error and `reasoning_model_settings()` / `noreason_model_settings()` produce valid `GoogleModelSettings` (asserted in a config-level test using the real settings constructors, not inline-coined); the Ollama default path is unchanged (existing config tests still pass); repo-wide grep confirms no other site hardcodes the gemini model list; full suite passes.
- success_signal: selecting gemini-3.1-pro as the main model passes validation and yields correct settings for both modes.
- prerequisites: none

✓ DONE **TASK-2 — Confirm gemini-pro inherits the FRONTIER 524k budget (Plan A already implements it)**
- files: `tests/test_flow_model_profile.py` (extend the existing gemini-profile test — do NOT add a near-duplicate new file; `:32-46` already asserts gemini→FRONTIER→524k and the override-wins case)
- done_when: the existing `tests/test_flow_model_profile.py` gains two net-new assertions on a model-pinned `LlmSettings(provider="gemini", model="gemini-3.1-pro-preview", api_key="x")`: (1) `max_context_tokens == FRONTIER_MAX_CONTEXT_TOKENS` (524_288) with the field unset — proving gemini-pro inherits the FRONTIER budget with no new code; (2) `ollama_num_ctx()` returns `None` on those settings (Ollama probe skipped on the gemini path). The pre-existing weak-local/override assertions stay green; full suite passes.
- success_signal: gemini-pro runs on the shipped 524k FRONTIER budget with zero impact on the Ollama path, proven by an assertion not just inspection.
- prerequisites: TASK-1

✓ DONE **TASK-3 — Live backend smoke (UAT)**
- files: `tmp/gemini_pro_smoke.py` (scratch, not shipped)
- done_when: with `GEMINI_API_KEY` set, a smoke retargets deps to the gemini-pro backend via env (`CO_LLM_PROVIDER=gemini`, `CO_LLM_MODEL=gemini-3.1-pro-preview` — honored by `LLM_ENV_MAP`, `llm.py:183-188`) — NOT by cloning `weather_smoke.py`'s `eval_deps()`/Ollama path, and it must NOT call `ensure_ollama_warm()` (gemini path). It drives (a) a reasoning-mode turn that makes a tool call and returns a final answer, and (b) a noreason helper call (e.g. via `llm_call`) — both succeed against the live API; tail the log, RCA-first on slow calls; observed tool-call order + timings recorded in the delivery summary. (Smoke is the gate; no pytest hits the live API.)
- success_signal: gemini-3.1-pro actually drives a real co turn end-to-end, reasoning + tool-calling.
- prerequisites: TASK-1, TASK-2

## Testing
- Config-level pytest: validation + settings construction for gemini-3.1-pro (real `GoogleModelSettings`, centralized knobs); Ollama default-path config tests unchanged. Pipe to `.pytest-logs/`.
- No pytest against the live Gemini API; TASK-3 smoke is the live gate (needs `GEMINI_API_KEY`).
- Watch LLM call timing live; RCA-first on slow/stalled calls; never fold cold-start into a call budget.

## Open Questions

All three original open questions are resolved; none survive as deferred.

1. **`max_context_tokens` — RESOLVED by shipped code (Plan A).** gemini-pro inherits `FRONTIER_MAX_CONTEXT_TOKENS = 524_288` via `_default_context_from_profile`/`resolve_model_profile` (`llm.py:284-288,52-69`). The original "~200K under the pricing cliff" proposal is superseded — Plan A deferred the cliff clamp. No decision or code needed; TASK-2 only asserts the inheritance.
2. **Reasoning thinking_level — RESOLVED at Gate 1: `HIGH`** for the main agent (strong-reasoner use case), `MINIMAL` for noreason helpers. Uppercase per shipped config casing.
3. **Model id — RESOLVED at Gate 1: `gemini-3.1-pro-preview`** (current pro-tier preview id). A preview id can be deprecated independent of GA pricing and would silently break the boot path, so the GA-id swap needs an owner: **the delivery summary must carry a dated follow-up marker** (`revisit gemini-3.1-pro-preview → GA id on/after 2026-07-01`) so it is not lost. Not a blocker for this milestone.

## Decisions

Critique converged C1 — both reviewers approve, Blocking none. TL refinement against shipped Plan A (pre-critique) closed OQ-1 and collapsed TASK-2; all C1 minors adopted.

| Issue | Decision | Rationale | Change |
|-------|----------|-----------|--------|
| TL pre-critique | refine | Plan A shipped after this plan was drafted; `_default_context_from_profile` already gives gemini-pro the 524k FRONTIER budget (`llm.py:284-288,52-69`) | OQ-1 closed; TASK-2 recast as a confirmation test; thinking_level casing fixed to UPPERCASE; Context gains a current-state-vs-Plan-A subsection |
| OQ-2 (Gate 1) | resolve | strong-reasoner use case wants depth; helpers stay fast | reasoning `thinking_level=HIGH`, noreason `MINIMAL` pinned in TASK-1 / High-Level Design |
| OQ-3 (Gate 1) | resolve | current pro-tier preview id, assumed by Plan 02 | target = `gemini-3.1-pro-preview`; GA-id swap given a dated follow-up owner |
| CD-m-1 | adopt | `weather_smoke.py`/`eval_deps()` runs the configured Ollama backend with no override and warms Ollama | TASK-3 done_when names the env retarget (`CO_LLM_PROVIDER`/`CO_LLM_MODEL`) and forbids `ensure_ollama_warm()` on the gemini path |
| CD-m-2 | adopt | grep claim was wrong — judge id `gemini-3.5-flash` (`evals/_settings.py:68`) + profile test also reference gemini | Context grep claim corrected to EXPECT three known harmless hits |
| CD-m-3 | adopt | `tests/test_flow_model_profile.py:32-46` already asserts gemini→524k; a new file would be a near-duplicate clean-tests later cuts | TASK-2 extends the existing test with two net-new assertions (model-pinned id + `ollama_num_ctx() is None`), not a new file |
| PO-m-1 | adopt | a preview id can be deprecated independent of GA pricing and silently break boot | OQ-3 + delivery summary carry a dated `revisit on/after 2026-07-01` marker |
| PO-m-2 | adopt | 524k budget can cross the >200K cliff unsignalled | Out-of-scope note records this as an accepted known cost of opting into pro, to be stated in the delivery summary |

## Final — Team Lead

Plan approved (TL). Critique converged C1 — both reviewers approve, Blocking none.

> Gate 1 — PO review required before proceeding.
> Right problem (selectable strong cloud backend, default unchanged)? Correct scope (config entry + 524k-inheritance confirmation + live smoke; vision/calibration/default-swap/cost-guardrails excluded)?
> Prerequisites: none beyond `GEMINI_API_KEY` (present). Once approved, run: `/orchestrate-dev 2026-06-19-105738-gemini-pro-backend`.
> Downstream: shipping this unblocks `2026-06-19-123306-model-profile-02-frontier-overlay` (TASK-1 smoke + TASK-3 measurement need this backend live).

## Delivery Summary — 2026-06-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_LLM_SETTINGS` pro entry (reasoning HIGH + noreason; output ≤65,536); validate_config clean; both modes yield valid GoogleModelSettings; Ollama path unchanged; grep finds no hardcoded model list; suite green | ✓ pass |
| TASK-2 | pinned pro model inherits FRONTIER 524k budget (field unset); `ollama_num_ctx()` is None; pre-existing asserts green | ✓ pass |
| TASK-3 | live env-retargeted smoke: reasoning turn w/ tool call + noreason `llm_call`, both against live API | ✓ pass |

**Tests:** scoped — 24 passed (test_flow_model_profile + bootstrap_config + llm_call), 0 failed. Model-profile suite re-run after the LOW fix: 6 passed.
**Doc Sync:** fixed — added the `gemini-3.1-pro-preview` row to the `_LLM_SETTINGS` table in `docs/specs/config.md` (narrow scope; config-data addition, no shared-module/API/schema change).

**Live smoke (TASK-3, gemini-3.1-pro-preview):**
- (a) reasoning turn: tool calls in order → `1. shell_exec {'cmd': 'date'}`; model_requests=2; elapsed 4.4s; correct final answer ("Friday, June 19, 2026").
- (b) noreason `llm_call`: reply `'PONG'`; elapsed 1.9s.
- Backend confirmed: provider=gemini, model=gemini-3.1-pro-preview, max_context_tokens=524288, validate_config=None.

**Deviation from plan (resolved during dev):** the plan pinned noreason `thinking_level=MINIMAL` (mirroring flash). The live API rejects it: `400 INVALID_ARGUMENT — Thinking level MINIMAL is not supported for this model`. MINIMAL is flash-only; pro's lowest supported level is `LOW`. Changed noreason to `LOW` (faithful to the plan's intent — "lowest level to keep helpers fast"). Reasoning stays `HIGH`. TASK-1 test + config.md table updated accordingly.

**Accepted known cost (PO-m-2):** gemini-pro runs on the shipped 524k FRONTIER budget; a turn can cross the >200K long-context pricing cliff with no user-visible signal. This is an accepted characteristic of opting into pro — cost guardrails are out of scope (deferred).

**Dated follow-up (OQ-3 / PO-m-1):** revisit `gemini-3.1-pro-preview` → GA id on/after 2026-07-01. A preview id can be deprecated independent of GA pricing and would silently break the boot path.

**Overall: DELIVERED**
All three tasks pass; lint clean; scoped tests green; doc synced. One in-flight deviation (noreason MINIMAL→LOW) forced by a live API constraint, resolved within plan intent.

## Implementation Review — 2026-06-19

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | pro entry (reasoning HIGH + noreason; output ≤65,536); validate_config clean; both modes yield valid GoogleModelSettings; Ollama path unchanged; grep finds no hardcoded model list | ✓ pass | `co_cli/config/llm.py:161` — `gemini-3.1-pro-preview` with reasoning(`thinking_level=HIGH`, max_tokens 65536) + noreason(`LOW`, 16384); test `tests/test_flow_model_profile.py:51-59` asserts `validate_config() is None` + both `*_model_settings()` produce the right `google_thinking_config`/`max_tokens`; grep confirms only the 3 known-harmless gemini hits (default flash id `llm.py:27`, `tests/test_flow_llm_call.py:116`, judge id `evals/_settings.py:68`) |
| TASK-2 | pinned pro model inherits FRONTIER 524k (field unset); `ollama_num_ctx()` is None | ✓ pass | `tests/test_flow_model_profile.py:63-65` — `max_context_tokens == FRONTIER_MAX_CONTEXT_TOKENS == 524_288` and `ollama_num_ctx() is None`; pre-existing weak-local/override asserts still green |
| TASK-3 | live env-retargeted smoke: reasoning turn w/ tool call + noreason `llm_call`, both against live API | ✓ pass (recorded) | Live gate cleared in Delivery Summary: reasoning turn `shell_exec{date}` 4.4s, noreason `llm_call`→`PONG` 1.9s; not re-run in review (would burn API quota, no new signal) |

### Issues Found & Fixed
No issues found. Stub-litmus on both new tests: strong (observed-value assertions, no mocks/fakes) — gutting `reasoning_model_settings()`/the profile resolver would fail them.

_Scope note (non-blocking):_ working tree also carries unrelated concurrent work — `co_cli/tools/user_profile/{view,write}.py`, `docs/.../model-profile-02-frontier-overlay.md`, `docs/reference/RESEARCH-self-learning-co-vs-hermes.md`, `uv.lock` — not part of this delivery. `docs/specs/config.md` is the expected TASK-1 doc-sync. Stage only this plan's files at ship time.

### Tests
- Command: `uv run pytest -v`
- Result: 796 passed, 0 failed (186.74s)
- Log: `.pytest-logs/20260619-183843-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads with the new config entry)
- `success_signal` TASK-1/2 verified via config tests (validate clean + 524k inheritance, both modes' settings correct); TASK-3 live success_signal recorded in Delivery Summary (gemini-3.1-pro drives a real reasoning+tool-call turn). Chat interaction non-gating (LLM-mediated).

### Overall: PASS
Faithful config-tier addition on working plumbing; strong tests, full suite green, boot smoke clean — ready to ship (stage only this plan's files).
