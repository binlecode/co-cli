# RESEARCH: TUI Multimodal Input — co-cli vs Peer Survey (hermes-agent, openclaw, opencode)
_Date: 2026-06-11_

A code-backed comparison of how co-cli and three peer terminal agents handle multimodal input (image and voice/audio), and where in the stack the actual bytes are processed. Based on direct source scans of `~/workspace_genai/co-cli/`, `~/workspace_genai/hermes-agent/`, `~/workspace_genai/openclaw/`, and `~/workspace_genai/opencode/`.

The three peers sit on a clean spectrum of **where *user-supplied* multimodal intake lives**: openclaw has none in the TUI, hermes forwards file *paths* to its backend, and opencode reads bytes and builds content blocks **in the TUI process itself**. co-cli sits on a different axis entirely: it has **no user-facing intake surface at all** — images enter only when the *agent* decides to look at a path via the `image_view` tool. Intake is model-initiated, not user-initiated.

## Comparison Matrix

| Dimension | co-cli | openclaw | hermes-agent | opencode |
|---|---|---|---|---|
| **Image intake in TUI** | None (agent-driven tool call) | None | Path-forwarding only | Full client-side byte intake |
| **Intake surfaces** | Agent calls `image_view(path)` — **no user surface** | — | Pasted/dropped file paths | Clipboard image paste · file-path paste · `@`-file mention |
| **Who initiates intake** | **The model** (tool call) | HTTP/node-event caller | The user (paste/drop) | The user (paste/mention) |
| **Who reads image bytes** | Agent process (tool `read_bytes()`) | Gateway only (non-TUI callers) | Backend (TUI sends path) | **TUI process** (base64 in-TUI) |
| **Content block built where** | Tool result (`ToolReturn.content` → `UserPromptPart`) | Gateway | Backend gateway | **TUI** (`FilePart` w/ `data:` URL) |
| **Wire format** | `BinaryContent` via pydantic-ai (single process) | `chat.send {message: string}` (text-only) | `prompt.submit {session_id, text}` (text-only) | `session.prompt {parts: [...]}` (**multimodal**) |
| **Backend topology** | Single local process (REPL + pydantic-ai loop → model API) | Remote gateway | Remote gateway | Local in-process Effect server/SDK |
| **Image resize/normalize** | None in `image_view` (≤20 MB → reject); PDF-render path downscales (150 DPI / ≤2000 px long edge / ≤10 pp) | Gateway (≤2 MB inline, else disk) | Backend | Server-side Photon WASM (tool-read path; ≤2000² / 5 MB) |
| **Vision capability gate** | Honest gate — `image_view` hidden unless agent model is vision-capable (Ollama `/api/show` probe / Gemini native); no fallback vision model | — | — | — |
| **Voice/audio input** | **None** | None in TUI (channel plugins only) | STT-via-backend over RPC | **None** (audio is notification-sound output only) |
| **Terminal image rendering** | Text label (`Image attached…`); `[image elided]` on replay | Text placeholder `[image/jpeg 42kb]` | None (path notice line) | MIME badges (`img`/`pdf`) + filename |
| **Graphics protocol** | None | None (OSC 8 links only) | None | None (`useKittyKeyboard` is keys, not graphics) |

**Peer spectrum (least → most client-side multimodal):** openclaw (no intake) → hermes (path-forwarding RPC) → opencode (client-side base64 content blocks). **co-cli is off this axis:** it has no user intake surface — the *agent* pulls images by calling `image_view` on a path, so the relevant question shifts from "where does the user's image enter" to "when does the model choose to look."

---

## co-cli — No User Intake Surface; the Agent Pulls Images via a Tool

co-cli is a single-process Rich REPL (`co chat`) whose input layer is **text-only**. There is no clipboard paste, no drag-drop path detection, and no `@`-file mention — image intake is **purely tool-driven**. The agent (not the user) decides to look at an image by calling `image_view(path)`. This makes co-cli categorically different from the three peers, whose intake is user-initiated (paste/drop/mention) or external-caller-initiated (HTTP/node-event).

### Input layer is text-only

- The REPL event loop feeds a text-only input queue ([main.py#L499](/Users/binle/workspace_genai/co-cli/co_cli/main.py#L499)) — no paste handler, no clipboard inspection, no drag-drop path heuristic, no `@`-mention. The user types text; the agent does the rest.

### Image path — agent-initiated tool call

- **Tool, not surface.** `image_view(path, prompt)` reads the file in the agent process: `data = resolved.read_bytes()` ([view.py#L103](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L103)). Supported MIME: PNG, JPEG, WEBP, GIF ([#L34](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L34)); PDF is explicitly out of scope and routed to the `documents` skill ([#L31](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L31)).
- **Content block built in the tool result.** It returns `ToolReturn(return_value="Image attached …", content=[prompt, BinaryContent(data=data, media_type=...)])` ([#L108](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L108)). pydantic-ai materializes `ToolReturn.content` as a *separate* `UserPromptPart` (not inside `ToolReturnPart.return_value`), so pixel data reaches the model on the next turn while the tool's textual return stays text. No base64 plumbing in co's code — pydantic-ai encodes `BinaryContent` for the provider.
- **Size rejection, not downscaling.** Images over ~20 MB return a `tool_error` rather than being auto-resized ([#L96](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L96)) — rationale: the agent chooses the path deliberately (a bounded read), not an unbounded user upload.
- **DEFERRED visibility.** `image_view` is hidden by default and unlocked only when the agent calls `tool_view("image_view")` ([tool_view.py#L91](/Users/binle/workspace_genai/co-cli/co_cli/tools/system/tool_view.py#L91)), keeping its schema out of the turn prompt until needed.

### Vision capability — honest gate, no fallback model

- Capability is resolved once at bootstrap: Gemini is treated as natively multimodal; Ollama is probed via a single `/api/show` call and `vision` is read from its `capabilities` array ([check.py#L113](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/check.py#L113)). A probe failure degrades to `vision=False` ([#L99](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/check.py#L99)) — a blind model is never assumed sighted.
- The result lands on `deps.agent_vision_capable` ([deps.py#L302](/Users/binle/workspace_genai/co-cli/co_cli/deps.py#L302), set in [bootstrap/core.py#L261](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/core.py#L261)) and gates the tool via `check_fn=_vision_available` ([view.py#L43](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L43)): `image_view` is invisible when the agent model cannot see. Vision is the agent model's own capability or nothing — no pinned secondary vision model, no describe-fallback.

### PDF / scanned-document path — render-then-`image_view`

- Text-first: the `documents` skill extracts a text layer with `pymupdf4llm` ([extract_pdf.py#L126](/Users/binle/workspace_genai/co-cli/co_cli/skills/documents/scripts/extract_pdf.py#L126)); a sparse text layer emits the `[no-text-layer: likely scanned]` sentinel ([#L251](/Users/binle/workspace_genai/co-cli/co_cli/skills/documents/scripts/extract_pdf.py#L251)).
- For scanned PDFs, `co-extract-pdf --render` rasterizes pages to PNG — `RENDER_DPI=150`, `RENDER_MAX_LONG_EDGE_PX=2000` (the model's ~4 MP ceiling), `RENDER_DEFAULT_MAX_PAGES=10` ([extract_pdf.py#L30](/Users/binle/workspace_genai/co-cli/co_cli/skills/documents/scripts/extract_pdf.py#L30)) — and each PNG is fed back through `image_view`. The downscale/normalize logic lives in the PDF handler, not in the image tool. This is the only place co downscales images.
- The `office` skill (`.docx`/`.pptx`/`.xlsx`) is text-extraction only — embedded images/charts are out of scope ([office/SKILL.md](/Users/binle/workspace_genai/co-cli/co_cli/skills/office/SKILL.md)).

### Voice path — none

- No audio, voice, STT, microphone, or speech anywhere in `co_cli/`. (No notification-sound output either, unlike opencode.)

### Rendering — text only

- The display layer renders only string tool results in a Rich panel ([display/core.py#L467](/Users/binle/workspace_genai/co-cli/co_cli/display/core.py#L467)); `BinaryContent` is never drawn to the terminal — it goes to the model, not the screen. On history replay, old image parts are elided to a `[image elided]` placeholder to prevent base64 bloat ([history_processors.py#L570](/Users/binle/workspace_genai/co-cli/co_cli/context/history_processors.py#L570)). No graphics protocol (kitty graphics / sixel / iTerm inline).

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

- **co-cli is the only model-initiated design.** The three peers all answer the question "where does the *user's* image enter the system" — openclaw (HTTP/node-event callers, gateway-side), hermes (file-path RPC to backend), opencode (bytes read in the TUI). co-cli has no user intake surface at all: the *agent* pulls an image by calling `image_view(path)`, and bytes enter as a tool-result `UserPromptPart`. This shifts the design question from "where does intake live" to "when does the model decide to look," and explains why co has no paste/drop/mention plumbing — it would have nothing to attach the image to.
- **Image intake among the peers is a three-point spectrum.** openclaw has no TUI intake (multimodal enters only via HTTP/node-event callers, processed gateway-side); hermes forwards file *paths* to its backend via `image.attach` RPC and keeps the TUI a thin control layer; opencode reads image bytes **in the TUI**, base64-encodes them into `FilePart` `data:` URLs, and submits multimodal `parts`. opencode is the only peer whose TUI→backend protocol is multimodal rather than text-only.
- **Byte handling tracks backend topology.** The two remote-gateway designs (openclaw, hermes) keep bytes server-side by construction — the thin TUI never holds them. opencode, with a *local in-process* server, reads bytes in the TUI process and hands off resize to the server. co-cli collapses the split entirely: REPL and pydantic-ai agent loop are one process, so the tool reads bytes and hands `BinaryContent` straight to the provider — no wire protocol to design, and no resize except in the PDF-render path.
- **Capability honesty is co-specific.** Only co gates the feature on the *agent model's own* vision capability — `image_view` is hidden when the model can't see, probed once at bootstrap, with no fallback vision model. The peers don't expose an equivalent gate because their intake is user-driven regardless of model capability.
- **Voice is rare and, where present, backend-delegated.** Only hermes has TUI voice input, and strictly as STT-over-RPC (control signals up, transcript text down, submitted as normal text). openclaw's voice infra is channel-plugin-only; opencode has none (its audio is notification-sound output); co-cli has none at all. No peer does client-side STT.
- **Nobody renders real pixels.** All four display attachments/tool-result images as text — co a `Image attached …` label (`[image elided]` on replay), openclaw a `[image/jpeg 42kb]` placeholder, hermes a `📎 Attached image …` notice, opencode a MIME badge. None uses a terminal graphics protocol (kitty graphics / sixel / iTerm inline).
