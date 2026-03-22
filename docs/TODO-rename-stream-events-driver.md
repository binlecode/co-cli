# TODO rename-stream-events-driver

## Scope

Rename `_stream_events()` to `_run_and_render_stream_events()` and update all surrounding code, tests, comments, and DESIGN docs so the name matches the function's actual responsibility.

This is a refactor for clarity, not a behavior change.

The target outcome is that readers can infer, from the name alone, that the function:

- runs the SDK stream
- renders/forwards stream updates to the frontend
- captures the final run result
- returns control to `run_turn()`

## Current-State Validation

Validated against current code on 2026-03-21:

- The function lives in `co_cli/context/_orchestrate.py` as `_stream_events(...)`.
- It wraps `agent.run_stream_events(...)`.
- It translates SDK events into frontend callbacks such as:
  - `on_text_delta`
  - `on_thinking_delta`
  - `on_tool_start`
  - `on_tool_progress`
  - `on_tool_complete`
- It captures the terminal `AgentRunResultEvent`.
- It flushes remaining buffered text/thinking and runs `frontend.cleanup()` on exit.
- It returns `(result, streamed_text)` to `run_turn()`.
- It is called both for the initial model pass and for approval resumption with `deferred_tool_results`.

Conclusion:

- `_stream_events()` is a misleadingly small name.
- The function does more than “stream events”.
- It is the internal driver that runs and renders a streamed agent turn segment.

## Target Design

Use this exact replacement name:

- `_run_and_render_stream_events()`

Keep the leading underscore.

Why the underscore stays:

- the function is an internal orchestration helper
- it is tightly coupled to `run_turn()`, `FrontendProtocol`, and the turn-result contract
- removing the underscore would incorrectly suggest a broader or more stable API surface

Why this exact name:

- `run` makes it explicit that the function actively drives `agent.run_stream_events(...)`
- `render` makes it explicit that it produces frontend-visible side effects
- `stream_events` preserves the concrete SDK mechanism being consumed
- the name is longer, but it is precise and hard to misread

## Non-Goals

- no change to approval behavior
- no change to retry behavior
- no change to buffering, throttling, or callback timing
- no change to `run_turn()` semantics
- no change to public CLI behavior

## Implementation Tasks

### 1. Rename the internal helper in orchestration code

Status: pending

Files:
- `co_cli/context/_orchestrate.py`

Changes:

- Rename `_stream_events(...)` to `_run_and_render_stream_events(...)`.
- Update the local section header comment above the function.
- Update the function docstring so the first sentence matches the new name and role.
- Update all internal call sites in the same module, including:
  - initial run path in `run_turn()`
  - deferred approval resumption path
  - grace-summary path if it uses the same helper
- Update nearby comments that currently say “resume via `_stream_events()`”.

Done when:

- `rg -n "_stream_events\\(" co_cli/context/_orchestrate.py` returns no stale function-call references.
- The implementation remains behaviorally identical.

### 2. Update imports and test references

Status: pending

Files:
- `tests/test_orchestrate.py`
- `tests/test_capabilities.py`
- any other test importing or naming `_stream_events`

Changes:

- Update imports from `_stream_events` to `_run_and_render_stream_events`.
- Update test names that currently embed `stream_events` if the new name improves clarity.
- Update test docstrings and comments that explain current behavior through the old name.
- Preserve test behavior; do not rewrite assertions unless strictly needed by the rename.

Done when:

- No test imports `_stream_events`.
- No test description uses the old name where it refers to the renamed helper.

### 3. Update internal comments and adjacent type docs

Status: pending

Files:
- `co_cli/tools/_result.py`
- any code comment or docstring referencing `_stream_events`

Changes:

- Rewrite comments that currently say “before `_stream_events()` sees them” or similar.
- Keep wording aligned with the clarified role:
  - internal driver
  - runs the stream
  - renders frontend events
  - dispatches tool-complete payloads

Done when:

- No internal code comment uses the old function name.
- Comments still describe behavior accurately after the rename.

### 4. Update DESIGN docs consistently

Status: pending

Files:
- `docs/DESIGN-core-loop.md`
- `docs/DESIGN-tools.md`
- `docs/DESIGN-system.md`
- `docs/DESIGN-index.md`

Changes:

- Replace `_stream_events()` with `_run_and_render_stream_events()` everywhere.
- Rename section headers and diagram labels where needed.
- Preserve the new explanation in `DESIGN-core-loop.md` that this helper is the adapter between SDK events and the CLI frontend.
- Make sure docs still explain that approval resolution happens outside this helper.

Specific doc updates:

- `DESIGN-core-loop.md`
  - rename “Streaming Phase In `_stream_events()`”
  - update every prose reference and pseudocode call site
- `DESIGN-tools.md`
  - update tool-flow diagrams and any line-by-line walkthroughs
- `DESIGN-system.md`
  - update file-purpose table entries
- `DESIGN-index.md`
  - update module index summary text

Done when:

- No canonical DESIGN doc references `_stream_events()`.
- The new name reads naturally in diagrams, prose, and pseudocode.

### 5. Decide whether test names should follow the new helper name mechanically

Status: pending

Files:
- `tests/test_orchestrate.py`
- `tests/test_capabilities.py`

Decision rule:

- If a test is specifically about the helper, rename the test to use `_run_and_render_stream_events`.
- If the longer name makes the test noisy without adding meaning, keep the existing `test_stream_events_*` naming pattern but update the body/docstring references.

Preferred direction:

- keep test function names concise where possible
- update imports, docstrings, and comments to the exact new helper name

Done when:

- naming is consistent enough that future readers do not think `_stream_events` still exists
- tests remain readable

## Acceptance Checks

- Repository search finds no production or canonical doc references to `_stream_events()`:
  - `co_cli/`
  - `docs/DESIGN-*.md`
- All orchestration call sites use `_run_and_render_stream_events()`.
- Test suite references the new helper name where imports or direct prose references require it.
- The function signature and return contract stay unchanged except for the identifier rename.
- Behavior remains identical for:
  - normal streamed text
  - verbose thinking output
  - tool start/progress/complete UI
  - deferred approval resumption
  - grace-summary fallback path

## Suggested Verification

- Run targeted orchestration tests that currently cover the helper behavior.
- Run targeted capability tests that validate `tool_progress_callback` dispatch.
- Re-read `docs/DESIGN-core-loop.md` Section 4.4 after the rename to confirm the name and explanation now match.

## Risks

- A pure rename can still leave stale references in doc prose, comments, or test descriptions.
- The longer name may make some test names or diagrams too noisy if applied mechanically everywhere.
- Search-and-replace must be careful around prose about the SDK method `run_stream_events(...)`, which should not be renamed.

## Open Questions

1. Keep existing short test names like `test_stream_events_*`, or rename them to match the helper exactly?
2. Should any diagram labels use a shorter display label such as `run+render stream` while prose and code use the exact helper name?

