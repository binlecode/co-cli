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
uv run co tail                   # Stream agent spans in real time (tail -f the JSON spans log)
uv run co tail --detail          # Append per-record detail block (input/output/args/result)
uv run co trace <trace_id>       # Snapshot tree of one trace from the structured-log spans file

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

See `docs/specs/01-system.md` for architecture, `CoDeps`, capability surface, and security boundaries. See `docs/specs/core-loop.md` for agent loop internals, orchestration, and approval mechanics.

### Memory and Session

Five operational tiers: **doctrine** (canon, soul seed, mindsets — auto-injected via the personality system; never queryable), **tools** (callable primitives), **skills** (procedural capability), **memory** (long-term declarative memory items: user preferences, rules, articles, notes), and **session** (past conversation transcripts; FTS5 chunk-cited on recall).

Storage: flat `~/.co-cli/memory/*.md` files with YAML frontmatter for memory items; JSONL transcripts in `~/.co-cli/sessions/`; FTS5 (BM25) search in `~/.co-cli/co-cli-search.db` indexed by the shared `IndexStore` infrastructure facade (memory + session + canon, source-discriminated). Canon is indexed there for personality auto-injection but never returned by any model-callable tool. Skills are discovered via the `<available_skills>` manifest injected into the static prompt.

Architecture: `co_cli/index/` (infrastructure facade — `IndexStore` public; retrieval, embedding, providers private) sits below two domain modules `co_cli/memory/` (`MemoryStore` — kinds, decay, dream) and `co_cli/session/` (`SessionStore` — transcripts, append-only). Tool surface at `co_cli/tools/memory/` (`memory_search`, `memory_view`, `memory_manage`) and `co_cli/tools/session/` (`session_search`, `session_view`).

Recall is search-driven: there is no `memory_list` or `memory_read` tool. Browse with `memory_search` / `session_search` (empty query is fine); full-body reads use `memory_view(name)` with the `filename_stem` from a search hit, or `session_view(session_id, start_line, end_line)` for verbatim turns. See `docs/specs/memory.md`, `docs/specs/sessions.md`, `docs/specs/skills.md`, `docs/specs/personality.md`, and `docs/specs/prompt-assembly.md` for the full model.

## Engineering Rules

- **Python 3.12+** with type hints everywhere.
- **Imports**: always explicit; never `from X import *`.
- **Comments**: no trailing comments; put comments on the line above, not at end of code lines.
- **`__init__.py`**: must be docstring-only (one-line module docstring or empty); never add imports, re-exports, or code. When converting a module to a package, all content goes into `_core.py` or named private submodules — never into `__init__.py`. Violating this causes circular imports and breaks the `_prefix.py` visibility contract.
- **`_prefix.py` helpers**: leading-underscore modules are package-private. If imported outside the package, drop the underscore.
- **Quality gates**: `scripts/quality-gate.sh` is the single source of truth. Tool configs in `pyproject.toml`. Never add `# noqa` or `# type: ignore` without a comment explaining why the tool is wrong for that line.

- **Surgical changes**: touch only what the task requires. Do not improve adjacent code, fix unrelated style, or refactor things not broken. Match existing style even if you'd do it differently. The only dead code you remove is orphans *your* changes created — pre-existing dead code gets mentioned, not deleted.

**Known Pitfall — DO NOT hardcode `~/.co-cli`**: use `USER_DIR` and derived constants from `co_cli/config/core.py`. Tests override `CO_HOME` to a temp dir — hardcoded paths bypass this and bleed state across test runs.

<important if="you are writing or modifying Python code (naming, classes, display)">
See `.agent_docs/code-conventions.md` — class naming suffixes, variable naming, shared primitives, display
</important>

<important if="you are writing, modifying, or reviewing tools or agents">
See `.agent_docs/tools.md` — tool pattern, approval, return types, CoDeps, config, versioning, adding a tool
</important>

<important if="you are writing, modifying, or reviewing tests or evals">
See `.agent_docs/testing.md` — pytest and eval rules (enforced policy)
</important>

<important if="you are reviewing code or planning implementation changes">
See `.agent_docs/review.md` — review discipline and code change principles
</important>

<important if="you are writing or modifying specs, or working with exec-plan artifacts">
See `.agent_docs/spec-conventions.md` — spec structure, section rules, artifact lifecycle
</important>

## Workflow

### Working with Claude Code

- **Subagents**: declare tool permissions upfront (Read, Edit, Bash, Grep). See `.agent_docs/review.md` for cleanup and integration-review rules.

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

Non-obvious nuances:
- `/orchestrate-dev` marks completed tasks `✓ DONE` (never deletes mid-delivery) and auto-invokes `/sync-doc`.
- `/review-impl` PASS verdict means TL reads it at Gate 2 and ships — no extra review pass.
- `/clean-tests [path]` is callable any time, not just pre-ship.
- For atomic/single-file changes, use Claude Code's built-in plan flow directly — no skill needed.
- **Staged-file hygiene**: before shipping, verify only related files are staged. Ask before staging any file that seems tangential.

## Docs

See `.agent_docs/spec-conventions.md` for full spec structure and artifact lifecycle rules.

Quick reference:
- Exec-plans: `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` — archive to `completed/` on ship, never delete.
- `REPORT-*.md`: permanent, lives in `docs/`, only produced by eval/benchmark/script runs.
- `docs/reference/`: research and background material (`RESEARCH-*`), not linked from specs.

## Reference Repos

Peer repos in `~/workspace_genai/` are used for design research. See `docs/reference/RESEARCH-peer-repos.md` for the table.
