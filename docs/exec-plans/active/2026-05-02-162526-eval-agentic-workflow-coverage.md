# Plan: Eval — Agentic Workflow Coverage

**Task type:** eval-coverage

## Context

Eval suite revised to **flow UAT only** — all structural evals (direct library calls,
algorithm comparisons, component-level checks) have been removed. Only evals that
exercise a real user interaction through `run_turn()` with a real LLM remain.

Current eval coverage after cleanup:

| Eval | What it covers |
|---|---|
| `eval_approval_flow.py` ✓ DONE | Tool approval — shell allow/deny, path, domain, scope-always, question-prompt |
| `eval_compaction_proactive.py` | Organic M3 fire via real research turns |
| `eval_compaction_multi_cycle.py` | M3 fires twice, iterative summary chain preserves facts |
| `eval_memory_write_recall.py` ✓ DONE | Agent saves artifact → indexed → recalled → appended → replaced |

Six workflows still have zero flow UAT coverage:

| Gap | Risk |
|---|---|
| Basic text chat (no tools) | Most common TUI interaction — never exercised end-to-end |
| Web research turn | Agent uses `web_search`/`web_fetch` to answer — only approval resolver tested, not a real turn |
| Session restore | Prior session loads on restart, agent references past context — core continuity promise untested |
| Skill dispatch lifecycle | Skill env set on entry, cleared on exit, no residue on next turn |
| Doom-loop guardrail | Safety intervention never verified to fire and change agent behavior |
| Interrupt + abort marker | Ctrl+C mid-turn state hygiene — next turn must recover cleanly |

Each gap becomes one eval script following existing `evals/` conventions:
real LLM, real stores, real side effects, no mocks, prepend dated section to
`docs/REPORT-eval-<name>.md`.

---

## Problem & Outcome

**Problem:** Six critical user-facing flows have no automated coverage. Regressions
in basic chat, web use, session continuity, skill dispatch, doom-loop safety, and
interrupt hygiene are invisible until manual use uncovers them.

**Outcome:** Six eval scripts, each targeting one flow. Each script runs via
`uv run python evals/eval_<name>.py`, emits pass/fail per sub-case, and appends
a dated section to a permanent REPORT.

---

## Scope

In scope:
- `evals/eval_basic_chat.py` (EVAL-3)
- `evals/eval_web_research.py` (EVAL-4)
- `evals/eval_session_restore.py` (EVAL-5)
- `evals/eval_skill_dispatch.py` (EVAL-6)
- `evals/eval_doom_loop_guardrail.py` (EVAL-7)
- `evals/eval_interrupt_abort_marker.py` (EVAL-8)
- One `docs/REPORT-eval-<name>.md` per eval, created on first run

Out of scope:
- Changes to production code
- New unit tests in `tests/`
- Structural evals (direct library calls, benchmark comparisons)

---

## Behavioral Constraints

- **Real everything**: all evals use real LLM provider, real SQLite stores,
  real filesystem under `tempfile.TemporaryDirectory`. No mocks, no stubs.
- **Real LLM calls must not be capped**: no `max_turns=1` workarounds. Use
  realistic turn budgets (5–15 turns per case).
- **Infra setup outside `asyncio.timeout`**: `ensure_ollama_warm()` and fixture
  setup run before entering the timed section.
- **Fail fast within a case**: each sub-case asserts immediately; a failing case
  logs its verdict and the script continues to the next case.
- **REPORT format**: prepend a dated `## Run <ISO8601>` section with a markdown
  table of sub-case verdicts (PASS / FAIL + reason). Never overwrite.
- **CO_HOME override**: every eval sets `CO_HOME` to the temp dir so stores and
  transcripts are isolated from `~/.co-cli/`.

---

## Implementation Plan

### EVAL-1 — `evals/eval_approval_flow.py` ✓ DONE

A1–A8 complete. Shell allow/deny, path approval, domain approval, scope-always
session rule, question-prompt routing — all verified.

---

### EVAL-2 — `evals/eval_memory_write_recall.py` ✓ DONE

W1–W5 complete. Agent saves artifact, artifact indexed, recalled in fresh session,
appended, replaced — all verified.

---

### EVAL-3 — `evals/eval_basic_chat.py`

**files:**
- `evals/eval_basic_chat.py` (new)
- `docs/REPORT-eval-basic-chat.md` (created on first run)

**Sub-cases:**

| ID | Name | Hypothesis |
|----|------|------------|
| C1 | `factual_question` | Agent answers a simple factual question with text only — no tool calls, finish reason `stop` |
| C2 | `multi_turn_context` | Second turn asks a follow-up; agent references its own prior response correctly |
| C3 | `instruction_following` | Agent follows an explicit format instruction (e.g. "reply in exactly 3 bullet points") |

**Fixture approach:**
- Single `run_turn()` per case, `SilentFrontend` (no approval expected).
- C1: ask a question with a known factual answer; assert answer token in response, no tools called.
- C2: two turns; assert second response references content from first.
- C3: format-constrained prompt; assert response matches the structural constraint.

**done_when:** C1–C3 PASS; REPORT exists; pytest passes.

---

### EVAL-4 — `evals/eval_web_research.py`

**files:**
- `evals/eval_web_research.py` (new)
- `docs/REPORT-eval-web-research.md` (created on first run)

**Sub-cases:**

| ID | Name | Hypothesis |
|----|------|------------|
| W1 | `web_fetch_executes` | Agent calls `web_fetch` for a real URL; approval fires; fetched content referenced in response |
| W2 | `web_search_executes` | Agent calls `web_search` for a query; results inform the response |
| W3 | `domain_approval_persists` | After approving domain in W1, second fetch to same domain skips approval |

**Fixture approach:**
- Real network access required (stable public URLs, e.g. example.com).
- HeadlessFrontend with approval stub set to `y` for all web_fetch prompts.
- Assert tool names in `frontend.approval_calls`, response contains page content token.
- W3: two sequential fetches to same domain; assert approval count = 1.

**Prerequisites:** network access.

**done_when:** W1–W3 PASS; REPORT exists; pytest passes.

---

### EVAL-5 — `evals/eval_session_restore.py`

**files:**
- `evals/eval_session_restore.py` (new)
- `docs/REPORT-eval-session-restore.md` (created on first run)

**Sub-cases:**

| ID | Name | Hypothesis |
|----|------|------------|
| R1 | `prior_context_available` | Agent given a seeded prior session references a known fact from that session in its response |
| R2 | `no_hallucination_from_absent_session` | Fresh session with no prior history: agent does not fabricate prior context |
| R3 | `multi_session_most_recent_wins` | Two seeded sessions; most recent session content appears in agent response over older |

**Fixture approach:**
- Write a synthetic JSONL transcript containing a unique token to `sessions_dir` under temp `CO_HOME`.
- Call `restore_session(deps)` to load it (same path as real bootstrap).
- Run a turn asking about the known fact; assert token or paraphrase in response.
- R2: skip `restore_session`; assert response does not contain the unique token.
- R3: write two transcripts with different tokens and timestamps; restore; assert most recent token present.

**done_when:** R1–R3 PASS; REPORT exists; pytest passes.

---

### EVAL-6 — `evals/eval_skill_dispatch.py`

**files:**
- `evals/eval_skill_dispatch.py` (new)
- `docs/REPORT-eval-skill-dispatch.md` (created on first run)

**Sub-cases:**

| ID | Name | Hypothesis |
|----|------|------------|
| S1 | `skill_turn_executes` | Dispatching a skill via `dispatch_command("/skill_name", ...)` completes and returns a response |
| S2 | `skill_env_applied` | Env overrides from `skill_env` are active during the skill turn |
| S3 | `skill_env_restored` | After the skill turn, overridden env vars revert to pre-skill values |
| S4 | `no_residue_next_turn` | A plain-text turn immediately after skill dispatch runs without any skill state residue |

**Fixture approach:**
- Write a minimal `.md` skill file to `skills_dir` under temp `CO_HOME` with a known
  `skill_env` override (e.g. a dummy env var) and a one-line instruction.
- Dispatch via `dispatch_command()`. Inspect `deps.runtime` before/after.
- S4: run a second plain `run_turn()` after skill completes; assert `active_skill_name` is None.

**done_when:** S1–S4 PASS; REPORT exists; pytest passes.

---

### EVAL-7 — `evals/eval_doom_loop_guardrail.py`

**files:**
- `evals/eval_doom_loop_guardrail.py` (new)
- `docs/REPORT-eval-doom-loop-guardrail.md` (created on first run)

**Sub-cases:**

| ID | Name | Hypothesis |
|----|------|------------|
| G1 | `guardrail_fires` | After `doom_loop_threshold` identical consecutive calls, `safety_prompt_text()` returns non-empty intervention text |
| G2 | `guardrail_content_correct` | The intervention text mentions the repeated tool name |
| G3 | `agent_changes_behavior` | Agent stops repeating the same tool call after the intervention (within 3 additional turns) |
| G4 | `below_threshold_silent` | With `threshold - 1` identical calls, `safety_prompt_text()` returns empty string |

**Fixture approach:**
- Register a minimal tool that always returns `"try again"`.
- Task the agent: "Keep calling `repeat_tool` until you get a different response."
- Track calls via a counter in the tool body.
- For G1/G2: call `safety_prompt_text(deps)` after each segment and inspect output.
- For G3: `run_turn()` with `max_turns=10`; verify call count stabilizes after intervention.
- For G4: set call counter to `threshold - 1`; verify `safety_prompt_text` is empty.

**done_when:** G1–G4 PASS; REPORT exists; pytest passes.

---

### EVAL-8 — `evals/eval_interrupt_abort_marker.py`

**files:**
- `evals/eval_interrupt_abort_marker.py` (new)
- `docs/REPORT-eval-interrupt-abort-marker.md` (created on first run)

**Sub-cases:**

| ID | Name | Hypothesis |
|----|------|------------|
| I1 | `interrupted_flag_set` | `TurnResult.interrupted` is True after a cancelled turn |
| I2 | `orphan_tool_call_dropped` | If the last `ModelResponse` had unanswered `ToolCallPart`s, it is absent from `TurnResult.messages` |
| I3 | `abort_marker_appended` | The last message in `TurnResult.messages` is the abort-marker `ModelRequest` |
| I4 | `next_turn_clean` | A subsequent `run_turn()` with the interrupted messages as history completes successfully |
| I5 | `outcome_error` | `TurnResult.outcome == "error"` when `CancelledError` is raised mid-segment |

**Fixture approach:**
- Register a slow tool (`asyncio.sleep(5)`) that the agent is instructed to call.
- Start `run_turn()` as a background `asyncio.Task`.
- Wait for the first `FunctionToolCallEvent` via an `asyncio.Event` set inside the tool.
- Cancel the task immediately after the event fires.
- For I4: pass `turn_result.messages` as `message_history` to a fresh `run_turn()` with
  a simple prompt; verify `outcome == "continue"`.

**done_when:** I1–I5 PASS; REPORT exists; pytest passes.

---

### TASK-FINAL — Full test suite gate

```bash
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-eval-agentic-workflow.log
```

**done_when:** exits 0.
**prerequisites:** EVAL-3 through EVAL-8.

---

## Sequencing

```
EVAL-3 (basic chat)       — baseline: proves run_turn + real LLM works; unblocks all others
EVAL-5 (session restore)  — restore_session path; no dependency
EVAL-4 (web research)     — network-dependent; run after EVAL-3 confirms harness
EVAL-6 (skill dispatch)   — most complex fixture; run after EVAL-3
EVAL-7 (doom loop)        — safety system; standalone after EVAL-3
EVAL-8 (interrupt/abort)  — state hygiene; standalone after EVAL-3
```

---

## Testing

Each eval is self-verifying. No new `tests/` unit tests added — evals serve as
the UAT gate for these flows. `uv run pytest -x` must still pass after all scripts
are added (evals live in `evals/`, not `tests/`, so pytest does not pick them up).
