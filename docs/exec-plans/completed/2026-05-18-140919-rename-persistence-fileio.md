# rename-persistence-fileio

## Problem

`co_cli/persistence/` is a cross-cutting infrastructure module whose only content is
`atomic.py` — two functions that write a file atomically via tempfile + `os.replace`.
The name `persistence` overstates scope (implies storage, state management).
`fileio` is accurate and unambiguous.

## Status

✓ DONE

## Scope

- Rename package directory: `co_cli/persistence/` → `co_cli/fileio/`
- Update import path in `__init__.py` docstring (module identity)
- Update 7 source import sites
- Update 1 test import site
- Rename test file to drop the stale `_persistence` suffix

No spec or doc changes needed — doc references to "persistence" in `docs/specs/`
point to `co_cli/session/persistence.py`, a separate module.

## Tasks

### T1 — Rename package ✓ DONE

- `git mv co_cli/persistence co_cli/fileio`

### T2 — Update source imports (7 sites) ✓ DONE

| File | Old import | New import |
|------|-----------|-----------|
| `co_cli/tools/tool_io.py` | `from co_cli.persistence.atomic import atomic_write_text` | `from co_cli.fileio.atomic import atomic_write_text` |
| `co_cli/tools/system/skills.py` | `from co_cli.persistence.atomic import atomic_write_text` | `from co_cli.fileio.atomic import atomic_write_text` |
| `co_cli/memory/service.py` | `from co_cli.persistence.atomic import atomic_write_text` | `from co_cli.fileio.atomic import atomic_write_text` |
| `co_cli/memory/dream.py` | `from co_cli.persistence.atomic import atomic_write_text` | `from co_cli.fileio.atomic import atomic_write_text` |
| `co_cli/skills/session_review.py` | `from co_cli.persistence.atomic import atomic_write_text` | `from co_cli.fileio.atomic import atomic_write_text` |
| `co_cli/skills/usage.py` | `from co_cli.persistence.atomic import atomic_write_text` | `from co_cli.fileio.atomic import atomic_write_text` |
| `co_cli/skills/curator.py` | `from co_cli.persistence.atomic import atomic_write_text` | `from co_cli.fileio.atomic import atomic_write_text` |
| `agent_docs/code-conventions.md` | `co_cli.persistence.atomic.atomic_write_text` | `co_cli.fileio.atomic.atomic_write_text` |
| `co_cli/tools/files/write.py` | `co_cli.persistence.atomic.atomic_write_text` | `co_cli.fileio.atomic.atomic_write_text` |

### T3 — Update test import + rename test file ✓ DONE

- `git mv tests/test_atomic_write_persistence.py tests/test_atomic_write.py`
- Update import in that file: `from co_cli.persistence.atomic` → `from co_cli.fileio.atomic`
- Update module docstring to reflect new path

### T4 — Quality gate ✓ DONE

```bash
scripts/quality-gate.sh full
```
