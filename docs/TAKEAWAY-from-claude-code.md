# Takeaway from Claude Code

## 1. Executive Summary

Claude Code is Anthropic's official CLI agent (TypeScript, ~14 official plugins in `plugins/`), built around a plugin architecture where agents, commands, skills, and hooks are self-contained markdown packages discovered at runtime. Its key architectural bet is **prompt-as-program**: complex workflows are orchestrated entirely through markdown files with YAML frontmatter -- no TypeScript orchestration code per feature. co-cli (Python/pydantic-ai) is smaller, soul-first, and code-driven; its strategic advantage is tight pydantic-ai integration, flat deps, and a simpler mental model. The insight: Claude Code's plugin primitives and hook lifecycle are worth understanding, but co-cli should adopt selectively -- the multi-agent orchestration patterns are powerful, while the full plugin marketplace infrastructure is premature.

## 2. Prompt Design: What Claude Code Does Differently

### Three Prompt Primitives

Claude Code defines three distinct markdown-file types, each with different autonomy levels:

- **Commands** (`commands/*.md`): User-invoked slash commands. Markdown body is the orchestration script, `$ARGUMENTS` injects user input. Example: `plugins/feature-dev/commands/feature-dev.md` defines a 7-phase workflow with inline agent launches, user checkpoints, and TodoWrite tracking. Frontmatter declares `allowed-tools` to restrict the command's tool access, and `argument-hint` for CLI help.

- **Agents** (`agents/*.md`): Autonomous sub-processes spawned via the Task tool. System prompt is the markdown body. Frontmatter specifies `model` (haiku/sonnet/opus/inherit), `color` for UI, `tools` for least-privilege access. Example: `plugins/feature-dev/agents/code-reviewer.md` (lines 1-47) runs as a sonnet sub-agent with restricted read-only tools plus TodoWrite.

- **Skills** (`skills/*/SKILL.md`): Educational knowledge that auto-activates based on the `description` field. Progressive disclosure: SKILL.md is the entry point, with `references/` and `examples/` subdirectories loaded on demand. Example: `plugins/plugin-dev/skills/agent-development/SKILL.md` has 416 lines of guidance plus 3 reference files and 2 example files.

**co-cli equivalent:** Rules in `co_cli/prompts/rules/*.md` assembled into one system prompt. No agent/command/skill distinction -- everything is a tool function registered via `agent.tool()`. co-cli's approach is simpler (one prompt, one agent) but cannot express multi-agent workflows or progressive knowledge loading.

**Tradeoff:** Claude Code's three primitives enable composable, multi-agent workflows without custom orchestration code. co-cli's single-agent model avoids the complexity of agent coordination but limits parallelism and role specialization.

### Plugin Architecture

Each Claude Code plugin is a self-contained directory:

```
plugin-name/
  .claude-plugin/plugin.json   # manifest with name, version, author
  commands/                     # auto-discovered .md files
  agents/                       # auto-discovered .md files
  skills/                       # subdirectories with SKILL.md
  hooks/hooks.json              # lifecycle event handlers
  .mcp.json                     # external tool servers
```

Discovery is convention-based: drop `.md` files in the right directory and they register automatically. `${CLAUDE_PLUGIN_ROOT}` provides portable path references. Source: `plugins/plugin-dev/skills/plugin-structure/SKILL.md` (lines 20-37).

**co-cli equivalent:** Tools are Python functions imported in `agent.py`. Prompt rules are assembled from `co_cli/prompts/rules/`. No plugin packaging, no manifest, no auto-discovery.

**Tradeoff:** Plugins enable distribution and team sharing (marketplace). co-cli's code-first approach means adding a tool requires editing Python files and re-importing, but it also means full type safety and IDE support -- no YAML debugging.

### Event-Driven Hook Composition

Claude Code defines 9 lifecycle events: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop, SessionEnd, PreCompact, Notification. Hooks are either `command` (shell scripts) or `prompt` (LLM-evaluated). They run in parallel, receive JSON on stdin, and return structured JSON to approve/block/modify operations.

Key examples from the repo:

- **Security hook** (`plugins/security-guidance/hooks/security_reminder_hook.py`, lines 31-126): PreToolUse hook that pattern-matches 9 security risks (command injection, XSS, eval, pickle, os.system) against file content being written. Uses session-scoped state files to avoid repeating warnings. Exit code 2 blocks the tool.

- **Ralph Wiggum Stop hook** (`plugins/ralph-wiggum/hooks/stop-hook.sh`, lines 1-177): Intercepts agent exit, reads transcript via `jq`, checks for completion promise in `<promise>` tags, increments iteration counter in a frontmatter state file, feeds the same prompt back as JSON `{"decision": "block", "reason": "$prompt"}`.

- **SessionStart hook** (`plugins/explanatory-output-style/hooks-handlers/session-start.sh`): Injects `additionalContext` into the session to modify Claude's output style. This is equivalent to appending to CLAUDE.md but distributable as a plugin.

**co-cli equivalent:** Approval is handled by `requires_approval=True` on tool registration, with `DeferredToolRequests` in the chat loop. No pre/post hooks, no session lifecycle events, no hook-based policy engine.

**Tradeoff:** Hooks give fine-grained control over every operation. co-cli's approval model is simpler (binary approve/deny per tool call) but cannot inject context, modify tool inputs, or enforce completion standards.

### Description-Based Agent Triggering

Agents are triggered by matching the `description` field. This field contains natural language conditions plus structured `<example>` blocks with Context/user/assistant/commentary tuples. Claude Code's LLM reads these descriptions and decides whether to spawn the agent for a given user request.

Source: `plugins/plugin-dev/skills/agent-development/references/triggering-examples.md` documents 4 trigger types: explicit request, proactive (after relevant work), implicit, and tool-usage-pattern-based. Recommended: 3-4 examples per agent, varying phrasing and covering both reactive and proactive triggers.

**co-cli equivalent:** No agent triggering -- co-cli has one agent with all tools registered. Tool selection happens via pydantic-ai's function-calling mechanism (tool docstrings).

**Tradeoff:** Description-based triggering enables role specialization without user configuration. But it depends on the LLM reliably parsing example blocks -- misfires (too eager or too cautious) are a known debugging challenge documented in the triggering-examples reference.

### Confidence-Scored Reviews

The code-review plugin (`plugins/code-review/commands/code-review.md`, lines 41-52) runs 4 parallel review agents (2 CLAUDE.md compliance, 1 diff-only bug detection, 1 contextual bug detection). Each issue is scored 0-100. Issues below 80 are filtered. Validated issues get a second pass via Opus sub-agents for bugs and Sonnet for style violations (line 55). False-positive categories are explicitly listed (lines 74-82).

**co-cli equivalent:** No confidence scoring. No multi-pass review pipeline. Code review would be a single tool call.

**Tradeoff:** Confidence scoring dramatically reduces false positives (their biggest stated challenge). But it requires multiple LLM calls per issue -- expensive. Worth adopting the concept (score + threshold) even in a single-agent model.

### Per-Agent Model Selection

Agent frontmatter includes `model: haiku|sonnet|opus|inherit`. The code-review command explicitly chooses models per task: haiku for triage (line 14), sonnet for CLAUDE.md compliance (line 33), opus for bug detection (lines 36-39). The feature-dev plugin uses sonnet for all three agents (code-explorer, code-architect, code-reviewer).

**co-cli equivalent:** One model for the entire agent, configured via `settings.llm_provider` + `settings.gemini_model`.

**Tradeoff:** Per-agent model selection optimizes cost (haiku for triage, opus for reasoning). co-cli would need pydantic-ai's sub-agent support to achieve this.

### Hookify Rule Engine

The hookify plugin (`plugins/hookify/`) lets users create behavioral rules in markdown with YAML frontmatter, stored as `.claude/hookify.{name}.local.md`. Rules specify event type (bash/file/stop/prompt), regex patterns, conditions with operators (regex_match, contains, not_contains, etc.), and action (warn/block). Rules activate immediately without restart.

A conversation-analyzer agent can scan the transcript for user frustration signals and auto-generate rules.

**co-cli equivalent:** No user-defined behavioral rules. All safety is hardcoded in tool registration (`requires_approval`) and the safe-command allowlist.

**Tradeoff:** Hookify gives users runtime control over agent behavior. co-cli's approach is simpler but less configurable. The conversation-analysis pattern (detecting frustration to auto-generate rules) is particularly clever.

## 3. Agent Loop: What Claude Code Does Differently

### Hook-Driven Permission Engine

Instead of a simple approve/deny per tool, Claude Code's hook system provides a policy engine. PreToolUse hooks can return `{"permissionDecision": "allow|deny|ask", "updatedInput": {...}}`. Multiple hooks run in parallel; any deny wins. The security-guidance hook (`security_reminder_hook.py`) demonstrates the pattern: it reads tool input JSON from stdin, checks patterns, returns exit code 2 to block, exit code 0 to allow. Session state prevents duplicate warnings.

Source: `plugins/plugin-dev/skills/hook-development/SKILL.md` (lines 124-153) documents the PreToolUse output format including `updatedInput` for modifying tool arguments before execution.

**co-cli equivalent:** `requires_approval=True` on tool registration, handled by `DeferredToolRequests` in the chat loop's `run_turn()` state machine. Binary approve/deny, no modification.

**Tradeoff:** Hook-driven permissions are more powerful (modify inputs, inject context, conditional policies). co-cli's approval model is sufficient for MVP but cannot evolve to policy-based security without architectural change.

### Self-Referential Agent Loop (Ralph Wiggum)

The Ralph plugin implements autonomous iteration using only a Stop hook and a state file. The mechanism:

1. `/ralph-loop` creates `.claude/ralph-loop.local.md` with frontmatter (iteration count, max, completion promise) and the prompt as body
2. Claude works on the task normally
3. When Claude tries to exit, the Stop hook (`hooks/stop-hook.sh`) fires
4. Hook reads transcript via `jq`, extracts last assistant message, checks for `<promise>` tag match
5. If no match: increments iteration, outputs `{"decision": "block", "reason": "$original_prompt"}`
6. Claude receives the same prompt again, sees its previous work in files/git
7. Loop continues until promise is true or max iterations reached

Source: `plugins/ralph-wiggum/hooks/stop-hook.sh` (lines 130-174) for the continuation logic.

**co-cli equivalent:** No autonomous iteration. The `run_turn()` loop handles one user message at a time. No Stop hook, no re-feeding mechanism.

**Tradeoff:** Ralph enables unattended overnight runs for well-defined tasks. It is elegant (file state + single hook = full iteration loop) but dangerous without proper escape hatches. co-cli's interactive model is safer but cannot do autonomous multi-hour work.

### Multi-Agent Orchestration via Commands

The feature-dev command (`plugins/feature-dev/commands/feature-dev.md`) orchestrates a 7-phase workflow:

1. Discovery (single agent, clarify requirements)
2. Codebase Exploration (2-3 code-explorer agents launched in parallel)
3. Clarifying Questions (blocks for user input)
4. Architecture Design (2-3 code-architect agents in parallel with different strategies)
5. Implementation (blocks for user approval, then sequential)
6. Quality Review (3 code-reviewer agents in parallel with different focuses)
7. Summary

Phases 2, 4, and 6 launch 6-8 sub-agents total, running in parallel within each phase. The command markdown is the orchestration script -- no TypeScript code manages the phases.

**co-cli equivalent:** No multi-agent orchestration. All work happens in a single pydantic-ai agent. The `/release` skill is the closest analogue but runs sequentially in one agent context.

**Tradeoff:** Parallel agent launches dramatically reduce wall-clock time for complex workflows. But they multiply API costs and require the parent context to synthesize multiple agent outputs. co-cli could achieve similar workflows using pydantic-ai's `Agent` with `result_type` for structured returns from sub-calls, but it would need explicit orchestration code.

### Sub-Agent Spawning via Task Tool

Commands and agents can spawn sub-agents via the Task tool with per-task model selection. The code-review command (lines 14, 24, 29-39) demonstrates this: haiku for triage, sonnet for compliance, opus for bug detection. Each sub-agent gets its own system prompt (from the agent `.md` file) and restricted tool access.

**co-cli equivalent:** No sub-agent spawning. pydantic-ai supports creating child agents, but co-cli does not use this capability.

**Tradeoff:** Sub-agents with model selection optimize cost/capability per task. The downside is context fragmentation -- sub-agents do not see the parent's full conversation history unless explicitly passed.

### TodoWrite for Task Tracking

Commands use `TodoWrite` to create and update task lists. The feature-dev command (line 16: "Use TodoWrite: Track all progress throughout") uses it for phase tracking. Crucially, TodoWrite state is NOT re-injected into the LLM context -- it is purely for the user's visibility and for the current agent to track its own progress.

**co-cli equivalent:** No task tracking tool. Progress is implicit in conversation history.

**Tradeoff:** TodoWrite provides lightweight progress visibility without context bloat. It is simple to implement (file-backed key-value store) and useful for multi-phase workflows.

## 4. Techniques to Adopt

### 4a. Confidence-Scored Tool Outputs

**What:** When a tool returns advisory results (search results, memory recalls, review findings), include a confidence score (0-100) and let the agent filter below a threshold.

**Why:** Reduces false positives in memory recall and review scenarios. The code-review plugin's 80-threshold filter is their most impactful quality technique.

**Sketch for pydantic-ai:** Add `confidence: int` to tool return dicts. Add a system prompt rule: "Discard results with confidence below 70 unless the user explicitly asks for low-confidence matches." No code change needed -- this is a prompt-level convention.

### 4b. Stop-Hook Equivalent for Completion Verification

**What:** Before the agent delivers a final response, verify that all stated sub-goals were addressed.

**Why:** Prevents premature completion -- the most common failure mode in multi-step tasks.

**Sketch for pydantic-ai:** Add a history processor that runs after the final assistant message. If the message looks like a completion (no pending tool calls), check conversation for unresolved sub-goals. If found, inject a system message: "You stated N sub-goals but only addressed M. Please complete the remaining." This fits naturally into co-cli's existing history processor pattern.

### 4c. Progressive Knowledge Loading (Skill Pattern)

**What:** Instead of loading all knowledge into the system prompt, load a summary (SKILL.md equivalent) and let the agent request deeper references on demand.

**Why:** co-cli's memory system already works this way (recall on demand), but curated knowledge (articles, guides) should follow the same pattern: index file with summaries, detail files loaded via tool call.

**Sketch for pydantic-ai:** The planned lakehouse tier (TODO-knowledge-articles.md) should adopt this structure: `articles/*/index.md` as the summary, with `references/` and `examples/` subdirectories. The `recall_article` tool returns the index; a `read_article_detail` tool loads specific reference files.

### 4d. Conversation-Driven Rule Generation

**What:** Analyze conversation history for user frustration patterns (corrections, reverts, "don't do X") and auto-generate behavioral rules.

**Why:** Hookify's conversation-analyzer agent is a compelling UX pattern -- it turns implicit feedback into explicit rules without requiring the user to configure anything.

**Sketch for pydantic-ai:** Add a `learn_from_corrections` tool (or aspect of memory) that scans recent history for correction signals. When detected, save as a memory tagged `[correction]` with the pattern and desired behavior. The memory recall system already loads relevant memories at conversation start -- corrections would surface automatically.

### 4e. Multi-Phase Workflow Commands

**What:** Define slash commands that orchestrate multi-step workflows with explicit user checkpoints.

**Why:** co-cli already has `/release` and `/sync-book` as skill files. Extending this to feature-dev-style workflows (explore -> design -> implement -> review) with explicit "wait for user" gates would add significant value.

**Sketch for pydantic-ai:** Skill markdown files already support multi-step instructions. Add a convention: `## Checkpoint: [description]` in the skill markdown means "present findings and wait for user input before proceeding." The chat loop recognizes checkpoint markers and pauses.

## 5. Techniques to Skip

### 5a. Full Plugin Marketplace Architecture

Claude Code's plugin system (manifest, auto-discovery, `${CLAUDE_PLUGIN_ROOT}`, marketplace distribution) is infrastructure for a platform with many contributors. co-cli is a single-user tool with a small team. The overhead of manifest schemas, convention-based discovery, and portable path references is not justified until co-cli has external plugin authors. co-cli's code-first tool registration (`agent.tool()` with `RunContext[CoDeps]`) provides better type safety and IDE support than markdown-based tool definition.

### 5b. Per-Agent Model Selection

Claude Code can run haiku for triage, sonnet for analysis, opus for reasoning -- across sub-agents in the same workflow. co-cli uses a single LLM provider (Gemini or Ollama). Adding per-tool-call model routing would require breaking pydantic-ai's single-agent model or creating a sub-agent framework. The complexity is not justified: Gemini 2.5 Pro is capable enough for all co-cli tasks, and Ollama models do not have a haiku/opus tier distinction. Revisit if co-cli adds Anthropic as a provider.

### 5c. Ralph Wiggum Autonomous Loops

The self-referential iteration pattern is elegant but dangerous for a local-first, approval-first tool. co-cli's design philosophy requires user consent for side effects. An autonomous loop that runs overnight, making file changes and shell commands without human review, violates the approval-first principle. If needed, a constrained version (read-only tools only, max 5 iterations, explicit user opt-in) would be safer.

### 5d. Prompt-Based Hooks (LLM-Evaluated)

Claude Code supports hooks where the evaluation logic itself is an LLM call: `{"type": "prompt", "prompt": "Evaluate if this tool use is appropriate..."}`. This is powerful but adds latency and cost to every tool invocation. co-cli's lightweight approval model (binary yes/no in the chat loop) is faster and predictable. LLM-evaluated hooks make sense for complex policy decisions but are over-engineering for co-cli's current scope.

### 5e. TodoWrite Task Tracking

While conceptually simple, adding a TodoWrite tool to co-cli has limited value in a single-agent model. The agent already tracks progress implicitly through conversation history. TodoWrite becomes valuable only with multi-agent workflows where sub-agents need shared state -- and co-cli does not have sub-agents yet. The memory system already serves the "persist state across turns" role.

## 6. Open Questions

### 6a. Sub-Agent Architecture for pydantic-ai

pydantic-ai supports creating child `Agent` instances, but co-cli has not explored this. Claude Code's command/agent separation demonstrates clear value (parallel codebase exploration, multi-strategy architecture design). **Question:** What is the minimal pydantic-ai sub-agent pattern that enables parallel tool execution with different system prompts? Does it require separate `Agent` instances or can `RunContext` achieve role switching?

### 6b. History Processor as Completion Verifier

Section 4b proposes a completion-verification history processor. **Question:** Can a pydantic-ai history processor access the pending response (the message about to be sent to the user) or only the history so far? If it cannot see the pending response, the verification must happen as a post-processing step outside the agent loop.

### 6c. Hook-Based vs. Tool-Based Safety Evolution

co-cli's safety model is tool-registration-based (`requires_approval`). Claude Code's is hook-based (PreToolUse hooks that can modify, block, or approve). **Question:** As co-cli adds more tools and safety rules, will the per-tool boolean scale? Or should co-cli introduce a lightweight policy layer (rules evaluated before tool execution) similar to hooks but without the full plugin infrastructure?

### 6d. Markdown-as-Program Viability

Claude Code's most distinctive bet is that markdown files with YAML frontmatter can replace orchestration code. The feature-dev command is 125 lines of markdown that orchestrates 7 phases with parallel sub-agents. **Question:** Would this pattern work with pydantic-ai, or does pydantic-ai's typed agent/tool model require Python code for orchestration? Could co-cli skills evolve to include agent-launch directives in markdown without sacrificing type safety?

### 6e. Session Lifecycle Events for co-cli

Claude Code has 9 lifecycle events. co-cli has none. **Question:** Which events would deliver the most value for co-cli? Candidates: SessionStart (load project context, check for updates), PreToolUse (safety policy), Stop (completion verification). What is the minimal event system that avoids the complexity of a full hook framework?
