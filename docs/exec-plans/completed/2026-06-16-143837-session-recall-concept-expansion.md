# session-recall-concept-expansion

## Context

Session recall is lexical, file-based, and deliberately mechanical (`session-search-ripgrep`, v0.8.298, dropped the hybrid vector index). Verified current state this session:

- **Engine** — `co_cli/session/_search.py:81-97`: `rg --null --line-number --no-heading --with-filename --fixed-strings --ignore-case --no-config --no-ignore --hidden -e <query> --glob *.jsonl`. `--fixed-strings` makes matching **literal substring only**. Python fallback (`:134-148`) is also literal (`needle in line.lower()`). Both paths drop matches that land only on structural JSON keys (`_build_hits`, `:151-200`).
- **Why literal** — a deliberate v1 floor. Completed plan `docs/exec-plans/completed/2026-06-03-230257-session-search-ripgrep.md:259-260` explicitly parked regex/multi-term as *"revisit"*: literal mirrors `tools/files/read.py` invocation hygiene and avoids the "query meant literally, interpreted as a pattern" silent-misinterpretation class.
- **Tool** — `co_cli/tools/session/recall.py`: `session_search(query="", limit=3)` (DEFERRED). Browse mode (`query=""`) returns recent-session metadata (id, date, title, size) via `_browse_recent` (`:25-48`); keyword mode caps at `_SESSIONS_CHANNEL_CAP = 3` unique sessions (`:18`). The **docstring is the only model-facing guidance** — there is no session-search skill, and co retired skill-based discovery (`completed/2026-05-14-161640-retire-skill-search.md`) in favor of prompt/rule-injected guidance.
- **Recall guidance home** — `co_cli/context/rules/07_memory_protocol.md` `## Recall`: "search before answering … If no results, make at most one broader retry, then surface the miss." This is where cross-session recall discipline already lives.
- **Skim primitive already exists** — browse mode is a newest-first session map (it returns title per session). `session_view(session_id, start_line, end_line)` (`co_cli/tools/session/view.py:23`) is the deep-read.
- **Engine safety** — rg's default regex (Rust `regex` crate) is linear-time, no catastrophic backtracking. `build_subprocess_env` (`co_cli/tools/shell_env.py:40`) + `--no-config` already sandbox the invocation.

## Problem & Outcome

Literal-only matching cannot bridge **vocabulary mismatch** — a core recall-miss class. When a past session recorded a structured entity (a flight code `AA890`, an error code, a path, a confirmation number) and the user later asks in different words ("what flight did you check?"), a literal query on the user's vocabulary (`flight`) misses it, because the session never contains that word. The mismatch splits two ways: (a) the entity has a **shape** (`\b[A-Z]{2}\d{2,4}\b`) the literal engine cannot express — addressed by the regex path (D-A); (b) the entity has a **synonym** (`checked in` / `boarding`) the user did not use — addressed by docstring guidance to fire multiple literal angles (D-B), no engine change. Today the agent has neither the engine affordance nor a documented cascade telling it to expand a concept into patterns/synonyms. Result: a silent "No session results found" that is actually a false negative.

**Outcome:** the agent can (1) search by structural pattern when intent maps to an entity shape, and (2) follows a documented recall cascade — pattern + synonym angles → honest uncertainty (naming what was searched and what was not) — so recall failures are visible, never silent. Content with *no lexical or structural handle at all* (FM-4) is out of scope: it is unreachable by any search engine and not worth brute-force machinery (see Scope/Out).

**Failure cost:** without this, cross-session recall silently misses any past content whose wording differs from the user's query — the exact "what did we decide / what did you check last time" case the feature exists for. The miss is invisible (looks identical to "genuinely nothing there"), which quietly erodes trust in recall and pushes the user to re-supply context the agent already has.

## Scope

**In:**
- Regex/pattern search path in `_search.py` + `store.search` + `session_search` tool param, **literal default preserved** (D-A).
- Concept-to-pattern / concept-to-synonym / multi-angle guidance in the `session_search` docstring (D-B).
- Recall cascade (pattern → synonym → honest miss) in `07_memory_protocol.md` `## Recall` (D-C).

**Out:**
- **Brute-force reverse-skim (FM-4, no-handle content).** Excluded by design, not deferred. Skim adds no capability (the agent can already browse via `session_search(query="")` and deep-read via `session_view`); its only pick-signal is the session `title` = the first user turn (`browser.py:32`), which is decoupled from buried mid-session content — so for the no-handle tangent it claims to recover, the mechanism is luck-based. Where title-skim is reliable (topic-level recall) the forward path already catches it. Not worth a standing rule rung. The honest-miss rung (rung 3) instead names what was searched and that the full history was not exhaustively read, keeping the miss honest without pretending skim is a fix.
- Persistent index of any kind (contradicts the rg/drop-hybrid direction).
- Semantic/embedding recall (the vocabulary-mismatch-at-scale case; explicitly deferred — only worth it past the exhaustive-skim corpus ceiling).
- New skill (cuts against `retire-skill-search`).
- Raising `_SESSIONS_CHANNEL_CAP` (multi-angle is achieved by repeated tool calls, not a bigger single-call payload) — see Open Questions.
- `docs/specs/sessions.md` rewrite — handled by `sync-doc` post-delivery.

## Behavioral Constraints

- **Zero backward compat.** No alias/stub. Existing `session_search(query=...)` literal behavior is byte-for-byte unchanged when `pattern` is not supplied.
- **Literal stays the default and the safe path.** Regex is opt-in via a distinct parameter; an ordinary keyword query is never reinterpreted as a pattern. Preserves the no-silent-misinterpretation guarantee.
- **`pattern` and `query` are mutually exclusive.** Supplying both returns a `tool_error` (no implicit precedence that could surprise).
- **Invalid regex never crashes and never silently lies.** In regex mode the pattern is `re.compile`-validated *before* dispatch; on `re.error` (or rg exit 2) the search returns an explicit error string to the model — never an empty "nothing found", and never a fallthrough into the Python line-scan with a known-bad pattern.
- **Engine stays mechanical and stateless.** No persistent state; per-request only.
- **Reliability over speed** is the stated priority — regex over a small corpus is acceptable cost; the Python-fallback regex risk is bounded by corpus size and noted, not optimized away.

## Failure Modes

Observed/analyzed this session against the current literal engine:

- **FM-1 (vocabulary mismatch, structured entity):** session contains `AA890 delayed`; query `flight` → 0 hits. The entity has a structural handle (`\b[A-Z]{2}\d{2,4}\b`) the engine cannot express. *Addressed by D-A + D-B.*
- **FM-2 (vocabulary mismatch, synonym):** session says `checked in / boarding`; query `flight` → miss. *Addressed by D-B multi-angle synonym guidance.*
- **FM-3 (silent false negative):** every miss above renders as "No session results found for 'flight'", indistinguishable from a true absence. *Addressed by D-C honest-miss — rung 3 names what was searched and that the full history was not exhaustively read, so the miss is never a bare "nothing found".*
- **FM-4 (no-handle content):** session says "the 6am to SFO got pushed back" — no lexical handle (no `flight`) and no structural shape (no code). *Out of scope: unreachable by literal, synonym, or regex search alike. Distinct from FM-1 — the `AA890` case has a shape regex catches; this case has nothing to match. Brute-force skim is rejected (see Scope/Out); a semantic index is the only real fix and is separately out of scope. The honest-miss rung surfaces this case rather than masking it.*

## High-Level Design

**D-A — Pattern mode (engine).** (OQ-1 resolved)
Thread a single keyword-only flag through all three layers: `search_sessions(sessions_dir, query, limit, *, is_regex=False)` → `store.search(query, limit, *, is_regex=False)` → tool. The tool exposes `session_search(query="", pattern="", limit=3)`; a non-empty `pattern` routes its string through the existing `query` channel with `is_regex=True` (reusing the strip/empty logic — no duplicate channel). When `is_regex`: `re.compile`-validate up front (on `re.error` return an explicit error, no dispatch); drop `--fixed-strings` from the rg args; everything else (null-delimited parse, line mapping, structural-key drop, ranking) unchanged. Python fallback switches `needle in line` → `compiled.search(line)`. `_build_hits` content-match check switches `needle in m.content.lower()` → `compiled.search(m.content)` so snippet-select stays consistent with what matched. A known-bad pattern never reaches the Python fallback (CD-M-1). Exactly one of `query`/`pattern` non-empty in search mode (both → `tool_error`); both empty = browse.

**D-B — Concept expansion guidance (docstring).**
Extend the `session_search` docstring with a short "expanding intent" block: translate a semantic concept into (1) **structural patterns** via `pattern=` for entities with a shape (codes, IDs, dates, amounts), (2) **synonym sets** fired as multiple literal `query=` calls, (3) **named entities** (carriers, product names). Emphasize multi-angle: a thin result from one angle is not a "no" — try the next angle. **Pattern hygiene (CD-m-2):** matching is per raw-JSONL-line and the snippet re-match is against decoded content — prefer unanchored token/shape patterns; avoid `^`/`$` anchors and literal JSON-escape chars (`\"`, `\\`), which match the raw line but not decoded content and get dropped.

**D-C — Recall cascade (rule).**
Add a "Cross-session recall cascade" paragraph to `07_memory_protocol.md` `## Recall`, each rung naming its escalation cue (PO-m-1):
1. literal keyword query → *if zero or only structural-key hits,* escalate →
2. structural `pattern=` and/or synonym/entity angles (fire several) → *if still no content-bearing hit,* escalate →
3. **honest miss** — say the recall was inconclusive and name what was searched (which keywords/patterns/angles) **and** that the full session history was not exhaustively read; never emit a bare "nothing found" that masks the possibility of no-handle content (FM-4) the search engine cannot reach.

## Tasks

### ✓ DONE TASK-1 — Regex/pattern search path in the engine
**files:** `co_cli/session/_search.py`, `co_cli/session/store.py`
**done_when:** `search_sessions(dir, r'\b[A-Z]{2}\d{2,4}\b', limit=3, is_regex=True)` over a fixtured session containing `AA890` returns a hit with a readable snippet, while `flight` passed as a literal `query` returns nothing; a malformed pattern (e.g. `[unterminated`) returns an explicit error result (not an empty "no results") and never raises or falls through to the Python line-scan.
**success_signal:** new test in `tests/test_flow_session_search.py` exercising regex-hit + literal-miss + malformed-explicit-error passes.
**prerequisites:** none

### ✓ DONE TASK-2 — `session_search` tool: `pattern` param + mutual exclusion + behavioral docstring
**files:** `co_cli/tools/session/recall.py`
**done_when:** `session_search(pattern=r'\b[A-Z]{2}\d{2,4}\b')` invoked through the tool returns the flight-code session as a line-cited hit; `session_search(query="x", pattern="y")` returns a `tool_error` ToolReturn; docstring contains the concept→pattern/synonym/multi-angle guidance plus the pattern-hygiene note (no `^`/`$` anchors, no JSON-escape chars).
**success_signal:** test invoking the tool (RunContext) asserts the regex-mode hit and the both-supplied `tool_error`.
**prerequisites:** TASK-1

### ✓ DONE TASK-3 — Recall cascade guidance in the memory protocol rule
**files:** `co_cli/context/rules/07_memory_protocol.md`
**done_when:** `## Recall` contains a "Cross-session recall cascade" paragraph naming the three ordered rungs (literal → pattern/synonym angles → honest uncertainty) **and the escalation cue for each rung** (zero/structural-only hits → next angle; angles exhausted → honest miss); the honest-miss rung explicitly states what was searched and that the full history was not exhaustively read (so FM-3 is not reopened); the existing "make at most one broader retry, then surface the miss" line is reconciled (not contradicted) with the multi-angle cascade.
**success_signal:** N/A (prompt/rule text).
**prerequisites:** TASK-2 (so the rule references the `pattern=` behavior that now exists)

### ✓ DONE TASK-4 — Concept-expansion eval (behavioral delta)
**files:** `evals/eval_session_continuity.py` (add a scenario; a new `evals/eval_session_recall.py` is acceptable if the scenario doesn't fit the existing file's shape)
**done_when:** an FM-1 scenario exists — a fixtured recent session recording a shaped entity with **no other recallable lexical handle** (e.g. only `AA890 delayed`, never the word "flight"), then a later prompt asking in different words ("what flight did you check last time?"). Pass = the agent reaches the answer via a `pattern=` call after the literal angle misses, having followed the cascade; the engine mechanics are *not* what's asserted here — the behavioral expansion (intent → pattern) is. Uses real on-disk JSONL under a `CO_HOME` temp dir, real LLM, centralized eval settings (`evals/_settings.py` / `_deps.py` / `_timeouts.py`) — no test stores, no inline ModelSettings.
**success_signal:** the eval run reaches the shaped entity through pattern mode (observable recall behavior, not a structural assertion); record under `evals/_outputs/` per eval convention.
**prerequisites:** TASK-2, TASK-3 (the docstring + rule that are supposed to elicit the expansion must both exist)

## Testing

Functional, behavior-only (per testing policy — assert observable recall behavior, never structural field presence beyond the documented hit/preview contract):
- **Regex bridges vocabulary mismatch:** fixtured session with `AA890`; pattern mode finds it, literal `flight` does not. (TASK-1/2)
- **Literal path unchanged:** an existing literal-query test still passes byte-for-byte (regression guard on the default path).
- **Malformed pattern is safe and honest:** invalid regex → explicit error result, no exception, no fallthrough to the Python line-scan. (TASK-1)
- **Mutual exclusion:** both `query` and `pattern` → `tool_error` ToolReturn. (TASK-2)
- Fixtures use real on-disk JSONL session files under a `CO_HOME` temp dir (no test stores). Run `tests/test_flow_session_search.py` piped to a timestamped `.pytest-logs/` file.

The functional tests above verify *engine mechanics only*. The feature's actual value — the agent autonomously expanding intent into a `pattern=`/synonym angle — is verified by the **TASK-4 eval**, not pytest (the expansion is elicited by docstring + rule, with nothing in code forcing it; a passing engine test does not prove the model ever reaches for the path).

## Open Questions

- **OQ-1 — RESOLVED (C1):** distinct `pattern=` tool arg → `is_regex=True` threaded through `store.search`/`search_sessions`, routing the pattern through the existing `query` channel. Explicit, mutually exclusive, no duplicate channel.
- **OQ-2 — `_SESSIONS_CHANNEL_CAP=3`:** left unchanged; multi-angle is repeated calls. Is cap-3 per-call too tight when the agent fires one broad pattern expecting several sessions? Default: keep (surgical), revisit only if a concrete starvation case appears in dev.
- **OQ-3 — Python-fallback regex backtracking:** the up-front `re.compile` catches *invalid* patterns, but a *valid* pathological pattern can still backtrack slowly over raw JSONL lines in the fallback path. Acceptable at co's corpus scale; noted risk, no guard (timeout / length cap) unless a real stall appears.


## Delivery Summary — 2026-06-16

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | engine regex hit + literal miss + malformed-explicit-error, no raise/fallthrough | ✓ pass |
| TASK-2 | tool `pattern=` hit + both-supplied `tool_error` + behavioral/hygiene docstring | ✓ pass |
| TASK-3 | `## Recall` cascade: 3 rungs + per-rung escalation cues; honest-miss reconciled | ✓ pass |
| TASK-4 | FM-1 eval reaches AA890 via `pattern=` after literal miss (real LLM, real JSONL) | ✓ pass |

**Implementation:**
- `_search.py` — new `SessionSearchResult(hits, error)`; `search_sessions(..., *, is_regex=False)` `re.compile`-validates up front (invalid → `error`, no dispatch, no fallthrough), drops `--fixed-strings` in regex mode; Python fallback + `_build_hits` snippet match use the compiled pattern when present. Literal path byte-for-byte unchanged.
- `store.py` — `search(..., *, is_regex=False)` returns `SessionSearchResult`.
- `recall.py` — `session_search(query="", pattern="", limit=3)`; `query`/`pattern` mutually exclusive (`tool_error`); regex routes through the `query` channel with `is_regex=True`; compile error → `tool_error`; docstring gained the concept→pattern/synonym/multi-angle "expanding intent" block + pattern-hygiene note.
- `07_memory_protocol.md` — "Cross-session recall cascade" (literal → pattern/synonym angles → honest miss) reconciling the prior "one broader retry" line.

**Tests:** scoped — 10 passed, 0 failed (`tests/test_flow_session_search.py`: 7 regression + 3 new: engine regex-bridge/literal-miss/malformed-error, tool pattern-mode hit, mutual-exclusion `tool_error`).
**Eval:** `evals/eval_session_recall.py` SR.A — **PASS**, judge 10/10. Observed cascade: literal `flight` miss → `airline` miss → `pattern=\b[A-Z]{2}\d{3,4}\b` → recovered AA890 → correct answer. Recorded under `evals/_outputs/`.
**Doc Sync:** fixed — `sessions.md` (§3 engine/semantics/recall-pipeline, §5 tool + domain-API sigs + `SessionSearchResult`) and `_search.py` module docstring.

**Deviations from plan:**
- TASK-4 done_when wording said "CO_HOME temp dir"; the eval scaffolding (`_deps`/`_settings`) runs against the real CO_HOME workspace by convention (real data, no test stores). The fixture JSONL is seeded into the real `deps.sessions_dir` (under CO_HOME), consistent with the existing `eval_session_continuity` pattern — not a coined temp dir. Per eval policy, the fixture is left in place (no cleanup).

**Overall: DELIVERED**
All four tasks pass; lint clean, scoped tests green, eval green, doc sync done.

## Implementation Review — 2026-06-16

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | engine regex hit + literal miss + malformed explicit-error, no raise/fallthrough | ✓ pass | `_search.py:86-99` — `re.compile`-validate up front; on `re.error` return `SessionSearchResult(error=...)` with no dispatch (no fallthrough to Python scan); `:123-124` drops `--fixed-strings` in regex mode; `:188-192` fallback uses `compiled.search`; `:213-216` `_build_hits` snippet match uses compiled pattern. Literal default path unchanged (`is_regex=False`, `compiled=None`). |
| TASK-2 | tool `pattern=` hit + both-supplied `tool_error` + behavioral/hygiene docstring | ✓ pass | `recall.py:177-181` mutual-exclusion `tool_error`; `:194-198` routes pattern through `query` channel with `is_regex=True`, compile error → `tool_error`; docstring `:151-165` has concept→pattern/synonym/multi-angle block + pattern-hygiene note (no `^`/`$`, no JSON-escape chars). |
| TASK-3 | `## Recall` cascade: 3 rungs + per-rung escalation cues; honest-miss reconciled | ✓ pass | `07_memory_protocol.md:23-38` — three ordered rungs each with escalation cue; rung 3 names what was searched + that history was not read exhaustively (FM-3 not reopened); `:25-26` reconciles the prior "one broader retry" line as the keyword step. |
| TASK-4 | FM-1 eval reaches AA890 via `pattern=` after literal miss (real LLM, real JSONL) | ✓ pass | `evals/eval_session_recall.py` — seeds real JSONL into `deps.sessions_dir`; `_pattern_call_made` + `_entity_recovered` assert the behavioral expansion, not engine mechanics; LLM judge rubric. Parses + imports clean. Delivery run: PASS, judge 10/10. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Working tree contains ~35 files from a **separate in-flight refactor** (google/ + daemons/dream/ renames, bootstrap, display, etc.) outside this plan's `files:` | n/a | staging hygiene (not blocking) | Not introduced by this delivery; flagged for `/ship` staged-file gate — stage ONLY this plan's files (`_search.py`, `store.py`, `recall.py`, `07_memory_protocol.md`, `evals/eval_session_recall.py`, `docs/specs/sessions.md`) |

_No blocking findings. The plan's own files are clean: lint passes, no dead code, no one-sided members, `_search.py` underscore visibility intact (imported only within `co_cli/session/`), `SessionSearchResult` has both producer and consumer, literal default byte-for-byte preserved._

### Tests
- Command: `uv run pytest tests/test_flow_session_search.py -v` (scoped — full suite not run; tree contaminated by unrelated refactor whose own failures would be out of this plan's scope)
- Result: 10 passed, 0 failed (7 regression incl. literal-default guard + 3 new: engine regex-bridge/literal-miss/malformed-error, tool pattern hit, mutual-exclusion `tool_error`)
- New tests are functional (assert snippet content, hits, `error` flag, `tool_error` metadata) — survive the stub-litmus
- Log: `.pytest-logs/*-review-impl-sessionsearch.log`

### Behavioral Verification
- `uv run co --help`: ✓ CLI loads (bootstrap imports the changed tool module without error)
- No CLI command / output-format / config surface changed — this delivery touches a model-facing tool docstring + an injected recall rule. The feature's user-observable behavior (intent → `pattern=` recall) is verified by the SR.A eval, not `co status` (which is not a registered command here).
- `success_signal` (TASK-4): verified per delivery run — observed cascade literal `flight` miss → `pattern=\b[A-Z]{2}\d{3,4}\b` → recovered AA890 → correct answer; judge 10/10.

### Overall: PASS
All four `✓ DONE` tasks confirmed against `done_when` with file:line evidence; scoped tests green, lint clean, no blocking findings. One non-blocking staging-hygiene note: the working tree carries an unrelated refactor — at `/ship`, stage only this plan's six files.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev session-recall-concept-expansion`
