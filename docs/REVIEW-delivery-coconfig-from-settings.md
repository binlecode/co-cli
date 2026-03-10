# REVIEW: delivery/coconfig-from-settings — Delivery Audit
_Date: 2026-03-10_

## What Was Scanned

- `co_cli/config.py` — all `DEFAULT_*` constants, `_deep_merge_settings()`, `Settings.fill_from_env` role_models default fill logic
- `co_cli/deps.py` — `CoConfig.from_settings()` classmethod, `CoConfig` dataclass field defaults
- `docs/DESIGN-core.md` — §9 Configuration section
- `docs/DESIGN-system.md` — §4.7 Security, Configuration, And Concurrency; §5 Config table
- `docs/DESIGN-flow-bootstrap.md` — §Settings Loading, `create_deps()` pseudocode, `role_models` defaults block
- `docs/DESIGN-system-bootstrap.md` — §1 Settings Loading And Deps Initialization; `create_deps()` pseudocode
- `docs/DESIGN-mcp-client.md` — deep-merge behavior note

## Delivery Audit

| Feature | Location | Coverage | Severity | Gap |
|---------|----------|----------|----------|-----|
| `CoConfig.from_settings()` classmethod | `co_cli/deps.py:117` | **Partial** | Medium | `DESIGN-flow-bootstrap.md` cites it once in a pseudocode comment (`CoConfig.from_settings(settings)`) as the canonical factory. `DESIGN-system-bootstrap.md` does not name the method — its pseudocode shows `config = CoConfig(..., ...)` without revealing the `from_settings` pattern. `DESIGN-core.md` and `DESIGN-system.md` document `CoConfig` fields but never name the classmethod or its responsibility (what it copies vs what it leaves at defaults). No doc states the design contract: computed/session fields are excluded and must be applied via `dataclasses.replace()`. |
| `DEFAULT_*` named constants in `config.py` | `co_cli/config.py:9–182` | **No gap** | — | These are module-level implementation constants shared by `Settings` field defaults and `CoConfig` defaults. They are not config settings the user sets — they are the single source of truth for default values. Their existence as named constants is an internal code-quality concern, not a user-visible design decision requiring doc coverage. `DESIGN-flow-bootstrap.md` does reference `DEFAULT_OLLAMA_*` by category. No doc gap. |
| `_deep_merge_settings()` behavior | `co_cli/config.py:53–66` | **Adequate** | — | Documented in `DESIGN-core.md` §9 (cites deep merge by name), `DESIGN-flow-bootstrap.md` §Settings Loading (labels Layer 2 explicitly as `deep merge via _deep_merge_settings`), and `DESIGN-mcp-client.md` §1 (explains key-by-key dict merge semantics for `mcp_servers`). The semantics — scalars and lists replaced wholesale, dicts merged key-by-key recursively — are fully covered in `DESIGN-mcp-client.md`. No gap. |
| `role_models` default fill (all roles get provider defaults when absent) | `co_cli/config.py:441–454` | **Adequate** | — | `DESIGN-flow-bootstrap.md` documents the merge order explicitly (provider defaults → explicit config → env var overrides) and names both providers (ollama: all 5 roles, gemini: reasoning only). `DESIGN-system-bootstrap.md` §1 and `DESIGN-llm-models.md` settings table both cover default fill per role. No gap. |
| `CoConfig.from_settings()` excludes computed fields (contract) | `co_cli/deps.py:117–160` | **Gap** | Medium | No DESIGN doc states which fields `from_settings()` explicitly omits and why (`session_id`, `exec_approvals_path`, `skills_dir`, `memory_dir`, `library_dir`, `personality_critique`, `knowledge_search_backend`, `mcp_count`). `DESIGN-flow-bootstrap.md` shows `dataclasses.replace(CoConfig.from_settings(settings), ...)` as pseudocode but does not articulate the deliberate split — what `from_settings` owns vs what `create_deps` overrides. A future caller constructing `CoConfig` manually could silently miss the override step. |

## Verdict

GAPS_FOUND

| Priority | Gap | Recommended Fix |
|----------|-----|-----------------|
| P2 | `CoConfig.from_settings()` not named or described in `DESIGN-system.md` or `DESIGN-system-bootstrap.md` | Add one sentence to `DESIGN-system-bootstrap.md` §Deps Initialization: name the method, state that it performs a bulk copy of all pure-copy settings fields, and note that computed/session fields remain at dataclass defaults and are applied via `dataclasses.replace()` after `from_settings()` returns. |
| P2 | `from_settings()` contract (excluded fields and why) not documented | Add a short note in the `create_deps()` pseudocode block in `DESIGN-system-bootstrap.md` that enumerates the fields overridden by `dataclasses.replace()` and states why they cannot come from `Settings` (they are resolved at runtime, not declared in config). |

Both gaps are documentation accuracy issues, not correctness issues. The code is correct. The docs are silent rather than wrong.
