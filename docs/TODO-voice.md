# TODO: Voice-to-Voice Round Trip

**Status**: Deferred
**Effort**: TBD (research to be refreshed before implementation)
**Priority**: Medium

---

## Overview

Voice-to-voice interaction as an overlay on the existing text chat loop. Text remains primary. Voice wraps `run_turn()` with speech input/output — no changes to agent, tools, or approval flow.

**Key Constraint**: Push-to-talk only in Phase 3. Continuous listening (always-on VAD) requires acoustic echo cancellation to prevent feedback loops — significant complexity deferred.

---

## Industry Research (2025-2026)

Research across OpenAI Realtime API, Google Gemini Live, Pipecat, LiveKit Agents, Bolna, and Vocode shows strong convergence on architecture patterns and component choices.

**Competitive landscape**: No peer CLI tool (codex, gemini-cli, opencode, claude-code, aider) has voice support — this is greenfield for CLI agents.

---

## Architecture

### Three Paradigms (Production Systems)

1. **Cascading**: STT → LLM → TTS (800ms-2s latency)
2. **Speech-to-speech**: Single model (200-500ms latency)
3. **Hybrid**: Audio encoder → text LLM → TTS (500-800ms latency)

**Co's Choice: Cascading** for Phase 3 because:
- Preserves full text transcripts (debugging, approval, logging)
- Keeps every component swappable (STT, LLM, TTS independent)
- Integrates directly with existing pydantic-ai agent (zero agent changes)
- Protocol boundary allows hybrid upgrade later with zero caller changes

### Pipeline

```
sounddevice (mic)
    → silero-vad (speech detection)
    → faster-whisper (STT)
    → existing pydantic-ai agent (LLM, unchanged)
    → kokoro-onnx (TTS)
    → sounddevice (speaker)
```

**Key insight**: Voice is an I/O overlay. The agent, tools, approval flow, and OTel tracing remain unchanged.

---

## Component Picks (Converged Local-First Best-in-Class)

| Component | Pick | Package | Size | Latency | License |
|-----------|------|---------|------|---------|---------|
| **VAD** | Silero VAD | `silero-vad` | 2MB | <1ms | MIT |
| **STT** | faster-whisper base.en | `faster-whisper` | ~150MB | 200-500ms CPU | MIT |
| **TTS** | Kokoro-82M | `kokoro-onnx` | ~350MB | <300ms | Apache 2.0 |
| **Audio I/O** | sounddevice | `sounddevice` | tiny | — | MIT |

**Total model weight**: ~500MB
**Total dependencies**: 5 pip packages (`silero-vad`, `faster-whisper`, `kokoro-onnx`, `sounddevice`, `soundfile`)

### Component Rationale

**Silero VAD**: De facto standard (Pipecat, LiveKit, RealtimeSTT, WhisperX all use it). 2MB, <1ms latency, MIT license.

**faster-whisper**: OpenAI Whisper optimized with CTranslate2. Base.en model is 150MB, 200-500ms CPU latency. Accurate for English, local-first.

**Kokoro-82M**: Quality/speed winner for local TTS. 350MB ONNX model, <300ms latency, Apache 2.0. Piper is faster fallback at lower quality if needed.

**sounddevice**: Minimal audio I/O wrapper. Tiny, MIT license, works across platforms.

---

## Streaming

**Converged pattern across all production voice frameworks**: Streaming at every stage is mandatory.

1. **Partial STT results** feed the LLM before the user finishes speaking
2. **LLM token streaming** feeds TTS incrementally
3. **Pipeline runs concurrently** via asyncio — capture, transcription, reasoning, and synthesis overlap

**Result**: Reduces perceived latency. User hears TTS output before LLM finishes generating full response.

---

## Activation Model

**Push-to-talk first** (key press to speak, release to submit):
- Toggled via `co chat --voice` or `/voice` slash command
- Simple UX, no false positives, no echo cancellation needed

**Continuous VAD (always-listening) deferred**:
- Requires acoustic echo cancellation to prevent system from hearing its own TTS output
- Adds significant complexity (AEC algorithms, calibration, platform-specific tuning)
- OpenAI Realtime API and Gemini Live use this, but with server-side processing

**Phase 3 constraint**: Push-to-talk only. Continuous listening is Phase 4+ if user demand justifies complexity.

---

## Barge-in and Interruption

**Converged pattern across all production systems**:

1. **Stop TTS playback immediately** (user starts speaking while co is talking)
2. **Truncate unplayed audio buffer** (discard remaining synthesis)
3. **Cancel in-progress LLM generation** (interrupt agent mid-turn)
4. **Preserve partial response** in conversation context (what co said before interruption)
5. **Start processing new utterance** (user's barge-in)

**Target**: <200ms from detection to new processing.

**Implementation**: VAD detects new speech → cancel asyncio tasks (TTS, LLM) → preserve partial transcript → start new turn.

---

## Turn Detection

**Baseline**: Silence-based VAD with 300-500ms configurable threshold.
- User stops speaking → VAD detects silence → submit turn to LLM
- Tunable threshold balances false-positive pauses vs. responsiveness

**Frontier**: Semantic VAD (ML classifier scoring probability user is done speaking based on content)
- OpenAI shipped it in Realtime API (2025), still immature
- Harder to implement, requires training data, can misfire

**Phase 3 choice**: Silence-based VAD (proven, simple, tunable). Leave protocol space for semantic upgrade if frontier matures.

---

## Latency Target

### Breakdown

```
VAD (<1ms) + STT (200ms) + LLM TTFT (300ms cloud) + TTS (300ms) ≈ 800ms
```

**Bottlenecks**:
- **LLM TTFT + TTS TTFB** account for 90%+ of total loop time
- STT and VAD are negligible (<200ms combined)

### Performance Tiers

| LLM Path | TTFT | Total Latency | Quality |
|----------|------|---------------|---------|
| **Gemini Flash (cloud)** | ~300ms | ~800ms | Production quality ✅ |
| **Ollama 7B (local)** | ~800ms | ~1250ms | Acceptable but not conversational ⚠️ |

### Context

- **Human response time**: ~230ms (biology baseline)
- **Production voice AI bar**: 500-800ms (feels natural)
- **Above 1500ms**: Feels broken (user perceives lag)

**Phase 3 target**: <800ms for cloud LLM path (Gemini Flash). Local path acceptable but slower.

---

## Integration Point

**Voice wraps the existing text chat loop as an overlay**:

1. Voice loop feeds **transcribed text** into `run_turn()` (same entry point as keyboard input)
2. Agent generates **text response** (unchanged logic)
3. Voice loop synthesizes **text → audio** and plays to speaker
4. **No changes** to agent, tools, or approval flow
5. **All voice I/O logged** in same OTel trace pipeline (transcripts, latency, errors)

**Design principle**: Text remains primary. Voice is a convenience overlay, not a separate mode.

---

## Boundaries (Phase 3)

**What's included**:
- ✅ Push-to-talk activation
- ✅ Silence-based turn detection
- ✅ Cascading pipeline (STT → LLM → TTS)
- ✅ Barge-in and interruption
- ✅ Streaming at all stages
- ✅ Local-first components (Silero VAD, faster-whisper, Kokoro-82M)
- ✅ OTel logging (transcripts, latency, errors)

**What's excluded** (Phase 3 boundaries):
- ❌ Wake word ("Hey Co") — push-to-talk only
- ❌ Continuous listening (always-on VAD) — requires echo cancellation
- ❌ Voice cloning or custom voice training
- ❌ Phone/telephony integration
- ❌ Speech-to-speech models — cascading keeps text in the loop for debugging, approval, and logging

---

## External Sources (Voice & Audio Research)

### APIs & Frameworks
1. OpenAI, "Realtime API VAD guide": https://platform.openai.com/docs/guides/realtime-vad
2. OpenAI, "Developer notes on the Realtime API": https://developers.openai.com/blog/realtime-api/
3. Google, "Gemini Live API overview": https://ai.google.dev/gemini-api/docs/live
4. Pipecat (Daily.co), voice AI framework: https://github.com/pipecat-ai/pipecat
5. LiveKit Agents: https://github.com/livekit/agents

### Components
6. Silero VAD: https://github.com/snakers4/silero-vad
7. faster-whisper: https://github.com/SYSTRAN/faster-whisper
8. Kokoro-82M (ONNX): https://github.com/thewh1teagle/kokoro-onnx
9. Piper TTS: https://github.com/rhasspy/piper

### Industry Research
10. "Cracking the <1-second voice loop" (30+ stack benchmarks): https://dev.to/cloudx/cracking-the-1-second-voice-loop-what-we-learned-after-30-stack-benchmarks-427
11. "Real-Time vs Turn-Based Voice Agent Architecture" (Softcery): https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture
12. "The voice AI stack for building agents in 2026" (AssemblyAI): https://www.assemblyai.com/blog/the-voice-ai-stack-for-building-agents

---

## Implementation Notes (For Phase 3 Planning)

### When to Execute This Phase

1. **After Phase 2c complete** (background execution shipped)
2. **Before committing to implementation**, refresh 2026 voice AI research:
   - Verify Silero VAD, faster-whisper, Kokoro-82M still best-in-class
   - Check if speech-to-speech models matured (text-free pipeline)
   - Evaluate new entrants (voice AI evolving rapidly)

### Estimated Effort

**TBD** — Requires fresh research and design iteration. Rough estimate: 2-3 weeks for MVP (push-to-talk, cascading pipeline, local components, OTel integration).

### Success Criteria

1. ✅ Push-to-talk works (`co chat --voice` or `/voice` command)
2. ✅ <800ms latency on cloud LLM path (Gemini Flash)
3. ✅ Barge-in interrupts TTS/LLM within 200ms
4. ✅ Full transcripts logged in OTel traces
5. ✅ No changes to agent, tools, or approval flow (overlay pattern validated)
6. ✅ 15+ functional tests (activation, latency, interruption, error handling)

---

**Status**: Research captured, implementation deferred until Phase 2c complete. This document will be refreshed before Phase 3 execution to validate component choices against 2026+ frontier.
