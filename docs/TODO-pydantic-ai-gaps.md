# TODO: Pydantic-AI Idiomatic Gaps

Audit of pydantic-ai API conformance and downstream simplifications.
Source: deep code scan + pydantic-ai doc research, 2026-03-17.
Replanned after bootstrap update, 2026-03-18. pydantic-ai upgraded to 1.70.0, 2026-03-18.

---

## Context

**Task type:** refactor — API conformance + dead code removal. No user-visible behavior changes.

**Bootstrap update delta (2026-03-18):**

- `create_deps()` restructured: `CoConfig.from_settings(settings, cwd=Path.cwd())` now resolves all cwd-relative paths in a single call — no `dataclasses.replace()` in bootstrap.
- `CoConfig` gained `system_prompt: str = ""` field; `personality_critique` field removed (critique now baked into `_build_system_prompt()` → `config.system_prompt`).
- `_bootstrap.py:36: config.system_prompt = _build_system_prompt(...)` — post-construction mutation introduced. This is the sole remaining `config.X =` mutation; it blocks TASK-8 until fixed.
- `prepare_provider` import and call removed from `agent.py` — **TASK-9 already shipped**.
- `agent.py` now passes `system_prompt=config.system_prompt` (field on config) instead of a local variable — TASK-1 still needed, source reference updated.
- `ROLE_REASONING` no longer imported in `agent.py` — TASK-4 must add it back.
- `inject_personality_critique` `@agent.instructions` removed from `agent.py` (critique merged into `_build_system_prompt`).

**Source-validated current state (2026-03-18):**

- TASK-1: `system_prompt=config.system_prompt` at `agent.py:131` — unshipped
- TASK-2: `usage=ctx.usage` absent at all four `agent.run()` callsites in `delegation.py`; existing manual `turn_usage.incr(result.usage())` already covers doom-loop guard; OTel parent-turn span undercounting is the remaining gap
- TASK-3: `tail_start` raw offset in both `truncate_history_window` and `precompute_compaction` — unshipped
- TASK-4: `Agent(model=None, ...)` at `agent.py:129` — unshipped; `ROLE_REASONING` must be re-imported in `agent.py` for the fix
- TASK-6: `GITHUB_TOKEN_BINLECODE` block at `agent.py:113-116` — unshipped; `from_settings()` now has `cwd` parameter but token resolution logic is unaffected
- TASK-7: `{prefix}_*` placeholder at `discover_mcp_tools()` line 291 — unshipped
- TASK-8: `_bootstrap.py:36: config.system_prompt = ...` is a post-construction mutation; fix must be part of TASK-8 (use `dataclasses.replace()` in `create_deps()` before adding `frozen=True`)
- TASK-9: ✓ SHIPPED — `prepare_provider` import and call removed from `agent.py`

**Workflow hygiene:** No orphaned DELIVERY docs. Clean.

---

## Problem & Outcome

**Problem:** Several pydantic-ai API misuses and dead code remain post-bootstrap-update:
- Mixed `system_prompt=`/`instructions=` API creates concatenation-order ambiguity
- OTel spans undercount tokens when sub-agents run (native usage threading absent)
- History window `tail_start` can orphan `ToolReturnPart`s — intermittent LLM errors on long conversations
- `Agent(model=None, ...)` is undocumented and fragile against future pydantic-ai validation tightening
- `config.system_prompt =` post-construction mutation prevents `frozen=True` enforcement on `CoConfig`
- Placeholder tool names and GitHub token in factory cause silent routing failures and config coupling

**Outcome:** All pydantic-ai callsites are idiomatic; history window is pair-safe at both boundaries; `CoConfig` is immutably enforced; all remaining dead code removed.

---

## Scope

| File | Tasks |
|------|-------|
| `co_cli/agent.py` | TASK-1, TASK-4, TASK-6, TASK-7 |
| `co_cli/context/_history.py` | TASK-3 |
| `co_cli/tools/delegation.py` | TASK-2 |
| `co_cli/deps.py` | TASK-6, TASK-8 |
| `co_cli/bootstrap/_bootstrap.py` | TASK-8 |

---

## Implementation Plan

### TASK-1 — Rename `system_prompt=` to `instructions=` in Agent constructor

**Severity:** High
**files:** `co_cli/agent.py`

**Problem:** `Agent(system_prompt=config.system_prompt)` at line 131 uses the deprecated constructor parameter while all per-turn injectors use `@agent.instructions`. Mixed API creates concatenation-order ambiguity and risks breakage if pydantic-ai formalizes the distinction.

**Fix:** Replace `system_prompt=config.system_prompt` → `instructions=config.system_prompt` in the `Agent()` call. One-line change.

**done_when:**
`grep -n "system_prompt=config.system_prompt" co_cli/agent.py` returns no matches.
`uv run pytest tests/test_agent.py -x` passes.

---

### TASK-2 — Thread `usage=ctx.usage` through sub-agent calls

**Severity:** High
**files:** `co_cli/tools/delegation.py`

**Problem:** All four `agent.run()` callsites in `delegation.py` lack `usage=ctx.usage`. pydantic-ai rolls sub-agent token counts into the parent run only when `usage=` is threaded. Without it, OTel parent-turn spans show artificially low token counts.

The existing manual `ctx.deps.runtime.turn_usage.incr(result.usage())` already covers the doom-loop guard — keep it. `usage=ctx.usage` serves native pydantic-ai tracking and OTel; both are needed.

**Fix:** Add `usage=ctx.usage` to each `agent.run()` call. Four callsites:
1. `delegate_coder` line ~37
2. `delegate_research` first call line ~105
3. `delegate_research` retry call line ~123
4. `delegate_analysis` line ~190

```python
result = await agent.run(
    task,
    deps=make_subagent_deps(ctx.deps),
    usage=ctx.usage,              # ← add
    usage_limits=UsageLimits(request_limit=max_requests),
    model_settings=rm.settings,
)
```

**done_when:**
`grep -c "usage=ctx.usage" co_cli/tools/delegation.py` prints `4`.
`uv run pytest tests/test_delegate_coder.py -x` passes.

---

### TASK-3 — Fix `tail_start` pair alignment in `truncate_history_window` and `precompute_compaction`

**Severity:** High
**files:** `co_cli/context/_history.py`

**Problem:** `tail_start = max(head_end, len(messages) - tail_count)` is a raw offset that can point to a `ModelRequest` containing `ToolReturnPart`s. The `ModelResponse` with their matching `ToolCallPart`s is at `tail_start - 1` — inside the dropped middle. The tail then begins with orphan tool returns, which pydantic-ai explicitly forbids.

The same raw formula exists in both `truncate_history_window` and `precompute_compaction`. Both must be fixed atomically. If only `truncate_history_window` is fixed, the stale-check (`precomputed.tail_start == tail_start`) will always evaluate `False` — permanently bypassing background compaction with no error or warning.

**Fix:** Extract a helper `_align_tail_start(messages, tail_start)` and call it from both functions:

```python
def _align_tail_start(messages: list[ModelMessage], tail_start: int) -> int:
    """Walk tail_start forward to a clean user-turn boundary.

    Ensures the tail never starts at a ModelRequest containing ToolReturnPart
    whose matching ToolCallPart was dropped into the middle section.
    Returns len(messages) if no clean boundary exists (caller should skip drop).
    """
    while tail_start < len(messages):
        msg = messages[tail_start]
        if isinstance(msg, ModelRequest) and not any(
            isinstance(p, ToolReturnPart) for p in msg.parts
        ):
            break
        tail_start += 1
    return tail_start
```

Call `tail_start = _align_tail_start(messages, tail_start)` immediately after computing the raw offset in both functions, with explicit skip guards:

```python
# in truncate_history_window:
tail_start = _align_tail_start(messages, tail_start)
if tail_start >= len(messages):
    return messages  # no clean boundary — keep everything

# in precompute_compaction:
tail_start = _align_tail_start(messages, tail_start)
if tail_start >= len(messages):
    return None  # no clean boundary — skip pre-computation
```

**done_when:**
`uv run pytest tests/test_history.py -x` passes.
`grep -c "_align_tail_start" co_cli/context/_history.py` returns `3` (one definition + two call sites).

---

### TASK-4 — Replace `model=None` with real default model at construction

**Severity:** Medium
**files:** `co_cli/agent.py`
**prerequisites:** [TASK-1]

**Problem:** `Agent(model=None, ...)` at `agent.py:129`. `None` is not a documented model value; pydantic-ai validates at `.run()` time today but may validate at construction in a future release.

**Fix:** Import `ROLE_REASONING` (was removed in bootstrap update) and `build_model` in `agent.py`. Resolve the reasoning model from config before constructing the agent and pass it as the constructor default. Use `.get()` with a `None` guard — mirrors `_bootstrap.py:34` — so users without `ROLE_REASONING` configured still get clean startup. Per-call role overrides at `agent.run(model=...)` are unchanged.

```python
from co_cli.config import ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS, ROLE_REASONING
from co_cli._model_factory import build_model

# in build_agent():
_reasoning_entry = config.role_models.get(ROLE_REASONING)
_primary_model = None  # fallback: existing behavior when role unconfigured
if _reasoning_entry:
    _primary_model, _ = build_model(
        _reasoning_entry, provider_name, config.llm_host, api_key=config.llm_api_key
    )

agent = Agent(
    model=_primary_model,              # ← real model when configured; None fallback otherwise
    instructions=config.system_prompt,  # ← requires TASK-1
    ...
)
```

**done_when:**
`grep -n "model=None" co_cli/agent.py` returns no matches inside the `Agent()` constructor call.
`uv run pytest tests/test_agent.py tests/test_bootstrap.py -x` passes.

---

### TASK-6 — Move GitHub token resolution out of `build_agent()`

**Severity:** Low
**files:** `co_cli/agent.py`, `co_cli/deps.py`
**prerequisites:** [TASK-8]

**Problem:** Hard-coded `os.getenv("GITHUB_TOKEN_BINLECODE", "")` block at `agent.py:113-116`. Issues: (1) config-level token overrides are bypassed; (2) invisible to `check_runtime()` health checks; (3) personal naming convention hard-coded in factory.

**Scope:** Relocate-only. The `name == "github"` coupling moves from `agent.py` to `deps.py`. Known limitation — a proper generalization (e.g., a `token_env` field on `MCPServerConfig`) belongs in a dedicated config-schema delivery.

**Fix:** In `CoConfig.from_settings()`, resolve the token while building the `mcp_servers` dict — before the `CoConfig` instance is constructed (compatible with `frozen=True` from TASK-8). Use `model_copy(update=...)` on `MCPServerConfig` to produce a new instance with the resolved env. The new `mcp_servers=resolved_servers` kwarg **replaces** the existing `mcp_servers=dict(s.mcp_servers) if s.mcp_servers else {}` kwarg in the `cls(...)` call — do not leave both.

```python
# in CoConfig.from_settings() — before the cls(...) call:
resolved_servers: dict[str, MCPServerConfig] = {}
for name, srv_cfg in (s.mcp_servers or {}).items():
    if name == "github":
        env = dict(srv_cfg.env) if srv_cfg.env else {}
        if "GITHUB_PERSONAL_ACCESS_TOKEN" not in env:
            token = os.getenv("GITHUB_TOKEN_BINLECODE", "")
            if token:
                env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
                # TODO(token-env): generalize when MCPServerConfig gains a token_env field
        srv_cfg = srv_cfg.model_copy(update={"env": env})
    resolved_servers[name] = srv_cfg

return cls(
    ...
    mcp_servers=resolved_servers,  # ← replaces: dict(s.mcp_servers) if s.mcp_servers else {}
    ...
)
```

Remove the corresponding block from `agent.py:112-116`.

**Sequencing:** Apply after TASK-8. Dict is built fresh before `CoConfig` is constructed — `frozen=True` does not affect this. No mutation of an existing frozen instance occurs.

**done_when:**
`grep -n "GITHUB_TOKEN_BINLECODE" co_cli/agent.py` returns no matches.
`grep -n "GITHUB_TOKEN_BINLECODE" co_cli/deps.py` returns a match (token resolution moved there).
`uv run pytest tests/test_agent.py -x` passes.

---

### TASK-7 — Remove placeholder tool names on `list_tools()` failure

**Severity:** Low
**files:** `co_cli/agent.py`

**Problem:** `discover_mcp_tools()` appends `f"{prefix}_*"` on `list_tools()` failure (line 291). This placeholder never matches real tool names — every MCP tool from a degraded server silently misses approval routing.

**Fix:** On failure, log and skip:

```python
except Exception as e:
    logger.warning(
        "MCP tool list failed for %r: %s", inner.tool_prefix, e
    )
    # contribute nothing — real tool names unknown
```

Remove the two lines that set `prefix` and append the placeholder.

**done_when:**
`grep -n "prefix}_\*" co_cli/agent.py` returns no matches.
`uv run pytest tests/test_agent.py -x` passes.

---

### TASK-8 — Eliminate `config.system_prompt =` mutation and add `frozen=True` to `CoConfig`

**Severity:** Improvement
**files:** `co_cli/deps.py`, `co_cli/bootstrap/_bootstrap.py`

**Problem (two-part):**

1. `_bootstrap.py:36: config.system_prompt = _build_system_prompt(...)` — post-construction field mutation on `CoConfig`. This is the sole remaining `config.X =` mutation (confirmed by `grep -rn "config\.[a-z_]* =" co_cli/` returning only this line). It prevents `frozen=True` from being applied.

2. `CoConfig` is documented as read-only injected configuration but is not enforced as immutable. After fixing part 1, it has zero post-construction field writes at runtime.

**Fix:**

Step 1 — eliminate mutation in `_bootstrap.py`. Replace:
```python
config.system_prompt = _build_system_prompt(config.llm_provider, normalized_model, config)
```
with:
```python
import dataclasses
config = dataclasses.replace(
    config,
    system_prompt=_build_system_prompt(config.llm_provider, normalized_model, config),
)
```
`dataclasses.replace()` constructs a new instance — works on both unfrozen (current) and frozen (after step 2) dataclasses.

Step 2 — add `frozen=True`:
Change `@dataclass` → `@dataclass(frozen=True)` on `CoConfig` in `deps.py`.

**Limitation:** `frozen=True` prevents field *rebinding* — reassigning `config.shell_safe_commands = [...]` raises `FrozenInstanceError`. It does NOT prevent in-place *element mutation* — `config.shell_safe_commands.append(x)` still succeeds silently. The actual enforcement for list-field element mutation is the existing convention that no tool modifies these lists; `frozen=True` does not close that gap.

**Out-of-scope survivor:** `sync_knowledge()` in `_bootstrap.py:89` does `deps.services.knowledge_index = None` — this is on `CoServices`, not `CoConfig`. `CoServices` is intentionally mutable (error recovery path) and is unaffected by this task.

**done_when:**
`grep -rn "\.system_prompt\s*=" co_cli/` returns no matches (the specific mutation eliminated, regardless of variable name).
`grep -rn "config\.[a-z_]* =" co_cli/` returns no matches (belt-and-suspenders check).
`grep -n "frozen=True" co_cli/deps.py` returns a match on the `CoConfig` decorator line.
`uv run pytest tests/test_bootstrap.py tests/test_agent.py tests/test_delegate_coder.py tests/test_capabilities.py -x` passes.

---

## Implementation Order

| Task | Severity | Effort | File(s) | Batch |
|------|----------|--------|---------|-------|
| TASK-1 — `system_prompt=` → `instructions=` | High | Trivial | `agent.py` | A |
| TASK-7 — remove `list_tools()` placeholders | Low | Trivial | `agent.py` | A |
| TASK-2 — `usage=ctx.usage` in sub-agent calls | High | Small | `delegation.py` | B |
| TASK-3 — pair-safe tail boundary + precompute | High | Small | `_history.py` | B |
| TASK-8 — eliminate mutation + `frozen=True` | Improvement | Small | `_bootstrap.py`, `deps.py` | B |
| TASK-6 — GitHub token out of `build_agent()` | Low | Small | `agent.py`, `deps.py` | C (after TASK-8) |
| TASK-4 — `model=None` → real default | Medium | Medium | `agent.py` | C (after A) |

## Testing

```bash
mkdir -p .pytest-logs

# Batch A (TASK-1, TASK-7)
uv run pytest tests/test_agent.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-batch-a.log

# Batch B (TASK-2, TASK-3, TASK-8)
uv run pytest tests/test_delegate_coder.py tests/test_history.py tests/test_bootstrap.py tests/test_agent.py tests/test_capabilities.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-batch-b.log

# Batch C (TASK-4, TASK-6)
uv run pytest tests/test_agent.py tests/test_bootstrap.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-batch-c.log

# Full regression after all batches
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```

## Open Questions

None — all questions answered by source inspection.

---

## Final — Team Lead

Plan approved. Three cycles: C1 and C2 on original plan; C3/C4 replan after bootstrap update. All blocking issues resolved (TASK-9 shipped, TASK-8 mutation prerequisite added, TASK-2 API verified). C4 both Core Dev and PO returned `Blocking: none`.

**pydantic-ai version note:** Upgraded to 1.70.0 (latest stable, Mar 17 2026). `Usage.__add__` dict mutation bug (v1.69.0 fix) now included — sub-agent usage accumulation correctness restored ahead of TASK-2.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev pydantic-ai-gaps`

## Cycle C3 — Team Lead
Replanning after bootstrap update. TASK-9 shipped. TASK-8 gains new prerequisite step (eliminate `config.system_prompt =` mutation). TASK-1 source reference updated. TASK-4 must re-import `ROLE_REASONING`. Submitting for Core Dev and PO review.

## Cycle C3 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** The plan is well-scoped, tackles real failure modes (orphaned tool returns, silent approval-routing misses, OTel undercounting), and produces no user-visible behavior changes. All tasks are tightly bounded to the audit findings with no speculative additions.

**Major issues:**
- none

**Minor issues:**
- **PO-m-1** [TASK-6]: The `name == "github"` coupling in `CoConfig.from_settings()` is acknowledged as a known limitation, but embedding it there moves personal naming convention debt one layer deeper into config construction rather than eliminating it. The fix is still an improvement over the factory, so it's acceptable for this delivery — but the task description should note this explicitly as a stop-gap and reference the deferred `token_env` field work so it doesn't get treated as the final design. Recommendation: add a `# TODO(token-env): generalize when MCPServerConfig gains token_env field` comment at the new callsite to keep the debt visible.
- **PO-m-2** [TASK-8]: The stated limitation that `frozen=True` does not protect mutable list fields is correct and disclosed. Since no tool currently mutates them and convention is the stated enforcement, this is fine for now — but the limitation comment in the TASK description could be clearer that it applies at the field level, not element level (i.e., reassigning `config.shell_safe_commands = [...]` will raise; `.append()` will not). Recommendation: tighten the wording to avoid future confusion for contributors reading the doc.

## Cycle C3 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2
**Summary:** Two blocking issues: TASK-2's `usage=ctx.usage` fix assumes a pydantic-ai `agent.run()` keyword argument and `RunContext.usage` accessor that do not exist in the public API — the plan conflates the `UsageLimits` parameter with the `RunUsage` accumulator, and the existing `turn_usage.incr()` pattern may already be sufficient. TASK-8's `done_when` grep gate is too narrow and will pass green even if a mutation survives on a differently-named variable, while silently ignoring an existing post-construction field mutation on `CoServices` in `sync_knowledge()` that the task description does not acknowledge.

**Major issues:**
- **CD-M-1** [TASK-2]: The plan adds `usage=ctx.usage` to each `agent.run()` callsite, but the pydantic-ai `Agent.run()` signature accepts `usage_limits: UsageLimits | None` — not a `usage=` keyword taking a `RunUsage`. `ctx.usage` is not a documented public field on `RunContext`; the pydantic-ai API does not expose parent-run usage threading through a `usage=` parameter on `run()`. The existing manual `ctx.deps.runtime.turn_usage.incr(result.usage())` in all three delegation tools already rolls sub-agent tokens into the parent-turn counter, which the doom-loop guard reads from. OTel span undercounting is a real gap, but the mechanism proposed is incorrect. Recommendation: Before writing any code, verify the exact `Agent.run()` signature with `uv run python -c "import inspect, pydantic_ai; print(inspect.signature(pydantic_ai.Agent.run))"` against the installed version and document the confirmed parameter. If `usage=` accepting a `RunUsage` does not exist, reframe TASK-2 as a documentation/validation task: confirm that `turn_usage.incr()` is the correct and complete pattern, close the OTel gap via span attributes rather than a missing API call, and mark the task done when the verification is recorded in the plan.
- **CD-M-2** [TASK-8]: The `done_when` gate `grep -rn "config\.[a-z_]* =" co_cli/` only catches mutations on a variable named exactly `config`. Two gaps: (1) `_bootstrap.py`'s `sync_knowledge()` function (line 89) does `deps.services.knowledge_index = None` — this is on `CoServices`, not `CoConfig`, so the grep misses it entirely. `CoServices` is not frozen, so this particular mutation survives after TASK-8 ships. The plan does not acknowledge this survivor mutation, leaving an implicit assumption that `CoServices` is also safe to mutate post-construction. (2) The grep would also miss `config.system_prompt =` if the variable were named `cfg` or `co_config` in a refactor. Recommendation: (a) Augment `done_when` with the targeted check `grep -rn "\.system_prompt\s*=" co_cli/` to catch the specific field regardless of variable name; (b) add an explicit note in TASK-8 acknowledging that `deps.services.knowledge_index = None` in `sync_knowledge()` is a surviving out-of-scope mutation on `CoServices` (not `CoConfig`) and is not blocked by this task.

**Minor issues:**
- **CD-m-1** [TASK-3]: The `_align_tail_start` helper is well-designed but the plan's fix pseudocode does not spell out the "skip the drop" behavior at both callsites concretely. For `truncate_history_window`, "skip" means `return messages` unchanged; for `precompute_compaction`, "skip" means `return None`. Without this, an implementer might insert a bare `pass` or an empty list assignment. The `done_when` grep count of `3` is correct and machine-verifiable. Recommendation: Add the explicit skip guards to the pseudocode: `if tail_start >= len(messages): return messages` and `if tail_start >= len(messages): return None` for each callsite respectively.
- **CD-m-2** [TASK-4]: The fix pseudocode uses `config.role_models[ROLE_REASONING]` (dict key access), which raises `KeyError` when `ROLE_REASONING` is not configured. The identical lookup in `_bootstrap.py:34` already uses the safe `.get(ROLE_REASONING)` with a `None` guard and a `normalized_model = ""` fallback. The `build_agent()` fix must mirror this pattern; otherwise a user without `ROLE_REASONING` in their `role_models` gets a crash at agent construction rather than a clean startup. Recommendation: Replace the index access with `config.role_models.get(ROLE_REASONING)` and add a `None`-guard that falls back to keeping `model=None` (current behavior) when the role is absent, so the fix is a no-op for users who haven't configured the reasoning role.
- **CD-m-3** [TASK-6]: The fix pseudocode correctly builds `resolved_servers` before calling `cls(...)`, but the existing `mcp_servers=dict(s.mcp_servers) if s.mcp_servers else {}` argument in `CoConfig.from_settings()` must be replaced — not left alongside — the new `mcp_servers=resolved_servers` argument. If both remain, the later kwarg overwrites the earlier one silently in Python (no error, just wrong behavior). Recommendation: Make the pseudocode explicit that the new `mcp_servers=resolved_servers` line replaces the existing `mcp_servers=dict(s.mcp_servers) if s.mcp_servers else {}` line in the `cls(...)` call, and add a `done_when` grep: `grep -n "GITHUB_TOKEN_BINLECODE" co_cli/deps.py` returns a match (confirms the token resolution moved there).

## Cycle C3 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | reject   | API verified: `uv run python -c "import inspect, pydantic_ai; print(inspect.signature(pydantic_ai.Agent.run))"` confirms `usage: '_usage.RunUsage | None' = None` in the signature; `RunContext.__init__` confirms `usage: 'RunUsage'` field → `ctx.usage` is valid. The mechanism is correct. |
| CD-M-2   | adopt    | (a) Tighten done_when to `grep -rn "\.system_prompt\s*=" co_cli/`; (b) add explicit note in TASK-8 that `knowledge_index = None` in `sync_knowledge()` is on `CoServices`, not `CoConfig` — out of scope for this task |
| CD-m-1   | adopt    | Add explicit skip guards to TASK-3 pseudocode: `return messages` / `return None` |
| CD-m-2   | adopt    | TASK-4: use `.get(ROLE_REASONING)` with None guard falling back to `model=None` |
| CD-m-3   | adopt    | TASK-6: explicitly note `mcp_servers=resolved_servers` replaces existing kwarg; add done_when grep in deps.py |
| PO-m-1   | adopt    | TASK-6: add `# TODO(token-env):` comment at new callsite |
| PO-m-2   | adopt    | TASK-8: tighten limitation wording to field rebinding vs. element mutation |

## Cycle C4 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** Both C3 minor items are resolved in the plan text. The TODO(token-env) comment is present in the TASK-6 pseudocode at the correct callsite, and the TASK-8 limitation wording now explicitly distinguishes field rebinding from element mutation with concrete examples. No new scope, value, or first-principles concerns.

**C3 resolution check:**
- PO-m-1: resolved — `# TODO(token-env): generalize when MCPServerConfig gains a token_env field` appears at line 218-219 in the TASK-6 fix pseudocode, at the exact callsite where the coupling lives.
- PO-m-2: resolved — TASK-8 Limitation now reads "prevents field *rebinding*" and "does NOT prevent in-place *element mutation*" with `= [...]` vs. `.append(x)` examples; the distinction is unambiguous for contributors.

**Major issues (new):**
- none

**Minor issues (new):**
- none

## Cycle C4 — Core Dev

**Assessment:** approve
**Blocking:** none
**Summary:** All five C3 items are fully resolved in the updated plan. API re-verified this cycle: `Agent.run()` signature confirms `usage: '_usage.RunUsage | None' = None` and `RunContext.__init__` confirms `usage: 'RunUsage'` field, so `usage=ctx.usage` is correct. No new blocking issues found.

**C3 resolution check:**
- CD-M-1: resolved (TL reject accepted) — `Agent.run(usage=...)` and `ctx.usage` re-verified via `inspect.signature` this cycle; mechanism is correct.
- CD-M-2: resolved — TASK-8 `done_when` leads with `grep -rn "\.system_prompt\s*=" co_cli/` (field-targeted, variable-agnostic); TASK-8 body explicitly calls out `deps.services.knowledge_index = None` as an out-of-scope survivor on `CoServices`, not `CoConfig`.
- CD-m-1: resolved — TASK-3 pseudocode now includes explicit skip guards: `if tail_start >= len(messages): return messages` in `truncate_history_window` and `if tail_start >= len(messages): return None` in `precompute_compaction`.
- CD-m-2: resolved — TASK-4 pseudocode uses `config.role_models.get(ROLE_REASONING)` with `_primary_model = None` fallback; mirrors `_bootstrap.py:34` pattern exactly.
- CD-m-3: resolved — TASK-6 pseudocode explicitly states `mcp_servers=resolved_servers` replaces the existing `mcp_servers=dict(s.mcp_servers) if s.mcp_servers else {}` kwarg; `done_when` includes `grep -n "GITHUB_TOKEN_BINLECODE" co_cli/deps.py` confirming the relocation.

**Major issues (new):**
- none

**Minor issues (new):**
- none
