# TODO: Skills System

Co-cli's current skill mechanism (`co_cli/_commands.py`) loads `.claude/skills/*.md` files as
slash commands and expands them into user messages. It is below the Claude Code standard in
several dimensions. Unresolved items in priority order:

---

## Gap 1 — Frontmatter parsing (P1)

**What:** Parse `user-invocable`, `disable-model-invocation`, `argument-hint`, and `description`
fields from skill YAML frontmatter before registering the skill. Currently the raw Markdown body
is loaded; all frontmatter is stripped or ignored.

**Why:** Without parsing frontmatter, there is no way to distinguish user-only skills from
model-invocable ones, and the `/` autocomplete menu has no descriptions to display. Skills are
loaded as opaque text blobs, making the remaining gaps (arg substitution, description injection,
flag gating) impossible to implement correctly.

**How:** In `_load_skills()` in `co_cli/_commands.py`, use `_frontmatter.parse_frontmatter()`
(already in the codebase) to split each `.md` file into `meta: dict` and `body: str`. Store
both on the skill object. The parsed fields are:
- `description: str` — shown in autocomplete and injected into system prompt
- `argument-hint: str` — displayed in the `/` menu after the command name
- `user-invocable: bool` (default `True`) — if `False`, hide from the `/` menu
- `disable-model-invocation: bool` (default `False`) — if `True`, block model auto-invoke

**Done when:** `uv run pytest tests/test_commands.py -v -k skill` passes; a skill `.md` file
with YAML frontmatter has its `description` field available on the loaded skill object and its
`body` field contains only the Markdown content (no frontmatter block).

---

## Gap 2 — Argument substitution (P1)

**What:** Before injecting skill body into the user message, interpolate `$ARGUMENTS` (full
remainder after the command name), `$0` (command name itself), `$1`, `$2`, ... (positional
args split on whitespace) in the skill body.

**Why:** Currently, arguments after the slash command are appended literally after the skill
body text. A skill like `/review-pr 123` sends the entire skill body plus ` 123` — the PR
number is not substituted into the skill's prompt template. Skills cannot have parameterized
templates.

**How:** In the slash command dispatch path in `co_cli/_commands.py`, after loading the skill
body and before building the user message, run a substitution pass:
1. Split the user input after the command name into `arguments_str` (full) and `args_list`
   (whitespace-split positional list).
2. Replace `$ARGUMENTS` with `arguments_str`.
3. Replace `$0` with the command name.
4. Replace `$1` ... `$N` with `args_list[0]` ... `args_list[N-1]` (empty string if out of range).
5. Append any unsubstituted remainder only if the skill body contains no `$ARGUMENTS`
   placeholder (backward-compatible behavior for skills that don't use substitution).

**Done when:** A skill file with `$ARGUMENTS` in its body, invoked as `/skill-name foo bar`,
produces a user message where `$ARGUMENTS` is replaced by `foo bar`. A skill without any `$`
placeholders has `foo bar` appended after its body (backward-compatible).

---

## Gap 3 — Description injection into system prompt (P1)

**What:** Inject the list of available skill names and descriptions into the per-turn system
prompt so the model knows what skills exist and can auto-invoke them by name.

**Why:** Without this, the model has no knowledge of user-defined skills between turns. It
cannot suggest `/skill-name` when the user's intent matches a skill's description. The
`/` autocomplete menu in the REPL can show descriptions (once Gap 1 is done) but the model
itself is blind to them.

**How:** In `co_cli/prompts/__init__.py` (or `_composer.py`), in `assemble_prompt()`, append
a `## Available Skills` section to the per-turn system prompt. Format: one line per skill —
`/skill-name — {description}`. Only include skills where `user-invocable=True` AND
`disable-model-invocation=False`. Skills with no description field are omitted from this
section (not injected).

Cap injected skill text at 2 KB total to avoid bloating the context window. If the cap is
exceeded, inject the first N skills that fit and add a note: `(+N more — type / to see all)`.

**Done when:** In a chat session with at least one skill file that has a `description` field,
the per-turn system prompt contains `## Available Skills` with that skill's name and description.
Verified by inspecting the OTel trace for the `system` message content.

---

## Gap 4 — `disable-model-invocation` / `user-invocable` flags (P2)

**What:** Respect the `user-invocable: false` frontmatter flag to hide a skill from the `/`
autocomplete menu (model can still invoke it), and `disable-model-invocation: true` to prevent
the model from auto-invoking it (user-only trigger).

**Why:** Some skills are operational/meta commands that should not appear in autocomplete (e.g.
a skill that dumps debug state) but should remain model-callable. Others are user-intent triggers
that should never be auto-invoked by the model (e.g. a skill that sends an email).

**How:**
- In the `/` autocomplete menu builder: filter out skills where `user-invocable == False`.
- In the system prompt injection (Gap 3): filter out skills where `disable-model-invocation == True`.
- Both checks require Gap 1 (frontmatter parsing) to be done first.

**Done when:** A skill with `user-invocable: false` does not appear in the `/` tab-completion
list but is still callable by the model. A skill with `disable-model-invocation: true` appears
in autocomplete but is excluded from the `## Available Skills` system prompt section.

---

## Gap 5 — Environment/binary gating (P2)

**What:** Suppress skills from loading when their declared runtime prerequisites are not met
— specifically: required binaries not on `PATH`, required env vars not set, or required OS
not matched.

**Why:** A skill for `gh` (GitHub CLI) will fail with a confusing error if `gh` is not installed.
A skill that requires `OPENAI_API_KEY` will silently degrade. Gating prevents injecting broken
skills into the context window and confusing the model.

**How:** Add an `openclaw`-compatible `requires` block to skill frontmatter (optional):
```yaml
requires:
  bins: ["gh"]          # all must be on PATH
  anyBins: ["rg", "ag"] # at least one must be on PATH
  env: ["GITHUB_TOKEN"] # all must be set (non-empty)
  os: ["darwin", "linux"] # host OS must match
```

In `_load_skills()`, after parsing frontmatter, evaluate the `requires` block:
- `bins`: `all(shutil.which(b) is not None for b in bins)`
- `anyBins`: `any(shutil.which(b) is not None for b in anyBins)`
- `env`: `all(os.getenv(e) for e in env)`
- `os`: `sys.platform` in `[os + "-" or os]` using a simple prefix match

Skills that fail gating are excluded from the loaded skill set silently (no warning).
Log at DEBUG level: `Skipping skill {name}: requires {what} not satisfied`.

**Done when:** A skill with `requires: bins: ["nonexistent-binary-xyz"]` in its frontmatter
is not present in the loaded skill list, confirmed by verifying the skill is absent from `/`
autocomplete and absent from the per-turn system prompt.

---

## Gap 6 — `allowed-tools` per-skill tool grants (P3)

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

## Gap 7 — Shell preprocessing (P3)

**What:** Evaluate `` !`command` `` blocks in skill bodies before the content is sent to
Claude. This allows dynamic context injection — e.g. the current branch, a file list, or
environment values.

**Why:** Static skill bodies cannot include runtime context. A skill that needs to reference
the current git branch must either ask Claude to run a shell command (extra turn) or have it
hardcoded. Shell preprocessing allows skills to inject live context at invocation time.

**How:** Before substituting `$ARGUMENTS` (Gap 2) and before building the user message,
scan the skill body for `` !`...` `` blocks. For each match, run the inner command via
`asyncio.create_subprocess_exec` with a 5-second timeout. Replace the block with stdout
output (trimmed). On error or timeout, replace with an empty string and log a warning.
Only evaluate up to 3 shell blocks per skill invocation to limit side effects.

**Done when:** A skill with `` !`git branch --show-current` `` in its body, when invoked,
produces a user message where that block is replaced by the current branch name.

---

## Gap 8 — `context: fork` subagent execution (P3, deferred)

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
