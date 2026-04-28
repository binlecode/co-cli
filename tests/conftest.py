"""Root test configuration — applies global collection-time skip rules."""

import os

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.getenv("CI") != "true":
        return
    skip = pytest.mark.skip(reason="requires local infrastructure (Ollama, GPU) — skipped in CI")
    for item in items:
        if item.get_closest_marker("local"):
            item.add_marker(skip)
