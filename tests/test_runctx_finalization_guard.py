"""Functional tests for the REPL-boundary run-context GC-finalization guard.

The guard installs a narrow ``sys.unraisablehook`` that suppresses the benign upstream
pydantic-ai cross-Context run-context-reset ``ValueError`` and delegates everything else to
the prior hook. These tests feed synthesized unraisables through the installed hook and assert
suppression of exactly the target signature, pass-through of everything else, and restoration of
the prior hook on exit.
"""

import contextvars
import sys
import types

from co_cli.main import _runctx_finalization_guard


def _real_runctx_error() -> ValueError:
    """Produce the genuine cross-Context reset ValueError pydantic-ai raises on GC finalization.

    Setting the token in a child Context and resetting it from the parent reproduces the exact
    message (embeds ``pydantic_ai.current_run_context`` via the Token repr).
    """
    var = contextvars.ContextVar("pydantic_ai.current_run_context")
    child = contextvars.Context()
    token = child.run(lambda: var.set("run-ctx"))
    try:
        var.reset(token)
    except ValueError as exc:
        return exc
    raise AssertionError("expected a cross-Context reset ValueError")


def _unraisable(exc: BaseException):
    return types.SimpleNamespace(exc_value=exc)


class _RecordingHook:
    """Stand-in prior hook that records every unraisable delegated to it."""

    def __init__(self):
        self.delegated = []

    def __call__(self, unraisable):
        self.delegated.append(unraisable)


def test_target_signature_is_suppressed():
    prior = _RecordingHook()
    sys.unraisablehook = prior
    try:
        with _runctx_finalization_guard():
            sys.unraisablehook(_unraisable(_real_runctx_error()))
        assert prior.delegated == []
    finally:
        sys.unraisablehook = sys.__unraisablehook__


def test_plain_value_error_is_delegated():
    prior = _RecordingHook()
    sys.unraisablehook = prior
    try:
        with _runctx_finalization_guard():
            u = _unraisable(ValueError("something unrelated went wrong"))
            sys.unraisablehook(u)
        assert prior.delegated == [u]
    finally:
        sys.unraisablehook = sys.__unraisablehook__


def test_non_value_error_is_delegated():
    prior = _RecordingHook()
    sys.unraisablehook = prior
    try:
        with _runctx_finalization_guard():
            u = _unraisable(RuntimeError("loop is closed"))
            sys.unraisablehook(u)
        assert prior.delegated == [u]
    finally:
        sys.unraisablehook = sys.__unraisablehook__


def test_different_context_without_runctx_var_is_delegated():
    prior = _RecordingHook()
    sys.unraisablehook = prior
    try:
        with _runctx_finalization_guard():
            u = _unraisable(ValueError("<Token ...> was created in a different Context"))
            sys.unraisablehook(u)
        assert prior.delegated == [u]
    finally:
        sys.unraisablehook = sys.__unraisablehook__


def test_prior_hook_restored_on_exit():
    prior = _RecordingHook()
    sys.unraisablehook = prior
    try:
        with _runctx_finalization_guard():
            assert sys.unraisablehook is not prior
        assert sys.unraisablehook is prior
    finally:
        sys.unraisablehook = sys.__unraisablehook__
