# TODO: Tier 1.5 Generic Agent Harness

**Slug:** `tier15-agent-harness`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-fork-claude-code-core-tools.md](reference/RESEARCH-fork-claude-code-core-tools.md), [RESEARCH-peer-capability-surface.md](reference/RESEARCH-peer-capability-surface.md), [RESEARCH-tools-fork-cc.md](reference/RESEARCH-tools-fork-cc.md)

Current-state validation against the latest code:
- `co` already has shared internal dispatch for subagents in [co_cli/tools/subagent.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/subagent.py), including `SUBAGENT_ROLES`, `_run_subagent(...)`, and per-role builders.
- So the current gap is not internal boilerplate reduction; that refactor already landed.
- The remaining gap is the public tool surface. `co` still exposes four separate role-bound tools:
  - `run_coding_subagent`
  - `run_research_subagent`
  - `run_analysis_subagent`
  - `run_reasoning_subagent`
- Current subagents are one-shot only. They do not expose a generic task-shaped delegation primitive.
- Current tier-1 workflow TODO intentionally excludes generic resumable orchestration in [docs/TODO-tier1-workflow-tools.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tier1-workflow-tools.md).
- Current tier-2 TODO covers task CRUD, MCP resources, and agent-callable skill activation, but not a generic agent harness in [docs/TODO-tier2-tool-surface.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tier2-tool-surface.md).
- The older `TODO-subagent-to-primitives.md` was directionally obsolete: current fork-cc research says the next high-value gap is a better delegation primitive, not deletion of delegation as a category, in [RESEARCH-fork-claude-code-core-tools.md](reference/RESEARCH-fork-claude-code-core-tools.md) §8.4.

Workflow artifact hygiene:
- `docs/TODO-subagent-to-primitives.md` was stale and is replaced by this TODO.

Shipped-work check:
- Shared subagent dispatch already exists; do not draft tasks that merely recreate the current `_run_subagent(...)` consolidation.
- No current file implements a generic public `run_agent_task`-style tool.

---

## Problem & Outcome

Problem: `co` has delegation, but not a general delegation primitive. Users and the model must choose among four role-specific one-shot subagent tools, each with slightly different argument shapes and semantics, and there is no single public harness that becomes the canonical delegation interface.

Failure cost: delegation remains fragmented and hard to extend. If we finalize downstream task or workflow surfaces before normalizing delegation, we risk baking current role-tool quirks into later layers and then reworking them once a generic harness is introduced.

Outcome: after tier 1 lands, `co` should add one generic foreground agent harness tool that:
- accepts an explicit role and task-shaped request
- preserves the focused role builders already working today
- becomes the long-term canonical delegation interface
- leaves the four existing `run_*_subagent` tools in place temporarily as compatibility wrappers
- intentionally leaves background agent jobs, resumability, and task-model unification to later TODOs

---

## Scope

In scope:
- add one generic public agent-harness tool over the existing subagent role registry
- normalize request shape and output envelope across the four current roles
- keep role-specific tools as compatibility wrappers in the first release
- define deprecation posture for the role-specific wrapper tools

Out of scope:
- backgrounded agent jobs
- resumable messaging or continuation
- shared task-model redesign
- swarm/team abstractions
- inter-agent messaging
- worktree isolation
- remote execution
- deleting the old role-specific tools in the same change that introduces the harness

---

## Behavioral Constraints

- Do not regress the existing focused-role behavior already encoded in [co_cli/tools/_subagent_builders.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/_subagent_builders.py).
- Do not collapse all roles into a single unrestricted agent. The harness should standardize the public interface, not erase tool-scope boundaries.
- The first release should be foreground-only. Do not mix agent backgrounding into this TODO.
- The first release should keep `run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, and `run_reasoning_subagent` as thin wrappers to avoid breaking existing prompting and tests.
- The wrapper tools become compatibility-only immediately once the generic harness lands; new prompting should prefer the generic tool.
- Keep the harness narrower than fork-cc `Agent`: no generic spawn-anything semantics, no worktree mode, no message inbox, no team lifecycle.

---

## Failure Modes

- Current prompt shape: "Delegate this repo investigation." Current behavior: the model must choose among four role-specific tools instead of one canonical delegation entrypoint.
- Current prompt shape: "Use the same delegation mechanism, but switch role depending on the task." Current behavior: each role tool has its own public surface, so the model learns four tools instead of one normalized pattern.
- Naive harness failure: replacing role tools with one unrestricted agent would throw away the current focused tool scopes and likely worsen result quality.
- Naive scope-creep failure: pulling background agent jobs or task unification into this TODO would turn a delegation-surface cleanup into a second async-task-system rewrite.

---

## High-Level Design

1. Keep the existing role registry and builders.
   `SUBAGENT_ROLES` and `_run_subagent(...)` are already the right internal seam. The harness should sit above them as a normalized public tool surface.

2. Add one generic public tool, tentatively `run_agent_task(...)`.
   Minimum input shape:
   - `role: "coding" | "research" | "analysis" | "reasoning"`
   - `task: str`
   - `max_requests: int = 0`
   - `domains: list[str] | None = None`
   - `inputs: list[str] | None = None`

3. Normalize the output contract.
   The harness should always return:
   - common metadata: `role`, `requests_used`, `request_limit`, `run_id`, `scope`
   - a stable display header
   - role-specific payload preserved in metadata

4. Preserve compatibility wrappers in v1.
   Each `run_*_subagent` tool becomes a thin call-through into the generic harness. Those wrappers remain for compatibility, but `run_agent_task` is the canonical surface going forward.

5. Keep task-model work downstream on purpose.
   This TODO makes later task unification easier, but it does not implement or decide that unification. Background agent jobs are intentionally deferred until the foreground harness exists.

---

## Implementation Plan

## TASK-1: Add a generic public agent harness tool over the existing role registry

files: `co_cli/tools/subagent.py`, `co_cli/agent.py`, `tests/test_tools_agent_harness.py`

Guard condition parity:
- Reuse the existing `SUBAGENT_ROLES` registry and `_run_subagent(...)` behavior.
- Keep current role-specific guard behavior such as role-based `domains` and `inputs` handling aligned with the nearest existing wrapper tool.

Implementation:
- Add `run_agent_task(...)` as a new tool in `co_cli/tools/subagent.py`.
- Validate `role` against `SUBAGENT_ROLES`.
- Validate role-specific optional arguments:
  - `domains` only for research unless a future role explicitly supports it
  - `inputs` only for analysis unless a future role explicitly supports it
- Route the request into `_run_subagent(...)` without changing existing focused builders.
- Register `run_agent_task` in `co_cli/agent.py` as a deferred tool with clear delegation-oriented search hints.

done_when:
- `uv run pytest tests/test_tools_agent_harness.py` passes and verifies:
  - valid roles dispatch successfully
  - invalid role returns `ModelRetry`
  - unsupported arg combinations return `ModelRetry`
  - `run_agent_task` is present in `build_tool_registry(...).tool_index`

success_signal: the model can delegate through one normalized tool instead of needing four different public entrypoints.

---

## TASK-2: Normalize output and convert existing role tools into compatibility wrappers

files: `co_cli/tools/subagent.py`, `tests/test_tools_agent_harness.py`, `tests/test_tools_subagent.py`

prerequisites: [TASK-1]

Implementation:
- Refactor the four `run_*_subagent` tools to call the new `run_agent_task` path internally.
- Keep their current public names and basic arg shapes.
- Ensure the generic harness returns a consistent metadata envelope while preserving role-specific payload fields.
- Update tool docstrings to mark the four legacy tools as compatibility surfaces and `run_agent_task` as the preferred long-term interface.

done_when:
- wrapper tools still pass their existing behavioral coverage
- generic harness output contains stable common metadata across all roles
- no caller-visible behavior regresses for the existing four tools

success_signal: existing prompts keep working, but the runtime now has one canonical delegation path.

---

## Testing

All pytest commands below follow the repository policy: pipe full output to a timestamped file under `.pytest-logs/`.

- Scope during implementation:
  - `uv run pytest tests/test_tools_agent_harness.py`
  - `uv run pytest tests/test_tools_subagent.py`
- Full suite before shipping:
  - `uv run pytest`

Required behavioral checks:
- generic harness validates roles and role-specific optional args correctly
- existing role tools remain compatible
- no unrestricted generic agent replaces the current focused role builders
- prompt text and tool descriptions make `run_agent_task` the canonical interface and the four role tools compatibility-only

---

## Open Questions

- None at planning time. Background agent jobs and task-model unification are intentionally downstream, not unresolved inside this TODO.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tier15-agent-harness`
