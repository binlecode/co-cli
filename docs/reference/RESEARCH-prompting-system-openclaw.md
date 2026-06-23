# RESEARCH: OpenClaw Prompting System

**Source:** `~/workspace_genai/openclaw` — refreshed to HEAD (commit `abd8a46b0a`, Tue Jun 23 10:11:18 2026)  
**Scope:** System prompt assembly, per-model routing, tool/skill injection, environment context, agent loop, design patterns

---

## 1. Top-Level Architecture

OpenClaw's prompting system is **modular and hierarchical**: the system prompt is assembled fresh at the start of each agent attempt from orthogonal concerns — runtime parameters, configuration-derived values, context files, and provider-specific overlays.

**Assembly formula** (`src/agents/system-prompt.ts:682–1419`, `src/agents/system-prompt-config.ts:58–65`):

```
systemPrompt = buildConfiguredAgentSystemPrompt({
  ...renderParams,
  ...configParams  // owner display, TTS hint, model aliases, memory, fs-workspace-only
})
```

Within `buildAgentSystemPrompt()` (lines 682–1419; the param object closes at 754 and the body runs to the trailing `return lines.filter(Boolean).join("\n")`), the prompt composes these orthogonal blocks:

| Block | Source | Content | Cache |
|---|---|---|---|
| **Stable prefix** | lines 1044–1277 | Tooling, Workspace, Safety, Skills, Memory, Docs, User Identity, Time, Bootstrap, Workspace Files header, stable Project Context, Silent Replies | Memoized by `cacheStablePromptPrefix(stablePrefixCacheKey, …)` (line 1044) — cached per hash of stable inputs |
| **Dynamic suffix** | lines 1280–1357 | Dynamic Project Context files, WebChat canvas, Messaging, Voice, Extra system prompt, Reactions, provider dynamic suffix, Heartbeats, Runtime line, Reasoning | Assembled per turn (`const lines = [stablePrefix]` at 1280, then `.push(...)`) |

**Cache boundary:** `SYSTEM_PROMPT_CACHE_BOUNDARY` (`src/agents/system-prompt-cache-boundary.ts`) is the marker string `"\n<!-- OPENCLAW_CACHE_BOUNDARY -->\n"`, pushed onto the stable-prefix array at `system-prompt.ts:1277`. It is provider-agnostic: **both** the Anthropic and OpenAI-completions backends split on it. The Anthropic provider (`src/llm/providers/anthropic.ts:1386–1405`) splits via `splitSystemPromptCacheBoundary()` and places `cache_control: ephemeral` on the `stablePrefix` block for KV-cache reuse; the OpenAI-completions provider (`src/llm/providers/openai-completions.ts:905–958`) splits the prefix the same way (and falls back to `stripSystemPromptCacheBoundary()` when uncached). The cache-boundary module also exposes `ensureSystemPromptCacheBoundary()` / `prependSystemPromptAdditionAfterCacheBoundary()` so hook-injected overrides still route dynamic additions into the uncached suffix.

**Key pattern:** Stable prefix is calculated once and memoized by cache key; dynamic content is appended below the boundary every turn. This gives KV-cache efficiency on the stable core while keeping per-turn context fresh.

---

## 2. Model/Provider-Specific Prompt Routing

**Entry points:** `src/plugins/provider-runtime.ts:215–238` (`resolveProviderSystemPromptContribution()`) and `src/plugins/provider-runtime.ts:270–286` (`transformProviderSystemPrompt()`).

`resolveProviderSystemPromptContribution()` now merges three layers in order: the gpt5 `baseOverlay` (from `resolveGpt5SystemPromptContribution()`), the provider plugin's `resolvePromptOverlay({ ...context, baseOverlay })` hook (`src/plugins/types.ts:1684`), and the plugin's `resolveSystemPromptContribution(context)` (`src/plugins/types.ts:1675`) — all combined via `mergeProviderSystemPromptContributions()`. Routing is **plugin-driven**, not hardcoded string-matching:

```typescript
// providers.ts — each provider plugin can declare:
resolveSystemPromptContribution(model, ctx) => {
  stablePrefix?: string,
  dynamicSuffix?: string,
  sectionOverrides?: { interaction_style?, tool_call_style?, execution_bias? }
}
transformSystemPrompt(model, systemPrompt) => string
```

**GPT-5 overlay** (`src/agents/gpt5-prompt-overlay.ts:128–152`, `resolveGpt5SystemPromptContribution()`): If `modelId` matches `GPT5_MODEL_ID_PATTERN` (`/(?:^|[/:])gpt-5(?:[.-]|$)/i`, line 10) — gated by `isGpt5ModelId()` — and (for the config-fallback path) the provider is in `OPENAI_FAMILY_GPT5_PROMPT_OVERLAY_PROVIDERS = [codex, codex-cli, openai, azure-openai, azure-openai-responses]` (line 11), apply GPT-5 personality overlays: a `GPT5_BEHAVIOR_CONTRACT` `stablePrefix` plus an `interaction_style` section override (interaction style + optional heartbeat guidance). Note: this entire module is now marked **`@deprecated`** — "Kept for OpenAI/Codex provider-owned compatibility while prompt behavior moves toward provider plugin ownership" (file header, line 1–5). It is wired in as the `baseOverlay` that provider plugins' `resolvePromptOverlay` can extend or supersede; it is no longer presented as the canonical per-model branch.

**Section overrides** (`src/agents/system-prompt-contribution.ts:6–35`): Providers can replace whole named sections:

| Section key | What it controls |
|---|---|
| `interaction_style` | How the agent responds to users |
| `tool_call_style` | Tool invocation discipline |
| `execution_bias` | Action-taking bias / proactivity |

The assembled system prompt passes through `transformProviderSystemPrompt()` (lines 270–286), which applies the plugin's `transformSystemPrompt()` and then plugin text replacements (`applyPluginTextReplacements`) after the full prompt is built.

**Comparison to OpenCode:** OpenCode uses wholly separate `.txt` files per model family (anthropic.txt, gpt.txt, beast.txt, etc.) — zero DRY. OpenClaw uses a **single base prompt + provider-plugin overlays** — more composable and maintainable.

**Comparison to co-cli:** co-cli uses BASE + additive per-profile overlay (profile delta applied at assembly time). OpenClaw's plugin hooks are per-provider rather than per-profile, but the structural idea (base + delta) is the same.

---

## 3. Environment Block

**Runtime metadata injection** (`src/agents/system-prompt.ts:1350–1357`, `src/agents/system-prompt-params.ts:47–105`):

Built fresh at attempt start via `buildSystemPromptParams()` (which resolves repo root via `resolveRepoRoot()` → `findGitRoot()`, timezone via `resolveUserTimezone()`, and time via `formatUserTime()`; the rest of `runtimeInfo` is passed in by the caller):

- **Agent identity** — agentId, sessionKey, sessionId
- **Host & OS** — host, os, arch, node version
- **Model** — current model ID, default model, provider
- **Repository** — repo root (discovered from workspace via `findGitRoot()`; overridable in config)
- **Timezone & time** — user timezone and formatted current time (via `resolveUserTimezone()`, `formatUserTime()`)
- **Active process sessions** — references to any background exec processes
- **Channel capabilities** — channel name, chat type, capabilities list (`inlineButtons`, `richtext`, etc.)

**Injection point:** Pushed under a `## Runtime` header (`system-prompt.ts:1350–1357`) as a single line built by `buildRuntimeLine()` (defined at `system-prompt.ts:1379`):

```
Runtime: agent=main | session=agent:main:session1 | sessionId=abc... | host=... | repo=... | os=... (arch) | node=... | model=claude-3.5-sonnet | default_model=... | shell=... | channel=slack | capabilities=inlineButtons,richtext | thinking=high
```

The `## Runtime` block also appends an optional model-identity line, active background exec-session references (`buildActiveProcessSessionReferenceLines()`), and a `Reasoning: <level> …` line.

This is regenerated on every attempt, so time, active processes, and runtime state are always current.

---

## 4. Instructions Block (AGENTS.md / CLAUDE.md Loading)

**OpenClaw does NOT perform per-turn reload of instruction files.** Unlike OpenCode, which reads AGENTS.md fresh each turn, OpenClaw has no upward-directory-search loader for workspace-level instruction overrides.

**Instructions come from two sources instead:**

1. **Config-derived metadata** (`src/agents/system-prompt-config.ts:35–63`, `resolveAgentSystemPromptConfig()`):
   - Owner display (raw or hashed identifiers) — from config
   - Subagent delegation mode — from config
   - Model aliases — from config
   - TTS hints — from config
   - Memory citations mode — from config
   - FS workspace-only policy — from config

2. **Context engine file discovery** (`src/agents/system-prompt.ts:173–235`):
   - `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `TOOLS.md`, `BOOTSTRAP.md`, `MEMORY.md` files are loaded by the context engine and passed as `contextFiles` to `buildEmbeddedSystemPrompt()` (`embedded-agent-runner/system-prompt.ts:85`, then `system-prompt.ts:995` via `prepareContextFilesForPrompt()`).
   - `prepareContextFilesForPrompt()` (line 191) sorts by basename (`sortContextFilesForPrompt`, line 173) and **partitions into a `stable` and a `dynamic` set**. Stable files render in the cached `# Project Context` section above the boundary (`buildProjectContextSection`, called at 1253–1258); dynamic files render in a separate `# Dynamic Project Context` section below the boundary (1284–1286).
   - There is no search-upward behavior; file discovery is workspace-rooted.

**Implication:** If an AGENTS.md is edited mid-session, the change does NOT take effect until the next attempt (no per-turn reload). For co-cli, this is the opposite of OpenCode — a deliberate trade: load cost is paid once per attempt, not per turn.

---

## 5. Skills Block

**Skills are prompt-hinted, not listed inline.** When a non-empty `skillsPrompt` is provided, the `## Skills` section is injected by `buildSkillsSection()` (`src/agents/system-prompt.ts:269–284`; called at line 967, rendered into the stable prefix):

```markdown
## Skills
Scan <available_skills>. If one clearly applies, read its SKILL.md at exact <location> with `read`, then follow it.
If a skill's <version> differs from a previous turn, re-read that skill before using it.
If several apply, choose the most specific. If none clearly apply, read none.
One skill up front max. Never guess/fabricate skill paths.
...
[skillsPrompt content]
```

The `skillsPrompt` is derived from config or passed at runtime; it typically provides a URL or path to a skill catalog. The agent then uses the `skill_workshop` tool to create/inspect/apply skill content on demand.

**Unlike OpenCode:** OpenCode injects a verbose runtime-enumerated list of available skills into the system prompt; the skill tool loads full content on demand. OpenClaw defers the entire manifest — the prompt gives the agent a discovery instruction, not a precomputed list. This is lighter on prompt tokens but requires an extra tool call to know what skills exist.

---

## 6. Session/Plan Context Injection (Reminders)

**OpenClaw does NOT inject synthetic message parts** for session-level context (no equivalent to OpenCode's `reminders.ts` plan.txt / build-switch.txt pattern).

All session context flows through:
- The base system prompt (runtime line, session ID)
- `extraSystemPrompt` parameter — injected below the cache boundary as a `## Subagent Context` (minimal mode) or `## Group Chat Context` section (`src/agents/system-prompt.ts:1314–1320`)

When a subagent or voice-consult caller needs to inject additional context, it passes it as `extraSystemPrompt` (e.g., `src/talk/agent-consult-runtime.ts:241`, 316–318), not as a synthetic user-side message. This means session context changes are system-prompt changes, not conversation-history injections.

---

## 7. Tool Injection

Tools are **NOT embedded as text in the system prompt**. Two parallel channels handle them:

1. **Tool summaries in prompt text** (`src/agents/system-prompt.ts:759–797` for the `coreToolSummaries` map + `toolOrder`, rendered into the `## Tooling` section at 1048): Core tool descriptions are rendered as plain-text summaries (names, brief descriptions). This tells the agent what tools exist and how to use them. The header note reads: "Available tools are policy-filtered. Names are case-sensitive; call exactly as listed."

2. **Structured tool schemas via API** (`src/agents/sessions/agent-session.ts`): The `AgentSession` maintains a `toolRegistry` (`Map<string, AgentTool>`, line 395) and a `toolDefinitions` map; active tools are set on `agent.state.tools` and their structured `{ name, description, parameters }` schemas surfaced via `getAllTools()` (line ~896) to the LLM provider's tool API — not as prompt text.

3. **Permission gating** (`setActiveToolsByName()`, line 914): Only tool names present in the registry are enabled; unknown names are ignored, and the base system prompt is rebuilt (`rebuildSystemPrompt`) to reflect the active set across both the summary text and the structured list.

4. **Deferred schemas** (`system-prompt.ts:1056–1058`): If `toolSchemaDirectoryPrompt` is provided (for MCP tools or out-of-band schemas), it is injected as a `### Deferred Tool Schemas` section — a pointer for the agent to discover them at runtime.

5. **MCP integration** (`src/plugins/types.ts`): Provider plugins register MCP servers; MCP tools are discovered, wrapped with permission checks, and passed alongside native tools via the same structured API channel.

**Pipeline per turn:**
```
toolRegistry → permission filter → structured schema → LLM tool API
                                 → plain-text summary → system prompt "## Tooling"
```

---

## 8. Agent-Specific Subprompts

**OpenClaw has NO per-agent system prompt files** (no equivalent to OpenCode's `explore.txt`, `compaction.txt`, `title.txt`, `summary.txt`).

Instead, subagent behavior is controlled via `promptMode` and `extraSystemPrompt`:

1. **`promptMode: "minimal"`** (`PromptMode` enum is `"full" | "minimal" | "none"`, documented at `src/agents/system-prompt.ts:63–65`; enforced via per-section `isMinimal` guards — e.g. `buildExecutionBiasSection` line 450, `buildVoiceSection` line 586, `buildUserIdentitySection` line 358, `buildHeartbeatSection` line 241): Filters out verbose sections — Owner identity, Docs, Voice, WebChat, Messaging workflows, Bootstrap, Execution Bias, Skills/Memory/Sub-Agent — keeping the core (Tooling, Workspace, Runtime). `"none"` keeps just the identity line. Used for subagents that don't need the full guidance surface.

2. **`extraSystemPrompt` parameter**: Callers inject subagent-specific instructions (e.g., compaction instructions, voice-bridge context) at call time — not from a file.

3. **Compaction** (`src/agents/embedded-agent-runner/compact.ts:1078–1153`): The compaction agent runs with the same unified system prompt, `promptMode: "minimal"` (resolved at 1078, set at 1099/1124), plus compaction-specific guidance passed via `extraSystemPrompt` (1113), and the result passes through `transformProviderSystemPrompt()` (1143). Summarization-instruction text comes from `src/agents/compaction.ts:113` (`buildCompactionSummarizationInstructions`). No separate compaction.txt. (Note: the old `agents/compact.ts` is gone — compaction is now split across `src/agents/compaction*.ts` and `src/agents/embedded-agent-runner/compact.ts`.)

4. **Voice consult** (`src/talk/agent-consult-runtime.ts:218` `consultRealtimeVoiceAgent()`, `extraSystemPrompt` at 316–318): Passes a custom `prompt` and brief voice-bridge instruction via `extraSystemPrompt`.

**Net effect:** All subagent behavior is controlled by three levers — `promptMode`, `extraSystemPrompt`, and `tools` list — rather than separate files. This keeps guidance in one place at the cost of less per-agent customization granularity.

---

## 9. Agent Loop Structure

**Main embedded-agent loop** (`src/agents/embedded-agent-runner/run.ts`, `src/agents/embedded-agent-runner/run/attempt.ts`; system prompt built by `buildAttemptSystemPrompt()` at `src/agents/embedded-agent-runner/run/attempt-system-prompt.ts:39`, params at `attempt.ts:1915`, provider contribution/transform at `attempt.ts:1979`/`1992`):

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

**Compaction:** Triggered on context overflow (preemptive + mid-turn paths in `attempt.ts`); the compaction agent runs with `promptMode: "minimal"` + compaction instructions in `extraSystemPrompt`. Full compaction logic at `src/agents/embedded-agent-runner/compact.ts` (system-prompt build at 1078–1153) and `src/agents/compaction.ts`.

---

## 10. Provider-Level System Prompt Formatting

**System prompt is a single string**, not an array (`src/plugins/provider-runtime.ts:270–286`):

After assembly, the system prompt is one string carrying the embedded `SYSTEM_PROMPT_CACHE_BOUNDARY` marker. The provider plugin applies `transformSystemPrompt()` before it reaches the LLM backend, and the backend splits the string at the boundary at request-serialization time:

- **Anthropic provider** (`src/llm/providers/anthropic.ts:1386–1405`): Splits via `splitSystemPromptCacheBoundary()` and emits a multi-block system message, putting `cache_control: ephemeral` on the `stablePrefix` block. Falls back to `stripSystemPromptCacheBoundary()` for a single block when there is no boundary.
- **OpenAI-completions provider** (`src/llm/providers/openai-completions.ts:905–958`): Also splits on the same boundary to emit the stable prefix separately (and `stripSystemPromptCacheBoundary()` otherwise). So the boundary is **not Anthropic-specific** — it is a provider-agnostic cache-segmentation marker honored by at least these two backends.
- **Other providers**: Each provider plugin serializes into its own request format.

**Session-level prompt storage** (`src/agents/sessions/agent-session.ts`): The current system prompt is exposed via the `systemPrompt` getter (line 877) reading `this.agent.state.systemPrompt`; updated via `setBaseSystemPrompt()` (line 934) and `setActiveToolsByName()` → `rebuildSystemPrompt()` (line 914) when the prompt or tool set changes.

Model-specific options are merged in the provider plugin at request time — not at system-prompt-build time.

---

## 11. Plugin / Extension Hooks on the System Prompt

**Plugins can mutate the system prompt in two ways:**

1. **Pre-assembly contribution** (`src/plugins/provider-runtime.ts:215–238`): Plugins declare `resolvePromptOverlay()` (`src/plugins/types.ts:1684`) and/or `resolveSystemPromptContribution()` (`src/plugins/types.ts:1675`) — resolved before `buildAgentSystemPrompt()` finalizes, merged with the gpt5 `baseOverlay` via `mergeProviderSystemPromptContributions()`. Contributions are merged as `stablePrefix`, `dynamicSuffix`, or named `sectionOverrides`. This is initialization/attempt-prep time, not mid-turn.

2. **Post-build string transform** (`src/plugins/provider-runtime.ts:270–286`): Plugins declare `transformSystemPrompt()` (`src/plugins/types.ts:1709`) — called after the full string is assembled, allowing text replacements, appends, or redactions before the LLM call.

3. **Character-level text transforms** (`src/agents/plugin-text-transforms.ts`): Input/output character replacements (`applyPluginTextReplacements`) that apply to system prompt and message content alike.

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
- `src/agents/gpt5-prompt-overlay.ts:20–85` — `GPT5_FRIENDLY_CHAT_PROMPT_OVERLAY` (20), `GPT5_HEARTBEAT_PROMPT_OVERLAY` (35), `GPT5_FRIENDLY_PROMPT_OVERLAY` (47), `GPT5_BEHAVIOR_CONTRACT` (50), etc.

These are reachable and used (wired in as the provider `baseOverlay`), though the module is now marked `@deprecated` as prompt ownership migrates to provider plugins; not dead.

---

## 14. Cross-Cutting Concern List: Across All Prompt Assembly

OpenClaw's single unified system prompt builder naturally ensures consistency. Because there is one code path (not seven files), concerns cannot diverge across model families. (All line numbers below are in `src/agents/system-prompt.ts` unless noted.)

### Universal (all models, all modes)

| Concern | What is covered | Where |
|---|---|---|
| **Identity** | "You are a personal assistant running inside OpenClaw" | line 1046 |
| **Tool discipline** | Parallelize independent calls; don't poll; use tools over bash | `## Tooling`, lines 1048–1085 |
| **Output discipline** | Markdown, MEDIA directives, no preamble | `buildAssistantOutputDirectivesSection` (395–422), rendered at 1245 |
| **Execution bias** | Act on actionable requests; continue until done | `buildExecutionBiasSection` (449–463) |
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
| **Sub-agent delegation** | If `sessions_spawn` available AND `mode=prefer` | `buildSubagentDelegationPreferenceSection` (92–119) |
| **Heartbeat guidance** | If heartbeat prompt provided | `buildHeartbeatSection` (240–251) |
| **Skills** | If a non-empty `skillsPrompt` is provided | `buildSkillsSection` (269–284) |
| **ACP harness spawning** | If `acpEnabled && !sandboxed` (`acpSpawnRuntimeEnabled`) | 1069–1085 |
| **Reactions** | If `reactionGuidance` provided (channel-generic — Discord, etc.) | 1321–1343 |
| **Reasoning format** | If `reasoningHint` set | 1248–1250 |
| **Voice (TTS)** | If `ttsHint` provided | `buildVoiceSection` (585–594) |

**GPT-5 only** (provider `baseOverlay`, now `@deprecated`):

| Concern | Where |
|---|---|
| **Interaction style override** | `src/agents/gpt5-prompt-overlay.ts:128–152` |
| **Behavior contract** | `src/agents/gpt5-prompt-overlay.ts:20–85` |

---

## 15. Architectural Comparison: OpenCode vs OpenClaw vs co-cli

| Dimension | OpenCode | OpenClaw | co-cli |
|---|---|---|---|
| **Base prompt strategy** | Whole separate file per model family | Single unified prompt builder + provider-plugin overlays | Single model-agnostic BASE shared by all profiles |
| **Per-model variation** | Completely separate files (anthropic.txt, gpt.txt, beast.txt, etc.) — zero DRY | Plugin-declared `stablePrefix`/`dynamicSuffix`/`sectionOverrides` | Additive overlay on BASE (BASE + profile delta) |
| **Composition model** | `env + instructions + skills` assembled per turn from separate concerns | Single builder; concerns are named sections within one function | BASE + overlay at prompt-assembly time |
| **Skills injection** | Verbose runtime-enumerated list in system prompt; tool loads content on demand | Prompt hint + discovery instruction only; manifest deferred to `skill_workshop` tool | `<available_skills>` manifest rendered per-turn as the `skill_manifest_prompt` dynamic instruction (re-reads the live index, outside the cached prefix); on-demand load |
| **Memory injection** | AGENTS.md/CLAUDE.md as plain text; reloaded per turn | Context engine file discovery (AGENTS.md, MEMORY.md etc.); no per-turn reload | Memory search-driven recall (BM25) + USER.md always-injected |
| **Session context** | Synthetic message parts (plan.txt, build-switch.txt) injected into history | `extraSystemPrompt` parameter only; no synthetic message injection | No session-level injection today |
| **Tool passing** | Structured tool definitions (not in prompt text) | Structured tool schemas via API + plain-text summaries in "## Tooling" | Structured tool definitions (not in prompt text) |
| **Per-provider schema transform** | `ProviderTransform.schema()` per tool, per model | `transformSystemPrompt()` post-build; MCP via context engine | N/A (single provider today) |
| **System prompt freshness** | Assembled fresh every turn (no caching) | Assembled fresh per attempt; stable prefix memoized by cache key (`cacheStablePromptPrefix`) | Static instructions assembled once per session; dynamic suffix (skills, deferred tools, time, safety, wrap-up) recomputed per request |
| **Plugin extension** | `experimental.chat.system.transform` hook mutates system array pre-LLM | `resolvePromptOverlay()` / `resolveSystemPromptContribution()` at attempt-prep; `transformSystemPrompt()` post-build | No plugin hook on system prompt |
| **Agent subprompts** | Per-agent `.txt` files (explore, compaction, title, summary) | `promptMode` enum (full/minimal/none) + `extraSystemPrompt` at call site | No per-agent subprompt files today |
| **Cache boundary marker** | N/A | `SYSTEM_PROMPT_CACHE_BOUNDARY` string (`<!-- OPENCLAW_CACHE_BOUNDARY -->`) — provider-agnostic; both Anthropic (`cache_control` on stable prefix) and OpenAI-completions split on it | No marker string; the static/dynamic boundary is the pydantic-ai `InstructionPart(dynamic=…)` flag — Anthropic provider caches the last static block, Ollama prefix-cache reuses the static prefix |

---

## 16. Key Takeaways for co-cli Design

1. **Plugin-driven overlays scale better than hardcoded per-model routing.** OpenClaw's `resolveSystemPromptContribution()` / `transformSystemPrompt()` plugin hooks mean adding a new model family requires no core code change. co-cli's BASE+overlay is already on this axis — the plugin model is the next step if multi-provider support is ever needed.

2. **Stable prefix + dynamic suffix + cache boundary is a real optimization. (Now implemented, without the marker string.)** co-cli separates its stable core (`BASE+overlay`, USER.md, toolset guidance, critique) from dynamic per-turn content (time, skill manifest, deferred-tool list, safety/wrap-up) via pydantic-ai's `InstructionPart` static/dynamic flag rather than an explicit boundary marker. The Anthropic provider places `cache_control` on the last static block; the Ollama path reuses the static prefix bytes. Payoff: cache hits on every same-day turn after the first (`prompt-assembly.md` §2.3).

3. **Minimal mode for subagents is simpler than per-agent prompt files.** Instead of a separate compaction.txt or explore.txt, a `promptMode` flag filters the appropriate sections. Guidance stays in one place; per-agent behavior is a call-site decision, not a file-layout decision. Co-cli could adopt this when subagents need lighter prompts.

4. **No per-turn instruction reload is a deliberate trade-off, not an oversight.** OpenClaw accepts stale instructions within a session in exchange for not re-reading disk on every turn. Co-cli's current per-session assembly sits in the same position. If per-turn freshness ever matters (live AGENTS.md edits during a session), OpenCode's pattern is the reference.

5. **All dynamic content injected programmatically — no shell templating.** OpenClaw's avoidance of `!cmd` interpolation in instruction files is the safer, more predictable path. Co-cli should stay on this side of the line.

6. **Skills deferred to a discovery hint is lighter on tokens.** OpenClaw does not enumerate skills in the system prompt — it tells the agent how to find them. This trades one extra tool-call round-trip for shorter prompts. Co-cli's current manifest-injected approach is richer (agent sees the list up front); the right choice depends on catalog size and model capability.

7. **Context engine file discovery is cleaner than upward-search for project instructions.** Rather than AGENTS.md upward traversal, OpenClaw delegates workspace file discovery to a dedicated engine. This could simplify co-cli's config-file loading if workspace-level instruction files become first-class.

---

## 17. Key File Index

(All source moved under `src/` in this refresh; the `agents/` → `src/agents/` shift accounts for most path changes below.)

| File | Purpose | Key Lines / Functions |
|---|---|---|
| `src/agents/system-prompt.ts` | Main system prompt builder | `buildAgentSystemPrompt()` (682–1419); stable prefix memoized (1044–1277, boundary push at 1277); dynamic suffix (1280–1357) |
| `src/agents/system-prompt-config.ts` | Config-derived prompt fields | `resolveAgentSystemPromptConfig()` (35–63); `buildConfiguredAgentSystemPrompt()` (58) |
| `src/agents/system-prompt-params.ts` | Runtime parameter collection | `buildSystemPromptParams()` (47–105); `resolveRepoRoot()` (74) |
| `src/agents/system-prompt-contribution.ts` | Provider overlay type definitions | Type defs: `stablePrefix`, `dynamicSuffix`, `sectionOverrides` (6–35) |
| `src/agents/system-prompt-cache-boundary.ts` | Cache boundary marker + split/strip helpers | `SYSTEM_PROMPT_CACHE_BOUNDARY` = `"\n<!-- OPENCLAW_CACHE_BOUNDARY -->\n"`; `splitSystemPromptCacheBoundary()`, `ensureSystemPromptCacheBoundary()` |
| `src/llm/providers/anthropic.ts` | Anthropic backend cache segmentation | Splits on boundary; `cache_control` on stable prefix block (1386–1405) |
| `src/llm/providers/openai-completions.ts` | OpenAI-completions backend cache segmentation | Splits on boundary / strips it (905–958) |
| `src/agents/gpt5-prompt-overlay.ts` | GPT-5 model-family overlay (now `@deprecated`) | `resolveGpt5SystemPromptContribution()` (128–152); constants (20–85); `GPT5_MODEL_ID_PATTERN` (10) |
| `src/agents/embedded-agent-runner/system-prompt.ts` | Embedded-run prompt wrapper | `buildEmbeddedSystemPrompt()` (22), `applySystemPromptToSession()` (137) |
| `src/agents/embedded-agent-runner/run/attempt-system-prompt.ts` | Per-attempt prompt build | `buildAttemptSystemPrompt()` (39) |
| `src/agents/embedded-agent-runner/run/attempt.ts` | Main attempt orchestrator | `buildSystemPromptParams()` at 1915; provider contribution at 1979; transform at 1992 |
| `src/agents/embedded-agent-runner/run.ts` | Embedded-agent loop entry | Attempt loop |
| `src/plugins/provider-runtime.ts` | Provider plugin integration | `resolveProviderSystemPromptContribution()` (215–238); `transformProviderSystemPrompt()` (270–286) |
| `src/plugins/types.ts` | Plugin hook type defs | `resolveSystemPromptContribution` (1675); `resolvePromptOverlay` (1684); `transformSystemPrompt` (1709) |
| `src/agents/sessions/agent-session.ts` | Session state and prompt storage | `systemPrompt` getter (877); `setBaseSystemPrompt()` (934); `setActiveToolsByName()` (914); `toolRegistry` (395); `getAllTools()` (~896) |
| `src/talk/agent-consult-runtime.ts` | Voice consult agent entry | `consultRealtimeVoiceAgent()` (218) with `extraSystemPrompt` (316–318) |
| `src/agents/embedded-agent-runner/compact.ts` | Compaction system-prompt build | `promptMode: "minimal"` + `extraSystemPrompt` (1078–1153); `transformProviderSystemPrompt()` (1143) |
| `src/agents/compaction.ts` | Compaction summarization logic | `buildCompactionSummarizationInstructions()` (113) |
