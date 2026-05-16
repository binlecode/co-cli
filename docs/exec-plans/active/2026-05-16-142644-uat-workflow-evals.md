# Plan: UAT Workflow Evals — Full Rebuild

> **Grilling round applied** (after C2 Final): cuts that favor effectiveness over coverage and depth over width — 6 structural-plumbing cases dropped (W3.D, W3.E `stats`, W4.D, W6.B/C/D/F), 3 behavioral upgrades in place (W1.A judge, W1.B tool-identity assertion, W2.D follow-up turn for continuity), latency budget per case (Behavioral Constraint #13), and full `TurnTrace` per `run_turn` plus OTel `trace_id` correlation and artifact diff (Behavioral Constraint #14). Net: 26 cases (down from 32), every case re-playable from disk trace.

## Context

`evals/` was wiped this session. It now contains 6 docstring-only stubs (`eval_daily_chat.py`,
`eval_session_continuity.py`, `eval_memory.py`, `eval_skills.py`, `eval_background.py`,
`eval_trust_visibility.py`) and nothing else — no helpers, no fixtures, no judge, no Ollama
warm-up, no `_outputs/`.

The new eval set is workflow-aligned: each file corresponds 1:1 to a user-facing console
workflow distilled from `docs/specs/` (see `docs/specs/00-mission.md`, `01-system.md`, `tui.md`,
`core-loop.md`, `skills.md`, `memory.md`, `sessions.md`, `compaction.md`, `dream.md`,
`observability.md`, `tools.md`).

### Why a full rebuild

The pre-wipe set (eval_basic_chat, eval_approval_flow, eval_canon_recall,
eval_compaction_multi_cycle, eval_compaction_proactive, eval_memory_write_recall,
eval_session_restore, eval_web_research) was organized by **subsystem under test**, not by
**user-visible workflow**. That orientation produces:
- Two evals on compaction internals, none on `/compact` from the REPL.
- No coverage of `/background` / `/tasks` / `/cancel` (a whole slash family).
- No coverage of `/skills` dispatch beyond a planning-time eval that lived as a separate file.
- `/reasoning`, `/approvals`, `/tools`, `/history` — no end-to-end exercise.
- Memory write/recall but no `/memory` slash surface (forget, dream, decay-review, restore).

The new orientation organizes evals by what a user *does* in the terminal. Subsystem
correctness still gets exercised — through the workflow that depends on it.

### Constraints from memory & repo policy

These are hard constraints, not preferences:

1. **Evals are UAT smoke runs against the real system** (`feedback_eval_real_world_data.md`,
   `agent_docs/testing.md`):
   - Real `~/.co-cli/` paths — real SQLite, real knowledge dir, real sessions dir, real
     `tool-results/` spill dir. No `tempfile.TemporaryDirectory`, no `CO_HOME` override.
   - Real LLM via real model handle from production config — never inline model/temperature
     overrides.
   - Real artifacts left behind by design. No cleanup, no `test-` prefix namespacing.
   - Every eval includes at least one **failure mode / degradation path / boundary condition**.

2. **`ensure_ollama_warm` must run outside any `asyncio.timeout`** — infrastructure prep, not
   behavior under test (`feedback_ensure_ollama_warm.md`,
   `feedback_call_timeout_no_cold_start.md`).

3. **Per-call timeouts cover warm-model latency only**. Never fold cold model load into the
   call budget. Watch LLM call durations closely; never bump timeouts to paper over a stall
   (`feedback_llm_call_timing.md`).

4. **Coverage by critical functionality, not by test-count target**
   (`feedback_no_test_count_rule.md`).

5. **No mocks/stubs/monkeypatch anywhere in `evals/`** — production code paths only.

6. **All temp files used for fixture seeding go in `tmp/` at the project root** (global rule).

7. **`__init__.py` is docstring-only** — eval helpers in `evals/_*.py`, no re-exports.

### Pre-existing patterns to draw from (now removed)

The wiped helpers were doing real work — the rebuild restores them with workflow alignment,
not the prior subsystem alignment. From git history (`git log -- evals/`):
- `_deps.py` — eval-side `make_eval_deps()` wrapper around `create_deps()`.
- `_fixtures.py` — fetched-article fixtures (web research; obsolete for new set).
- `_judge.py` — `judge_with_llm(rubric, transcript)` LLM rubric grader.
- `_observability.py` — JSONL eval-run telemetry under `evals/_outputs/`.
- `_ollama.py` — `ensure_ollama_warm()` with stale-timeout retry.
- `_timeouts.py` — per-call budget constants distinct from `tests/_timeouts.py`.
- `_tools.py` — minimal tool registration helpers for synthetic scenarios.

The rebuild keeps the **shape** (helpers under `evals/_*.py`, REPORT files in `docs/`) but
revisits **which** helpers are necessary for the new set.

---

## Problem & Outcome

**Problem.** No working eval coverage. The 6 stub files import nothing and exercise nothing —
running `uv run python evals/eval_*.py` prints nothing useful. Ship cycles have lost their
UAT smoke layer. Regressions in any of the 6 user workflows would only be caught by manual
chat-loop exercise.

**Failure cost.** Without these evals, the next ship can break any of:
- A daily REPL turn silently corrupting session JSONL.
- `/resume` failing to rehydrate todos/plan after a real prior session.
- `knowledge_manage` write that doesn't update the FTS index — recall returns 0 hits.
- A skill dispatch that runs the body without injecting `skill_env`, leaving env vars
  globally polluted on the next turn.
- `/background <cmd>` orphaning a task that survives REPL exit.
- `/approvals` rules silently dropped on session rotate.

These are classes of regression that have surfaced during development of the relevant
subsystems; the eval layer is the fastest way to surface them before commit.

**Outcome.** Six runnable `evals/eval_<workflow>.py` scripts, each:
1. Bootstraps a real `CoDeps` via `create_deps()` against the real `~/.co-cli/` workspace.
2. Drives the workflow's user-facing path end-to-end (slash dispatch, tool surface, or
   `run_turn` as appropriate).
3. Verifies observable outcomes: returned values, persisted files, FTS index entries,
   emitted telemetry, side effects on `deps.session` / `deps.runtime`.
4. Includes at least one failure-mode / degradation case.
5. Writes a permanent `docs/REPORT-eval-<workflow>.md` with a dated `## Run <ISO8601>`
   section prepended on every run.
6. Exits 0 on PASS, non-zero on any sub-case fail; sub-case verdicts logged immediately and
   the script continues past a failure to the next case (per the pattern in the prior
   eval-agentic-coverage plan).

---

## Scope

**In scope** — new files under `evals/`:

| File | Workflow | Specs |
|---|---|---|
| `evals/eval_daily_chat.py` | W1: default `co chat` turn | core-loop, prompt-assembly, dream |
| `evals/eval_session_continuity.py` | W2: `/new` `/clear` `/sessions` `/resume` `/compact` | sessions, compaction, self-planning |
| `evals/eval_memory.py` | W3: `knowledge_*` + `/memory` | memory, knowledge, dream |
| `evals/eval_skills.py` | W4: `/<skill>` dispatch + `skill_manage` + reviewer/curator | skills, tui |
| `evals/eval_background.py` | W5: `/background` `/tasks` `/cancel` `/history` | tui (slash ref) |
| `evals/eval_trust_visibility.py` | W6: `/approvals` `/reasoning` `/tools` | tui, observability, tools |

**In scope** — shared infrastructure under `evals/`:

| File | Why it must exist |
|---|---|
| `evals/_deps.py` | Real `CoDeps` builder — calls `create_deps(frontend, stack)` with the real settings. No `CO_HOME` overrides. |
| `evals/_ollama.py` | `ensure_ollama_warm()` — called outside `asyncio.timeout`. |
| `evals/_timeouts.py` | Re-exports `tests/_timeouts.py` constants for shared model-latency budgets, plus eval-only longer-budget constants where evals legitimately need them (`DREAM_CYCLE_BUDGET_S`, `MULTI_TURN_COMPACT_BUDGET_S`). No value duplication. Module top must carry a one-line comment justifying the cross-package import: "warm-LLM-latency constants are single-source-of-truth for both `tests/` and `evals/`." |
| `evals/_observability.py` | Per-run JSONL writer at `evals/_outputs/<eval>-<ts>/run.jsonl` capturing per-case `CaseResult` rows (verdict, duration, **model-call seconds, token usage, OTel `trace_id`, list of trace files**). Owns the `TurnTrace` writer — see `### Trace capture` in High-Level Design. |
| `evals/_judge.py` | LLM judge — invoked by **W1.A** (response on-topic + voice) and **W4.A** (skill body adherence). All other workflows use deterministic asserts. |
| `evals/_trace.py` | (new) `TurnTrace(prompt_snapshot, message_history, tool_calls, thinking, token_usage, model_call_seconds, span_ids)`; `capture_artifact_diff(paths_before, paths_after) → str`. Persisted under `evals/_outputs/<eval>-<ts>/case_<id>.jsonl` (one line per `run_turn`) + `…/case_<id>-artifact-diff.txt`. |
| `evals/_report.py` | Common report-prepender — emits the dated `## Run <ISO8601>` block to `docs/REPORT-eval-<workflow>.md`, **with header table (case verdict / duration / model-call seconds / token usage), top-3 slow operations, regression-vs-prior-run diff, and trace-file links per case**. |

**Permanent docs created at first run** (not pre-created):
- `docs/REPORT-eval-daily-chat.md`
- `docs/REPORT-eval-session-continuity.md`
- `docs/REPORT-eval-memory.md`
- `docs/REPORT-eval-skills.md`
- `docs/REPORT-eval-background.md`
- `docs/REPORT-eval-trust-visibility.md`

**Explicitly out of scope:**
- Pytest integration. Evals are standalone programs (`uv run python evals/eval_<name>.py`).
  Nothing in `evals/` runs under `pytest`.
- Production code changes. If an eval can't exercise a workflow because the production API
  is wrong, that's a separate plan — flag as Open Question, don't fix in this round.
- Any rewrite of `tests/`. Test suite is governed by the clean-tests skill and lives in
  `tests/`. Evals and tests are non-overlapping.
- A "run all evals" wrapper script — six explicit entry points are clearer than aggregation.
- Restoring `evals/_fixtures.py` or `evals/_tools.py` — both were tied to evals that no
  longer exist. New evals build their fixtures inline; new helpers grow only on second use.
- `co traces` / `co tail` as runnable eval surfaces — these are read-only viewers over OTel
  spans persisted by the system. W6 asserts spans are persisted; viewer rendering is not
  exercised.

---

## Behavioral Constraints

These bind every eval file:

1. **Real `~/.co-cli/` workspace.** `make_eval_deps()` calls `create_deps(frontend, stack)`
   with production settings. No temp dirs, no env overrides. Eval artifacts persist by
   design.
2. **Real LLM.** Model handle comes from `deps.model`. Never construct a separate model.
   Never pass `model=` or `model_settings=` to `agent.run()`.
3. **`ensure_ollama_warm()` outside `asyncio.timeout`.** Always called as the first step
   inside `main()` before the timed section starts.
4. **Per-call `asyncio.timeout(N)` wraps each external `await`.** N comes from
   `evals/_timeouts.py` constants. Never inline a numeric timeout. Never bump a constant
   to make an eval green — diagnose the slow call.
5. **No `max_turns=1`** — realistic budgets (5–15 per turn).
6. **No `monkeypatch`/`mock`/`patch`.** If a workflow needs a fixture (e.g. a pre-seeded
   skill file), the eval writes the real file to the real skill dir, runs against it, and
   leaves it there.
7. **Output discipline:** every eval prints `[<eval>] <case>: PASS|FAIL — <reason>` lines
   to stdout, prepends a dated section to its REPORT, and writes a JSONL run record to
   `evals/_outputs/`.
8. **Sub-case failure does not abort the run** — each case captures its verdict and the
   script continues. The script exits non-zero iff any case failed.
9. **Failure-mode coverage is mandatory.** Every eval has ≥ 1 case that exercises a
   boundary, degradation, or error path — not just happy-path success.
10. **Hot, not cold.** Each eval is a single-process run. The warm model handle and warm
    `CoDeps` are shared across all sub-cases. No per-case bootstrap.
11. **Two SKIPPED categories — distinct, not interchangeable.**
    - **`SKIPPED:mcp`** — case logged a `[skip]` because an MCP server is unreachable
      (`deps.degradations` carries the entry); sub-case guards on `tool_name in
      deps.tool_index` before running. Per `agent_docs/testing.md` eval rule "skip
      gracefully if prerequisite missing."
    - **`SKIPPED:product-gap`** — case logged a `[skip]` because the production surface
      it would test does not exist or is package-private (e.g. the dropped W6.E
      observability reader). These should be tracked as Open Questions and resolved by
      a follow-up plan, not silently re-skipped on every run.
    Both record `SKIPPED` rather than PASS/FAIL; the run continues; the script exit code
    is unaffected by SKIPs.
12. **Deterministic artifact names.** Eval-seeded artifacts use fixed names — `eval_smoke`,
    `eval_W3_fact_A`, `eval_W3_fact_B`, `tmp/eval_bg.out` — so re-runs overwrite in place
    instead of accumulating. The "real artifacts left behind" policy must not become
    "real artifacts pile up over months of re-runs."
13. **Per-case latency budget.** Every `CaseResult` records `model_call_seconds` and
    asserts against a per-case budget (from `_timeouts.py`). Budget failure = case FAIL
    with reason `[slow] N.Ns vs budget M.Ms`. This catches model-regression slowdowns
    the absolute `asyncio.timeout` doesn't (timeout fires above the ceiling, not against
    expected baseline). Per `feedback_llm_call_timing.md` — *watch durations closely*.
14. **Full trace persisted for every `run_turn`.** `_observability.py` always writes a
    `TurnTrace` per turn driven by an eval: assembled-prompt snapshot, full message
    history, tool calls + args + returns, thinking stream, token usage, model latency,
    OTel `span_ids`. Trace lives at `evals/_outputs/<eval>-<ts>/case_<id>.jsonl`. This
    is the depth-over-width pivot — fewer cases, but every case is deeply re-playable
    for UAT review and debugging.

---

## High-Level Design

### Module shape

```
evals/
├── _deps.py            # make_eval_deps(), make_eval_frontend()
├── _ollama.py          # ensure_ollama_warm()
├── _timeouts.py        # CALL_TIMEOUT_S, COMPACTION_TIMEOUT_S, DREAM_TIMEOUT_S, ...
├── _observability.py   # EvalRun context manager → JSONL under _outputs/
├── _judge.py           # judge_with_llm(rubric_md, transcript) → JudgeVerdict
├── _report.py          # prepend_report(report_path, eval_name, run_iso, cases)
├── _outputs/           # created on first run; JSONL per-run records
├── eval_daily_chat.py
├── eval_session_continuity.py
├── eval_memory.py
├── eval_skills.py
├── eval_background.py
└── eval_trust_visibility.py
```

### `make_eval_deps()` contract (`evals/_deps.py`)

```python
async def make_eval_deps() -> tuple[CoDeps, Agent[CoDeps, Any], Frontend]:
    """
    Production bootstrap against ~/.co-cli/. No overrides.
    Returns (deps, agent, frontend) so each eval can drive run_turn() and slash dispatch.
    """
```

- Calls `create_deps(frontend, stack)` exactly as `main.py` does.
- Calls `build_agent(...)` exactly as `main.py` does.
- **Approval bypass via session state, not frontend override.** After `create_deps()`
  returns, the helper inserts an allow-all rule into `deps.session.session_approval_rules`
  so the agent never reaches an approval prompt in the first place. This uses the
  production approval-rule mechanism — no mock, no fake — and isolates the W6 case that
  exercises `/approvals` to drive that path separately.
- **`EvalFrontend` non-interactive adapter** — concrete subclass of `TerminalFrontend`
  that overrides ONLY `prompt_question(question)` → returns `question.options[0]`
  (defensive; should be unreached now that approvals are pre-resolved via
  `session_approval_rules`). Rendering surfaces (`on_status`, `clear_status`, `cleanup`)
  inherit from `TerminalFrontend` unchanged. This is a production-protocol-compliant
  frontend implementation, not a mock; the override returns a value from the real
  `question.options` list computed by the production code.
- **`prompt_selection` is module-level, not a frontend method.** Verified at
  `co_cli/display/core.py:131` and called directly by `_cmd_resume`
  (`co_cli/commands/resume.py:64`). Eval frontends cannot intercept it via subclassing.
  W2.D therefore exercises the rehydration mechanism via the production helpers
  (`load_transcript` + `_rehydrate_todos`) rather than the slash surface. A follow-up
  plan should refactor `_cmd_resume` to call `ctx.deps.frontend.prompt_selection(...)`
  so a future eval can drive `/resume` end-to-end.

### Trace capture (`evals/_trace.py` + `_observability.py`)

The harness's debugging value depends on full per-turn trace. Every `run_turn` driven by
an eval is wrapped with a context manager that captures:

```python
@dataclass
class TurnTrace:
    case_id: str                      # e.g. "W1.B"
    turn_index: int                   # 0..N within the case
    prompt_snapshot: dict             # {static_hash, dynamic_hash, recall_block_text}
    user_input: str
    assistant_text: str
    tool_calls: list[ToolCallRecord]  # name, args (truncated), return (truncated or hash + spill path)
    thinking: str | None              # captured iff reasoning_display == "full"
    token_usage: dict                 # {prompt, completion, total}
    model_call_seconds: float
    span_ids: list[str]               # OTel span IDs emitted during this turn
```

Trace I/O:
- One line per `TurnTrace` appended to `evals/_outputs/<eval>-<ts>/case_<case_id>.jsonl`.
- Long fields (`assistant_text`, tool returns) truncated to 4 KB inline + sidecar
  reference if longer; the spill convention from `tool_results_dir` is reused so trace
  size stays bounded.
- Captured *always* (verbose flag is for `thinking` only — opt-in via
  `EVAL_VERBOSE_TRACE=1` env, or auto-enabled on case FAIL).

OTel correlation:
- Each `CaseResult` in `run.jsonl` carries `trace_id` (the shared OTel trace ID emitted
  during that case) and a list `trace_files` pointing at the case's `case_<id>.jsonl`.
- REPORT renders each case as `[trace](_outputs/<eval>-<ts>/case_<id>.jsonl) ·
  [otel](co tail --trace <trace_id>)` so a reviewer can pivot from the human REPORT
  straight to either layer.

Artifact diff:
- Cases that mutate `~/.co-cli/knowledge/`, `~/.co-cli/skills/`, or the active session
  JSONL wrap their work in `capture_artifact_diff(paths_before, paths_after)`.
- Diff (file added / removed / size+mtime change) goes to
  `…/case_<id>-artifact-diff.txt`. Cheap (`os.scandir` walk), high debugging value.

### `judge_with_llm()` contract (`evals/_judge.py`)

```python
async def judge_with_llm(
    rubric_md: str,
    transcript: list[Any],
    *,
    model: LlmModel,
) -> JudgeVerdict:
    """Returns JudgeVerdict(passed: bool, score: int, rationale: str)."""
```

- Field name is `passed`, not `pass` (Python keyword).
- Used by **W1.A** (one rubric question: "did the response engage with the prompt
  on-topic, in the agent's voice?") and **W4.A** (skill body adherence). Every other
  case asserts on observable structural outcomes — not LLM-judged prose quality.
- **Judge model pinned distinct from agent under test.** The judge handle is constructed
  with the production config but uses the **`settings.llm.judge_model` override** (a new
  optional setting that defaults to `settings.llm.model` if unset). Pin in `~/.co-cli/`
  config to a different local model so a regression in the agent doesn't simultaneously
  regress the judge. Open Question §5 tracks the config-side change if not yet present.

### Case structure inside an eval

```python
async def main() -> int:
    await ensure_ollama_warm()
    deps, agent, frontend = await make_eval_deps()
    cases: list[CaseResult] = []

    async with EvalRun("daily_chat") as run:
        cases.append(await case_w1_happy_path(deps, agent, frontend))
        cases.append(await case_w1_with_tool_call(deps, agent, frontend))
        cases.append(await case_w1_recall_injection(deps, agent, frontend))
        cases.append(await case_w1_failure_mode_disabled_recall(deps, agent, frontend))

    run.persist(cases)
    prepend_report("docs/REPORT-eval-daily-chat.md", "daily_chat", run.iso, cases)
    return 0 if all(c.passed for c in cases) else 1
```

### Workflow-by-workflow scenario design

#### W1 — `eval_daily_chat.py`

End-to-end exercise of `run_turn()` from a real user prompt through agent run, tool loop,
session persist, and (optionally) dream trigger.

| Case | What it does | PASS criteria | Regression caught |
|---|---|---|---|
| W1.A `happy_path_qualified_response` | Single `run_turn(user_input="hi, summarize my last session")`; capture full `TurnTrace`; call `judge_with_llm` with rubric: "did the response engage with the prompt on-topic, in the agent's voice (per soul seed)?" | `TurnResult.outcome == "continue"`; `judge.passed == True`; session JSONL grew by ≥ 2; `model_call_seconds <= W1_TURN_BUDGET_S` | Agent returns plausible-but-wrong / off-topic / voiceless text — the most damaging quiet regression |
| W1.B `tool_choice_quality` | Prompt: "list files in the current directory" — clearly answerable with `file_find` | At least one `ToolReturnPart` with `tool_name == "file_find"` (asserted by **name**, not just count); `shell_exec` invoking `ls` is an accepted fallback (case PASSes with reason `[shell_fallback]` rather than the canonical choice); response references the returned listing; latency within budget | Tool-choice regression (agent picks an unrelated tool, hallucinates a listing without calling any tool, or burns multiple tool turns when one is enough) |
| W1.C `recall_used_in_response` | Pre-seed knowledge artifact `eval_W1_seed` with a **short body (<100 chars)** that **opens with** a fixed distinctive token (e.g. `MNEMONIC_TOKEN_42`), so the recall snippet (capped at 100 chars per `co_cli/tools/memory/recall.py:37`) actually contains it; prompt that references the artifact's topic without quoting the token | Assembled prompt contains the recall block referencing `eval_W1_seed`; **AND** assistant response contains `MNEMONIC_TOKEN_42` verbatim — recall *used*, not just injected | Recall injected and ignored — silent recall regression |
| W1.D `dream_callable_smoke` (**failure mode / boundary**) | After turns A–C, call `run_dream_cycle(deps, miner_tool=knowledge_manage, dry_run=True, timeout_secs=DREAM_CYCLE_BUDGET_S)` | Returns without exception; `deps.resource_locks` has no dream-cycle entries after return; `dry_run=True` so no knowledge-dir mutation | Dream cycle raises on a real-world session / leaves dangling locks |

#### W2 — `eval_session_continuity.py`

| Case | What it does | PASS criteria | Regression caught |
|---|---|---|---|
| W2.A `new_rotates_session_id` | Drive `/new` via `dispatch()`; capture pre/post `deps.session.session_id` and JSONL filename | New `session_id`, new JSONL file appears on disk, prior file still exists | `/new` reuses the same session id / clobbers prior JSONL |
| W2.B `clear_resets_history` | Drive `/clear` via `dispatch()`; assert returned `SlashOutcome` is `ReplaceTranscript` with `history == []` (`dispatch()` wraps `_cmd_clear`'s bare `list[]` return per `co_cli/commands/core.py`); assert on-disk session JSONL is **unchanged** | `/clear` corrupting session JSONL / losing prior turns |
| W2.C `sessions_lists_real_sessions` | Drive `/sessions`; assert output enumerates at least the session created by W2.A | `/sessions` failing to discover real session files |
| W2.D `resume_provides_continuity` | Drive a turn that establishes a fact (e.g. "remember that my deploy id is DEPLOY_77") AND writes a todo via `todo_write`; rotate session with `/new`; call production `load_transcript(path)` against the prior JSONL → `prior_messages`; call `_rehydrate_todos(prior_messages)`; drive a follow-up turn passing `message_history=prior_messages` so the agent has the prior conversation in context (mirrors `main.py:337`'s `/resume` swap-in): "what is my deploy id?" | Todos non-empty after rehydrate; **follow-up response contains `DEPLOY_77`** (continuity-as-user-value, not just state plumbing) | Resume is structurally complete but the agent acts amnesic — user value lost |
| W2.E `compact_replaces_with_summary` (**boundary**) | Drive N synthetic turns to inflate history past `compaction_ratio`; call `/compact`; assert returned history is shorter AND contains a summary marker | `/compact` never triggers / produces empty summary |
| W2.F `compact_idempotent_user_visible` (**failure mode**) | Run W2.E, then immediately drive `/compact` a second time; assert second call returns **the same history reference / equivalent length / no new summary marker** observable to the user — i.e. the user sees no further compression | `/compact` thrashes the history on repeated calls |

#### W3 — `eval_memory.py`

**Case ordering is a hard contract** — W3.A's seeded artifact (deterministic name
`eval_W3_fact`) is read by W3.B/W3.D/W3.E and removed by W3.F before W3.G runs. W3.G uses
its own deterministic pair (`eval_W3_dupA`, `eval_W3_dupB`). Reruns overwrite in place;
no accumulation.

| Case | What it does | PASS criteria | Regression caught |
|---|---|---|---|
| W3.A `agent_chooses_to_save` | Prompt: "I want you to remember that my staging deploy id is `STG_DEPLOY_42`." — a clearly-durable fact the agent should save; capture `TurnTrace` to see the tool choice | At least one `ToolCallPart` with `tool_name == "knowledge_manage"` and `action="add"`; artifact file appears under `~/.co-cli/knowledge/` AND `chunks_fts` row count grew; artifact body contains `STG_DEPLOY_42` | Agent declines to save when it should / saves the wrong content |
| W3.B `recall_ranks_correct_artifact` | Search via `knowledge_search` with a phrase distinctive to W3.A | The W3.A artifact is the **#1 hit**, not merely *some* hit; line-cited snippet contains `STG_DEPLOY_42` | Recall returns hits but ranks the right artifact below junk |
| W3.C `session_search_finds_prior_turn` | Call `session_search` with a phrase from W3.A's prompt | ≥ 1 hit referencing the current session JSONL | Session indexing skipped at write time |
| W3.D `/memory list` | Drive `/memory list` via `dispatch()` | Output enumerates the W3.A artifact's stem | `/memory list` hides real artifacts |
| W3.E `/memory forget` (**boundary**) | Drive `/memory forget <stem>`; subsequent `knowledge_search` of same phrase | Artifact removed from disk AND `chunks_fts` row removed AND search returns 0 hits | `forget` deletes file but leaves stale FTS row (or vice versa) |
| W3.F `dream_decay_preserves_content` (**failure mode**) | Pre-seed `eval_W3_dupA` and `eval_W3_dupB` with near-identical bodies but **each carrying one unique distinctive token** (`TOKEN_A_ONLY`, `TOKEN_B_ONLY`); call `run_dream_cycle(deps, miner_tool=knowledge_manage, dry_run=False, timeout_secs=DREAM_CYCLE_BUDGET_S)` | Exactly one of the pair remains; the other moved to archive; **surviving body contains both tokens** = PASS. If exactly one token missing = `SOFT_FAIL` (LLM-merge may drop rare tokens — degradation, not regression); if both missing = FAIL | Dedup keeps one and silently drops content from the other (the FAIL case); LLM-merge quality degradation (SOFT_FAIL signal for review, not gate failure) |

#### W4 — `eval_skills.py`

Deterministic skill name `eval_smoke` is written to `~/.co-cli/skills/eval_smoke.md` and
left in place by design. W4.A is the only case that uses the LLM judge — body adherence
is not structurally checkable.

| Case | What it does | PASS criteria | Regression caught |
|---|---|---|---|
| W4.A `dispatch_user_skill` | Write `~/.co-cli/skills/eval_smoke.md` with `skill_env: {CO_EVAL_TOKEN: "EVALTOKEN_<rand>"}` and a multi-step body (1: repeat the env value; 2: also report `$ARGUMENTS`); `refresh_skills(deps)`; `dispatch("/eval_smoke evaluating_arg1", ctx)`; apply `skill_env` to `os.environ`; run the delegated turn | Response contains the literal token AND the literal argument `evaluating_arg1`; `judge_with_llm` rubric "did the response follow each numbered instruction in the skill body?" returns `passed=True` | Skill body never reaches the agent / `skill_env` not threaded / multi-step adherence regression |
| W4.B `env_restored_after_dispatch` (**boundary**) | After W4.A, call `cleanup_skill_run_state(...)`; assert `CO_EVAL_TOKEN` is no longer in `os.environ` AND `deps.runtime.active_skill_name is None` | env vars leak across turns / skill state pinned globally |
| W4.C `skill_manage_create_edit_delete` | Drive `skill_manage(action="create"|"patch"|"delete")` against a fresh deterministic skill `eval_W4_lifecycle`; assert disk state after each step | `skill_manage` partial-write / phantom-delete |
| W4.D `builtin_shadowing_blocked` (**failure mode**) | Attempt `skill_manage(action="create", name="help", ...)`; AND if write succeeds, call `refresh_skills(deps)` and dispatch `/help`; assert the **built-in** `/help` handler still runs (not the user file) | A user skill silently overrides a built-in slash command |

#### W5 — `eval_background.py`

**Scope note.** `/history` is **not** exercised in W5 — `co_cli/commands/history.py`
whitelists only `web_research`, `knowledge_analyze`, `reason`, `task_start` as delegation
sources; background launches are not listed there. If "history shows background launches"
becomes a product requirement, that is a separate plan.

| Case | What it does | PASS criteria | Regression caught |
|---|---|---|---|
| W5.A `background_command_runs` | Drive `/background sleep 0.1 && echo done > tmp/eval_bg.out`; assert task appears in `deps.session.background_tasks`; poll until completion | Task in registry with running status, then completed status; file `tmp/eval_bg.out` contains `done` | `/background` runs the command in-foreground / loses task handle |
| W5.B `tasks_lists_running` | Start a `sleep 5` background task; drive `/tasks` while running | Output includes the task's 12-hex id and a running indicator; `/tasks <id>` detail view returns the command line | `/tasks` shows zero tasks while one is running |
| W5.C `cancel_kills_task` (**boundary**) | Start a `sleep 30` task, capture its `pid`, drive `/cancel <id>` | Task removed from `deps.session.background_tasks` within 2s; `os.kill(pid, 0)` raises `ProcessLookupError` (no orphan subprocess; uses stdlib only — no `psutil` dep) | `/cancel` marks task done in registry but orphans the subprocess |
| W5.D `output_capture_truncation` (**failure mode**) | Run a background command that emits > spill threshold (`yes | head -c 500000`); inspect the captured output for the task | Output is spilled to `tool-results/` and the in-memory record holds a placeholder, not the full blob | Spill threshold not enforced for background output |

#### W6 — `eval_trust_visibility.py`

W6 is intentionally narrow after the grilling cut. Only `/approvals` (W6.A) and the
unknown-slash safety boundary (W6.B) remain. `/approvals`'s case clears
`session_approval_rules` at entry, exercises list+clear, then **re-installs the
allow-all rule before returning** so anything that follows runs under normal approval
discipline.

| Case | What it does | PASS criteria | Regression caught |
|---|---|---|---|
| W6.A `approvals_list_clear` | Clear `deps.session.session_approval_rules`; insert one known entry via the production approval path; drive `/approvals list` → assert it lists the entry; `/approvals clear` → assert list is empty | `session_approval_rules` rule add/list/clear breaks |
| W6.B `unknown_slash_local_only` (**boundary**) | Drive `/this_is_not_a_command` via `dispatch()`; snapshot `deps.runtime.turn_usage` before and after | Returned `LocalOnly()`; turn_usage unchanged (no LLM call happened) | Unknown slash reaches the LLM and burns tokens |

**Cuts.** The original W6.B `reasoning_cycle`, W6.C `reasoning_inherited_by_fork`,
W6.D `tools_list_contains_known_subset`, W6.E `observability_visible_via_tail`, and
W6.F `help_lists_builtins` were dropped in the grilling round — internal-state
plumbing checks that don't defend a user-visible behavior gap. Reasoning-display mode
and tool registration are exercised transitively by every other workflow that drives a
turn; their direct slash-command surface is one line of trivial dispatch.

**Observability surface deferred.** The original W6.E (`observability_visible_via_tail`)
is dropped from this plan: `co_cli/observability/tail.py`'s row-fetch helpers
(`_query_recent`, `_query_new`) carry the leading-underscore package-private contract
(`agent_docs/code-conventions.md`'s `_prefix.py` rule), and reading the OTel SQLite
schema directly violates PO-M-1's user-visible-surface principle. A follow-up plan
should either (a) promote a public observability reader, or (b) ship a dedicated
`eval_observability.py` that owns the schema-coupling risk in one place. Tracked as a
new entry in Open Questions §2.

---

## Tasks

Task ordering: helpers first (TASK-1, TASK-2), then evals. **Dependency graph:**

```
TASK-1 (deps + ollama + timeouts)
  └─ TASK-2 (observability + trace + report)
       ├─ TASK-3 (judge)
       │    ├─ TASK-4 (W1 daily chat)         ← grilling restored judge for W1.A
       │    └─ TASK-7 (W4 skills)
       ├─ TASK-5 (W2 session continuity)
       ├─ TASK-6 (W3 memory)
       ├─ TASK-8 (W5 background)
       └─ TASK-9 (W6 trust/visibility)
```

TASK-5/6/8/9 can be parallelized once TASK-1 and TASK-2 land. TASK-4 and TASK-7 wait on
TASK-3 (judge). TASK-2 now also owns trace capture, so it is heavier than originally
scoped.

**Wall-time guidance.** After the grilling cuts: case counts W1=4, W2=6, W3=6, W4=4,
W5=4, W6=2 (**26 total**). Most W2/W5/W6 cases are dispatch-only. LLM-call estimate
~25–40 (W1 has 1 judge call, W2.D has 2 turns + 1 follow-up, W3.A drives a tool turn,
W4.A drives a dispatched turn + judge, W2.E inflation turns). ~5–10 minutes on warm
local 35B at ~25 tok/s. Per-case latency budgets enforced via Behavioral Constraint
#13; per-eval wall budgets are noted below. Bumping budgets without diagnosing the
slow call is a violation (`feedback_llm_call_timing.md`).

### TASK-1 — Shared deps + warm-up + timeouts helpers

**files:**
- `evals/_deps.py` (new) — `make_eval_deps()` + `EvalFrontend` (subclass of `TerminalFrontend` overriding `prompt_question` and `prompt_selection` to non-interactive deterministic returns); inserts allow-all `session_approval_rules` entry after `create_deps()` returns.
- `evals/_ollama.py` (new) — `ensure_ollama_warm()` with stale-timeout retry. Called outside `asyncio.timeout`.
- `evals/_timeouts.py` (new) — re-exports `tests/_timeouts.py` constants; defines `DREAM_CYCLE_BUDGET_S` and `MULTI_TURN_COMPACT_BUDGET_S` for eval-only longer budgets.

**done_when:** `uv run python -c "import asyncio; from evals._deps import make_eval_deps, EvalFrontend; from evals._ollama import ensure_ollama_warm; from evals._timeouts import DREAM_CYCLE_BUDGET_S; asyncio.run(ensure_ollama_warm()); deps, agent, fe = asyncio.run(make_eval_deps()); assert deps.model is not None; assert isinstance(fe, EvalFrontend); print('ok')"` exits 0 and prints `ok`.

**success_signal:** Any eval script can call `await make_eval_deps()` after `await ensure_ollama_warm()` and obtain a real production `CoDeps` bound to `~/.co-cli/` with approvals pre-resolved.

**wall budget:** < 60s after Ollama is warm.

**prerequisites:** none.

### TASK-2 — Observability + Trace + REPORT helpers

**files:**
- `evals/_observability.py` (new) — `EvalRun(name)` async context manager + `CaseResult` dataclass with `passed: bool, name: str, duration_s: float, model_call_seconds: float, token_usage: dict, trace_id: str | None, trace_files: list[str], reason: str`.
- `evals/_trace.py` (new) — `TurnTrace` dataclass + `record_turn(case_id, turn_index, ...)` writer + `capture_artifact_diff(paths_before, paths_after)`.
- `evals/_report.py` (new) — `prepend_report(path, eval_name, run_iso, cases, prior_section=None)`; emits header table (verdict / duration / model_call_seconds / tokens), top-3 slow operations, regression-vs-prior diff, per-case trace-file links.
- `evals/_outputs/<eval>-<ts>/` (per-run directory, created on first run).

**done_when:** A throwaway script under `tmp/` opens `EvalRun("smoke")`, drives a real one-shot turn, records `TurnTrace` + `CaseResult`, calls `prepend_report("tmp/eval-smoke.md", ...)`. Verifies: (a) `evals/_outputs/smoke-<ts>/run.jsonl` has the case row with non-empty `trace_id` and `trace_files`; (b) `evals/_outputs/smoke-<ts>/case_smoke.jsonl` has the `TurnTrace` line with prompt_snapshot + tool_calls + token_usage populated; (c) `tmp/eval-smoke.md` opens with a dated `## Run <iso>` section that includes the case in its header table and links to the trace file.

**success_signal:** A reviewer reading a REPORT row can click straight to the trace file and replay why a case passed/failed step by step.

**wall budget:** < 30s (one real turn for the trace fixture).

**prerequisites:** TASK-1.

### TASK-3 — LLM judge helper

**files:**
- `evals/_judge.py` (new) — `judge_with_llm(rubric_md, transcript, *, model) -> JudgeVerdict(passed: bool, score: int, rationale: str)`. Field is `passed`, not `pass` (keyword).

**done_when:** A throwaway script under `tmp/` that builds a small transcript + 3-question rubric and awaits `judge_with_llm(...)` returns a `JudgeVerdict` with `passed` being `bool` and `rationale` being non-empty. Wrapped in `asyncio.timeout(LLM_REASONING_TIMEOUT_SECS)`.

**success_signal:** W1.A and W4.A can score response quality via a rubric.

**wall budget:** < 90s (one LLM call, warm model).

**prerequisites:** TASK-1. TASK-4 and TASK-7 both depend on this (post-grilling W1.A now uses the judge as well).

### TASK-4 — `evals/eval_daily_chat.py` (W1)

**files:**
- `evals/eval_daily_chat.py`
- `docs/REPORT-eval-daily-chat.md` (created on first run)

**done_when:** `uv run python evals/eval_daily_chat.py` exits 0 with W1.A–W1.D PASS; REPORT contains a `## Run <iso>` section at the top.

**success_signal:** A user typing a prompt at the `co` REPL gets a qualified, on-topic, on-voice response; the agent picks the right tool when one is needed; recall is *used*, not just injected; the dream cycle is callable on a real session.

**wall budget:** ~4 min (~4 turn calls + 1 judge call + dream-cycle smoke).

**prerequisites:** TASK-1, TASK-2, TASK-3.

### TASK-5 — `evals/eval_session_continuity.py` (W2)

**files:**
- `evals/eval_session_continuity.py`
- `docs/REPORT-eval-session-continuity.md`

**done_when:** `uv run python evals/eval_session_continuity.py` exits 0 with W2.A–W2.F PASS.

**success_signal:** `/new` `/clear` `/sessions` `/resume` `/compact` all behave per spec against real session JSONL state.

**wall budget:** ~4 min (W2.E inflates ~10 synthetic turns to trigger `/compact`).

**prerequisites:** TASK-1, TASK-2.

### TASK-6 — `evals/eval_memory.py` (W3)

**files:**
- `evals/eval_memory.py`
- `docs/REPORT-eval-memory.md`

**done_when:** `uv run python evals/eval_memory.py` exits 0 with W3.A–W3.F PASS, with W3 case ordering preserved as documented in the W3 section header.

**success_signal:** Knowledge can be written, recalled, viewed, listed, forgotten; the dream cycle's merge/decay/archive runs and modifies the real knowledge dir.

**wall budget:** ~3 min (W3.A is the only LLM-heavy case; the rest are tool/dispatch calls + one real dream cycle in W3.G).

**prerequisites:** TASK-1, TASK-2.

### TASK-7 — `evals/eval_skills.py` (W4)

**files:**
- `evals/eval_skills.py`
- `docs/REPORT-eval-skills.md`

**done_when:** `uv run python evals/eval_skills.py` exits 0 with W4.A–W4.D PASS. `~/.co-cli/skills/eval_smoke.md` is left in place by design (deterministic name; reruns overwrite).

**success_signal:** A user typing `/eval_smoke <args>` gets the skill body expanded into a real turn, env vars set and restored, hot-reload works, and built-in commands are protected from user-skill shadowing.

**wall budget:** ~3 min (one dispatched turn + one judge call + lifecycle tool calls).

**prerequisites:** TASK-1, TASK-2, TASK-3.

### TASK-8 — `evals/eval_background.py` (W5)

**files:**
- `evals/eval_background.py`
- `docs/REPORT-eval-background.md`

**done_when:** `uv run python evals/eval_background.py` exits 0 with W5.A–W5.D PASS.

**success_signal:** `/background` launches a real subprocess, `/tasks` shows it, `/cancel` kills it cleanly with no orphan process, oversized background output spills.

**wall budget:** ~1 min (no LLM calls; subprocess + polling only).

**prerequisites:** TASK-1, TASK-2.

### TASK-9 — `evals/eval_trust_visibility.py` (W6)

**files:**
- `evals/eval_trust_visibility.py`
- `docs/REPORT-eval-trust-visibility.md`

**done_when:** `uv run python evals/eval_trust_visibility.py` exits 0 with W6.A–W6.B PASS (W6.B–F dropped in the grilling round — see W6 section "Cuts").

**success_signal:** `/approvals` add/list/clear behaves per spec; unknown slash never burns LLM tokens.

**wall budget:** < 30s (dispatch-only; no LLM calls).

**prerequisites:** TASK-1, TASK-2.

---

## Testing

This plan is the eval rebuild — there is no separate test suite for the evals themselves.
Validation is operational:

| Gate | How to verify |
|---|---|
| Helpers importable | `uv run python -c "from evals._deps import make_eval_deps; from evals._ollama import ensure_ollama_warm; from evals._judge import judge_with_llm; from evals._observability import EvalRun; from evals._report import prepend_report; from evals._timeouts import CALL_TIMEOUT_S"` exits 0. |
| Each eval runnable | `uv run python evals/eval_<workflow>.py` exits 0 (each of the 6) with REPORT updated. |
| Pytest unaffected | `uv run pytest -x` continues to pass — `evals/` is not imported anywhere under `tests/` or `co_cli/`. |
| No `~/.co-cli/` leaks into `tests/` | `grep -r "from evals" tests/` returns empty. |
| Ship gate clean | `scripts/quality-gate.sh full` passes (lint + pytest). |

The 6 eval runs **themselves** are not in any CI gate — they are UAT smoke runs invoked
manually before ship. This matches the prior policy.

---

## Open Questions

All items resolved inline in C1/C2 (former auto-approve frontend, dream pollution,
SKIPPED categories, MCP degradation, real-artifact accumulation, miner_tool resolution,
W6.E feasibility) were moved into the plan body and are not duplicated here.

1. **Skill curator + session-reviewer pass coverage** — a separate follow-up plan
   (`eval_post_turn_passes.py`) should ship if reviewer/curator regressions become a
   shipping concern. Deferred per PO-M-2.
2. **Observability eval** — the dropped W6.E indicates the OTel reader surface needs
   either a public helper or its own dedicated eval that owns the schema coupling. File
   a follow-up plan to choose between (a) promote `tail.py`'s row-fetch helpers to
   public + add eval, or (b) ship `eval_observability.py` that quarantines the schema
   coupling.
3. **`_cmd_resume` slash-surface refactor.** Current `/resume` is an interactive
   module-level picker (`prompt_selection`); evals can't drive it via a frontend
   subclass. A follow-up should refactor to `ctx.deps.frontend.prompt_selection(...)` so
   W2.D can exercise the slash surface end-to-end. Not blocking — W2.D currently
   exercises the rehydration mechanism directly.
4. **W2.E inflation strategy.** W2.E drives N synthetic turns to inflate history past
   `compaction_ratio`. Open: what is N for a typical workspace? Dev measures at TASK-5
   implementation and bakes the value into the eval (with a comment citing the measured
   threshold).
5. **Judge model isolation.** The grilling round restored a judge call for W1.A. To
   prevent a single-model regression from masking itself, the judge should run on a
   different model handle than the agent under test. Open: does `settings.llm` already
   support a `judge_model` override, and if not, should we add one as a small config
   change in `_deps.py`? Falling back to the same model is allowed but logged as
   `[judge_model_same_as_agent]` in `CaseResult.reason`.


## Cycle C3 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| C3-B-1 (blocker) | adopt | `file_list` does not exist; actual tool is `file_find` (verified at `co_cli/tools/files/read.py`). | W1.B's tool-name assertion changed to `file_find`; `shell_exec`+`ls` added as accepted fallback with `[shell_fallback]` reason so a reasonable alternative tool choice doesn't FAIL the case. |
| C3-M-1 | adopt | W2.D didn't explicitly thread `load_transcript` output as `message_history=` into the follow-up turn; mirrors `main.py:337`'s `/resume` swap-in. | W2.D row now spells out the wiring: `load_transcript(path) → prior_messages`; `_rehydrate_todos(prior_messages)`; follow-up `run_turn(..., message_history=prior_messages)`. |
| C3-M-2 | adopt | Recall snippet is capped at 100 chars (`co_cli/tools/memory/recall.py:37`); a long body or buried token would miss the assertion for fixturing reasons, not behavior reasons. | W1.C row now requires the seeded body to be `<100 chars` and **open** with the distinctive token, so the snippet preview always contains it. |
| (C3 Core Dev observation on W3.F merge) | adopt | LLM-merge in `run_dream_cycle` may drop rare tokens — that's degradation, not regression. | W3.F now uses three-state result: both tokens preserved = PASS; exactly one missing = SOFT_FAIL (surfaces as a review signal in REPORT, doesn't fail the gate); both missing = FAIL. |

C3 PO returned `Blocking: none`. C3 Core Dev's one blocker (C3-B-1) was a literal tool
rename Core Dev itself named the right answer for; no C4 needed.

## Final — Team Lead (G1-Ready)

Plan ready for Gate 1.

- C1 → C2 → grilling → C3 converged. **26 cases**, full trace depth, behavioral
  upgrades in place.
- W1.B tool name corrected (`file_find`); W1.C fixture sized for the 100-char recall
  snippet; W2.D resume wiring made explicit; W3.F gets a three-state result to keep
  LLM-merge variance out of the fail gate.
- Five open questions remain (curator/reviewer follow-up evals, observability eval,
  resume slash-surface refactor, W2.E inflation N, judge-model isolation) — none
  blocking; tracked in the Open Questions section for follow-up plans.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev uat-workflow-evals`

## Cycle C3 — PO

**Assessment: approve. Blocking: none.**

The grilling amendments improve value discipline, not erode it. The cuts (W6.B/C/D/F, W3.D, W3.E `stats`, W4.D) shed plumbing-state checks that didn't defend a user-visible behavior gap — net 26 cases is a saner UAT footprint than 32. The behavioral upgrades (W1.A judge, W1.B tool-by-name, W1.C recall-used-not-just-injected, W2.D follow-up turn for continuity-as-user-value, W3.A agent-chooses-to-save, W3.F merge-preserves-tokens) move the right assertions from "plumbing wired" to "user gets value" — that **is** UAT, not benchmark drift. Two judge calls out of 26 cases is not prose-grading territory.

**Non-blocking notes (do not gate):**
- **PO-N-1** `eval_trust_visibility.py` at 2 cases is thin. Acceptable for this round; if W6.A also gets cut later, fold remaining case into `eval_session_continuity.py` and retire the file. Not a problem yet.
- **PO-N-2** Behavioral Constraint #13 (per-case latency budget) is justified — `feedback_llm_call_timing.md` explicitly says *watch durations closely*, and a per-case baseline catches what `asyncio.timeout` ceilings miss. Performance-as-UAT is appropriate for a smoke layer that gates ship.
- **PO-N-3** Trace + report expansion (`_trace.py`, OTel `trace_id`, top-3-slow-ops, regression-vs-prior diff) is depth-over-width, which is the explicit user direction. The trace file is the artifact that makes a FAIL re-playable from disk — high debugging value, not aspirational. Approved.

Ship it. Gate 1 → `/orchestrate-dev uat-workflow-evals`.

## Cycle C3 — Core Dev

**Assessment.** Grilling amendments verified against source. One blocker (W1.B wrong tool name); the other 4 flagged risks have clean answers or small descriptive gaps that don't block dev. Dependency graph in the Tasks section is internally consistent (TASK-3 → TASK-4/TASK-7; TASK-2 → all evals).

**Blocking:**

- **C3-B-1 W1.B — `tool_name == "file_list"` is wrong.** No such tool exists. `co_cli/tools/files/read.py` defines `file_find` (filename/glob list), `file_read`, and `file_search` (content search). For "list files in the current directory" the agent will pick **`file_find`** (it accepts a glob like `*` and returns matching paths). The case must assert `tool_name == "file_find"`. The shell-based fallback (`shell_exec` running `ls`) is also a possibility the agent might pick, so the case should accept `file_find` as the primary expectation OR a shell `ls` invocation as the secondary. Otherwise the case FAILs on first run not because of a regression but because the assertion never matched reality.

**Major issues:**

- **C3-M-1 W2.D — `message_history` wiring under-specified.** Verified: `_cmd_resume` returns `ReplaceTranscript(history=messages)` and `co_cli/main.py:337` swaps the REPL's `message_history` accordingly, so the next `agent.run(..., message_history=...)` does carry the prior fact. **But the W2.D row in the W2 table doesn't say the eval feeds `load_transcript()`'s return value as `message_history=` to the follow-up `run_turn`.** Without that wiring, the rotated turn has no prior context and the case will FAIL spuriously. The row needs one explicit line: "pass the loaded messages as `message_history=` to the follow-up `run_turn`." (One sentence in the W2.D cell; not a new task.)

- **C3-M-2 W1.C — token-in-recall depends on seeded artifact body length.** Verified: `co_cli/tools/memory/recall.py:37` caps snippets at `_SNIPPET_DISPLAY_CHARS = 100`. The recall block injected into the assembled prompt uses this same snippet. The "response contains `MNEMONIC_TOKEN_42` verbatim" assertion only holds if the token sits within the first 100 chars of the seeded body. The W1.C cell should state: "seed body is short (≤ 100 chars) with the token at the top." Otherwise the agent never sees the token and the case fails on artifact construction, not on a recall regression.

**Minor issues:**

- **C3-m-1 W3.F merge semantics — correct in spirit, possibly flaky.** Verified: `co_cli/memory/dream.py:_merge_cluster` calls an LLM sub-agent with `dream_merge.md`, which instructs "preserves every meaningful fact across the inputs without inventing anything new." So the merged body IS expected to contain both tokens. However, the merge prompt is a hard-constraint prompt to a small local LLM — rare tokens like `TOKEN_A_ONLY` could be dropped as "filler." Acceptable as written, but the case should record a soft-fail in `CaseResult.reason` ("merge dropped token X") rather than hard-failing the eval. The current PASS criteria reads as hard-fail.

- **C3-m-2 Judge model isolation (Open Q5) — confirmed: no `judge_model` field today.** Verified: `LlmSettings` (`co_cli/config/llm.py:145`) has `api_key, provider, host, model, max_ctx` — no `judge_model`. Plan already flags this as Open Q5 with a fallback to the agent model + `[judge_model_same_as_agent]` reason log. That's the right call for a planning doc; resolution belongs in a separate small config plan, not this one.

- **C3-m-3 TASK-2 trace scope is heavy but not blocking.** `_trace.py` captures prompt snapshot, message history, tool calls, thinking, token usage, span IDs, plus `capture_artifact_diff`. That is genuinely a lot of surface for one task. The done_when criterion is concrete enough to bound it; if Dev hits a wall, split into "core TurnTrace" (mandatory for TASK-4) + "artifact_diff + OTel correlation" (deferrable). Not a blocker — flagging for Dev awareness.

- **C3-m-4 Trace persistence size — not a real problem at this scale.** ~26 cases × ~30 turns × ~5 KB/turn × dozens of runs ≈ low single-digit GB after months. `_outputs/<eval>-<ts>/` already namespaces per run; a simple "keep last 10 runs" cron is a 10-line follow-up if it ever matters. Don't address in this plan.

- **C3-m-5 W3.A assertion is fair.** Verified: `co_cli/context/rules/04_tool_protocol.md:115` explicitly instructs the agent — when the user says "remember" — to call `knowledge_manage(action='create', ...)`. W3.A's "agent_chooses_to_save" assertion defends documented behavior, not a hopeful guess.

- **C3-m-6 `EVAL_VERBOSE_TRACE=1` env var.** No existing collision in the repo (`grep` returns only the plan itself). The user's prior concern about env-var state pollution is about `CO_HOME`-style globals shared with production code — this is a verbosity flag set in `evals/` only, never read in `co_cli/`. Safe.

- **C3-m-7 Latency budget calibration (#13).** Plan defers the budget number to impl-time measurement. Acceptable for a planning doc; budgets calibrated against a stale baseline are worse than budgets calibrated against the actual TASK-4 first-run timing.

**Summary.** One must-fix (W1.B wrong tool name → use `file_find`). Two should-fixes (W2.D `message_history` wiring sentence; W1.C body-length constraint sentence). Rest is fine. Net: amendments hold up under scrutiny.
