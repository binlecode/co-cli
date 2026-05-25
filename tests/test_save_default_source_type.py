"""Default source_type for agent-initiated saves must be 'manual', not 'detected'.

Agent-initiated `memory_manage(create)` lands here when no source_type is passed.
The default reflects the de-facto writer (agent deliberately curating), not the
legacy 'detected' label (background-reviewer pattern detection — no longer used).
"""

from co_cli.memory.item import load_memory_item
from co_cli.memory.service import save_memory_item


def test_save_memory_item_default_source_type_is_manual(tmp_path) -> None:
    memory_dir = tmp_path / "memory"

    result = save_memory_item(
        memory_dir,
        content="a distilled note worth keeping",
        memory_kind="note",
        title="distilled note",
    )

    item = load_memory_item(result.path)
    assert item.source_type == "manual", (
        f"default source_type for agent-initiated save must be 'manual', got {item.source_type!r}"
    )
