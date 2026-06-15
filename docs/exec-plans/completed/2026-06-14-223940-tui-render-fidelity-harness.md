# TUI render-fidelity harness — drive the real REPL app and assert on rendered terminal bytes

Task type: additive test-infrastructure — a reusable integration harness that drives the real `prompt_toolkit` Application and captures the actual ANSI byte stream, plus a first regression test over `/status`. No production code changes.

## Context

Two REPL render bugs shipped undetected and were just fixed in `co_cli/main.py` (uncommitted):
1. **Garbled ANSI mid-app** — `patch_stdout()` ran at the default `raw=False`, so prompt_toolkit's `Vt100_Output.write()` replaced every ESC byte with `?` for all styled `console.print` output during the app. Fixed → `patch_stdout(raw=True)` (`main.py:726`).
2. **No input echo** — the inline `Application` + `TextArea` never committed accepted input to scrollback, and `accept_handler` didn't echo it, so nothing the user typed appeared. Fixed → an echo `console.print` in `_handle_one_input` (`main.py`, after the blank-input guard).

**Why the test layer missed both (verified, not assumed):**
- Unit/flow tests assert via `console.capture()` (`tests/test_flow_status_command.py`) — captures rich markup in isolation, bypassing `prompt_toolkit` + `patch_stdout` entirely. Cannot see the `write`-vs-`write_raw` sanitization.
- `HeadlessFrontend` (`co_cli/display/headless.py`) implements the `Frontend` protocol but bypasses the real Application.
- The one real-app harness, `tests/integration/test_repl_terminal_owner.py`, **does** drive `build_repl_app` via `create_pipe_input()` + `create_app_session()` + `patch_stdout()` and feeds keystrokes through the pipe — but captures with **`PlainTextOutput`**, documented as "Output that won't include any ANSI escape sequences." It discards the exact bytes that were broken, and never asserts the input echo.

**De-risking already done (PoC, this session):**
- Mechanism confirmed: through a real `patch_stdout(raw=False)`, `sys.stdout.write("\x1b[1mBOLD\x1b[0m")` is captured as `?[1mBOLD?[0m`; under `raw=True` it is captured as `\x1b[1mBOLD\x1b[0m`. So a `Vt100_Output`-captured byte stream distinguishes broken vs fixed.
- Capture choice confirmed: `Vt100_Output(buffer, get_size, term=...)` preserves ESC; `PlainTextOutput` strips it.
- SGR emission confirmed, with a correction (CD-M-1): the module `console` (`display/core.py:54`) is built with no `color_system` arg, so its `_color_system` is resolved **once at import** (non-tty in pytest) and frozen to `None`. Setting `console._force_terminal = True` alone flips `is_terminal` but does **not** recompute `_color_system` → `console.print` still emits **plain text, zero SGR**. The harness must set BOTH `console._force_terminal = True` AND `console._color_system = ColorSystem.TRUECOLOR` (the `rich.color.ColorSystem` enum — the `"truecolor"` string is only accepted at construction), then restore both on teardown (original `_color_system` is `None`; restoring to `None` re-suppresses SGR, so no bleed). Verified by Core Dev: `_force_terminal=True` + `_color_system=ColorSystem.TRUECOLOR` → `'\x1b[36mhello\x1b[0m\n'`.
- No `console.file` swap needed (CD-m-3): the module console has `_file=None`, so `console.file` dynamically resolves to `sys.stdout` at print time — under `patch_stdout` that is the StdoutProxy. The only mutated global state is the two color attributes above.
- The full-app `run_async()` drive works in pytest (proven by `test_repl_terminal_owner.py`); a non-pytest background PoC of it hung on lifecycle teardown — treated as an environment artifact, but the harness must own explicit start/feed/await/exit with bounded polling (mirroring that test).

## Problem & Outcome

**Problem.** Nothing in the automated suite validates what the REPL actually renders to a terminal — escape-sequence fidelity, input echo, layout. Render regressions are invisible until a human runs `co chat` in a real tty.

**Outcome.** A reusable `tests/integration` harness that (a) drives the real `build_repl_app` through pipe-fed keystrokes under production-equivalent `patch_stdout`, (b) captures the real ANSI byte stream via `Vt100_Output`, and (c) exposes it for assertions. Plus a first regression test over `/status` asserting input echo, no ESC-sanitization garble, real styled section headers, and all six sections — a test that fails on the pre-fix behavior and passes on the fix.

**Failure cost.** Without it, any future change that re-sanitizes ANSI, drops the input echo, or otherwise corrupts terminal rendering ships silently — exactly how these two bugs reached a user. The only safety net is manual tty smoke, which is unreliable and unenforced.

## Scope

**In scope.**
- A reusable render-fidelity harness in the test layer: pipe-input drive of `build_repl_app`, `Vt100_Output` byte capture, a fixture that forces the module `console` into a tty-equivalent SGR-emitting state and restores it, and a `drive(keys) -> captured_text` helper with bounded polling.
- One regression test over `/status` (echo + no-garble + styled headers + six sections) built on the harness.

**Out of scope.**
- Production code changes of any kind (the `raw=True` + echo fixes already landed; this plan only adds validation). Per the `no_eval_test_driven_api` rule, no production API is added to make the harness pin the `raw` flag.
- Tests for other surfaces (footer toolbar, completion menu, approval-keystroke round-trip) — the harness is built to enable them, but they are follow-ups. (The approval-keystroke round-trip stays manual-smoke per `test_repl_terminal_owner.py`'s existing note.)
- Pinning the production `raw=True` setting itself (would require production API) — stays covered by manual tty smoke, like the approval round-trip. See Open Question 1.
- Any `evals/` addition — this is deterministic, no-LLM, and belongs as an integration test, not LLM UAT.

## Behavioral Constraints

- **No mocks or fakes.** Drive the real `Application`, real module `console`, real `patch_stdout`, real `dispatch`/`_handle_one_input`. Forcing the console's color state and supplying a fixed terminal size are environment simulation (peer to the existing `console._width`/`project_info` pinning in `tests/test_flow_bootstrap_banner.py`), not mocking a dependency-under-test.
- **No global-state bleed.** The module `console` is shared. Any mutation (`_force_terminal`, color system) MUST be restored in a `finally`/fixture teardown so other tests see the original console.
- **Deterministic, no LLM.** `/status` makes no model call; the harness must not require a warm model and must not call `ensure_ollama_warm`.
- **Production-equivalent stdout patch.** The harness drives under `patch_stdout(raw=True)` to mirror production (`main.py:726`); the test exercises the real `_handle_one_input` echo path, not a reimplementation.
- **Bounded, no unbounded waits.** All readiness/flush waits poll with an explicit deadline (mirror `test_repl_terminal_owner.py`'s `_wait_running`/`_poll_until`), and the app is always torn down in a `finally`.

## High-Level Design

The harness mirrors `test_repl_terminal_owner.py`'s real-app drive, with two deliberate changes that make render fidelity observable:

1. **Capture with `Vt100_Output`, not `PlainTextOutput`** — preserves the ESC byte stream so assertions can distinguish `\x1b[` (correct) from `?[` (sanitized garble). Construct with `get_size=lambda: Size(rows=24, columns=80)` (`prompt_toolkit.data_structures.Size` — never a tuple or `None`; the full-app `Renderer` attribute-accesses `.columns`/`.rows` during layout under `run_async`, so `lambda: None` would `AttributeError`).
2. **Force the module `console` to emit SGR** — a `forced_tty_console()` context manager sets BOTH `console._force_terminal=True` and `console._color_system=ColorSystem.TRUECOLOR` (see Context §De-risking / CD-M-1) so `console.print` markup produces real SGR, then restores both on teardown.

The harness exposes a small helper: build deps (minimal `CoDeps` — config, session path, usage path; empty catalogs are fine for `/status`), wire `_build_accept_handler` + `build_key_bindings` + `build_repl_app`, run under `create_pipe_input()` + `create_app_session(output=Vt100_Output(...))` + `patch_stdout(raw=True)`, then `drive(text)`: send `text + "\r"`, await `runtime.turn_task`, poll the captured buffer until a sentinel (or deadline), return the captured string. Always `app.exit()` + await the run task in `finally`.

The first test calls `drive("/status")` and asserts on the returned bytes.

## Tasks

### ✓ DONE TASK-1 — Reusable render-fidelity harness

- **files:** `tests/integration/_tui_harness.py` (new)
- Provide: (a) a context-managed `forced_tty_console()` that saves, sets, and restores BOTH `co_cli.display.core.console._force_terminal` (→ `True`) and `console._color_system` (→ `ColorSystem.TRUECOLOR`; restore the original, which is `None`) — per CD-M-1, both are required for `console.print` to emit SGR; no `console.file` swap (CD-m-3); (b) a `make_repl_deps(tmp_path)` building a minimal real `CoDeps` (no LLM, no MCP — the `tests/test_flow_status_command.py` deps shape) and wiring the accept_handler with `agent=None` (confirmed safe at G1: `_handle_one_input` `main.py:435-453` routes `/status` through `dispatch_command` → `LocalOnly` → returns at the `should_continue` branch before `_run_foreground_turn`, the only agent consumer; no model/orchestrator build needed, reinforcing the no-LLM constraint); (c) an async `drive_repl(deps, keys, *, sentinel)` that builds and runs the real `build_repl_app` under `create_pipe_input()` + `create_app_session(output=Vt100_Output(buffer, get_size=lambda: Size(rows=24, columns=80), term="xterm-256color"))` + `patch_stdout(raw=True)`, feeds `keys + "\r"`, awaits the armed `turn_task`, polls the buffer until `sentinel` appears or a bounded deadline, tears down (`app.exit()` + await run task) in `finally`, and returns the captured string.
- **done_when:** the TASK-2 test (the harness's sole consumer) drives `/status` through `drive_repl` and its fidelity assertions pass — the captured bytes contain a real `\x1b[` SGR sequence and **no** `?[` sanitized garble — proving the harness captures fidelity that `PlainTextOutput` would have hidden. (No standalone `test_harness_captures_ansi_fidelity` and no second non-`/status` stimulus — folded into TASK-2 per CD-m-2.)
- **success_signal:** N/A (test infrastructure)
- **prerequisites:** none

### ✓ DONE TASK-2 — `/status` echo + render-fidelity regression test

- **files:** `tests/integration/test_repl_render_fidelity.py` (new)
- Using the TASK-1 harness, `drive_repl(deps, "/status", sentinel="Capabilities")` and assert on the captured terminal bytes: (a) the submitted input is echoed to scrollback — assert the dim prompt-marker echo-prefix is present AND the command word `status` appears; do **NOT** assert the literal `/status` (CD-M-2, corrected at G1: the echo source is `console.print(f"[dim]{PROMPT_CHAR}[/dim] {user_input}")` at `main.py:411` with `PROMPT_CHAR = "❯"` — there is **no** `Co ` prefix; `[dim]` renders as SGR 2, so the echo line is `\x1b[2m❯\x1b[0m ` followed by the input, and rich `highlight=True` splits the leading `/` into its own SGR run — `\x1b[2m❯\x1b[0m \x1b[35m/\x1b[0m\x1b[95mstatus\x1b[0m` — so `/status` is never a contiguous substring once SGR is on. Assert the dim marker run (`\x1b[2m❯\x1b[0m`, or the bare `❯` marker) paired with `status` to keep the assertion specific to the echo line, since `status` also appears in section bodies); (b) **no** `?[` ESC-sanitization garble anywhere; (c) at least one real `\x1b[` SGR sequence is present (the styled section headers actually rendered, not stripped); (d) all six section titles present (`Session`, `Model & context`, `Dream`, `Work in flight`, `Capabilities`, `Degraded`).
- **done_when:** `uv run pytest tests/integration/test_repl_render_fidelity.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tui-fidelity.log` passes on current (fixed) code, covering echo, no-garble, real-SGR, and six-section assertions.
- **dev note (not a done_when gate, CD-m-4):** during dev, confirm that reverting `main.py` to `patch_stdout()` / removing the echo makes the relevant assertions fail — evidence the test is a real guard; do not leave the revert in. review-impl must not treat this manual revert as a required reproducible artifact.
- **success_signal:** a render regression (re-sanitized ANSI or dropped input echo) fails CI instead of reaching a user.
- **prerequisites:** TASK-1

## Testing

- Scoped integration run per TASK-2, fail-fast (`-x`), tail the log. No LLM expected — flag any model call or `ensure_ollama_warm` as a defect.
- Confirm no cross-test bleed: run the new tests together with `tests/test_flow_bootstrap_banner.py` and `tests/test_flow_status_command.py` (which also touch the module `console`) in one invocation; all must pass, proving the `forced_tty_console()` teardown restores global state.
- **Delivery summary must restate the residual gap (PO-m-1):** the production `raw=True` flag itself is **not** pinned by this harness and remains covered only by manual tty smoke — call this out explicitly at delivery so it is visible at Gate 2 rather than implied.
- Pre-ship: `scripts/quality-gate.sh full` as the safety net (run by `/ship`, not here).

## Open Questions

_All resolved in Cycle C1 — see decisions table._

1. **Pinning the production `raw=True` setting — RESOLVED (CD-m-5, PO-m-1).** Not pinned by this harness; left to manual tty smoke (consistent with the existing manual approval-keystroke round-trip). Adding a named production stdout-patch helper purely for test import would be the test-driven production API that `no_eval_test_driven_api` rejects. The harness guards what is regression-prone with real teeth: the input echo (real `_handle_one_input` prod path) and the ANSI-fidelity contract. Residual gap restated in the delivery summary per Testing.
2. **Harness home — RESOLVED (CD-m-6).** Helper module `tests/integration/_tui_harness.py` (importable across future TUI test files; `forced_tty_console()` reads cleanest as an explicit call-site context manager, keeping the load-bearing global-console mutation visible rather than hidden in a conftest fixture).


---

## Final — Team Lead

Plan approved. C2 converged: PO returned `Blocking: none` on C1; Core Dev's two C1 blockers (CD-M-1 console SGR-forcing needs both `_force_terminal` + `_color_system`; CD-M-2 echo assertion cannot use literal `/status`) were adopted verbatim and verified resolved at C2 (`Blocking: none`). All minors adopted.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tui-render-fidelity-harness`

## Gate 1 — Review verdict: APPROVED

Claims verified against current (uncommitted) source, not the de-risking notes:
- Right problem: confirmed. `patch_stdout(raw=True)` live at `main.py:737`; echo live at `main.py:411`. The miss is real — `test_flow_status_command.py` uses `console.capture()` (bypasses `patch_stdout`); `test_repl_terminal_owner.py:124` captures with `PlainTextOutput` (strips ESC). Neither can see fidelity or echo.
- Correct scope: confirmed. Reuses the proven real-app drive pattern; refuses a test-driven production API per `no_eval_test_driven_api`; residual `raw=True` gap honestly disclosed. `/status` makes no LLM call (`status.py` gathers from in-memory `deps`); all six sections always render (`status.py:42-57`, unconditional loop).

One blocking correction applied: TASK-2(a) and CD-M-2 cited a fabricated `Co ❯` echo prefix / byte string. Source echo is `[dim]❯[/dim] {user_input}` (`main.py:411`, `PROMPT_CHAR = "❯"`) — no `Co`. Assertion corrected to the dim `\x1b[2m❯\x1b[0m` marker paired with `status`.

Confirmation folded into TASK-1: `agent=None` is safe for `/status` (`_handle_one_input` `main.py:435-453` returns before `_run_foreground_turn`), so the harness needs no model/orchestrator build.

Cleared for `/orchestrate-dev tui-render-fidelity-harness`.

---

## Delivery Summary — 2026-06-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | TASK-2 drives `/status` through `drive_repl`; fidelity assertions pass (real `\x1b[` SGR, no `?[` garble) | ✓ pass |
| TASK-2 | `pytest tests/integration/test_repl_render_fidelity.py` passes on fixed code (echo + no-garble + real-SGR + six sections) | ✓ pass |

**Tests:** scoped — 9 passed, 0 failed (the new test + `test_flow_bootstrap_banner.py` + `test_flow_status_command.py`, run together to prove no global-`console` bleed). No LLM (`spans=0`), 0.43s for the harness test.
**Doc Sync:** clean (skipped — test-infrastructure only; no shared module, public API, or schema touched).

**Guard verified (dev note, CD-m-4 — not left in tree):**
- Echo guard: replacing `main.py:411`'s echo `console.print` with `pass` trips assertion (a) — the dim `\x1b[2m❯` marker disappears. The harness calls the real `_handle_one_input`, so this path is genuinely exercised.
- Garble guard: flipping the **harness's own** `patch_stdout(raw=True)` → `patch_stdout()` makes the captured section/echo SGR sanitize to `?[34mCapabilities?[0m`, tripping both (a) and (b). Confirmed the no-garble assertion catches the exact `raw=False` mechanism the production bug rode on.

**Residual gap (PO-m-1, restated at delivery):** the production `raw=True` flag at `main.py:737` is **not** pinned by this harness — the harness sets its own `patch_stdout(raw=True)` independently (no production stdout-patch API exists to import, and adding one purely for test would be the test-driven API `no_eval_test_driven_api` rejects). So a future revert of `main.py` to `patch_stdout()` would NOT fail this test. That regression stays covered only by manual tty smoke, alongside the existing manual approval-keystroke round-trip. The harness guards what it can with real teeth: the input echo (real prod path) and the ANSI-fidelity contract.

**Overall: DELIVERED**
Both tasks pass; lint clean; scoped + cross-bleed tests green; no LLM. The harness is reusable for future TUI render tests (footer toolbar, completion menu) per the plan's out-of-scope follow-ups.

---

## Implementation Review — 2026-06-14

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | TASK-2 drives /status through drive_repl; real `\x1b[` SGR present, no `?[` garble | ✓ pass | `_tui_harness.py:60-68` forced_tty_console saves+restores both `_force_terminal`/`_color_system` (orig None → re-suppresses, no bleed, vs `display/core.py:55`); `:84-90` make_repl_deps = no model/agent (mirrors `test_flow_status_command.py:36-42`); `:149-181` bounded-poll drive + finally teardown |
| TASK-2 | pytest test_repl_render_fidelity.py passes (echo + no-garble + real-SGR + six sections) | ✓ pass | `test:46-47` dim echo marker + `status` (no literal /status); `:50` no `?[`; `:54` ESC floor; `:57-58` six titles == `status.py:43-48`; sentinel "Capabilities" is a real title |

### Adversarial check (false-green hunt)
- (a) `\x1b[2m❯` is **unique** to the rich echo (`main.py:411` `[dim]`); the TextArea prompt char (`_app.py:150`) is plain, not dim — verified by re-injecting: echo removal turns (a) red. **Real guard.**
- (b) `?[` absence is not vacuous: flipping the harness to raw=False produced 20 `?[` occurrences (`?[2m❯?[0m`). **Real guard.**
- (c) `\x1b[` floor is trivially satisfied by prompt_toolkit's own renderer control sequences — downgraded to a weak corroborator; comment corrected to say so (the SGR weight is on (a)+(b)).
- Production `raw=True` (`main.py:737`) is **not** pinned: reverting it leaves the test green (harness sets its own raw=True). Confirmed real and accurately disclosed in the delivery summary — residual gap stays on manual tty smoke.

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Function-local `ShellBackend` import diverges from reference test's top-level import | _tui_harness.py | minor | Moved to module top |
| Assertion (c) comment overclaimed its evidentiary role | test_repl_render_fidelity.py:52-54 | minor | Reworded to "weak corroborator"; SGR weight noted on (a)+(b) |
| RUF003 ambiguous `❯` introduced in the reworded comment | test_repl_render_fidelity.py:55 | blocking (lint) | Reworded comment to drop the literal char |

### Tests
- Command: `uv run pytest -p no:randomly`
- Result: 728 passed, 0 failed
- Log: `.pytest-logs/20260509-173946-review-impl.log`

### Behavioral Verification
- No user-facing production change — test-infrastructure only (main.py net-zero from this delivery; the raw=True+echo fixes pre-existed). success_signal ("render regression fails CI") proven via negative controls during dev. Skipped with justification.

### Overall: PASS
Both tasks meet done_when with file:line evidence; adversarial pass found no blocking false-green (echo + ANSI-fidelity guards verified by regression re-injection); full suite green; lint clean. The unpinned production raw=True flag is a known, documented residual gap covered by manual tty smoke.
