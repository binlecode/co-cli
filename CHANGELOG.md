# Changelog

## [Unreleased]

## [0.8.122]

### Refactor
- Renamed `_INFERENCE_DEFAULTS` → `_INFERENCE_MODEL_SETTINGS` — the table is canonical per-model knobs, not "defaults" of anything; bootstrap defaults are kept separately at the top of `llm.py`
- Added `DEFAULT_LLM_MODELS: dict[str, str]` for per-provider default model id (full id with variant tag); replaces the single hardcoded `DEFAULT_LLM_MODEL` constant
- Pydantic `model_validator` on `LlmSettings` auto-resolves empty `llm.model` to `DEFAULT_LLM_MODELS[provider]`; "no model configured" is no longer a reachable bootstrap failure mode
- Deduplicated scalar + extra_body extraction across `reasoning_model_settings()` / `noreason_model_settings()` via `_ollama_settings()` and `_gemini_settings()` translators
- `reasoning_model_settings()` is now provider-aware (closes a latent gap where Gemini-specific keys were silently ignored)
- Stale path comment in `bootstrap/check.py` (`config/_llm.py` → `config/llm.py`)

### Tests
- Added `test_flow_llm_settings.py` exercising `reasoning_model_settings()` end-to-end against real Ollama; closes the reasoning-path coverage gap (existing `test_flow_llm_call.py` only covers noreason)
- Added `LLM_REASONING_TIMEOUT_SECS = 30` constant for reasoning-mode tests

### Docs
- Synced `docs/specs/config.md` to the renamed table and new `DEFAULT_LLM_MODELS`
- Updated `docs/specs/bootstrap.md` failure-mode table — removed the now-unreachable "No model configured" entry; added unknown-model and noreason-only-model failure modes

## [0.8.119]

### Refactor
- Removed `context_window` from `_INFERENCE_DEFAULTS` — static fallbacks replaced by runtime probe
- Added `max_ctx` to `LlmSettings` as a safety ceiling on the Ollama probe result
- `effective_num_ctx()` now returns 0 when probe has not run (unknown) instead of a stale static default; caps probe result at `max_ctx`
- Removed `LlmModel.context_window` and `reasoning_context_window()` — compaction budget now sourced exclusively from `effective_num_ctx()`
- Simplified `resolve_compaction_budget` signature: no `context_window` param; uses `effective_num_ctx()` directly

## [0.8.117]

### Refactor
- Trimmed `_INFERENCE_DEFAULTS` for ollama qwen3.x: reasoning down to `max_tokens` + `context_window`; noreason down to `think=false` + `reasoning_effort=none` — all other params deferred to the served model
- `reasoning_model_settings()` and `noreason_model_settings()` now build `ModelSettings` conditionally, omitting absent keys rather than hard-coding them

## [0.8.115]

### Fixes
- Corrected ship skill version bump rule: bump to nearest even (feature) or odd (bugfix) patch number, not a fixed +1/+2 increment

## [0.8.114]

### Refactor
- Unified canon into the artifacts channel: `_search_canon_channel()` deleted; canon flows through `_search_artifacts()` as `kind='canon'` (source='canon' in MemoryStore)
- `ArtifactKindEnum.CANON` added; `sync_dir()` auto-sets `kind='canon'` when `source='canon'`
- Three-pass FTS5 structure: canon priority → user priority → waterfall (rule/article/note, dual-capped by count and chars)
- Four module constants: `_ARTIFACTS_CANON_CAP=3`, `_ARTIFACTS_USER_CAP=3`, `_ARTIFACTS_WATERFALL_CHUNK_CAP=5`, `_ARTIFACTS_WATERFALL_SIZE_CAP=2000`
- `character_recall_limit` config field deprecated (kept for one version; not consumed by recall)

## [0.8.113]

### Fixes
- Lowered `compaction_ratio` default from 0.65 → 0.50: trigger now fires at ~16k tokens (32k ctx) instead of ~21k, giving the LLM ~5k more headroom before context pressure degrades output quality
- Headroom per pass: ~24% (was ~36%); tail budget unchanged at 20% × budget; shape invariant `tail_fraction < compaction_ratio` still satisfied (0.20 < 0.50)
- Removed redundant `compaction_ratio = 0.5` eval override in `eval_compaction_multi_cycle.py` (now matches production default)

## [0.8.111]

### Fixes
- Removed dead `evict_batch_tool_outputs` history processor (200k threshold never fired; redundant with at-write spill in `tool_output()`)
- Removed `batch_spill_chars` config field and `last_overbudget_batch_signature` runtime state
- Removed `asyncio.timeout(90)` from `_PerCallTimeoutCapability` — per-call timeout fired mid-stream causing `httpx.ReadError` crash; outer 360s segment hang timeout is the correct guard
- Added `httpx.ReadError` to `run_turn` error handlers (pydantic-ai streaming path does not wrap this as `ModelAPIError`)
- Added per-LLM-call timing to `_PerCallTimeoutCapability` — DEBUG log every call, WARNING when ≥81s
- Fixed `AgentRunResult.data` → `.output` in eval judge (pydantic-ai API rename)
- `eval_compaction_multi_cycle`: replaced broken LLM judge gate with deterministic keyword chain check; added `outcome="error"` turn detection; set `compaction_ratio=0.5` to trigger phase-2 earlier on local models; added summary content previews

### Refactor
- Centralize eval model construction — no local `build_model()` calls in eval files

## [0.8.107]

### Features
- Canon recall merged into unified FTS pipeline (`source='canon'`): `MemoryStore.sync_dir(no_chunk=True)`, `get_chunk_content()`, `_sync_canon_store()` at bootstrap, `_search_canon_channel()` rewritten to BM25 + full-body fetch
- `canon_recall.py` deleted — bespoke token-overlap recall path removed
- `eval_canon_recall.py` updated with FTS-appropriate sub-cases (`canon-fts-match`, `canon-top-hit-relevant`)
