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

Three-channel recall model: **artifacts** (persistent knowledge artifacts), **sessions** (past session transcripts), and **canon** (read-only character scenes). Static personality content (seed, mindsets, personality-context artifacts) is auto-injected into the system prompt; everything else is dynamic, loaded on-demand via tools, and never baked into the system prompt.

Flat `~/.co-cli/knowledge/*.md` files with YAML frontmatter store artifact entries (`kind: knowledge` with an `artifact_kind` subtype). FTS5 (BM25) search runs in `~/.co-cli/co-cli-search.db` (artifacts + Obsidian); session transcripts have a separate index at `~/.co-cli/session-index.db`. Implementation lives in `co_cli/memory/` (sessions and artifacts as co-equal kinds) with the unified tool surface in `co_cli/tools/memory/`. See `docs/specs/memory.md` for the full Memory model and `docs/specs/prompt-assembly.md` for how recall injects into the turn.

Four unified `memory_*` tools cover all channels:
- `memory_search` — recall across artifacts (BM25) + sessions (LLM-summarized) + canon in one call
- `memory_list` — paginated inventory of artifacts
- `memory_create` — save a new artifact (all kinds: preference, feedback, rule, article, reference, note, decision)
- `memory_modify` — append or surgically replace a passage in an existing artifact

Full-body artifact reads use the generic `file_read` tool against the path that
`memory_search` surfaces. Canon hits ship full body inline; sessions ship LLM summaries inline.

## Engineering Rules

- **Python 3.12+** with type hints everywhere.
- **Imports**: always explicit; never `from X import *`.
- **Comments**: no trailing comments; put comments on the line above, not at end of code lines.
- **`__init__.py`**: must be docstring-only (one-line module docstring or empty); never add imports, re-exports, or code. When converting a module to a package, all content goes into `_core.py` or named private submodules — never into `__init__.py`. Violating this causes circular imports and breaks the `_prefix.py` visibility contract.
- **`_prefix.py` helpers**: leading-underscore modules are package-private. If imported outside the package, drop the underscore.
- **Quality gates**: `scripts/quality-gate.sh` is the single source of truth. Tool configs in `pyproject.toml`. Never add `# noqa` or `# type: ignore` without a comment explaining why the tool is wrong for that line.

**Known Pitfall — DO NOT hardcode `~/.co-cli`**: use `USER_DIR` and derived constants from `co_cli/config/core.py`. Tests override `CO_HOME` to a temp dir — hardcoded paths bypass this and bleed state across test runs.

<important if="you are writing or modifying Python code (naming, classes, display)">
See `agent_docs/code-conventions.md` — class naming suffixes, variable naming, shared primitives, display
</important>

<important if="you are writing, modifying, or reviewing tools or agents">
See `agent_docs/tools.md` — tool pattern, approval, return types, CoDeps, config, versioning, adding a tool
</important>

<important if="you are writing, modifying, or reviewing tests or evals">
See `agent_docs/testing.md` — pytest and eval rules (enforced policy)
</important>

<important if="you are reviewing code or planning implementation changes">
See `agent_docs/review.md` — review discipline and code change principles
</important>

<important if="you are writing or modifying specs, or working with exec-plan artifacts">
See `agent_docs/spec-conventions.md` — spec structure, section rules, artifact lifecycle
</important>

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
- `/orchestrate-dev <slug>`: execute from plan, mark completed tasks `✓ DONE` (never delete mid-delivery), append delivery summary, auto-invoke sync-doc.
- `/review-impl <slug>`: deep self-correcting review — evidence-first spec check (file:line for every claim), adversarial self-check, auto-fix of blocking findings, full test suite with mandatory RCA, behavioral verification. Appends pass/fail verdict. **PASS means ship.**
- `/sync-doc [doc...]`: fix spec inaccuracies in `docs/specs/` in-place. No args means all specs. Auto-invoked by `orchestrate-dev`.
- `/deliver [slug]`: lightweight solo delivery — implement directly, test-gate, self-review, ship. No subagent orchestration. Use instead of `/orchestrate-dev` when the task is simple enough for a single-dev pass, or without a slug for ad-hoc work described inline.
- `/ship [slug]`: post-Gate-2 ship — full test safety net, version bump, plan archive (`git mv` to `completed/`), commit.
- `/test-hygiene [path]`: standalone test quality gate — enforce `agent_docs/testing.md` rules, purge structural/redundant tests, verify behavioral depth, run full suite. Default path: `tests/`. Call any time, not just pre-ship.
- **Staged-file hygiene**: before shipping, verify only related files are staged — never include unrelated changes. Ask the user before staging any file that seems tangential to the task.

## Docs

See `agent_docs/spec-conventions.md` for full spec structure and artifact lifecycle rules.

Quick reference:
- Exec-plans: `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` — archive to `completed/` on ship, never delete.
- `REPORT-*.md`: permanent, lives in `docs/`, only produced by eval/benchmark/script runs.
- `docs/reference/`: research and background material (`RESEARCH-*`), not linked from specs.

## Reference Repos

Peer repos in `~/workspace_genai/` are used for design research. See `docs/reference/RESEARCH-personality-peer-survey.md` for personality research.

| Repo | Relevance to co-cli |
|------|---------------------|
| `fork-claude-code` | Agent CLI, tool approval, config, compaction, TUI |
| `hermes-agent` | Direct co-cli peer — agent CLI, REPL, streaming |
| `elizaos` | Character personality schema, tool policy layering, memory scoping |
| `letta` | Memory architecture (MemGPT-style) |
| `gemini-cli` | Agent CLI, config patterns, tool design |
| `opencode` | Agent CLI, tool patterns, config |
| `opensouls` | CognitiveStep + MentalProcess chaining (TypeScript, near-stale) |
