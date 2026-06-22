# RESEARCH: OpenClaw Prompting System

**Source:** `~/workspace_genai/openclaw` — pulled to HEAD 2026-06-22 (commit `e66aa357f85b46b5d370efe181cd005a75fbab22`)  
**Scope:** System prompt assembly, per-model routing, tool/skill injection, environment context, agent loop, design patterns

---

## 1. Top-Level Architecture

OpenClaw's prompting system is **modular and hierarchical**: the system prompt is assembled fresh at the start of each agent attempt from orthogonal concerns — runtime parameters, configuration-derived values, context files, and provider-specific overlays.

**Assembly formula** (`agents/system-prompt.ts:682–1358`, `agents/system-prompt-config.ts:58–65`):

```
systemPrompt = buildConfiguredAgentSystemPrompt({
  ...renderParams,
  ...configParams  // owner display, TTS hint, model aliases, memory, fs-workspace-only
})
```

Within `buildAgentSystemPrompt()` (lines 682–1358), the prompt composes these orthogonal blocks:

| Block | Source | Content | Cache |
|---|---|---|---|
| **Stable prefix** | lines 1044–1278 | Tooling, Workspace, Safety, Skills, Memory, Docs, User Identity, Time, Bootstrap, Workspace Files header, Silent Replies | Cached (LRU) per hash of stable inputs |
| **Dynamic suffix** | lines 1281–1358 | Project context files, WebChat canvas, Messaging, Voice, Extra system prompt, Reactions, Heartbeats, Runtime line, Reasoning | Assembled per turn |

**Cache boundary:** `SYSTEM_PROMPT_CACHE_BOUNDARY` (`agents/system-prompt-cache-boundary.ts`) is a marker string inserted between stable and dynamic sections. Anthropic's prompt-caching backend uses this boundary for KV cache reuse — content above it stays cached across turns; content below it is re-sent each call.

**Key pattern:** Stable prefix is calculated once and cached by hash; dynamic content is appended below the boundary every turn. This gives KV-cache efficiency on the stable core while keeping per-turn context fresh.

---

## 2. Model/Provider-Specific Prompt Routing

**Entry points:** `plugins/provider-runtime.ts:215–238` (`resolveProviderSystemPromptContribution()`) and `plugins/provider-runtime.ts:270–286` (`transformProviderSystemPrompt()`).

Routing is **plugin-driven**, not hardcoded string-matching:

```typescript
// providers.ts — each provider plugin can declare:
resolveSystemPromptContribution(model, ctx) => {
  stablePrefix?: string,
  dynamicSuffix?: string,
  sectionOverrides?: { interaction_style?, tool_call_style?, execution_bias? }
}
transformSystemPrompt(model, systemPrompt) => string
```

**GPT-5 overlay** (`agents/gpt5-prompt-overlay.ts:128–152`): If `model.id` matches `/gpt-5/i` AND provider is in `[codex, codex-cli, openai, azure-openai, azure-openai-responses]`, apply GPT-5 personality overlays (interaction style, heartbeat guidance, behavior contract). This is the only hardcoded per-model-family branch; all other routing is via plugin.

**Section overrides** (`agents/system-prompt-contribution.ts:6–35`): Providers can replace whole named sections:

| Section key | What it controls |
|---|---|
| `interaction_style` | How the agent responds to users |
| `tool_call_style` | Tool invocation discipline |
| `execution_bias` | Action-taking bias / proactivity |

The assembled system prompt passes through `transformProviderSystemPrompt()` (lines 277–286), which applies plugin transformations and text-level replacements after the full prompt is built.

**Comparison to OpenCode:** OpenCode uses wholly separate `.txt` files per model family (anthropic.txt, gpt.txt, beast.txt, etc.) — zero DRY. OpenClaw uses a **single base prompt + provider-plugin overlays** — more composable and maintainable.

**Comparison to co-cli:** co-cli uses BASE + additive per-profile overlay (profile delta applied at assembly time). OpenClaw's plugin hooks are per-provider rather than per-profile, but the structural idea (base + delta) is the same.

---

## 3. Environment Block

**Runtime metadata injection** (`agents/system-prompt.ts:1351–1356`, `agents/system-prompt-params.ts:47–105`):

Built fresh at attempt start via `buildSystemPromptParams()`:

- **Agent identity** — agentId, sessionKey, sessionId
- **Host & OS** — host, os, arch, node version
- **Model** — current model ID, default model, provider
- **Repository** — repo root (discovered from workspace via `findGitRoot()`; overridable in config)
- **Timezone & time** — user timezone and formatted current time (via `resolveUserTimezone()`, `formatUserTime()`)
- **Active process sessions** — references to any background exec processes
- **Channel capabilities** — channel name, chat type, capabilities list (`inlineButtons`, `richtext`, etc.)

**Injection point:** Rendered as a single `Runtime:` line at lines 1351–1356:

```
Runtime: agent=main | session=agent:main:session1 | sessionId=abc... | host=... | os=... | node=... | model=claude-3.5-sonnet | channel=slack | capabilities=inlineButtons,richtext | thinking=high
```

This is regenerated on every attempt, so time, active processes, and runtime state are always current.

---

## 4. Instructions Block (AGENTS.md / CLAUDE.md Loading)

**OpenClaw does NOT perform per-turn reload of instruction files.** Unlike OpenCode, which reads AGENTS.md fresh each turn, OpenClaw has no upward-directory-search loader for workspace-level instruction overrides.

**Instructions come from two sources instead:**

1. **Config-derived metadata** (`agents/system-prompt-config.ts:35–55`):
   - Owner display (raw or hashed identifiers) — from config
   - Model aliases — from config
   - TTS hints — from config
   - Memory citations mode — from config
   - FS workspace-only policy — from config

2. **Context engine file discovery** (`agents/system-prompt.ts:191–238`):
   - `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `TOOLS.md`, `BOOTSTRAP.md`, `MEMORY.md` files are loaded by the context engine and passed as `contextFiles` to `buildEmbeddedSystemPrompt()` (lines 85–86, 995–1000, 1253–1258, 1284–1289).
   - These are sorted by basename and inserted below the cache boundary in the "Project Context" section.
   - There is no search-upward behavior; file discovery is workspace-rooted.

**Implication:** If an AGENTS.md is edited mid-session, the change does NOT take effect until the next attempt (no per-turn reload). For co-cli, this is the opposite of OpenCode — a deliberate trade: load cost is paid once per attempt, not per turn.

---

## 5. Skills Block

**Skills are prompt-hinted, not listed inline.** When the "skill" permission is enabled, the "## Skills" section is injected (`agents/system-prompt.ts:269–284`, 967–970, 1140–1141):

```markdown
## Skills
Scan <available_skills>. If one clearly applies, read its SKILL.md at exact <location> with `read`, then follow it.
...
[skillsPrompt content]
```

The `skillsPrompt` is derived from config or passed at runtime; it typically provides a URL or path to a skill catalog. The agent then uses the `skill_workshop` tool to load full skill content on demand.

**Unlike OpenCode:** OpenCode injects a verbose runtime-enumerated list of available skills into the system prompt; the skill tool loads full content on demand. OpenClaw defers the entire manifest — the prompt gives the agent a discovery instruction, not a precomputed list. This is lighter on prompt tokens but requires an extra tool call to know what skills exist.

---

## 6. Session/Plan Context Injection (Reminders)

**OpenClaw does NOT inject synthetic message parts** for session-level context (no equivalent to OpenCode's `reminders.ts` plan.txt / build-switch.txt pattern).

All session context flows through:
- The base system prompt (runtime line, session ID)
- `extraSystemPrompt` parameter — injected below the cache boundary as a "## Subagent Context" or "## Group Chat Context" section (`agents/system-prompt.ts:1315–1320`)

When a subagent or voice-consult caller needs to inject additional context, it passes it as `extraSystemPrompt` (e.g., `agents/talk/agent-consult-runtime.ts:241`, 316–318), not as a synthetic user-side message. This means session context changes are system-prompt changes, not conversation-history injections.

---

## 7. Tool Injection

Tools are **NOT embedded as text in the system prompt**. Two parallel channels handle them:

1. **Tool summaries in prompt text** (`agents/system-prompt.ts:798–874`): Core tool descriptions are rendered in the "## Tooling" section as plain-text bullet-point summaries (names, brief descriptions). This tells the agent what tools exist and how to use them.

2. **Structured tool schemas via API** (`agents/sessions/agent-session.ts:890–905`): The `AgentSession` maintains a `toolRegistry` (Map<toolName, AgentTool>) and passes structured schema definitions to the LLM provider's tool API — not as prompt text.

3. **Permission gating** (lines 843–847): Tool names are normalized to lowercase; tools not available for the current permission context are excluded from both the prompt summary and the structured schema list.

4. **Deferred schemas** (lines 1057–1059): If `toolSchemaDirectoryPrompt` is provided (for MCP tools or out-of-band schemas), it is injected as a "### Deferred Tool Schemas" section — a pointer for the agent to discover them at runtime.

5. **MCP integration** (`plugins/types.ts`): Provider plugins register MCP servers; MCP tools are discovered, wrapped with permission checks, and passed alongside native tools via the same structured API channel.

**Pipeline per turn:**
```
toolRegistry → permission filter → structured schema → LLM tool API
                                 → plain-text summary → system prompt "## Tooling"
```

---

## 8. Agent-Specific Subprompts

**OpenClaw has NO per-agent system prompt files** (no equivalent to OpenCode's `explore.txt`, `compaction.txt`, `title.txt`, `summary.txt`).

Instead, subagent behavior is controlled via `promptMode` and `extraSystemPrompt`:

1. **`promptMode: "minimal"`** (`agents/system-prompt.ts:64`, 709, 928–929): Filters out verbose sections — Owner identity, Docs, Voice, WebChat, Messaging workflows, Bootstrap — keeping only Tooling, Workspace, and Runtime. Used for subagents that don't need the full guidance surface.

2. **`extraSystemPrompt` parameter**: Callers inject subagent-specific instructions (e.g., compaction instructions, voice-bridge context) at call time — not from a file.

3. **Compaction** (`agents/compact.ts:1107–1153`): The compaction agent runs with the same unified system prompt, minimal mode, plus compaction-specific guidance passed via `extraSystemPrompt`. No separate compaction.txt.

4. **Voice consult** (`agents/talk/agent-consult-runtime.ts:297–318`): Passes a custom `prompt` and brief voice-bridge instruction via `extraSystemPrompt`.

**Net effect:** All subagent behavior is controlled by three levers — `promptMode`, `extraSystemPrompt`, and `tools` list — rather than separate files. This keeps guidance in one place at the cost of less per-agent customization granularity.

---

## 9. Agent Loop Structure

**Main embedded-agent loop** (`agents/embedded-agent-runner/run.ts`, `agents/embedded-agent-runner/run/attempt.ts:200–250`):

```
for each attempt:
  1. Resolve model (with auth profile fallbacks)
  2. Build runtime parameters: buildSystemPromptParams()
     → time, repo root, channel, capabilities, active sessions
  3. Load context files from context engine
  4. Build system prompt (once per attempt):
     a. Config-derived fields: buildConfiguredAgentSystemPrompt()
     b. Core prompt: buildAgentSystemPrompt() [stable prefix cached]
     c. Provider transformation: transformProviderSystemPrompt()
  5. Prepare stream function (caching, recovery, text transforms)
  6. Call LLM.streamChat(systemPrompt, messages, tools)
  7. Stream and process tool calls
  8. Check context overflow → spawn compaction subagent if needed
  9. Check retry triggers (rate limit, failover)
  10. Return result or retry
```

**System prompt freshness:** Built once per attempt. NOT rebuilt mid-turn. If context files or runtime state changes, the next attempt rebuilds it.

**Compaction:** Triggered on context overflow; compaction agent is spawned via `sessions_spawn` as a subagent with `promptMode: "minimal"` + compaction instructions in `extraSystemPrompt`. Full compaction logic at `agents/compact.ts:1–200`.

---

## 10. Provider-Level System Prompt Formatting

**System prompt is a single string**, not an array (`plugins/provider-runtime.ts:270–286`):

After assembly, the system prompt is one string. The provider plugin applies `transformSystemPrompt()` before it reaches the LLM backend:

- **Anthropic provider**: Passes `systemPrompt` as the leading system-role message block, using Anthropic's multi-block cache-enabled format with `SYSTEM_PROMPT_CACHE_BOUNDARY` as the cache breakpoint.
- **OpenAI provider**: Wraps `systemPrompt` as a `{ role: "system", content: ... }` message.
- **Other providers**: Each provider plugin serializes into its own request format.

**Session-level prompt storage** (`agents/sessions/agent-session.ts:877–879`): The current system prompt is stored in `this.agent.state.systemPrompt`; updated via `setBaseSystemPrompt()` when rebuilt.

Model-specific options are merged in the provider plugin at request time — not at system-prompt-build time.

---

## 11. Plugin / Extension Hooks on the System Prompt

**Plugins can mutate the system prompt in two ways:**

1. **Pre-assembly contribution** (`plugins/provider-runtime.ts:215–238`): Plugins declare `resolveSystemPromptContribution()` — called before `buildAgentSystemPrompt()` finalizes. Contributions are merged as `stablePrefix`, `dynamicSuffix`, or named `sectionOverrides`. This is compile-time / initialization-time, not per-turn.

2. **Post-build string transform** (`plugins/provider-runtime.ts:270–286`): Plugins declare `transformSystemPrompt()` — called after the full string is assembled, allowing text replacements, appends, or redactions before the LLM call.

3. **Character-level text transforms** (`plugins/plugin-text-transforms.ts`): Input/output character replacements that apply to system prompt and message content alike.

There is **no equivalent to OpenCode's `experimental.chat.system.transform` runtime hook** — OpenClaw's plugin API is defined at plugin init, not invoked per-turn from session code.

---

## 12. Shell-Command / Dynamic Content Templating in Prompts

**OpenClaw does NOT support `!`cmd`` or shell-command interpolation** in any prompt files or instruction files.

All dynamic content (repo root, time, channel capabilities, etc.) is injected programmatically via `buildSystemPromptParams()` and the context engine. There are no text-file templates with shell interpolation.

This is simpler and safer than OpenCode's `bashRegex` approach (which is acknowledged as buggy — shell expansion doesn't work in instruction files).

---

## 13. Orphaned or Dead Prompt Files

**No orphaned prompt files exist.** OpenClaw has **no `.txt` prompt files on disk** — all prompts are built in TypeScript code.

The only hardcoded prompt strings are module-level constants in:
- `agents/gpt5-prompt-overlay.ts:20–85` — GPT5_FRIENDLY_CHAT_PROMPT_OVERLAY, GPT5_BEHAVIOR_CONTRACT, etc.

These are reachable and used; not dead.

---

## 14. Cross-Cutting Concern List: Across All Prompt Assembly

OpenClaw's single unified system prompt builder naturally ensures consistency. Because there is one code path (not seven files), concerns cannot diverge across model families.

### Universal (all models, all modes)

| Concern | What is covered | Where |
|---|---|---|
| **Identity** | "You are a personal assistant running inside OpenClaw" | `system-prompt.ts:1046` |
| **Tool discipline** | Parallelize independent calls; don't poll; use tools over bash | lines 1049–1066 |
| **Output discipline** | Markdown, MEDIA directives, no preamble | lines 1245–1246, 413–422 |
| **Execution bias** | Act on actionable requests; continue until done | lines 449–463 |
| **Safety** | No self-preservation; obey stops; don't bypass safeguards | lines 959–966 |

### Near-universal (all except minimal mode)

| Concern | Where |
|---|---|
| **OpenClaw control** (gateway, config, restart) | lines 1134–1152 |
| **Memory** (MEMORY.md loading, memory_search) | lines 974–979 |
| **Sandbox awareness** (container/host mounts) | lines 1174–1236 |

### Conditional (vary by config / runtime capability)

| Concern | When | Lines |
|---|---|---|
| **Sub-agent delegation** | If `sessions_spawn` available AND `mode=prefer` | 92–119 |
| **Heartbeat guidance** | If heartbeat prompt provided | 240–251 |
| **Skills** | If skill permission enabled | 269–284 |
| **ACP harness spawning** | If `acpEnabled && !sandboxed` | 1069–1085 |
| **Reactions** | If Telegram + reaction guidance provided | 1321–1343 |
| **Reasoning format** | If `reasoningTagHint` set | 1248–1250 |
| **Voice (TTS)** | If `ttsHint` provided | 585–594 |

**GPT-5 only** (provider overlay):

| Concern | Where |
|---|---|
| **Interaction style override** | `gpt5-prompt-overlay.ts:128–152` |
| **Behavior contract** | `gpt5-prompt-overlay.ts:20–85` |

---

## 15. Architectural Comparison: OpenCode vs OpenClaw vs co-cli

| Dimension | OpenCode | OpenClaw | co-cli |
|---|---|---|---|
| **Base prompt strategy** | Whole separate file per model family | Single unified prompt builder + provider-plugin overlays | Single model-agnostic BASE shared by all profiles |
| **Per-model variation** | Completely separate files (anthropic.txt, gpt.txt, beast.txt, etc.) — zero DRY | Plugin-declared `stablePrefix`/`dynamicSuffix`/`sectionOverrides` | Additive overlay on BASE (BASE + profile delta) |
| **Composition model** | `env + instructions + skills` assembled per turn from separate concerns | Single builder; concerns are named sections within one function | BASE + overlay at prompt-assembly time |
| **Skills injection** | Verbose runtime-enumerated list in system prompt; tool loads content on demand | Prompt hint + discovery instruction only; manifest deferred to `skill_workshop` tool | Skills manifest injected via static prompt |
| **Memory injection** | AGENTS.md/CLAUDE.md as plain text; reloaded per turn | Context engine file discovery (AGENTS.md, MEMORY.md etc.); no per-turn reload | Memory search-driven recall (BM25) + USER.md always-injected |
| **Session context** | Synthetic message parts (plan.txt, build-switch.txt) injected into history | `extraSystemPrompt` parameter only; no synthetic message injection | No session-level injection today |
| **Tool passing** | Structured tool definitions (not in prompt text) | Structured tool schemas via API + plain-text summaries in "## Tooling" | Structured tool definitions (not in prompt text) |
| **Per-provider schema transform** | `ProviderTransform.schema()` per tool, per model | `transformSystemPrompt()` post-build; MCP via context engine | N/A (single provider today) |
| **System prompt freshness** | Assembled fresh every turn (no caching) | Assembled fresh per attempt; stable prefix cached by hash (LRU) | Assembled fresh per session (not per turn) |
| **Plugin extension** | `experimental.chat.system.transform` hook mutates system array pre-LLM | `resolveSystemPromptContribution()` at init; `transformSystemPrompt()` post-build | No plugin hook on system prompt |
| **Agent subprompts** | Per-agent `.txt` files (explore, compaction, title, summary) | `promptMode` enum (full/minimal/none) + `extraSystemPrompt` at call site | No per-agent subprompt files today |
| **Cache boundary marker** | N/A | `SYSTEM_PROMPT_CACHE_BOUNDARY` string — Anthropic KV-cache reuse | N/A |

---

## 16. Key Takeaways for co-cli Design

1. **Plugin-driven overlays scale better than hardcoded per-model routing.** OpenClaw's `resolveSystemPromptContribution()` / `transformSystemPrompt()` plugin hooks mean adding a new model family requires no core code change. co-cli's BASE+overlay is already on this axis — the plugin model is the next step if multi-provider support is ever needed.

2. **Stable prefix + dynamic suffix + cache boundary is a real optimization.** If co-cli targets Anthropic's prompt caching, separating the stable core (tools, guidance, workspace config) from dynamic per-turn content (time, active sessions, channel state) lets the KV cache reuse the stable block. The cost is one extra marker string; the payoff is cache hits on every turn after the first.

3. **Minimal mode for subagents is simpler than per-agent prompt files.** Instead of a separate compaction.txt or explore.txt, a `promptMode` flag filters the appropriate sections. Guidance stays in one place; per-agent behavior is a call-site decision, not a file-layout decision. Co-cli could adopt this when subagents need lighter prompts.

4. **No per-turn instruction reload is a deliberate trade-off, not an oversight.** OpenClaw accepts stale instructions within a session in exchange for not re-reading disk on every turn. Co-cli's current per-session assembly sits in the same position. If per-turn freshness ever matters (live AGENTS.md edits during a session), OpenCode's pattern is the reference.

5. **All dynamic content injected programmatically — no shell templating.** OpenClaw's avoidance of `!cmd` interpolation in instruction files is the safer, more predictable path. Co-cli should stay on this side of the line.

6. **Skills deferred to a discovery hint is lighter on tokens.** OpenClaw does not enumerate skills in the system prompt — it tells the agent how to find them. This trades one extra tool-call round-trip for shorter prompts. Co-cli's current manifest-injected approach is richer (agent sees the list up front); the right choice depends on catalog size and model capability.

7. **Context engine file discovery is cleaner than upward-search for project instructions.** Rather than AGENTS.md upward traversal, OpenClaw delegates workspace file discovery to a dedicated engine. This could simplify co-cli's config-file loading if workspace-level instruction files become first-class.

---

## 17. Key File Index

| File | Purpose | Key Lines / Functions |
|---|---|---|
| `agents/system-prompt.ts` | Main system prompt builder | `buildAgentSystemPrompt()` (682–1358); stable prefix (1044–1278); dynamic suffix (1281–1358) |
| `agents/system-prompt-config.ts` | Config-derived prompt fields | `buildConfiguredAgentSystemPrompt()`, `resolveAgentSystemPromptConfig()` (58–65) |
| `agents/system-prompt-params.ts` | Runtime parameter collection | `buildSystemPromptParams()` (47–105) |
| `agents/system-prompt-contribution.ts` | Provider overlay type definitions | Type defs: `stablePrefix`, `dynamicSuffix`, `sectionOverrides` (6–35) |
| `agents/system-prompt-cache-boundary.ts` | Cache boundary marker | `SYSTEM_PROMPT_CACHE_BOUNDARY` constant |
| `agents/gpt5-prompt-overlay.ts` | GPT-5 model-family overlay | `resolveGpt5SystemPromptContribution()` (128–152); constants (20–85) |
| `agents/embedded-agent-runner/system-prompt.ts` | Embedded-run prompt wrapper | `buildEmbeddedSystemPrompt()`, `applySystemPromptToSession()` |
| `agents/embedded-agent-runner/run/attempt-system-prompt.ts` | Per-attempt prompt build | `buildAttemptSystemPrompt()` (39–59) |
| `agents/embedded-agent-runner/run/attempt.ts` | Main attempt orchestrator | Calls `buildSystemPromptParams()` at ~line 1912 |
| `plugins/provider-runtime.ts` | Provider plugin integration | `resolveProviderSystemPromptContribution()` (215–238); `transformProviderSystemPrompt()` (270–286) |
| `agents/sessions/agent-session.ts` | Session state and prompt storage | `setBaseSystemPrompt()`, `systemPrompt` getter (877–905) |
| `agents/talk/agent-consult-runtime.ts` | Voice consult agent entry | `consultRealtimeVoiceAgent()` with `extraSystemPrompt` (297–318) |
| `agents/compact.ts` | Compaction agent | Compaction instructions via `extraSystemPrompt` (1107–1153) |
