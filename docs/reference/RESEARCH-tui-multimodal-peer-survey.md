# RESEARCH: TUI Multimodal Input — Peer Survey (hermes-agent, openclaw, opencode)
_Date: 2026-06-09_

A code-backed comparison of how three peer terminal agents handle multimodal input (image and voice/audio) in their TUI layer, and where in the stack the actual bytes are processed. Based on direct source scans of `~/workspace_genai/hermes-agent/`, `~/workspace_genai/openclaw/`, and `~/workspace_genai/opencode/`.

The three sit on a clean spectrum of **where multimodal intake lives**: openclaw has none in the TUI, hermes forwards file *paths* to its backend, and opencode reads bytes and builds content blocks **in the TUI process itself**.

## Comparison Matrix

| Dimension | openclaw | hermes-agent | opencode |
|---|---|---|---|
| **Image intake in TUI** | None | Path-forwarding only | Full client-side byte intake |
| **Intake surfaces** | — | Pasted/dropped file paths | Clipboard image paste · file-path paste · `@`-file mention |
| **Who reads image bytes** | Gateway only (non-TUI callers) | Backend (TUI sends path) | **TUI process** (base64 in-TUI) |
| **Content block built where** | Gateway | Backend gateway | **TUI** (`FilePart` w/ `data:` URL) |
| **TUI→backend protocol** | `chat.send {message: string}` (text-only) | `prompt.submit {session_id, text}` (text-only) | `session.prompt {parts: [...]}` (**multimodal**) |
| **Backend topology** | Remote gateway | Remote gateway | Local in-process Effect server/SDK |
| **Image resize/normalize** | Gateway (≤2 MB inline, else disk) | Backend | Server-side Photon WASM (tool-read path; ≤2000² / 5 MB) |
| **Voice/audio input** | None in TUI (channel plugins only) | STT-via-backend over RPC | **None** (audio is notification-sound output only) |
| **Terminal image rendering** | Text placeholder `[image/jpeg 42kb]` | None (path notice line) | MIME badges (`img`/`pdf`) + filename |
| **Graphics protocol** | None (OSC 8 links only) | None | None (`useKittyKeyboard` is keys, not graphics) |

**Spectrum (least → most client-side multimodal):** openclaw (no intake) → hermes (path-forwarding RPC) → opencode (client-side base64 content blocks).

---

## openclaw — Pure-Text TUI, Multimodal Is Gateway-Only

The openclaw TUI is a **pure-text terminal chat surface**. It accepts plain text through a `CustomEditor` component, routes it via `chat.send` RPC, and renders responses as Markdown. There is no image paste/drag-drop intake and no voice/audio capture in the TUI layer.

### Image path

- **No TUI intake surface.** `CustomEditor` ([custom-editor.ts#L46](/Users/binle/workspace_genai/openclaw/src/tui/components/custom-editor.ts)) wraps the `pi-tui` `Editor` and overrides only keyboard shortcuts — no paste handler, no clipboard inspection, no image branch.
- **Protocol is text-only at the TUI boundary.** `ChatSendOptions` ([tui-backend.ts#L13](/Users/binle/workspace_genai/openclaw/src/tui/tui-backend.ts#L13)) carries `message: string` with no `attachments` field; the submit handler signature is `sendMessage: (value: string) => Promise<void>` ([tui-submit.ts#L4](/Users/binle/workspace_genai/openclaw/src/tui/tui-submit.ts#L4)).
- **Attachments exist in the protocol but are never populated by the TUI.** `ChatSendParamsSchema` defines `attachments: Type.Optional(Type.Array(Type.Unknown()))` ([logs-chat.ts#L78](/Users/binle/workspace_genai/openclaw/packages/gateway-protocol/src/schema/logs-chat.ts#L78)), but neither the gateway backend ([gateway-chat.ts#L193](/Users/binle/workspace_genai/openclaw/src/tui/gateway-chat.ts#L193)) nor the embedded backend ([embedded-backend.ts#L334](/Users/binle/workspace_genai/openclaw/src/tui/embedded-backend.ts#L334)) passes it.
- **All image processing is gateway-side.** `parseMessageWithAttachments` ([chat-attachments.ts#L241](/Users/binle/workspace_genai/openclaw/src/gateway/chat-attachments.ts#L241)) handles base64 intake from HTTP/node-event callers: MIME sniff, size gate (default 20 MB), inline `ChatImageContent` for ≤2 MB ([#L20](/Users/binle/workspace_genai/openclaw/src/gateway/chat-attachments.ts#L20)), disk offload (`media://inbound/<id>`) for larger. Content-block assembly happens only in `src/llm/providers/`, never in `src/tui/`.
- **Tool-result images render as text placeholders.** `ToolExecutionComponent` renders `[${mime}${size}${omitted}]` (e.g. `[image/jpeg 42kb]`) for `type === "image"` blocks ([tool-execution.ts#L41](/Users/binle/workspace_genai/openclaw/src/tui/components/tool-execution.ts#L41)). No graphics protocol; OSC 8 hyperlinks are the only non-text sequences.

### Voice path

- **Zero voice footprint in `src/tui/`** — no references to `talk`, `realtimeTranscription`, `voice`, `microphone`, or `record`.
- Voice infra lives in `src/talk/` (realtime session lifecycle, [session-runtime.ts](/Users/binle/workspace_genai/openclaw/src/talk/session-runtime.ts)) and `src/realtime-transcription/` (streaming STT over WebSocket). Both are registered as plugin capabilities ([registry.ts#L1305](/Users/binle/workspace_genai/openclaw/src/plugins/registry.ts#L1305)) and consumed by **channel plugins** (Discord voice, etc.), not the TUI. `sendAudio(audio: Buffer)` has no caller in `src/tui/`.
- Batch STT for audio file attachments ([audio-transcription-runner.ts](/Users/binle/workspace_genai/openclaw/src/media-understanding/audio-transcription-runner.ts)) is reachable only from non-TUI channels.

---

## hermes-agent — TUI Forwards Image Paths; Voice Is STT-via-Backend

The hermes-agent TUI is a thin control layer. It has image intake, but **path-forwarding only**: it detects pasted/dropped paths and sends them to the backend via `image.attach` RPC. No image bytes are processed or embedded in the TUI. Voice is STT-over-RPC.

### Image path

- **Intake surfaces:** `Cmd/Ctrl+V` paste emits a `PasteEvent` ([textInput.tsx#L920](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/components/textInput.tsx#L920)); `/paste` command calls `rpc('clipboard.paste', {session_id})` ([core.ts#L399](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/slash/commands/core.ts#L399)); drag-drop paths route to `image.attach`.
- **Client-side path heuristic, no bytes.** `looksLikeDroppedPath()` matches `file://` URIs, `~/`, `./`/`../`, quoted paths, Windows drive letters, bare absolute paths ([useComposerState.ts#L65](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/useComposerState.ts#L65)); on match it calls `gw.request('image.attach', {path, session_id})` — **path only** ([#L158](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/useComposerState.ts#L158)).
- **Backend returns metadata-only ack.** `ImageAttachResponse` has `name`, `height`, `width`, `token_estimate` ([gatewayTypes.ts#L327](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/gatewayTypes.ts#L327)); the TUI shows a status line `"📎 Attached image: {name} · {w}x{h} · ~{tok} tok"` ([messages.ts#L15](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/domain/messages.ts#L15)). No in-TUI image rendering; bytes stay in backend session state.
- **Submit protocol is text-only.** `prompt.submit {session_id, text}` ([useSubmission.ts#L110](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/useSubmission.ts#L110)); `PromptSubmitResponse` is `{ ok?: boolean }`; `Msg` carries `text` only ([types.ts#L112](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/types.ts#L112)). The backend splices the previously-attached image into context before the LLM call — the TUI has no visibility into this.

### Voice path — STT via backend

- **Three-gate capability model:** backend `voice.status` event ([createGatewayEventHandler.ts#L553](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/createGatewayEventHandler.ts#L553)); TUI `voice.enabled` flag via `/voice on|off` ([useInputHandlers.ts#L224](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/useInputHandlers.ts#L224)); `VoiceToggleResponse {stt_available, audio_available}` ([gatewayTypes.ts#L337](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/gatewayTypes.ts#L337)).
- **Capture is control-signal only.** `Ctrl+B` (configurable `voice.record_key`) toggles `voice.record {action: 'start'|'stop'}` RPC. VAD/recording run in the backend or sidecar; the TUI issues start/stop only.
- **STT is backend-owned.** Backend transcribes (e.g. OpenAI Whisper) and publishes a `voice.transcript {text?, no_speech_limit?}` event ([#L572](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/createGatewayEventHandler.ts#L572)); the handler clears input, defers a `submit()`, and injects the transcript through the **normal text prompt path** — voice and typed text are indistinguishable at the submission layer. Three consecutive silence detections disable voice mode ([#L599](/Users/binle/workspace_genai/hermes-agent/ui-tui/src/app/createGatewayEventHandler.ts#L599)).

---

## opencode — TUI Reads Bytes and Builds Content Blocks Client-Side

The opencode TUI (TypeScript/Solid.js on `@opentui/core`, in `packages/tui/`) does **client-side multimodal intake**: it reads image bytes, base64-encodes them, and constructs `FilePart` content blocks directly in the prompt composer, then submits them as multimodal `parts`. The backend is a local in-process Effect server/SDK (`packages/core/` + `packages/server/`), not a remote gateway.

### Image path

- **Three intake surfaces, all client-side:**
  1. **Clipboard image paste** — the hidden `prompt.paste` command reads the clipboard; `image/*` → `pasteAttachment()`, `text/plain` → `pasteInputText()` ([prompt/index.tsx#L364](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/index.tsx#L364)).
  2. **File-path bracketed-paste** — `onPaste` decodes bytes; empty paste (Windows image-clipboard quirk) redirects to `prompt.paste`, else routes to `pasteInputText()` which tests for a local path ([#L1380](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/index.tsx#L1380)).
  3. **`@`-file mention** — autocomplete builds a `FilePart` with a `file://` URL, mime `text/plain` (server resolves) ([autocomplete.tsx#L238](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/autocomplete.tsx#L238)).
- **Platform-native clipboard image read (real byte intake).** `clipboard.read()` returns `{ data, mime }`: macOS `osascript` PNGf→tempfile→base64 ([clipboard.ts#L31](/Users/binle/workspace_genai/opencode/packages/tui/src/clipboard.ts#L31)); Windows/WSL PowerShell `Clipboard::GetImage()`→base64 ([#L53](/Users/binle/workspace_genai/opencode/packages/tui/src/clipboard.ts#L53)); Linux `wl-paste`/`xclip -t image/png` ([#L62](/Users/binle/workspace_genai/opencode/packages/tui/src/clipboard.ts#L62)); `clipboardy` text fallback ([#L71](/Users/binle/workspace_genai/opencode/packages/tui/src/clipboard.ts#L71)). OSC 52 is used for clipboard *write* only ([#L23](/Users/binle/workspace_genai/opencode/packages/tui/src/clipboard.ts#L23)).
- **Local file read by MIME.** `readLocalAttachment()` sniffs extension: SVG → text part; other `image/*` or PDF → binary part; else ignored ([local-attachment.ts#L36](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/local-attachment.ts#L36)). Supported: PNG, JPEG, GIF, WEBP, AVIF, SVG, PDF ([#L25](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/local-attachment.ts#L25)).
- **Content block built in the TUI.** `pasteAttachment()` constructs `FilePart { type: "file", mime, filename, url: "data:${mime};base64,${content}", source: {...} }` and pushes it into `store.prompt.parts`, shown as a virtual `[Image N]`/`[PDF N]` extmark ([prompt/index.tsx#L1208](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/index.tsx#L1208), block at [#L1231](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/index.tsx#L1231)).
- **Multimodal submit.** `sdk.client.session.prompt({ ..., parts: [...editorParts, {type:"text", text}, ...nonTextParts] })` — `nonTextParts` are the file parts ([#L1087](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/index.tsx#L1087)). Slash commands forward `nonTextParts.filter(x => x.type === "file")` ([#L1083](/Users/binle/workspace_genai/opencode/packages/tui/src/component/prompt/index.tsx#L1083)).
- **Backend lowering.** Incoming file part → `Prompt.FileAttachment {uri, mime, name, source}` ([prompt.ts#L9](/Users/binle/workspace_genai/opencode/packages/core/src/session/prompt.ts#L9)); `media()` emits provider part `{ type: "file", mediaType: mime, data: uri, filename }` ([to-llm-message.ts#L13](/Users/binle/workspace_genai/opencode/packages/core/src/session/runner/to-llm-message.ts#L13)). The AI-SDK provider resolves the `data:`/`file://` URL.
- **Server-side resize (tool path).** `Image.normalize()` (Photon WASM) enforces `auto_resize` default true, max 2000×2000, max 5 MB base64 ([image.ts#L53](/Users/binle/workspace_genai/opencode/packages/core/src/image.ts#L53)); wired into the **Read tool** when the agent reads an image file ([tool/read.ts#L40](/Users/binle/workspace_genai/opencode/packages/core/src/tool/read.ts#L40), normalize at [#L62](/Users/binle/workspace_genai/opencode/packages/core/src/tool/read.ts#L62)). User-pasted data URLs are not routed through this resizer.
- **Rendering = MIME badges.** Transcript file parts render as colored badges (`image/* → "img"`, `pdf`, `txt`) + filename ([routes/session/index.tsx#L1351](/Users/binle/workspace_genai/opencode/packages/tui/src/routes/session/index.tsx#L1351), render at [#L1416](/Users/binle/workspace_genai/opencode/packages/tui/src/routes/session/index.tsx#L1416)). No pixels; no graphics protocol (`useKittyKeyboard` at [app.tsx#L188](/Users/binle/workspace_genai/opencode/packages/tui/src/app.tsx#L188) is the kitty *keyboard* protocol).

### Voice path — none

- **No voice/STT/microphone input** anywhere in the TUI or backend chat path. No record hotkey, no `voice.*` RPC, no transcript injection.
- `audio.ts` is **output-only** — loads and plays notification sounds via `@opentui/core` `Audio` ([audio.ts#L38](/Users/binle/workspace_genai/opencode/packages/tui/src/audio.ts#L38)); its sole consumer is the attention chime ([attention.ts#L160](/Users/binle/workspace_genai/opencode/packages/tui/src/attention.ts#L160)). `stopVoice(voice: AudioVoice)` refers to playback voices/channels ([audio.ts#L45](/Users/binle/workspace_genai/opencode/packages/tui/src/audio.ts#L45)), not speech/STT.

---

## Synthesis

- **Image intake is a three-point spectrum.** openclaw has no TUI intake (multimodal enters only via HTTP/node-event callers, processed gateway-side); hermes forwards file *paths* to its backend via `image.attach` RPC and keeps the TUI a thin control layer; opencode reads image bytes **in the TUI**, base64-encodes them into `FilePart` `data:` URLs, and submits multimodal `parts`. opencode is the only one whose TUI→backend protocol is multimodal rather than text-only.
- **Byte handling tracks backend topology.** The two remote-gateway designs (openclaw, hermes) keep bytes server-side by construction — the thin TUI never holds them. opencode, with a *local in-process* server, can afford to read bytes in the TUI process and still hand off resize/normalization (Photon WASM) to the server for the tool-read path.
- **Voice is rare and, where present, backend-delegated.** Only hermes has TUI voice input, and strictly as STT-over-RPC (control signals up, transcript text down, submitted as normal text). openclaw's voice infra is channel-plugin-only; opencode has none (its audio is notification-sound output). No peer does client-side STT.
- **Nobody renders real pixels.** All three display attachments/tool-result images as text — openclaw a `[image/jpeg 42kb]` placeholder, hermes a `📎 Attached image …` notice, opencode a MIME badge. None uses a terminal graphics protocol (kitty graphics / sixel / iTerm inline).
