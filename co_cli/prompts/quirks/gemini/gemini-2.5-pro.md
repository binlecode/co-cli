---
inference:
  # Source: https://ai.google.dev/gemini-api/docs/models
  # Default temperature is 1.0; setting below 1.0 causes looping / degraded
  # performance in thinking models (Google's explicit guidance).
  # top_k is fixed at 64 for all Gemini 2.5 series — not user-configurable.
  # top_p: 0.95 is consistent with official GenerationConfig examples.
  temperature: 1.0
  top_p: 0.95
  max_tokens: 65536
---
