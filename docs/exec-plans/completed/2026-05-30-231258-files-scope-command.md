# filescope-command

> **Status: Gate-1 reviewed — ready to implement.** Adds a read-only `/filescope` slash command that
> prints the active filesystem scope — the resolved `file_search_roots` (read scope) and the
> `workspace_dir` (write anchor). Pure observability surface; no behavior change to any tool.
>
> **Dependency: CLEARED.** This plan consumes `deps.file_search_roots`, added by the
> **file-search-roots** plan, which shipped in `6390d73c` (archived). The field exists at
> `co_cli/deps.py:311`; `workspace_dir` at `co_cli/deps.py:307`. Spun off from that plan's
> "Follow-ups (out of scope)" section to keep its scope tight.

## Context

`file_search_roots` decouples read scope (a multi-root list) from the write anchor (`workspace_dir`).
Once it exists, an operator configuring `file_search_paths` (e.g. to add an Obsidian vault) has **no
surface to confirm which roots are actually active**. The built-in slash commands cover tools,
skills, memory, sessions, tasks, queue, etc. — none reports filesystem scope:

```
/help /clear /new /tools /history /compact /memory /dream /approvals
/skills /background /tasks /cancel /queue /resume /sessions /reasoning
```

`/tools` confirms `file_search` / `file_read` *exist*; nothing shows the roots they cover or the
anchor writes land under.

**Failure cost:** silent misconfiguration. An empty or typo'd `file_search_paths` leaves the vault
unreachable with zero feedback — `file_search` simply returns no vault hits, indistinguishable from
"nothing matched." A one-line scope readout turns a silent gap into an obvious one.

## Problem & Outcome

**Problem:** no read-only surface exposes the resolved read scope (`file_search_roots`) and write
anchor (`workspace_dir`), so a misconfigured or empty root list is invisible until searches quietly
miss.

**Outcome:** `/files` prints the active read roots and the write anchor, so an operator can confirm
scope at a glance and catch a bad `file_search_paths` immediately.

## Design (settled)

A leaf read-only command, identical in shape to `/tools` (`co_cli/commands/tools.py`): a handler in
its own module, registered in `BUILTIN_COMMANDS`, reading `ctx.deps` and printing via `console`. It
mutates nothing and returns `None` (the `LocalOnly` path). Registration auto-wires help listing
(`help.py` iterates `BUILTIN_COMMANDS`) and tab-completion (`completer.py`), and the name is
auto-reserved against skill-name collisions (`registry.py` `filter_namespace_conflicts`).

Output (no args), e.g.:

```
File search roots (read scope):
  1. /Users/me/workspace_genai/co-cli
  2. /Users/me/Documents/obsidian/KnowledgeBase
  3. /Users/me/Documents/obsidian/KnowledgeBas        (missing)
Write anchor (workspace_dir): /Users/me/workspace_genai/co-cli
```

Each root carries an existence marker: a root that fails `Path.exists()` is flagged `(missing)`.
This is the command's whole point — a typo'd or stale `file_search_paths` entry otherwise prints
back looking exactly as intended, and the misconfiguration stays silent. The marker turns it into
an obvious one. `Path.exists()` is a read, so this stays within BC-1.

When `file_search_roots == [workspace_dir]` (default/unconfigured), show the single root and note it
is the default scope, so the zero-config case reads as intentional, not empty.

## Behavioral Constraints

- **BC-1 (read-only):** the command never mutates deps, config, or filesystem; it only reads
  `ctx.deps.file_search_roots` and `ctx.deps.workspace_dir` and prints. Returns `None`.
- **BC-2 (no new deps surface):** consumes the existing `deps.file_search_roots` / `deps.workspace_dir`
  fields as-is; this plan adds no deps/config fields (file-search-roots owns those).
- **BC-3 (headless-safe):** the handler runs in REPL context like every other built-in; it does not
  depend on `ctx.completer` / `ctx.input_queue` / `ctx.frontend` (any of which may be `None`).

## Task naming decision (settled at Gate 1)

Command name: **`/filescope`**. It is the only candidate that names what the command actually
outputs — file *scope* (read roots + write anchor). `/files` / `/file` were rejected: they imply
"list files" or "act on one file" and collide with the `file_search` / `file_read` tool surface.
`/roots` is accurate but undersells the write-anchor line. `/filescope` is a single unbroken token,
matching every existing builtin (`/approvals`, `/reasoning`), and tab-completes cleanly.

## ✓ DONE TASK-1 — add the `/filescope` slash command

- files: `co_cli/commands/filescope.py` (new), `co_cli/commands/core.py` (register)
- New module `co_cli/commands/filescope.py` with `async def _cmd_filescope(ctx: CommandContext,
  args: str) -> None` — mirror `tools.py`: read `ctx.deps.file_search_roots` and
  `ctx.deps.workspace_dir`, print a numbered read-scope list + the write anchor via `console`. Each
  root that fails `Path.exists()` is flagged `(missing)`. If the roots list is exactly
  `[workspace_dir]`, label it as the default (workspace-only) scope. `args` is ignored.
- Register in `core.py`: add `from co_cli.commands.filescope import _cmd_filescope` and
  `BUILTIN_COMMANDS["filescope"] = SlashCommand("filescope", "Show file search roots (read scope)
  and the workspace write anchor", _cmd_filescope)`.
- done_when: `/filescope` appears in `BUILTIN_COMMANDS`; running it in a REPL prints every entry of
  `deps.file_search_roots` plus `deps.workspace_dir`; a test builds a `CommandContext` with deps
  whose `file_search_roots == [workspace, vault]` and asserts both paths appear in the rendered
  output; a second test points a root at a non-existent path and asserts the output flags it
  `(missing)`; AND `uv run pytest` over the commands tests passes.
- success_signal: an operator types `/filescope` and sees the vault listed as a read root after
  configuring `file_search_paths` — and a typo'd vault path shown flagged `(missing)`.
- prerequisites: none — `deps.file_search_roots` exists (`co_cli/deps.py:311`).

## Acceptance / verification

- `/filescope` is listed by `/help` and tab-completes (auto-wired via `BUILTIN_COMMANDS`).
- Output lists every resolved read root and the write anchor; the default single-root case is
  labeled as workspace-only, not shown as ambiguous/empty.
- A read root that does not exist on disk is flagged `(missing)` in the output.
- The command mutates nothing (BC-1) and runs without `completer`/`input_queue`/`frontend` (BC-3).
- Specs: `docs/specs/tui.md` gains the `/filescope` row in the built-in slash-command table
  (the `| Command | Args | Description | Returns |` table, ~lines 198-213).

## Decisions (settled at Gate 1)

1. **Command name:** `/filescope` — names the output (file scope = read roots + write anchor);
   avoids the "list files" / "act on a file" implication of `/files` / `/file` and the
   `file_search`/`file_read` tool collision. See "Task naming decision".
2. **Missing-root marker:** each root is flagged `(missing)` when it fails `Path.exists()`.
   Required, not optional — without it the command only echoes config and the silent-misconfig
   failure mode it exists to catch stays silent.
3. **Default-case label:** show the single workspace root with a "default scope" note so zero-config
   reads as intentional, not empty.

---

> Gate 1 — PASS (reviewed 2026-06-03). Ready for `/orchestrate-dev`.

## Delivery Summary — 2026-06-03

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `/filescope` in `BUILTIN_COMMANDS`; prints all `file_search_roots` + `workspace_dir`; both-paths test + `(missing)` test pass under pytest | ✓ pass |

**Files changed:**
- `co_cli/commands/filescope.py` (new) — `_cmd_filescope` handler; numbered read-scope list, default-scope label, `(missing)` marker via `Path.exists()`, write-anchor line. Prints with `soft_wrap=True` so long paths don't wrap.
- `co_cli/commands/core.py` — import + `BUILTIN_COMMANDS["filescope"]` registration.
- `tests/commands/test_filescope_command.py` (new, extra file) — registration, all-roots-and-anchor, missing-root-flagged.
- `docs/specs/tui.md` — `/filescope` row added to the built-in slash-command table.

**Tests:** scoped — 3 passed, 0 failed
**Doc Sync:** narrow (`docs/specs/tui.md` table row) — clean

**Note:** one extra file beyond `files:` — `tests/commands/test_filescope_command.py`. The plan's `done_when` mandated tests but listed no test path; added under the conventional `tests/commands/` location.

**Implementation note:** initial scoped run failed because rich wrapped long temp paths mid-token at the narrow capture width. Fixed by printing path lines with `soft_wrap=True` (paths should overflow, not wrap) — no test-shaping of production output.

**Overall: DELIVERED**
`/filescope` registered, auto-wired into `/help` and tab-completion via `BUILTIN_COMMANDS`; scoped tests green, lint clean, spec synced.

## Implementation Review — 2026-06-03

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `/filescope` in `BUILTIN_COMMANDS`; prints all `file_search_roots` + `workspace_dir`; both-paths test + `(missing)` test pass | ✓ pass | `core.py:47` registers `BUILTIN_COMMANDS["filescope"]`; `filescope.py:11-24` reads `deps.file_search_roots`/`deps.workspace_dir`, numbered list + write-anchor line; `filescope.py:22` `(missing)` via `root.exists()`; auto-wired into `/help` (`help.py:13` iterates `BUILTIN_COMMANDS.values()`) and completer/namespace filter (`registry.py:31`). `done_when` re-run: 3 passed. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `test_filescope_is_registered` asserts dict membership (structural) | `tests/commands/test_filescope_command.py:48` | minor | Kept — `done_when` explicitly mandates the `BUILTIN_COMMANDS` registration check; rendering is covered functionally by the other two tests. No change. |

### Behavioral Constraints
- BC-1 (read-only): handler only reads deps + `Path.exists()`, returns `None` — confirmed `filescope.py:11-25`.
- BC-2 (no new deps surface): consumes existing `file_search_roots`/`workspace_dir` — no deps/config fields added.
- BC-3 (headless-safe): no `completer`/`input_queue`/`frontend` access — confirmed.

### Tests
- Command: `uv run pytest tests/commands/ tests/test_flow_slash_dispatch.py tests/test_flow_compaction_slash_commands.py tests/test_flow_queue_command.py`
- Result: 21 passed, 0 failed
- Scope note: full-repo suite was scoped to the slash-command blast radius (new isolated module + one registry line). The working tree carries extensive **unrelated** in-progress changes (compaction, llm/call, index/store, ~20 other test files) from other streams; a full run would conflate their signal and is out of this plan's scope.
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Behavioral Verification
- `uv run co status`: N/A — no `status` subcommand exists in this CLI (`co --help`: chat/tail/trace/dream/google).
- Real dispatch path: `dispatch("/filescope", ctx)` with `file_search_roots=[workspace, real-vault, typo'd-vault]` rendered the numbered read scope, flagged the typo'd path `(missing)`, printed the write anchor, and returned `LocalOnly`.
- `success_signal` verified: operator sees the vault listed as read root #2 and the typo'd vault path flagged `(missing)`.

### Overall: PASS
Single read-only observability command; `done_when` met, behavioral `success_signal` confirmed via real dispatch, slash-command suite green, lint clean — ready to ship.
