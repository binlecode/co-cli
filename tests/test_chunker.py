"""Functional tests for co_cli._chunker.chunk_text."""

from co_cli.knowledge._chunker import Chunk, chunk_text


def test_short_text_returns_single_chunk() -> None:
    """Short text (< chunk_size in tokens) must yield exactly one chunk."""
    text = "Hello, world!\nThis is a short document."
    chunks = chunk_text(text, chunk_size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].start_line == 0
    assert chunks[0].content == text


def test_multi_paragraph_text_produces_multiple_chunks() -> None:
    """Long multi-paragraph text must produce more than one chunk."""
    # Each paragraph is ~400 tokens (1600 chars); chunk_size=512 → they don't all fit together
    para = "x" * 1600
    text = "\n\n".join([para] * 5)
    chunks = chunk_text(text, chunk_size=512, overlap=0)
    assert len(chunks) > 1
    # Each chunk's content must fit within budget (overlap=0 so no prefix inflation)
    for chunk in chunks:
        assert len(chunk.content) / 4 <= 512


def test_overlap_content_matches_between_chunks() -> None:
    """The tail of chunk N must appear at the start of chunk N+1."""
    para = "y" * 1600
    text = "\n\n".join([para] * 5)
    overlap = 64
    chunks = chunk_text(text, chunk_size=512, overlap=overlap)
    assert len(chunks) >= 2
    # Check overlap between consecutive chunks
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        curr = chunks[i]
        overlap_chars = overlap * 4
        if len(prev.content) >= overlap_chars:
            expected_prefix = prev.content[-overlap_chars:]
            assert curr.content.startswith(expected_prefix), (
                f"Chunk {i} does not start with overlap from chunk {i - 1}"
            )


def test_line_ranges_bound_each_chunk() -> None:
    """start_line and end_line must correctly index into the original line list."""
    # Build text: 10 paragraphs of 3 lines each, separated by blank lines
    paragraphs = []
    for p in range(10):
        paragraphs.append(f"para{p}_line0\npara{p}_line1\npara{p}_line2")
    text = "\n\n".join(paragraphs)
    lines = text.split("\n")

    chunks = chunk_text(text, chunk_size=32, overlap=0)
    assert len(chunks) > 1

    for chunk in chunks:
        assert 0 <= chunk.start_line <= chunk.end_line
        assert chunk.end_line < len(lines)
        # The chunk content (ignoring overlap prefix) must contain lines from the indicated range
        chunk_lines = chunk.content.split("\n")
        # At least one line from the chunk must match the original lines in the declared range
        original_segment = lines[chunk.start_line: chunk.end_line + 1]
        assert any(l in chunk_lines for l in original_segment if l.strip()), (
            f"Chunk {chunk.index} line range [{chunk.start_line},{chunk.end_line}] "
            f"doesn't match chunk content"
        )


def test_single_oversized_paragraph_splits_into_multiple_chunks() -> None:
    """A document with no blank lines exceeding chunk_size must be split."""
    # 200 lines of 40 chars each → 200 * 40 / 4 = 2000 tokens >> chunk_size=128
    lines = [f"Line {i:04d}: " + "a" * 30 for i in range(200)]
    text = "\n".join(lines)
    chunks = chunk_text(text, chunk_size=128, overlap=0)
    assert len(chunks) > 1
    # Verify full coverage: every line appears in some chunk
    for i, line in enumerate(lines):
        found = any(line in c.content for c in chunks)
        assert found, f"Line {i} not found in any chunk"


def test_empty_string_returns_one_empty_chunk() -> None:
    """Empty string input must return exactly one Chunk with content=''."""
    chunks = chunk_text("", chunk_size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0].content == ""
    assert chunks[0].index == 0
    assert chunks[0].start_line == 0
    assert chunks[0].end_line == 0


def test_overlap_clamped_when_overlap_exceeds_chunk_size() -> None:
    """overlap >= chunk_size must be silently clamped; no error, valid chunks returned."""
    para = "z" * 1600
    text = "\n\n".join([para] * 4)
    # overlap=600 > chunk_size=512 — should be clamped without raising
    chunks = chunk_text(text, chunk_size=512, overlap=600)
    assert len(chunks) >= 1
    # All chunks must have valid structure
    for chunk in chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.index >= 0
        assert chunk.start_line >= 0
        assert chunk.end_line >= chunk.start_line
