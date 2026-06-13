# RESEARCH: TUI↔Agent Architectural Seam — co-cli vs Peer Survey

_Date: 2026-06-12_
_Peers surveyed: codex (Rust), opencode (TypeScript), hermes-agent (Python+TS), openclaw (TypeScript)_
_Method: direct source scans of `~/workspace_genai/{co-cli,codex,opencode,hermes-agent,openclaw}`. File:line anchors throughout. Distinct from [RESEARCH-tui-multimodal-peer-survey.md](RESEARCH-tui-multimodal-peer-survey.md), which covers multimodal *input intake*; this doc covers the *architectural boundary* between the display/UI layer and the agent loop._

---

## 0. Thesis

There is one question every terminal-agent architecture answers, explicitly or by accident: **what is the seam between the thing that owns the terminal and the thing that runs the model loop?** The five surveyed agents fall on a single axis defined by **process topology**, because topology forces the seam's shape:

- **Single process, single front-end** → a **direct call interface** is sufficient and lowest-overhead. **co-cli** alone sits here (the `Frontend` Protocol).
- **Single process, multiple front-ends (incl. out-of-process clients)** → a **serializable message/event protocol** carried over in-memory channels. **codex** sits here (the `app-server` Op/Event protocol over bounded mpsc).
- **Client–server (UI process ≠ core process)** → a **cross-process protocol** is mandatory: HTTP+SSE (**opencode**) or JSON-RPC over stdio/WebSocket (**hermes**, **openclaw**).

The converged "best practice" among the mature multi-surface agents is a **serializable message protocol with ≥2 independent consumers**. co-cli deliberately scopes one notch tighter than that convergence — a single in-process front-end family — and its call interface is the *correct* choice for that scope. The entire cost of the narrower scope is concentrated in exactly one place: the display seam is the only non-serializable seam in the survey. This doc establishes that claim in detail and identifies the precise condition under which it should be revisited (codex is the migration exemplar).

A second, universal finding cuts across all five: **the seam is proven real only by a second consumer that exercises it.** Every agent has ≥2 — and co-cli's `HeadlessFrontend` (run by the real eval loop) is its instance of that pattern. On *this* axis co-cli is squarely converged, not divergent.

Two further conclusions, developed in §7 and §8: the serializable-seam decision and the separate-process decision are **independent** (codex has the former without the latter) and must not be bundled as one "cleaner boundary" (§7); and the multi-instance future needs **one shared message envelope but two split transports** — a live session for the human, a durable polled queue between instances — not a single common chat transport (§8).

---

## 1. Convergence Matrix

| Dimension | **co-cli** | **codex** | **opencode** | **hermes-agent** | **openclaw** |
|---|---|---|---|---|---|
| Language | Python | Rust | TypeScript | Python core + TS TUI | TypeScript |
| Topology | Single process | Single process (TUI + app-server in one tokio runtime) | Client–server, local HTTP (server embedded *or* spawned daemon) | Cross-process always (TS TUI spawns Python gateway) | Embedded *or* remote gateway |
| **Seam mechanism** | **Call interface** — `Frontend` Protocol (typed async methods) | **Message protocol over channels** — `ClientRequest`/`ServerRequest`/`ServerNotification` enums on bounded mpsc | **HTTP routes + SSE event stream** (OpenAPI/SDK) | **JSON-RPC method registry** (stdio/WebSocket) | **JSON-RPC method registry** (WebSocket) |
| **Serializable across a process boundary?** | **No** (in-process Python objects) | **Yes** (same enums run over socket/stdio transports too) | Yes (HTTP/JSON) | Yes (JSON-RPC) | Yes (JSON-RPC) |
| Display transport | Direct method calls (`on_text_delta`, `on_tool_*`, `on_status`) | `ServerNotification` stream, lossless/best-effort tiers + `Lagged` marker | `GET /api/event` SSE subscription | Emitted JSON events (`_emit`) | `ReplyPayload` pipeline via `respond()` |
| Streaming throttle / backpressure | 20 FPS render throttle in `StreamRenderer`; no queue between agent and frontend (synchronous calls) | Bounded mpsc (cap 128); 7 lossless variants block sender, rest `try_send`-drop with `Lagged` | 16 ms debounce + auto-reconnect/backoff in TUI consumer; ephemeral vs durable event split | event queue in gatewayClient | accumulate-then-`respond()` |
| Approval mechanism | `await prompt_approval(subject) → 'y'/'n'/'a'` (direct awaitable) | `ServerRequest::…RequestApproval` ↔ response correlated by `request_id` | `permission.v2.asked` SSE event → `POST …/permission/:id/reply` | `approval.request` event ↔ `approval.respond` RPC | handler callbacks in dispatch |
| Terminal framework | `prompt_toolkit` (single `Application`, inline) + Rich as ANSI bridge | `ratatui` + `crossterm` (inline scrollback; alt-screen for overlays) | OpenTUI (Solid.js) | TS TUI (ink-style) over RPC | TS TUI over RPC |
| Terminal ownership model | One `Application` owns terminal; `run_in_terminal` suspends for blocking prompts | One unified `ChatWidget` draw loop; `tokio::select` over 4 event sources | Solid.js store reconciled from SSE events | client renders from event stream | client renders from event stream |
| **# process-separated consumers of the seam** | 2 (both in-process) | ≥3 (TUI, `codex-exec`, `mcp-server`) | ≥5 (TUI, web, desktop, Slack, SDK) | ≥3 (TUI, desktop, headless CLI) | ≥4 (TUI, web, CLI, operator UI) |
| Second-consumer proof | `TerminalFrontend` + `HeadlessFrontend` (eval loop runs real agent against no-op) | `codex-exec` uses the *same* protocol with programmatic approval | GitHub/Slack/web all hit the same HTTP API | gateway methods testable in isolation | gateway methods shared across surfaces |

**Spectrum (tightest → widest seam scope):** co-cli (in-process call interface) → codex (in-process serializable message protocol) → openclaw/hermes (RPC gateway) → opencode (HTTP+SSE server with web/desktop/Slack surfaces).

---

## 2. co-cli — Single Process, Call Interface (`Frontend` Protocol)

### Topology
A single Python process: the `co chat` REPL, the pydantic-ai agent loop, and the model client all live together. There is no server, no gateway, no second process for the foreground turn. (Background daemon work — dream — is a shared-state async split-brain of the same process, not a separate front-end; out of scope here.)

### The seam: `Frontend` Protocol
`co_cli/display/core.py` defines `Frontend` as a `@runtime_checkable Protocol` — a **typed async call interface**, not a message bus. Its surface (verified `core.py:251-317`):

- **Streaming:** `on_text_delta`, `on_text_commit`, `on_thinking_delta`, `on_thinking_commit`, `on_reasoning_progress`, `on_final_output`
- **Tool lifecycle:** `on_tool_start(tool_id, name, args_display)`, `on_tool_progress(tool_id, message)`, `on_tool_complete(tool_id, result)`
- **Status:** `on_status(message)`, `update_status(snapshot: StatusSnapshot)`, `clear_status()`
- **Interactive prompts (coroutines):** `async prompt_approval(subject) -> str`, `async prompt_question(prompt) -> str`, `async prompt_confirm(message) -> bool`
- **Teardown:** `cleanup()`

The agent loop reaches the user **only** through this object. In `co_cli/context/orchestrate.py` every user-facing effect is a `frontend.*` call — `prompt_question` (`:223`), `prompt_approval` (`:246`), `on_tool_start` (`:285`), `on_tool_complete` (`:328`/`:330`), `on_status` (e.g. `:487`/`:760`), `on_final_output` (`:616`), `cleanup` (`:404`). The two indirection points are still frontend-bound, not escapes: `deps.runtime.tool_progress_callback` (`:286`) and `deps.runtime.status_callback` (`:759`) are assigned closures over `frontend.on_tool_progress` / `frontend.on_status`.

### Display flow
`StreamRenderer` (`co_cli/display/stream_renderer.py`) is a per-segment buffer state machine that calls the frontend directly. It throttles live renders to **20 FPS** (`_RENDER_INTERVAL = 0.05`, `stream_renderer.py:32`), flushes thinking before text, and reduces thinking to short progress lines in `summary` mode (`_reduce_thinking`, `:123`). There is **no queue** between the renderer and the frontend — calls are synchronous. Backpressure is implicit: the agent loop is `await`-driven and the terminal render is fast enough that no buffering tier is needed (contrast codex, §3).

`TerminalFrontend` (`core.py:325-576`) drives one `prompt_toolkit.Application`. In-flight surfaces (assistant text, thinking, tool labels, status) render into a single ANSI buffer (`render_to_ansi`, `core.py:81`) shown by the app's in-flight window and updated via `app.invalidate()`; committed output prints to scrollback via `print_formatted_text(ANSI(...))`. The four live surfaces are mutually exclusive in time, so one in-flight region suffices.

### Approval flow
`prompt_approval` (`core.py:525`) is a direct awaitable returning `'y'`/`'n'`/`'a'`. It suspends the owned app via `run_in_terminal(_ask)`, which restores cooked-mode terminal ownership for a blocking `Prompt.ask`. There is no `request_id`, no correlation table, no message round-trip — the coroutine *is* the request/response.

### Terminal ownership
One `Application` owns the inline terminal (`full_screen=False`). `app.run_async()` runs under `patch_stdout()` so incidental `console.print` sites reflow above the input. Blocking prompts (`prompt_approval`/`prompt_question`/`prompt_confirm`) and the legacy arrow-key `prompt_selection` all funnel through `run_in_terminal`, which suspends the app and hands the terminal back cleanly. `TerminalFrontend` never calls `get_app()` itself — the app handle is injected via `bind_app` — which is exactly what makes `HeadlessFrontend` substitutable.

### The control seam (queue) is separate and topology-aware
co's only genuine async decoupling is the mid-turn input queue on `_ReplRuntime` (`co_cli/display/_app.py:69`): a `deque[str]` that buffers submissions arriving while a turn is active, drained one per turn boundary via the turn task's `add_done_callback`. The agent loop is **queue-agnostic** — `orchestrate.py` holds no reference to it. This is a bounded *control-signal* buffer, not a bus: text control-signals stay in-memory, and inter-instance collaboration (if ever built) would use a durable file-backed queue between processes, not an in-process bus (§8).

### Second-consumer proof
`HeadlessFrontend` (`co_cli/display/headless.py`) is a no-op implementation of the full protocol; it stores `last_status_snapshot` for inspection and mirrors the async prompt signatures. **The entire eval suite runs the real agent loop against this no-op frontend.** That this works end-to-end is the load-bearing proof that the agent loop is genuinely frontend-agnostic and the seam has not leaked.

### Guard rail
A tool must never import `co_cli/display` to print. User-facing output from inside a tool goes through `deps.runtime.tool_progress_callback` (the pattern at `orchestrate.py:286`) or the `Frontend` prompts the orchestrator exposes. A direct `console`/`display` import is an upward-import error of the same class the package one-way rule forbids ([01-system.md](../specs/01-system.md): `main → bootstrap → agent → tools / …`) and silently breaks `HeadlessFrontend` (and therefore evals).

---

## 3. codex — Single Process, Serializable Message Protocol (the sharp comparison)

codex is the most instructive peer because it shares co-cli's topology (single process) yet made the **opposite seam choice**, and for a specific, legible reason.

### Topology
TUI and core run in one tokio runtime. `InProcessAppServerClient` (`codex-rs/app-server-client/src/lib.rs`) bridges them by running the existing `MessageProcessor` and outbound routing on tasks, **replacing socket/stdio transports with bounded in-memory channels** (`codex-rs/app-server/src/in_process.rs:1-39`). The crucial point: it is the *same* `app-server` protocol codex uses over real sockets — the in-process path is an optimization of a serializable protocol, not a different mechanism.

### The seam: typed enums over bounded mpsc
- **UI→core:** `ClientRequest` / `ClientNotification`, wrapped as `InProcessClientMessage::{Request{response_tx}, Notification, ServerRequestResponse, ServerRequestError, Shutdown}` (`in_process.rs:170-189`).
- **core→UI:** `InProcessServerEvent::{ServerRequest, ServerNotification, Lagged{skipped}}` (`in_process.rs:156-163`).
- Protocol enums live in `codex-app-server-protocol` (`protocol/common.rs`).

### Display flow + backpressure (the part co-cli does not have)
`ServerNotification` (`protocol/common.rs:1519-1613`) has 85+ variants; display-relevant ones include `AgentMessageDelta` (`item/agentMessage/delta`), `ReasoningTextDelta`, `ReasoningSummaryTextDelta`, `PlanDelta`, `ItemStarted`/`ItemCompleted`, `TurnStarted`/`TurnCompleted`, `ThreadTokenUsageUpdated`, `CommandExecutionOutputDelta`, `McpToolCallProgress`.

Because a message protocol decouples producer from consumer, codex needs an explicit backpressure policy — a **two-tier delivery guarantee** (`app-server-client/src/lib.rs:175-273`):

```rust
fn server_notification_requires_delivery(n: &ServerNotification) -> bool {
    matches!(n,
        TurnCompleted(_) | ThreadSettingsUpdated(_) | ItemCompleted(_)
        | AgentMessageDelta(_) | PlanDelta(_)
        | ReasoningSummaryTextDelta(_) | ReasoningTextDelta(_))
}
```

- **Lossless tier** (7 variants — transcript-affecting deltas + completion): the sender **blocks** (`event_tx.send(event).await`) until the consumer drains. These can never be dropped.
- **Best-effort tier** (progress, exec output, etc.): `try_send`; on a full queue the event is dropped and a skip counter increments, surfaced to the UI as `InProcessServerEvent::Lagged { skipped }` so it can show a "lagged" notice. Orphaned server requests are rejected to avoid leaks.
- **Channel capacity:** `CHANNEL_CAPACITY = 128` (`app-server-transport/src/transport/mod.rs:24`), re-exported as `DEFAULT_IN_PROCESS_CHANNEL_CAPACITY`, overridable, clamped ≥1.

This entire tier system is **machinery co-cli does not need**, because co's synchronous call interface has no queue to saturate — the cost of co's simpler seam is that it can never decouple producer from consumer, and the benefit is that it never has to reason about lossy delivery.

### Approval flow
A `ServerRequest::CommandExecutionRequestApproval` / `FileChangeRequestApproval` / `PermissionsRequestApproval` / `ToolRequestUserInput` blocks the agent in `MessageProcessor`. The TUI (`tui/src/app/app_server_requests.rs`) stores `request_id`, shows a modal, and replies via `resolve_server_request(request_id, result)` → `InProcessClientMessage::ServerRequestResponse`. **Same request/response semantics as co's `prompt_approval`, but explicitly correlated by `request_id`** because the transport is a message channel, not a stack frame.

### Terminal ownership
`ratatui` + `crossterm`, `Terminal = CustomTerminal<CrosstermBackend<Stdout>>` (`tui/src/tui.rs:68`). **Inline scrollback by default** (not alt-screen): `init()` enables raw mode + bracketed paste but does not enter the alt screen (`tui.rs:362-371`); `enter_alt_screen`/`leave_alt_screen` are toggled only for overlays (pager, fullscreen diffs/approvals). One unified `ChatWidget` renders transcript + composer in a single frame; a `tokio::select!` over four sources (`app.rs:1160`) — `app_event_rx`, active-thread events, crossterm `tui_events`, and `app_server.next_event()` (the `ServerNotification` stream) — drives a `FrameRequester` that coalesces repaints. This is the same "single owner of the terminal, one draw surface" principle as co's single `Application`, implemented in a different framework.

### Second-consumer proof
`codex-exec` (headless) and `codex-rs/mcp-server` consume the **identical** `ClientRequest`/`ServerRequest`/`ServerNotification` protocol; `codex-exec` handles approvals programmatically (`EventProcessorWithHumanOutput` / `...JsonOutput`) instead of drawing modals. This — plus the fact that the same enums run over real sockets in non-embedded mode — is why codex paid for a serializable protocol in the first place: **one core, many surfaces, some out-of-process.**

---

## 4. opencode — Client–Server over HTTP + SSE (widest surface)

### Topology (corrected)
**There is no Go TUI.** A prior survey claim of a Go TUI is wrong for the current tree: zero `.go`/`go.mod` files exist; the old `packages/sdk/go/` was deleted (~May 2025). The TUI is **TypeScript on OpenTUI** (`@opentui/core`, `@opentui/solid`, `solid-js` — `packages/tui/package.json:54-56`, entry `packages/tui/src/index.tsx`, app `packages/tui/src/app.tsx`). It is nonetheless **client–server**: the TUI talks to an HTTP server that is either embedded in the same Node process or a spawned/shared daemon (`packages/cli/src/services/daemon.ts:38-160`, spawn at `:122-125`).

### The seam: HTTP routes + SSE
Effect `HttpApi` schema (`packages/server/src/api.ts:17-38`). Turn-relevant routes:
- `POST /api/session/:sessionID/prompt` — submit a turn (`groups/session.ts:109-128`); returns a durably-logged `SessionInput.Admitted`.
- `GET /api/session/:sessionID/message` — cursor-paginated message fetch (`groups/message.ts:25-48`).
- `POST /api/session/:sessionID/permission/:requestID/reply` — approval reply (`groups/permission.ts:68-85`), payload `{reply: "once"|"always"|"reject", message?}`, returns `204`.
- `GET /api/event` — SSE event stream (`groups/event.ts:16-32`, handler `handlers/event.ts:18-63`), filtered by directory/workspace.

### Display flow
A rich event taxonomy (`packages/core/src/session/event.ts:51-510`) split into **durable** (replayed) vs **ephemeral** (live-only) events — e.g. durable `session.next.text.started/ended`, `...tool.called/success/failed`, `...step.*`, `...reasoning.started/ended`, `...compaction.started/ended`; ephemeral `session.next.text.delta`, `...tool.input.delta`, `...reasoning.delta`, `...compaction.delta`. The TUI subscribes via the generated SDK `client.event()` and applies events into a Solid.js store with `reconcile` (`packages/tui/src/context/sync-v2.tsx:120-290`), **debouncing at 16 ms** and auto-reconnecting with exponential backoff (`packages/tui/src/context/sdk.tsx:82-117`).

### Approval flow
Bidirectional and split across transports: the server **publishes** `permission.v2.asked` over SSE (`packages/core/src/permission.ts:210`); the TUI renders a dialog; the reply is a **POST** to the permission route; the handler publishes `permission.v2.replied` and resolves a server-side `Deferred` (`permission.ts:234-250`).

### Second-consumer proof
The HTTP API has ≥5 consumers, all via `@opencode-ai/sdk`: the OpenTUI TUI, the Astro **web** app (`packages/web`), the Electron **desktop** app (`packages/desktop`), the **Slack** bridge (`packages/slack`), and the SDK itself. This is the strongest multi-surface justification in the survey.

---

## 5. hermes-agent & openclaw — JSON-RPC Method Registries

### hermes-agent (Python core + TS TUI)
Always cross-process. The TS TUI **spawns the Python gateway** as a child (`ui-tui/src/gatewayClient.ts:338`: `spawn(python, ['-m','tui_gateway.entry'])`) or attaches over WebSocket (`:420`). The seam is a **JSON-RPC method registry**, not a display abstraction: `_methods: dict[str, callable]` (`tui_gateway/server.py:125`), populated by an `@method(name)` decorator (`:763-768`), dispatched in `handle_request()` (`:790-799`). Display updates are emitted events (`_emit("message.start", sid)`, `server.py:4871`) consumed by `publish(ev)` in the client (`gatewayClient.ts:158-173`). A turn is `gw.request('prompt.submit', {session_id, text})` (`useSubmission.ts:110`) → handler (`server.py:4609-4668`) → `agent.run_conversation(...)` with a stream callback (`:5007`). Approvals are an `approval.request` event ↔ `approval.respond` RPC. Consumers: TUI, Electron desktop, headless CLI (`cli.py`), and unit tests that call `@method` handlers directly.

### openclaw (TypeScript)
Flexible topology: **embedded** agent in the TUI process (`src/tui/embedded-backend.ts`) **or** a remote gateway over WebSocket (`src/tui/gateway-chat.ts`). Even embedded, it routes through the **same JSON-RPC method registry** — `createCoreGatewayMethodDescriptors()` / `createGatewayMethodRegistry()` (`src/gateway/server.impl.ts:59-71`). A turn is `client.request("chat.send", {sessionKey, message})` (`gateway-chat.ts:195`) → handler (`server-methods/chat.ts:2949`) → `dispatchInboundMessage(...)` (`:3760`). Display flows through a `ReplyPayload` pipeline delivered via the method's `respond()` callback (`chat.ts:3543-3548`). Consumers: TUI, web/dashboard, CLI, operator UI, plugin-bound agents — all via the same `GatewayClient` protocol.

**Both** demonstrate the same convergence as opencode/codex from a different language: the UI↔core boundary is a serializable RPC surface with multiple consumers, *even when the two halves happen to share a process* (openclaw embedded mode). That is the tell — openclaw pays the RPC cost in-process for the *same* reason codex does: surface reuse, not intra-UI decoupling.

---

## 6. Cross-Cutting Dimensions

### 6.1 Seam serializability — the one real divergence
co-cli's `Frontend` is the **only non-serializable seam** in the survey. Every other agent's seam survives a process boundary unchanged (codex's enums over sockets; opencode's HTTP/JSON; hermes/openclaw's JSON-RPC). co's seam is in-process Python objects — `await frontend.prompt_approval(subject)` is a stack frame, not a message. This is not an oversight: it is the direct consequence of co's single-front-end scope, and it is what makes the seam cheap (no `request_id` correlation, no delivery tiers, no reconnect logic, no schema).

### 6.2 The "second consumer" test — universal, and co passes
Across all five, the seam is proven by a second consumer that exercises it independently:

| Agent | Primary surface | Independent second consumer(s) |
|---|---|---|
| co-cli | `TerminalFrontend` | `HeadlessFrontend` (real eval loop) |
| codex | ratatui TUI | `codex-exec`, `mcp-server` |
| opencode | OpenTUI TUI | web, desktop, Slack, SDK |
| hermes | TS TUI | desktop, headless CLI, tests |
| openclaw | TUI | web, CLI, operator UI |

The principle is converged best practice and co-cli follows it. The *difference* is only that co's two consumers are both in-process, so the test proves "frontend-agnostic" but not "process-agnostic."

### 6.3 Approval is request/response everywhere
No agent fire-and-forgets approvals. All five block the agent on a user decision: co (`await ... -> y/n/a`), codex (`ServerRequest` by `request_id`), opencode (SSE ask + POST reply + server `Deferred`), hermes (`approval.request`/`approval.respond`), openclaw (handler callbacks). co's version is the only one expressed as a language-level awaitable rather than a correlated message — again a topology consequence.

### 6.4 Backpressure: needed only when there's a queue
codex needs a two-tier lossless/best-effort delivery policy with a `Lagged` marker because its message channel can saturate. opencode debounces at 16 ms and reconnects because SSE is a network stream. **co-cli needs neither** — its synchronous 20 FPS-throttled calls have no inter-process queue to overflow. The mid-turn *input* queue (`_ReplRuntime`) is the only queue co has, and it buffers user control-signals, not agent→UI display, so it never participates in display backpressure. Less machinery is the dividend of the narrower seam.

### 6.5 Terminal ownership — universal "single owner, coalesced repaint"
Every TUI agent converges on **one entity owning the terminal with a single coalesced draw surface**, regardless of framework: co's single `prompt_toolkit.Application` + `render_to_ansi` bridge + `run_in_terminal` suspension; codex's single `ChatWidget` draw loop + `FrameRequester` + alt-screen-only-for-overlays; opencode's Solid.js store reconciled from SSE. This is the least controversial convergence and co-cli is fully aligned: inline (non-alt-screen) ownership, blocking prompts suspend cleanly, streaming coalesced into one in-flight region.

---

## 7. Verdict for co-cli (rejected-by-design / conditional, not gap)

1. **The call interface is correct for co's stated scope** — single user, single agent, single process, single front-end family. An in-process event bus there (the only alternative *within* one UI) buys nothing and adds correlation/delivery machinery for no consumer. This is a deliberate rejection, not a missing feature.

2. **co is converged on the dimensions that matter independent of topology**: a narrow load-bearing seam, a second consumer that proves it (`HeadlessFrontend`/evals), request/response approvals, single-owner coalesced terminal rendering, a topology-aware *control* queue. None of these are behind peer best practice.

3. **The single divergence is concentrated and conditional.** co's display seam is the only non-serializable one. The Q2/Q3 framing already made the *control* seam topology-aware (in-process buffer now, durable file queue for inter-instance later). The display seam is not — and that asymmetry is the thing to watch.

4. **The condition that would force a change** is the appearance of a **second, process-separated surface**: a web UI, an attachable/remote TUI, or *display* streaming between two co instances (not merely the disk-queue control signal). At that point the `Frontend` call interface is exactly the part that cannot cross the boundary.

5. **The protocol seam and the process split are independent decisions — and codex proves it.** "A serializable seam" and "the CLI is a separate process" are routinely bundled as one idea ("cleaner boundary"); they are not. codex has the fully serializable `Op/Event` protocol *and* keeps the TUI and agent in **one process**, carrying that protocol over in-memory channels (`in_process.rs:1-39`); it splits to a real process boundary only for the consumers that are genuinely out-of-process (`codex-exec`, `mcp-server`). So a serializable boundary does **not** require a process split — and the split, taken alone, is not a cleanliness win. It *adds* surface: a versioned wire schema, `request_id` correlation, transport-death handling, reconnect/backoff, and notification backpressure tiers (codex needs a 7-variant lossless/best-effort split *because* it has a channel that can saturate — `lib.rs:175-273`; co has no such queue today). The decoupling, the provable abstraction, and the testability that a split is supposed to buy, co **already has in-process** via the `Frontend` protocol + `HeadlessFrontend` + the one-way package rule. The only thing a split adds that in-process cannot is physical enforcement (an upward import becomes impossible-by-construction) and crash isolation — both real but minor for a single local CLI, and neither worth the boundary machinery on its own.

6. **The migration ladder is incremental, in demand order — codex first, opencode only later.** (a) *Now:* the `Frontend` call interface — correct; nothing to do. (b) *If the seam should be serializable/multi-consumer but stay local:* codex's pattern — keep one process, replace the call interface with a serializable `Op/Event`-style protocol over in-memory channels, the existing `Frontend` method names becoming notification/request variants. This is the "cleaner boundary," **minus** the process split. (c) *Only when a genuinely out-of-process surface exists* (web UI, attachable/remote TUI, or display-streaming between two instances — see §8): promote that same protocol across a process boundary (JSON-RPC or HTTP+SSE, like hermes/opencode). Each rung is justified by a concrete consumer, never by "cleaner" in the abstract.

**Bottom line:** co-cli is not behind the converged TUI best practice — it is scoped one notch tighter (single in-process surface), and pays for that scope in exactly one place (a non-serializable display seam). The choice is sound for the current product, the cost is localized and named, and the upgrade path is known and incremental. The boundary becomes serializable when there is a second consumer worth the schema (rung b); it becomes a separate process only when that consumer is genuinely out-of-process (rung c). Do not conflate the two.

---

## 8. Multi-Instance: One Shared Envelope, Two Split Transports

The forward-looking question is: *for multiple co instances to talk to each other **and** to the human at the CLI, what interface is needed?* The intuitive answer — "a common serializable chat interface" — is **half right**, and the half it gets wrong is the half that matters. "Chat interface" decomposes into two layers; one is common, one must not be.

### 8.1 What is genuinely common: the message envelope

The unit that crosses *every* boundary — human→co, co→human, co→co — is a **conversation message**. A single serializable envelope schema for all three is the correct unifying abstraction:

```
Message {
  from:        ParticipantId      # NEW — who sent it (human-cli, co-instance-A, …)
  to:          ParticipantId | Channel   # NEW — recipient / channel
  role:        user | assistant | tool
  parts:       [text | tool_call | tool_result | binary]   # already pydantic-ai shape
  ...
}
```

co already has ~80% of this. pydantic-ai `ModelMessage` types are serializable, and co persists them as JSONL transcripts via `persist_session_history()` (`co_cli/session/`). The *content* schema exists and is proven on disk. **The only missing field is identity/addressing** — today a message carries a `role` but no `from`/`to`, because there is exactly one human and one agent, so addressing is implicit. Multi-instance makes addressing explicit; that is the real new requirement, not a new transport.

### 8.2 What must NOT be common: the transport / delivery semantics

The trap is unifying *transport* across the human leg and the inter-instance leg. They have opposite requirements:

| | **human ↔ co (CLI)** | **co ↔ co** |
|---|---|---|
| Latency | live streaming (text deltas, tool progress) | turn-grained; no deltas needed |
| Interaction | interactive round-trips (`prompt_approval` y/n/a) | fire-and-collect; recipient may be offline |
| Durability | ephemeral OK (a human is watching) | **must survive a down/absent instance** |
| Co's existing doctrine | the `Frontend` seam (→ `Op/Event` if remote, §7 rung b/c) | **durable file-backed queue, consumer polls** |

co's own doctrine already pins the inter-instance transport and it is the *opposite* of the human seam: a **durable file/disk queue** as the sole cross-process bridge — the producer never gates on consumer liveness, and the consumer wakes by **polling / fs-watch, not a live socket or RPC nudge**. The human seam, by contrast, *is* a live interactive session. Collapse them into one transport and you get one of two failures: over-serialize the local UI (codex-grade machinery so a single human can watch a stream), or under-specify inter-agent delivery (lose durability — messages evaporate when the peer instance is down).

### 8.3 The accurate architecture

- **One serializable message envelope** (shared) — `from`/`to`/`role`/`parts`. *This is the thing the "common serializable chat interface" intuition correctly identifies.*
- **Two channels carrying it** (per-topology):
  - **Live session** for human↔co — the `Frontend` seam; serialized to an `Op/Event` protocol *only if* the CLI becomes a remote process (§7 rung b → c).
  - **Durable mailbox/queue** for co↔co — file-backed, polled, decoupled, per co's existing queue doctrine.
- **A routing/identity layer** — once there is >1 participant, every message needs "from whom, to whom." This is the genuinely new subsystem.

In this model **the human at the CLI is one participant among the co instances** — a client that speaks the common envelope but over the *live-session* transport, while instances exchange the same envelope over the *durable* transport. The envelope is common; the delivery is not.

### 8.4 Peer precedent

**openclaw is the one peer that already runs a multi-participant chat model**: a gateway with **channels, message provenance, and routing**, where humans and agents are peers in a shared channel (`src/gateway/server-methods/chat.ts` builds a `MsgContext` with provenance + routing, `:3479-3520`; `dispatchInboundMessage`, `:3760`). So the participant-in-a-shared-channel instinct is converged, not novel. The divergence co would keep: openclaw uses a **live WebSocket gateway for everything**; co's doctrine keeps the *inter-instance* leg **durable-file-backed and polled**, not a live bus — trading liveness for the decoupling/offline-survival its queue invariants require. codex and opencode, by contrast, keep inter-agent work (sub-sessions/sub-agents) *server-internal* and expose only the human seam — they are not multi-instance-peer systems, so they offer no precedent for the co↔co leg.

### 8.5 Net new work, in dependency order

1. **Identity/addressing on the message envelope** (`from`/`to`) — the prerequisite for everything; small, schema-level, and co's serialized transcripts already carry the rest.
2. **A durable inter-instance mailbox channel** — file-backed, polled, honoring the producer-not-gated-on-consumer and no-side-channel-nudge invariants. This is the co↔co transport.
3. **A routing/identity layer** — directory of participants, delivery to a `to` address or channel.
4. **(Conditional) Serialize the `Frontend` seam** — only if/when the CLI itself becomes a remote process (§7 rung c). Not required for instances to talk to *each other*; required only for the human to drive an instance across a process boundary.

The headline: **a common serializable envelope — yes, and co is most of the way there. A common transport — no; the human leg stays a live session, the inter-instance leg stays a durable polled queue, by co's own doctrine.**

---

## Appendix — File:line Index

**co-cli**
- `co_cli/display/core.py:251-317` — `Frontend` protocol surface
- `co_cli/display/core.py:325-576` — `TerminalFrontend`; `:81` `render_to_ansi`; `:525` `prompt_approval`
- `co_cli/display/headless.py` — `HeadlessFrontend`
- `co_cli/display/stream_renderer.py:32` `_RENDER_INTERVAL=0.05` (20 FPS); `:123` `_reduce_thinking`
- `co_cli/display/_app.py:69` — `_ReplRuntime.queue`
- `co_cli/context/orchestrate.py:223,246,285,286,328,330,404,487,616,759,760` — frontend call sites + callback bindings

**codex**
- `codex-rs/app-server/src/in_process.rs:1-39,156-189` — in-process bridge, message/event enums
- `codex-rs/app-server-client/src/lib.rs:175-273` — lossless/best-effort delivery, `Lagged`
- `codex-rs/app-server-transport/src/transport/mod.rs:24` — `CHANNEL_CAPACITY=128`
- `codex-rs/app-server-protocol/src/protocol/common.rs:1519-1613` — `ServerNotification` variants
- `codex-rs/tui/src/tui.rs:68,362-371` — terminal type, inline init
- `codex-rs/tui/src/app.rs:1160` — 4-way `tokio::select`; `:1349` single-frame render
- `codex-rs/tui/src/app/app_server_requests.rs` — approval `request_id` handling
- `codex-rs/exec/` — headless consumer of same protocol

**opencode**
- `packages/tui/package.json:54-56`, `packages/tui/src/index.tsx` — OpenTUI/Solid TUI (no Go)
- `packages/server/src/groups/{session,message,permission,event}.ts` — HTTP routes + SSE
- `packages/core/src/session/event.ts:51-510` — durable/ephemeral event taxonomy
- `packages/core/src/permission.ts:210,234-250` — permission publish/reply/Deferred
- `packages/tui/src/context/{sdk.tsx:82-117,sync-v2.tsx:120-290}` — SSE consume + Solid store
- `packages/cli/src/services/daemon.ts:38-160` — embed/spawn server
- consumers: `packages/{web,desktop,slack}`, `packages/sdk/js`

**hermes-agent**
- `tui_gateway/server.py:125,763-768,790-799` — method registry, `@method`, dispatch
- `tui_gateway/server.py:4609-4668,4871,5007` — `prompt.submit`, `_emit`, `run_conversation`
- `ui-tui/src/gatewayClient.ts:158-173,338,420` — publish, spawn, attach
- `cli.py` — headless consumer

**openclaw**
- `src/tui/{embedded-backend.ts,gateway-chat.ts}` — embed vs remote
- `src/gateway/server.impl.ts:59-71` — method registry
- `src/gateway/server-methods/chat.ts:2949,3543-3548,3760` — `chat.send`, reply pipeline, dispatch
