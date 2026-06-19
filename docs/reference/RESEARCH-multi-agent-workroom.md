# RESEARCH: Multi-Agent Workroom & File Exchange Pattern
_Original proposal: 2026-03-10_
_Reconciled against implementation: 2026-06-13 (v0.8.352)_
_Status: **Superseded by what actually shipped.** The three proposed directories (`.co-cli/workspaces/`, `.co-cli/exchange/`, `.co-cli/schedules/`) were NOT built. The underlying needs were each met by a different mechanism. This doc is retained as a design-rationale record of why, extended in §8 with forward design input from the Stanford DeLM paper (2026-06-16)._

## Executive Summary

The 2026-03-10 proposal advocated three new directories under `.co-cli/` to harden sub-agent isolation, file ingress, and background-task legibility:

1. **Sub-agent Sandboxing** (`.co-cli/workspaces/`): ephemeral per-task scratchpads.
2. **File Exchange** (`.co-cli/exchange/`): an ingress/egress directory for file drops.
3. **Markdown-Backed Background Tasks** (`.co-cli/schedules/`): a SQLite cron scheduler reading `.md` payloads.

None of these three directories exist in the shipped system. The needs behind them were real, and each was solved — but by mechanisms that fit `co`'s actual grain better than a new directory would have:

| Proposed pillar | What it wanted | What actually shipped |
| --- | --- | --- |
| `.co-cli/workspaces/` sandboxing | Stop sub-agents polluting the working tree; prove execution safety | **Path-boundary enforcement** (`tools/files/fs_guards.py`) on a `workspace_dir` (write anchor) + `file_search_roots` (read roots), plus **tool-surface isolation** for task agents (`TaskAgentSpec`). No per-task chroot. |
| `.co-cli/exchange/` file drop | Async bulk-context ingress while the terminal is busy | **In-memory REPL input queue** (the doc's own §3.5 recommendation) shipped for the *text* twin; the *bulk-file* twin was never needed — multi-root `file_search_roots` lets the agent read files in place. |
| `.co-cli/schedules/` cron | Legible recurring background work | **Session-scoped background tasks** (`task_start`/`task_status`/`task_cancel`/`task_list`), **interactive stdin drive** (`task_write`/`task_close`), **PTY** shell mode, and the **dream daemon's file kick-queue** for recurring memory/skill review. No recurring cron scheduler. |

The most useful outcome of this proposal was a *correct prediction it argued against itself*: §3.5 concluded the text queue should be in-memory, not a file. That is exactly what shipped (`_ReplRuntime` deque, `/queue` command). The file-backed exchange half it was paired with was correctly dropped.

---

## 1. The Original Problem (unchanged framing)

The 2026-03-10 framing identified three scaling risks of operating directly in the project root:

1. **Sub-agent pollution** — if sub-agents gain write access they could corrupt the working tree while iterating.
2. **Multimodal friction** — base64 API payloads for file ingress bloat context and are brittle.
3. **Opaque background tasks** — a SQLite-stored command string hides multi-step instructions from the user's IDE.

All three are still recognizable concerns. The resolutions below show how each was actually addressed.

---

## 2. Pillar 1 — Sub-agent Isolation: boundary enforcement, not workspaces

**Proposed:** route each sub-agent into `.co-cli/workspaces/task_<uuid>/`, add `CoDeps.workspace_dir` as a chroot, raise `SecurityError` on `../` breakout, clean up on success.

**Shipped (different shape):**

### 2.1 `workspace_dir` exists — but as a write anchor, not a chroot
`CoDeps` carries two path fields (`co_cli/deps.py`):
- `workspace_dir: Path` (deps.py:316) — the single **write anchor**, resolved from config at bootstrap (`resolve_workspace_paths`, deps.py:364–383).
- `file_search_roots: list[Path]` (deps.py:320) — the **read roots**; defaults to `[workspace_dir]` when unset (`__post_init__`, deps.py:355–356). A non-empty config list is authoritative and total (no implicit `workspace_dir` append).

This is the opposite of the proposal's single chroot: reads are deliberately *multi-root* (the user can grant additional read-only roots), while writes are confined to one anchor.

### 2.2 Boundary enforcement is real — but raises `ValueError`, not `SecurityError`
`co_cli/tools/files/fs_guards.py` implements the join-then-resolve guard the proposal asked for:
- `enforce_read_boundary(path, roots)` — for each root, `(root / path).resolve()`, accept the first that `is_relative_to` that root; returns `(resolved, root)`. An absolute path passes through under whichever root contains it; a relative path anchors to `roots[0]`. Raises `ValueError` if no root contains it.
- `enforce_write_boundary(path, workspace_dir)` — `(workspace_dir / path).resolve()` must stay under `workspace_dir`; else `ValueError`.

Both block `..` traversal and in-bounds symlinks whose resolved target escapes (the post-`.resolve()` `is_relative_to` check). The violation is a plain `ValueError`, caught and surfaced as a `tool_error()` — there is no dedicated `SecurityError` type. Wired into `file_read`/`file_write`/`file_search`, and `shell_exec` resolves its `work_dir` through `enforce_write_boundary` before running (`co_cli/tools/shell/execute.py`). `ShellBackend` itself is stateless and holds no anchor (`co_cli/tools/shell_backend.py`).

### 2.3 Sub-agents are isolated by tool surface and forked state — not by directory
There is **no `.co-cli/workspaces/`** and no per-task chroot. `fork_deps()` (deps.py:386–441) builds a sub-agent's deps by:
- **inheriting** `workspace_dir` and `file_search_roots` *by reference* (deps.py:429–430) — sub-agents see the same filesystem as the parent;
- **sharing** `file_tracker`, `resource_locks`, `tool_dispatch_sem`, `usage_accumulator`, `degradations` (cross-agent coordination by design);
- **resetting** `runtime` to a fresh `CoRuntimeState(agent_depth = parent + 1)` (deps.py:428) and resetting per-session fields (todos, background_tasks, …);
- **excluding** the toolset entirely — task agents wire their own minimal surface.

Isolation therefore comes from (a) a deliberately narrow **tool surface** declared per task agent in `TaskAgentSpec` (`co_cli/agent/spec.py:36–58`, built by `build_task_agent`, `co_cli/agent/build.py:58–108`) and (b) forked runtime/session state — *not* from a filesystem jail. The proposal's "physical isolation" was answered by boundary checks + surface restriction.

`agent_depth` is tracked (incremented by `fork_deps`) but **not enforced** — there is no `MAX_AGENT_DEPTH` check. `run_standalone` (`co_cli/agent/run.py:20–85`) explicitly never depth-checks because daemons are top-level.

### 2.4 There is no model-callable "delegate" / "spawn sub-agent" tool
The proposal's `tools/delegation.py` with `delegate_coder` / `delegate_research` does not exist, and there is no general-purpose subagent-spawn tool exposed to the model (`tools/agent_tool.py` is only the `@agent_tool` registration decorator). Runtime delegation today is internal:
- the **dream daemon** runs `MEMORY_REVIEW_SPEC` / `SKILL_REVIEW_SPEC` task agents via `run_standalone` + `fork_deps_for_reviewer` (`co_cli/daemons/dream/_reviewer.py:71–100`);
- **skill invocation** (`/skillname args`) routes to a `DelegateToAgent` command outcome (`co_cli/commands/core.py:104–149`) that runs an in-turn delegated agent.

> Note on the original §4 (AutoGen comparison): the build-time dev-workflow skills (`orchestrate-plan`, `orchestrate-dev`) are Claude-Code build-time tooling for developing co-cli — not runtime agent behavior. Don't conflate them with the runtime delegation surface above (build-time vs runtime layering).

---

## 3. Pillar 2 — File ingress: the in-memory input queue shipped; the file exchange did not

**Proposed:** a `.co-cli/exchange/` directory scanned each turn boundary, synthesizing an automatic `[System: New files detected …]` turn; paired with an in-memory text queue for chat control signals.

**Shipped:** the **in-memory text queue only**. `.co-cli/exchange/` was never built; no `_scan_exchange_directory`, no turn-boundary directory scan.

The proposal's §3.5 already argued the text queue should be in-memory (cancelable on Ctrl-C, ephemeral, no file-locking races) rather than a `queue.jsonl`. That reasoning held, and the queue shipped exactly so:
- `_ReplRuntime` owns a `collections.deque[str]` FIFO (`co_cli/main.py`); the accept handler enqueues mid-turn submissions and runs immediately when idle; a done-callback drains the next item on turn completion or Esc-cancel.
- `/queue` command surface (`co_cli/commands/queue.py`, `_queue_control.py`): `list`, `clear`, `pop [n]`.
- Bounded cap + drop policy ("oldest" / "newest"), config-gated; head-item preview in the status toolbar.
- Shipped across v0.8.260 → v0.8.268 (plan: `docs/exec-plans/completed/2026-05-23-151807-repl-input-queue.md`). There is no standalone `RESEARCH-input-queue.md`.

The **bulk-file** half turned out unnecessary: multi-root `file_search_roots` plus the boundary-checked file tools let the user keep files wherever they are and have the agent read them in place — answering RFC question 7 ("track files wherever they are on disk") in the affirmative. Base64/multimodal ingress is handled separately by the vision plumbing track, not by a drop directory.

---

## 4. Pillar 3 — Background work: session tasks + dream kick-queue, not a cron scheduler

**Proposed:** a SQLite `scheduled_jobs` table storing a `payload_file TEXT` reference to `.co-cli/schedules/<job>.md`, with `/schedule add` opening the template in `$EDITOR`.

**Shipped:** none of it. There is **no scheduler, no `scheduled_jobs` table, no `/schedule` command, no `.co-cli/schedules/`**, and `RESEARCH-cron-scheduler.md` was never written. Recurring background work and long-running processes are covered by three concrete mechanisms instead:

### 4.1 Session-scoped background tasks
`co_cli/tools/background.py` + `co_cli/tools/tasks/control.py` provide four tools:
- `task_start(command, description, work_dir)` — approval-gated spawn via `asyncio.create_subprocess_shell` (stdin=PIPE, stdout→log); returns `task_id`.
- `task_status(task_id, tail_lines)` — status + tail of the log.
- `task_cancel(task_id)` — SIGTERM → 200ms → SIGKILL via process group.
- `task_list(status_filter)` — enumerate/filter tasks.

Each task streams to `~/.co-cli/logs/bg-{task_id}.log` (single source of truth; `LOGS_DIR`, `config/core.py`).

### 4.2 Interactive stdin drive (v0.8.352)
- `task_write(task_id, input, newline)` — write to a running task's stdin; `TaskInputError` on dead task / closed pipe.
- `task_close(task_id)` — close stdin (EOF).
- Human surface: `/write <id> <input>`.
- Approval is gated once at `task_start` (the command); writes to an already-approved interactive process are allowed by design.

### 4.3 PTY shell mode (v0.8.352, Phase 1)
`ShellBackend.run_command(..., pty=False)` gains a `pty=True` path using stdlib `pty.openpty()` — output fidelity only (programs see a tty via `isatty`), no stdin drive on a one-shot command. Surfaced through `shell_exec(..., pty=False)` (`co_cli/tools/shell/execute.py`). Plan: `docs/exec-plans/completed/2026-05-28-200025-toolgap-interactive-terminal.md`.

### 4.4 Recurring background work → dream daemon kick-queue
The proposal's "legible recurring tasks" need is, in practice, only exercised by memory/skill curation, which runs through the **dream daemon's file-based kick-queue** (`~/.co-cli/daemons/dream/queue` + `done/`/`failed/`, `config/core.py`). Main writes JSON kick files; the daemon polls. Per design doctrine the queue is the *sole* cross-process bridge — no producer→consumer wake-up signal, and the producer never gates on consumer liveness. This is durable and inspectable (JSON files on disk), which captures the proposal's "legibility" goal without a cron table or `.md` payloads.

---

## 5. Directories actually created on startup

`_ensure_dirs()` (`co_cli/config/core.py`) creates, under `USER_DIR` (`$CO_HOME` or `~/.co-cli`):
`logs/`, `memory/`, `sessions/`, `tool-results/`, `daemons/dream/queue/` (+ `done/`, `failed/`).

**Not created** (proposed, never built): `.co-cli/exchange/`, `.co-cli/workspaces/`, `.co-cli/schedules/`.

---

## 6. Disposition of the original RFC questions

- **PO — "is `exchange/` the right UX, or just track files in place?"** → Resolved as *track in place*. Multi-root `file_search_roots` + boundary-checked file tools made a drop directory unnecessary.
- **TL — "should the boundary apply to shell too, or just file tools?"** → Resolved as *both*. `shell_exec` resolves `work_dir` through `enforce_write_boundary`; file tools use `enforce_read_boundary` / `enforce_write_boundary`.

## 7. Why the three directories were the wrong unit

The proposal reached for a *new directory* per concern. The shipped system reached for the smallest mechanism that fit `co`'s existing grain:
- isolation → a **field pair + guard functions**, reusing the deps/tool layering, instead of an ephemeral-dir lifecycle to create, copy into, and garbage-collect;
- ingress → an **in-memory queue** for control + **existing file tools** for data, instead of a watched directory and synthesized system turns;
- background → **session-scoped task tools + the dream queue**, instead of a cron table and `$EDITOR`-managed payload files.

Each avoids a persistent on-disk surface that would need its own TTL, cleanup, and traversal hardening — the very risks §6 of the original flagged. The proposal's analytical core (in-memory text queue; enforce boundaries on both file and shell I/O) was right; its packaging (three new directories) was not adopted.

---

## 8. Forward: DeLM structural patterns (2026-06-16)

Source: Stanford DeLM paper (Mao & Mirhoseini, 2026-06-16). SWE-bench Verified: +10.5% over strongest baseline, ~50% cost reduction per task. LongBench-v2 Multi-Doc QA: highest accuracy across GPT-5.4, Claude Sonnet, Gemini Flash, DeepSeek-V4-Pro.

This section records the structural patterns from DeLM that are relevant to co's parallel agent future. It does not describe anything currently built.

### 8.1 The structural argument

DeLM's central claim is architectural, not a tuning result: a central orchestrator that merges and rebroadcasts is a serialization point, not a coordinator. Its failure modes are structural:

- all intermediate state is compressed through one context window, losing or distorting findings
- evidence clusters are pre-assigned before relevance is known, causing sub-agents to return for clarification
- failures stay private — every agent rediscovers the same dead ends independently

The fix is to eliminate the orchestrator role, not optimize it. Each of its three jobs (assign, merge, rebroadcast) is replaced by a simpler decentralized mechanism.

### 8.2 The three primitives

| Primitive | What it replaces | Key property |
|---|---|---|
| **Task queue** (self-assigned) | Dispatcher / assignment step | Agents pull work; throughput scales with fleet size, not coordinator speed |
| **Gist board** (shared state) | Merge + rebroadcast step | Verified findings accumulate; no single agent holds the full picture |
| **Last-agent sufficiency check** | Completion detection | Whoever finishes last inspects the board and decides whether more work is needed |

### 8.3 Cross-agent communication patterns worth carrying forward

**Failure constraints as first-class shared state.** When an agent hits a dead end, it writes that as a binding constraint — not a log entry, not a summary, but a "do not go here" fact that later agents inherit and build around. This is the highest-leverage pattern in the paper: it converts private waste into collective pruning.

**Verified-only writes.** An agent does not broadcast until it has confirmed its finding is sound. Unverified partials stay local. This keeps the shared board clean — other agents are not misled by work-in-progress guesses that later turn out wrong.

**Coarse-to-fine access by default.** The board holds short summaries (gists). An agent reads the full evidence only when its specific task requires it. Without this, coordination itself becomes a long-context problem — every agent reads every other agent's full trace before it can start. The default must be compact; detail is opt-in.

### 8.4 Near-term actionable for co (no architecture change needed)

The dream daemon already curates memory from session review. Adding **failure/constraint as a distinct memory kind** — not just "what worked" but "what was ruled out and why, with evidence pointer" — would make recall more useful immediately. The unfold-on-demand pattern (short item by default, full evidence on `memory_view`) is already how co's memory recall works. No agent-layer change required; this is a dream curation behavior change only.

### 8.5 Design guardrails (what not to build)

**Do not add an orchestrator tool.** If co moves toward parallel runtime agents, the correct move is to add a shared state surface and a self-assigned task queue — not a dispatcher role exposed as a tool. An orchestrator tool recreates the bottleneck in a new form.

**The last-agent-closes-the-loop step is a hidden coordinator.** DeLM's "no central controller" framing understates this: the final agent still decides sufficiency for the whole run. It is not free — it holds the full gist board in context and reasons over all accumulated state. Budget for it explicitly rather than treating it as overhead-free.

**The 50% cost figure is task-class-specific.** SWE-bench and multi-doc QA are naturally parallelizable with low inter-task dependency. Tasks with tight sequential dependencies or shared mutable state will not see the same gains. Apply the model where parallel exploration is the actual bottleneck, not universally.

**"No central controller" is a framing, not a fact.** The gist board *is* a coordination mechanism — asynchronous and decentralized, but coordination nonetheless. The complexity moved from a role to a data structure. Design the data structure with the same care you would give a coordinator's decision logic.
