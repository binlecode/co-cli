"""Regression tests for skill environment lifecycle cleanup."""

from tests._settings import make_settings

from co_cli.deps import CoDeps
from co_cli.skills.lifecycle import cleanup_skill_run_state
from co_cli.tools.shell_backend import ShellBackend


def test_cleanup_skill_restores_set_env_var():
    """Key that was set before a skill run is restored to its original value."""
    import os

    key = "TEST_CO_SKILL_RESTORE_KEY"
    original = "original-value"
    os.environ[key] = original
    try:
        os.environ[key] = "skill-injected-value"
        deps = CoDeps(shell=ShellBackend(), config=make_settings())
        cleanup_skill_run_state({key: original}, deps)
        assert os.environ[key] == original
    finally:
        os.environ.pop(key, None)


def test_cleanup_skill_removes_absent_env_var():
    """Key absent before the skill run is removed during cleanup."""
    import os

    key = "TEST_CO_SKILL_ABSENT_KEY"
    os.environ.pop(key, None)
    os.environ[key] = "skill-injected-value"
    try:
        deps = CoDeps(shell=ShellBackend(), config=make_settings())
        cleanup_skill_run_state({key: None}, deps)
        assert key not in os.environ
    finally:
        os.environ.pop(key, None)


def test_cleanup_skill_clears_active_skill_name():
    """cleanup_skill_run_state must always clear the active skill marker."""
    deps = CoDeps(shell=ShellBackend(), config=make_settings())
    deps.runtime.active_skill_name = "my-skill"
    cleanup_skill_run_state({}, deps)
    assert deps.runtime.active_skill_name is None
