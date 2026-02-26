# TODO: Skills System

Co-cli's current skill mechanism (`co_cli/_commands.py`) loads `.claude/skills/*.md` files as
slash commands expanded into user messages. It is below the Claude Code standard in several
dimensions. Unresolved items in priority order:

## Gap 1 — Frontmatter parsing (Low effort, High value)

Parse `user-invocable`, `disable-model-invocation`, `argument-hint`, and `description` from
skill YAML frontmatter. Currently the raw Markdown body is loaded; all frontmatter is ignored.

## Gap 2 — Argument substitution (Low effort, High value)

Interpolate `$ARGUMENTS`, `$0`, `$1` placeholders before injecting skill content. Currently
arguments after the slash command are appended literally, not substituted into the skill body.

## Gap 3 — Description injection into system prompt (Low effort, High value)

Inject skill descriptions into the per-turn system prompt so the model knows what skills exist
between turns. Without this, auto-invocation (model triggers a skill based on description
match) is impossible. Also required for the `/` autocomplete menu to show helpful descriptions.

## Gap 4 — `disable-model-invocation` / `user-invocable` flags (Low effort, Medium value)

Respect `user-invocable: false` to hide a skill from the `/` menu (Claude-only invocation) and
`disable-model-invocation: true` to block auto-invoke so only the user can trigger it.

## Gap 5 — Environment/binary gating (Medium effort, Medium value)

Suppress skills from context when their runtime prerequisites are not met. Modeled after
openclaw's `metadata.openclaw.requires` block (`bins`, `anyBins`, `env`, `config`, `os`).
A skill for `gh` should not appear when `gh` is not on PATH.

## Gap 6 — `allowed-tools` per-skill tool grants (Medium effort, Low value)

Allow a skill to declare which tools it needs; grant those tools without a separate approval
prompt for the duration of the skill run.

## Gap 7 — Shell preprocessing (Medium effort, Low value)

Evaluate `` !`command` `` blocks in skill bodies before Claude sees the content, enabling
dynamic context injection (e.g. current branch, file list, env values).

## Gap 8 — `context: fork` subagent execution (High effort, Low value — deferred)

Run a skill in an isolated subagent context so it does not pollute the main conversation
history. Requires subagent delegation infrastructure (see TODO-co-agentic-loop-and-prompting).

## Reference

- OpenClaw implementation: `src/agents/skills/` (types, workspace, frontmatter, plugin-skills)
- CC official skills docs: `https://docs.anthropic.com/en/docs/claude-code/skills`
- AgentSkills open standard: `https://agentskills.io`
- Full gap analysis source: `docs/TAKEAWAY-openclaw-skills.md` (converted 2026-02-25)
