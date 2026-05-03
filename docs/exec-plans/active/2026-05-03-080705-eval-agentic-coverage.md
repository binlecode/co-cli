# Plan: Eval — Agentic Coverage Lift

## Outcome

Six eval scripts closing UAT gaps in skill dispatch, doom-loop safety, interrupt hygiene,
context overflow recovery, cross-session recall, and domain approval live turns. Each runs
via `uv run python evals/eval_<name>.py` and prepends a dated section to a permanent REPORT.

---

## Scope

- `evals/eval_skill_dispatch.py` (EVAL-A) — new
- `evals/eval_doom_loop_guardrail.py` (EVAL-B) — new
- `evals/eval_interrupt_abort_marker.py` (EVAL-C) — new
- `evals/eval_overflow_recovery.py` (EVAL-D) — new
- `evals/eval_session_memory_recall.py` (EVAL-E) — new
- `evals/eval_approval_flow.py` (EVAL-F) — add A9 domain-approval live turn sub-case

Out of scope: production code changes, new `tests/` unit tests, structural evals.

---

## Behavioral Constraints

- **Real everything**: real LLM, real SQLite stores, real filesystem under `tempfile.TemporaryDirectory`. No mocks, no stubs.
- **No artificial caps on turns**: use realistic `max_turns` (5–15). No `max_turns=1`.
- **Infrastructure outside `asyncio.timeout`**: `ensure_ollama_warm()` and fixture setup precede the timed section.
- **Fail fast within each case**: sub-case verdicts logged immediately; script continues to next case.
- **REPORT format**: prepend dated `## Run <ISO8601>` section; never overwrite.
- **CO_HOME isolation**: use `make_eval_deps()` with temp dir overrides; never touch `~/.co-cli/`.
- **pytest non-interference**: evals in `evals/`, not `tests/`; `uv run pytest` must pass unmodified.

---

## Implementation Plan

### EVAL-A — `evals/eval_skill_dispatch.py`

**files:**
- `evals/eval_skill_dispatch.py` (new)
- `docs/REPORT-eval-skill-dispatch.md` (created on first run)

| ID | Name | Hypothesis |
|----|------|------------|
| S1 | `skill_turn_executes_end_to_end` | Full dispatch chain — skill loaded, dispatched via `dispatch()`, env active during turn, response references env token, env restored, `active_skill_name` is None after cleanup |

*(S2 `env_clean_after_cleanup` dropped — cleanup assertions are the final step of S1; a separate case adds no scenario boundary.)*

**Fixture approach:**
- Write a minimal skill `.md` file to `skills_dir` under temp `CO_HOME`:
  - `skill_env: {CO_EVAL_SKILL_TOKEN: "SKLTOKEN12345"}`
  - instruction body: "The user asked: '{{user_input}}'. The value of CO_EVAL_SKILL_TOKEN is: SKLTOKEN12345. Include it in your reply."
- Load skills: `loaded = load_skills(skills_dir, settings=deps.config); deps.skill_commands = filter_namespace_conflicts(loaded, set(BUILTIN_COMMANDS.keys()), [])`.
- Build agent: `agent = build_agent(config=deps.config)`.
- Construct `CommandContext(agent=agent, deps=deps, message_history=[], frontend=frontend)`.
- Call `outcome = await dispatch("/eval_skill", ctx)` — assert `isinstance(outcome, DelegateToAgent)`.
- Apply env: `saved_env = {k: os.environ.get(k) for k in outcome.skill_env}; os.environ.update(outcome.skill_env); deps.runtime.active_skill_name = "eval_skill"`.
- Call `result = await run_turn(agent=agent, user_input=outcome.delegated_input, deps=deps, message_history=[], frontend=frontend)`.
- Assert `"SKLTOKEN12345" in _response_text(result)` and `result.outcome == "continue"`.
- Call `cleanup_skill_run_state(saved_env, deps)` — assert env var absent from `os.environ`, `active_skill_name` is None.

**done_when:** `uv run python evals/eval_skill_dispatch.py` exits 0; S1 PASS; REPORT exists.

**success_signal:** Running `/eval_skill` in the terminal executes the skill body, uses the env override, and returns to clean state for the next turn.

---

### EVAL-B — `evals/eval_doom_loop_guardrail.py`

**files:**
- `evals/eval_doom_loop_guardrail.py` (new)
- `docs/REPORT-eval-doom-loop-guardrail.md` (created on first run)

| ID | Name | Hypothesis |
|----|------|------------|
| G1 | `guardrail_fires` | After `doom_loop_threshold` identical consecutive tool calls, `safety_prompt_text()` returns non-empty text containing the repeated tool name |
| G2 | `below_threshold_silent` | With `threshold - 1` identical calls, `safety_prompt_text()` returns empty string |
| G3 | `agent_stops_looping` | Agent stops repeating the same call after the guardrail text is injected (within 5 additional turns) |

*(G2 `guardrail_names_tool` merged into G1 — both build the same synthetic fixture and call `safety_prompt_text()` once; the tool-name check is a second assert on the same output.)*

**Fixture approach:**
- G1–G2 *(mechanism checks — deterministic, no LLM)*: `safety_prompt_text()` derives the doom-loop count by scanning `ctx.messages` via `_count_consecutive_same_calls(messages)`. Build a synthetic messages list of `N = doom_loop_threshold` identical `ModelResponse(parts=[ToolCallPart(tool_name="repeat_tool", ...)])` entries. Construct a minimal `RunContext`-compatible stub (read `prompt_text.py` for the exact ctx signature). Call `safety_prompt_text(ctx)` and inspect the return value: assert non-empty AND contains `"repeat_tool"` (G1); build list of `N-1` entries and assert empty string (G2).
- G3 *(UAT — live turn)*: build a custom agent with `repeat_tool` registered (always returns `"try again"`); `repeat_tool` is added at `build_agent()` call time. Task agent: "Keep calling repeat_tool until you get a different response. Stop after 15 turns." Run `run_turn()`. Count `ToolCallPart` entries in result messages; assert count stabilizes at or below `doom_loop_threshold + 3`.

**done_when:** `uv run python evals/eval_doom_loop_guardrail.py` exits 0; G1–G3 PASS; REPORT exists.

**success_signal:** Agent stuck in a tool loop breaks out gracefully within a bounded number of turns rather than hitting the hard turn limit.

---

### EVAL-C — `evals/eval_interrupt_abort_marker.py`

**files:**
- `evals/eval_interrupt_abort_marker.py` (new)
- `docs/REPORT-eval-interrupt-abort-marker.md` (created on first run)

| ID | Name | Hypothesis |
|----|------|------------|
| I1 | `interrupt_state_correct` | After cancellation: `TurnResult.interrupted` is True, `outcome == "error"`, orphan `ToolCallPart` response is absent from messages, and last message is the abort-marker `ModelRequest` |
| I2 | `next_turn_clean` | A subsequent `run_turn()` with the interrupted messages as history completes with `outcome='continue'` and a `ToolReturnPart` from `file_list` appears in result messages |

*(I1 merges original I1+I2+I3 — all three share the identical fixture: slow tool + asyncio.Task + cancel. They are three asserts on one interrupted `TurnResult`, not three separate scenarios.)*

**Fixture approach:**
- Register a slow tool (`asyncio.sleep(8)`) that the agent is instructed to call.
- Start `run_turn()` as a background `asyncio.Task`.
- Use an `asyncio.Event` set inside the tool to signal when the tool call is in flight.
- Cancel the outer task immediately after the event fires.
- Inspect `TurnResult` for I1: assert `interrupted is True`, `outcome == "error"`, orphan `ModelResponse` with unanswered `ToolCallPart`s is absent, last message is abort-marker `ModelRequest`.
- For I2: pass `turn_result.messages` as `message_history` to a fresh `run_turn()` that asks the agent to call `file_list` on a known temp directory; assert `outcome == "continue"` AND that a `ToolReturnPart` from `file_list` appears in the result messages — verifying history integrity, not just text output.

**done_when:** `uv run python evals/eval_interrupt_abort_marker.py` exits 0; I1–I2 PASS; REPORT exists.

**success_signal:** Ctrl+C mid-turn leaves history in a clean state; the next turn picks up correctly without orphan tool calls or corrupt state.

---

### EVAL-D — `evals/eval_overflow_recovery.py`

**files:**
- `evals/eval_overflow_recovery.py` (new)
- `docs/REPORT-eval-overflow-recovery.md` (created on first run)

| ID | Name | Hypothesis |
|----|------|------------|
| O1 | `recovery_correct` | `recover_overflow_history()` returns a shorter history with a compaction marker AND the tail `UserPromptPart` with the unique token is present |
| O2 | `run_turn_survives_overflow` | A turn with a deliberately overfilled history completes with `outcome='continue'` after inline overflow recovery |
| O3 | `recovery_is_idempotent` | Calling `recover_overflow_history()` twice does not shrink history beyond the first compaction |

*(O1 merges original O1+O2 — both call `recover_overflow_history()` once on the same fixture and assert complementary properties of the same returned list.)*

**Fixture approach:**
- Build a long synthetic message history (inline): large `ToolReturnPart`s totalling at least 2× `config.llm.num_ctx` char-estimated tokens. Include a known unique token in the tail `UserPromptPart`.
- O1 *(algorithm correctness)*: construct a `RunContext` with the synthetic deps and call `await recover_overflow_history(run_ctx, history)`. Signature: `async def recover_overflow_history(ctx: RunContext[CoDeps], messages: list[ModelMessage]) -> list[ModelMessage] | None`. Assert returned history is shorter and contains a compaction marker; assert tail `UserPromptPart` with the unique token is present.
- O2 *(provider-conditional)*: if `deps.config.llm.provider` is not `ollama`, call `run_turn()` with the overfilled history and assert `outcome == "continue"`. Under Ollama emit `SKIP`: `"Ollama does not enforce context limits — HTTP overflow path not exercisable"`.
- O3: call recovery twice; assert second result is same length as first.

**done_when:** `uv run python evals/eval_overflow_recovery.py` exits 0; O1, O3 PASS; O2 PASS or SKIP; REPORT exists.

**success_signal:** The overflow recovery algorithm correctly compacts and preserves the tail; live-turn end-to-end verified on context-enforcing providers (cloud).

---

### EVAL-E — `evals/eval_session_memory_recall.py`

**files:**
- `evals/eval_session_memory_recall.py` (new)
- `docs/REPORT-eval-session-memory-recall.md` (created on first run)

| ID | Name | Hypothesis |
|----|------|------------|
| M1 | `session_channel_indexed` | A seeded past session is indexed into MemoryStore; `store.search(token)` returns ≥ 1 hit with `source='session'` |
| M2 | `agent_recalls_session_via_search` | Agent in a fresh turn with no message_history calls `memory_search` and references the unique token from the past session |
| M3 | `no_session_bleed` | A unique token planted only in a second isolated session store does not appear in a search against the first store |

**Fixture approach:**
- Write a synthetic session JSONL to `sessions_dir` under temp `CO_HOME` containing a unique token (e.g. `MEMRECALL{N}`).
- Index sessions: `deps.memory_store.sync_sessions(deps.sessions_dir)`. (Alternatively `init_session_index(deps, current_session_path, frontend)` from `co_cli.bootstrap.core` — read bootstrap.py to choose.)
- M1: call `deps.memory_store.search("MEMRECALL{N}")` directly; assert ≥ 1 hit with `source='session'`.
- M2: run a turn asking "What do you know about MEMRECALL{N}?" with empty `message_history`; assert `memory_search` appears in `ToolCallPart` entries AND the token appears in response text.
- M3: repeat M1 against a separate `MemoryStore` with an empty sessions dir; assert 0 hits.

**done_when:** `uv run python evals/eval_session_memory_recall.py` exits 0; M1–M3 PASS; REPORT exists.

**success_signal:** Agent can recall facts from past sessions by calling `memory_search`, not just by receiving prior transcript in `message_history`.

---

### EVAL-F — `evals/eval_approval_flow.py` (add A9) ✓ DONE

**files:**
- `evals/eval_approval_flow.py` (modified)
- `docs/REPORT-eval-approval-flow.md` (exists)

| ID | Name | Result |
|----|------|--------|
| A9 | `domain_approval_live_turn` | PASS |

**Delivered:** `approval_subject_fn: Callable[[dict], ApprovalSubject] | None` added to `ToolInfo` (no monkeypatch needed). A9 registers a synthetic `domain_fetch_test` tool via `_A9_AGENT.tool()` and injects `ToolInfo` with `approval_subject_fn` into `deps.tool_index`. All A1–A9 passing.

**done_when:** ✓ DONE — `uv run python evals/eval_approval_flow.py` exits 0; A1–A9 all PASS.

---

### TASK-FINAL — Full test suite gate

```bash
mkdir -p .pytest-logs
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-eval-agentic-coverage.log
```

**done_when:** exits 0.

**prerequisites:** [EVAL-A, EVAL-B, EVAL-C, EVAL-D, EVAL-E, EVAL-F]

---

## Sequencing

```
EVAL-F  ✓ DONE
EVAL-A  most fixture-complex (skill loading + dispatch chain)
EVAL-E  requires bootstrap session-indexing read; standalone
EVAL-B  safety; standalone
EVAL-C  state hygiene; standalone
EVAL-D  algorithm tests standalone; O2 live turn cloud-only
```

---

## Final — Team Lead

Plan approved. All C1 blocking issues resolved. EVAL-F delivered.
Cases deduped: S2 dropped (subsumed by S1); G1+G2 merged; I1+I2+I3 merged; O1+O2 merged.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev eval-agentic-coverage`
