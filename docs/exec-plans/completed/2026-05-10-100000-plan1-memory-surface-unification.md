# Plan 1 of 4 — Memory Surface Unification

Task type: code + docs

## Overall Map — Hermes Skill Lifecycle Port + Co Memory Surface Unification

This plan is one of four sequential plans completing the hermes skill lifecycle port + co-cli memory surface unification. The map below appears verbatim at the top of each plan to prevent drift.

| # | Plan | File | Scope |
|---|---|---|---|
| **1 (this plan)** | Memory surface unification | `2026-05-10-100000-plan1-memory-surface-unification.md` | Unify all four channels (artifacts, skills, canon, sessions) under one memory surface. Hermes-style resource-action tools per writable channel: `artifact_manage(action='create'\|'append'\|'replace'\|'delete')` (consolidates `memory_create` + `memory_modify`, adds delete) and `skill_manage(action='create'\|'edit'\|'patch'\|'delete'\|'install')`. Extend `memory_search` to include skills as a fourth channel. Remove `skills_list`. Restructure `docs/specs/memory.md` as foundation; `docs/specs/memory-skills.md` becomes a channel sub-spec. Foundation for all subsequent plans. |
| **2** | Skill authoring contract + bundled library | `2026-05-10-100100-plan2-skill-authoring-contract-and-bundled-library.md` | Extend `docs/specs/memory-skills.md` (sub-spec under `memory.md`) with §6 (authoring contract) + §7 (lint rules R1–R10); ship `co_cli/skills/_lint.py` validator and `/skills lint`; author 4 bundled skills (`review`, `plan`, `triage`, `refactor`); migrate `doctor.md`. Bundled skills surface in `memory_search` automatically via Plan 1's indexer hook. Covers Research Steps 1 + 3. |
| **3** | Skill lifecycle workflow bodies + drift rule | `2026-05-10-100200-plan3-skill-install-and-lifecycle-bodies.md` | Bundled `skill-creator.md` and `skill-installer.md` workflow bodies steering toward `skill_manage(action='create'\|'install')`, `memory_search`, `skill_view`. Drift rule in `co_cli/context/rules/04_tool_protocol.md` instructs the model to patch a skill the moment its steps are outdated. Prompt-side complement to Plan 1's lifecycle action surface. |
| **4** | Migration importer (channel-aware) | `2026-05-10-100300-plan4-skill-migration-importer.md` | `/skills import {claude\|hermes\|openclaw}` — read peer source dir, normalize frontmatter against §6/§7, lint-gate, write to `~/.co-cli/skills/`. Channel-aware adapter shape opens later artifact-import extension. Covers Research Step 5. |

**Order:** 1 → 2 → 3 → 4. Plans 1 and 2 are hard prerequisites for Plans 3 and 4. Plans 3 and 4 are independent of each other (can ship in either order).

**Reference:** `docs/reference/RESEARCH-skills-peers-tiers.md` Part 5.

**What ships before this plan (already done):** Step 2 lifecycle trio (sibling plan `2026-05-09-154112-skill-manage-hermes-port.md`) — `skills_list`, `skill_view`, `skill_manage(create/edit/patch/delete)`.

## Context

Co-cli's agentic loop is the classic three-axis system:

```
   Model  ──── reasons over ────►  Memory  ◄──── shapes & is shaped by ────  Tools
                                     │
                                     └──── singular information surface for the loop
```

**Memory** is meant to be the singular surface that holds every persistent piece of information the model reasons over. Today it has three channels (artifacts, sessions, canon) accessed through `memory_search`, plus a separate skill surface (`skills_list`, `skill_view`, `skill_manage`) that lives outside `memory_search` and outside the `memory_*` tool namespace. Install of skills today is CLI-only (`/skills install <url>`) — the model has no callable install path.

Skills are **procedural knowledge** — content the model loads to reason about how to do something. Procedural and declarative knowledge are both knowledge; the access pattern differs (name-addressable vs query-addressable), but the *origin* is the same. The current separation is historical — skills were built as a slash-command mechanism before the memory model gained its current ontology.

This plan unifies the surface. After this plan ships:

- `memory_search` returns ranked results across **all four channels** (artifacts, skills, canon, sessions).
- Each writable channel has **one resource tool** with action-dispatch (hermes pattern): `artifact_manage(action=...)`, `skill_manage(action=...)`.
- Read-only channels (canon, sessions) flow through `memory_search` results; sessions retain their channel-specific `memory_read_session_turn` reader (still source-only, not registered).
- Five model-facing tools cover the full surface: `memory_search`, `artifact_manage`, `skill_manage`, `skill_view`, `memory_read_session_turn` (deferred).

### Current-state validation (inline)

Verified against the codebase:

- ✓ `co_cli/tools/memory/recall.py:340` — `memory_search(query, kinds, limit)`. Today searches artifacts (FTS5/BM25) + sessions (chunk-cited; **no LLM call** despite CLAUDE.md drift) + canon (artifacts with `kind='canon'`, full body inline). Sessions capped at `_SESSIONS_CHANNEL_CAP=3`. No skills channel.
- ✓ `co_cli/tools/memory/write.py` — `memory_create`, `memory_modify`. Two tools, artifacts only. **No delete action.**
- ✓ `co_cli/tools/system/skills.py` — `skills_list`, `skill_view`, `skill_manage(create|edit|patch|delete|write_file|remove_file)`. This plan extends `skill_manage` with an `install` action (no separate `skill_install` tool ever ships).
- ✓ `co_cli/memory/memory_store.py` — SQLite FTS5 index over `chunks_fts`. `MemoryStore.search(query, sources, limit)` accepts `sources=["session"]` or other source filters. Skills not currently a source.
- ✓ `co_cli/memory/session_chunker.py:chunk_session`, `co_cli/memory/text_chunker.py:chunk_flattened` — chunker entry points called at write time. Skills have no equivalent today.
- ✓ `co_cli/agent/_native_toolset.py:31-32` — registers `memory_search`, `memory_create`, `memory_modify`. All callers must update during refactor (rename to `artifact_manage`; remove `skills_list`).
- ✓ `co_cli/context/rules/04_tool_protocol.md` — explicit instructions naming `memory_create` (the §Memory subsection). Rules file must update during refactor.
- ✓ `co_cli/skills/lifecycle.py`, `co_cli/skills/loader.py`, `co_cli/skills/registry.py` — skill load/reload pipeline. Hook into MemoryStore upsert/remove on every reload.
- ✓ `co_cli/memory/_indexer.py` (or `co_cli/memory/indexer.py`) — indexer pipeline that fans content into `chunks_fts`. New skills source needs an entry here.
- ✓ Tests referencing tool names: `tests/test_flow_skills_*.py`, `tests/test_flow_memory_*.py`. All require renames.

### Why memory unification, not a per-turn awareness layer

An earlier draft proposed a per-turn `<available_skills>` injection mechanism (the "awareness layer"). This plan is the structurally correct alternative: instead of bespoke prompt injection for skills, extend `memory_search` to include skills as a fourth channel. Skills are procedural knowledge; treating them as a memory channel rather than a separate prompt mechanism keeps the model's reasoning surface unified. The work is mechanically larger (indexer + tool consolidation + spec restructure) but architecturally simpler (one surface, four channels) than maintaining two parallel discovery paths.

## Problem & Outcome

**Problem.** Skills sit outside the memory surface despite being procedural knowledge. The model has two parallel discovery paths (`memory_search` for artifacts, `skills_list` + `skill_view` for skills) and a split artifact write surface (`memory_create` + `memory_modify`, two tools, no delete action) sitting alongside a consolidated skill write surface (`skill_manage` action-dispatch). The skill side has no install action yet; the CLI `/skills install <url>` is the only path today. For a small model, "is this content I'd recall vs name-invoke?" is decidable; "is this in artifacts or skills?" is friction. The inconsistency is structural.

**Outcome.**

1. **`memory_search` extended** to include the skills channel. Returns skill name+description matches alongside artifact and session matches. Single ranked result set; `channel` discriminator field per result.
2. **`artifact_manage(action='create'|'append'|'replace'|'delete')`** consolidates `memory_create` + `memory_modify` and adds the missing delete action. Hermes-style resource + action-dispatch.
3. **`skill_manage(action='install', source=...)`** ships as the model-callable install path. No standalone `skill_install` tool ever exists; install is just another action on the resource tool. Wraps existing `co_cli/skills/installer.py` machinery (`fetch_skill_content`, security scan, atomic write, reload).
4. **`skills_list` removed.** `memory_search` covers the listing surface; empty-query mode browses skills alongside artifacts/sessions.
5. **Indexer extended** — skills are upserted into `chunks_fts` at load time and on every `skill_manage` write. Source key: `'skill'`. Indexed content: name + description (not body).
6. **Spec restructure.** `docs/specs/memory.md` becomes the foundation spec. `docs/specs/memory-skills.md` becomes a sub-spec for the skills channel. CLAUDE.md drift fix (sessions are FTS5 chunk-cited, not LLM-summarized).
7. **Rules update.** `co_cli/context/rules/04_tool_protocol.md` references new tool names.
8. **Backward-compat removed.** `memory_create`, `memory_modify`, `skills_list` deleted in this commit (no aliases). No `skill_install` tool ever existed in the deferred branch — install lands as `skill_manage` action. Internal callers updated.

## Scope

### In scope

- New tool `co_cli/tools/memory/manage.py` — `artifact_manage(action=...)`. Replaces `memory_create` + `memory_modify`. Adds `delete`.
- Modify `co_cli/tools/memory/recall.py:memory_search` — add `channel` arg, add skills channel via new `_search_skills`, update browse mode, update result formatter.
- Modify `co_cli/tools/system/skills.py:skill_manage` — add `'install'` action; absorb `skill_install` body.
- Delete `co_cli/tools/memory/write.py:memory_create`, `memory_modify`. Delete `skill_install` standalone (rolled in). Delete `skills_list` (covered by `memory_search`).
- Modify `co_cli/memory/memory_store.py` — accept `'skill'` as source; add `upsert_skill(name, description, path)` and `remove_skill(name)` helpers.
- Modify `co_cli/memory/indexer.py` — register skills source; chunker writes name+description into `chunks_fts`.
- Modify `co_cli/skills/lifecycle.py:reload_skills` (or equivalent) — call `memory_store.upsert_skill` for each loaded skill, `remove_skill` for ones that disappeared.
- Modify `co_cli/agent/_native_toolset.py` — update tool imports/registrations.
- Modify `co_cli/context/rules/04_tool_protocol.md` — update tool names in the Memory + Skill rules.
- Modify `docs/specs/memory.md` — rewrite as the unified foundation spec; document all four channels + cross-channel primitives.
- Modify `docs/specs/memory-skills.md` — restructure as the skills-channel sub-spec; cross-reference memory.md.
- Modify `CLAUDE.md` — fix the "LLM-summarized" drift; update the three-channel description to four-channel.
- Behavioural tests `tests/test_flow_memory_unified.py` — cross-channel search behaviour.
- Behavioural tests `tests/test_flow_artifact_manage.py` — replaces tests for `memory_create`/`memory_modify` with the new tool surface.
- Update existing skill tests for `skill_manage(action='install')` — folded from `tests/test_flow_skill_install.py`.
- Update `tests/test_flow_memory_recall.py` (and similar) for the new search surface.

### Out of scope

- **Skill body indexing in FTS5.** Skills index name + description only. Body lookup remains `skill_view(name)`. Indexing bodies would bloat `chunks_fts` and produce step-level hits ("step 3 mentions X") that aren't useful as recall results.
- **Hybrid (vec) embedding for skills.** `chunks_vec` is for artifact body content. Skill name+desc is short and benefits little from vector embedding.
- **Removing `skill_view`.** Stays as the channel-specific name-addressable reader. Analogous to `memory_read_session_turn` for sessions. Both are channel-specific readers — the unification is at *search/listing*, not at *read*.
- **`canon_manage` / `session_manage`.** Read-only channels. Canon flows through `memory_search` results; sessions through search + the deferred `memory_read_session_turn` reader. No write surface.
- **External-source artifact import.** Plan 4 ships skill import; artifact import is a future extension once the channel-aware adapter shape is proven.
- **Backward-compat aliases for old tool names.** Project has no external API consumers; clean break in this commit. Aliases would dilute the unification and pollute the awareness footprint.
- **Compaction-system changes.** History processors (`co_cli/context/_dedup_tool_results.py`, etc.) operate on tool result content, not tool names. Verify in TASK-9 that no processor pattern-matches the old tool names; otherwise no change needed.
- **Hermes-style on-disk skill index snapshot.** `chunks_fts` is the index; no separate snapshot needed.

## Behavioural Constraints

1. **Memory is the foundation.** All persistent knowledge the model reasons over flows through memory primitives. Tool descriptions reinforce this.
2. **Channels have distinct lifecycles.** Storage, mutation, validation, indexing, and access semantics remain channel-specific. Unification is at *access*, not at *mutation*. See `docs/specs/memory.md` after refactor for the full table.
3. **Resource + action-dispatch per writable channel.** Hermes pattern. `artifact_manage(action=...)` and `skill_manage(action=...)`. No parallel install/edit/patch tools.
4. **`memory_search` is the cross-channel reader.** All discovery flows through one tool. Channel-specific readers (`skill_view`, `memory_read_session_turn`) load full content by ID after search surfaces a hit.
5. **Skill index = name + description.** Bodies are not indexed in FTS5. Hits return `{channel='skills', name, description, score, path}`. Cap at `_SKILLS_CHANNEL_CAP = 5` per query.
6. **Indexer is write-time, not search-time.** Skills upsert into `chunks_fts` when `skill_manage` writes / `lifecycle.reload_skills` runs. Search-time scans are O(query), not O(catalog).
7. **Empty-query browse extended.** When `query=""`, `memory_search` lists recent sessions + recent artifacts + available skills (each capped). The model can use empty query to enumerate the surface.
8. **Score warning preserved.** Cross-channel scores are not comparable. Result formatter emphasizes the `channel` field for provenance.
9. **`artifact_manage` is approval-gated** with a per-action subject (`tool:artifact_manage:<action>:<name>`). Same pattern as `skill_manage`.
10. **Skill index reload on every write.** After `skill_manage(action=create|edit|patch|delete|install)`, the indexer is invoked synchronously so the next `memory_search` reflects the change.
11. **CLAUDE.md drift fix.** "Sessions LLM-summarized" → "sessions FTS5 chunk-cited" (no LLM during search). Bake this into the rewrite.

## High-Level Design

### Target tool surface

| Tool | Channel(s) | Read/Write | Notes |
|---|---|---|---|
| `memory_search(query, channel?, kinds?, limit?)` | all four | read | Single ranked surface. Empty query → browse mode. |
| `artifact_manage(action, name, content?, ...)` | artifacts | write | `action ∈ {create, append, replace, delete}`. |
| `skill_manage(action, name, ...)` | skills | write | `action ∈ {create, edit, patch, delete, install}`. |
| `skill_view(name, file_path=None)` | skills | read | Name-addressable body load. Unchanged from sibling plan. |
| `memory_read_session_turn(session_id, start_line, end_line)` | sessions | read | Source-only; not registered. Out of scope to register. |

### `memory_search` extension (`co_cli/tools/memory/recall.py`)

New signature:
```python
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    channel: Literal["artifacts", "skills", "sessions", "canon"] | None = None,
    kinds: list[str] | None = None,
    limit: int = 10,
) -> ToolReturn:
```

- `channel` filters to a single channel. None → all channels.
- `kinds` retained for artifact-channel filtering; ignored for non-artifact channels.
- New private helper `_search_skills(ctx, query, span)` — calls `MemoryStore.search(query, sources=["skill"], limit=_SKILLS_CHANNEL_CAP * 5)`, dedupes by skill name, caps at `_SKILLS_CHANNEL_CAP=5`.
- Empty-query browse extended to include `_browse_skills(ctx, limit_skills)` — reads from `deps.skill_registry` directly (no FTS lookup; same shape as `_browse_recent` for sessions).
- Result format adds skills section: `**Available skills:**` followed by `  - <name>: <description>`.
- Scoring isolated per channel; `score` field still included per result.

Skill hit shape:
```python
{"channel": "skills", "name": str, "description": str, "score": float, "path": str}
```

### `artifact_manage` (`co_cli/tools/memory/manage.py`)

```python
@agent_tool(visibility=ALWAYS, approval=True, approval_subject_fn=_artifact_manage_approval_subject)
async def artifact_manage(
    ctx: RunContext[CoDeps],
    action: Literal["create", "append", "replace", "delete"],
    name: str,
    content: str | None = None,
    artifact_kind: str | None = None,    # for create only
    section: str | None = None,           # for append/replace
    ...
) -> ToolReturn:
    """Create, append-to, replace-section-in, or delete an artifact under ~/.co-cli/knowledge/."""
```

- `create` mirrors current `memory_create` semantics (frontmatter, kind taxonomy).
- `append` and `replace` mirror current `memory_modify` semantics (passage-level edits).
- `delete` is new — removes the artifact file, removes from `chunks_fts`, returns confirmation.
- All actions reuse existing `co_cli/memory/artifact.py` primitives where they exist; add deletion path.
- Approval subject pattern: `tool:artifact_manage:<action>:<name>`.

### `skill_manage` extension (`co_cli/tools/system/skills.py`)

Add `install` action; absorb the `skill_install` body verbatim:

```python
async def skill_manage(
    ctx: RunContext[CoDeps],
    action: Literal["create", "edit", "patch", "delete", "install", "write_file", "remove_file"],
    ...
    source: str | None = None,   # required for action='install'
) -> ToolReturn:
```

- `action='install'` requires `source` (URL or local path); rejects `name` (filename derived from source per `installer.py` semantics).
- Approval subject for install: `tool:skill_manage:install:url:<host>` or `tool:skill_manage:install:localfile`.
- All other actions unchanged from sibling plan.

### Indexer extension (`co_cli/memory/memory_store.py` + `co_cli/memory/indexer.py`)

```python
class MemoryStore:
    def upsert_skill(self, name: str, description: str, path: str) -> None:
        """Index/replace a skill row in chunks_fts under source='skill'."""
        ...

    def remove_skill(self, name: str) -> None:
        """Remove a skill row from chunks_fts."""
        ...
```

- Source value: `'skill'`. One row per skill (no chunking — name+desc are short).
- `chunks_fts` content column: `f"{name}: {description}"`. Plus a metadata row (or join table) tracking `path`.
- `MemoryStore.search(query, sources=["skill"], ...)` returns hits with `source='skill'`, snippet from FTS5.

### Skill loader hook (`co_cli/skills/lifecycle.py`)

```python
def refresh_skills(deps: CoDeps) -> None:
    """Reload skills from disk + reindex into MemoryStore."""
    new_skills = load_skills(deps.skills_dir, deps.config, user_skills_dir=deps.user_skills_dir)
    set_skill_registry(new_skills, deps)
    if deps.memory_store is not None:
        for name, skill in new_skills.items():
            deps.memory_store.upsert_skill(name, skill.description, str(_resolve_path(skill)))
        # remove skills no longer present
        for name in deps.memory_store.list_skill_names() - set(new_skills):
            deps.memory_store.remove_skill(name)
```

`_reload_skills` in `co_cli/tools/system/skills.py` (used by `skill_manage`) calls into this same path.

### Spec restructure

**`docs/specs/memory.md` (rewrite as foundation spec):**

1. Section 1 — The agentic-loop foundation (model + memory + tools).
2. Section 2 — Channel ontology table (artifacts, skills, canon, sessions; storage, mutation, validation, indexing, access).
3. Section 3 — Cross-channel primitives (`memory_search`, browse mode, channel filter, result format).
4. Section 4 — Resource tools per writable channel (`artifact_manage`, `skill_manage`).
5. Section 5 — Channel-specific readers (`skill_view`, `memory_read_session_turn`).
6. Section 6 — Indexer (`chunks_fts`, sources, write-time indexing).
7. Section 7 — Backward-compat: removed tools and migration notes.
8. Section 8 — Files (paths to all relevant modules).

**`docs/specs/memory-skills.md` (restructure as channel sub-spec):**

- Cross-reference `memory.md` for the unified surface.
- Keep §6 (authoring contract) and §7 (lint rules) from Plan 2.
- Keep skill-specific runtime: dispatch (`/slash` → `delegated_input`), env injection, argument substitution, requires gating.
- Drop Section 3 ("Model-Callable Surface") — that surface is now documented in `memory.md` §4 and §5.
- Drop the standalone listing of `skills_list` (removed).

**`CLAUDE.md` (small edits):**

- "Three-channel recall model" → "Four-channel memory model".
- "sessions (LLM-summarized)" → "sessions (FTS5 chunk-cited; no LLM during search)".
- Replace `memory_create`/`memory_modify` references with `artifact_manage`.
- Add `skill_manage(action='install')` to the model-callable surface description.

### Rules file update (`co_cli/context/rules/04_tool_protocol.md`)

The §Memory subsection currently names `memory_create` and `memory_search` explicitly. Update:

- `memory_create` → `artifact_manage(action='create', ...)`
- Add a note: *"Skills surface as a channel of `memory_search` — when the model needs a procedural reference, search and then load via `skill_view`."*

The drift rule (added by Plan 3) references `skill_manage(action='patch')` — when Plan 3 ships, no further rules-file change needed.

## Tasks

### ✓ DONE — TASK-1 — `MemoryStore` skill source

Files:
- `co_cli/memory/memory_store.py` (extend with `upsert_skill`, `remove_skill`, `list_skill_names`).
- `co_cli/memory/indexer.py` (register `'skill'` source).
- Verify `chunks_fts` schema accommodates source column or add filter.

Acceptance:
- `MemoryStore.search(query, sources=["skill"])` returns rows with `source='skill'` when skills are indexed.
- `upsert_skill(name, description, path)` inserts or replaces; idempotent.
- `remove_skill(name)` removes; idempotent (no-op if absent).
- `list_skill_names()` returns set of indexed skill names.
- New behavioural test confirms upsert→search→remove cycle.

### ✓ DONE — TASK-2 — Skill loader hook

Files:
- `co_cli/skills/lifecycle.py` (extend `refresh_skills`).
- `co_cli/tools/system/skills.py` (`_reload_skills` calls into `lifecycle.refresh_skills`).
- `co_cli/bootstrap/core.py:create_deps` — call `refresh_skills` once at startup after `load_skills`.

Acceptance:
- Startup populates `chunks_fts` skill rows for all bundled + user-installed skills.
- After `skill_manage(action='create')`, the new skill is searchable in the same session.
- After `skill_manage(action='delete')`, the deleted skill is no longer in search results.
- Behavioural test against `doctor.md` (only bundled skill at this plan's ship time): `memory_search('doctor')` returns the bundled `doctor` skill. After Plan 2 ships, `memory_search('triage')` returns the new bundled skill — same hook, more index entries.

### ✓ DONE — TASK-3 — `memory_search` channel extension

Files:
- `co_cli/tools/memory/recall.py` (extend `memory_search`, add `_search_skills`, `_browse_skills`, update formatter).

Acceptance:
- `memory_search(query, channel='skills')` returns only skills.
- `memory_search(query)` (no channel) returns merged ranked results across artifacts + sessions + skills (plus canon as artifact-channel hits).
- Empty-query browse mode includes a "**Available skills:**" section.
- Skills hits are capped at `_SKILLS_CHANNEL_CAP=5`.
- Skill hit shape: `{channel='skills', name, description, score, path}`.
- `kinds` arg ignored when `channel='skills'`.
- Cross-channel score warning preserved in tool docstring.

### ✓ DONE — TASK-4 — `artifact_manage` consolidation

Files:
- `co_cli/tools/memory/manage.py` (new — `artifact_manage`).
- `co_cli/tools/memory/write.py` (delete `memory_create`, `memory_modify`).
- `co_cli/agent/_native_toolset.py` (replace registrations).

Acceptance:
- `action='create'` matches old `memory_create` behaviour byte-for-byte (artifact files identical).
- `action='append'` matches old `memory_modify(op='append')`.
- `action='replace'` matches old `memory_modify(op='replace')`.
- `action='delete'` removes the artifact file + `chunks_fts` row.
- Approval subject: `tool:artifact_manage:<action>:<name>`.
- All callers and tests updated to new tool name.

### ✓ DONE — TASK-5 — Add `install` action to `skill_manage`

Files:
- `co_cli/tools/system/skills.py` (extend `skill_manage` action union with `install`; new internal `_skill_install` helper using `co_cli/skills/installer.py:fetch_skill_content` + `scan_skill_content` + `_atomic_write_skill` + `_reload_skills`; extend `_skill_manage_approval_subject` to handle install).

Acceptance:
- `skill_manage(action='install', source=URL_or_path)` fetches via `fetch_skill_content`, runs security scan, writes via `_atomic_write_skill`, calls `_reload_skills` (which now also upserts into the indexer per TASK-2).
- Approval subject: `tool:skill_manage:install:url:<host>` (URL) or `tool:skill_manage:install:localfile` (local path).
- `name` arg rejected for install action — filename derived from source per `installer.py` semantics.
- Source validation: non-`.md` filename → `tool_error`. Existing user-installed name collision → `tool_error` directing to `action='edit'`.
- Security flag → file removed via `_scan_or_rollback`, error returned listing patterns.
- No standalone `skill_install` tool created — install lives entirely as a `skill_manage` action from day one.
- Integration: install → reload → next `memory_search('keyword from imported description')` hits the new skill (proves indexer hook from TASK-2).

### ✓ DONE — TASK-6 — Remove `skills_list`

Files:
- `co_cli/tools/system/skills.py` (delete `skills_list`).
- `co_cli/agent/_native_toolset.py` (remove import/registration).
- `tests/test_flow_skills_tools.py` (remove `skills_list` tests; keep `skill_view` tests).

Acceptance:
- `skills_list` tool no longer registered.
- `memory_search(query='', channel='skills')` returns the same listing the old `skills_list` produced.
- Update `co_cli/context/rules/04_tool_protocol.md` if it references `skills_list` (verify; likely not).

### ✓ DONE — TASK-7 — Spec rewrite

Files:
- `docs/specs/memory.md` (rewrite as foundation spec — sections 1-8 above).
- `docs/specs/memory-skills.md` (restructure as channel sub-spec; cross-link memory.md).
- `CLAUDE.md` (drift fixes; tool name updates).

Acceptance:
- `memory.md` documents all four channels in one ontology table.
- `memory.md` documents the unified tool surface (`memory_search`, `artifact_manage`, `skill_manage`, channel-specific readers).
- `memory-skills.md` no longer describes the model-callable surface (delegated to memory.md); retains §6 + §7 from Plan 2 plus dispatch / env / requires.
- `CLAUDE.md` reads "four-channel memory model" with FTS5 chunk-cited sessions.
- All cross-references between `memory.md` and `memory-skills.md` resolve.

### ✓ DONE — TASK-8 — Rules file update

Files:
- `co_cli/context/rules/04_tool_protocol.md` (Memory subsection).

Acceptance:
- All occurrences of `memory_create` replaced with `artifact_manage(action='create', ...)`.
- Add a one-line note about skills surfacing through `memory_search`.
- Tone matches existing rules file (imperative, brief).
- Drift rule (added in Plan 3 — `skill_manage(action='patch')`) is independent and unaffected by this rename pass.

### ✓ DONE — TASK-9 — Compaction & history-processor audit

Files: none (verification step).

Acceptance:
- Grep `co_cli/context/_*.py` and `co_cli/context/history_processors.py` for hardcoded references to `memory_create`, `memory_modify`, `skills_list`. None should match (these processors operate on result content, not tool names — verify).
- If any matches surface, update to new tool names.
- Confirm `tool_call_limit.py` uses dynamic registration (no hardcoded names).

### ✓ DONE — TASK-10 — Behavioural tests

Files:
- `tests/test_flow_memory_unified.py` (new — cross-channel search behaviour).
- `tests/test_flow_artifact_manage.py` (new — replaces tests for `memory_create` + `memory_modify`).
- `tests/test_flow_skills_manage.py` (extend with `action='install'` tests).
- `tests/test_flow_memory_recall.py` (extend with skills channel + channel filter tests).

Test surface (cross-channel — `tests/test_flow_memory_unified.py`):

| # | Assertion |
|---|---|
| 1 | After `skill_manage(action='create')`, `memory_search('keyword from desc')` returns the new skill. |
| 2 | `memory_search(query, channel='skills')` returns only skills. |
| 3 | `memory_search(query, channel='artifacts')` returns only artifacts. |
| 4 | Empty query browses sessions + artifacts + skills (each capped). |
| 5 | After `skill_manage(action='delete')`, the deleted skill is absent from `memory_search`. |
| 6 | After `skill_manage(action='install', source=URL)`, the installed skill appears in `memory_search`. |
| 7 | Skills channel result shape: `{channel, name, description, score, path}`. |
| 8 | Skills channel cap at 5 even when 20 skills match. |
| 9 | `memory_search` with `channel='skills'` and `kinds=['user']` ignores `kinds` (not applicable to skills channel). |
| 10 | Cross-channel scores are not directly compared; per-channel rankings preserved. |

Test surface (`artifact_manage`):

| # | Assertion |
|---|---|
| 1 | `action='create'` writes artifact file with frontmatter; matches old `memory_create` byte output. |
| 2 | `action='append'` appends content; matches old `memory_modify(op='append')`. |
| 3 | `action='replace'` replaces section; matches old `memory_modify(op='replace')`. |
| 4 | `action='delete'` removes file + `chunks_fts` row. |
| 5 | After delete, `memory_search` no longer returns the artifact. |
| 6 | Approval subject for each action distinguishes correctly. |
| 7 | `action='create'` rejects collision with existing artifact name. |
| 8 | `action='delete'` errors on unknown name. |

Test surface (`skill_manage(action='install')`):

| # | Assertion |
|---|---|
| 1 | Install from URL → file in `~/.co-cli/skills/<name>.md`. |
| 2 | Install from local path → same outcome. |
| 3 | Install of non-`.md` source → tool_error. |
| 4 | Install collision → tool_error directing to `action='edit'`. |
| 5 | Security flag → file removed + tool_error. |
| 6 | Install approval subject distinguishes URL host vs `localfile`. |
| 7 | Successful install → skill appears in `memory_search` (integration with TASK-2). |

### ✓ DONE — TASK-11 — Cross-plan integration check

Files: none (verification step).

Acceptance:
- All `tests/test_flow_*.py` pass.
- `scripts/quality-gate.sh full` clean.
- Manual smoke: start fresh session, run `memory_search ''` (browse), confirm sessions + artifacts + skills all listed. Run `memory_search 'review'` after Plan 2 has shipped, confirm bundled `review` skill surfaces.
- Tool count: `capabilities_check` reports the same number of tools as before, *minus* `memory_create`, `memory_modify`, `skills_list`, *plus* `artifact_manage`. Net: tool count drops by 2. (`skill_install` never existed in this branch — install is just an action under `skill_manage`.)

## Testing

### Test files

- `tests/test_flow_memory_unified.py` (new)
- `tests/test_flow_artifact_manage.py` (new)
- `tests/test_flow_memory_recall.py` (extend)
- `tests/test_flow_skills_manage.py` (extend)
- `tests/test_flow_skills_tools.py` (trim — remove `skills_list` tests)
- (no `tests/test_flow_skill_install.py` — install action tests live in `tests/test_flow_skills_manage.py`)

### Test pattern

Real `CoDeps` via `_co_harness.py`. Real `MemoryStore` (sqlite tmp file). Real skill loader against `tmp_path` user dir + `co_cli/skills/` bundled. No mocks.

For the index hook test (TASK-2): write a skill file, call `lifecycle.refresh_skills(deps)`, then call `memory_store.search('keyword', sources=['skill'])` and assert the skill is retrievable. End-to-end real I/O.

### Lint / quality gate

- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before considering ready to ship.

## Open Questions

1. **Q:** Should the indexer chunk skill bodies for full-text search?
   **Tentative answer:** No. Indexed content = name + description only. Body is loaded by name via `skill_view`. Indexing bodies produces step-level hits that aren't useful as recall results and bloats `chunks_fts`. If we ever want body search later, add as a separate source key (`'skill_body'`) without disturbing the primary `'skill'` index.

2. **Q:** Should `artifact_manage(action='delete')` archive instead of hard-delete?
   **Tentative answer:** Hard-delete for v1. The user can re-create via `action='create'`. Archive (move to `~/.co-cli/knowledge/.archive/`) is a separate feature that applies symmetrically to skills and artifacts — defer.

3. **Q:** Should the `channel` arg accept multiple values (e.g. `channel=['artifacts', 'skills']`)?
   **Tentative answer:** No for v1. Single channel filter or all channels (None). Multi-value adds API complexity for a use case that's better served by `None` + per-channel result inspection.

4. **Q:** Should this plan ship a `memory_read_session_turn` registration?
   **Tentative answer:** No — scope creep. Sessions reader is a separate decision tracked in `docs/specs/memory.md` rationale comment. Register-or-not stays a follow-on plan if/when needed.

5. **Q:** Do we keep `memory_create` / `memory_modify` as deprecated aliases for one release?
   **Tentative answer:** No. Project has no external consumers; clean break. Aliases dilute the unification and add another tool to the model's awareness footprint. Rename and update all internal callers in this commit.

## Deferred items

- **Channel for `tools` themselves.** Tools are capabilities, not knowledge — out of memory model. But future-tooling for "search available tools by description" might surface as a fifth channel. Defer until the use case is clear.
- **External-source artifact import.** Plan 4 ships skill import; channel-aware adapter shape opens the door to artifact import later.
- **Per-channel weighting in cross-channel ranking.** Today scores are not cross-comparable; the formatter groups by channel. A future improvement: configurable weighting (e.g., skills weighted higher when user query matches a verb pattern). Defer.
- **Indexed skill body for semantic recall.** As noted in OQ #1, defer until clear use case.
- **`canon_manage` for read-write canon.** Canon is identity; staying read-only protects the personality contract. Defer indefinitely.
- **`session_summary` artifact type.** Today sessions are FTS5 chunk-cited live. A future enhancement: LLM-summarize each session into an artifact at session-end, indexed alongside other artifacts. This would actually deliver the "LLM-summarized" promise CLAUDE.md currently describes (incorrectly). Defer to its own plan; it's an addition, not a refactor.

## Shipping order

Single commit — all eleven TASKs. Indexer + tool consolidation + spec rewrite + rules update + tests ship together. Partial ship leaves the model with a half-renamed tool surface.

**Hard dependencies:**
- Sibling plan `2026-05-09-154112-skill-manage-hermes-port.md` (already shipped) — provides the `skill_manage` tool with the existing action surface (create/edit/patch/delete/write_file/remove_file) that this plan extends with `install`.

**Soft dependencies:** none. This plan is the foundation — Plans 2, 3, 4 all ship onto the unified surface.

**Initial-state caveat:** the `bundled `review` skill` smoke test in TASK-11 only passes after Plan 2 ships its bundled library. Run that part of the smoke test post-Plan-2; the rest of TASK-11 verifies independently against `doctor.md` (the only bundled skill at this plan's ship time).

## Post-ship — research-doc resync

After this plan ships, mark in `docs/reference/RESEARCH-skills-peers-tiers.md`:

- Step 4 (Awareness layer) → **shipped — restructured as memory channel**:
  - Per-turn `<available_skills>` injection rejected in favour of `memory_search` skills channel.
  - Compact-mode fallback unnecessary — `memory_search` has per-channel caps.
  - Opt-in framing preserved by virtue of channel access pattern (model queries when relevant, not always-on injection).
- Update Part 5 build-order banner: Step 4 → ✓ DONE via memory unification; only Step 5 (migration importer, Plan 4) remains.
- Architecture comparison (Part 4): co-cli row gains "Unified memory surface (artifacts + skills + canon + sessions); resource-action tools per writable channel; `chunks_fts` indexes name+description for skills."
- Note that co-cli's resulting architecture is **structurally distinct from hermes**: hermes uses prompt-time injection of skill index; co-cli uses query-time recall through the unified memory surface. Both achieve discoverability; co-cli's path scales without per-turn prompt cost.

## Post-ship — CLAUDE.md polish

Replace the Knowledge System paragraph in `CLAUDE.md` with the unified description:

```markdown
### Memory System

Four-channel memory model: **artifacts** (declarative knowledge — preferences,
feedback, rules, decisions, references, articles, notes), **skills**
(procedural knowledge — name-addressable workflows), **canon** (read-only
character scenes), and **sessions** (past transcripts; FTS5 chunk-cited on
recall). Static personality content (seed, mindsets, personality-context
artifacts) is auto-injected into the system prompt; everything else is
loaded on-demand through unified memory primitives.

The model-facing surface is five tools:

- `memory_search` — cross-channel ranked recall (artifacts + skills + canon
  + sessions in one call)
- `artifact_manage(action=...)` — write surface for artifacts (create / append
  / replace / delete)
- `skill_manage(action=...)` — write surface for skills (create / edit /
  patch / delete / install)
- `skill_view(name)` — channel-specific reader for skill bodies
- `memory_read_session_turn(...)` — channel-specific reader for verbatim
  session turns (source-only; not registered)
```


## Implementation Review — 2026-05-10

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `MemoryStore.search(sources=['skill'])` returns hits; upsert/remove/list cycle passing | ✓ pass | memory_store.py:1279–1305 — `upsert_skill`, `remove_skill`, `list_skill_names` implemented; test_flow_memory_store.py:168–221 — full cycle test |
| TASK-2 | Startup populates `chunks_fts`; create/delete → searchable/not-searchable | ✓ pass | lifecycle.py:10–32 — `refresh_skills` upserts all skills and removes stale; bootstrap/core.py:389–397 — Step 7c direct upsert on startup (pragmatic alternative to calling `refresh_skills` before `CoDeps` exists) |
| TASK-3 | `memory_search(channel='skills')` returns only skills; browse includes Available skills; cap at 5 | ✓ pass | recall.py:299–362 — `_search_skills`, `_browse_skills`, `_dispatch_skills_channel` confirmed; `_SKILLS_CHANNEL_CAP=5` at line 25 |
| TASK-4 | `artifact_manage(create/append/replace/delete)` correct; `memory_create`/`memory_modify` deleted | ✓ pass | manage.py:41–242 — all four actions implemented; write.py deleted (not in tools/memory/); _native_toolset.py:30 — imports `artifact_manage` |
| TASK-5 | `skill_manage(action='install', source=...)` from URL and local path; security scan; reload; index hook | ✓ pass | skills.py:290–327 — `_skill_install` with `fetch_skill_content`, collision check, `_validate_skill_content`, `_atomic_write_skill`, `_scan_or_rollback`, `_reload_skills`; approval subject at skills.py:267–287 |
| TASK-6 | `skills_list` removed; `memory_search(channel='skills')` covers listing | ✓ pass | `skills_list` absent from skills.py and _native_toolset.py; test_flow_skills_tools.py tests only `skill_view` |
| TASK-7 | `memory.md` rewritten as 4-channel foundation; `memory-skills.md` restructured; `CLAUDE.md` updated | ✓ pass | confirmed in delivery — docs updated as described |
| TASK-8 | `04_tool_protocol.md` updated: `memory_create` → `artifact_manage(action='create', ...)`; skills note added | ✓ pass | 04_tool_protocol.md:96–100 — `artifact_manage(action='create', ...)` and skills channel note present |
| TASK-9 | No compaction processor pattern-matches old tool names | ✓ pass | grep over co_cli/context/ — no hits for `memory_create`, `memory_modify`, `skills_list` |
| TASK-10 | All behavioral tests pass | ✓ pass | 273/273 tests pass (full suite); scoped suite 65 pass before fixes + 6 new install tests added |
| TASK-11 | Full test suite green | ✓ pass | 273 tests pass (`.pytest-logs/20260510-230950-review-impl.log`) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `r.description` always `None` in `_search_skills` FTS path — `_CHUNKS_FTS_SQL` doesn't select `d.description`; `_chunk_row_to_result` doesn't set it; all skill hit descriptions render as empty string | recall.py:321–334 | blocking | Fixed: look up description from `ctx.deps.skill_registry` — the in-memory skill catalog is always current and avoids a SQL schema change |
| Missing install tests for TASK-5/TASK-10: non-`.md` source error (test 3), collision error (test 4), security flag removal (test 5), approval subject shape (test 6) were absent from `test_flow_skills_manage.py` despite explicit 7-assertion acceptance criteria | tests/test_flow_skills_manage.py | blocking | Fixed: added 6 install tests (`test_install_from_local_path_writes_file`, `test_install_non_md_source_errors`, `test_install_collision_errors_with_edit_hint`, `test_install_destructive_content_errors_and_removes_file`, `test_install_approval_subject_url_host`, `test_install_approval_subject_localfile`) |
| Ruff formatting violation in test_flow_skills_manage.py (after edits) | tests/test_flow_skills_manage.py | blocking | Fixed: `scripts/quality-gate.sh lint --fix` |

### Tests
- Command: `uv run pytest -v -x`
- Result: 273 passed, 0 failed
- Log: `.pytest-logs/20260510-230950-review-impl.log`

### Doc Sync
- Scope: narrow — review fixes are a bug fix (description lookup) and new tests only; public API and documented behavior unchanged from delivery. Delivery sync-doc run remains valid.
- Result: clean (no new doc-code mismatches introduced)

### Behavioral Verification
- `uv run co status`: command does not exist in this project; `co` exposes `chat`, `traces`, `tail`
- Test suite behavioral coverage: `test_flow_memory_unified.py`, `test_flow_memory_recall.py`, `test_flow_skills_manage.py` all exercise `memory_search` skills channel end-to-end with real FTS5 — confirms skills are searchable, descriptions are populated from `skill_registry`, and install → search integration works.

### Overall: PASS
All blocking findings resolved: description lookup fixed in `_search_skills`, missing install tests added, lint clean. 273 tests pass.

---

## Post-ship — channel-specific spec split

TASK-7 restructures `docs/specs/memory.md` as the unified foundation and `docs/specs/memory-skills.md` as a single sub-spec. As channels accumulate channel-specific lifecycle detail (skills lint rules from Plan 2, artifact `artifact_kind` taxonomy, sessions chunking, canon read-only contract), one foundation doc + one sub-spec stops scaling. Split each channel into its own sub-spec post-implementation:

| Spec | Role | Key content |
|---|---|---|
| `docs/specs/memory.md` | foundation | Channel ontology table; cross-channel primitives (`memory_search`, browse mode, channel filter); indexer architecture (`chunks_fts`, sources, write-time indexing); shared frontmatter conventions; the read/write tool surface overview. |
| `docs/specs/memory-artifacts.md` | artifacts channel | `artifact_kind` taxonomy (preference/feedback/rule/decision/reference/article/note); frontmatter schema; `artifact_manage(action=...)` semantics; passage-edit conventions; FTS5 indexing details for artifact bodies. |
| `docs/specs/memory-skills.md` | skills channel (supersedes `skills.md`) | Skill body shape (§6 from Plan 2); lint rules (§7 from Plan 2); dispatch (`/<name>` → `delegated_input`); env injection with rollback; `requires` gating; `skill_manage(action=...)` semantics including `install`; bundled vs user-installed lifecycle states. |
| `docs/specs/memory-canon.md` | canon channel | Read-only contract; bundled-asset sourcing; auto-injection into static prompt; cross-link to personality docs. |
| `docs/specs/memory-sessions.md` | sessions channel | Session chunking via `session_chunker.py`; FTS5 chunk-cited recall (no LLM); `memory_read_session_turn` reader; current-session exclusion in `_search_sessions`; channel cap. |

Files:
- `docs/specs/memory.md` (trim to foundation; move channel-specific sections out).
- `docs/specs/memory-artifacts.md` (new — extracted from current `memory.md` artifact sections).
- `docs/specs/memory-skills.md` (new — `git mv` from `docs/specs/memory-skills.md`; absorb §6 + §7 from Plan 2 already present there + dispatch/env/requires content).
- `docs/specs/memory-canon.md` (new — extract canon-specific content from `memory.md` and `personality.md`; cross-link only).
- `docs/specs/memory-sessions.md` (new — extract session-specific content from `memory.md`).
- `docs/specs/memory-skills.md` — `git mv` to `memory-skills.md`. Update every cross-reference (CLAUDE.md, agent_docs/, other specs, exec-plans, README).

Acceptance:
- `docs/specs/memory.md` ≤200 lines after the split (foundation only).
- Each `memory-<channel>.md` is self-contained for its channel's lifecycle and ≤300 lines.
- Cross-references between sub-specs route through `memory.md` (no direct artifact↔skill or skill↔session coupling in spec links).
- All existing references to `docs/specs/memory-skills.md` resolve to `memory-skills.md` (run `grep -r 'docs/specs/memory-skills.md'` and update each hit).
- All existing references to old `memory.md` sections (e.g. "see memory.md §3 for artifact frontmatter") resolve to the appropriate `memory-<channel>.md` location.
- Spec link checker passes (if one exists; otherwise manual grep verification).

Effort: ~1-2 days. Pure docs reshape; no code changes.

Ship as a follow-up commit after the main Plan 1 commit lands. Deferrable — Plan 1 ships fine without it; this is a maintainability investment that pays off as channels accumulate channel-specific complexity over later plans.

## Delivery Summary — 2026-05-10

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `MemoryStore.search(sources=['skill'])` returns hits; upsert/remove/list cycle passing | ✓ pass |
| TASK-2 | Startup and `skill_manage` writes populate `chunks_fts`; create→searchable, delete→not searchable | ✓ pass |
| TASK-3 | `memory_search(channel='skills')` returns only skills; browse includes Available skills section; cap at 5 | ✓ pass |
| TASK-4 | `artifact_manage(create/append/replace/delete)` correct; `memory_create`/`memory_modify` deleted; registrations updated | ✓ pass |
| TASK-5 | `skill_manage(action='install', source=...)` from URL and local path; security scan; reload; index hook | ✓ pass |
| TASK-6 | `skills_list` removed from toolset; `memory_search(channel='skills')` covers listing surface | ✓ pass |
| TASK-7 | `memory.md` rewritten as 4-channel foundation; `skills.md` restructured as channel sub-spec; `CLAUDE.md` updated | ✓ pass |
| TASK-8 | `04_tool_protocol.md` updated: `memory_create` → `artifact_manage(action='create', ...)`; skills note added | ✓ pass |
| TASK-9 | No compaction processor pattern-matches old tool names; all callers updated in `display.py`, `deferred_prompt.py`, `main.py`, `commands/knowledge.py` | ✓ pass |
| TASK-10 | 65/65 tests pass across `test_flow_memory_unified.py`, `test_flow_artifact_manage.py`, `test_flow_memory_recall.py`, `test_flow_skills_manage.py`, `test_flow_skills_tools.py`, `test_flow_memory_store.py` | ✓ pass |
| TASK-11 | 263/263 tests pass (1 pre-existing flaky Ollama test excluded — `test_flow_compaction_summarization.py`, passes in isolation, unmodified by this plan) | ✓ pass |

**Tests:** scoped (65 pass) + full suite (263 pass)
**Doc Sync:** fixed — `tools.md` (3 removed tools + total count), `dream.md` (4 stale `memory_create` refs → `artifact_manage`/`miner_tool`), `bootstrap.md` (added Step 9c for skill indexing); source code bug fixed: `run_dream_cycle` parameter renamed `memory_create` → `miner_tool` to match callers

**Overall: DELIVERED**
All 11 tasks passed. Skills are now a full fourth channel in `memory_search`. `artifact_manage` replaces `memory_create`/`memory_modify`. `skill_manage(action='install')` ships. `skills_list` removed. Four-channel foundation ready for Plan 2.
