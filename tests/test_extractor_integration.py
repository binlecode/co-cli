"""Integration test for the insights extractor agent.

Verifies end-to-end: _insights_extractor_agent detects a preference in a window,
calls save_insight, and a file appears in the insights dir. Uses real deps, real
model from production config, real filesystem via tmp_path.
"""

import asyncio
from pathlib import Path

import pytest
from tests._settings import make_settings

from co_cli._model_factory import build_model
from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.deps import CoDeps
from co_cli.memory._extractor import _insights_extractor_agent
from co_cli.tools.shell_backend import ShellBackend


@pytest.mark.asyncio
async def test_insights_extractor_writes_file_for_clear_preference(tmp_path: Path) -> None:
    """Extractor agent must detect a clear preference and write at least one insight file."""
    memory_dir = tmp_path / "memory"
    config = make_settings()
    llm_model = build_model(config.llm)
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=None,
        config=config,
        memory_dir=memory_dir,
        model=llm_model,
    )

    window = (
        "User: I always use pytest for all my Python projects and I never use unittest.\n"
        "Co: Understood, I'll keep that in mind when writing tests for you."
    )

    async with asyncio.timeout(30):
        await _insights_extractor_agent.run(
            window,
            deps=deps,
            model=llm_model.model,
            model_settings=NOREASON_SETTINGS,
        )

    files = list(memory_dir.glob("*.md"))
    assert len(files) >= 1, (
        f"extractor must write at least one insight file to {memory_dir}, got {files}"
    )
