# co-cli System Workflows

Canonical enumeration of co-cli's core functional workflows. This is the shared
reference for any audience that needs to reason about coverage:

- `/clean-tests` — coverage gap audit
- `/orchestrate-plan` — scope check before drafting
- `/review-impl` — behavioral verification anchor
- onboarding / new-contributor explanation

Specs in `docs/specs/` go *deep* on each subsystem. This registry goes *wide*
across every user-facing flow with entry points and primary failure modes.

---

## Format

Each workflow lists:

- **Entry** — `file.py: function` or `file.py` if dispatch is multi-function
- **Behavior** — what the workflow does, in 1–2 sentences
- **Primary failure modes** — observable regressions a behavioral test must catch
- **Required test depth** — what kind of test legitimately defends this flow
- **Spec** — owning spec file in `docs/specs/`

Severity calibration for `/clean-tests` Phase 4.5:

- **User-facing**: chat loop, slash commands, tool execution, memory recall, approval
  prompts, REPL — uncovered = **Blocking**
- **Internal mechanism**: planners, history processors, span emitters — uncovered =
  **Minor** unless they gate a user-facing workflow

---

## 1. Bootstrap & Startup

### 1.1 Settings load with env precedence

- **Entry**: `co_cli/config/core.py: load_config` (called via `Settings` import)
- **Behavior**: Loads `~/.co-cli/settings.json`, merges `~/.co-cli/.env`, applies
  shell env vars, validates the result, caches the singleton.
- **Primary failure modes**: env var not honored; secrets file overrides shell
  vars; invalid config silently accepted; precedence inverted.
- **Required test depth**: end-to-end with a real settings file + env override;
  assert resolved value reflects the highest-precedence layer.
- **Spec**: bootstrap.md §2.1, config.md

### 1.2 LLM model validation + Ollama num_ctx probe

- **Entry**: `co_cli/bootstrap/check.py: probe_ollama_model` + `co_cli/config/llm.py: validate_config`
- **Behavior**: Validates provider/model pair, probes Ollama's `/api/show` for the
  model's runtime `num_ctx`, caps by `llm.max_ctx`, stores on `deps.model_max_ctx`.
- **Primary failure modes**: missing API key not surfaced as startup error;
  noreason-only model loaded; probe result not capped; `model_max_ctx` zero or
  unset; agentic-context floor not enforced.
- **Required test depth**: real `validate_config()` + real Ollama probe (or skipped
  with `skipif` when host unreachable); assert `deps.model_max_ctx` is set and
  capped correctly.
- **Spec**: bootstrap.md §2.4

### 1.3 Knowledge backend resolution + degradation

- **Entry**: `co_cli/bootstrap/core.py: _discover_memory_backend`
- **Behavior**: Probes embedder/reranker availability; chooses `hybrid` or `fts5`;
  degrades hybrid→fts5 once on construction failure; aborts on fts5 failure unless
  `grep` was explicitly configured. Records reason in `deps.degradations`.
- **Primary failure modes**: silent degradation without `degradations` entry; hybrid
  chosen when reranker unreachable; fts5 failure swallowed; `grep` not honored.
- **Required test depth**: real backend construction with reachable/unreachable
  reranker; assert chosen backend and degradation key.
- **Spec**: bootstrap.md §8

### 1.4 Knowledge directory sync (`source='knowledge'`)

- **Entry**: `co_cli/bootstrap/core.py: _sync_memory_store` → `MemoryStore.sync_dir`
- **Behavior**: Indexes every `.md` under `knowledge_dir` into `chunks` + `chunks_fts`
  (and `chunks_vec` in hybrid). SHA256 hash-skip on rerun. Closes store and aborts
  on failure.
- **Primary failure modes**: hash-skip broken (re-indexes unchanged files); failure
  not surfaced as startup abort; partial sync leaves stale rows.
- **Required test depth**: real `MemoryStore` + real `knowledge_dir`, run twice and
  assert no re-index on second run, then mutate a file and assert re-index.
- **Spec**: memory.md §3.1, bootstrap.md §9

### 1.5 Canon sync (`source='canon'`)

- **Entry**: `co_cli/bootstrap/core.py: _sync_canon_store`
- **Behavior**: For active `personality`, indexes `souls/{role}/memories/*.md` with
  `no_chunk=True` (one chunk per file = full body). No-op when `store is None` or
  `personality` is empty.
- **Primary failure modes**: chunked instead of whole-body; no-op condition not
  honored; `kind='canon'` not auto-set.
- **Required test depth**: real canon files + real store; assert one chunk per file
  with full-body content; assert no-op on `store=None` and empty personality.
- **Spec**: memory.md §4

### 1.6 Skill loading (bundled + user-global, two-pass precedence)

- **Entry**: `co_cli/skills/loader.py: load_skills`
- **Behavior**: Loads bundled skills (`co_cli/skills/*.md`, `scan=False`), then
  user-global (`~/.co-cli/skills/*.md`, `scan=True`). User overrides bundled on name
  collision. Failed `requires` check skips the skill silently.
- **Primary failure modes**: user skill does not override bundled; security scan
  not run on user-global pass; built-in command name allowed to be shadowed; one
  bad file aborts the whole load.
- **Required test depth**: real `load_skills()` over a bundled + user dir mix with
  collision; assert user version wins; assert continuation past a malformed file.
- **Spec**: skill.md §2

### 1.7 MCP server discovery

- **Entry**: `co_cli/agents/mcp.py: _build_mcp_toolsets` + `discover_mcp_tools`
- **Behavior**: Each configured MCP server is entered on `AsyncExitStack`. Per-server
  failure isolation: a bad server records `mcp.<prefix>` in `degradations` and is
  skipped; successful servers contribute to merged `tool_index`.
- **Primary failure modes**: one bad server aborts startup; degradation key missing;
  merged `tool_index` includes failed-server tools.
- **Required test depth**: real MCP toolset with one good + one bad server; assert
  `degradations` populated and good-server tools present.
- **Spec**: bootstrap.md §6, 01-system.md §2.4

### 1.8 Session restore (latest *.jsonl)

- **Entry**: `co_cli/bootstrap/core.py: restore_session` (delegates to `co_cli/memory/session.py: find_latest_session`)
- **Behavior**: Picks the latest `*.jsonl` by filename, sets `deps.session.session_path`.
  Empty in-memory `message_history` — resume is explicit via `/resume`.
- **Primary failure modes**: latest filename not picked; new path not generated when
  none exists; in-memory history pre-populated.
- **Required test depth**: real sessions dir with multiple files; assert session_path
  is the latest by filename; assert message_history is empty.
- **Spec**: memory.md §2.2, bootstrap.md §12

### 1.9 Session index init

- **Entry**: `co_cli/bootstrap/core.py: init_session_index`
- **Behavior**: After `restore_session`, syncs past sessions into `MemoryStore` under
  `source='session'`. Excludes the current session path. Failure logs warning, does
  not abort.
- **Primary failure modes**: current session indexed; failure abort startup; legacy
  `session-index.db` not removed.
- **Required test depth**: real session files + real store; assert past sessions
  indexed but current excluded; assert legacy DB removal on first run.
- **Spec**: memory.md §2.3, bootstrap.md §12b

### 1.10 Capability discovery & degradation reporting

- **Entry**: `co_cli/bootstrap/core.py: create_deps` (collects degradations); `co_cli/tools/system/capabilities.py: capabilities_check`
- **Behavior**: Bootstrap detects optional integration health; runtime tool reports
  status via `capabilities_check` (also surfaced by `/doctor`).
- **Primary failure modes**: degradation set but not reported; tool reports stale
  state; `/doctor` reads wrong source.
- **Required test depth**: bootstrap with one degraded backend; call
  `capabilities_check`; assert it lists the degradation.
- **Spec**: tools.md §3 (Interaction & Session Control)

---

## 2. REPL & Slash Dispatch

### 2.1 REPL loop input + Ctrl+C handling

- **Entry**: `co_cli/main.py: _chat_loop`
- **Behavior**: One line at a time via `PromptSession`. Empty skipped. `exit`/`quit`
  break. First Ctrl+C prints exit hint, second within 2s breaks. EOF breaks.
- **Primary failure modes**: empty input drives a turn; Ctrl+C immediately exits;
  Ctrl+C double-press window not enforced.
- **Required test depth**: drive the loop with simulated input through the real
  `PromptSession` or its hook seam; assert observable exit behavior.
- **Spec**: tui.md §2

### 2.2 Slash command dispatch (built-in)

- **Entry**: `co_cli/commands/core.py: dispatch` → `BUILTIN_COMMANDS` lookup
- **Behavior**: First token after `/` matches `BUILTIN_COMMANDS`; handler runs; result
  becomes `LocalOnly` / `ReplaceTranscript` / list-as-history.
- **Primary failure modes**: unknown command reaches LLM; legacy `list[Any]` return
  not converted to `ReplaceTranscript`; wrong handler invoked.
- **Required test depth**: dispatch real built-ins (`/help`, `/clear`, `/new`); assert
  return type and side effects on `deps.session`.
- **Spec**: tui.md §2, skill.md §2 (Dispatch order)

### 2.3 Slash command dispatch (skill → DelegateToAgent)

- **Entry**: `co_cli/commands/core.py: dispatch` → skill match path
- **Behavior**: Resolves skill body, expands `$ARGUMENTS` / `$0` / `$N`, returns
  `DelegateToAgent(delegated_input, skill_env, skill_name)`. Argument expansion is
  positional; no args = body as-is.
- **Primary failure modes**: argument expansion off-by-one; `$ARGUMENTS` raw blob not
  passed; skill_env blocked keys leak through; built-in name shadowed.
- **Required test depth**: dispatch a real skill with args; assert expanded body and
  blocked-key filtering.
- **Spec**: skill.md §2 (Argument Expansion)

### 2.4 Skill env injection + cleanup

- **Entry**: `co_cli/skills/lifecycle.py: cleanup_skill_run_state`; injection in `co_cli/main.py: _chat_loop`
- **Behavior**: Snapshots prior `os.environ` values for skill_env keys, applies new
  values, runs the delegated turn, restores in `finally`. Clears `active_skill_name`.
- **Primary failure modes**: env vars leak past the turn; rollback skipped on
  exception/interrupt; system path keys not blocked.
- **Required test depth**: dispatch a skill with `skill_env`; assert env restored
  after turn (including in error/interrupt paths).
- **Spec**: skill.md §2 (Skill Env Lifecycle)

### 2.5 `/resume` (load past session)

- **Entry**: `co_cli/commands/resume.py: _cmd_resume`
- **Behavior**: Lists sessions, prompts pick, calls `load_transcript()`, returns
  `ReplaceTranscript` with adopted history.
- **Primary failure modes**: load_transcript silently truncates on malformed lines;
  > 50MB cap not enforced; `session_path` not updated.
- **Required test depth**: real transcript file + real picker hook; assert history
  adopted and session_path set.
- **Spec**: memory.md §2.2

### 2.6 `/new` and `/clear`

- **Entry**: `co_cli/commands/new.py`, `co_cli/commands/clear.py`
- **Behavior**: `/new` rotates session path, clears in-memory history. `/clear`
  clears in-memory history only. Both reset compaction runtime fields.
- **Primary failure modes**: compaction state not reset; session file deleted;
  history not cleared.
- **Required test depth**: real `/new` and `/clear`; assert
  `post_compaction_token_estimate` and `message_count_at_last_compaction` reset.
- **Spec**: tui.md §3, compaction.md §1.5

### 2.7 `/compact` (manual compaction)

- **Entry**: `co_cli/commands/compact.py`
- **Behavior**: Calls `compact_messages(ctx, history, bounds=(0, n, n), focus)` then
  `commit_compaction`. Returns `ReplaceTranscript` with compacted history. Resets
  thrash counter unconditionally.
- **Primary failure modes**: marker shape wrong (proactive `has_tail=True` vs
  `/compact` `has_tail=False`); thrash not reset; no-tail summary missing.
- **Required test depth**: real LLM-backed `/compact` on a multi-turn history;
  assert marker text contains "next message", absent "preserved verbatim".
- **Spec**: compaction.md §2.6

### 2.8 `/sessions` listing

- **Entry**: `co_cli/commands/sessions.py`
- **Behavior**: Lists past sessions with timestamps and titles, optionally filtered
  by keyword.
- **Primary failure modes**: filter not honored; sort order wrong; current session
  not excluded.
- **Required test depth**: real sessions dir + filter; assert ordering and exclusion.
- **Spec**: memory.md §2.2

### 2.9 `/skills` family

- **Entry**: `co_cli/commands/skills.py`
- **Behavior**: `list` shows loaded skills; `check` reports skip reasons across both
  tiers; `install <path|url>` copies + reloads + scans + confirms; `reload` rescans
  user-global; `upgrade <name>` reinstalls from `source-url`.
- **Primary failure modes**: `install` accepts unsafe content without confirm;
  `reload` rescans bundled (it should not); `upgrade` ignores stored URL; symlink
  escapes user dir.
- **Required test depth**: real `/skills install` of a skill file; assert security
  scan ran, file copied, registry reloaded.
- **Spec**: skill.md §2 (Skill Management Commands)

### 2.10 `/memory` family

- **Entry**: `co_cli/commands/knowledge.py`
- **Behavior**: `list/count` query artifacts; `forget` deletes after confirm; `dream`
  runs cycle; `restore` brings archived artifact back; `decay-review` previews/runs
  decay; `stats` prints corpus counts + last-dream timestamp.
- **Primary failure modes**: `forget` deletes without confirm; `restore` ambiguity
  silently picks one; `stats` shows wrong cumulative counters.
- **Required test depth**: real artifacts + dream state; exercise each subcommand;
  assert observable filesystem and DB state.
- **Spec**: memory.md §3.2, dream.md §2.8

### 2.11 `/approvals` view + clear

- **Entry**: `co_cli/commands/approvals.py`
- **Behavior**: Lists session approval rules, allows clear-all or per-rule clear.
- **Primary failure modes**: `clear` does not clear; list returns stale data after
  in-turn additions.
- **Required test depth**: add real rules via approval flow, then `/approvals list`,
  then `clear`; assert state mutations.
- **Spec**: tui.md §3

### 2.12 `/reasoning` mode toggle

- **Entry**: `co_cli/commands/reasoning.py`
- **Behavior**: Reads/writes `deps.session.reasoning_display` (`off` | `summary` | `full`).
  `next`/`cycle` advances. Read by `StreamRenderer` at next turn start.
- **Primary failure modes**: in-flight stream switches mode (must not); invalid mode
  accepted; child agent does not inherit via `fork_deps`.
- **Required test depth**: set mode via command, run a turn, assert renderer used the
  set mode.
- **Spec**: tui.md §2.5

### 2.13 `/background` and `/tasks` / `/cancel`

- **Entry**: `co_cli/commands/background.py`, `co_cli/commands/tasks.py`, `co_cli/commands/cancel.py`
- **Behavior**: `/background <cmd>` spawns detached shell process; `/tasks` lists or
  shows detail; `/cancel <id>` SIGTERM→SIGKILL.
- **Primary failure modes**: detail lookup by 12-hex-char ID; cancel does not actually
  terminate; orphaned process group.
- **Required test depth**: spawn a real sleep process, list it, cancel it; assert
  exit signal and removed-from-list.
- **Spec**: tui.md §3, tools.md §3 (Execution, Jobs & Delegation)

### 2.14 `/history` (delegation history)

- **Entry**: `co_cli/commands/history.py`
- **Behavior**: Shows sub-agent + background invocations from current session.
- **Primary failure modes**: ordering wrong; sub-agent invocations missed.
- **Required test depth**: run a delegation tool, then `/history`; assert entry
  present.
- **Spec**: tui.md §3

### 2.15 `/tools` listing

- **Entry**: `co_cli/commands/tools.py`
- **Behavior**: Lists registered native + MCP tools with descriptions and visibility.
- **Primary failure modes**: deferred tools missing from listing; MCP tools missing.
- **Required test depth**: load real tool registry; assert representative native +
  MCP entries appear.
- **Spec**: tui.md §3

### 2.16 `/help` listing

- **Entry**: `co_cli/commands/help.py: _cmd_help`
- **Behavior**: Renders a table of every built-in slash command plus every
  user-invocable skill command (with description and optional argument hint).
- **Primary failure modes**: built-in command missing from the table;
  user-invocable skill omitted; non-user-invocable skill surfaced; argument
  hint dropped.
- **Required test depth**: real `BUILTIN_COMMANDS` registry + real
  `skill_commands` dict mixing user-invocable and internal-only entries;
  assert listing contents and hint formatting.
- **Spec**: tui.md §3

---

## 3. Foreground Turn Orchestration

### 3.1 One-turn execution (`run_turn`)

- **Entry**: `co_cli/context/orchestrate.py: run_turn`
- **Behavior**: Resets per-turn runtime state, drives one or more
  `_execute_stream_segment` calls, handles approval loop / overflow / interrupt /
  HTTP errors, returns `TurnResult`.
- **Primary failure modes**: per-turn reset missed (`compaction_applied_this_turn`
  bleeds across turns); error path returns wrong outcome; interrupt builder drops
  paired tool calls incorrectly.
- **Required test depth**: drive `run_turn()` with a real agent + real model; assert
  outcome on golden path and on a forced HTTP error.
- **Spec**: core-loop.md §1, §2.1

### 3.2 Stream segment execution

- **Entry**: `co_cli/context/orchestrate.py: _execute_stream_segment`
- **Behavior**: One `agent.run_stream_events` call wrapped in
  `asyncio.timeout(_LLM_SEGMENT_HANG_TIMEOUT_SECS)`. Per-event handling for
  `PartStartEvent` / `PartDeltaEvent` / `FunctionToolCallEvent` /
  `FunctionToolResultEvent` / `AgentRunResultEvent`.
- **Primary failure modes**: `latest_result` left unset (must raise `RuntimeError`);
  buffers not flushed in `finally`; `latest_usage` not merged into runtime.
- **Required test depth**: real streaming run; assert latest_result populated,
  buffers flushed, usage merged.
- **Spec**: core-loop.md §2.2

### 3.3 Tool approval loop

- **Entry**: `co_cli/context/orchestrate.py: _run_approval_loop`
- **Behavior**: While `latest_result.output is DeferredToolRequests`, sets
  `runtime.resume_tool_names`, collects approvals via
  `_collect_deferred_tool_approvals`, feeds back into next segment as
  `tool_approval_decisions`. Clears `resume_tool_names` on exit.
- **Primary failure modes**: `resume_tool_names` left stale on exit; native + MCP
  tools handled inconsistently; nested DeferredToolRequests mishandled.
- **Required test depth**: real approval-gated tool with auto-approve and deny
  paths; assert resume cleared.
- **Spec**: core-loop.md §2.3

### 3.4 Approval subject resolution + remembered rule match

- **Entry**: `co_cli/tools/approvals.py: resolve_approval_subject` + `is_auto_approved`
- **Behavior**: Maps tool shape → `ApprovalSubject` (`shell` first token / `path`
  parent dir / `domain` hostname / `tool` name). Checks
  `deps.session.session_approval_rules` for exact `kind+value` match.
- **Primary failure modes**: subject kind misclassified; first-token logic ignores
  pipes; remember-on-`a` fails to persist.
- **Required test depth**: per tool shape (shell, file_write, web_fetch, MCP); assert
  subject and rule-match outcome.
- **Spec**: core-loop.md §2.3

### 3.5 Clarify (question prompt)

- **Entry**: `co_cli/tools/system/user_input.py: clarify` + `_collect_deferred_tool_approvals` clarify path
- **Behavior**: When metadata contains `questions`, prompts each via
  `frontend.prompt_question`, collects answers, encodes as
  `ToolApproved(override_args={"user_answers": [...]})`.
- **Primary failure modes**: answer order misaligned to questions; multi-select
  truncated; clarify path enters approval flow.
- **Required test depth**: real clarify with multiple questions; assert answers
  positionally aligned in resume payload.
- **Spec**: core-loop.md §2.3, tools.md §3

### 3.6 Context overflow recovery

- **Entry**: `co_cli/context/compaction.py: recover_overflow_history` (called from `run_turn` overflow branch)
- **Behavior**: On HTTP 413 or 400+overflow-evidence, strips all `ToolReturnPart`s to
  per-tool markers. PATH 1: if stripped fits, `commit_compaction` and retry. PATH 2:
  else plan + `compact_messages` + `commit_compaction`. Gated by
  `overflow_recovery_attempted` (one-shot).
- **Primary failure modes**: PATH 1 still calls LLM; PATH 2 fails on second overflow
  but does not return terminal; one-shot gate not honored; thrash state not reset.
- **Required test depth**: simulate real HTTP 413 with mock-free provider; assert
  PATH 1 vs PATH 2 selection and thrash reset.
- **Spec**: compaction.md §2.7

### 3.7 HTTP 400 reformulation budget

- **Entry**: `co_cli/context/orchestrate.py: run_turn` (400 non-overflow branch)
- **Behavior**: When HTTP 400 has reformulation budget, appends a reflection request
  describing the rejected tool call, decrements `tool_reformat_budget`, retries.
- **Primary failure modes**: budget never decrements; reflection appended as user
  message; falls through to overflow path on real overflow.
- **Required test depth**: simulate 400 + N retries; assert budget exhaustion path.
- **Spec**: core-loop.md §2.5

### 3.8 Interrupt handling

- **Entry**: `co_cli/context/orchestrate.py: _build_interrupted_turn_result`
- **Behavior**: On `KeyboardInterrupt` / `CancelledError`, drops trailing
  `ModelResponse` with unanswered `ToolCallPart`s, appends abort marker
  `ModelRequest` so next turn knows to verify state.
- **Primary failure modes**: paired tool calls left unbalanced; abort marker missing;
  outcome flag wrong.
- **Required test depth**: cancel mid-segment; assert dropped trailing response and
  abort marker present.
- **Spec**: core-loop.md §2.5

### 3.9 Output limit checks (length finish + ctx ratio)

- **Entry**: `co_cli/context/orchestrate.py: _check_output_limits`
- **Behavior**: After successful segment, if `finish_reason == "length"` shows
  truncation status; if `deps.model_max_ctx` set, checks
  `latest_response_input_tokens / model_max_ctx` against `ctx_warn_threshold` and
  `ctx_overflow_threshold`.
- **Primary failure modes**: length warning suppressed; threshold inverted;
  `model_max_ctx=None` divides by zero.
- **Required test depth**: real result with length finish and high input_tokens;
  assert status messaging.
- **Spec**: core-loop.md §2.5

### 3.10 Transcript persistence + child-session branching

- **Entry**: `co_cli/main.py: _finalize_turn` → `co_cli/memory/transcript.py: persist_session_history`
- **Behavior**: Normal turn appends tail. When `compaction_applied_this_turn`,
  rewrites session file in place (truncate + write). `persisted_message_count` is
  the durability cursor.
- **Primary failure modes**: rewrite path appends instead; cursor not advanced;
  session file partially written on crash.
- **Required test depth**: real transcript + real compaction; assert file rewritten
  with compacted state.
- **Spec**: memory.md §2.1, compaction.md §1.1

### 3.11 Reasoning display modes (`off` / `summary` / `full`)

- **Entry**: `co_cli/display/stream_renderer.py: StreamRenderer`
- **Behavior**: `off` discards thinking; `summary` reduces to short status lines via
  `on_reasoning_progress`; `full` streams raw thinking.
- **Primary failure modes**: `off` mode emits a partial line; `summary` drops too
  aggressively; `full` does not flush at end.
- **Required test depth**: real renderer per mode with synthesized
  `ThinkingPartDelta`; assert output shape per mode.
- **Spec**: core-loop.md §2.2, tui.md §2.5

### 3.12 Doom-loop and reflection-cap injection

- **Entry**: `co_cli/agents/_instructions.py: safety_prompt`
- **Behavior**: Detects identical-tool-call streak (`doom_loop_threshold`) or
  shell-error streak (`max_reflections`); injects warning text into instructions
  context.
- **Primary failure modes**: streak counter never resets; warning not injected;
  threshold off-by-one.
- **Required test depth**: synthesize streak in real `safety_state`; assert warning
  in dynamic instruction output.
- **Spec**: prompt-assembly.md §2.4, core-loop.md §2.4

---

## 4. Prompt Assembly

### 4.1 Static instruction assembly

- **Entry**: `co_cli/context/assembly.py: build_static_instructions`
- **Behavior**: Joins seed + mindsets + numbered rules + recency advisory +
  toolset-guidance + category-awareness + soul critique. Stable for the session.
- **Primary failure modes**: any block out of order; rule files missed; recency
  advisory dropped from cacheable prefix; critique placed before operational guidance.
- **Required test depth**: real personality + real rules dir; assert exact block
  order and content.
- **Spec**: prompt-assembly.md §2.1

### 4.2 Toolset guidance gating

- **Entry**: `co_cli/context/guidance.py: build_toolset_guidance`
- **Behavior**: Emits per-tool guidance blocks only when the tool is registered.
  Currently: `MEMORY_GUIDANCE` (gated on `memory_search`), `CAPABILITIES_GUIDANCE`
  (gated on `capabilities_check`).
- **Primary failure modes**: guidance emitted for absent tool; missing for present
  tool; ordering inconsistent.
- **Required test depth**: build guidance with each tool absent/present; assert block
  appears or not.
- **Spec**: prompt-assembly.md §2.1

### 4.3 Category awareness prompt for deferred tools

- **Entry**: `co_cli/tools/deferred_prompt.py: build_category_awareness_prompt`
- **Behavior**: One-sentence hint listing `VisibilityPolicyEnum.DEFERRED` categories
  reachable via `search_tools`. Empty when no deferred tools exist.
- **Primary failure modes**: always-visible tools listed; categories hardcoded
  instead of derived.
- **Required test depth**: build with mixed visibility tools; assert listed
  categories match `DEFERRED` set.
- **Spec**: prompt-assembly.md §2.1

### 4.4 Dynamic instruction layers (`safety_prompt`, `current_time_prompt`)

- **Entry**: `co_cli/agents/_instructions.py`
- **Behavior**: Two `@agent.instructions` callbacks evaluated per request.
  `current_time_prompt` is always-on (Block 1, tail position). `safety_prompt`
  conditionally returns warning text. Neither is persisted into history.
- **Primary failure modes**: append-only invariant violated (per-turn variance in
  Block 0 invalidates prefix cache); current time stale across requests;
  safety_prompt result stored back to history.
- **Required test depth**: per-request build; assert callbacks fired and Block 0
  cache-stable.
- **Spec**: prompt-assembly.md §2.2, §2.3

---

## 5. History Processors

### 5.1 `dedup_tool_results`

- **Entry**: `co_cli/context/history_processors.py: dedup_tool_results`
- **Behavior**: Pre-tail region only. Collapses identical `(tool_name, sha256(content))`
  returns into back-references pointing at the latest `tool_call_id`. Eligibility:
  string content ≥ 200 chars.
- **Primary failure modes**: protected tail mutated; non-string content corrupted;
  back-reference points to wrong `tool_call_id`.
- **Required test depth**: real history with duplicates and last-turn protection;
  assert collapse behavior and tail untouched.
- **Spec**: compaction.md §2.3

### 5.2 `evict_old_tool_results`

- **Entry**: `co_cli/context/history_processors.py: evict_old_tool_results`
- **Behavior**: Pre-tail region only. Keeps 5 most-recent per tool name (counted
  per `COMPACTABLE_TOOLS`). Replaces older with `semantic_marker` carrying
  tool_name + 1–3 args + size/outcome signal.
- **Primary failure modes**: non-compactable tool affected (e.g. memory_create);
  protected tail mutated; markers lose `tool_call_id` pairing; counter off-by-one.
- **Required test depth**: real history with 6+ shell + 1 memory_create; assert only
  shell evicted, memory_create untouched, pairing preserved.
- **Spec**: compaction.md §2.3

### 5.3 `enforce_request_size` (L2 force-spill)

- **Entry**: `co_cli/context/history_processors.py: enforce_request_size`
- **Behavior**: Walks full message list, force-spills largest unspilled
  `ToolReturnPart`s until aggregate ≤ `deps.spill_threshold_tokens`. Skip cases:
  below threshold / no candidates / all-spilled / fallback-to-summarize.
- **Primary failure modes**: protected tail respected (it should not be — recency is
  evict's job); already-spilled re-counted; cross-batch accumulation missed.
- **Required test depth**: real multi-batch history; assert largest-first spill,
  span emitted, fallback path on insufficient spill.
- **Spec**: compaction.md §2.4

### 5.4 `proactive_window_processor` (L3 LLM compaction)

- **Entry**: `co_cli/context/compaction.py: proactive_window_processor`
- **Behavior**: Six steps: token count (max local/reported) → gates (threshold +
  anti-thrash) → boundary planner → assembly via `compact_messages` → savings calc
  → `commit_compaction`. Static-marker fallback when gate closed or model=None.
- **Primary failure modes**: anti-thrash window not honored; circuit breaker probe
  cadence wrong; commit happens before assembly; runtime fields written when
  exception raised mid-flow.
- **Required test depth**: real LLM-backed compaction at threshold; below-threshold
  fast path; circuit-breaker tripped; anti-thrash gate blocking.
- **Spec**: compaction.md §2.5

### 5.5 `sanitize_surrogate_codepoints`

- **Entry**: `co_cli/context/history_processors.py: sanitize_surrogate_codepoints`
- **Behavior**: Last in pipeline. Replaces lone Unicode surrogates (U+D800–U+DFFF)
  with U+FFFD across all parts including summary text from upstream processor.
- **Primary failure modes**: ordering before `proactive_window_processor` (would miss
  summary text); valid surrogate pairs broken; non-string content mutated.
- **Required test depth**: history with embedded lone surrogate in `ToolReturnPart`
  and `TextPart`; assert sanitized output.
- **Spec**: compaction.md §2.3

---

## 6. Compaction (Layered Budget)

### 6.1 L0 admission cap (tool calls per ModelResponse)

- **Entry**: `co_cli/tools/lifecycle.py: wrap_tool_execute` + `co_cli/agents/tool_call_limit.py`
- **Behavior**: Counts tool calls per `ctx.run_step`; first 6 execute, calls 7+
  rejected with structured `max_tool_calls_per_turn_exceeded` payload returned as
  the tool's "result". Span emitted on `after_node_run`.
- **Primary failure modes**: counter not reset on `run_step` change; rejection runs
  the tool; payload missing guidance; cap value drift.
- **Required test depth**: real lifecycle with 7+ tool calls in one
  `ModelResponse`; assert 6 executed, 1 rejected with proper payload.
- **Spec**: compaction.md §2.1

### 6.2 L1 emit-time spill (`spill_if_oversized`)

- **Entry**: `co_cli/tools/tool_io.py: spill_if_oversized` (called by `tool_output`)
- **Behavior**: When `len(content) > spill_threshold_chars` (default 4_000), writes
  to `tool-results/<sha16>.txt`, returns `<persisted-output>` placeholder with
  preview. `file_read` exempted (`math.inf`).
- **Primary failure modes**: file_read recurses spill; preview truncated mid-line;
  filename not content-addressed; below-threshold inputs spilled.
- **Required test depth**: real oversized result and below-threshold; assert spill
  vs pass-through; assert preview shape.
- **Spec**: compaction.md §2.2

### 6.3 Boundary planner (`plan_compaction_boundaries`)

- **Entry**: `co_cli/context/_compaction_boundaries.py: plan_compaction_boundaries`
- **Behavior**: Walks turn groups from end, accumulates tokens until `tail_fraction
  × budget` or `_MIN_RETAINED_TURN_GROUPS=1` reached. Last group retained
  unconditionally. Returns `None` when planner cannot find a boundary.
- **Primary failure modes**: `None` not returned for single-turn history; oversized
  last group dropped; head/tail overlap not detected.
- **Required test depth**: real multi-turn history of varying sizes; assert
  boundaries and `None` cases.
- **Spec**: compaction.md §2.5

### 6.4 Marker assembly + enrichment

- **Entry**: `co_cli/context/_compaction_markers.py: build_compaction_marker` + `gather_compaction_context`
- **Behavior**: Assembles `head | marker | [todo_snapshot] | [search breadcrumbs] | tail`.
  `has_tail=True` for proactive/overflow; `has_tail=False` for `/compact`. Enrichment
  pulls active todos (≤10, ≤1.5K chars).
- **Primary failure modes**: marker shape wrong per `has_tail`; breadcrumbs include
  unpaired tool_call_id; todo cap exceeded.
- **Required test depth**: build markers per `has_tail` and per todo state; assert
  shape and caps.
- **Spec**: compaction.md §2.6

### 6.5 Summarizer LLM call

- **Entry**: `co_cli/context/summarization.py: summarize_messages`
- **Behavior**: Direct `llm_call`, no tools, `_SUMMARIZE_PROMPT`. Prior summaries
  passed via `message_history` as `SUMMARY_MARKER_PREFIX`. Carry-forward rule
  handles PENDING → RESOLVED across cycles.
- **Primary failure modes**: prior summary embedded in prompt instead of message
  history; carry-forward rule lost between cycles; tool access leaked into call.
- **Required test depth**: real LLM-backed summarize with prior summary; assert
  carry-forward and no tool calls in trace.
- **Spec**: compaction.md §2.6

### 6.6 Token estimation (`max(local, reported)`)

- **Entry**: `co_cli/context/summarization.py: estimate_message_tokens`, `latest_response_input_tokens`
- **Behavior**: Local char-based estimate floored against latest `ModelResponse.usage.input_tokens`.
  Max-floor ensures stale provider count cannot suppress trigger.
- **Primary failure modes**: `compaction_applied_this_turn` not zero-ing reported
  count; ToolCallPart args not counted; empty list returns non-zero.
- **Required test depth**: real history including `ToolCallPart.args`; assert local
  estimate counts args and max-floor against reported.
- **Spec**: compaction.md §2.5

### 6.7 Compaction commit (`commit_compaction`)

- **Entry**: `co_cli/context/compaction.py: commit_compaction`
- **Behavior**: Sole writer of `compaction_applied_this_turn`,
  `post_compaction_token_estimate`, `message_count_at_last_compaction`. Token
  estimate computed before any field write.
- **Primary failure modes**: partial commit on token-estimator failure; field
  writers exist outside this function.
- **Required test depth**: simulate token-estimator failure inside commit; assert
  no field changed.
- **Spec**: compaction.md §1.5, §2.6

---

## 7. Tool Lifecycle & Execution

### 7.1 Native tool registration

- **Entry**: `co_cli/tools/agent_tool.py: agent_tool` decorator + `TOOL_REGISTRY`
- **Behavior**: Self-populating registry. Each `@agent_tool` registers at import time
  with `ToolInfo` metadata (visibility, approval, lock, gate).
- **Primary failure modes**: tool not in `tool_index` after import; metadata
  conflicts; visibility default wrong.
- **Required test depth**: import a real tool module; assert presence and metadata.
- **Spec**: tools.md §1, §3

### 7.2 MCP tool discovery

- **Entry**: `co_cli/agents/mcp.py: discover_mcp_tools`
- **Behavior**: Runtime discovery from connected MCP servers. All discovered tools
  are DEFERRED. Merged into `tool_index`.
- **Primary failure modes**: merge clobbers native tool name; visibility wrong;
  failed-server tools leaked.
- **Required test depth**: real MCP connection; assert discovered tools merged with
  DEFERRED visibility.
- **Spec**: tools.md §1

### 7.3 Lifecycle hook chain

- **Entry**: `co_cli/tools/lifecycle.py: CoToolLifecycle`
- **Behavior**: 4 hooks fire per call: `before_node_run` (dedup tool calls in same
  ModelResponse), `before_tool_validate` (JSON repair), `before_tool_execute` (path
  normalization), `after_tool_execute` (MCP spill + telemetry enrichment).
- **Primary failure modes**: dedup keeps wrong copy; JSON repair corrupts valid
  args; path normalization escapes workspace; telemetry attributes missing.
- **Required test depth**: real lifecycle with duplicate/malformed/relative-path
  inputs; assert each hook's mutation.
- **Spec**: tools.md §2

### 7.4 Sequential locking + cross-agent path locking

- **Entry**: `co_cli/deps.py: ResourceLockStore` (referenced as `deps.resource_locks`)
- **Behavior**: Mutating tools (`file_write`, `file_patch`, `code_execute`) registered
  with `is_concurrent_safe=False`. Cross-agent path locks fail-fast on contention.
- **Primary failure modes**: parallel mutations slip through; lock leaked across
  turns; contention not surfaced as `tool_error`.
- **Required test depth**: real concurrent mutation attempts; assert sequential
  ordering and contention error.
- **Spec**: tools.md §2 (Concurrency Safety)

### 7.5 Read-before-write enforcement (`file_patch`)

- **Entry**: `co_cli/tools/files/write.py: file_patch` + `deps.file_partial_reads`
- **Behavior**: Patch fails if file was not read in full prior, or only a snippet was
  read.
- **Primary failure modes**: partial-read patches succeed; staleness check skipped.
- **Required test depth**: real patch attempts after no-read / partial-read /
  full-read; assert allow/deny.
- **Spec**: tools.md §2 (Concurrency Safety)

### 7.6 Staleness tracking (`file_tracker`)

- **Entry**: `co_cli/deps.py: file_tracker` + checked in `file_write`/`file_patch`
- **Behavior**: Reads snapshot mtime; writes/patches fail if disk mtime advanced
  before commit.
- **Primary failure modes**: mtime not snapshotted; staleness check skipped.
- **Required test depth**: real read → external touch → write; assert failure.
- **Spec**: tools.md §2

---

## 8. Tools — Files

### 8.1 `file_find`

- **Entry**: `co_cli/tools/files/read.py: file_find`
- **Behavior**: List directory or find files by glob pattern; capped at `max_entries`.
- **Primary failure modes**: pattern interpreted as regex; cap not enforced; symlink
  loops crash.
- **Required test depth**: real workspace with patterns; assert listing.
- **Spec**: tools.md §3 (Workspace & File Operations)

### 8.2 `file_read` (line range, cap, fuzzy not-found)

- **Entry**: `co_cli/tools/files/read.py: file_read`
- **Behavior**: Reads workspace file. 500-line default cap with continuation hint;
  500 KB full-read gate; 2000-char per-line truncation; fuzzy name suggestions on
  not-found.
- **Primary failure modes**: cap not enforced; line range off-by-one; fuzzy
  suggestions point outside workspace; truncation not signaled.
- **Required test depth**: real files of various sizes; assert all four behaviors.
- **Spec**: tools.md §3

### 8.3 `file_search` (regex content search)

- **Entry**: `co_cli/tools/files/read.py: file_search`
- **Behavior**: Regex content search with glob-limited file set, configurable
  context_lines, head_limit, offset. `output_mode="content"` default.
- **Primary failure modes**: glob ignored (searches everything); head_limit
  off-by-one; offset not honored.
- **Required test depth**: real workspace; assert glob, context, pagination.
- **Spec**: tools.md §3

### 8.4 `file_write` (approval + lock)

- **Entry**: `co_cli/tools/files/write.py: file_write`
- **Behavior**: Approval-gated, sequentially locked. Subject = parent dir.
- **Primary failure modes**: approval bypassed; concurrent write slips; staleness
  ignored.
- **Required test depth**: real write with auto-approve; assert content + staleness
  check.
- **Spec**: tools.md §3

### 8.5 `file_patch` (read-before-write + auto-lint)

- **Entry**: `co_cli/tools/files/write.py: file_patch`
- **Behavior**: Targeted replacement with fuzzy fallback; `show_diff` flag;
  `.py` files auto-linted post-patch.
- **Primary failure modes**: fuzzy fallback corrupts target; auto-lint skipped on
  syntax error path; show_diff malformed.
- **Required test depth**: real patches across exact/fuzzy/multi-match cases.
- **Spec**: tools.md §3

---

## 9. Tools — Web

### 9.1 `web_search` (Brave)

- **Entry**: `co_cli/tools/web/search.py: web_search`
- **Behavior**: Brave API call with optional domain filter. Results capped by
  `max_results`. Gated on `brave_search_api_key`.
- **Primary failure modes**: API key not loaded; domain filter ignored; rate limit
  not surfaced.
- **Required test depth**: real Brave API call (skip if key absent); assert filter.
- **Spec**: tools.md §3

### 9.2 `web_fetch` (URL fetch + format)

- **Entry**: `co_cli/tools/web/fetch.py: web_fetch`
- **Behavior**: Fetches URL; `format` controls output (`markdown` default, `html`,
  `text`). Domain allowlist/blocklist applied. Approval subject = hostname.
- **Primary failure modes**: format not honored; allowlist bypassed; redirect leaks
  to blocked domain.
- **Required test depth**: real fetch per format; allow/block list scenarios.
- **Spec**: tools.md §3

---

## 10. Tools — Shell, Tasks, Delegation

### 10.1 `shell` command-shape policy

- **Entry**: `co_cli/tools/shell/execute.py: shell` + `co_cli/tools/shell_policy.py`
- **Behavior**: Classifies `cmd` as `DENY` / `ALLOW` (safe-prefix) / `REQUIRE_APPROVAL`.
  Only `REQUIRE_APPROVAL` reaches deferred approval. `workdir` blocks traversal.
  Hard cap by `shell.max_timeout`.
- **Primary failure modes**: destructive command auto-approved; safe-prefix list
  drift; workdir traversal escapes workspace; timeout cap not enforced.
- **Required test depth**: per-policy class; assert approval/deny/run.
- **Spec**: tools.md §3, core-loop.md §2.3

### 10.2 `code_execute`

- **Entry**: `co_cli/tools/code/execute.py: code_execute`
- **Behavior**: Run interpreter command with same approval policy as shell. Locked
  (sequential).
- **Primary failure modes**: same as shell + lock leak.
- **Required test depth**: real interpreter invocation with approval; assert lock.
- **Spec**: tools.md §3

### 10.3 Background tasks (`task_start/status/cancel/list`)

- **Entry**: `co_cli/tools/tasks/*` + `deps.session.background_tasks`
- **Behavior**: `task_start` spawns detached process group, returns `task_id`.
  `task_status` polls stdout/stderr/state. `task_cancel` does SIGTERM→SIGKILL.
  `task_list` enumerates with optional status filter.
- **Primary failure modes**: orphaned process group; tail_lines off-by-one;
  task_id collision; SIGKILL never sent.
- **Required test depth**: real `sleep` task; full lifecycle; assert state
  transitions.
- **Spec**: tools.md §3

### 10.4 Delegation subagents (`web_research`, `knowledge_analyze`, `reason`)

- **Entry**: `co_cli/tools/delegation/*` + `co_cli/deps.py: fork_deps`
- **Behavior**: Each spawns a subagent with `fork_deps()` and an isolated tool
  surface. `web_research` has web tools; `knowledge_analyze` has memory + drive
  tools; `reason` has no external tools.
- **Primary failure modes**: parent runtime mutated by child; reasoning_display
  not inherited; max_requests cap not enforced; child tools exceed scope.
- **Required test depth**: real delegation with each subagent; assert isolation
  and tool scope.
- **Spec**: tools.md §3, core-loop.md §2.7

### 10.5 Session todos (`todo_write` / `todo_read`)

- **Entry**: `co_cli/tools/todo/rw.py: todo_write` + `todo_read`
- **Behavior**: `todo_write` replaces (`merge=False`) or merges-by-id
  (`merge=True`) the per-session todo list on `deps.session.session_todos`.
  Each item has `id`, `content`, `status ∈ {pending, in_progress, completed}`,
  `priority`. At most one `in_progress` item allowed per session. `todo_read`
  returns the formatted list plus pending/in-progress counts for end-of-turn
  verification.
- **Primary failure modes**: multiple `in_progress` items accepted; duplicate
  ids accepted; empty `content` accepted; unknown status/priority accepted;
  merge silently discards unknown ids; replace doesn't clear prior items.
- **Required test depth**: real `RunContext` with a fresh `deps.session`;
  exercise replace, merge, the single-in_progress invariant, and read-after-write.
- **Spec**: tools.md §3, self-planning.md

---

## 11. Tools — External Integrations (gate-conditional)

Each integration is gated on a config setting; absence skips registration.

### 11.1 Obsidian (`obsidian_list/search/read`)

- **Entry**: `co_cli/tools/obsidian/*`; gated on `obsidian_vault_path`
- **Behavior**: List/search/read Obsidian vault notes with tag/folder filters.
- **Primary failure modes**: vault path not honored; tag filter ignored; vault
  escapes blocked.
- **Required test depth**: real vault fixture (or skip if path unset); assert list,
  search, read.
- **Spec**: tools.md §3

### 11.2 Google Drive / Gmail / Calendar

- **Entry**: `co_cli/tools/google/*`; gated on `google_credentials_path`
- **Behavior**: Drive search/read; Gmail list/search/draft (draft = approval-gated);
  Calendar list/search.
- **Primary failure modes**: ADC fallback not honored; draft sends instead of
  drafts; calendar window inverted; OAuth refresh fails silently.
- **Required test depth**: skipif credentials absent; otherwise real API calls;
  assert each surface.
- **Spec**: tools.md §3

---

## 12. Memory — Knowledge Channel

### 12.1 `memory_search` canon priority pass

- **Entry**: `co_cli/tools/memory/recall.py: _search_artifacts` (Pass 1)
- **Behavior**: When personality set and canon kind allowed, BM25 search over
  `source='canon'` with full-body fetch via `get_chunk_content('canon', path, 0)`.
  Inline full body in `snippet` field — no follow-up `file_read` needed.
- **Primary failure modes**: snippet truncated (must be full body); FTS5 `snippet()`
  used instead of stored chunk; canon excluded by `kinds=['user']` not honored.
- **Required test depth**: real personality + real canon files; assert full-body
  inline and kind isolation.
- **Spec**: memory.md §4

### 12.2 `memory_search` user priority pass

- **Entry**: `co_cli/tools/memory/recall.py: _search_artifacts` (Pass 2)
- **Behavior**: BM25 over `source='knowledge'` with `kinds=['user']`, capped at
  `_ARTIFACTS_USER_CAP`.
- **Primary failure modes**: cap exceeded; cross-kind leakage; cap value drift.
- **Required test depth**: real artifacts of mixed kinds; assert user-only at cap.
- **Spec**: memory.md §3.2

### 12.3 `memory_search` waterfall (rule/article/note, dual-cap)

- **Entry**: `co_cli/tools/memory/recall.py: _search_artifacts` (Pass 3)
- **Behavior**: Searches non-priority kinds with dual cap: stop at chunk count OR
  cumulative size, whichever first.
- **Primary failure modes**: only one cap honored; canon/user leak through;
  cumulative size off-by-one.
- **Required test depth**: real artifacts forcing both caps separately; assert
  whichever-first.
- **Spec**: memory.md §3.2

### 12.4 `knowledge_search` grep fallback (store=None)

- **Entry**: `co_cli/tools/memory/recall.py: _grep_recall`
- **Behavior**: When `memory_store is None` (e.g. `search_backend='grep'`),
  in-memory substring match over title + content. Canon excluded.
- **Primary failure modes**: canon included in grep path; case-sensitivity drift;
  whole-file load fails on large dir.
- **Required test depth**: real artifacts dir with grep backend; assert canon
  exclusion.
- **Spec**: memory.md §3.2

### 12.5 `knowledge_search` browse mode (empty query)

- **Entry**: `co_cli/tools/memory/recall.py: knowledge_search` (empty `query`)
- **Behavior**: Returns recent-session metadata + artifact inventory; no FTS, no
  LLM. Excludes current session.
- **Primary failure modes**: current session included; artifact count wrong; no-LLM
  guarantee broken.
- **Required test depth**: empty query against real store; assert no-LLM and
  current-session exclusion.
- **Spec**: memory.md §2.3

### 12.6 `knowledge_manage` (create / append / replace / delete)

- **Entry**: `co_cli/tools/memory/manage.py: knowledge_manage`
- **Behavior**: Unified write surface with `action ∈ {create, append, replace, delete}`.
  `create` — writes new artifact, rejects unknown kinds; `append` — adds to body without
  overwriting; `replace` — requires target appears exactly once (ambiguous → error);
  `delete` — errors if artifact missing. Atomic write + reindex on each action.
- **Primary failure modes**: canon kind accepted on create; ambiguous target silently
  accepted on replace; missing artifact silently skipped on delete; append overwrites
  instead of extending; reindex skipped after write.
- **Required test depth**: real `MemoryStore` + real filesystem; one behavioral
  assertion per action.
- **Spec**: memory.md §3.3

### 12.8 `memory_store.sync_dir` hash-skip

- **Entry**: `co_cli/memory/memory_store.py: MemoryStore.sync_dir`
- **Behavior**: Parses frontmatter, SHA256 hash-skips unchanged files, chunks body,
  writes to `chunks` + `chunks_fts` (+ `chunks_vec` in hybrid). `no_chunk=True` for
  canon.
- **Primary failure modes**: hash-skip false negative; no_chunk ignored; partial
  failure leaves stale rows.
- **Required test depth**: real dir, sync, mutate, sync; assert second pass skips
  unchanged.
- **Spec**: memory.md §3.1

### 12.9 `knowledge_search(kinds=['skills'])` — removed channel guard

- **Entry**: `co_cli/tools/memory/recall.py: knowledge_search` (channel='skills' early-return)
- **Behavior**: Channel='skills' was removed and now returns a `tool_error` immediately:
  "channel='skills' is no longer supported — use skill_view or the manifest instead."
  Skills are their own surface accessible via `skill_view` / `skill_manage`.
- **Primary failure modes**: guard bypassed and request reaches FTS; error message absent
  or misleading; non-error return type returned for this channel.
- **Required test depth**: call `memory_search(channel='skills', query='anything')`; assert
  result is a tool error; assert no FTS rows returned.
- **Spec**: skill.md §2 (skills are a separate surface from memory)

---

## 13. Memory — Sessions Channel

### 13.1 Session transcript append (JSONL)

- **Entry**: `co_cli/memory/transcript.py: persist_session_history` (append path)
- **Behavior**: Appends `messages[persisted_message_count:]` as JSONL serialized via
  `ModelMessagesTypeAdapter`. Cursor advances. File `chmod 0o600`.
- **Primary failure modes**: cursor not advanced; permissions wrong; mid-write
  corruption.
- **Required test depth**: real transcript writes across multiple turns; assert
  cursor and content.
- **Spec**: memory.md §2.1

### 13.2 Session transcript rewrite on compaction

- **Entry**: `co_cli/memory/transcript.py: persist_session_history` (rewrite path)
- **Behavior**: When `history_compacted=True` (sourced from
  `runtime.compaction_applied_this_turn`), truncates and writes full compacted state.
- **Primary failure modes**: rewrite when cursor mismatched; partial rewrite on
  crash; double-rewrite same turn.
- **Required test depth**: real compaction → rewrite; assert file replaced
  atomically.
- **Spec**: memory.md §2.1, compaction.md §1.5

### 13.3 Session indexing (chunks + FTS5)

- **Entry**: `co_cli/memory/memory_store.py: index_session` + `co_cli/memory/session_chunker.py`
- **Behavior**: Parse uuid8 + created_at from filename; chunk via
  `flatten_session` + `chunk_flattened`; SHA256 hash-skip; index doc + chunks under
  `source='session'`.
- **Primary failure modes**: chunk overlap wrong; line-bounds off-by-one;
  hash-skip incorrect.
- **Required test depth**: real session file + real store; assert chunk shape,
  bounds, hash-skip.
- **Spec**: memory.md §2.3

### 13.4 `load_transcript` (50 MB cap, malformed-line skip)

- **Entry**: `co_cli/memory/transcript.py: load_transcript`
- **Behavior**: Loads `*.jsonl` skipping malformed lines. Refuses files > 50 MB.
- **Primary failure modes**: cap not enforced; malformed line aborts load; control
  records leaked.
- **Required test depth**: real file with malformed line + 50.1 MB file; assert
  skip and refusal.
- **Spec**: memory.md §2.1

### 13.5 Sessions channel recall

- **Entry**: `co_cli/tools/memory/recall.py: _search_sessions`
- **Behavior**: BM25 over `source='session'` (limit 15) → dedup to one best chunk
  per unique session (cap `_SESSIONS_CHANNEL_CAP=3`). Excludes current session.
- **Primary failure modes**: current session included; dedup keeps multiple chunks
  per session; cap exceeded.
- **Required test depth**: real sessions index + real query; assert dedup, cap,
  exclusion.
- **Spec**: memory.md §2.3

### 13.6 `session_search` model-callable tool

- **Entry**: `co_cli/tools/memory/recall.py: session_search`
- **Behavior**: Empty `query` → recent-N session metadata (id, when, title) browse
  mode (no FTS, no LLM). Non-empty `query` → BM25 chunk-cited search returning
  `(session_id, when, source, chunk_text, start_line, end_line, score)`. Excludes
  the current session.
- **Primary failure modes**: browse mode triggers FTS or LLM; current session
  surfaces in either mode; chunk citation fields missing; limit not honored;
  whitespace-only query treated as non-empty.
- **Required test depth**: real sessions index with ≥2 past sessions + current;
  assert browse-mode no-FTS guarantee, non-empty BM25 path, citation completeness,
  and current-session exclusion in both branches.
- **Spec**: memory.md §2.3

### 13.7 `session_view` verbatim turn loader

- **Entry**: `co_cli/tools/memory/view.py: session_view`
- **Behavior**: Reads a 1-indexed JSONL line range from a past session and returns
  per-line `{line, role, content_preview, tool_name}`. Refuses ranges over 200
  lines or content over 16KB (truncated=True). Validates `start_line >= 1` and
  `end_line >= start_line`.
- **Primary failure modes**: range cap bypassed; size cap bypassed; truncated
  flag not set when caps hit; invalid range silently coerced; content_preview
  leaks full body when truncated.
- **Required test depth**: real session JSONL fixture covering the range cap, the
  size cap, and a malformed range; assert refusal vs truncation behavior per case.
- **Spec**: memory.md §2.3

---

## 14. Dream Cycle

### 14.1 Manual dream trigger (`/memory dream`)

- **Entry**: `co_cli/commands/knowledge.py` → `co_cli/memory/dream.py: run_dream_cycle`
- **Behavior**: Runs full mine → merge → decay cycle, prints counts. `--dry`
  variant skips writes and state persistence.
- **Primary failure modes**: dry-run writes files; phase isolation broken;
  cumulative stats not updated.
- **Required test depth**: real cycle on a real session/artifact fixture; assert
  counts and dry-run no-write.
- **Spec**: dream.md §2.6

### 14.2 Auto dream trigger (session teardown)

- **Entry**: `co_cli/main.py: _maybe_run_dream_cycle`
- **Behavior**: When `consolidation_enabled=true` and trigger=`session_end`, runs
  cycle in teardown. Errors logged, never block shutdown.
- **Primary failure modes**: failure surfaces as shutdown error; runs even when
  disabled; wrong trigger mode honored.
- **Required test depth**: real teardown with toggled config; assert cycle ran/
  didn't run; assert no shutdown abort on dream failure.
- **Spec**: dream.md §2.1

### 14.3 Phase 1: transcript mining

- **Entry**: `co_cli/memory/dream.py: _mine_transcripts` (or per-phase function)
- **Behavior**: Tool-using miner Agent reads recent unprocessed sessions (capped by
  `consolidation_lookback_sessions`), windows them, calls `memory_create` for
  durable artifacts. Marks session processed only on completion.
- **Primary failure modes**: session marked processed on agent failure (lost retry);
  per-session save cap exceeded; window cap drift.
- **Required test depth**: real miner + real session; assert artifacts created and
  processed-list entries.
- **Spec**: dream.md §2.3

### 14.4 Phase 2: merge clustering + LLM call

- **Entry**: `co_cli/memory/dream.py: _merge_similar_artifacts`
- **Behavior**: Same-kind clustering by token-Jaccard; cap on clusters and
  per-cluster size; `llm_call` (no tools) for consolidated body; archive originals
  only after consolidated artifact durable.
- **Primary failure modes**: cross-kind merge; decay_protected merged; archive
  before consolidated written; consolidated body invents facts.
- **Required test depth**: real artifacts + real LLM; assert single-kind clusters
  and archive ordering.
- **Spec**: dream.md §2.4

### 14.5 Phase 3: decay archiving

- **Entry**: `co_cli/memory/decay.py` + `co_cli/memory/archive.py: archive_artifacts`
- **Behavior**: Selects candidates by age cutoff + `last_recalled` window; skips
  `decay_protected`; archives at most 20/cycle into `_archive/` with collision
  resolution. Removes active index rows.
- **Primary failure modes**: cap exceeded; protected archived; index rows leaked;
  collision overwrites.
- **Required test depth**: real artifacts of varying ages + real archive dir;
  assert selection and collision-suffix.
- **Spec**: dream.md §2.5

### 14.6 Dream state load/save

- **Entry**: `co_cli/memory/dream.py: load_dream_state` + `save_dream_state`
- **Behavior**: Reads/writes `knowledge/_dream_state.json`. Forgiving on corrupt
  state (returns fresh, logs warning).
- **Primary failure modes**: corrupt state aborts load; cumulative stats reset on
  corrupt instead of fresh; partial write on crash.
- **Required test depth**: load corrupt + missing + valid state files; assert
  forgiving + content.
- **Spec**: dream.md §2.2

### 14.7 Cycle timeout

- **Entry**: `co_cli/memory/dream.py: run_dream_cycle` (asyncio.timeout wrap)
- **Behavior**: Whole cycle wrapped in `asyncio.timeout()`. On timeout: result
  marked `timed_out=True`, partial counts returned, errors list populated.
- **Primary failure modes**: timeout swallowed; partial counts wrong; phase
  isolation broken on timeout.
- **Required test depth**: real cycle with synthesized slow phase; assert
  `timed_out=True` and partial result.
- **Spec**: dream.md §2.7

### 14.8 Restore archived artifact

- **Entry**: `co_cli/memory/archive.py: restore_artifact` (`/memory restore`)
- **Behavior**: Moves file from `_archive/` back to active dir, reindexes if store
  available. Ambiguous slug fails rather than guesses.
- **Primary failure modes**: ambiguous slug picks one silently; reindex skipped;
  filename collision overwrites.
- **Required test depth**: real archive with ambiguous + unambiguous slugs; assert
  fail/move + reindex.
- **Spec**: dream.md §2.8

---

## 15. Skills System

### 15.1 Skill containment check (`_is_safe_skill_path`)

- **Entry**: `co_cli/skills/loader.py: _is_safe_skill_path`
- **Behavior**: For user-global pass, resolves symlinks and verifies path is inside
  `root`. Symlinks escaping load root are skipped with warning.
- **Primary failure modes**: symlink to `/etc/passwd` loaded; bundled pass
  incorrectly applies check; resolution fails on circular symlink.
- **Required test depth**: real symlinks in/out of root; assert behavior.
- **Spec**: skill.md §2 (Containment Check)

### 15.2 Skill security scan

- **Entry**: `co_cli/skills/loader.py: scan_skill_content`
- **Behavior**: Static regex checks for credential exfil, pipe-to-shell, destructive
  shell, prompt injection. Different policy per path (load=warn, install=confirm,
  `skill_manage`=rollback).
- **Primary failure modes**: scan misses pattern; install without confirm; rollback
  fails to delete.
- **Required test depth**: real skill content per pattern + per path; assert
  detection and policy.
- **Spec**: skill.md §2 (Security Scan)

### 15.3 Skill manifest coverage (bundled + user-global)

- **Entry**: `co_cli/skills/loader.py: load_skills` → manifest injected into static prompt
- **Behavior**: All skills (bundled and user-installed) appear in the `<available_skills>`
  manifest block in the static prompt. Model discovers skills via manifest scan; no
  separate search tool is needed.
- **Primary failure modes**: user-installed skill missing from manifest; hidden skill
  (`disable-model-invocation`) surfaced in manifest; bundled skill omitted.
- **Required test depth**: real `load_skills()` with bundled + user skills; assert
  manifest contains expected entries and excludes hidden ones.
- **Spec**: skill.md §2, §3

### 15.4 `skill_view` (full body load)

- **Entry**: `co_cli/tools/system/skills.py: skill_view`
- **Behavior**: Returns full SKILL.md body inline (`spill_threshold_chars=inf`).
  Plugin-qualified `plugin:skill` names accepted (prefix stripped). `file_path`
  param returns error.
- **Primary failure modes**: spill triggered; plugin prefix not stripped;
  `file_path` silently accepted.
- **Required test depth**: real skill view including plugin prefix; assert inline
  body and `file_path` error.
- **Spec**: skill.md §3

### 15.5 `skill_manage` write operations

- **Entry**: `co_cli/tools/system/skills.py: skill_manage`
- **Behavior**: `create`/`edit`/`patch`/`delete`/`install`/`write_file`/`remove_file` for
  user-installed skills. Validates frontmatter, runs security scan, rolls back on flag,
  reloads registry. `install` fetches from a source URL; `write_file` and `remove_file`
  are reserved (return error). Bundled skills are read-only.
- **Primary failure modes**: bundled skill written; scan bypassed; rollback leaves
  partial file; reload skipped; `write_file`/`remove_file` silently accepted instead
  of erroring.
- **Required test depth**: real skill_manage per action incl. bundled-protection
  + scan-rollback; assert filesystem and registry state.
- **Spec**: skill.md §3 (Write tool)

### 15.6 `skill-env` blocked-key filter

- **Entry**: `co_cli/skills/loader.py: _SKILL_ENV_BLOCKED`
- **Behavior**: Filters `PATH`, `PYTHONPATH`, `HOME`, etc. out of `skill-env` before
  injection.
- **Primary failure modes**: blocked key leaks through; non-blocked key dropped.
- **Required test depth**: real skill with blocked + allowed keys; assert filtered
  set in `outcome.skill_env`.
- **Spec**: skill.md §2 (Security Scan)

---

## 16. Approvals

### 16.1 Approval prompt collection (`y` / `n` / `a`)

- **Entry**: `co_cli/context/orchestrate.py: _collect_deferred_tool_approvals` →
  `frontend.prompt_approval`
- **Behavior**: Prompts user per pending call; encodes choice into
  `DeferredToolResults` as `True` (approved) or `ToolDenied(...)` (denied).
  `a` choice triggers session-rule remember.
- **Primary failure modes**: `a` choice not remembered; denied returned as
  exception instead of `ToolDenied`; multi-call ordering scrambled.
- **Required test depth**: real prompt with each choice; assert encoded result.
- **Spec**: core-loop.md §2.3

### 16.2 Auto-approval via session rules

- **Entry**: `co_cli/tools/approvals.py: is_auto_approved`
- **Behavior**: Looks up exact `(kind, value)` match in
  `deps.session.session_approval_rules`; returns True without prompting.
- **Primary failure modes**: partial match auto-approves; rule kind misclassified;
  rule scope broader than intended.
- **Required test depth**: real rules per kind; assert exact-match behavior.
- **Spec**: core-loop.md §2.3

### 16.3 Tool-denial path (`ToolDenied`)

- **Entry**: deferred-results path in `_collect_deferred_tool_approvals`
- **Behavior**: Denied call resumes with `ToolDenied(reason)` payload; agent sees a
  structured denial rather than a crashed turn.
- **Primary failure modes**: denial crashes turn; payload missing; double-denial
  loop.
- **Required test depth**: real deny → resume → next turn; assert structured
  denial in `ToolReturnPart`.
- **Spec**: core-loop.md §2.3, tools.md §2

---

## 17. Observability

### 17.1 Span emission (`co.turn`, `co.tool`, `co.dream`)

- **Entry**: `co_cli/observability/*` + decorator/wrapper sites
- **Behavior**: Per-turn `co.turn` span; per-tool `co.tool.<name>` with attributes
  `result_size`, `source`, `requires_approval`; per-cycle `co.dream.cycle` envelope.
- **Primary failure modes**: span not closed on error path; attributes missing;
  duplicate root span.
- **Required test depth**: real run + real exporter; assert span tree shape and
  attributes.
- **Spec**: observability.md

### 17.2 SQLite span exporter + redaction

- **Entry**: `co_cli/observability/telemetry.py: SQLiteSpanExporter`
- **Behavior**: Persists spans to `~/.co-cli/co-cli-traces.db` for `co logs` and
  `co traces` viewers. PII redaction applied to attributes.
- **Primary failure modes**: secrets persisted unredacted; export fails silently;
  trace DB grows unbounded.
- **Required test depth**: real export with synthesized PII; assert redaction in
  persisted row.
- **Spec**: observability.md

### 17.3 Trace viewers (`co logs`, `co traces`)

- **Entry**: `co_cli/main.py` CLI subcommands → datasette / HTML viewer
- **Behavior**: `co logs` opens datasette table viewer; `co traces` renders nested
  HTML span tree.
- **Primary failure modes**: viewer crashes on empty DB; nested-span ordering
  wrong; redacted attribute rendered as raw value.
- **Required test depth**: real DB → viewer launch; assert rendered content (or
  smoke-only for the launcher).
- **Spec**: observability.md

### 17.4 `tool_budget.resolved` span

- **Entry**: `co_cli/bootstrap/core.py: _emit_tool_budget_span`
- **Behavior**: Emits a `tool_budget.resolved` OpenTelemetry span during bootstrap with
  attributes: `budget.context_window_tokens`, `budget.spill_ratio`,
  `budget.tool_call_limit`, `budget.spill_threshold_chars`,
  `budget.spill_threshold_tokens`.
- **Primary failure modes**: span not emitted on startup; attributes missing or wrong
  value; emitted outside the bootstrap tracer scope.
- **Required test depth**: real tracer with in-memory exporter; assert span name and
  all five attributes.
- **Spec**: bootstrap.md
- **Test**: `tests/test_flow_bootstrap_budget_span.py`

---

## 18. Personality

### 18.1 Personality static prompt assembly

- **Entry**: `co_cli/personality/prompts/loader.py: load_soul_seed`,
  `load_soul_mindsets`, `load_soul_critique`
- **Behavior**: Loads `souls/{role}/seed.md`, `mindsets/*.md`, `critique.md` for the
  active personality. Fed into static instructions.
- **Primary failure modes**: missing role silently substitutes default; mindset
  files loaded out of order; critique placed before operational guidance.
- **Required test depth**: real soul dir per role; assert load + ordering.
- **Spec**: personality.md, prompt-assembly.md §2.1

### 18.2 Personality discovery and validation

- **Entry**: `co_cli/personality/prompts/validator.py`
- **Behavior**: Discovers available personalities under `souls/`, validates each
  has required files (seed.md, etc.).
- **Primary failure modes**: invalid personality accepted at startup; required-file
  list drift; case-sensitivity bug on macOS.
- **Required test depth**: real soul dirs (valid + missing files); assert discovery
  + rejection.
- **Spec**: personality.md

---

## Coverage Audit Procedure (used by `/clean-tests`)

For each registered workflow:

1. From the Phase 2 catalog (test → production code path), find tests whose
   `production code path invoked` includes the workflow's **Entry**.
2. Classify:
   - **Covered** — ≥1 test drives the entry point and asserts on observable
     outcome aligned with at least one **Primary failure mode**
   - **Stub-covered** — test reaches the entry point but assertion is structural,
     truthy-only, or doesn't map to a listed failure mode
   - **Uncovered** — no test reaches the entry point
3. Append to coverage table; severity per the calibration above.
4. Tests with no workflow mapping → flag as **scope drift** (either workflow
   missing from this registry or test is misnamed).

Maintenance: when a new feature is added or a subsystem changes, update this file
*before* writing tests so coverage gaps surface immediately.
