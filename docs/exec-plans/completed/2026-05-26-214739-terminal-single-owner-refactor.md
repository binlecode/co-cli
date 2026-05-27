# terminal-single-owner-refactor

## Context

This is a **Phase 0** refactor that must land **before** the `repl-input-queue` plan
(`docs/exec-plans/active/2026-05-23-151807-repl-input-queue.md`). User decision (approved):
adopt **Option B** terminal-ownership architecture, modeled on `hermes-agent` (the closest peer).

**Successor commitment (confirmed at Gate 1).** `repl-input-queue` is an **active, plan-approved
exec-plan** parked at its own Gate 1 pending this refactor; it is the committed next plan, not
speculative. This Phase 0 exists solely to unblock it, so the deferred-value risk is bounded:
the foundation has a named consumer. Two-way evidence the plans are coupled — (1) this plan's
Scope §"Downstream impact" lists the queue plan's constraints it invalidates; (2) the queue
plan's own Scope already assumes primitives this refactor supplies (async `prompt_*` via
producer-cancel, and **"first Ctrl+C press while turn running → cancel running turn task"** —
which is why BC2 below preserves mid-turn turn-cancel rather than dropping it).

Co's REPL today has **two libraries each owning the terminal**:
- **Output**: Rich `Live` — four streaming surfaces (`_live`, `_thinking_live`, `_tool_live`,
  `_status_live`; five constructor sites — `_status_live` is built in two branches) all on the
  module-global `console` (`co_cli/display/core.py:51`, `364/386/418/473/490`).
- **Input**: prompt_toolkit `PromptSession.prompt_async` (`co_cli/main.py:459,513`).

They never run concurrently. The chat loop is a strict **baton-pass**
(`co_cli/main.py:509–556`): `set_input_active(True)` → `await prompt_async()` →
`set_input_active(False)` → `await` turn (Live renders). `set_input_active`
(`co_cli/display/core.py:345`) is a cooperative flag that merely *buffers* status output during
the prompt — not real terminal arbitration. The baton-pass is precisely what blocks any
"type while busy" input-queue work: input capture and output rendering cannot coexist on the
terminal while two libraries both want to drive the cursor.

### Code-accuracy verification (anchors)

- Single themed console, `file=None`: `co_cli/display/core.py:51`. Rich resolves `sys.stdout`
  **dynamically per write** (`Console.file` property = `self._file or sys.stdout`), so a
  `patch_stdout` proxy is honored. The `repl-input-queue` plan's claim (line 42) that "Rich
  Console captures `sys.stdout` at module import" is **false** and must not propagate.
- Four `Live` instances + `set_input_active` buffering: `co_cli/display/core.py:345–499`.
- `Frontend` protocol: `co_cli/display/core.py:202`. `HeadlessFrontend`: `co_cli/display/headless.py:10`.
- Sync approval prompts `prompt_approval`/`prompt_question`/`prompt_confirm`:
  `co_cli/display/core.py:518–564` (use `Prompt.ask`/`console.input` + SIGINT swap).
- **Approval call stack is already async**: `_collect_deferred_tool_approvals`
  (`co_cli/context/orchestrate.py:197`, `async def`) calls the sync `prompt_approval` at line
  264; reached via `_run_approval_loop` (427) ← `run_turn` (724). Other call sites:
  `commands/_utils.py` (`prompt_confirm`).
- Raw `sys.stdout.write` arrow-menu: `prompt_selection` / `_render_selection`
  (`co_cli/display/core.py:99–175`) — termios raw-mode read, bypasses Rich entirely.
- Chat loop + `PromptSession(bottom_toolbar=frontend.render_footer_toolbar)`:
  `co_cli/main.py:459–556`. `_handle_one_input` (per-input state machine): `co_cli/main.py:321`.

### Reference: how hermes implements Option B (verified in `hermes-agent/cli.py`)

- `Application(..., full_screen=False)` owns the **inline** terminal (`cli.py:10839,10843`),
  with `Layout(HSplit([...]))` (`10765`) and a `TextArea` input widget (`10117`).
- Rich is demoted to a **stateless renderable→ANSI builder**: `Console` → `StringIO` →
  `print_formatted_text(ANSI(text))` (`cli.py:1602–1604, 1240–1243`; imports `47,61,68,69`).
- **Zero Rich `Live`** (`rg` confirms none).
- Streaming/dynamic regions use `FormattedTextControl` + throttled `app.invalidate()`
  (`_invalidate(min_interval=0.25)`, `cli.py:3274`).
- Synchronous interactive prompts (Prompt.ask, pickers) run via `run_in_terminal(...)`
  (`cli.py:2076, 7074, 7111`) — suspends the app, runs with clean terminal ownership, resumes.

## Problem & Outcome

**Problem.** Two terminal owners (Rich Live + prompt_toolkit) can only coexist via a
sequential baton-pass, so input and output never run at the same time. Every richer
interaction model — type-ahead while busy, an always-live prompt, mid-turn status — is
architecturally blocked until one library owns the terminal.

**Outcome.** A single owner: a persistent `prompt_toolkit.Application(full_screen=False)`
drives the inline REPL. Rich becomes a stateless renderable→ANSI builder; committed output is
emitted to scrollback via `print_formatted_text(ANSI(...))`; in-flight streaming renders into a
`FormattedTextControl` window updated by throttled `app.invalidate()` and committed to
scrollback on completion. Rich `Live` is removed entirely. **Observable UX is unchanged** from
today (inline prompt + scrollback transcript, streaming Markdown, panels, toolbar, approvals,
slash completion, FileHistory, Ctrl+C double-press exit, theme). This is a behavior-preserving
re-architecture whose payoff is the foundation it lays.

**Failure cost.** Without this, the `repl-input-queue` plan cannot deliver its headline
("type while busy, items stack up"): with two terminal owners the producer can't read stdin
while Live paints, so the queue can only fill via OS tty buffering at turn boundaries and the
depth indicator is meaningless. Phase 0 is the prerequisite that makes Phase 1 honest and
simple.

## Scope

**In (Phase 0 — behavior-preserving):**
- Persistent `Application(full_screen=False)` as the REPL driver, replacing the
  `while True: prompt_async()` loop.
- Stateless `render_to_ansi(renderable) -> str` bridge; all committed output routed through
  `print_formatted_text(ANSI(...))`.
- In-flight streaming via a single `FormattedTextControl` window + throttled `invalidate()`;
  commit-to-scrollback on completion. Removal of all Rich `Live` surfaces (five constructor sites).
- `prompt_approval`/`prompt_question`/`prompt_confirm` become **async** and run their blocking
  read via `run_in_terminal(...)`; the three already-async call sites `await` them.
- `prompt_selection` and any raw `sys.stdout.write` interactive paths wrapped in
  `run_in_terminal`.
- Bottom toolbar rendered by a layout `Window` (replacing `PromptSession.bottom_toolbar`),
  still fed by `frontend.render_footer_toolbar`.
- Ctrl+C double-press exit, EOF, `exit`/`quit`, slash dispatch — preserved through the
  Application's `accept_handler` → existing `_handle_one_input`.

**Out (deferred):**
- Input queue / type-while-busy / always-live-during-turn prompt → `repl-input-queue` (Phase 1).
  In Phase 0, submissions during an active turn are **dropped**, matching today's behavior.
- `/queue`, collect-mode, interrupt/steer, file-backed queue → later phases of `repl-input-queue`.
- Alt-screen / full-screen TUI, Textual adoption (Option C) — explicitly rejected.
- Any change to agent loop, tools, memory, or session behavior.

**Downstream impact on `repl-input-queue` (blocked-for-rewrite, not just sequencing).**
This plan invalidates several constraints of the successor plan; that plan must be revised
*after* this lands, before it proceeds past its own Gate 1. Required changes there:
- (a) Delete its line-42 false premise ("Rich `Console` captures `sys.stdout` at module
  import, so `patch_stdout` cannot proxy") — disproven at this plan's Context line 25–28, and
  `patch_stdout` is now the chosen mechanism (BC4).
- (b) Retire its C4 ("Sync frontend prompts require producer cancel + restart") and its TASK-3
  producer cancel/restart sandwich — stdin arbitration for approvals is now `run_in_terminal`'s
  job, since `prompt_*` are async (BC5).
- (c) Revise its C6 note ("the SIGINT swap stays as-is") and C10 ("`Frontend` protocol stays
  stable") — the SIGINT swap is removed and the protocol is already async post-Phase-0.
- (d) Re-baseline its phase boundary: the always-live-during-turn prompt it deferred to its own
  Phase 2 is unlocked cheaply once a single owner exists; its Phase 1 "type while busy" becomes
  honestly deliverable. (This plan does not edit the queue plan — the edit happens when that
  plan is reopened.)

## Behavioral Constraints

- **BC1. Inline, not alt-screen.** `full_screen=False`. The transcript stays in terminal
  scrollback; exiting leaves it intact. This is a REPL, like Claude Code/codex.
- **BC2. Observable UX identical to today.** Streaming Markdown reflow, thinking panel, tool
  panels, error/info/status rendering, toolbar segments, approval panels, slash completion menu,
  FileHistory up-arrow, theme switching, Ctrl+C semantics — all unchanged from a user's view.
  **Ctrl+C is fully behavior-preserving, including mid-turn:** today a single Ctrl+C during a
  running turn aborts the turn (KeyboardInterrupt unwinds the awaited `_run_foreground_turn`,
  caught at `main.py:539–551`) *and* arms the 2 s double-press-exit window (routes to
  `_handle_one_input(user_input=None)`, `main.py:344–357`). The new model reproduces both: the
  `c-c` key binding, when a turn task is active, **cancels that task** and routes to
  `_handle_one_input(user_input=None)` for the double-press arming. Only interrupt-with-requeue
  and steering are deferred to `repl-input-queue` — blunt turn-cancel is existing behavior and is
  kept. The thinking panel's *transient* erase-on-completion (`transient=True`, `core.py:386–391`)
  is also reproduced: the in-flight buffer clears the thinking region before the final dim text
  commits to scrollback.
- **BC3. Rich `Live` fully removed.** `grep -rn "rich.live\|from rich.live\| Live(" co_cli/`
  returns zero hits after this lands.
- **BC4. Single terminal owner, enforced by `patch_stdout`.** `app.run_async()` runs inside
  `prompt_toolkit.patch_stdout.patch_stdout()`. Because Rich's `console` resolves `sys.stdout`
  dynamically per write (verified, Context line 25), the ~128 incidental `console.print` /
  `display_*` call sites across `commands/*`, `bootstrap/banner.py`, `bootstrap/security.py`,
  `main.py` are automatically reflowed below the input area by the `StdoutProxy` — no per-site
  rewrite. Committed *streaming* output (assistant text, tool panels) routes explicitly through
  `print_formatted_text(ANSI(render_to_ansi(...)))`; in-flight streaming uses the
  `FormattedTextControl`. Background-thread writers (dream daemon, session-end kicks) that emit
  while the app runs route via `loop.call_soon_threadsafe` + `run_in_terminal` (hermes pattern,
  `cli.py:2001`). `patch_stdout` is the load-bearing half of single-ownership, not polish.
  **Verified mechanism (first-class, not coincidence): Rich's `Console.file` property is
  `self._file or sys.stdout` followed by `getattr(file, "rich_proxied_file", file)` —
  `rich_proxied_file` is precisely the attribute prompt_toolkit's `StdoutProxy` exposes. Rich
  cooperates with `patch_stdout` by design. This same verified fact is what the `repl-input-queue`
  correction (a) below must cite when deleting its line-42 false premise.**
  **Two-output-path ordering caveat (inherited seam):** path (a) `console.print`-via-`StdoutProxy`
  and path (b) `print_formatted_text` are only guaranteed ordering-safe under Phase 0's
  no-concurrency invariant (a turn owns the terminal exclusively; no idle `console.print`
  interleaves a live stream). `repl-input-queue` breaks that invariant and must re-examine this
  ordering — flagged here so the seam is explicit, not discovered later.
- **BC5. `prompt_*` become coroutines.** The `Frontend` protocol's three interactive prompts
  change from sync to `async def`. Justified: their call sites are already async
  (`orchestrate.py:197`), and `run_in_terminal` (the sanctioned mechanism under an owned app)
  is awaitable. `HeadlessFrontend` mirrors the async signatures.
- **BC6. No input queue.** Mid-turn submissions are dropped (today's behavior). The
  `accept_handler` has a single, clearly-marked seam where Phase 1 will swap drop→enqueue.
- **BC7. `HeadlessFrontend` remains a full `Frontend` implementation** for evals/tests, with
  the new async prompt signatures. The eval/test surface is preserved.
- **BC8. Zero backward-compat.** No Live fallback, no dual render path, no sync-prompt shim.
  The old `PromptSession`/`set_input_active`-buffering path is removed, not aliased.

## High-Level Design

```
 ┌──────────────── _chat_loop (session scope) ─────────────────┐
 │  app = Application(full_screen=False, layout=Layout(HSplit([ │
 │      Window(FormattedTextControl(get_inflight)),  # streaming│
 │      input_area: TextArea(completer, history, accept_handler)│
 │      Window(FormattedTextControl(get_toolbar), height=1),    │
 │  ])), key_bindings=kb, style=...)                            │
 │                                                              │
 │  committed output ──► print_formatted_text(ANSI(            │
 │                         render_to_ansi(renderable)))  ──► scrollback (above app)
 │                                                              │
 │  TerminalFrontend (single owner adapter):                    │
 │    on_text_delta/thinking/tool/status ─► set inflight text   │
 │                                          + app.invalidate()  │
 │                                            (throttled FPS)   │
 │    on_*_commit ─► print_formatted_text(ANSI(final))          │
 │                   + clear inflight + invalidate              │
 │    prompt_* (async) ─► await run_in_terminal(Prompt.ask…)    │
 │                                                              │
 │  input_area.accept_handler(text):                            │
 │    if turn active: drop  (Phase 1 seam: enqueue)             │
 │    else: schedule _handle_one_input(text, …) as turn task    │
 │                                                              │
 │  await app.run_async()   # top-level REPL driver             │
 └──────────────────────────────────────────────────────────────┘
```

**`StreamRenderer` is unchanged — a boundary the rewrite respects (F1/F6).**
`co_cli/display/stream_renderer.py` is the per-segment buffer state machine that drives *all*
`on_*_delta` / `on_*_commit` calls; it is Frontend-agnostic and stays as-is. Two consequences
the design relies on: (1) it **already throttles deltas at 20 FPS** (`_RENDER_INTERVAL = 0.05`),
so the frontend's delta handlers simply call `invalidate()` directly — **no second throttle**
inside `TerminalFrontend` (the diagram's "throttled FPS" note refers to this existing layer,
not a new one); (2) its `flush_for_tool_output()` commits text to scrollback *before* any tool
surface renders, so the live surfaces (text / thinking / tool labels / status) are
**mutually exclusive in time** — which is precisely what makes a *single* in-flight region
correct rather than an under-modeling.

**Why a `FormattedTextControl` window for streaming (vs. appending to scrollback per delta).**
co's UX re-renders growing Markdown in place (Live behavior). Appending each delta to
scrollback would lose live Markdown reflow (scrollback can't be rewritten). The faithful port
is hermes's model: hold the in-flight message in a `FormattedTextControl`, re-render its ANSI
on `invalidate()`, then on commit print the final block to scrollback and clear the control.
This accepts the same tall-content limitation Rich Live already has (content taller than the
viewport).

**Rich→ANSI bridge — the single rendering primitive (F4).** One stateless
`render_to_ansi(renderable, *, width) -> str`: a Rich `Console(file=StringIO(),
force_terminal=True, color_system=…, width=width)`, print the renderable, return the captured
string. It is the **sole** Rich-renderable→string routine; the committed-streaming path and the
in-flight control both go through it, so they cannot diverge in width/color from each other.
`render_to_ansi` stays pure — `width` is supplied by the caller (resolved from the bound app,
see below), never by reaching into `get_app()` from inside the helper.

**Two output paths, one named boundary (F4).** After this lands the terminal has exactly two
writers: (a) **incidental one-shot messages** — the ~128 `console.print`/`display_*` sites
(command output, banners, security findings) keep using the module-global `console`, made safe
by the `patch_stdout` wrap (BC4/TASK-4); (b) **streaming surfaces** — the assistant
text/thinking/tool/status regions go through the frontend → `render_to_ansi` →
`print_formatted_text(ANSI(...))` / the in-flight control. Path (a) is the exception for
fire-and-forget lines; path (b) is canonical for anything the frontend owns. New code emitting
a streaming surface uses (b); a one-shot status line uses `console.print`. Both render through
the same themed `console`, so styling is identical.

**App↔frontend binding — explicit, one-directional (F3).** The layout (built in `_app.py`)
needs the frontend (its in-flight control reads the frontend's buffer) and the frontend needs
the app (to call `invalidate()`). Resolve the cycle by construction order, not a hidden global:
`build_repl_app(...)` returns the `Application`, then `_chat_loop` calls `frontend.bind_app(app)`.
`TerminalFrontend` holds that handle and never calls prompt_toolkit's `get_app()` itself —
keeping it unit-testable with a stub app and free of ambient state. `bind_app` is **concrete to
`TerminalFrontend`, not added to the `Frontend` protocol** — `_chat_loop` constructs the
concrete `TerminalFrontend` directly (`main.py:456`), and `HeadlessFrontend` has no app, so the
protocol surface stays minimal (no method to stub headless-side).

**Approvals under an owned app.** `prompt_*` become `async def`; each `await
run_in_terminal(lambda: Prompt.ask(...))`. `run_in_terminal` suspends the Application's input
processing and restores cooked-mode terminal ownership for the blocking read, then resumes —
so the existing `Prompt.ask` body is reused verbatim. The SIGINT handler swap
(`core.py:524–525,550–551`) is removed: `run_in_terminal` owns terminal state for the duration.

**Concurrency model — co diverges from hermes here (be explicit).** hermes runs `app.run()`
**synchronously on the main thread** with the agent on a **daemon thread**
(`hermes-agent/cli.py:14625` `app.run()`; `11781` `Thread(target=run_agent, daemon=True)`),
bridging cross-thread via `patch_stdout` thread-safety + `invalidate()`. co is already
fully-async (`run_turn` and its whole stack are asyncio), so co adopts the
**`app.run_async()` + same-loop turn-task** pattern instead: the `accept_handler` schedules the
turn via `asyncio.ensure_future(...)`, frontend callbacks fire on the same loop and call
`invalidate()`, and `run_in_terminal` (awaited from the turn task) handles approval stdin. This
is a legitimate prompt_toolkit pattern but is *not* hermes's thread model — its hazards
(accept_handler must not block the app input loop; the in-flight `FormattedTextControl` may be
mid-render when the turn task yields) are co-specific and validated by TASK-7 (unit: scheduling +
mid-turn Ctrl+C cancel) and TASK-8 (integration: invalidate + `run_in_terminal` while a delta is
mid-render). We borrow
hermes's *primitives* (`patch_stdout`, ANSI bridge, `FormattedTextControl`, `run_in_terminal`),
not its threading.

**Migration shape.** This is a big-bang within the terminal layer (`display/core.py` +
`main.py:_chat_loop`) — there is no coherent half-Live/half-Application intermediate. It ships
as one plan. Each task is independently unit-testable; the integration smoke (TASK-8) is the
gate.

## Tasks

### ✓ DONE TASK-1: Stateless Rich→ANSI render bridge

- files: `co_cli/display/core.py` (add `render_to_ansi`), `tests/test_display.py`
- done_when: `uv run pytest -x tests/test_display.py::test_render_to_ansi_panel` passes —
  asserts `render_to_ansi(Panel("hi"), width=40)` returns a non-empty string that contains an
  ANSI escape sequence (`\x1b[`) and is byte-identical across two calls with the same args
  (statelessness). No assertion on specific Rich box-drawing glyphs (version-brittle, CD-m-2).
- success_signal: N/A (refactor primitive).
- prerequisites: none

### ✓ DONE TASK-2: Application scaffold (layout, input, toolbar, key bindings)

- files: **new** `co_cli/display/_app.py` (the layout/app factory — distinct concern from
  themes/protocol/`TerminalFrontend`, so it does not bloat `core.py`; package-private per the
  `_prefix` convention, `__init__.py` stays docstring-only; `_app.py` imports `render_to_ansi`
  from `core.py`, never the reverse — F2), `co_cli/main.py`
- done_when: a `build_repl_app(frontend, completer, history, accept_handler, key_bindings)`
  factory in `_app.py` returns an `Application(full_screen=False)` whose `Layout` contains the
  in-flight window, the input `TextArea`, and the toolbar window. The `TextArea` carries over today's
  completion UX: `completer`, FileHistory, `complete_while_typing=True`, and the
  `_COMPLETION_STYLE` from `main.py:439–449` (CD-m-4). Key bindings include explicit
  `@kb.add('c-c')` and `@kb.add('c-d')` handlers that translate the keystroke into
  `_handle_one_input(user_input=None, eof=...)` (CD-M-4). **Mid-turn Ctrl+C is NOT a no-op — it is
  behavior-preserving (BC2):** when a turn task is active the `c-c` handler cancels that task and
  routes to `_handle_one_input(user_input=None)` so the 2 s double-press-exit arming is identical
  to today. Only interrupt-with-requeue / steering is deferred to `repl-input-queue`; blunt
  turn-cancel is kept because the successor plan already assumes it exists.
  `uv run pytest -x tests/test_display.py::test_repl_app_builds` constructs it headlessly
  (no `.run()` — a `full_screen=False` Application builds without a tty, verified) and asserts
  the three layout regions are present, the toolbar window's text callable invokes
  `frontend.render_footer_toolbar`, the completion style is applied, the idle `c-c` binding
  dispatches to `_handle_one_input` with `user_input=None`, and a `c-c` binding fired while a
  (stub) turn task is active cancels that task before routing to `_handle_one_input`.
- success_signal: N/A.
- prerequisites: TASK-1

### ✓ DONE TASK-3: Rewrite `TerminalFrontend` to drive the Application (remove Rich Live)

- files: `co_cli/display/core.py`, `co_cli/display/headless.py`, `tests/test_display.py`
- done_when: `TerminalFrontend` gains `bind_app(app)` (F3) and holds the handle (no `get_app()`
  calls inside the frontend); `on_text_delta`/`on_thinking_delta`/`on_tool_*`/`on_status`/
  `on_reasoning_progress` update a **single** in-flight ANSI buffer and call `app.invalidate()`
  directly — **no second throttle** (StreamRenderer already throttles at 20 FPS, F1); `on_*_commit`/
  `on_final_output` route through `print_formatted_text(ANSI(render_to_ansi(...)))`. **Dead-code
  removal set (F5)** — all of the following are deleted, not stubbed:
  Rich `Live` surfaces (five sites: `core.py:364/386/418/473/490`) and the `_clear_status_live`
  + every `_*_live` helper; `set_input_active` (from the `Frontend` protocol, `TerminalFrontend`,
  `HeadlessFrontend`, **and** its four `main.py` call sites); `_pending_status` field; the
  introspection orphans `active_surface`/`active_tool_messages`/`active_status_text`
  (`core.py:304–322`, CD-m-1) and the now write-only `_status_text`; `clear_status` is either
  redefined to clear the in-flight buffer or removed (its one `main.py` caller updated
  accordingly). **Kept:** `_active_tools`/`_tool_labels` (still drive the multi-tool label block
  and map completion→panel title). Gates: `grep -rn "rich.live\| Live(\|set_input_active\|_pending_status\|_status_live" co_cli/`
  returns zero; `uv run pytest -x tests/test_display.py` passes (existing `render_footer_toolbar`
  tests unchanged; new tests assert in-flight buffer content after a delta and that commit clears it).
  **Transient-thinking parity (BC2):** today `_thinking_live` is `transient=True` (the live thinking
  region erases itself on stop, then `on_thinking_commit` prints the final dim text to scrollback).
  The port must reproduce this: `on_thinking_delta` fills the in-flight buffer; `on_thinking_commit`
  **clears the in-flight buffer (erasing the region)** and prints the final dim text via
  `print_formatted_text`. A test asserts the in-flight buffer is empty after `on_thinking_commit`
  and the committed text is the final thinking text — distinguishing transient-thinking from the
  text-commit path (which commits its in-flight content rather than discarding it).
- success_signal: N/A.
- prerequisites: TASK-2

### ✓ DONE TASK-4: `patch_stdout` wrap + module-console routing + background writers

- files: `co_cli/main.py` (wrap `app.run_async()` in `patch_stdout()`), `co_cli/display/core.py`
  (ensure committed streaming output uses `print_formatted_text(ANSI(...))`), audit of
  background writers (`maybe_autospawn_dream`, `_fire_session_end_kicks`)
- done_when: `app.run_async()` runs inside `patch_stdout()`; a unit test
  `tests/test_display.py::test_console_print_reflows_under_patch_stdout` drives a
  `create_pipe_input()` + `AppSession` app, calls a representative `console.print(...)` while
  the app is "running", and asserts the text is emitted via the `StdoutProxy` (not raw to the
  original `sys.stdout`); any background-thread writer found in the audit routes via
  `loop.call_soon_threadsafe` + `run_in_terminal` (hermes `cli.py:2001`), documented in
  delivery. This is the load-bearing mechanism behind BC4.
- success_signal: N/A.
- prerequisites: TASK-3

### ✓ DONE TASK-5: Async `prompt_*` via `run_in_terminal`

- files: `co_cli/display/core.py` (`prompt_approval`/`prompt_question`/`prompt_confirm` →
  `async def`, body wrapped in `await run_in_terminal(...)`; the SIGINT swap is removed from
  `prompt_approval` and `prompt_question` only — `prompt_confirm` (`core.py:561`) uses
  `console.input` and has no SIGINT swap to remove),
  `co_cli/display/headless.py` (async signatures), `co_cli/context/orchestrate.py:264`
  (await the call), `co_cli/commands/_utils.py` (`_confirm` at line 8 → `async def`, await
  `prompt_confirm`), `co_cli/commands/memory.py:117,192` (await `_confirm` — both inside async
  `_cmd_*` handlers), `tests/test_display.py` (CD-M-3 / PO-m-1: full transitive await cascade)
- done_when: `grep -rn "signal.signal(signal.SIGINT" co_cli/display/core.py` returns zero;
  `grep -rn "def _confirm" co_cli/commands/_utils.py` shows `async def`;
  `uv run pytest -x tests/test_flow_chat_loop.py` passes with the async `HeadlessFrontend`
  prompts; a new `tests/test_display.py::test_prompt_approval_is_coroutine` asserts the method
  is a coroutine function and that `HeadlessFrontend.prompt_approval` awaits to the canned
  response. The no-frontend fallback in `_confirm` (`ctx.frontend is None`) stays a direct sync
  `console.input(msg)` read — not wrapped in `run_in_terminal` (no owned app in that path), so
  do not asyncify it (CD-m-6).
- success_signal: N/A.
- prerequisites: TASK-3

### ✓ DONE TASK-6: Route interactive `prompt_selection` through `run_in_terminal`

- files: `co_cli/display/core.py` (`prompt_selection`), call sites from `rg -rn "prompt_selection" co_cli/`
  (the in-REPL caller is `co_cli/commands/resume.py:80`)
- done_when: `prompt_selection`'s termios read executes inside `run_in_terminal` when
  `get_app_or_none() is not None`, and runs inline (direct read) otherwise — for non-REPL
  callers (CD-m-3, mirroring hermes `cli.py:7103–7117`);
  `uv run pytest -x tests/test_display.py` passes; manual smoke noted in delivery: the
  `/resume` picker renders correctly inside the REPL without corrupting the input line.
- success_signal: N/A.
- prerequisites: TASK-3

### ✓ DONE TASK-7: Rewrite `_chat_loop` to run on `app.run_async()`

- files: `co_cli/main.py`, `tests/test_flow_chat_loop.py`
- done_when: `_chat_loop` constructs the app (TASK-2), calls `frontend.bind_app(app)` (F3),
  wraps it in `patch_stdout()` (TASK-4), and drives the REPL via `await app.run_async()`. The
  shared turn state — the current turn task reference and the is-active flag read by both the
  `accept_handler` ("if turn active: drop") and the Ctrl+C key binding — has **one owner**: a
  single mutable holder created in `_chat_loop` scope and passed by reference to the factory's
  `accept_handler` and `key_bindings` (extend `_IterationState` rather than introduce module
  globals — F7). The holder carries the **turn-task reference** so the `c-c` key binding can
  cancel an active turn (BC2 mid-turn Ctrl+C). The `accept_handler` schedules the turn via
  `asyncio.ensure_future(...)` running the existing `_handle_one_input` (unchanged contract) for
  idle submissions and drops mid-turn submissions; **mid-turn Ctrl+C cancels the held turn task
  then routes to `_handle_one_input(user_input=None)`** (preserving turn-abort + double-press
  arming); idle Ctrl+C double-press exit, EOF, `exit`/`quit`, and slash dispatch all route through
  `_handle_one_input`;
  `PromptSession`/`bottom_toolbar`/`set_input_active` wiring is removed.
  **Concurrency-hazard discipline (co's same-loop divergence from hermes — see High-Level Design):
  the `accept_handler` must not block the app input loop (it only schedules), and the in-flight
  `FormattedTextControl` may be mid-render when the turn task yields.** Tests:
  `tests/test_flow_chat_loop.py::test_accept_handler_schedules_turn_task` asserts the accept_handler
  path schedules `_handle_one_input` for an idle submission and is a no-op for a mid-turn submission
  (the explicit accept_handler → ensure_future → callback flow, CD-M-2);
  `tests/test_flow_chat_loop.py::test_ctrl_c_cancels_active_turn` asserts a `c-c` while a turn task
  is active cancels that task and arms the double-press window (and a second `c-c` within 2 s
  exits);
  `uv run pytest -x tests/test_flow_chat_loop.py` passes (existing `_handle_one_input`
  behavioral tests intact).
- success_signal: REPL launches, accepts a prompt, streams a turn, and returns to an idle
  prompt — identical to today.
- prerequisites: TASK-3, TASK-4, TASK-5

### ✓ DONE TASK-8: Integration smoke — REPL renders a real turn under a running owned Application

- files: `tests/integration/test_repl_terminal_owner.py` (new)
- done_when: an integration test drives a **genuinely running** `app.run_async()` via
  prompt_toolkit's `create_pipe_input()` + `AppSession` (so `run_in_terminal`'s suspend/restore
  path is real, not the no-app inline fallback — CD-M-5), with a real warm Ollama model
  (`ensure_ollama_warm()` outside any `asyncio.timeout`, per memory). It feeds one prompt
  through the pipe into the accept_handler, awaits the turn under
  `asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS)` with `noreason_model_settings()`, and
  asserts: the turn produced committed output, the in-flight buffer is empty at end, no Rich
  `Live` was instantiated, and an approval round-trip resolves via the async `prompt_approval`
  under `run_in_terminal`. **It also exercises the same-loop concurrency hazard directly: while the
  turn task is streaming (in-flight buffer non-empty, a delta mid-render), it issues an
  `app.invalidate()` and a `run_in_terminal` approval and asserts neither corrupts the in-flight
  buffer nor the committed scrollback — the explicit validation of co's `run_async` + turn-task
  divergence from hermes's thread model (High-Level Design §Concurrency).** PLUS a mandatory
  **manual tty smoke** documented in delivery (real terminal: streaming Markdown reflow on a long
  answer, an approval panel mid-turn, a theme switch, `/resume` picker, **and a mid-turn Ctrl+C
  that aborts the turn then a second within 2 s that exits**) — the parity checks named in Testing.
- success_signal: A full turn streams and commits to scrollback under a running single-owner
  Application, end to end.
- prerequisites: TASK-1, TASK-2, TASK-3, TASK-4, TASK-5, TASK-7

## Testing

Per `.agent_docs/testing.md` and project memory:
- `pytest -x` (fail-fast); rerun `--lf` after fixing.
- LLM-touching tests use `noreason_model_settings()`/`reasoning_model_settings()` and hit
  `llm.host` from config; never probe Ollama directly.
- `ensure_ollama_warm()` outside `asyncio.timeout`.
- Watch LLM call durations; don't paper over slow calls with timeout bumps.
- All runs piped to `.pytest-logs/<timestamp>-<scope>.log`.

Coverage focus (critical functionality, not count):
- `render_to_ansi` statelessness + faithful styling.
- In-flight streaming: delta updates the buffer; commit flushes to scrollback and clears.
- Async approval round-trip under `run_in_terminal` (headless: awaits to canned response).
- `_handle_one_input` behavioral parity (existing suite unchanged).
- Integration: a real turn streams and commits under a running owned Application; zero Live.

**Gate-2 parity checks (human-eyeballable, behavior-preserving — PO-m-2).** "Identical to today"
is verified at Gate 2 against this concrete before/after list, not asserted:
1. Streaming Markdown reflows correctly on a long multi-paragraph answer.
2. An approval panel appears mid-turn and resolves on `y`/`n`/`a`.
3. `/resume` arrow-key picker renders and selects without corrupting the input line.
4. Theme switch (`--theme` / theme command) recolors output.
5. Ctrl+C while idle once shows the exit hint; twice within 2s exits; slash completion menu
   styling intact.
6. Ctrl+C **during a running turn** aborts the turn and shows the exit hint; a second Ctrl+C
   within 2s exits (mid-turn behavior identical to today — BC2).
7. The thinking region appears while reasoning, then erases and leaves only the final dim
   thinking text (transient parity — BC2).

Tests to modify (not add):
- `tests/test_flow_chat_loop.py` — `HeadlessFrontend` prompt methods become async; any direct
  prompt-call assertions await. `_handle_one_input` contract is otherwise unchanged.
- `tests/test_display.py` — `render_footer_toolbar` tests unchanged; Live-specific assertions
  (if any) replaced by in-flight-buffer assertions.

## Open Questions

- **OQ-1 (tall in-flight content).** A `FormattedTextControl` window holding a response taller
  than the viewport has the same limitation Rich Live has today. Proposed resolution: accept
  Live-parity in Phase 0; revisit incremental paragraph-commit only if it regresses UX.
- ~~**OQ-2 (protocol async change, BC5).**~~ Resolved (PO-M-1): adopted. `prompt_*` become
  coroutines; the downstream-impact subsection in Scope records that `repl-input-queue` is
  blocked-for-rewrite as a result (its C4/C6/C10 are superseded).
- ~~**OQ-3 (background writers).**~~ Resolved (CD-m-5): folded into TASK-4. Background-thread
  writers route via `loop.call_soon_threadsafe` + `run_in_terminal`; incidental `console.print`
  is covered by the `patch_stdout` wrap (BC4).

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev terminal-single-owner-refactor`

### Gate 1 — PO verdict (2026-05-26): APPROVED

**Right problem — yes (verified against source).** Two-terminal-owners baton-pass confirmed
(`main.py:512–514`); the BC4 `patch_stdout` premise verified and *stronger* than originally
stated — Rich's `Console.file` honors `rich_proxied_file`, the exact `StdoutProxy` hook (first-class
cooperation). All anchors check out (5 `Live` sites, 4 `set_input_active` call sites, `resume.py:80`,
SIGINT swap); TASK-5's async-cascade caller list is exhaustive (verified: only `orchestrate.py:245/264`
and `_utils._confirm`←`memory.py:117,192`).

**Correct scope — yes, after the following fixes (applied to this plan at Gate 1):**
1. **MUST-FIX (resolved): mid-turn Ctrl+C.** Original TASK-2 made it a no-op, contradicting BC2 and
   deleting behavior `repl-input-queue` already assumes. Now Phase 0 **preserves turn-cancel**:
   `c-c` cancels the active turn task then arms double-press exit (BC2, TASK-2, TASK-7, Gate-2 #6).
2. Concurrency divergence (co's `run_async`+turn-task vs hermes thread model) now validated by both
   TASK-7 (mid-turn cancel) and TASK-8 (invalidate + `run_in_terminal` while a delta is mid-render).
3. Transient-thinking parity made an explicit TASK-3 test + Gate-2 #7.
4. BC4 cites the verified `rich_proxied_file` hook; two-output-path ordering flagged as a seam
   inherited by `repl-input-queue`.
5. TASK-5 SIGINT-swap wording corrected (`prompt_confirm` has none).

**Deferred-value risk — bounded.** `repl-input-queue` confirmed as the **committed next plan**
(active, plan-approved exec-plan at its own Gate 1); this Phase 0 has a named consumer. See Context
§"Successor commitment".

> Ready for `/orchestrate-dev terminal-single-owner-refactor`.

## Delivery Summary — 2026-05-26

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `test_render_to_ansi_panel` — ANSI present + byte-identical across calls | ✓ pass |
| TASK-2 | `test_repl_app_builds` — 3 layout regions, toolbar callable, completion style, idle/active-turn c-c | ✓ pass |
| TASK-3 | grep `rich.live\|Live(\|set_input_active\|_pending_status\|_status_live` zero; `test_display.py` green; transient-thinking parity | ✓ pass |
| TASK-4 | `test_console_print_reflows_under_patch_stdout` via StdoutProxy; background-writer audit | ✓ pass |
| TASK-5 | SIGINT-swap grep zero; `_confirm` async; `test_flow_chat_loop.py` green; `prompt_approval` is coroutine | ✓ pass |
| TASK-6 | `prompt_selection` termios read inside `run_in_terminal` when app present, inline otherwise; `test_display.py` green | ✓ pass |
| TASK-7 | `_chat_loop` on `app.run_async()` + `bind_app` + `patch_stdout`; `_ReplRuntime` single owner; accept-schedule + mid-turn-cancel tests | ✓ pass |
| TASK-8 | real turn streams + commits under a running owned Application; in-flight empty; no Live; concurrency hazard | ✓ pass |

**Tests:** scoped — test_display.py (21), test_flow_chat_loop.py (10, incl. 1 warm-LLM turn + accept/cancel),
test_flow_tool_call_functional.py (4, incl. async clarify/approval cascade), approval_subject+delegation+agent_build (23),
integration/test_repl_terminal_owner.py (1, real warm Ollama turn ~17s). All passed. Lint clean.

**Doc Sync:** fixed — `tui.md` (REPL diagram + loop section: Application/run_async, accept_handler scheduling,
mid-turn Ctrl+C, patch_stdout; Frontend surface: async prompts, single-owner TerminalFrontend, render_to_ansi,
_app.py symbols, toolbar Window; Files +_app.py), `core-loop.md` (turn-flow diagram entry + async approval prompts /
run_in_terminal note), `bootstrap.md` (startup sequence: SlashCommandCompleter + FileHistory).

**Overall: DELIVERED**

Behavior-preserving single-owner re-architecture landed: a persistent `prompt_toolkit.Application(full_screen=False)`
drives the inline REPL; Rich is demoted to the stateless `render_to_ansi` bridge; all Rich `Live` and the
`PromptSession`/`set_input_active` baton-pass are removed; `prompt_*` are coroutines run via `run_in_terminal`.

**Implementation notes / deviations from the literal plan (all sound):**
- **TASK-1:** the literal `Panel("hi")` carries `border_style="none"` and emits *no* ANSI; the test uses a styled
  panel (`border_style="cyan"`) — the real path every app panel exercises — rather than distorting production to
  satisfy a degenerate renderable.
- **TASK-4 ordering:** the `patch_stdout()` *wrap* physically lands in TASK-7's `_chat_loop` rewrite (it can only
  exist once `app.run_async()` exists). The background-writer audit found **zero in-process threads** in `co_cli/`:
  status callbacks fire on the asyncio loop, the dream daemon is a separate `spawn_detached` process, and
  session-end kicks are disk-queue writes — so the anticipated `loop.call_soon_threadsafe` + `run_in_terminal`
  routing is **not needed** (nothing to route).
- **TASK-2/F7:** the single turn-state holder is `_ReplRuntime` (in `_app.py`), carrying `state` + `turn_task` +
  a `control_tasks` set (retains c-c/c-d dispatch task refs; RUF006). The turn-task reference lives there rather
  than on the per-iteration `_IterationState` (which `_handle_one_input` replaces wholesale each turn).
- **TASK-8:** the live approval *keystroke* round-trip rides Rich's `Prompt.ask`, which reads the real tty and
  cannot be driven deterministically without mocking (forbidden). The integration test validates the
  `run_in_terminal` mechanism it rides on (canned callback under the genuinely-running app, asserting no in-flight/
  scrollback corruption); the typed-`y`/`n`/`a` round-trip is part of the mandatory manual tty smoke (Gate-2 #2).

**Next step:** `/review-impl terminal-single-owner-refactor` — full suite + evidence scan + auto-fix → verdict.

## Implementation Review — 2026-05-27

Stance: issues exist, PASS is earned. Per-task evidence collected by 8 parallel subagents, then
all high-risk citations cold-re-verified by an adversarial subagent (10/10 CONFIRMED-PASS, zero
downgrades).

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `test_render_to_ansi_panel` — ANSI present, byte-identical across calls | ✓ pass | `core.py:81` `render_to_ansi(renderable, *, width: int) -> str` — keyword-only, pure (no `get_app()`, no module mutation); sole render routine feeds in-flight `:378` + committed `:389` |
| TASK-2 | `test_repl_app_builds` — 3 regions, toolbar callable, completion style, idle/active-turn c-c | ✓ pass | `_app.py:100-107` `build_repl_app`; layout `:134-137`; c-c cancel-then-dispatch `:87-91`; import direction one-way (`_app`→`core`, F2) |
| TASK-3 | dead-code grep zero; `test_display.py` green; transient-thinking parity | ✓ pass | `bind_app` `core.py:351-357`, no `get_app()` in frontend; single in-flight `:341`; commit via `print_formatted_text(ANSI(render_to_ansi(...)))` `:387-389`; gate grep → zero; `on_thinking_commit` clears-then-commits `:434-439` vs `on_text_commit` commits-then-clears `:426-428` |
| TASK-4 | `test_console_print_reflows_under_patch_stdout` via StdoutProxy; bg-writer audit | ✓ pass | `patch_stdout()` wraps `await app.run_async()` `main.py:559-560`; audit confirmed **zero in-process threads** in `co_cli/` (dream = detached process, kicks = disk-queue writes) |
| TASK-5 | SIGINT-swap grep zero; `_confirm` async; `test_flow_chat_loop.py` green; coroutine assertion | ✓ pass | `prompt_approval/question/confirm` async + `run_in_terminal` `core.py:514/536/555`; SIGINT swap gone; await cascade complete (`orchestrate.py:245/264`, `_utils._confirm:8`, `memory.py:117/192`); no-frontend fallback stays sync `console.input` `_utils.py:16` |
| TASK-6 | `prompt_selection` termios inside `run_in_terminal` when app present, inline otherwise | ✓ pass | branch correct (not inverted) `core.py:221-223`; `prompt_selection` async, awaited at `resume.py:80` (handler async); `test_display.py` 22 passed |
| TASK-7 | `_chat_loop` on `app.run_async()` + `bind_app` + `patch_stdout`; single owner; accept/cancel tests | ✓ pass | `build_repl_app`/`bind_app`/`patch_stdout`/`run_async` `main.py:547-560`; **`turn_active` is a computed `@property` `_app.py:63-65`** (no stored flag → no drop-forever bug); accept_handler drops mid-turn, `ensure_future` for idle `main.py:452-454`; `PromptSession`/`bottom_toolbar`/`set_input_active` grep → zero |
| TASK-8 | real turn streams + commits under running owned Application; no Live; concurrency hazard | ✓ pass | `create_pipe_input()`+`create_app_session` genuinely-running app; `ensure_ollama_warm()` outside `asyncio.timeout`; `noreason_model_settings()` via config; **no mocks** anywhere; asserts committed output, empty in-flight, no Rich Live attrs, `run_in_terminal` approval, invalidate+approval-while-delta-mid-render → no corruption. ~17s warm turn |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Clean-exit crash: `MemoryStore` has no `close()` — `_drain_and_cleanup` raised `AttributeError` on every session end | `main.py:216` | blocking | Routed through the `.index` property: `deps.memory_store.index.close()` (`IndexStore.close()` exists, closes the FTS5 conn, idempotent). **Pre-existing** (introduced `8c86d658`, 2026-05-12) — surfaced by Phase 7 behavioral verification; fixed per review policy (fix pre-existing issues found during review). |
| Extra file in diff: `stream_renderer.py` (plan declares StreamRenderer unchanged, F1/F6) | `stream_renderer.py:110` | minor | Single stale-comment fix replacing a reference to the removed `_status_live` — justified collateral of TASK-3's dead-code removal, not a behavior change. Kept. |

### Tests
- Command: `uv run pytest -x` (full suite, re-run after the fix)
- Result: **612 passed, 0 failed** (316.94s)
- Log: `.pytest-logs/20260527-*-review-impl-postfix.log`
- Lint: `scripts/quality-gate.sh lint` → PASS (ruff check + format clean, 320 files)

### Behavioral Verification
- `uv run co chat` (EOF/abort smoke, headless): ✓ bootstraps to `✓ Ready (degraded)`, single-owner
  `Application` renders the inline `Co ❯` prompt + toolbar (`· idle` segment confirms
  `render_footer_toolbar` wired through the toolbar `Window`), exits cleanly (exit 0) — **no crash**
  after the `.index.close()` fix. (Degraded = TEI reranker unavailable, environmental, expected.)
- TASK-7/TASK-8 `success_signal` (REPL launches, accepts a prompt, streams a turn, commits to
  scrollback, returns to idle): verified by the TASK-8 integration test driving a genuinely-running
  `app.run_async()` end-to-end with a real warm Ollama turn.
- **Deferred to Gate-2 manual tty smoke** (require a real interactive terminal; explicitly
  "human-eyeballable" per plan §Testing PO-m-2): streaming Markdown reflow on a long answer,
  approval keystroke `y`/`n`/`a` round-trip (rides Rich `Prompt.ask`, undriveable without forbidden
  mocking), `/resume` arrow-picker, theme switch, mid-turn Ctrl+C abort-then-exit, transient-thinking
  erase. Parity checks #1–#7 stand for the human at Gate 2.

### Overall: PASS
All 8 tasks satisfy their `done_when` with file:line evidence; adversarial cold-read confirmed every
high-risk claim (the no-drop-forever `turn_active` property, the dead-code gate, transient-thinking
parity, the sync no-frontend fallback). One blocking issue — a pre-existing clean-exit crash — was
found via behavioral verification and fixed in scope. Full suite green, lint clean. The streaming/
Ctrl+C/picker/theme parity checks (#1–#7) remain for the human at Gate 2 per the plan's design.
