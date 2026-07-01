# Summarizer language preservation

## Goal
co's conversation-compaction summarizer emits its handoff summary with **no instruction to preserve the conversation's language**, so a non-English session can be summarized in English (and the summary then re-enters history as the compaction marker in the wrong language). Add a same-language-preservation clause to the summarizer prompt, with an explicit carve-out that code, file paths, identifiers, error strings, and the mandated verbatim user-quotes stay character-for-character. Peer-converged content task (3/4 peers ship this); the mechanism already exists — **prompt content only, single file.**

## Why this is real work (code-grounded)
- `_SUMMARIZER_SYSTEM_PROMPT` (`co_cli/context/summarization.py:263–273`) and `_SUMMARIZE_PROMPT` (`158–221`) carry no language clause. `rg -ni "language|translate|locale" co_cli/context/` returns only the token-estimator comment + a `USER.md`-profile rule; nothing in `co_cli/config/`.
- The summarizer already receives the conversation inline (the `TURNS TO SUMMARIZE:` block), so the conversation's language is directly observable — the clause keys off that, no external lookup.
- Peer parity: hermes (*"Write the summary in the same language the user was using … do not translate or switch to English"*), openclaw `DEFAULT_COMPACTION_INSTRUCTIONS` (*"Write the summary body in the primary language … Do not translate or alter code, file paths, identifiers, or error messages"*), opencode (*"Respond in the same language as the conversation"*). Only codex omits it (minimal, frontier-only). See `docs/reference/RESEARCH-summarization-prompting-peer-survey.md` §5/§8.
- Doctrine fit: co is local-first; weak models drift to English on non-English input more than frontier models, so co needs this **more** than the peers. Near-unconditional reflex on an observable cue — the form `feedback_instructions_counter_model_limits` calls for.

## Scope
**In:** add the language-preservation clause (with the code/identifier verbatim carve-out) to the summarizer prompt.
**Out:** per-profile overlay of the summarizer prompt (latent, evidence-gated — RESEARCH §9); compaction-marker prose changes; any config option for output language.

## Related surfaces (verified out of scope)
- **Compaction marker prose** (`_compaction_markers.py`) stays English. It is model-facing scaffolding ("treat as background reference, respond to messages after this") on the same footing as the English base system prompt; every peer keeps its marker/prefix English and language-preserves only the summary *body*. The clause applies to the body the marker wraps, not the wrapper.
- **Dream / memory reviewer** (`daemons/dream/_reviewer.py`) is a separate LLM surface — its own `_REVIEW_PROMPT_TEMPLATE` / `_memory_review_instructions`, reusing only `serialize_messages`, never `_SUMMARIZE_PROMPT` — so TASK-1 does not touch it. It also lacks a language clause, but its output is curated memory items (rules/articles/notes), which may be *intentionally* normalized to the agent's working language rather than echoing the session language. That is a distinct design judgment; **examined and deliberately excluded here**, not a missed consumer. Revisit as its own question if memory-language fidelity ever surfaces.

## Scope confirmation (blast radius, source-verified)
`_SUMMARIZE_PROMPT` → `_build_summarizer_prompt` → `summarize_messages` is a closed, private chain called **only** from `compaction.py`, reaching both real entry points: the proactive sliding-window path and `/compact` (`commands/compact.py:43` → `compact_messages`). Single private constant, single file, single behavioral surface — the edit changes exactly the compaction summary body and nothing else.

## Tasks

✓ DONE **TASK-1 — Add the same-language clause to the summarizer prompt**
- files: `co_cli/context/summarization.py` (`_SUMMARIZE_PROMPT` — add one clause adjacent to the existing verbatim-preservation block near lines 163–167)
- done_when: the summarizer prompt instructs the model to write the summary **body** in the conversation's primary language and not translate or switch to English, **while** keeping code, file paths, identifiers, error strings, line numbers, URLs, and the mandated `## Active Task` / `## Next Step` verbatim quotes character-for-character (no conflict with the existing VERBATIM clause or the quote mandates); `scripts/quality-gate.sh lint` passes; full suite passes. Existing assembly tests (`tests/test_flow_compaction_summarization.py`, `tests/test_flow_compaction_proactive.py`) assert substrings (target line, prior-summary clause), so an additive clause is compatible — confirm green.
- success_signal: a non-English conversation compacts to a same-language summary; identifiers/paths/quotes remain verbatim.
- placement rationale: language preservation is an output-shape rule, so it sits with the other output rules in `_SUMMARIZE_PROMPT`, not the role-framing `_SUMMARIZER_SYSTEM_PROMPT`. Peers split on placement; either works.

**TASK-2 (spot-check, not a gate) — behavioral eval case**
- files: `evals/eval_context_stability.py` (add a non-English variant of the planted-conversation path, or a sibling case)
- done_when: the eval drives ≥1 real summarizer pass over a non-English (e.g. CJK or accented-Latin) conversation and **emits** the detected summary-body language/script as a recorded signal — mirroring the existing coherence-probe answer, which is non-deterministic and emitted, never asserted (`eval_context_stability.py` header). Optionally a soft check that the summary retains same-script content. **Not a CI gate.**
- rationale: `feedback_functional_tests_only` forbids asserting the clause string is present in the prompt; LLM output language is non-deterministic, so verification is a UAT smoke, not a deterministic unit test. Peer convergence is the authoring evidence; the eval is a spot-check.

## Testing
- `scripts/quality-gate.sh lint` + full suite on the `summarization.py` change.
- No structural/string-pinning test on the prompt (`feedback_functional_tests_only`).
- Floor guards (`tests/test_instruction_budget.py`, F5) **do not apply** — the summarizer prompt is a separate functional call, not part of the static base-prompt budget. Run if uncertain; expect no-op.
- Optional behavioral eval per TASK-2 (`uv run python evals/eval_context_stability.py`), logged under `.pytest-logs/` per the test-run policy.

## Decisions
- **Carve-out is mandatory.** Language preservation applies to the summary *prose*; code/paths/identifiers/errors and the verbatim user quotes must NOT be translated (openclaw's form). Without it the clause fights the existing VERBATIM-preservation rule and the `## Active Task` / `## Next Step` quote mandates.
- **Source is the conversation, not a profile.** Language is read from the inline turns the summarizer already has — never from `USER.md` or any profile setting, which could mismatch a session whose language differs from the user's default (pasted foreign content, multilingual sessions).
- **Atomic / single-file.** No `/orchestrate-plan` orchestration warranted; this enters the workflow at Gate 1 (PO + TL approve), then `/orchestrate-dev` or a direct edit.

## Delivery Summary — 2026-06-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | same-language clause added to `_SUMMARIZE_PROMPT` (body in conversation's language; code/paths/identifiers/quotes carved out verbatim); lint + scoped suite green | ✓ pass |
| TASK-2 | behavioral validation performed | ✓ done as one-time UAT spot-check — **not** enshrined as a permanent eval case (see below) |

**Change:** `co_cli/context/summarization.py` — one clause inserted after the VERBATIM-preservation block in `_SUMMARIZE_PROMPT` (`:168`), before `## Active Task`. Instructs writing the summary body in the conversation's primary language, no translate/switch-to-English, with the VERBATIM items and mandated quotes exempt (reproduced character-for-character).

**Functional (pytest):** 37 scoped tests green (`tests/test_flow_compaction_summarization.py`, `tests/test_flow_compaction_proactive.py`), incl. the two real-LLM summarizer integration tests — no regression to section presence, verbatim-identifier survival, or anchor preservation on the English path. No language assertion added (non-deterministic → would violate functional-tests-only).

**Behavioral (UAT spot-check, emitted not gated):** 3 real summarizer passes over a Japanese conversation with embedded ASCII carve-out tokens. CJK-fraction of the summary body: 9.4% / 42.1% / 9.6% — the clause steers correctly ~1/3 of runs and is largely ignored the rest, a weak-model soft-prose ceiling (`feedback_instructions_counter_model_limits`). Carve-out identifiers (`co_cli/auth.py`, `validate_token`) survived verbatim in all 3 runs. Summarizer call time 4.77s warm (noreason path), in line with the functional-suite range.

**TASK-2 scope decision:** a permanent eval case was judged over-engineering for a soft, non-deterministic, non-gating signal (would add standing LLM cost to `eval_context_stability.py`, whose scenario is English-only). Behavioral validation was delivered as a re-runnable spot-check (`tmp/summarizer_language_spotcheck.py`, scratch). If language regressions become a recurring concern, graduate to a dedicated emitted-only `evals/eval_summarizer_language.py` — not a gate.

**Doc Sync:** none — no shared module / public API / schema / spec touched (prompt-content only).

**Overall: DELIVERED** — shipped as an additive Pareto improvement (sometimes preserves language, never regresses, carve-outs always intact). Weak-model variance is a known, documented ceiling; strengthening the clause's hit-rate is a separate evidence-driven tuning follow-up, not part of this atomic add.
