# TODO: Coding Tool Convergence Execution Plan

Date: 2026-02-28  
Source: converted from `docs/TAKEAWAY-coding-tool-convergence.md`

This TODO tracks unimplemented work to align Co's coding workflow with converged, high-value patterns from top reference systems, while preserving Co's approval-first architecture.

## Sequencing

1. TODO 1: Native File Tools for Coding (P0)
2. TODO 2: Shell Policy Engine S1 (P0)
3. TODO 3: `delegate_coder` Tool-Level Subagent (P1)
4. TODO 4: Coding Eval Gates (P1)
5. TODO 5: Workspace Checkpoint + Rewind (P2)
6. TODO 6: Approval Risk Classifier (P2, optional)

---

## TODO 1 — Native File Tools for Coding (P0)

### Gap Analysis

Current state:
- Coding edits are mostly executed through `run_shell_command`.
- Agent tool registry has no first-class `read_file` / `edit_file` / `write_file` tools.

Converged pattern:
- Top coding systems expose native file operations as first-class tools.
- Shell remains fallback, not primary editing surface.

### Reason of Adoption

1. Reduce command-generation error surface for common file tasks.
2. Improve determinism and reviewability of file mutations.
3. Enable stronger path-boundary guarantees than raw shell editing.

### Suitability / Benefit for Co

1. Direct fit with existing `agent.tool(...)` registration and approval model.
2. Keeps shell tool for non-file ops while shifting routine edits to safer primitives.
3. Improves user trust by making edit intent explicit at tool-call level.

### Implementation Design (Code-Ready)

Add new module:
- `co_cli/tools/files.py`

Add tool functions:
```python
async def list_directory(ctx: RunContext[CoDeps], path: str = ".", glob: str | None = None, max_entries: int = 500) -> dict[str, Any]
async def read_file(ctx: RunContext[CoDeps], path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]
async def find_in_files(ctx: RunContext[CoDeps], query: str, path: str = ".", glob: str | None = None, max_matches: int = 200) -> dict[str, Any]
async def write_file(ctx: RunContext[CoDeps], path: str, content: str) -> dict[str, Any]
async def edit_file(ctx: RunContext[CoDeps], path: str, search: str, replace: str, replace_all: bool = False) -> dict[str, Any]
```

Path safety layer:
- Resolve all paths against workspace root.
- Reject traversal and symlink escape.
- Reject writes outside workspace root.

Suggested helper contract:
```python
def resolve_workspace_path(workspace_root: Path, user_path: str) -> Path:
    # raise ValueError on escape
```

Agent registration:
- `co_cli/agent.py`
  - register read-only tools with `requires_approval=all_approval`
  - register `write_file` / `edit_file` with `requires_approval=True`

Tool return contract:
- `dict[str, Any]` with `display` always present.
- Include metadata fields: `path`, `line_count`, `match_count`, `bytes_written`, `changed`.

Tests:
- `tests/test_tools_files.py` (new)
  - path traversal rejection
  - symlink escape rejection
  - read ranges correctness
  - edit one vs edit all semantics
  - write approval path exercised through orchestration functional tests

### Acceptance Criteria

1. File read/list/find tasks run without shell for normal workflows.
2. All write/edit actions require approval.
3. Path and symlink escapes are blocked.
4. Tool outputs follow `display` + metadata contract.

---

## TODO 2 — Shell Policy Engine S1 (P0)

### Gap Analysis

Current state:
- Safe command check is prefix + operator blacklist in `_approval.py`.
- Classification is intentionally simple but misses policy-grade command parsing.

Converged pattern:
- Stronger systems use parser/policy semantics beyond flat prefix checks.

### Reason of Adoption

1. Reduce false-safe classifications for complex command forms.
2. Keep approval UX while tightening deterministic safety guarantees.

### Suitability / Benefit for Co

1. Works with existing deferred approval flow (`_handle_approvals`).
2. Preserves "approval is security boundary" philosophy while hardening pre-check.

### Implementation Design (Code-Ready)

Add policy evaluator:
- `co_cli/shell_policy.py` (new)

Core API:
```python
class ShellDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"

@dataclass
class ShellPolicyResult:
    decision: ShellDecision
    reason: str

def evaluate_shell_command(cmd: str, safe_prefixes: list[str], workspace_root: Path) -> ShellPolicyResult
```

Policy behavior:
1. Reject dangerous structural patterns (`deny`): control chars, heredocs in restricted mode, suspicious env injection forms.
2. Require approval for chaining/redirection/subshell forms.
3. Allow only exact/prefix-safe read operations for auto-approval.
4. Preserve backward compatibility: unknown => `require_approval`.

Integrate:
- Replace `_is_safe_command` call in `_orchestrate.py::_handle_approvals` with policy evaluator decision.
- Keep existing user prompt flow unchanged.

Tests:
- `tests/test_shell_policy.py` (new)
  - allow/read-only
  - require-approval for compound commands
  - deny cases
  - regression coverage for current safe list semantics

### Acceptance Criteria

1. No command is auto-approved unless policy returns explicit `ALLOW`.
2. Existing harmless workflows still auto-approve.
3. High-risk patterns are denied or forced to approval predictably.

---

## TODO 3 — `delegate_coder` Tool-Level Subagent (P1)

### Gap Analysis

Current state:
- Single-agent runtime only; no implemented coding delegation tool.

Converged pattern:
- Specialized subagents are used for focused workflows.
- Delegation is tool-invoked and traceable.

### Reason of Adoption

1. Use coding-specialized model (`qwen3-coder-next:*‑agentic`) without changing parent model.
2. Improve completion quality for complex code tasks.

### Suitability / Benefit for Co

1. Aligns with existing planned delegation pattern.
2. Keeps `run_turn()` as primary orchestration primitive.
3. Supports incremental rollout behind explicit tool invocation.

### Implementation Design (Code-Ready)

Add subagent factory:
- `co_cli/agents/coder.py` (new)

```python
class CoderResult(BaseModel):
    summary: str
    diff_preview: str
    files_touched: list[str]
    tests_run: list[str] = []
    confidence: float

def make_coder_agent(model_name: str, base_url: str) -> Agent[CoDeps, CoderResult]
```

Add delegation tool:
- `co_cli/tools/delegation.py` (new)

```python
async def delegate_coder(
    ctx: RunContext[CoDeps],
    task: str,
    max_requests: int = 12,
    coder_model: str = "qwen3-coder-next:q4_k_m-agentic",
) -> dict[str, Any]
```

Execution model (safe first release):
1. Subagent analyzes, plans, and returns structured patch plan + diff preview.
2. Parent executes actual file mutation through approved `edit_file` / `write_file` tools.
3. No subagent direct write path in phase 1.

Agent registration:
- `co_cli/agent.py`: register `delegate_coder` as read-only tool (`requires_approval=all_approval`).

Config extension:
- `co_cli/config.py`: optional `coder_delegate_model` field.
- Env: `CO_CLI_CODER_DELEGATE_MODEL`.

Tests:
- `tests/test_delegate_coder.py` (new)
  - structured output shape
  - model routing to coder model
  - no write bypass path
  - usage limit respected

### Acceptance Criteria

1. Parent model remains default orchestrator.
2. Coding specialization uses delegated coder model only at tool level.
3. No file mutation bypasses approval-gated write/edit tools.
4. OTel spans show parent tool span with nested subagent run.

---

## TODO 4 — Coding Eval Gates (P1)

### Gap Analysis

Current state:
- Existing eval suite covers behavior/signal/safety paths, not coding-delegation quality gates.

Converged pattern:
- Mature systems gate tool quality continuously for regression prevention.

### Reason of Adoption

1. Prevent silent degradation when adding file tools + delegation.
2. Quantify real quality gains from coder subagent.

### Suitability / Benefit for Co

1. Fits current eval framework and telemetry stack.
2. Enables release gating by measurable coding outcomes.

### Implementation Design (Code-Ready)

Add eval runner:
- `evals/eval_coding_toolchain.py` (new)
- Data: `evals/coding_toolchain.jsonl` (new)

Metrics:
1. `edit_success_rate`
2. `patch_apply_rate`
3. `post_edit_test_pass_rate`
4. `approval_prompts_per_case`
5. `tool_error_recovery_rate`

Case format:
```json
{"id":"...", "prompt":"...", "expected_files":["..."], "checks":[...]}
```

Gate defaults:
- `edit_success_rate >= 0.80`
- `patch_apply_rate >= 0.90`
- `tool_error_recovery_rate >= 0.70`

Outputs:
- `evals/coding_toolchain-data.json`
- `evals/coding_toolchain-result.md`

### Acceptance Criteria

1. New coding eval runs in CI/local with reproducible output artifacts.
2. Release process can fail on gate regression.

---

## TODO 5 — Workspace Checkpoint + Rewind (P2)

### Gap Analysis

Current state:
- No explicit workspace checkpoint and rewind flow for coding turns.

Converged pattern:
- Rewind/undo is common where agents edit code.

### Reason of Adoption

1. Increase recoverability for failed or unwanted edits.
2. Reduce risk perception for autonomous code changes.

### Suitability / Benefit for Co

1. Complements approval-first model.
2. Natural extension once native file write/edit tools exist.

### Implementation Design (Code-Ready)

Add checkpoint module:
- `co_cli/workspace_checkpoint.py` (new)

Core APIs:
```python
def create_checkpoint(root: Path, label: str) -> str
def list_checkpoints(root: Path) -> list[CheckpointInfo]
def restore_checkpoint(root: Path, checkpoint_id: str) -> RestoreResult
```

Storage approach:
- Git-backed snapshot if repo exists.
- Filesystem copy fallback for non-git workspace (bounded to changed files manifest).

Commands/tools:
- Slash commands in `_commands.py`:
  - `/checkpoint [label]`
  - `/rewind [checkpoint_id|last]`

Safety:
- Rewind requires explicit approval confirmation prompt.

Tests:
- `tests/test_rewind.py` (new)

### Acceptance Criteria

1. User can revert last coding change set reliably.
2. Restores both content and file lifecycle events (create/delete/modify).

---

## TODO 6 — Approval Risk Classifier (P2, Optional)

### Gap Analysis

Current state:
- Approval routing is static by tool + safe-command checks.
- No learned risk tiering.

Converged pattern:
- Some systems reduce prompt fatigue via policy/risk routing.

### Reason of Adoption

1. Reduce approval fatigue without lowering safety.
2. Keep user in control for high-risk actions.

### Suitability / Benefit for Co

1. Optional overlay; can be flag-guarded.
2. Works with existing deferred approval path.

### Implementation Design (Code-Ready)

Add classifier:
- `co_cli/_approval_risk.py` (new)

```python
class ApprovalRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

def classify_tool_call(tool_name: str, args: dict[str, Any]) -> ApprovalRisk
```

Routing policy:
1. `HIGH` => always prompt
2. `MEDIUM` => prompt unless explicit scoped session approval
3. `LOW` => auto-approve only if policy flag enabled

Config:
- `approval_risk_enabled: bool = False`
- `approval_auto_low_risk: bool = False`

Integrate into:
- `_orchestrate.py::_handle_approvals`

Tests:
- `tests/test_approval_risk.py` (new)

### Acceptance Criteria

1. Feature disabled by default.
2. Enabling feature reduces prompt count without approval bypass regressions.

---

## Exit Criteria (Plan-Level)

1. Coding tasks default to file tools over shell.
2. Coder-specific model is used only through tool-level delegation.
3. Safety posture remains approval-first with no bypass path.
4. Coding quality is protected by dedicated eval gates.
