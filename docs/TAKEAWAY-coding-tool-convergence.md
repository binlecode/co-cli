# TAKEAWAY: Coding Tool Convergence (Adoptable Enhancements)

Date: 2026-02-28  
Scope: Co coding workflow (`run_shell_command`, approvals, safety, orchestration)

Superseded for execution by: `docs/TODO-coding-tool-convergence.md`

## 1) Executive Takeaway

Co is already strong on approval-first execution and runtime safety hygiene.  
The largest gap versus converged top systems is not model quality, but tool shape:

1. Missing native file-edit tools (still shell-heavy for coding edits)
2. Missing implemented tool-level coder subagent pattern
3. Missing rewind/undo checkpoint path for safe iteration
4. Safe-command classification is still prefix-based, not parser/policy-grade

Recommended direction:
- Keep `qwen3:30b-a3b-thinking-2507-q8_0-agentic` as parent orchestrator
- Add coding-specialized subagent as a **tool**, powered by `qwen3-coder-next:*‑agentic`
- Shift routine coding from shell to native file tools
- Preserve approval-first invariant (no approval bypass through delegation)

---

## 2) What Has Converged Across Top Reference Systems

Practical convergence (as reflected in local peer analysis docs):

1. Approval or policy-gated side effects
2. Native file tools for read/edit/write as first-class coding surface
3. Rewind/undo capability when agents mutate files
4. Subagent delegation for specialized workflows
5. Strong observability and eval gates on tool behavior

Not converged enough to force immediate adoption:

1. OS-level sandbox strictness (divergent by product)
2. One universal plugin marketplace format
3. One universal multi-agent coordination pattern

Implication for Co:
- Continue approval-first (existing design principle)
- Invest in file tools + delegation + rewind before sandbox rearchitecture

---

## 3) Current State (Co)

Strengths:

1. Canonical deferred approval flow (`requires_approval=True`, `DeferredToolRequests`)
2. Safe environment and process-tree termination in shell backend
3. Typed orchestration loop (`run_turn`) with retry, compaction, interruption handling
4. Solid tracing + eval infrastructure already present

Gaps:

1. Coding edits still route through shell rather than structured file ops
2. No implemented coder subagent delegation tool yet
3. No rewind/checkpoint UX for agent-made code changes
4. Prefix safety classifier is easy to reason about but not policy-complete

---

## 4) Adoptable Enhancements (Prioritized)

## P0 (Immediate, high impact)

### A. Ship native coding file tools

Add tools:

1. `list_directory(path=".", pattern=None, max_entries=...)`
2. `read_file(path, start_line=None, end_line=None)`
3. `find_in_files(query, path=".", glob=None, max_matches=...)`
4. `write_file(path, content)` (approval-gated)
5. `edit_file(path, search, replace, all=False)` (approval-gated)

Why:
- Matches converged CLI coding UX
- Reduces shell overreach for common tasks
- Enables tighter path controls and clearer diffs

Acceptance:
- Most coding tasks complete without shell usage for file edits
- Path traversal/symlink escape blocked at tool boundary
- Existing approval UX reused for write/edit

### B. Harden shell command classification

Add a policy layer on top of current prefix checks:

1. Segment command into shell control operators
2. Deny dangerous operators/patterns deterministically
3. Enforce workspace path policy where relevant
4. Keep default behavior: non-safe => explicit approval

Why:
- Closes gap with parser/policy-oriented peers
- Keeps current approval model; no architectural churn

---

## P1 (Next, strategic)

### C. Implement coder subagent as a tool (`delegate_coder`)

Pattern:
- Parent agent calls `delegate_coder(...)`
- Tool creates focused coder agent using `qwen3-coder-next:*‑agentic`
- Returns structured result (`CoderResult`)
- Parent decides follow-up actions

Suggested `CoderResult`:

1. `summary: str`
2. `proposed_changes: list[FileChange]`
3. `diff_preview: str`
4. `tests_run: list[TestRun]`
5. `confidence: float`
6. `display: str`

Critical constraint:
- No approval bypass. If subagent can mutate files, those mutations must still pass approval gates.  
- Safer first release: subagent proposes patch/diff; parent executes approved edits.

Why:
- Uses Co’s intended delegation-as-tool architecture
- Enables coding specialization without switching global model
- Keeps `run_turn` as universal primitive

### D. Add coding eval gates

Track and gate:

1. File edit success rate
2. Broken patch rate
3. Test pass rate after agent edits
4. Approval burden (prompt count per task)
5. Recovery success after tool errors

Why:
- Prevents regressions when tool surface expands
- Mirrors best-practice evaluation discipline from peers

---

## P2 (Safety and UX maturation)

### E. Rewind/checkpoint for coding sessions

Add:

1. `checkpoint_workspace` before mutating turns
2. `/rewind` or `/undo-last-turn` for fast rollback
3. Clear display of files touched and recovery status

Why:
- Converged safety pattern once write tools exist
- Increases trust in autonomous edits

### F. Risk classifier for approval routing (optional)

Add lightweight classifier to route tool approvals:

1. `low-risk` pre-approved by policy
2. `high-risk` always prompt
3. Keep user override path explicit

Why:
- Reduces approval fatigue
- Retains approval-first philosophy

---

## 5) Recommended Tool Catalog (Target)

Coding core:

1. `list_directory` (read-only)
2. `read_file` (read-only)
3. `find_in_files` (read-only)
4. `write_file` (approval)
5. `edit_file` (approval)
6. `run_shell_command` (approval; fallback and system ops)

Coding delegation:

1. `delegate_coder` (tool-level subagent; coder model specific)
2. `delegate_research` (optional extension)
3. `delegate_analysis` (optional extension)

Safety/rollback:

1. `checkpoint_workspace`
2. `rewind_workspace`
3. `show_diff_preview`

Observability:

1. Keep OTel spans for parent + delegated subagent runs
2. Track approval and tool-chain metrics per turn

---

## 6) Suitability Filter for Co (What to Avoid Right Now)

Do not adopt yet:

1. Full OS-sandbox rewrite before file tools/delegation maturity
2. Autonomous code mutation without approval checkpoints
3. Complex marketplace/plugin packaging before core coding reliability is stable

Reason:
- Lower ROI than file tools + delegation + rewind in current stage
- Violates current principle sequencing (security and controllability first)

---

## 7) Implementation Plan (Concrete)

Phase 1 (P0):

1. Add native file tools + tests
2. Add path boundary enforcement
3. Add shell policy hardening layer

Phase 2 (P1):

1. Add `co_cli/agents/coder.py` (coder model factory)
2. Add `co_cli/tools/delegation.py::delegate_coder`
3. Register delegation tool in `agent.py`
4. Add evals for coder delegation outcomes

Phase 3 (P2):

1. Add checkpoint/rewind mechanics
2. Add slash command UX for rewind
3. Add approval-risk classifier (optional, behind flag)

---

## 8) Success Criteria

Minimum success bar:

1. 70%+ coding edit tasks use file tools (not shell editing)
2. `delegate_coder` improves completion quality on complex coding tasks vs baseline
3. No approval bypass regressions in delegated flows
4. Rewind restores workspace reliably after failed edits
5. Eval suite catches tool-chain regressions before release

---

## 9) Final Recommendation

Adopt the coder model at **tool delegation level**, not system level.  
Prioritize file tools first, then coder delegation, then rewind.  
This path matches cross-system convergence while fitting Co’s current architecture and safety principles.
