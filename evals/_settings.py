"""Shared eval settings — sourced from the real system config, overridable.

The single common settings surface for all evals. Eval-level sizing/config knobs
(the operational context window today; room for more) live here so every eval
draws the same values, sourced from ``load_config()`` — the same real config the
CLI loads. Evals therefore validate real behavior, never a coined/fictional
config. Each value defaults to the system-configured setting and is overridable
for the rare eval that genuinely needs a non-default (e.g. a smaller window for
tractability) via the centralized ``eval_max_ctx`` entry point — never coined
inline in an eval body.

Sibling centralized modules: ``_timeouts`` (per-call budgets), ``_deps`` (the
real ``CoDeps`` factory). This module owns sizing/config constants. Mirrors
``tests/_settings.py`` on the test side.
"""

from __future__ import annotations

from co_cli.config.core import Settings, load_config

_BASE: Settings | None = None


def _load_base() -> Settings:
    """Load the real system config once (user + project + env, fully validated)."""
    global _BASE
    if _BASE is None:
        _BASE = load_config()
    return _BASE


EVAL_MAX_CTX: int = _load_base().llm.max_ctx
"""Operational context window for evals — the real system-configured ``max_ctx``
(``co_cli/config/llm.py`` ``DEFAULT_MAX_CTX = 65_536`` unless overridden in
``settings.json`` / env). ``create_deps`` resolves ``deps.model_max_ctx`` to this
same value, so applying it to eval deps is idempotent by default; the override
lever lives in :func:`eval_max_ctx`, not inline in any eval."""


def eval_max_ctx(override: int | None = None) -> int:
    """Return the eval context window: the system-configured value by default.

    Pass ``override`` only when an eval genuinely needs a non-default window
    (e.g. a smaller one to keep a long real-LLM loop tractable). This is the
    single, centralized place an eval may diverge from the system window — never
    set ``deps.model_max_ctx`` to a literal inline in an eval body.
    """
    return EVAL_MAX_CTX if override is None else override
