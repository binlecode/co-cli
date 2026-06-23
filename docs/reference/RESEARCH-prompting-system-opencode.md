# RESEARCH: OpenCode Prompting System

**Source:** `~/workspace_genai/opencode` — refreshed to HEAD (commit `fbf889db8`, Mon Jun 22 19:57:21 2026)  
**Scope:** System prompt assembly, per-model routing, tool/skill/memory injection, agent loop, design patterns

---

## 1. Top-Level Architecture

OpenCode's prompting system is **modular and dynamic**: the system prompt is assembled fresh each turn from orthogonal concerns, not cached as a monolith.

**Assembly formula** (`session/prompt.ts:1309-1315`):

```
system = [...env, ...instructions, ...(skills ? [skills] : [])]
```

Three concerns compose independently:

| Block | Source | Reloaded per turn |
|---|---|---|
| `env` | `SystemPrompt.environment(model)` | Yes |
| `instructions` | `instruction.system()` | Yes — reads AGENTS.md/CLAUDE.md off disk |
| `skills` | `SystemPrompt.skills(agent)` | Yes — recalculated per permission state |

On top of this, a **model-specific base prompt** is selected via `SystemPrompt.provider(model)` and passed to the LLM as the leading system block (`session/system.ts:24-38`).

---

## 2. Model-Specific Prompt Routing

**Entry point:** `session/system.ts:24-38` — `provider(model)` function.

Routing is string-match on `model.api.id`:

```typescript
if (model.api.id.includes("gpt-4") || model.api.id.includes("o1") || model.api.id.includes("o3"))
  return [PROMPT_BEAST]
if (model.api.id.includes("gpt")) {
  if (model.api.id.includes("codex")) return [PROMPT_CODEX]
  return [PROMPT_GPT]
}
if (model.api.id.includes("gemini-")) return [PROMPT_GEMINI]
if (model.api.id.includes("claude"))  return [PROMPT_ANTHROPIC]
if (model.api.id.toLowerCase().includes("trinity")) return [PROMPT_TRINITY]
if (model.api.id.toLowerCase().includes("kimi"))    return [PROMPT_KIMI]
return [PROMPT_DEFAULT]
```

**Prompt files** live at `session/prompt/*.txt`, imported as string literals at module load time:

| File | When used |
|---|---|
| `anthropic.txt` | All Claude models |
| `gpt.txt` | Non-GPT4/o-series GPT models |
| `beast.txt` | GPT-4, o1, o3 |
| `gemini.txt` | Gemini family |
| `codex.txt` | Codex models |
| `trinity.txt` | Trinity models |
| `kimi.txt` | Kimi models |
| `default.txt` | All other / fallback |

Each file is a **complete, standalone system prompt** for that model family — no shared base; each file carries its own full instruction set. This is the whole-prompt-per-model pattern (high duplication; no DRY across files).

**Comparison to co-cli:** co-cli uses `BASE + per-profile overlay` (model-agnostic core + additive delta). OpenCode uses wholly separate files per model family — maximum per-model control, zero shared base.

---

## 3. Environment Block

`SystemPrompt.environment(model)` (`session/system.ts:54-90`) injects:

- Model ID and provider ID
- Working directory and workspace root
- Whether the directory is a git repo (`Is directory a git repo: yes/no` — a boolean, not a status summary)
- Platform and current date (`new Date().toDateString()`)
- Available project references (as `<available_references>` XML block, sorted by name)

This is regenerated every turn — the date is always current.

---

## 4. Instructions Block (AGENTS.md / CLAUDE.md)

`instruction.system()` (`session/instruction.ts:155-170`; path list assembled at `instruction.ts:60-67`) loads user/project instruction files in this order:

1. Global: `<global.config>/AGENTS.md`, `~/.claude/CLAUDE.md` (the CLAUDE.md entry is gated by the `disableClaudeCodePrompt` flag)
2. Project: `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md` (`CONTEXT.md` is marked deprecated) — searched upward from cwd to worktree root, first project-level match wins
3. Config-specified files from `config.instructions`

Files are injected as plain text. The search-upward behavior means nested projects can override outer ones.

---

## 5. Skills Block

`SystemPrompt.skills(agent)` (`session/system.ts:92-104`):

```typescript
if (Permission.disabled(["skill"], agent.permission).has("skill")) return
const list = yield* skill.available(agent)
return ["Skills provide specialized instructions...", Skill.fmt(list, { verbose: true })].join("\n")
```

Skills are injected as a human-readable list in the system prompt, gated by permission. The skill _tool_ loads the skill's full content on demand.

---

## 6. Plan/Build Context Injection (Reminders)

`session/reminders.ts:15-90` (`SessionReminders.apply`) injects synthetic message parts for session-level context. Only three prompt files are imported and wired (`reminders.ts:11-13`):

- **plan.txt** — injected when `agent.name === "plan"` (READ-ONLY constraints, planning workflow)
- **build-switch.txt** — injected when transitioning from plan agent to build agent
- **plan-mode.txt** — used only on the experimental plan-mode branch (gated by the `experimentalPlanMode` flag); its `${planInfo}` placeholder is substituted with plan-file existence info

These are injected as synthetic user message parts (`synthetic: true`, pushed onto the last user message — not system prompt additions), so they appear in the conversation history at the right moment.

> Note: `prompt/plan-reminder-anthropic.txt` exists on disk but is **not imported or referenced anywhere** in the source — it is an orphaned file (see §13).

---

## 7. Tool Injection

Tools are **not embedded in the system prompt** — they are passed as structured tool definitions to the LLM API.

**Pipeline** (`session/tools.ts:24-150`, `session/llm.ts:280-353`):

1. Tool registry lookup per `modelID`, `providerID`, `agent` (tools.ts:74-78)
2. Per-provider schema transformation via `ProviderTransform.schema(model, ToolJsonSchema.fromTool(item))` (tools.ts:79)
3. AI SDK tool wrapping with permission checks and plugin hooks (tools.ts:80-114)
4. MCP tool integration with the same schema transform + permission gates (tools.ts:117-150)
5. Tool filtering by `agent.permission + session.permission + user.tools` (llm/request.ts:201-203)

Tools passed to `streamText()` as `tools:` (llm.ts:318).

---

## 8. Agent-Specific Prompts

Subagents can carry their own prompt files (`agent/prompt/`):

| File | Agent | Purpose |
|---|---|---|
| `explore.txt` | explore | File-search specialist |
| `compaction.txt` | compaction | Context summarization |
| `summary.txt` | summary | Session summarization |
| `title.txt` | title | Title generation |

Agent's `Agent.Info.prompt` field (agent.ts:51) is optional — if set, it replaces (not appends to) the model-routed base prompt.

---

## 9. Agent Loop Structure

**Main loop** (`session/prompt.ts:1134-1385`, `runLoop(sessionID)`):

```
while (true):
  1. Fetch filtered messages from DB
  2. Extract last user / assistant / tasks
  3. Check exit: finish reason + no pending tool calls → break
  4. Handle subtasks, compaction triggers
  5. Generate title (step 1 only)
  6. Fetch model
  7. Apply session reminders (plan/build context) → synthetic parts
  8. Assemble system: env + instructions + skills
  9. Resolve tools (fresh per turn)
  10. Call LLM: streamText(system, messages, tools)
  11. Stream response, process tool calls
  12. Execute tools, append results
  13. Check context overflow → compaction if needed
  14. Loop
```

Every turn assembles a fresh system prompt. There is no caching of the assembled system string.

**Compaction** (`session/prompt.ts:1202-1221`): triggered automatically on context overflow; uses the `compaction.txt` agent prompt.

---

## 9b. Loop Logic: Dispatcher vs Recovery-Wrapper

The interesting comparison between OpenCode and co-cli is not who owns the loop (OpenCode hand-rolls `runLoop`; co delegates to pydantic-ai). Ownership is incidental. The loop *logic* — what each iteration decides, what state it carries, what it iterates over — is genuinely different.

**Grounding fact.** OpenCode passes **no** `stopWhen`/`stepCountIs` to `streamText` (`session/llm.ts:280-353`). Under the AI SDK that means each `handle.process` call is **one model request**: it executes the tool calls that request returned, marks `assistantMessage.finish = "tool-calls"` (`prompt.ts:347,379`), and the **outer `runLoop` is itself the tool-feedback loop** — it loops back and sends results to the model (exit condition keys on `finish !== "tool-calls"`, `prompt.ts:1164-1168`). co's tool-feedback loop, by contrast, lives **inside** one `agent.run_stream_events(...)` call (pydantic-ai owns it); co's outer loop iterates per *run*, not per request.

### 1. Reducer over the message log vs continuation over carried state

OpenCode's loop has **no in-memory turn state**. Every iteration re-reads all messages from the DB (`MessageV2.filterCompactedEffect`, `prompt.ts:1145`), derives `{user, assistant, finished, tasks}` (`MessageV2.latest`, 1149), and decides the next action purely by inspecting that state. The "program counter" lives in the message log:

- exit? → `lastAssistant.finish && !["tool-calls"].includes(finish) && !hasToolCalls` (1164-1168)
- pending subtask in `tasks`? → dispatch (1197)
- compaction task queued? → run (1202)
- last message overflowed? → enqueue compaction (1214)
- else → generate

It is an event-sourced reducer: `(message log) → next action`. Kill the process mid-loop, restart, re-read the log, resume exactly.

co carries `_TurnState` and mutates it across iterations (`orchestrate.py:120-168`): `current_input`, `current_history`, `tool_approval_decisions`, `model_requests`, `tool_reformat_budget`, `overflow_recovery_attempted`. None of this is re-derivable from the transcript. Each outer-loop pass is a *continuation* of the previous, not a re-derivation. Program counter = `_TurnState` + the Python exception stack.

### 2. What each loop iterates over

| | OpenCode | co |
|---|---|---|
| One outer iteration = | one **model request** | one **run** (= a full SDK tool-loop) |
| Who feeds tool results back to the model | **the loop itself** (re-loop on `finish="tool-calls"`) | the **SDK**, inside one `run_stream_events` — invisible to the loop |
| Loop's primary mode | iterating (many passes per turn is normal) | straight-line (happy path is **one** pass) |

co's happy path runs the outer `while True` body exactly once (`orchestrate.py:780-819`); it iterates a second time only to *recover* (length-continuation, overflow compaction, 400 reformulation). OpenCode's loop iterates as its normal mode — one lap per model request until the model stops calling tools. **OpenCode's loop is a tool-feedback engine; co's outer loop is a recovery engine wrapped around an SDK that hides the tool-feedback engine.**

### 3. Inline step-gating vs post-hoc abort

Because OpenCode's loop *is* the tool loop, it governs cadence in-band: it counts `step`, reads `agent.steps ?? Infinity` as `maxSteps`, and on the final step splices `MAX_STEPS_PROMPT` into the messages (`prompt.ts:1231-1232, 1327`) — it tells the model "this is your last move, wrap up." Graceful in-band degradation.

co cannot do this — the tool loop is inside pydantic-ai, so co never sees individual requests as they happen and has no place to inject a "last step" nudge. Its only inline guard is the per-request parallel-call cap (`MAX_TOOL_CALLS_PER_MODEL_REQUEST=3`) plus a consecutive-violation streak finalized at run boundaries (`orchestrate.py:438-442`). For the doom-loop case its only lever is a **post-hoc accumulator** — `model_requests` summed across runs, hard-aborting the turn at 40 (`_check_turn_caps`, 479-497). This is exactly why co's core-loop spec frames the cap as "circuit breaker, not work limit": it is a hard kill because co structurally cannot do OpenCode's soft "wrap up now."

### 4. Decision driver: state inspection vs exceptions + output types

OpenCode decides by **inspecting message state** — finish reason, presence of tool parts, queued tasks, overflow flag. Every branch is positive dispatch off observed state (*dispatch-shaped* control flow).

co decides by **catching exceptions and pattern-matching output types** (`orchestrate.py:821-871`): `DeferredToolRequests` → approval loop; `ModelHTTPError` + `is_context_overflow` → overflow recovery; code 400 → reformulation budget; `finish_reason=="length"` + text present → token-boost retry; `ModelAPIError`/`TimeoutError`/`UnexpectedModelBehavior` → terminal; `KeyboardInterrupt` → interrupted. The body is a `try` with seven `except` arms — *error-shaped* control flow where the happy path falls straight through and every branch is a recovery handler.

### 5. Where compaction, subtasks, and approval live

| Concern | OpenCode | co |
|---|---|---|
| **Compaction** | First-class loop work: queued `task.type==="compaction"` branch (1202), reactive `isOverflow` enqueue (1214-1221), and a `"compact"` verb returned from the model call (1365) — all re-enter the same loop | Split across two non-loop layers: a history **processor** the SDK runs before each request (`proactive_window_processor`), and an **exception arm** (`_attempt_overflow_recovery`, `orchestrate.py:697-725`). The loop never has a "now compacting" state |
| **Subtasks/subagents** | A loop branch (`handleSubtask`, 1197) — sub-work is a peer of generation | Pushed **out of the loop** via `fork_deps()`; `run_turn` is single-purpose |
| **Approval** | No approval state in the loop — permission checked at tool-execution inside `streamText`'s tool wrapping; a denial is just a tool result | A **dedicated extra loop level** (`_run_approval_loop`, 445-476), forced entirely by pydantic-ai surfacing approval as `DeferredToolRequests` (SDK pauses the run, hands control back, co re-enters with decisions) |

### One-sentence version

OpenCode's loop is a **stateless reducer that dispatches the next unit of work** (generate / subtask / compact) off the message log, looping once per model request and governing cadence in-band. co's outer loop is a **stateful recovery machine** that delegates the entire tool-feedback loop to its SDK, runs straight-through on success, and exists almost entirely to catch and recover from what the SDK surfaces back — approval, overflow, length, malformed output — with only a post-hoc turn-level counter where OpenCode has a graceful step gate. Neither is "the loop" in the same sense: OpenCode's is a work dispatcher, co's is an exception-handling wrapper around a hidden dispatcher.

---

## 10. Provider-Level System Prompt Formatting

`session/llm/request.ts:99-112` — how the assembled `system[]` array is passed to the provider:

- **OpenAI OAuth**: `options.instructions = system.join("\n")`  
- **Other providers**: System blocks added as `{ role: "system", content: x }` messages

Model-specific options merged in order: `base → model.options → agent.options → variant` (llm/request.ts:80-98).

---

## 11. Plugin Hooks on System Prompt

`session/llm/request.ts:69-78` — plugins can transform the system prompt array before it reaches the LLM:

```typescript
yield* input.plugin.trigger(
  "experimental.chat.system.transform",
  { sessionID: input.sessionID, model: input.model },
  { system },
)
```

The hook fires at `session/llm/request.ts:69-73` — after the system array is assembled but before it reaches the LLM. Plugins mutate `output.system` in place. After the trigger, OpenCode consolidates any plugin-appended blocks: header stays as `system[0]`, rest joined as `system[1]` (lines 74-78). The hook name carries the `experimental.` prefix, signalling unstable API. Defined in `packages/plugin/src/index.ts`.

---

## 12. Plugin Shell-Command Templating Asymmetry

OpenCode supports `!`cmd`` syntax for dynamic content via shell execution (`bashRegex` at `session/prompt.ts:1672`). This is applied via `ConfigMarkdown.shell()` **only for command markdown definitions** (`prompt.ts:1443`), not for AGENTS.md or instruction files. `instruction.ts` has no `ConfigMarkdown` import and does no shell processing. This is a known asymmetry acknowledged as a bug — shell expansion in instruction files does not work.

---

## 13. Orphaned Prompt File

`session/prompt/copilot-gpt-5.txt` exists on disk but is not imported in `system.ts` and has no routing entry. It is unreachable at runtime — likely a work-in-progress for a future model family.

---

## 14. Architectural Comparison: OpenCode vs co-cli

| Dimension | OpenCode | co-cli |
|---|---|---|
| **Base prompt** | Whole separate file per model family (`anthropic.txt`, `gpt.txt`, etc.) | Single model-agnostic BASE shared by all profiles |
| **Per-model variation** | Completely separate files — maximum control, zero DRY | Additive overlay on top of BASE (BASE + profile delta) |
| **Composition** | `env + instructions + skills` assembled per turn | BASE + overlay composed at prompt-assembly time |
| **Skills injection** | Verbose list in system prompt; skill tool loads full content on demand | `<available_skills>` manifest rendered per-turn as the `skill_manifest_prompt` dynamic instruction (re-reads the live index, outside the cached prefix); skill tool loads on demand |
| **Memory injection** | Instructions files (AGENTS.md/CLAUDE.md) injected as plain text | Memory search-driven recall (FTS5 BM25); USER.md always-injected profile |
| **Session context** | Synthetic message parts (reminders.ts) | No session-level synthetic injection today |
| **Tool passing** | Structured tool definitions (not in prompt text) | Structured tool definitions (not in prompt text) |
| **Per-provider schema transform** | `ProviderTransform.schema()` per tool, per model | N/A (single provider today) |
| **System prompt freshness** | Assembled fresh every turn (no caching) | Static instructions assembled once per session; dynamic instruction suffix (skills, deferred tools, time, safety, wrap-up) recomputed per request |
| **Plugin extension** | `experimental.chat.system.transform` hook mutates system array in place | No plugin hook on system prompt |
| **Agent subprompts** | Per-agent `.txt` files (explore, compaction, title, summary) | No per-agent subprompt files today |

---

## 15. Key Takeaways for co-cli Design

1. **OpenCode's whole-file-per-model is the maximum-duplication end of the spectrum.** co-cli's BASE+overlay is already more DRY. No reason to abandon the overlay architecture.

2. **Dynamic per-turn system prompt re-assembly** is the peer pattern (OpenCode re-reads AGENTS.md every turn). co-cli already re-evaluates per-turn dynamic instructions (`skill_manifest_prompt`, `deferred_tool_awareness_prompt`, `current_time_prompt`, `safety_prompt`, `wrap_up_prompt`) every request — what it does *not* do is reload project instruction files (AGENTS.md/CLAUDE.md) per turn. Adopting that specific reload would need no architectural change.

3. **Reminders as synthetic message parts** (not system prompt additions) is a useful pattern for injecting session-state context at the right conversation moment — co-cli has no equivalent today.

4. **Plugin hook on system prompt** (`experimental.chat.system.transform`) lets plugins append to the system array at request time. co-cli doesn't have this today. The `experimental.` prefix signals it's unstable API.

5. **Per-agent subprompt files** (explore.txt, compaction.txt) are a clean way to scope subagent behavior without polluting the main system prompt.

6. **Permission-gated skills list** in system prompt (only listed if "skill" permission enabled) is the same pattern co-cli uses.

7. **MCP tool integration** uses the same schema-transform pipeline as native tools — no special-casing in the prompt.

---

## 14. Common Concern List Across All Per-Model Prompts

All 7 per-model prompt files (`anthropic`, `default`, `gemini`, `gpt`, `beast`, `kimi`, `trinity`) independently restate the same five concerns. There is no shared code base — each file is a standalone restatement — but the concern list converges across all of them.

### Universal (7/7)

| Concern | What all files cover |
|---|---|
| **Identity** | "You are OpenCode/opencode, an interactive CLI tool/agent for software engineering" |
| **Output discipline** | No preamble, no postamble, no summarizing what you just did |
| **Tool usage** | Parallelize independent calls; prefer file tools over bash for file ops |
| **Engineering workflow** | Read/understand codebase first → implement → verify (lint/test) |
| **Output format** | GitHub-flavored Markdown, monospace CLI rendering context |

### Near-universal (5–6/7)

| Concern | Present in | Missing from |
|---|---|---|
| **Follow existing conventions** | All (varies in depth) | — |
| **No auto-commit** | anthropic, default, kimi, trinity, gpt (partial) | gemini, beast |
| **Security / no secrets** | default, gemini, kimi, trinity | anthropic, gpt, beast |

### Absent from most (≤4/7)

| Concern | Files |
|---|---|
| URL policy ("NEVER guess URLs") | anthropic, default only |
| No emojis unless asked | anthropic, default, gpt, kimi |
| Code references (`file:line` pattern) | anthropic, default, beast, kimi |
| Task/todo tracking | anthropic (TodoWrite), trinity (markdown todo) |

---

## 15. General (Non-Coding-Specific) Common Concerns — Drill-Down

Three of the five universal concerns are general agent behavior, not coding-specific:

### 1. Identity

Every file opens with an explicit self-declaration. The wording varies — some say "the best coding agent on the planet" (anthropic, beast), others are neutral ("an interactive CLI agent", "an interactive CLI tool"). The constant is: the agent names itself, names its medium (CLI), and names its domain (software engineering). This is the one sentence all 7 share in structure.

### 2. Output Discipline

All 7 files prohibit the same class of output behaviors, each phrased differently:

- No conversational openers ("Got it", "Great question", "Sure!")
- No trailing summaries of what was just done
- No narrating planned actions without doing them ("I will now...")
- No over-explanation of obvious steps

**Depth varies significantly.** `default.txt` and `kimi.txt` are most explicit (rule + multiple verbatim bad-examples). `gpt.txt` and `beast.txt` express this via tone framing ("deeply pragmatic", "friendly coding teammate") rather than prohibition lists. `trinity.txt` and `beast.txt` also allow longer pre-action announcements ("Always tell the user what you are going to do before making a tool call"), which contradicts the spirit of the others.

### 3. Output Format

All 7 agree on: GitHub-flavored Markdown, monospace CLI font, CommonMark rendering. Beyond this baseline, three distinct sub-styles emerge:

| Sub-style | Files | Character |
|---|---|---|
| **Hard verbosity cap** | default, kimi | "fewer than 4 lines unless asked for detail"; one-word answers preferred |
| **Structured response channels** | gpt, beast | `commentary` (progress updates) vs `final` (completed response); explicit section/header rules |
| **Principle-based** | anthropic, gemini, trinity | "concise and direct" without a line cap; examples shown rather than rules stated |

The hard-cap files (default, kimi) are the most constraining. The channel-based files (gpt, beast) are the most structured. The principle-based files give the most latitude.

---

## 16. Key File Index

| File | Purpose |
|---|---|
| `session/system.ts` | `provider()` routing (24-38), `environment()` (54-90), `skills()` (92-104) |
| `session/prompt.ts` | `runLoop()` (1134-1385), system assembly (1309-1315) |
| `session/instruction.ts` | `system()` loader (155-170); AGENTS.md/CLAUDE.md path list (60-67) |
| `session/reminders.ts` | Plan/build synthetic message injection (15-90) |
| `session/tools.ts` | Tool registry, schema transform, filtering (24-150) |
| `session/llm.ts` | `streamText()` invocation (280-353) |
| `session/llm/request.ts` | Request preparation, provider format, tool filter (56-204) |
| `session/prompt/*.txt` | Per-model base prompts (anthropic, gpt, beast, gemini, codex, ...) |
| `agent/prompt/*.txt` | Per-subagent prompts (explore, compaction, summary, title) |
