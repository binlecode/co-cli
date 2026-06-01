# files-scope-command

> **Status: draft plan (not yet Gate-1 approved).** Adds a read-only `/files` slash command that
> prints the active filesystem scope — the resolved `file_search_roots` (read scope) and the
> `workspace_dir` (write anchor). Pure observability surface; no behavior change to any tool.
>
> **Hard dependency:** this plan consumes `deps.file_search_roots`, which does not exist until the
> **file-search-roots** plan (`2026-05-30-221735-file-search-roots.md`) ships (its TASK-2 adds the
> deps field). **Do not start this plan until file-search-roots is shipped.** Spun off from that
> plan's "Follow-ups (out of scope)" section to keep its scope tight.

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
Write anchor (workspace_dir): /Users/me/workspace_genai/co-cli
```

When `file_search_roots == [workspace_dir]` (default/unconfigured), show the single root and note it
is the default scope, so the zero-config case reads as intentional, not empty.

## Behavioral Constraints

- **BC-1 (read-only):** the command never mutates deps, config, or filesystem; it only reads
  `ctx.deps.file_search_roots` and `ctx.deps.workspace_dir` and prints. Returns `None`.
- **BC-2 (no new deps surface):** consumes the existing `deps.file_search_roots` / `deps.workspace_dir`
  fields as-is; this plan adds no deps/config fields (file-search-roots owns those).
- **BC-3 (headless-safe):** the handler runs in REPL context like every other built-in; it does not
  depend on `ctx.completer` / `ctx.input_queue` / `ctx.frontend` (any of which may be `None`).

## Task naming decision

Command name: **`/files`** (matches the user's first-listed phrasing). Alternative `/roots` is more
precise (the command shows roots + anchor, not files), but `/files` is the discoverable noun an
operator reaches for. Settle at Gate 1 if the reviewer prefers `/roots`; the implementation differs
only by the registry key + module name.

## TASK-1 — add the `/files` slash command

- files: `co_cli/commands/files.py` (new), `co_cli/commands/core.py` (register)
- New module `co_cli/commands/files.py` with `async def _cmd_files(ctx: CommandContext, args: str)
  -> None` — mirror `tools.py`: read `ctx.deps.file_search_roots` and `ctx.deps.workspace_dir`,
  print a numbered read-scope list + the write anchor via `console`. If the roots list is exactly
  `[workspace_dir]`, label it as the default (workspace-only) scope. `args` is ignored.
- Register in `core.py`: add `from co_cli.commands.files import _cmd_files` and
  `BUILTIN_COMMANDS["files"] = SlashCommand("files", "Show file search roots (read scope) and the
  workspace write anchor", _cmd_files)`.
- done_when: `/files` appears in `BUILTIN_COMMANDS`; running it in a REPL prints every entry of
  `deps.file_search_roots` plus `deps.workspace_dir`; a test builds a `CommandContext` with deps
  whose `file_search_roots == [workspace, vault]` and asserts both paths appear in the rendered
  output; AND `uv run pytest` over the commands tests passes.
- success_signal: an operator types `/files` and sees the vault listed as a read root after
  configuring `file_search_paths`.
- prerequisites: **file-search-roots plan shipped** (`deps.file_search_roots` exists).

## Acceptance / verification

- `/files` is listed by `/help` and tab-completes (auto-wired via `BUILTIN_COMMANDS`).
- Output lists every resolved read root and the write anchor; the default single-root case is
  labeled as workspace-only, not shown as ambiguous/empty.
- The command mutates nothing (BC-1) and runs without `completer`/`input_queue`/`frontend` (BC-3).
- Specs: `docs/specs/tools.md` (or the commands reference, wherever the built-in slash-command list
  lives) gains the `/files` row.

## Decisions (for Gate 1)

1. **Command name:** `/files` (recommended, user's phrasing) vs `/roots` (more precise). See "Task
   naming decision". Recommendation: `/files`.
2. **Default-case label:** show single workspace root with a "default scope" note vs print nothing
   special. Recommendation: label it, so zero-config reads as intentional.

---

> Gate 1 — PO review required before proceeding (and file-search-roots must ship first).
