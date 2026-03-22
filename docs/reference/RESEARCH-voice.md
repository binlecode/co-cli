# REVIEW: Voice for Co

Status: forward-looking
Aspect: multimodal interaction surface
Pydantic-AI patterns: transcript-first interaction, interruption correctness, streaming I/O boundaries

This document replaces the earlier split between `TODO-voice.md` and the older review. It is the single source of truth for whether voice is worth shipping in co-cli, what the current codebase can support, and what 2026 best practice says we should and should not copy.

## Verdict

Voice is viable for Co only as a narrow overlay on the existing text-first system, not as a primary interaction mode and not as a separate agent stack.

The benefit is real but bounded:

- accessibility and hands-busy use
- faster short follow-ups while reading logs or docs
- a stronger companion feel for the product vision

The adoption cost is also real and likely higher than the direct product gain:

- heavy optional dependencies
- fragile cross-platform audio I/O
- difficult interruption correctness
- low demand for spoken interaction in a terminal-centric engineering workflow

Tech lead recommendation: keep voice deferred until background execution and file tools are mature, then ship only a tightly scoped Phase K MVP behind an optional extra.

## Current Co Reality

Any voice design has to fit the system that exists today:

- `co_cli/_orchestrate.py` already provides the execution primitive: `run_turn()`
- interrupted turns are already patched with dangling tool-return repair plus an abort marker
- `co_cli/main.py` already centralizes the REPL loop, slash-command dispatch, Ctrl-C handling, and post-turn hooks
- `co_cli/status.py` already owns environment diagnostics, which is the natural home for future audio checks
- OTel trace output already flows into `co-cli.db` and is surfaced through `co logs`, `co traces`, and `co tail`

That means the right integration model is still an I/O overlay:

`mic/audio-in -> voice frontend -> text prompt -> run_turn() -> text output -> TTS/audio-out`

Not acceptable:

- a second agent loop
- voice-specific approval logic
- hidden speech-only context that diverges from text history
- any design that bypasses normal interrupt recovery

## 2026 Frontier Practice

Top systems in 2026 converge on low-latency streaming voice, but not on the same deployment shape.

### What frontier systems do

- OpenAI Realtime exposes low-latency audio and recommends a direct audio path for real-time speech apps, while also explicitly documenting a chained architecture for text-centric applications where transcription remains first-class.
- Gemini Live / Multimodal Live provides native streaming audio/video sessions and built-in tool usage, pushing the state of the art toward multimodal live sessions rather than discrete terminal turns.
- LiveKit Agents treats turn detection, interruption, and transport as first-class concerns and recommends Silero as the default local VAD plugin for production pipelines.
- Pipecat-style systems optimize modular cascades with aggressive queueing, interruption handling, and waterfalling between STT, LLM, and TTS.

### What Co should borrow

- streaming pipeline stages
- explicit turn detection
- barge-in as a product requirement, not a nice-to-have
- telemetry for stage latency and interruption events
- component isolation so STT/TTS/VAD can be swapped independently

### What Co should reject

- continuous always-on listening as the default
- WebRTC-first architecture
- cloud speech-to-speech as the only supported path
- any approach that weakens transcript fidelity for traces, approvals, or history correctness

For Co, frontier voice best practice is not "copy the most advanced speech stack". It is "adopt the parts that preserve the text-grounded agent contract".

## Benefit vs Adoption Tradeoff

| Dimension | Benefit | Cost / Risk | Assessment |
|---|---|---|---|
| Accessibility | High | Moderate implementation cost | Strongest reason to ship |
| Terminal convenience | Medium | High support burden | Useful, but niche |
| Companion/product vision | Medium | Moderate | Supports roadmap identity |
| Core productivity for engineers | Low to medium | High | Weak ROI versus file tools/background tasks |
| Latency perception | Medium if done well | High | Must feel responsive or users will abandon it |
| Platform support | Broad in theory | High in practice | Main adoption drag |

Net: voice is strategically valid, but not near the top of the backlog.

## Architecture Recommendation

### Phase K MVP

Ship only push-to-talk, not continuous listening.

Recommended flow:

1. user holds a key to capture audio
2. local VAD gates utterance boundaries
3. STT produces a full text transcript
4. transcript is submitted through normal `run_turn()`
5. assistant text is committed to history exactly as today
6. TTS speaks only committed assistant text

This is intentionally conservative. It gives Co a voice mode without changing the conversational contract.

### Why not native speech-to-speech

Native S2S is where frontier products are going, but it is the wrong first implementation for Co:

- Co needs text transcripts as the canonical record
- Co’s approval and safety model is built around explicit turn history
- the CLI is not a live-call environment
- terminal users will tolerate slightly slower speech if correctness is high

Inference from frontier systems: Co should treat speech-to-speech as a possible future transport optimization, not as the foundational architecture.

### Streaming policy

Do not start with speculative early prompting from partial STT. It adds state complexity before the basics are proven.

Start with:

- stream TTS from committed assistant text chunks only
- no partial-user-transcript submission to the LLM
- no continuous microphone while TTS is playing

That gives a simpler correctness envelope for the first version.

## Interruption and History Correctness

This is the hardest part and the main reason the feature remains deferred.

Co already has turn interruption recovery for keyboard interrupts. Voice barge-in must build on that exact mechanism, not invent a parallel path.

Non-negotiable rule:

Only the assistant text that was actually played to the user may be preserved as spoken history before interruption.

Implication:

- generated but unsynthesized text must not be committed
- synthesized but unplayed text must not be committed
- interruption handling needs a playback cursor, not just a text buffer

If this is wrong, Co’s internal history diverges from what the user actually heard, and the next turn becomes untrustworthy.

Before real audio integration, prove this state machine with text-only tests that simulate:

- assistant streaming
- partial commit
- mid-stream interruption
- restart on a new user turn

## Component Guidance

The earlier component picks are still directionally sound, but they should be treated as candidates, not frozen decisions.

Current recommendation:

- VAD: Silero remains the default benchmark for local turn detection and is aligned with LiveKit guidance.
- STT: a local Whisper-family backend is still the pragmatic starting point for transcript fidelity and offline operation.
- TTS: use a local model only if startup time and footprint stay behind an optional dependency boundary.
- Audio I/O: keep the hardware layer thin and replaceable.

What matters more than the exact package:

- lazy import and lazy model load
- strict optional extra boundary
- swappable provider interface
- fixture-driven testing path independent of real hardware

## Adoption Constraints

If voice ships, the following are mandatory:

1. Optional install only. Core `co` must stay fast and small without audio deps.
2. Text remains canonical. Voice is an input/output surface, not a second interaction model.
3. `co status` grows audio diagnostics before general release.
4. Functional tests use real audio fixtures on disk, not mocks and not microphone-dependent CI.
5. Voice spans and metrics land in existing OTel tracing, not a separate logging path.
6. Slash-command and CLI activation must fail gracefully when voice extras are missing.

## Delivery Gate

Do not start implementation until these are already true:

- background execution is shipped
- file tools are shipped
- interrupt state-machine tests exist for partial assistant commit
- a small audio HAL design exists with fixture-backed tests

## Decision

Keep voice in the roadmap as a deferred Phase K capability.

When it moves, the right product shape is:

- push-to-talk
- transcript-first
- local or hybrid cascading pipeline
- explicit interruption semantics
- full reuse of `run_turn()`, approval flow, and OTel traces

Anything more ambitious than that should be treated as a second phase after the MVP proves user demand.

## External References

- OpenAI Realtime API docs: real-time audio path plus documented chained alternative for text-centric apps
- Google Gemini Live / Multimodal Live docs: live multimodal sessions with streaming audio and built-in tool use
- LiveKit Agents docs: production voice-agent pipeline patterns and Silero VAD recommendation
- Pipecat docs: modular streaming voice pipelines with interruption-oriented orchestration
