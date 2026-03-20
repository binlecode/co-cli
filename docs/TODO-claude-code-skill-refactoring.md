# TODO: Refactor Agent Skills for Progressive Disclosure

## Context
Based on Anthropic's "Lessons from Building Claude Code: How We Use Skills" (https://x.com/trq212/status/2033949937936085378), our current agent skills (`orchestrate-plan`, `orchestrate-dev`, `delivery-audit`, `sync-doc`) are monolithic `SKILL.md` files. This causes context window bloat and limits the agents' ability to use helper scripts for deterministic verification. We need to refactor these into structured folders using "Progressive Disclosure."

## Problem & Outcome
**Problem:** Monolithic `SKILL.md` files are hard to maintain, overwhelm the LLM's context window, and rely on the LLM for tasks better suited for standard compute (like DAG validation or grep counting).
**Outcome:** All skills are restructured into modular directories (`assets/`, `references/`, `scripts/`). The main `SKILL.md` files are slimmed down to act only as routing instructions and high-level orchestrators that point the agent to specific files and scripts when needed.

## Scope
**In Scope:**
- Restructuring the 4 existing skills in `.claude/skills/`.
- Extracting templates into `assets/`.
- Extracting checklists and historical failure modes into `references/gotchas.md`.
- Creating executable Python/Bash scripts in `scripts/` to automate verification tasks currently performed manually by the LLM.
- Slimming down the main `SKILL.md` files to update references.

**Out of Scope:**
- Creating entirely new skills.
- Modifying the core `co-cli` agent runner logic (this is purely a content/structure refactor of the skill folders).

## Implementation Plan

### Phase 1: Refactor `orchestrate-plan`
- **TASK-1:** Create folder structure for `orchestrate-plan`
  - `files:` `.claude/skills/orchestrate-plan/`
  - `done_when:` Directories `assets/`, `references/`, and `scripts/` exist within the skill folder.
  - `prerequisites:` []
- **TASK-2:** Extract TODO template
  - `files:` `.claude/skills/orchestrate-plan/SKILL.md`, `.claude/skills/orchestrate-plan/assets/TODO-template.md`
  - `done_when:` The markdown structure for the `TODO-<slug>.md` file is moved to `assets/TODO-template.md` and `SKILL.md` references this new file path.
  - `prerequisites:` [TASK-1]
- **TASK-3:** Extract Gotchas and Checklists
  - `files:` `.claude/skills/orchestrate-plan/SKILL.md`, `.claude/skills/orchestrate-plan/references/gotchas.md`, `.claude/skills/orchestrate-plan/references/checklists.md`
  - `done_when:` Core Dev and PO checklists are moved to `references/checklists.md`, and common planning pitfalls are documented in `references/gotchas.md`. `SKILL.md` instructs the subagents to read these files.
  - `prerequisites:` [TASK-1]
- **TASK-4:** Create DAG Validation Script
  - `files:` `.claude/skills/orchestrate-plan/SKILL.md`, `.claude/skills/orchestrate-plan/scripts/check-dag.py`
  - `done_when:` `check-dag.py` exists, is executable, successfully parses a dummy TODO file for circular `prerequisites`, and `SKILL.md` instructs Core Dev to run this script.
  - `prerequisites:` [TASK-1]

### Phase 2: Refactor `orchestrate-dev`
- **TASK-5:** Create folder structure for `orchestrate-dev`
  - `files:` `.claude/skills/orchestrate-dev/`
  - `done_when:` Directories `assets/`, `references/`, and `scripts/` exist within the skill folder.
  - `prerequisites:` []
- **TASK-6:** Extract DELIVERY template
  - `files:` `.claude/skills/orchestrate-dev/SKILL.md`, `.claude/skills/orchestrate-dev/assets/DELIVERY-template.md`
  - `done_when:` The markdown structure for the `DELIVERY-<slug>.md` file is moved to `assets/DELIVERY-template.md` and `SKILL.md` references this new file path.
  - `prerequisites:` [TASK-5]
- **TASK-7:** Extract Gotchas and QA Checklists
  - `files:` `.claude/skills/orchestrate-dev/SKILL.md`, `.claude/skills/orchestrate-dev/references/gotchas.md`, `.claude/skills/orchestrate-dev/references/qa-checklists.md`
  - `done_when:` "Step 4 — Self-review" and "Step 2 — Independent code review" lists are moved to `references/qa-checklists.md`. Common coding/integration pitfalls are in `gotchas.md`. `SKILL.md` points to these.
  - `prerequisites:` [TASK-5]
- **TASK-8:** Create Task Verification Helper Script
  - `files:` `.claude/skills/orchestrate-dev/SKILL.md`, `.claude/skills/orchestrate-dev/scripts/verify-task.sh`
  - `done_when:` `verify-task.sh` exists, is executable, can run a provided test command safely, and `SKILL.md` instructs the TL/Devs to use this script for Step 5 (Verify done_when) instead of manual guessing.
  - `prerequisites:` [TASK-5]

### Phase 3: Refactor Analytical Skills (`delivery-audit` & `sync-doc`)
- **TASK-9:** Refactor `delivery-audit` structure and script
  - `files:` `.claude/skills/delivery-audit/SKILL.md`, `.claude/skills/delivery-audit/scripts/extract-features.py`, `.claude/skills/delivery-audit/assets/audit-report.md`
  - `done_when:` The reporting template is extracted. A script (`extract-features.py`) is created to automate grepping the codebase for `@tool` or CLI commands, returning a JSON list for the agent to compare against docs.
  - `prerequisites:` []
- **TASK-10:** Refactor `sync-doc` structure
  - `files:` `.claude/skills/sync-doc/SKILL.md`, `.claude/skills/sync-doc/references/gotchas.md`
  - `done_when:` `gotchas.md` is created emphasizing scope control (e.g., "Do not touch code, only fix docs"). `SKILL.md` is updated to point to it.
  - `prerequisites:` []

### Phase 4: Global Config & Polish
- **TASK-11:** Implement Global Skill Config
  - `files:` `.claude/skills/config.json`, all `SKILL.md` files
  - `done_when:` A central `config.json` exists defining project defaults (e.g., `test_command`, `linter`, `docs_dir`). All `SKILL.md` files instruct the agent to read this config for project-specific settings instead of hardcoding them.
  - `prerequisites:` [TASK-1, TASK-5, TASK-9, TASK-10]

## Testing
- Run `/orchestrate-plan dummy-feature` and verify the agent successfully finds and uses the extracted template and DAG script.
- Run `/orchestrate-dev dummy-feature` and verify it uses the external verification script and outputs the correct delivery template.

## Open Questions
- Does `co-cli` support pre-execution hooks (like the `/freeze` concept mentioned in the article) to restrict `sync-doc` to only edit files in `docs/`? (If not, rely on `gotchas.md` prompting for now).
