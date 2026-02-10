# REVIEW by Codex: Prompt Gap Analysis (`co-cli` vs `codex`)

## Scope and Method

- Scope: prompt design and prompt implementation architecture (not general tool breadth).
- Baselines:
  - `co-cli` at `/Users/binle/workspace_genai/co-cli`
  - `codex` at `/Users/binle/workspace_genai/codex`
- Method:
  1. trace runtime prompt assembly paths in both codebases,
  2. compare prompt assets, layering, and update semantics,
  3. map gaps to concrete implementation changes.
- Analysis date: February 9, 2026.

## Executive Summary

`co-cli` currently uses a **single static system prompt** loaded at agent construction time (`co_cli/agent.py:82`-`co_cli/agent.py:91`), while `codex` uses a **layered per-turn prompt pipeline** (base instructions + policy/developer overlays + user instructions/AGENTS + environment context) in `codex-rs/core/src/codex.rs:2131`-`codex-rs/core/src/codex.rs:2191`.

The largest prompt architecture gaps are:

1. no layered prompt composition in `co-cli` (high),
2. no AGENTS/skills instruction channel injection (high),
3. no policy-to-prompt rendering for sandbox/approval modes (high),
4. no collaboration-mode overlay system (high),
5. no model/personality-specific instruction families (medium-high),
6. no prompt update path when runtime context changes (medium-high),
7. prompt doc/runtime drift already present (medium).

## Runtime Prompt Architecture Comparison

### `codex` (layered, dynamic)

- Base instructions are model-selected with precedence (config override -> history -> model default): `codex-rs/core/src/codex.rs:314`-`codex-rs/core/src/codex.rs:323`.
- Multiple base instruction families and model-specific templates: `codex-rs/core/src/models_manager/model_info.rs:19`-`codex-rs/core/src/models_manager/model_info.rs:35`, `codex-rs/core/src/models_manager/model_info.rs:115`-`codex-rs/core/src/models_manager/model_info.rs:240`.
- Per-turn initial context layering order in one function:
  - policy-derived developer instructions,
  - explicit developer instructions,
  - collaboration-mode developer instructions,
  - personality spec injection,
  - user instructions (AGENTS/skills),
  - environment context
  at `codex-rs/core/src/codex.rs:2131`-`codex-rs/core/src/codex.rs:2191`.
- Policy fragments are modular prompt files and wrapped with explicit tags:
  - approval/sandbox fragments loaded in `codex-rs/protocol/src/models.rs:223`-`codex-rs/protocol/src/models.rs:237`,
  - composed via `DeveloperInstructions::from_policy` and `<permissions instructions>` wrapper in `codex-rs/protocol/src/models.rs:300`-`codex-rs/protocol/src/models.rs:369`.
- AGENTS and skills are assembled as user instructions:
  - discovery/merge: `codex-rs/core/src/project_doc.rs:39`-`codex-rs/core/src/project_doc.rs:83`,
  - user message wrapper format: `codex-rs/core/src/instructions/user_instructions.rs:29`-`codex-rs/core/src/instructions/user_instructions.rs:44`,
  - skills guidance rendering: `codex-rs/core/src/skills/render.rs:3`-`codex-rs/core/src/skills/render.rs:43`.
- Collaboration mode presets are first-class overlays:
  - `plan/default` templates wired in `codex-rs/core/src/models_manager/collaboration_mode_presets.rs:6`-`codex-rs/core/src/models_manager/collaboration_mode_presets.rs:51`.

### `co-cli` (single-layer, mostly static)

- Prompt loading is file-based but single template (`system.md`) via `load_prompt`:
  - `co_cli/prompts/__init__.py:10`-`co_cli/prompts/__init__.py:28`.
- Agent is constructed once with `system_prompt=system.md`:
  - `co_cli/agent.py:82`-`co_cli/agent.py:91`.
- Chat loop keeps message history but does not inject additional instruction channels:
  - `co_cli/main.py:124`-`co_cli/main.py:199`.
- Prompt text includes style and directive/inquiry logic (`co_cli/prompts/system.md:3`-`co_cli/prompts/system.md:42`) but no modular policy/personality/collab overlays.

## Gap Matrix (Prompt Design + Implementation)

### 1. Prompt Layering Model (High)

- `codex`: layered initial context builder (`codex-rs/core/src/codex.rs:2131`-`codex-rs/core/src/codex.rs:2191`).
- `co-cli`: single static prompt at agent creation (`co_cli/agent.py:82`-`co_cli/agent.py:91`).
- Gap: `co-cli` cannot independently evolve base behavior, policy behavior, user instructions, and environment context.
- Impact: reduced controllability, weaker runtime adaptation, harder safe overrides.

### 2. Policy-to-Prompt Composition (Sandbox + Approval) (High)

- `codex`: dynamic policy instructions rendered from runtime policy and wrapped in `<permissions instructions>` (`codex-rs/protocol/src/models.rs:300`-`codex-rs/protocol/src/models.rs:369`), with separate prompt modules (`codex-rs/protocol/src/prompts/permissions/...`).
- `co-cli`: approval/safety behavior mostly in code paths:
  - tool approval flags in `co_cli/agent.py:93`-`co_cli/agent.py:116`,
  - auto-safe command logic in `co_cli/_approval.py:4`-`co_cli/_approval.py:19`,
  - approval loop in `co_cli/_orchestrate.py:336`-`co_cli/_orchestrate.py:360`.
- Gap: model is not given structured, mode-accurate policy instructions comparable to codex.
- Impact: weaker model-side planning around escalations/permissions; more policy behavior implicit in harness only.

### 3. AGENTS.md + Skills Instruction Channel (High)

- `codex`: explicit AGENTS discovery and merge (`codex-rs/core/src/project_doc.rs:39`-`codex-rs/core/src/project_doc.rs:83`) and user-role injection wrapper (`codex-rs/core/src/instructions/user_instructions.rs:29`-`codex-rs/core/src/instructions/user_instructions.rs:44`), with skill metadata guidance (`codex-rs/core/src/skills/render.rs:3`-`codex-rs/core/src/skills/render.rs:43`).
- `co-cli`: no equivalent AGENTS/skills ingestion path in runtime chat/agent setup (`co_cli/main.py:124`-`co_cli/main.py:199`, `co_cli/agent.py:82`-`co_cli/agent.py:91`).
- Gap: repo-local instructions are not programmatically surfaced to the model at runtime.
- Impact: lower alignment with project-specific conventions and workflows.

### 4. Collaboration Mode Overlay System (High)

- `codex`: built-in mode presets and mode-specific developer instructions (`codex-rs/core/src/models_manager/collaboration_mode_presets.rs:12`-`codex-rs/core/src/models_manager/collaboration_mode_presets.rs:51`) backed by templates like `core/templates/collaboration_mode/default.md` and `plan.md`.
- `co-cli`: no mode abstraction; behavior is monolithic in one prompt (`co_cli/prompts/system.md:1`-`co_cli/prompts/system.md:42`).
- Gap: cannot switch between execution styles (default vs plan-like flows) without rewriting the base prompt.
- Impact: no policy-safe way to alter collaboration semantics per session/task.

### 5. Model-Specific Prompt Families + Personality Templating (Medium-High)

- `codex`: per-model instruction families + template variables for personality (`codex-rs/core/src/models_manager/model_info.rs:19`-`codex-rs/core/src/models_manager/model_info.rs:35`, `codex-rs/protocol/src/openai_models.rs:268`-`codex-rs/protocol/src/openai_models.rs:287`).
- `co-cli`: same prompt regardless of provider/model; only model transport/settings vary (`co_cli/agent.py:45`-`co_cli/agent.py:80`, `co_cli/agent.py:82`-`co_cli/agent.py:91`).
- Gap: no model-tailored instruction optimization.
- Impact: weaker cross-model consistency and less controllable model-specific behavior.

### 6. Prompt Update Semantics on Runtime Changes (Medium-High)

- `codex`: update items exist for policy/personality/collab/model transitions (`codex-rs/core/src/codex.rs:1518`-`codex-rs/core/src/codex.rs:1605`).
- `co-cli`: `/model` switches model object only (`co_cli/_commands.py:130`-`co_cli/_commands.py:137`, `co_cli/_commands.py:139`-`co_cli/_commands.py:186`) without any prompt-family switch/update.
- Gap: model and runtime state can change while prompt assumptions remain static.
- Impact: mismatch between active runtime configuration and model instructions.

### 7. Environment Context as Structured Prompt Input (Medium)

- `codex`: environment context is injected as structured item each turn (`codex-rs/core/src/codex.rs:2187`-`codex-rs/core/src/codex.rs:2190`), with standard tags defined in protocol (`codex-rs/protocol/src/protocol.rs:62`-`codex-rs/protocol/src/protocol.rs:65`).
- `co-cli`: no equivalent structured environment-context prompt item in turn assembly (`co_cli/main.py:124`-`co_cli/main.py:199`).
- Gap: less explicit runtime grounding (cwd/shell context) to the model.
- Impact: more reliance on implicit tool docs and prior conversational state.

### 8. Prompt Modularity and Governance Surface (Medium)

- `codex`: prompt assets are split by concern (base instructions, permission fragments, collaboration templates, personalities) and compiled in (`include_str!`) via typed builders (`codex-rs/protocol/src/models.rs:223`-`codex-rs/protocol/src/models.rs:237`, `codex-rs/core/src/models_manager/collaboration_mode_presets.rs:6`-`codex-rs/core/src/models_manager/collaboration_mode_presets.rs:10`).
- `co-cli`: single editable prompt file and simple loader (`co_cli/prompts/system.md`, `co_cli/prompts/__init__.py:10`-`co_cli/prompts/__init__.py:28`).
- Gap: lower separation-of-concerns; higher coupling of unrelated prompt concerns.
- Impact: harder controlled evolution and greater regression risk when prompt grows.

### 9. Prompt-Focused Test Coverage (Medium)

- `codex`: prompt assembly paths are tested around initial context behavior in core tests (for example, `codex-rs/core/src/codex.rs:5168`-`codex-rs/core/src/codex.rs:5192` and `codex-rs/core/src/codex.rs:5311`-`codex-rs/core/src/codex.rs:5322`).
- `co-cli`: tests focus on tool registration/approval/history (`tests/test_agent.py:1`-`tests/test_agent.py:66`) with no equivalent prompt-layer assembly contract tests.
- Gap: missing regression harness for prompt composition semantics.
- Impact: prompt behavior changes are harder to verify and can drift silently.

### 10. Internal Prompt/Runtime Drift Already Visible (Medium)

- Drift A:
  - `co_cli/prompts/system.md:36` says shell runs in Docker sandbox.
  - runtime can fallback to subprocess/no isolation (`co_cli/main.py:89` and `co_cli/main.py:109`).
- Drift B:
  - TODO says strict verbatim rule was removed (`docs/TODO-prompts-refactor.md:30`),
  - prompt still contains strict directive verbatim rule (`co_cli/prompts/system.md:20`).
- Gap: source-of-truth inconsistencies between docs, prompt text, and runtime.
- Impact: increased policy confusion and harder behavior debugging.

## What `co-cli` Already Does Well (Relevant to Prompt Strategy)

- Prompt concision and explicit directive/inquiry split are clear and practical (`co_cli/prompts/system.md:9`-`co_cli/prompts/system.md:27`).
- Critical safety is enforced in code (not prompt-only), which is robust:
  - web policy/domain guards in `co_cli/tools/web.py:95`-`co_cli/tools/web.py:206`,
  - shell execution constrained by backend and timeout in `co_cli/tools/shell.py:20`-`co_cli/tools/shell.py:37`,
  - approval orchestration in `co_cli/_orchestrate.py:336`-`co_cli/_orchestrate.py:360`.

## Prioritized Closure Plan (Prompt Architecture)

### P0 (highest leverage)

1. Introduce a prompt builder pipeline in `co_cli`:
   - `base` + `policy overlay` + `mode/persona overlay` + `project instructions` + `environment context`.
2. Add modular prompt fragments:
   - `co_cli/prompts/permissions/{sandbox_mode,approval_policy}/*.md`,
   - `co_cli/prompts/collaboration_mode/{default,plan}.md`.
3. Add AGENTS ingestion:
   - start with root-to-cwd AGENTS discovery and injection as a dedicated message block.
4. Make system prompt policy-aware at runtime:
   - do not hardcode Docker-only assumptions when subprocess fallback is active.

### P1

5. Add model/provider-specific prompt profiles (at minimum `gemini` vs `ollama` families).
6. Add runtime prompt update behavior when `/model`, approval mode, or sandbox backend changes.
7. Add structured environment context injection (cwd/shell/isolation/network policy).

### P2

8. Add prompt assembly tests:
   - deterministic snapshot tests for compiled prompt layers,
   - behavior tests for policy/mode toggles and AGENTS inclusion,
   - drift tests that assert docs and prompt text are aligned for key rules.

## Suggested Acceptance Checks for the Refactor

1. Prompt compilation test:
   - given `(sandbox=subprocess, approval=ask, mode=default)`, compiled prompt must include matching permission fragment and no Docker-only statement.
2. AGENTS propagation test:
   - AGENTS content in cwd must appear in injected instruction channel.
3. Mode switch test:
   - switching to plan mode should alter overlay text without replacing base policy text.
4. Model switch test:
   - `/model` updates both model object and instruction profile.
5. Drift guard:
   - fail CI when `docs/TODO-prompts-refactor.md` assertions conflict with `co_cli/prompts/system.md` key clauses.

## Bottom Line

`co-cli` currently has a good single-prompt MVP and solid code-side safety guards, but compared to codex it is missing the **prompt layering contract** that makes instructions policy-aware, context-aware, and mode-aware at runtime. Closing that one architectural gap will unlock most of the remaining improvements with manageable incremental changes.
