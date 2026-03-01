# TODO: Voice-to-Voice Round Trip

**Priority:** P3 (deferred until background execution ships)
**Dependency:** `TODO-background-execution.md` must ship first (Phase 2c complete)

Voice is an I/O overlay on the existing text chat loop. The agent, tools, approval flow,
and OTel tracing are unchanged. Text remains primary.

---

## 1 — Voice I/O Layer (P3)

**What:** Add a `co_cli/voice.py` module implementing the cascading pipeline:
`sounddevice (mic) → Silero VAD → faster-whisper (STT) → existing run_turn() → Kokoro-82M TTS → sounddevice (speaker)`.
Activated via `co chat --voice` flag or `/voice` slash command in the REPL.

**Why:** No peer CLI tool (codex, gemini-cli, opencode, claude-code, aider) has voice support.
The cascading architecture keeps full text transcripts in the OTel trace (debugging, approval,
logging) and allows each component to be swapped independently.

**How:**
- Component picks (confirmed best-in-class as of 2026):
  - VAD: `silero-vad` (~2 MB, <1 ms, MIT) — de facto standard in Pipecat and LiveKit
  - STT: `faster-whisper` base.en (~150 MB, 200–500 ms CPU, MIT)
  - TTS: `kokoro-onnx` (~350 MB ONNX, <300 ms, Apache 2.0)
  - Audio I/O: `sounddevice` (tiny, MIT)
- Activation model: push-to-talk only in Phase 3 (key press to speak, release to submit). No
  continuous listening — requires echo cancellation (deferred to Phase 4+).
- Pipeline runs concurrently via asyncio — capture, transcription, reasoning, and synthesis
  overlap. Reduces perceived latency.
- Voice deps are optional extras: `uv sync --extra voice`. Guard all voice imports so
  `co chat` (without `--voice`) does not require the ~500 MB model weight.

**Done when:**
- `co chat --voice` launches the push-to-talk loop without error
- STT transcripts are logged in OTel traces under the `chat_turn` span
- TTS plays agent responses through the speaker
- End-to-end latency on Gemini Flash is under 800 ms (VAD + STT + LLM TTFT + TTS)
- No changes to `agent.py`, tool registration, or approval flow (overlay validated)
- `uv run pytest tests/test_voice.py` passes (activation, pipeline wiring, error handling)

**Before implementing:** Refresh component research — verify Silero VAD, faster-whisper,
Kokoro-82M are still best-in-class. Check if speech-to-speech models (single-model pipeline,
200–500 ms) have matured enough to replace the cascading stack.

**Files:**
- `co_cli/voice.py` (new) — `VoicePipeline` class, push-to-talk loop, STT/TTS wrappers
- `co_cli/main.py` — add `--voice` flag to `co chat`, add `/voice` slash command handler
- `pyproject.toml` — `[project.optional-dependencies] voice = [...]`
- `tests/test_voice.py` (new)

---

## 2 — Barge-in and Interruption (P3, with item 1)

**What:** When the user starts speaking while co is playing TTS output, immediately stop
TTS playback, cancel the in-progress LLM generation, and start processing the new utterance.
Preserve in the conversation context whatever partial response co had spoken before interruption.

**Why:** Without barge-in, the user must wait for co to finish speaking before they can correct
it or ask a follow-up. Sub-200 ms interruption detection is the production voice AI standard.

**How:** VAD detects new speech during TTS playback. Cancel the active `asyncio.Task` for TTS
synthesis and playback. Cancel the active `run_turn()` task via the existing Ctrl-C abort path
(inject `ABORT_MARKER` — same mechanism used for keyboard interrupt). Preserve the partial
TTS text in the turn history before starting the new turn. Target: <200 ms from detection to
processing start.

**Done when:** While co is playing a TTS response, pressing the push-to-talk key or
speaking interrupts playback within 200 ms and starts a new turn. The partial co response
appears in the conversation history before the user's barge-in message.

---

## 3 — OTel Logging for Voice Turns (P3, with item 1)

**What:** Log voice-specific telemetry in the existing `co-cli.db` OTel trace pipeline:
transcript text, STT latency, TTS latency, total round-trip latency, VAD confidence, and
any STT/TTS errors.

**Why:** Without structured trace data, voice latency regressions are invisible. The `co traces`
viewer already shows turn-level spans — voice should be visible as attributes on the
`chat_turn` span, not a separate system.

**How:** In `VoicePipeline`, after each pipeline stage, record elapsed time as OTel span
attributes: `voice.stt_latency_ms`, `voice.tts_latency_ms`, `voice.transcript`,
`voice.tts_chars`, `voice.total_latency_ms`. Use the existing `instrument_all()` span context
— voice pipeline stages become child spans of `chat_turn`.

**Done when:** After a voice turn, `co traces` shows `voice.stt_latency_ms` and
`voice.transcript` attributes on the `chat_turn` span.

---

## Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `voice_stt_model` | `CO_VOICE_STT_MODEL` | `"base.en"` | faster-whisper model size |
| `voice_tts_voice` | `CO_VOICE_TTS_VOICE` | `"af_heart"` | Kokoro voice ID |
| `voice_vad_threshold` | `CO_VOICE_VAD_THRESHOLD` | `0.5` | Silero VAD confidence cutoff |
| `voice_silence_ms` | `CO_VOICE_SILENCE_MS` | `400` | Silence duration (ms) to submit turn |

---

## External References

- Silero VAD: https://github.com/snakers4/silero-vad
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Kokoro-82M (ONNX): https://github.com/thewh1teagle/kokoro-onnx
- Pipecat (voice AI framework): https://github.com/pipecat-ai/pipecat
- LiveKit Agents: https://github.com/livekit/agents
- "Cracking the <1-second voice loop": https://dev.to/cloudx/cracking-the-1-second-voice-loop-what-we-learned-after-30-stack-benchmarks-427
