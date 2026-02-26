# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
uv sync                          # Install all dependencies (runtime + dev)
uv run co chat                   # Interactive REPL
uv run co status                 # System health check
uv run co logs                   # Datasette trace viewer (table)
uv run co traces                 # Nested HTML trace viewer
uv run co debug-personality      # Personality prompt layer inspector

uv run pytest                    # Run all functional tests
uv run pytest -v                 # Verbose output
uv run pytest tests/test_tools.py            # Single test file
uv run pytest tests/test_tools.py::test_name # Single test function
uv run pytest --cov=co_cli                   # With coverage

# Demo Scripts
uv run python scripts/test_memory_lifecycle_movie_query.py  # Memory lifecycle demo

# Evaluation Suite (evals/)
uv run python evals/eval_tool_calling.py              # Tool selection + intent (JSONL scored)
uv run python evals/eval_tool_chains.py               # Multi-step chain completion
uv run python evals/eval_conversation_history.py      # Multi-turn context retention
uv run python evals/eval_safety_doom_loop.py           # Doom loop detection (deterministic)
uv run python evals/eval_safety_abort_marker.py       # Ctrl-C abort marker injection
uv run python evals/eval_safety_grace_turn.py         # Budget exhaustion grace turn
uv run python evals/eval_memory_proactive_recall.py   # Proactive memory injection (W1)
uv run python evals/eval_memory_signal_detection.py   # Signal detection + contradiction (W2/W6)
uv run python evals/eval_signal_analyzer.py           # Mini-agent classification: high/low/none confidence
uv run python evals/eval_memory_decay.py              # Decay lifecycle (deterministic, W7)
uv run python evals/eval_personality_adherence.py    # Personality adherence (P2, heuristic scored)
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

All knowledge is dynamic — loaded on-demand via tools, never baked into the system prompt. Two tiers:

**Memory** — conversation-derived knowledge (preferences, decisions, corrections, patterns):
- Storage: `.co-cli/knowledge/memories/*.md`
- Tools: `save_memory(content, tags)`, `recall_memory(query)`, `list_memories()`

**Lakehouse** — curated articles and multimodal assets (future):
- Storage: `.co-cli/knowledge/articles/*.md`, assets in `articles/assets/{slug}/`
- Tools: `save_article()`, `recall_article()`, `list_articles()` (planned)

**Retrieval:** grep + frontmatter for MVP (<200 items). Future: SQLite FTS5 → hybrid search with vectors.

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
- **Display**: Use `co_cli.display.console` for all terminal output. Use semantic style names — never hardcode color names at callsites. See `docs/DESIGN-08-theming-ascii.md` for style inventory and theme architecture

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
- `docs/DESIGN-02-personality.md` — Personality system: 4 file-driven roles, 5 traits, structural per-turn injection, reasoning depth override
- `docs/DESIGN-llm-models.md` — LLM model configuration (Gemini, Ollama) + Ollama local setup guide
- `docs/DESIGN-04-streaming-event-ordering.md` — Streaming event ordering, boundary-safe rendering, and regression coverage
- `docs/DESIGN-logging-and-tracking.md` — Telemetry architecture, SQLite schema, viewers, real-time tail
- `docs/DESIGN-07-context-governance.md` — Context governance (history processors, sliding window, summarisation)
- `docs/DESIGN-08-theming-ascii.md` — Theming, ASCII art banner, display helpers
- `docs/DESIGN-tools.md` — All native tool implementations: Memory, Shell, Obsidian, Google (Drive/Gmail/Calendar), Web (search + fetch)
- `docs/DESIGN-14-memory-lifecycle-system.md` — Memory lifecycle management: auto-triggered signal detection (implemented), context loading, dedup, consolidation, decay, protection, search evolution
- `docs/DESIGN-15-mcp-client.md` — MCP client: external tool servers via Model Context Protocol (stdio transport, auto-prefixing, approval inheritance)

### TAKEAWAY (incomplete adoptions backlog — no design content, no completed items)

`docs/TAKEAWAY-*.md` files track work items adopted from peer systems that are not yet fully implemented. Three statuses: OPEN = not started, PARTIAL = in progress, BLOCKED = external dependency.

**Lifecycle rule:** When an item is completed, merge its design logic into the relevant DESIGN doc and remove the entry from the TAKEAWAY file. TAKEAWAY files track only incomplete work — completed items do not stay here.

### TODO (remaining work items only — no design content, no status tracking)

**Lifecycle rule:** When a section or item ships, remove it from the TODO doc and merge its design into the relevant DESIGN doc. TODO docs contain only unimplemented work — completed sections do not stay here.
- `docs/TODO-co-agentic-loop-and-prompting.md` — Ground-up agentic loop + prompting architecture design (goal-driven ReAct, doom loop detection, prompt composition)
- `docs/TODO-background-execution.md` — Background task execution for long-running operations
- `docs/TODO-knowledge-articles.md` — Lakehouse tier: articles, multimodal assets, learn mode, search scaling
- `docs/TODO-voice.md` — Voice-to-voice round trip (deferred)
- `docs/TODO-sqlite-fts-and-sem-search-for-knowledge-files.md` — SQLite FTS5 + semantic search: unified index for all text sources (memories, articles, Obsidian, Drive)
- `docs/TODO-tool-docstring-template.md` — 4-dimension template for tool docstrings: what it does, what it returns, when/how to use, caveats

### Skills
- `/release <version|feature|bugfix>` — Full release workflow: tests, version bump, changelog, design doc sync, TODO cleanup, commit
- `/sync-book` — Sync GitHub Pages design book: index, nav ordering, cross-references, excludes, push

## Reference Repos (local, for design research)

Peer CLI tools cloned in `~/workspace_genai/` for studying shell safety, approval flows, sandbox designs, and UX patterns:

| Repo | Language | Key files for shell safety / approval |
|------|----------|--------------------------------------|
| `codex` | Rust | `codex-rs/core/src/command_safety/` — deepest: tokenizes cmds, inspects flags, recursive shell wrapper parsing. Also `codex-rs/linux-sandbox/src/bwrap.rs` — vendored bubblewrap |
| `gemini-cli` | TypeScript | `tools.allowed` prefix matching in settings, tool executor middleware |
| `opencode` | Go | Multi-provider, flexible model switching |
| `claude-code` | TypeScript | `packages/core/src/scheduler/policy.ts` — hook-based permission engine; `packages/cli/src/config/settings.ts` — allow/deny rules (post-CVE-2025-66032); `packages/core/src/utils/sandbox.ts` |
| `aider` | Python | Simplest model — no sandbox, `io.confirm_ask()` for everything; proves you can ship without a sandbox if approval gate is strict |
| `openclaw` | TypeScript | `src/memory/` — production hybrid search: FTS5 + sqlite-vec + embedding cache, weighted merge, multi-provider embeddings, chunking with overlap |
