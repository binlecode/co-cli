# CLAUDE.md

This file provides guidance to Claude Code (`claude.ai/code`) when working with this repository.

## Setup (run once after clone)

```bash
uv sync                          # Install all dependencies (runtime + dev)
git config core.hooksPath .githooks  # Activate version-controlled git hooks
```

## Build & Run Commands

```bash
uv run co chat                   # Interactive REPL
uv run co status                 # System health check
uv run co logs                   # Datasette trace viewer (table)
uv run co traces                 # Nested HTML trace viewer

# Quality gates (scripts/quality-gate.sh — single source of truth for all checks)
scripts/quality-gate.sh lint              # ruff check + format (pre-commit hook)
scripts/quality-gate.sh lint --fix        # ruff auto-fix + format
scripts/quality-gate.sh types             # lint + pyright
scripts/quality-gate.sh full              # lint + pyright + pytest (pre-push hook + ship gate)

# ALL pytest runs MUST pipe to a timestamped log under .pytest-logs/ (mkdir -p first).
# Never truncate output before the log file (no | head, | tail, | grep before tee).
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log

# Evals: uv run python evals/eval_<name>.py  (ls evals/ for full list)
```

## System Overview

### Architecture

See `docs/specs/system.md` for architecture, `CoDeps`, capability surface, and security boundaries. See `docs/specs/core-loop.md` for agent loop internals, orchestration, and approval mechanics.

### Knowledge System

All knowledge is dynamic, loaded on-demand via tools, and never baked into the system prompt. Flat `.co-cli/memory/*.md` files with YAML frontmatter store both memories (`kind: memory`) and articles (`kind: article`). FTS5 (BM25) search runs in `search.db`. See `docs/specs/context.md` for the full schema, tool API, lifecycle, and work record provenance model.

## Engineering Rules

### Code

- **Python 3.12+** with type hints everywhere.
- **Imports**: always explicit; never `from X import *`.
- **Comments**: no trailing comments; put comments on the line above, not at end of code lines.
- **`__init__.py`**: must be docstring-only (one-line module docstring or empty); never add imports, re-exports, or code. When converting a module to a package, all content goes into `_core.py` or named private submodules — never into `__init__.py`.
- **`_prefix.py` helpers**: leading-underscore modules are package-private. If imported outside the package, drop the underscore.
- **Class naming conventions** (enforced — violations block merge): every public type must use one of these suffixes: `*State` (mutable lifecycle data), `*Result` (immutable pass/fail outcome), `*Output` (agent/pipeline payload), `*Config` (configuration — preferred; `*Settings` acceptable for Pydantic sub-models), `*Info` (read-only descriptor), `*Registry` (registration lookup table — built during a registration phase, queried at runtime), `*Store` (persistent storage layer), `*Context` (input bag for a call), `*Event` (async/streaming event), `*Error` (exception class), `*Enum` (enumeration — co-cli convention; distinguishes enum types from data containers). Exception: discriminated union leaves that exist purely as tagged members of a union type (e.g. `type Outcome = Foo | Bar`) are exempt.
- **Variable and function naming**: use descriptive names that reveal intent — including loop variables (e.g. `idx`, `key`, `val` over `i`, `k`, `v`). Well-known conventions (`fd`, `db`) are fine as-is.
- **Display**: use the project's shared `console` object for all terminal output. Use semantic style names; never hardcode color names at callsites.
- **Quality gates**: `scripts/quality-gate.sh` is the single source of truth for all automated checks. `lint` = ruff (pre-commit enforced), `types` = lint + pyright, `full` = lint + pyright + pytest. Tool configs live in `pyproject.toml`. Never add `# noqa` or `# type: ignore` without a comment explaining why the tool is wrong for that line.

### Agents, Tools, and Config

- **Tool pattern**: tools use `agent.tool()` with `RunContext[CoDeps]`, following pydantic-ai conventions (deferred approval, history processors). All runtime resources come from `ctx.deps` — never import settings directly, never hold module-level state, and never put approval prompts inside tools.
- **Tool approval**: tools that mutate system state (filesystem writes, shell execution, external service writes, process spawning) use `requires_approval=True`. Read-only operations do not. Approval UX lives in the chat loop.
- **Tool return type**: tools returning user-facing data must use the project's `tool_output()` helper for structured returns. Never return a raw `str`, bare `dict`, or `list[dict]`.
- **CoDeps**: flat dataclass — access service handles, config, and paths via `ctx.deps.*` (e.g. `ctx.deps.shell`, `ctx.deps.config.memory.max_count`).
- **Sub-agent isolation**: use the subagent deps factory in `deps.py`. Do not manually field-copy.
- **Config**: `Settings` uses nested Pydantic sub-models in `co_cli/config/` (one file per group). Add new fields to an existing group if it fits; only create a new nested group when it has meaningful cohesion. Config precedence: env vars > `.co-cli/settings.json` (project) > `~/.co-cli/settings.json` (user) > defaults.
- **User-global paths**: `~/.co-cli/` (overridable via `CO_CLI_HOME`). Project-local: `.co-cli/`.
- **Versioning**: `MAJOR.MINOR.PATCH`; patch odd = bugfix, even = feature. Bump only in `pyproject.toml`. Git history is the changelog; releases use GitHub Releases — tag `vX.Y.Z` and push to trigger `.github/workflows/release.yml`.
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
- **Suite hygiene**: every test must target a real failure mode — ask "if deleted, would a regression go undetected?" Tests must pass or fail (no skips except credential-gated external integrations via `pytest.mark.skipif`). Remove or update stale tests when changing public APIs — do not skip them. Any policy violation blocks the full run. `pyproject.toml` enforces `-x --durations=0`. Known anti-patterns that pass the deletion question but add no coverage:
  - *Fixture not wired*: `tmp_path` (or any injected fixture) in the signature but never passed to a production function — assertion trivially passes.
  - *Duplicate with trivial delta*: two tests dispatch the same function/command and assert the same invariant; the extra test adds only a trivially-true assertion (e.g. `result.flag is False` where False is the default).
  - *Structural pre-empted by imports*: testing that a package directory exists when any `from co_cli.x import ...` failure surfaces first.
  - *Truthy-only assertion*: `assert result.version` instead of `assert re.fullmatch(r"\d+\.\d+\.\d+", result.version)` — passes even if the value is wrong.
  - *Subsumed file*: an entire test file whose every test is a strict subset of tests in another file covering the same module.
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

- When interrupted or redirected, immediately stop the current approach and follow the new direction — do not continue previous work or expand scope.
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
TL:  /orchestrate-plan <slug>  → docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md  (TL + Core Dev + PO)
  - create plan if none exists
  - refine plan if one exists
  - validate current state inline (no separate review step)
    ↓
👤  Gate 1: PO + TL approve plan          (right problem? correct scope?)
    ↓
Dev: /orchestrate-dev <slug>              (implement + self-review + test + sync-doc → delivery summary appended to plan)
    ↓
TL: /review-impl <slug>                   (evidence-first scan + auto-fix + full tests + behavioral verification → verdict appended to plan)
    ↓
👤  Gate 2: TL reads plan                 (plan + ✓ DONE marks + delivery summary + review-impl verdict — PASS means ship)
    ↓
ship
    ↓
git mv docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md docs/exec-plans/completed/
```

- `/orchestrate-plan <slug>`: create or refine `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` — TL drafts, Core Dev (implementation risk) and PO (scope + first principles) critique in parallel, TL decides. Includes inline current-state validation before drafting.
- `/orchestrate-dev <slug>`: execute from `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`, mark shipped tasks `✓ DONE` (never delete mid-delivery), append delivery summary to plan, auto-invoke sync-doc.
- `/review-impl <slug>`: deep self-correcting review — evidence-first spec check (file:line for every claim), adversarial self-check, auto-fix of blocking findings, full test suite with mandatory RCA, behavioral verification against running system. Appends pass/fail verdict to plan. **PASS means ship — no further gate needed.**
- `/sync-doc [doc...]`: fix spec inaccuracies in `docs/specs/` in-place. No args means all specs. Auto-invoked by `orchestrate-dev`.
- `/deliver [slug]`: lightweight solo delivery — implement a clear task directly, test-gate, self-review, and ship. No subagent orchestration. Use instead of `/orchestrate-dev` when the task is simple enough for a single-dev pass, or without a slug for ad-hoc work described inline.

## Docs

### Spec Conventions

Specs in `docs/specs/` are **post-implementation documentation** — they always stay in sync with the latest code and are the authoritative reference during planning. They must never appear as tasks in an exec-plan: spec updates are outputs of delivery, not inputs to it. All updates happen automatically through `/sync-doc` (auto-invoked by `orchestrate-dev` after delivery). Any task whose `files:` list includes a `docs/specs/` path is invalid and must be removed.

Every component spec follows this four-section template:

1. **What & How** — one paragraph + architecture diagram
2. **Core Logic** — processing flows, key functions, design decisions, error handling, security
3. **Config** — settings table (`Setting | Env Var | Default | Description`); skip if no configuration
4. **Files** — file table (`File | Purpose`)

Never paste source code into specs. Use pseudocode to explain processing logic. Pseudocode keeps docs readable, avoids staleness when code changes, and forces focus on intent over syntax.

Start at `docs/specs/system.md` for top-level system architecture, `CoDeps`, capability surface, and security boundaries. `docs/specs/core-loop.md` covers the agent loop, orchestration, and approval flow. All component specs live in `docs/specs/` and are named `<component>.md` and `flow-<component>.md`.

`flow-*.md` specs are sequence-owning documents. Their `Core Logic` section must follow execution order strictly from start to finish, introduce data structures at the step where they first matter, attach failure/degradation behavior to the relevant step, and avoid separate taxonomy sections that duplicate the flow.

`docs/reference/` is for research, proposals, and background material (`RESEARCH-*`, `ROADMAP-*`) and is not linked from specs.

### Artifact Lifecycle

- `REPORT-*.md` lives directly in `docs/`.
- `REPORT-<scope>.md` is permanent — only eval/benchmark/script runs produce these.
- Exec-plans live at `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` (creation date). Each plan tracks: plan content, `✓ DONE` marks (never delete mid-delivery), delivery summary, and review verdict. On Gate 2 PASS, use `git mv docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md docs/exec-plans/completed/` — never delete.

## Reference Repos

Peer repos in `~/workspace_genai/` are used for design research. See `docs/reference/RESEARCH-peer-personality.md` for personality research.

| Repo | Relevance to co-cli |
|------|---------------------|
| `fork-claude-code` | Agent CLI, tool approval, config, compaction, TUI |
| `hermes-agent` | Direct co-cli peer — agent CLI, REPL, streaming |
| `elizaos` | Character personality schema, tool policy layering, memory scoping |
| `letta` | Memory architecture (MemGPT-style) |
| `gemini-cli` | Agent CLI, config patterns, tool design |
| `opencode` | Agent CLI, tool patterns, config |
| `opensouls` | CognitiveStep + MentalProcess chaining (TypeScript, near-stale) |

### Research Rules

- **Correct repo targeting**: when comparing peer repos, always use `fork-claude-code` (at `~/workspace_genai/fork-claude-code`), not the public `claude-code`. Confirm the exact path before reading anything. Do not proceed with analysis until repo paths are verified.
