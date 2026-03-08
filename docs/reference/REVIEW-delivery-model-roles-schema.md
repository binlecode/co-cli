# Delivery Audit: model-roles-schema

**Scope:** model-roles-schema refactor — `ModelEntry`, `role_models`, `VALID_ROLE_NAMES`, `resolve_role_model`, `_resolve_summarization_model`

**Auditor:** delivery-audit skill
**Date:** 2026-03-08

---

## Phase 1 — Scope

Relevant modules:
- `co_cli/config.py` — `ModelEntry`, `role_models`, `VALID_ROLE_NAMES`, `_parse_role_models`, `fill_from_env`
- `co_cli/deps.py` — `CoConfig.role_models`
- `co_cli/agents/_factory.py` — `make_subagent_model`, `resolve_role_model`
- `co_cli/_history.py` — `_resolve_summarization_model`
- `co_cli/_model_check.py` — `PreflightResult.role_models`

Primary DESIGN doc: `docs/DESIGN-llm-models.md`
Secondary DESIGN docs checked: `docs/DESIGN-core.md`, `docs/DESIGN-index.md`, `docs/DESIGN-flow-bootstrap.md`

---

## Phase 2 — Feature Inventory

### New symbols confirmed in source

| Symbol | Location | Status |
|--------|----------|--------|
| `ModelEntry` | `co_cli/config.py:108` | `BaseModel` with `model: str`, `api_params: dict[str, Any] = {}` |
| `VALID_ROLE_NAMES` | `co_cli/config.py:115` | `frozenset` — `{"reasoning", "summarization", "coding", "research", "analysis"}` |
| `role_models` | `co_cli/config.py:288` | `dict[str, list[ModelEntry]]` field on `Settings` |
| `_parse_role_models` | `co_cli/config.py:219` | validator — coerces plain strings and dicts to `ModelEntry` |
| `fill_from_env` role logic | `co_cli/config.py:366–391` | 5 per-role env vars (`CO_MODEL_ROLE_REASONING` etc.); Ollama defaults include `api_params={"think": False}` for summarization, analysis, research |
| `make_subagent_model` | `co_cli/agents/_factory.py:14` | accepts `ModelEntry`; `api_params` baked into `OpenAIChatModel.settings` as `{"extra_body": api_params}` |
| `resolve_role_model` | `co_cli/agents/_factory.py:39` | returns head model for a role or fallback |
| `_resolve_summarization_model` | `co_cli/_history.py:197` | thin wrapper over `resolve_role_model` for the summarization role |

### Deleted symbols confirmed absent from source

| Symbol | Expected to be gone | Confirmed absent |
|--------|--------------------|--------------------|
| `model_roles` (Settings field) | yes | yes — `role_models` only |
| `VALID_MODEL_ROLES` | yes | yes — `VALID_ROLE_NAMES` only |
| `get_role_head()` | yes | yes — no matches in `co_cli/` |
| `CoConfig.summarization_model` | yes | yes — field absent from `deps.py` |
| `make_model_run_settings()` | yes | yes — function not in `_factory.py` |
| `qwen3.5` string-inspection hack in `_history.py` | yes | yes — hack removed |

**Note:** `make_model_run_settings()` was proposed in the TODO/plan but the actual implementation took a different (simpler) approach: `api_params` are baked directly into `OpenAIChatModel.settings` at `make_subagent_model()` construction time via `{"extra_body": api_params}`. This is functionally equivalent and correct.

### `api_params` baking mechanism (actual vs documented)

Actual implementation in `_factory.py:28`:
```python
model_settings = {"extra_body": model_entry.api_params} if model_entry.api_params else None
return OpenAIChatModel(model_name, provider=..., settings=model_settings)
```

This bakes `api_params` into the model object at construction — not into a separate `model_settings` argument to `agent.run()`. The `make_model_run_settings()` helper described in the TODO was not implemented; `make_subagent_model` handles both concerns.

---

## Phase 3 — Coverage Check

### `docs/DESIGN-llm-models.md` — Primary doc

| Symbol | Coverage | Assessment |
|--------|----------|------------|
| `ModelEntry` — what it is | Section 2, line 29: "Each entry is a `ModelEntry(model, api_params)`" | Covered — fields named |
| `ModelEntry` — string coercion | Section 2, line 29: "plain model name strings are coerced to `ModelEntry` by `_parse_role_models`" | Covered |
| `role_models` — field purpose | Section 2, lines 24–29: role chain explained | Covered |
| `role_models` env vars | Section 3 Config table, lines 86–90: all 5 env vars listed | Covered |
| `role_models` valid role names | Section 2: "Mandatory: reasoning ... Optional: summarization, coding, research, analysis" | Covered by enumeration |
| `VALID_ROLE_NAMES` — symbol | Section 5 Files table, line 122: listed as part of `config.py` | Named but not explained |
| `resolve_role_model` | Section 2, line 56: "_resolve_summarization_model(config, fallback) helper in _history.py" — ONLY summarization helper mentioned | **MISSING** — `resolve_role_model` in `_factory.py` not documented |
| `api_params` — baking mechanism | Section 2, line 29 mentions `api_params` exists; no description of HOW it is applied | **INCOMPLETE** — baking into `OpenAIChatModel.settings` via `extra_body` not documented |
| `_resolve_summarization_model` | Section 2, line 56 + Section 5 Files table, line 126 | Covered |

**BLOCKING: `DESIGN-llm-models.md` Section 4 is stale.**

Section 4 "Provider Quirks — qwen3.5 Summarization — Mandatory `think=False`" (lines 94–106) states that `summarize_messages` in `_history.py` uses `isinstance(model, OpenAIChatModel)` + string inspection on `model_name.lower()` to detect qwen3.5 models and passes `extra_body={"think": False}`. This hack was **explicitly removed** as part of this refactor — `api_params={"think": False}` in the `ModelEntry` now handles this at config time, not at call time in `_history.py`. The described `isinstance` / `model_name.lower()` check no longer exists in `_history.py`. This section now describes removed behavior and will mislead anyone debugging or extending the summarization model.

### `docs/DESIGN-core.md` — CoDeps table

| Coverage point | Assessment |
|----------------|------------|
| `role_models` listed in `CoConfig` key fields (line 215) | Covered — `role_models (dict of role → list[ModelEntry])` |
| `make_subagent_model` listed in `agents/_factory.py` module entry | Covered in modules table |

### `docs/DESIGN-index.md` — Config reference and module index

| Coverage point | Assessment |
|----------------|------------|
| `role_models` Config Reference row (line 140) | Covered — lists 4 env vars but **omits `CO_MODEL_ROLE_SUMMARIZATION`** from the main table row (it appears separately on line 151 as its own row) |
| `CO_MODEL_ROLE_SUMMARIZATION` | Present on line 151 as standalone row | Covered |
| `agents/_factory.py` module entry (line 244) | Stale: says `make_subagent_model(model_name, provider, ollama_host)` — signature shows `model_name: str` but actual signature is `model_entry: ModelEntry` | **MINOR** — signature description outdated |
| `VALID_ROLE_NAMES` | Not mentioned in config reference | Minor gap — not a config setting, but validation symbol worth noting in the files table |

### `docs/DESIGN-flow-bootstrap.md`

| Coverage point | Assessment |
|----------------|------------|
| `PreflightResult.role_models` type | Line 76: `role_models: dict[str, list[ModelEntry]] | None` | Covered and correct |
| State mutations table (line 341) | `deps.config.role_models` — correct name | Covered |
| Full Startup Sequence (line 139) | `deps.config.role_models["reasoning"][0].model` — correct `.model` extraction | Covered and correct |

### Stale `model_roles` references (old name)

All occurrences are in non-DESIGN docs (REVIEW-*, DELIVERY-*, TODO-*):

| File | Reference | Action needed |
|------|-----------|---------------|
| `docs/REVIEW-startup-flow-consolidation.md` lines 31, 35, 36, 47 | `model_roles` used to describe what the DESIGN doc said at audit time | No action — REVIEW files are historical snapshots |
| `docs/reference/REVIEW-delivery-startup-flow-consolidation.md` line 131 | Same — historical REVIEW | No action |
| `docs/REVIEW-agentic-design-patterns.md` line 165 | `model_roles` in a comparison context | Minor — could be updated but not a DESIGN doc |
| `docs/DELIVERY-approval-simplify.md` lines 20, 23 | `model_roles` in delivery notes | No action — DELIVERY files are scaffolding |
| `docs/DELIVERY-codeps-refactor.md` lines 29, 45, 49, 74 | `model_roles` in delivery notes | No action — DELIVERY files are scaffolding |
| `docs/TODO-model-roles-schema.md` | Extensively references `model_roles` — this is the TODO that drove the refactor | No action — TODO is the pre-refactor spec, will be cleaned up post-delivery |

No `model_roles` references found in any DESIGN-*.md docs. The sync-doc pass was effective.

---

## Phase 4 — Second Pass

### Confirmed blocking issues

**B-1 (BLOCKING): `DESIGN-llm-models.md` Section 4 describes a removed code path.**

Section 4 "qwen3.5 Summarization — Mandatory `think=False`" (lines 94–106) describes `isinstance` + string inspection logic in `summarize_messages` / `_history.py`. This code was removed in this refactor — `api_params={"think": False}` in `ModelEntry` now handles this at config time. Anyone reading Section 4 will believe the hack is still present, and may be confused when debugging summarization behavior (the logic they are looking for does not exist in `_history.py`).

**Fix required:** Replace Section 4 content to describe the new mechanism: `api_params={"think": False}` is set in the Ollama defaults for summarization/analysis/research entries in `config.py`; `make_subagent_model` passes `api_params` as `{"extra_body": api_params}` in `OpenAIChatModel.settings`; no runtime detection or string inspection occurs.

### Confirmed minor issues

**m-1 (MINOR): `DESIGN-llm-models.md` does not document `resolve_role_model`.**

The function exists in `_factory.py` (line 39), is called by `_resolve_summarization_model` in `_history.py`, and is the canonical pattern for all role-model resolution. It is not mentioned in Section 2 (Sub-agent Construction) or Section 5 (Files). `_resolve_summarization_model` is mentioned but its delegation to `resolve_role_model` is not.

**Fix required (minor):** Add `resolve_role_model` to Section 2 pseudocode and the Files table entry for `_factory.py`.

**m-2 (MINOR): `DESIGN-llm-models.md` does not document the `api_params` baking mechanism.**

Section 2 mentions that `ModelEntry(model, api_params)` exists and coercion happens. It does not explain that `api_params` is baked into `OpenAIChatModel.settings` as `{"extra_body": api_params}` at construction time. This is the key behavioral guarantee that makes `api_params={"think": False}` work.

**Fix required (minor):** Add one sentence to the Sub-agent Construction subsection describing how `api_params` flows into `OpenAIChatModel.settings`.

**m-3 (MINOR): `DESIGN-index.md` modules table entry for `agents/_factory.py` has stale signature.**

Line 244 describes `make_subagent_model(model_name, provider, ollama_host)` — the old signature. The new signature is `make_subagent_model(entry: ModelEntry, provider, ollama_host)`.

**Fix required (minor):** Update the modules table entry to reflect `make_subagent_model` accepts `ModelEntry`, and add `resolve_role_model` to the description.

**m-4 (MINOR): `VALID_ROLE_NAMES` not explained in docs.**

`VALID_ROLE_NAMES` is listed in the `config.py` Files table row (`DESIGN-llm-models.md` line 122) but not explained. Its role (validation guard on `role_models` keys at settings parse time) is not documented anywhere in the DESIGN docs.

**Fix required (minor, low priority):** One line in Section 2 describing `VALID_ROLE_NAMES` as the validation set for `role_models` keys.

---

## Phase 5 — Verdict

**VERDICT: NEEDS_ATTENTION**

| Issue | Severity | Description |
|-------|----------|-------------|
| B-1 | BLOCKING | `DESIGN-llm-models.md` Section 4 describes a removed code path (qwen3.5 string-inspection hack); misleads developers debugging summarization behavior |
| m-1 | MINOR | `resolve_role_model` not documented in `DESIGN-llm-models.md` |
| m-2 | MINOR | `api_params` baking into `OpenAIChatModel.settings` not documented |
| m-3 | MINOR | `DESIGN-index.md` modules table has stale `_factory.py` signature (`model_name: str` should be `entry: ModelEntry`) |
| m-4 | MINOR | `VALID_ROLE_NAMES` purpose not explained (only listed in Files table) |

**Blocking count: 1 | Minor count: 4**

The code refactor is complete and correct. All deleted symbols are gone, all new symbols are present, and the core coverage in DESIGN-llm-models.md §2–3 and DESIGN-flow-bootstrap.md is accurate. The single blocking issue is a stale documentation section that actively contradicts the shipped code, creating a real debugging hazard.

### Required actions before sign-off

1. **[BLOCKING]** Rewrite `DESIGN-llm-models.md` Section 4 "qwen3.5 Summarization" to describe the new `api_params`-driven mechanism. Remove the description of `isinstance` + string inspection (that code is gone).

### Recommended follow-on (non-blocking)

2. Add `resolve_role_model` to `DESIGN-llm-models.md` §2 and §5 Files table.
3. Add one sentence about `api_params` → `extra_body` baking in `DESIGN-llm-models.md` §2.
4. Update `DESIGN-index.md` modules table `_factory.py` entry signature.
5. Add `VALID_ROLE_NAMES` validation role to `DESIGN-llm-models.md` §2.
