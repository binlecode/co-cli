"""Prompt assembly for the Co CLI agent.

Static instruction scaffold assembly lives here: soul scaffold, mindsets, and behavioral
rules. Runtime-only layers such as date and conditional safety warnings are added later
via ``@agent.instructions`` in ``co_cli/agent/core.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.config.llm import ModelProfile

if TYPE_CHECKING:
    from co_cli.config.core import Settings

_RULES_DIR = Path(__file__).parent / "rules"

_OVERLAYS_DIR = Path(__file__).parent / "overlays"

_RULE_FILENAME_RE = re.compile(r"^(?P<order>\d{2})_(?P<rule_id>[a-z0-9_]+)\.md$")


def _collect_rule_files() -> list[tuple[int, str, Path]]:
    """Load and validate numbered rule filenames.

    Contract:
    - Filename format: ``NN_rule_id.md`` (e.g. ``01_interaction.md``)
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


def build_rules_block() -> str:
    """Assemble the behavioral rules into one block, in strict numbered order.

    The numbered ``NN_rule_id.md`` files under ``rules/``, each stripped and
    joined with blank lines in order. This is the **base** — the shared
    intersection rule set, profile-agnostic. Public so callers that need the
    rules independently of soul/mindset assembly — prompt-ablation evals composing
    ``seed + <varied mindsets> + rules`` — reuse the exact production rule text
    without importing the private ``_collect_rule_files`` or duplicating the walk.

    Per-profile divergence is append-only and lives entirely in
    :func:`build_profile_overlay`; nothing here filters or removes base content.
    """
    rule_parts: list[str] = []
    for _order, _name, rule_path in _collect_rule_files():
        content = rule_path.read_text(encoding="utf-8").strip()
        if content:
            rule_parts.append(content)
    return "\n\n".join(rule_parts)


def build_profile_overlay(profile: ModelProfile) -> str | None:
    """Return the append-only prompt overlay for ``profile``, or ``None``.

    One file per profile: ``overlays/<profile>.md`` (e.g. ``overlays/frontier.md``).
    Absent or empty → ``None`` → nothing is appended. Append-only by construction:
    this reads and returns a profile's own overlay file and never touches the base,
    so there is no expressible path by which an overlay removes or filters base
    content. The composed prompt is always ``base + overlay(profile)``.
    """
    overlay_path = _OVERLAYS_DIR / f"{profile.value}.md"
    if not overlay_path.is_file():
        return None
    content = overlay_path.read_text(encoding="utf-8").strip()
    return content or None


def build_base_instructions(config: Settings) -> str:
    """Build the base instructions (seed + mindsets + rules) for model and personality.

    This is the **base** layer — one of three static-instruction builders the
    orchestrator joins into the cached prefix, alongside ``_toolset_guidance_provider``
    and ``_personality_critique_provider``. It does not build the whole static literal.

    Assembles sections in explicit order:
    1. Soul seed (identity anchor)
    2. Mindsets
    3. Behavioral rules (numbered, strict order)

    Canon and critique are NOT injected here — canon is indexed at bootstrap under
    ``source='canon'`` for personality-system use only (no model-callable read path,
    no runtime consumer wired up yet); critique is appended in ``core.py`` after
    operational guidance.

    Returns the fully assembled base instructions string.
    """
    parts: list[str] = []

    seed: str | None = None
    mindsets: str | None = None

    if config.personality:
        from co_cli.personality.prompts.loader import (
            load_soul_mindsets,
            load_soul_seed,
        )

        seed = load_soul_seed(config.personality)
        mindsets = load_soul_mindsets(config.personality) or None

    # 1. Soul seed — identity declaration, always first
    if seed:
        parts.append(seed)

    # 2. Mindsets
    if mindsets:
        parts.append(mindsets)

    # 3. Behavioral rules (strict numbered order, profile-agnostic base)
    rules_block = build_rules_block()
    if rules_block:
        parts.append(rules_block)

    prompt = "\n\n".join(parts)

    if not prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    return prompt
