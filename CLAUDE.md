# CLAUDE.md

This file provides guidance to Claude Code (`claude.ai/code`) when working with this repository.

## Build & Run Commands

```bash
uv sync                          # Install all dependencies (runtime + dev)
uv run co chat                   # Interactive REPL
uv run co status                 # System health check
uv run co logs                   # Datasette trace viewer (table)
uv run co traces                 # Nested HTML trace viewer

# ALL pytest runs MUST pipe to a timestamped log under .pytest-logs/ (mkdir -p first).
# Never truncate output before the log file (no | head, | tail, | grep before tee).
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log

# Evals: uv run python evals/eval_<name>.py  (ls evals/ for full list)
```

## System Overview

### Architecture

See `docs/DESIGN-system.md` for architecture, `CoDeps`, capability surface, and security boundaries. See `docs/DESIGN-core-loop.md` for agent loop internals, orchestration, and approval mechanics.

### Knowledge System

All knowledge is dynamic, loaded on-demand via tools, and never baked into the system prompt. Flat `.co-cli/memory/*.md` files with YAML frontmatter store both memories (`kind: memory`) and articles (`kind: article`). FTS5 (BM25) search runs in `search.db`. See `docs/DESIGN-context.md` for the full schema, tool API, lifecycle, and work record provenance model.

## Engineering Rules

### Code

- **Python 3.12+** with type hints everywhere.
- **Imports**: always explicit; never `from X import *`.
- **Comments**: no trailing comments; put comments on the line above, not at end of code lines.
- **`__init__.py`**: must be docstring-only (one-line module docstring or empty); never add imports, re-exports, or code. When converting a module to a package, all content goes into `_core.py` or named private submodules — never into `__init__.py`.
- **`_prefix.py` helpers**: leading-underscore modules are package-private. If imported outside the package, drop the underscore.
- **Class naming conventions** (enforced — violations block merge): every public type must use one of these suffixes: `*State` (mutable lifecycle data), `*Result` (immutable pass/fail outcome), `*Output` (agent/pipeline payload), `*Config`/`*Settings`/`*Policy` (configuration), `*Info` (read-only descriptor), `*Registry` (read-heavy lookup), `*Client`/`*Backend` (IO adapter), `*Store`/`*Index` (persistent storage), `*Command` (callable handler), `*Context` (input bag for a call), `*Rule` (auth/behavioral rule), `*Enum` (enumeration).
- **Variable and function naming**: use descriptive names that reveal intent — including loop variables (e.g. `idx`, `key`, `val` over `i`, `k`, `v`). Well-known conventions (`fd`, `db`) are fine as-is.
- **Display**: use the project's shared `console` object for all terminal output. Use semantic style names; never hardcode color names at callsites.

### Agents, Tools, and Config

- **Tool pattern**: tools use `agent.tool()` with `RunContext[CoDeps]`, following pydantic-ai conventions (deferred approval, history processors). All runtime resources come from `ctx.deps` — never import settings directly, never hold module-level state, and never put approval prompts inside tools.
- **Tool approval**: tools that mutate system state (filesystem writes, shell execution, external service writes, process spawning) use `requires_approval=True`. Read-only operations do not. Approval UX lives in the chat loop.
- **Tool return type**: tools returning user-facing data must use the project's `tool_output()` helper for structured returns. Never return a raw `str`, bare `dict`, or `list[dict]`.
- **CoDeps**: flat dataclass — access service handles, config, and paths via `ctx.deps.*` (e.g. `ctx.deps.shell`, `ctx.deps.config.memory.max_count`).
- **Sub-agent isolation**: use the subagent deps factory in `deps.py`. Do not manually field-copy.
- **Config**: `Settings` uses nested Pydantic sub-models in `co_cli/config/` (one file per group). Add new fields to an existing group if it fits; only create a new nested group when it has meaningful cohesion. Config precedence: env vars > `.co-cli/settings.json` (project) > `~/.co-cli/settings.json` (user) > defaults.
- **User-global paths**: `~/.co-cli/` (overridable via `CO_CLI_HOME`). Project-local: `.co-cli/`.
- **Versioning**: `MAJOR.MINOR.PATCH`; patch odd = bugfix, even = feature. Bump only in `pyproject.toml`.
- **No `.env` files**: use `settings.json` or env vars.

### Testing

> **These rules are enforced repository policy, not guidance.** Any test or test change that violates them must be fixed or removed before regression testing or merge.

#### Evals (`evals/`)

- **Evals are separate from tests**: evals run as standalone programs (`uv run python evals/eval_<name>.py`), not pytest. Pass/fail gates live inside the runner. Shared helpers belong in `evals/_*.py`, not in `co_cli/`.
- **Evals run against the real configured system**: never override config with `_ENV_DEFAULTS`, `os.environ`, or fallback defaults. If a prerequisite is missing, skip gracefully.
- **Evals must seek corners**: every eval must include at least one failure mode, degradation path, or boundary condition.

#### Tests (`tests/`)

- **Only pytest files in `tests/`**: `test_*.py` or `*_test.py`, using `pytest` + `pytest-asyncio`. Non-test scripts go in `scripts/`, evaluations in `evals/`.
- **Real dependencies only — no fakes**: never use `monkeypatch`, `unittest.mock`, `pytest-mock`, or hand-assembled domain objects that bypass production code paths. Use real `CoDeps` with real services, real SQLite, real filesystem, real FTS5. If a behavior cannot be tested without fakes, the production API is wrong — fix the API. `conftest.py` must be limited to neutral pytest plumbing (e.g. session-scoped markers, asyncio mode) — never shadow config or inject substitutes.
- **IO-bound timeouts**: wrap each individual `await` to external services (LLMs, network, subprocess) with `asyncio.timeout(N)` — including warmup and preflight awaits. Never wrap multiple sequential awaits in one block. Import constants from the test timeouts module — never hardcode inline. Never increase a timeout to make a test pass — a timeout violation means wrong role, wrong agent context, or wrong model config. Diagnose the root cause, fix the test or config.
- **Suite hygiene**: every test must target a real failure mode — ask "if deleted, would a regression go undetected?" Tests must pass or fail (no skips except credential-gated external integrations via `pytest.mark.skipif`). Remove or update stale tests when changing public APIs — do not skip them. Any policy violation blocks the full run. `pyproject.toml` enforces `-x --durations=0`.
- **Test data isolation**: use `tmp_path` for all filesystem writes. For shared stores, use `test-` prefix identifiers and delete in `try/finally` — cleanup failure must fail the test.
- **Scope pytest during implementation**: run only affected test files during dev (`uv run pytest tests/test_foo.py`). Full suite before shipping only. Never dismiss a failure as flaky — stop, diagnose, then fix.
- **Production config only — no overrides**: do not pass `model=` or `model_settings=` to `agent.run()` — use the production orchestration path or invoke the agent with no override. Do not strip personality in tests. Use non-thinking model settings for tool-calling, signal-detection, and orchestration tests. Cache module-level agents rather than rebuilding per call.
- **Never copy inline logic into tests**: do not replicate display formatting or string construction in assertions.
- **Google credentials**: never configure or inject — they resolve automatically via settings, `~/.co-cli/google_token.json`, or ADC.

### Review Discipline

- **Deep pass on first round**: read every function body, trace call paths, check for stale imports and dead code. Do not skim signatures or assume correctness from names.
- **Evidence-based verdicts**: do not declare "ready" unless you can cite `file:line` references. If zero issues found, list every file read and what was checked. If scope is unclear, ask rather than rubber-stamp.
- Always check `docs/reference/` for research/best-practice docs before reviews or design proposals.
- **Design philosophy**: design from first principles — MVP-first but production-grade. Add abstractions only when a concrete need exists. When researching peers, focus on convergent best practices, not volume.

### Code Change Principles

- Prefer fail-fast over redundant fallbacks. Clean up dead code during implementation, not as a separate pass.
- After renames or file moves: (1) grep for ALL remaining references to the old name across the whole repo, (2) check test imports specifically — they are the most common miss, (3) run the full test suite. Done only when grep finds zero stale references AND tests pass.

## Workflow

### Working with Claude Code

- When asked to analyze or review, confirm the approach before searching, fetching, or writing.
- When asked to append to an existing doc, never create a new file instead.
- Never add unsolicited notes, reminders, or meta-commentary to outputs unless explicitly asked.
- **Subagents**: declare tool permissions upfront (Read, Edit, Bash, Grep). Each subagent cleans up dead code before returning. After all finish, do an integration review for stale imports and orphaned references.

### Dev Workflow

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

## Docs

### DESIGN Doc Conventions

DESIGN docs are **post-implementation documentation** — they always stay in sync with the latest code and are the authoritative reference during planning. They must never appear as tasks in a TODO file: DESIGN doc updates are outputs of delivery, not inputs to it. All updates happen automatically through `/sync-doc` (auto-invoked by `orchestrate-dev` after delivery). Any TODO task whose `files:` list includes a `docs/DESIGN-*.md` path is invalid and must be removed.

Every component DESIGN doc follows this four-section template:

1. **What & How** — one paragraph + architecture diagram
2. **Core Logic** — processing flows, key functions, design decisions, error handling, security
3. **Config** — settings table (`Setting | Env Var | Default | Description`); skip if no configuration
4. **Files** — file table (`File | Purpose`)

Never paste source code into DESIGN docs. Use pseudocode to explain processing logic and describe detailed implementation. Pseudocode keeps docs readable, avoids staleness when code changes, and forces focus on intent over syntax.

Start at `docs/DESIGN-system.md` for top-level system architecture, `CoDeps`, capability surface, and security boundaries. `docs/DESIGN-core-loop.md` covers the agent loop, orchestration, and approval flow. All component docs live in `docs/` and are named `DESIGN-<component>.md` and `DESIGN-flow-<component>.md`.

`docs/reference/` is for research, proposals, and background material (`RESEARCH-*`, `ROADMAP-*`) and is not linked from DESIGN docs.

### Artifact Lifecycle

- `REPORT-*.md` and `TODO-*.md` live directly in `docs/`, not in subdirectories.
- `REPORT-<scope>.md` is permanent — only eval/benchmark/script runs produce these.
- `TODO-<slug>.md` tracks a delivery: plan, `✓ DONE` marks (never delete mid-delivery), delivery summary, and review verdict. Deleted after Gate 2 PASS.

## Reference Repos

Peer CLI tools in `~/workspace_genai/` are used for design research. See `docs/reference/RESEARCH-peer-systems.md` for detailed notes and `docs/reference/RESEARCH-peer-personality.md` for personality research.
