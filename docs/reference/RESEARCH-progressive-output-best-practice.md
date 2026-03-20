# RESEARCH — Progressive Output And Display Best Practice

## Goal

Identify what actually converges across reference CLI assistant systems for progressive output and display, then translate that into a practical design direction for Co.

This note is grounded in:
- local peer synthesis in `docs/reference/RESEARCH-peer-systems.md`
- current Co implementation
- current public signals from Codex, Claude Code, and Gemini CLI

## What Actually Converges

### 1. Persistent default + per-session override

Converged expectation:
- users want a saved default for display verbosity / reasoning visibility
- a CLI/session flag should override that default for one run

Implication for Co:
- `--verbose` alone is not sufficient
- Co should add a persistent config setting for reasoning/progress display

### 2. Multiple display modes, not boolean-only

Converged expectation:
- on/off is too coarse
- normal interactive use and debugging use are different

Implication for Co:
- Co should prefer `off | summary | full` over a boolean `verbose`

### 3. Real-time progress is preferred over silent waiting

Converged expectation:
- users prefer live progress to a long period of silence followed by a final dump
- progress should be surfaced in product/task terms rather than pure implementation traces

Implication for Co:
- `Co is thinking...` is acceptable only as a temporary fallback
- once actual reasoning or tool progress exists, Co should switch to that live progress view

### 4. Product-semantic progress is better than raw debug firehoses by default

From the peer-systems synthesis in `docs/reference/RESEARCH-peer-systems.md`, the strongest shared pattern is not "show raw internals." It is:
- keep state inspectable
- prefer product-semantic UX improvements
- preserve bounded complexity
- avoid infrastructure-heavy or model-heavy transformation layers unless clearly justified

Implication for Co:
- default progress should tell the user what Co is doing
- full raw reasoning should remain available as an explicit debugging mode

### 5. Keep the implementation bounded and inspectable

Converged expectation:
- avoid hidden second-pass summarization layers for UI only
- avoid heavyweight machinery when a simple local-first transformation is enough

Implication for Co:
- do not add a second model call just to summarize reasoning for display
- do not treat progressive output as a giant universal event bus problem if a dedicated progress channel is enough

## What Does Not Converge

The ecosystem does **not** appear to converge on one exact algorithm for converting raw thinking tokens into a concise public summary stream.

There is no strong evidence of a single best-practice policy for:
- sentence splitting rules
- exact throttle interval
- exact visible length cap
- exact filler stripping rules
- exact “steps only” reducer logic

Implication for Co:
- the exact `summary` reducer is a Co product policy choice
- Co should not present its reducer details as if they are industry-standard behavior

## Comparison To Co Today

Current Co behavior:
- reasoning/thinking is hidden unless chat starts with `--verbose`
- there is no persistent config default for this behavior
- `Co is thinking...` can sit on screen for the whole pre-tool model delay
- tool-owned progress, generic waiting, and tool-call annotations can overlap awkwardly
- `check_capabilities` returns a single final panel, not incremental tool-result streaming

Main gaps relative to converged practice:
- no config-backed default
- no mode-based reasoning/progress display
- weak handoff from generic waiting to real progress
- no dedicated progress channel with clear ownership rules

## Best-Practice Direction For Co

### Recommended control-plane design

Co should adopt:

```text
reasoning_display = "off" | "summary" | "full"
```

With this precedence:
- env var
- project config
- user config
- built-in default
- CLI flag override for current session

Compatibility:
- keep `--verbose` as an alias for `full`

### Recommended runtime/display design

Co should separate:
- durable status messages
- live progress
- raw reasoning/thinking
- tool-call annotations
- tool-result panels

Recommended priority model:
1. tool-owned progress
2. reasoning summary progress
3. generic waiting fallback

Rules:
- `Co is thinking...` is only a fallback before real progress exists
- once real progress exists, replace the generic waiting line
- if a tool owns progress, suppress redundant tool-call annotation for that tool
- preserve the last meaningful progress line in scrollback when transitioning to final output

### Recommended `summary` policy

`summary` should be treated as **Co's v1 product policy**, not a claimed ecosystem standard.

Recommended design:
- derive it from the existing model thinking stream
- render short operator-style progress lines
- do not expose raw chain-of-thought
- do not use a second model pass
- keep the implementation local, inspectable, and bounded

Good examples:
- `Reviewing context before choosing a tool...`
- `Checking provider and model availability...`
- `Comparing likely failure points...`

Bad examples:
- raw internal chain-of-thought
- repeated self-referential filler
- implementation-noisy output that duplicates tool internals

## Recommendation For TODO / Implementation Work

`docs/TODO-reasoning-display.md` should:
- keep the config/override/mode model as converged best practice
- explicitly label the exact `summary` behavior as Co's v1 policy
- avoid presenting reducer specifics as an industry-standard algorithm
- center the design on progress ownership and handoff rather than on string/status heuristics

## Bottom Line

The converged best practice is:
- saved default
- CLI override
- multiple display modes
- live product-semantic progress
- bounded, inspectable implementation

The non-converged part is the exact `summary` reducer.

So Co should adopt the converged architecture, but treat the `summary` implementation details as an internal product decision that will likely need iteration.
