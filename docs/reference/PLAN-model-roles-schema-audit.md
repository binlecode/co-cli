# Plan Audit Log: Model Roles Schema Refactor
_Slug: model-roles-schema | Date: 2026-03-07_

## Cycle C1 — Team Lead
Submitting for Core Dev and PO parallel review.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2, CD-M-3
**Summary:** Three unlisted files (`agents/coder.py`, `agents/research.py`, `agents/analysis.py`) call `make_subagent_model(model_name: str, ...)` directly and will break when TASK-3 changes the signature. The `api_params` fix never reaches the sliding-window summarization hot path because `CoConfig.summarization_model` stays a plain `str`. The `fill_from_env` mode='before' validator writes `data["model_roles"]` (old key) and must also be updated in TASK-1 or backward-compat remapping won't fire.

**Major issues:**
- **CD-M-1** [TASK-3 / TASK-5]: `agents/coder.py`, `agents/research.py`, `agents/analysis.py` each call `make_subagent_model(model_name, ...)` with a plain `str`. None appear in any task file list. Changing the first param to `entry: ModelEntry` breaks all three. Recommendation: Add these three files to TASK-5, update each `make_*_agent` to accept `entry: ModelEntry`, pass `entry.model` internally, and pass derived `model_settings` to `agent.run()`.
- **CD-M-2** [TASK-2 / TASK-4]: The `api_params` / `think=False` fix never reaches the sliding-window processor. `_history.py` lines 405 and 490 use `ctx.deps.config.summarization_model: str` — a bare string. TASK-4 adds `model_settings` to the public functions, but without a route from `CoConfig` to the processor, the primary hot path is excluded. Recommendation: Add `CoConfig.summarization_run_settings: dict | None = None` — pre-computed from `make_model_run_settings(entry, provider)` at `create_deps()` time. The processor and command callers both read this from CoConfig; no new imports in `_history.py`.
- **CD-M-3** [TASK-1 — `fill_from_env`]: `fill_from_env` (mode='before') writes `data["model_roles"]` (old key). After rename, `@field_validator("role_models")` fires only if `"role_models"` is present in data. If `fill_from_env` still writes the old key, the validator never fires. Recommendation: Update `fill_from_env` in TASK-1 to write `data["role_models"]`, apply old-key → new-key remapping at the top of `fill_from_env`, not in the field_validator.

**Minor issues:**
- **CD-m-1** [TASK-8]: `test_create_deps_derives_summarization_model_from_role` and `test_status_fast_fails_without_reasoning_models` mutate `settings.model_roles` directly. Not named in TASK-8 done_when. Recommendation: add both to the verification list.
- **CD-m-2** [TASK-6 — failover]: done_when only checks grep for absence of old name; does not verify `.model` extraction was added. Recommendation: add functional assertion verifying `_swap_model_inplace` receives a `str`, not `ModelEntry`.
- **CD-m-3** [TASK-1 — `Settings.save()` rollback]: plan covers forward-compat but not rollback (older binary reading new settings.json). Recommendation: note briefly in scope.
- **CD-m-4** [TASK-1]: `VALID_MODEL_ROLES` → `VALID_ROLE_NAMES` marked "optional". Recommendation: make mandatory and add to done_when grep check.

## Cycle C1 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** Plan solves two real structural problems with minimum scope. `ModelEntry` is the smallest typed wrapper that addresses both issues. No blocking concerns.

**Major issues:**
(none)

**Minor issues:**
- **PO-m-1** [TASK-1 / Context]: The interaction between Modelfile-baked `/no_think` and the new `api_params={"think": False}` layer (double-application = redundant but harmless) is not documented. Recommendation: add one-line note to Scope section.
- **PO-m-2** [TASK-3 done_when]: Missing assertion for Gemini provider + `api_params={"think": False}` → `None`. Recommendation: add one-line assertion.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | Add agents/coder.py, research.py, analysis.py to TASK-5; update make_*_agent signatures |
| CD-M-2   | adopt/modify | Add `CoConfig.summarization_run_settings: dict \| None = None`; pre-computed at create_deps; no new imports in _history.py |
| CD-M-3   | adopt    | Backward-compat key remapping moved to top of fill_from_env (mode='before'); field_validator handles type coercion only |
| CD-m-1   | adopt    | Name both tests explicitly in TASK-8 done_when |
| CD-m-2   | adopt    | Add functional assertion: failover path passes str (not ModelEntry) to _swap_model_inplace |
| CD-m-3   | reject   | Rollback is a deployment concern; no forward-compat customization in model_dump planned — note in scope is sufficient |
| CD-m-4   | adopt    | VALID_MODEL_ROLES → VALID_ROLE_NAMES made mandatory; added to TASK-1 done_when |
| PO-m-1   | adopt    | Add one-line note to Scope: double-application is harmless |
| PO-m-2   | adopt    | Add gemini assertion to TASK-3 done_when |
