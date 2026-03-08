# TODO: CoConfig from_settings() Factory + Named Constants

**Slug:** `coconfig-from-settings`
**Type:** Refactor (no behavior change)

---

## Context

### Why tests manually copy 3 fields from Settings

The five "heavy" test files (`test_signal_analyzer.py`, `test_commands.py`, `test_tool_calling_functional.py`, `test_memory_lifecycle.py`, `test_llm_e2e.py`) all produce a module-level `_CONFIG` by manually forwarding exactly three fields from the live `settings` singleton:

```python
_CONFIG = CoConfig(
    role_models={k: list(v) for k, v in settings.role_models.items()},
    llm_provider=settings.llm_provider,
    ollama_host=settings.ollama_host,
)
```

This pattern emerged because `CoConfig` has no factory: callers who need a "settings-backed" config must know which fields to forward and must write the forwarding code themselves. The three-field copy is the minimal viable version — it contains only the fields the LLM-dependent tests actually exercise. Every other field defaults silently.

### Current Settings → CoConfig mapping gap

`main.py:create_deps()` (lines 171–211) contains a ~40-line inline block that copies every field from `Settings` into `CoConfig`. This mapping is not reusable. Any path that needs to construct a settings-backed `CoConfig` outside of `main.py` must duplicate the logic or accept silent defaults. Tests are the primary casualty: they get the right model/provider but default `memory_max_count`, `doom_loop_threshold`, `web_http_max_retries`, etc., even when a real `settings.json` is present.

### Affected files and their current anti-pattern

**Pattern A — partial `_CONFIG` from settings (replaced by this plan):**

| File | Line |
|------|------|
| `tests/test_signal_analyzer.py` | 21–25 |
| `tests/test_commands.py` | 21–25 |
| `tests/test_tool_calling_functional.py` | 30–34 |
| `tests/test_memory_lifecycle.py` | 30–34 |
| `tests/test_llm_e2e.py` | 27–31 |
| `evals/_common.py` | ~62–87 (also broken: references stale `s.model_roles` / `get_role_head` — does not exist in current API) |

**Pattern B — bare `CoConfig()` with only test-local overrides (no change needed):**

These construct `CoConfig` with fields that are test-local (`session_id`, `memory_dir`, `obsidian_vault_path`, `doom_loop_threshold`, `knowledge_search_backend`, etc.) and have no meaningful connection to the user's `settings.json`. The bare `CoConfig()` base is appropriate — the test is exercising a specific behavior, not the full settings chain.

Files: `test_approval.py`, `test_orchestrate.py`, `test_background.py`, `test_history.py`, `test_doom_loop.py`, `test_memory.py`, `test_skills_loader.py`, `test_agent.py`, `test_capabilities.py`, `test_bootstrap.py`, `test_shell.py`, `test_obsidian.py`, `test_context_overflow.py`, `test_save_article.py`, `test_knowledge_index.py`, `test_google_cloud.py`, `test_delegate_coder.py`, `test_web.py`, plus inline instances in `test_memory_lifecycle.py` and `test_llm_e2e.py`.

**Pattern C — intentional hardcoded provider values (no change):**
- `test_model_check.py`: hardcodes `llm_provider="gemini"` / `llm_provider="ollama"` as deliberate test scenarios.

---

## Problem & Outcome

**Problem:** `CoConfig` has no `from_settings()` factory. The 40-line inline mapping in `main.py` is the only place that correctly and completely maps `Settings → CoConfig`. Tests that care about LLM routing copy 3 of 35+ fields and silently get defaults for the rest. If a new field is added to `Settings` and mapped in `main.py`, tests will not automatically pick it up.

**Outcome:** A single `CoConfig.from_settings(s: Settings) -> CoConfig` classmethod becomes the canonical, reusable path. `main.py` calls it then overrides computed/session fields via `dataclasses.replace()`. Tests use `CoConfig.from_settings(settings)` for the module-level config constant, so they automatically inherit any new pure-copy field. A `.co-cli/settings.json` at project root with non-default sentinel values ensures the `settings` singleton reads real configured values during test runs, making default-passthrough bugs detectable immediately.

---

## Scope

**In scope:**
- Named constants for all magic literals in `Settings` field defaults (`co_cli/config.py`) and `CoConfig` field defaults (`co_cli/deps.py`).
- `CoConfig.from_settings(s: Settings) -> CoConfig` classmethod in `co_cli/deps.py`.
- `main.py` refactored to use `from_settings()` + `dataclasses.replace()` for computed/session overrides. Line-292 `skills_dir` mutation consolidated into `create_deps()`.
- `.co-cli/settings.json` at project root with operationally-valid non-default values.
- Cleanup of Pattern A files (5 test files + `evals/_common.py`, including fixing stale API references in `_common.py`).

**Out of scope:**
- New fields, new tools, new behavior.
- Changing how `CoConfig` fields are used by tools.
- Pattern B and Pattern C tests.
- `CoSessionState`, `CoRuntimeState`, `CoDeps`.

---

## High-Level Design

### 1. Named constants extraction

**`co_cli/config.py`** — all inline literals in `Settings` field definitions become `DEFAULT_*` constants at module scope above the `Settings` class. Full list includes (but not limited to):

```python
DEFAULT_LLM_PROVIDER = "ollama"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_NUM_CTX = 262144
DEFAULT_CTX_WARN_THRESHOLD = 0.85
DEFAULT_CTX_OVERFLOW_THRESHOLD = 1.0
DEFAULT_MEMORY_MAX_COUNT = 200
DEFAULT_MEMORY_DEDUP_WINDOW_DAYS = 7
DEFAULT_MEMORY_DEDUP_THRESHOLD = 85
DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS = 30
DEFAULT_MEMORY_CONSOLIDATION_TOP_K = 5
DEFAULT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS = 20
DEFAULT_MEMORY_AUTO_SAVE_TAGS: list[str] = ["correction", "preference"]
DEFAULT_KNOWLEDGE_CHUNK_SIZE = 600
DEFAULT_KNOWLEDGE_CHUNK_OVERLAP = 80
DEFAULT_KNOWLEDGE_SEARCH_BACKEND = "fts5"
DEFAULT_KNOWLEDGE_RERANKER_PROVIDER = "local"
DEFAULT_DOOM_LOOP_THRESHOLD = 3
DEFAULT_MAX_REFLECTIONS = 3
DEFAULT_TOOL_OUTPUT_TRIM_CHARS = 2000
DEFAULT_MAX_HISTORY_MESSAGES = 40
DEFAULT_SHELL_MAX_TIMEOUT = 600
DEFAULT_SHELL_SAFE_COMMANDS: list[str] = [...]   # replaces private _DEFAULT_SAFE_COMMANDS
DEFAULT_WEB_HTTP_MAX_RETRIES = 2
DEFAULT_WEB_HTTP_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_WEB_HTTP_BACKOFF_MAX_SECONDS = 8.0
DEFAULT_WEB_HTTP_JITTER_RATIO = 0.2
DEFAULT_BACKGROUND_MAX_CONCURRENT = 5
DEFAULT_BACKGROUND_TASK_RETENTION_DAYS = 7
DEFAULT_BACKGROUND_AUTO_CLEANUP = True
DEFAULT_BACKGROUND_TASK_INACTIVITY_TIMEOUT = 0
# ... all remaining inline literals
```

**`co_cli/deps.py`** — `CoConfig` field defaults that duplicate `Settings` defaults reference the same constants (imported from `co_cli.config`). CoConfig-only path defaults get their own constants at the top of `deps.py`:

```python
DEFAULT_EXEC_APPROVALS_PATH = Path(".co-cli/exec-approvals.json")
DEFAULT_SKILLS_DIR = Path(".co-cli/skills")
DEFAULT_MEMORY_DIR = Path(".co-cli/memory")
DEFAULT_LIBRARY_DIR = Path(".co-cli/library")
```

### 2. `CoConfig.from_settings()` classmethod

Maps all **pure-copy fields** (direct value transfer, no computation):

`obsidian_vault_path` (with `Path(s.obsidian_vault_path) if s.obsidian_vault_path else None` — the only type coercion: Settings is `str | None`, CoConfig is `Path | None`), `google_credentials_path`, `shell_safe_commands`, `shell_max_timeout`, `gemini_api_key`, `brave_search_api_key`, `web_fetch_allowed_domains`, `web_fetch_blocked_domains`, `web_policy`, `web_http_max_retries`, `web_http_backoff_base_seconds`, `web_http_backoff_max_seconds`, `web_http_jitter_ratio`, `memory_max_count`, `memory_dedup_window_days`, `memory_dedup_threshold`, `memory_recall_half_life_days`, `memory_consolidation_top_k`, `memory_consolidation_timeout_seconds`, `memory_auto_save_tags`, `knowledge_chunk_size`, `knowledge_chunk_overlap`, `personality`, `max_history_messages`, `tool_output_trim_chars`, `doom_loop_threshold`, `max_reflections`, `knowledge_reranker_provider`, `role_models` (with `{k: list(v) for k, v in s.role_models.items()}`), `ollama_host`, `llm_provider`, `ollama_num_ctx`, `ctx_warn_threshold`, `ctx_overflow_threshold`.

**Fields NOT set by `from_settings()` — overridden by `main.py` via `dataclasses.replace()`:**

| Field | Reason |
|---|---|
| `session_id` | Generated at session start |
| `exec_approvals_path` | `Path.cwd() / ".co-cli/exec-approvals.json"` — runtime cwd |
| `skills_dir` | `Path.cwd() / ".co-cli/skills"` — runtime cwd |
| `memory_dir` | `Path.cwd() / ".co-cli/memory"` — runtime cwd |
| `library_dir` | Conditional: `Path(s.library_path) if s.library_path else DATA_DIR / "library"` |
| `personality_critique` | `load_soul_critique(s.personality)` — file I/O |
| `knowledge_search_backend` | Resolved after backend availability check (may fall back). `from_settings()` intentionally leaves this at the dataclass default `"fts5"` — the raw `settings.knowledge_search_backend` is NOT forwarded here because runtime resolution always overrides it via `dataclasses.replace()`. A caller that constructs `CoConfig.from_settings(s)` and reads `knowledge_search_backend` without going through `main.py`'s resolution will get `"fts5"` regardless of `s.knowledge_search_backend`. |
| `mcp_count` | `len(s.mcp_servers)` — derivative |

**Fields in `Settings` intentionally omitted from `from_settings()` (no `CoConfig` counterpart):**

| Settings field | Why omitted |
|---|---|
| `background_max_concurrent`, `background_task_retention_days`, `background_auto_cleanup`, `background_task_inactivity_timeout` | Consumed directly by `TaskRunner` in `main.py`, not injected into `CoConfig` |
| `session_ttl_minutes` | Unused in current codebase |
| `theme` | Display-only, not injected into deps |
| `tool_retries`, `model_http_retries`, `max_request_limit` | Agent construction params, not in `CoConfig` |
| `knowledge_embedding_provider`, `knowledge_embedding_model`, `knowledge_embedding_dims`, `knowledge_hybrid_vector_weight`, `knowledge_hybrid_text_weight`, `knowledge_reranker_model` | Consumed by `KnowledgeIndex` constructor in `main.py`, not injected into `CoConfig` (except `knowledge_reranker_provider` which IS mapped) |
| `library_path` | Used to compute `library_dir` (a computed field, see above) |
| `mcp_servers` | Used to compute `mcp_count` (a computed field, see above) |

### 3. `main.py` refactor

The `create_deps()` function's inline 40-line block becomes:

```python
config = CoConfig.from_settings(settings)
config = dataclasses.replace(
    config,
    session_id=session_id,
    exec_approvals_path=exec_approvals_path,
    memory_dir=memory_dir,
    library_dir=library_dir,
    skills_dir=Path.cwd() / ".co-cli" / "skills",
    personality_critique=_personality_critique,
    knowledge_search_backend=resolved_knowledge_backend,
    mcp_count=len(settings.mcp_servers),
)
```

The existing line-292 mutation (`deps.config.skills_dir = Path.cwd() / ".co-cli" / "skills"`) is **deleted** — `skills_dir` is now set inside `create_deps()` via `dataclasses.replace()`.

### 4. `.co-cli/settings.json` at project root

`Settings` resolves project config as `Path.cwd() / ".co-cli" / "settings.json"`. Pytest runs from the project root, so the file at `.co-cli/settings.json` is the operative config for both `uv run pytest` and `uv run co chat` launched from the project root. Values must be real and operationally valid. Numeric sentinels are chosen to differ from dataclass defaults so any `CoConfig()` construction that bypasses `from_settings()` will produce a detectable wrong value.

Note: env vars (Layer 3) override `settings.json` — tests that set `LLM_PROVIDER=gemini` etc. will override `llm_provider` from the sentinel for those fields. The regression surface is only effective when no conflicting env vars are set.

The `role_models` block must contain the user's actual configured model names — content confirmed with user before TASK-5 is implemented.

### 5. Test cleanup (Pattern A)

Replace in each of the six Pattern A files:

```python
# Before
_CONFIG = CoConfig(
    role_models={k: list(v) for k, v in settings.role_models.items()},
    llm_provider=settings.llm_provider,
    ollama_host=settings.ollama_host,
)

# After
_CONFIG = CoConfig.from_settings(settings)
```

`evals/_common.py` additionally requires fixing stale `s.model_roles` → `s.role_models` and removing the `get_role_head` reference (which does not exist in the current `co_cli.config` API).

---

## Implementation Plan

### TASK-1: Extract named constants in `co_cli/config.py`

**files:** `co_cli/config.py`

**done_when:** Every `Field(default=...)` call in `Settings` uses a named `DEFAULT_*` constant (no bare literals). Explicitly includes `DEFAULT_SHELL_SAFE_COMMANDS` (replacing `_DEFAULT_SAFE_COMMANDS`) and background-task constants (`DEFAULT_BACKGROUND_MAX_CONCURRENT`, etc.). Constants are `UPPERCASE_SNAKE` at module scope above `Settings`. `uv run pytest tests/test_model_roles_config.py` passes.

**prerequisites:** none

---

### TASK-2: Update `CoConfig` field defaults in `co_cli/deps.py` to reference constants

**files:** `co_cli/deps.py`

**done_when:** `CoConfig` fields whose defaults duplicate `Settings` defaults reference the same constants (imported from `co_cli.config`). CoConfig-specific path fields use `DEFAULT_EXEC_APPROVALS_PATH`, `DEFAULT_SKILLS_DIR`, `DEFAULT_MEMORY_DIR`, `DEFAULT_LIBRARY_DIR` defined at top of `deps.py`. No bare integer or string literals remain in `CoConfig` field defaults. `uv run pytest` passes.

**prerequisites:** TASK-1

---

### TASK-3: Add `CoConfig.from_settings()` classmethod

**files:** `co_cli/deps.py`

**done_when:** `CoConfig.from_settings(s: Settings) -> CoConfig` exists as a classmethod mapping all pure-copy fields. Computed fields (`session_id`, `exec_approvals_path`, `skills_dir`, `memory_dir`, `library_dir`, `personality_critique`, `knowledge_search_backend`, `mcp_count`) are left at dataclass defaults. Running `python -c "from co_cli.deps import CoConfig; from co_cli.config import Settings; c = CoConfig.from_settings(Settings.model_validate({'llm_provider': 'gemini'})); assert c.llm_provider == 'gemini'; assert c.session_id == ''"` exits 0.

**prerequisites:** TASK-1, TASK-2

---

### TASK-4: Refactor `main.py` to use `CoConfig.from_settings()` + `dataclasses.replace()`

**files:** `co_cli/main.py`

**done_when:** The 40-line `config = CoConfig(...)` block in `create_deps()` is replaced with `CoConfig.from_settings(settings)` followed by `dataclasses.replace(config, session_id=..., exec_approvals_path=..., memory_dir=..., library_dir=..., skills_dir=..., personality_critique=..., knowledge_search_backend=..., mcp_count=...)`. Line-292 `deps.config.skills_dir = ...` mutation is removed. `uv run pytest` passes. `uv run co status` exits without error.

**prerequisites:** TASK-3

---

### TASK-5: Create `.co-cli/settings.json` at project root

**files:** `.co-cli/settings.json`

**done_when:** ✅ File exists at `.co-cli/settings.json`. `reasoning[0] = qwen3:30b-q4_k_m-agentic`, `reasoning[1] = qwen3:30b-a3b-thinking-2507-q8_0-agentic` (fallback). Sentinel numerics: `memory_max_count: 150`, `doom_loop_threshold: 4`, `max_history_messages: 35`, `tool_output_trim_chars: 1800`, `memory_dedup_threshold: 80`, `shell_max_timeout: 500`. Verified: `python -c "from co_cli.config import get_settings; s = get_settings(); assert s.memory_max_count == 150"` exits 0.

**prerequisites:** none

---

### TASK-6: Update Pattern A files to use `CoConfig.from_settings(settings)`

**files:**
- `tests/test_signal_analyzer.py`
- `tests/test_commands.py`
- `tests/test_tool_calling_functional.py`
- `tests/test_memory_lifecycle.py`
- `tests/test_llm_e2e.py`
- `evals/_common.py`

**done_when:** All six files replace their partial `_CONFIG = CoConfig(...)` with `_CONFIG = CoConfig.from_settings(settings)`. The `{k: list(v) ...}` snippet is gone from each. `evals/_common.py`'s stale `s.model_roles` / `get_role_head` references are fixed. `uv run pytest` completes with 0 failures and 0 errors. `uv run co status` reports healthy.

**prerequisites:** TASK-3, TASK-5

---

## Testing

- `uv run pytest` must pass unchanged — zero failures, zero errors.
- No test behavior changes: tests that set explicit overrides (e.g., `doom_loop_threshold=2`) continue to do so.
- `.co-cli/settings.json` sentinel acts as the regression surface: any new Pattern A anti-pattern (`CoConfig()` bypassing `from_settings()`) will produce detectable wrong values for the sentinel fields — provided no conflicting env vars override them.

---

## Open Questions

None — all questions answered by source inspection, except the `role_models` values for TASK-5 (confirmed with user before implementation).

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev coconfig-from-settings`
