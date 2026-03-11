# TODO: Orch Cycle High-Impact High-Effort Fixes (Future Phase)

Task type: doc

## Context

Date: 2026-03-11

Source: SOTA comparison analysis (updated 2026-03-11). Five architectural gaps identified in the
orchestration skill cycle that require non-trivial infrastructure, new agent patterns, or
fundamental changes to the execution model. Documented here for future planning — not ready
for implementation without a dedicated design and review cycle per gap.

**Gaps being addressed:**

1. **No sandboxed execution** — orchestrate-dev runs directly against the live working tree.
   A blocked task leaves the tree in a partially-modified state. No automatic rollback.

2. **No dynamic re-planning on failure** — a blocked task escalates to the human for full
   plan re-entry. SOTA systems attempt diagnosis and autonomous re-planning before escalating.

3. **No codebase impact analysis** — orchestrate-plan relies on TL's manual search to find
   dependencies. No automated analysis of what imports/calls the files in a task's `files:` list.

4. **No eval harness integration** — the project's `evals/` suite is never invoked by the
   orchestration cycle. Behavioral regressions (especially for AI-native tools) are not caught
   at delivery time.

These are independent of each other. Each can be designed and shipped as a separate cycle.

---

## Gap 1 — Sandboxed / Isolated Execution

### Problem

orchestrate-dev executes tasks directly on the working branch. When a task is blocked:
- The working tree may have partial edits across multiple files.
- The only recovery instruction is "stash or commit unrelated work first" (a pre-flight warning,
  not a recovery mechanism).
- Re-running from a blocked task requires manual inspection to determine what to undo.

### SOTA Pattern

Two complementary approaches used by leading systems:

**Git worktree isolation (Claude Code, aider):**
Each orchestrate-dev session creates a fresh git branch from HEAD before any task executes.
All edits happen on that branch. On DELIVERED: merge to working branch. On BLOCKED: the
branch is abandoned (or kept for inspection) — the working branch is untouched.

**Per-task checkpoint (Devin, OpenHands):**
Before each task, snapshot the working state (git stash or branch tag). On task failure,
restore to the pre-task snapshot automatically without human intervention.

### Design Notes for Future Planning

- Worktree approach is cleaner for orchestrate-dev: single branch per delivery session,
  merged only on full DELIVERED status. Works with existing git tooling (`git worktree add`).
- The delivery branch name convention: `orch/<slug>/<timestamp>`.
- Phase 1 creates the worktree. Phase 4 (on DELIVERED) merges and deletes it. On BLOCKED,
  leaves the branch for TL inspection and reports the branch name in the delivery report.
- Requires orchestrate-dev to thread the worktree path through all subagent task executions —
  subagents must cd into the worktree, not the project root.
- Risk: subagents that spawn shells or read config may accidentally operate on the project root
  if the worktree path isn't explicitly enforced.
- Dependencies: none on the skills side; requires the agent runtime to support `git worktree`.

### Estimated Scope

- orchestrate-dev SKILL.md: Phase 1 (create worktree), Phase 3 (TODO lifecycle in worktree),
  Phase 4 (merge on DELIVERED, abandon on BLOCKED).
- New DESIGN doc section or update to DESIGN-skills.md covering worktree lifecycle.
- ~1 design cycle + 1 dev cycle.

---

## Gap 2 — Dynamic Re-planning on Blocked Tasks

### Problem

When a task fails its `done_when` criterion, orchestrate-dev halts completely and escalates to
the human. The escalation path is: human reads the delivery report → human edits the TODO →
human re-runs `/orchestrate-dev`. This is a full cycle for what may be a one-line plan fix.

### SOTA Pattern

Top systems (OpenHands, Devin 2, Gemini CLI via event-driven `a2a-server` tasks) add a diagnosis-and-retry loop before human escalation:

```
task fails done_when
    → diagnosis agent: reads the failure output + the task spec
    → classifies failure:
        - plan error (bad done_when, missing step): propose plan edit → re-execute
        - code error (fixable without plan change): fix + re-verify (up to N retries)
        - blocker (requires new work or human decision): escalate
    → if plan edit or code fix succeeds: continue execution
    → if classification is "blocker" or retry limit hit: escalate to human
```

### Design Notes for Future Planning

- The diagnosis agent needs: task spec, done_when text, failure output, relevant source files.
  It must NOT have access to the full implementation context (to avoid confirmation bias).
- Retry limit: 2 autonomous retries max. Third failure → escalate. This is the standard limit
  used by OpenHands and Devin to prevent runaway loops.
- Plan edit path: diagnosis agent proposes a minimal diff to the TODO task (new files:, revised
  done_when, added step). TL reviews the proposed edit inline before re-execution. This keeps
  the human in the loop for plan changes without requiring a full Gate 1 cycle.
- Code fix path: diagnosis agent spawns a fresh implementation subagent (no context from the
  failing attempt). This avoids the agent repeating the same mistake with more confidence.
- Risk: autonomous re-planning can drift from the original spec. The proposed plan edit must
  be constrained to the failing task only — no other tasks may be modified.
- Dependencies: Gap 1 (worktree isolation) is a prerequisite — autonomous retry requires a
  clean rollback point for each attempt.

### Estimated Scope

- orchestrate-dev SKILL.md: new Phase 2 failure handler between Step 5 (verify done_when)
  and Step 6 (report task result).
- New diagnosis subagent spec embedded in orchestrate-dev.
- ~1 design cycle + 1-2 dev cycles. Should not be planned until Gap 1 ships.

---

## Gap 3 — Codebase Impact Analysis in orchestrate-plan

### Problem

orchestrate-plan Phase 1 instructs TL to "search the codebase for relevant modules, patterns,
and tests related to the feature." This is manual and unsystematic. A task's `files:` list may
include a module that is imported by 12 other modules — none of which are in `files:`. Those
downstream dependents are not in scope, so a breaking change to the module's API is not caught
until tests fail (or until a cross-module regression surfaces after delivery).

### SOTA Pattern

Indexed codebase tools (Claude Code's symbol index, Cursor's codebase indexer, Gemini CLI's
repo-level context) automatically answer: "what else in the codebase depends on these files?"

The pattern for orchestrate-plan:
```
for each file in union(all tasks' files:):
    grep/ast-parse for: imports of this module, calls to its public functions
    build: importers list, callers list
    surface: "co_cli/main.py imports co_cli/tools/memory.py — not in any task's files:"
```

Then flag: if a changed file has importers not covered by any task's `files:`, TL must either:
(a) add those files to the appropriate task's `files:`, or
(b) explicitly document in the task why the importers are unaffected.

### Design Notes for Future Planning

- Python-level analysis: `grep -r "from co_cli.tools.memory import\|import co_cli.tools.memory"`
  is sufficient for this codebase (small, no dynamic imports). No AST parser needed.
- Integrate as Phase 1 step 6 in orchestrate-plan: "Impact surface check."
- Output format: a table in the Context section of the TODO: `File | Importers not in scope`.
- TL must address each row (add to files: or document as safe). Core Dev checks this table
  in Phase 2 critique.
- Risk: surface can be large for widely-imported modules (e.g. `deps.py`, `config.py`). Need
  a filter: flag only importers that call functions the task modifies (not all importers).
  Conservative filter: flag all importers; TL prunes.
- Dependencies: none. Can be planned independently.

### Estimated Scope

- orchestrate-plan SKILL.md: Phase 1 new step, TODO template new `## Impact Surface` section.
- ~1 design cycle + 1 dev cycle (skills-only, no code changes).

---

## Gap 4 — Eval Harness Integration in orchestrate-dev

### Problem

The project has a behavioral eval suite in `evals/`. The orchestration cycle never invokes it.
`done_when` criteria can only verify what the plan author anticipated. Behavioral regressions —
especially regressions in agent behavior (memory signal detection, tool calling, safety patterns)
— require running evals against the live system after changes.

This gap is especially significant for co-cli because the project's primary deliverable is agent
behavior, not data transformation logic. A code change that passes all pytest tests may still
regress a recall pattern or a signal detection heuristic.

### SOTA Pattern

Eval-driven CI is standard practice for AI-native projects. The pattern:
```
after delivery:
    if evals/ directory exists:
        run relevant evals (scope by changed modules → map to eval files)
        record: pass/fail count, any regressions vs last known baseline
        if regressions: flag in delivery report under ## Eval Results
```

Not a blocking gate by default (evals are probabilistic) — but regressions must be noted
in the delivery report for TL review at Gate 2.

### Design Notes for Future Planning

- Eval-to-module mapping: maintain a comment or manifest in `evals/` that maps each eval file
  to the modules it exercises. orchestrate-dev Phase 3 uses this to scope which evals to run.
  If no mapping exists: run all evals (conservative).
- Eval runtime is slow (LLM calls). Run as a background task during sync-doc (Phase 3 Step 3)
  so wall time is minimized.
- Baseline tracking: store last-known pass/fail counts per eval in `.co-cli/eval-baseline.json`.
  Regression = current count < baseline. Improvement = current count > baseline (record, no flag).
- Partial evals (skipped due to missing API key) must not be counted as regressions.
- Risk: eval non-determinism means a "regression" may be noise. Use a threshold: flag only if
  pass rate drops by >10% vs baseline on the same eval.
- Dependencies: Gap 1 (worktree) is useful but not required. Can be planned independently.

### Estimated Scope

- orchestrate-dev SKILL.md: Phase 3 new step, delivery report new `## Eval Results` section.
- New eval manifest convention in `evals/` (a README or inline comments mapping evals to modules).
- Baseline tracking: `.co-cli/eval-baseline.json` format and update protocol.
- ~1 design cycle + 1 dev cycle. Recommend planning after Gap 3 (impact analysis) since
  knowing which modules changed helps scope which evals to run.

---

## Gap 5 — No execution observability or standard agent protocols

### Problem

Orchestration currently uses a bespoke text-based delegation protocol (`delegation.py`). The user can't easily see the internal event stream of what a subagent is doing. Furthermore, there is no cycle detection or depth limit (like OpenClaw's subagent spawn depth limits), risking infinite loops if subagents continually spawn other subagents.

### SOTA Pattern

Leading systems solve this through structured observability and standardized protocols:
- **OpenClaw:** Added Agentic Control Protocol (ACP) support, and explicit subagent spawn depth limits to prevent runaway delegation loops.
- **Gemini CLI:** Shifted towards event-driven subagent task execution (`a2a-server`) and deep browser agent capabilities.

### Design Notes for Future Planning

- Adopt standard ACP or SSE streams for subagent logging to provide real-time observability into subagent thought processes and tool use.
- Add a hard `spawn_depth` parameter to `delegation.py` to enforce a maximum delegation depth.
- Event streams should be surfaced to the user in a collapsible or filtered UI so they can monitor progress without being overwhelmed.

### Estimated Scope

- Update `delegation.py` with `spawn_depth` limits and tracking.
- Implement SSE or ACP event emitting for subagent actions.
- ~1 design cycle + 1 dev cycle.

---

## Recommended Planning Order

Dependencies and sequencing:

```
Gap 5 (observability)      — independent, highly recommended for safety (spawn limits)
Gap 3 (impact analysis)    — independent, no prerequisites, highest planning ROI
Gap 1 (worktree isolation) — independent, prerequisite for Gap 2
Gap 4 (eval harness)       — independent, easier after Gap 3 ships
Gap 2 (dynamic re-plan)    — requires Gap 1 to ship first
```

Each gap is a separate `/orchestrate-plan` → `/orchestrate-dev` cycle. Do not bundle.
