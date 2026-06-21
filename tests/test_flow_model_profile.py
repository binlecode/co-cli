"""Model-profile seam — budget resolves per profile; default path is unchanged.

Guards the model-profile-01-seam contract: one ``ModelProfile`` resolved from
``config.llm`` supplies the default context budget, the weak-local default stays a
hard 64k clamp, a user override still wins, and the wired profile-overlay builder is
a no-op for the default backend (byte-identical assembled prompt).
"""

from __future__ import annotations

import pytest

from co_cli.agent.orchestrator import (
    ORCHESTRATOR_SPEC,
    _base_instructions_provider,
    _model_profile_overlay_provider,
)
from co_cli.bootstrap.core import create_deps
from co_cli.config.llm import (
    FRONTIER_MAX_CONTEXT_TOKENS,
    MAX_CONTEXT_TOKENS,
    LlmSettings,
    ModelProfile,
    profile_max_context_tokens,
    resolve_model_profile,
)


def test_weak_local_budget_is_hard_64k_default() -> None:
    """The default (ollama) backend resolves to WEAK_LOCAL and the 64k baseline budget."""
    llm = LlmSettings(provider="ollama")
    assert resolve_model_profile(llm) is ModelProfile.WEAK_LOCAL
    assert llm.max_context_tokens == MAX_CONTEXT_TOKENS == 65_536


def test_frontier_budget_is_half_provider_max_default() -> None:
    """A frontier (gemini) backend resolves to FRONTIER and half the 1M max window."""
    llm = LlmSettings(provider="gemini", api_key="x")
    assert resolve_model_profile(llm) is ModelProfile.FRONTIER
    assert llm.max_context_tokens == FRONTIER_MAX_CONTEXT_TOKENS == 524_288
    assert profile_max_context_tokens(ModelProfile.FRONTIER) == 524_288


def test_user_override_wins_over_profile_default() -> None:
    """An explicit max_context_tokens beats the profile-derived default, either provider."""
    assert (
        LlmSettings(provider="gemini", api_key="x", max_context_tokens=300_000).max_context_tokens
        == 300_000
    )
    assert LlmSettings(provider="ollama", max_context_tokens=32_768).max_context_tokens == 32_768


def test_gemini_pro_is_usable_main_backend() -> None:
    """gemini-3.1-pro-preview validates and yields valid settings for both modes."""
    llm = LlmSettings(provider="gemini", model="gemini-3.1-pro-preview", api_key="x")
    assert llm.validate_config() is None
    reasoning = llm.reasoning_model_settings()
    noreason = llm.noreason_model_settings()
    assert reasoning["max_tokens"] == 65_536
    assert reasoning["google_thinking_config"] == {"thinking_level": "HIGH"}
    assert noreason["max_tokens"] <= 65_536
    assert noreason["google_thinking_config"] == {"thinking_level": "LOW"}


def test_gemini_pro_inherits_frontier_budget_and_skips_ollama_probe() -> None:
    """The pinned pro model inherits the 524k FRONTIER budget; no Ollama probe runs."""
    llm = LlmSettings(provider="gemini", model="gemini-3.1-pro-preview", api_key="x")
    assert llm.max_context_tokens == FRONTIER_MAX_CONTEXT_TOKENS == 524_288
    assert llm.ollama_num_ctx() is None


def test_overlay_provider_immediately_follows_base() -> None:
    """The overlay seam sits directly after the base builder, so overlay is adjacent to base."""
    builders = ORCHESTRATOR_SPEC.static_instruction_builders
    assert (
        builders.index(_model_profile_overlay_provider)
        == builders.index(_base_instructions_provider) + 1
    )


@pytest.mark.asyncio
async def test_assembled_prompt_byte_identical_to_base() -> None:
    """The reordered overlay seam contributes nothing while overlays are empty.

    Assembling the full (reordered) ``ORCHESTRATOR_SPEC`` static prefix equals the
    prefix with the overlay builder removed entirely — the baseline shape from Plan
    1a. Unconditional: the overlay file is absent, so this changes no prompt content
    for the default (weak-local) path regardless of the seam's position.
    """
    deps = await create_deps(on_status=lambda _s: None, stack=None, theme_override=None)
    assert resolve_model_profile(deps.config.llm) is ModelProfile.WEAK_LOCAL
    assert _model_profile_overlay_provider(deps) is None

    def assemble(builders: tuple) -> str:
        return "\n\n".join(piece for b in builders if (piece := b(deps)))

    reordered = ORCHESTRATOR_SPEC.static_instruction_builders
    baseline = tuple(b for b in reordered if b is not _model_profile_overlay_provider)
    assert assemble(reordered) == assemble(baseline)
