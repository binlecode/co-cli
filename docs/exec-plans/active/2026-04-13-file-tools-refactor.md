# TODO: File Tools Refactor

**Slug:** `file-tools-refactor`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Gap analysis against peers (fork-claude-code, aider, goose) performed 2026-04-11.
Source: `co_cli/tools/files.py` (343 lines, 5 tools).

Three classes of gap, ordered by risk:

| Gap | Risk | Peer signal |
|-----|------|-------------|
| No staleness check before write/edit | Data loss — agent clobbers concurrent edits | fork-claude-code: mtime + content check before any mutation |
| No file size guard in `edit_file` | OOM on large files | fork-claude-code: 1 GiB hard cap |
| Hardcoded UTF-8 in read/write/edit | Silent corruption on UTF-16 / latin-1 files | fork-claude-code: BOM detection + encoding preservation |

**Current-state notes (working tree has a partial implementation):**
- **Staleness guard (TASK-1)**: fully implemented. `file_read_mtimes: dict[str, float]` is on `CoDeps` (`deps.py:158`, `default_factory=dict`). `read_file` records mtime (`:173`). `write_file` checks + updates mtime (`:284–292`). `edit_file` checks before lock + updates after write (`:339–343`, `:367`). `make_subagent_deps` shares the dict by reference (`:224`). **Tests are absent** — all TASK-1 work is tests-only.
- **Size guard (TASK-2a)**: fully implemented. `_MAX_EDIT_BYTES = 10 * 1024 * 1024` at `files.py:31`. Guard in `edit_file` at `:349–352`. **Tests absent** — TASK-2a is tests-only.
- **Encoding detection (TASK-2b)**: `_detect_encoding` exists at `files.py:42–47` but has a correctness bug: `path.read_bytes()[:2048]` loads the entire file before slicing (`:44`). `read_file` uses it (`:168`). `edit_file` reads with detected encoding (`:353`) but writes back hardcoded `encoding="utf-8"` (`:366`) — silently corrupts UTF-16 files. Two code fixes + tests remain.
- `deps.py:158` uses `field(default_factory=dict, repr=False)` — this is correct and sufficient; `make_subagent_deps` passes the dict explicitly, sharing works. No structural change needed to `deps.py`.

**Revised delivery scope:** Fix 2 bugs in `files.py`; write tests for all three features.

Out of scope: multi-format reads (images, PDFs, Jupyter) — new deps, separate ticket.
Out of scope (deferred): `find_in_files` context lines — usability improvement, no safety failure mode.

---

## Problem & Outcome

**Problem:** `write_file` and `edit_file` mutate files without checking whether the file changed since the agent last read it. If something else modifies the file between the read and write, the agent silently clobbers the change.
**Failure cost:** Silent data loss — user edits, concurrent tool writes, or subagent modifications are overwritten with no warning.

**Problem:** `edit_file` reads entire file contents with no size bound.
**Failure cost:** OOM crash on large files; agent cannot recover gracefully.

**Problem:** `read_file` and `edit_file` hardcode UTF-8 and crash with a generic error on UTF-16 files.
**Failure cost:** Agent cannot read or edit legitimate UTF-16 source files (e.g. Windows-generated code); user gets a confusing "binary file" error.

**Outcome:** After this delivery, the agent detects stale writes before they happen, fails fast on oversized edits, and reads UTF-16 files without error.

---

## Scope

In scope:
- Staleness guard on `write_file` and `edit_file` (mtime-based, session-scoped).
- File size hard block on `edit_file` (10 MB threshold).
- BOM-based encoding detection on `read_file` and `edit_file` (UTF-16 only; UTF-8 default).
- `make_subagent_deps` updated to share `file_read_mtimes` by reference.
- Tests for all new behavior.

Out of scope: `write_file` encoding preservation, cross-process staleness detection, soft-warn paths, `find_in_files` encoding detection.

`read_file` size guard: explicitly out of scope. `read_file` is read-only (no mutation risk) and accepts `start_line`/`end_line` parameters that bound memory use for large files. The OOM risk profile differs from `edit_file`, which unconditionally loads the full file for search/replace. A `read_file` size guard belongs in a separate usability ticket if evidence of real failures emerges.

---

## Behavioral Constraints

- The staleness check is advisory (session-scoped, not cross-process). It protects against within-session races only.
- New-file writes (path did not exist at read time) skip the staleness check entirely — the key will not be in `file_read_mtimes`.
- After a successful `write_file`, update `file_read_mtimes` to the new mtime so a second write in the same turn does not trigger a false-positive stale error.
- `write_file` always writes UTF-8 — no encoding preservation. No concrete use case for co-cli rewriting files in other encodings.
- The staleness check on `edit_file` runs before acquiring the resource lock (fail-fast, no lock held on error).
- `_detect_encoding` reads only a 2048-byte prefix — no full file load for detection.

---

## High-Level Design

### Staleness guard

`file_read_mtimes: dict[str, float]` is on `CoDeps` directly, shared by reference in `make_subagent_deps`. Not on `CoSessionState` — must be fully shared across parent and all subagents, not partially inherited.

```
CoDeps field declaration (already in deps.py:158):
  file_read_mtimes: dict[str, float] = field(default_factory=dict, repr=False)

make_subagent_deps (already in deps.py:224):
  file_read_mtimes=base.file_read_mtimes   # shared by reference

read_file (on success):
  file_read_mtimes[str(resolved)] = resolved.stat().st_mtime

write_file:
  async with ctx.deps.resource_locks.try_acquire(str(resolved)):
    # staleness check — existing files only
    if str(resolved) in file_read_mtimes:
      if resolved.stat().st_mtime != file_read_mtimes[str(resolved)]:
        return tool_error("File changed since last read — re-read before writing")
    resolved.write_text(content, encoding="utf-8")
    file_read_mtimes[str(resolved)] = resolved.stat().st_mtime   # update after write

edit_file:
  # staleness check — before acquiring lock (fail-fast)
  if str(resolved) in file_read_mtimes:
    if resolved.stat().st_mtime != file_read_mtimes[str(resolved)]:
      return tool_error("File changed since last read — re-read before writing")
  async with ctx.deps.resource_locks.try_acquire(str(resolved)):
    enc = _detect_encoding(resolved)
    content = resolved.read_text(encoding=enc)
    ... apply replacement ...
    resolved.write_text(updated, encoding=enc)   # preserve original encoding
    file_read_mtimes[str(resolved)] = resolved.stat().st_mtime   # update after write
```

### Size guard

Module-level constant in `files.py`. One hard block only — no soft-warn path.

```
_MAX_EDIT_BYTES = 10 * 1024 * 1024  # 10 MB

edit_file (inside lock, before read):
  if resolved.stat().st_size > _MAX_EDIT_BYTES:
    return tool_error(f"File too large to edit in-place ({resolved.stat().st_size // 1024} KB) — use shell tools")
```

### Encoding detection

Module-level helper. BOM detection only — UTF-16 files use UTF-16, everything else uses UTF-8.

```
_detect_encoding(path: Path) -> str:
  with open(path, 'rb') as fh:
    raw = fh.read(2048)   # bounded read — never loads the full file
  if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
    return "utf-16"
  return "utf-8"

read_file:
  enc = _detect_encoding(resolved)
  content = resolved.read_text(encoding=enc)
  # UnicodeDecodeError → binary-file error (existing behaviour, unchanged)

edit_file:
  enc = _detect_encoding(resolved)
  content = resolved.read_text(encoding=enc)
  updated = content.replace(search, replacement)
  resolved.write_text(updated, encoding=enc)   # preserve encoding on write-back
  # UnicodeDecodeError → binary-file error (existing behaviour, unchanged)
```

---

## Implementation Plan

### ✓ DONE — TASK-1: Staleness guard

**files:** `co_cli/deps.py`, `co_cli/tools/files.py`, `tests/test_tools_files.py`

**Working tree state:** Fully implemented in `files.py` and `deps.py`. This task is **tests-only** — no production code changes needed.

Verify before writing tests:
- `deps.py:158`: `file_read_mtimes: dict[str, float] = field(default_factory=dict, repr=False)` ✓
- `deps.py:224`: `file_read_mtimes=base.file_read_mtimes` in `make_subagent_deps` ✓  
- `files.py:173`: `read_file` records mtime on success ✓
- `files.py:284–292`: `write_file` checks mtime inside lock, updates on success ✓
- `files.py:339–343`: `edit_file` checks mtime before lock, updates on success ✓

`done_when:` `uv run pytest tests/test_tools_files.py -k "staleness or records_mtime" -x` passes, covering:
- `test_read_file_records_mtime`: call `read_file` on a real file, assert `ctx.deps.file_read_mtimes[str(resolved)]` equals the file's actual `st_mtime`.
- `test_write_file_stale_blocked`: read file, set `ctx.deps.file_read_mtimes[str(resolved)] = 0.0` to simulate staleness, attempt `write_file` → `result.metadata["error"] is True` and message contains "changed since last read".
- `test_edit_file_stale_blocked`: same for `edit_file`.
- `test_write_file_new_file_skips_staleness`: write to a path that was never read → no error (key not in `file_read_mtimes`).
- `test_write_file_mtime_updated_after_write`: read → `write_file` (success) → `write_file` again (success, no stale error).

`success_signal:` When the agent reads a file, another tool call modifies it, and the agent tries to write — it receives an error message "File changed since last read" instead of silently overwriting.

---

### ✓ DONE — TASK-2: File size guard + encoding detection

**files:** `co_cli/tools/files.py`, `tests/test_tools_files.py`

#### 2a — File size guard

**Working tree state:** Fully implemented. `_MAX_EDIT_BYTES = 10 * 1024 * 1024` at `files.py:31`. Guard in `edit_file` at `:349–352`. This sub-task is **tests-only**.

Guard conditions documented for reference:
- `write_file` has no size limit (intentional — agent supplies all content; no full read required).
- `edit_file` has a 10 MB hard block (intentional — full file read required for search/replace).

#### 2b — Encoding detection (2 code fixes + tests)

**Working tree state:** Partially implemented with 2 bugs.

**Fix 1 — `_detect_encoding` bounded read (`files.py:44`):**
Replace `raw = path.read_bytes()[:2048]` with:
```python
with open(path, "rb") as fh:
    raw = fh.read(2048)
```
`Path.read_bytes()` loads the entire file before slicing — defeating the purpose of the 2048-byte prefix for large files.

**Fix 2 — `edit_file` encoding-preserving write-back (`files.py:366`):**
Replace `resolved.write_text(updated, encoding="utf-8")` with `resolved.write_text(updated, encoding=enc)`.
`enc` is already bound from `_detect_encoding(resolved)` at `:353`.

`done_when:` `uv run pytest tests/test_tools_files.py -k "size or encoding or utf16" -x` passes, covering:
- `test_edit_file_size_guard`: create a file of exactly `_MAX_EDIT_BYTES + 1` bytes in `tmp_path`, attempt `edit_file` → `result.metadata["error"] is True` and message contains "too large".
- `test_read_file_utf16le`: write a UTF-16LE file via `path.write_bytes(b'\xff\xfe' + "hello utf16".encode("utf-16-le"))`, attempt `read_file` → success and "hello utf16" in `result.return_value`.
- `test_edit_file_utf16`: write a UTF-16 file via `path.write_bytes(b'\xff\xfe' + original.encode("utf-16-le"))`, attempt `edit_file` → success, updated file is valid UTF-16 (re-readable with correct content), not UTF-8.

`success_signal:` Agent can read and edit UTF-16 files without a "binary file" error; attempting to edit a file larger than 10 MB returns a clear error rather than an OOM crash.

---

## Testing

All tests live in `tests/test_tools_files.py`. No mocks. All tests use `tmp_path` for filesystem isolation. No fakes, no `monkeypatch`, no `unittest.mock`.

Staleness simulation: set `ctx.deps.file_read_mtimes[str(resolved)] = 0.0` directly on the real `CoDeps` object (not a mock — just state manipulation).

Size guard test: write a real file larger than 10 MB into `tmp_path`. For CI speed, write exactly `_MAX_EDIT_BYTES + 1` bytes.

UTF-16 test: use stdlib `Path.write_bytes` with BOM prefix (`b'\xff\xfe'`) + UTF-16LE payload so the file is a real UTF-16LE file, not a mocked object.

---

## Open Questions

None — all questions answerable by inspection of existing source.

---

## Delivery order

1. TASK-1 (staleness guard) — safety, affects write/edit correctness
2. TASK-2 (size + encoding) — safety, affects read/edit correctness

Both tasks are independently shippable. No prerequisites between them — they touch different concerns in the same file and can be implemented sequentially or batched into a single commit.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev file-tools-refactor`

---

## Delivery Summary — 2026-04-11

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_tools_files.py -k "staleness" -x` passes (4 tests) | ✓ pass |
| TASK-2 | `uv run pytest tests/test_tools_files.py -k "size or encoding or utf16" -x` passes (3 tests) | ✓ pass |

**Tests:** full suite — 389 passed, 0 failed
**Independent Review:** clean / 0 blocking / 5 minor
**Doc Sync:** fixed (DESIGN-tools.md — tool reference table updated; Concurrency Safety section: `write_file` added to lock table, `file_read_mtimes` staleness guard documented)

**Overall: DELIVERED**
Both safety features shipped: session-scoped staleness guard on `write_file`/`edit_file`, 10 MB hard block on `edit_file`, and BOM-based UTF-16 encoding detection on `read_file`/`edit_file`.

---

## Implementation Review — 2026-04-11

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `pytest -k "staleness or records_mtime"` — 5 tests | ✓ pass | `deps.py:158` field; `deps.py:224` shared by reference; `files.py:174` mtime recorded on read; `files.py:284–289` check inside lock; `files.py:293` update after write; `files.py:339–344` check before lock in edit; `files.py:368` update after edit |
| TASK-2 | `pytest -k "size or encoding or utf16"` — 3 tests | ✓ pass | `files.py:31` constant; `files.py:350–353` size guard inside lock; `files.py:44–48` bounded read in `_detect_encoding`; `files.py:169` read uses detected enc; `files.py:354` edit reads with enc; `files.py:367` edit writes with `encoding=enc` |

### Issues Found & Fixed
No issues found.

### Tests
- TASK-1: `uv run pytest tests/test_tools_files.py -k "staleness or records_mtime" -x` → 5 passed
- TASK-2: `uv run pytest tests/test_tools_files.py -k "size or encoding or utf16" -x` → 3 passed
- Full suite: `uv run pytest -v` → **390 passed, 0 failed**
- Log: `.pytest-logs/*-review-impl.log`

### Doc Sync
- Scope: narrow — `docs/DESIGN-tools.md` only
- Result: clean — Concurrency Safety section (`:216`) accurately documents `file_read_mtimes` staleness guard; tool reference table (`:293–296`) documents BOM detection and staleness guard on `read_file`, `write_file`, `edit_file`

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components nominal, system starts without error
- `success_signal` (TASK-1): staleness guard confirmed in source — `write_file`/`edit_file` return `tool_error("File changed since last read — re-read before writing")` on mtime mismatch (`files.py:289`, `:344`)
- `success_signal` (TASK-2): size guard and encoding confirmed — `edit_file` returns "too large" error at `files.py:351`; `_detect_encoding` uses bounded read and `edit_file` preserves encoding on write-back at `files.py:367`

### Overall: PASS
All spec requirements confirmed with file:line evidence, 390 tests green, quality gate clean, doc sync accurate. Ship-ready.
