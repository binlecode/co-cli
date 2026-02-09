# TODO: Prompt Architecture Refactoring

**Status:** Proposed
**Goal:** Align `co-cli` prompt management with industry best practices (Codex, OpenCode) to fix reasoning gaps and improve maintainability.

## 1. Gap Analysis

| Feature | Current `co-cli` | Peer Systems (Codex/OpenCode) | Gap Impact |
| :--- | :--- | :--- | :--- |
| **Tool Output Handling** | "Verbatim" strict mode | Implicit Synthesis ("Think out loud") | Agent acts as a "dumb pipe," failing simple analytical queries (e.g., "When is lunch?"). |
| **Prompt Storage** | Inlined string (`agent.py`) | External Markdown (`prompts/*.md`) | Hard to edit, no syntax highlighting, requires code changes to tweak. |
| **Composition** | Monolithic string | Modular/Templated | Hard to reuse snippets (e.g., security rules) across different agent personas. |
| **Model Tuning** | Single prompt | Model-specific routing | Suboptimal performance on non-Gemini models (e.g., Claude needing XML tags). |

## 2. Implementation Plan

### Phase 1: Fix Reasoning Gap (Immediate)
**Objective:** Allow the agent to answer "Find/When" questions by relaxing the strict "verbatim" rule.

- [ ] **Update System Prompt:**
  - Explicitly distinguish between **List/Show** (verbatim) and **Find/Analyze** (synthesis).
  - Remove "Never reformat/summarize" directive.
  - Add "High-Signal Output" instruction.

### Phase 2: Externalize Prompts (High Priority)
**Objective:** Move prompts out of Python code into Markdown files for better DX.

- [ ] **Create Directory:** `co_cli/prompts/`
- [ ] **Migrate System Prompt:** Move content from `agent.py` to `co_cli/prompts/system.md`.
- [ ] **Implement Loader:** Add `load_prompt(name: str)` utility in `co_cli/agent.py` using `importlib.resources` or `Path`.
- [ ] **Hot Reloading (Optional):** Allow editing `.md` files without restarting the CLI (dev mode).

### Phase 3: Modularization (Medium Priority)
**Objective:** Split the "God Prompt" into reusable components.

- [ ] **Extract Snippets:**
  - `co_cli/prompts/snippets/security.md`
  - `co_cli/prompts/snippets/tools.md`
  - `co_cli/prompts/snippets/style.md`
- [ ] **Template Engine:** Use simple f-string or `jinja2` (if already a dep) to stitch prompts:
  ```python
  system_prompt = load_template("system.md", security=load_snippet("security.md"))
  ```

### Phase 4: Model-Specific Routing (Future)
**Objective:** Optimize prompts for different LLM providers.

- [ ] **Structure:**
  - `co_cli/prompts/gemini/system.md`
  - `co_cli/prompts/anthropic/system.md`
- [ ] **Router:** Select prompt path based on `settings.llm_provider`.

## 3. Success Metrics

1.  **Lunch Time Test:** Agent correctly identifies lunch time from a calendar list without just dumping the raw JSON/text.
2.  **Edit Velocity:** Prompt changes can be made and tested in < 1 minute by editing a Markdown file.
3.  **Code Cleanliness:** `agent.py` shrinks by ~100 lines.