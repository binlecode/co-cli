# RESEARCH: Prompt Gaps — Skill Prompts

_Date: 2026-04-21_

This doc covers gaps in `co-cli`'s **skill system** — how skill bodies are authored, discovered, and surfaced to the agent. Scope is `co-cli`'s own skill implementation (bundled `co_cli/skills/*.md`, user-global `~/.co-cli/skills/*.md`), not the Claude Code harness skill files (`.claude/skills/*`).

**Related work:**
- `docs/exec-plans/active/2026-04-28-081359-main-flow-prompt-parity.md` — phased plan for main orchestrator agent prompt gaps (skill dispatch flows through this; supersedes the retired `RESEARCH-prompt-gaps-main-flow.md`)
- `RESEARCH-prompt-gaps-llm-tools.md` — gaps in LLM-calling tools

**Priority note:** This is a forward-looking gap analysis. `co-cli` ships exactly one bundled skill today (`co_cli/skills/doctor.md`), so the immediate surface area is small. The gaps named here become load-bearing when (a) more bundled skills are added, or (b) users start installing skills seriously.

## Scope

Reviewed `co-cli` files:

- `docs/specs/skills.md` — skill system spec
- `co_cli/skills/doctor.md` — the only bundled skill
- `co_cli/commands/_skill_types.py` — `SkillConfig` dataclass
- `co_cli/commands/_commands.py` — loader, scanner, dispatch, `/skills` commands
- `co_cli/bootstrap/core.py` — `create_deps()` — skill loading at startup

Peer files reviewed:

- `hermes-agent/skills/` — 26 top-level categories, 100+ skills (the primary peer for this surface)
- `hermes-agent/skills/software-development/subagent-driven-development/SKILL.md` (example structured skill)
- `hermes-agent/agent/prompt_builder.py:583-808` (`build_skills_system_prompt` — skill index assembly)
- `hermes-agent/agent/skill_utils.py` (frontmatter parsing, platform gating, condition filtering)
- `hermes-agent/agent/skill_commands.py` (dispatch, lifecycle)

Fork-cc and codex have no comparable user-installable skill system, so hermes is the only real peer here.

## Architectural Difference: Prompt Overlay vs Loaded Reference

The two systems take very different design positions.

### `co-cli` — skills as prompt overlays

From `docs/specs/skills.md:24-38`:

> Skills in `co-cli` are prompt overlays loaded from markdown files and exposed through slash commands. A skill does not register a new tool. Instead, it expands into an agent-body string that is fed back into the main agent for a normal LLM turn.

Dispatch flow:
1. user types `/doctor`
2. `dispatch()` matches the skill in `ctx.deps.skill_commands`
3. skill body is copied into `delegated_input`
4. `main.py` runs `run_turn()` with `delegated_input` as the user message
5. normal agent loop executes

The skill body **is** the prompt that drives the turn. There is no separate LLM call, no skill index in the system prompt, no "load this skill first" instruction.

### Hermes — skills as indexed loadable references

From `agent/prompt_builder.py:777-799`, hermes bakes a full skills index into the system prompt with a mandatory-scan instruction:

> Before replying, scan the skills below. If a skill matches or is even partially relevant to your task, you MUST load it with skill_view(name) and follow its instructions. Err on the side of loading — it is always better to have context you don't need than to miss critical steps, pitfalls, or established workflows.

Dispatch flow:
1. user sends any message
2. model sees the skills index in the system prompt (already loaded at session start)
3. model decides whether a skill is relevant
4. model calls `skill_view(name)` to load the SKILL.md body into context
5. model follows the skill's workflow

The skill body is referenced on demand by the model, not automatically injected.

### Consequence: different gap profiles

- `co-cli` skills are **opt-in by user** (user types `/doctor`); the agent cannot proactively reach for a skill it needs
- Hermes skills are **opt-in by model** (model decides to load based on skills index); the user never needs to know skills exist
- `co-cli` skill bodies are **prompts executed once**; they drive a single turn
- Hermes skill bodies are **workflow references loaded into context**; the model may revisit them across multiple turns

## Concrete Gaps

### Gap 1: No system-prompt skill discovery surface

Evidence:

- `co_cli/prompts/_assembly.py:87-160` assembles the static prompt without any skill index
- `co_cli/agent/_instructions.py` has no `add_skill_awareness_prompt` equivalent
- `deps.skill_commands` and `get_skill_registry()` (from `docs/specs/skills.md:131-138`) exist but the agent never sees them through a prompt channel
- The `disable_model_invocation` frontmatter flag implies model-invocation was considered, but no path to the model exists today

Why it matters:

- Even with user-installed skills, the agent cannot proactively suggest or invoke them
- The full-bodied skill prompts are invisible until the user happens to type the right slash command
- This makes skills a discoverable-only-by-documentation feature — users who haven't read `/skills list` get no value

Peer comparison: Hermes's `build_skills_system_prompt()` in `agent/prompt_builder.py:583-808` is the exact mechanism `co-cli` lacks. It builds a compact category-grouped index from all SKILL.md files, applies platform / tools / toolsets filters, and injects with mandatory-scan instruction.

### Gap 2: No skill authoring contract

Evidence:

- `docs/specs/skills.md` documents the frontmatter schema and load semantics but not what a good skill *body* should contain
- The only bundled skill (`co_cli/skills/doctor.md`) is 23 lines with ad-hoc structure: preamble + "Respond with this exact structure" block
- No template, no required sections, no style guide for skill body authoring
- No documented examples of skill body patterns (workflow skill, diagnostic skill, multi-step skill, etc.)

Why it matters:

- User-installed skills arrive with unpredictable shape
- There is no quality bar for third-party skills
- The `/skills install <url>` path in `docs/specs/skills.md:190-200` does a security scan but not a structural-quality scan

Peer comparison: Hermes's bundled skills demonstrate a consistent structure. `hermes-agent/skills/software-development/subagent-driven-development/SKILL.md` uses:

- rich frontmatter: `name`, `description`, `version`, `author`, `license`, `metadata.hermes.tags`, `metadata.hermes.related_skills`
- standardized sections: `## Overview`, `## When to Use`, `## The Process`, numbered steps
- concrete code examples inline (as `python` fenced blocks showing `read_file`, `todo`, `delegate_task` calls)
- explicit "vs. manual execution" comparisons
- related-skills cross-references

This is a de-facto authoring contract that `co-cli` has not documented or enforced.

### Gap 3: No skill self-improvement loop

Evidence:

- `co_cli/prompts/rules/` contains no guidance about updating stale or inaccurate skills
- `docs/specs/skills.md` documents the `/skills upgrade <name>` CLI command but has no prompt-level pressure for the model to notice and patch skill issues
- The `/skills` command family supports `install`, `reload`, `upgrade` but not `patch` — there is no model-invokable path to edit a skill in place

Why it matters:

- Skills drift out of sync with the codebase they describe
- A stale skill becomes a liability: the model follows outdated steps or references removed tools
- Without a self-improvement loop, skill quality degrades over time

Peer comparison: Hermes's `SKILLS_GUIDANCE` in `agent/prompt_builder.py:164-171`:

> After completing a complex task (5+ tool calls), fixing a tricky error, or discovering a non-trivial workflow, save the approach as a skill with skill_manage.
>
> When using a skill and finding it outdated, incomplete, or wrong, patch it immediately with skill_manage(action='patch') — don't wait to be asked. Skills that aren't maintained become liabilities.

This pairs a model-invokable `skill_manage` tool with a prompt-level "keep skills maintained" invariant. `co-cli` has neither half of this mechanism.

### Gap 4: Sparse bundled skill library

Evidence:

- `co_cli/skills/` contains exactly one file: `doctor.md`
- `docs/specs/skills.md:23` describes skills as "prompt overlays" but there are essentially zero bundled examples of common workflows (plan, review, refactor, triage, etc.)

Why it matters:

- Users see "Skills" in the docs and `/skills list` shows one item — the feature looks undercooked
- Skills that would be obvious wins (structured code review, plan drafting, investigation templates) are absent
- The `co-cli` slash-command surface is artificially thin compared to what the skill system supports

Peer comparison: Hermes ships 26 top-level skill categories. While that may be over-aggressive (see Peer Weaknesses below), the delta from 26 → 1 is large, and `co-cli` has room to add 3–5 high-value bundled skills without approaching hermes-scale bloat.

### Gap 5: No authoring-vs-installation quality tiering

Evidence:

- `docs/specs/skills.md:112-127` — security scan behavior differs by path (startup warning-only vs `/skills install` explicit-confirmation) but there is no separate *quality* tier
- Bundled skills are version-controlled so they bypass the security scan, but there is no lint, no schema validation, no template conformance check

Why it matters:

- A bundled skill shipped with `co-cli` should meet higher quality bar than a user-installed one
- Without a tiering / quality contract, there is no clean way to enforce "bundled skills are reference-quality"

## Where `co-cli` Is Better

Despite the gaps, two aspects of the skills architecture are genuinely better than hermes:

### 1. Ephemeral turn-scoped env injection

`docs/specs/skills.md:172-184` — `skill-env` frontmatter injects env vars only for the duration of the dispatched turn, with rollback on success/failure/interrupt. Filtered through `_SKILL_ENV_BLOCKED` to prevent overriding critical process variables (`PATH`, `PYTHONPATH`, `HOME`).

Hermes has no equivalent — skill-managed env vars leak into the rest of the session.

### 2. Single-tier dispatch, not prompt-bloat

Because `co-cli` skills are prompt overlays driving one turn, they do not inflate every system prompt the way hermes's mandatory skills index does. The cost of adding a new skill to `co-cli` is zero ongoing prompt overhead; the cost of adding one to hermes is +1 line in every session's system prompt (and attention contention).

This means if `co-cli` does add a skill-discovery surface (Gap 1), it should be **optional / lazy**, not mandatory-scan like hermes.

## Peer Weaknesses

### 1. Hermes's skills index is over-aggressive

`hermes-agent/agent/prompt_builder.py:777-799` — "If a skill matches or is even partially relevant to your task, you MUST load it with skill_view(name)."

This creates:
- unnecessary tool churn — model calls `skill_view` for marginally relevant skills
- prompt mass devoted to the skills index that many tasks do not need
- over-eager loading when a quick answer would be better

### 2. Hermes skill bodies are heavy

The example `subagent-driven-development/SKILL.md` is hundreds of lines with detailed per-step code examples. Once loaded via `skill_view()`, this consumes significant context. For a short task, this is pure overhead.

### 3. Hermes's first-match-wins platform gating is coarse

`skill_matches_platform()` filters skills by declared platform. A skill that declares multiple platforms is fine, but a skill with no platform declaration is always loaded. This can surface incompatible skills silently.

## Recommended Direction

### P3 (forward-looking — defer until skills library grows)

1. **Define a skill authoring contract.** Add `docs/specs/skill-authoring.md` (or a section in `docs/specs/skills.md`) specifying:
   - required sections: `## When to use`, `## Steps`, `## Output contract` (or similar)
   - recommended frontmatter: `argument-hint`, `requires.bins`, `user-invocable`, `disable-model-invocation`
   - style conventions (tense, imperative mood, concrete tool examples)
   - length budget (e.g. <150 lines for bundled skills)
   - closes Gap 2

2. **Add 3–5 high-value bundled skills.** Candidates:
   - `plan` — draft an implementation plan for a task
   - `review` — structured self-review of pending changes
   - `triage` — diagnose a failing test or error
   - `refactor` — apply a named refactor pattern
   - closes Gap 4

3. **Add an opt-in skill-awareness prompt layer.** Similar to `add_category_awareness_prompt` in `co_cli/agent/_instructions.py:21-24`:
   - Lists loaded, model-invocable skills (filtering out `disable_model_invocation=True`)
   - Short, compact format (~1 line per skill: `name — description`)
   - Injected as a runtime `@agent.instructions` callback, not baked into the static prompt
   - Instruction should be aspirational, not mandatory: "The following skills are available — invoke them with `skill_run(name)` when directly relevant"
   - Addresses Gap 1 without adopting hermes's over-aggressive framing
   - Requires new `skill_run` tool for model-side dispatch

4. **Add a skill-update prompt rule.** Add to `co_cli/prompts/rules/04_tool_protocol.md`:
   - "When you invoke a skill and find its steps outdated or wrong for the current task, note the drift and suggest a patch."
   - Pair with an optional `skill_patch` tool (deferred-approval) for model-invokable edits
   - Closes Gap 3

### Not recommended

1. **Do not adopt hermes's mandatory-scan skill index.** The "you MUST load any even partially relevant skill" framing is too aggressive and would nullify `co-cli`'s prompt-mass advantage.

2. **Do not bake skill bodies into the system prompt.** Keep co's current "skill body drives a single turn" model; skills should remain prompt overlays, not reference documents loaded into context.

## Bottom Line

`co-cli`'s skill system is architecturally lean but under-used. The four concrete gaps are:

1. skill discovery (no prompt surface for the agent to know skills exist)
2. authoring contract (no quality standard for skill bodies)
3. self-improvement loop (no model-invokable skill maintenance)
4. sparse bundled library (exactly one shipped skill)

Closing these is not urgent. The skills system works as designed for the single bundled skill. But as `co-cli` matures, the authoring contract and model-side discovery become load-bearing — and they are easier to design now than to retrofit after a library of user-installed skills accumulates with inconsistent shape.

The ordering should be: author contract first (Gap 2), then bundled library fill-in (Gap 4), then model-side discovery (Gap 1), then self-improvement (Gap 3). Gap-1 work depends on authoring conventions being settled, so inverting this order creates rework.
