"""Paragraph-aware text chunking for memory artifacts.

Returns the canonical ``Chunk`` write contract directly. ``start_line`` /
``end_line`` carry 0-indexed file line numbers.
"""

from dataclasses import dataclass, field

from co_cli.index.chunk import Chunk


@dataclass
class _ChunkCtx:
    """Mutable state shared across chunk_text helpers."""

    chunk_tokens: int
    overlap_chars: int
    chunks: list[Chunk] = field(default_factory=list)
    overlap_prefix: str = ""
    acc_paragraphs: list[tuple[int, list[str]]] = field(default_factory=list)
    acc_tokens: float = 0.0
    acc_start: int = 0


def _build_paragraphs(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Group lines into paragraph blocks separated by blank lines."""
    paragraphs: list[tuple[int, list[str]]] = []
    current_start = 0
    current_lines: list[str] = []
    for idx, line in enumerate(lines):
        if line.strip() == "":
            if current_lines:
                paragraphs.append((current_start, current_lines))
                current_lines = []
            current_start = idx + 1
        else:
            if not current_lines:
                current_start = idx
            current_lines.append(line)
    if current_lines:
        paragraphs.append((current_start, current_lines))
    return paragraphs


def _emit_chunk(ctx: _ChunkCtx, content: str, start_line: int, end_line: int) -> None:
    ctx.chunks.append(
        Chunk(index=len(ctx.chunks), content=content, start_line=start_line, end_line=end_line)
    )
    if ctx.overlap_chars <= 0:
        ctx.overlap_prefix = ""
    elif len(content) > ctx.overlap_chars:
        ctx.overlap_prefix = content[-ctx.overlap_chars :]
    else:
        ctx.overlap_prefix = content


def _split_para_into_chunks(ctx: _ChunkCtx, para_start: int, para_lines: list[str]) -> None:
    """Split a paragraph at line boundaries (and char boundaries if needed)."""
    buf_lines: list[str] = []
    buf_start = para_start
    for rel_idx, line in enumerate(para_lines):
        abs_line = para_start + rel_idx
        line_tokens = len(line) / 4
        if line_tokens > ctx.chunk_tokens:
            if buf_lines:
                _emit_chunk(
                    ctx, ctx.overlap_prefix + "\n".join(buf_lines), buf_start, abs_line - 1
                )
                buf_lines = []
                buf_start = abs_line
            step = ctx.chunk_tokens * 4
            pos = 0
            while pos < len(line):
                _emit_chunk(ctx, ctx.overlap_prefix + line[pos : pos + step], abs_line, abs_line)
                pos += step
            buf_start = abs_line + 1
            continue
        buf_tokens = sum(len(ln) / 4 for ln in buf_lines)
        if buf_tokens + line_tokens > ctx.chunk_tokens and buf_lines:
            _emit_chunk(ctx, ctx.overlap_prefix + "\n".join(buf_lines), buf_start, abs_line - 1)
            buf_lines = []
            buf_start = abs_line
        buf_lines.append(line)
    if buf_lines:
        _emit_chunk(
            ctx,
            ctx.overlap_prefix + "\n".join(buf_lines),
            buf_start,
            para_start + len(para_lines) - 1,
        )


def _flush_acc(ctx: _ChunkCtx) -> None:
    if not ctx.acc_paragraphs:
        return
    parts = ["\n".join(para_lines) for _, para_lines in ctx.acc_paragraphs]
    joined = "\n\n".join(parts)
    end_line = ctx.acc_paragraphs[-1][0] + len(ctx.acc_paragraphs[-1][1]) - 1
    _emit_chunk(ctx, ctx.overlap_prefix + joined, ctx.acc_start, end_line)
    ctx.acc_paragraphs = []
    ctx.acc_tokens = 0.0


def chunk_text(
    text: str,
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    """Split text into overlapping chunks using token estimation (len/4).

    Split priority: paragraph boundaries > line boundaries > character split.
    Overlap prepends the last `overlap_tokens` tokens of the previous chunk.
    """
    text = text.replace("\r\n", "\n")

    if text == "":
        return [Chunk(index=0, content="", start_line=0, end_line=0)]

    if overlap_tokens >= chunk_tokens:
        overlap_tokens = chunk_tokens // 4

    lines = text.split("\n")

    if len(text) / 4 <= chunk_tokens:
        return [Chunk(index=0, content=text, start_line=0, end_line=max(0, len(lines) - 1))]

    ctx = _ChunkCtx(chunk_tokens=chunk_tokens, overlap_chars=overlap_tokens * 4)
    paragraphs = _build_paragraphs(lines)

    for para_start, para_lines in paragraphs:
        para_text = "\n".join(para_lines)
        para_tokens = len(para_text) / 4

        if para_tokens > chunk_tokens:
            _flush_acc(ctx)
            ctx.acc_start = para_start
            _split_para_into_chunks(ctx, para_start, para_lines)
            continue

        if ctx.acc_tokens + para_tokens > chunk_tokens and ctx.acc_paragraphs:
            _flush_acc(ctx)
            ctx.acc_start = para_start
            ctx.acc_tokens = 0.0

        if not ctx.acc_paragraphs:
            ctx.acc_start = para_start
        ctx.acc_paragraphs.append((para_start, para_lines))
        ctx.acc_tokens += para_tokens

    _flush_acc(ctx)
    return ctx.chunks
