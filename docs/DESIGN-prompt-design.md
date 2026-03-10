# Design: Prompt Architecture

## 1. What & How

This doc is the canonical spec for co-cli's **prompt composition** — how the system prompt is assembled at agent creation, how per-turn instruction layers are built before each model request, and the design principles that govern what goes into the prompt vs what is kept tool-loaded.

Runtime execution (loop topology, approval, context governance) has moved to dedicated docs. See [DESIGN-core-loop.md](DESIGN-core-loop.md), [DESIGN-flow-approval.md](DESIGN-flow-approval.md), and [DESIGN-flow-context-governance.md](DESIGN-flow-context-governance.md).

`DESIGN-core.md` remains the system skeleton and nav map.

```mermaid
graph TD
    U[User input] --> C[chat_loop main.py]
    C --> O[run_turn _orchestrate.py]
    O --> S[_stream_events]
    S --> A[agent.run_stream_events]

    A --> HP[history_processors]
    HP --> H1[inject_opening_context]
    HP --> H2[truncate_tool_returns]
    HP --> H3[detect_safety_issues]
    HP --> H4[truncate_history_window]

    A --> T[tool calls]
    T -->|requires_approval| D[DeferredToolRequests]
    D --> AP[_collect_deferred_tool_approvals]
    AP --> A

    A --> R[final result]
    R --> O
    O --> C

    P1[assemble_prompt static] --> A
    P2[@agent.instructions per-turn] --> A
```

## 2. Core Logic

### Loop Topology, Approval, and Safety Policies

These are runtime execution concerns — they now live in dedicated flow docs:

> - **Turn execution:** [DESIGN-core-loop.md](DESIGN-core-loop.md) — `run_turn` state machine, streaming, approval re-entry loop, doom loop / grace turn / shell reflection, error handling, interrupt recovery, turn outcome contract
> - **Approval:** [DESIGN-flow-approval.md](DESIGN-flow-approval.md) — three-tier decision chain, shell inline policy, `"a"` persistence semantics, MCP approval inheritance

### Prompt Architecture

Prompt composition uses two mechanisms:

1. **Static assembly** (`assemble_prompt`), once per agent creation:
- soul seed + character base memories + all 6 mindset files (`souls/{role}/seed.md` + planted memories + `mindsets/{role}/*.md`, placed first)
- ordered rules from `prompts/rules/*.md`
- soul examples from `souls/{role}/examples.md` (when file exists, trailing rules)
- optional model counter-steering from `prompts/quirks/{provider}/{model}.md`

2. **Per-turn conditional layers** (`@agent.instructions`):
- current date
- shell guidance
- project instructions from `.co-cli/instructions.md` when present
- personality-context memories when role exists
- review lens (`## Review lens`) from `souls/{role}/critique.md` when role exists
- available skills (`## Available Skills`) listing `/name — description` entries from `skill_registry` when non-empty; capped at 2 KB

All 6 mindset files for the active role are now in the static soul block (assembled once at agent creation via `load_soul_mindsets()`). They are no longer a per-turn layer.

Design principles:
- System prompt defines identity and behavior policy.
- Tool docstrings define tool selection/chaining guidance.
- Knowledge remains tool-loaded; no bulk memory/articles embedded in base prompt.

For the deep spec on prompt composition (assembly order, per-turn layers, budget), see `DESIGN-personality.md`.

### Tool Preamble Injection

When the model emits no text delta before its first tool call, `_stream_events()` auto-injects a dim status message via `frontend.on_status()` — fires at most once per `_stream_events()` call. Maps tool names to context-appropriate messages (`recall_memory` → "Checking saved context before answering.", `web_search` → "Looking up current sources.", etc.).

> **Streaming detail:** [DESIGN-core-loop.md](DESIGN-core-loop.md) §4.4 — how `_stream_events` dispatches events and when the preamble fires.

### Context Governance

History processors registered on the agent prevent context overflow. Context governance (processor chain, tool output trimming, sliding-window summarization, precomputed compaction, message history lifecycle) is now specified in its own flow doc.

> **Full flow spec:** [DESIGN-flow-context-governance.md](DESIGN-flow-context-governance.md) — processor chain (inject_opening_context → truncate_tool_returns → detect_safety_issues → truncate_history_window), summarization model rationale, background pre-computation, slash command effects on history, DeferredToolRequests history interaction.


### Future Extensions (Deferred)

1. **Sub-agent delegation** — Fully shipped. Phase A: `delegate_coder` (code analysis) and `delegate_research` (web research + synthesis) with structured outputs (`CoderResult`, `ResearchResult`). Phase B: `delegate_analysis` (knowledge-base + Drive synthesis, `AnalysisResult`), `turn_usage` budget accumulation on `CoDeps` shared across all three delegation tools. See [DESIGN-tools-delegation.md](DESIGN-tools-delegation.md) for implementation details.

2. **Confidence-scored advisory outputs**
- Add confidence metadata for advisory tools (e.g., search/recall) when ranking quality justifies it.
- Blocked on FTS5 ranked retrieval baseline (meaningful signal precondition).

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `max_request_limit` | `CO_CLI_MAX_REQUEST_LIMIT` | `50` | Max model requests per user turn (`UsageLimits.request_limit`) |
| `model_http_retries` | `CO_CLI_MODEL_HTTP_RETRIES` | `2` | Provider/network retry budget per turn |
| `doom_loop_threshold` | `CO_CLI_DOOM_LOOP_THRESHOLD` | `3` | Consecutive identical tool calls before intervention |
| `max_reflections` | `CO_CLI_MAX_REFLECTIONS` | `3` | Consecutive shell error threshold before intervention |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Agent-level tool retry limit |
| `tool_output_trim_chars` | `CO_CLI_TOOL_OUTPUT_TRIM_CHARS` | `2000` | Max chars per ToolReturnPart in older messages. `0` disables |
| `max_history_messages` | `CO_CLI_MAX_HISTORY_MESSAGES` | `40` | Message-count trigger for sliding-window compaction. `0` disables |
| `role_models["summarization"]` | `CO_MODEL_ROLE_SUMMARIZATION` | provider default (instruct model for ollama; primary model for gemini) | Summarization model chain; head used for auto-compaction and `/compact`. Falls back to primary if unset |
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Personality preset for per-turn personality layer |
| `session_ttl_minutes` | `CO_SESSION_TTL_MINUTES` | `60` | Session persistence TTL (minutes) |
| `role_models` | `CO_MODEL_ROLE_REASONING`, `CO_MODEL_ROLE_CODING`, `CO_MODEL_ROLE_RESEARCH`, `CO_MODEL_ROLE_ANALYSIS` | provider default for `reasoning` | Role model chains (comma-separated per env var); `reasoning` is main-agent role |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/main.py` | Session loop, slash dispatch, run_turn integration, background compaction trigger |
| `co_cli/_orchestrate.py` | `run_turn`, `_stream_events`, `_collect_deferred_tool_approvals`, tool preamble injection, interrupt patching |
| `co_cli/_exec_approvals.py` | Persistent exec approvals: `derive_pattern()`, `find_approved()`, `add_approval()`, `update_last_used()`, `prune_stale()` |
| `co_cli/agent.py` | Agent factory, static prompt assembly call, per-turn `@agent.instructions` layers |
| `co_cli/prompts/__init__.py` | Static prompt assembly (`instructions` + ordered rules + quirks) |
| `co_cli/prompts/model_quirks.py` | Model-specific counter-steering and inference metadata |
| `co_cli/_history.py` | History processors: opening-context recall, safety checks, compaction |
| `co_cli/_commands.py` | Slash command handlers: `/compact`, `/clear`, `/history` |
| `co_cli/config.py` | Loop/prompt-relevant settings |
| `co_cli/deps.py` | Runtime fields consumed by loop processors and prompt layers |
| `co_cli/_session.py` | Session persistence: `new_session()`, `load_session()`, `save_session()`, `is_fresh()`, `touch_session()`, `increment_compaction()` — TTL-based session restore wired in `run_bootstrap()` |
| `co_cli/tools/capabilities.py` | `check_capabilities` — capability introspection tool (registered in `agent.py`) |
| `docs/DESIGN-core.md` | System-level skeleton and integration map |
| `docs/DESIGN-personality.md` | Personality composition and per-turn personality injection details |
| `docs/DESIGN-tools.md` | Tools index: Common Conventions, approval table, docstring standard, cross-tool routing, links to [execution](DESIGN-tools-execution.md), [integrations](DESIGN-tools-integrations.md), [delegation](DESIGN-tools-delegation.md) child docs |
| `tests/test_history.py` | Functional tests for processors, summarisation, and `/compact` |
