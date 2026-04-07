from dataclasses import dataclass


@dataclass
class Chunk:
    index: int
    content: str
    start_line: int
    # 0-based line index of last line (inclusive)
    end_line: int


def chunk_text(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """Split text into overlapping chunks using token estimation (len/4).

    Split priority: paragraph boundaries > line boundaries > character split.
    Overlap prepends the last `overlap` tokens of the previous chunk.
    """
    # Normalise Windows line endings
    text = text.replace("\r\n", "\n")

    # Empty string edge case
    if text == "":
        return [Chunk(index=0, content="", start_line=0, end_line=0)]

    # Clamp overlap silently when overlap >= chunk_size
    if overlap >= chunk_size:
        overlap = chunk_size // 4

    lines = text.split("\n")

    # Short-document fast path
    if len(text) / 4 <= chunk_size:
        return [Chunk(index=0, content=text, start_line=0, end_line=max(0, len(lines) - 1))]

    overlap_chars = overlap * 4

    # Build paragraph blocks (groups of lines separated by blank lines)
    # Each paragraph is (start_line_index, list_of_lines)
    paragraphs: list[tuple[int, list[str]]] = []
    current_start = 0
    current_lines: list[str] = []

    for i, line in enumerate(lines):
        if line.strip() == "":
            if current_lines:
                paragraphs.append((current_start, current_lines))
                current_lines = []
            current_start = i + 1
        else:
            if not current_lines:
                current_start = i
            current_lines.append(line)

    if current_lines:
        paragraphs.append((current_start, current_lines))

    chunks: list[Chunk] = []
    overlap_prefix = ""

    def emit_chunk(content: str, start_line: int, end_line: int) -> None:
        nonlocal overlap_prefix
        chunks.append(Chunk(
            index=len(chunks),
            content=content,
            start_line=start_line,
            end_line=end_line,
        ))
        # Compute overlap prefix from this chunk's content; empty when overlap_chars is zero
        if overlap_chars <= 0:
            overlap_prefix = ""
        elif len(content) > overlap_chars:
            overlap_prefix = content[-overlap_chars:]
        else:
            overlap_prefix = content

    def split_lines_into_chunks(para_start: int, para_lines: list[str]) -> None:
        """Split a paragraph at line boundaries (and char boundaries if needed)."""
        nonlocal overlap_prefix
        buf_lines: list[str] = []
        buf_start = para_start

        for rel_i, line in enumerate(para_lines):
            abs_line = para_start + rel_i
            line_tokens = len(line) / 4

            if line_tokens > chunk_size:
                # Hard-split this single line at character boundaries
                if buf_lines:
                    full = overlap_prefix + "\n".join(buf_lines)
                    emit_chunk(full, buf_start, abs_line - 1)
                    buf_lines = []
                    buf_start = abs_line

                step = chunk_size * 4
                pos = 0
                while pos < len(line):
                    seg = line[pos: pos + step]
                    full = overlap_prefix + seg
                    emit_chunk(full, abs_line, abs_line)
                    pos += step
                buf_start = abs_line + 1
                continue

            buf_tokens = sum(len(l) / 4 for l in buf_lines)
            if buf_tokens + line_tokens > chunk_size and buf_lines:
                full = overlap_prefix + "\n".join(buf_lines)
                emit_chunk(full, buf_start, abs_line - 1)
                buf_lines = []
                buf_start = abs_line

            buf_lines.append(line)

        if buf_lines:
            full = overlap_prefix + "\n".join(buf_lines)
            emit_chunk(full, buf_start, para_start + len(para_lines) - 1)
            buf_lines = []

    # Accumulate paragraphs into chunks
    acc_paragraphs: list[tuple[int, list[str]]] = []
    acc_tokens: float = 0.0
    acc_start: int = 0

    def flush_acc() -> None:
        nonlocal acc_paragraphs, acc_tokens, acc_start, overlap_prefix
        if not acc_paragraphs:
            return
        # Join accumulated paragraphs with blank line separator
        parts: list[str] = []
        for _, para_lines in acc_paragraphs:
            parts.append("\n".join(para_lines))
        joined = "\n\n".join(parts)
        end_line = acc_paragraphs[-1][0] + len(acc_paragraphs[-1][1]) - 1
        full = overlap_prefix + joined
        emit_chunk(full, acc_start, end_line)
        acc_paragraphs = []
        acc_tokens = 0.0

    for para_start, para_lines in paragraphs:
        para_text = "\n".join(para_lines)
        para_tokens = len(para_text) / 4

        if para_tokens > chunk_size:
            # Flush any accumulated paragraphs first
            flush_acc()
            acc_start = para_start
            # Split this oversized paragraph at line/char level
            split_lines_into_chunks(para_start, para_lines)
            # overlap_prefix is updated inside split_lines_into_chunks via emit_chunk
            continue

        if acc_tokens + para_tokens > chunk_size and acc_paragraphs:
            flush_acc()
            acc_start = para_start
            acc_tokens = 0.0

        if not acc_paragraphs:
            acc_start = para_start
        acc_paragraphs.append((para_start, para_lines))
        acc_tokens += para_tokens

    flush_acc()

    return chunks
