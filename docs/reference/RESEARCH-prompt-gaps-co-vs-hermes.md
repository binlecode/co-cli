# RESEARCH: Prompt Assembly Gaps — `co-cli` vs Hermes

Code-verified comparison of system prompt assembly between co-cli and hermes-agent.

Last refresh: 2026-04-28 against `co_cli/agent/core.py`, `co_cli/context/assembly.py`,
`co_cli/context/guidance.py`, `co_cli/agent/_instructions.py`,
`co_cli/context/prompt_text.py`, `co_cli/tools/deferred_prompt.py`,
`co_cli/personality/prompts/loader.py`.

## Sources

### co-cli
- [`co_cli/agent/core.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/core.py) — `build_agent()`, static prompt assembly site, per-turn `agent.instructions()` registration
- [`co_cli/context/assembly.py`](/Users/binle/workspace_genai/co-cli/co_cli/context/assembly.py) — `build_static_instructions()`, `RECENCY_CLEARING_ADVISORY`
- [`co_cli/context/guidance.py`](/Users/binle/workspace_genai/co-cli/co_cli/context/guidance.py) — `MEMORY_GUIDANCE`, `CAPABILITIES_GUIDANCE`, `build_toolset_guidance()`
- [`co_cli/agent/_instructions.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_instructions.py) — `current_time_prompt`, `safety_prompt` (per-turn callback wrappers)
- [`co_cli/context/prompt_text.py`](/Users/binle/workspace_genai/co-cli/co_cli/context/prompt_text.py) — `safety_prompt_text` (doom-loop / shell-reflection detection)
- [`co_cli/tools/deferred_prompt.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/deferred_prompt.py) — `build_category_awareness_prompt()` (now called at build time, not per-turn)
- [`co_cli/context/rules/`](/Users/binle/workspace_genai/co-cli/co_cli/context/rules/) — `01_identity.md` … `05_workflow.md`
- [`co_cli/personality/prompts/loader.py`](/Users/binle/workspace_genai/co-cli/co_cli/personality/prompts/loader.py) — soul scaffold loaders, `load_personality_memories()`

### Hermes
- [`run_agent.py:3396–3561`](/Users/binle/workspace_genai/hermes-agent/run_agent.py) — `AIAgent._build_system_prompt()`
- [`agent/prompt_builder.py`](/Users/binle/workspace_genai/hermes-agent/agent/prompt_builder.py) — all prompt constants + `build_skills_system_prompt`, `build_context_files_prompt`, `build_environment_hints`, `TOOL_USE_ENFORCEMENT_GUIDANCE`, `OPENAI_MODEL_EXECUTION_GUIDANCE`, `GOOGLE_MODEL_OPERATIONAL_GUIDANCE`, `PLATFORM_HINTS`

---

## 1. Prompt Assembly Layer Maps

### co-cli — static + per-turn

**Block 0 — Static**, assembled once at agent construction in `build_agent()`. Composed of three concatenated parts:

**Part A — `build_static_instructions(config)`** (`context/assembly.py`):

| # | Layer | Source | Notes |
|---|---|---|---|
| 1 | Soul seed | `souls/{role}/seed.md` | Identity anchor |
| 2 | Character memories | `souls/{role}/memories/*.md` | Optional |
| 3 | Mindsets | `souls/{role}/mindsets/{task_type}.md` | Optional |
| 3b | Personality-context memories | `~/.co-cli/knowledge/*.md` tagged `personality-context` | Top 5 by recency, via `load_personality_memories()`; process-cached |
| 4 | Behavioral rules | `context/rules/01–05_*.md` | identity, safety, reasoning, tool_protocol, workflow |
| 4b | Recency-clearing advisory | `RECENCY_CLEARING_ADVISORY` | Static, cacheable; explains `[tool result cleared…]` placeholders |
| 5 | Soul examples | `souls/{role}/examples.md` | Optional, trailing rules |
| 6 | Soul critique | `souls/{role}/critique.md` | Optional, framed under `## Review lens` |

**Part B — `build_toolset_guidance(tool_index)`** (`context/guidance.py`), gated on tool presence:

| Block | Constant | Gate |
|---|---|---|
| Memory guidance | `MEMORY_GUIDANCE` | `memory_search` in tool_index |
| Capability self-check | `CAPABILITIES_GUIDANCE` | `capabilities_check` in tool_index |

**Part C — `build_category_awareness_prompt(tool_index)`** (`tools/deferred_prompt.py`):
single-sentence hint listing deferred-tool categories reachable via `search_tools`.
Built at construction time and inserted into the static prompt — emits empty string when no deferred tools exist.

**Block 1 — Per-turn `agent.instructions()` callbacks** (`agent/_instructions.py`), evaluated fresh every model request, never persisted in message history:

| Layer | Function | Content | Condition |
|---|---|---|---|
| Safety warnings | `safety_prompt` → `safety_prompt_text` | Doom-loop / shell-reflection-cap warnings | When streak ≥ threshold |
| Current time | `current_time_prompt` | `"Current time: <weekday>, <month> <day>, <year> <H:MM AM/PM>"` | Always |

`safety_prompt` is registered first so structural behavioral guidance sits above the ephemeral grounding; `current_time_prompt` is at the tail — last text before the user turn.

### Hermes — session-built, rebuilt post-compression

**`_build_system_prompt()`** (`run_agent.py:3396`), called once per session and after every compression event:

| # | Layer | Source | Condition |
|---|---|---|---|
| 1 | Identity | `~/.hermes/SOUL.md` or `DEFAULT_AGENT_IDENTITY` | Always |
| 2 | Tool-aware guidance | `MEMORY_GUIDANCE` + `SESSION_SEARCH_GUIDANCE` + `SKILLS_GUIDANCE` (`prompt_builder.py:144–171`) | Only when `memory`/`session_search`/`skill_manage` are in loaded tools |
| 3 | Nous subscription | `build_nous_subscription_prompt()` | Config-gated |
| 4 | Tool-use enforcement | `TOOL_USE_ENFORCEMENT_GUIDANCE` (`prompt_builder.py:173–186`) | Model-gated: auto = gpt/codex/gemini/gemma/grok |
| 4a | OpenAI execution discipline | `OPENAI_MODEL_EXECUTION_GUIDANCE` (`prompt_builder.py:196–254`) | gpt/codex only |
| 4b | Google operational directives | `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` (`prompt_builder.py:258–276`) | gemini/gemma only |
| 5 | User/gateway system_message | Caller-supplied | Optional |
| 6 | Frozen memory: MEMORY.md | `_memory_store.format_for_system_prompt("memory")` | Config-gated (`_memory_enabled`) |
| 7 | Frozen user profile: USER.md | `_memory_store.format_for_system_prompt("user")` | Config-gated (`_user_profile_enabled`) |
| 8 | External memory provider | `_memory_manager.build_system_prompt()` | Config-gated |
| 9 | Skills index | `build_skills_system_prompt()` | When `skills_list`/`skill_view`/`skill_manage` loaded |
| 10 | Context files | `build_context_files_prompt()` — scans for AGENTS.md, .cursorrules, .hermes.md, CLAUDE.md | Skip if `skip_context_files=True` |
| 11 | Timestamp + session metadata | `now.strftime(...)` + session_id + model + provider | Always |
| 12 | Environment hints | `build_environment_hints()` — WSL, Termux detection | When detected |
| 13 | Platform hints | `PLATFORM_HINTS[platform]` (`prompt_builder.py:285–+`) | 14 channels: whatsapp, telegram, discord, slack, signal, email, cron, cli, sms, bluebubbles, wecom, wechat, qq |

---

## 2. Gap Matrix

| Dimension | co-cli | Hermes | Gap |
|---|---|---|---|
| **Identity / persona** | Structured filesystem: `souls/{role}/` with 6 file types (seed, memories, mindsets, examples, critique) | `SOUL.md` singleton or `DEFAULT_AGENT_IDENTITY` hardcoded fallback | Different architectures; co-cli richer. No gap to address. |
| **Behavioral rules** | `context/rules/01–05_*.md` — identity, safety, reasoning, tool_protocol, workflow | `TOOL_USE_ENFORCEMENT_GUIDANCE` + model-specific guidance, injected conditionally | **Gap (§3.1)**: hermes gates enforcement by model family; co-cli's `04_tool_protocol.md` has equivalent content but is always injected and not model-branched. Low priority — co-cli targets Claude only. |
| **Frozen memory blocks** | None — memory is tool-based only (`memory_search`); `personality-context` artifacts are injected, but only those tagged that way | MEMORY.md + USER.md injected as static blocks in every system prompt | **Gap (§3.2)**: hermes puts durable user facts in every prompt automatically; co-cli requires model to call `memory_search` explicitly (or relies on `personality-context` tagging). |
| **Tool-aware guidance** | `MEMORY_GUIDANCE` + `CAPABILITIES_GUIDANCE` in `context/guidance.py`, gated on tool_index membership | MEMORY_GUIDANCE, SESSION_SEARCH_GUIDANCE, SKILLS_GUIDANCE — only when those tools are loaded | **Resolved.** Earlier draft flagged this as a gap; co-cli now mirrors the hermes pattern via `build_toolset_guidance()`. The `## Memory` section was removed from `04_tool_protocol.md` and migrated to `MEMORY_GUIDANCE`. |
| **Skills index** | No skill-level system-prompt injection; skills discoverable via `search_tools`. `build_category_awareness_prompt` adds a single-sentence category hint when deferred tools exist | `build_skills_system_prompt()` injects a formatted skill index | **Partial gap (§3.4)**: hermes guarantees per-skill visibility at turn start; co-cli surfaces categories only and relies on the model calling `search_tools` for specifics. Low priority — co-cli's deferred search is intentional. |
| **Context files** | No AGENTS.md / CLAUDE.md injection | Scans for AGENTS.md, .cursorrules, .hermes.md, CLAUDE.md with prompt-injection detection | **Gap (§3.5)**: hermes picks up project-level instructions from standard files automatically. co-cli requires explicit personality configuration instead. Not recommended to port — attack surface. |
| **Session / model metadata** | Per-turn date+time only (`current_time_prompt`) | Session start timestamp, session ID, model name, provider injected at build time | Minor. co-cli has date+time per-turn; session ID is in `capabilities_check` output but not in the prompt. |
| **Environment hints** | macOS BSD specifics live in `04_tool_protocol.md`'s `## Shell` section | `build_environment_hints()` — WSL path translation, Termux detection | Minor. co-cli has no WSL/Termux users expected. |
| **Platform hints** | N/A — CLI-only | 14 channel-specific formatting/media hints | N/A. co-cli is not a multi-channel platform. |
| **Recency-clearing advisory** | `RECENCY_CLEARING_ADVISORY` static block explains `[tool result cleared…]` placeholders introduced by `evict_old_tool_results` | None — hermes does not have an equivalent eviction mechanism in the system prompt | **co-cli advantage**: provides structured guidance about the eviction artifact without breaking prefix cache. |
| **Post-compression rebuild** | System prompt NOT rebuilt — prefix-cache stable | System prompt rebuilt after compression (`run_agent.py:7282–7284`) | **Hermes degrades prefix cache**; co-cli intentionally avoids this. co-cli advantage. |

---

## 3. Gaps in Detail

### 3.1 Tool-use enforcement not model-gated

**co-cli:** `04_tool_protocol.md` includes an `## Execute, don't promise` section ("When you say you will do something, do it in the same response — make the tool call immediately. Never end a turn with a statement of intent.") and parallel/sequential tool call guidance. This is always injected regardless of model.

**Hermes:** `TOOL_USE_ENFORCEMENT_GUIDANCE` says the same thing more directly ("You MUST use your tools to take action — do not describe what you would do"). It is gated by model family (auto: gpt/codex/gemini/gemma/grok) and supplemented by `OPENAI_MODEL_EXECUTION_GUIDANCE` (tool persistence, mandatory tool use for arithmetic/hashes/time/git, prerequisite checks, verification) for GPT/Codex models.

**Implication:** co-cli sends the same guidance regardless of whether the model needs it. Anthropic/Claude models generally don't need enforcement prompting; injecting it for all models wastes tokens and may be counter-productive.

**Port consideration:** Not worth porting model-family branching — co-cli targets Claude models only. The enforcement content already exists in `04_tool_protocol.md`. Low priority.

### 3.2 No frozen MEMORY.md / USER.md block in system prompt

**co-cli:** All non-personality memory is accessed via `memory_search` tool calls. The only static memory block is the `personality-context`-tagged subset injected by `load_personality_memories()` at agent construction (top 5 by recency).

**Hermes:** MEMORY.md + USER.md contents are injected as frozen blocks at session build time (`run_agent.py:3479–3488`). User preferences and persistent facts are available before the first turn with zero tool calls.

**Implications:**
- Hermes's model never needs to call a memory tool to know user preferences — they're already in context.
- Cost: every session pays the token cost of MEMORY.md + USER.md even when irrelevant.
- co-cli's `MEMORY_GUIDANCE` block tells the model: "Character base memories and user experience memories are both loaded in the system prompt before the first turn — do not call memory_search at turn start." This is accurate for **personality-context-tagged** artifacts but **not** for general user-preference artifacts saved via `memory_create`.

**Port consideration:** Medium. Adding an optional `auto_inject_user_profile` config key that injects high-priority `kind=preference` artifacts into the static prompt would close this gap without requiring a full MEMORY.md system. Personality-context artifacts are already injected (layer 3b in the static assembly) — extending the injection to also include user-preference artifacts is the minimal change.

### 3.3 [RESOLVED] Memory guidance unconditionally injected

**Status:** Closed. The `## Memory` section was removed from `04_tool_protocol.md` and moved to `MEMORY_GUIDANCE` in `co_cli/context/guidance.py`. `build_toolset_guidance()` only emits it when `memory_search` is present in `tool_index`. `CAPABILITIES_GUIDANCE` follows the same gate on `capabilities_check`.

This mirrors the Hermes pattern exactly — guidance text and the tool that satisfies it travel together.

### 3.4 Per-skill index not in system prompt

**co-cli:** Skills are discoverable via `search_tools` (deferred tool discovery). The model gets a single-sentence category-level hint at static build time via `build_category_awareness_prompt`, e.g. "Additional capabilities available via search_tools: file editing (file_write, file_patch), background tasks (task_start), …". It must call `search_tools` to see individual skills.

**Hermes:** `build_skills_system_prompt()` builds a formatted index of available skills (name, description, trigger conditions) and injects it into the system prompt. Model sees per-skill availability at turn start.

**Implication:** co-cli shows the model which **categories** are reachable but not the individual skills/tools within each category. For large skill sets this is more cache-friendly; for small curated sets it costs an extra `search_tools` round-trip.

**Port consideration:** Medium. A read-only `skills_list` + `skill_view` model-callable tool (already noted in RESEARCH-tools-gaps-co-vs-hermes.md §2.1) would be more ergonomic than full system-prompt injection. Injecting the entire skill index in the system prompt is noisy for large skill sets.

### 3.5 No context file injection

**co-cli:** Does not scan or inject AGENTS.md, CLAUDE.md, or .cursorrules. Project-level instructions reach the model only through personality configuration.

**Hermes:** `build_context_files_prompt()` scans the working directory (and git root) for AGENTS.md, .cursorrules, .hermes.md, CLAUDE.md with prompt-injection detection (`_scan_context_content` at `prompt_builder.py:55–73` — 10 patterns including invisible unicode, `ignore previous instructions`, hidden divs, exfil patterns).

**Implication:** Hermes picks up project conventions automatically; co-cli users who want project instructions must configure a personality. This is an intentional design difference — co-cli uses explicit personality profiles rather than ambient file scanning.

**Port consideration:** Low priority. The auto-file-injection pattern increases attack surface (prompt injection via AGENTS.md). co-cli's explicit model is safer. Not recommended.

---

## 4. co-cli Advantages Over Hermes

| Advantage | Detail |
|---|---|
| **Prefix-cache stability post-compression** | co-cli never rebuilds the system prompt after compression — Block 0 remains identical across all turns, maximizing Anthropic prefix cache hits. Per-turn variance lives in Block 1 callbacks (date/time, conditional safety warnings) which are appended outside the cached prefix. Hermes rebuilds the system prompt after every compression event (`run_agent.py:7282`), breaking the cache each time. |
| **Recency-clearing advisory** | `RECENCY_CLEARING_ADVISORY` is a static, cacheable block that explains the `[tool result cleared…]` placeholders the model encounters after `evict_old_tool_results` runs. Hermes has no equivalent because its compaction model doesn't insert these placeholders. |
| **Richer personality system** | co-cli's `souls/` structure with seed/memories/mindsets/examples/critique gives fine-grained control over persona layers. Hermes has one `SOUL.md` singleton with no sub-structure. |
| **Compaction enrichment** | co-cli gathers enrichment context (recent file paths, pending todos, prior summaries) to guide the summarizer. Hermes has no equivalent enrichment-gathering pass. |
| **Personality-context artifact injection** | co-cli can inject curated T2 artifacts tagged `personality-context` into the static prompt — user-shaped, not hard-coded. Hermes's MEMORY.md/USER.md are flat files maintained manually. |
| **Toolset-gated guidance** | `build_toolset_guidance()` emits `MEMORY_GUIDANCE` and `CAPABILITIES_GUIDANCE` only when their backing tools are loaded — same pattern as hermes but with stricter coupling (the gate and the tool live in the same registry). |
| **Structured behavioral rules** | co-cli's numbered `01–05_*.md` rules are modular, independently auditable, and strictly ordered. Hermes's guidance is inline string constants — no file-level separation. |

---

## 5. Priority Ordering

| Priority | Item | Risk | Effort |
|---|---|---|---|
| **Medium** | Inject `kind=preference` artifacts into static prompt (§3.2) | User preferences not available at turn start without explicit recall | Low — extend layer 3b in `build_static_instructions` (or `load_personality_memories`) to also include `artifact_kind=preference` artifacts |
| **Low** | Model-callable `skills_list` + `skill_view` (already in RESEARCH-tools-gaps §2.1) | Per-skill discovery requires `search_tools` proactivity; categories alone may be too coarse | Medium — read-only tools over existing skill registry |
| **Skip** | Context file injection (§3.5) | Prompt injection attack surface | Not recommended |
| **Skip** | Model-family-gated tool-use enforcement (§3.1) | co-cli targets Claude only — no benefit | N/A |
| **Done** | Gate memory/session guidance on tool availability (§3.3) | — | Closed: `build_toolset_guidance()` in `context/guidance.py` |
