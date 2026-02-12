"""Prompt assembly for the Co CLI agent.

Rules-fixed system prompt: instructions + behavioral rules + counter-steering.
Context (personality, knowledge) is loaded on-demand via tools.
"""

import re
from pathlib import Path

from co_cli.prompts._manifest import PromptManifest


_PROMPTS_DIR = Path(__file__).parent
_RULES_DIR = _PROMPTS_DIR / "rules"

_RULE_FILENAME_RE = re.compile(r"^(?P<order>\d{2})_(?P<rule_id>[a-z0-9_]+)\.md$")


def _load_instructions() -> str:
    """Load the bootstrap instructions file."""
    path = _PROMPTS_DIR / "instructions.md"
    if not path.exists():
        raise FileNotFoundError(f"Instructions file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


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
            "Invalid rule filename(s): "
            f"{invalid_sorted}. Expected format: NN_rule_id.md"
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
        raise ValueError(
            "Rule order prefixes must be contiguous starting at 01. "
            f"Found: {found}"
        )

    return parsed


def assemble_prompt(
    provider: str,
    model_name: str | None = None,
) -> tuple[str, PromptManifest]:
    """Assemble rules-only system prompt.

    Assembly order:
    1. Bootstrap instructions (instructions.md)
    2. All behavioral rules (rules/*.md)
    3. Model-specific counter-steering (if quirks exist)

    Context (personality, knowledge) is NOT included — loaded
    on-demand by the agent via context tools.

    Args:
        provider: LLM provider name ("gemini", "ollama").
        model_name: Normalized model identifier for quirk lookup.

    Returns:
        Tuple of (assembled_prompt, manifest).

    Raises:
        FileNotFoundError: If instructions or rule files are missing.
        ValueError: If assembled prompt is empty.
    """
    manifest = PromptManifest()
    parts: list[str] = []

    # 1. Bootstrap instructions
    instructions = _load_instructions()
    parts.append(instructions)
    manifest.parts_loaded.append("instructions")

    # 2. All behavioral rules (strict numbered order)
    for _order, name, rule_path in _collect_rule_files():
        content = rule_path.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
            manifest.parts_loaded.append(name)

    # 3. Counter-steering (model-specific quirks)
    if model_name:
        from co_cli.prompts.model_quirks import get_counter_steering

        counter_steering = get_counter_steering(provider, model_name)
        if counter_steering:
            parts.append(f"## Model-Specific Guidance\n\n{counter_steering}")
            manifest.parts_loaded.append("counter_steering")

    prompt = "\n\n".join(parts)

    if not prompt.strip():
        raise ValueError("Assembled prompt is empty after processing")

    manifest.total_chars = len(prompt)
    return prompt, manifest
