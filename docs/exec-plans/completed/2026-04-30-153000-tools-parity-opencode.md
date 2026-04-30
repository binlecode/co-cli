Task type: code-feature

# Plan: Tool Parity & Implementation Gap Analysis against Opencode

## Context
A review of `co-cli`'s agent tools against `opencode`'s system capabilities reveals significant functional parity. Both systems provide core capabilities for file manipulation, shell execution, user clarification, task delegation, and web fetching. However, a deeper look into the tools that share parity reveals specific implementation gaps where `co-cli`'s tools are either more restrictive or lack quality-of-life features present in `opencode`. 

## Problem & Outcome
**Problem:** While `co-cli` matches `opencode` in core tool categories, the implementation gaps limit the agent's efficiency and user experience. For example, `co-cli`'s shell execution lacks directory scoping (`workdir`), the `clarify` tool cannot batch questions or handle multi-select, and `web_fetch` enforces hardcoded formatting.
**Failure cost:** Ephemeral shell sessions without `workdir` force the agent into awkward command chaining (e.g. `cd src && npm run test`). The limited `clarify` tool requires multiple conversational turns to gather complex user preferences.
**Outcome:** `co-cli` tools are upgraded to close the implementation gaps with `opencode`, resulting in cleaner shell execution, richer interactive prompts, and more flexible data ingestion.

## Scope
This plan targets the implementation gaps for tools that already share functional parity:

1. **User Input (`clarify` vs `question`):**
   - Refactor `clarify` to accept a structured list of questions rather than a single string.
   - Introduce support for multi-select options (`multiple: bool`).
   - Add support for rich options (label + description) instead of bare strings.
   - **Opencode parity:** `question` tool takes `questions: array` in a single call; each question is `{question: str, header: str, options: [{label, description}] | None, multiple?: bool, custom?: bool}`; returns `answers: string[][]`. Python mapping simplifies to `list[str]` — one string per question, comma-joined for multi-select — avoiding the mixed-type complexity of the TypeScript model. co-cli's CLI renders sequentially via `questionary` rather than a tabbed TUI.
2. **Shell Execution (`shell` vs `bash`):**
   - Add a `workdir` parameter to the `shell` tool to allow execution in a specific directory relative to the workspace root.
   - (Note: Persistent shell sessions are deferred to a separate architecture plan).
3. **Web Fetch (`web_fetch` vs `webfetch`):**
   - Add a `format` parameter (`text`, `markdown`, `html`) defaulting to `markdown`.
   - Add an optional `timeout` parameter to allow the agent to wait longer for slow sites.
4. ~~**File Reading (`file_read` vs `read`):** Deferred — `start_line`/`end_line` with the built-in continuation hint is a coherent API; aligning to opencode's `limit`/`offset` naming is cosmetic churn with no behavioral gain. Cut from scope.~~

## Behavioral Constraints
- **Shell:** `workdir` must strictly resolve within the configured `workspace_root` to prevent directory traversal escapes.
- **Clarify:** The tool must gracefully fallback to a standard textual prompt if the CLI display layer does not support complex multi-select UI components (e.g. `Questionary` checkboxes).
- **Web Fetch:** Format switching must bypass `html2text` if `html` is requested, returning the raw decoded payload.

## High-Level Design
1. **`shell` Tool Upgrade:**
   - Update `co_cli/tools/shell.py` signature: `async def shell(ctx, cmd: str, timeout: int = 120, workdir: str | None = None)`.
   - Update `ShellBackend.run_command` in `co_cli/tools/shell_backend.py` to accept `cwd`. Import and reuse `_enforce_workspace_boundary` from `co_cli/tools/files/helpers.py`.
2. **`clarify` Tool Upgrade:**
   - Redefine arguments in `co_cli/tools/user_input.py` to accept `questions: list[dict]`, where each dict is `{question: str, options: list[{label: str, description: str}] | None, multiple: bool = False}`.
   - Update `QuestionRequired.__init__` in `co_cli/tools/approvals.py` to carry `questions: list[dict]` instead of `question: str, options: list[str] | None`.
   - The CLI renders questions sequentially via `questionary` (radio for single-select, checkbox for multi-select).
   - Return shape: `list[str]` — one string per question, positionally aligned to `questions`. Multi-select answers are comma-joined into a single string. Injected as `user_answers` via `override_args`.
   - Update the LLM-facing docstring: "one clarify call should collect all related questions" (replaces "call clarify exactly ONCE per question").
3. **`web_fetch` Tool Upgrade:**
   - Update `co_cli/tools/web/fetch.py` signature: `async def web_fetch(ctx, url: str, format: str = "markdown", timeout: int = 15)`.
   - Pass `timeout` to the HTTP client and conditionally apply `_html_to_markdown` based on `format`.

## Implementation Plan

- **✓ DONE — TASK-1: Upgrade `shell` with `workdir`**
  - **files:** `co_cli/tools/shell.py`, `co_cli/tools/shell_backend.py`, `tests/tools/test_shell.py`
  - **done_when:** `shell` successfully executes commands in a nested directory without requiring `cd`, and traversal attempts outside the workspace raise an error.
  - **prerequisites:** []
  - **note:** `_enforce_workspace_boundary` already exists in `co_cli/tools/files/helpers.py` — import it rather than reimplementing. `ShellBackend.run_command` already passes `cwd=self.workspace_dir`; add a `cwd` override param to thread the per-call `workdir` down to subprocess.

- **✓ DONE — TASK-2: Upgrade `web_fetch` with `format` and `timeout`**
  - **files:** `co_cli/tools/web/fetch.py`, `tests/tools/test_web.py`
  - **done_when:** `web_fetch` returns raw HTML when `format="html"` is requested, and timeout overrides are respected by the HTTP client.
  - **prerequisites:** []

- **✓ DONE — TASK-3: Refactor `clarify` for batched and multi-select questions**
  - **files:** `co_cli/tools/user_input.py`, `co_cli/tools/approvals.py`, `tests/approvals/test_user_input.py`
  - **done_when:** A batch clarify call renders all questions sequentially (radio for single-select, checkbox for multi-select) and returns `list[str]` — one string per question, comma-joined for multi-select — positionally aligned to the input `questions` list.
  - **prerequisites:** []

## Open Questions
- **Persistent Shell State:** `opencode` maintains a persistent shell session across bash calls (preserving `export`, `alias`, virtualenv activations). `co-cli` uses ephemeral subprocesses. Implementing a true persistent PTY backend in Python is highly complex and error-prone across OSs. Should we pursue a persistent shell backend, or just rely on `workdir` and command chaining?
- **Skill Tool Injection:** `opencode` has a dedicated `skill` tool for the agent to load capabilities dynamically. In `co-cli`, skills are primarily user-driven (`/orchestrate-dev`). Should we add an autonomous `load_skill` tool?

## Final — Team Lead
> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `shell` executes in nested dir without `cd`; traversal blocked | ✓ pass |
| TASK-2 | `web_fetch` returns raw HTML for `format="html"`; short timeout returns error | ✓ pass |
| TASK-3 | Batch clarify returns JSON `list[str]` positionally aligned to questions | ✓ pass |

**Extra files touched:**
- `co_cli/context/orchestrate.py` — clarify dispatch iterates questions, injects `user_answers`
- `co_cli/display/core.py` — `QuestionPrompt` gains `multiple: bool`; `RichFrontend.prompt_question` handles multi-select

**Tests:** scoped (touched files) — 62 passed, 0 failed
**Doc Sync:** fixed (`tools.md`: clarify/web_fetch/shell signatures + flow; `core-loop.md`: clarify dispatch steps)

**Overall: DELIVERED**
All three tool upgrades shipped: `shell` now supports workspace-relative `workdir` with traversal protection; `web_fetch` exposes `format` (markdown/html/text) and per-call `timeout`; `clarify` refactored to accept a batch `questions` list and return a positionally-aligned JSON `list[str]` with multi-select support.
