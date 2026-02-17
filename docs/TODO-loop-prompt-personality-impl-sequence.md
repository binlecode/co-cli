# TODO: Loop, Prompt & Personality — Implementation Sequence

**Sources**: `TODO-co-agentic-loop-and-prompting.md` (Phases 1-5), `TODO-co-personality-enhancements.md` (H1-H5, M1-M4)
**Status**: Phases 1-4 + H1-H5 + Tier 1 (5a, 5b, M4) done. Test gate PASS (85.2%). Phase 3 deferred. Tier 2 next.

---

## Completed Work

```
Phase 1  [DONE]  Prompt foundation, processors, memory linking, abort marker
Phase 2  [DONE]  Safety (doom loop, grace turn), typed returns, auto-compaction
Phase 4  [DONE]  Shell reflection, instruction file, LLM retry, finish reason
H1-H5   [DONE]  Personality calibration, override mandate, memory-informed,
                 compaction addendum, load_personality registration
Gate fix [DONE]  Docstring/rule fixes per FIX-p1-test-gate-failures.md
Phase 3  [DEFERRED]  Test gate passed — sub-agents not needed
5a      [DONE]  Expand quirk DB: +3 Gemini models (6 total), default → gemini-2.5-flash
5b      [DONE]  Background compaction: CompactionResult, precompute_compaction(), bg task in chat_loop
M4      [DONE]  Personality debugger: `co debug-personality` CLI command (7 diagnostic layers)
```

### Test Gate History

| Run | Date | Result | Notes |
|-----|------|--------|-------|
| 1st | 2026-02-15 | 73.7% FAIL (14/19) | 5 failures: docstring/rule issues |
| Fix | 2026-02-16 | — | save_memory, web_fetch, recall_memory docstrings + Rule 04 |
| 2nd | 2026-02-16 | 85.2% PASS (23/27) | All original failures fixed; 4 intent-dim remain |

Gate verdict: **PASS (85.2% >= 80%)** — Phase 3 deferred. Proceed to Phase 5.

---

## Remaining Work — Execution Order

### Tier 1: Complete

All Tier 1 items (5a, 5b, M4) are done. See Completed Work above.

### Tier 2: Sequential Chain (must go in order, next up)

```
5d  Personality axes refactor
 → M1  Conditional personality injection
   → M2  Fragment composition for preset scaling
     → M3  Persona drift detection
```

| ID | Item | Source | Effort | Files |
|----|------|--------|--------|-------|
| 5d | Personality axes refactor | Agentic §11.2-11.4 | Medium | `co_cli/prompts/`, `_registry.py`, `_composer.py` |
| M1 | Conditional personality injection | Personality M1 | Small | `co_cli/agent.py` |
| M2 | Fragment composition | Personality M2 | Medium | `co_cli/prompts/personalities/`, `_composer.py` |
| M3 | Persona drift detection | Personality M3 | Large | New module |

**5d** — Replace character/style markdown essays with axis labels (communication, relationship, curiosity, emotional tone). Soul seed (<100 tokens) + axis summary (<100 tokens) in system prompt instead of full essays (500-1000+ tokens). All H-items survive:
- H5 registration is permanent (only content changes)
- H1 calibration moves to role reference docs
- H3 adoption mandate stays with the soul seed
- H2 memory scan is orthogonal to the axis model
- H4 addendum mechanism is independent of prompt content

**M1** — Only inject personality-heavy content when the conversation warrants it. Uses the three-way intent classification (Rule 05). Shallow inquiries get soul seed only; creative/emotional exchanges get full personality. Depends on 5d's axis model.

**M2** — Composable personality fragments instead of monolithic character files. A "sarcastic mentor" composes `fragment/sarcastic.md` + `fragment/mentor.md`. Needed when presets grow beyond 5.

**M3** — Monitor agent outputs for personality consistency. Compare against calibration examples (H1) as baseline. Needs 5d's measurable axis dimensions.

### Tier 3: Deferred

| ID | Item | Source | Condition |
|----|------|--------|-----------|
| 5c | Confidence-scored outputs | Agentic §15 | Only if search quality degrades |
| P3 | Sub-agents (research + analysis) | Agentic §7 | Only if tool-chain completion regresses below 80% |

---

## Dependency Graph

```
Tier 1 (independent):
  5a ─────────────────── standalone
  5b ─────────────────── standalone
  M4 ─────────────────── standalone

Tier 2 (sequential):
  5d ──→ M1 ──→ M2 ──→ M3

Tier 3 (conditional):
  5c ─────────────────── only if needed
  P3 ─────────────────── only if gate regresses
```

No Tier 1 item blocks any Tier 2 item. 5d can start before or after Tier 1 work. The only hard ordering constraint is within the Tier 2 chain.
