"""Model-specific behavioral quirks and counter-steering prompts.

This module provides model-specific guidance to compensate for known behavioral
patterns that interfere with co-cli's design goals (Directive vs Inquiry compliance,
tool output handling, fact verification).

## Design

**Goal:** Improve agent behavior without touching agent.py or system.md structure.

**Approach:** Inject targeted counter-steering text at prompt assembly time based on
provider + model_name lookup.

**Quirk Categories:**

1. **Verbose** - Model repeats/summarizes tool output despite "show verbatim" rule
   Counter-steering: Emphasize brevity, trust tool formatting

2. **Overeager** - Model treats observations as directives (false positive modifications)
   Counter-steering: Emphasize scope boundaries, "user said X ≠ user requested Y"

3. **Lazy** - Model leaves TODOs, placeholders, incomplete implementations
   Counter-steering: Emphasize thoroughness, "no TODOs, complete all code"

4. **Hesitant** - Model asks permission for read-only operations (false negative paralysis)
   Counter-steering: Emphasize autonomy for safe operations, confidence in decisions

## Usage

```python
from co_cli.prompts.model_quirks import get_counter_steering

# In prompt assembly (prompts/__init__.py):
counter_steering = get_counter_steering("gemini", "gemini-1.5-pro")
if counter_steering:
    base_prompt += f"\\n\\n## Model-Specific Guidance\\n\\n{counter_steering}"
```

## Adding New Models

To add a new model quirk:

1. Identify the quirk category (verbose, overeager, lazy, hesitant) via behavioral testing
2. Add entry to MODEL_QUIRKS dict with provider:model_name key
3. Set quirk flags (can combine multiple: lazy=True, verbose=True)
4. Write counter-steering text that directly contradicts the unwanted behavior
5. Test with `uv run co chat` and observe if behavior improves

**Example:**

```python
"ollama:codellama": {
    "lazy": True,
    "counter_steering": (
        "You are diligent and thorough! "
        "Always provide complete, fully working code. "
        "NEVER leave TODO, FIXME, or placeholder comments."
    ),
}
```

## Evidence

Quirk identification based on:
- Aider's model warnings (leaderboards.aider.chat)
- Codex documentation (model-specific best practices)
- Community reports (GitHub issues, forum discussions)
- Internal testing (observed behavior in co-cli chat sessions)

Counter-steering prompts follow techniques from:
- Aider's model-specific prompts (github.com/paul-gauthier/aider)
- Codex's model quirk database (github.com/codex-rs/codex)
- Anthropic's prompt engineering guide (docs.anthropic.com)
"""

from typing import TypedDict


class ModelInference(TypedDict, total=False):
    """Model-specific inference parameters.

    Fields:
        temperature: Sampling temperature.
        top_p: Nucleus sampling threshold.
        max_tokens: Maximum output tokens.
        num_ctx: Context window size (overrides settings.ollama_num_ctx).
        extra_body: Additional body params (top_k, repeat_penalty, etc.).
    """

    temperature: float
    top_p: float
    max_tokens: int
    num_ctx: int
    extra_body: dict


DEFAULT_INFERENCE: ModelInference = {
    "temperature": 0.7,
    "top_p": 1.0,
    "max_tokens": 16384,
}


class ModelQuirks(TypedDict, total=False):
    """Type-safe structure for model quirk entries.

    Fields:
        verbose: Model repeats/summarizes tool output
        overeager: Model treats observations as directives
        lazy: Model leaves TODOs or incomplete implementations
        hesitant: Model asks permission for safe operations
        counter_steering: Prompt text to inject (required if any flag is True)
        inference: Model-specific inference parameters.
    """

    verbose: bool
    overeager: bool
    lazy: bool
    hesitant: bool
    counter_steering: str
    inference: ModelInference


# Model quirk database
# Key format: "provider:model_name" (provider lowercased, model_name as returned by API)
MODEL_QUIRKS: dict[str, ModelQuirks] = {
    # Ollama models (current)
    "ollama:llama3.1": {
        "hesitant": True,
        "inference": {
            "temperature": 0.7,
            "top_p": 1.0,
            "max_tokens": 16384,
        },
        "counter_steering": (
            "You are confident and decisive! "
            "For read-only operations (reading files, searching code, running tests), proceed "
            "immediately without asking permission. Only ask for approval when side effects are "
            "involved (modifying files, running shell commands, sending messages). "
            "Trust your judgment — if the operation is safe and reversible, just do it."
        ),
    },
    "ollama:glm-4.7-flash": {
        "overeager": True,
        # Terminal / SWE-Bench Verified profile
        # Source: https://huggingface.co/zai-org/GLM-4.7-Flash
        # IMPORTANT: Ollama's OpenAI-compatible API ignores num_ctx from request
        # params — num_ctx MUST be baked into the Modelfile. Use the -agentic
        # variant (e.g. glm-4.7-flash:q4_k_m-agentic) which has
        # PARAMETER num_ctx 202752. Without it, Ollama defaults to 2048 tokens
        # and GLM loses multi-turn conversation history.
        "inference": {
            "temperature": 0.7,
            "top_p": 1.0,
            "max_tokens": 16384,
            "num_ctx": 202752,
            "extra_body": {"repeat_penalty": 1.0},
        },
        "counter_steering": (
            "CRITICAL: You tend to modify code when user only asks questions. "
            "These are NOT action requests: "
            "'What if we added X?', 'Maybe we should Y', 'This could Z', 'The code looks messy', 'The README could mention X'. "
            "These are observations/questions - respond with explanation or ask 'Would you like me to do that?'. "
            "NEVER modify code unless user uses imperative action verbs: 'Fix X', 'Add Y', 'Update Z', 'Delete A'. "
            "When uncertain, ASK 'Would you like me to [action]?' instead of proceeding.\n\n"
            "CRITICAL: You are in a MULTI-TURN conversation. The messages above this system prompt "
            "ARE your conversation history — previous user messages and your previous responses. "
            "When the user says 'the first one', 'option 2', 'yes', 'that one', or any short reference, "
            "look at YOUR PREVIOUS RESPONSE in the message array to understand what they mean. "
            "Do NOT claim you have no context. Do NOT look for conversation history inside the system prompt."
        ),
    },
    "ollama:qwen3": {
        # Thinking mode profile
        # Source: https://huggingface.co/Qwen/Qwen3-30B-A3B
        "inference": {
            "temperature": 0.6,
            "top_p": 0.95,
            "max_tokens": 32768,
            "num_ctx": 262144,
            "extra_body": {"top_k": 20, "repeat_penalty": 1.0},
        },
    },
}


def get_model_inference(provider: str, model_name: str | None) -> ModelInference:
    """Get inference parameters for a model. Returns defaults for unknown models.

    Args:
        provider: LLM provider name (case-insensitive).
        model_name: Normalized model identifier. If None, returns DEFAULT_INFERENCE.

    Returns:
        ModelInference dict with temperature, top_p, max_tokens, and optional
        num_ctx / extra_body.
    """
    if not model_name:
        return dict(DEFAULT_INFERENCE)

    provider_lower = provider.lower()
    lookup_key = f"{provider_lower}:{model_name}"
    quirks = MODEL_QUIRKS.get(lookup_key)
    if quirks and "inference" in quirks:
        return dict(quirks["inference"])
    return dict(DEFAULT_INFERENCE)


def normalize_model_name(model_name: str) -> str:
    """Normalize model name for quirk lookup by stripping quantization tags.

    Ollama models may include quantization suffixes (e.g., ":q4_k_m", ":q8_0")
    that must be removed before quirk database lookup.

    Examples:
        >>> normalize_model_name("glm-4.7-flash:q4_k_m")
        "glm-4.7-flash"
        >>> normalize_model_name("gemini-1.5-pro")
        "gemini-1.5-pro"
    """
    return model_name.split(":")[0]


def get_counter_steering(provider: str, model_name: str) -> str:
    """Get model-specific counter-steering prompt text.

    Args:
        provider: LLM provider name (case-insensitive: "gemini", "ollama")
        model_name: Model identifier as returned by API (e.g., "gemini-1.5-pro", "deepseek-coder")

    Returns:
        Counter-steering prompt text if model has known quirks, empty string otherwise.

    Examples:
        >>> get_counter_steering("gemini", "gemini-1.5-pro")
        "Be careful not to exceed the scope of the user's request..."

        >>> get_counter_steering("ollama", "deepseek-coder")
        "You are diligent and thorough!..."

        >>> get_counter_steering("unknown", "unknown")
        ""
    """
    provider_lower = provider.lower()
    lookup_key = f"{provider_lower}:{model_name}"

    quirks = MODEL_QUIRKS.get(lookup_key)
    if quirks:
        return quirks.get("counter_steering", "")

    return ""


def list_models_with_quirks() -> list[str]:
    """List all models that have registered quirks.

    Returns:
        List of "provider:model_name" strings for models with known quirks.

    Examples:
        >>> models = list_models_with_quirks()
        >>> "gemini:gemini-1.5-pro" in models
        True
        >>> "ollama:deepseek-coder" in models
        True
        >>> len(models) >= 10
        True
    """
    return list(MODEL_QUIRKS.keys())


def get_quirk_flags(provider: str, model_name: str) -> dict[str, bool]:
    """Get quirk flags for debugging (which quirks are active for this model).

    Args:
        provider: LLM provider name (case-insensitive)
        model_name: Model identifier as returned by API

    Returns:
        Dict with keys: verbose, overeager, lazy, hesitant (all bool values).
        Returns all False if model has no quirks.

    Examples:
        >>> flags = get_quirk_flags("gemini", "gemini-1.5-pro")
        >>> flags["overeager"]
        True
        >>> flags["verbose"]
        False

        >>> flags = get_quirk_flags("unknown", "unknown")
        >>> all(not v for v in flags.values())
        True
    """
    provider_lower = provider.lower()
    lookup_key = f"{provider_lower}:{model_name}"

    quirks = MODEL_QUIRKS.get(lookup_key, {})

    return {
        "verbose": quirks.get("verbose", False),
        "overeager": quirks.get("overeager", False),
        "lazy": quirks.get("lazy", False),
        "hesitant": quirks.get("hesitant", False),
    }
