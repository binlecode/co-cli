"""Per-task ModelSettings for non-reasoning LLM calls.

NOREASON_SETTINGS is a complete, self-contained ModelSettings used for all
non-reasoning calls (delegation agents, compaction, memory extraction). Includes
all extra_body keys needed by Ollama's OpenAI API. Provider-specific keys
are silently ignored by Gemini's GoogleProvider.

The main agent uses quirks-derived base settings from build_model(),
stored as deps.model.settings — not a static constant.
"""

from pydantic_ai.settings import ModelSettings

NOREASON_SETTINGS = ModelSettings(
    temperature=0.7,
    top_p=0.8,
    max_tokens=16384,
    extra_body={
        "reasoning_effort": "none",
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
        "num_ctx": 131072,
        "num_predict": 16384,
    },
)
