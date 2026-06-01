# file-search-roots

> **Status: draft plan (not yet Gate-1 approved).** Owns the **complete removal of the obsidian
> tooling** (surface + phantom) and its replacement. Generalize the file-read/search scope from a
> single `workspace_dir` to a dedicated multi-root list, `file_search_roots`; the Obsidian vault
> becomes the first read-only consumer. Task 0 (the `obsidian_*` tool-surface removal) is already
> done in the working tree; Tasks 1–5 build the replacement and delete the phantom. Supersedes the
> deferred "obsidian as memory provider / Direction A" idea.

## Context

The three `obsidian_*` tools were removed (2026-05-30) as the wrong surface. We then evaluated how
a 2026 frontier agent should reach an Obsidian vault and rejected two earlier ideas:

- **Direction A (re-index the vault into co's IndexStore under `index_source='obsidian'`, surfaced
  via a `memory_search` corpus filter)** — a 2023-era self-hosted-RAG pattern. Stale second copy,
  per-call resync, reimplements (worse) what the vault tooling already does. Abandoned.
- **A′ (Obsidian Local REST API MCP server)** — frontier-valid and rich (Dataview, graph,
  backlinks, surgical edits), but requires Obsidian running + plugin + server + token (dies
  headless), and reintroduces a `memory_search`-vs-obsidian routing decision with a steer co only
  half-controls (the MCP tool's description is server-owned).

**Chosen: B′ — filesystem-native.** Treat the vault as files and let co's existing ripgrep-based
`file_search` / `file_read` read it. ripgrep is the production-grade, frontier-standard primitive
for agentic file search (Claude Code, Cursor, Codex CLI, Aider all use it); a personal vault sits
well inside its sweet spot (<100k files). This needs **no index** (`file_search` shells out to
`rg` live — no IndexStore, no sync, no staleness) and **no new tool surface** (the vault folds into
`file_search`, so the routing question dissolves: `file_search` = files on disk, `memory_search` =
co's curated memory).

**The gap B′ exposes:** co's file tools are single-root and hard-confined. `workspace_dir` is one
`Path` (`deps.py:307`), and both read and write tools funnel every path through
`enforce_workspace_boundary(path, workspace_dir)` (`fs_guards.py:6`), which rejects anything not
`is_relative_to(workspace_dir)`. A vault outside the workspace is unreachable, and a symlink can't
cheat it (`.resolve()` follows the link to the real external path, which then fails the check).

So B′ requires a small, real architectural change: a dedicated multi-root read/search scope.

**Routing — relocated, not dissolved (intended).** B′ makes vault notes reachable only via
`file_search` / `file_read`; they never surface through `memory_search` and co loses any signal
that they are higher-trust declarative knowledge. Promoting a note to durable memory stays a
deliberate `memory_manage` act. This is the intended v1 trade (no index, no staleness), not an
oversight.

## Problem & Outcome

**Problem:** co's file read scope is hard-confined to a single `workspace_dir`; an Obsidian vault
(or any folder) outside the workspace is unreachable, and a symlink can't cheat the `.resolve()`
boundary check. The leftover `obsidian_*` phantom (config field, capability flag, `_check_obsidian`,
`OBSIDIAN` enums) lingers as dead forward-hooks for the abandoned Direction A.

**Outcome:** a dedicated multi-root read/search scope (`file_search_roots`) lets `file_search` /
`file_read` reach configured non-workspace roots (the vault first) while writes stay anchored to
`workspace_dir`; the obsidian phantom is deleted.

**Failure cost:** none silent — nothing breaks without this. The vault simply stays unreachable
(a capability gap, not a regression) and the abandoned-Direction-A phantom (enums, capability flag,
`_check_obsidian`) accrues as dead code.

### Design (settled)

The file-search scope is its **own dedicated variable**, decoupled from the write/cwd anchor:

```
file_search_roots: list[Path]   # DEDICATED — the file_read / file_search scope.
                                #   NOT derived from workspace_dir.
                                #   e.g. [<workspace path>, <obsidian vault>]

workspace_dir: Path             # SEPARATE — write / cwd anchor only
                                #   (file_write/file_patch land here; relative-path base)
```

Read scope and write scope are answered by *different* variables — no positional tiering:

```
file_read / file_search  → enforce_read_boundary(path, file_search_roots)   # under ANY root
file_write / file_patch  → enforce_write_boundary(path, workspace_dir)      # under workspace_dir
```

Consequences: writes never touch the vault (safe; no corruption / no conflict with a running
Obsidian app); obsidian stops being a special integration and becomes one entry in a general list;
and nothing obsidian-shaped survives in the index, so the phantom enums delete cleanly.

### Design principle (routing — applies beyond the vault)

This generalizes into co's standing knowledge/data-search routing rule:

- **External file/folder knowledge or data** → add the folder as an entry in `file_search_roots`;
  reached via `file_search` / `file_read`. Lexical, live filesystem read — no index, no sync, no
  staleness, read-only scope. Adding a folder is a config entry, not a new tool.
- **co's own curated memory** → the DB-backed IndexStore (FTS5/BM25 + hybrid recall) + MemoryStore
  semantics (kinds, decay, dream), surfaced via `memory_search` / `memory_view`.

The dividing line is **ownership + curation, not file format**: co owns and curates it → memory
pipeline; co only reads someone else's folder → `file_search_roots`. This is what dissolves the
routing question (`file_search` = files on disk, `memory_search` = curated memory) and rules out the
abandoned Direction A (re-indexing the vault — a stale second copy). Consequence (cf. PO-m-3): an
external note is reachable only via `file_search` until a deliberate `memory_manage` act promotes it
into curated memory — never automatic. The matching runtime-spec clarifications already landed in
`docs/specs/memory.md` ("Memory vs. files on disk") and `docs/specs/tools.md` ("Routing boundary");
the `file_search_roots` *mechanics* stay scoped to this plan's implementation (Task 5 / acceptance).

## Behavioral Constraints

- **BC-1 (writes stay workspace-anchored):** `file_write` / `file_patch` must reject any path that
  resolves outside `workspace_dir`, *even when that path is under a `file_search_root`*. Read scope
  never widens write scope.
- **BC-2 (no escape):** `..` traversal out of all roots raises `ValueError`; an in-vault symlink
  whose resolved target is under no root raises `ValueError`.
- **BC-3 (configured list is authoritative and total):** when `file_search_paths` is non-empty,
  `file_search_roots` is exactly that list resolved — no implicit `workspace_dir` append. An
  operator who lists only the vault gets vault-only read scope (workspace not searched). Only the
  unconfigured (empty) case defaults to `[workspace_dir]`.
- **BC-4 (zero-config behavior is byte-identical to today):** with `file_search_paths` empty,
  `file_search_roots == [workspace_dir]`; `file_search` / `file_read` display and accept paths
  exactly as today (relative-to-workspace, no root label). No new prompts, no surprise roots. This
  is the user-visible product guarantee of the default.
- **BC-5 (multi-root display round-trips):** when more than one root is configured, every
  `file_search` hit is displayed as an **absolute path**, and `file_read` accepts that absolute
  path verbatim. Absolute form is unambiguous across same-named subpaths and survives the path
  layer (see TASK-3a); no per-root label scheme is introduced.

---

## Task 0 — remove the `obsidian_*` tool surface  ✓ DONE (working tree, uncommitted)

The three live-query tools were the wrong surface (a parallel search path that bypassed the
uniform `memory_search`/`file_search` scoping). Removed directly this session; lint clean, 18
affected tests pass. Recorded as "RESOLVED by removal" in the small-model audit plan
(`2026-05-29-234336-tool-surface-small-model-audit.md`, Task 1).

- **Deleted** `co_cli/tools/obsidian/` package (the 3 tools + helpers + the lazy
  `_sync_obsidian_dir` indexer — all orphaned by the removal).
- **Rewired:** `agent/toolset.py` (import + integration comment); `tools/agents/delegation.py`
  (dropped the 3 names from `KNOWLEDGE_ANALYZE_SPEC` — would otherwise raise `ValueError` at build
  time); `tools/display.py`, `context/_tool_result_markers.py`, `tools/shell/execute.py` (removed
  obsidian display/marker/steer entries).
- **Tests:** removed `test_obsidian_tool_drops_out_without_vault_path` and the obsidian marker test
  (config-gate-drop invariant still covered by the google equivalent).
- **Specs:** `tools.md` (native count 34→31; DEFERRED 16→13; config-gated 10→7; group + Files rows
  removed; config row reworded), `agents.md` (verification row + build comment), `compaction.md`
  (marker list).
- **Kept (deleted later in Task 5):** `obsidian_vault_path` config, `_check_obsidian`, the
  capability flag, and the `OBSIDIAN` enum values — kept here only as transitional hooks; B′ makes
  them dead.

**Task ordering:** TASK-1 → TASK-2 → TASK-3a → TASK-3 → TASK-4 → TASK-6 (sequential; each depends on
the prior; TASK-6 documents the shipped surface so it lands after TASK-4). TASK-5 (phantom deletion)
is independent and may run any time after TASK-2 (which removes the `obsidian_vault_path` config/deps
it would otherwise double-own).

## ✓ DONE TASK-1 — split the boundary guard (`fs_guards.py`)

- files: `co_cli/tools/files/fs_guards.py`, `co_cli/tools/files/read.py`,
  `co_cli/tools/files/write.py`, `co_cli/tools/shell/execute.py`
- Replace the single `enforce_workspace_boundary(path, workspace_dir)` with two guards. Both keep
  today's **join-then-resolve** mechanism (`fs_guards.py:15`: `(base / path).resolve()` — so an
  absolute `path` ignores the left operand and passes through, a relative one is anchored to the
  base), then check containment. No filesystem-existence probing in either guard — they are pure
  boundary logic (existence is the caller's concern, e.g. `file_read`'s not-found branch).
  - `enforce_read_boundary(path, roots: list[Path]) -> tuple[Path, Path]` — the single-base guard
    lifted over a list: for each root **in order**, compute `(root / path).resolve()` and accept the
    **first** whose result `is_relative_to(root.resolve())`, returning `(resolved, root)`; raise
    `ValueError` if no root contains it. Consequences that fall straight out of `(root / path)`:
    an **absolute** `path` (the multi-root display form, BC-5) is accepted under whichever root
    contains it; a **relative** `path` anchors to `roots[0]` (it is always `is_relative_to(roots[0])`
    barring `..` escape), so single-root relative resolution is byte-identical to today
    (`roots[0] == workspace_dir`, BC-4) and multi-root relative also binds to `roots[0]` — the
    canonical cross-root form is absolute (BC-5), not relative.
  - `enforce_write_boundary(path, workspace_dir: Path) -> Path` — today's `enforce_workspace_boundary`
    behavior, renamed (unchanged single-base join-resolve-check).
- Both still block `..` traversal (the post-resolve `is_relative_to` check) and catch in-vault
  symlinks whose resolved target is under no root (`.resolve()` follows the link out of bounds).
- **Zero-backward-compat call-site sweep (all of them):** remove `enforce_workspace_boundary`; the
  call sites are: `read.py:358` (file_read) and `read.py:455` (file_search) → **read** guard;
  `write.py:406`, `write.py:443`, `write.py:513` and `shell/execute.py:70` (workdir) → **write**
  guard. Shell workdir stays write-anchored to `workspace_dir` (BC-1).
- done_when: `grep -rn enforce_workspace_boundary co_cli/ tests/` returns nothing; both
  `enforce_read_boundary` and `enforce_write_boundary` are defined in `fs_guards.py`; AND
  `uv run pytest tests/test_flow_files_read.py tests/test_flow_files_write.py` passes.
- success_signal: N/A (refactor — no behavior change yet; roots still default to workspace).
- prerequisites: none.

## ✓ DONE TASK-2 — add `file_search_roots` to config + deps

- files: `co_cli/config/core.py`, `co_cli/deps.py`
- **Config** (`config/core.py`): add `file_search_paths: list[str]` (default `[]`). Keep
  `workspace_path` as its own separate field (write anchor). **Remove `obsidian_vault_path`**
  (field at `:100` and the `OBSIDIAN_VAULT_PATH` env-map entry at `:143`).
- **Deps** (`deps.py`): add `file_search_roots: list[Path]`, resolved from `file_search_paths`.
  - **Default when unconfigured (empty):** `[workspace_dir]` (BC-4 — preserves today's behavior).
  - **Configured (non-empty):** exactly the resolved list, authoritative and total — no implicit
    `workspace_dir` append (BC-3).
  - Remove the `obsidian_vault_path` deps field + resolution + fork copy (`deps.py:308, 338-340,
    390`). De-dup with TASK-5: TASK-2 owns the deps.py/config.py obsidian removals; TASK-5 owns the
    rest.
- done_when: `uv run python -c` snippet builds deps with empty config and asserts
  `file_search_roots == [workspace_dir]`, then with `file_search_paths=[<tmp>]` asserts
  `file_search_roots == [Path(<tmp>).resolve()]` (no workspace appended).
- success_signal: an operator can configure a non-workspace folder and it appears in
  `deps.file_search_roots`.
- prerequisites: TASK-1.

## ✓ DONE TASK-3a — make path normalization multi-root-aware (`lifecycle.py` / `categories.py`)

- files: `co_cli/tools/categories.py`, `co_cli/tools/lifecycle.py`
- **The blocker:** `CoToolLifecycle.before_tool_execute` rewrites `args["path"] = str((workspace_dir
  / args["path"]).resolve())` for every tool in `PATH_NORMALIZATION_TOOLS = {file_read, file_write,
  file_patch}` (`lifecycle.py:295-297`, `categories.py:8-14`) **before** the tool's own guard runs.
  A vault-relative path is joined to `workspace_dir` and can never reach `file_read`'s read guard.
- **Fix:** remove `file_read` from `PATH_NORMALIZATION_TOOLS` (leave `file_write`, `file_patch` —
  they stay workspace-anchored, BC-1). `file_read` then resolves its raw `path` itself via
  `enforce_read_boundary(path, file_search_roots)` (wired in TASK-3). `file_search` was never in
  this set, so it is unaffected. Absolute paths (the multi-root display form, BC-5) pass through
  unchanged regardless.
- Note: `FILE_TOOLS` (compaction working-set tracking, `categories.py:17-24`) still includes
  `file_read`; tracking now sees the raw/resolved path `file_read` reports — acceptable, no behavior
  depends on it being workspace-joined.
- Note (refetch telemetry unaffected): the `refetch_attempt` check at `read.py:353`
  (`Path(path).resolve().is_relative_to(tool_results_dir)`) keys off **absolute** spill paths
  (`tool_results_dir` = `~/.co-cli/tool-results`, outside all roots); `.resolve()` is identical
  before/after, the guard still rejects out-of-root spill paths, so spill-refetch behavior is
  unchanged. No code change — stated to close the loop.
- Note (relative-path base): with `file_read` out of `PATH_NORMALIZATION_TOOLS`,
  `enforce_read_boundary` (TASK-1) owns relative-path resolution via its per-root `(root / path)`
  join. Single-root relative anchors to `roots[0]` (= `workspace_dir`, byte-identical to today, BC-4);
  multi-root relative also anchors to `roots[0]` since the canonical cross-root form is absolute
  (BC-5). No existence probing in the guard — `file_read` keeps its own not-found / typo-suggestion
  branch (`read.py:362-369`) against the resolved path.
- done_when: `file_read` is absent from `PATH_NORMALIZATION_TOOLS`; a test calls `file_read` on an
  absolute path under a non-workspace root and asserts the content is returned (not a boundary
  error and not a workspace-joined miss).
- success_signal: `file_read("<abs vault path>")` returns vault file content.
- prerequisites: TASK-2.

## ✓ DONE TASK-3 — thread roots through `file_search` / `file_read`

- files: `co_cli/tools/files/read.py`
- `file_read`: resolve raw `path` via `enforce_read_boundary(path, ctx.deps.file_search_roots)`.
- `file_search`: stop reading `ctx.deps.workspace_dir`; use `ctx.deps.file_search_roots`.
  - **Multi-root search is per-root iteration, NOT multi-path rg.** `_glob_ripgrep` runs `rg
    --files` with `cwd=resolved` and no dir arg by design (cwd-relative globs; absolute paths rebuilt
    via `resolved / p`) — passing multiple dir args breaks both the glob contract and the
    reconstruction. Instead: when `path` has an explicit prefix, resolve it via the read guard to its
    one containing root and search there (single-root, as today). When `path` is a broad glob (no
    prefix), iterate each root in `file_search_roots`, run the existing single-root machinery
    (`_glob_ripgrep` / `_glob_python` / `_grep_shell` / `_grep_python`) per root, then merge.
  - **Cross-root merge/sort/pagination — per code path (the three helpers behave differently):**
    - *rg-glob (`_glob_ripgrep`):* call each root with an over-fetch cap, concat entries, slice
      globally for `offset`/`limit`. Each root is pre-sorted by `--sortr=modified`; cross-root
      ordering is **per-root-grouped** — do NOT promise a true global mtime sort (glob entries are
      `{"name","type"}` dicts with no mtime; a global sort would require re-stat'ing every path).
    - *python-glob (`_glob_python`):* same shape; same no-global-mtime caveat.
    - *grep (`_grep_shell` / `_grep_python`):* these paginate **internally** and return an already
      sliced list plus a single-root `total_match_count` (`read.py:264-268`, `306-308`). Call each
      root with `offset=0, limit=fetch_cap` to **neutralize per-root pagination**, concat the
      per-root lists, slice globally, and **sum `total_match_count` across roots** (the displayed
      `count=` at `read.py:530` is currently one root's total). No mtime sort (results are line
      matches). `truncated` aggregates as: merged length before the global slice exceeds the page.
    - All paths: `_grep_shell` / `_grep_python` relativization currently takes one `workspace_dir`
      (`read.py:237-308`) — thread the per-root base through instead.
  - **Gate-1 clarification (over-fetch cap — define it).** `fetch_cap` is only defined for the glob
    path today (`read.py:462`). For the grep merge it MUST be the global `offset + limit` so the
    cross-root global slice can honor the page; a per-root `fetch_cap` smaller than `offset + limit`
    silently drops later-root results. **Unlimited case (`limit <= 0`):** today this is one
    workspace; multi-root makes it an unbounded content scan across every root (incl. a vault) with
    no per-root ceiling. Apply a sane over-fetch ceiling for the `limit <= 0` path (mirror the
    glob path's `200` default rather than truly unbounded) and `log`/note when it bounds coverage —
    do not fan an uncapped grep across roots.
  - **Gate-1 clarification (per-root tagging for absolute display — make it explicit).** The helpers
    relativize to their per-root base internally, so after the merge each entry has lost which root
    it came from. TASK-4's absolute display (BC-5) reconstructs `root / name`, so the merge step must
    tag each merged entry with its source root (or absolutize `name` at merge time). Decide this here,
    in TASK-3's merge, not as a bolt-on in TASK-4 — a lossy merge cannot round-trip.
  - Honor per-root `.gitignore`/hidden defaults as today (see Follow-up: vault `.obsidian/` noise).
- Write tools (`files/write.py`): unchanged scope — `enforce_write_boundary(path, workspace_dir)`.
- done_when: a test configures `[workspace, tmp_vault]`, drops a file in each, and asserts
  `file_search(content=<marker present in both>)` returns hits from BOTH roots in one call; AND
  `uv run pytest tests/test_flow_files_read.py` passes (single-root path unchanged).
- success_signal: a broad `file_search` returns vault hits alongside workspace hits.
- prerequisites: TASK-3a.

## ✓ DONE TASK-4 — multi-root path display (absolute form)

- files: `co_cli/tools/files/read.py`
- Per BC-5 and Gate-1 decision 2 (settled: **absolute**): when `len(file_search_roots) == 1` (the
  default/unconfigured case), display relative-to-that-root exactly as today (BC-4 — byte-identical;
  existing substring asserts in `tests/test_flow_files_read.py` stay green). When more than one root
  is configured, display each hit as its **absolute path**.
- Absolute hits round-trip: `file_read` (no longer path-normalized, TASK-3a) resolves the absolute
  path via the read guard directly. No per-root label scheme — absolute is unambiguous across
  same-named subpaths.
- **Gate-1 clarification:** the absolute form is reconstructed from the per-root tag carried out of
  the TASK-3 merge (`root / name`) — TASK-4 owns the display switch (`len(roots) > 1 → absolute`),
  but the source-root tagging is TASK-3's responsibility (see TASK-3 clarification). TASK-4 must not
  re-derive the root by guessing.
- done_when: a test runs multi-root `file_search`, captures a printed (absolute) hit string, feeds
  it verbatim to `file_read`, and asserts the correct file's content is returned.
- success_signal: copy-pasting a multi-root `file_search` result into `file_read` reads the right
  file.
- prerequisites: TASK-3.

## ✓ DONE TASK-5 — delete the obsidian phantom

- files: `co_cli/memory/item.py`, `co_cli/index/store.py`, `co_cli/bootstrap/check.py`,
  `co_cli/tools/system/capabilities.py`
- Nothing obsidian-shaped survives B′, so remove the now-dead declarations (kept earlier only as
  "forward hooks" for the rejected Direction A). De-dup: deps.py/config.py obsidian sites are owned
  by TASK-2; TASK-5 owns the rest:
  - `SourceTypeEnum.OBSIDIAN` (`memory/item.py:43`) and `IndexSourceEnum.OBSIDIAN` (`item.py:53`).
  - `index/store.py:11` docstring `'obsidian'` source-string mention.
  - `bootstrap/check.py`: the `:28` comment, the `_obsidian_vault` derivation + `_check_obsidian`
    call (`:331-334`), `_check_obsidian` itself, the `("obsidian", …)` named-check, and the
    `capabilities` `obsidian` key (`:375`).
  - `capabilities.py:189` `obsidian=caps["obsidian"]`. **Coupling:** this reads the key produced at
    `check.py:375` — remove both together or `capabilities` raises `KeyError`.
- done_when: `grep -rn obsidian co_cli/` returns nothing, AND `uv run pytest` over the bootstrap /
  capabilities / memory-item tests passes.
- success_signal: N/A (dead-code removal).
- prerequisites: TASK-2.

## ✓ DONE TASK-6 — tool-surface prompt clarity (optional-arg defaults, hermes pattern)

The multi-root change alters the agent-facing semantics of `file_read` / `file_search`, so their
docstrings (the agent prompt surface) must be brought into line with hermes' optional-arg
convention while we are in there. **Pattern (hermes `tools/file_tools.py:1196-1285`):** every
optional arg's description states (a) the **default value** inline, (b) **what that default does**
(the behavior at the default), and (c) **when to override it**. Never a bare `Optional` with no
default value or consequence.

- files: `co_cli/tools/files/read.py` (`file_read` + `file_search` docstrings only — no behavior
  change beyond TASK-3/3a/4).
- **`file_read`:**
  - `path` doc currently says "File path relative to the workspace root" — now **stale/misleading**
    under multi-root. Restate: a path relative to the workspace root (the default/zero-config base),
    OR an absolute path under any configured `file_search_root` (the form `file_search` prints in
    multi-root mode, BC-5). State that relative paths anchor to the workspace root.
  - `start_line` / `end_line`: replace the bare "Optional." Per the hermes pattern, state the
    default behavior already described in the body ("omitted → up to 500 lines from the top; set
    `start_line` to continue") at the arg line itself, so the default is legible from the arg doc
    alone.
- **`file_search`:** the arg block (`read.py:435-450`) already documents most defaults well
  (`path` default `**/*`, `content` default None, `case_insensitive`/`files_only` False, `limit` 50
  /0=unlimited, `offset` 0). Audit each against the three-part pattern and close gaps:
  - `path` default-`**/*` description says "every file in the **workspace**" — restate as "every
    file under the active file-search root(s)" so the default's meaning is correct when more than one
    root is configured (BC-4: single-root wording/behavior unchanged).
  - Confirm `limit=0` (unlimited) explains the consequence, and that the over-fetch ceiling from
    TASK-3 (multi-root unbounded scan) is reflected if it changes observable behavior.
- **Scope guard:** docstrings/prompt text only. No new args, no signature changes, no rewording of
  unaffected tools (surgical — match existing voice). This is the *only* task that touches prompt
  copy; TASK-3/3a/4 stay behavior-only.
- done_when: `file_read` and `file_search` docstrings carry no bare `Optional`/undocumented-default
  arg; every optional arg states value + behavior-at-default; `file_read.path` documents
  absolute-under-any-root; `uv run pytest tests/test_flow_files_read.py` passes (docstring change is
  prompt-only — existing assertions unaffected).
- success_signal: an agent reading only the tool schema knows what each omitted arg will do without
  trial calls.
- prerequisites: TASK-4 (final multi-root behavior settled — docstrings describe the shipped surface).

## Acceptance / verification

- `file_search` / `file_read` reach a configured non-workspace root (the vault); `file_write` /
  `file_patch` reject it with a clear boundary error.
- `..` traversal out of all roots is rejected; in-vault symlinks pointing outside any root are
  rejected.
- **User guarantee:** unconfigured `file_search_roots` behaves exactly as today (workspace-only,
  byte-identical display, no new prompts, no surprise roots) — existing file tests pass unchanged
  (BC-4).
- New tests: multi-root read/search success; write-into-extra-root rejection; traversal/symlink
  escape rejection; display round-trip (`file_search` hit → `file_read`).
- Specs updated: `docs/specs/tools.md` (config fields: `file_search_paths` added,
  `obsidian_vault_path` removed; `file_search` description notes read-only reference roots),
  `docs/specs/01-system.md` / security-boundary section (read vs write scope split).
- Tool-surface prompts (TASK-6): `file_read` / `file_search` docstrings document every optional
  arg's default value + behavior-at-default (hermes pattern); `file_read.path` documents
  absolute-under-any-root.
- `grep -rn obsidian co_cli/` returns nothing after Task 5.

## Follow-ups (out of scope — not part of Gate 1)

- **`/files` (or `/roots`) status command.** There is currently no slash command exposing the
  active file-search scope (built-ins: `/help /clear /new /tools /history /compact /memory /dream
  /approvals /skills /background /tasks /cancel /queue /resume /sessions /reasoning`). Once
  `file_search_roots` exists, an operator has no surface to confirm which roots are active — an empty
  or typo'd `file_search_paths` would silently leave the vault unreachable with no feedback. A small
  read-only command printing the resolved `file_search_roots` + the write anchor (`workspace_dir`)
  would pair naturally with this feature. Deliberately deferred: it is a UX/observability nicety, not
  part of the read/write-scope split this plan owns. Tracked as its own plan:
  `2026-05-30-231258-files-scope-command.md` (hard-depends on this plan shipping first).
- **Vault `.obsidian/` (and `.trash`) search noise.** The grep shell runs `--no-ignore --hidden`
  (`read.py:213-214`); "honor defaults as today" therefore descends into a vault's `.obsidian/`
  (plugin configs, `workspace.json`, caches) and `.trash`. Sensible for a code workspace, noisy for
  a notes vault. Accepted as a v1 trade (no per-root ignore policy); a future per-root ignore/hidden
  default is the natural home for this. Not part of this plan's scope.

1. **Default for `file_search_roots` when unconfigured:** SETTLED → `[workspace_dir]` (BC-4).
   Explicit-only would make empty config search nothing and silently break every existing user.
2. **Display form for extra-root hits:** SETTLED → **absolute paths** (BC-5). Reversed from the
   earlier root-prefixed-relative recommendation: absolute is deterministic, unambiguous across
   same-named subpaths, round-trips through `file_read`, survives the path-normalization layer, and
   needs no label-parsing code. Single-root (default) display stays relative-to-workspace (BC-4).
3. **Read-only enforcement granularity:** SETTLED → writes confined to `workspace_dir` only (BC-1).
   A future per-root `mode: rw|ro` is YAGNI; `file_search_roots` are uniformly read-only.
4. **Config naming:** SETTLED → `file_search_paths` (config, list of strings) → `file_search_roots`
   (deps, resolved Paths). Config-side name mirrors the deps var.

## Final — Team Lead

Plan approved.

## Gate 1 — PO + TL sign-off (2026-05-30)

**APPROVED.** Premises validated against source: `enforce_workspace_boundary` (`fs_guards.py:6`),
`PATH_NORMALIZATION_TOOLS` + pre-resolve (`lifecycle.py:295-297`), `obsidian_vault_path` sites
(`deps.py:308,338,390`), `file_search`/grep helpers single-`workspace_dir` relativization
(`read.py:452-455,237-308`), Task 0 done in working tree — all confirmed.

- **Problem ✓** — vault unreachable (`.resolve()` defeats symlink); B′ filesystem-native is the
  right call over Direction A (stale index) and A′ (dies headless). Faithful to the standing
  external-folders-vs-curated-memory routing dichotomy.
- **Scope ✓** — phantom deletion (TASK-5) correctly coupled (B′ is what makes it dead); follow-ups
  correctly deferred; BC-1…BC-5 tight and testable; BC-4 byte-identical default is the right guarantee.
- **Design ✓** — guard split, `file_read` PATH_NORMALIZATION removal (the real blocker), and the
  three-code-path merge analysis are sound.

Three dev-phase refinements folded in (none were blockers): grep over-fetch `fetch_cap = offset+limit`
+ a ceiling for the `limit<=0` unbounded multi-root scan (TASK-3); per-root source tagging out of the
merge so absolute display round-trips (TASK-3/TASK-4); and the `.obsidian/` search-noise follow-up.

Added **TASK-6** (tool-surface prompt clarity): the multi-root change shifts the `file_read` /
`file_search` agent-facing semantics, so their docstrings must document every optional arg's default
value + behavior-at-default + override guidance per the hermes convention
(`hermes-agent/tools/file_tools.py:1196-1285`), and `file_read.path` must document the new
absolute-under-any-root form. Prompt-copy only, lands after TASK-4.

Cleared to proceed: `/orchestrate-dev file-search-roots`.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev file-search-roots`

## Delivery Summary — 2026-05-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `enforce_workspace_boundary` gone; both new guards defined; read/write tests pass | ✓ pass |
| TASK-2 | empty config → `[workspace_dir]`; configured → exact resolved list (no append) | ✓ pass |
| TASK-3a | `file_read` absent from `PATH_NORMALIZATION_TOOLS`; abs-path read under extra root returns content | ✓ pass |
| TASK-3 | `file_search(content=…)` returns hits from BOTH roots in one call; single-root unchanged | ✓ pass |
| TASK-4 | multi-root hit (absolute) feeds verbatim to `file_read`, returns the right file | ✓ pass |
| TASK-5 | `grep -rn obsidian co_cli/` empty; bootstrap/capabilities/memory-item tests pass | ✓ pass |
| TASK-6 | no bare `Optional`; every optional arg states value + behavior-at-default; abs-under-root documented | ✓ pass |

**Tests:** scoped — 70 passed, 0 failed (files-read 17, files-write 9, fork/delegation/capability/observability/bootstrap-config 54-set subset, memory-item 14, bootstrap-ctx 2). Lint: full tree clean (`ruff check` + `format --check`).
**Doc Sync:** narrow scope (3 specs) — `config.md` (`file_search_paths` row added, `obsidian_vault_path` removed, `workspace_path` reworded as write anchor); `tools.md` (config row swapped; `file_search` contract + Settings table updated for multi-root read scope); `01-system.md` (paths tree gains `file_search_roots` with read/write annotation). Full `/sync-doc` deliberately skipped — many specs are concurrently modified in the working tree; targeted edits avoid churning co-worker work.

**Overall: DELIVERED**
All 7 tasks passed `done_when`; lint clean; scoped tests green; docs synced. Design refinements landed: a `CoDeps.__post_init__` derives `file_search_roots = [workspace_dir]` for direct construction (not just bootstrap), preserving BC-4 everywhere; multi-root grep takes a 200-row per-root ceiling on the unlimited path (single-root stays byte-identical) with an OTEL span flag when coverage is bounded.

## Implementation Review — 2026-05-30

Stance: issues exist — PASS is earned. Reviewed TASK-1 … TASK-6 (+ Task 0). Six parallel evidence subagents (one per task), one adversarial cold-re-read pass, full suite with RCA, behavioral check. Note: the working tree carries substantial concurrent work from other in-flight plans; this review verified the file-search-roots changes are fully confined to the declared files (`grep` for `file_search_roots`/`file_search_paths`/`enforce_read_boundary`/`enforce_write_boundary`/`obsidian` shows zero leakage into the concurrent-work files).

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `enforce_workspace_boundary` gone; both guards defined; read/write tests pass | ✓ pass | `fs_guards.py:6` read guard, `:32` write guard; per-root `(root/path).resolve()` + first-`is_relative_to` accept (`:25-29`), no existence probing; call sites `read.py:435,545` (read), `write.py:406,443,513` + `shell/execute.py:70` (write, BC-1); old name grep empty; 26 passed |
| TASK-2 | empty → `[workspace_dir]`; configured → exact resolved list (no append) | ✓ pass | `core.py:106` `file_search_paths` (default `[]`), `:103` `workspace_path` separate; `obsidian_vault_path` + env-map gone; `deps.py:311` field, `:347-351` bootstrap resolve, `:329-334` `__post_init__` guarded by `if not self.file_search_roots`; runtime asserts both branches |
| TASK-3a | `file_read` absent from `PATH_NORMALIZATION_TOOLS`; abs-path read under extra root returns content | ✓ pass | `categories.py:12-17` set is exactly `{file_write, file_patch}`; `lifecycle.py:295-298` reads the set dynamically (no lifecycle edit needed — lifecycle.py correctly not in diff); `test_file_read_absolute_path_under_extra_root` passes |
| TASK-3 | `file_search(content=…)` returns hits from BOTH roots; single-root unchanged | ✓ pass | `read.py:534` uses `file_search_roots` (zero `workspace_dir` refs in file); per-root iteration `:543-552`; grep merge sums true per-root `total_match_count` (`:388`, counted pre-pagination), global slice `:600`; `limit<=0` multi-root 200 ceiling applied to every root with OTEL flag (`:585-592`); `test_file_search_content_spans_both_roots` passes |
| TASK-4 | multi-root absolute hit feeds verbatim to `file_read`, returns right file | ✓ pass | `read.py:538` `display_base = None if multi_root else roots[0]`; absolute reconstructed from iterating root (not guessed); `test_multiroot_search_hit_roundtrips_to_file_read` asserts printed hit `== str((vault/…).resolve())` and round-trips |
| TASK-5 | `grep -rn obsidian co_cli/` empty; bootstrap/capabilities/memory-item tests pass | ✓ pass | `memory/item.py:40-52` enums valid sans OBSIDIAN; `index/store.py:10-11` docstring clean; `check.py:341-361` no obsidian named-check/capability key; `capabilities.py:186-197` no `caps["obsidian"]` reader (producer+consumer removed together — no KeyError); `grep -rni obsidian co_cli/ tests/ evals/` zero; 59 passed |
| TASK-6 | no bare `Optional`; every optional arg states value + behavior-at-default; abs-under-root documented | ✓ pass | `read.py:418-421` `file_read.path` documents absolute-under-any-root + relative anchoring; `:422-427` `start_line`/`end_line` state default+behavior; `:513-532` `file_search` args (`**/*` → "under active root(s)", `limit=0` cap noted); signatures unchanged (prompt-only); 17 passed |

### Issues Found & Fixed
No blocking issues found. The adversarial pass re-read all 11 cited claim clusters cold and confirmed every one (guards, merge slicing/summation, `limit<=0` ceiling applied per-root, BC-4 byte-identity, `__post_init__` no-clobber guard, fork inheritance, zero dangling obsidian refs, prompt-only docstrings).

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `shell_exec` anchors workdir to `deps.shell.workspace_dir` (= `os.getcwd()`), a different var than `deps.workspace_dir` (= `config.workspace_path` or cwd); they diverge only if `workspace_path` is configured ≠ launch cwd | `shell/execute.py:70`, `bootstrap/core.py:415` | minor | Pre-existing — the change was a pure guard rename, not a re-anchor. Out of scope for this plan; noted for a future shell-anchor reconciliation. Not fixed here. |

### Tests
- Command: `uv run pytest -x -q`
- Result: **653 passed, 0 failed** (459s)
- Log: `.pytest-logs/20260530-235109-review-impl.log`
- Lint: `scripts/quality-gate.sh lint` — clean (ruff check + format, 324 files)

### Behavioral Verification
- `co status` is not a command in this CLI (commands: `chat`, `tail`, `trace`, `dream`) — the skill's example command does not apply here.
- Capability-report path (the obsidian-removed surface): confirmed at source that `check_runtime`'s `capabilities` dict (`check.py:351-361`) and `named_checks` (`:341-348`) carry no `obsidian` key, and the consumer (`capabilities.py:186-197`) reads no `obsidian` key — `KeyError` impossible. Exercised live by 59 passing bootstrap/capabilities tests (real stores, no mocks).
- File-tool surface (`file_read`/`file_search` multi-root): `success_signal`s verified via 17 real-filesystem tests — abs-path read under an extra root returns content (TASK-3a), broad `file_search` returns hits from both roots in one call (TASK-3), multi-root absolute hit round-trips into `file_read` (TASK-4), BC-4 single-root display byte-identical. `file_search_roots` default/configured resolution confirmed by runtime asserts (TASK-2 `success_signal`).

### Overall: PASS
All 7 tasks confirmed against source with file:line evidence; full suite green (653); lint clean; obsidian phantom fully removed with zero dangling references; multi-root read scope + workspace-anchored writes behave per BC-1…BC-5. One pre-existing, out-of-scope shell-anchor divergence noted, not blocking. Ready for Gate 2 → `/ship file-search-roots`.
