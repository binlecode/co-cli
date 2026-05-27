# RESEARCH: Input Queue — Peer System Comparison

Status: forward-looking
Aspect: interaction loop and queued user turns
Pydantic-AI patterns: turn ownership around `run_turn()` and `run_stream_events`

## Executive Summary

This review compares user-input queue behavior in `codex`, `gemini-cli`, `openclaw`, and `hermes-agent` and proposes an adoption plan for `co-cli`.

Headline:
- `codex`: explicit TUI-owned FIFO queue for user turns, with one-item drain when idle.
- `gemini-cli`: explicit UI queue; queued messages are combined and submitted when stream is idle and MCP is ready.
- `openclaw`: no TUI turn queue; `chat.send` is immediate, while backend session logic can enqueue follow-up runs when a run is active.
- `hermes-agent`: dual TUI-owned queues (`_pending_input` + `_interrupt_queue`) with a config-selected `busy_input_mode` ∈ {interrupt, queue, steer}; FIFO one-at-a-time drain per turn, mid-run interrupts processed inline.
- `co-cli` today: no input queue in chat loop; one prompt -> one `run_turn()` call.

Recommended co adoption:
- Add a CLI/TUI-owned input queue (not inside `run_turn()`), FIFO by default.
- Keep `run_turn()` single-turn and event-emitting.
- Add optional "collect" mode (Gemini-style merge) after FIFO baseline is stable.
- Hermes-style mode selection (`interrupt`/`queue`/`steer`) is a viable Phase 3 superset of the codex baseline if mid-turn steering ever lands in co.

---

## Scope and Method

Date of research: 2026-03-04 (local)

Reference repos were pulled before review and inspected at:
- `gemini-cli`: `e63d273e4e24`
- `codex`: `ce139bb1afad`
- `openclaw`: `9c6847074d77`
- `hermes-agent`: `fe6c86623fab`

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

---

## 4) Hermes

### Discovery

Hermes-agent uses an explicit **dual-queue** design in the CLI loop, with mode-based routing controlled by config.

Queue infrastructure:
- `_pending_input` (`queue.Queue`) — holds messages submitted while idle, drained one-per-turn.
- `_interrupt_queue` (`queue.Queue`) — holds messages submitted while the agent is mid-run; consumed inline as interrupts.

Evidence:
- [cli.py:2118](/Users/binle/workspace_genai/hermes-agent/cli.py:2118)
- [cli.py:2119](/Users/binle/workspace_genai/hermes-agent/cli.py:2119)

Mode-based submission routing (`busy_input_mode` from config):
- `interrupt` (default) — enqueue to `_interrupt_queue`, triggering mid-run `agent.interrupt()`.
- `queue` — enqueue to `_pending_input` for next-turn execution.
- `steer` — attempt `agent.steer()` injection; fall back to `_pending_input` if rejected or unsupported.

Evidence:
- [cli.py:1891](/Users/binle/workspace_genai/hermes-agent/cli.py:1891)
- [cli.py:9580](/Users/binle/workspace_genai/hermes-agent/cli.py:9580)

Drain behavior:
- While agent runs: `chat()` polls `_interrupt_queue` with 0.1s timeout, consumes one item, calls `agent.interrupt(msg)`.
- After agent completes: `process_loop()` calls `_pending_input.get(timeout=0.1)`, consuming one queued message per turn (FIFO, no merging).
- Background process completions are synthesized into messages and pushed to `_pending_input` automatically.

Evidence:
- [cli.py:8754](/Users/binle/workspace_genai/hermes-agent/cli.py:8754)
- [cli.py:10906](/Users/binle/workspace_genai/hermes-agent/cli.py:10906)
- [cli.py:10920](/Users/binle/workspace_genai/hermes-agent/cli.py:10920)

Interrupt cleanup & double-interrupt:
- On interrupt, agent thread is interrupted and the loop polls up to 50× (10s max) for clean shutdown.
- A second Ctrl+C during cleanup sets `_should_exit` and breaks the loop.

Evidence:
- [cli.py:8767](/Users/binle/workspace_genai/hermes-agent/cli.py:8767)
- [cli.py:8799](/Users/binle/workspace_genai/hermes-agent/cli.py:8799)

### Analysis

Strengths:
- Explicit TUI-owned queues — same architectural clarity as codex.
- Mode selection separates intent (interrupt vs. defer vs. steer) instead of forcing one fixed semantic.
- FIFO one-at-a-time drain preserves deterministic turn boundaries.
- Background process notifications hook into the same queue infrastructure — uniform delivery surface.

Tradeoffs:
- Two queues + three modes is more surface area than codex's single FIFO; requires deciding mode at submit time.
- `steer` mode depends on agent supporting mid-run steering; co does not have this primitive today.
- Interrupt cleanup polling (10s) is a coarse safety net rather than a deterministic handshake.

---

## Cross-System Comparison

| Dimension | Codex | Gemini CLI | OpenClaw | Hermes |
|---|---|---|---|---|
| Primary queue owner | TUI/chat widget | UI hook (`useMessageQueue`) | Backend reply pipeline | TUI/CLI loop |
| Frontend submit while busy | Queue in UI | Queue in UI | Send immediately | Mode-dependent (interrupt / queue / steer) |
| Queued unit | `UserMessage` turn | text string | `FollowupRun` session task | text string (per-queue) |
| Drain trigger | turn complete / idle check | stream idle + MCP ready | run finalization + drain scheduler | turn complete (pending) / inline poll (interrupt) |
| Drain granularity | one queued turn | merged batch | one follow-up run | one item per drain (no merging) |
| Ordering | FIFO (`VecDeque`) | append order, merged on send | queue order + policy | FIFO (`queue.Queue` × 2) |
| Policy richness | moderate | low | high (mode/drop/debounce/cap) | moderate-high (3 modes + interrupt path) |
| Replay/resume handling | explicit suppression/restore | N/A | backend stateful | interrupt cleanup polling + double-interrupt exit |

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
- Consider hermes-style `busy_input_mode` (`interrupt` / `queue` / `steer`) with a dedicated `_interrupt_queue` — only viable once co adds mid-turn interrupt/steer primitives. Hermes-agent is the closest reference here.
- Background-event auto-enqueue (hermes pushes process notifications into `_pending_input` — same surface as user input) is a clean extension if co daemons need to inject prompts.

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
4. Reserve hermes-style dual-queue + mode selection (`interrupt`/`queue`/`steer`) for Phase 3 — only after mid-turn steering/interrupt primitives exist in co.

This gives predictable behavior, minimal architecture disruption, and a clean upgrade path toward richer queue policy if needed. Hermes-agent is the most directly comparable reference (Python, single-process CLI loop, explicit TUI-owned queues) and should be re-consulted at each phase boundary.
