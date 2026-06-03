"""Root test configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests._timeouts import PYTEST_PER_TEST_TIMEOUT_SECS

if TYPE_CHECKING:
    from _pytest.config import Config


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: Config) -> None:
    """Apply the test-owned per-test timeout ceiling.

    The per-test pytest-timeout budget is a calibrated testing setting, so it
    lives in tests/_timeouts.py — not pyproject.toml, which holds only
    infra-level config. pytest-timeout reads ``config.getvalue("timeout")``
    (the ``--timeout`` option) ahead of env and ini, and caches it in its own
    ``pytest_configure``; ``tryfirst`` runs this hook before that cache is
    built. An explicit ``--timeout`` on the command line is left untouched.
    """
    if config.option.timeout is None:
        config.option.timeout = PYTEST_PER_TEST_TIMEOUT_SECS
