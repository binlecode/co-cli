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
- **After refactoring**: always check for dead code, stale imports, and misplaced lazy imports before reporting done.
- **Renames and large refactors**: verify all references across code, tests, AND docs in the same change.

### Agents, Tools, and Config

- **Tool pattern**: new tools must use `agent.tool()` with `RunContext[CoDeps]`. Do not use `tool_plain()` for new tools.
- **Tool deps**: access runtime resources via `ctx.deps`. Do not import `settings` directly in tool files. Do not put approval prompts inside tools.
- **Tool approval**: tools that mutate system state (filesystem writes, shell execution, external service writes, process spawning) use `requires_approval=True`. Read-only operations (file reads, searches, network fetches) do not. Approval UX lives in the chat loop, not inside tools.
- **Tool return type**: tools returning user-facing data must return `ToolResult` via `make_result()` from `co_cli.tools._result`. The `display` field is the pre-formatted string shown to the user; additional metadata fields (`count`, `next_page_token`, etc.) are passed as keyword arguments to `make_result()`. Never return a raw `str`, bare `dict`, or `list[dict]`.
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

> **These rules are enforced repository policy, not guidance.** Any test or test change that violates them must be fixed or removed before regression testing or merge.

#### Evals (`evals/`)

- **Evals are a separate validation surface** — not part of the pytest test suite and must not be treated as tests by policy or tooling. End-to-end chain and capability validation goes to `evals/`, not `tests/`. Rules in this section apply to `evals/` only; rules in the Tests section apply to `tests/` only.
- **Eval runner**: evals run as standalone programs (`uv run python evals/eval_<name>.py`), not pytest files. Pass/fail gates and reporting live inside the runner itself.
- **Evals run against the real configured system**: never override or fake config settings. Do not add `_ENV_DEFAULTS` blocks, `os.environ` overrides, or any fallback that shadows the user's real settings. If a prerequisite (API key, personality, provider) is not configured, check at runtime and skip gracefully — do not silently inject defaults.
- **Eval infrastructure stays in `evals/`**: shared helpers (frontends, fixtures, span analysis, check engine) belong in `evals/_*.py` sub-modules, not in `co_cli/`.
- **Evals must seek corners, not just the happy path**: every eval must include at least one failure mode, degradation path, or boundary condition. For pipeline evals: what happens when a dependency is unavailable, input is at the edge of valid, or a multi-step chain partially fails. A green eval suite that skips failure paths is worse than no eval — it creates confidence that isn't earned.

#### Tests (`tests/`)

- **Only pytest files in `tests/`**: all files must be `test_*.py` or `*_test.py`. Framework: `pytest` + `pytest-asyncio`. Non-test scripts go in `scripts/`, evaluations in `evals/`.
- **Functional tests only — no unit tests, ever**: all tests exercise real code paths with real services (real SQLite, real filesystem, real FTS5). Tests exist to find bugs in critical functionality, not to achieve coverage percentages. Every test must target a real failure mode a user or the agent would hit. Never test string constants, internal helpers in isolation, or assert on implementation details.
- **No mocks, fakes, or patching**: never use `monkeypatch`, `unittest.mock`, `pytest-mock`, or any other substitution for real services. Use real `CoDeps(services=CoServices(shell=ShellBackend(), knowledge_index=idx), config=CoConfig(...))` with real `RunContext`. If a behavior cannot be tested without fakes, the production API is wrong — fix the API.
- **IO-bound timeouts are mandatory and absolute**: wrap each individual `await` to external services (LLMs, network, subprocess) with `asyncio.timeout(N)`. Never wrap multiple sequential awaits or a retry loop in one block. Local SQLite/filesystem calls do not need timeouts. Let `TimeoutError` propagate — no try/catch. When a test times out, stop all testing immediately; check `uv run co logs` for the root cause before running again.
- **Keep the test suite clean — violations block regression**: before any full test run after a code change, remove or update tests that are stale, redundant, or policy-violating. A test exercising a removed API, asserting on a deleted constant, or using fakes must be deleted, not skipped. When changing a public API (signature, return shape, class name), scan `tests/` and update or remove callers in the same commit. Any active policy violation — timeout, mock usage, fake dep, or skip — blocks the full run.
- **Critical functionality focus**: each test validates behavior that matters — correct tool results, expected pipeline output, safety invariants. Ask: “if this test were deleted, would a real regression go undetected?” If no, do not write it.
- **No skips**: tests must pass or fail. Exception: API-dependent tests requiring paid external credentials (Brave Search) may use `pytest.mark.skipif` when the key is absent — without a valid key those tests hang on network timeouts rather than failing with a useful error.
- **No `conftest.py`**: tests run against the real `config.py` singleton. If a test fails because of a wrong default in `config.py`, fix `config.py`. Tests are the first consumer of production config; if the default is broken for tests, it is broken for users too.
- **Test timing always on**: `pyproject.toml` enforces `-x --durations=0` — fail-fast with per-test wall times. Unexpectedly slow tests indicate over-broad scope or missing `asyncio.timeout`.
- **Google credentials**: never configure or inject in tests. They resolve automatically through `google_credentials_path` in settings, `~/.config/co-cli/google_token.json`, or ADC at `~/.config/gcloud/application_default_credentials.json`.
- **Test data isolation and cleanup**: tests must not leave data in shared stores (knowledge index, memory dir, library dir, SQLite DBs). Use `tmp_path` for all filesystem writes. For shared stores, delete test-introduced records in `try/finally` — cleanup failure must fail the test. Records in any shared store must use a `test-` prefix in identifiers (`session_id=”test-...”`, slug `test-...`, tag `test`) to be identifiable for bulk-delete.
- **Run the full test suite after any code change**: `uv run pytest` piped to a timestamped log (see Build & Run Commands). Do not consider a change done until the suite is green.
- **Never dismiss a test failure as flaky**: always do proper root cause analysis. If a test fails, stop all testing immediately and diagnose before re-running.

### Review Behavior

- When asked to review a document, plan, or delivery: do a **deep review on the first pass**. Read every source file referenced before giving any assessment. Do not declare something "ready" unless you can cite specific code confirming each item.
- Never perform a shallow pass that requires 5+ review cycles. If the scope is unclear, ask a clarifying question rather than rubber-stamping and iterating.

### Task Execution

- When asked to analyze or review, do **not** start searching, fetching, or writing before understanding the full request. Read the complete instruction first.
- When asked to append to an existing file, do **not** create a new file instead.
- Do not add unsolicited notes, reminders, or meta-commentary to outputs unless explicitly asked.

## Docs

### DESIGN Doc Conventions

DESIGN docs are **post-implementation documentation** — they always stay in sync with the latest code and are the authoritative reference during planning. They must never appear as tasks in a TODO file: DESIGN doc updates are outputs of delivery, not inputs to it. All updates happen automatically through `/sync-doc` (auto-invoked by `orchestrate-dev` after delivery). Any TODO task whose `files:` list includes a `docs/DESIGN-*.md` path is invalid and must be removed.

Every component DESIGN doc follows this four-section template:

1. **What & How** — one paragraph + architecture diagram
2. **Core Logic** — processing flows, key functions, design decisions, error handling, security
3. **Config** — settings table (`Setting | Env Var | Default | Description`); skip if no configuration
4. **Files** — file table (`File | Purpose`)

Never paste source code into DESIGN docs. Use pseudocode to explain processing logic and describe detailed implementation. Pseudocode keeps docs readable, avoids staleness when code changes, and forces focus on intent over syntax.

Start at `docs/DESIGN-index.md` for navigation, config reference, and module index. `docs/DESIGN-system.md` covers top-level system architecture, `CoDeps`, capability surface, and security boundaries. `docs/DESIGN-core-loop.md` covers the agent loop, orchestration, and approval flow. All 20+ component docs live in `docs/` and are named `DESIGN-<component>.md` and `DESIGN-flow-<component>.md`.

`docs/reference/` is for research and background material (`RESEARCH-*`, `ROADMAP-*`) and is not linked from DESIGN docs.

Workflow artifact placement:

- `REPORT-*.md` and `TODO-*.md` live directly in `docs/`, not in subdirectories.

Workflow artifact lifecycle:

- `REPORT-<scope>.md` is permanent. It is an eval, pipeline run, or benchmarking report. Only eval/benchmark/script runs produce REPORT- files.
- **Reporting Structure Guidelines:** 
  - **Qualitative Behavior Evals** (`evals/*-result.md`): Must include "Per-Case Results" tables, "Drift/Error Tracing" (failed turns with prompt/response context), and Pass/Fail Gates.
  - **Quantitative Benchmarks** (`evals/benchmark-*-result.md` or `scripts/*-result.md`): Must include a "Results Summary" table showing Deltas between models (e.g., Throughput, TTFT, Total Time), "Detailed Findings" narratives explaining hardware/context anomalies, and explicitly list "API Parameters Forced" in the header.
- `TODO-<slug>.md` is the single source of work tracking for a delivery. It holds: the plan, `✓ DONE` task marks, delivery summary + independent review (appended by `orchestrate-dev`), and implementation verdict (appended by `/review-impl`). Tasks are never deleted mid-delivery. The file is deleted after Gate 2 acceptance (PASS verdict → ship → delete).

TODO lifecycle:

- When a task ships, mark it `✓ DONE` in `docs/TODO-<slug>.md` — do not delete it. The record is preserved for debugging, troubleshooting, and revert until Gate 2 PASS.
- Design details merged into a DESIGN doc (via sync-doc) are noted in the task entry, not stripped from the TODO.
- The full TODO (done + pending tasks) is deleted after Gate 2 PASS (review-impl verdict → ship → delete).

### Skills and Workflow

The workflow skills map onto the dev workflow. Human gates are at decisions, not artifacts.

```text
[optional] TL researches scope → docs/reference/RESEARCH-<scope>.md
[optional] 👤  TL reads research: gaps to address in design?
    ↓
TL:  /orchestrate-plan <slug>  → docs/TODO-<slug>.md  (TL + Core Dev + PO)
  - create TODO if none exists
  - refine TODO if one exists
  - validate current state inline (no separate review step)
    ↓
👤  Gate 1: PO + TL approve plan          (right problem? correct scope?)
    ↓
Dev: /orchestrate-dev <slug>              (implement + self-review + test + sync-doc → delivery summary appended to TODO)
    ↓
TL: /review-impl <slug>                   (evidence-first scan + auto-fix + full tests + behavioral verification → verdict appended to TODO)
    ↓
👤  Gate 2: TL reads TODO                 (plan + ✓ DONE marks + delivery summary + review-impl verdict — PASS means ship)
    ↓
ship
    ↓
🗑  Delete TODO-<slug>.md
```

- `/orchestrate-plan <slug>`: create or refine `docs/TODO-<slug>.md` — TL drafts, Core Dev (implementation risk) and PO (scope + first principles) critique in parallel, TL decides. Includes inline current-state validation before drafting.
- `/orchestrate-dev <slug>`: execute from `docs/TODO-<slug>.md`, mark shipped tasks `✓ DONE` (never delete mid-delivery), append delivery summary to TODO, auto-invoke sync-doc.
- `/review-impl <slug>`: deep self-correcting review — evidence-first spec check (file:line for every claim), adversarial self-check, auto-fix of blocking findings, full test suite with mandatory RCA, behavioral verification against running system. Appends pass/fail verdict to TODO. **PASS means ship — no further gate needed.**
- `/sync-doc [doc...]`: fix DESIGN doc inaccuracies in-place. No args means all docs. Auto-invoked by `orchestrate-dev`.

## Reference Repos

Peer CLI tools in `~/workspace_genai/` are used for design research. Key repos: `codex` (shell safety, sandbox), `claude-code` (permission engine), `openclaw` (hybrid memory search), `letta` (three-tier memory), `mem0` (LLM-driven extraction), `aider` (minimal approval model), `gemini-cli`, and `opencode`. File-level notes moved to `docs/reference/RESEARCH-peer-systems.md`.

- **Proactive research**: before proposing a solution or design, check existing research and best-practice docs in `docs/reference/` first. Do not wait to be pointed to them.
