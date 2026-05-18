"""Chunk dataclass — the write contract between domain modules and IndexStore.

Domain modules (memory, session) produce `Chunk` records via their own
chunkers and hand them to `IndexStore.index_chunks(...)`. The index layer
treats chunks as opaque (source, doc_path, chunk_index, content, line range).
"""

from dataclasses import dataclass


@dataclass
class Chunk:
    """A single indexable chunk emitted by a domain chunker."""

    index: int
    content: str
    start_line: int
    end_line: int
