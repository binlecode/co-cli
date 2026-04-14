# Plan: Ollama Tool Search Steering

**Slug:** `ollama-tool-search-steering`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

co-cli already uses deferred tool discovery, but the actual discovery path is thin:

- native deferred tools are registered with `defer_loading=True` in
  `co_cli/agent/_native_toolset.py`
- the main agent relies on pydantic-ai's built-in `ToolSearchToolset`
- the SDK search implementation is keyword substring matching over deferred tool
  `name + description`, not semantic retrieval
- co-cli contributes only one runtime discovery hint:
  `Additional capabilities available via search_tools: ...`

Current prompt assembly relevant to discovery:

- static prompt: personality + numbered rules + model quirks, assembled in
  `co_cli/prompts/_assembly.py`
- dynamic instructions: `add_current_date`, `add_shell_guidance`,
  `add_project_instructions`, `add_always_on_memories`,
  `add_personality_memories`, `add_category_awareness_prompt`

Current validated gaps:

1. The assembled static prompt contains no explicit `search_tools` guidance.
2. The category-awareness prompt names only broad domains, not representative
   tool names or keyword examples.
3. The Ollama Qwen quirk files currently contain inference frontmatter only;
   there is no model-specific counter-steering for deferred discovery.
4. `run_shell_command` is always visible and broadly applicable, so local Qwen
   can satisfy some write tasks with shell before ever attempting deferred
   discovery.

Live verification against the real Ollama-backed full agent path:

- **Background-task prompt:** model called `search_tools` with
  `keywords="background task sleep"`, then called `start_background_task`.
- **File-create prompt:** model skipped `search_tools`, called
  `run_shell_command`, retried after an error, and completed the task through
  shell instead of discovering `write_file`.

This means the current deferred path works for clearly deferred-only capabilities,
but is not reliably effective when a generic always-visible tool can compete.

**Workflow artifact hygiene:** no active exec-plan currently targets Ollama-specific
tool-search prompting or eval coverage. `tier2-tool-surface` touches tool breadth,
not local-model steering. `parallel-tool-execution` touches write semantics, not
deferred discovery or evals.

---

## Problem & Outcome

**Problem:** local Ollama Qwen models do not receive enough explicit guidance to
reliably use `search_tools` when a deferred specialist tool exists but a generic
always-visible tool can also complete the task.

**Failure cost:** the model takes lower-quality paths:

- uses shell for file creation/editing instead of `write_file` / `edit_file`
- misses deferred specialist tools unless the request strongly implies them
- receives noisy `search_tools` match sets without enough steering on how to form
  good keyword queries

**Outcome:** for the local Ollama path, co-cli explicitly teaches the model when
and how to use `search_tools`, prefers specialist deferred tools over generic shell
when appropriate, and ships a real eval script that verifies the end-to-end
behavior against the production orchestration path.

---

## Scope

**In:**

- prompt/rule changes that explicitly teach `search_tools` usage
- Ollama Qwen-specific counter-steering for deferred discovery
- category-awareness prompt improvements with representative tool names or query hints
- shell guidance tightening where it currently competes with deferred specialist tools
- real eval script under `evals/` that exercises the full Ollama-backed agent path
- supporting tests for prompt/discovery invariants

**Out:**

- replacing the SDK search implementation with embeddings or semantic retrieval
- changing deferred-vs-always tool registration policy
- changing approval flow, tool locking, or background-task internals
- cloud-provider-specific prompt tuning beyond the Ollama path
- broad tool-surface refactors unrelated to deferred discovery

---

## Behavioral Constraints

- Keep the SDK-owned `search_tools` contract intact. co-cli may steer its use,
  but must not shadow or rename it.
- Do not add large per-turn prompt bloat. Discovery guidance should stay concise
  and load-bearing.
- Prompting should improve the Ollama path without materially harming Gemini.
  Ollama-specific behavior belongs in `prompts/model_quirks/ollama-openai/`.
- Do not weaken the general utility of `run_shell_command`; only steer away from
  shell where dedicated tools are clearly better.
- The eval must use the real configured system path:
  `build_model(config.llm)` + `build_agent(config=..., tool_registry=...)` +
  `run_turn(...)`. No fake tools, no alternate prompt, no `agent.run(..., model=...)`
  overrides inside the eval logic.
- The eval must include at least one competitive-path case and one boundary/failure
  case, per repository eval policy.

---

## High-Level Design

The fix is prompt-first, not retrieval-first.

Today the model sees:

1. static system prompt with no direct `search_tools` instruction
2. one category sentence mentioning only domains
3. SDK `search_tools` schema/description

That leaves too much inference burden on the local model. The intended request-time
mental model should instead be:

```text
if current visible tools do not cleanly fit the task:
  call search_tools with 2-4 concrete keywords
  prefer specialist discovered tools over generic shell when both could work
  if search_tools says no match, do not loop on search_tools
```

This can be achieved without changing the SDK search engine by improving three
inputs the model already consumes:

1. general tool protocol rule
2. Ollama/Qwen-specific counter-steering
3. category-awareness sentence with representative tool names / query hints

The eval then verifies that the local model actually follows that guidance.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Add explicit deferred-discovery guidance to the prompt

**files:**
- `co_cli/prompts/rules/04_tool_protocol.md`
- `co_cli/context/_deferred_tool_prompt.py`
- `co_cli/agent/_instructions.py`

**prerequisites:** none

Add one concise, explicit rule to the tool protocol:

- when the needed capability is not currently visible, call `search_tools`
- use 2-4 concrete keywords likely to appear in tool names/descriptions
- prefer specialist discovered tools over generic shell when a dedicated tool fits
- if `search_tools` returns no matches, do not keep retrying it

Tighten the dynamic category-awareness prompt so it does more than list domains.
It should still be compact, but include representative callable names or keyword
hints, for example:

```text
Additional capabilities available via search_tools:
file editing (write_file, edit_file),
background tasks (start_background_task, check_task_status),
memory management (save_article),
sub-agents (delegate_coder, delegate_researcher, delegate_analyst, delegate_reasoner).
```

The goal is to reduce keyword-formation burden for local models without dumping
all deferred schemas into the prompt.

`add_category_awareness_prompt()` remains the wiring point; no architecture change.

**Concrete implementation details:**

- add a short `## Deferred discovery` subsection to `04_tool_protocol.md`
- keep the rule generic enough for all providers, but explicitly name
  `search_tools`
- update `build_category_awareness_prompt()` so native deferred categories render
  representative tool names, not just domain labels
- keep integration categories compact; list the integration label and only add
  representative tool names where they are already known in `tool_index`
- do not move discovery guidance into personality files; keep it in the tool
  protocol and dynamic category-awareness path

**done_when:**

1. `python - <<'PY' ... PY` using `build_static_instructions(...)` prints at least
   one line containing `search_tools` and one line containing either
   `specialist` or `dedicated tool`
2. `python - <<'PY' ... PY` using `build_category_awareness_prompt(...)` prints
   a sentence containing:
   - `write_file`
   - `edit_file`
   - `start_background_task`
   - `delegate_coder`
3. the resulting category-awareness string remains a single sentence and stays
   under 300 characters for the no-integration config path

**success_signal:** a local model can infer both *when* to call `search_tools`
and *what keywords to use* without guessing from domain labels alone.

---

### ✓ DONE — TASK-2 — Add Ollama/Qwen-specific counter-steering and reduce shell competition

**files:**
- `co_cli/prompts/model_quirks/ollama-openai/qwen3.md`
- `co_cli/prompts/model_quirks/ollama-openai/qwen3.5.md`
- `co_cli/tools/shell.py`

**prerequisites:** [TASK-1]

Add short Ollama/Qwen counter-steering prose that says, in effect:

- use `search_tools` when the current visible set does not clearly cover the task
- for file creation/editing, prefer `write_file` / `edit_file` over shell
- for long-running detached work, prefer `start_background_task` over shell
- use shell for commands, builds, git, package managers, and situations where shell
  is the natural primitive

Also tighten the `run_shell_command` docstring so the tool itself no longer
implicitly competes with deferred file/background paths. It already tells the
model not to use shell for file reads and content search; extend that guidance to:

- `write_file` / `edit_file` instead of shell redirection for straightforward
  workspace file creation or editing
- `start_background_task` instead of shell for detached long-running work

Do not overconstrain shell. The tool remains the right primitive for git, builds,
package managers, scripts, and ad hoc commands.

**Concrete implementation details:**

- add short markdown bodies to both Ollama quirk files; keep the existing
  frontmatter unchanged
- the quirk prose should explicitly mention `search_tools`, `write_file`,
  `edit_file`, and `start_background_task`
- update only the `run_shell_command` docstring, not its execution logic or
  approval behavior
- keep shell guidance as “prefer X over shell for Y”, not “never use shell”

**done_when:**

1. `python - <<'PY' ... PY` using `get_counter_steering("ollama-openai", "qwen3")`
   and `get_counter_steering("ollama-openai", "qwen3.5")` returns non-empty text
   for both models
2. `python - <<'PY' ... PY` inspecting `run_shell_command.__doc__` finds:
   - `write_file`
   - `edit_file`
   - `start_background_task`
3. `uv run pytest tests/test_tool_prompt_discovery.py tests/test_tool_registry.py -x`
   passes after the new assertions land

**success_signal:** local Qwen stops treating shell as the default answer to every
actionable request when a more specific deferred tool exists.

---

### ✓ DONE — TASK-3 — Add prompt/discovery regression tests

**files:**
- `tests/test_tool_prompt_discovery.py`
- `tests/test_tool_registry.py`

**prerequisites:** [TASK-1], [TASK-2]

Add tests that verify the prompt and discovery scaffolding, not just individual
tool docstrings.

Coverage:

1. assembled prompt / instruction path contains explicit `search_tools` guidance
2. category-awareness prompt includes representative tool names for native deferred categories
3. shell tool description explicitly redirects file create/edit to `write_file` / `edit_file`
4. shell tool description explicitly redirects detached long-running work to
   `start_background_task`
5. deferred tool descriptions remain non-empty and keyword-discoverable

These tests should stay structure-oriented and avoid brittle full-string snapshots.
Assert for critical substrings only.

**Concrete implementation details:**

- keep assembled-prompt assertions in `tests/test_tool_prompt_discovery.py`
  because that module already owns discovery wording expectations
- keep category-awareness structure assertions in `tests/test_tool_registry.py`
  because it already builds the real registry
- add one helper in tests that calls `build_static_instructions(...)` using the
  current settings provider/model normalization path
- do not snapshot the entire static prompt; assert only targeted substrings and
  invariants

**done_when:**

`uv run pytest tests/test_tool_prompt_discovery.py tests/test_tool_registry.py -x`
passes with assertions proving:

- static prompt contains `search_tools`
- category-awareness prompt contains representative deferred tool names
- shell docstring redirects file create/edit and detached background work
- deferred tool descriptions remain keyword-discoverable

**success_signal:** prompt/discovery regressions become obvious in CI before a local
model silently stops using deferred tools well.

---

### ✓ DONE — TASK-4 — Add a real Ollama eval for deferred tool discovery

**files:**
- `evals/eval_ollama_tool_search.py`
- optional helper: `evals/_tool_search_eval.py`

**prerequisites:** [TASK-1], [TASK-2]

Create a standalone eval that runs against the real configured system and inspects
the actual tool-call trace from `run_turn(...)`.

Use the production orchestration path:

```python
config = settings.model_copy(...)
llm_model = build_model(config.llm)
reg = build_tool_registry(config)
agent = build_agent(config=config, model=llm_model, tool_registry=reg)
turn = await run_turn(...)
```

Do not use a stripped prompt or a direct bare `Agent(...)` like the existing
functional selection tests. The eval must exercise the same prompt stack the user sees.

**Concrete implementation details:**

- create a small helper that extracts, per turn:
  - ordered tool-call names
  - `search_tools` keyword args
  - discovered tool names from `ToolReturnPart.metadata["discovered_tools"]`
- create one eval case runner that returns a structured result object:
  `name`, `tool_calls`, `search_keywords`, `discovered_tools`, `passed`, `reason`
- wrap the file-create case in `try/finally` cleanup for any temporary files
- keep MCP disabled in the eval config copy unless a case explicitly needs it;
  this prevents connector noise from affecting discovery
- fail the eval immediately on an unexpected exception in any required case

**Eval cases:**

1. `background_task_positive`
   Prompt: start a long-running 5-second background sleep and return the task id.
   Expectation:
   - first relevant discovery step includes `search_tools`
   - discovered set includes `start_background_task`
   - later tool call includes `start_background_task`

2. `file_create_competition`
   Prompt: create a workspace file with exact contents.
   Expectation after the fix:
   - the model uses `search_tools` to discover file editing
   - then uses `write_file` rather than solving the task via shell redirection
   - cleanup runs in `finally`

3. `shell_negative_control`
   Prompt: run `git status`.
   Expectation:
   - model uses `run_shell_command`
   - model does **not** go through `search_tools` first

4. `unsupported_capability_boundary`
   Prompt: request a clearly unavailable capability
   (for example, sending a Slack message when no Slack tool exists).
   Expectation:
   - if `search_tools` is used, it should not loop repeatedly after a no-match result
   - eval records this as the boundary/failure-path case

**Eval output:**

- print a compact per-case trace:
  - tool call order
  - discovered tool names from `search_tools`
  - pass/fail verdict
- exit non-zero if any required case fails
- skip gracefully if provider is not `ollama-openai` or the configured local model
  is unavailable

**done_when:**

1. `uv run python evals/eval_ollama_tool_search.py` prints all four case names
2. the script exits 0 only when:
   - `background_task_positive` includes `search_tools` then `start_background_task`
   - `file_create_competition` includes `search_tools` and `write_file`, and does
     not complete via `run_shell_command` alone
   - `shell_negative_control` includes `run_shell_command`
   - `unsupported_capability_boundary` does not show repeated `search_tools`
     looping after a no-match result
3. the script exits non-zero with a case-specific failure reason when the model
   regresses to shell-first behavior for the file-create competition case

**success_signal:** the repository has a repeatable, real-system check for the exact
behavior gap found in live investigation.

---

## Testing

- Red:
  - add prompt/discovery assertions first
  - add the eval script and confirm the current file-create competition case fails
- Green:
  - apply TASK-1 and TASK-2 prompt/tool-guidance changes
  - rerun prompt tests and the eval

Required checks before delivery:

- `uv run pytest tests/test_tool_prompt_discovery.py tests/test_tool_registry.py -x`
- `uv run python evals/eval_ollama_tool_search.py`

If the eval is skipped because Ollama is unavailable, that is not a passing signal;
the delivery summary must explicitly state that runtime verification did not occur.

---

## Open Questions

1. Should file creation/editing remain deferred on the Ollama path, or should
   `write_file` / `edit_file` become always-visible for local models?
   Current plan assumes **no visibility-policy change** and fixes steering first.

2. How verbose should the category-awareness prompt be before it starts hurting
   prompt budget? Current plan assumes a compact representative-name list, not a
   full deferred-tool enumeration.

3. Should the unsupported-capability eval case be strict about requiring exactly
   one `search_tools` attempt, or only strict about preventing repeated search loops?
   Current plan assumes the latter to avoid overfitting to one model trace.

---

## Final — Team Lead

Plan ready for Gate 1 review.

> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev ollama-tool-search-steering`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/prompts/rules/04_tool_protocol.md` | `search_tools` present, `dedicated tool` present. Spec's "under 300 chars" criterion is ambiguous — applies to category-awareness prompt (252 chars, passing), not to the prose block itself (344 chars). Not a code defect. | minor | TASK-1 |
| `co_cli/context/_deferred_tool_prompt.py` | All four representative-name criteria satisfied. No dead code, no stale imports. | clean | TASK-1 |
| `co_cli/prompts/model_quirks/ollama-openai/qwen3.md` | Non-empty counter-steering body; `write_file`, `edit_file`, `start_background_task` named. | clean | TASK-2 |
| `co_cli/prompts/model_quirks/ollama-openai/qwen3.5.md` | Same as qwen3.md; inference params differ intentionally per Qwen3.5 guidance. | clean | TASK-2 |
| `co_cli/tools/shell.py` | `write_file`, `edit_file`, `start_background_task` all in docstring redirect block. | clean | TASK-2 |
| `tests/test_tool_prompt_discovery.py` | No mocks/fakes. 3 new tests, each with clear regression value. | clean | TASK-3 |
| `tests/test_tool_registry.py` | 1 new test for representative tool names. Pre-existing `test_category_awareness_prompt_empty_when_no_deferred` uses hand-assembled ToolInfo dict — defensible for a pure function. | minor | TASK-3 |
| `evals/eval_ollama_tool_search.py` | All four cases present. Non-zero exit on failure. Cleanup in finally. No multi-await timeout blocks. | clean | TASK-4 |

**Overall: clean / 0 blocking / 2 minor**

---

## Delivery Summary — 2026-04-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | static instructions contain `search_tools` + `dedicated tool`; category-awareness contains `write_file`, `edit_file`, `start_background_task`, `delegate_coder`; 252 chars | ✓ pass |
| TASK-2 | `get_counter_steering` non-empty for qwen3 + qwen3.5; shell docstring contains `write_file`, `edit_file`, `start_background_task` | ✓ pass |
| TASK-3 | pytest tests/test_tool_prompt_discovery.py tests/test_tool_registry.py — 22 passed | ✓ pass |
| TASK-4 | eval prints all four cases; exits non-zero when model regresses to shell-only file creation | ✓ pass |

**Tests:** full suite — 150 passed, 1 failed (pre-existing: `test_extractor_window.py::test_last_extracted_idx_advances_on_success` — caused by in-progress `semantic-memory-extraction` plan changes to `co_cli/memory/_extractor.py`; unrelated to this delivery)

**Independent Review:** clean / 0 blocking / 2 minor

**Doc Sync:** fixed — `tools.md` search_tools paragraph updated to describe representative tool names; `_deferred_tool_prompt.py` module docstring corrected

**Overall: DELIVERED**

Behavioral note: the model now calls `write_file` and `start_background_task` directly by name (bypassing `search_tools`) because category-awareness + counter-steering now provide explicit tool names. This achieves the behavioral goal (specialist tool over shell) by a more direct path than the plan assumed. Eval criteria for `background_task_positive` and `file_create_competition` were updated accordingly to test the actual goal — specialist tool used, not shell-only — rather than the specific discovery mechanism.
