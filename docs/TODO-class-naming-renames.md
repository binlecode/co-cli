# TODO: class naming renames — align with updated CLAUDE.md rule

Two classes violate the updated naming rule (suffix audit + CLAUDE.md update 2026-04-10):
- `LlmModel` — read-only config/info container; should be `*Info`
- `ShellBackend` — concrete executor, not a pluggable abstraction; should be `ShellExecutor`

(`ToolRegistry` retained — fits `*Registry` as a registration lookup table built during bootstrap.)

---

### TASK-1: Rename `LlmModel` → `LlmModelInfo`

**files:**
- `co_cli/_model_factory.py`
- `co_cli/deps.py`
- `co_cli/agent.py`
- `co_cli/context/summarization.py` (docstring only)

**done_when:** `grep -r "LlmModel" co_cli/ --include="*.py"` returns zero results

**notes:** `deps.py` imports it under `TYPE_CHECKING`; `agent.py` uses it as a type annotation in `build_agent()`. No test files import it directly.

---

### TASK-2: Rename `ShellBackend` → `ShellExecutor`

**files:**
- `co_cli/tools/shell_backend.py`
- `co_cli/deps.py`
- `co_cli/bootstrap/core.py`
- `tests/` (bulk — ~20 test files import `ShellBackend` directly)

**done_when:** `grep -r "ShellBackend" co_cli/ tests/ --include="*.py"` returns zero results

**notes:** Highest blast radius of the two. Every test fixture that builds `CoDeps` instantiates `ShellBackend()` directly. The module filename `shell_backend.py` should stay as-is (only the class name changes). `deps.py` imports it under `TYPE_CHECKING`.
