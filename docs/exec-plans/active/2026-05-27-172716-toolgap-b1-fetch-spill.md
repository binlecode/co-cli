# Tool Gap Batch 1 — `web_fetch` multi-URL + `tool_output_raw` spill-safe

Task type: code

## Context

Batch 1 of the ROI-ordered tool-parity gaps
(`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md` §5, refreshed 2026-05-27,
co v0.8.258). The two cheapest high-ROI items, split from the combined plan so
they ship as one tight unit ahead of the harder batches.

- **`web_fetch` multi-URL** (§1.4) — hermes's `web_extract` takes up to 5 URLs
  and fetches them concurrently; co's `web_fetch` is single-URL. Real
  per-research-turn latency win.
- **`tool_output_raw` spill-safe** (§4.1) — `tool_output_raw` bypasses the spill
  ceiling; a helper handed oversized content dumps it unbounded into context.
  Correctness/safety, not a feature.

Sibling batches: `…-toolgap-b2-document-extract.md` (document handling),
`…-toolgap-b3-pty-rolefilter.md` (interactive + recall).

### Hermes parity reference (grounded, not copied)

- **`web_extract`** (`hermes-agent/tools/web_tools.py`): URL cap 5
  (`maxItems: 5`, handler slices `[:5]`); parallel via
  `asyncio.gather(*tasks, return_exceptions=True)`; a failed task is logged and
  skipped (`if isinstance(result_item, BaseException): continue`), batch
  continues; result shape `{url, title, content, error, blocked_by_policy?}`.
  Hermes *also* LLM-summarizes pages >5000 chars — **co does not adopt this**:
  co's `web_fetch` is a direct fetch (no LLM in the fetch path, per project
  doctrine), and co's spill mechanism already bounds oversized pages. So co
  mirrors hermes's *fan-out + per-URL error isolation + cap*, not its
  summarization.
- **`max_result_size_chars`** (`hermes-agent/tools/registry.py:422`,
  `budget_config.py:17` = `100_000`): hermes caps per-tool result size and
  truncates in dispatch. co's equivalent is richer — `spill_if_oversized`
  (`tool_io.py:65`) content-addresses oversized results to disk with a preview
  placeholder. The gap is only that `tool_output_raw` skips it.

### Verified current state (2026-05-27)

- `web_fetch` (`co_cli/tools/web/fetch.py:116`): `web_fetch(ctx, url: str,
  format=…, timeout=…)`. Body validates URL, runs `_is_domain_allowed` +
  `is_url_safe`, fetches via SSRF-safe `httpx.AsyncClient`, converts HTML→md,
  truncates at `_MAX_FETCH_CHARS`, returns `tool_output(display, ctx=ctx,
  url=final_url, content_type=…, truncated=…)`. `retries=3` on the decorator.
- `tool_output_raw` (`co_cli/tools/tool_io.py:261`): returns
  `ToolReturn(return_value=display, metadata=…)` with **no** size check. Live
  callsites: `co_cli/tools/web/search.py:229,232,246` (all short error
  strings). `spill_if_oversized(content, tool_results_dir, tool_name, *,
  force=False)` needs only a `Path`, not a `RunContext` — but ctx-less helpers
  have no per-session `tool_results_dir` to spill into.

## Problem & Outcome

**Problem.** Multi-page reads are serial (N × latency); a ctx-less helper that
ever returns large content silently overflows context.

**Outcome.**
1. `web_fetch` accepts `urls: list[str]`, fetches in parallel (bounded ≤5),
   returns a per-URL result list; single-URL form unchanged.
2. `tool_output_raw` can no longer emit unbounded content — it hard-caps with a
   truncation marker (it has no per-session dir to disk-spill into).

## Scope

### In scope
- `co_cli/tools/web/fetch.py` — extract per-URL body into `_fetch_one`; add the
  `urls` path with bounded `asyncio.gather`.
- `co_cli/tools/tool_io.py` — add a hard ceiling + truncation marker to
  `tool_output_raw`.
- `docs/specs/tools.md` — update `web_fetch` entry + the `tool_output_raw`
  contract note.
- Tests: `tests/test_flow_web_fetch.py` (extend/new), `tests/test_tool_io.py`
  (extend/new).

### Out of scope
- LLM summarization of fetched pages (hermes does it; co deliberately doesn't).
- Disk-spill from `tool_output_raw` (no per-session dir available ctx-less —
  truncation is the honest fix).
- `web_search` multi-query, `web_crawl`.

## Behavioural Constraints
1. **Single-URL `web_fetch` keeps working identically** — same return shape for
   the scalar case; `urls` is additive (Open Q1 decides the exact signature).
2. **Per-URL isolation** — each URL keeps its own `_is_domain_allowed` +
   `is_url_safe` check; one URL's failure (SSRF reject, 404, timeout) returns a
   per-URL `error` entry and never aborts the batch (mirror hermes
   `return_exceptions=True` + skip).
3. **Bounded fan-out** — cap at 5 URLs (hermes parity); excess is rejected via
   `ModelRetry` with a clear message, not silently truncated.
4. **`tool_output_raw` preserves the ctx-less use case** — helpers without a
   `RunContext` still call it; the fix caps size, it does not require a ctx.

## High-Level Design

### `web_fetch` multi-URL
```python
async def web_fetch(ctx, url: str | None = None, urls: list[str] | None = None,
                    format: FetchFormat = "markdown", timeout=_FETCH_TIMEOUT) -> ToolReturn:
    targets = _resolve_targets(url, urls)          # exactly-one-of guard; cap 5 -> ModelRetry
    if len(targets) == 1:
        return await _fetch_one_output(ctx, targets[0], format, timeout)   # unchanged scalar shape
    results = await asyncio.gather(
        *(_fetch_one(ctx, u, format, timeout) for u in targets),
        return_exceptions=True,
    )
    entries = [_to_entry(u, r) for u, r in zip(targets, results)]   # {url, content, content_type, truncated, error?}
    return tool_output(_format_multi(entries), ctx=ctx, results=entries)
```
- `_fetch_one` = today's body refactored to return a dataclass/dict (no
  `ToolReturn`); raises become per-URL `error` entries via `return_exceptions`.
- `_format_multi` concatenates per-URL sections; `tool_output()` spill applies
  to the aggregate (no per-call LLM step).

### `tool_output_raw` spill-safe
```python
_RAW_MAX_CHARS = SPILL_THRESHOLD_CHARS  # reuse the existing 4000-char ceiling

def tool_output_raw(display: str, **metadata) -> ToolReturn:
    if len(display) > _RAW_MAX_CHARS:
        display = display[:_RAW_MAX_CHARS] + f"\n…[truncated {len(display)-_RAW_MAX_CHARS} chars; ctx-less helper cannot disk-spill]"
    return ToolReturn(return_value=display, metadata=metadata or None)
```

## Tasks

### TODO — TASK-1 — `web_fetch` multi-URL
Files: `co_cli/tools/web/fetch.py`.
Impl: extract current fetch body (lines ~169-228) into `_fetch_one(ctx, url,
format, timeout) -> dict` returning `{url, content, content_type, truncated}`
or raising; add `_resolve_targets` (exactly-one-of `url`/`urls`, cap 5 →
`ModelRetry`); add the `asyncio.gather(return_exceptions=True)` aggregate path;
keep the scalar path returning today's exact shape.
**done_when:**
- `web_fetch(urls=[a,b,c])` returns one `tool_output` whose `results` metadata
  is a 3-entry list, each `{url, content_type, truncated, error?}`.
- A batch with one SSRF-blocked + one 404 + one OK URL returns 3 entries (2 with
  `error`, 1 with content) and exit is success (no raise).
- `web_fetch(urls=[6 urls])` raises `ModelRetry` naming the cap.
- `web_fetch(url="…")` returns byte-identical shape to today (regression test).
- Per-URL `_is_domain_allowed` + `is_url_safe` still run (a blocked domain in a
  batch yields a per-URL error, not a batch abort).

### TODO — TASK-2 — `tool_output_raw` spill-safe (§4.1)
Files: `co_cli/tools/tool_io.py`.
Impl: add `_RAW_MAX_CHARS = SPILL_THRESHOLD_CHARS`; truncate-with-marker in
`tool_output_raw`; update the docstring to state the ceiling.
**done_when:**
- `tool_output_raw("x"*10_000).return_value` length ≤ `SPILL_THRESHOLD_CHARS` +
  marker, and ends with the truncation marker.
- Short content (the `web/search.py` error strings) is returned unchanged
  (no marker).
- No `RunContext` is required (signature unchanged); existing callsites compile
  and pass.

### TODO — TASK-3 — Spec + gate
Files: `docs/specs/tools.md`. Append/adjust the `web_fetch` entry (`urls`, cap
5, per-URL errors) and add a one-line `tool_output_raw` ceiling note.
**done_when:** spec reflects both changes; `scripts/quality-gate.sh full` clean.

## Testing
- `tests/test_flow_web_fetch.py` — real multi-URL fetch against stable public
  URLs + a deliberately bad URL in the batch (real data, no mocks); assert
  per-URL isolation, cap rejection, scalar regression.
- `tests/test_tool_io.py` — oversized vs short `tool_output_raw`.
- On landing, flip §1.4 / §4.1 / §5 rows in
  `RESEARCH-tools-gaps-co-vs-hermes.md` 🟠 → ✅.

## Open Questions
1. **`web_fetch` signature** — keep scalar `url` + add optional `urls`
   (exactly-one-of) vs collapse to `urls: list[str]` with a 1-element
   convenience? **Rec:** keep both, reject passing both — preserves the dominant
   single-URL ergonomics and the regression guarantee; additive optional param
   is allowed under zero-backward-compat (no alias, no removal).
2. **Aggregate spill vs per-URL spill** — spill the concatenated multi-URL
   display as one blob (current design) vs spill each oversized URL
   individually? **Rec:** aggregate via the standard `tool_output()` path;
   per-URL spill is premature.

## Deferred items
- Page summarization (hermes's >5000-char LLM pass) — against co doctrine.
- `web_crawl` / recursive fetch.

## Shipping order
TASK-1 + TASK-2 (independent, parallelizable) → TASK-3 gate → ship as Batch 1.
