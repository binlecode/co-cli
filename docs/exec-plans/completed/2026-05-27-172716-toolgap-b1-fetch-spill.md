# Tool Gap Batch 1 — restore article URL-dedup + remove `tool_output_raw` spill bypass

Task type: code

## Context

Batch 1 of the ROI-ordered tool-parity gaps
(`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md` §5, refreshed 2026-05-27,
co v0.8.260). Two surgical fixes that ship as one tight unit:

- **Remove the `tool_output_raw` spill bypass** (§4.1) — `tool_output_raw`
  builds a tool result with no spill check. It is the one path by which a
  tool-call output reaches context unbounded. The bypass is an abstraction
  leak: an impl-layer helper (`_http_get_with_retries`) constructs a *terminal*
  `ToolReturn`, and the ctx-bearing entrypoint forwards it untouched. The fix
  routes helper errors back through the tool boundary (`tool_error`, which
  spills via `ctx`) and deletes `tool_output_raw`. Correctness/safety.
- **Restore the URL-keyed article-save capability** — co once supported saving
  an article with `source_url` as the dedup key (decay-protected, consolidate
  on re-save). Commit `1df01f2f` (v0.8 "memory surface unification") collapsed
  the pre-unification `knowledge_*` tool surface into `memory_manage` and
  removed the dedicated article entry point, but preserved its plumbing
  (`_find_article_by_url`, the `source_url` branch in `save_memory_item`,
  `SourceTypeEnum.WEB_FETCH`, the dedup tests). The capability was lost
  silently — re-saving the same URL now creates duplicates, fragmenting recall.
  Restore **inside `memory_manage`** by threading `source_url` through its
  create action so the orphaned branch lights up. The pre-unification
  `knowledge_*` namespace does not return; this is purely a single-parameter
  extension on the memory surface. `tags` / caller-supplied `related` are
  **not** in B1 — they would require new `MemoryItem` schema + service-layer
  signature changes, not restoration of orphaned plumbing; see Deferred items.

Sibling batches: `…-toolgap-b2-document-extract.md` (document handling),
`…-toolgap-b3-pty-rolefilter.md` (interactive + recall).

### Why multi-URL `web_fetch` is NOT in B1 (rejected as parity-cosmetic)

The earlier draft of B1 proposed a multi-URL `web_fetch(urls: list[str])` to
mirror hermes's `web_extract` fan-out. On audit, this is **pure API-shape
overhead** with no capability gain:

- `web_fetch` is `is_read_only=True` + `is_concurrent_safe=True`
  (`fetch.py:113-114`).
- `toolset.py:104` wires `sequential = not is_concurrent_safe` into pydantic-ai's
  `ToolDefinition.sequential` (`pydantic_ai/tools.py:636` — *"Whether this tool
  requires a sequential/serial execution environment"*).
- So when the model emits multiple `web_fetch(url=…)` calls in one toolcall
  response, the runtime dispatches them **concurrently**. The latency win the
  `urls: list[str]` design was supposed to deliver is already free at the layer
  above — no `asyncio.gather`, no `_validate_targets`, no `_to_entry`, no
  `_format_multi` needed in `web_fetch` itself.
- Per-URL error isolation: also free — each call is its own `ToolReturn`; one
  failure doesn't affect siblings.
- Bounded fan-out: the model paces tool calls naturally; if a hard cap matters
  it belongs at the orchestration layer (concurrent-call limit), not in
  per-tool argument shape.

Adding `urls: list[str]` would have **complicated** the surface — list
validation, dual return shape, multi-domain approval extension
(`ApprovalSubject.match_values`), batched display formatting, multi-URL
compaction markers — all to duplicate a mechanism the runtime already provides.
Hermes needs its batched shape because its trailing LLM summarization wants
the whole batch in one prompt; co rejects summarization (next subsection), so
the parity argument collapses too. The web_fetch scalar `url: str` signature
stays as-is.

### Why co rejects summarization

Hermes's `web_extract` LLM-summarizes any page >5000 chars
(`process_content_with_llm`, `DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION = 5000`,
output capped at `MAX_OUTPUT_SIZE = 5000`, chunked at 100k via an auxiliary
Gemini Flash model). co does not adopt this. Both tools answer the same
question — a fetched page is bigger than you want in context — but with
opposite strategies:

- **hermes compresses eagerly.** An auxiliary LLM distills any >5000-char page
  to a ≤5000-char summary at fetch time. **Lossy** (the summary is the model's
  interpretation, not the source), **non-deterministic**, and pays an extra
  LLM call per oversized page.
- **co preserves fully.** `spill_if_oversized` writes the *complete* page to a
  content-addressed file and returns a 1500-char preview + a `file_read`
  pointer. **Lossless**, **deterministic**, **no LLM call** in the fetch path,
  and defers relevance selection to the main agent.
- **Doctrine alignment.** co holds "no LLM inside a tool's fetch path"; spill
  already bounds context losslessly, so summarization would trade away
  losslessness, determinism, and cost for eager distillation co does not need.

### Recovered design — agent-mediated article save with URL-keyed dedup

The **capability** existed before the unification: a dedicated article-save
path with URL-keyed dedup, decay protection, and consolidation on re-save.
The pre-unification tool surface that exposed it (in
`co_cli/tools/knowledge/write.py`, deleted at `1df01f2f`) is gone and stays
gone — that namespace and tool shape are not what we restore. The behavior
we restore is exactly what `save_memory_item`'s URL-keyed branch already
implements today (lines 156-219):

- On call with `source_url`: `_find_article_by_url(memory_dir, source_url,
  index_store)` is consulted.
- If a prior article with the same `source_url` exists → consolidate: preserve
  its `id`, merge new content/title in, keep `related`, mark
  `source_type=WEB_FETCH`, `source_ref=source_url`, `decay_protected=True`,
  bump `updated_at`.
- If no prior article → write a new one with the same `WEB_FETCH`/`source_ref`/
  `decay_protected` stamping.

So the restoration is plumbing-already-there. The work in B1 is purely
**surface**: thread `source_url` from `memory_manage` into the
`save_memory_item` call (`manage.py:114`) so the branch is reachable. The
unification's principle — one action-based write tool, parameter-driven —
stands; no separate tool, no namespace return.

The unification commit (v0.8 "memory surface unification") collapsed the
pre-unification `knowledge_*` write tools into a single action-based
`memory_manage` tool. The URL-dedup *plumbing* was preserved in
`save_memory_item`; the *surface* that exposed it was deleted. The result: the
dead branch in `service.py:156-219`, the orphaned `_find_article_by_url`, the
orphaned `SourceTypeEnum.WEB_FETCH`, and the dedup tests at
`tests/test_flow_memory_write.py:75,196` — all real capability, no production
caller. `memory.md`'s "Substrate accumulation (passive)" wording was a
post-hoc rewrite that papered over the gap — co was *never* passive; the agent
always called the tool explicitly.

The restoration is small: thread `source_url` through `memory_manage` so
`save_memory_item`'s existing URL-keyed branch fires.

### Verified current state (2026-05-27)

- `web_fetch` (`co_cli/tools/web/fetch.py:116`): `web_fetch(ctx, url: str,
  format=…, timeout=…)`. `is_read_only=True`, `is_concurrent_safe=True`,
  `retries=3`. Scalar URL signature stays as-is.
- `tool_output_raw` (`co_cli/tools/tool_io.py:261`): returns
  `ToolReturn(return_value=display, metadata=…)` with **no** size check. The
  *only* live callsites are `co_cli/tools/web/search.py:229,232,246` — all the
  error path of `_http_get_with_retries`. No tests reference it.
- The bypass is an abstraction leak: `_http_get_with_retries`
  (`search.py:192`, returns `httpx.Response | ToolReturn`) is an impl helper
  shared by `web_fetch` and `web_search`. On terminal error it builds a
  `ToolReturn` itself; both entrypoints then do `if not isinstance(resp_or_error,
  httpx.Response): return resp_or_error` (`fetch.py:196`, `search.py:343`) —
  forwarding it without passing through `tool_output()`. Both entrypoints
  **have `ctx`**, so the tool boundary can spill; the helper just short-circuits
  past it.
- `tool_error(message, *, ctx)` (`tool_io.py:283`) already exists and is
  `tool_output(message, ctx=ctx, error=True)` — it spills. It is the drop-in
  replacement for the forwarded `ToolReturn`.
- `save_memory_item` (`co_cli/memory/service.py:135`) has a `source_url: str |
  None = None` parameter and a URL-keyed-dedup branch (lines 156-219) — fully
  implemented and tested (`tests/test_flow_memory_write.py:75,196`), but
  unreached: no `save_memory_item(source_url=…)` callsite in `co_cli/`. The
  branch accepts no caller-supplied `tags` or `related`; on consolidation it
  preserves `existing.related` (`service.py:177`). `MemoryItem` has no `tags`
  field (`item.py:57-75`).
- `memory_manage` (`co_cli/tools/memory/manage.py:114`) calls `save_memory_item`
  **without** `source_url` — always takes the Jaccard-dedup path with
  `source_type=MANUAL` (unless caller overrides).

## Problem & Outcome

**Problem.**
1. One tool-call path (`tool_output_raw`, via `_http_get_with_retries`) reaches
   context bypassing the spill ceiling that every other tool result passes
   through.
2. The URL-keyed article-save capability was orphaned in the v0.8 unification
   refactor. Re-encountering the same URL across sessions creates duplicate
   articles, fragmenting recall — the mature plumbing exists but no surface
   reaches it.

**Outcome.**
1. Every tool-call output flows through `tool_output()` → spill.
   `_http_get_with_retries` returns an error string, the ctx-bearing
   entrypoints wrap it via `tool_error`, `tool_output_raw` is deleted.
2. `memory_manage(action="create", kind="article", source_url=…)` exposes the
   orphaned URL-dedup branch. Re-saves consolidate (same `artifact_id`,
   content updated, existing `related` preserved by the branch) rather than
   duplicating. Absent `source_url`: today's Jaccard path unchanged.

## Scope

### In scope
- `co_cli/tools/web/search.py` — `_http_get_with_retries` returns
  `httpx.Response | str` (error message), never `ToolReturn`; the `web_search`
  entrypoint wraps the error via `tool_error`.
- `co_cli/tools/web/fetch.py` — entrypoint wraps the helper-error case via
  `tool_error(resp_or_error, ctx=ctx)`. The scalar `url: str` signature stays
  as-is.
- `co_cli/tools/tool_io.py` — delete `tool_output_raw`; drop the "for ctx-less
  helpers call `tool_output_raw`" pointer from `tool_error`'s docstring.
- `co_cli/tools/memory/manage.py` — add `source_url` parameter to
  `memory_manage`'s create action; pass it through to `save_memory_item`.
  Light up the orphaned URL-dedup branch.
- `docs/specs/tools.md` — remove any `tool_output_raw` mention; state the
  invariant (all tool output spills via the entrypoint). Update the
  `memory_manage` entry with the new `source_url` param and the URL-dedup
  behavior.
- `docs/specs/memory.md` — replace both the "Substrate accumulation
  (passive)" prose (line 166) and the lifecycle-table row implying
  `web_fetch → save_memory_item` auto-wire (line 229) with the actual design
  (explicit agent-mediated article save with URL-keyed dedup; no automatic
  `web_fetch → memory` wire).
- Tests: `tests/test_tool_io.py` (extend/new), `tests/test_flow_memory_write.py`
  (the existing `source_url` tests should still pass; add a tool-surface test
  exercising `memory_manage` with `source_url` end-to-end).

### Out of scope
- **`tags` / caller-supplied `related` on `memory_manage`** — not restoration.
  `MemoryItem` has no `tags` field (`item.py:57-75`); `save_memory_item` has
  no `tags` parameter and no caller-supplied-`related` parameter
  (`service.py:135-147`); the URL-keyed branch's consolidation merges
  neither. Adding them would be a real schema + service-layer change, not
  surface-only restoration of orphaned plumbing. Deferred to its own plan.
- **Multi-URL `web_fetch`** — runtime concurrency (pydantic-ai
  `sequential=False`) already provides parallel fan-out when the model emits
  multiple `web_fetch` calls in one response. Per-tool `urls: list[str]` is
  redundant API-shape overhead, not capability. Rejected, not deferred — see
  "Why multi-URL `web_fetch` is NOT in B1".
- LLM summarization of fetched pages — rejected by doctrine ("no LLM in fetch
  path"; spill bounds context losslessly).
- Multi-domain approval extension — moot without multi-URL fetch.
- Any automatic `web_fetch → memory` wire — explicitly rejected. Ingestion is
  agent-mediated; `web_fetch` and `memory_manage` stay isolated.
- `web_search` multi-query, `web_crawl`.

## Behavioural Constraints
1. **No tool-call output bypasses spill** — impl helpers return data or an
   error value; only the ctx-bearing entrypoint constructs the `ToolReturn`,
   always via `tool_output()`/`tool_error()`. The web error strings are short
   and unchanged in content; they now simply route through the spilling path.
2. **Article URL-dedup is opt-in and agent-mediated** — `source_url` is
   optional on `memory_manage`'s create action. Absent → today's Jaccard path,
   unchanged. Present + `kind="article"` → URL-keyed branch fires:
   `source_type=WEB_FETCH`, `source_ref=source_url`, `decay_protected=True`;
   re-saves with the same URL consolidate (preserve `artifact_id`, preserve
   `existing.related` from the prior item, update content + timestamp). No
   `tags` / new `related` merge in B1 — see Out of scope.
3. **`web_fetch` and `memory_manage` stay isolated** — no auto-wire, no
   lifecycle hook, no shared state beyond the URL string the agent carries
   forward across tool calls. `web_fetch` produces transient content;
   `memory_manage` writes deliberate, curated items. The agent is the bridge.

## High-Level Design

### Remove the `tool_output_raw` spill bypass
```python
# search.py — helper no longer builds a ToolReturn
async def _http_get_with_retries(...) -> "httpx.Response | str":
    ...
    if not decision.retryable:
        return decision.message
    if attempt >= attempts_total:
        return f"{decision.message} Retries exhausted ({max_retries})."
    ...
    return f"{tool_name} failed for {target}."

# fetch.py / search.py entrypoints — wrap via the spilling, ctx-bearing path
resp_or_error = await _http_get_with_retries(...)
if not isinstance(resp_or_error, httpx.Response):
    return tool_error(resp_or_error, ctx=ctx)

# tool_io.py — tool_output_raw deleted; tool_error docstring pointer removed
```

### Restore URL-keyed article save via `memory_manage`
```python
# manage.py — extend the create signature; thread source_url through
async def memory_manage(
    ctx, action: str,
    *,
    name: str | None = None,
    kind: str | None = None,
    content: str | None = None,
    source_type: str | None = None,
    source_url: str | None = None,         # ← restored
    ...
) -> ToolReturn:
    ...
    if action == "create":
        result = save_memory_item(
            memory_dir,
            content=content,
            memory_kind=kind,
            title=name,
            source_type=source_type or SourceTypeEnum.MANUAL.value,
            source_url=source_url,         # lights up the URL-keyed branch when set
            consolidation_similarity_threshold=...,
            index_store=ctx.deps.index_store,
        )
        ...
```

`save_memory_item` already routes on `source_url is not None` (`service.py:156`)
— no service-layer change needed for the restoration. The URL-keyed branch
(`service.py:156-219`) stamps `source_type=WEB_FETCH`, `source_ref=source_url`,
`decay_protected=True`; on consolidation it preserves `existing.related`. The
restoration is **surface-only**: light up the plumbing by exposing it to the
tool layer with a single new parameter. (`tags` / caller-supplied `related`
would be net-new schema + service work; see Out of scope.)

## Tasks

### ✓ DONE — TASK-1 — Remove the `tool_output_raw` spill bypass (§4.1)
Files: `co_cli/tools/web/search.py`, `co_cli/tools/web/fetch.py`,
`co_cli/tools/tool_io.py`.
Impl: change `_http_get_with_retries` return type to `httpx.Response | str`
(its 3 terminal-error returns become bare error strings); both entrypoints
change `return resp_or_error` → `return tool_error(resp_or_error, ctx=ctx)`;
delete `tool_output_raw` and drop the ctx-less pointer from `tool_error`'s
docstring.
**done_when:**
- `tool_output_raw` no longer exists in `co_cli/`; no callsite references it.
- `_http_get_with_retries` never returns a `ToolReturn` (type is
  `httpx.Response | str`).
- A `web_fetch`/`web_search` that hits a terminal HTTP error returns a
  `ToolReturn` built by `tool_error` (so an oversized error body would spill —
  via the same `tool_output()` path as any other result).
- `tool_error`'s docstring no longer points helpers at `tool_output_raw`.

### ✓ DONE — TASK-2 — Restore URL-keyed article save via `memory_manage`
Files: `co_cli/tools/memory/manage.py`, `docs/specs/memory.md`.
Impl: add `source_url: str | None = None` to `memory_manage`'s create
signature; pass it through to `save_memory_item`. Update `memory.md`:
replace both the "Substrate accumulation (passive)" prose (line 166) and the
lifecycle-table row implying `web_fetch → save_memory_item` auto-wire
(line 229) with the actual design — article ingestion is explicit and
agent-mediated; `source_url` enables URL-keyed dedup so re-saves consolidate
rather than duplicate; `web_fetch` and `memory_manage` remain isolated tools
the agent composes.
**done_when:**
- `memory_manage(action="create", kind="article", source_url="X",
  content="…")` creates an article with `source_type=WEB_FETCH`,
  `source_ref="X"`, `decay_protected=True`.
- A second call with the same `source_url` consolidates: same `artifact_id`,
  content updated, `existing.related` preserved by the branch (the existing
  `service.py:156-219` branch does this; no new merge logic in B1).
- A call **without** `source_url`: identical to today (Jaccard dedup,
  `source_type=MANUAL`).
- `_find_article_by_url`, the `source_url` branch in `save_memory_item`, and
  `SourceTypeEnum.WEB_FETCH` are no longer orphans — they have a live caller.
- `docs/specs/memory.md` no longer contains "Substrate accumulation
  (passive)" prose at line 166 nor the `web_fetch → save_memory_item` row in
  the lifecycle table at line 229; both are rewritten to describe the
  agent-mediated explicit-save flow with URL-keyed dedup.

### ✓ DONE — TASK-3 — Spec + gate
Files: `docs/specs/tools.md`, `docs/specs/memory.md` (also touched in TASK-2).
- `tools.md`: remove any `tool_output_raw` mention (lines 204, 237; also the
  module docstring example in `co_cli/tools/tool_io.py:13,15`); state the
  invariant (every tool result is constructed at the ctx-bearing entrypoint
  via `tool_output()`/`tool_error()`, so all spill). Update the
  `memory_manage` entry with the new `source_url` param and the URL-dedup
  behavior.
**done_when:** specs reflect both changes and name no `tool_output_raw`;
`scripts/quality-gate.sh full` clean.

## Testing
- `tests/test_tool_io.py` — terminal HTTP error returns a `tool_error`-built
  `ToolReturn`; `tool_output_raw` is gone (no import/usage).
- `tests/test_flow_memory_write.py` — the existing `source_url` URL-dedup tests
  (lines 75, 196) should still pass post-restoration (they exercise
  `save_memory_item` directly; they prove the branch works). Add a
  tool-surface test exercising `memory_manage(action="create",
  kind="article", source_url=…)` end-to-end: first call saves, second call
  with same URL consolidates (same `artifact_id`).
- On landing, flip §4.1 and §5 rows in
  `RESEARCH-tools-gaps-co-vs-hermes.md` 🟠 → ✅; flip §1.4 (multi-URL) row to
  ⛔ **decided not to adopt** with a one-line pointer to the "Why multi-URL is
  NOT in B1" rationale.

## Open Questions
None — all design questions resolved:
- Multi-URL `web_fetch`: rejected (parity-cosmetic; runtime concurrency
  suffices).
- Multi-domain approval: moot without multi-URL.
- URL-dedup restoration shape: parameter extension on `memory_manage` (not a
  new tool — preserves the unification's single-action-based-tool surface).

## Deferred items
- **Multi-URL `web_fetch`** — runtime concurrency makes it redundant. Will not
  revisit unless concurrent dispatch proves insufficient in practice.
- **Page summarization** (hermes's >5000-char LLM pass) — rejected by
  doctrine; spill bounds context losslessly + deterministically with no LLM in
  the fetch path.
- **`_MAX_FETCH_CHARS` (lossy 100k truncation) vs spill (lossless) — pre-existing
  redundancy.** `web_fetch` hard-truncates at `_MAX_FETCH_CHARS = 100_000`
  (`fetch.py:217`) before spill writes to disk; the lossy cut runs before the
  lossless mechanism, undercutting spill's "nothing discarded" promise on very
  large pages. Pre-existing, out of scope here, candidate for a separate
  cleanup if spill is the real size-bound.
- `web_crawl` / recursive fetch.

## Shipping order
TASK-1 (`tool_output_raw` removal) and TASK-2 (URL-dedup restoration) are
independent and parallelizable. TASK-3 (specs + gate) last → ship as Batch 1.

## Delivery Summary — 2026-05-27

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `tool_output_raw` gone; `_http_get_with_retries` returns `httpx.Response \| str`; both web entrypoints wrap helper-error via `tool_error`; `tool_error` docstring no longer points at `tool_output_raw` | ✓ pass |
| TASK-2 | `memory_manage(create, kind=article, source_url=…)` stamps `WEB_FETCH`/`source_ref`/`decay_protected=True`; second call with same URL consolidates (same `artifact_id`, existing `related` preserved); absent `source_url` falls back to Jaccard/`MANUAL`; orphans (`_find_article_by_url`, URL branch, `WEB_FETCH`) now reached; `memory.md` prose + lifecycle table + ASCII diagram rewritten | ✓ pass |
| TASK-3 | `tools.md` mentions no `tool_output_raw`; invariant stated at the entrypoint; `memory.md` (also touched in TASK-2) coherent; lint clean | ✓ pass (full-gate deferred to `/review-impl` per orchestrate-dev contract) |

**Files changed**
- TASK-1 (TL): `co_cli/tools/web/search.py`, `co_cli/tools/web/fetch.py`, `co_cli/tools/tool_io.py`, `tests/test_tool_io.py` (new)
- TASK-2 (Dev-1): `co_cli/tools/memory/manage.py`, `docs/specs/memory.md`, `tests/test_flow_memory_item_manage.py` (new — tool-surface URL-dedup test, planned in the Testing section)
- TASK-3 (TL): `docs/specs/tools.md`
- Plan: `docs/exec-plans/active/2026-05-27-172716-toolgap-b1-fetch-spill.md` (✓ DONE marks + this summary)

**Scope notes**
- Dev-1 extended TASK-2's `memory.md` rewrite to the ASCII lifecycle diagram immediately following the rewritten prose, because leaving the diagram showing `web_fetch → kind=article` would have contradicted the corrected text. In-spirit extension, declared on the way through.
- TASK-3 also corrected stale `tool_output`/`tool_error` signatures in the same `tools.md` edit window (the bypass-removal rewrite made the surrounding stale signatures more visible than the deletion alone would have); not a separate refactor.

**Tests:** scoped — 22 passed, 0 failed
- `tests/test_tool_io.py` — 3 passed (spill-bypass regression guards)
- `tests/test_flow_memory_write.py` — 7 passed (existing `save_memory_item(source_url=…)` tests still green)
- `tests/test_flow_memory_item_manage.py` — 12 passed (3 new URL-dedup tool-surface tests + 9 existing)

**Doc Sync:** clean — no other spec carries a stale `tool_output_raw` reference; other `memory_manage` mentions in `01-system.md`, `skills.md`, `dream.md`, `observability.md` are name-only (no parameter signatures), so the `source_url` addition creates no stale references.

**Overall: DELIVERED**
All three tasks ✓; lint clean; 22/22 scoped tests pass. Ready for `/review-impl toolgap-b1-fetch-spill`.

## Implementation Review — 2026-05-27

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 #1 | `tool_output_raw` gone from `co_cli/`; no callsite refs it | ✓ pass | `grep -rn tool_output_raw co_cli/ tests/` returns only the regression-guard test |
| TASK-1 #2 | `_http_get_with_retries` returns `httpx.Response \| str`, never `ToolReturn` | ✓ pass | `co_cli/tools/web/search.py:205` annotation; signature smoke confirms |
| TASK-1 #3 | Both web entrypoints wrap helper error via `tool_error` | ✓ pass | `co_cli/tools/web/fetch.py:196-197`, `co_cli/tools/web/search.py:341-342` |
| TASK-1 #4 | `tool_error` docstring no longer points helpers at `tool_output_raw` | ✓ pass | `co_cli/tools/tool_io.py:272-286` docstring rewritten |
| TASK-2 #1 | `memory_manage(create, kind=article, source_url=X)` stamps WEB_FETCH/source_ref/decay_protected | ✓ pass | `co_cli/memory/service.py:178-180`; `tests/test_flow_memory_item_manage.py::test_artifact_manage_create_with_source_url_stamps_web_fetch` |
| TASK-2 #2 | Second call with same URL consolidates (same `artifact_id`, `existing.related` preserved) | ✓ pass | `co_cli/memory/service.py:168-192`; consolidation test passes |
| TASK-2 #3 | Without `source_url`: today's Jaccard/`MANUAL` path | ✓ pass | `co_cli/memory/service.py:221-286`; manual-path test passes |
| TASK-2 #4 | `_find_article_by_url`, URL branch, `SourceTypeEnum.WEB_FETCH` now have a live caller | ✓ pass | `co_cli/tools/memory/manage.py:134` threads `source_url` → `service.py:157` |
| TASK-2 #5 | `memory.md` rewritten — no "Substrate accumulation (passive)", no auto-wire row | ✓ pass | `grep -n "Substrate accumulation" docs/specs/memory.md` returns 0 |
| TASK-3 #1 | `tools.md` removes `tool_output_raw`; invariant stated | ✓ pass | `docs/specs/tools.md:201` invariant paragraph; table row deleted |
| TASK-3 #2 | `memory_manage` entry updated with `source_url` + URL-dedup behavior | ✓ pass | `docs/specs/memory.md:101` (fixed in this review — see Issues table) |
| TASK-3 #3 | Specs reflect both changes; name no `tool_output_raw` | ✓ pass | `grep -rn tool_output_raw docs/specs/` returns 0 |
| TASK-3 #4 | Quality-gate clean | ✓ pass | `scripts/quality-gate.sh lint` + full pytest below |
| Scope creep | `tags`/caller-supplied `related` NOT added | ✓ pass | `grep -n tags co_cli/memory/` returns 0; `save_memory_item` signature unchanged |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `memory_manage()` signature row in spec stale — missing `source_url` (added in TASK-2) and pre-existing `source_type` | `docs/specs/memory.md:101` | blocking (TASK-3 done_when #2 unmet) | Updated row to include both params + a one-sentence URL-dedup-on-create note |
| Trailing comment `_MAX_FETCH_BYTES = … # 1 MB pre-decode limit` | `co_cli/tools/web/fetch.py:32` | minor (pre-existing; CLAUDE.md "no trailing comments" rule; user-feedback says fix in review) | Moved to line above |
| Trailing comment `converter.body_width = 0 # No line wrapping` | `co_cli/tools/web/fetch.py:85` | minor (same as above) | Moved to line above |

### Tests
- Command: `uv run pytest -v`
- Result: **625 passed, 0 failed** in 6:15
- Log: `.pytest-logs/20260527-232259-review-impl.log`

### Behavioral Verification
- `uv run co status`: N/A — no `status` subcommand in this CLI (only `chat`/`tail`/`trace`/`dream`)
- Programmatic surface smoke (drove the actual changed surfaces):
  - `_http_get_with_retries` return annotation: `'httpx.Response | str'` ✓
  - `memory_manage` parameters: `[ctx, action, name, content, kind, section, source_type, source_url]` ✓ (`source_url` lands on the agent-visible signature)
  - `tool_output_raw` absent from `co_cli.tools.tool_io` ✓
  - `build_native_toolset(config)` assembles: 27 tools indexed, including `memory_manage`/`web_fetch`/`web_search` ✓
- User-observable outcomes verified: (1) tool-call errors from web tools spill via `tool_error → tool_output → spill_with_span` (lossless ceiling restored on the one path that bypassed it); (2) agent can call `memory_manage(action="create", kind="article", source_url=…)` and re-saves consolidate instead of duplicating.

### Overall: PASS
