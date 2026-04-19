# Plan: Remove `search_articles` Surface Debt

**Task type:** `refactor` (behavior-preserving for generic knowledge search; public tool-surface cleanup for article discovery)

## Context

**Current state (verified against the live codebase):**

- `co_cli/tools/knowledge.py:491` — `search_knowledge()` is the universal reusable-recall tool. It searches `source="knowledge"`, `obsidian`, and `drive`, and returns the generic ranked schema `{source, kind, title, snippet, score, path}`.
- `co_cli/tools/knowledge.py:786` — `search_articles()` is still a separate ALWAYS-visible native tool. It searches only article artifacts and returns the article-specific continuation schema `{article_id, title, origin_url, tags, snippet, slug}`.
- `co_cli/tools/knowledge.py:843` — `read_article()` still instructs the model to call `search_articles()` first and use the returned `slug`.
- `co_cli/agent/_native_toolset.py:24` and `:52` — `search_articles` is still imported and registered in the native toolset as an ALWAYS-visible read tool alongside `search_knowledge`.
- `co_cli/context/tool_display.py:25` — display metadata still contains a `search_articles -> query` entry.
- `tests/test_articles.py:15` and `:118` — article-search tests still import and assert directly against `search_articles()`.

**Architectural mismatch:**

Articles are already part of the unified knowledge layer, not a separate layer. `ArtifactKindEnum` in `co_cli/knowledge/_artifact.py:23` defines `article` as one `artifact_kind` alongside `preference`, `decision`, `rule`, `feedback`, `reference`, and `note`. The separate `search_articles()` public tool is therefore surface debt, not a principled boundary.

**Why the old plan is not reusable:**

- It targets `co_cli/tools/articles.py`, but the live code moved to `co_cli/tools/knowledge.py`.
- It assumes `source="library"`; the live code uses `source="knowledge"`.
- It encodes stale behavior constraints such as preserving `source="memory"` rejection in `search_knowledge()`, but the live implementation no longer has that contract.
- It includes `docs/specs/*` paths in task file lists, which violates repo policy for exec-plans.

## Problem & Outcome

**Problem:** `search_articles()` duplicates the public knowledge-search surface and teaches the model that article discovery is a separate top-level concept, even though articles are already unified under the knowledge layer. At the same time, `read_article()` still depends on an article-index style result shape that generic `search_knowledge()` does not currently return.

**Failure cost:** Deleting `search_articles()` without replacing its continuation payload in `search_knowledge()` would regress the `search -> read_article(slug)` workflow. Keeping both tools preserves historical behavior but maintains unnecessary surface area and prompt ambiguity.

**Outcome:** `search_knowledge()` becomes the sole public search entry point for knowledge artifacts, including article discovery. It gains an explicit article-index mode for `kind="article"` requests that need continuation-friendly fields. `search_articles()` is removed from the registered tool surface once the new `search_knowledge()` contract is in place and covered by tests.

## Scope

**In scope:**

1. Add a narrow, explicit article-index result mode to `search_knowledge()`.
2. Reuse the existing article-only FTS and grep helper paths; do not reimplement ranking.
3. Repoint `read_article()` guidance to the new `search_knowledge(..., kind="article", ...)` flow.
4. Remove `search_articles()` from the native toolset and display metadata.
5. Rewrite article-search tests around the new public contract.

**Out of scope:**

- Retrieval-quality redesign across knowledge, memory, Obsidian, or Drive
- Changing `read_article()` to accept anything other than `slug`
- Folding article-body reads into `search_knowledge()`
- Adding new knowledge artifact kinds
- Spec updates as explicit plan tasks; `sync-doc` is an output of delivery, not an input task

## Behavioral Constraints

1. Default `search_knowledge()` behavior must remain unchanged when the new article-index mode is not requested.
2. The article-index path must preserve the continuation fields currently relied on by `read_article()`: `article_id`, `title`, `origin_url`, `tags`, `snippet`, `slug`.
3. Both FTS and grep fallback paths must emit the same article-index schema.
4. `read_article()` must continue to work from a `slug` returned by the new `search_knowledge()` article-index path.
5. `search_articles()` must no longer be registered in the native toolset after the cleanup is complete.
6. The cleanup must not introduce a silent shape switch for ordinary `search_knowledge()` callers; article-index behavior must be explicit in the API.

## High-Level Design

### Public contract after cleanup

`search_knowledge()` remains the single reusable-recall tool. When `kind="article"` is passed, it implicitly returns the article-index continuation schema:

```python
search_knowledge(
    query="asyncio",
    kind="article",
    source="knowledge",
)
```

That call returns `{article_id, title, origin_url, tags, snippet, slug}` so the model can follow with:

```python
read_article(slug="python-asyncio-guide-ab12cd")
```

### Why implicit shape switching on `kind="article"`

No existing `search_knowledge()` caller passes `kind="article"` — that path was exclusively served by `search_articles()`. An explicit `result_mode` parameter would add permanent API surface debt for no benefit. Instead, `search_knowledge()` implicitly returns the article-index continuation schema when `kind="article"` is requested. All other `kind` values continue to return the generic schema unchanged.

### Implementation strategy

Do not reimplement article search. Reuse `_fts_search_articles()` and `_grep_search_articles()` inside `co_cli/tools/knowledge.py` as the implementation path for the article kind. Keep the current ranking and fallback behavior intact; only move the public entry point.

## Implementation Plan

### TASK-1 — Add article-index mode to `search_knowledge()`

```text
files:
  - co_cli/tools/knowledge.py

done_when: >
  search_knowledge(..., kind="article", source="knowledge") returns the article-index schema:
  article_id, title, origin_url, tags, snippet, slug.
  AND search_knowledge with any other kind continues to return the generic schema.

success_signal: N/A

prerequisites: []
```

**Implementation notes:**

- Fast-path into `_fts_search_articles()` / `_grep_search_articles()` when `kind == "article"` and `source == "knowledge"`, returning the article-index continuation schema.
- Keep current generic post-processing for all other `kind` values — no new parameter added.

### TASK-2 — Repoint article-read guidance to the unified search surface

```text
files:
  - co_cli/tools/knowledge.py

done_when: >
  read_article() no longer instructs the model to call search_articles() first.
  The guidance instead points to
  search_knowledge(..., kind="article", source="knowledge").

success_signal: N/A

prerequisites: [TASK-1]
```

**Implementation notes:**

- Update the `search_knowledge()` docstring to document the new article-index mode.
- Update the `read_article()` docstring and examples to reference the new discovery flow.
- Remove stale intra-module language that treats `search_articles()` as the public article entry point.

### TASK-3 — Remove `search_articles()` from the public native surface

```text
files:
  - co_cli/agent/_native_toolset.py
  - co_cli/context/tool_display.py

done_when: >
  search_articles is no longer imported or registered in the native toolset.
  AND tool display metadata no longer references search_articles.

success_signal: N/A

prerequisites: [TASK-1, TASK-2]
```

**Implementation notes:**

- Remove `search_articles` from `co_cli/tools/knowledge` imports in `_native_toolset.py`.
- Remove `search_articles` from `NATIVE_TOOLS`.
- Remove the `search_articles` display-arg mapping from `co_cli/context/tool_display.py`.
- Keeping the function temporarily in `knowledge.py` is acceptable only as an unregistered compatibility shim during the edit sequence; the desired end state is no public tool-surface exposure.

### TASK-4 — Rewrite tests around the new contract

```text
files:
  - tests/test_articles.py
  - tests/test_agent.py
  - tests/test_tool_prompt_discovery.py

done_when: >
  Article-search assertions go through search_knowledge() article-index mode,
  not search_articles().
  AND read_article() succeeds from a slug returned by search_knowledge().
  AND native-tool registration assertions no longer expect search_articles.

success_signal: >
  uv run pytest tests/test_articles.py tests/test_agent.py tests/test_tool_prompt_discovery.py -x

prerequisites: [TASK-3]
```

**Implementation notes:**

- Replace direct `search_articles()` assertions with `search_knowledge(..., kind="article", source="knowledge")`.
- Cover both FTS and grep article-index paths.
- Add one regression guard proving `search_knowledge()` with a non-article kind returns the generic schema.
- Update any tool-surface expectations that still mention `search_articles`.

### TASK-5 — Remove or quarantine the compatibility wrapper

```text
files:
  - co_cli/tools/knowledge.py

done_when: >
  Either search_articles() is deleted entirely,
  OR it remains only as a clearly transitional, unregistered shim with no
  active references from the native tool surface or tests.

success_signal: >
  rg -n "search_articles" co_cli tests | sed -n '1,120p'

prerequisites: [TASK-4]
```

**Implementation notes:**

- Prefer deletion if no production code or tests still require the symbol.
- If a short-lived compatibility wrapper is kept, mark it transitional and ensure it is not imported into `_native_toolset.py`.

## Testing

Focused dev test:

```bash
mkdir -p .pytest-logs
uv run pytest tests/test_articles.py tests/test_agent.py tests/test_tool_prompt_discovery.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-search-knowledge-surface.log
```

Full regression gate before shipping:

```bash
mkdir -p .pytest-logs
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```

Targeted verification points:

- `search_knowledge(kind="article", source="knowledge")` returns `slug` and article-index fields
- FTS and grep article-index paths emit the same continuation schema
- `read_article()` still works from the returned slug
- `search_knowledge()` with any other kind returns the generic cross-source schema
- the native tool surface no longer exposes `search_articles`

## Delivery Notes

- `sync-doc` should run after implementation to reconcile specs with the new tool surface.
- Any research/reference docs mentioning `search_articles` should be cleaned up during delivery, but they are not tracked here as explicit plan tasks because exec-plans must not treat `docs/specs/*` as implementation inputs.
