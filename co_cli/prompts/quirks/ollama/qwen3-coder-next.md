---
flags: [lazy, verbose]
inference:
  # Source: https://huggingface.co/Qwen/Qwen3-Coder-Next
  # Recommended decoding profile: temperature=1.0, top_p=0.95, top_k=40.
  # Agentic context must be baked via Modelfile because Ollama OpenAI API ignores num_ctx.
  temperature: 1.0
  top_p: 0.95
  max_tokens: 65536
  num_ctx: 262144
  extra_body:
    top_k: 40
    repeat_penalty: 1.0
---
CRITICAL: Tool call output must be strictly valid. If tools are available and you need one, emit exactly one <tool_call> block per call with valid JSON for arguments. No markdown code fences. No trailing commas. No pseudo-JSON.

CRITICAL: Do not fabricate tool calls. If no tool is needed, answer directly in plain text.

CRITICAL: Do not emit <think> tags or long hidden-style reasoning text. Keep output concise and action-oriented.

CRITICAL: For coding tasks, do not leave TODOs, placeholders, or partial patches. Return complete edits that can run.
