"""Batch 1 Integration Tests - Deps Pattern

Functional tests only - no mocks.
"""

import pytest

from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox
from co_cli.main import create_deps


class TestCreateDeps:
    """Test the create_deps() factory function."""

    def test_create_deps_returns_codeps(self, monkeypatch):
        """create_deps() returns a valid CoDeps instance."""
        monkeypatch.setenv("CO_CLI_AUTO_CONFIRM", "false")

        from co_cli import config, main as main_module
        test_settings = config.Settings()
        monkeypatch.setattr(config, "settings", test_settings)
        monkeypatch.setattr(main_module, "settings", test_settings)

        deps = create_deps()

        assert isinstance(deps, CoDeps)
        assert isinstance(deps.sandbox, Sandbox)
        assert deps.auto_confirm is False
        assert len(deps.session_id) > 0

    def test_create_deps_auto_confirm_true(self, monkeypatch):
        """create_deps() respects auto_confirm setting."""
        monkeypatch.setenv("CO_CLI_AUTO_CONFIRM", "true")

        from co_cli import config, main as main_module
        test_settings = config.Settings()
        monkeypatch.setattr(config, "settings", test_settings)
        monkeypatch.setattr(main_module, "settings", test_settings)

        deps = create_deps()

        assert deps.auto_confirm is True


def test_sandbox_cleanup_on_fresh():
    """Verify sandbox.cleanup() doesn't error on fresh sandbox."""
    sandbox = Sandbox(container_name="test-cleanup")
    sandbox.cleanup()  # Should not raise
