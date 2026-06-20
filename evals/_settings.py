"""Shared eval settings — sourced from the real system config, overridable.

The single common settings surface for all evals — the one place the eval
context window is defined, so every eval draws the same baseline. The value is
sourced from ``load_config()`` (the same real config the CLI loads) then bounded
by an eval-wide pressure cap. Evals therefore validate real behavior (32k is a
valid configurable window, not a fictional one), never a coined config divorced
from production. Capped at :data:`_PRESSURE_CAP_TOKENS` so the compaction/spill
ladder is exercised under magnified pressure within a tractable real-LLM run.
Evals apply it via :func:`apply_eval_window` (called on built ``deps``), which
lowers co's accounting window AND re-derives every field keyed off it (e.g.
``spill_threshold_tokens``) together. :func:`eval_max_context_tokens` exposes the value and
an ``override`` lever for a case that must diverge — never coined inline.

Sibling centralized modules: ``_timeouts`` (per-call budgets), ``_deps`` (the
real ``CoDeps`` builder). This module owns sizing/config constants. Mirrors
``tests/_settings.py`` on the test side.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from co_cli.config.core import USER_DIR, Settings, load_config
from co_cli.config.llm import LlmSettings
from co_cli.llm.factory import LlmModel, build_model

_BASE: Settings | None = None


def _load_base() -> Settings:
    """Load the real system config once (user + project + env, fully validated)."""
    global _BASE
    if _BASE is None:
        _BASE = load_config()
    return _BASE


# Eval-wide context-window pressure cap. Deliberately HALF the system default
# (``co_cli/config/llm.py`` ``MAX_CONTEXT_TOKENS = 65_536``) so every eval runs the
# compaction/spill ladder under magnified pressure: the proactive trigger
# (``compaction_ratio x model_max_context_tokens``) drops to 0.50 x 32k = 16k, crossed in
# fewer turns. 32k is itself a valid configurable window, so this stays real
# behavior — a bounded one, not a fictional config.
_PRESSURE_CAP_TOKENS: int = 32_768

EVAL_MAX_CONTEXT_TOKENS: int = min(_load_base().llm.max_context_tokens, _PRESSURE_CAP_TOKENS)
"""Shared operational context window for ALL evals — the real system-configured
``max_context_tokens`` bounded by :data:`_PRESSURE_CAP_TOKENS` (32k). The single source of
truth for the eval window; evals read it via :func:`eval_max_context_tokens`. The per-eval
override lever also lives in :func:`eval_max_context_tokens`, not inline in any eval."""


def eval_max_context_tokens(override: int | None = None) -> int:
    """Return the eval context window: the shared :data:`EVAL_MAX_CONTEXT_TOKENS` by default.

    Pass ``override`` only when an eval genuinely needs a non-default window
    (e.g. a smaller one to keep a long real-LLM loop tractable). This is the
    single, centralized place an eval may diverge from the shared baseline — never
    set ``deps.model_max_context_tokens`` to a literal inline in an eval body.
    """
    return EVAL_MAX_CONTEXT_TOKENS if override is None else override


EVAL_JUDGE_PROVIDER = "gemini"
EVAL_JUDGE_MODEL = "gemini-3.5-flash"
"""Eval-only judge: a frontier Gemini model, distinct from the local-Ollama agent
under test. Pinning a strong cross-provider judge is what makes the behavioral
verdicts trustworthy (Constraint #17) — a self-judging local model both produces
and grades the output. This lives in the EVAL layer only: production
``build_judge_model`` (``co_cli/llm/factory.py``) deliberately keeps the judge on
the agent's provider (cross-provider judges are out of scope there). Override the
model id with ``CO_EVAL_JUDGE_MODEL``."""


def make_eval_judge() -> tuple[LlmModel, str] | None:
    """Build the eval-only Gemini judge, or None when no API key is present.

    Resolves the key from ``GEMINI_API_KEY`` (fallback ``CO_LLM_API_KEY``) — store
    it under ``~/env-secrets/`` and export it for the eval run. Returns
    ``(LlmModel, model_name)`` so the caller can set both ``deps.judge_model`` and
    ``deps.config.llm.judge_model`` (the latter only drives the
    ``[judge_model=<name>]`` annotation). Returns None when no key is set, so evals
    still run — they fall back to the agent model and flag
    ``[judge_model_same_as_agent]`` (Constraint #17).
    """
    model_name = os.environ.get("CO_EVAL_JUDGE_MODEL", EVAL_JUDGE_MODEL)
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("CO_LLM_API_KEY")
    if not api_key:
        return None
    judge_llm = LlmSettings(provider=EVAL_JUDGE_PROVIDER, model=model_name, api_key=api_key)
    return build_model(judge_llm), model_name


def apply_eval_judge(deps: Any) -> str:
    """Override ``deps.judge_model`` with the eval-only Gemini judge if configured.

    Returns a short status string for the eval to print. No-op (self-judge) when
    no API key is set. Call right after ``make_eval_deps()`` / ``eval_deps()``,
    alongside :func:`apply_eval_window`.
    """
    built = make_eval_judge()
    if built is None:
        return f"judge: self (no {EVAL_JUDGE_PROVIDER.upper()} api key — set GEMINI_API_KEY)"
    judge_model, model_name = built
    deps.judge_model = judge_model
    deps.config.llm.judge_model = model_name
    return f"judge: {model_name} (eval-only, distinct from agent)"


def eval_agent_uses_ollama(deps: Any) -> bool:
    """Centralized predicate: is the eval's agent-under-test on the local Ollama backend?

    The agent-under-test backend is selected through the SAME real config mechanism the
    CLI uses — ``load_config()`` honors ``CO_LLM_PROVIDER`` / ``CO_LLM_MODEL`` (and resolves
    ``GEMINI_API_KEY`` for the gemini provider), and ``create_deps`` builds the model from
    that config via ``build_model`` (whose gemini settings come from
    ``LlmSettings.reasoning_model_settings()`` / ``_gemini_settings``). So an eval points
    its agent at the live gemini frontier path by running, e.g.::

        CO_LLM_PROVIDER=gemini CO_LLM_MODEL=gemini-3.1-pro-preview \\
            uv run python evals/eval_rule_compliance.py

    No inline ``ModelSettings`` or model coining at the eval layer (``feedback_evals_centralized_settings``):
    the backend flows entirely from config. This predicate is the single place the harness
    asks "is this the local path?" so the Ollama-only warm-up (``ensure_ollama_warm``, which
    must NOT run on the gemini path) is gated centrally rather than re-deriving the provider
    check inline in each eval.
    """
    return deps.config.llm.uses_ollama()


EVAL_WORKSPACE_DIR = USER_DIR / "eval-workspace"
"""Isolated write/read anchor for evals — a real, stable dir under CO_HOME, NOT the
repo. ``create_deps`` resolves ``workspace_dir`` to ``Path.cwd()`` when
``config.workspace_path`` is unset (``deps.py``); under an eval that cwd is the repo
root, so a relative ``file_write`` by the agent escapes into the working tree —
``enforce_write_boundary`` permits it because the boundary IS the repo root. Pinning a
dedicated dir keeps eval side effects real (Constraint: all data real, no cleanup) but
contained."""


def apply_eval_workspace(deps: Any) -> None:
    """Re-anchor built ``deps`` to :data:`EVAL_WORKSPACE_DIR` so agent file writes during
    an eval land in an isolated dir, never the repo. Sets the write boundary
    (``workspace_dir``) AND ``file_search_roots`` together — mirroring production's
    ``resolve_workspace_paths`` default (``file_search_roots = [workspace_dir]``). Applied
    centrally in ``_deps`` for every eval; isolation is a safety invariant, not opt-in.

    The dir is wiped on each build so every eval starts from a clean workspace —
    otherwise agent-created files (a written test module, ``__pycache__``, a leaked
    decision doc) accumulate across runs and bleed into a later case's file search,
    corrupting its behavioral signal. Fixtures re-seed their ``workspace/`` subtree
    via ``load_fixture`` after this runs.
    """
    if EVAL_WORKSPACE_DIR.exists():
        shutil.rmtree(EVAL_WORKSPACE_DIR)
    EVAL_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    deps.workspace_dir = EVAL_WORKSPACE_DIR
    deps.file_search_roots = [EVAL_WORKSPACE_DIR]


def apply_eval_window(deps: Any) -> None:
    """Pin built ``deps`` to the shared eval window AND re-derive every field that
    ``create_deps`` derived from ``model_max_context_tokens`` — today ``spill_threshold_tokens``
    (``bootstrap/core.py``: ``int(spill_ratio * model_max_context_tokens)``). Call right after
    ``make_eval_deps()`` / ``eval_deps()``.

    This is a deliberate **budget simulation**: it lowers co's accounting window so
    the L2 spill and L3 compaction triggers fire earlier (magnified pressure) while
    the model keeps its physical ``num_ctx``. Both fields must move together —
    setting ``model_max_context_tokens`` alone leaves ``spill_threshold_tokens`` frozen at the
    system-window value, so L2 would never fire (L3 trims everything first).

    Why post-construction and not via config before build: co's ceiling invariant
    requires ``num_ctx <= max_context_tokens`` and the model's per-call ``num_ctx`` is pinned in
    ``_LLM_SETTINGS`` at the system window, so lowering ``config.llm.max_context_tokens`` first
    is rejected by ``validate_ollama_num_ctx``. The eval simulates a tighter budget,
    not a physically smaller model, so it overrides after the validated build.
    """
    deps.model_max_context_tokens = EVAL_MAX_CONTEXT_TOKENS
    deps.spill_threshold_tokens = int(deps.config.compaction.spill_ratio * EVAL_MAX_CONTEXT_TOKENS)
