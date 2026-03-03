# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
uv sync                          # Install all dependencies (runtime + dev)
uv run co chat                   # Interactive REPL
uv run co status                 # System health check
uv run co logs                   # Datasette trace viewer (table)
uv run co traces                 # Nested HTML trace viewer

uv run pytest                    # Run all functional tests
uv run pytest -v                 # Verbose output
uv run pytest tests/test_tools.py            # Single test file
uv run pytest tests/test_tools.py::test_name # Single test function
uv run pytest --cov=co_cli                   # With coverage

# Demo Scripts
uv run python scripts/test_memory_lifecycle_movie_query.py  # Memory lifecycle demo

# Evaluation Suite (evals/)
uv run python evals/eval_tool_chains.py               # Multi-step chain completion
uv run python evals/eval_conversation_history.py      # Multi-turn context retention
uv run python evals/eval_safety_abort_marker.py       # Ctrl-C abort marker injection
uv run python evals/eval_safety_grace_turn.py         # Budget exhaustion grace turn
uv run python evals/eval_memory_proactive_recall.py   # Proactive memory injection (W1)
uv run python evals/eval_memory_signal_detection.py   # Signal detection + contradiction (W2/W6)
uv run python evals/eval_signal_analyzer.py           # Mini-agent classification: high/low/none confidence
uv run python evals/eval_signal_detector_approval.py  # Approval path: high auto-save, low approve/deny, no-signal
uv run python evals/eval_personality_behavior.py      # Personality behavior: 1-turn + multi-turn consistency (heuristic scored)

# Tool-calling quality gate (functional pytest)
uv run pytest tests/test_tool_calling_functional.py
```

## Architecture

```
User ──▶ Typer CLI (main.py) ──▶ Agent (pydantic-ai) ──▶ Tools (RunContext[CoDeps])
              │                        │
              │                   instrument_all()
              ▼                        │
         prompt-toolkit           SQLiteSpanExporter ──▶ co-cli.db
         + rich console
```

See `docs/DESIGN-core.md` for module descriptions, processing flows, and approval pattern.

## Knowledge System

All knowledge is dynamic — loaded on-demand via tools, never baked into the system prompt.

**Current state:** All knowledge in flat `.co-cli/knowledge/*.md` (YAML frontmatter, markdown body).
Memories (`kind: memory`) and articles (`kind: article`) coexist, distinguished by `kind` frontmatter.
FTS5 (BM25) search via `KnowledgeIndex` in `search.db`.

Agent-facing tools: `save_memory`, `update_memory`, `append_memory`, `save_article`, `search_knowledge` (cross-source, primary search), `list_memories`, `read_article_detail`, `list_notes`, `read_note`.
Internal adapters (not agent-registered): `recall_memory`, `recall_article`, `search_notes`.

## Coding Standards

- **Python 3.12+** with type hints everywhere
- **Imports**: Always explicit — never `from X import *`
- **Comments**: No trailing comments — put comments on the line above, not at end of code lines
- **`__init__.py`**: Prefer empty (docstring-only) — no re-exports unless the module is a public API facade
- **`_prefix.py` helpers**: Internal/shared helpers in a package use leading underscore. Private to the package — not registered as tools, not part of the public API
- **Tool pattern**: New tools must use `agent.tool()` with `RunContext[CoDeps]`, access runtime resources via `ctx.deps`
- **Tool approval**: Side-effectful tools use `requires_approval=True`. Approval UX lives in the chat loop, not inside tools
- **Tool return type**: Tools returning data for the user MUST return `dict[str, Any]` with a `display` field (pre-formatted string with URLs baked in) and metadata fields (e.g. `count`, `next_page_token`). Never return raw `list[dict]`
- **No global state in tools**: Settings are injected through `CoDeps`, not imported directly in tool files
- **CoDeps is flat scalars only**: `CoDeps` holds flat fields (`ctx.deps.memory_max_count`, `ctx.deps.brave_search_api_key`), never config objects. `main.py` reads `Settings` once and injects scalar values into `CoDeps`. Tools never import or reference `Settings` — simple is good
- **Pydantic-ai idiomatic**: Agent, deps, tools, and agentic flows must follow pydantic-ai's patterns — flat deps dataclass with direct field access (`ctx.deps.api_key`), `RunContext[CoDeps]` for tools, `DeferredToolRequests` for approval, history processors for memory. Don't wrap, abstract over, or deviate from the SDK's conventions
- **Config precedence**: env vars > `.co-cli/settings.json` (project) > `~/.config/co-cli/settings.json` (user) > built-in defaults
- **XDG paths**: Config in `~/.config/co-cli/`, data in `~/.local/share/co-cli/`
- **Versioning**: `MAJOR.MINOR.PATCH` — patch digit: odd = bugfix, even = feature. Bump in `pyproject.toml` only — version is read via `tomllib` from `pyproject.toml` at runtime
- **Status checks**: All environment/health probes live in `co_cli/status.py` (`get_status() → StatusInfo` dataclass). Callers (banner, `co status` command) handle display only
- **Display**: Use `co_cli.display.console` for all terminal output. Use semantic style names — never hardcode color names at callsites

## Testing Policy

- **Only pytest files in tests/** — All files in `tests/` must be pytest test files (`test_*.py` or `*_test.py`). Non-test scripts (demos, evaluations, utilities) go in `scripts/`.
- **Functional tests only** — no mocks or stubs. Tests hit real services. No unit tests: never test string constants, internal helpers in isolation, or assert on implementation details. Every test must exercise a real code path that a user or the agent would trigger.
- **Critical functionality focus** — each test must validate behavior that matters: a tool returning correct results, a pipeline producing expected output, a safety invariant holding. Do not write tests for trivial paths (empty input → empty output), negative edge cases with no real-world trigger, or assertions that merely restate what the code does. Ask: "if this test were deleted, would a real regression go undetected?" If no, don't write it.
- **No skips** — tests must pass or fail, never skip. **Exception:** API-dependent tests requiring paid external credentials (Brave Search) use `pytest.mark.skipif` when the key is absent — without a valid key these tests hang on network timeouts rather than failing with a useful error.
- **Google tests resolve credentials automatically**: explicit `google_credentials_path` in settings, `~/.config/co-cli/google_token.json`, or ADC at `~/.config/gcloud/application_default_credentials.json`
- Framework: `pytest` + `pytest-asyncio`
- Set `LLM_PROVIDER=gemini` or `LLM_PROVIDER=ollama` env var for LLM E2E tests

## Design Principles

- **Best practice + MVP**: When researching peer systems, focus on best practices (what 2+ top systems converge on), not volume or scale. Design for MVP first — ship the smallest thing that solves the user problem. Use protocols/abstractions so post-MVP enhancements require zero caller changes.

## Anti-Patterns

- Do not use `tool_plain()` for new tools — use `agent.tool()` with `RunContext`
- Do not import `settings` directly in tool files — use `ctx.deps`
- Do not pass `Settings` objects into `CoDeps` — flatten to scalar fields. One access pattern, no divergence traps
- Do not put approval prompts inside tools — use `requires_approval=True` and handle in the chat loop
- Do not use mocks in tests — and do not write unit tests (asserting on constants, testing helpers in isolation, checking string concatenation). Every test must exercise a real functional path
- Do not use `.env` files — use `settings.json` or env vars

## Docs

### Doc conventions

DESIGN docs always stay in sync with the latest code — no version stamps needed.

Every component DESIGN doc follows a 4-section template:

1. **What & How** — One paragraph + architecture diagram
2. **Core Logic** — Processing flows, key functions, design decisions, error handling, security
3. **Config** — Settings table (`Setting | Env Var | Default | Description`). Skip if no configuration
4. **Files** — File table (`File | Purpose`)

**No code paste in DESIGN docs** — never copy-paste source code into design documents. Use pseudocode to explain processing logic and describe detailed implementation. Pseudocode keeps docs readable, avoids staleness when code changes, and forces focus on intent over syntax.

`DESIGN-core.md` is the skeleton: system overview, agent loop, cross-cutting concerns, module/dependency tables. Detail lives in the component docs.

### Design (architecture and implementation details, kept in sync with code)
- `docs/DESIGN-core.md` — System overview, agent loop: factory, `CoDeps`, orchestration, streaming, approval, cross-cutting concerns, modules, dependencies
- `docs/DESIGN-personality.md` — Personality system: 4 file-driven roles, 5 traits, structural per-turn injection, reasoning depth override
- `docs/DESIGN-llm-models.md` — LLM model configuration (Gemini, Ollama) + Ollama local setup guide
- `docs/DESIGN-logging-and-tracking.md` — Telemetry architecture, SQLite schema, viewers, real-time tail
- `docs/DESIGN-context-governance.md` — Context governance (history processors, sliding window, summarisation)
- `docs/DESIGN-prompt-design.md` — Agentic loop + prompt architecture: run_turn, approval re-entry (four-tier), tool preamble, safety policy, static/per-turn prompt layers
- `docs/DESIGN-tools.md` — All native tool implementations: Memory, Shell (four-tier approval), Obsidian, Google (Drive/Gmail/Calendar), Web (search + fetch), Capabilities
- `docs/DESIGN-knowledge.md` — Knowledge system: flat storage, kinds, FTS5/hybrid search, tool surface, memory lifecycle (signal detection, precision edits, dedup, decay, tag/temporal filtering)
- `docs/DESIGN-mcp-client.md` — MCP client: external tool servers via Model Context Protocol (stdio transport, auto-prefixing, approval inheritance)

### TODO (remaining work items only — no design content, no status tracking)

**Lifecycle rule:** When a section or item ships, remove it from the TODO doc and merge its design into the relevant DESIGN doc. TODO docs contain only unimplemented work — completed sections do not stay here.
- `docs/TODO-subagent-delegation.md` — Sub-agent delegation: research + analysis sub-agents, budget sharing, confidence-scored advisory outputs
- `docs/TODO-background-execution.md` — Background task execution for long-running operations
- `docs/TODO-voice.md` — Voice-to-voice round trip (deferred)
- `docs/TODO-openclaw-adoption.md` — Deferred task: `/new` slash command (TASK-9, blocked on `_index_session_summary()`)
- `docs/TODO-skills-system.md` — P3 skills gaps (Gaps 8–10): `allowed-tools` grants, shell preprocessing, `context:fork` subagent
- `docs/TODO-gap-openclaw-analysis.md` — P3 openclaw gaps (§8–§14): MMR re-ranking, embedding provider, process registry, security audit command, skills system, cron scheduling, config includes
- `docs/TODO-coding-tool-convergence.md` — Coding tool convergence: native file tools (read/list/find/write/edit), shell policy engine, coder subagent delegation, coding eval gates, workspace checkpoint + rewind, approval risk classifier (P0–P2)

### Skills

Three skills map onto the dev workflow. Human gates are at decisions, not artifacts.

```
PO brief
    ↓
TL:  /orchestrate-plan  → docs/TODO-<slug>.md  (TL + Reviewer + Auditor)
    ↓
👤  Gate 1: PO + TL approve plan          (right problem? correct scope?)
    ↓
Dev: /orchestrate-dev   → docs/DELIVERY-<slug>.md  (implement + self-review + test + sync-doc)
    ↓
👤  Gate 2: TL reviews delivery report    (all done_when passed?)
    ↓
👤  Gate 3: PO acceptance                 (does it work for the user?)
    ↓
ship
```

- `/orchestrate-plan <slug>` — TL drafts plan → Core Dev critiques in parallel → TL decides → repeat until clean, produces `docs/TODO-<slug>.md`
- `/orchestrate-dev <slug>` — Implements approved plan: execute tasks, self-review, verify done_when, run tests, sync docs, produce `docs/DELIVERY-<slug>.md`
- `/sync-doc [doc...]` — Verify DESIGN docs against current source code and fix inaccuracies in-place. No args = all DESIGN docs. Also invoked internally by `orchestrate-dev`.

## Reference Repos (local, for design research)

Peer CLI tools cloned in `~/workspace_genai/` for studying shell safety, approval flows, sandbox designs, memory architectures, and UX patterns:

| Repo | Language | Key files for shell safety / approval / memory |
|------|----------|-------------------------------------------------|
| `codex` | Rust | `codex-rs/shell-command/src/command_safety/` — deepest: tokenizes cmds, inspects flags, recursive shell wrapper parsing. Also `codex-rs/linux-sandbox/src/bwrap.rs` — vendored bubblewrap |
| `gemini-cli` | TypeScript | `tools.allowed` prefix matching in settings, tool executor middleware |
| `opencode` | TypeScript | Multi-surface agentic coding tool (CLI + desktop + cloud); `packages/opencode/` — agent loop, tool execution, provider abstraction |
| `claude-code` | TypeScript | `packages/core/src/scheduler/policy.ts` — hook-based permission engine; `packages/cli/src/config/settings.ts` — allow/deny rules (post-CVE-2025-66032); `packages/core/src/utils/sandbox.ts` |
| `aider` | Python | Simplest model — no sandbox, `io.confirm_ask()` for everything; proves you can ship without a sandbox if approval gate is strict |
| `openclaw` | TypeScript | `src/memory/` — production hybrid search: FTS5 + sqlite-vec + embedding cache, weighted merge, multi-provider embeddings, chunking with overlap |
| `letta` | Python | Three-tier memory: in-context blocks (`letta/schemas/memory.py`, `block.py`) + archival passages with pgvector/tags (`letta/orm/passage.py`, `letta/services/passage_manager.py`) + summarization on overflow (`letta/services/summarizer/`). Agent-driven memory tools in `letta/functions/function_sets/base.py` (`core_memory_*`, `archival_memory_*`, `memory_rethink`). Key insight: agent decides what to archive — no auto-save, no decay, consolidation is agent-initiated |
| `sidekick-cli` | Python | Closest peer in scope: REPL CLI, multi-provider LLM, MCP, per-project config. `src/sidekick/agent.py` — approval UX: three-option (yes/always/no) with per-tool session disable. `src/sidekick/config.py` — config + MCP server parsing. `src/sidekick/mcp/servers.py` — MCP approval callback wiring |
| `mem0` | Python | Production memory layer: hybrid vector + knowledge graph search, cross-session persistence, LLM-driven extraction. `mem0/memory/main.py` — add/search/update/delete API. `mem0/memory/graph_memory.py` — graph store (Neo4j/Kuzu). `mem0/vector_stores/` — pluggable backends (Qdrant, Chroma, pgvector, Faiss). Key for co-cli: how fact extraction + contradiction resolution is LLM-driven, not rule-based |
