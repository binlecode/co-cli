"""Eval session setup: CoDeps factory and model detection."""

from typing import Any

from co_cli.config.core import get_settings, settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend


def detect_model_tag() -> str:
    """Auto-detect a model tag from the current LLM config."""
    provider = settings.llm.provider.lower()
    model = settings.llm.model
    if provider == "gemini":
        return f"gemini-{model}" if model else "gemini"
    if provider == "ollama":
        return f"ollama-{model}" if model else "ollama"
    return provider


def make_eval_deps(**overrides: Any) -> CoDeps:
    """Build a CoDeps suitable for evals, pulling defaults from settings.

    Pass keyword overrides to customise any CoDeps field, e.g.
    ``make_eval_deps(brave_search_api_key=None)``.
    Service fields (shell, memory_store, model) can
    also be passed as overrides and are extracted before building CoDeps.

    The ``model`` default is an ``LlmModel`` built via ``build_model(settings.llm)`` —
    real sessions get this via bootstrap, and CoDeps.model is typed as ``LlmModel | None``
    (with ``.context_window``, ``.settings``, ``.model``). Defaulting to None here would let
    downstream paths like compaction.py (which reads ``deps.model.context_window``) trip
    AttributeErrors only inside the eval. Callers can still pass ``model=None`` explicitly
    when truly model-free behavior is desired.
    """
    s = get_settings()

    # Extract non-config fields before building CoDeps. Sentinel for `model` so we
    # can distinguish "unset → build the default" from "explicitly None".
    _UNSET = object()
    shell = overrides.pop("shell", ShellBackend())
    memory_store = overrides.pop("memory_store", None)
    model = overrides.pop("model", _UNSET)
    knowledge_dir = overrides.pop("knowledge_dir", None)
    # Discard legacy overrides that no longer map to current fields
    overrides.pop("session_id", None)
    overrides.pop("mcp_servers", None)

    if model is _UNSET:
        model = build_model(s.llm)

    deps = CoDeps(
        shell=shell,
        memory_store=memory_store,
        model=model,
        config=s,
        session=CoSessionState(),
    )
    if knowledge_dir is not None:
        deps.knowledge_dir = knowledge_dir
    return deps
