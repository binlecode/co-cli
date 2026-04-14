"""Integration tests for the memory extractor implementation and agent.

Verifies end-to-end: _memory_extractor_agent detects a preference in a window,
calls save_memory, and a file appears in the memory dir. Uses real deps, real
model from production config, real filesystem via tmp_path.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli._model_factory import build_model
from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.deps import CoDeps
from co_cli.knowledge._store import KnowledgeStore
from co_cli.memory._extractor import _memory_extractor_agent, _run_extraction_async
from co_cli.tools.shell_backend import ShellBackend


@pytest.mark.asyncio
@pytest.mark.local
async def test_memory_extractor_writes_file_for_clear_preference(tmp_path: Path) -> None:
    """Extractor agent must detect a clear preference and write at least one memory file."""
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
        await _memory_extractor_agent.run(
            window,
            deps=deps,
            model=llm_model.model,
            model_settings=NOREASON_SETTINGS,
        )

    files = list(memory_dir.glob("*.md"))
    assert len(files) >= 1, (
        f"extractor must write at least one memory file to {memory_dir}, got {files}"
    )


@pytest.mark.asyncio
@pytest.mark.local
async def test_run_extraction_async_indexes_memory_and_advances_cursor(tmp_path: Path) -> None:
    """Direct helper path must write a file, index it, and advance the extraction cursor."""
    memory_dir = tmp_path / "memory"
    db_path = tmp_path / "search.db"
    config = make_settings()
    llm_model = build_model(config.llm)
    knowledge_store = KnowledgeStore(config=config, knowledge_db_path=db_path)
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_store=knowledge_store,
        config=config,
        memory_dir=memory_dir,
        model=llm_model,
    )
    messages = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "I always prefer pytest for all testing, and I do not want trailing "
                        "comments in code."
                    )
                )
            ]
        ),
        ModelResponse(
            parts=[
                TextPart(
                    content="Understood. I will default to pytest and avoid trailing comments."
                )
            ],
            model_name="test-model",
        ),
    ]

    try:
        async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
            await _run_extraction_async(
                messages,
                deps=deps,
                frontend=None,
                cursor_start=0,
            )

        files = list(memory_dir.glob("*.md"))
        results = knowledge_store.search("pytest", source="memory", kind="memory", limit=5)

        assert deps.session.last_extracted_message_idx == len(messages)
        assert len(files) >= 1
        assert any(Path(result.path).parent == memory_dir for result in results)
    finally:
        knowledge_store.close()
