"""Model-profile seam — budget resolves per profile; default path is unchanged.

Guards the model-profile-01-seam contract: one ``ModelProfile`` resolved from
``config.llm`` supplies the default context budget, the weak-local default stays a
hard 64k clamp, a user override still wins, and the wired profile-overlay builder is
a no-op for the default backend (byte-identical assembled prompt).
"""

from __future__ import annotations

import pytest

from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC, _model_profile_overlay_provider
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


@pytest.mark.asyncio
async def test_default_assembled_prompt_byte_identical() -> None:
    """The wired profile-overlay builder contributes nothing on the default backend.

    Assembling the static prefix with vs without the overlay builder yields the same
    string, so this plan changes no prompt content for the default (weak-local) path.
    """
    deps = await create_deps(on_status=lambda _s: None, stack=None, theme_override=None)
    assert resolve_model_profile(deps.config.llm) is ModelProfile.WEAK_LOCAL
    assert _model_profile_overlay_provider(deps) is None

    def assemble(builders: tuple) -> str:
        return "\n\n".join(piece for b in builders if (piece := b(deps)))

    with_overlay = ORCHESTRATOR_SPEC.static_instruction_builders
    without_overlay = tuple(b for b in with_overlay if b is not _model_profile_overlay_provider)
    assert assemble(with_overlay) == assemble(without_overlay)
