---
inference:
  # Source: https://ai.google.dev/gemini-api/docs/gemini-3
  # Default temperature is 1.0; Google strongly recommends keeping it at 1.0
  # for thinking models — lower values cause looping and performance issues.
  # top_k is fixed at 64 for all Gemini 2.5/3 series — not user-configurable.
  # top_p: 0.95 is consistent with official GenerationConfig examples.
  temperature: 1.0
  top_p: 0.95
  max_tokens: 65536
---
