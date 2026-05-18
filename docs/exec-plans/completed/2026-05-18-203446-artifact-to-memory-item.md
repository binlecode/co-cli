# Artifact → MemoryItem Simplification

## Context

The `artifact` semantic layer in `co_cli/memory/` is a wrapper noun that buys nothing. `MemoryArtifact` (the dataclass) has zero kind-specific fields — `user` / `rule` / `article` / `note` / `canon` items all share the identical schema. The only differentiator is the `artifact_kind` string. Peer systems (mem0 `MemoryItem`, autogen `MemoryContent`, elizaos `Memory` + fact subtype) converge on `MemoryItem + kind` for exactly this shape.

The half-finished rename is already visible in code: `co_cli/memory/store.py:89` reads `frontmatter.get("artifact_kind") or frontmatter.get("kind")`, and live files in `~/.co-cli/knowledge/*.md` carry both `kind: knowledge` (domain) and `artifact_kind: <subtype>` (within-domain kind). The domain field is being addressed by the upstream `knowledge → memory` rename; this plan collapses the within-domain `artifact_kind` into `memory_kind` and renames the dataclass and helpers accordingly.

**Upstream prerequisite — SHIPPED in v0.8.212 (commit `aea8683`).** The `knowledge → memory` refactor (`docs/exec-plans/completed/2026-05-17-221710-memory-module-refactor.md`) explicitly preserved `MemoryArtifact` and `ArtifactKindEnum`. Post-ship state:
- DB source value is `'memory'`
- Tools are `memory_search` / `memory_view` / `memory_manage`
- Settings are `MemorySettings`, env vars `CO_MEMORY_*`
- `IndexStore`/`MemoryStore`/`SessionStore` split is in place
- Live user data: per upstream's zero-migration policy, users move `~/.co-cli/knowledge/` → `~/.co-cli/memory/` manually. As of this plan's drafting, `~/.co-cli/knowledge/` still exists on the dev machine; the migration script (Task 7) walks the configured `memory_dir` and is a no-op until the user moves their data, then idempotent after.

This plan removes the remaining `artifact` semantic layer that sits inside the memory domain.

**Per zero-backward-compat rule** (`feedback_zero_backward_compat`): hard rename, no aliases. **One real cost**: existing user `~/.co-cli/memory/*.md` frontmatter carries `artifact_kind:` — a one-shot scan-and-rewrite script migrates the field name. The script runs once at startup (idempotent: reads `artifact_kind`, writes `memory_kind`, deletes the old key) and then disappears next release. See Task 7.

## Decisions

| Decision | Choice |
|---|---|
| Dataclass name | `MemoryArtifact` → `MemoryItem` |
| Enum name | `ArtifactKindEnum` → `MemoryKindEnum` (values unchanged: `USER` / `RULE` / `ARTICLE` / `NOTE` / `CANON`) |
| Frontmatter field | `artifact_kind:` → `memory_kind:` |
| File name | `co_cli/memory/artifact.py` → `co_cli/memory/item.py` |
| Function renames | `save_artifact`/`mutate_artifact` → `save_memory_item`/`mutate_memory_item`; `load_artifact(s)` → `load_memory_item(s)`; `filter_artifacts` → `filter_memory_items`; `format_artifact_row` → `format_memory_item_row`; `artifact_to_frontmatter` → `memory_item_to_frontmatter`; `render_artifact_file` → `render_memory_item_file`; `find_similar_artifacts` → `find_similar_memory_items` |
| Method renames on `MemoryStore` | `list_artifacts` → `list_memory_items`; `search_artifacts` → `search_memory_items` |
| Constants | `_ARTIFACTS_USER_CAP` → `_ITEMS_USER_CAP`; `_ARTIFACTS_WATERFALL_CHUNK_CAP` → `_ITEMS_WATERFALL_CHUNK_CAP`; `_ARTIFACTS_WATERFALL_SIZE_CAP` → `_ITEMS_WATERFALL_SIZE_CAP` |
| Live-data migration | One-shot startup scan script; rewrites every `~/.co-cli/memory/*.md` frontmatter — deletes script after one release |
| Eval-fixture migration | Same script logic, applied at plan time to `evals/_fixtures/*/memory/*.md` (path post-upstream rename) |
| Phasing | Single atomic PR after the upstream rename ships |

**Out of scope (do not do):**
- Reopening the `kind:` (domain) field — that's the upstream plan's job
- Touching `canon` operational segregation (canon is still indexed for personality auto-injection; never returned by model-callable tools — same as today)
- Touching `IndexSourceEnum` (memory / obsidian / drive) — that's source-domain, orthogonal to kind
- Touching `SourceTypeEnum` (detected / web_fetch / manual / obsidian / drive / consolidated) — unrelated
- Adding new kinds or removing existing ones
- Changing chunking, search backend, or DB schema

## Tasks

### Task 1 — Rename dataclass and enum in `co_cli/memory/item.py` (renamed from `artifact.py`)

**Files:**
- `co_cli/memory/artifact.py` → `co_cli/memory/item.py` (`git mv`)

**Changes inside `item.py`:**
- Module docstring: replace "MemoryArtifact is the single reusable-artifact model" with "MemoryItem is the single reusable memory-item model"
- `class ArtifactKindEnum(StrEnum)` → `class MemoryKindEnum(StrEnum)` (values unchanged)
- `@dataclass class MemoryArtifact` → `@dataclass class MemoryItem`
- Field `artifact_kind: str` → `memory_kind: str`
- `def _coerce_fields(...) -> MemoryArtifact` → returns `MemoryItem`; reads `frontmatter["memory_kind"]` (no fallback — migration script already ran)
- `def load_artifact(path) -> MemoryArtifact` → `def load_memory_item(path) -> MemoryItem`
- `def load_artifacts(memory_dir, artifact_kinds=...) -> list[MemoryArtifact]` → `def load_memory_items(memory_dir, memory_kinds=...) -> list[MemoryItem]`
- `def filter_artifacts(entries, filters)` → `def filter_memory_items(entries, filters)`
- `def format_artifact_row(m: MemoryArtifact)` → `def format_memory_item_row(m: MemoryItem)`; body uses `m.memory_kind`
- Default kind in `_coerce_fields`: `MemoryKindEnum.NOTE.value`

### Task 2 — Update frontmatter render/parse in `co_cli/memory/frontmatter.py`

**Files:**
- `co_cli/memory/frontmatter.py`

**Changes:**
- Import `MemoryItem` (not `MemoryArtifact`)
- `def artifact_to_frontmatter(artifact: MemoryArtifact)` → `def memory_item_to_frontmatter(item: MemoryItem)`
- Frontmatter key `"artifact_kind": artifact.artifact_kind` → `"memory_kind": item.memory_kind`
- `def render_artifact_file(artifact: MemoryArtifact)` → `def render_memory_item_file(item: MemoryItem)`
- Docstring: replace "MemoryArtifact" references with "MemoryItem"

### Task 3 — Rename write surface in `co_cli/memory/service.py` and config field

**Files:**
- `co_cli/memory/service.py`
- `co_cli/config/memory.py`

**Changes:**
- Module docstring: "Provides save_memory_item and mutate_memory_item as the canonical write path"
- Imports: `MemoryArtifact, ArtifactKindEnum, load_artifact, load_artifacts, artifact_to_frontmatter, render_artifact_file` → `MemoryItem, MemoryKindEnum, load_memory_item, load_memory_items, memory_item_to_frontmatter, render_memory_item_file`
- `class SaveArtifactResult` → `class SaveMemoryItemResult` (field `artifact_id` → `memory_item_id`)
- `class MutateArtifactResult` → `class MutateMemoryItemResult` (same field rename)
- `def save_artifact(deps, *, artifact_kind, ...)` → `def save_memory_item(deps, *, memory_kind, ...)`
- `def mutate_artifact(...)` → `def mutate_memory_item(...)`
- Inline frontmatter reads: `frontmatter.get("artifact_kind", ...)` → `frontmatter.get("memory_kind", ...)`
- All local var `artifact` → `item`; `artifact_kind` → `memory_kind`; constructor calls `MemoryArtifact(...)` → `MemoryItem(...)`
- `find_similar_artifacts(...)` call → `find_similar_memory_items(...)`
- Error messages: "mutate_artifact action=..." → "mutate_memory_item action=..."

**Changes in `co_cli/config/memory.py`:**
- Field `max_artifact_count: int` → `max_item_count: int`
- Env-var mapping key `"max_artifact_count": "CO_MEMORY_MAX_ARTIFACT_COUNT"` → `"max_item_count": "CO_MEMORY_MAX_ITEM_COUNT"`
- Update any reference to this field in callers (grep `max_artifact_count` — verify no other call sites)

### Task 4 — Rename similarity and decay helpers

**Files:**
- `co_cli/memory/similarity.py`
- `co_cli/memory/decay.py`
- `co_cli/memory/archive.py`
- `co_cli/memory/dream.py`

**Changes:**
- `similarity.py`: `def find_similar_artifacts(content, artifact_kind, artifacts, ...)` → `def find_similar_memory_items(content, memory_kind, items, ...)`; type annotations `MemoryArtifact` → `MemoryItem`; comparison `a.artifact_kind == artifact_kind` → `a.memory_kind == memory_kind`
- `decay.py`: imports + signatures; local `artifacts` → `items`
- `archive.py`: import + parameter rename
- `dream.py`: import + call sites for `load_memory_items`

### Task 5 — Rename `MemoryStore` methods and `IndexStore` boundary

**Files:**
- `co_cli/memory/store.py`
- `co_cli/index/store.py`

**Changes in `memory/store.py`:**
- Import `MemoryKindEnum, IndexSourceEnum` (not `ArtifactKindEnum`)
- `sync_dir`: `frontmatter.get("artifact_kind") or frontmatter.get("kind")` → `frontmatter.get("memory_kind")` (single field; migration ran)
- `reindex_one`: `frontmatter.get("artifact_kind", ArtifactKindEnum.NOTE.value)` → `frontmatter.get("memory_kind", MemoryKindEnum.NOTE.value)` (separate code path — called after writes; must be updated alongside `sync_dir`)
- Default `MemoryKindEnum.NOTE.value`
- `def list_artifacts(kinds, limit)` → `def list_memory_items(kinds, limit)`
- `def search_artifacts(query, kinds, limit)` → `def search_memory_items(query, kinds, limit)`

**Changes in `index/store.py`:**
- The `list_artifacts(source, kinds, limit)` SQL helper that `MemoryStore` calls — rename to `list_items(source, kinds, limit)` (the parameter `kinds` already operates on the `kind` column; method body unchanged)
- Note: `IndexStore` has no `search_artifacts` — `MemoryStore.search_artifacts` delegates to `self._index.search(...)` directly. No rename needed in `index/store.py` for search.
- Drop the `"channel": "artifacts"` key from return-shape dicts if it survives (already noted as a bug-fix-on-rewrite in the upstream plan; verify it's gone, otherwise fix here)

### Task 6 — Rename tool layer

**Files:**
- `co_cli/tools/memory/recall.py`
- `co_cli/tools/memory/view.py`
- `co_cli/tools/memory/manage.py`
- `co_cli/commands/memory.py`
- `co_cli/bootstrap/core.py` (only if it calls renamed surfaces — verify)

**Changes in `recall.py`:**
- Imports: `MemoryItem, load_memory_items`
- `_list_artifacts` → `_list_memory_items`
- `_search_artifacts` → `_search_memory_items`
- `ctx.deps.memory_store.list_artifacts(...)` → `ctx.deps.memory_store.list_memory_items(...)`
- `ctx.deps.memory_store.search_artifacts(...)` → `ctx.deps.memory_store.search_memory_items(...)`
- Caps: `_ARTIFACTS_USER_CAP` → `_ITEMS_USER_CAP` (and the two waterfall caps)
- Result-dict key `kind=a.artifact_kind` → `kind=a.memory_kind`
- Parameter `artifact_kinds=kinds` (to `load_memory_items`) → `memory_kinds=kinds`

**Changes in `view.py`:**
- Import `MemoryKindEnum` (not `ArtifactKindEnum`)
- `frontmatter.get("artifact_kind", ArtifactKindEnum.NOTE.value)` → `frontmatter.get("memory_kind", MemoryKindEnum.NOTE.value)`

**Changes in `manage.py`:**
- Call sites: `save_artifact` → `save_memory_item`; `mutate_artifact` → `mutate_memory_item`
- Parameter passed in tool args: if the tool's input schema has `artifact_kind`, rename to `memory_kind` (verify tool schema and rules text)

**Changes in `commands/memory.py`:**
- Import: `filter_memory_items, format_memory_item_row, load_memory_items`
- Call sites updated

### Task 7 — One-shot migration script: `artifact_kind` → `memory_kind` in `~/.co-cli/memory/*.md`

**New file:** `co_cli/memory/_migrate_artifact_kind.py`

**Behavior:**
- Invoked once during bootstrap (`co_cli/bootstrap/core.py` — earliest point after `memory_dir` is resolved, before `MemoryStore.sync_dir()` reads files)
- Walks `memory_dir` for all `*.md` files (skip `_archive/`, skip `_dream_state.json`)
- For each file: parses frontmatter with existing `parse_frontmatter`
- If `artifact_kind` key is present:
  1. Set `memory_kind = frontmatter.pop("artifact_kind")`
  2. Rewrite the file via `atomic_write_text` (`co_cli/persistence/atomic.py`) with the renamed frontmatter
  3. Log at INFO: `migrated frontmatter: {path.name} (artifact_kind → memory_kind)`
- If `artifact_kind` is absent (already migrated): no-op, no log
- Idempotent — second invocation does nothing
- Returns count migrated; bootstrap logs the count to the startup banner observability span

**Bootstrap wiring (`co_cli/bootstrap/core.py`):**
- After `memory_dir` is known and exists, before `MemoryStore.sync_dir(memory_dir)`, call `migrate_artifact_kind_frontmatter(memory_dir)`
- Place behind a one-line guard so it only runs when there's a chance of work — e.g., quick check if any file with `artifact_kind` exists is overkill; just walk and no-op on absent key
- Failure mode: if a single file fails to rewrite (e.g., parse error), log WARNING and continue — do not abort startup. The file stays on `artifact_kind`; downstream code already does not handle that key (per Task 1), so subsequent recall on that file will surface a kind=`note` default — acceptable degradation for a malformed file

**Tests for the migration script (in `tests/`):**
- `tests/test_flow_memory_migrate_artifact_kind.py` (new) — covers:
  - Migrates file with `artifact_kind: rule` → frontmatter has `memory_kind: rule`, no `artifact_kind`
  - Idempotent — running twice produces no changes after the first
  - Preserves all other frontmatter fields (id, title, created, source_type, etc.)
  - Preserves body bytes exactly
  - Skips files without `artifact_kind` (already migrated)
  - Skips `_archive/` directory
  - Handles malformed frontmatter without aborting (logs warning, leaves file untouched)

**Removal plan:** after one release ships and per project rules (zero-backward-compat), the migration script and its bootstrap call are deleted in the next release. Cut a follow-up plan: `2026-XX-XX-remove-artifact-kind-migration.md` (one task: delete the file + the bootstrap call + the test).

### Task 8 — Migrate eval fixtures at plan time

**Note:** upstream did not rename the fixture subdirectory; fixtures still live under `evals/_fixtures/*/knowledge/`. This task does *two* things: rename the subdir AND rewrite the frontmatter field.

**Subdir renames (`git mv`):**
- `evals/_fixtures/groundedness_baseline/knowledge/` → `evals/_fixtures/groundedness_baseline/memory/`
- `evals/_fixtures/multistep_research_baseline/knowledge/` → `evals/_fixtures/multistep_research_baseline/memory/`
- `evals/_fixtures/user_model_baseline/knowledge/` → `evals/_fixtures/user_model_baseline/memory/`

**Frontmatter rewrite (in each `.md` under the renamed dirs):**
- `evals/_fixtures/groundedness_baseline/memory/eval_B1_known_fact.md`
- `evals/_fixtures/multistep_research_baseline/memory/decision_use_sqlite.md`
- `evals/_fixtures/multistep_research_baseline/memory/project_helios_context.md`
- `evals/_fixtures/user_model_baseline/memory/pref_pst.md`
- `evals/_fixtures/user_model_baseline/memory/pref_python.md`
- `evals/_fixtures/user_model_baseline/memory/pref_terse.md`

In every fixture: frontmatter `artifact_kind: <kind>` → `memory_kind: <kind>`. Done by hand or by running the migration script against the fixtures dir once — committed result. No `artifact_kind` should remain anywhere in `evals/`.

**Eval code that references the old subdir path:** grep `evals/` for the literal string `"knowledge"` (subdir name in path joins) and update to `"memory"`. Check `evals/eval_memory.py`, `evals/_fixtures.py`, `evals/eval_daily_chat.py` at minimum.

### Task 9 — Update tests

**Renames:**
- `tests/test_flow_artifact_manage.py` → `tests/test_flow_memory_item_manage.py`

**Files to update (substitute `artifact` → `memory_item` / `MemoryArtifact` → `MemoryItem` / `artifact_kind` → `memory_kind` / `ArtifactKindEnum` → `MemoryKindEnum`):**
- `tests/test_flow_memory_item_manage.py` (renamed above)
- `tests/test_flow_memory_write.py`
- `tests/test_flow_memory_store.py`
- `tests/test_flow_memory_canon_recall.py`
- `tests/test_flow_memory_artifacts_waterfall_cap.py` → also rename file to `test_flow_memory_items_waterfall_cap.py`
- Any test asserting frontmatter keys must use `memory_kind`
- Any test constructing `MemoryArtifact(...)` directly must construct `MemoryItem(...)`

### Task 10 — Update specs

**Files:**
- `docs/specs/memory.md`
- `docs/specs/dream.md`
- `docs/specs/observability.md`
- `docs/specs/knowledge.md` (already slated for DELETE in upstream plan — confirm it's gone)

**Changes:**
- Section "Functional Architecture": diagram labels using "artifact" → "memory item"
- Tables: `_ARTIFACTS_USER_CAP=3` → `_ITEMS_USER_CAP=3` etc.
- Result-field row for `memory_search`: still `{kind, title, snippet, ...}` (the `kind` key in results stays — it's the public field name, distinct from the storage column name internally; verify in `recall.py` Task 6)
- Frontmatter spec: `artifact_kind:` → `memory_kind:`
- Public-interface table entries: `load_artifact`, `load_artifacts`, `filter_artifacts`, `MemoryArtifact`, `ArtifactKindEnum` — replaced with renamed forms
- Update the data model section to reflect the simplification: "MemoryItem is the single reusable memory-item model — user / rule / article / note / canon items share this schema, differentiated by the `memory_kind` field"

### Task 11 — Update agent rule files and skill prompts

**Files:**
- `co_cli/context/rules/04_tool_protocol.md`
- `co_cli/skills/triage.md`
- `co_cli/skills/session_review_prompts.py` — prose uses "knowledge artifact names" (lines 32–33) and "artifacts for …" (line 16); update to "memory item names" / "memory items"
- `co_cli/commands/core.py` — CLI help string "Manage memory artifacts" → "Manage memory items"
- Any other agent-facing prompt or rule that mentions `artifact_kind` parameter name (already getting a separate `knowledge_* → memory_*` pass in upstream plan — verify after that lands)

**Changes:**
- The tool-input parameter name as documented to the agent: `artifact_kind` → `memory_kind`
- The narrative text around what to save: "remember this note as a rule" wording stays unchanged (it talks about the kind value, not the field name)

### Task 12 — Update CLAUDE.md and agent_docs

**Files:**
- `/Users/binle/workspace_genai/co-cli/CLAUDE.md` (project root)
- `agent_docs/code-conventions.md`
- `agent_docs/tools.md`

**Changes:**
- Memory system section: replace any "artifact" references with "memory item"
- Tool signatures: input schema for `memory_manage` uses `memory_kind`

## Verification

### Build / lint
```bash
scripts/quality-gate.sh lint --fix
```

### Tests (incremental, fail-fast)
```bash
mkdir -p .pytest-logs
uv run pytest -x tests/test_flow_memory_migrate_artifact_kind.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-migrate.log
uv run pytest -x tests/test_flow_memory_item_manage.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-item-manage.log
uv run pytest -x tests/test_flow_memory_write.py tests/test_flow_memory_store.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-memory.log
uv run pytest -x tests/test_flow_memory_canon_recall.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-canon.log
uv run pytest -x tests/test_flow_memory_items_waterfall_cap.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-waterfall.log
```

### Full quality gate
```bash
scripts/quality-gate.sh full
```

### Migration smoke (manual)
1. **Pre-state assertion**: confirm `~/.co-cli/memory/*.md` files exist and have `artifact_kind:` in frontmatter. If the user has not yet moved `~/.co-cli/knowledge/` → `~/.co-cli/memory/` (per upstream's manual-move policy), prompt them to do so first or skip this smoke and verify behavior via a fixture-populated temp dir.
2. Start REPL: `uv run co chat`
3. Verify bootstrap log contains: `migrated frontmatter: <N> files (artifact_kind → memory_kind)`
4. Inspect 2–3 files manually: `grep -E "^(artifact_kind|memory_kind):" ~/.co-cli/memory/*.md | head` — every line should be `memory_kind:`, no `artifact_kind:` remains
5. Second startup: `uv run co chat` again — migration log line should report 0 migrated (idempotency)
6. In REPL: ask agent to recall a known fact — verify `memory_search` returns the right item with `kind: <subtype>` in the result
7. In REPL: ask agent to save a new note — verify `memory_manage(action='create', memory_kind='note', ...)` is called with approval prompt; verify written file has `memory_kind: note` (no `artifact_kind:`)
8. Decay / dream cycle smoke: run `uv run co dream` (or whatever invokes the dream loop) — verify no crashes on the renamed surface

### Eval smoke
```bash
uv run python evals/eval_memory.py
uv run python evals/eval_daily_chat.py
```

Rubric pass rates should not move. If degraded, root-cause then fix — do not paper over.

## Out of scope (do not do)

- Reopening the `kind:` (domain) field — that's the upstream plan's responsibility
- Backward-compat fallback for `artifact_kind` reads in production code paths (only the migration script reads it, and only to delete it)
- Migration of the `_archive/` directory contents — archived files are intentionally invisible to recall (`co_cli/memory/archive.py:6`); leaving `artifact_kind:` in archived files is harmless. The migration script explicitly skips `_archive/`
- Touching `SourceTypeEnum` or `IndexSourceEnum`
- Adding new memory kinds, removing existing ones, or changing the canon segregation
- Changing approval-subject strings, search backends, or chunking constants
- Auto-deleting the migration script — the deletion is a separate follow-up plan after one release ships

## Critical files for execution reference

| Purpose | Path |
|---|---|
| Dataclass + enum (rename + file move) | `co_cli/memory/artifact.py` → `co_cli/memory/item.py` |
| Frontmatter render/parse | `co_cli/memory/frontmatter.py` |
| Write surface + config field | `co_cli/memory/service.py`, `co_cli/config/memory.py` |
| Domain store methods | `co_cli/memory/store.py` |
| Index-store SQL methods | `co_cli/index/store.py` |
| Similarity / decay / archive / dream | `co_cli/memory/{similarity,decay,archive,dream}.py` |
| Tool layer | `co_cli/tools/memory/{recall,view,manage}.py` |
| CLI command | `co_cli/commands/memory.py` |
| Bootstrap (migration call site) | `co_cli/bootstrap/core.py` |
| New migration module | `co_cli/memory/_migrate_artifact_kind.py` |
| New migration test | `tests/test_flow_memory_migrate_artifact_kind.py` |
| Specs | `docs/specs/{memory,dream,observability}.md` |
| Agent rules / skills | `co_cli/context/rules/04_tool_protocol.md`, `co_cli/skills/triage.md` |
| Project CLAUDE.md | `/Users/binle/workspace_genai/co-cli/CLAUDE.md` |
| Eval fixtures | `evals/_fixtures/*/memory/*.md` |
