# CLAUDE.md

This file provides guidance to Claude Code (`claude.ai/code`) when working with this repository.

## Build & Run Commands

```bash
uv sync                          # Install all dependencies (runtime + dev)
uv run co chat                   # Interactive REPL
uv run co status                 # System health check
uv run co logs                   # Datasette trace viewer (table)
uv run co traces                 # Nested HTML trace viewer

uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log       # Run all tests; ALWAYS pipe to timestamped log
uv run pytest -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log   # Verbose; same log rule
uv run pytest tests/test_tools.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-test_tools.log
uv run pytest tests/test_tools.py::test_name 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-test_name.log
uv run pytest --cov=co_cli 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-cov.log

# MANDATORY: ALL pytest runs must be piped to a timestamped log file under .pytest-logs/.
# Format: .pytest-logs/YYYYMMDD-HHMMSS-<descriptor>.log
# Never truncate pytest output (no | head, | tail, | grep on the pipe before the log file).
# mkdir -p .pytest-logs before first run if the directory does not exist.

# Evals: uv run python evals/eval_<name>.py  (ls evals/ for full list)
# Tool-calling quality gate (functional pytest)
uv run pytest tests/test_tool_calling_functional.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool_calling.log
```

## System Overview

### Architecture

```text
User ──▶ Typer CLI (main.py) ──▶ Agent (pydantic-ai) ──▶ Tools (RunContext[CoDeps])
              │                        │
              │                   instrument_all()
              ▼                        │
         prompt-toolkit           SQLiteSpanExporter ──▶ co-cli.db
         + rich console
```

See `docs/DESIGN-system.md` for system overview (architecture diagrams), `CoDeps`, capability surface, and security boundaries. See `docs/DESIGN-core-loop.md` for agent loop internals, orchestration, and approval mechanics. See `docs/DESIGN-index.md` for doc navigation and config/module reference.

### Knowledge System

All knowledge is dynamic, loaded on-demand via tools, and never baked into the system prompt. Flat `.co-cli/knowledge/*.md` files with YAML frontmatter store both memories (`kind: memory`) and articles (`kind: article`). FTS5 (BM25) search runs via `KnowledgeIndex` in `search.db`. See `docs/DESIGN-knowledge.md` for the full schema, tool API, and lifecycle.

## Engineering Rules

### Code

- **Python 3.12+** with type hints everywhere.
- **Imports**: always explicit; never `from X import *`.
- **Comments**: no trailing comments; put comments on the line above, not at end of code lines.
- **`__init__.py`**: prefer empty (docstring-only); no re-exports.
- **`_prefix.py` helpers**: internal/shared helpers in a package use a leading underscore. They are private to the package, not registered as tools, and not part of the public API.
- **Display**: use `co_cli.display.console` for all terminal output. Use semantic style names; never hardcode color names at callsites.
- **Design philosophy**: when researching peer systems, focus on best practices (what 2+ top systems converge on), not volume or scale. Design from first principles: non-over-engineered, MVP-first but production-grade. Add abstractions only when a concrete need exists in the current scope — never speculatively.

### Agents, Tools, and Config

- **Tool pattern**: new tools must use `agent.tool()` with `RunContext[CoDeps]`. Do not use `tool_plain()` for new tools.
- **Tool deps**: access runtime resources via `ctx.deps`. Do not import `settings` directly in tool files. Do not put approval prompts inside tools.
- **Tool approval**: tools that mutate system state (filesystem writes, shell execution, external service writes, process spawning) use `requires_approval=True`. Read-only operations (file reads, searches, network fetches) do not. Approval UX lives in the chat loop, not inside tools.
- **Tool return type**: tools returning user-facing data must return `dict[str, Any]` with a `display` field (pre-formatted string with URLs baked in) plus metadata fields such as `count` or `next_page_token`. Never return raw `list[dict]`.
- **No global state in tools**: tools must not hold or mutate module-level state. All runtime resources are accessed through `ctx.deps`.
- **CoDeps is grouped, not flat**: `CoDeps` holds four sub-groups:
  - `services`: runtime objects such as `ShellBackend`, `KnowledgeIndex`, `TaskRunner`
  - `config`: read-only scalars from `Settings`
  - `session`: per-session mutable state such as approvals, skill grants, todos
  - `runtime`: per-run transient state such as compaction, usage, processor state
- Access grouped deps as `ctx.deps.config.memory_max_count`, `ctx.deps.services.shell`, etc. Tools never import or reference `Settings` directly.
- **Sub-agent isolation**: use `make_subagent_deps(base)` to create isolated child-agent deps sharing services and config. Do not pass `Settings` objects into `CoDeps`; flatten scalar fields into `CoConfig`, and do not manually field-copy for sub-agent isolation.
- **Pydantic-ai idiomatic**: agents, deps, tools, and agentic flows must follow pydantic-ai conventions such as `RunContext[CoDeps]` for tools, `DeferredToolRequests` for approval, and history processors for memory. Do not wrap, abstract over, or deviate from the SDK’s conventions.
- **Config precedence**: env vars > `.co-cli/settings.json` (project) > `~/.config/co-cli/settings.json` (user) > built-in defaults.
- **XDG paths**: config in `~/.config/co-cli/`; data in `~/.local/share/co-cli/`.
- **Versioning**: `MAJOR.MINOR.PATCH`; patch digit odd = bugfix, even = feature. Bump only in `pyproject.toml`; version is read via `tomllib` from `pyproject.toml` at runtime.
- **Status checks**: status assembly lives in `co_cli/_status.py` (`get_status() -> StatusInfo` dataclass). Integration health checks live in `co_cli/_doctor.py` and are consumed by status, bootstrap, and capability flows. Callers such as the banner and `co status` handle display only.
- **Do not use `.env` files**: use `settings.json` or env vars.

### Testing

- **Testing rules are mandatory**: these are enforced repository policy, not guidance. Any test or test change that violates them must be fixed or removed before regression testing or merge.
- **Only pytest files in `tests/`**: all files in `tests/` must be pytest test files (`test_*.py` or `*_test.py`). Non-test scripts such as demos, one-off utilities, and helper scripts go in `scripts/`. Evaluations go in `evals/`.
- **Framework**: `pytest` + `pytest-asyncio`.
- **End-to-end validation belongs in `evals/`**: chain and capability validation goes to `evals/`, not `tests/`.
- **Evals run against the real configured system**: evals must never override or fake config settings. Do not add `_ENV_DEFAULTS` blocks, `os.environ` overrides, or any fallback that shadows the user's real settings. If a prerequisite (API key, personality, provider) is not configured, check at runtime and skip gracefully — do not silently inject defaults.
- **Eval infrastructure stays in `evals/`**: shared eval helpers (frontends, fixtures, span analysis, check engine) belong in `evals/_*.py` sub-modules, not in `co_cli/`. The eval package is the correct boundary for eval-centric code.
- **Functional tests only — no unit tests**: NO unit tests, never. All pytest tests must be functional tests that exercise real code paths with real services (real SQLite, real filesystem, real FTS5 index). Tests exist to find bugs in critical functionality, not to achieve coverage percentages. Every test must target a real failure mode a user or the agent would hit. Never test string constants, internal helpers in isolation, or assert on implementation details.
- **No fake deps**: use the real `RunContext` and real `CoDeps` — no fakes, no stubs, no custom scaffolding, no exceptions. Tests use real `CoDeps(services=CoServices(shell=ShellBackend(), knowledge_index=idx), config=CoConfig(...))` with real `RunContext`.
- **No mocks, stubs, or monkeypatching**: never use `monkeypatch`, `unittest.mock`, `pytest-mock`, or any other form of patching. If a behavior cannot be tested without injecting fake dependencies, the production API is wrong and must be fixed. No exceptions.
- **IO-bound tests must have explicit timeouts**: wrap each individual `await` call to external services (LLMs, network, subprocess) with `asyncio.timeout(N)` so tests fail fast instead of hanging. Never wrap multiple sequential awaits or a retry loop in one shared timeout block. Local SQLite/filesystem calls do not need timeouts. Let `TimeoutError` propagate — no try/catch wrapper.
- **Timeout = full stop**: when a test times out, stop all testing immediately. Do not re-run, do not increase the timeout value. Check the trace log (`uv run co logs`) to find the root cause and fix it before running any test again.
- **Clean up tests before regression**: before running the full test suite after any code change, first remove or update tests that are now stale, redundant, or policy-violating for the changed code. Stale tests pollute failure signals and make real regressions harder to spot. Only run the full regression after the test file is clean.
- **Policy-violating tests block regression**: do not continue to broader test runs while a timeout, mock/stub usage, fake dep pattern, skip, or other rules violation is still present. Fix the first violation, then resume from there.
- **Remove stale tests**: a test that exercises a removed or renamed API, asserts on a deleted constant, or depends on infrastructure that no longer exists must be deleted, not skipped or commented out. If you change a public API such as a function signature, return shape, or class name, scan `tests/` for callers and update or remove them in the same commit.
- **Critical functionality focus**: each test must validate behavior that matters, such as a tool returning correct results, a pipeline producing expected output, or a safety invariant holding. Do not write tests for trivial paths such as empty input -> empty output, negative edge cases with no real-world trigger, or assertions that merely restate what the code does. Ask: “if this test were deleted, would a real regression go undetected?” If no, do not write it.
- **No skips**: tests must pass or fail, never skip. Exception: API-dependent tests requiring paid external credentials (Brave Search) may use `pytest.mark.skipif` when the key is absent, because without a valid key those tests hang on network timeouts rather than failing with a useful error.
- **Google credentials**: do not configure or inject credentials in tests. They resolve automatically through `google_credentials_path` in settings, `~/.config/co-cli/google_token.json`, or ADC at `~/.config/gcloud/application_default_credentials.json`.
- **Test timing is always on**: `pyproject.toml` enforces `-x --durations=0` so every run is fail-fast and reports per-test wall time. When adding a test, check that its timing is proportionate to what it exercises; unexpectedly slow tests usually indicate over-broad scope or missing `asyncio.timeout`.
- **No `conftest.py`**: eat your own dogfood. Tests run against the real `config.py` settings singleton, not overridden fixtures. If a test fails because of a wrong default in `config.py`, fix `config.py`. Never add `conftest.py` to inject test-only config. Tests are the first consumer of production config; if the default is broken for tests, it is broken for users too.
- **Test data isolation and cleanup**: functional tests must not leave data in shared stores (knowledge index, memory dir, library dir, SQLite DBs). Use `tmp_path` (pytest-managed temp dir, auto-deleted after the test) for all filesystem writes. For shared stores that cannot use `tmp_path`, delete test-introduced records in a `try/finally` block. Cleanup is mandatory — if it fails, the test must explicitly fail and report the failure. Test-introduced records that land in any shared store must use a `test-` prefix in their identifier (e.g. `session_id="test-..."`, slug `test-...`, memory tag `test`). This makes stray records identifiable and safe to bulk-delete.

## Docs

### DESIGN Doc Conventions

DESIGN docs always stay in sync with the latest code; no version stamps are needed.

Every component DESIGN doc follows this four-section template:

1. **What & How** — one paragraph + architecture diagram
2. **Core Logic** — processing flows, key functions, design decisions, error handling, security
3. **Config** — settings table (`Setting | Env Var | Default | Description`); skip if no configuration
4. **Files** — file table (`File | Purpose`)

Never paste source code into DESIGN docs. Use pseudocode to explain processing logic and describe detailed implementation. Pseudocode keeps docs readable, avoids staleness when code changes, and forces focus on intent over syntax.

Start at `docs/DESIGN-index.md` for navigation, config reference, and module index. `docs/DESIGN-system.md` covers top-level system architecture, `CoDeps`, capability surface, and security boundaries. `docs/DESIGN-core-loop.md` covers the agent loop, orchestration, and approval flow. All 20+ component docs live in `docs/` and are named `DESIGN-<component>.md` and `DESIGN-flow-<component>.md`.

`docs/reference/` is for research and background material (`RESEARCH-*`, `ROADMAP-*`) and is not linked from DESIGN docs.

Workflow artifact placement:

- `REPORT-*.md`, `TODO-*.md`, and `DELIVERY-*.md` live directly in `docs/`, not in subdirectories.

Workflow artifact lifecycle:

- `REPORT-<scope>.md` is permanent. It is an eval, pipeline run, or benchmarking report. Only eval/benchmark/script runs produce REPORT- files.
- **Reporting Structure Guidelines:** 
  - **Qualitative Behavior Evals** (`evals/*-result.md`): Must include "Per-Case Results" tables, "Drift/Error Tracing" (failed turns with prompt/response context), and Pass/Fail Gates.
  - **Quantitative Benchmarks** (`evals/benchmark-*-result.md` or `scripts/*-result.md`): Must include a "Results Summary" table showing Deltas between models (e.g., Throughput, TTFT, Total Time), "Detailed Findings" narratives explaining hardware/context anomalies, and explicitly list "API Parameters Forced" in the header.
- `TODO-<slug>.md` is the single source of work tracking for a delivery. It holds the plan, the audit log from `/orchestrate-plan`, and the `/delivery-audit` coverage results (appended at the end). `orchestrate-dev` marks shipped tasks `✓ DONE` — tasks are never deleted mid-delivery. The file is deleted only at Gate 3 (PO acceptance), in the same Claude Code workflow session that deletes the DELIVERY file.
- `DELIVERY-<slug>.md` is temporary scaffolding for Gate 2 and Gate 3 only. After PO acceptance at Gate 3, delete it in the same Claude Code workflow session that records acceptance.

TODO lifecycle:

- When a task ships, mark it `✓ DONE` in `docs/TODO-<slug>.md` — do not delete it. The record is preserved for debugging, troubleshooting, and revert until Gate 3.
- Design details merged into a DESIGN doc (via sync-doc) are noted in the task entry, not stripped from the TODO.
- The full TODO (done + pending tasks) is deleted at Gate 3 alongside the DELIVERY file.

### Skills and Workflow

The workflow skills map onto the dev workflow. Human gates are at decisions, not artifacts.

```text
[optional] TL:  /research <scope>  → docs/reference/RESEARCH-<scope>.md
[optional] 👤  TL reads research: gaps to address in design?
    ↓
TL:  /orchestrate-plan <slug>  → docs/TODO-<slug>.md  (TL + Core Dev + PO)
  - create TODO if none exists
  - refine TODO if one exists
  - validate current state inline (no separate review step)
    ↓
👤  Gate 1: PO + TL approve plan          (right problem? correct scope?)
    ↓
Dev: /orchestrate-dev <slug>   → docs/DELIVERY-<slug>.md  (implement + self-review + test + sync-doc + delivery-audit → appended to TODO)
    ↓
👤  Gate 2: TL reviews delivery report    (all done_when passed?)
    ↓
👤  Gate 3: PO acceptance                 (does it work for the user?)
    ↓
ship
    ↓
🗑  Delete DELIVERY-<slug>.md  (temporary scaffolding — delete immediately after Gate 3)
```

- `/orchestrate-plan <slug>`: create or refine `docs/TODO-<slug>.md` — TL drafts, Core Dev (implementation risk) and PO (scope + first principles) critique in parallel, TL decides. Includes inline current-state validation before drafting.
- `/orchestrate-dev <slug>`: execute from `docs/TODO-<slug>.md`, mark shipped tasks `✓ DONE` (never delete mid-delivery), produce `docs/DELIVERY-<slug>.md`, auto-invoke sync-doc and delivery-audit.
- `/sync-doc [doc...]`: fix DESIGN doc inaccuracies in-place. No args means all docs. Auto-invoked by `orchestrate-dev`.
- `/delivery-audit <scope>`: inverse coverage check of tools/settings/commands vs DESIGN docs. Results appended to `docs/TODO-<scope>.md`. Auto-invoked by `orchestrate-dev`.
- `/research <scope>`: free-form discovery, producing `docs/reference/RESEARCH-<scope>.md`. Outside the delivery workflow. See reference repos in `docs/reference/` for key files per repo.

## Reference Repos

Peer CLI tools in `~/workspace_genai/` are used for design research. Key repos: `codex` (shell safety, sandbox), `claude-code` (permission engine), `openclaw` (hybrid memory search), `letta` (three-tier memory), `mem0` (LLM-driven extraction), `aider` (minimal approval model), `gemini-cli`, and `opencode`. File-level notes moved to `docs/reference/RESEARCH-peer-systems.md`.
