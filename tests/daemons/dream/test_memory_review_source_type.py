"""Verify the memory_create tool accepts and persists source_type='session_review'.

The daemon's memory reviewer tags items with source_type='session_review'; this
verifies the tool surface actually accepts and round-trips that parameter.
"""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.item import load_memory_item
from co_cli.tools.memory.manage import memory_create
from co_cli.tools.shell_backend import ShellBackend


@pytest.mark.asyncio
async def test_memory_create_persists_source_type_session_review(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        memory_dir=memory_dir,
        index_store=None,
        memory_store=None,
    )
    ctx: RunContext[CoDeps] = RunContext(deps=deps, model=None, usage=RunUsage())

    result = await memory_create(
        ctx,
        name_title="reviewer-extracted-fact",
        content="The user prefers terse responses.",
        kind="note",
        source_type="session_review",
    )

    assert result.metadata is None or not result.metadata.get("error"), (
        f"memory_create rejected source_type parameter: {result}"
    )
    path_str = result.metadata.get("path") if result.metadata else None
    assert path_str is not None, "save did not return a path"
    item = load_memory_item(Path(path_str))
    assert item.source_type == "session_review"
