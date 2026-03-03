# TODO: Skills System

P3 skill system enhancements. P1/P2 items (Gaps 1–6: skills loader, frontmatter parsing, arg substitution, description injection, invocation flags, `/doctor` skill) shipped with the openclaw-skills-adoption-review delivery.

---

## Gap 8 — `allowed-tools` per-skill tool grants (P3)

**What:** Allow a skill to declare which tools it needs in frontmatter
(`allowed-tools: ["run_shell_command", "web_search"]`). Grant those tools without an
approval prompt for the duration of the skill invocation.

**Why:** Some skills encapsulate a known-safe workflow that requires tools normally gated
behind approval. Without per-skill grants, every invocation of those tools still prompts the
user, defeating the purpose of the skill.

**How:** When a skill is invoked, extract `allowed-tools` from its frontmatter. In
`_handle_approvals()` in `co_cli/main.py`, if the current turn was triggered by a skill
invocation and the pending tool name is in the skill's `allowed-tools` list, auto-approve
without prompting. Clear the per-skill grants after the turn completes.

**Note:** This requires passing skill invocation context into the approval flow, which
currently has no such concept. Requires design work before implementation.

**Done when:** A skill with `allowed-tools: ["run_shell_command"]` triggers a shell command
in the same turn without prompting the user for approval. After the turn, a non-skill turn
that triggers the same shell command does prompt normally.

---

## Gap 9 — Shell preprocessing (P3)

**What:** Evaluate `` !`command` `` blocks in skill bodies before the content is sent to
Claude. This allows dynamic context injection — e.g. the current branch, a file list, or
environment values.

**Why:** Static skill bodies cannot include runtime context. A skill that needs to reference
the current git branch must either ask Claude to run a shell command (extra turn) or have it
hardcoded. Shell preprocessing allows skills to inject live context at invocation time.

**How:** Before substituting `$ARGUMENTS` and before building the user message,
scan the skill body for `` !`...` `` blocks. For each match, run the inner command via
`asyncio.create_subprocess_exec` with a 5-second timeout. Replace the block with stdout
output (trimmed). On error or timeout, replace with an empty string and log a warning.
Only evaluate up to 3 shell blocks per skill invocation to limit side effects.

**Done when:** A skill with `` !`git branch --show-current` `` in its body, when invoked,
produces a user message where that block is replaced by the current branch name.

---

## Gap 10 — `context: fork` subagent execution (P3, deferred)

**What:** Run a skill in an isolated subagent context so its conversation turns do not
pollute the main conversation history.

**Why:** Skills that generate multiple intermediate tool calls (e.g. a research skill that
does 5 web searches and synthesizes a report) leave a noisy history in the main context.
Forked execution would keep the main history clean.

**How:** Requires subagent delegation infrastructure (see `TODO-subagent-delegation.md`).
When `context: fork` is set in skill frontmatter, delegate skill execution to a subagent
via `delegate_research()` or a generic `delegate_skill()` tool. Return only the final
summary to the main conversation.

**Done when:** A skill with `context: fork` in its frontmatter runs its tool calls in a
subagent span (visible in OTel traces as a child span), with only the final output message
appearing in the main conversation history.

**Dependency:** Requires `TODO-subagent-delegation.md` Phase A to ship first.

---

## Reference

- OpenClaw implementation: `~/workspace_genai/openclaw/src/agents/skills/` (types, workspace, frontmatter, plugin-skills)
- CC official skills docs: https://docs.anthropic.com/en/docs/claude-code/skills
- AgentSkills open standard: https://agentskills.io
- Full gap analysis source: `docs/TAKEAWAY-openclaw-skills.md`
