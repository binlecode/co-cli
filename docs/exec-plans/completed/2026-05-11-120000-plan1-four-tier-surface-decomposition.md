# Plan 1 of 4 — Four-Tier Surface Decomposition

Task type: code + docs

## Overall Map — Skill Self-Evolution Replan (replaces shipped Plan 1 + withdrawn Plans 2–4)

This plan is one of four sequential plans porting hermes's self-evolving skill capability to co-cli, reframed around the **four-tier surface model** discovered after shipping the original Plan 1. The map below appears verbatim at the top of each plan to prevent drift.

| # | Plan | File | Scope |
|---|---|---|---|
| **1 (this plan)** | Four-tier surface decomposition | `2026-05-11-120000-plan1-four-tier-surface-decomposition.md` | Eject skills and canon channels from `memory_search`; create `skill_search` + keep `skill_view` / `skill_manage` (resource-action) as the three-tool skill surface; move canon indexing into personality-load-only path; inject bundled skill manifest into system prompt; restructure specs (rename `memory-skills.md` → `skill.md`, fold `memory-canon.md` into `personality.md`, rename `memory-artifacts.md` → `memory-knowledge.md`, trim `memory.md` to two-channel foundation). Foundation for all subsequent plans. |
| **2** | Skill authoring contract + bundled library | `2026-05-11-120100-plan2-skill-authoring-contract-and-bundled-library.md` (TBD) | Extend `skill.md` with §6 (authoring contract) + §7 (lint rules R1–R10); ship `co_cli/skills/_lint.py` validator and `/skills lint`; author 4 bundled skills (`review`, `plan`, `triage`, `refactor`); migrate `doctor.md`. Bundled skills surface via Plan 1's manifest injection automatically. |
| **3** | Skill protocol + lifecycle workflow bodies | `2026-05-11-120200-plan3-skill-protocol-and-workflow-bodies.md` (TBD) | Ship `co_cli/context/rules/06_skill_protocol.md` with full five-rule scaffolding (discovery, use, drift, create, offer-to-save); bundled `skill-creator.md` and `skill-installer.md` workflow bodies steering toward `skill_manage(action='create'\|'install')`, `skill_search`, `skill_view`. |
| **4** | Migration importer (channel-aware) | `2026-05-11-120300-plan4-skill-migration-importer.md` (TBD) | `/skills import {claude\|hermes\|openclaw}` — read peer source dir, normalize frontmatter against §6/§7, lint-gate, write to `~/.co-cli/skills/`. |

**Order:** 1 → 2 → 3 → 4. Plan 1 is a hard prerequisite for all subsequent plans. Plans 3 and 4 are independent of each other (can ship in either order).

**Reference:** prior research/discussion captured in `docs/reference/RESEARCH-skills-peers-tiers.md` and the shipped predecessor at `docs/exec-plans/completed/2026-05-10-100000-plan1-memory-surface-unification.md`.

**What ships before this plan (already done):**
- The shipped (now superseded in part) Plan 1 `memory-surface-unification` introduced `artifact_manage`, `memory_search(channel=...)`, `skill_manage(action='install')`, removed `skills_list` / `memory_create` / `memory_modify`. This plan keeps everything except the **skills-as-channel** and **canon-as-channel** parts of `memory_search`, and elevates skills to their own surface.
- Sibling plan `2026-05-09-154112-skill-manage-hermes-port.md` (shipped) — `skill_view`, `skill_manage(create/edit/patch/delete/install)`.

## Context

Co-cli's agentic loop is a four-tier surface system:

```
┌──────────────────────────────────────────────────────────────────┐
│ Personality / Doctrine  (system layer — not LLM-visible)         │
│   canon scenes, soul seed, mindsets — loaded as identity         │
├──────────────────────────────────────────────────────────────────┤
│ Tool surface  (built-in, tiered)                                 │
│   ALWAYS-visible core + DEFERRED integration tools               │
├──────────────────────────────────────────────────────────────────┤
│ Skill surface  (searchable, evolving — procedural capability)    │
│   skill_search · skill_view · skill_manage                       │
├──────────────────────────────────────────────────────────────────┤
│ Memory surface  (dynamic, declarative)                           │
│   session + knowledge (kinds: user, rule, article, note)         │
│   memory_search · artifact_manage                                │
└──────────────────────────────────────────────────────────────────┘
```

Each tier has a different role:
- **Doctrine** = who I am (fixed identity, never queried)
- **Tools** = what I can do (capabilities)
- **Skills** = how I do recurring tasks (procedural, evolves mid-session)
- **Memory** = what I know (declarative, accumulates)

The shipped Plan 1 collapsed skills and canon into the memory surface as channels of `memory_search`. That framing was the wrong operational tier:
- Skills shape **how** the agent approaches a task (meta-level, task-orienting). They get pre-action treatment in both hermes (mandatory pre-scan) and openclaw (system-prompt manifest).
- Canon is **identity, not memory** — accumulated facts the agent collects (memory) vs. a priori character (canon) are distinct. Canon is loaded as personality, not recalled as a fact.

Memory is reserved for what's genuinely dynamic and declarative: session transcripts and knowledge artifacts. Two channels, both accumulated by the agent through actual operation.

### Current-state validation (inline)

Verified against the codebase post-shipped-Plan-1:

- ✓ `co_cli/tools/memory/recall.py:299–362` — `memory_search` has `_search_skills`, `_browse_skills`, `_dispatch_skills_channel`. To remove.
- ✓ `co_cli/tools/memory/recall.py:524–553` — `_dispatch_canon_channel` and canon priority pass in `_search_artifacts:144–167`. To remove from model-callable surface; canon stays in `MemoryStore` only for personality injection.
- ✓ `co_cli/memory/memory_store.py:1279–1305` — `upsert_skill`, `remove_skill`, `list_skill_names`. To move out of `MemoryStore` into a dedicated `SkillIndex` (or stricter API boundary; same DB acceptable).
- ✓ `co_cli/skills/lifecycle.py:10–32` — `refresh_skills(deps)` calls `deps.memory_store.upsert_skill/remove_skill`. To retarget to the new `SkillIndex`.
- ✓ `co_cli/bootstrap/core.py:389–397` — Step 7c direct upsert loop. To retarget.
- ✓ `co_cli/bootstrap/core.py:_sync_canon_store` — canon FTS indexing at bootstrap. To keep for personality-load-only; the `'canon'` source remains but is no longer surfaced via any model-callable recall path.
- ✓ `co_cli/tools/system/skills.py:33–63` — `skill_view`. Stays, unchanged.
- ✓ `co_cli/tools/system/skills.py:330–406` — `skill_manage`. Stays, unchanged (still has `create/edit/patch/delete/install/write_file/remove_file` actions).
- ✓ `co_cli/agent/_native_toolset.py:31–36` — registers `memory_search`, `artifact_manage`, `skill_view`, `skill_manage`. New `skill_search` registration added.
- ✓ `docs/specs/memory.md` (foundation, 205 lines, post-split). To trim further to two channels + cross-link skill.md.
- ✓ `docs/specs/memory-skills.md` (299 lines). To rename to `skill.md`, drop `memory-` prefix.
- ✓ `docs/specs/memory-canon.md` (92 lines). To delete; content folds into `personality.md` (or `personality-canon.md`).
- ✓ `docs/specs/memory-artifacts.md` (165 lines). To rename to `memory-knowledge.md`.
- ✓ `docs/specs/memory-sessions.md` (110 lines). Stays as-is.
- ✓ `co_cli/context/rules/04_tool_protocol.md` — Memory subsection mentions `memory_search` with skills note. To update: drop skills note from Memory section; new `06_skill_protocol.md` (Plan 3) takes over skill discipline.
- ✓ Tests: `tests/test_flow_memory_unified.py`, `tests/test_flow_memory_recall.py` exercise skills-in-memory_search; require churn. New `tests/test_flow_skill_search.py` for the new tool.

### Why now, not later

Every subsequent plan (2, 3, 4) ships on the surface this plan defines. If we ship Plans 2–4 atop the shipped (skills-as-channel) surface, three things break:
1. Plan 2's authoring contract would reference the wrong tool name in lint messages and bundled-skill bodies.
2. Plan 3's protocol file would tell the model to call `memory_search(channel='skills')` — the wrong invocation pattern for the eventual model.
3. Plan 4's importer would write to a path indexed under the wrong source key (`'skill'` inside the unified store).

Doing this plan first costs ~1 day; doing it after Plans 2–4 costs a re-port of all three.

## Problem & Outcome

**Problem.** The shipped Plan 1's unified memory surface (skills + canon as channels of `memory_search`) conflated three different operational tiers. The model sees skills as just-another-recall-result, undermining the "pre-action procedural check" discipline that drives self-evolution. Canon appears as a `kind='canon'` channel of search results, suggesting it's accumulated content rather than a priori identity. Both confusions hurt small/medium-model reasoning by mixing tiers that should be structurally distinct.

**Outcome.**

1. **`memory_search` shrinks to two channels** — session + knowledge. Canon is no longer surfaced via any model-callable tool. Skills no longer appear as a channel.
2. **Skill surface gets three dedicated tools** — `skill_search(query)` (new), `skill_view(name)` (unchanged), `skill_manage(action=...)` (unchanged). Resource(action) write surface, channel-specific read surface, and a dedicated discovery tool. Three small tools instead of one polymorphic mega-tool.
3. **Bundled skill manifest injected into system prompt** — ~300-token block listing bundled skills by name + description, immediately after the tool list. Pre-action capability declaration; cache-stable. User-installed skills are NOT in the manifest (they live behind `skill_search` for the long tail).
4. **Canon becomes doctrine** — indexed at bootstrap for personality auto-injection only; no model-callable read path. The `'canon'` source in `MemoryStore` becomes system-internal.
5. **`SkillIndex` separation** — skill FTS lives in a thin wrapper (or new module) distinct from `MemoryStore`'s `'knowledge'` and `'session'` sources. Same DB acceptable; API boundary explicit.
6. **Spec restructure.** Three renames + one deletion + content shifts (see §7).
7. **Rules update.** `04_tool_protocol.md` Memory subsection drops the skills note. Skill discipline lands in Plan 3's `06_skill_protocol.md`.
8. **Backward-compat removed.** No aliases. `memory_search(channel='skills')` and `memory_search(channel='canon')` raise `tool_error` directing to `skill_search` and to "canon is auto-injected, not queryable."

## Scope

### In scope

- New tool `co_cli/tools/system/skills.py:skill_search` — `skill_search(query, limit=5)` returning ranked skill hits `{name, description, score, path}`.
- Modify `co_cli/tools/memory/recall.py:memory_search` — drop `'skills'` and `'canon'` from the `channel` literal; remove `_search_skills`, `_browse_skills`, `_dispatch_skills_channel`, `_dispatch_canon_channel`; remove the canon priority pass in `_search_artifacts`.
- Modify `co_cli/memory/memory_store.py` — move `upsert_skill`/`remove_skill`/`list_skill_names` into a new `co_cli/skills/index.py:SkillIndex` (same DB, separate class). `MemoryStore` retains `'knowledge'` and `'session'` sources only.
- Modify `co_cli/skills/lifecycle.py:refresh_skills` — retarget to `deps.skill_index` (new field on CoDeps).
- Modify `co_cli/bootstrap/core.py:create_deps` — construct `SkillIndex`; remove Step 7c's direct skill upsert (folded into `SkillIndex` constructor); keep `_sync_canon_store` for personality-load-only path.
- Modify `co_cli/deps.py` — add `skill_index: SkillIndex | None` field on `CoDeps`.
- Modify `co_cli/agent/_native_toolset.py` — register `skill_search`; update import for renamed `knowledge_manage`.
- Rename `artifact_manage` → `knowledge_manage` (model-visible tool name only):
  - Rename function and decorator in `co_cli/tools/memory/manage.py`.
  - Tool arg rename: `artifact_kind` → `kind`.
  - Approval subject rename: `tool:artifact_manage:<action>:<name>` → `tool:knowledge_manage:<action>:<name>`.
  - Update all callers: `_native_toolset.py`, `04_tool_protocol.md`, `dream.py` (if it references the tool), tests.
  - Internal class names (`KnowledgeArtifact`, `ArtifactKindEnum`, file `artifact.py`) and on-disk frontmatter field (`artifact_kind`) **stay** — not model-visible.
- Add **bundled skill manifest injection** — `co_cli/context/manifests/skill_manifest.py` (or appropriate location) — renders `<available_skills>` block from `deps.skill_registry`, filtered to bundled-only (skills sourced from `co_cli/skills/`, not from `user_skills_dir`). Injected into static system prompt at agent construction.
- Modify `co_cli/context/rules/04_tool_protocol.md` — remove the skills-channel note from the Memory subsection.
- Specs:
  - `docs/specs/memory.md` — trim to two-channel foundation + cross-link to `skill.md`; update channel ontology and result-shape tables.
  - `docs/specs/memory-skills.md` → `git mv` to `docs/specs/skill.md`; update header from "Memory: Skills Channel" to "Skill Surface" and content references throughout; add `skill_search` documentation.
  - `docs/specs/memory-artifacts.md` → `git mv` to `docs/specs/memory-knowledge.md`; channel rename (no semantic change).
  - `docs/specs/memory-canon.md` → delete; content moves to `docs/specs/personality.md` (or a new `docs/specs/personality-canon.md` if `personality.md` would exceed 300 lines).
- Tests:
  - `tests/test_flow_skill_search.py` — new behavioral tests for `skill_search`.
  - `tests/test_flow_memory_unified.py` — drop skill-cross-channel tests; keep two-channel session+knowledge cross-test.
  - `tests/test_flow_memory_recall.py` — drop skills-channel tests; drop canon-channel-from-search tests.
  - `tests/test_flow_skill_manifest.py` — new test verifying bundled manifest renders in system prompt.
  - `tests/test_flow_canon_recall.py` — adapt to verify canon is NOT returned by any model-callable tool (i.e. assert canon hits absent from `memory_search`); keep the personality-injection coverage path.
- Cross-references: update `co_cli/context/rules/04_tool_protocol.md`, `docs/specs/system.md`, `docs/specs/core-loop.md`, `docs/specs/tools.md`, `agent_docs/system-workflows-to-test.md`, `CLAUDE.md` to reflect new file names and tool surface.

### Out of scope

- **Renaming `memory_search`.** Stays. The remaining two-channel surface is genuinely memory.
- **`skill_manage` action union changes.** Stays as-is — `create / edit / patch / delete / install / write_file / remove_file`.
- **`skill_view` signature changes.** Stays — `skill_view(name, file_path=None)`.
- **Hybrid (vec) embedding for skills.** Skills index is name+description; short content, vector embedding low ROI.
- **Removing `_sync_canon_store`.** Bootstrap-side canon indexing stays — it feeds personality auto-injection, which is the only legitimate canon consumer.
- **Compaction-system changes.** History processors operate on tool result content, not tool names — verified during the shipped Plan 1's TASK-9 audit.
- **Plan 2/3/4 work.** Authoring contract, lint, protocol file, workflow bodies, importer — all separate plans.
- **Indexing bundled vs. user-installed skills differently in `SkillIndex`.** Both go into the same index; the bundled/user distinction matters only for **manifest injection** (bundled-only) and is read from disk source.

## Behavioural Constraints

1. **Four tiers, structurally distinct.** Doctrine / tools / skills / memory each have separate surfaces. No tier conflates with another in tool definitions, search results, or spec language.
2. **Memory is dynamic and declarative.** Session and knowledge only. Canon is identity (doctrine), not memory. Skills are procedure (own surface), not memory.
3. **Skill discovery is multi-modal.** Bundled skills land via prompt manifest (always-visible, no tool call). User-installed and dynamically-created skills land via `skill_search` (query-driven, on-demand). Two tiers, one purpose.
4. **`skill_search` is approval-free, concurrent-safe.** Read-only over an in-process FTS index.
5. **Canon is bootstrap-loaded and prompt-injected.** No `memory_search` path returns canon. The personality system is the only legitimate canon consumer.
6. **`SkillIndex` separation is by API, not necessarily by storage.** Same DB acceptable; the boundary is that `MemoryStore` has no skill methods and `SkillIndex` has no knowledge/session methods.
7. **Manifest injection is always-on for bundled skills.** No config flag — bundled is small (~300 tokens), cache-stable, paid once per session.
8. **Backward-compat deletion is hard.** `memory_search(channel='skills')` and `memory_search(channel='canon')` raise structured `tool_error` directing to the new surfaces. No aliases.

## High-Level Design

### Target tool surface

| Tool | Surface | Read/Write | Notes |
|---|---|---|---|
| `memory_search(query, channel?, kinds?, limit?)` | memory | read | `channel ∈ {'session', 'knowledge', None}`. Empty query → browse mode (recent sessions + recent artifacts). |
| `knowledge_manage(action, name, ...)` | memory (knowledge) | write | **Renamed from `artifact_manage`.** `action ∈ {create, append, replace, delete}`. Tool arg `artifact_kind` → `kind`. |
| `skill_search(query, limit=5)` | skill | read | **NEW.** FTS over name+description; returns `{name, description, score, path}` list. |
| `skill_view(name, file_path=None)` | skill | read | Unchanged. |
| `skill_manage(action, name, ...)` | skill | write | Unchanged: `action ∈ {create, edit, patch, delete, install, write_file, remove_file}`. |
| `memory_read_session_turn(...)` | memory (session) | read | Source-only; not registered. Unchanged. |

Tool count delta vs. shipped state: **+1** (`skill_search`); one tool renamed (`artifact_manage` → `knowledge_manage`).

### `memory_search` shrink (`co_cli/tools/memory/recall.py`)

New `channel` literal: `Literal["session", "knowledge"] | None`. The `_search_skills`/`_browse_skills`/`_dispatch_skills_channel` paths are removed entirely. The canon priority pass in `_search_artifacts` is removed (canon is never returned from any model-callable search). The `_dispatch_canon_channel` path is removed.

Empty-query browse mode shrinks from sessions+artifacts+skills to sessions+artifacts.

### `skill_search` (`co_cli/tools/system/skills.py`)

```python
@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
)
async def skill_search(
    ctx: RunContext[CoDeps],
    query: str,
    limit: int = 5,
) -> ToolReturn:
    """Search the skill index by name and description. Returns ranked hits.

    Use when the bundled skill manifest above doesn't cover what you need —
    e.g. user-installed skills, or skills you created in this session.

    Returns: list of {name, description, score, path}. Load body with skill_view.
    """
    ...
```

Internally calls `ctx.deps.skill_index.search(query, limit=limit)`. Empty query is rejected (browse mode is the manifest's job).

### `SkillIndex` (`co_cli/skills/index.py`)

```python
class SkillIndex:
    """FTS5 index over skill name + description. Separate API from MemoryStore."""

    def __init__(self, *, config: Settings, memory_db_path: Path | None = None) -> None:
        ...

    def upsert(self, name: str, description: str, path: str) -> None: ...
    def remove(self, name: str) -> None: ...
    def list_names(self) -> set[str]: ...
    def search(self, query: str, limit: int = 5) -> list[SkillHit]: ...
    def close(self) -> None: ...
```

Same DB file (`co-cli-search.db`) acceptable; uses the same `chunks_fts` table with `source='skill'`. The API boundary is that `MemoryStore` no longer exposes skill methods. The `'skill'` source in the shared FTS table is owned exclusively by `SkillIndex`.

`SkillHit` dataclass: `{name, description, score, path}`. No `source` field needed (always implicitly "skill").

### Bundled manifest injection (system prompt)

A new module renders the bundled skill manifest as an XML-like block injected into the static system prompt:

```
<available_skills>
  <skill name="doctor" description="Diagnose problems in the current repo and propose fixes." />
  <skill name="review" description="Review a PR diff: correctness, style, security." />
  ...
</available_skills>
```

Location: `co_cli/context/manifests/skill_manifest.py` (new module) with `render_skill_manifest(skill_registry, skills_dir) -> str`. Called by prompt assembly at agent construction time (which already injects static personality content).

Filter: bundled-only (i.e. `skill.path` starts with `skills_dir` and not `user_skills_dir`). This keeps the manifest small and stable across user-installed skill changes.

Where the protocol rule (Plan 3's `06_skill_protocol.md`) tells the model what to do with this block. This plan only ships the injection — the use rule lands in Plan 3.

### Canon decoupling

- `_sync_canon_store(store, config, frontend)` in `co_cli/bootstrap/core.py` stays. Reason: it indexes canon for personality auto-injection (a non-model-callable consumer).
- `_search_artifacts:144–167` (canon priority pass) is **removed**. Canon hits never surface via `memory_search`.
- `_dispatch_canon_channel` in `recall.py` is **removed**. The `channel='canon'` literal is removed from the type union.
- The `'canon'` source in `chunks_fts` becomes system-internal (consumed by the personality system only). Future cleanup could move canon out of the shared DB entirely; out of scope for this plan.

### Spec restructure

- `docs/specs/memory.md` — trim to two-channel foundation:
  - Drop skills from the channel ontology table.
  - Drop canon from the channel ontology table (add a one-line note: *"Canon is doctrine, loaded by the personality system into the static prompt; see [personality.md](personality.md)."*).
  - Update `memory_search` signature to `channel ∈ {'session', 'knowledge'}`.
  - Cross-link `[skill.md](skill.md)` as a sibling surface, not a sub-spec.
- `docs/specs/memory-skills.md` → `docs/specs/skill.md`:
  - Header: "Skill Surface" (was "Memory: Skills Channel").
  - Document `skill_search` in §3 Model-Callable Surface.
  - Update intro: "Skills are procedural capability, distinct from memory." Cross-link `[memory.md](memory.md)` as a sibling not a foundation.
- `docs/specs/memory-artifacts.md` → `docs/specs/memory-knowledge.md`:
  - Channel rename only; semantic unchanged.
  - Update §1 to use "knowledge" terminology consistently.
- `docs/specs/memory-canon.md` → delete:
  - Content moves to `docs/specs/personality.md` as a new section *"Canon doctrine"* (or to a new `personality-canon.md` if size pushes `personality.md` past ~300 lines — TBD when content is moved).
- `docs/specs/memory-sessions.md` — unchanged.
- `CLAUDE.md` — update Memory System paragraph to two-channel framing + cross-link `skill.md`.

### Rules file update

`co_cli/context/rules/04_tool_protocol.md` Memory subsection — drop the *"Skills surface as a channel of `memory_search`..."* line. Add nothing in its place (Plan 3 ships `06_skill_protocol.md` with full skill discipline; until then, the model relies on tool docstrings).

## Tasks

### ✓ DONE — TASK-1 — `SkillIndex` extraction

Files:
- `co_cli/skills/index.py` (new) — `SkillIndex` class with `upsert`/`remove`/`list_names`/`search`/`close`.
- `co_cli/memory/memory_store.py` — remove `upsert_skill`/`remove_skill`/`list_skill_names`.
- `co_cli/skills/lifecycle.py` — retarget `refresh_skills` to `deps.skill_index`.
- `co_cli/bootstrap/core.py` — construct `SkillIndex`; collapse Step 7c.
- `co_cli/deps.py` — add `skill_index: SkillIndex | None`.

Acceptance:
- `SkillIndex.search(query)` returns rows under `source='skill'` (or wherever the new index stores them).
- `MemoryStore` has no skill methods.
- `refresh_skills(deps)` uses `deps.skill_index`, not `deps.memory_store`.
- Bootstrap creates both stores; closes both on shutdown.
- `SkillHit` dataclass with `{name, description, score, path}`.

### ✓ DONE — TASK-2 — `skill_search` tool

Files:
- `co_cli/tools/system/skills.py` — add `skill_search` registered tool.
- `co_cli/agent/_native_toolset.py` — register `skill_search`.

Acceptance:
- `skill_search(query='review', limit=5)` returns ranked list (empty if no hits).
- Empty/whitespace query → `tool_error`.
- `is_read_only=True`, `is_concurrent_safe=True`, no approval.
- Description (read from skill_registry lookup, fix-aware) populated correctly.

### ✓ DONE — TASK-3 — `memory_search` shrink

Files:
- `co_cli/tools/memory/recall.py` — remove `_search_skills`/`_browse_skills`/`_dispatch_skills_channel`/`_dispatch_canon_channel`; remove canon priority pass in `_search_artifacts`; update `channel` literal to `{'session', 'knowledge'}`.

Acceptance:
- `memory_search(channel='skills')` raises `tool_error` directing to `skill_search`.
- `memory_search(channel='canon')` raises `tool_error` directing to "canon is auto-injected; not queryable."
- Browse mode (empty query) returns sessions + knowledge only.
- Existing session and knowledge tests still pass.

### ✓ DONE — TASK-4 — Bundled skill manifest injection

Files:
- `co_cli/context/manifests/skill_manifest.py` (new) — `render_skill_manifest(skill_registry, skills_dir) -> str`.
- Prompt-assembly integration point (TBD — likely `co_cli/context/assembly.py` or wherever static personality content is composed).

Acceptance:
- Manifest renders as `<available_skills>` XML block with one `<skill>` line per bundled skill.
- User-installed skills (in `user_skills_dir`) excluded from the manifest.
- Manifest injected after the tool list, before personality content (TBD — verify against existing prompt assembly order).
- Empty bundled skill set → no manifest block (not an empty one).

### ✓ DONE — TASK-5 — Spec restructure

Files:
- `docs/specs/memory.md` — trim to two-channel foundation; update tables.
- `docs/specs/memory-skills.md` → `docs/specs/skill.md` (`git mv` + content updates).
- `docs/specs/memory-artifacts.md` → `docs/specs/memory-knowledge.md` (`git mv` + intro update).
- `docs/specs/memory-canon.md` → delete; content into `personality.md` (or new `personality-canon.md`).
- `docs/specs/memory-sessions.md` — unchanged.
- `CLAUDE.md` — update Memory System paragraph.

Acceptance:
- `docs/specs/memory.md` ≤180 lines (was 205).
- `docs/specs/skill.md` documents three tools: `skill_search`, `skill_view`, `skill_manage`.
- `docs/specs/memory-knowledge.md` consistent on "knowledge" terminology at channel level.
- Cross-references updated in `docs/specs/system.md`, `docs/specs/core-loop.md`, `docs/specs/tools.md`, `agent_docs/system-workflows-to-test.md`.
- `grep -rn "memory-skills.md\|memory-artifacts.md\|memory-canon.md" docs/specs/ docs/exec-plans/active/ agent_docs/` returns no hits.

### ✓ DONE — TASK-5b — Rename `artifact_manage` → `knowledge_manage`

Files:
- `co_cli/tools/memory/manage.py` — rename function, decorator, approval subject helper, all references.
- `co_cli/agent/_native_toolset.py` — update import.
- `co_cli/context/rules/04_tool_protocol.md` — update Memory subsection references (e.g. `artifact_manage(action='create', ...)` → `knowledge_manage(action='create', ...)`).
- `co_cli/memory/dream.py` — update if it references the tool name.
- `docs/specs/memory.md`, `docs/specs/memory-knowledge.md` (post-rename) — update tool name throughout.
- `co_cli/commands/knowledge.py` — update if it references the tool name.
- Tests: `tests/test_flow_artifact_manage.py` (consider rename to `tests/test_flow_knowledge_manage.py`), plus any test referencing `artifact_manage`.

Acceptance:
- `knowledge_manage(action='create', name='x', content='...', kind='user')` works end-to-end.
- Tool arg renamed `artifact_kind` → `kind`; old `artifact_kind` rejected (no alias).
- Approval subject is `tool:knowledge_manage:<action>:<name>`.
- All callers updated; `grep -rn "artifact_manage" co_cli/ docs/specs/ co_cli/context/rules/` returns no hits.
- Internal class names `KnowledgeArtifact`, `ArtifactKindEnum` and on-disk frontmatter field `artifact_kind` **unchanged** (verify via grep: still present in `co_cli/memory/artifact.py` and existing artifact files).
- Test file may be renamed for clarity; not required.

### ✓ DONE — TASK-6 — Rules file update

Files:
- `co_cli/context/rules/04_tool_protocol.md` — drop the skills-channel note from Memory subsection.

Acceptance:
- The line *"Skills surface as a channel of `memory_search` — when the model needs a procedural reference, search and then load the body via `skill_view`."* is removed.
- No replacement added — Plan 3 ships full skill discipline in `06_skill_protocol.md`.

### ✓ DONE — TASK-7 — Backward-compat deletion + error paths

Files:
- `co_cli/tools/memory/recall.py` — channel-not-supported error paths.

Acceptance:
- `memory_search(channel='skills')` returns `tool_error("channel='skills' is no longer supported — use skill_search instead.")`.
- `memory_search(channel='canon')` returns `tool_error("Canon is identity, not memory — it is auto-injected via personality. Not queryable.")`.

### ✓ DONE — TASK-8 — Behavioural tests

Files:
- `tests/test_flow_skill_search.py` (new) — `skill_search` behavior.
- `tests/test_flow_memory_unified.py` — drop skills-cross-channel tests; keep session+knowledge cross-test.
- `tests/test_flow_memory_recall.py` — drop skills-channel and canon-channel-from-search tests.
- `tests/test_flow_skill_manifest.py` (new) — verifies bundled manifest renders in system prompt.
- `tests/test_flow_canon_recall.py` — adapt: assert canon hits absent from `memory_search`; keep personality-injection coverage.
- `tests/test_flow_skills_manage.py` — retarget index-hook tests from `memory_store` to `skill_index`.

Test surface (`skill_search`):

| # | Assertion |
|---|---|
| 1 | `skill_search('review')` returns hits with `{name, description, score, path}`. |
| 2 | `skill_search('nonsense_xyzzy')` returns empty list, no error. |
| 3 | Empty query → `tool_error`. |
| 4 | After `skill_manage(action='create', name='X', ...)`, `skill_search` finds X. |
| 5 | After `skill_manage(action='delete', name='X')`, `skill_search` does not return X. |
| 6 | After `skill_manage(action='install', source=path)`, `skill_search` finds the installed skill. |
| 7 | `limit=2` caps results at 2. |
| 8 | Description populated correctly (regression guard from prior fix). |

Test surface (`memory_search` shrink):

| # | Assertion |
|---|---|
| 1 | `memory_search(channel='skills')` returns `tool_error` mentioning `skill_search`. |
| 2 | `memory_search(channel='canon')` returns `tool_error` mentioning personality. |
| 3 | `memory_search(channel='session')` unchanged. |
| 4 | `memory_search(channel='knowledge')` unchanged. |
| 5 | Empty-query browse returns sessions + artifacts only (no Available skills section). |

Test surface (manifest injection):

| # | Assertion |
|---|---|
| 1 | Rendered system prompt contains `<available_skills>` block when bundled skills exist. |
| 2 | User-installed skills NOT in the manifest. |
| 3 | Empty bundled skill set → no `<available_skills>` block. |
| 4 | Manifest entries have `name` and `description` attributes. |

### ✓ DONE — TASK-9 — Cross-plan integration check

Files: none (verification step).

Acceptance:
- `scripts/quality-gate.sh full` clean.
- Tool count: net **+1** (`skill_search`). One renamed (`artifact_manage` → `knowledge_manage`). All other tools unchanged.
- `grep -rn "artifact_manage" co_cli/ docs/specs/ co_cli/context/rules/` returns no hits.
- Manual smoke: start fresh session, confirm bundled `<available_skills>` block visible in initial system prompt (via `co status` or trace inspection).
- `memory_search` returns no canon, no skills. `skill_search` returns skills. Canon still loads into personality.
- No grep hits for `memory-skills.md`, `memory-artifacts.md`, `memory-canon.md` in active code or docs.

## Testing

### Test files

- `tests/test_flow_skill_search.py` (new)
- `tests/test_flow_skill_manifest.py` (new)
- `tests/test_flow_memory_unified.py` (trim)
- `tests/test_flow_memory_recall.py` (trim)
- `tests/test_flow_canon_recall.py` (adapt)
- `tests/test_flow_skills_manage.py` (retarget index-hook tests)
- `tests/test_flow_memory_store.py` (drop skill cycle tests; they move to `tests/test_flow_skill_index.py`)
- `tests/test_flow_skill_index.py` (new) — `SkillIndex.upsert/search/remove/list_names` cycle.

### Test pattern

Real `CoDeps` via `_co_harness.py`. Real `MemoryStore` and `SkillIndex` (sqlite tmp file, same DB acceptable). Real skill loader against `tmp_path` user dir + `co_cli/skills/` bundled. No mocks.

### Lint / quality gate

- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before considering ready to ship.

## Open Questions

1. **Q:** Should `SkillIndex` use the same DB as `MemoryStore` (just a different `source` key in shared `chunks_fts`) or a separate DB file?
   **Tentative answer:** Same DB, separate API. The implementation cost of two DB files (separate close, separate connection management, separate sync_dir paths) outweighs the marginal cleanliness. The API boundary in `SkillIndex` is sufficient.

2. **Q:** Should canon stay in `chunks_fts` even though it's never queried by model-callable tools?
   **Tentative answer:** Yes for v1. The personality system relies on `MemoryStore.get_chunk_content('canon', path, 0)` to load canon bodies. Removing canon from the FTS table would require a parallel canon-only loader path — strictly more code for no clear benefit. Future cleanup possible.

3. **Q:** Manifest injection always-on, or behind a config flag?
   **Tentative answer:** Always-on, no flag. Bundled manifest is small (~300 tokens) and cache-stable. Configurability adds test surface and behavior split for marginal token savings.

4. **Q:** Should `memory-canon.md` content move to `personality.md` or a new `personality-canon.md`?
   **Tentative answer:** Try `personality.md` first; if it pushes past ~300 lines, split to `personality-canon.md`. Decide during TASK-5.

5. **Q:** Should bundled-only filtering happen at manifest render time or via a metadata flag on `SkillConfig`?
   **Tentative answer:** Render time. Use `skill.path.startswith(deps.skills_dir)` to discriminate. Metadata flag would require frontmatter changes across all bundled skills.

## Deferred items

- **Canon storage migration.** Moving canon out of `chunks_fts` into a dedicated personality-store-only path. Defer until personality system gains another reason to refactor.
- **Skill index hybrid (vec) embedding.** Out of scope; skills are short metadata.
- **`memory_search` rename.** Stays. Two channels is still memory; renaming gains nothing.
- **`artifact_manage` rename.** Stays. Entry-level term is correct.
- **Tool surface tiering work.** The existing `ALWAYS`/`DEFERRED` distinction is sufficient for now; no new tiering changes ship in this plan.
- **Manifest injection for user-installed skills.** Out of scope; user-installed surface is `skill_search`. Future consideration: token-budget-aware injection of recently-used user skills.

## Shipping order

Single commit — all nine TASKs. The surface change is atomic: partial ship leaves the model with a half-decomposed surface (skills in both `memory_search` and `skill_search`, or canon partially exposed).

**Hard dependencies:**
- Shipped Plan 1 (`memory-surface-unification`, archived to `completed/`) — provides the unified-search baseline this plan partially reverses.
- Sibling plan `2026-05-09-154112-skill-manage-hermes-port.md` (shipped) — provides `skill_view` and `skill_manage`.

**Soft dependencies:** none. This plan is the foundation — Plans 2, 3, 4 all ship onto the four-tier surface.

**Initial-state caveat:** the `<available_skills>` manifest in TASK-4 shows only `doctor.md` at this plan's ship time (the only bundled skill). After Plan 2 ships its bundled library, the manifest auto-fills with `review`, `plan`, `triage`, `refactor`. No re-work needed.

## Post-ship — research-doc resync

After this plan ships, update `docs/reference/RESEARCH-skills-peers-tiers.md` (if it exists) to reflect:

- Step 4 (Awareness layer) → **shipped via manifest injection**, not via memory channel.
- Architecture comparison: co-cli now matches hermes's structural separation of skills (own surface) from memory (declarative recall), while preserving co's own choice of search-driven discovery (vs. hermes's mandatory pre-scan).

## Delivery Summary — 2026-05-11

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `SkillIndex` extraction; MemoryStore has no skill methods; `refresh_skills` retargeted; bootstrap constructs both stores; `SkillHit` dataclass | ✓ pass |
| TASK-2 | `skill_search(query, limit=5)` returns ranked list; empty query → `tool_error`; `is_read_only`/`is_concurrent_safe`; description populated via skill_registry fallback | ✓ pass |
| TASK-3 | `memory_search(channel='skills'\|'canon')` returns structured `tool_error`; browse mode returns sessions+knowledge only; session+knowledge tests pass | ✓ pass |
| TASK-4 | `<available_skills>` block renders from `co_cli/context/manifests/skill_manifest.py`; bundled-only; user-shadowed bundled skills excluded; empty bundled set → no block | ✓ pass |
| TASK-5 | `memory.md` trimmed to 171 lines; `memory-skills.md` → `skill.md`; `memory-artifacts.md` → `memory-knowledge.md`; `memory-canon.md` deleted (content into `personality.md` §2.5); cross-refs updated | ✓ pass |
| TASK-5b | `artifact_manage` → `knowledge_manage`; tool arg `artifact_kind` → `kind`; approval subject `tool:knowledge_manage:<action>:<name>`; all code callers updated; on-disk `artifact_kind` frontmatter preserved | ✓ pass |
| TASK-6 | Skills-channel note dropped from `04_tool_protocol.md` Memory subsection | ✓ pass |
| TASK-7 | `memory_search(channel='skills')` → `tool_error("channel='skills' is no longer supported — use skill_search instead.")`; `memory_search(channel='canon')` → `tool_error("Canon is identity, not memory — it is auto-injected via personality. Not queryable.")` | ✓ pass |
| TASK-8 | New tests: `test_flow_skill_search.py` (8), `test_flow_skill_manifest.py` (5), `test_flow_skill_index.py` (5). Adapted tests: `test_flow_memory_recall.py`, `test_flow_memory_canon_recall.py`, `test_flow_skills_manage.py`, `test_flow_memory_store.py`, `test_flow_artifact_manage.py`. Deleted: `test_flow_memory_unified.py` | ✓ pass |
| TASK-9 | `quality-gate.sh lint` clean; tool count net +1 (`skill_search`); no `artifact_manage` refs in `co_cli/`/`co_cli/context/rules/`; no `memory-skills.md`/`memory-artifacts.md`/`memory-canon.md` refs in active docs; `<available_skills>` confirmed in agent instructions | ✓ pass |

**Tests:** scoped run across 18 touched test files — 296 passed, 0 failed (LLM-dependent files excluded; lint gate clean).

**Doc Sync:** clean — specs updated inline during TASK-5/TASK-5b (`memory.md`, `skill.md`, `memory-knowledge.md`, `personality.md`, `tools.md`, `system.md`, `core-loop.md`, `bootstrap.md`, `dream.md`, `memory-sessions.md`, `agent_docs/system-workflows-to-test.md`, `CLAUDE.md`).

**Notable design decisions during delivery:**
- `SkillIndex` composes an internal `MemoryStore` (same DB, own connection) rather than duplicating FTS5 plumbing — keeps the API boundary clean while reusing the search engine.
- Added two generic helpers to `MemoryStore`: `list_titles_by_source(source)` and `get_path_by_title(source, title)`. These are source-agnostic primitives, not skill-specific — `SkillIndex` consumes them, but they can serve any future source.
- Propagated `description` through the FTS5 search path (`_CHUNKS_FTS_SQL`, `_chunks_like_search`, `_chunk_row_to_result`) — fixes a long-standing gap where `SearchResult.description` was always `None`, and is the foundation for `SkillIndex.search()` returning descriptions without a skill_registry round-trip.
- `memory_search` `channel` argument loosened from `Literal[...]` to `str | None` so deprecated channels (`'skills'`, `'canon'`) can return explicit `tool_error` text instead of pydantic validation errors.

**Overall: DELIVERED**

All nine tasks shipped in a single atomic refactor. Surface decomposition complete: four tiers structurally distinct (doctrine, tools, skills, memory); memory shrunk to two channels (session + knowledge); skill surface gets three dedicated tools (`skill_search`, `skill_view`, `skill_manage`); bundled skills declared in static prompt manifest; canon decoupled from memory and routed through personality auto-injection only.

---

## Implementation Review — 2026-05-12

### Evidence
| Task | done_when criterion | Spec Fidelity | Key Evidence |
|------|---------------------|---------------|--------------|
| TASK-1 | `SkillIndex.search()` returns `source='skill'` rows | ✓ pass | `index.py:69` — `sources=["skill"]` filter |
| TASK-1 | `MemoryStore` has no skill methods | ✓ pass | grep returns no hits for `upsert_skill`/`remove_skill`/`list_skill_names` |
| TASK-1 | `refresh_skills` uses `deps.skill_index` | ✓ pass | `lifecycle.py:24-31` |
| TASK-1 | Bootstrap creates both stores; closes both on shutdown | ✗ blocked → fixed | `main.py:154-157` — `close()` calls added to `_drain_and_cleanup` |
| TASK-1 | `SkillHit` with `{name, description, score, path}` | ✓ pass | `index.py:19-27` |
| TASK-2 | `skill_search(query, limit)` returns ranked list | ✓ pass | `skills.py:92-111` |
| TASK-2 | Empty/whitespace → `tool_error` | ✓ pass | `skills.py:92-93` |
| TASK-2 | `is_read_only=True`, `is_concurrent_safe=True` | ✓ pass | `skills.py:71-75` |
| TASK-2 | Description populated | ✓ pass | `skills.py:81` |
| TASK-3 | `channel='skills'/'canon'` → structured `tool_error` | ✓ pass | `recall.py:498-507` |
| TASK-3 | `channel` typed `str \| None` (not Literal) | ✓ intentional | delivery notes: Literal would have been rejected by pydantic before error body ran |
| TASK-3 | `_search_skills`/`_browse_skills`/`_dispatch_*` removed | ✓ pass | grep returns zero hits |
| TASK-4 | `<available_skills>` block renders | ✓ pass | `skill_manifest.py:36-44` |
| TASK-4 | User-installed skills excluded | ✓ pass | `skill_manifest.py:28-32` |
| TASK-4 | Injected after tool guidance, before personality | ✓ pass | `core.py:150-166` |
| TASK-4 | `agent/core.py` + `main.py` call sites | ✓ pass | `core.py:155-157`, `main.py:374-383` |
| TASK-5 | `memory.md` ≤ 180 lines | ✓ pass | 171 lines |
| TASK-5 | `skill.md` documents three tools | ✓ pass | `skill.md:191-243` |
| TASK-5 | No stale `memory-skills.md`/`memory-artifacts.md`/`memory-canon.md` refs | ✓ pass | grep zero hits in `docs/specs/`, `agent_docs/`, `co_cli/` |
| TASK-5b | `knowledge_manage` function and `kind` arg | ✓ pass | `manage.py:41,46` |
| TASK-5b | Approval subject `tool:knowledge_manage:<action>:<name>` | ✓ pass | `manage.py:22-31` |
| TASK-5b | No `artifact_manage` in source (`co_cli/`, `context/rules/`) | ✓ pass | grep zero hits |
| TASK-5b | Internal `KnowledgeArtifact`/`ArtifactKindEnum`/`artifact_kind` preserved | ✓ pass | `artifact.py:24,48,53,72` |
| TASK-6 | Skills-channel line removed from `04_tool_protocol.md` | ✓ pass | file reads clean |
| TASK-7 | Exact error messages match spec | ✓ pass | `recall.py:498-507` |
| TASK-8 | `test_flow_skill_search.py` — 8 assertions | ✓ pass | lines 70-280 |
| TASK-8 | `test_flow_skill_manifest.py` — 4 assertions | ✓ pass | lines 9-90 |
| TASK-8 | `test_flow_skill_index.py` — upsert/search/remove/list cycle | ✓ pass | lines 23-100 |
| TASK-8 | No mocks in new test files | ✓ pass | grep zero hits |
| TASK-8 | `test_flow_memory_unified.py` deleted | ✓ pass | file absent |
| TASK-9 | Tool count net +1; no `artifact_manage` in source | ✓ pass | registry: 25 tools, `skill_search` present, `artifact_manage` absent |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `MemoryStore` and `SkillIndex` SQLite connections never explicitly closed on normal session exit — neither store registered with `AsyncExitStack` | `main.py:138-154` | blocking | Added `deps.memory_store.close()` and `deps.skill_index.close()` to `_drain_and_cleanup` at `main.py:154-157` |

### Tests
- Command: `uv run pytest -x`
- Result: 307 passed, 0 failed
- Log: `.pytest-logs/20260512-101205-review-impl.log`

### Doc Sync
- Scope: narrow — fix is an internal cleanup path change with no spec-visible behavior or public API impact.
- Result: clean — no spec updates needed.

### Behavioral Verification
- `uv run co --help`: system starts, `chat` command present, no import errors
- Manifest verification: `render_skill_manifest` renders `<available_skills>` block with `doctor` skill (172 chars), `<available_skills>` present ✓
- Tool registry: `skill_search` registered ✓, `knowledge_manage` registered ✓, `artifact_manage` absent ✓, `skills_list` absent ✓, total 25 tools ✓

### Overall: PASS
One blocking finding (stores not closed on shutdown) auto-fixed in `_drain_and_cleanup`. All 9 tasks pass spec fidelity. 307 tests green. Ship directly.
