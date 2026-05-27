# Tool Gap Batch 3 — `shell_exec` pty + `session_search` role_filter

Task type: code

## Context

Batch 3 of the ROI-ordered tool-parity gaps
(`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md` §1.5, §1.3, §5) — the two
harder items. Both carry a **grounding caveat that downgrades the research
doc's optimistic framing**; this plan surfaces them honestly and recommends
scoping/deferral rather than shipping a misleading capability.

- **`shell_exec` `pty`** (§1.5) — hermes's `terminal(pty=True)` runs interactive
  CLIs. **Caveat:** co's `shell_exec` is one-shot blocking with **no stdin
  channel**, so pty buys output *fidelity* (isatty/ANSI/line-buffering), not
  interactive *drive*. True interactive drive needs a stdin write channel co
  doesn't have (the §1.5/§3 `process.write/submit/close` gap).
- **`session_search` `role_filter`** (§1.3) — hermes filters at message level.
  **Caveat:** co's recall chunks are **role-mixed by construction**, so clean
  parity needs a schema change, not the "low effort" pass-through the doc
  assumed.

Batch 3 may slip without blocking Batches 1–2. Either task may be **deferred**
on its open question rather than shipped half-true.

### Hermes parity reference (grounded, not copied)

- **pty** (`hermes-agent/tools/process_registry.py:543`): hermes spawns
  `ptyprocess.PtyProcess.spawn([shell, "-lic", cmd], dimensions=(30,120))` with
  a daemon **reader thread**, falls back to pipe mode on `ImportError`, and
  disables pty for stdin-piped commands (`_command_requires_pipe_stdin`, e.g.
  `gh auth login --with-token`). Deps: `ptyprocess` (unix) / `pywinpty` (win).
  This machinery exists because hermes has a **persistent interactive process
  registry** with `process.write`/`submit`/`close`. **co has none of that** —
  `shell_exec` is async one-shot. So co uses **stdlib `pty.openpty()` +
  asyncio**, no `ptyprocess` dep, no reader thread, no interactive write path.
- **role_filter** (`hermes-agent/tools/session_search_tool.py:567`,
  `hermes_state.py:2189`): comma-separated `"user,assistant,tool"` parsed to a
  list, applied as `WHERE m.role IN (?, …)` against an FTS5 index where **each
  indexed unit is a single message with one `role`**. co's index unit is a
  multi-message chunk (`co_cli/session/chunker.py` packs `User:`/`Assistant:`/
  `Tool[…]:` lines into one chunk) — there is no single `role` column to filter.

### Verified current state (2026-05-27)

- `shell_exec` (`co_cli/tools/shell/execute.py:17`) → `ctx.deps.shell.run_command`
  (`co_cli/tools/shell_backend.py:18`): `asyncio.create_subprocess_exec("sh","-c",
  cmd, stdout=PIPE, stderr=STDOUT, start_new_session=True)`, `proc.communicate()`
  with `asyncio.wait_for(timeout)`, `kill_process_tree` on timeout. **No stdin,
  no pty.** Docstring states "No interactive input — commands that prompt for
  stdin will hang and timeout."
- `session_search` (`co_cli/tools/session/recall.py:126`): `(query="",
  limit=3)`. `_search_sessions` calls `ctx.deps.session_store.search(query,
  limit=…)` → chunk dicts `{session_id, when, source, chunk_text, start_line,
  end_line, score}`. `r.source` exists; no per-message role on the chunk.
- `chunker.py:66+` renders role-prefixed lines into a chunk; a chunk spans
  multiple roles.

## Problem & Outcome

**Problem.** Some CLIs change output when stdout isn't a TTY (no color, full
buffering, "not a terminal" refusals); recall can't be narrowed to "what *I*
said" vs "what *the assistant* said."

**Outcome (scoped honestly).**
1. `shell_exec(pty=True)` runs the one-shot command under a pseudo-terminal so
   the child sees a TTY — **output fidelity only**, clearly documented as not
   interactive drive.
2. `session_search(role_filter=…)` — **only if** an honest signal is achievable
   in-batch (post-filter); otherwise deferred with the schema-change path
   documented.

## Scope

### In scope
- `co_cli/tools/shell/execute.py` + `co_cli/tools/shell_backend.py` — `pty`
  flag plumbed to a pty-backed `run_command` path.
- `co_cli/tools/session/recall.py` (+ `co_cli/session/` search plumbing) —
  `role_filter`, per Open Q2 resolution.
- `docs/specs/tools.md` — both surfaces, with the honest caveats.
- Tests: `tests/test_flow_shell.py`, `tests/test_flow_session_search.py`.

### Out of scope
- **Interactive stdin drive** (sending input to a prompting CLI) — needs a write
  channel on `task_*`; separate, larger gap (§1.5/§3). Explicitly **not** what
  pty delivers here.
- **`ptyprocess`/`pywinpty` dependency** — not needed for co's one-shot model.
- **Per-message role re-indexing** of sessions — the schema-change path; a
  separate plan if post-filter proves insufficient (Open Q2).
- **Anchored-scroll `session_search` shape** — `session_view` already covers
  verbatim slices; only revisit if a real need surfaces.

## Behavioural Constraints
1. **`pty=False` default is byte-for-byte unchanged** — current `run_command`
   path untouched when pty is off.
2. **pty stays blocking + one-shot** — same timeout/workdir/policy gate;
   `kill_process_tree` on timeout still works (pty child in its own session).
   No stdin write path is added.
3. **No new dependency** — stdlib `pty`/`os`/`asyncio` only.
4. **role_filter must be honest** — it filters on a real per-role signal with
   documented semantics ("chunks containing a matching <role> line"); it must
   **never** silently return role-mixed chunks as if filtered. If neither honest
   option fits the batch, **defer** the task (Open Q2).

## High-Level Design

### `shell_exec` pty (output fidelity)
- Decorator/signature: add `pty: bool = False` to `shell_exec`; pass through to
  `run_command(cmd, timeout, cwd, extra_env, pty=…)`.
- `ShellBackend.run_command` pty branch:
```python
if pty:
    master, slave = pty.openpty()
    proc = await asyncio.create_subprocess_exec(
        "sh", "-c", cmd, cwd=…, env=…, start_new_session=True,
        stdin=slave, stdout=slave, stderr=slave,
    )
    os.close(slave)
    # drain master fd via loop.add_reader / asyncio.to_thread os.read until EOF,
    # bounded by asyncio.wait_for(timeout); kill_process_tree on timeout.
    return proc.returncode, decoded_output
```
- Output is combined (the TTY merges stdout/stderr); decode with
  `errors="replace"`. Document that ANSI escapes may appear (the point of pty).

### `session_search` role_filter (Open Q2 — recommend post-filter)
- Add `role_filter: str | None` (comma-separated `"user,assistant,tool"`),
  parsed like hermes (`[r.strip() for r in role_filter.split(",") if r.strip()]`).
- **Option B (recommended, no schema change):** in `_search_sessions`, after
  fetching chunk hits, keep a chunk only if it contains a line whose role prefix
  ∈ filter (the chunker's `User:`/`Assistant:`/`Tool[…]:` prefixes). Surface the
  semantics in the docstring and trim `chunk_text` to matching-role lines.
- **Option A (deferred):** add a role column / per-message indexing in
  `co_cli/session/` for clean `WHERE role IN (…)` parity — re-architects
  co's deliberate multi-message chunking; only if B's recall quality is poor.

## Tasks

### TODO — TASK-1 — `shell_exec` `pty=True` (output fidelity)
Files: `co_cli/tools/shell/execute.py`, `co_cli/tools/shell_backend.py`.
Impl: add `pty` flag; pty branch in `run_command` using stdlib `pty.openpty()`
+ asyncio master-fd drain; preserve timeout/kill semantics.
**done_when:**
- `pty=False` path is unchanged (existing shell tests pass untouched).
- `shell_exec("python3 -c 'import sys;print(sys.stdout.isatty())'", pty=True)`
  returns `True`; with `pty=False` returns `False`.
- Timeout under pty still kills the process group and surfaces partial output
  (mirror the existing timeout test, with `pty=True`).
- No `ptyprocess`/`pywinpty` import; `uv pip list` unchanged.
- Docstring states pty = output fidelity, **not** interactive stdin drive.

### TODO — TASK-2 — `session_search` `role_filter` (or defer)
Files: `co_cli/tools/session/recall.py` (+ `co_cli/session/` if Option A).
Impl: Option B post-filter per High-Level Design.
**done_when (if shipped):**
- `role_filter="user"` returns only chunks containing a `User:` line; assistant
  /tool-only chunks are dropped.
- `chunk_text` shown is trimmed to matching-role lines (no misleading
  full-chunk display).
- No filter / empty filter → today's behavior unchanged.
- Docstring documents the "chunk contains a matching <role> line" semantics.
**defer_when:** Option B's recall is too lossy in a quick spike → record the
Option A schema-change path as a follow-up plan and drop this task from Batch 3
(do **not** ship a filter that returns role-mixed chunks).

### TODO — TASK-3 — Spec + gate
Files: `docs/specs/tools.md`.
**done_when:** `shell_exec` entry documents `pty` (fidelity-only caveat);
`session_search` entry documents `role_filter` semantics (or notes it deferred);
`scripts/quality-gate.sh full` clean.

## Testing
- `tests/test_flow_shell.py` — real `pty=True` isatty assertion + pty timeout
  kill (real subprocess, no mocks).
- `tests/test_flow_session_search.py` — seed a real session with distinct
  user/assistant/tool turns; assert role_filter narrows to the right chunks.

## Open Questions
1. **pty value vs effort** — given co can't drive interactive CLIs (no stdin
   channel), is output-fidelity pty worth it, or is the real investment the
   `task_*` stdin write channel (the actual interactive-drive blocker)?
   **Rec:** ship the cheap fidelity pty here (small, self-contained), and file
   the write-channel as a separate higher-effort gap — don't conflate them.
2. **role_filter signal** — Option B (post-filter, no schema change, honest but
   chunk-granular) vs Option A (per-message re-index, clean parity, large).
   **Rec:** spike B; if recall quality holds, ship B; else **defer** to an
   Option-A follow-up. Never ship a mixed-chunk passthrough.
3. **pty output decoding** — strip ANSI escapes before returning, or keep them?
   **Rec:** keep raw (ANSI is the reason to use pty); note it in the docstring
   so callers can strip if needed.

## Deferred items
- Interactive stdin drive (`task_*` write/submit/close channel) — the real
  interactive-CLI capability; separate, larger plan.
- `terminal.watch_patterns` — mid-process regex notifier; niche, follows the
  write channel.
- Per-message session re-indexing (role_filter Option A) — if Option B is
  insufficient.

## Shipping order
TASK-1 (pty) and TASK-2 (role_filter spike) are independent. TASK-1 ships
regardless; TASK-2 ships **or defers** on its spike. TASK-3 gates whatever
lands. Batch 3 does not block Batches 1–2.
