# Model-profile 02 — frontier overlay (`overlays/frontier.md`)

## Goal
Author `overlays/frontier.md`: the prompt content a **frontier (strong) model** specifically needs, borrowed from the peer-converged parity. BASE stays model-agnostic; this overlay holds only what a frontier reasoner needs that the neutral core does not.

Mechanism is shipped (Plan 1b: `build_rules_block()` = BASE, `build_profile_overlay(frontier)` appends `overlays/frontier.md`). **Nothing to build — this plan is content only.**

co's frontier backend today is gemini (`gemini-3.1-pro-preview`), resolved to the single `frontier` profile. The per-provider split (`frontier` → `gemini`/`openai`/…) is **deferred until a 2nd frontier backend is wired** — until then `overlays/frontier.md` IS the frontier overlay.

## Peer sources (converged parity)
- hermes `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` / `OPENAI_MODEL_EXECUTION_GUIDANCE` (`prompt_builder.py`)
- opencode `gemini.txt` / `gpt.txt` (`system.ts:25-39` selector)
- codex `gpt_5_codex_prompt.md` + `prompts/`
- openclaw `gpt5-prompt-overlay.ts`

Borrow the form and content these converge on for a strong reasoner — primarily output compactness/verbosity discipline and output-shape discipline (see *Coordination with shipped Plan 03* below for what is explicitly out of scope). **Authoring caveat:** the peers' *gemini* guidance targets gemini-flash (a weak tier) and is hand-holding; co's frontier is gemini-3.1-pro, so weight the strong-model sources (gpt-5/codex/openclaw) over flash-tier gemini guidance. Judgment per section, recorded with its peer source.

## Scope
**In:** extract the frontier-specific guidance the peers converge on, adapt to co's tool surface, author into `overlays/frontier.md` as append-only `##` sections.
**Out:** BASE neutralization and the weak overlay (**Plan 03, shipped v0.8.434**); the mechanism (1b); the 2nd-provider split (deferred — co has no 2nd frontier backend; authoring one now is a faith build); **keep-going/persistence reflexes** (Plan 03 classified act-this-turn/persistence as weak-model compensation a strong reasoner does natively — by the partition rule it is dead-weight for frontier; see *Coordination* below).

## Coordination with shipped Plan 03 (G1 scope decisions)
Plan 03 (shipped v0.8.434) made BASE model-agnostic and relocated the weak scaffolding into `overlays/weak_local.md` (`Execution`, `Completeness`, `Intent classification`, `When NOT to over-plan`, `Error recovery`, `Conciseness`). Two of those touch concepts the peers also prompt for *frontier* models, so this overlay is authored against the shipped weak overlay, not in a vacuum:

- **Persistence / keep-going — DROPPED from frontier.** Plan 03's thesis is that act-this-turn / persistence / thoroughness is weak-model compensation a strong reasoner does natively (that is *why* `Execution` lives in `weak_local`, not BASE). The partition rule (`docs/specs/prompt-assembly.md` §2.1: overlay only when dead-weight for a strong reasoner) therefore excludes it from frontier. The peers' frontier persistence prompts are deliberately not borrowed. If a live-gemini smoke later shows the frontier model stalling, revisit with evidence — not on faith now.

- **Conciseness — frontier keeps a *distinct* compactness reflex.** `weak_local`'s `## Conciseness` is no-preamble / no-chitchat hand-holding (a model habit). Frontier's need is different: gemini-3.1-pro is verbose-by-default and has a >200k pricing cliff, so an output-density / length-budget reflex is a cost lever a strong model genuinely needs beyond the neutral base — the leading frontier-specific candidate. Author it *complementary, not near-duplicate*: frontier = output density / length budget (cost), weak = no preamble/postamble (habit). Do not restate weak's bad-phrase citations. If the two ever converge on the same prose, the content is universal and belongs in BASE, not split across both overlays.

## Tasks

**TASK-1 — Author `overlays/frontier.md` from peer-converged frontier parity**
- files: `co_cli/context/overlays/frontier.md` (create + author), `evals/eval_rule_compliance.py` (one `_INVENTORY` row per authored section; `_PROBES` entry only if a section is single-turn observable), this plan (record peer source per section)
- done_when: frontier-specific sections borrowed from the peers are authored as append-only `##` sections in `overlays/frontier.md` per the rule-authoring standard in `docs/specs/prompt-assembly.md` §2.1 (low-inference reflex form; no `tool_name(` call-syntax; backtick bare tool names); each section records its peer source(s); `uv run python evals/eval_rule_compliance.py --inventory` passes (inventory count = parsed sections); floor guards pass (`tests/test_instruction_budget.py`); full suite passes. If nothing is warranted for a strong frontier model beyond BASE, `overlays/frontier.md` stays empty and that is recorded as the valid outcome.
- success_signal: `overlays/frontier.md` holds exactly the frontier-specific content the peer convergence supports.
- prerequisites: Plan 1b shipped; gemini backend wired (`f5dab436`); `GEMINI_API_KEY`.

## Testing
- `--inventory` + floor guards + full suite on any overlay change.
- Optional (not a gate): a behavioral smoke (`tmp/weather_smoke.py` pattern) on the live gemini path to sanity-check an authored section changes frontier behavior. Peer convergence is the authoring basis; smoke is a spot-check, not a gate.
- No structural/fitness tests on overlay files.

## Decisions
**Reset 2026-06-22 (first-principle rewrite).** Supersedes the prior framing (ablation Δ → "dead-on-frontier set" → joint BASE re-partition). That approach was over-engineered and structurally blind to the actual task: ablation tests whether co's *existing* rules fire, never whether co is *missing* frontier techniques the peers have. The real job is borrowing peer-converged frontier content into an additive overlay; BASE neutralization is Plan 03's. No measurement gate; peer convergence is the evidence.
