# DESIGN: Co Evolution (Frontier-Grounded, Feb 2026)

## 1. Vision

`co-cli` should evolve from a capable tool-calling assistant into a personal companion for knowledge work, while preserving its identity:

1. Local-first runtime and storage.
2. Approval-first for side effects.
3. Incremental, testable delivery.

The target product shape is text-first, automation-capable, and safe by default.

### 1.1 The "Finch" Vision

Co aspires to be the CLI version of the robot companion from "Finch" (2021): a helpful assistant that learns, develops personality, and forms lasting relationships with its user.

**Core traits:**
- **Helpful:** Completes tasks efficiently and accurately
- **Curious:** Asks clarifying questions, seeks to understand context
- **Adaptive:** Learns user preferences and patterns over time
- **Empathetic:** Understands emotional context, adjusts tone appropriately
- **Loyal:** Remembers past interactions, maintains continuity across sessions
- **Growing:** Evolves from simple command executor to thoughtful partner

**Five pillars of co's character:**
1. **Soul:** Identity, personality, interaction style (selected by user from templates)
2. **Internal Knowledge:** Learned context, patterns, user habits (persists across sessions)
3. **External Knowledge:** Tools for accessing data (Obsidian, web, Google, Slack, MCP)
4. **Emotion:** Tone, empathy, context-aware communication (adapts to situation)
5. **Habit:** Workflow preferences, approval patterns, personalization (user-configurable)

Unlike a pure tool executor, co should anticipate needs, remember preferences, and develop a working relationship with its user over weeks and months of use.

## 2. Frontier Snapshot (as of February 9, 2026)

The current frontier is no longer "single-turn tool calls." It is end-to-end agents with planning, tool orchestration, asynchronous execution, and explicit safety controls.

### 2.1 Unified agent surfaces (research + tools + action)

1. OpenAI launched `ChatGPT agent` on July 17, 2025, combining capabilities from Operator and deep research into a single mode with connectors and tool execution.
2. Anthropic launched Claude 4 on May 22, 2025, with extended thinking + tool use, parallel tools, and stronger long-horizon agent behavior.
3. Google announced Gemini app `Agent Mode`, Project Mariner, and Jules at I/O 2025, converging on the same "plan + act + user oversight" interaction pattern.

Implication for Co:

1. Keep one primary loop: observe -> plan -> execute tools -> ask approval when needed -> summarize with citations.
2. Avoid fragmented "feature islands" (separate research mode, separate automation mode, separate planning mode).

### 2.2 Asynchronous, long-running execution is now baseline

1. OpenAI's agent stack includes background execution modes for longer tasks.
2. Anthropic's Claude Code supports background tasks (for example via GitHub Actions integration).
3. Google Jules moved from beta to broad availability in 2025 and added proactive/scheduled workflows.

Implication for Co:

1. Add resumable background runs as a first-class primitive.
2. Treat foreground chat as control plane and background jobs as execution plane.

### 2.3 Protocol convergence: MCP now matters

1. OpenAI added remote MCP support in the Responses API tool stack.
2. Anthropic added MCP connector capabilities and a broader agent-tooling surface (skills, memory tool, tool search).
3. Google announced A2A protocol support and MCP support in Gemini API/SDK tooling.

Implication for Co:

1. MCP client support should move from TODO to core roadmap.
2. Keep native tools for critical local/safety paths; use MCP to expand breadth.

### 2.4 Safety posture converges on human-in-the-loop for consequential actions

1. OpenAI agent mode requests confirmation before high-impact actions.
2. Anthropic computer-use guidance explicitly recommends VM isolation, domain restrictions, and human confirmation for meaningful real-world consequences.
3. Google Project Mariner UX keeps users in control, with stop/takeover affordances.

Implication for Co:

1. Double down on approval-first rather than diluting it for convenience.
2. Keep strict network/sandbox policies and explicit user control boundaries.

## 3. Co CLI Ground Truth (Current State)

Based on the current repository:

1. Web intelligence already exists: `web_search` and `web_fetch` are implemented and wired into the agent.
2. Web fetch already includes domain policy + private-network blocking + redirect revalidation.
3. Google/Slack/Obsidian/Shell tools exist; this is already a multi-surface assistant.
4. Explicit persistent personal memory tools (`save_memory`, `recall_memory`, `list_memories`) are not yet implemented.
5. MCP client support is planned but not yet shipped (`docs/TODO-mcp-client.md`).
6. No built-in background job runner for long agent tasks yet.
7. No voice runtime yet.

This means Co has strong foundations; the largest gaps are memory, MCP extensibility, and async execution.

## 4. Updated Roadmap (MVP-First, Frontier-Aligned)

### Phase 1: Consolidate the core operator loop

**Core capabilities (task execution):**

1. Add explicit local memory tools with manual writes only (no hidden ingestion).
2. Add a planner/result contract that always returns:
   - planned steps,
   - executed tools,
   - citations/evidence links,
   - pending approvals or blocked actions.
3. Add task checkpoints so a turn can pause/resume safely.

**Identity layer (personality foundation):**

4. Add personality system with pre-set templates:
   - Fixed set of personality options (professional, friendly, terse, inquisitive)
   - User-selectable via config or runtime command
   - Personality injected at prompt assembly time
   - Templates are bounded config space (explicit, not implicit)
   - Starting point for evolution toward "Finch"-like companion

5. Design internal knowledge system (distinct from external knowledge):
   - **External knowledge:** Tools (Obsidian, web_search, Google, Slack, MCP servers)
   - **Internal knowledge:** Co's learned context, patterns, user preferences
   - Boundary: External = queried on demand, Internal = always available in context
   - Storage: `.co-cli/internal/` directory for persistent learned knowledge
   - Access: Agent SDK memory handling for session memory, file-based for cross-session

Exit criteria:

1. Users can run multi-step tasks with clear traceability and deterministic approval points.
2. Memory behavior is explainable and auditable.
3. Users can select personality that shapes co's interaction style.
4. Internal knowledge persists across sessions without manual memory tool calls.

### Phase 2: Ship MCP + background execution + user preferences

**Extensibility:**

1. Implement MCP client Phase 1 (stdio) from `docs/TODO-mcp-client.md`.
2. Add background job execution with:
   - explicit start command,
   - status inspection,
   - cancellation,
   - persisted logs/traces.
3. Require approval policy inheritance for every MCP tool call.

**Personalization:**

4. Add user workflow preferences system:
   - **Need:** One-size-fits-all preference won't work for different job contexts
   - **Design approach:** Research peer systems (Codex, Gemini CLI, Claude Code, Aider) + 2026 best practices
   - **Implementation:** Simple, explicit templating solution (code or LLM, explicit > implicit)
   - **Injection point:** Prompt assembly time, after personality, before project instructions
   - **Settings examples:**
     - `auto_approve_tools`: list of trusted tools
     - `verbosity_level`: minimal/normal/verbose
     - `proactive_suggestions`: whether to offer unsolicited ideas
     - `output_format_preferences`: preferred formats by data type
   - **Storage:** `.co-cli/preferences.json` with template selection or explicit overrides

Exit criteria:

1. Co can run long tasks without blocking the chat loop.
2. External tools are extensible via MCP without weakening approvals.
3. Users can configure workflow preferences that adapt co's behavior to their work style.

### Phase 3: Selective autonomy and richer I/O

1. Add optional scheduling for approved recurring tasks.
2. Pilot controlled computer-use style actions only in isolated environments.
3. Add voice-to-voice round trip as an overlay on the text loop (see §4.1).

Exit criteria:

1. Unattended tasks are opt-in, bounded, and reversible.
2. Voice/computer-use do not bypass approval or audit trails.

### 4.1 Voice-to-Voice Round Trip Design

Industry research (2025-2026) across OpenAI Realtime API, Google Gemini Live, Pipecat, LiveKit Agents, Bolna, and Vocode shows strong convergence. No peer CLI tool (codex, gemini-cli, opencode, claude-code, aider) has voice support — this is greenfield.

#### Architecture

Three paradigms exist in production: cascading (STT → LLM → TTS, 800ms-2s), speech-to-speech (single model, 200-500ms), and hybrid (audio encoder → text LLM → TTS, 500-800ms). Co adopts cascading for Phase 3 because it preserves full text transcripts, keeps every component swappable, and integrates directly with the existing pydantic-ai agent. The protocol boundary allows a hybrid upgrade later with zero caller changes.

Pipeline:

```
sounddevice (mic)
    → silero-vad (speech detection)
    → faster-whisper (STT)
    → existing pydantic-ai agent (LLM, unchanged)
    → kokoro-onnx (TTS)
    → sounddevice (speaker)
```

#### Component picks (converged local-first best-in-class)

| Component | Pick | Package | Size | Latency | License |
|-----------|------|---------|------|---------|---------|
| VAD | Silero VAD | `silero-vad` | 2MB | <1ms | MIT |
| STT | faster-whisper base.en | `faster-whisper` | ~150MB | 200-500ms CPU | MIT |
| TTS | Kokoro-82M | `kokoro-onnx` | ~350MB | <300ms | Apache 2.0 |
| Audio I/O | sounddevice | `sounddevice` | tiny | — | MIT |

Total model weight: ~500MB. Five pip packages (+ `soundfile`).

Silero VAD is the de facto standard (Pipecat, LiveKit, RealtimeSTT, WhisperX all use it). Kokoro-82M is the quality/speed winner for local TTS. Piper is a faster fallback at lower quality.

#### Streaming

All production voice frameworks converge: streaming at every stage is mandatory. Partial STT results feed the LLM before the user finishes speaking. LLM token streaming feeds TTS incrementally. The pipeline runs concurrently via asyncio — capture, transcription, reasoning, and synthesis overlap.

#### Activation model

Push-to-talk first (key press to speak, release to submit). Toggled via `co chat --voice` or `/voice` slash command. Continuous VAD (always-listening) deferred — it requires acoustic echo cancellation to prevent the system from hearing its own TTS output, adding significant complexity.

#### Barge-in and interruption

Converged pattern across all production systems: (1) stop TTS playback immediately, (2) truncate unplayed audio buffer, (3) cancel in-progress LLM generation, (4) preserve partial response in conversation context, (5) start processing new utterance. Target: <200ms from detection to new processing.

#### Turn detection

Silence-based VAD with 300-500ms configurable threshold is the baseline. Semantic VAD (ML classifier scoring probability user is done speaking based on content) is the frontier — OpenAI shipped it, still immature. Co starts with silence-based, leaves protocol space for semantic upgrade.

#### Latency target

```
VAD (<1ms) + STT (200ms) + LLM TTFT (300ms cloud) + TTS (300ms) ≈ 800ms
```

LLM TTFT + TTS TTFB account for 90%+ of total loop time. The cloud LLM path (Gemini Flash ~300ms TTFT) hits the 500-800ms production quality bar. Fully local (Ollama 7B ~800ms TTFT) lands at ~1250ms — acceptable but not conversational.

Human response time is ~230ms. Production voice AI bar is 500-800ms. Above 1500ms feels broken.

#### Integration point

Voice wraps the existing text chat loop as an overlay. Text remains primary. The voice loop feeds transcribed text into `run_turn()` and synthesizes the text response — no changes to agent, tools, or approval flow. All voice I/O is logged in the same OTel trace pipeline.

#### Boundaries

1. No wake word in Phase 3 (push-to-talk only).
2. No voice cloning or custom voice training.
3. No phone/telephony integration.
4. No speech-to-speech models — cascading keeps text in the loop for debugging, approval, and logging.

## 5. Boundaries and Non-Goals (Near Term)

1. No default-on autonomous background execution.
2. No implicit sensitive-memory ingestion.
3. No broad browser/desktop automation outside isolated, explicitly approved runs.
4. No replacement of text UX as the primary control surface.

## 6. Principle

Adopt frontier patterns where they improve outcomes, but keep Co's design contract intact:

1. Local-first data/control.
2. Approval-first side effects.
3. Tooling that remains composable, inspectable, and testable.

## 7. Sources

1. OpenAI, "Introducing ChatGPT agent" (July 17, 2025): https://openai.com/index/introducing-chatgpt-agent/
2. OpenAI, "New tools for building agents" (March 11, 2025): https://openai.com/index/new-tools-for-building-agents/
3. OpenAI platform changelog (Responses API / MCP updates): https://platform.openai.com/docs/changelog
4. OpenAI Help, "ChatGPT agent" (updated 2026): https://help.openai.com/en/articles/11752874-chatgpt-agent
5. Anthropic, "Introducing Claude 4" (May 22, 2025): https://www.anthropic.com/news/claude-4
6. Anthropic Claude docs, "Computer use tool": https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
7. Anthropic release notes (2025-2026 API/tooling timeline): https://platform.claude.com/docs/en/release-notes/overview
8. Google I/O 2025 updates (Agent Mode, Project Mariner, Jules, MCP/A2A): https://blog.google/technology/google-io/gemini-updates-io-2025/
9. Google DeepMind, "Project Mariner": https://deepmind.google/models/project-mariner/
10. Google Labs, "Jules now available" (July 23, 2025): https://blog.google/technology/google-labs/jules-now-available/
11. Google Labs, "New ways to build with Jules" (October 2, 2025): https://blog.google/technology/google-labs/jules-tools-jules-api/
12. Google Developers, "Jules proactive updates" (December 10, 2025): https://blog.google/technology/developers/jules-proactive-updates/
13. OpenAI, "Realtime API VAD guide": https://platform.openai.com/docs/guides/realtime-vad
14. OpenAI, "Developer notes on the Realtime API": https://developers.openai.com/blog/realtime-api/
15. Google, "Gemini Live API overview": https://ai.google.dev/gemini-api/docs/live
16. Pipecat (Daily.co), voice AI framework: https://github.com/pipecat-ai/pipecat
17. LiveKit Agents: https://github.com/livekit/agents
18. Silero VAD: https://github.com/snakers4/silero-vad
19. faster-whisper: https://github.com/SYSTRAN/faster-whisper
20. Kokoro-82M (ONNX): https://github.com/thewh1teagle/kokoro-onnx
21. Piper TTS: https://github.com/rhasspy/piper
22. "Cracking the <1-second voice loop" (30+ stack benchmarks): https://dev.to/cloudx/cracking-the-1-second-voice-loop-what-we-learned-after-30-stack-benchmarks-427
23. "Real-Time vs Turn-Based Voice Agent Architecture" (Softcery): https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture
24. "The voice AI stack for building agents in 2026" (AssemblyAI): https://www.assemblyai.com/blog/the-voice-ai-stack-for-building-agents
