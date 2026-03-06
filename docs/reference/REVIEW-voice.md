# REVIEW: Voice-to-Voice System Architecture & Tech Lead Mandate (Phase 3)

**Document Reviewed**: `docs/TODO-voice.md`
**Date of Review**: February 28, 2026
**Context**: Evaluation against 2025/2026 production-grade Voice AI standards and Co-CLI system principles.

---

## PART 1: System Architecture Review

### 1. Executive Summary

The architectural plan outlined in `TODO-voice.md` for adding voice capabilities to the Co CLI is **pragmatic, highly aligned with the project's core principles, and achievable**. 

By choosing a "Cascading Pipeline" (STT → LLM → TTS) and a "Push-to-Talk" activation model, the design deliberately trades the ultra-low latency (<500ms) of bleeding-edge Speech-to-Speech (S2S) models for absolute control, debuggability, and adherence to Co's text-first/privacy-first mandates. 

While the broader voice AI industry (OpenAI Realtime, Gemini Live, LiveKit) has largely shifted to continuous-listening S2S architectures over WebRTC, Co's proposed local-first overlay is the correct technical choice for a terminal-based engineering assistant.

### 2. Industry State (2025-2026) vs. Co's Design

In 2026, production-grade voice agents generally fall into two categories:

1. **Unified Speech-to-Speech (S2S)**: Models like Gemini 2.0 Flash Live API or GPT-4o Realtime that process audio-in to audio-out. They achieve <500ms latency, preserve emotional prosody, and handle native barge-in. Frameworks like **LiveKit** dominate this space.
2. **Streaming Modular Pipelines**: Highly optimized cascading setups (STT → LLM → TTS) using frameworks like **Pipecat**, achieving 500ms-800ms latency through aggressive parallel execution (waterfalling).

**Comparison:**

| Feature | 2026 Industry Standard (e.g., LiveKit/Pipecat) | Co `TODO-voice.md` Design | Assessment |
| :--- | :--- | :--- | :--- |
| **Pipeline Architecture** | Native S2S or Cloud Cascading | Local-first Cascading (STT→LLM→TTS) | **Appropriate.** S2S models lose explicit text transcripts, which Co needs for telemetry, debugging, and terminal rendering. |
| **Activation / Mic** | Continuous Listening + AEC | Push-to-Talk (PTT) | **Excellent tradeoff.** PTT completely avoids the complex Acoustic Echo Cancellation (AEC) needed for terminal environments. |
| **Target Latency** | < 500ms | ~800ms (Cloud), ~1250ms (Local) | **Acceptable risk.** 1250ms local latency borders on sluggish, but for a dev tool, predictable correctness beats conversational speed. |
| **Barge-in / Interruption** | WebRTC Stop-and-Sync | PTT interruption / async cancel | **Solid.** The design accurately describes the "truncate buffer + preserve partial transcript" method used by top frameworks. |

### 3. Evaluation of Component Choices

The selection of local-first components in the document represents the best-in-class for offline, lightweight execution:

*   **VAD (Silero VAD)**: Remains the undisputed industry standard for local VAD. At 2MB and <1ms latency, it is used under the hood by nearly all major frameworks (including Pipecat).
*   **STT (faster-whisper)**: Highly optimized. While cloud providers (Deepgram) can hit <300ms, faster-whisper base.en is the most reliable balance of speed and footprint (~150MB) for local usage.
*   **TTS (Kokoro-82M)**: A breakthrough in local TTS. It significantly outperforms Piper in naturalness while keeping latency <300ms via ONNX.
*   **Audio I/O (sounddevice)**: Standard, cross-platform, minimal overhead.

**Verdict**: The component stack is mature, heavily battle-tested, and perfectly matches Co's requirement to run locally without heavy GPU dependencies.

### 4. Architectural Deep Dive & Best Practices

#### The "Overlay" Pattern
The decision to treat voice as an I/O wrapper around the existing `run_turn()` logic is the strongest architectural choice in the document. By not altering the underlying Pydantic-AI agent, tools, or approval flows, you avoid the "forked logic" trap that plagues many multimodal tools.

#### Streaming and Waterfalling
The document correctly identifies that streaming at every stage is mandatory. In 2026, the best practice is **Parallel Execution**:
1. STT streams word-by-word into the LLM prompt buffer.
2. The LLM starts generating tokens before the user finishes speaking (Speculative Inference / Early Prompting).
3. TTS begins synthesizing the first sentence fragment while the LLM is still generating the second.
*Recommendation*: Ensure the asyncio pipeline utilizes a pub/sub or queue-based ring buffer between these components to prevent backpressure, exactly as Pipecat handles chunking.

#### Barge-in Protocol
The document accurately outlines the "Stop-and-Sync" protocol. 
*Recommendation*: Be extremely precise with the "Preserve partial response" step. If Co is interrupted, you must calculate exactly which words were synthesized and played through `sounddevice` before the interruption, and append *only those words* to the LLM history. If you append the LLM's full generated text, the agent will hallucinate having said things the user never actually heard.

---

## PART 2: Tech Lead Perspective & Mandate

### 1. The Reality Check: Dependency & Distribution Bloat
The industry research highlights `faster-whisper` (~150MB model) and `kokoro-onnx` (~350MB model) as lightweight choices. However, the *model* size isn't the true problem—the *runtime* size is. 
To run these locally, we need `ctranslate2`, `onnxruntime`, and potentially heavy numerical libraries. This will balloon our clean `uv` environment from tens of megabytes to well over 1-2 GB. 
**Tech Lead Verdict**: Unacceptable for a default installation. Co is a fast, terminal-native tool. If we ship voice, it **must** be an optional install group (e.g., `uv tool install co-cli[voice]`). The core application must start instantly and degrade gracefully if the voice dependencies are missing. We will not punish our core text-first users with a gigabyte-scale download.

### 2. The Audio I/O Support Nightmare
The proposal casually lists `sounddevice` as a "tiny, cross-platform" wrapper. 
**Tech Lead Verdict**: Any veteran systems engineer knows cross-platform microphone access is a minefield. On Linux, we will immediately hit PipeWire vs. PulseAudio vs. ALSA driver conflicts. On macOS, terminal microphone permissions (TCC prompts) are notoriously finicky and often require terminal restarts. Shipping this means we are inviting a massive influx of support issues completely unrelated to our core AI value proposition.
*Mitigation*: We must strictly encapsulate the audio interface and provide a built-in diagnostic module (e.g., `co status --audio`) to debug missing permissions or dead drivers before we officially launch the feature.

### 3. Strict Testing Policy Violation
Co's `GEMINI.md` mandates: *"Functional Testing: No mocks. Tests must verify real side effects."*
**Tech Lead Verdict**: How do we test `sounddevice` in headless GitHub Actions? CI runners do not have sound cards or microphones. If we rely on hardware, our CI breaks. If we mock `sounddevice`, we violate our core engineering policy. 
*Decision*: We must design a rigorous Audio HAL (Hardware Abstraction Layer). For functional tests, the "real" implementation must read from/write to `.wav` fixtures on disk, mathematically proving the audio bytes are processed correctly without needing an actual speaker/mic or a mock object.

### 4. The "Overlay" Pattern vs. State Management
The research praises the "Voice as an I/O Overlay" approach, keeping the Pydantic-AI agent untouched. 
**Tech Lead Verdict**: While theoretically clean, handling "barge-in" requires canceling active `asyncio` tasks and accurately truncating the LLM text context to *exactly* what was played out of the speaker. This is highly stateful, race-condition prone, and brittle. If the barge-in state machine fails, the conversation history gets corrupted, violating our "Privacy & Safe Execution" principles because the user can no longer trust what the Brain "thinks" happened.
*Decision*: Before building the STT/TTS pipeline, we must build and rigorously test the "Interruption & Context Truncation" state machine using purely rapid-fire text inputs.

### 5. Strategic Priority (Is this a gimmick?)
Co is a CLI tool for engineers. We live on keyboards. While voice is the 2026 industry darling for consumer agents, how often does a developer want to talk out loud to their terminal in a busy office?
**Tech Lead Verdict**: Voice is a powerful accessibility and convenience feature for remote work, but it does not enhance our core loop (safe shell execution, local reasoning). It remains firmly deferred to Phase 3 or 4. We will prioritize robust Context Governance, Memory Lifecycle, and Background Execution over Voice.

---

## Conclusion & Mandates for Phase 3 Execution

If/when we proceed with Voice, the following constraints are non-negotiable:
1. **Optional Dependency Boundary**: Voice packages must be strictly isolated behind an optional `[voice]` extra in `pyproject.toml`.
2. **File-Backed Audio Testing**: Functional tests must use bit-exact `.wav` file pipelines to maintain the "No Mocks" policy in CI.
3. **Diagnostic Tooling**: Must ship with `co status` checks for audio hardware and OS permissions.
4. **Text-First Truncation Testing**: The barge-in context truncation logic must be proven to be race-condition free with text before any audio bytes are synthesized.