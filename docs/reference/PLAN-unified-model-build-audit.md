# Plan Audit Log: Unified Model Build — ModelRegistry
_Slug: unified-model-build | Date: 2026-03-09_

---
# Audit Log

## Cycle C1 — Team Lead
Submitting for Core Dev review.

## Cycle C1 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** The plan correctly identifies the actual problem (signature leakage from a two-value tuple return) and proposes the minimum abstraction needed to fix it. The `ModelRegistry` pattern mirrors existing registries in the codebase, keeps `ResolvedModel` as a plain dataclass, and avoids proxy/wrapper layers — this is first-principles engineering. No scope creep detected.

**Major issues:**
- none

**Minor issues:**
- **PO-m-1** [TASK-3 — delegation guard condition]: The fallback comparison `rm is fallback` to detect an unconfigured role is subtle and relies on object identity, which is a non-obvious contract for future maintainers. The plan acknowledges a secondary `ctx.deps.config.role_models` check for the `None`-registry case, adding a second code path doing the same guard. Recommendation: add a `ModelRegistry.is_configured(role) -> bool` method so the guard reads clearly at delegation sites and the dual-path complexity disappears.
- **PO-m-2** [TASK-2 — inline import in `main.py`]: The spec places the `ModelRegistry` import inside `create_deps()` as a lazy import, but `_factory.py` is already a core module with no circular-import risk from `main.py`. A top-level import is cleaner and consistent with how `main.py` imports other factory/config symbols. Recommendation: move to a top-level import unless there is a documented circular-import reason to keep it lazy.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2, CD-M-3
**Summary:** The registry design is sound and the DAG is acyclic. However three blocking issues need resolution before implementation: `_cmd_compact` calls `_resolve_summarization_model` with a wrong call signature that the plan's `done_when` greps will not catch; `test_delegate_coder.py` tests that call `make_coder_agent` and `make_research_agent`/`make_analysis_agent` with the old signature will break but the plan's `done_when` only covers `coder.py` — research and analysis factories are missed; and the `model_registry` field type in `deps.py` must be `Any | None` but the plan's grep criterion checks for `ModelRegistry | None`, which will fail unless the implementation deviates from the `Any` pattern specified two lines earlier in the same task.

**Major issues:**

- **CD-M-1** [TASK-4 — `_cmd_compact` call-site discrepancy]: The live `_commands.py` code at line 258–262 calls `_resolve_summarization_model(ctx.deps.config, fallback=ctx.agent.model)` and immediately passes the return value as the `model` positional argument to `_run_summarization_with_policy`. This means `_cmd_compact` is already consuming the two-value tuple incorrectly — `model` receives a `tuple[Any, Any]` instead of `Any`. The TASK-4 spec describes the correct fix (`rm = ctx.deps.services.model_registry.get("summarization", fallback)`), but the `done_when` grep (`grep -n "model_registry" co_cli/_commands.py`) will pass even if `_cmd_compact`'s unpacking is still wrong, because it only checks for the string's presence. Similarly, `_cmd_new` at line 419 has the same pattern. Recommendation: add explicit `done_when` criteria verifying that `_resolve_summarization_model` no longer appears in `_commands.py` (`grep -n "_resolve_summarization_model" co_cli/_commands.py` returns empty) and that `_run_summarization_with_policy` is called with a `ResolvedModel` argument (not a raw tuple) at both call sites.

- **CD-M-2** [TASK-3 — `done_when` covers only `coder.py`; research and analysis factories unchecked]: The `done_when` criterion `grep -n "ResolvedModel" co_cli/agents/coder.py` verifies the coder factory is updated. But `co_cli/agents/research.py` and `co_cli/agents/analysis.py` also return `(agent, model_settings)` tuples today (same WIP pattern), and `test_delegate_coder.py` lines 128–129 and 165–166 call `make_research_agent` and `make_analysis_agent` with the old `(ModelEntry, provider, host)` signature. These tests will fail after TASK-3 but there is no `done_when` criterion for the research/analysis factories, and the plan's single pytest gate (`uv run pytest tests/test_delegate_coder.py -v`) will catch the runtime failures but not signal which factory is at fault. Recommendation: add `grep -n "ResolvedModel" co_cli/agents/research.py` and `grep -n "ResolvedModel" co_cli/agents/analysis.py` to `done_when`, and note that the three `test_make_*_agent_*` tests in `test_delegate_coder.py` must be updated to pass `ResolvedModel` instead of `(ModelEntry, provider, host)`.

- **CD-M-3** [TASK-2 — `done_when` type annotation mismatch]: The spec body says to annotate `model_registry` as `Any | None` (consistent with `knowledge_index` and `task_runner`), but the `done_when` grep checks for `model_registry: ModelRegistry | None`. If the implementation faithfully follows the `Any` pattern, the grep will return empty and the criterion will appear to fail. If the implementation writes `ModelRegistry | None` to satisfy the grep, it introduces a direct import of `ModelRegistry` into `deps.py`, breaking the circular-import avoidance rationale stated immediately above. Recommendation: align the `done_when` grep with the stated `Any` type: change the criterion to `grep -n "model_registry" co_cli/deps.py` (presence only), and add a separate check that no `ModelRegistry` symbol is imported at module scope in `deps.py`.

**Minor issues:**

- **CD-m-1** [TASK-4 — `_history.py` retains `from co_cli.config import settings` import after refactor]: Both `truncate_history_window` (line 409) and `precompute_compaction` (line 494) currently do `from co_cli.config import settings as _settings` solely to read `_settings.model_http_retries` and pass it to `_run_summarization_with_policy`. After TASK-4 removes `_resolve_summarization_model`, these two lazy settings imports remain. The plan does not mention removing or replacing them, which will leave `_history.py` with a residual direct `settings` import — a violation of the "no direct settings import in non-main modules" principle. Recommendation: add a `done_when` criterion that `grep -n "from co_cli.config import settings" co_cli/_history.py` returns empty, and note that `model_http_retries` must be threaded via `ctx.deps` or a new `CoConfig` field to remove the import.

- **CD-m-2** [TASK-6 — `test_model_registry_builds_from_config` requires a live Ollama model name]: The spec instructs the test to use "a real model name from the Ollama defaults in `config.py`". If the test environment does not have that model pulled, `build_model()` (Ollama path) creates an `OpenAIChatModel` with `httpx.AsyncClient` — construction succeeds without network, so this is safe. However the spec does not explicitly state this assumption, and a future reader might add a `.run()` call and break the test. Recommendation: add a one-line comment in the spec ("construction only — no network call; `OpenAIChatModel` connects lazily") so the test author does not add an accidental live call.

- **CD-m-3** [TASK-5 — `TYPE_CHECKING` import target is already present]: The plan says "add `CoServices` to the `if TYPE_CHECKING:` block in `_signal_analyzer.py` (already has `CoConfig`)". Reading the file, the `if TYPE_CHECKING:` block imports only `CoConfig`. The instruction is correct, but the phrase "already has `CoConfig`" is slightly confusing since `CoConfig` is being *removed* from the runtime signature in this task (replaced by `CoServices`). Recommendation: clarify that `CoConfig` is removed from the `TYPE_CHECKING` block and replaced by `CoServices`, not that both remain.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | Added `done_when` criteria: `_resolve_summarization_model` absent from `_commands.py`; `_run_summarization_with_policy` called with `ResolvedModel` at both call sites |
| CD-M-2   | adopt    | Extended `done_when` to grep `research.py` and `analysis.py`; noted `test_delegate_coder.py` `test_make_*_agent_*` tests must be updated |
| CD-M-3   | adopt    | Changed `done_when` grep to presence-only (`grep -n "model_registry" co_cli/deps.py`); added negative check that `ModelRegistry` is not imported at module scope |
| PO-m-1   | adopt    | Added `is_configured(role) -> bool` to `ModelRegistry` spec in TASK-1; updated TASK-3 to use it instead of `rm is fallback` object-identity guard |
| PO-m-2   | adopt    | Changed TASK-2 spec to use top-level import of `ModelRegistry` in `main.py` |
| CD-m-1   | modify   | Scoped fix into existing tasks: add `model_http_retries: int = 2` to `CoConfig` in TASK-2 (deps.py already in scope); update TASK-4 to read from `ctx.deps.config.model_http_retries`; added `done_when` criterion |
| CD-m-2   | adopt    | Added construction-only note to TASK-6 spec |
| CD-m-3   | adopt    | Clarified TASK-5 spec: `CoConfig` removed from `TYPE_CHECKING` block and replaced by `CoServices` |
