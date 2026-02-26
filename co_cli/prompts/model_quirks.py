"""Model-specific behavioral quirks and counter-steering prompts.

This module provides model-specific guidance to compensate for known behavioral
patterns that interfere with co-cli's design goals (Directive vs Inquiry compliance,
tool output handling, fact verification).

## Design

**Goal:** Improve agent behavior without touching agent.py or system.md structure.

**Approach:** Quirk data lives in markdown files under ``prompts/quirks/{provider}/``.
Each file uses YAML frontmatter for structured data (flags, inference params) and
the markdown body for counter-steering prose. This module loads and caches the
files, exposing the same public API as before.

**Quirk Categories:**

1. **Verbose** - Model repeats/summarizes tool output despite "show verbatim" rule
2. **Overeager** - Model treats observations as directives (false positive modifications)
3. **Lazy** - Model leaves TODOs, placeholders, incomplete implementations
4. **Hesitant** - Model asks permission for read-only operations (false negative paralysis)

## Adding New Models

Create a file at ``co_cli/prompts/quirks/{provider}/{model_name}.md``:

```markdown
---
flags: [lazy, verbose]
inference:
  temperature: 0.7
  top_p: 1.0
  max_tokens: 16384
---
Counter-steering prose goes here in the markdown body.
```

The ``flags`` list uses zero or more of: verbose, overeager, lazy, hesitant.
The ``inference`` block is optional (defaults apply if absent).
The body is optional (models needing only inference tuning leave it empty).
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

from co_cli._frontmatter import parse_frontmatter

_QUIRKS_DIR = Path(__file__).parent / "quirks"

_FLAG_NAMES = ("verbose", "overeager", "lazy", "hesitant")


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
    """Type-safe structure for a parsed quirk entry.

    Fields:
        verbose: Model repeats/summarizes tool output
        overeager: Model treats observations as directives
        lazy: Model leaves TODOs or incomplete implementations
        hesitant: Model asks permission for safe operations
        counter_steering: Prompt text to inject (the markdown body)
        inference: Model-specific inference parameters (from frontmatter)
    """

    verbose: bool
    overeager: bool
    lazy: bool
    hesitant: bool
    counter_steering: str
    inference: ModelInference


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _load_quirk(provider: str, model_name: str) -> ModelQuirks | None:
    """Load and parse a single quirk file. Returns None if no file exists."""
    quirk_path = _QUIRKS_DIR / provider / f"{model_name}.md"
    if not quirk_path.is_file():
        return None

    raw = quirk_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw)

    quirks: dict[str, Any] = {}

    # Parse flags
    flags = fm.get("flags", [])
    if isinstance(flags, list):
        for flag in flags:
            if flag in _FLAG_NAMES:
                quirks[flag] = True

    # Parse inference
    inference = fm.get("inference")
    if isinstance(inference, dict):
        quirks["inference"] = inference

    # Body = counter-steering prose
    body_text = body.strip()
    if body_text:
        quirks["counter_steering"] = body_text

    return quirks  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public API (unchanged signatures)
# ---------------------------------------------------------------------------


def normalize_model_name(model_name: str) -> str:
    """Normalize model name for quirk lookup by stripping quantization tags.

    Ollama models may include quantization suffixes (e.g., ":q4_k_m", ":q8_0")
    that must be removed before quirk database lookup.

    Examples:
        >>> normalize_model_name("qwen3-coder-next:q4_k_m-agentic")
        "qwen3-coder-next"
        >>> normalize_model_name("gemini-1.5-pro")
        "gemini-1.5-pro"
    """
    return model_name.split(":")[0]


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

    quirks = _load_quirk(provider.lower(), model_name)
    if quirks and "inference" in quirks:
        return dict(quirks["inference"])
    return dict(DEFAULT_INFERENCE)


def get_counter_steering(provider: str, model_name: str) -> str:
    """Get model-specific counter-steering prompt text.

    Args:
        provider: LLM provider name (case-insensitive: "gemini", "ollama")
        model_name: Model identifier (normalized, e.g., "qwen3-coder-next")

    Returns:
        Counter-steering prompt text if model has known quirks, empty string otherwise.
    """
    quirks = _load_quirk(provider.lower(), model_name)
    if quirks:
        return quirks.get("counter_steering", "")
    return ""


def list_models_with_quirks() -> list[str]:
    """List all models that have registered quirks.

    Returns:
        List of "provider:model_name" strings for models with quirk files.
    """
    results: list[str] = []
    if not _QUIRKS_DIR.is_dir():
        return results
    for provider_dir in sorted(_QUIRKS_DIR.iterdir()):
        if not provider_dir.is_dir():
            continue
        provider = provider_dir.name
        for quirk_file in sorted(provider_dir.glob("*.md")):
            model = quirk_file.stem
            results.append(f"{provider}:{model}")
    return results


def get_quirk_flags(provider: str, model_name: str) -> dict[str, bool]:
    """Get quirk flags for debugging (which quirks are active for this model).

    Args:
        provider: LLM provider name (case-insensitive)
        model_name: Model identifier as returned by API

    Returns:
        Dict with keys: verbose, overeager, lazy, hesitant (all bool values).
        Returns all False if model has no quirks.
    """
    quirks = _load_quirk(provider.lower(), model_name) or {}
    return {flag: quirks.get(flag, False) for flag in _FLAG_NAMES}
