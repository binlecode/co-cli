"""Shared helpers for the eval suite.

Extracts duplicated patterns (model detection, deps construction, frontend
stubs, tool-call extraction) so individual evals stay focused on scoring logic.
"""

from typing import Any

from pydantic_ai.messages import ToolCallPart
from pydantic_ai.settings import ModelSettings

from co_cli._orchestrate import FrontendProtocol
from co_cli.config import settings, get_settings
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend


# ---------------------------------------------------------------------------
# Model tag detection
# ---------------------------------------------------------------------------


def detect_model_tag() -> str:
    """Auto-detect a model tag from the current LLM config."""
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        return f"gemini-{settings.gemini_model}"
    if provider == "ollama":
        return f"ollama-{settings.ollama_model}"
    return provider


# ---------------------------------------------------------------------------
# CoDeps factory
# ---------------------------------------------------------------------------


def make_eval_deps(**overrides: Any) -> CoDeps:
    """Build a CoDeps suitable for evals, pulling defaults from settings.

    Pass keyword overrides to customise any field, e.g.
    ``make_eval_deps(session_id="my-eval", brave_search_api_key=None)``.
    """
    s = get_settings()
    defaults: dict[str, Any] = {
        "shell": ShellBackend(),
        "session_id": "eval",
        "obsidian_vault_path": None,
        "google_credentials_path": None,
        "shell_safe_commands": [],
        "brave_search_api_key": s.brave_search_api_key,
        "web_policy": s.web_policy,
        "web_http_max_retries": s.web_http_max_retries,
        "web_http_backoff_base_seconds": s.web_http_backoff_base_seconds,
        "web_http_backoff_max_seconds": s.web_http_backoff_max_seconds,
        "web_http_jitter_ratio": s.web_http_jitter_ratio,
        "doom_loop_threshold": s.doom_loop_threshold,
        "max_reflections": s.max_reflections,
        "memory_max_count": s.memory_max_count,
        "memory_dedup_window_days": s.memory_dedup_window_days,
        "memory_dedup_threshold": s.memory_dedup_threshold,
        "memory_decay_strategy": s.memory_decay_strategy,
        "memory_decay_percentage": s.memory_decay_percentage,
        "max_history_messages": s.max_history_messages,
        "tool_output_trim_chars": s.tool_output_trim_chars,
        "summarization_model": s.summarization_model,
    }
    defaults.update(overrides)
    return CoDeps(**defaults)


# ---------------------------------------------------------------------------
# Model settings
# ---------------------------------------------------------------------------


def make_eval_settings(
    model_settings: ModelSettings | None = None,
    *,
    max_tokens: int | None = None,
) -> ModelSettings:
    """Build eval settings from real model configuration.

    All values are passed through as-is from the quirks database so evals run
    against the same parameters as live sessions. Both providers now supply
    model_settings via get_agent():
      - Ollama: temperature from quirks (e.g. 0.6 for qwen3). Never override
        to 0 — thinking models produce degenerate loops at temperature=0.
      - Gemini: temperature from quirks (1.0 for 2.5/3 series). Google's
        guidance: setting below 1.0 causes looping in thinking models.

    Falls back to temperature=0 only when no model settings exist at all
    (e.g. unit tests / unknown providers).

    Args:
        model_settings: Settings from get_agent(), or None for fallback.
        max_tokens: Optional cap on max_tokens. Surface-level evals (personality
            adherence, heuristic checks) pass a small cap (e.g. 2048) to limit
            thinking chain length on local models. Omit to use the quirks default.
    """
    if model_settings is None:
        base: dict[str, Any] = {"temperature": 0}
        if max_tokens is not None:
            base["max_tokens"] = max_tokens
        return ModelSettings(**base)

    base = {}
    for attr in ("temperature", "top_p", "max_tokens"):
        val = getattr(model_settings, attr, None)
        if val is not None:
            base[attr] = val
    if hasattr(model_settings, "extra_body") and model_settings.extra_body:
        base["extra_body"] = model_settings.extra_body
    if max_tokens is not None:
        base["max_tokens"] = max_tokens
    return ModelSettings(**base)


# ---------------------------------------------------------------------------
# SilentFrontend — minimal FrontendProtocol for E2E evals
# ---------------------------------------------------------------------------


class SilentFrontend:
    """Minimal frontend that captures status messages.

    Pass ``approval_response`` to control tool approval behaviour:
      - ``"y"`` (default): auto-approve everything
      - ``"n"``: deny everything
    """

    def __init__(self, *, approval_response: str = "y"):
        self.statuses: list[str] = []
        self.final_text: str | None = None
        self._approval_response = approval_response

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_call(self, name: str, args_display: str) -> None:
        pass

    def on_tool_result(self, title: str, content: Any) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.statuses.append(message)

    def on_final_output(self, text: str) -> None:
        self.final_text = text

    def prompt_approval(self, description: str) -> str:
        return self._approval_response

    def cleanup(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------


def extract_first_tool_call(
    messages: list[Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Extract the first ToolCallPart from agent messages."""
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                return part.tool_name, part.args_as_dict()
    return None, None


def extract_tool_calls(messages: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Extract all ToolCallParts from agent messages as (name, args) tuples."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                calls.append((part.tool_name, part.args_as_dict()))
    return calls
