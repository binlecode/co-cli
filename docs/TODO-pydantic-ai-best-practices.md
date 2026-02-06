# TODO: Pydantic AI Best Practices Refactoring

**Status:** In Progress (Batches 1-4 complete)
**Goal:** Refactor `co-cli` to serve as a reference implementation of pydantic-ai production patterns.
**Approach:** Vertical slices - each batch is independently verifiable.

**Reference:**
- Best Practices: `docs/PYDANTIC-AI-CLI-BEST-PRACTICES.md`
- Local SDK: `/Users/binle/workspace_genai/pydantic-ai`
- Key Examples: `bank_support.py`, `rag.py`, `weather_agent.py`

---

## Completed

| Batch | Focus | Design Doc |
|-------|-------|------------|
| 1 | Shell tool + CoDeps foundation (`deps.py`, `RunContext`, `create_deps()`, `google-gla:` model string) | `DESIGN-tool-shell-sandbox.md`, `DESIGN-co-cli.md` |
| 2 | Obsidian tools (`ModelRetry`, `obsidian_vault_path` in deps, `search_notes`) | `DESIGN-tool-obsidian.md`, `DESIGN-co-cli.md` |
| 3-4 | Google tools + Slack (`google_auth.py`, Drive/Gmail/Calendar/Slack → `RunContext`, `comm.py` deleted) | `DESIGN-tool-google.md`, `DESIGN-tool-slack.md`, `DESIGN-co-cli.md` |

---

## Remaining Gap Analysis

| Category | Current | Best Practice | Severity |
|----------|---------|---------------|----------|
| **Testing** | Real services only | `TestModel` for unit tests | 🟡 Medium |
| **Tool Approval** | `rich.prompt.Confirm` inside tools | `requires_approval=True` + `DeferredToolRequests` | 🔵 Deferred (Batch 6) |

---

## Batch 5: Cleanup & Polish

**Goal:** Remove legacy patterns, add tests.

### 5.1 Update tools/__init__.py

```python
"""Agent tools - external integrations."""
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.google_drive import search_drive, read_drive_file
from co_cli.tools.slack import post_slack_message
from co_cli.tools.google_gmail import draft_email
from co_cli.tools.google_calendar import list_calendar_events

__all__ = [
    "run_shell_command",
    "search_notes", "list_notes", "read_note",
    "search_drive", "read_drive_file",
    "post_slack_message",
    "draft_email",
    "list_calendar_events",
]
```

### 5.2 Add Unit Test

```python
# tests/test_agent_unit.py
import pytest
from pydantic_ai.models.test import TestModel
from co_cli.agent import get_agent
from co_cli.deps import CoDeps
from co_cli.sandbox import Sandbox

@pytest.fixture
def test_deps(tmp_path):
    return CoDeps(
        sandbox=Sandbox(),
        obsidian_vault_path=str(tmp_path),
        session_id="test",
    )

@pytest.mark.asyncio
async def test_agent_responds(test_deps):
    agent = get_agent()
    with agent.override(model=TestModel()):
        result = await agent.run("Hello", deps=test_deps)
        assert result.output
```

### Checklist

- [ ] Remove all `tool_plain()` calls (should all be `agent.tool()`)
- [ ] Remove global `settings` imports from all tools
- [ ] Update `tools/__init__.py` with clean exports
- [ ] Add basic unit test with `TestModel`
- [ ] Run full test suite

### Verification

```bash
# Run all tests
uv run pytest -v

# Manual verification
uv run python -m co_cli.main chat
# Test all tool categories work
```

---

## Post-Migration Structure

```
co_cli/
├── deps.py              # CoDeps dataclass
├── agent.py             # Agent + tool registration
├── config.py            # Settings (unchanged)
├── sandbox.py           # Sandbox class (unchanged)
├── main.py              # CLI + create_deps()
└── tools/
    ├── __init__.py      # Re-exports
    ├── _google.py       # Shared Google auth
    ├── shell.py         # run_shell_command
    ├── obsidian.py      # search_notes, list_notes, read_note
    ├── drive.py         # search_drive, read_drive_file
    ├── slack.py         # post_slack_message
    ├── gmail.py         # draft_email
    └── calendar.py      # list_calendar_events
```

---

## Future: Batch 6 (Deferred)

Migrate from `typer.confirm()` / `rich.prompt.Confirm` inside tools to pydantic-ai's native `requires_approval=True` + `DeferredToolRequests` pattern. Requires significant chat loop changes — deferred until the deps pattern is proven across all tools.

---

## Reference

- Best Practices: `docs/PYDANTIC-AI-CLI-BEST-PRACTICES.md`
- Pydantic AI Docs: https://ai.pydantic.dev
- Deferred Tools: https://ai.pydantic.dev/deferred-tools/
