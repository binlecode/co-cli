---
inference:
  # Source: https://huggingface.co/Qwen/Qwen3.5-35B-A3B
  # Official thinking-mode decoding guidance: temperature=1.0, top_p=0.95, top_k=20, min_p=0, presence_penalty=1.5.
  # NOTE: temperature 1.0 differs from Qwen3-2507 thinking (0.6) — intentional per Qwen3.5 guidance.
  # num_ctx baked in Modelfile (Ollama OpenAI API ignores request-side num_ctx).
  temperature: 1.0
  top_p: 0.95
  max_tokens: 32768
  extra_body:
    top_k: 20
    min_p: 0
    presence_penalty: 1.5
---
