# RESEARCH: Input Queue — Peer System Comparison

Status: forward-looking
Aspect: interaction loop and queued user turns
Pydantic-AI patterns: turn ownership around `run_turn()` and `run_stream_events`

## Executive Summary

This review compares user-input queue behavior in `codex`, `gemini-cli`, and `openclaw` and proposes an adoption plan for `co-cli`.

Headline:
- `codex`: explicit TUI-owned FIFO queue for user turns, with one-item drain when idle.
- `gemini-cli`: explicit UI queue; queued messages are combined and submitted when stream is idle and MCP is ready.
- `openclaw`: no TUI turn queue; `chat.send` is immediate, while backend session logic can enqueue follow-up runs when a run is active.
- `co-cli` today: no input queue in chat loop; one prompt -> one `run_turn()` call.

Recommended co adoption:
- Add a CLI/TUI-owned input queue (not inside `run_turn()`), FIFO by default.
- Keep `run_turn()` single-turn and event-emitting.
- Add optional "collect" mode (Gemini-style merge) after FIFO baseline is stable.

---

## Scope and Method

Date of research: 2026-03-04 (local)

Reference repos were pulled before review and inspected at:
- `gemini-cli`: `e63d273e4e24`
- `codex`: `ce139bb1afad`
- `openclaw`: `9c6847074d77`

Method:
- Read concrete submit/dispatch/queue/drain code paths.
- Cross-check with tests where queue semantics are asserted.
- Compare against current `co-cli` turn loop and `run_turn()` contract.

---

## System Findings

## 1) Codex

### Discovery

Input queue is explicit in TUI state:
- `queued_user_messages: VecDeque<UserMessage>` in chat widget.
- Queueing conditions include task-running/review/session-not-configured.

Evidence:
- [chatwidget.rs:4273](/Users/binle/workspace_genai/codex/codex-rs/tui/src/chatwidget.rs:4273)
- [chatwidget.rs:4285](/Users/binle/workspace_genai/codex/codex-rs/tui/src/chatwidget.rs:4285)

Submission and execution:
- `submit_user_message(...)` constructs `Op::UserTurn { ... }` and submits op.
- This keeps queue ownership in UI, execution ownership in core op pipeline.

Evidence:
- [chatwidget.rs:4466](/Users/binle/workspace_genai/codex/codex-rs/tui/src/chatwidget.rs:4466)

Drain behavior:
- `maybe_send_next_queued_input()` pops exactly one queued message when idle.
- Called on turn completion and other turn-ending paths.

Evidence:
- [chatwidget.rs:5002](/Users/binle/workspace_genai/codex/codex-rs/tui/src/chatwidget.rs:5002)
- [chatwidget.rs:1578](/Users/binle/workspace_genai/codex/codex-rs/tui/src/chatwidget.rs:1578)

Replay/resume behavior:
- Queue autosend can be suppressed during replay restore, then resumed.

Evidence:
- [app.rs:1483](/Users/binle/workspace_genai/codex/codex-rs/tui/src/app.rs:1483)

### Analysis

Strengths:
- Clear separation: UI queue vs core execution loop.
- Deterministic FIFO and one-at-a-time drain semantics.
- Good replay/resume safety.

Tradeoffs:
- More UI state complexity (pending previews, suppression flags, replay interactions).

---

## 2) Gemini CLI

### Discovery

Queue is explicit in UI hook:
- `useMessageQueue` stores `messageQueue: string[]`.
- `addMessage()` appends trimmed submissions.

Evidence:
- [useMessageQueue.ts:30](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/hooks/useMessageQueue.ts:30)
- [useMessageQueue.ts:36](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/hooks/useMessageQueue.ts:36)

Submission routing in app container:
- If slash command or `(Idle && MCP ready)`: submit immediately.
- Else: queue via `addMessage(submittedValue)`.

Evidence:
- [AppContainer.tsx:1260](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/AppContainer.tsx:1260)
- [AppContainer.tsx:1307](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/AppContainer.tsx:1307)
- [AppContainer.tsx:1316](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/AppContainer.tsx:1316)

Drain behavior:
- Effect auto-submits when `streamingState === Idle`, MCP ready, queue non-empty.
- Multiple queued messages are merged (`join("\n\n")`) into one submission.

Evidence:
- [useMessageQueue.ts:67](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/hooks/useMessageQueue.ts:67)
- [useMessageQueue.ts:76](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/hooks/useMessageQueue.ts:76)

Test confirmation:
- Auto-submit on transition to idle is covered.

Evidence:
- [useMessageQueue.test.tsx:142](/Users/binle/workspace_genai/gemini-cli/packages/cli/src/ui/hooks/useMessageQueue.test.tsx:142)

### Analysis

Strengths:
- Very simple queue model.
- Practical UX for "type while busy".
- Coupled with readiness gating (MCP) to avoid premature sends.

Tradeoffs:
- Merging multiple queued prompts can blur strict turn boundaries.

---

## 3) OpenClaw

### Discovery

TUI side:
- Submit handler routes directly to `sendMessage(...)` (no explicit turn queue).
- `sendMessage` immediately issues `client.sendChat(...)` with a new `runId`.

Evidence:
- [tui.ts:42](/Users/binle/workspace_genai/openclaw/src/tui/tui.ts:42)
- [tui-command-handlers.ts:458](/Users/binle/workspace_genai/openclaw/src/tui/tui-command-handlers.ts:458)

Gateway side:
- `chat.send` acks quickly and dispatches asynchronously.

Evidence:
- [chat.ts:722](/Users/binle/workspace_genai/openclaw/src/gateway/server-methods/chat.ts:722)
- [chat.ts:847](/Users/binle/workspace_genai/openclaw/src/gateway/server-methods/chat.ts:847)

Backend follow-up queue (session-level):
- During reply generation, if session run is active, policy can enqueue follow-up instead of starting now.
- Enqueue call: `enqueueFollowupRun(queueKey, followupRun, resolvedQueue)`.
- Drainer runs queued follow-ups sequentially.

Evidence:
- [get-reply-run.ts:444](/Users/binle/workspace_genai/openclaw/src/auto-reply/reply/get-reply-run.ts:444)
- [agent-runner.ts:200](/Users/binle/workspace_genai/openclaw/src/auto-reply/reply/agent-runner.ts:200)
- [agent-runner.ts:212](/Users/binle/workspace_genai/openclaw/src/auto-reply/reply/agent-runner.ts:212)
- [drain.ts:66](/Users/binle/workspace_genai/openclaw/src/auto-reply/reply/queue/drain.ts:66)
- [drain.ts:157](/Users/binle/workspace_genai/openclaw/src/auto-reply/reply/queue/drain.ts:157)

Execution serialization:
- Agent run entry enqueues through session lane + global lane.
- Default lane concurrency is 1.

Evidence:
- [run.ts:243](/Users/binle/workspace_genai/openclaw/src/agents/pi-embedded-runner/run.ts:243)
- [run.ts:259](/Users/binle/workspace_genai/openclaw/src/agents/pi-embedded-runner/run.ts:259)
- [command-queue.ts:60](/Users/binle/workspace_genai/openclaw/src/process/command-queue.ts:60)

### Analysis

Strengths:
- Strong backend control for session-level serialization and follow-up policy.
- Queue supports richer modes (`collect`, `followup`, `steer-backlog`, etc.).

Tradeoffs:
- Queue semantics are less visible at TUI level.
- Behavior depends on backend queue mode and active-run state, which is harder to reason about from frontend alone.

## Cross-System Comparison

| Dimension | Codex | Gemini CLI | OpenClaw |
|---|---|---|---|
| Primary queue owner | TUI/chat widget | UI hook (`useMessageQueue`) | Backend reply pipeline |
| Frontend submit while busy | Queue in UI | Queue in UI | Send immediately |
| Queued unit | `UserMessage` turn | text string | `FollowupRun` session task |
| Drain trigger | turn complete / idle check | stream idle + MCP ready | run finalization + drain scheduler |
| Drain granularity | one queued turn | merged batch | one follow-up run |
| Ordering | FIFO (`VecDeque`) | append order, merged on send | queue order + policy |
| Policy richness | moderate | low | high (mode/drop/debounce/cap) |
| Replay/resume handling | explicit suppression/restore | N/A | backend stateful |

---

## co-cli Baseline (Current)

`co-cli` currently processes one input at a time from prompt loop:
- Prompt reads one `user_input`.
- Chat loop calls `run_turn(agent, user_input=...)`.
- No input queue in loop around `run_turn()`.

Evidence:
- [main.py:273](/Users/binle/workspace_genai/co-cli/co_cli/main.py:273)
- [main.py:327](/Users/binle/workspace_genai/co-cli/co_cli/main.py:327)

`run_turn()` contract is single-turn orchestration:
- Accepts one `user_input: str`.
- Streams events through frontend protocol callbacks.

Evidence:
- [\_orchestrate.py:475](/Users/binle/workspace_genai/co-cli/co_cli/_orchestrate.py:475)
- [\_orchestrate.py:300](/Users/binle/workspace_genai/co-cli/co_cli/_orchestrate.py:300)

Implication:
- Any input queue for co should be owned by chat-loop/TUI layer, not embedded into `run_turn()`.

---

## Adoption Recommendations for co-cli

## Recommended Target

Adopt a **Codex-like explicit UI queue** first, with optional Gemini-style collect mode later.

Why this target:
- Matches co architecture: `main.py` owns prompt and turn loop, `run_turn()` stays single-turn.
- Preserves deterministic turn boundaries and simple debugging.
- Avoids immediate jump into OpenClaw-level queue policy complexity.

## Proposed Co Queue Contract

Queue owner:
- `main.py` chat loop (or dedicated `InputQueueManager` owned by loop).

Queue payload:
- `QueuedInput` with `text`, `created_at`, `source` (`user`/`skill`), optional `mode_overrides`.

Execution rules:
- If no active turn: submit immediately.
- If active turn: enqueue.
- On turn end (`continue`/`error`/`stop`): dequeue exactly one and run next.
- Optional future mode: `collect` merges pending text blocks into one turn.

Display behavior:
- Show pending queue depth and preview in status line.
- Preserve up-arrow history independently from queue.

## Suggested Phase Plan

Phase 1 (MVP, low risk):
- FIFO queue in `main.py` only.
- One-by-one drain after each completed turn.
- No merge, no drop policy, no debounce.

Phase 2 (UX polish):
- Add queue preview + clear/edit commands (`/queue`, `/queue clear`, `/queue pop`).
- Add optional `collect` mode (`join("\n\n")`) gated by config.

Phase 3 (advanced policy, optional):
- Add queue cap and drop policy (`old`, `new`, `summarize`).
- Consider steer/backlog semantics only if co introduces active in-turn steering.

## Test Recommendations for co

Add functional tests for:
- Busy enqueue: second input during active turn is queued, not lost.
- FIFO drain: queued items execute in original order.
- Interrupt/error paths: queue remains consistent and resumes.
- Slash/skill interaction: queued normal inputs unaffected by slash command handling.
- Optional collect mode: merged submission is deterministic and logged clearly.

---

## Risks and Mitigations

Risk: queue hidden from user causes confusion.
- Mitigation: explicit status + `/queue` inspection command.

Risk: merged prompts reduce traceability.
- Mitigation: keep FIFO default; mark merged turns in history metadata.

Risk: approval flow interaction edge cases.
- Mitigation: keep queue logic outside `run_turn()`; drain only after turn settles.

Risk: future TUI evolution diverges from loop queue semantics.
- Mitigation: centralize queue contract in one module and reuse in any future UI.

---

## Conclusion

For `co-cli`, the best adoption path is:
1. **Codex-style explicit FIFO queue at UI/chat-loop layer**.
2. Keep `run_turn()` unchanged as a single-turn executor + event emitter.
3. Add Gemini-style `collect` as opt-in only after baseline queue correctness is proven.

This gives predictable behavior, minimal architecture disruption, and a clean upgrade path toward richer queue policy if needed.
