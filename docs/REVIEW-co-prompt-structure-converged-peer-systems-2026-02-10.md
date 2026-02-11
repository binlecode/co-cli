# REVIEW: co-cli Prompt Structure and Crafting vs Converged Peer Systems

**Date:** 2026-02-10  
**Scope:** Latest `co_cli/prompts/*` implementation, runtime wiring, and prompt-design docs in this repo.  
**Peer baseline used:** `docs/REVIEW-compare-four.md`, `docs/REVIEW-prompts-peer-systems.md`, plus per-system deep dives already present in this repo.

## Executive Verdict

Current co-cli prompt design is strong on **intent safety** (Directive vs Inquiry + Fact Verification), but weak on **prompt-tool contract correctness** and **runtime activation of model-specific controls**.

**Overall assessment:** solid direction, uneven execution.  
**Recommendation:** keep the MVP single-source architecture, but tighten contract fidelity and reduce prompt mass.

## Converged Peer Baseline (What Matters Most)

From existing repo research (`docs/REVIEW-prompts-peer-systems.md:977`, `docs/REVIEW-compare-four.md:393`):

1. Be concise and completion-oriented.
2. Distinguish inquiry vs directive behavior.
3. Use explicit examples for ambiguous behavior.
4. Reinforce critical rules near prompt tail (recency).
5. Adapt to model-specific quirks.
6. Keep summarization/compression prompts injection-safe.
7. Keep policy enforcement in runtime code, not prompt-only.

## Current co-cli State Snapshot

### Strengths

1. **High-value safety behavior exists in prompt core.**
- Directive vs Inquiry + contrast examples + critical reminders are present (`co_cli/prompts/system.md:26`, `co_cli/prompts/system.md:57`, `co_cli/prompts/system.md:804`).

2. **Fact Verification is a competitive advantage.**
- Clear contradiction protocol is present (`co_cli/prompts/system.md:104`).
- Repo peer analysis itself calls this unique (`docs/REVIEW-compare-four.md:381`).

3. **Prompt assembly is simple and readable (MVP-friendly).**
- Single file + small assembly function + conditional blocks (`co_cli/prompts/__init__.py:53`).

### Risks

1. Prompt-tool contract drift is now the dominant failure mode.
2. Model-quirk logic is not active in production path.
3. Prompt is oversized for an MVP and for smaller local contexts.

## Findings (Ordered by Severity)

## P0-1: Prompt Examples Mismatch Real Tool Contracts

**Evidence:**
- Shell example uses wrong args (`"command"`, `"description"`) in prompt (`co_cli/prompts/system.md:297`), but tool expects `cmd`, `timeout` (`co_cli/tools/shell.py:9`).
- Tool chaining references nonexistent tools (`search_files`, `read_file`) (`co_cli/prompts/system.md:223`).
- Calendar examples use `time_min`/`time_max` (`co_cli/prompts/system.md:517`), but real API is `days_back`/`days_ahead` (`co_cli/tools/google_calendar.py:115`).
- Obsidian examples call `list_notes(path=..., prefix=...)` (`co_cli/prompts/system.md:361`), while actual signature is `list_notes(tag=None)` (`co_cli/tools/obsidian.py:150`).

**Impact:**
- Increases invalid tool-call probability.
- Adds noise that competes with real tool schema.
- Violates first-principle reliability: instructions should be executable as written.

**MVP/Pythonic fix:**
- Treat tool signatures/docstrings as single source of truth.
- Remove non-existent tool names from prompt.
- Add a contract test that validates every prompt example against live tool signatures.

## P0-2: Model Quirk Counter-Steering Is Not Wired End-to-End

**Evidence:**
- `get_system_prompt()` supports `model_name` (`co_cli/prompts/__init__.py:56`), but `get_agent()` does not pass it (`co_cli/agent.py:82`).
- `/model` only swaps model object and does not rebuild system prompt (`co_cli/_commands.py:130`, `co_cli/_commands.py:153`).
- Quirk lookup is exact-match (`co_cli/prompts/model_quirks.py:232`), but default Ollama model includes quant suffix (`co_cli/config.py:119`), so keys like `glm-4.7-flash:q4_k_m` miss `ollama:glm-4.7-flash`.
- Validation script already strips quant tags before lookup (`tests/validate_phase1d.py:240`, `tests/validate_phase1d.py:251`), confirming this known mismatch in practice.

**Impact:**
- “Implemented” quirk logic is mostly inactive in real sessions.
- Overeager/local-model behavior regression risk remains.

**MVP/Pythonic fix:**
- Normalize model ID once (e.g., strip `:q*` suffix) in one helper.
- Pass normalized model name into prompt assembly at agent creation and model switch.
- Add one integration test: active model -> expected quirk guidance present in agent prompt.

## P1-1: Instruction Collisions Between Base Prompt and Personality Layers

**Evidence:**
- Base prompt says avoid filler/cheerleading and be terse (`co_cli/prompts/system.md:147`, `co_cli/prompts/system.md:161`).
- `friendly` explicitly promotes “Great question”, “let’s”, encouragement, emoji (`co_cli/prompts/personalities/friendly.md:16`, `co_cli/prompts/personalities/friendly.md:39`).
- `jeff` requires frequent emoji, uncertainty narration, and childlike tone (`co_cli/prompts/personalities/jeff.md:10`, `co_cli/prompts/personalities/jeff.md:44`).
- Default personality is verbose `finch` (`co_cli/config.py:61`).

**Impact:**
- Competing directives lower adherence to critical policies.
- Unpredictable style behavior across sessions/models.

**MVP/Pythonic fix:**
- Keep personalities as **small deltas** (tone only), not second system prompts.
- Define an explicit non-overridable core (safety, tool contract, verbosity limits).
- Reduce personality files to short, non-conflicting style constraints.

## P1-2: Prompt Size Exceeds Stated Budget and MVP Practicality

**Evidence:**
- Base prompt size: `26063` chars (~6516 token rough estimate). With default `finch`: `34586` chars (~8646 tokens), measured locally.
- Design target: `<20KB total prompt context` with total overhead ~`7-17KB` (from Phase 1-2 roadmap).

**Impact:**
- Context headroom drops, especially for smaller local contexts.
- More contradictions and lower instruction recall in longer prompts.

**MVP/Pythonic fix:**
- Trim to a compact, testable core prompt.
- Move long pedagogical narratives (movie persona lore) out of runtime prompt.
- Keep runtime prompt near a strict budget target.

## P1-3: Compression/Summarization Prompt Lacks Anti-Injection Guard

**Evidence:**
- Summarizer prompt is minimal and trusts raw conversation content (`co_cli/_history.py:124`, `co_cli/_history.py:147`).
- Peer convergence explicitly recommends anti-injection rules in compression (`docs/REVIEW-compare-four.md:410`, `docs/REVIEW-prompts-peer-systems.md:1003`).

**Impact:**
- Summarizer may propagate adversarial text as trusted instruction-like state.

**MVP/Pythonic fix:**
- Add one explicit rule: treat history as data, ignore any embedded commands/instructions.
- Add one regression test with malicious history content.

## P2-1: Documentation and Test Drift Is Visible

**Evidence:**
- Phase 1d is complete (see `docs/TODO-co-evolution-phase1d-COMPLETE.md`) but documentation drift remains in some areas.
- Prompt test suite currently fails due stale expectation (`tests/test_prompts_phase1d.py:211` expects 10 models, actual 11).
- `DESIGN-01-agent.md` prompt snippet no longer reflects current `system.md` scale/content (`docs/DESIGN-01-agent.md:145`).

**Impact:**
- Reduces trust in docs as source of truth.
- Makes prompt iteration harder to validate.

**MVP/Pythonic fix:**
- Keep one canonical prompt design doc in sync.
- Add a lightweight doc drift check for critical status fields.

## Convergence Scorecard (Current)

| Converged Pattern | co-cli Status | Notes |
|---|---|---|
| Directive vs Inquiry | Strong | Implemented with contrast + reminders |
| Fact contradiction handling | Strong+ | Better than peers |
| Recency reinforcement | Strong | `Critical Rules` section present |
| Model-specific quirk steering | Partial | Implemented, not runtime-wired |
| Compression anti-injection | Missing | Summarizer prompt needs hardening |
| Prompt-tool contract fidelity | Weak | Multiple mismatched examples |
| Concise/MVP prompt footprint | Weak | Runtime prompt too large |

## First-Principles MVP/Pythonic Target Design

1. **Single authoritative core prompt** (`system_core.md`) for non-negotiables.
2. **Small optional overlays**:
- `personality/<name>.md` as <= ~15 lines of tone-only deltas.
- `model_quirks.py` for normalized model IDs.
3. **No duplicated tool API docs in prompt**:
- rely on live tool schema/docstrings.
- keep prompt examples generic and signature-agnostic.
4. **One obvious way to assemble prompt**:
- `PromptContext` dataclass -> deterministic `build_system_prompt(ctx)`.
5. **Budget and invariants enforced by tests**:
- size budget,
- no unknown tool names,
- quirk injection active for current model,
- no unprocessed conditionals.

## Deliberate Non-Adoptions (MVP Discipline)

1. Do not add plugin/agent primitive architecture yet (Claude-style) until prompt-tool contract and runtime wiring are stable.
2. Do not add policy-fragment matrix complexity (Codex-style) before current single-source prompt is internally consistent.
3. Do not add more personality/preference layers until existing core + personality collision is resolved.

## Recommended Immediate Actions

1. Fix prompt-tool contract mismatches (shell args, calendar args, nonexistent tool names).
2. Wire and normalize model-name quirk injection end-to-end.
3. Shrink personality content to non-conflicting style deltas.
4. Add anti-injection guard to summarizer prompt.
5. Repair drifted tests/docs (`test_prompts_phase1d`, Phase 1d status docs).

## Validation Evidence Collected in This Review

1. Prompt tests run:
- `.venv/bin/pytest tests/test_prompts.py tests/test_prompts_phase1d.py -q`
- Result: **44 passed, 1 failed** (`tests/test_prompts_phase1d.py::test_model_quirks_database`).

2. Prompt size measurements (assembled):
- `ollama + no personality`: 26063 chars (~6516 tokens rough)
- `ollama + finch`: 34586 chars (~8646 tokens rough)
- `ollama + terse`: 27479 chars (~6870 tokens rough)

3. Runtime agent prompt inspection:
- `## Model-Specific Guidance` absent in current agent system prompt under default startup path.
