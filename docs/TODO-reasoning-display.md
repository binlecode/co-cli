# TODO — Reasoning Display Best Practice

## Goal

Adopt the converged CLI pattern for reasoning/progress display in Co:
- persistent config sets the default behavior
- CLI flags override config for the current session
- reasoning display uses modes, not a boolean only

## Recommended Product Decision

Co should adopt:

```text
reasoning_display = "off" | "summary" | "full"
```

Behavior:
- `off`: no reasoning/thinking content is shown; only status/progress and final output
- `summary`: stream compact reasoning progress suitable for default interactive use
- `full`: stream full model thinking/reasoning content, equivalent to current `--verbose` intent

Compatibility:
- keep `co chat --verbose` as an alias for `co chat --reasoning-display full`
- add a persistent config path so users do not need to pass `--verbose` every session

## Why This Is The Best Practice

This matches the common pattern in modern agentic CLIs:
- users expect a saved default for display verbosity
- one-off CLI flags should override the saved default
- reasoning display usually needs more than on/off because default UX and debugging UX are different

Canonical rationale:
- `docs/RESEARCH-progressive-output-best-practice.md`
- `docs/reference/RESEARCH-peer-systems.md`

Why Co should not stop at `verbose = true|false`:
- boolean config is acceptable as a short-term bridge but is too coarse
- the product needs a safe default interactive mode and a debugging mode
- `/doctor` and future long-running tools also need progressive display that does not imply full raw reasoning

Important distinction:
- the config + override + multi-mode model is converged best practice
- the exact behavior of `summary` mode is not converged across reference systems
- `summary` below is Co's proposed v1 product policy, not an ecosystem-standard reasoning format

## Proposed Co Design

### 1. Config

Add to `Settings`:

```text
reasoning_display: Literal["off", "summary", "full"] = "summary"
```

Config precedence remains:
- env vars
- project `.co-cli/settings.json`
- user `~/.config/co-cli/settings.json`
- built-in default

Suggested env var:

```text
CO_CLI_REASONING_DISPLAY
```

### 2. CLI

Add:

```text
co chat --reasoning-display off|summary|full
```

Compatibility:
- `--verbose` maps to `full`
- explicit `--reasoning-display ...` should take precedence over `--verbose`

### 3. Rendering Contract

Keep these concerns separate:
- persistent status messages
- progress/probe updates
- streamed reasoning/thinking
- tool-call annotations
- tool-result panels

Reasoning display policy:
- `off`: suppress model thinking stream
- `summary`: stream concise reasoning/progress to the main CLI view
- `full`: stream raw thinking content in the existing detailed style

### 3.1 `summary` Mode — Implementation Contract Aligned To Peer-System Convergence

This section defines **Co's v1 summary policy** using the patterns that do converge in:
- `docs/RESEARCH-progressive-output-best-practice.md`
- `docs/reference/RESEARCH-peer-systems.md`
- prefer product-semantic progress over raw debug output
- preserve Co's trusted local operator posture
- keep state inspectable and bounded
- avoid framework-heavy or model-heavy transformation layers

`summary` is not raw chain-of-thought. It is a lightweight progress surface that exposes what the system is doing in operator terms.

Input sources, in priority order:
1. tool-owned progress signals
2. model thinking stream (`ThinkingPart`, `ThinkingPartDelta`)
3. generic waiting fallback (`Co is thinking...`) only before real progress exists

Output shape:
- exactly one live progress line at a time
- wording should describe current action or next immediate step, not inner monologue
- examples:
  - `Checking provider and model availability...`
  - `Reviewing context before choosing a tool...`
  - `Comparing likely failure points...`

Transformation rules:
- consume the model thinking stream incrementally
- reduce it into short operator-style progress text rather than exposing raw reasoning
- prefer the latest actionable/stateful fragment over preserving every intermediate thought
- keep the rendered text short enough to work as a single live terminal line

Boundaries:
- no second model call
- no LLM-based summarization pass
- no attempt to preserve full reasoning fidelity
- `full` remains the only mode that shows raw thinking content

Rendering rules:
- one live progress region only
- replace the current live progress line in place
- preserve the last meaningful progress line in scrollback when the system transitions to tool output or final answer
- do not display raw tool-call annotations when the tool already owns the progress region

Tool interaction:
- if a tool exposes progress, tool progress takes priority over model reasoning progress
- if a tool does not expose progress, the system may fall back to normal tool-call annotation
- tool-owned progress should be phrased in product/task terms, not implementation terms where avoidable

Fallback behavior:
- show `Co is thinking...` only until the first real reasoning or tool progress update arrives
- once real progress exists, replace the generic waiting line
- never show generic waiting and live progress as parallel active states

Completion behavior:
- when final assistant text starts, stop the live progress region
- leave the last meaningful progress line in scrollback so the user can reconstruct what happened
- the final answer remains the primary artifact; progress is supporting context

Why this aligns with the peer-systems research:
- it favors inspectable, user-facing operator progress over raw verbose traces
- it keeps the implementation simple and local-first
- it avoids adding an extra summarization model or hidden transformation layer
- it treats progress as a product-semantic UX surface, which matches the research recommendation to prefer product-semantic improvements over infrastructure expansion

### 4. Progress UX

Do not overload `on_status` for all progressive rendering.

Co should introduce a dedicated progress path for long-running work:
- model reasoning progress
- tool-owned progress such as `/doctor`
- background checks and similar multi-phase flows

The generic `Co is thinking...` line should be replaced or superseded as soon as real streamed progress is available.

Frontend ownership rules:
- one live progress region only
- priority order:
  1. tool-owned progress
  2. reasoning summary progress
  3. generic `Co is thinking...`
- lower-priority progress must yield immediately when a higher-priority progress source becomes active

### 5. `/doctor`

`/doctor` should follow the general progress model, not a doctor-only rendering rule.

Target behavior:
- initial generic waiting state only until first real progress arrives
- once the model/tool starts producing meaningful progress, switch to that live progress view
- suppress redundant display layers when progress is already active
  - avoid showing generic `Co is thinking...`
  - avoid noisy `check_capabilities()` annotation when tool-owned progress is visible

## Implementation Tasks

- Add `reasoning_display` to `Settings` and config loading
- Add CLI override `--reasoning-display`
- Preserve `--verbose` as alias to `full`
- Thread resolved reasoning display mode into chat loop / orchestration
- Stop dropping `ThinkingPart`/`ThinkingPartDelta` in default interactive mode when `summary` is active
- Add a reasoning-to-progress reducer that converts `ThinkingPart` deltas into short operator-style progress lines
- Introduce a dedicated progress rendering path instead of relying on string-based status heuristics
- Make `/doctor` use the general progress path
- Suppress redundant tool-call annotations when a tool is already providing progressive updates

## Acceptance Criteria

- Users can set a persistent default in config without passing `--verbose`
- CLI flag can override config for one session
- Default interactive mode shows useful progressive reasoning/progress without dumping raw full thinking
- Full debugging mode still exists
- `/doctor` no longer sits on `Co is thinking...` for the whole delay if reasoning/progress exists
- `/doctor` no longer shows stacked redundant layers of status + tool call + progress

## Default Chosen

Recommended default for Co:

```text
reasoning_display = "summary"
```

Rationale:
- `off` hides too much for an agentic CLI
- `full` is too noisy for normal interactive use
- `summary` is the best default tradeoff for day-to-day use
