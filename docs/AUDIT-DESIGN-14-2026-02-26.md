# Audit: DESIGN-14 vs Latest Code

Date: 2026-02-26
Scope: `docs/DESIGN-14-memory-lifecycle-system.md` vs current implementation

## Findings

1. **High**: Auto-save policy in code is broader than the design doc.

Doc says only **high-confidence explicit corrections** should save silently, while preferences should require approval ([docs/DESIGN-14-memory-lifecycle-system.md:18](/Users/binle/workspace_genai/co-cli/docs/DESIGN-14-memory-lifecycle-system.md:18), [docs/DESIGN-14-memory-lifecycle-system.md:76](/Users/binle/workspace_genai/co-cli/docs/DESIGN-14-memory-lifecycle-system.md:76)).

Code auto-saves any `confidence == "high"` regardless of `tag` ([co_cli/main.py:248](/Users/binle/workspace_genai/co-cli/co_cli/main.py:248), [co_cli/main.py:249](/Users/binle/workspace_genai/co-cli/co_cli/main.py:249)).

2. **Medium**: Exception-handling behavior in doc is overstated.

Doc says any exception in `analyze_for_signals` is caught ([docs/DESIGN-14-memory-lifecycle-system.md:63](/Users/binle/workspace_genai/co-cli/docs/DESIGN-14-memory-lifecycle-system.md:63)).

In code, only `signal_agent.run(...)` is inside `try`; prompt file read and agent construction are outside and can still throw ([co_cli/_signal_analyzer.py:159](/Users/binle/workspace_genai/co-cli/co_cli/_signal_analyzer.py:159), [co_cli/_signal_analyzer.py:164](/Users/binle/workspace_genai/co-cli/co_cli/_signal_analyzer.py:164), [co_cli/_signal_analyzer.py:174](/Users/binle/workspace_genai/co-cli/co_cli/_signal_analyzer.py:174)).

3. **Medium**: Sensitive-memory prevention is not enforced deterministically in code.

The “never save sensitive content” rule is prompt-only ([co_cli/prompts/agents/signal_analyzer.md:43](/Users/binle/workspace_genai/co-cli/co_cli/prompts/agents/signal_analyzer.md:43)); saved candidates are written directly ([co_cli/main.py:249](/Users/binle/workspace_genai/co-cli/co_cli/main.py:249), [co_cli/main.py:258](/Users/binle/workspace_genai/co-cli/co_cli/main.py:258), [co_cli/tools/memory.py:523](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py:523)). Model misclassification can persist sensitive text.

4. **Low (doc accuracy)**: Intro says only auto-triggered detection is implemented, but lifecycle mechanics are already live in code.

Doc statement ([docs/DESIGN-14-memory-lifecycle-system.md:8](/Users/binle/workspace_genai/co-cli/docs/DESIGN-14-memory-lifecycle-system.md:8)) conflicts with implemented dedup/consolidation/decay/gravity paths ([co_cli/tools/memory.py:196](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py:196), [co_cli/tools/memory.py:301](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py:301), [co_cli/tools/memory.py:464](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py:464), [co_cli/tools/memory.py:766](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py:766)).

5. **Low (doc accuracy)**: Doc claims decay needs LLM summarization via `RunContext`, but code currently uses deterministic concatenation (no LLM summarizer call).

Mismatch between ([docs/DESIGN-14-memory-lifecycle-system.md:102](/Users/binle/workspace_genai/co-cli/docs/DESIGN-14-memory-lifecycle-system.md:102)) and ([co_cli/tools/memory.py:370](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py:370), [co_cli/tools/memory.py:386](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py:386)).

## Testing Gap

1. Current tests cover deterministic helpers, not the `main.py` post-turn integration branch (`high` auto-save vs `low` approval) ([tests/test_signal_analyzer.py:1](/Users/binle/workspace_genai/co-cli/tests/test_signal_analyzer.py:1), [co_cli/main.py:241](/Users/binle/workspace_genai/co-cli/co_cli/main.py:241)).

## Verification Run

Command:

```bash
.venv/bin/pytest -q tests/test_signal_analyzer.py tests/test_memory.py tests/test_memory_decay.py
```

Result:

- 30 passed
- 1 warning (deprecated `asyncio.get_event_loop()` usage in tests)

## Actions

| Finding | Action taken |
|---------|-------------|
| 1 (High) | Doc fix only. Code auto-save on `confidence=="high"` is the correct policy. DESIGN-14 §18 and §76–80 updated. |
| 2 (Medium) | Code fix. Try block widened in `analyze_for_signals` to cover prompt read and agent construction. |
| 3 (Medium) | Accepted for MVP. Sensitive content prevention is prompt-enforced. Noted in DESIGN-14 §87–89. |
| 4 (Low) | Doc fix. DESIGN-14 intro updated — dedup/consolidation/decay marked as implemented. |
| 5 (Low) | Doc fix. Decay description corrected — deterministic concatenation, no LLM call. |
| Testing gap | E2E tests for `analyze_for_signals` added in `tests/test_signal_analyzer.py`. |
