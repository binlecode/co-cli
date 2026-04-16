"""Integration tests for the knowledge extractor implementation and agent.

Verifies end-to-end: _knowledge_extractor_agent detects a preference in a window,
calls save_memory, and a file appears in the memory dir. Uses real deps, real
model from production config, real filesystem via tmp_path.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._settings import make_settings
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.deps import CoDeps
from co_cli.knowledge._store import KnowledgeStore
from co_cli.llm._factory import build_model
from co_cli.memory._extractor import _run_extraction_async
from co_cli.tools.shell_backend import ShellBackend


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
        knowledge_dir=memory_dir,
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
        results = knowledge_store.search("pytest", source="knowledge", limit=5)

        assert deps.session.last_extracted_message_idx == len(messages)
        assert len(files) >= 1
        assert any(Path(result.path).parent == memory_dir for result in results)
    finally:
        knowledge_store.close()
