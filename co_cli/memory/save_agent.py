"""Memory write subagent — write-dispatcher tool and agent singleton.

Module-level Agent singleton that owns all writes inside the memory directory.
Registered tools: read_file, list_directory, find_in_files (read-only), memory (write).
Same singleton pattern as _memory_save_agent in _save.py and _extraction_agent in _extractor.py.
"""

import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.files import find_in_files, list_directory, read_file
from co_cli.tools.tool_output import tool_output

_PROMPT_PATH = Path(__file__).parent / "prompts" / "memory_save_agent.md"


class MemoryActionEnum(StrEnum):
    """Actions the memory write-dispatcher tool can perform."""

    CREATE = "create"
    EDIT = "edit"
    APPEND = "append"
    DELETE = "delete"


def _resolve_memory_path(ctx: RunContext[CoDeps], path: str) -> Path:
    """Resolve a memory-relative path and verify it stays within memory_dir.

    Raises ValueError if path escapes the memory directory.
    """
    root = ctx.deps.memory_dir.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"path {path!r} is outside memory directory")
    return resolved


async def memory(
    ctx: RunContext[CoDeps],
    action: MemoryActionEnum,
    path: str,
    content: str | None = None,
    search: str | None = None,
    replacement: str | None = None,
    replace_all: bool = False,
) -> ToolReturn:
    """Create, edit, append to, or delete a file inside the memory directory.

    action=create: write content to path (atomic). Fails if path already exists.
    action=edit:   replace search with replacement. search must appear exactly
                   once unless replace_all=True.
    action=append: append content to end of existing file body.
    action=delete: remove path from memory directory.

    All paths are relative to the memory directory. Absolute paths and path
    traversal (../) are rejected.
    """
    from co_cli.tools.resource_lock import ResourceBusyError

    resolved = _resolve_memory_path(ctx, path)

    match action:
        case MemoryActionEnum.CREATE:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            tmp = resolved.with_name(resolved.name + ".tmp")
            try:
                async with ctx.deps.resource_locks.try_acquire(str(resolved)):
                    if resolved.exists():
                        raise FileExistsError(f"Memory file already exists: {path!r}")
                    try:
                        tmp.write_text(content or "", encoding="utf-8")
                        os.replace(tmp, resolved)
                    except Exception:
                        tmp.unlink(missing_ok=True)
                        raise
            except ResourceBusyError:
                return tool_output(
                    f"File {path} is being modified by another tool call — retry next turn",
                    ctx=ctx,
                )
            return tool_output(f"Created: {path}", ctx=ctx, path=str(resolved), action="create")

        case MemoryActionEnum.EDIT:
            if search is None:
                raise ValueError("search is required for action=edit")
            if replacement is None:
                raise ValueError("replacement is required for action=edit")
            try:
                async with ctx.deps.resource_locks.try_acquire(str(resolved)):
                    body = resolved.read_text(encoding="utf-8")
                    count = body.count(search)
                    if count == 0:
                        raise ValueError(f"search string not found in {path!r}")
                    if count > 1 and not replace_all:
                        raise ValueError(
                            f"search string found {count} times in {path!r}"
                            " — use replace_all=True or provide more context"
                        )
                    if replace_all:
                        updated = body.replace(search, replacement)
                    else:
                        updated = body.replace(search, replacement, 1)
                    resolved.write_text(updated, encoding="utf-8")
            except ResourceBusyError:
                return tool_output(
                    f"File {path} is being modified by another tool call — retry next turn",
                    ctx=ctx,
                )
            return tool_output(f"Edited: {path}", ctx=ctx, path=str(resolved), action="edit")

        case MemoryActionEnum.APPEND:
            try:
                async with ctx.deps.resource_locks.try_acquire(str(resolved)):
                    if not resolved.exists():
                        raise FileNotFoundError(f"Memory file not found: {path!r}")
                    body = resolved.read_text(encoding="utf-8")
                    updated = body.rstrip() + "\n" + (content or "")
                    resolved.write_text(updated, encoding="utf-8")
            except ResourceBusyError:
                return tool_output(
                    f"File {path} is being modified by another tool call — retry next turn",
                    ctx=ctx,
                )
            return tool_output(f"Appended: {path}", ctx=ctx, path=str(resolved), action="append")

        case MemoryActionEnum.DELETE:
            try:
                async with ctx.deps.resource_locks.try_acquire(str(resolved)):
                    if not resolved.exists():
                        raise FileNotFoundError(f"Memory file not found: {path!r}")
                    resolved.unlink()
            except ResourceBusyError:
                return tool_output(
                    f"File {path} is being modified by another tool call — retry next turn",
                    ctx=ctx,
                )
            return tool_output(f"Deleted: {path}", ctx=ctx, path=str(resolved), action="delete")

        case _:
            raise ValueError(f"Unknown action: {action!r}")


class SaveMemoryAgentOutput(BaseModel):
    """Structured output from the memory save subagent."""

    summary: str
    files_touched: list[str]
    actions: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


_save_memory_agent: Agent[CoDeps, SaveMemoryAgentOutput] = Agent(
    deps_type=CoDeps,
    output_type=SaveMemoryAgentOutput,
    instructions=_PROMPT_PATH.read_text(encoding="utf-8").strip(),
)
_save_memory_agent.tool(read_file, requires_approval=False)  # type: ignore[arg-type]  # pydantic-ai .tool() overloads require exact AgentDepsT match; Agent[CoDeps, ...] is correct
_save_memory_agent.tool(list_directory, requires_approval=False)  # type: ignore[arg-type]  # same as above
_save_memory_agent.tool(find_in_files, requires_approval=False)  # type: ignore[arg-type]  # same as above
_save_memory_agent.tool(memory, requires_approval=False)  # type: ignore[arg-type]  # same as above
