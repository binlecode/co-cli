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

# Evals: uv run python evals/eval_<name>.py  (ls evals/ for full list)
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

See `docs/DESIGN-core.md` for system overview (architecture diagrams), agent loop internals, CoDeps, orchestration, and approval mechanics. See `docs/DESIGN-index.md` for doc navigation and config/module reference.

## Knowledge System

All knowledge is dynamic — loaded on-demand via tools, never baked into the system prompt. Flat `.co-cli/knowledge/*.md` files with YAML frontmatter; memories (`kind: memory`) and articles (`kind: article`) in the same store. FTS5 (BM25) search via `KnowledgeIndex` in `search.db`. See `docs/DESIGN-knowledge.md` for full schema, tool API, and lifecycle.

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
- **CoDeps is grouped, not flat**: `CoDeps` holds four sub-groups — `services` (runtime objects: `ShellBackend`, `KnowledgeIndex`, `TaskRunner`), `config` (read-only scalars from `Settings`), `session` (per-session mutable state: approvals, skill grants, todos), `runtime` (per-run transient state: compaction, usage, processor state). Access as `ctx.deps.config.memory_max_count`, `ctx.deps.services.shell`, etc. Tools never import or reference `Settings` directly. Use `make_subagent_deps(base)` to create isolated child-agent deps sharing services and config.
- **Pydantic-ai idiomatic**: Agent, deps, tools, and agentic flows must follow pydantic-ai's patterns — `RunContext[CoDeps]` for tools, `DeferredToolRequests` for approval, history processors for memory. Don't wrap, abstract over, or deviate from the SDK's conventions
- **Config precedence**: env vars > `.co-cli/settings.json` (project) > `~/.config/co-cli/settings.json` (user) > built-in defaults
- **XDG paths**: Config in `~/.config/co-cli/`, data in `~/.local/share/co-cli/`
- **Versioning**: `MAJOR.MINOR.PATCH` — patch digit: odd = bugfix, even = feature. Bump in `pyproject.toml` only — version is read via `tomllib` from `pyproject.toml` at runtime
- **Status checks**: All environment/health probes live in `co_cli/_status.py` (`get_status() → StatusInfo` dataclass). Callers (banner, `co status` command) handle display only
- **Display**: Use `co_cli.display.console` for all terminal output. Use semantic style names — never hardcode color names at callsites
- **Design philosophy**: When researching peer systems, focus on best practices (what 2+ top systems converge on), not volume or scale. Design for MVP first — ship the smallest thing that solves the user problem. Use protocols/abstractions so post-MVP enhancements require zero caller changes.

## Testing Policy

- **Only pytest files in tests/** — All files in `tests/` must be pytest test files (`test_*.py` or `*_test.py`). Non-test scripts (demos, evaluations, utilities) go in `scripts/`.
- **Functional tests only** — no mocks or stubs. Tests exercise real code paths with real services (real SQLite, real filesystem, real FTS5 index). No unit tests: never test string constants, internal helpers in isolation, or assert on implementation details. Every test must exercise a real code path that a user or the agent would trigger.
- **No fake deps** — `RunContext` is instantiable via `pydantic_ai._run_context.RunContext(deps=deps, model=agent.model, usage=RunUsage())`. Tests use real `CoDeps(services=CoServices(shell=ShellBackend(), knowledge_index=idx), config=CoConfig(...))` with real `RunContext`. No custom fake dataclass scaffolding. Any deviation requires explicit approval.
- **No mocks, stubs, or monkeypatching** — never use `monkeypatch`, `unittest.mock`, `pytest-mock`, or any form of patching. If a behavior cannot be tested without injecting fake dependencies, the production API is wrong — fix the API. The only exception is environment variables (`monkeypatch.setenv`) when testing config parsing.
- **IO-bound tests must have explicit timeouts** — wrap `asyncio` calls to external services (LLM, network, subprocess spawning) with `asyncio.timeout(N)` so tests fail fast instead of hanging. Use adaptive values: set N = expected_worst_case + safety_margin, not a flat ceiling. Guidelines: subprocess lifecycle tests ≤ 15s, single HTTP requests ≤ 30s, HTTP with retries/backoff ≤ 30s (verify against tool retry config), LLM summarization ≤ 60s, full agent runs ≤ 120s. Local SQLite/filesystem tests need no timeout. Let `TimeoutError` propagate — pytest reports it as a hard failure automatically. No try/catch wrapper needed.
- **Timeout = fail fast, then fix** — when a test times out during CI or a dev run, treat it as a hard failure (not a flake). Always run with `-x` (stop on first failure). Stop immediately, read the test output log to identify root cause, then fix the underlying issue (broken dep, missing service, wrong timeout value, or stale test). Never re-run hoping it clears.
- **Remove stale tests** — a test that exercises a removed or renamed API, asserts on a deleted constant, or depends on infrastructure that no longer exists must be deleted, not skipped or commented out. If you change a public API (function signature, return shape, class name), scan `tests/` for callers and update or remove them in the same commit.
- **Critical functionality focus** — each test must validate behavior that matters: a tool returning correct results, a pipeline producing expected output, a safety invariant holding. Do not write tests for trivial paths (empty input → empty output), negative edge cases with no real-world trigger, or assertions that merely restate what the code does. Ask: "if this test were deleted, would a real regression go undetected?" If no, don't write it.
- **No skips** — tests must pass or fail, never skip. **Exception:** API-dependent tests requiring paid external credentials (Brave Search) use `pytest.mark.skipif` when the key is absent — without a valid key these tests hang on network timeouts rather than failing with a useful error.
- **Google tests resolve credentials automatically**: explicit `google_credentials_path` in settings, `~/.config/co-cli/google_token.json`, or ADC at `~/.config/gcloud/application_default_credentials.json`
- **Test timing is always on** — `pyproject.toml` sets `addopts = "--durations=0"` so every run reports per-test wall time. When adding a test, check its timing is proportionate to what it exercises — unexpectedly slow tests indicate over-broad scope or missing `asyncio.timeout`.
- **No conftest.py — eat your own dogfood** — tests run against the real `config.py` settings singleton, not overridden fixtures. If a test fails because of a wrong default in `config.py`, fix `config.py`. Never add `conftest.py` to inject test-only config. Tests are the first consumer of production config — if the default is broken for tests, it is broken for users too.
- Framework: `pytest` + `pytest-asyncio`
- Set `LLM_PROVIDER=gemini` or `LLM_PROVIDER=ollama` env var for LLM E2E tests

## Anti-Patterns

- Do not use `tool_plain()` for new tools — use `agent.tool()` with `RunContext`
- Do not import `settings` directly in tool files — use `ctx.deps`
- Do not pass `Settings` objects into `CoDeps` — flatten scalar fields into `CoConfig`. Use `make_subagent_deps(base)` for sub-agent isolation, not manual field copying
- Do not put approval prompts inside tools — use `requires_approval=True` and handle in the chat loop
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

Start at `docs/DESIGN-index.md` (navigation, config reference, module index). `docs/DESIGN-core.md` covers agent loop, CoDeps, orchestration, and approval. All 20+ component docs live in `docs/` — named `DESIGN-<component>.md` and `DESIGN-flow-<component>.md`.

`docs/reference/` — research and background material (RESEARCH-*, ROADMAP-*). Not linked from DESIGN docs.

**Workflow doc placement:** All workflow artifacts — `REVIEW-*.md`, `TODO-*.md`, `DELIVERY-*.md` — live directly in `docs/` (not in subdirectories). `docs/reference/` is for research only.

**TODO lifecycle:** When a section ships, remove it from `docs/TODO-<slug>.md` and merge its design into the relevant DESIGN doc. TODO docs contain only unimplemented work.

### Skills

Six skills map onto the dev workflow. Human gates are at decisions, not artifacts.

```
PO brief / TL pre-check
    ↓
     /orchestrate-review <scope>  → docs/REVIEW-<scope>.md  (Code Dev + Auditor)
    ↓
👤  TL reads verdict: HEALTHY / NEEDS_ATTENTION / ACTION_REQUIRED
    ↓
[optional] TL:  /research <scope>  → docs/reference/RESEARCH-<scope>.md
[optional] 👤  TL reads research: gaps to address in design?
    ↓
TL:  /orchestrate-plan  → docs/TODO-<slug>.md  (TL + Reviewer + Auditor)
    ↓
👤  Gate 1: PO + TL approve plan          (right problem? correct scope?)
    ↓
Dev: /orchestrate-dev   → docs/DELIVERY-<slug>.md  (implement + self-review + test + sync-doc + delivery-audit)
    ↓
👤  Gate 2: TL reviews delivery report    (all done_when passed?)
    ↓
👤  Gate 3: PO acceptance                 (does it work for the user?)
    ↓
ship
    ↓
🗑  Delete DELIVERY-<slug>.md  (temporary scaffolding — not a permanent record)
```

- `/orchestrate-review <scope>` — Code↔doc accuracy + TODO health → TL verdict. Run after every delivery or before planning.
- `/orchestrate-plan <slug>` — TL drafts plan → Core Dev critiques → TL decides → produces `docs/TODO-<slug>.md`
- `/orchestrate-dev <slug>` — Implements approved plan, produces `docs/DELIVERY-<slug>.md`
- `/sync-doc [doc...]` — Fix DESIGN doc inaccuracies in-place. No args = all docs. Auto-invoked by `orchestrate-dev`.
- `/delivery-audit <scope>` — Inverse coverage check: tools/settings/commands vs DESIGN docs. Auto-invoked by `orchestrate-dev`.
- `research <scope>` — Free-form task: compare co-cli against peer systems → `docs/reference/RESEARCH-<scope>.md`. See Reference Repos in `docs/reference/` for key files per repo.

## Reference Repos

Peer CLI tools in `~/workspace_genai/` for design research. Key repos: `codex` (shell safety, sandbox), `claude-code` (permission engine), `openclaw` (hybrid memory search), `letta` (three-tier memory), `sidekick-cli` (approval UX), `mem0` (LLM-driven extraction), `aider` (minimal approval model), `gemini-cli`, `opencode`. File-level notes moved to `docs/reference/RESEARCH-peer-systems.md`.
