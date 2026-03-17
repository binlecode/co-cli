# TODO: Post-Antipattern Fix Simplifications

**Task type: refactor** — code simplifications unlocked once `bootstrap-antipattern-fixes` ships.
**Prerequisites: `bootstrap-antipattern-fixes` fully delivered (Gate 3 accepted).**

---

## Context

Two simplifications become safe only after the antipattern fixes land:

1. **`frozen=True` on `CoConfig`** — enabled by TASK-1 (removes `session_id` mutation) and TASK-7 (removes `mcp_count` set). After those ship, `CoConfig` has zero post-construction field writes at runtime. Freezing it makes accidental mutation a `FrozenInstanceError` at call time rather than a silent state divergence.

2. **Remove redundant `prepare_provider()` call in `agent.py`** — enabled by TASK-4 (strips env mutation from `prepare_provider()`) and TASK-5 (makes `config` required in `get_agent()`). After those ship, `prepare_provider()` is a pure validator already called inside `ModelRegistry.from_config()` at `create_deps()` time. The second call in `agent.py` is redundant — by the time `get_agent()` is invoked, the provider has already been validated at bootstrap.

Neither simplification is safe to apply before its prerequisites: freezing `CoConfig` while `deps.config.session_id = ...` writes still exist will raise `FrozenInstanceError` at bootstrap; removing `prepare_provider()` from `agent.py` before TASK-4 strips the env mutation would remove the only `os.environ` side-effect in the Gemini boot path.

---

## Problem & Outcome

**Problem:**
- `CoConfig` is documented as read-only config but is not enforced as immutable — frozen dataclass semantics are the correct enforcement tool.
- `agent.py` validates the provider a second time on every `get_agent()` call, after `create_deps()` already validated and raised on failure. Dead validation path imports `prepare_provider` into `agent.py` for no runtime value.

**Outcome:**
- `CoConfig` is `@dataclass(frozen=True)` — accidental mutation raises immediately at the write site.
- `agent.py` no longer imports or calls `prepare_provider()` — the function lives only in `_model_factory.py` where it is needed by `ModelRegistry.from_config()`.

---

## Scope

**In scope:**
- Add `frozen=True` to `CoConfig`; verify no remaining mutation sites exist
- Remove `prepare_provider` import and call from `agent.py`

**Out of scope:**
- Freezing `CoSessionState` or `CoRuntimeState` — both are intentionally mutable
- Removing `prepare_provider()` from `_model_factory.py` — it is still needed by `from_config()` and is a named, documented validation step
- Any other change to `agent.py` or `deps.py` beyond the two targeted edits

---

## Implementation Plan

### TASK-A: Freeze `CoConfig`

**files:**
- `co_cli/deps.py`

**done_when:**
`grep -n "frozen=True" co_cli/deps.py` returns a match on the `CoConfig` dataclass decorator.
`python -c "import dataclasses; from co_cli.deps import CoConfig; c = CoConfig(); dataclasses.replace(c, theme='dark')"` exits cleanly (replace still works on frozen dataclasses).
`uv run pytest tests/test_bootstrap.py tests/test_agent.py tests/test_delegate_coder.py tests/test_capabilities.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-freeze-ta.log` passes.

**prerequisites:** `bootstrap-antipattern-fixes` Gate 3 accepted.

Steps:
1. Before making the change: run `grep -rn "deps\.config\.[a-z_]* =" co_cli/ tests/ evals/` and confirm zero matches (verifies all mutation sites were removed by the antipattern tasks).
2. In `deps.py`: change `@dataclass` on `CoConfig` → `@dataclass(frozen=True)`.
3. Run the `done_when` commands above. If any test fails with `FrozenInstanceError`, that is a missed mutation site from the antipattern tasks — fix at that site, do not unfreeze `CoConfig`.

---

### TASK-B: Remove redundant `prepare_provider()` call from `agent.py`

**files:**
- `co_cli/agent.py`

**done_when:**
`grep -n "prepare_provider" co_cli/agent.py` returns no matches.
`uv run pytest tests/test_agent.py tests/test_google_cloud.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-freeze-tb.log` passes.

**prerequisites:** `bootstrap-antipattern-fixes` Gate 3 accepted (specifically TASK-4 and TASK-5).

Steps:
1. In `agent.py`: remove `from co_cli._model_factory import prepare_provider` from the imports.
2. Remove the `prepare_provider(provider_name, config.llm_api_key)` call (currently line 101 — verify line after antipattern tasks ship).
3. Verify `_model_factory.py` still imports and calls `prepare_provider` in `ModelRegistry.from_config()` — that call must remain.

---

## Testing

```bash
mkdir -p .pytest-logs

# After TASK-A (freeze CoConfig):
uv run pytest tests/test_bootstrap.py tests/test_agent.py tests/test_delegate_coder.py tests/test_capabilities.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-freeze-ta.log

# After TASK-B (remove prepare_provider from agent.py):
uv run pytest tests/test_agent.py tests/test_google_cloud.py -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-freeze-tb.log

# Full regression:
uv run pytest -x -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-post-ap-simplification-full.log
```

No new test files required.

---

## Open Questions

None — both simplifications are fully analyzable from current source.
