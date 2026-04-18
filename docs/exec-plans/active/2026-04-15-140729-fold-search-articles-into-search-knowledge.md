# Plan: Fold `search_articles` Into `search_knowledge`

**Task type: refactor** — reduce public tool-surface duplication by removing `search_articles` as a registered tool and folding its article-index workflow into `search_knowledge` without regressing the current `search -> read_article` flow.

---

## Context

`search_knowledge` is already the unified knowledge-search entry point and supports `source="library"` plus `kind="article"` filtering [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:122). At the same time, `search_articles` remains a separate always-visible native tool that performs a narrower library-only article search and returns an article-oriented summary schema with `article_id`, `origin_url`, `tags`, `snippet`, and `slug` [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:475).

That split creates public-surface duplication:

- the model sees both `search_knowledge` and `search_articles` for article discovery
- docs/specs describe both paths
- tests cover both paths
- `read_article` still instructs the model to call `search_articles` first [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:531)

The overlap is real, but the tools are not yet fully interchangeable. Default `search_knowledge` results are generic cross-source retrieval results and do not guarantee the article-specific fields needed to continue into `read_article`, especially `slug` [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:148).

The current native toolset still registers both tools as always-visible [co_cli/agent/_native_toolset.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_native_toolset.py).

No active exec-plan covers this exact surface fold.

---

## Problem & Outcome

**Problem:** `search_articles` is a redundant top-level tool from a surface-area perspective, but it still carries workflow-specific output that `search_knowledge` does not explicitly preserve.

**Failure cost:** Removing `search_articles` too early would degrade the article retrieval flow. The agent could still find articles through `search_knowledge`, but it would lose the structured article index shape that currently feeds `read_article`. The most likely regression is loss of `slug`, which would make the documented `search -> read_article` path worse even while the tool count goes down.

**Outcome:** `search_knowledge` becomes the sole public search tool for library article discovery by adding an explicit article-index mode. `search_articles` is removed from the registered native tool surface only after `search_knowledge` can emit the same article-oriented continuation data needed by `read_article`.

---

## Scope

**In scope:**
- Add an explicit article-index mode to `search_knowledge`
- Reuse the existing article-only FTS and grep helpers as the implementation path
- Update `read_article` guidance to reference the new `search_knowledge` flow
- Remove `search_articles` from native tool registration
- Update tests, specs, and research/tool-surface docs that describe `search_articles` as a public tool

**Out of scope:**
- Changing article ranking behavior
- Changing `read_article` to accept anything other than `slug`
- Folding `read_article` into `search_knowledge`
- Broad retrieval redesign across memories, Obsidian, and Drive
- Prompt or agent strategy changes unrelated to the tool contract

---

## Behavioral Constraints

- Default `search_knowledge(...)` behavior must remain unchanged for existing generic cross-source callers.
- `source="memory"` rejection behavior in `search_knowledge` must remain unchanged.
- The new article-index mode must preserve the fields required by the current article lookup flow:
  - `article_id`
  - `title`
  - `origin_url`
  - `tags`
  - `snippet`
  - `slug`
- FTS and grep fallback paths must emit the same article-index schema.
- `read_article` must still work from a slug obtained via the new `search_knowledge` article-index path.
- `search_articles` must be absent from the registered native tool surface after the refactor is complete.

---

## Concerns

### Primary regression concern

The dangerous version of this change is “delete `search_articles`, tell users to use `search_knowledge(source="library", kind="article")`, and stop there.” That reduces tool count but silently downgrades the article retrieval workflow because the generic search result shape is not designed as a direct article index.

### Result-shape concern

`search_knowledge` currently returns generic `{source, kind, title, snippet, score, path}`-style data [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:148). `search_articles` returns article-specific continuation data, including `slug` and `origin_url` [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:494). If the fold does not preserve that distinction explicitly, `read_article` becomes harder to use and tests will likely drift toward brittle filename/path inference.

### Scope-creep concern

This is a tool-surface unification task, not a retrieval-quality task. Ranking, source mixing, contradiction logic, and article-body loading should not change in the same pass. Keep the implementation narrow.

### Docs drift concern

`search_articles` is referenced in specs, research docs, reports, and tests. If code lands first and docs are not swept, the repository will immediately become inconsistent.

---

## High-Level Design

### Public contract after this change

`search_knowledge` remains the unified tool and gains an explicit article-index output mode. The intended call path becomes:

```python
search_knowledge(
    query="asyncio",
    source="library",
    kind="article",
    result_mode="article_index",
)
```

That mode returns the current article-search continuation shape so the model can immediately follow with:

```python
read_article(slug="...")
```

### Why explicit mode instead of silent shape switching

A silent shape change based only on `source="library"` and `kind="article"` would make `search_knowledge` behavior harder to reason about and would risk breaking generic callers that expect the current cross-source-style schema. An explicit `result_mode` keeps the fold local and legible.

### Implementation strategy

Do not reimplement article search. Dispatch `search_knowledge` into the existing `_fts_search_articles()` and `_grep_search_articles()` helper paths when the request is specifically for library articles in article-index mode [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:342), [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py:412).

This keeps:

- current article search ranking
- current fallback behavior
- current article result formatting

while shrinking the public tool surface.

---

## Implementation Plan

### TASK-1 — Add article-index mode to `search_knowledge`

```text
files:
  - co_cli/tools/articles.py

done_when: >
  search_knowledge(..., source="library", kind="article", result_mode="article_index")
  returns the article summary schema currently needed by read_article:
  article_id, title, origin_url, tags, snippet, slug.
  AND default search_knowledge behavior is unchanged when result_mode is omitted.

success_signal: N/A

prerequisites: []
```

**Implementation notes:**
- Add a parameter such as `result_mode: Literal["default", "article_index"] = "default"`.
- Fast-path `search_knowledge` into `_fts_search_articles()` / `_grep_search_articles()` when:
  - `source == "library"`
  - `kind == "article"`
  - `result_mode == "article_index"`
- Preserve current generic `search_knowledge` behavior otherwise.

---

### TASK-2 — Repoint `read_article` guidance and internal docs in code

```text
files:
  - co_cli/tools/articles.py

done_when: >
  read_article no longer instructs the model to call search_articles first.
  It instead points to search_knowledge(..., source="library", kind="article",
  result_mode="article_index").

success_signal: N/A

prerequisites: [TASK-1]
```

**Implementation notes:**
- Update the `read_article` docstring examples and guidance.
- Update the `search_knowledge` docstring to document the article-index mode explicitly.
- Remove stale intra-module guidance that treats `search_articles` as the public discovery entry point.

---

### TASK-3 — Remove `search_articles` from native registration

```text
files:
  - co_cli/agent/_native_toolset.py
  - co_cli/context/tool_display.py

done_when: >
  search_articles is no longer registered in the native toolset.
  AND any display metadata keyed to search_articles is removed or folded
  onto search_knowledge behavior.

success_signal: N/A

prerequisites: [TASK-1, TASK-2]
```

**Implementation notes:**
- Remove `search_articles` from imports and registration in `_build_native_toolset()`.
- If a temporary compatibility wrapper is kept in `co_cli/tools/articles.py`, it must be unregistered and clearly marked transitional.
- Prefer deletion over long-lived compatibility once tests and docs are migrated.

---

### TASK-4 — Rewrite tests around the new public contract

```text
files:
  - tests/test_articles.py

done_when: >
  All article-search assertions run through search_knowledge in article-index mode,
  not search_articles.
  AND read_article still succeeds from a slug returned by search_knowledge.
  AND a guard test proves default search_knowledge output remains unchanged.

success_signal: >
  uv run pytest tests/test_articles.py -x passes.

prerequisites: [TASK-1, TASK-2, TASK-3]
```

**Implementation notes:**
- Replace `search_articles` tests with:
  - article-index FTS path assertions
  - article-index grep fallback assertions
  - no-match assertions through `search_knowledge`
- Keep `read_article` tests, but derive the slug from the new article-index result path.
- Add one targeted regression test for default `search_knowledge` behavior with no `result_mode`.

---

### TASK-5 — Sync specs and tool-surface docs

```text
files:
  - docs/specs/tools.md
  - docs/specs/cognition.md
  - docs/reference/RESEARCH-peer-tool-surface-survey.md
  - other docs returned by repo-wide grep for search_articles

done_when: >
  No active spec or tool-surface doc describes search_articles as a public tool.
  AND the new search_knowledge article-index mode is documented where the
  article retrieval flow is described.

success_signal: N/A

prerequisites: [TASK-3, TASK-4]
```

**Implementation notes:**
- Update tool catalogs and architecture diagrams that currently enumerate both tools.
- Update any “search_articles then read_article” flow descriptions.
- Keep the docs explicit that `read_article` still exists as a separate full-body read step.

---

## Testing

Focused dev test:

```bash
uv run pytest tests/test_articles.py -x
```

Full regression gate before shipping:

```bash
mkdir -p .pytest-logs
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```

Targeted verification points:

- article-index mode returns `slug`
- grep fallback and FTS return the same article-index schema
- `read_article` works from the returned slug
- default cross-source `search_knowledge` remains unchanged
- native tool inventory no longer includes `search_articles`

---

## Open Questions

1. Should `search_articles` remain in the module temporarily as an unregistered compatibility wrapper for one release cycle, or should it be deleted immediately once call sites/tests/docs are migrated?
2. Is `result_mode="article_index"` the preferred name, or does the repo want a more general name such as `output_mode` for future expansion?

The implementation can proceed without blocking on either question, but the first affects deletion timing and the second affects API naming cleanliness.

---

# Audit Log

