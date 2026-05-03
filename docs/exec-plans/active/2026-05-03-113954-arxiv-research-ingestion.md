# Plan: arXiv Research Ingestion

**Task type:** code-feature

## Context

Gap 4 from `docs/reference/RESEARCH-memory-peer-for-co-second-brain.md` — research ingestion pipeline.

`co` already has `article` (`ArtifactKindEnum.ARTICLE`) and `note` artifact kinds, and `memory_create` with `source_url` for URL-keyed dedup and decay protection. The artifact schema in `co_cli/memory/artifact.py` stores `source_ref` (opaque reference field) and `source_type`. The gap is the **ingest path**: there is no `arxiv_search` tool and no workflow overlay for discovery → preview → import. Research must be captured manually via `memory_create`.

Current-state validation:
- `co_cli/memory/artifact.py:24-28` — `ArtifactKindEnum` defines `USER`, `RULE`, `ARTICLE`, `NOTE`. ARTICLE kind is present and functional.
- `co_cli/tools/memory/write.py:28-124` — `memory_create` accepts `artifact_kind`, `title`, `content`, `source_url`, `description`. URL-keyed dedup is live. `source_url` requires `artifact_kind="article"`.
- `co_cli/agent/_native_toolset.py` — `NATIVE_TOOLS` tuple is the registration list; `requires_config` gates optional tools.
- No existing `arxiv_search` or research ingestion code anywhere in `co_cli/tools/`.
- No existing `research-import` or `arxiv` skill in `co_cli/skills/`.
- No stale TODO files for this scope.

arXiv API facts (from upstream docs, no account needed):
- Endpoint: `https://export.arxiv.org/api/query`
- Query param: `search_query=ti:attention+AND+all:transformer`, `max_results=N`
- Returns Atom XML; Python stdlib `xml.etree.ElementTree` parses it — no new dependency.
- No authentication required. Courtesy rate limit: ≤ 3 req/s suggested by arXiv ToS.

## Problem & Outcome

**Problem:** `co` has no native path for discovering and importing research papers into the knowledge corpus. The `article` artifact kind exists but requires fully manual `memory_create` calls with all fields populated by hand.

**Failure cost:** Users who want to use `co` as a second brain for research must either (a) manually compose `memory_create` calls with all frontmatter fields, or (b) not use `co` for research at all. The arXiv discovery use-case is completely unsupported.

**Outcome:** After this plan ships:
1. The agent can call `arxiv_search(query)` to discover papers and return structured metadata (title, authors, abstract, arxiv_id, pdf_url, published date).
2. A `/research-import` skill walks the user through discovery → review → import into the knowledge corpus via `memory_create`, with frontmatter auto-populated from `arxiv_search` output.

## Scope

In scope:
- `arxiv_search` native tool: read-only, always-visible, no API key.
- `research-import` bundled skill: slash-command overlay chaining `arxiv_search` → `memory_create`.

Out of scope:
- Full-text PDF ingestion (abstract-first is sufficient per the research doc).
- `citation_verify` tool (Gap 5 — separate plan).
- arXiv feed watching / recurring sync (deferred per the research doc).
- Any config key or settings additions (arXiv is public, no key needed).

## Behavioral Constraints

1. `arxiv_search` must never call an endpoint other than `https://export.arxiv.org/api/query`. No dynamic URL construction from user input.
2. Empty or blank `query` must raise `ModelRetry` before any HTTP call.
3. `max_results` is silently clamped to 10 without error — over-limit values are not a model error (matches `web_search` behavior).
4. `max_results < 1` must raise `ModelRetry`.
5. `arxiv_search` is `is_read_only=True`, `approval=False`. It must never write any state.
6. The skill must invoke `memory_create` with `artifact_kind="article"` and `source_url` set to the canonical arXiv abstract URL — triggering URL-keyed dedup so re-importing the same paper updates rather than duplicates.
7. On HTTP errors from the arXiv API, return `tool_error()` — do not raise `ModelRetry` for terminal errors (4xx).
8. XML parse errors from arXiv (malformed response) must return `tool_error()` — never propagate as unhandled exceptions.

## High-Level Design

### TASK-1: `arxiv_search` tool

New file `co_cli/tools/web/arxiv.py`. Pattern mirrors `web_search`: `@agent_tool(visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True, retries=2)`, `httpx.AsyncClient`. HTTP retry helpers are extracted to `co_cli/tools/web/_http.py` (new shared package-private module) and imported from there — not from `search.py`. `search.py` is also updated to import from `_http.py` instead of defining the helpers inline.

Return schema per result:
```python
{
    "arxiv_id": str,      # e.g. "2304.12345"
    "title": str,
    "authors": list[str],
    "abstract": str,
    "published": str,     # ISO date "YYYY-MM-DD"
    "arxiv_url": str,     # "https://arxiv.org/abs/{arxiv_id}"
    "pdf_url": str,       # "https://arxiv.org/pdf/{arxiv_id}"
}
```

XML parsing uses `xml.etree.ElementTree` (stdlib, no new dep). Atom namespace prefix: `{http://www.w3.org/2005/Atom}`. arXiv `id` field is a URL like `http://arxiv.org/abs/2304.12345v2`; strip to bare `arxiv_id` by splitting on `/abs/` and dropping the version suffix.

Wire into `NATIVE_TOOLS` in `co_cli/agent/_native_toolset.py`.

### TASK-2: `research-import` skill

New file `co_cli/skills/research_import.md`. Skill body guides the agent to:
1. Call `arxiv_search` with the user's query.
2. Present results as a numbered list (title, first author + year, arxiv_id).
3. Ask the user to pick one (or say "skip").
4. For the chosen paper: call `memory_create(artifact_kind="article", title=..., content=..., source_url=arxiv_url, description=abstract[:180], decay_protected=True)`.
5. Confirm save to the user. Ask: "Import another paper from these results? (1-N, or 'done')". Repeat step 4 for each selection until the user says "done" or "skip".

Content field format: `# {title}\n\n**Authors:** {authors joined}\n**Published:** {published}\n**arXiv:** {arxiv_url}\n\n## Abstract\n\n{abstract}`

## Implementation Plan

### TASK-1 — `arxiv_search` native tool
- **files:**
  - `co_cli/tools/web/_http.py` (new — extract `parse_retry_after`, `compute_backoff_delay`, `classify_web_http_error`, `_http_get_with_retries`, `RETRYABLE_STATUS_CODES`, `TERMINAL_STATUS_CODES`, `WebRetryResult` from `search.py`)
  - `co_cli/tools/web/search.py` (update — replace inline definitions with imports from `_http.py`)
  - `co_cli/tools/web/arxiv.py` (new — `arxiv_search` tool importing HTTP helpers from `_http.py`)
  - `co_cli/agent/_native_toolset.py` (add import + add `arxiv_search` to `NATIVE_TOOLS`)
  - `tests/test_flow_arxiv_search.py` (new)
- **done_when:** `uv run pytest tests/test_flow_arxiv_search.py -x` passes all tests; AND `uv run python -c "from co_cli.agent.core import build_tool_registry; from co_cli.config.core import Settings; r = build_tool_registry(Settings()); assert 'arxiv_search' in r.tool_index, 'arxiv_search not registered'"` exits 0.
- **success_signal:** Agent calls `arxiv_search("attention is all you need")` and returns a list of papers with titles, authors, abstracts, and arXiv URLs.
- **Red-Green-Refactor:** Write test stubs first (empty tool → tests fail), implement → tests pass, clean up.

Tests to include:
1. `test_arxiv_search_returns_results` — real API call; asserts `results` list is non-empty; each result has `arxiv_id`, `title`, `authors`, `abstract`, `arxiv_url`, `pdf_url`, `published`. Wrap with `async with asyncio.timeout(15):`.
2. `test_arxiv_search_empty_query_raises` — calls with `query=""` and asserts `ModelRetry` is raised before any HTTP call. No timeout wrapper needed.
3. `test_arxiv_search_max_results_cap` — calls with `max_results=100`; asserts returned count is ≤ 10. Wrap with `async with asyncio.timeout(15):`.
4. `test_arxiv_search_max_results_zero_raises` — calls with `max_results=0` and asserts `ModelRetry`. No timeout wrapper needed.
5. `test_arxiv_search_http_error_returns_tool_error` — uses `httpx.MockTransport` to inject a 404 response; asserts the tool returns `ToolReturn` with `error=True` rather than raising. No timeout wrapper needed (transport is synchronous).

Guard conditions vs. peer (`web_search`):
- `web_search` raises `ModelRetry` on empty query → `arxiv_search` same.
- `web_search` silently caps at `_MAX_RESULTS = 8` → `arxiv_search` silently clamps to 10 (arXiv returns metadata only, not full page content, so 10 is safe; Brave results carry far heavier context).
- `web_search` has no lower-bound check on `max_results` (always ≥1 from default) → `arxiv_search` adds explicit `max_results < 1` guard (arXiv API would return 0 results silently without it, confusing the model).

### TASK-2 — `research-import` bundled skill
- **files:**
  - `co_cli/skills/research_import.md` (new)
  - `tests/test_flow_research_import_skill.py` (new)
- **prerequisites:** [TASK-1]
- **done_when:** `uv run pytest tests/test_flow_research_import_skill.py -x` passes both tests: (1) `test_research_import_skill_loads` — `load_skills(Path("co_cli/skills"))` returns a dict containing key `"research-import"` with `skill.user_invocable is True`; (2) `test_research_import_triggers_arxiv_search` — real LLM agent-turn test dispatching the skill body with query `"transformer attention"` and asserting that `ToolCallPart(tool_name="arxiv_search")` appears in `turn.messages`.
- **success_signal:** User types `/research-import transformer attention mechanisms` and co presents a numbered paper list, asks which to import, then calls `memory_create` and confirms the save.
- **Red-Green-Refactor:** Write test stubs first (missing file → fail), create skill file → pass.

## Testing

All tests are in `tests/` using pytest + pytest-asyncio. No mocks except `httpx.MockTransport` for the HTTP error path test (the one case where error-path behavior cannot be triggered against the real API without infrastructure). Real arXiv API calls for `test_arxiv_search_returns_results` and `test_arxiv_search_max_results_cap`. Network-dependent tests do not need `skipif` guards (arXiv is reliably public).

Timeout scope per test:
- `test_arxiv_search_returns_results`: wrap body with `async with asyncio.timeout(15):`
- `test_arxiv_search_max_results_cap`: wrap body with `async with asyncio.timeout(15):`
- `test_arxiv_search_empty_query_raises`: no timeout (raises before any network call)
- `test_arxiv_search_max_results_zero_raises`: no timeout (raises before any network call)
- `test_arxiv_search_http_error_returns_tool_error`: no timeout (synchronous MockTransport)
- `test_research_import_skill_loads`: no timeout (filesystem only)
- `test_research_import_triggers_arxiv_search`: wrap body with `async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):`

## Open Questions

None. All implementation details are determinable from arXiv API docs and existing codebase patterns.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev arxiv-research-ingestion`
