# GUIDE: Eval Agentic Flows

This guide summarizes each active eval runner in `evals/` and the core behavior it evaluates.

## Eval Runners

| Eval | Core target |
|---|---|
| `eval_tool_chains.py` | Multi-step tool orchestration: whether the agent executes the expected tool sequence in order and still produces a final answer. |
| `eval_conversation_history.py` | Context retention across turns: remembers prior user details, corrections, and prior tool outputs in follow-up turns. |
| `eval_safety_abort_marker.py` | Interrupt safety: Ctrl-C/cancel should mark history so the next turn knows prior work was interrupted. |
| `eval_safety_grace_turn.py` | Budget exhaustion safety: when turn/request limit is hit, the agent should fail gracefully with progress/resume guidance (`/continue`) instead of hard crashing. |
| `eval_memory_proactive_recall.py` | Proactive memory recall injection: opening-context memory retrieval should auto-inject relevant memories (without explicit tool calls). |
| `eval_memory_signal_detection.py` | Proactive memory save triggering: agent calls `save_memory` tool during `run_turn()` on preference/correction/decision signals (W2/W6). |
| `eval_signal_analyzer.py` | Signal classifier accuracy: classify user messages into `high`/`low`/`none` confidence buckets with guardrails against false positives. Calls `analyze_for_signals()` directly. |
| `eval_signal_detector_approval.py` | Approval policy correctness: high-confidence auto-save, low-confidence approval/deny flow, and no-action on no-signal. Tests post-turn dispatch hook side effects (file write, approval call, status message). |
| `eval_personality_behavior.py` | Personality consistency: single-turn and multi-turn cases run through the real agent. Scored by heuristic checks per turn; reports drift (turn-0 pass → later-turn fail) and tool leakage. |

## Signal Eval Layers

The three signal evals test distinct code paths — they are not redundant:

| Eval | Layer | What it covers |
|---|---|---|
| `eval_signal_analyzer.py` | Classifier | `analyze_for_signals()` mini-agent accuracy |
| `eval_signal_detector_approval.py` | Dispatch hook | Post-turn `main.py` hook: approval UX, `on_status`, file written |
| `eval_memory_signal_detection.py` | Agent loop | `run_turn()` — agent proactively calls `save_memory` tool |

## Golden Datasets

- `personality_behavior.jsonl`: Single-turn and multi-turn personality checks (finch, jeff).

## Pytest Coverage (non-eval)

- `tests/test_tool_calling_functional.py`: Functional tool-calling quality gate (`tool_selection`, `arg_extraction`, `refusal`, `intent`, `error_recovery`) using the agentic Ollama model.
