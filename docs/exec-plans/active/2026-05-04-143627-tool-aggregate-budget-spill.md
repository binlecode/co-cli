# Plan: bounded tool output — tool-call cap, per-call spill refit, per-llm-turn aggregate spill

Task type: code-feature

## Status

Delivered in commit `491ab27` (v0.8.126). L0 cap, L1 refit, L2 aggregate spill, OTEL coverage, and 23 unit tests are in main. Two follow-up items remain.

## Remaining tasks

### TASK-A — Integration test for L0 brake ✓ DONE

prerequisites: []

Closed with the pragmatic approach: the brake and run-step reset are already exhaustively covered in `test_flow_tool_call_limit.py`; the only real gap was the `enforce_tool_call_limit` OTEL span from `after_node_run`, which a real-LLM test would not have tested any more directly.

Delivered in `tests/test_flow_tool_call_limit_otel.py` (3 tests):
- `test_enforce_tool_call_limit_span_on_saturation` — 8 calls → span fires with `issued=8`, `allowed=6`, `rejected=2`, `limit_exceeded=True`, `limit=6`.
- `test_enforce_tool_call_limit_span_within_cap` — 3 calls → span fires with `limit_exceeded=False`.
- `test_enforce_tool_call_limit_span_skipped_for_non_call_tools_node` — non-`CallToolsNode` → no span.

Fixture monkeypatches `lifecycle_module._TRACER` with an `InMemorySpanExporter`-backed tracer (deterministic, no global OTEL state side effects). Uses `CallToolsNode` and `UserPromptNode` from the public `pydantic_ai` API.

done_when:
- `uv run pytest tests/test_flow_tool_call_limit_otel.py -x` green. ✓

### TASK-B — Fix stale inline comment

prerequisites: []

`co_cli/agent/_tool_call_limit.py:5` says `# one worst-case model turn fits the tail at 32K; see Sizing`. The plan's Sizing section was rewritten around the 64K floor (the 32K hypothetical row was removed). The comment now points readers at a claim that no longer exists in the doc.

files:
- `co_cli/agent/_tool_call_limit.py:5` — change inline comment to:
  ```python
  MAX_TOOL_CALLS_PER_MODEL_TURN = 6  # six worst-case non-spilling calls fit the 64K-floor tail; see plan Sizing
  ```

done_when:
- Comment matches the Sizing reasoning in this plan (and the section in `docs/specs/compaction.md` if that spec ends up referencing the constant).
- `grep -n "32K" co_cli/agent/_tool_call_limit.py` returns no match.

success_signal: a reader following the comment back to Sizing finds the math it claims to summarize.

### TASK-C — Fail fast when probed `num_ctx` undercuts configured `max_ctx`

prerequisites: []

Today `_probe_model_ctx` silently downcaps: `model_max_ctx = min(num_ctx, config.llm.max_ctx)`. If the loaded Ollama model exposes a smaller `num_ctx` than the user configured, bootstrap proceeds with the smaller value — which can fall under the Sizing floor and break the L2 invariant invisibly. Treat the configured `max_ctx` as a floor: if the model can satisfy it, use the configured value; otherwise raise.

This obviates the soft-warn / degradations / `_BUDGET_FLOOR` constant approach — the invariant can't be silently violated because bootstrap dies before `create_deps()` returns.

files:
- `co_cli/bootstrap/core.py` — replace the `min()` downcap in `_probe_model_ctx` with a fail-fast check:
  ```python
  num_ctx, capabilities = probe_ollama_model(config.llm.host, config.llm.model)
  if num_ctx is not None and num_ctx < config.llm.max_ctx:
      raise ValueError(
          f"Ollama model {config.llm.model!r} reports num_ctx={num_ctx:,} "
          f"but max_ctx={config.llm.max_ctx:,} is configured. "
          f"Raise the model's num_ctx (Modelfile) or lower max_ctx in settings."
      )
  if num_ctx is None:
      logger.warning(
          "ollama ctx probe failed; using configured max_ctx=%d as fallback",
          config.llm.max_ctx,
      )
  validate_ollama_num_ctx(config)
  return config.llm.max_ctx, capabilities
  ```
  Probe-failure path is unchanged (still falls back to configured value with a warning) — only the `num_ctx < max_ctx` case flips from silent downcap to hard error.

- `tests/test_flow_bootstrap_*` (extend an existing bootstrap test): assert `_probe_model_ctx` raises `ValueError` when probed `num_ctx < config.llm.max_ctx` and returns `config.llm.max_ctx` when `num_ctx >= config.llm.max_ctx`.

done_when:
- `_probe_model_ctx` no longer calls `min(num_ctx, config.llm.max_ctx)`.
- Bootstrap with a configured `max_ctx` higher than the model's `num_ctx` raises `ValueError` with a message naming both values.
- Bootstrap with `num_ctx >= max_ctx` returns `config.llm.max_ctx` unchanged.

success_signal: the configured `max_ctx` is a contract — either the model honors it and bootstrap proceeds, or bootstrap fails loudly. No silent downcap, no soft-warn, no degradations entry, no `_BUDGET_FLOOR` constant to maintain.

## Final — Team Lead

Plan delivery summary recorded above. Once TASK-A and TASK-B land, run `/review-impl tool-aggregate-budget-spill` for the full self-correcting review pass before Gate 2, then `/ship`.

## Implementation Review — 2026-05-06

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-A | OTEL `enforce_tool_call_limit` span coverage | ✓ pass | `tests/test_flow_tool_call_limit_otel.py:51,77,100` — saturation, within-cap, non-CallToolsNode skip; spans asserted from `co_cli/tools/lifecycle.py:79` |
| TASK-B | comment matches Sizing; no "32K" string | ✓ pass | `co_cli/agent/_tool_call_limit.py:5` — `# 6 non-spilling calls fit the 64K-floor tail; see Sizing`; `grep "32K"` returns no match |
| TASK-C | `_probe_model_ctx` no longer downcaps; raises when undercut | ✓ pass | `co_cli/bootstrap/core.py:243-244` calls `_check_ollama_num_ctx_floor`, returns `config.llm.max_ctx` unchanged on satisfied path; tests at `tests/test_flow_bootstrap_budget_span.py:38,47` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `monkeypatch.setattr(lifecycle_module, "_TRACER", ...)` violates `agent_docs/testing.md:17` no-mock rule | `tests/test_flow_tool_call_limit_otel.py:26` | blocking | Refactored `CoToolLifecycle` to expose `_tracer: Tracer = field(default_factory=...)` (production API fix). Test now injects via `CoToolLifecycle(_tracer=tracer)`. Removed module-level `_TRACER` from `co_cli/tools/lifecycle.py:28`. |
| `pytest.raises(ValueError)` too broad (PT011) | `tests/test_flow_bootstrap_budget_span.py:40` | blocking | Added `match="num_ctx=32,768"` |
| Import sort + format wrap (I001) | `tests/test_flow_tool_call_limit_otel.py:3`, `co_cli/agent/_tool_call_limit.py:5` | blocking | Auto-fixed by `ruff --fix`; shortened TASK-B comment to fit 100-char line |
| Doc inaccuracy: observability.md cited `tool_budget.turn_tool_calls` with stale attrs (`tool_call.count`, `tool_call.run_step`) | `docs/specs/observability.md:170` | blocking | Updated to actual span name `tool_budget.enforce_tool_call_limit` and emitted attrs (`budget.context_window_tokens`, `tool_calls.{limit,issued,allowed,rejected,limit_exceeded}`) |

### Tests
- Command: `uv run pytest`
- Result: 160 passed, 0 failed in 125.74s
- Log: `.pytest-logs/20260506-1726*-review-impl.log`
- Notable: `test_reasoning_model_settings_drive_real_call` initially failed at 30s timeout on first reasoning-mode call to `qwen3.5:35b-a3b`. Three reruns (3.69s, 4.79s, 4.22s) confirmed cold-reasoning-path warmup, not a regression — `ensure_ollama_warm` covers the noreason path only.

### Doc Sync
- Scope: narrow — fixed `docs/specs/observability.md` (single-line span-name + attrs correction)
- Result: fixed (see Issues table)

### Behavioral Verification
- `uv run co chat`: ✓ booted to v0.8.126, ollama qwen3.5:35b-a3b, hybrid+TEI knowledge, 25 tools, 1 skill, 1 MCP, "✓ Ready"
- `success_signal` checks:
  - TASK-A: enforce_tool_call_limit span fires with correct attrs — verified in 3 OTEL exporter tests
  - TASK-B: reader following the comment finds the 64K-floor Sizing math — verified
  - TASK-C: model that can't satisfy `max_ctx` fails bootstrap loudly — verified by `test_ollama_num_ctx_floor_raises_when_undercut`

### Scope notes (not blocking — informational for ship)
- Plan Status section (line 7) still says "Two follow-up items remain" — stale; should be updated before/at ship.
- TASK-B and TASK-C are not marked `✓ DONE` in the plan but are delivered in code.
- Working tree contains out-of-scope changes that will be staged together if `git add -A` is used: `co_cli/tools/tool_io.py` + `co_cli/tools/categories.py` (head+tail spill preview), `tests/test_flow_spill_*.py` (test pruning), `.claude/skills/test-hygiene/SKILL.md`, deleted `docs/reference/RESEARCH-context-management-comparison.md`. Recommend a separate commit or `/ship` only the plan's `files:` list.
- Untracked at repo root: `finch_2021_film_wikipedia.md`, `finch_2021_study.md` — off-topic content; move to `tmp/` per CLAUDE.md or delete.

### Overall: PASS
All three tasks pass with evidence. Four blocking findings auto-fixed (one production-API refactor, two lint, one doc inaccuracy). Full suite green. Ship-ready, but staged-files hygiene check required before `/ship` — confirm only plan-related files go in the commit.
