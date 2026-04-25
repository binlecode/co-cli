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
scripts/quality-gate.sh full              # lint + pytest (pre-push hook + ship gate)

# ALL pytest runs MUST pipe to a timestamped log under .pytest-logs/ (mkdir -p first).
# Never truncate output before the log file (no | head, | tail, | grep before tee).
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log

# Evals: uv run python evals/eval_<name>.py  (ls evals/ for full list)
```

## System Overview

### Architecture

See `docs/specs/system.md` for architecture, `CoDeps`, capability surface, and security boundaries. See `docs/specs/core-loop.md` for agent loop internals, orchestration, and approval mechanics.

### Knowledge System

All knowledge is dynamic, loaded on-demand via tools, and never baked into the system prompt. Flat `~/.co-cli/knowledge/*.md` files with YAML frontmatter store knowledge artifacts (`kind: knowledge` with an `artifact_kind` subtype). FTS5 (BM25) search runs in `~/.co-cli/co-cli-search.db`. See `docs/specs/memory-knowledge.md` for the Memory + Knowledge model (transcripts, retrieval, extraction, dreaming) and `docs/specs/prompt-assembly.md` for how recall injects into the turn.

## Engineering Rules

### Code

- **Python 3.12+** with type hints everywhere.
- **Imports**: always explicit; never `from X import *`.
- **Comments**: no trailing comments; put comments on the line above, not at end of code lines.
- **`__init__.py`**: must be docstring-only (one-line module docstring or empty); never add imports, re-exports, or code. When converting a module to a package, all content goes into `_core.py` or named private submodules — never into `__init__.py`. Violating this causes circular imports and breaks the `_prefix.py` visibility contract.
- **`_prefix.py` helpers**: leading-underscore modules are package-private. If imported outside the package, drop the underscore.
- **Class naming**: names must reveal the class's role. Prefer the suffix conventions below where they fit. Self-evident named concepts (e.g. `ShellBackend`, `AgentLoop`) do not need a suffix.

  | Suffix | Meaning |
  |--------|---------|
  | `*State` | Mutable lifecycle data |
  | `*Result` | Immutable pass/fail outcome |
  | `*Output` | Agent/pipeline payload |
  | `*Settings` | Persisted configuration — top-level `Settings` and any nested submodel; maps to `settings.json` |
  | `*Config` | Runtime configuration — in-memory, not persisted (e.g. `SkillConfig` loaded from `.md`); **confirm with the user before introducing a new `*Config` name** |
  | `*Info` | Read-only descriptor |
  | `*Registry` | Registration lookup table |
  | `*Store` | Persistent storage layer |
  | `*Context` | Input bag for a call |
  | `*Event` | Async/streaming event |
  | `*Error` | Exception class |
  | `*Enum` | Enumeration |
- **Variable and function naming**: use descriptive names that reveal intent — including loop variables (e.g. `idx`, `key`, `val` over `i`, `k`, `v`). Well-known conventions (`fd`, `db`) are fine as-is.
- **Suffix preservation**: preserve existing suffix conventions (e.g. `*Registry`, `*Info`) unless explicitly told otherwise. Before proposing a rename, verify the new name against peer codebases and existing conventions in this repo.
- **Shared primitives**: for cross-cutting concerns, use the existing project primitive before adding another path. Config loading, console output, filesystem roots, tool outputs, approval flow, tracing, and test harnesses should each have one obvious implementation route.
- **Display**: use the project's shared `console` object for all terminal output. Use semantic style names; never hardcode color names at callsites.
- **Quality gates**: `scripts/quality-gate.sh` is the single source of truth for all automated checks. `lint` = ruff (pre-commit enforced), `full` = lint + pytest. Tool configs live in `pyproject.toml`. Never add `# noqa` or `# type: ignore` without a comment explaining why the tool is wrong for that line.

### Agents, Tools, and Config

- **Tool pattern**: native tools use `@agent_tool(...)` with `RunContext[CoDeps]`; `_build_native_toolset()` registers them with pydantic-ai. All runtime resources come from `ctx.deps` — never import settings directly. Never hold module-level state in tool files: tool modules are imported once and shared across all runs in the same process, so mutable globals cause test interference and session bleed. Never put approval prompts inside tools — that bypasses the deferred-approval mechanism and breaks approval-resume.
- **Tool approval**: tools that mutate system state (filesystem writes, external service writes, process spawning) use `approval=True` on `@agent_tool(...)`. Runtime-approval tools such as `shell` and `code_execute` may raise `ApprovalRequired` based on command policy. Read-only operations do not require approval. Approval UX lives in the chat loop.
- **Tool return type**: tools returning user-facing data must use the project's `tool_output()` helper for structured returns; use `tool_error()` for failures. Never return a raw `str`, bare `dict`, or `list[dict]` — raw returns silently omit tracing metadata and the structured fields the chat loop depends on.
- **CoDeps**: flat dataclass — access service handles, config, and paths via `ctx.deps.*` (e.g. `ctx.deps.shell`, `ctx.deps.config.memory.max_count`).
- **Sub-agent isolation**: use the subagent deps factory in `deps.py`. Do not manually field-copy.
- **Config**: `Settings` uses nested Pydantic sub-models in `co_cli/config/` (one file per group). Add new fields to an existing group if it fits; only create a new nested group when it has meaningful cohesion. Config precedence: env vars > `~/.co-cli/settings.json` > defaults. (No project-local `.co-cli/settings.json` layer exists today; all user state is user-global.)
- **User-global paths**: `~/.co-cli/` (overridable via `CO_HOME`). No project-local state directory exists.
- **Versioning**: `MAJOR.MINOR.PATCH`; patch odd = bugfix, even = feature. Bump only in `pyproject.toml`. Git history is the changelog; releases use GitHub Releases — tag `vX.Y.Z` and push to trigger `.github/workflows/release.yml`.
- **No `.env` files**: use `settings.json` or env vars.

#### Adding a Tool

- Tool file in `co_cli/tools/`; import and register in `_build_native_toolset()` in `co_cli/agent.py`.
- Return `tool_output()` / `tool_error()` — never a raw `str`, `dict`, or `list`.
- First docstring line is the tool schema description — make it count.
- `approval=True` for any tool that writes files, spawns processes, or calls external write APIs.
- `ALWAYS` visibility = present every turn; `DEFERRED` = discovered via search_tools on demand.

### Testing

> **These rules are enforced repository policy, not guidance.** Any test or test change that violates them must be fixed or removed before regression testing or merge.

#### Evals (`evals/`)

- **Evals are separate from tests**: evals run as standalone programs (`uv run python evals/eval_<name>.py`), not pytest. Pass/fail gates live inside the runner. Shared helpers belong in `evals/_*.py`, not in `co_cli/`.
- **Evals run against the real configured system**: never override config with `_ENV_DEFAULTS`, `os.environ`, or fallback defaults. If a prerequisite is missing, skip gracefully.
- **Evals never create their own model or agent settings**: all LLM call parameters (model, temperature, context window, reasoning mode) must come from the project's config and factory functions — never hardcoded inline. Use the production code paths as-is; do not substitute simplified settings to "speed up" or "simplify" the eval.
- **Evals must seek corners**: every eval must include at least one failure mode, degradation path, or boundary condition.

#### Tests (`tests/`)

- **Only pytest files in `tests/`**: `test_*.py` or `*_test.py`, using `pytest` + `pytest-asyncio`. Non-test scripts go in `scripts/`, evaluations in `evals/`.
- **Real dependencies only — no fakes**: never use `monkeypatch`, `unittest.mock`, `pytest-mock`, or hand-assembled domain objects that bypass production code paths. Use real `CoDeps` with real services, real SQLite, real filesystem, real FTS5. If a behavior cannot be tested without fakes, the production API is wrong — fix the API. `conftest.py` must be limited to neutral pytest plumbing (e.g. session-scoped markers, asyncio mode) — never shadow config or inject substitutes.
- **Behavior over structure**: tests must drive a production code path and assert on observable outcomes — return values, persisted state, emitted events, raised errors, side effects. Do not assert on facts Python or the import system already enforce: file/directory layout, module importability, class or attribute presence, registration tables, type annotations, or docstrings. If a test would still pass after gutting the function body to `pass` (or `return None`), it is structural — rewrite it to exercise behavior or delete it. The test name should describe *what the code does*, not *how it is arranged*.
- **IO-bound timeouts**: wrap each individual `await` to external services (LLMs, network, subprocess) with `asyncio.timeout(N)` — including warmup and preflight awaits. Never wrap multiple sequential awaits in one block. Import constants from the test timeouts module — never hardcode inline. Never increase a timeout to make a test pass — a timeout violation means wrong role, wrong agent context, or wrong model config. Diagnose the root cause, fix the test or config.
- **Suite hygiene**: every test must target a real failure mode — ask "if deleted, would a regression go undetected?" Tests must pass or fail (no skips except credential-gated external integrations via `pytest.mark.skipif`). Remove or update stale tests when changing public APIs — do not skip them. Any policy violation blocks the full run. `pyproject.toml` enforces `-x --durations=0`. Known anti-patterns that pass the deletion question but add no coverage:
  - *Fixture not wired*: `tmp_path` (or any injected fixture) in the signature but never passed to a production function — assertion trivially passes.
  - *Duplicate with trivial delta*: two tests dispatch the same function/command and assert the same invariant; the extra test adds only a trivially-true assertion (e.g. `result.flag is False` where False is the default).
  - *Truthy-only assertion*: `assert result.version` instead of `assert re.fullmatch(r"\d+\.\d+\.\d+", result.version)` — passes even if the value is wrong.
  - *Subsumed file*: an entire test file whose every test is a strict subset of tests in another file covering the same module.
- **Test data isolation**: use `tmp_path` for all filesystem writes. For shared stores, use `test-` prefix identifiers and delete in `try/finally` — cleanup failure must fail the test.
- **Scope pytest during implementation**: run only affected test files during dev (`uv run pytest tests/test_foo.py`). Run the full suite only before shipping. Never dismiss a failure as flaky — stop, diagnose, then fix.
- **Production config only — no overrides**: do not pass `model=` or `model_settings=` to `agent.run()` — use the production orchestration path or invoke the agent with no override. Do not strip personality in tests. Use non-thinking model settings for tool-calling, signal-detection, and orchestration tests. Cache module-level agents rather than rebuilding per call.
- **Never copy inline logic into tests**: do not replicate display formatting or string construction in assertions.
- **Google credentials**: never configure or inject — they resolve automatically via settings, `~/.co-cli/google_token.json`, or ADC.
- **No pytest markers**: do not add markers (e.g. `integration`, `slow`) unless explicitly requested.

### Review Discipline

- **Deep pass on first round**: read every function body, trace call paths, check for stale imports and dead code. Do not skim signatures or assume correctness from names.
- **Evidence-based verdicts**: do not declare "ready" unless you can cite `file:line` references. If zero issues found, list every file read and what was checked. If scope is unclear, ask rather than rubber-stamp.
- Always check `docs/reference/` for research/best-practice docs before reviews or design proposals.
- **Design philosophy**: design from first principles — MVP-first but production-grade. Add abstractions only when a concrete need exists, and simplify any implementation or abstraction that is hard to explain in one short paragraph unless the complexity is forced by an external contract. When researching peers, focus on convergent best practices, not volume.
- **Peer research verification**: when comparing against peer tools, always confirm the correct repo/source before reading. Do a deep code scan (grep/read) to verify claimed gaps exist — do not report features as missing without evidence.

### Code Change Principles

- Prefer fail-fast over redundant fallbacks. Clean up dead code during implementation, not as a separate pass.
- Do not swallow foreground or user-visible errors with broad `except`, empty handlers, or log-and-continue paths. Let unexpected errors propagate. Convert expected non-fatal conditions into typed project-standard results or exceptions with actionable context; for tool failures, use `tool_error()`. Background cleanup, shutdown, and best-effort degradation paths may log and continue only when failing the main operation would be worse than losing the auxiliary work.
- When ambiguity affects behavior, persistence, security, approval, or public API shape, stop and surface the assumption. For low-risk local implementation details, make the smallest coherent assumption and state it in the delivery summary.
- After renames or file moves: (1) grep for ALL remaining references to the old name across the whole repo, (2) check test imports specifically — they are the most common miss, (3) run the full test suite. Done only when grep finds zero stale references AND tests pass.

### Known Pitfalls

**DO NOT hardcode `~/.co-cli` or `Path.home() / ".co-cli"` in source code.**
Use `USER_DIR` and derived constants (`SETTINGS_FILE`, `SEARCH_DB`, etc.) from `co_cli/config/_core.py`. Tests override `CO_HOME` to a temp dir — hardcoded paths bypass this and bleed state across test runs.

## Workflow

### Working with Claude Code

- When interrupted or redirected, immediately stop the current approach and follow the new direction — do not continue previous work or expand scope.
- When asked to analyze or review, confirm the approach before searching, fetching, or writing.
- When asked to append to an existing doc, never create a new file instead.
- Never add unsolicited notes, reminders, or meta-commentary to outputs unless explicitly asked.
- **Subagents**: declare tool permissions upfront (Read, Edit, Bash, Grep). Each subagent cleans up dead code before returning. After all finish, do an integration review for stale imports and orphaned references.
- Keep plans concise and actionable — resist over-engineering. If the user pushes back on complexity, simplify immediately rather than defending the design.

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
Dev: /orchestrate-dev <slug>              (implement + self-review + lint + scoped tests + sync-doc → delivery summary appended to plan)
    ↓
TL: /review-impl <slug>                   (evidence-first scan + auto-fix + full tests + behavioral verification → verdict appended to plan)
    ↓
👤  Gate 2: TL reads plan                 (plan + ✓ DONE marks + delivery summary + review-impl verdict — PASS means ship)
    ↓
/ship <slug>                              (full tests safety net + version bump + plan archive + commit)
```

- `/orchestrate-plan <slug>`: create or refine `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` — TL drafts, Core Dev (implementation risk) and PO (scope + first principles) critique in parallel, TL decides. Includes inline current-state validation before drafting.
- `/orchestrate-dev <slug>`: execute from `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`, mark completed tasks `✓ DONE` (never delete mid-delivery), append delivery summary to plan, auto-invoke sync-doc.
- `/review-impl <slug>`: deep self-correcting review — evidence-first spec check (file:line for every claim), adversarial self-check, auto-fix of blocking findings, full test suite with mandatory RCA, behavioral verification against running system. Appends pass/fail verdict to plan. **PASS means ship — no further gate needed.**
- `/sync-doc [doc...]`: fix spec inaccuracies in `docs/specs/` in-place. No args means all specs. Auto-invoked by `orchestrate-dev`.
- `/deliver [slug]`: lightweight solo delivery — implement a clear task directly, test-gate, self-review, and ship. No subagent orchestration. Use instead of `/orchestrate-dev` when the task is simple enough for a single-dev pass, or without a slug for ad-hoc work described inline.
- `/ship [slug]`: post-Gate-2 ship — full test safety net, version bump, plan archive (`git mv` to `completed/`), commit. Run after Gate 2 PASS or after ad-hoc work outside the plan flow.
- **Staged-file hygiene**: before shipping, verify only related files are staged — never include unrelated changes. Ask the user before staging any file that seems tangential to the task.

## Docs

### Spec Conventions

Specs in `docs/specs/` are **living requirements and progress-tracking documents** — they define intent, track development milestones, and stay in sync with the latest code. Every spec has a human-maintained `## Product Intent` section (Goal, Functional areas, Non-goals, Success criteria, Status, Known gaps) followed by four implementation sections. `/sync-doc` keeps sections 1–4 accurate against code but never touches `## Product Intent`. Specs must never appear as tasks in an exec-plan: spec updates are outputs of delivery, not inputs to it. Any task whose `files:` list includes a `docs/specs/` path is invalid and must be removed.

Every spec follows the `## Product Intent` / `## 1. What & How` / `## 2. Core Logic` / `## 3. Config` / `## 4. Files` structure. Use pseudocode, never source code. Sequence-owning specs (`bootstrap.md`, `core-loop.md`, flow diagrams in `compaction.md`, `prompt-assembly.md`, `memory-knowledge.md`) follow execution order strictly — no separate taxonomy sections that duplicate the flow.

Specs live in `docs/specs/` — one file per subsystem.

`docs/reference/` is for research and background material (`RESEARCH-*`) and is not linked from specs.

### Artifact Lifecycle

- `REPORT-*.md` lives directly in `docs/`.
- `REPORT-<scope>.md` is permanent — only eval/benchmark/script runs produce these.
- Exec-plans live at `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` (creation date). Each plan tracks: plan content, `✓ DONE` marks (never delete mid-delivery), delivery summary, and review verdict. On Gate 2 PASS, run `/ship <slug>` — it handles plan archiving (`git mv` to `completed/`) as part of the commit. Never delete a plan.

## Reference Repos

Peer repos in `~/workspace_genai/` are used for design research. See `docs/reference/RESEARCH-peer-personality-survey.md` for personality research.

| Repo | Relevance to co-cli |
|------|---------------------|
| `fork-claude-code` | Agent CLI, tool approval, config, compaction, TUI |
| `hermes-agent` | Direct co-cli peer — agent CLI, REPL, streaming |
| `elizaos` | Character personality schema, tool policy layering, memory scoping |
| `letta` | Memory architecture (MemGPT-style) |
| `gemini-cli` | Agent CLI, config patterns, tool design |
| `opencode` | Agent CLI, tool patterns, config |
| `opensouls` | CognitiveStep + MentalProcess chaining (TypeScript, near-stale) |
