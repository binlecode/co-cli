"""Static instructions must not contain the ## Character block.

Regression guard for the canon-channel refactor: character memories are surfaced
on demand via memory_search rather than statically injected into the system prompt.
"""

from pathlib import Path

import pytest
from tests._settings import make_settings

from co_cli.context.assembly import build_static_instructions
from co_cli.tools.memory._canon_recall import _SOULS_DIR


@pytest.mark.parametrize("role", ["tars", "finch", "jeff"])
def test_no_character_block_in_static_instructions(role: str, tmp_path: Path) -> None:
    cfg = make_settings(personality=role)
    prompt = build_static_instructions(cfg, knowledge_dir=tmp_path / "knowledge")

    assert "## Character\n" not in prompt, (
        f"## Character block must not appear in static instructions for role={role}"
    )

    memories_dir = _SOULS_DIR / role / "memories"
    sample = next(memories_dir.glob("*.md"), None)
    if sample is not None:
        raw = sample.read_text(encoding="utf-8")
        body = raw.split("---\n", 2)[-1].strip().split("\n")[0]
        if body:
            assert body not in prompt, (
                f"Memory file body from {sample.name} must not appear in static instructions "
                f"for role={role}"
            )
