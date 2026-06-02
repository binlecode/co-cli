# drop-web-research-add-fetch-extraction

Drop the `web_research` subagent-delegation tool (and the now-orphaned in-turn delegation
machinery), and fix `web_fetch` to return extracted main-page content instead of whole-page
HTML chrome. Together these remove a redundant capability and fix the real failure it was
masking.

## Context

The architecture question "is `web_research` needed?" was answered empirically by
`evals/eval_research_direct.py` (report: `docs/REPORT-eval-research-direct.md`): the main
agentic loop conducts and completes multi-step research on its own via `web_search` →
`web_fetch` → synthesize, with **zero delegation** (2/3 cases clean PASS; matches the
hermes/openclaw peer survey — neither ships a compound research tool). `web_research` is a
DEFERRED delegation tool that spawns a sub-agent; it is the **last in-turn delegation tool**
(`docs/specs/agents.md:115`).

The one research task that failed (heavy multi-doc comparison) did **not** fail for lack of
a research capability. It hit the **120 s per-segment wall-clock budget**
(`co_cli/context/orchestrate.py:388`, `LLM_SEGMENT_TIMEOUT_SECS`) because `web_fetch` runs
the *entire* fetched HTML through html2text (`co_cli/tools/web/fetch.py`), returning ~100 KB
of nav/sidebar/footer chrome per page. That bloat (a) slows every subsequent generation on
the growing prompt and (b) tipped a serial generate→fetch loop over the segment budget.
Compaction did not help because the failure is on the **time** axis, not the token axis it
governs (see analysis in `docs/REPORT-eval-research-direct.md` discussion).

`web_research` "earns its keep" only as a *workaround* for that bloat (sub-agent context
isolation + smaller per-call prompts), not as a missing ability. Fix the bloat at the source
and the workaround is no longer justified. Decision (confirmed with PO): **drop `web_research`
+ fix `web_fetch` content extraction.**

### Extraction library decision

`trafilatura` (Apache-2.0, v2.0.0, actively maintained — the extractor behind FineWeb /
RefinedWeb). 2026 benchmarks: highest open-source F1 (0.958), beating Mozilla Readability
(0.947), newspaper4k, goose3; it internally falls back to readability-lxml, so it is a
superset. `readability-lxml` is semi-unmaintained (out of sync with upstream) and was
rejected. Hermes offloads extraction to SaaS backends (Firecrawl/Tavily) + an auxiliary-LLM
summarization pass — **not portable**: co's `web_fetch` is a raw `httpx` GET with no
extraction backend, and the LLM-summarization half is exactly the `web_research` latency we
are removing. co does extraction **in-process** (local-first, no third-party data egress).
trafilatura emits markdown directly, so it composes with — and can front — the existing
html2text path as a fail-open fallback. Footprint cost (lxml + a few permissive deps) is
accepted: co is local, the bottleneck is Ollama generation, not parsing.

## Problem & Outcome

**Problem.** A redundant delegation tool (`web_research`) sits on the surface masking a real
`web_fetch` defect (whole-page HTML → context bloat → segment-timeout on heavy research).

**Outcome.**
- `web_research` and the orphaned in-turn delegation primitive (`run_attempt`,
  `MAX_AGENT_DEPTH`, `merge_delegation_usage`) are gone. The only task-agent path left is the
  daemon `run_standalone` (dream reviewer) — unchanged.
- `web_fetch` returns extracted main content (markdown) for HTML pages, fail-open to the
  current full html2text conversion when extraction yields nothing.
- The heavy-research case in `eval_research_direct.py` completes under the 120 s segment
  budget (validating the fix at the source); the eval becomes a standing guard that research
  stays in the atomic loop.
- Specs (`agents.md`, `tools.md`, `observability.md`) reflect the simplified surface.

## Behavioral Constraints

- **Zero-backward-compat** ([[feedback_zero_backward_compat]]): no `web_research` alias, no
  deprecation shim, no compat row in specs. Hard removal.
- **No migration code** ([[feedback_no_migration_code]]): nothing reads or references the old
  tool after this lands.
- **Orphan-only cleanup** (CLAUDE.md surgical rule): `run_attempt` / `MAX_AGENT_DEPTH` /
  `merge_delegation_usage` are orphaned *by this change* (web_research was their only caller),
  so removing them is in-scope. Do **not** touch `run_standalone`, `build_task_agent`,
  `TaskAgentSpec`, or `fork_deps` — all still used by the dream daemon.
- **Fail-open extraction**: any trafilatura error or empty/whitespace result MUST fall back to
  `_html_to_markdown(full_html)` — a fetch must never fail or return empty because extraction
  choked. `format="html"` / `format="text"` paths stay raw (no extraction).
- Preserve `web_fetch`'s SSRF guard, domain policy, Cloudflare fallback, retry, content-type
  allowlist, and `_MAX_FETCH_CHARS` truncation backstop — extraction slots in *only* at the
  `"html" + format=="markdown"` conversion point (`fetch.py:217-218`).
- **Tests/repros** use real config model settings per [[feedback_tests_use_config_model_settings]];
  the extraction unit tests are pure-function (no network, no LLM) and deterministic.

## High-Level Design

### Part A — drop `web_research` (code)

Dead-set verified by grep (`web_research` is the only in-turn delegation caller):

| File | Action |
|---|---|
| `co_cli/tools/agents/delegation.py` | **DELETE** (whole file: `web_research`, `WEB_RESEARCH_SPEC`, `AgentOutput`, `_researcher_instructions`). |
| `co_cli/tools/agents/__init__.py` + dir | **DELETE** the package — `delegation.py` was its only module. |
| `co_cli/agent/run.py` | Remove `run_attempt`, `MAX_AGENT_DEPTH`, `merge_delegation_usage`. Keep `run_standalone`. Drop now-unused imports (`ModelRetry`, `RunContext`). Update module docstring to daemon-only. |
| `co_cli/context/orchestrate.py:179` | Rewrite the `_merge_segment_usage` docstring sentence that name-drops the deleted `merge_delegation_usage` ("Delegation sub-agent tools accumulate via merge_delegation_usage; this one is owned by the foreground orchestrator.") — orphaned reference created by this change. |
| `co_cli/agent/toolset.py:15` | Remove `from co_cli.tools.agents.delegation import web_research  # noqa: F401`. |
| `co_cli/tools/display.py:35` | Remove `"web_research": "query",`. |
| `co_cli/tools/web/search.py:290-293` | Remove the `web_research` steer clause from `web_search`'s docstring ("…for a multi-page question needing reading + synthesis use web_research."). Reword so the remaining "single quick lookup vs full page content" guidance reads clean and self-contained. |
| `co_cli/commands/history.py:9-14` | Remove `"web_research"` from `_DELEGATION_TOOLS`, leaving `{"task_start"}`. |

### Part B — `web_fetch` content extraction (code)

`co_cli/tools/web/fetch.py`:
- Add `import trafilatura` (module top) and `trafilatura>=2.0.0` to `pyproject.toml`
  `dependencies`.
- New pure helper:
  ```python
  def _extract_main_content(html: str, url: str) -> str | None:
      """Extract main-article markdown from HTML; None when nothing usable extracted.

      Fail-open: any extraction error returns None so the caller falls back to
      full-page conversion. Drops nav/header/footer/sidebar boilerplate so the
      model receives content, not chrome.
      """
      try:
          extracted = trafilatura.extract(
              html,
              url=url,
              output_format="markdown",
              include_links=True,
              include_tables=True,
              favor_recall=True,
          )
      except Exception:
          return None
      if not extracted or not extracted.strip():
          return None
      return extracted
  ```
- At the conversion site (`fetch.py:217-218`; grep for `if "html" in content_type and format`, do not trust the line ref) replace:
  ```python
  if "html" in content_type and format == "markdown":
      text = _html_to_markdown(text)
  ```
  with:
  ```python
  if "html" in content_type and format == "markdown":
      extracted = _extract_main_content(text, final_url)
      text = extracted if extracted is not None else _html_to_markdown(text)
  ```
- `_html_to_markdown`, `_MAX_FETCH_CHARS` truncation, and the non-markdown paths are
  unchanged. (`favor_recall=True` keeps borderline content rather than over-pruning — safer
  for doc/reference pages.)

### Part C — specs + tests + eval

Specs sync in-place (sync-doc discipline): remove the in-turn-delegation machinery and the
`web_research` rows; the daemon `run_standalone` section stays.

## Tasks

### ✓ DONE TASK-1 — drop `web_research` + orphaned in-turn delegation machinery

**Files:** delete `co_cli/tools/agents/` (pkg); edit `co_cli/agent/run.py`,
`co_cli/context/orchestrate.py`, `co_cli/agent/toolset.py`, `co_cli/tools/display.py`,
`co_cli/commands/history.py`.

**done_when:**
- `grep -rn "web_research" co_cli/` returns **0** (excluding nothing — production is clean).
- `grep -rn "run_attempt\|MAX_AGENT_DEPTH\|merge_delegation_usage" co_cli/` returns **0** (includes the `orchestrate.py:179` docstring orphan).
- `grep -rn "tools.agents\|tools import agents" co_cli/ tests/` returns **0**.
- `ruff check co_cli/agent/run.py` reports no unused imports (ModelRetry/RunContext removed).
- `co_cli/agent/run.py` still defines `run_standalone`; `grep -rn "run_standalone" co_cli/daemons/` still resolves.
- `uv run python -c "from co_cli.bootstrap.core import create_deps"` imports clean (registry builds without the delegation import).

### ✓ DONE TASK-2 — remove `web_research` from docstrings / display / history / steer

**Files:** `co_cli/tools/web/search.py`, `co_cli/tools/display.py`,
`co_cli/commands/history.py` (display/history covered structurally in TASK-1; this task owns
the `web_search` docstring rewrite).

**done_when:**
- `web_search` docstring no longer mentions `web_research`; the remaining when-to-use guidance
  (snippets-vs-full-page) reads as a complete thought (manual read).
- `uv run python tmp/audit_tool_schemas.py` shows `web_research` absent from the registry and
  the DEFERRED bucket reduced by its former size (~1,268 chars).

### ✓ DONE TASK-3 — `web_fetch` trafilatura content extraction

**Files:** `co_cli/tools/web/fetch.py`, `pyproject.toml`.

**Action:** add dependency; add `_extract_main_content`; wire fail-open at the markdown
conversion site (High-Level Design Part B).

**done_when:**
- `uv sync` resolves `trafilatura>=2.0.0`; `uv run python -c "import trafilatura"` works.
- `uv run pytest tests/ -k "web_fetch or fetch" -x` passes.
- On a real chrome-heavy page (e.g. `https://docs.astral.sh/uv/concepts/resolution/`), a
  `web_fetch` smoke returns markdown whose length is a small fraction of the raw HTML and
  contains the article body, not the nav menu (manual `co chat` or a scratch repro in `tmp/`).

### ✓ DONE TASK-4 — tests

**Files:** delete `tests/test_flow_delegation_agent.py`; edit
`tests/test_flow_deferred_tool_stubs.py`; add `tests/test_web_fetch_content_extraction.py`.

**Action:**
- DELETE `tests/test_flow_delegation_agent.py` (its entire subject — `web_research` +
  `MAX_AGENT_DEPTH` — is removed).
- In `tests/test_flow_deferred_tool_stubs.py:194-208`, replace the `"web_research"` fixture
  entry (NATIVE, integration=None) with `"task_start"` (also NATIVE deferred, integration=None)
  and update assertion (e) `mapping["task_start"] == ""` — preserves the test's intent (a
  None-integration native tool renders in the general no-sub-header section).
- ADD `tests/test_web_fetch_content_extraction.py` (pure-function, no network/LLM):
  1. HTML with `<nav>/<header>/<footer>` boilerplate + an `<article>` with distinctive text →
     `_extract_main_content` returns markdown containing the article text and **not** the nav
     text.
  2. Degenerate HTML (no article, e.g. a bare link list) → `_extract_main_content` returns
     `None` (fail-open), and the `web_fetch` markdown path falls back to `_html_to_markdown`.
  3. trafilatura raising (monkeypatched to throw) → `_extract_main_content` returns `None`
     (exception fail-open), no propagation.

**done_when:**
- `uv run pytest tests/test_web_fetch_content_extraction.py tests/test_flow_deferred_tool_stubs.py -x` passes.
- `grep -rn "web_research\|MAX_AGENT_DEPTH" tests/` returns **0**.

### ✓ DONE TASK-5 — specs sync

**Files:** `docs/specs/agents.md`, `docs/specs/tools.md`, `docs/specs/observability.md`.

**Action:**
- `agents.md`: remove the `### run_attempt — the in-turn primitive` and
  `### web_research — single-span retry topology` sections, the `MAX_AGENT_DEPTH` config row
  (`:158`), and the inventory rows referencing `web_research` / `run_attempt` (`:41,52,75,186-187,200,214`).
  Reframe the delegation overview: there are now **no in-turn delegation tools**; only daemon
  task agents (`run_standalone`, dream reviewer) remain. Keep the `run_standalone` section.
- `tools.md`: remove the Delegation row (`:35`) and the delegation section (`:200,273`).
- `observability.md`: remove the `co.web_research.retry_loop` span row (`:181`).

**done_when:**
- `grep -rn "web_research\|run_attempt\|MAX_AGENT_DEPTH\|in-turn delegation" docs/specs/` returns **0**.
- `agents.md` `run_standalone` / daemon sections intact (manual read); no dangling references
  to deleted sections in the spec's own cross-links.

### ✓ DONE TASK-6 — eval validation (corroborating, not the proof)

**Files:** `evals/eval_research_direct.py` (framing update only).

**Validation methodology — read first.** The eval is **noisy**: across the four recorded runs
in `docs/REPORT-eval-research-direct.md`, `doc-compare` swung FAIL(never searched) →
FAIL(errored pre-search) → SOFT_FAIL(timeout) → FAIL(never searched), and `release-notes`
swung 109s↔160s. The variance is dominated by Ollama generation latency and model search
behavior, **not** fetch payload size — and "never searched" is a model-behavior failure this
change does not touch. So a single post-fix run cannot prove (or disprove) the fix.

- **Primary proof of the fix** is the deterministic, directly-attributable signal in TASK-3:
  extracted-markdown bytes vs full-page-markdown bytes on a real chrome-heavy page. That ratio
  is what this change actually controls; record it.
- **TASK-6 is corroborating only.** Do not gate the plan on a single `doc-compare` outcome.

**Action:**
1. Update the eval docstring + the `web_research` count usage: with the tool gone,
   `frontend.count("web_research")` is structurally 0; keep the `delegate==0` check as a
   **regression guard** ("no delegation tool exists or reappears") and note the tool was
   dropped. No logic change to the PASS rule.
2. Re-run `uv run python evals/eval_research_direct.py` and record the new
   `docs/REPORT-eval-research-direct.md` run section.

**done_when:**
- Eval completes and the DECISION line reflects research staying in the atomic loop with zero
  delegation (the regression guard holds).
- Record the `doc-compare` outcome against the per-page byte-reduction from TASK-3 in the
  delivery summary as **corroborating** evidence. A non-timeout `doc-compare` (PASS or
  non-timeout SOFT_FAIL) is a positive signal; a "never searched" / model-behavior failure is
  **not** a regression of this change — note it and move on rather than blocking.
- If `doc-compare` still times out *specifically on the segment wall-clock* after extraction
  lands, note it and reopen the timeout as a separate plan (per Out of scope) — do not retune
  the timeout here.

## Testing

- `scripts/quality-gate.sh full` (lint + full pytest).
- `tests/test_web_fetch_content_extraction.py` — extraction + fail-open (TASK-4).
- `evals/eval_research_direct.py` — corroborating behavioral signal for the fetch fix and
  zero-delegation regression guard (TASK-6); the deterministic byte-reduction in TASK-3 is the
  primary proof.
- `co chat` smoke: a research turn (e.g. "compare uv vs pip dependency locking") completes
  without timeout and cites fetched pages.

## Coordinate with active plans

- **`2026-05-28-142556-prefill-trim-2-tool-guidance-dedup.md` (Gate 1).** Its TASK-2 edits
  `web/search.py` + `web/fetch.py` docstrings and (per the Gate-1 review Nit 2) was told to
  **preserve** the `web_search`↔`web_research` steer. Dropping `web_research` **inverts** that:
  the steer must be **removed**, which THIS plan does. **Sequencing:** land this plan first;
  then prefill-trim-2's TASK-2 re-baselines against the post-drop `web_search` docstring and
  its "preserve the steer" instruction is dropped. Both plans touch `web/search.py` +
  `web/fetch.py` — do not run them concurrently. prefill-trim-2's TASK-4 schema-budget guard
  counts the **ALWAYS** bucket; `web_research` is DEFERRED, so the guard ceiling is unaffected
  (only the DEFERRED bucket shrinks) — note in that plan's re-measure.
- **`2026-06-02-100055-deferred-tool-stub-grouping.md` (active).** It also touches
  `tests/test_flow_deferred_tool_stubs.py`. This plan swaps the `web_research` fixture entry
  for `task_start`; coordinate the edit so the two don't clobber. The runtime deferred-stub
  prompt is built from the live registry (no hardcoded `web_research`), so it auto-adapts.

## Out of scope

- Timeout changes (per-call vs per-segment, fix #3). Confirmed not needed once fetch bloat is
  fixed; the 120 s segment guard is a correctly-functioning safety net, not the bug. If a
  measured heavy case still times out *after* extraction lands, reopen as a separate plan.
- `run_standalone` / daemon task-agent path, `TaskAgentSpec`, `build_task_agent`, `fork_deps`
  — all retained, untouched.
- Adding any external extraction/reader API (Jina/Firecrawl) — rejected (third-party data
  egress, extra hop, availability dep); co extracts in-process.
- `web_search` provider/behavior (Brave) — only its docstring steer is edited.

## Open Questions

- trafilatura flag tuning (`favor_recall`, `include_tables`, `include_links`) — start with the
  values in Part B; revisit only if the TASK-3 smoke shows over- or under-pruning on real
  pages. Not a blocker.

## Delivery Summary — 2026-06-02

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `web_research` / `run_attempt` / `MAX_AGENT_DEPTH` / `merge_delegation_usage` grep 0 in `co_cli/`; `create_deps` imports clean; `run_standalone` intact + daemon resolves | ✓ pass |
| TASK-2 | `web_search` docstring no longer cites `web_research`; live registry = 35 tools, `web_research` absent, `web_fetch`/`web_search` present | ✓ pass |
| TASK-3 | `uv sync` resolves `trafilatura>=2.0.0`; fetch tests pass; chrome-heavy smoke returns article body | ✓ pass |
| TASK-4 | extraction + deferred-stub tests pass; `web_research`/`MAX_AGENT_DEPTH` grep 0 in `tests/` | ✓ pass |
| TASK-5 | `web_research`/`run_attempt`/`MAX_AGENT_DEPTH`/`in-turn delegation` grep 0 across `docs/specs/`; `run_standalone`/daemon sections intact | ✓ pass |
| TASK-6 | eval completes; DECISION = research stays in atomic loop, zero delegation; doc-compare recorded vs byte-reduction | ✓ pass |

**Team:** TL (TASK-1, 2, 4, 6 + integration) · Dev-1 (TASK-3) · Dev-2 (TASK-5).

**Tests:** scoped — 38 passed, 0 failed (`test_web_fetch_content_extraction.py`, `test_flow_deferred_tool_stubs.py`, `test_display.py`; plus `tests/ -k "web_fetch or fetch"` 3 passed). **Lint:** clean.

**Doc Sync:** TASK-5 *was* the spec sync (full `docs/specs/` scope) — clean + verified, no separate `/sync-doc` pass.

**Primary proof (deterministic, TASK-3 smoke — `docs.astral.sh/uv/concepts/resolution/`):**
raw HTML 141,713 → extracted markdown 36,257 (25.6% of raw). Caveat: vs the *previous* output (full html2text, 43,855), the post-markdown reduction on this content-dense page is ~17% — extraction's biggest wins are on chrome-heavy pages whose html2text output exceeds the `_MAX_FETCH_CHARS` cap; on already-lean pages the gain is modest.

**Corroborating signal (TASK-6 eval, run 2026-06-02T20:22Z):** 4/4 PASS, **zero delegation**. The heavy `doc-compare` case flipped **SOFT_FAIL → PASS** (search=0 fetch=8, completed in 192.6s across multiple sub-120s segments — no segment-budget timeout), where it FAILED/SOFT_FAILed in all 4 prior runs. Model-call time dropped broadly: release-notes 160.2s → 72.9s (−87s), current-fact 96.1s → 70.2s (−26s) — consistent with less per-page context bloat. **Honesty note (per the Gate-1 reframe): the eval is noisy; a single passing run isn't causal proof. The deterministic byte reduction is the proof; the eval corroborates, and the across-the-board generation speedups make the causal story coherent.**

**Deviations from plan (TL calls):**
1. **Dropped the monkeypatch test (TASK-4 test 3).** `testing.md:17` bans `monkeypatch`/mocks as enforced policy; the trafilatura-raises exception path can't be tested without faking. Kept tests 1 & 2 on real inputs. The `try/except → None` fail-open is verified by code review, not a fake.
2. **Adjusted TASK-4 test 2 fixture.** The plan assumed a bare link list → `None`, but with `favor_recall=True` trafilatura extracts the links. The real `None` trigger is contentless HTML — tested that (the genuine fail-open branch) plus that `_html_to_markdown` (the fallback target) still yields content.
3. **Fixed 3 spec stragglers beyond TASK-5's file list** (Dev-2 escalated): `01-system.md:274` (`run_attempt`/"in-turn primitive" row), and two phantom "Delegation agents share model handle" test-gate rows (`01-system.md:292`, `config.md:371`) pointing at test files that never existed. The TASK-5 `done_when` grep covers all of `docs/specs/`, so these were in-scope by the criterion even though the file list omitted them.
4. **Fixed the `orchestrate.py:179` docstring orphan** (Gate-1 Required-fix-1) — folded into TASK-1.

**Out-of-scope follow-up surfaced:** if a future heavy multi-doc case still hits the 120s *segment* wall-clock budget after extraction, reopen the per-call-vs-per-segment timeout as a separate plan (per Out of scope) — do not retune the timeout here.

**Overall: DELIVERED** — all 6 tasks pass `done_when`, lint clean, scoped tests green, specs synced, eval corroborates the fix-at-source.

**Next step:** `/review-impl drop-web-research-add-fetch-extraction` — full suite + evidence scan + behavioral verification → verdict at Gate 2.

## Implementation Review — 2026-06-02

Stance: issues exist — PASS earned. Four parallel cold-read reviewers (one per task group) + line-level adversarial confirmation.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | greps 0 in `co_cli/`; import clean; `run_standalone` intact | ✓ pass | `run.py:20` defines `run_standalone`; all 6 remaining imports used; `_reviewer.py:100,119` call it; no `RunContext`/`ModelRetry` residue |
| TASK-2 | `web_search` docstring clean; registry 35 tools, no `web_research` | ✓ pass | `search.py:290-292` complete snippets-vs-full-page thought; `display.py:35`/`history.py:12` retain `task_start` only |
| TASK-3 | `trafilatura>=2.0.0`; fetch tests pass; chrome-heavy smoke | ✓ pass | `fetch.py:92` helper, `:108-111` fail-open (exception + empty), `:241-243` wired before `_MAX_FETCH_CHARS` truncation; diff is 3 surgical hunks, SSRF/retry/allowlist untouched |
| TASK-4 | extraction + deferred-stub tests pass; `tests/` grep 0 | ✓ pass | real `_extract_main_content`/`_html_to_markdown` calls, no fakes; `task_start` swap exercises None-integration general-section branch |
| TASK-5 | `docs/specs/` grep 0; `run_standalone`/daemon intact | ✓ pass | grep exit 1 across specs; `agents.md:32` correct no-in-turn-delegation prose; mermaid/tables well-formed |
| TASK-6 | eval completes; DECISION zero-delegation; doc-compare recorded | ✓ pass | PASS-rule logic unchanged (`:298` `proven` expr, `:227` `n_delegate>0`); REPORT 4 PASS, doc-compare SOFT_FAIL→PASS |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Test-gate rows cite `test_flow_delegation_agent.py` (deleted by TASK-4) | `agents.md:181-182` | blocking | Repointed to `test_flow_fork_deps.py` (where `agent_depth`/fresh-runtime behaviors live) |
| Residual "delegation earns its keep" framing (contradicts the drop) | `eval:34`, `eval:288` | blocking | Reworded to per-segment-timeout framing |
| `delegated` flag keyed off `"DELEGATED"` — new reason string lacks it → decision-text branch unreachable | `eval:294` | blocking (latent, introduced by the framing edit) | Aligned detector to `"REGRESSION: web_research"` |
| Index row lists stale "delegation agent, judge model" | `01-system.md:61` | minor | Reworded to "daemon task agents" |
| Test docstrings over-claim "the fail-open path" (only empty-result branch tested) | `test_web_fetch_content_extraction.py:1,62` | minor | Tightened; noted exception branch is review-only (no-mocks policy) |
| `favor_recall=True` rationale undocumented | `fetch.py:96` | minor | Added one-line note |

Non-blocking nits left as-is: `import trafilatura` top-level startup cost (consistent with existing top-level `html2text`); the pre-existing stray `FetchFormat = Literal` placement at `fetch.py:7` (out of scope).

### Tests
- Command: `uv run pytest -q` (full suite)
- Result: **451 passed, 0 failed, 1 deselected** in 299s
- Log: `.pytest-logs/20260516-180318-review-impl-full.log`
- Scoped re-runs after fixes: 10 passed (`test_web_fetch_content_extraction.py`, `test_flow_deferred_tool_stubs.py`)

### Behavioral Verification
- `uv run co --help`: ✓ boots clean (project has no `co status`; commands are chat/tail/trace/dream/google)
- Tool registry assembles with `web_research` absent, `web_fetch`/`web_search` present (35 tools) ✓
- **TASK-6 eval = live behavioral proof:** 4 real end-to-end turns through production `ORCHESTRATOR_SPEC`; `web_fetch` returned extracted content, research completed, **zero delegation**, doc-compare flipped SOFT_FAIL→PASS. `success_signal` (research stays in atomic loop, no delegation tool) verified.

### Overall: PASS
All `done_when` met with file:line evidence, 3 blocking findings auto-fixed, full suite green (451/0), behavioral proof via the eval. Ready for Gate 2 → `/ship`. Note for ship: the tree carries unrelated coworker WIP (google-auth, etc.) — stage only this plan's files.
