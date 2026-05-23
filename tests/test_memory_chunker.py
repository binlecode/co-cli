"""Unit tests for memory chunker structure-aware splitting.

Covers the two enhancements beyond paragraph/line splitting:
- Sentence-level fallback for over-budget single-line paragraphs.
- ATX heading (`^#{1,6}\\s`) as a hard section boundary with overlap suppression.
"""

from co_cli.memory.chunker import chunk_text


def test_short_text_returns_single_chunk():
    """Sub-budget text returns one chunk via the short-circuit path."""
    chunks = chunk_text("hello world", chunk_tokens=200, overlap_tokens=20)
    assert len(chunks) == 1
    assert chunks[0].content == "hello world"


def test_sentence_split_on_long_single_line_paragraph():
    """A 4-sentence single-line paragraph splits on sentence boundaries, not mid-word."""
    sentence = (
        "The database connection pool exhausted under load and the request queue "
        "grew without bound until the upstream proxy timed out and shed traffic, "
        "which masked the underlying capacity problem for several hours during the "
        "incident response window. "
    )
    body = (sentence * 4).strip()
    assert "\n" not in body, "test setup must be a single line (no newlines)"

    chunks = chunk_text(body, chunk_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 2, f"expected multiple chunks, got {len(chunks)}"

    for c in chunks:
        last = c.content.rstrip()[-1]
        assert last in ".!?", (
            f"chunk does not end on a sentence terminator (mid-word split?): "
            f"...{c.content[-40:]!r}"
        )


def test_char_split_fallback_when_no_sentence_boundaries():
    """One huge unbroken token falls through sentence-split to char-split."""
    body = "a" * 2000
    chunks = chunk_text(body, chunk_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 2

    chunk_chars = 200 * 4
    overlap_chars = 20 * 4
    for c in chunks:
        assert len(c.content) <= chunk_chars + overlap_chars, (
            f"chunk exceeds budget+overlap: {len(c.content)} chars"
        )


def test_heading_starts_new_chunk_without_prior_section_overlap():
    """`# Heading` forces flush AND suppresses overlap from the prior section."""
    para_a = "Alpha section content. " * 25
    para_b = "Bravo section content. " * 25
    body = f"{para_a}\n\n# Heading B\n\n{para_b}"

    chunks = chunk_text(body, chunk_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 2

    heading_chunks = [c for c in chunks if c.content.startswith("# Heading B")]
    assert len(heading_chunks) == 1, (
        f"exactly one chunk should start with `# Heading B` (no overlap leakage); "
        f"chunk starts: {[c.content[:30] for c in chunks]}"
    )

    for c in chunks:
        if "# Heading B" in c.content:
            assert "Alpha" not in c.content, (
                f"heading-bearing chunk leaks prior-section content: {c.content[:120]!r}"
            )


def test_multi_level_headings_are_boundaries():
    """`##` and `###` also force flush + overlap suppression."""
    body_h2 = f"{'Alpha. ' * 100}\n\n## Subheading\n\n{'Bravo. ' * 100}"
    chunks = chunk_text(body_h2, chunk_tokens=200, overlap_tokens=20)
    h2_starting = [c for c in chunks if c.content.startswith("## Subheading")]
    assert len(h2_starting) == 1, "## should start its own chunk"

    body_h3 = f"{'Alpha. ' * 100}\n\n### Subsubheading\n\n{'Bravo. ' * 100}"
    chunks = chunk_text(body_h3, chunk_tokens=200, overlap_tokens=20)
    h3_starting = [c for c in chunks if c.content.startswith("### Subsubheading")]
    assert len(h3_starting) == 1, "### should start its own chunk"


def test_non_heading_hash_does_not_force_flush():
    """`#hashtag` (no space after `#`) is NOT treated as a heading boundary.

    Compared against a real `# heading`: with heading-rule, the heading chunk
    contains no prior-section content; without (the `#hashtag` case), the
    `#hashtag` token packs with prior `Alpha` content in the same chunk.
    """
    para_a = "Alpha. " * 60
    para_b = "Bravo. " * 60

    body_hashtag = f"{para_a}\n\n#hashtag\n\n{para_b}"
    chunks = chunk_text(body_hashtag, chunk_tokens=200, overlap_tokens=20)
    co_located = [c for c in chunks if "#hashtag" in c.content and "Alpha" in c.content]
    assert co_located, (
        "`#hashtag` should pack into the same chunk as prior Alpha content "
        "(heading rule must not have triggered a flush)"
    )

    body_real = f"{para_a}\n\n# heading\n\n{para_b}"
    chunks_real = chunk_text(body_real, chunk_tokens=200, overlap_tokens=20)
    real_starting = [c for c in chunks_real if c.content.startswith("# heading")]
    assert len(real_starting) == 1, "real `# heading` (with space) should start its own chunk"
    assert "Alpha" not in real_starting[0].content, (
        "control: real heading chunk should NOT contain prior-section content"
    )


def test_chunk_line_numbers_track_paragraph_positions():
    """Chunk start_line / end_line metadata maps to 0-indexed source lines.

    Catches off-by-one and zeroed-out regressions in citation metadata.
    Budget is tight enough that each paragraph emits its own chunk, so the
    expected line-number mapping is deterministic.
    """
    body = "Alpha.\n\nBravo.\n\nCharlie.\n\nDelta.\n\nEcho."
    chunks = chunk_text(body, chunk_tokens=2, overlap_tokens=0)
    assert len(chunks) == 5, f"expected one chunk per paragraph, got {len(chunks)}"
    actual = [(c.start_line, c.end_line) for c in chunks]
    expected = [(0, 0), (2, 2), (4, 4), (6, 6), (8, 8)]
    assert actual == expected, f"line numbers off: expected {expected}, got {actual}"


def test_overlap_applied_within_section():
    """Within a section, chunk N+1 begins with the last `overlap_tokens` of chunk N.

    Catches: overlap mechanism silently broken (overlap_prefix never set,
    overlap_chars miscomputed, prefix not prepended).
    """
    para_a = "Alpha. " * 100
    para_b = "Bravo. " * 100
    body = f"{para_a}\n\n{para_b}"

    chunks = chunk_text(body, chunk_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 2

    overlap_tail = chunks[0].content[-80:]
    assert chunks[1].content.startswith(overlap_tail), (
        f"chunk 1 should start with chunk 0's last 80 chars (overlap_tokens=20 → 80 chars); "
        f"chunk0 tail: {overlap_tail[:40]!r}..., chunk1 head: {chunks[1].content[:40]!r}..."
    )
