# AUDIT: trace-report-finch-jeff

Date: 2026-02-26
Scope:
- `evals/trace-report-finch-jeff.md`
- `scripts/trace_report_personality.py`
- `evals/eval_personality_behavior.py`
- `evals/_common.py`
- `evals/personality_behavior.jsonl`
- personality prompt wiring in `co_cli/agent.py` and `co_cli/prompts/personalities/*`

## Findings

1. High - Trace report run config was not aligned with canonical personality eval config.
- `trace_report_personality.py` used default `make_eval_settings(model_settings)` and `request_limit=5`, while canonical eval uses `max_tokens=2048` and `request_limit=4`.
- This made PASS/FAIL less comparable to `eval_personality_behavior.py` outcomes.

2. High - `p2-jeff-uncertainty` check is too weak for persona validation.
- Current check (`required_any`) passes if any broad phrase appears (for example, `depends`), allowing mostly non-Jeff behavior to pass.
- This creates false-positive PASS for Jeff adherence.

3. Medium - Tool-use behavior is not scored even when it conflicts with prompt strategy.
- The Jeff trace used `web_search` for a broad architecture question where prompt rules prefer direct answer for stable concepts.
- Report still marks PASS because scoring only checks lexical phrase presence.

4. Medium - Timeline detail had tool-argument extraction gaps.
- Timeline code read `tool_arguments` only, but many spans carry args in `gen_ai.tool.call.arguments`.
- Result: timeline often omitted useful detail despite args existing in tool sections.

5. Medium - Span selection can be cross-run fragile.
- Span query is `start_time >= start_ns` then picks first root trace heuristically.
- In concurrent/background runs, this can attribute wrong spans.

6. Low - Token delta label implied causality.
- The text `from tool result injection` over-claimed cause; delta is only input-token difference vs prior request.

7. Low - Trace report logic is P2-only by construction.
- Uses first-turn prompt/checks (`case.turns[0]`, `checks_per_turn[0]`) and does not support multi-turn reporting semantics.

## Requested Actions

8. Align trace-run config to canonical eval settings. DONE
- Updated `scripts/trace_report_personality.py` to use:
  - `max_tokens=2048` via `make_eval_settings(model_settings, max_tokens=2048)`
  - `UsageLimits(request_limit=4)`

9. Fix timeline/tool detail correctness and markdown safety. DONE
- Updated timeline arg extraction to check `gen_ai.tool.call.arguments`, `tool_arguments`, and `gen_ai.tool.arguments`.
- Added execute-tool name fallback from span name (`execute_tool <name>`).
- Escaped markdown table cells (`|`, newline) in timeline output.
- Updated token-delta wording to neutral text: `vs prior request`.

## Notes
- This audit does not change scoring heuristics in `personality_behavior.jsonl`.
- If desired, next patch can harden Jeff/Finch checks to reduce false positives.
