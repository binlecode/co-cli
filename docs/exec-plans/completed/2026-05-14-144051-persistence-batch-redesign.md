# Plan — Persistence Primitives + MemoryTransaction Object Redesign

Task type: refactoring

**Scope framing.** Two coordinated structural changes downstream of `2026-05-14-101909-plan3.5c-pre-atomic-writes.md`:

(a) Move `atomic_write_text` out of `co_cli/memory/mutator.py` into a dedicated `co_cli/persistence/` package. It currently lives in `memory/` only because `memory/` is the agreed shared-primitive home — not because the primitive has anything to do with memory. Promote it to a package named for what it does, add `atomic_write_bytes` so the first future binary writer doesn't reinvent, and extend `atomic_write_text` with `errors: str = "strict"` so existing call sites that need lossy encoding (specifically `tool_io.py`) can fold cleanly.

(b) Replace `MemoryStore.transaction()` + `_in_transaction` flag with an explicit `MemoryTransaction` object. Today, `index()`, `index_chunks()`, and `remove()` silently change commit semantics based on a private flag — public API behaviour depends on hidden state. A `MemoryTransaction` returned from `store.transaction()` exposes deferred-commit methods on its own surface; the public `MemoryStore.index()` / `index_chunks()` / `remove()` / `remove_chunks()` always commit. No hidden mode-switching.

Both ship together in a single PR because they share the same boundary discipline: name modules for their responsibility, expose semantics through types not flags. TASK-2 and TASK-3 are parallelizable but the doc-sync rule changes in TASK-4 want both landings co-located so callers see the new norms simultaneously.

## Relationship to other plans

| Plan | Status | Relevance |
|---|---|---|
| 3.5c-pre — atomic-write hygiene | **Shipped** at `6024a9d` (v0.8.190) — "refactor(memory/skills): system-wide atomic write hygiene" | Established the canonical helper and the FTS5 transaction. This plan re-homes the helper and replaces the transaction surface. |

This plan is independent of the 3.5x family and any in-flight 3.5c turn-boundary work. **Hard prerequisite satisfied:** 3.5c-pre is shipped. `co_cli/memory/mutator.py` is in tree; `_index_no_commit` / `_index_chunks_no_commit` splits exist; `transaction()` + `_in_transaction` still present for this plan to delete.

## Context

After 3.5c-pre landed, the review surfaced five structural concerns:

1. **`mutator.atomic_write_text` lives in `memory/` by convention, not semantics.** `memory/` plays a "shared-primitive package" role for cross-package primitives. As more primitives accrete, `memory/` will become a junk drawer in spirit — which the project's `feedback_no_util_modules.md` was meant to prevent. The rule's intent is "no junk drawers"; a focused `persistence/` package isn't one.

2. **`MemoryStore._in_transaction` is a hidden mode-switch on the public API.** Inside a `with store.transaction():` block, `index()` and `index_chunks()` skip their inner commits via `if not self._in_transaction:` guards at `memory_store.py:433, 494`. Outside the block, they commit. A reader cannot tell from the signature which semantics they get.

3. **One genuinely thin wrapper to fold; two domain wrappers to keep but simplify.** The pre-audit framing called for deleting three wrappers as "thin mkdir adapters." Audit (Gate 1) corrected this:
   - `_atomic_write_skill` (`tools/system/skills.py:161-164`) — pure `mkdir + atomic_write_text`. Genuinely thin. 5 internal callers. **Fold.**
   - `write_curator_state` (`skills/curator.py:168-172`) — resolves `_curator_state_path(deps)` + `json.dumps(state, indent=2)` + mkdir + atomic write. 3 external callers passing only `(deps, state)`. **Keep**, drop the now-redundant mkdir line.
   - `write_records` (`skills/usage.py:91-95`) — resolves `_sidecar_path(deps)` + `json.dumps(data, indent=2)` + mkdir + atomic write. 5 callers passing only `(deps, data)`. **Keep**, drop the now-redundant mkdir line.

4. **`co_cli/tools/tool_io.py:99-103` retains its own `tmp.write_text + os.replace` pattern.** Audit found `errors="replace"` is load-bearing (tool-spill content can contain arbitrary subprocess bytes that fail strict UTF-8). Fold by extending `atomic_write_text` with `errors=` parameter (D10).

5. **No `atomic_write_bytes`.** Text-only primitive. First future binary atomic writer reinvents.

## Problem & Outcome

**Problem.** Atomic-write hygiene is implemented but housed in the wrong package, and the FTS5 transaction is implemented but leaks state through the public API. Both are correct in their effects and reader-hostile in their shapes: callers must know unwritten rules (which package owns the primitive; whether they're "inside" a transaction). Future contributors will either re-implement (because the primitive isn't where they look) or trip the mode-switch (because the signature lies).

**Outcome.**
- `co_cli/persistence/atomic.py` owns `atomic_write_text(path, content, *, encoding="utf-8", errors="strict")` and `atomic_write_bytes(path, content)`. Both build `mkdir(parents=True, exist_ok=True)` into the primitive.
- One wrapper folded (`_atomic_write_skill`); two domain wrappers simplified (mkdir removed, function bodies and signatures unchanged so 8 call sites stay intact).
- `MemoryStore.transaction()` returns a `MemoryTransaction` context manager. `MemoryTransaction.index/index_chunks/remove` defer commits; `MemoryTransaction.__exit__` commits on success or rolls back on exception. `MemoryStore.index() / index_chunks() / remove() / remove_chunks()` always commit — one semantics, no flag.
- `MemoryStore._transaction_open` flag (renamed from `_in_transaction` to avoid name-reuse footgun) exists only to refuse nested transactions, never to alter public-method behaviour.
- Grep audit narrows by one file (only `co_cli/persistence/atomic.py`). Reader-facing semantics match the type surface — no hidden state.

## Scope

### In scope

- New package `co_cli/persistence/` with `atomic.py` exposing `atomic_write_text` (with `encoding=` and `errors=` keyword args) and `atomic_write_bytes`. `__init__.py` is docstring-only per project rule.
- Both primitives `mkdir(parents=True, exist_ok=True)` before writing. Always. No opt-out.
- Delete `co_cli/memory/mutator.py`. Update every importer to `co_cli.persistence.atomic`. Per zero-backward-compat rule: no re-export shim.
- Delete the one true thin wrapper: `co_cli/tools/system/skills.py:_atomic_write_skill`. Its 5 internal callers (lines 175, 201, 227, 278, 372) invoke `atomic_write_text(path, content)` directly.
- Keep `write_curator_state` (`skills/curator.py:168`) and `write_records` (`skills/usage.py:91`) — they encapsulate path resolution from `deps` and JSON serialization. Just delete their `path.parent.mkdir(...)` line (now redundant). 3 + 5 external callers unchanged.
- Fold `co_cli/tools/tool_io.py:99-103` via `atomic_write_text(file_path, content, errors="replace")`. Preserve the existing `if not file_path.exists():` content-dedup guard.
- Replace `MemoryStore.transaction()` + `_in_transaction` flag with a `MemoryTransaction` object:
  - `MemoryTransaction` lives in `co_cli/memory/memory_store.py` (same file; tightly coupled to `MemoryStore` internals).
  - `MemoryStore.transaction() -> MemoryTransaction` is the only public multi-write transaction surface.
  - `MemoryTransaction` exposes `.index`, `.index_chunks`, `.remove` deferring commits.
  - `MemoryTransaction.__enter__` sets flags only — NO explicit `conn.execute("BEGIN")` (D11). sqlite3 legacy mode opens the transaction implicitly on the first DML, matching the proven pattern in the existing `transaction()` and `index_session`.
  - `MemoryTransaction.__exit__` calls `conn.commit()` on success or `conn.rollback()` on exception, directly.
  - `MemoryStore.index() / index_chunks() / remove() / remove_chunks()` return to "always commit" — single semantics. Each split into `_<name>_no_commit` (body minus commit) + always-commit public wrapper.
  - `_remove_no_commit` internally calls `_remove_chunks_no_commit` first (preserves chunks-before-docs ordering currently at `memory_store.py:1222-1228`).
  - New `MemoryStore._transaction_open` nesting-guard flag (explicitly NOT `_in_transaction` to avoid name-reuse with the deleted flag).
- Update `SkillIndex.upsert` to use `with self._store.transaction() as tx: tx.index(...); tx.index_chunks(...)`.
- Update `agent_docs/code-conventions.md` to reference `co_cli.persistence.atomic_write_text`, plus a one-line rule that multi-step writes use `with store.transaction() as tx:` and hidden transaction state is forbidden.

### Out of scope

- User-facing `Write` tool (`co_cli/tools/files/write.py`) atomicity — still intentionally non-atomic for symlink reasons. Add a one-line atomicity contract note to its docstring (folded into TASK-4); behavior unchanged.
- Append-mode writers (`co_cli/tools/background.py`, `co_cli/memory/transcript.py`) — unaffected; semantics differ.
- `sync_dir` / `sync_sessions` per-file commit batching. `sync_dir` (`memory_store.py:1064`) currently commits per file (one `index()` + one `index_chunks()` = 2 commits/file). Wrapping a whole sync in a single `store.transaction()` would reduce commits but trade failure isolation (one bad file aborts the whole batch). Out of scope — tracked in Deferred items.
- Cross-process locking — owned by `co_cli/tools/resource_lock.py`, not in scope.

## Behavioural Constraints

1. **One atomic primitive home.** `atomic_write_text` and `atomic_write_bytes` live only in `co_cli/persistence/atomic.py`. `grep -rn 'tempfile.NamedTemporaryFile' co_cli/` returns hits only in that file.

2. **Primitives always mkdir parent.** No caller pre-creates `path.parent`. The wrapper-mkdir layer is gone — both for the deleted `_atomic_write_skill` and the kept `write_curator_state` / `write_records` (their internal mkdir lines are removed; their public signatures and call sites are unchanged).

3. **No hidden mode-switch on `MemoryStore`.** `MemoryStore.index()`, `index_chunks()`, `remove()`, `remove_chunks()` have exactly one semantics: every call commits. No flag, no conditional commit, no `_transaction_open` check inside their bodies. `_transaction_open` exists only to refuse nested transactions.

4. **`MemoryTransaction` is the only multi-write transaction surface.** Outside callers do not reach into `_conn` or any other private attribute. Any deferred-commit operation goes through `with store.transaction() as tx:`.

5. **Zero backward compat.** Per project rule (`feedback_zero_backward_compat.md`): no aliases, no re-export shims, no compat tables. `co_cli.memory.mutator` deleted outright; the old `MemoryStore.transaction()` (returning `Iterator[None]`, using `_in_transaction` flag) deleted outright. The new `store.transaction()` returns a `MemoryTransaction` object — different shape, same method name; reusing the name is the cleanest call site.

6. **No `_in_transaction` symbol survives.** The old flag name is dangerous to reuse (carried "skip my own commit" semantics; future readers would assume the old meaning). The nesting-guard flag is `_transaction_open`. `grep -rn "_in_transaction" co_cli/ tests/` returns zero hits after TASK-3.

7. **Success-path behaviour unchanged.** Same files written to same paths with same content. Failure-mode shape unchanged from 3.5c-pre. Only import paths (caller side) and the transaction call shape change.

## Failure Modes

1. **`MemoryTransaction` used outside `with` block.** Caller does `tx = store.transaction(); tx.index(...)` without entering the context.
   - Mitigation: `MemoryTransaction.__init__` is inert. `__enter__` sets `_active = True`. Each `tx.index/index_chunks/remove` raises `RuntimeError` if `_active` is False.

2. **Nested transactions.** Caller does `with store.transaction(): ...; with store.transaction(): ...`.
   - Mitigation: `MemoryStore._transaction_open` flag, checked in `MemoryTransaction.__enter__`. Second entry raises `RuntimeError("Nested transactions not supported")`. This flag does NOT alter `MemoryStore.index()/index_chunks()/remove()/remove_chunks()` semantics — it only refuses re-entry.

3. **Implicit mkdir surprise.** `atomic_write_text(typo_path/file.txt)` silently creates `typo_path/`.
   - Acceptance: this is the new contract. Forgetting `mkdir` is the more frequent and silently-write-failing error; typo'd paths creating dirs is rare and easily caught (file ends up in unexpected location, visible immediately). Trade is favourable.

4. **`tool_io.py` content-dedup must survive the fold.** Current code at `tool_io.py:100` guards the write with `if not file_path.exists():` — content-addressed by hash prefix so duplicate spills don't re-write.
   - Mitigation: TASK-2 acceptance requires the `if not file_path.exists():` guard to stay around the `atomic_write_text(..., errors="replace")` call.

5. **Transaction rollback doesn't undo Python-side state.** The current FTS5 rollback test asserts the row+chunks are absent from SQLite after exception. It does not assert anything about in-memory caches. If `SkillIndex` or any caller keeps an in-memory mirror that's mutated before the commit, rollback leaves it inconsistent.
   - Acceptance: out of scope. No current caller maintains such a mirror. If introduced, that caller owns the rollback discipline for its own state.

## High-Level Design

### Persistence primitive

```python
# co_cli/persistence/atomic.py
"""Atomic full-file overwrite primitives for co_cli."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> None:
    """Atomic full-file overwrite. Creates parent dirs as needed.

    Tempfile lives in path.parent so os.replace stays on the same filesystem.
    On exception at any point, the tempfile is cleaned up and the target is
    untouched (left as old content or absent).

    Pass errors="replace" when writing content from arbitrary subprocess
    output that may contain non-UTF-8 bytes (see tools/tool_io.py).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
            encoding=encoding,
            errors=errors,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Binary variant — same atomicity contract, no encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, suffix=".tmp", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
```

### MemoryTransaction object

```python
# co_cli/memory/memory_store.py (excerpt)

class MemoryTransaction:
    """Deferred-commit transaction over a MemoryStore connection.

    Use via `with store.transaction() as tx: tx.index(...); tx.index_chunks(...)`.
    Commits on __exit__ success, rolls back on exception. Nesting raises.

    No explicit BEGIN — sqlite3 legacy mode opens the transaction implicitly
    on the first DML inside the context, matching the proven pattern used by
    the prior transaction() context manager and index_session.
    """

    def __init__(self, store: "MemoryStore") -> None:
        self._store = store
        self._active = False

    def __enter__(self) -> "MemoryTransaction":
        if self._store._transaction_open:
            raise RuntimeError("Nested transactions not supported")
        self._store._transaction_open = True
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        try:
            if exc_type is None:
                self._store._conn.commit()
            else:
                self._store._conn.rollback()
        finally:
            self._store._transaction_open = False
            self._active = False

    def index(self, **kwargs) -> None:
        self._guard()
        self._store._index_no_commit(**kwargs)

    def index_chunks(self, source: str, doc_path: str, chunks: list) -> None:
        self._guard()
        self._store._index_chunks_no_commit(source, doc_path, chunks)

    def remove(self, source: str, path: str) -> None:
        self._guard()
        self._store._remove_no_commit(source, path)

    def _guard(self) -> None:
        if not self._active:
            raise RuntimeError("MemoryTransaction methods called outside `with` block")


class MemoryStore:
    def __init__(self, ...) -> None:
        ...
        self._transaction_open: bool = False

    # Public methods — always commit. No _transaction_open branch in any body.
    def index(self, **kwargs) -> None:
        self._index_no_commit(**kwargs)
        self._conn.commit()

    def index_chunks(self, source, doc_path, chunks) -> None:
        self._index_chunks_no_commit(source, doc_path, chunks)
        self._conn.commit()

    def remove(self, source: str, path: str) -> None:
        self._remove_no_commit(source, path)
        self._conn.commit()

    def remove_chunks(self, source: str, path: str) -> None:
        self._remove_chunks_no_commit(source, path)
        self._conn.commit()

    # Private no-commit bodies. _index_no_commit and _index_chunks_no_commit
    # already exist from 3.5c-pre; this plan adds the remove pair.

    def _remove_no_commit(self, source: str, path: str) -> None:
        """Remove a document without committing. Chunks first (rowid ordering)."""
        self._remove_chunks_no_commit(source, path)
        self._conn.execute(
            "DELETE FROM docs WHERE source = ? AND path = ?",
            (source, path),
        )

    def _remove_chunks_no_commit(self, source: str, path: str) -> None:
        """Remove all chunk rows for (source, path) without committing.

        Body is current remove_chunks() (memory_store.py:497-516) minus the
        trailing self._conn.commit().
        """
        if self._backend == "hybrid":
            rowids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT rowid FROM chunks WHERE source=? AND doc_path=?",
                    (source, path),
                ).fetchall()
            ]
            if rowids:
                placeholders = ",".join("?" * len(rowids))
                self._conn.execute(
                    f"DELETE FROM {self._chunks_vec_table} WHERE rowid IN ({placeholders})",
                    rowids,
                )
        self._conn.execute(
            "DELETE FROM chunks WHERE source=? AND doc_path=?",
            (source, path),
        )

    def transaction(self) -> MemoryTransaction:
        return MemoryTransaction(self)
```

The `_transaction_open` flag exists only to refuse nested transactions. No public method reads it. There is no path through the code where `index()` / `index_chunks()` / `remove()` / `remove_chunks()` skip their commits.

### SkillIndex.upsert

```python
def upsert(self, name: str, description: str, path: str) -> None:
    with self._store.transaction() as tx:
        tx.index(source="skill", path=path, title=name, description=description)
        tx.index_chunks(
            "skill", path,
            [Chunk(index=0, content=f"{name}: {description}", start_line=0, end_line=0)],
        )
```

### Caller migration map

| Site | Before | After |
|---|---|---|
| Importers of `co_cli.memory.mutator` (8 files: service.py, dream.py, installer.py, tools/system/skills.py, skills/curator.py, skills/usage.py, agents/session_review.py, agents/skill_curator.py) | `from co_cli.memory.mutator import atomic_write_text` | `from co_cli.persistence.atomic import atomic_write_text` |
| `tools/system/skills.py:_atomic_write_skill` | wrapper | DELETED — 5 internal call sites (lines 175, 201, 227, 278, 372) invoke `atomic_write_text(path, content)` directly |
| `skills/curator.py:write_curator_state` | function body: `path.parent.mkdir(parents=True, exist_ok=True); atomic_write_text(path, json.dumps(state, indent=2))` | KEPT — body becomes single line: `atomic_write_text(path, json.dumps(state, indent=2))`. Signature and 3 callers unchanged. |
| `skills/usage.py:write_records` | function body: `path.parent.mkdir(parents=True, exist_ok=True); atomic_write_text(path, json.dumps(data, indent=2))` | KEPT — body becomes single line: `atomic_write_text(path, json.dumps(data, indent=2))`. Signature and 5 callers unchanged. |
| `installer.py:write_skill_file` | `dest_dir.mkdir(...) + atomic_write_text(...)` | `atomic_write_text(...)` (mkdir is built-in) |
| `dream.py:save_dream_state` | `atomic_write_text(path, json.dumps(...))` | unchanged content; import path updated |
| `tool_io.py:100-103` | local `tmp.write_text(..., errors="replace") + os.replace` | `atomic_write_text(file_path, content, errors="replace")` inside the existing `if not file_path.exists():` guard |
| `SkillIndex.upsert` | `with self._store.transaction(): self._store.index(...); self._store.index_chunks(...)` | `with self._store.transaction() as tx: tx.index(...); tx.index_chunks(...)` (same method name, new return shape) |
| `tests/test_flow_skill_index.py` | merged from `test_skill_index_transaction.py` (untracked) by clean-tests | existing atomicity tests kept; API updated to `with store.transaction() as tx:`; new test cases added (see Testing) |

## Tasks

### ✓ DONE TASK-1 — Create persistence package, promote primitives, build in mkdir + errors=

Files:
- NEW `co_cli/persistence/__init__.py` (docstring-only per project rule).
- NEW `co_cli/persistence/atomic.py` — `atomic_write_text(path, content, *, encoding="utf-8", errors="strict")` + `atomic_write_bytes(path, content)`. Both with built-in `path.parent.mkdir(parents=True, exist_ok=True)`.
- DELETE `co_cli/memory/mutator.py`.
- UPDATE 8 caller files' imports from `co_cli.memory.mutator` to `co_cli.persistence.atomic` (service.py, dream.py, installer.py, tools/system/skills.py, skills/curator.py, skills/usage.py, agents/session_review.py, agents/skill_curator.py).
- MOVE `tests/test_flow_atomic_write.py` → `tests/test_atomic_write_persistence.py`; update import; add new test cases (see Testing).

Acceptance:
- `co_cli/memory/mutator.py` does not exist.
- `grep -rn "co_cli\.memory\.mutator" co_cli/ tests/` returns no hits.
- `grep -rn "tempfile.NamedTemporaryFile" co_cli/` returns hits only in `co_cli/persistence/atomic.py`.
- `atomic_write_text(deep/path/that/doesnt/exist.txt, ...)` succeeds without explicit prior mkdir.
- `atomic_write_text(path, content_with_surrogates, errors="replace")` writes a valid file without raising.
- All existing tests pass unchanged (modulo the import-path edits).

done_when: `scripts/quality-gate.sh full` clean AND grep audits return pinned sets.

prerequisites: 3.5c-pre committed (verify `co_cli/memory/mutator.py` in tree before starting).

### ✓ DONE TASK-2 — Delete `_atomic_write_skill`, simplify two domain wrappers, fold tool_io

Files:
- `co_cli/tools/system/skills.py` — delete `_atomic_write_skill` (lines 161-164); 5 internal callers (lines 175, 201, 227, 278, 372) invoke `atomic_write_text(path, content)` directly.
- `co_cli/skills/curator.py:write_curator_state` — KEEP function; delete the `path.parent.mkdir(parents=True, exist_ok=True)` line (line 171). Body becomes `atomic_write_text(path, json.dumps(state, indent=2))`. Signature and 3 callers unchanged.
- `co_cli/skills/usage.py:write_records` — KEEP function; delete the `path.parent.mkdir(parents=True, exist_ok=True)` line (line 94). Body becomes `atomic_write_text(path, json.dumps(data, indent=2))`. Signature and 5 callers unchanged.
- `co_cli/skills/installer.py:write_skill_file` — remove the `dest_dir.mkdir(...)` call (now redundant).
- `co_cli/tools/tool_io.py:99-103` — replace the local `tmp_path = file_path.with_suffix(...) + tmp_path.write_text(...) + os.replace(...)` block with `atomic_write_text(file_path, content, errors="replace")`. PRESERVE the surrounding `if not file_path.exists():` guard (content-dedup by hash prefix).

Acceptance:
- `_atomic_write_skill` is deleted; `grep -rn "_atomic_write_skill" co_cli/` returns no hits.
- `write_curator_state` and `write_records` still exist with unchanged signatures; both have NO `mkdir(...)` call before `atomic_write_text(...)`.
- `tool_io.py` retains the `if not file_path.exists():` guard around the new `atomic_write_text(..., errors="replace")` call (verify by reading `tool_io.py:90-110` after edit).
- `grep -rn "tempfile\." co_cli/skills/ co_cli/agents/ co_cli/tools/" returns no hits.
- `grep -rn "tempfile.NamedTemporaryFile" co_cli/` returns hits only in `co_cli/persistence/atomic.py`.
- No `mkdir(parents=True, exist_ok=True)` call immediately precedes an `atomic_write_text` call anywhere in `co_cli/`.
- Existing tests for skill manage, curator state, usage records all pass unchanged (8 retained call sites must continue to work).

done_when: `scripts/quality-gate.sh full` clean AND grep audits pinned.

prerequisites: [TASK-1].

### ✓ DONE TASK-3 — MemoryTransaction replaces transaction() + _in_transaction

Files:
- `co_cli/memory/memory_store.py`:
  - Add `MemoryTransaction` class (same file).
  - Verify `_index_no_commit` and `_index_chunks_no_commit` already exist (from 3.5c-pre).
  - Add `_remove_no_commit(source, path)` — body calls `_remove_chunks_no_commit(source, path)` first, then `DELETE FROM docs WHERE source = ? AND path = ?`.
  - Add `_remove_chunks_no_commit(source, path)` — body of current `remove_chunks()` (lines 497-516) minus the trailing `self._conn.commit()`.
  - Rewrite public `index()`, `index_chunks()`, `remove()`, `remove_chunks()` as always-commit wrappers (each calls its `_no_commit` then `self._conn.commit()`).
  - DELETE the `if not self._in_transaction:` guards in `index()` (line 433) and `index_chunks()` (line 494).
  - DELETE the existing `MemoryStore.transaction()` method (lines 329-351).
  - DELETE the `MemoryStore._in_transaction` flag (line 293, plus the comment lines 290-292).
  - Add `MemoryStore._transaction_open: bool = False` for nesting-guard ONLY. Name is deliberately NOT `_in_transaction`.
  - Add new `MemoryStore.transaction() -> MemoryTransaction` factory method.
- `co_cli/skills/index.py:SkillIndex.upsert` — switch from `with self._store.transaction(): self._store.index(...)` to `with self._store.transaction() as tx: tx.index(...); tx.index_chunks(...)`.
- `tests/test_flow_skill_index.py` — atomicity tests already present (merged by clean-tests); update API to use `tx.index(...)` / `tx.index_chunks(...)`; add new test cases (see Testing).

Acceptance:
- `grep -rn "_in_transaction" co_cli/ tests/` returns ZERO hits (no surviving symbol with this name).
- `grep -rn "MemoryStore.transaction" co_cli/` returns hits only for the new factory method definition and call sites; no references to the deleted `Iterator[None]` version.
- `MemoryStore.index()`, `index_chunks()`, `remove()`, `remove_chunks()` always commit. Reading each body, no branch skips `self._conn.commit()`.
- `_remove_no_commit` and `_remove_chunks_no_commit` exist as private methods on `MemoryStore`.
- `_remove_no_commit` calls `_remove_chunks_no_commit` before deleting from docs (preserves chunks-before-docs ordering).
- `with store.transaction() as tx: tx.index(...); raise X` rolls back the insert (real sqlite, no fakes).
- `with store.transaction() as tx: tx.remove(...); raise X` rolls back the deletion — both chunks and docs rows still present (real sqlite, no fakes).
- Nested `with store.transaction(): with store.transaction(): ...` raises `RuntimeError("Nested transactions not supported")`.
- `tx.index(...)` / `tx.remove(...)` outside a `with` raises `RuntimeError`.
- `SkillIndex.upsert` happy-path and rollback tests pass.

done_when: `scripts/quality-gate.sh full` clean AND `grep -rn "_in_transaction" co_cli/ tests/` returns zero hits.

prerequisites: [TASK-1] (independent of TASK-2; can run parallel).

### ✓ DONE TASK-4 — Doc sync

Files:
- `agent_docs/code-conventions.md` — update the rule from 3.5c-pre to reference `co_cli.persistence.atomic_write_text`. Add a second one-line rule: "Multi-step writes to `MemoryStore` use `with store.transaction() as tx: ...`. Hidden transaction state on the store is forbidden."
- `co_cli/tools/files/write.py` (user-facing Write tool) — add a one-line atomicity contract note to its docstring: "Direct write, follows symlinks; not atomic. For internal atomic writes, use `co_cli.persistence.atomic_write_text`."
- Sweep `docs/specs/memory.md` and `docs/specs/knowledge.md` for stale `transaction()` / `_in_transaction` / `mutator` references and update.

Acceptance:
- Both convention rules are present and discoverable in `code-conventions.md`.
- `tools/files/write.py` docstring carries the atomicity note.
- `grep -rn "MemoryStore.transaction\|memory\.mutator\|_in_transaction" docs/specs/` returns no hits (other than this plan and 3.5c-pre archives).

done_when: `scripts/quality-gate.sh full` clean AND grep audits pinned.

prerequisites: [TASK-1, TASK-2, TASK-3].

## Testing

Per `agent_docs/testing.md`: real services, no fakes.

- `tests/test_atomic_write_persistence.py` (renamed/moved from `test_flow_atomic_write.py`):
  - Existing 5 behavioural cases unchanged (modulo import path).
  - NEW `test_atomic_write_creates_missing_parent_dirs` — pass a path whose parent does not exist; assert the file is written and the parent now exists.
  - NEW `test_atomic_write_text_errors_replace_handles_invalid_codepoints` — pass content containing unpaired surrogates with `errors="replace"`; assert the write succeeds and the on-disk content has replacement chars where expected.
  - NEW `test_atomic_write_bytes_happy_path` — write known bytes, read back, assert byte-equality and parent-dir creation.
  - If a natural exception path for `atomic_write_bytes` orphan-cleanup cannot be induced with real services, document the omission in a docstring comment in the test file; do NOT introduce monkeypatch.

- `tests/test_flow_skill_index.py` (merged from `test_skill_index_transaction.py` by clean-tests):
  - `test_upsert_commits_both_row_and_chunks_on_success` (existing — update API when MemoryTransaction lands).
  - `test_upsert_rolls_back_when_index_chunks_fails` (existing — update API; real sqlite error via malformed Chunk).
  - NEW `test_nested_transaction_raises`.
  - NEW `test_transaction_method_outside_with_raises`.
  - NEW `test_transaction_remove_rolls_back_on_exception` — write a doc + chunks via direct `index()` / `index_chunks()` (committed), then in a `with transaction() as tx:` block call `tx.remove(...)` and raise. Assert both docs and chunks rows still present after rollback.

No mocks, no monkeypatch, no fakes. If a behaviour cannot be tested with real services, the design is wrong — fix the design.

## Shipping order

Single PR. TASK-4 doc-sync wants the persistence module path and the transaction call shape both live at the same time so the conventions doc lands coherent. Task graph:

```
TASK-1 → TASK-2
       ↘ TASK-3
TASK-2, TASK-3 → TASK-4
```

TASK-2 and TASK-3 are independent of each other; can run in parallel after TASK-1 finishes.

**Hard dependencies:**
- **3.5c-pre shipped at `6024a9d` (v0.8.190).** `co_cli/memory/mutator.py` is in tree. Prerequisite satisfied — no remaining sequencing block.

**Soft dependencies:** none.

**Downstream beneficiaries:**
- Future binary writers — `atomic_write_bytes` is there.
- Future multi-write callers — `with store.transaction() as tx:` is the discoverable, type-honest surface.
- Future maintainers — `grep tempfile.NamedTemporaryFile co_cli/` and `grep memory.mutator co_cli/` give one-file answers.

## Open Questions

All resolved. See `## Open Decisions Resolved — 2026-05-14` below. Summary pointers:
- **D10** — `tool_io.py` fold strategy: extend `atomic_write_text` with `errors=` parameter; fold rather than document an exemption.
- **D11** — `MemoryTransaction.__enter__`: no explicit `BEGIN`; rely on sqlite3 legacy mode's implicit transaction-open via first DML.
- **D12** — `remove` in transaction surface: include now; requires `_remove_no_commit` + `_remove_chunks_no_commit` splits.
- **D13** — Naming: `Batch` → `MemoryTransaction`, `store.batch()` → `store.transaction()`, nesting-guard flag is `_transaction_open` (NOT `_in_transaction` — name-reuse footgun).

## Deferred items

- **Atomic Write tool semantics** — same as 3.5c-pre; still a separate grill.
- **Broader transaction discipline for other `MemoryStore` mutators** — `MemoryTransaction` covers `index`/`index_chunks`/`remove`. If a future caller needs to batch other operations (`embedding_cache` writes, `index_session`), extend `MemoryTransaction` then.
- **`sync_dir` / `sync_sessions` per-file commit batching** — current per-file commit semantics preserve failure isolation. Wrapping a whole sync in one `store.transaction()` is a separate optimization with trade-offs (atomicity vs. partial-progress on bad files). Track for a future plan if perf telemetry shows commit overhead.
- **Per-file lock layer** — `resource_lock.py` is its own concern.

## Open Decisions Resolved — 2026-05-14

### D10. `tool_io.py` fold strategy (post-audit)
- Question: Audit of `tool_io.py:99-103` found it uses `path.write_text(content, encoding="utf-8", errors="replace") + os.replace` — the `errors="replace"` is load-bearing because tool-spill content can contain non-UTF-8 from arbitrary subprocess output. The plan's "fold by default" tentative answer needs a concrete strategy. Add `errors=` to the primitive, or document an exemption?
- Recommended: Extend the primitive — add `errors: str = "strict"` to `atomic_write_text`. `tool_io.py` becomes a normal caller passing `errors="replace"`.
- Chosen: Extend `atomic_write_text` with `errors: str = "strict"`.
- Why: One parameter line exposes an option `NamedTemporaryFile` already accepts. Exemption would leave a permanent parallel pattern in `tool_io.py` plus a doc burden. Keeping the grep audit pinned to one file is worth more than the parameter cost.
- Constraint: TASK-1 adds `errors: str = "strict"` to `atomic_write_text` only (not `atomic_write_bytes` — bytes don't encode). TASK-2 folds `tool_io.py:99-103` via `atomic_write_text(file_path, content, errors="replace")`. Grep audit `tempfile.NamedTemporaryFile co_cli/` returns hits ONLY in `co_cli/persistence/atomic.py` after TASK-2 — no surviving exemption.

### D11. `MemoryTransaction.__enter__` — explicit BEGIN vs implicit
- Question: The plan's `Batch.__enter__` calls `self._store._conn.execute("BEGIN")` explicitly. The existing `MemoryStore.transaction()` (and `index_session`) use `with self._conn:` with no explicit BEGIN — the first DML in `_index_no_commit` triggers an implicit `BEGIN DEFERRED` in sqlite3 legacy mode. Should the new transaction object call `BEGIN` explicitly, or match the proven pattern?
- Recommended: Drop the explicit BEGIN; rely on implicit transaction-open via first DML.
- Chosen: No explicit BEGIN.
- Why: `sqlite3.connect(..., timeout=5)` uses default `isolation_level=""` (legacy mode). Mixing explicit `BEGIN` with legacy-mode implicit tracking risks `OperationalError: cannot start a transaction within a transaction` on any path where stray DML precedes `__enter__`. The implicit pattern is proven by `transaction()` and `index_session`. Rollback semantics are identical either way.
- Constraint: `MemoryTransaction.__enter__` sets `_active=True` and the store's `_transaction_open` nesting flag — no `conn.execute("BEGIN")`. `MemoryTransaction.__exit__` calls `conn.commit()` on success or `conn.rollback()` on exception, both directly on `self._store._conn`. No reliance on `with self._conn:` context-manager protocol either (explicit commit/rollback keeps the control flow readable).

### D12. `remove` in the transaction surface
- Question: The plan exposes `index`, `index_chunks`, `remove` on the transaction object. But `MemoryStore.remove()` currently calls `remove_chunks()` (which commits) then commits again — adding `Batch.remove` requires implementing BOTH `_remove_no_commit` AND `_remove_chunks_no_commit`. No current caller uses `remove` in a transaction. Include now, or defer?
- Recommended (revised after naming change): Include `remove`.
- Chosen: Include `remove`.
- Why: With the type renamed to `MemoryTransaction` (D13), it reads as the transaction primitive for `MemoryStore` writes — not as the upsert-helper. An API with `index/index_chunks` but no `remove` is a primitive with an undocumented hole; future readers needing compound delete-then-reindex would reach past the type (breaking atomicity) or file confusion bugs. Implementation cost is contained: two mechanical splits matching the established `_index_no_commit`/`_index_chunks_no_commit` pattern.
- Constraint: TASK-3 must also split `MemoryStore.remove()` and `MemoryStore.remove_chunks()` into `_remove_no_commit` + `_remove_chunks_no_commit` (no-commit bodies) plus always-commit public wrappers. `MemoryTransaction.remove` calls `_remove_no_commit` which internally calls `_remove_chunks_no_commit` first (preserving the chunks-before-docs ordering at memory_store.py:1222-1228). Add a `MemoryTransaction.remove` rollback test using a real sqlite failure; no fakes.

### D13. Naming — `Batch` is the wrong concept (emergent)
- Question: The plan names the object `Batch` with factory `store.batch()`. "Batch" implies homogeneous items in one operation (e.g., bulk insert of 100 same-type rows). This type is a heterogeneous transaction grouper: `index` + `index_chunks` + `remove` — different operations, one atomic boundary. That's a bundled transaction, not a batch. Also considered: bare `Transaction` (too broad — collides with `sqlite3.Connection` transactions, future DB concepts, generic transaction meaning), `MemoryWriteTransaction` (preemptive — no `MemoryReadTransaction` planned, methods imply writes).
- Recommended: `MemoryTransaction` with factory `store.transaction()`.
- Chosen: `MemoryTransaction` + `store.transaction()`.
- Why: Domain prefix matches `MemoryStore` (project naming pattern: domain + concept, like `SkillIndex`, `SearchResult`). `Transaction` is the precise DB concept the type implements. The `Write` qualifier was preemptive — `MemoryStore` itself doesn't carry it despite having both read and write methods, and reserving namespace for a hypothetical `MemoryReadTransaction` violates "don't design for hypothetical future requirements." Methods on the type (`index`, `index_chunks`, `remove`) already declare write-intent.
- Constraint:
  - Plan-wide rename: `Batch` → `MemoryTransaction`, `store.batch()` → `store.transaction()`.
  - The old `MemoryStore.transaction()` (returning `Iterator[None]`, using `_in_transaction` flag) is deleted outright per zero-backward-compat. The new `store.transaction()` returns a `MemoryTransaction` object — different shape, same name. No live-code collision because the old method is gone. Reusing the name is the cleanest call site.
  - Test file: the plan's TASK-3 rename `test_skill_index_transaction.py` → `test_skill_index_batch.py` is MOOT — `clean-tests` merged `test_skill_index_transaction.py` into `tests/test_flow_skill_index.py`. TASK-3 testing targets `tests/test_flow_skill_index.py`.
  - `code-conventions.md` rule (TASK-4) reads: "Multi-step writes to `MemoryStore` use `with store.transaction() as tx: tx.index(...); tx.index_chunks(...)`. Hidden transaction state on the store is forbidden."
  - Nesting-guard flag is `_transaction_open`, NOT `_in_transaction`. The old flag name carried "skip my own commit" semantics that would mislead future readers if reused. Grep audit asserts zero `_in_transaction` hits after TASK-3.

### Resolved from codebase (no interview)
- D4. `tool_io.py:99-103` actual pattern → `path.write_text(content, encoding="utf-8", errors="replace") + os.replace`; no orphan cleanup on exception; the `errors="replace"` is the load-bearing deviation from the primitive (source: `co_cli/tools/tool_io.py:99-103`).
- D5. `MemoryStore._conn` isolation mode → default `isolation_level=""` (legacy mode); existing `transaction()` uses `with self._conn:` with no explicit BEGIN (source: `co_cli/memory/memory_store.py:286, 329-351, 1168`).
- D6. `remove()` / `_remove_no_commit` gap → `remove()` at line 1220 calls `remove_chunks()` (which commits) then commits again; neither `_remove_no_commit` nor `_remove_chunks_no_commit` exists (source: `co_cli/memory/memory_store.py:497-517, 1220-1229`).
- D7. `SkillIndex.upsert` call shape → uses `with self._store.transaction(): self._store.index(...); self._store.index_chunks(...)`; no `remove` in the transaction; replaces directly to `with self._store.transaction() as tx: tx.index(...); tx.index_chunks(...)` after rename (source: `co_cli/skills/index.py:41-57`).
- D8. Importers of `co_cli.memory.mutator` → 9 files: `memory/service.py:31`, `memory/dream.py:36`, `skills/installer.py:15`, `tools/system/skills.py:22`, `skills/curator.py:17`, `skills/usage.py:38`, `agents/session_review.py:14`, `agents/skill_curator.py:13`, `tests/test_flow_atomic_write.py:13` (source: grep).
- D9. 3.5c-pre ship status → `_index_no_commit` and `_index_chunks_no_commit` exist (the no-commit split is done); `transaction()` and `_in_transaction` still present; `co_cli/memory/mutator.py` is untracked (not committed). Hard dependency on 3.5c-pre is satisfied at the split level but the new `mutator.py` file must be committed before this plan executes (source: git status + `co_cli/memory/memory_store.py:286-293, 329-351` + `co_cli/memory/mutator.py`).

### Deferred
_(none — all open branches resolved.)_

---
Summary: 3 open → 4 resolved (1 emergent: D13 naming) · 0 deferred · 6 from codebase

## Delivery Summary — 2026-05-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | persistence package landed; quality-gate clean; grep audits pinned | ✓ pass |
| TASK-2 | thin wrapper deleted; two domain wrappers simplified; tool_io folded; grep audits pinned | ✓ pass |
| TASK-3 | MemoryTransaction object replaces transaction()+_in_transaction; `_in_transaction` returns 0 hits; rollback tests pass on real sqlite | ✓ pass |
| TASK-4 | code-conventions.md cites `co_cli.persistence.atomic_write_text` + transaction rule; file_write docstring carries atomicity note; docs/specs/ clean of stale refs | ✓ pass |

**Files changed (15):**
- NEW: `co_cli/persistence/__init__.py`, `co_cli/persistence/atomic.py`
- DELETED: `co_cli/memory/mutator.py`
- RENAMED: `tests/test_flow_atomic_write.py` → `tests/test_atomic_write_persistence.py` (extended with 3 new cases: mkdir, errors=replace, atomic_write_bytes)
- EDITED (memory_store + transaction surface): `co_cli/memory/memory_store.py`, `co_cli/skills/index.py`, `tests/test_flow_skill_index.py`
- EDITED (wrapper fold + tool_io fold): `co_cli/tools/system/skills.py`, `co_cli/skills/curator.py`, `co_cli/skills/usage.py`, `co_cli/skills/installer.py`, `co_cli/tools/tool_io.py`, `tests/test_flow_skills_manage.py` (stale docstring comment)
- EDITED (importer migrations to `co_cli.persistence.atomic`): `co_cli/memory/service.py`, `co_cli/memory/dream.py`, `co_cli/agents/session_review.py`, `co_cli/agents/skill_curator.py`
- EDITED (docs): `agent_docs/code-conventions.md`, `co_cli/tools/files/write.py`

**Tests:** scoped — 149 passed, 0 failed (test_atomic_write_persistence + test_flow_skill_index + test_flow_memory_store + test_flow_memory_write + test_flow_skills_manage + test_flow_skill_installer_dispatch + test_flow_skill_usage + test_flow_skill_curator + test_flow_spill). Dev-1 subagent additionally green on its scoped set (124 passed); Dev-2 subagent additionally green on its scoped set (99 passed).

**Lint:** `scripts/quality-gate.sh lint` clean — ruff check + ruff format both pass.

**Grep audits (all pinned):**
- `co_cli/memory/mutator.py` removed
- `co_cli.memory.mutator` imports: 0 hits
- `tempfile.NamedTemporaryFile` in `co_cli/`: only `co_cli/persistence/atomic.py:28,50`
- `_in_transaction` in `co_cli/` + `tests/`: 0 hits
- `_atomic_write_skill` in `co_cli/`: 0 hits
- `tempfile.` in `co_cli/skills/ co_cli/agents/ co_cli/tools/`: 0 hits
- `MemoryStore.transaction | memory.mutator | _in_transaction` in `docs/specs/`: 0 hits
- Adjacent `mkdir(parents=True...)` immediately followed by `atomic_write_text(...)`: 0 hits

**Doc Sync:** clean. `code-conventions.md` shared-primitives section now cites `co_cli.persistence.atomic.atomic_write_text` (+ `atomic_write_bytes`), states the implicit-mkdir contract, and adds the multi-step write rule referencing `with store.transaction() as tx:`. `co_cli/tools/files/write.py:file_write` docstring carries the explicit atomicity contract pointer. `docs/specs/memory.md` and `docs/specs/knowledge.md` swept — already free of stale `transaction()`/`_in_transaction`/`mutator` references (clean-tests had already aligned them); no further edits needed.

**Team:** TL handled TASK-1 (persistence package + 8 importer migrations, critical path) and TASK-4 (doc sync); Dev-1 handled TASK-2 (wrapper fold + tool_io); Dev-2 handled TASK-3 (MemoryTransaction + rollback tests). TASK-2 and TASK-3 ran in parallel after TASK-1 completed.

**Overall: DELIVERED**
All four tasks landed within their `done_when` criteria; grep audits pinned; full scoped test set (149 tests across both Dev workstreams + integration) green; lint clean; specs already aligned.

**Next step:** `/review-impl persistence-batch-redesign` — full suite + evidence scan + auto-fix → verdict appended to plan.

## Implementation Review — 2026-05-14

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | persistence package landed; grep audits pinned | ✓ pass | `co_cli/persistence/atomic.py:13` (`errors="strict"` kwarg), `:25` + `:47` (built-in mkdir), `:45` (`atomic_write_bytes`); 8 importers migrated (service.py:33, dream.py:38, installer.py:15, tools/system/skills.py:22, skills/curator.py:17, skills/usage.py:38, agents/session_review.py:14, agents/skill_curator.py:13); `co_cli/memory/mutator.py` deleted; `tests/test_atomic_write_persistence.py` 8/8 pass (3 new cases: mkdir, errors=replace, bytes) |
| TASK-2 | wrapper folded + tool_io folded; grep audits pinned | ✓ pass | `_atomic_write_skill` deleted; 5 internal callers at `co_cli/tools/system/skills.py:169,195,221,272,366` call `atomic_write_text` directly; `write_curator_state` body `co_cli/skills/curator.py:168-171` no mkdir; `write_records` body `co_cli/skills/usage.py:91-94` no mkdir; `write_skill_file` body `co_cli/skills/installer.py:81-85` no mkdir; `tool_io.py:100-101` content-dedup guard preserved around `atomic_write_text(file_path, content, errors="replace")` |
| TASK-3 | `_in_transaction` 0 hits; MemoryTransaction in place | ✓ pass | `MemoryTransaction` class `co_cli/memory/memory_store.py:229-278`; no explicit BEGIN in `__enter__` (lines 242-247); public `index()` always commits at `:466`, `index_chunks()` at `:526`, `remove_chunks()` at `:552`, `remove()` at `:1270`; `_remove_no_commit` calls `_remove_chunks_no_commit` first at `:1261`; `SkillIndex.upsert` at `skills/index.py:51` uses `tx.index/tx.index_chunks`; new tests `test_nested_transaction_raises`, `test_transaction_method_outside_with_raises`, `test_transaction_remove_rolls_back_on_exception` real-sqlite, no mocks |
| TASK-4 | conventions + docstring + spec sweep | ✓ pass | `agent_docs/code-conventions.md:52` cites `co_cli.persistence.atomic.atomic_write_text` (+ `atomic_write_bytes`) with mkdir contract; `:54` carries the `with store.transaction() as tx:` rule with "hidden transaction state forbidden"; `co_cli/tools/files/write.py:497-498` carries atomicity contract note; `docs/specs/knowledge.md:74` + `:125` updated to `co_cli/persistence/atomic.py::atomic_write_text` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Redundant `knowledge_dir.mkdir(parents=True, exist_ok=True)` preceding `atomic_write_text` in `save_dream_state` — violates TASK-2 acceptance ("no adjacent mkdir+atomic_write anywhere in `co_cli/`"). Primitive's built-in mkdir makes the L104 call redundant since `path.parent == knowledge_dir`. | `co_cli/memory/dream.py:104` | blocking | Deleted L104; behavior identical (`atomic_write_text` now does the mkdir internally) |
| Stale spec reference: `Writes use atomic_write() (temp-file + os.replace)` — old pre-3.5c-pre name; TASK-4 sweep missed it. | `docs/specs/knowledge.md:74` | blocking | Updated to `Writes use co_cli.persistence.atomic.atomic_write_text (tempfile + os.replace, parent mkdir built-in)` |
| Stale file-table row: `co_cli/memory/_mutator.py \| atomic_write() — temp-file + os.replace write helper` — points to non-existent file (the `_mutator.py` underscore form predates 3.5c-pre's promotion). TASK-4 sweep missed it. My orchestrate-dev sweep grep used `memory.mutator` (dot-form), which didn't catch the `_mutator.py` path-form. | `docs/specs/knowledge.md:125` | blocking | Replaced row with `co_cli/persistence/atomic.py \| atomic_write_text() / atomic_write_bytes() — full-overwrite atomic write helpers …` |
| Subagent advisory: TASK-1 "9th caller" (tool_io.py) flagged as plan/impl undercount | n/a | false positive | tool_io.py's migration is TASK-2's fold scope, not TASK-1's import-migration list. Plan TASK-1 count of 8 is correct. |
| Subagent advisory: code-conventions / write.py uses `co_cli.persistence.atomic.atomic_write_text` (full path) vs. plan's abbreviated `co_cli.persistence.atomic_write_text` | n/a | false positive | Implementation correct: `co_cli/persistence/__init__.py` is docstring-only per CLAUDE.md rule, so the short form wouldn't import. Plan text was an abbreviation. |

### Tests
- Command: `uv run pytest -v`
- Result: **487 passed, 0 failed** (211.71s)
- Log: `.pytest-logs/20260514-*-review-impl.log`

### Behavioral Verification
- `co_cli/persistence/atomic.py` exercised directly: `atomic_write_text` to deep-nested missing path (mkdir built-in), `atomic_write_bytes` round-trip, `errors="replace"` lossy encode all OK.
- `MemoryTransaction` behavior verified via Phase 5 full suite: rollback preserves docs+chunks rows on exception, nested raises `RuntimeError("Nested transactions not supported")`, outside-`with` raises `RuntimeError("MemoryTransaction methods called outside `with` block")` — all real sqlite, no mocks.
- No user-visible CLI surface changed (commands `chat`, `traces`, `tail` unchanged; no tool signature change; `file_write` gained a docstring atomicity note only — behavior-neutral). Standard `co status` not present in this project; primitive smoke + suite coverage is the strongest signal available.

### Final audits (10/10 PASS)
1. `co_cli/memory/mutator.py` removed — PASS
2. `co_cli.memory.mutator` imports = 0 — PASS
3. `tempfile.NamedTemporaryFile` in `co_cli/` only at `co_cli/persistence/atomic.py:28,50` — PASS
4. `_in_transaction` in `co_cli/` + `tests/` = 0 — PASS
5. `_atomic_write_skill` = 0 — PASS
6. `tempfile.*` in `co_cli/skills/ co_cli/agents/ co_cli/tools/` = 0 — PASS
7. `docs/specs/` stale refs (`MemoryStore.transaction \| memory.mutator \| _in_transaction \| _mutator \| atomic_write()`) = 0 — PASS
8. Adjacent `mkdir(parents=True...)` + `atomic_write_text/bytes` within 5-line window across `co_cli/` = 0 — PASS
9. `code-conventions.md` carries `with store.transaction() as tx:` rule — PASS
10. `file_write` docstring carries `co_cli.persistence.atomic.atomic_write_text` reference — PASS

### Overall: PASS
Two real blocking findings (`dream.py:104` redundant mkdir; `knowledge.md:74,125` stale `_mutator.py`/`atomic_write()` refs) auto-fixed in Phase 4. Full suite 487/487 green; lint clean; all 10 audits pinned. TL may proceed to `/ship persistence-batch-redesign`.

## Gate 1 — PO

**Verdict: PASS (post-revision).** Blocking fixes B1–B3 from the prior review have been applied to the plan body; non-blocking N1–N4 items have explicit resolutions. Plan is ready for `/orchestrate-dev` once the hard prerequisite (3.5c-pre commit) is confirmed in tree.

### Revision summary

**B1 — Wrapper-deletion scope corrected.**
- Audit established `_atomic_write_skill` is the only true thin wrapper (5 internal callers, just mkdir + atomic write). Fold confirmed.
- `write_curator_state` (3 callers via `_curator_state_path(deps)` + JSON) and `write_records` (5 callers via `_sidecar_path(deps)` + JSON) are kept; only the now-redundant `path.parent.mkdir(...)` line is removed from each body. Signatures and call sites unchanged.
- Caller migration map, Context #3, In scope, and TASK-2 file list all reflect this.

**B2 — TASK-3 acceptance criteria refreshed for D12.**
- Explicit enumeration of `_remove_no_commit` + `_remove_chunks_no_commit` private splits, public `remove()` + `remove_chunks()` always-commit wrappers, chunks-before-docs ordering preservation.
- New test case `test_transaction_remove_rolls_back_on_exception` added to Testing section.
- Acceptance grep on `_in_transaction` is now ZERO hits (no surviving symbol).

**B3 — Nesting-guard flag renamed to `_transaction_open`.**
- D13 constraint updated. Behavioural Constraint #6 added: "No `_in_transaction` symbol survives." TASK-3 acceptance asserts `grep -rn "_in_transaction" co_cli/ tests/` returns zero hits. Removes name-reuse footgun.

**N1 — Bundling rationale documented.**
- Single PR confirmed. Rationale: TASK-4 doc-sync wants the persistence module path and transaction call shape live simultaneously. Stated in Scope framing and Shipping order.

**N2 — `sync_dir` commit batching explicitly out of scope.**
- Documented in Out of scope and Deferred items with trade-off note (atomicity vs. failure isolation on bad files). Tracked for future plan.

**N3 — 3.5c-pre prerequisite surfaced as Gate 1 verification step.**
- Relationship to other plans section and Shipping order both state the prerequisite explicitly. `mutator.py` must be committed before `/orchestrate-dev` runs.

**N4 — `tool_io.py` content-dedup preservation added to TASK-2 acceptance.**
- TASK-2 acceptance: "tool_io.py retains the `if not file_path.exists():` guard around the new `atomic_write_text(..., errors="replace")` call (verify by reading `tool_io.py:90-110` after edit)." Failure Modes #4 also documents this.

### Scope verification

- All four TASKs have testable acceptance criteria with grep audits.
- Task graph parallelism preserved (TASK-2 ⊥ TASK-3 after TASK-1).
- Deletions match zero-backward-compat rule (no shims, no aliases).
- 13 caller sites enumerated; 8 import path updates + 5 direct call substitutions + 3 wrapper-body edits + 1 tool_io fold + 1 SkillIndex.upsert rewrite.

### Gate 1 sign-off prerequisites

1. ✅ B1–B3 blocking fixes applied to plan body.
2. ✅ N1–N4 non-blocking items resolved with explicit decisions.
3. ✅ **3.5c-pre shipped at `6024a9d` (v0.8.190).** `co_cli/memory/mutator.py` in tree; `_index_no_commit` / `_index_chunks_no_commit` splits in place; 9 importers of `co_cli.memory.mutator` confirmed; `transaction()` + `_in_transaction` still present (to be deleted by TASK-3).

**Plan cleared for `/orchestrate-dev`.**
