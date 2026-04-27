"""Prompt assembly for the Co CLI agent.

Static instruction scaffold assembly lives here: soul scaffold, character memories,
mindsets, personality-context knowledge artifacts, behavioral rules, and examples.
Runtime-only layers such as date and conditional safety warnings are added later
via ``@agent.instructions`` in ``agent/_core.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.context.compaction import COMPACTABLE_KEEP_RECENT

if TYPE_CHECKING:
    from co_cli.config._core import Settings

_PROMPTS_DIR = Path(__file__).parent
_RULES_DIR = _PROMPTS_DIR / "rules"

_RULE_FILENAME_RE = re.compile(r"^(?P<order>\d{2})_(?P<rule_id>[a-z0-9_]+)\.md$")


RECENCY_CLEARING_ADVISORY = (
    "## Tool result recency\n\n"
    "Tool results may be automatically cleared from context to free space. "
    f"The {COMPACTABLE_KEEP_RECENT} most recent results per tool type are always kept. "
    "Note important information from tool results in your response — "
    "the original output may be cleared on later turns."
)
"""Static, cacheable advisory describing the ``[tool result cleared…]`` placeholders.

Built once at module-load time from ``COMPACTABLE_KEEP_RECENT`` (static per
process). Gets injected verbatim into the static instruction scaffold by
``build_static_instructions`` so it lives in the cacheable prefix — no
per-turn interpolation, no dynamic gating. Borrowed pattern from
``fork-claude-code`` (Gap G fix).
"""


def _collect_rule_files() -> list[tuple[int, str, Path]]:
    """Load and validate numbered rule filenames.

    Contract:
    - Filename format: ``NN_rule_id.md`` (e.g. ``01_identity.md``)
    - Numeric prefixes must be unique and contiguous from 01
    """
    rule_paths = sorted(_RULES_DIR.glob("*.md"))
    if not rule_paths:
        raise ValueError(f"No rule files found in {_RULES_DIR}")

    parsed: list[tuple[int, str, Path]] = []
    invalid_names: list[str] = []
    for path in rule_paths:
        match = _RULE_FILENAME_RE.fullmatch(path.name)
        if not match:
            invalid_names.append(path.name)
            continue
        parsed.append((int(match.group("order")), match.group("rule_id"), path))

    if invalid_names:
        invalid_sorted = ", ".join(sorted(invalid_names))
        raise ValueError(
            f"Invalid rule filename(s): {invalid_sorted}. Expected format: NN_rule_id.md"
        )

    order_counts: dict[int, int] = {}
    for order, _rule_id, _path in parsed:
        order_counts[order] = order_counts.get(order, 0) + 1
    duplicates = sorted(order for order, count in order_counts.items() if count > 1)
    if duplicates:
        duplicate_str = ", ".join(f"{n:02d}" for n in duplicates)
        raise ValueError(f"Duplicate rule order prefix(es): {duplicate_str}")

    parsed.sort(key=lambda item: item[0])
    orders = [order for order, _rule_id, _path in parsed]
    expected = list(range(1, len(parsed) + 1))
    if orders != expected:
        found = ", ".join(f"{n:02d}" for n in orders)
        raise ValueError(f"Rule order prefixes must be contiguous starting at 01. Found: {found}")

    return parsed


def build_static_instructions(config: Settings, *, knowledge_dir: Path | None = None) -> str:
    """Build the static instructions string for the given model and personality.

    Assembles sections in explicit order:
    1. Soul seed (identity anchor)
    2. Character memories
    3. Mindsets
    4. Behavioral rules (numbered, strict order)
    4b. Recency-clearing advisory (explains the ``[tool result cleared…]``
        placeholders that appear after ``truncate_tool_results`` runs)
    5. Soul examples
    6. Critique (self-assessment lens)

    Returns the fully assembled static instructions string.
    """
    parts: list[str] = []

    seed: str | None = None
    character_memories: str | None = None
    mindsets: str | None = None
    examples: str | None = None
    critique: str | None = None

    if config.personality:
        from co_cli.prompts.personalities._loader import (
            load_character_memories,
            load_personality_memories,
            load_soul_critique,
            load_soul_examples,
            load_soul_mindsets,
            load_soul_seed,
        )

        seed = load_soul_seed(config.personality)
        character_memories = load_character_memories(config.personality) or None
        mindsets = load_soul_mindsets(config.personality) or None
        examples = load_soul_examples(config.personality) or None
        critique = load_soul_critique(config.personality) or None

    # 1. Soul seed — identity declaration, always first
    if seed:
        parts.append(seed)

    # 2. Character memories
    if character_memories:
        parts.append(character_memories)

    # 3. Mindsets
    if mindsets:
        parts.append(mindsets)

    # 3b. Personality memories — curated knowledge artifacts tagged personality-context;
    # session-stable, loaded once at agent construction for prefix-cache stability.
    if config.personality:
        personality_memories_content = load_personality_memories(knowledge_dir=knowledge_dir)
        if personality_memories_content:
            parts.append(personality_memories_content)

    # 4. Behavioral rules (strict numbered order)
    for _order, _name, rule_path in _collect_rule_files():
        content = rule_path.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)

    # 4b. Recency-clearing advisory — static, cacheable; explains the
    # ``[tool result cleared…]`` placeholders the model will encounter.
    parts.append(RECENCY_CLEARING_ADVISORY)

    # 5. Soul examples — concrete trigger→response patterns, trailing rules
    if examples:
        parts.append(examples)

    # 6. Critique — self-assessment lens, always last
    if critique:
        parts.append(f"## Review lens\n\n{critique}")

    prompt = "\n\n".join(parts)

    if not prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    return prompt
