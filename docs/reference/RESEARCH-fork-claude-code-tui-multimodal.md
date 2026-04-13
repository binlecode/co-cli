# RESEARCH: fork-claude-code TUI Multimodal Input
_Date: 2026-04-10_

This note is based on a direct source scan of `~/workspace_genai/fork-claude-code/`.
It records only code-backed facts about TUI support for image input, voice/audio input, and the backend processing path.

## Scope

Files checked for this note:

- [PromptInput.tsx](/Users/binle/workspace_genai/fork-claude-code/components/PromptInput/PromptInput.tsx#L1151)
- [TextInput.tsx](/Users/binle/workspace_genai/fork-claude-code/components/TextInput.tsx#L1)
- [BaseTextInput.tsx](/Users/binle/workspace_genai/fork-claude-code/components/BaseTextInput.tsx#L1)
- [usePasteHandler.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/usePasteHandler.ts#L1)
- [textInputTypes.ts](/Users/binle/workspace_genai/fork-claude-code/types/textInputTypes.ts#L120)
- [config.ts](/Users/binle/workspace_genai/fork-claude-code/utils/config.ts#L53)
- [imagePaste.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imagePaste.ts#L124)
- [imageStore.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imageStore.ts#L1)
- [imageResizer.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imageResizer.ts#L169)
- [processUserInput.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processUserInput.ts#L300)
- [processTextPrompt.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processTextPrompt.ts#L19)
- [apiLimits.ts](/Users/binle/workspace_genai/fork-claude-code/constants/apiLimits.ts#L17)
- [claude.ts](/Users/binle/workspace_genai/fork-claude-code/services/api/claude.ts#L952)
- [UserImageMessage.tsx](/Users/binle/workspace_genai/fork-claude-code/components/messages/UserImageMessage.tsx#L14)
- [AttachmentMessage.tsx](/Users/binle/workspace_genai/fork-claude-code/components/messages/AttachmentMessage.tsx#L232)
- [useVoiceIntegration.tsx](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoiceIntegration.tsx#L281)
- [useVoice.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoice.ts#L632)
- [voice.ts](/Users/binle/workspace_genai/fork-claude-code/services/voice.ts#L1)
- [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L1)
- [voiceModeEnabled.ts](/Users/binle/workspace_genai/fork-claude-code/voice/voiceModeEnabled.ts#L16)
- [mcp client.ts](/Users/binle/workspace_genai/fork-claude-code/services/mcp/client.ts#L2478)
- [mcpOutputStorage.ts](/Users/binle/workspace_genai/fork-claude-code/utils/mcpOutputStorage.ts#L65)

## Core Findings

- The TUI has first-class image intake. The text-input props expose `onImagePaste`, and the persisted pasted-content type is only `'text' | 'image'`. See [textInputTypes.ts](/Users/binle/workspace_genai/fork-claude-code/types/textInputTypes.ts#L120) and [config.ts](/Users/binle/workspace_genai/fork-claude-code/utils/config.ts#L53).
- I did not find a parallel TUI `onAudioPaste` hook or an audio variant in `PastedContent` in the scanned TUI input path. In the TUI input types, image is the only non-text pasted content type. See [textInputTypes.ts](/Users/binle/workspace_genai/fork-claude-code/types/textInputTypes.ts#L120) and [config.ts](/Users/binle/workspace_genai/fork-claude-code/utils/config.ts#L53).
- Image intake reaches the model as Anthropic image content blocks. The prompt path builds `ImageBlockParam` values, resizes them, and appends them to the user message content. See [processUserInput.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processUserInput.ts#L351) and [processTextPrompt.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processTextPrompt.ts#L66).
- Voice/audio input in the TUI is implemented as speech-to-text, not as raw audio blocks added to the user prompt. The voice integration writes transcript text back into the input buffer. See [useVoiceIntegration.tsx](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoiceIntegration.tsx#L281).
- The voice backend is separate from the main messages API path. It records PCM audio locally, streams binary audio over a WebSocket to `voice_stream`, receives transcript events, and then injects text into the input field. See [voice.ts](/Users/binle/workspace_genai/fork-claude-code/services/voice.ts#L335), [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L5), and [useVoice.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoice.ts#L683).
- Audio content does exist elsewhere in the codebase, but in the scanned paths it is MCP result handling, not TUI user input. MCP `audio` content is persisted to disk and converted to a text block reference, while MCP `image` content is converted to an actual image block. See [mcp client.ts](/Users/binle/workspace_genai/fork-claude-code/services/mcp/client.ts#L2490) and [mcpOutputStorage.ts](/Users/binle/workspace_genai/fork-claude-code/utils/mcpOutputStorage.ts#L65).

## Image Path: TUI to Model

### 1. Intake surfaces

- `PromptInput` defines `onImagePaste(...)`, creates a `PastedContent` object with `type: 'image'`, caches a file path, stores the image asynchronously, and inserts an `[Image #N]` placeholder into the prompt text. See [PromptInput.tsx](/Users/binle/workspace_genai/fork-claude-code/components/PromptInput/PromptInput.tsx#L1151).
- The `chat:imagePaste` handler explicitly reads the clipboard and forwards the result into `onImagePaste(...)`. See [PromptInput.tsx](/Users/binle/workspace_genai/fork-claude-code/components/PromptInput/PromptInput.tsx#L1619).
- `BaseTextInput` routes terminal paste handling through `usePasteHandler(...)`, passing `onImagePaste` through the stack. See [BaseTextInput.tsx](/Users/binle/workspace_genai/fork-claude-code/components/BaseTextInput.tsx#L49).

### 2. Paste and drag detection

- `usePasteHandler` accumulates bracketed-paste chunks, detects image file paths, and calls `tryReadImageFromPath(...)` for dragged or pasted filesystem paths. See [usePasteHandler.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/usePasteHandler.ts#L94).
- The same hook also handles empty bracketed paste on macOS by checking the clipboard for an image and then calling `onImagePaste(...)`. See [usePasteHandler.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/usePasteHandler.ts#L179) and [usePasteHandler.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/usePasteHandler.ts#L214).
- `getImageFromClipboard()` supports a native macOS clipboard path via `image-processor-napi`, and otherwise shells out to platform clipboard commands, then resizes the image before returning base64 data. See [imagePaste.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imagePaste.ts#L124).
- `tryReadImageFromPath()` reads image files from absolute paths, or from a clipboard-derived path when only a filename is available, then resizes and returns base64 data plus media type and dimensions. See [imagePaste.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imagePaste.ts#L351).

### 3. Storage and TUI rendering

- The image cache uses `~/.claude/image-cache/<sessionId>/`. `storeImage()` writes the base64 payload to disk and `getStoredImagePath()` returns the cached path. See [imageStore.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imageStore.ts#L18), [imageStore.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imageStore.ts#L54), and [imageStore.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imageStore.ts#L104).
- `UserImageMessage` renders `[Image #N]` and turns it into a clickable file hyperlink when the stored path exists and terminal hyperlinks are supported. See [UserImageMessage.tsx](/Users/binle/workspace_genai/fork-claude-code/components/messages/UserImageMessage.tsx#L14).
- Queued commands also preserve `imagePasteIds`, and `AttachmentMessage` renders the queued text plus `UserImageMessage` entries. See [attachments.ts](/Users/binle/workspace_genai/fork-claude-code/utils/attachments.ts#L1072) and [AttachmentMessage.tsx](/Users/binle/workspace_genai/fork-claude-code/components/messages/AttachmentMessage.tsx#L232).

### 4. Resize and normalization before API send

- `maybeResizeAndDownsampleImageBuffer(...)` enforces image size and dimension constraints. It checks the raw buffer, metadata, compression paths, and resize paths. See [imageResizer.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imageResizer.ts#L169).
- `maybeResizeAndDownsampleImageBlock(...)` converts a base64 image block back into a buffer, resizes it if needed, and returns a normalized base64 image block. See [imageResizer.ts](/Users/binle/workspace_genai/fork-claude-code/utils/imageResizer.ts#L445).
- The image constants are `API_IMAGE_MAX_BASE64_SIZE = 5 MB`, `IMAGE_TARGET_RAW_SIZE = 3.75 MB`, and client-side dimension caps of `2000x2000`. See [apiLimits.ts](/Users/binle/workspace_genai/fork-claude-code/constants/apiLimits.ts#L17).

### 5. User-message assembly

- `processUserInputBase(...)` extracts images from `pastedContents`, stores them on disk, converts them into `ImageBlockParam` values, resizes them, and collects metadata text. See [processUserInput.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processUserInput.ts#L351).
- `processTextPrompt(...)` builds the final user message by concatenating text blocks followed by image blocks, and records `imagePasteIds` on the `UserMessage`. See [processTextPrompt.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processTextPrompt.ts#L66).
- The API layer treats only `image` and `document` blocks as media for the per-request cap, and strips oldest media beyond `API_MAX_MEDIA_PER_REQUEST`. See [claude.ts](/Users/binle/workspace_genai/fork-claude-code/services/api/claude.ts#L952) and [claude.ts](/Users/binle/workspace_genai/fork-claude-code/services/api/claude.ts#L1308).
- The main LLM request path sends the normalized prompt through `anthropic.beta.messages.create(...)`, using streaming for the normal path and a separate non-streaming fallback path. See [claude.ts](/Users/binle/workspace_genai/fork-claude-code/services/api/claude.ts#L864) and [claude.ts](/Users/binle/workspace_genai/fork-claude-code/services/api/claude.ts#L1822).

## Voice/Audio Path: TUI to Transcript

### 1. Capability gating

- Voice mode is guarded by a GrowthBook kill switch and by Anthropic OAuth availability. The code explicitly states that `voice_stream` is not available with API keys, Bedrock, Vertex, or Foundry. See [voiceModeEnabled.ts](/Users/binle/workspace_genai/fork-claude-code/voice/voiceModeEnabled.ts#L16).

### 2. Local audio capture

- `services/voice.ts` implements microphone capture using `audio-capture-napi` first, with Linux fallbacks to `arecord` or `rec`/SoX. See [voice.ts](/Users/binle/workspace_genai/fork-claude-code/services/voice.ts#L1), [voice.ts](/Users/binle/workspace_genai/fork-claude-code/services/voice.ts#L190), and [voice.ts](/Users/binle/workspace_genai/fork-claude-code/services/voice.ts#L335).
- The recording format is `16 kHz`, `mono`, and the STT connection later advertises `encoding=linear16`, `sample_rate=16000`, `channels=1`. See [voice.ts](/Users/binle/workspace_genai/fork-claude-code/services/voice.ts#L40) and [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L144).

### 3. STT transport and backend

- `voiceStreamSTT.ts` states that the client connects to Anthropic's `/api/ws/speech_to_text/voice_stream` endpoint using Claude Code OAuth credentials. See [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L5) and [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L36).
- The code states that this endpoint uses `conversation_engine` backed models for speech-to-text. See [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L5).
- The WebSocket protocol is JSON control messages plus binary audio frames, with transcript responses arriving as `TranscriptText` and `TranscriptEndpoint`. See [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L10) and [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L74).
- When the `tengu_cobalt_frost` flag is on, the client adds `use_conversation_engine=true` and `stt_provider=deepgram-nova3`. See [voiceStreamSTT.ts](/Users/binle/workspace_genai/fork-claude-code/services/voiceStreamSTT.ts#L153).

### 4. Hook-level processing

- `useVoice.startRecordingSession()` starts local recording immediately, buffers audio until the WebSocket is ready, and then flushes buffered audio to the STT connection. See [useVoice.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoice.ts#L683) and [useVoice.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoice.ts#L917).
- Final transcripts are accumulated or flushed depending on mode, and interim transcripts are maintained in voice state. See [useVoice.ts](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoice.ts#L783).
- `useVoiceIntegration.handleVoiceTranscript(...)` writes the transcript back into the prompt input buffer and places the cursor after the inserted text. See [useVoiceIntegration.tsx](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoiceIntegration.tsx#L281).

## Explicit Non-Findings

- In the scanned TUI prompt-input path, I did not find a user audio attachment pipeline analogous to the image pipeline.
- I did not find an audio variant in `PastedContent`; the type is only `'text' | 'image'`. See [config.ts](/Users/binle/workspace_genai/fork-claude-code/utils/config.ts#L53).
- I did not find a TUI `onAudioPaste` callback in the input prop surface; the only media-specific paste callback there is `onImagePaste`. See [textInputTypes.ts](/Users/binle/workspace_genai/fork-claude-code/types/textInputTypes.ts#L120).
- In the scanned prompt assembly path, audio is not converted into user `ContentBlockParam` audio blocks. The voice path terminates in inserted transcript text, while image data becomes `type: 'image'` blocks. See [useVoiceIntegration.tsx](/Users/binle/workspace_genai/fork-claude-code/hooks/useVoiceIntegration.tsx#L281), [processUserInput.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processUserInput.ts#L368), and [processTextPrompt.ts](/Users/binle/workspace_genai/fork-claude-code/utils/processUserInput/processTextPrompt.ts#L66).
- The only scanned `audio` content-block conversion I found was in MCP result transformation, and that path persists audio blobs to disk as a text-block reference instead of creating an outbound user audio prompt block. See [mcp client.ts](/Users/binle/workspace_genai/fork-claude-code/services/mcp/client.ts#L2490).

## Bottom Line

- `fork-claude-code` TUI has concrete image-input support end to end: intake, local storage, rendering, resize/normalization, and model submission as Anthropic image blocks.
- `fork-claude-code` TUI also has concrete audio support, but as a voice transcription subsystem: local microphone capture plus `voice_stream` speech-to-text, with the result injected back into the text prompt.
- In the scanned TUI codepath, images are multimodal prompt input. Audio is not.
