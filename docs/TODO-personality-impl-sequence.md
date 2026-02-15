# TODO: Personality Enhancements — Implementation Sequence

**Source**: `TODO-co-personality-enhancements.md` (H1-H5)
**Relationship**: Pre-Phase-5d work; informs agentic loop Phase 5d axis refactor
**Status**: All H-items implemented. Next: Phase 1 test gate, then Phase 3/5.

---

## Decided Order

```
1. [DONE] H5  — register load_personality + precedence note    (bug fix, unblocks H2)
2. [DONE] H1  — calibration examples in finch.md, jeff.md      (pure content)
3. [DONE] H3  — persona override mandate in assemble_prompt()  (one sentence)
   ── run Phase 1 test gate here ──
4. [DONE] H2  — memory-informed personality                     (small code, depends H5)
5. [DONE] H4  — personality-aware compaction addendum            (small code)
   ── then if Phase 1 test gate < 80%: Phase 3 ──
   ── then: Phase 5 (5d axes refactor informed by H1-H5) ──
```

---

## Rationale

**H5 first** — This is a bug fix. `load_personality` is defined in `context.py` but never registered on the agent in `agent.py`. The tool is dead code. Everything else in the personality system assumes this tool works, so it must be fixed first. H2 explicitly depends on H5 (memory-informed personality needs a functioning `load_personality`).

**H1 second** — Pure content change (append `## Calibration` sections to `finch.md` and `jeff.md`). Zero code changes — the composer already reads full file content. Low risk, fast to land, and the calibration examples immediately improve personality quality for the test gate.

**H3 third** — One sentence added to `assemble_prompt()`. Strengthens persona adoption before the test gate measures tool-chain completion rates. If personality drift contributes to incomplete tool chains, this fix catches it before the gate runs.

**Test gate after H3** — At this point, H5 (tool registration), H1 (calibration), and H3 (adoption mandate) are all landed. These are the three items that improve personality quality without adding new code paths. Running the Phase 1 test gate here (5 research prompts, 2 models, 80% pass criterion per `TODO-co-agentic-loop-and-prompting.md` §20) validates the prompt-only foundation before introducing memory integration.

**H2 fourth** — Memory-informed personality (~20 lines in `context.py`). Depends on H5's registration. Scans memories tagged `personality-context` and appends as `## Learned Context`. Small code change, but introduces a cross-module import (`_load_all_memories` from `memory.py`). Lands after the test gate because it's a code change, not a prompt fix — the gate should measure prompt-only improvements first.

**H4 fifth** — Personality-aware compaction addendum (~10 lines in `_history.py`). Adds `_PERSONALITY_COMPACTION_ADDENDUM` constant and `personality_active` parameter to `summarize_messages()`. Independent of H1-H3 but sequenced last because compaction is the least frequently exercised code path. Aligns with Phase 1c's planned compaction prompt rewrite — the addendum mechanism survives any base prompt change.

---

## Dependency Notes

| Item | Hard dependencies | Soft dependencies |
|------|-------------------|-------------------|
| H5 | None | — |
| H1 | None | Benefits from H5 (calibration only visible if tool is registered) |
| H3 | None | — |
| H2 | H5 (load_personality must be registered) | H1 (calibration examples enrich personality context) |
| H4 | None (structural) | H2 (personality memories are preserved by compaction addendum) |

---

## Phase 1 Test Gate Integration

The test gate from `TODO-co-agentic-loop-and-prompting.md` §20:
- **What**: 5 research prompts across 2 models
- **Pass criterion**: 80%+ complete full tool chains
- **Script**: `scripts/eval_tool_calling.py`

Insert the gate run after H3 lands (items 1-3 complete). Results determine the path forward:
- **Pass (>=80%)**: Phase 3 (sub-agents) deferred. Continue with H2 and H4, then Phase 5.
- **Fail (<80%)**: Continue with H2 and H4, then Phase 3, then Phase 5.

The gate measures the combined effect of Phases 1-2-4 (already implemented) plus H5+H1+H3 (personality fixes). Personality quality directly affects tool-chain completion — a drifting persona may abandon multi-step plans.

---

## Sequencing Against Agentic Loop Phases

```
Already done:  Phase 1 (prompt rewrite, processors, memory linking)
               Phase 2 (safety, loop returns)
               Phase 4 (resilience, retry, streaming)

This sequence: H5 → H1 → H3 → [test gate] → H2 → H4

Next:          Phase 3  (sub-agents — only if test gate < 80%)
               Phase 5  (polish — 5d axis refactor informed by H1-H5)
```

Phase 5d (axis refactor, §11.2-11.4) replaces character/style markdown with axis labels. All H-items survive:
- H5 registration is permanent (only content changes)
- H1 calibration moves to role reference docs (§11.1)
- H3 adoption mandate stays with the soul seed (axis 1)
- H2 memory scan is orthogonal to the axis model
- H4 addendum mechanism is independent of prompt content
