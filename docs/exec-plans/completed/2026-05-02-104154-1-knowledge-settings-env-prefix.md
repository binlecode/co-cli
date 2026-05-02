# Plan: Migrate KnowledgeSettings to pydantic_settings env_prefix

**Task type:** config-refactor

**Sequence:** spin-out #1 of 3 (smallest, mechanical — independent of the other two
and of the recall-path cleanup; pickable any time).

**Status:** stub — Gate 1 not yet started. Drafted as a spin-out from
`docs/exec-plans/active/2026-05-02-090658-knowledge-recall-path-cleanup.md` (originally
TASK-25 / O7). Expand via `/orchestrate-plan knowledge-settings-env-prefix` when ready
to execute.

## Context

`KnowledgeSettings` (`co_cli/config/knowledge.py`) is a `pydantic.BaseModel`. Env-var
overrides are wired through a parallel registry called `KNOWLEDGE_ENV_MAP` — every new
field requires both a `Field(...)` declaration and a manual map entry, and the map is
consumed by `co_cli/config/core.py:155`.

This refactor unifies the two: `KnowledgeSettings` becomes a `pydantic_settings.BaseSettings`
with `env_prefix="CO_KNOWLEDGE_"`, and the parallel registry is deleted.

Bundling this into the recall-path cleanup was rejected at Gate 1 because it is unrelated
to the recall path and entangles a config-layer refactor diff with recall-path code.

## Problem & Outcome

**Problem:** Adding a new `KnowledgeSettings` field requires two edits in two files
(`Field(...)` + `KNOWLEDGE_ENV_MAP[...]`). The two can drift, and the registry hides the
env-var contract from anyone reading the dataclass.

**Outcome:** Adding a new field automatically gets env-var override via the prefix —
`CO_KNOWLEDGE_<FIELD_NAME_UPPER>`. The parallel registry is deleted; the dataclass is
the single source of truth for both shape and env mapping.

## Scope

**In scope:**
- `co_cli/config/knowledge.py` — migrate `KnowledgeSettings` from `BaseModel` to
  `BaseSettings`. Add `model_config = SettingsConfigDict(extra="forbid",
  env_prefix="CO_KNOWLEDGE_", env_nested_delimiter="__")`. Delete `KNOWLEDGE_ENV_MAP`.
- `co_cli/config/core.py:155` — remove the `"knowledge": KNOWLEDGE_ENV_MAP` entry; remove
  the `from co_cli.config.knowledge import KNOWLEDGE_ENV_MAP` import.
- Field-specific exceptions where the legacy env var doesn't follow the prefix pattern
  (e.g. `CO_CHARACTER_RECALL_LIMIT` rather than `CO_KNOWLEDGE_CHARACTER_RECALL_LIMIT`)
  — preserved via `Field(..., alias="CO_CHARACTER_RECALL_LIMIT")` per field.
- `tests/` — add a test that confirms `CO_KNOWLEDGE_<FIELD>=value` env override produces
  `config.knowledge.<field> == value` for at least one field. Add a regression test for
  any aliased field.

**Out of scope:**
- Migrating other `*Settings` classes — this plan is `KnowledgeSettings` only. If the
  pattern proves out, follow-up plans can convert the remaining settings classes.
- Removing legacy env vars — aliases preserve them indefinitely.

## Behavioral Constraints

- All currently-supported env vars must continue to work — no env-var contract break.
  Verified by enumerating the keys in today's `KNOWLEDGE_ENV_MAP` and asserting each one
  still produces the expected setting after migration.
- Field aliases must NOT alter the JSON-config contract — `KnowledgeSettings(**dict)`
  with the dict-key matching the field name must still work for in-process construction.
- `extra="forbid"` must remain — typos in `settings.json` should error, not silently
  ignore.

## High-Level Design

1. Audit current `KNOWLEDGE_ENV_MAP` — list every key and target env var.
2. Identify env vars that don't match `CO_KNOWLEDGE_<FIELD_UPPER>` — those need
   per-field aliases.
3. Migrate to `BaseSettings` + `SettingsConfigDict`. Delete the map.
4. Remove the `core.py` import and entry.
5. Test: env-override path for one new field (no alias), one aliased field, one JSON
   config field.

## Implementation Plan

✓ DONE — TASK-1: Migrate `KnowledgeSettings` to `BaseSettings` with `env_prefix="CO_KNOWLEDGE_"`,
`settings_customise_sources` (env > JSON priority), `AliasChoices` on `character_recall_limit`,
delete `KNOWLEDGE_ENV_MAP`.

✓ DONE — TASK-2: Remove `KNOWLEDGE_ENV_MAP` import and `"knowledge"` entry from `core.py`.

✓ DONE — TASK-3: Add 4 tests: plain-prefix env override, env > JSON config priority,
legacy `CO_CHARACTER_RECALL_LIMIT` alias, JSON config without env var.

## Delivery Summary

Delivered via `/deliver`. 112/112 tests pass. `pydantic_settings==2.12.0` (transitive dep of
pydantic-ai) confirmed available. Key non-obvious finding: `BaseSettings` nested inside a
`BaseModel` DOES trigger `__init__` env loading, but `init_kwargs` beat env by default —
`settings_customise_sources` is required to preserve the existing `env > JSON config` contract.
`CO_CHARACTER_RECALL_LIMIT` legacy alias preserved via `AliasChoices`. v0.8.90.
