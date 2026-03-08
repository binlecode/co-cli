# Plan Audit Log: CoConfig from_settings() Factory + Named Constants
_Slug: coconfig-from-settings | Date: 2026-03-08_

## Cycle C1 — Team Lead
Submitting for Core Dev review.

## Cycle C1 — PO
**Assessment:** revise
**Blocking:** PO-M-1, PO-M-2
**Summary:** The core fix (`from_settings()` + Pattern A cleanup) is the right solution and the right problem. However, named-constants extraction (TASK-1+2) is scope-creep that doesn't serve the stated outcome, and the sentinel-file mechanism introduces a live operational side-effect that exceeds what the testing concern warrants. Strip those two issues and the plan is tight.

**Major issues:**
- **PO-M-1** [Scope / TASK-1 + TASK-2]: Named constants for ~30+ Settings literals are editorial cleanup, not a prerequisite for `from_settings()`. The stated problem is "tests get incomplete config because there is no factory." That is fully solved by TASK-3+4+6 alone. TASK-1+2 add DRY between `Settings` and `CoConfig` defaults, which is a real (minor) improvement, but it is a separate concern that belongs in a separate, independently reviewable task or a follow-on. Bundling it here triples the diff size of a refactor that should be small and mechanical, making review harder and risk higher. Recommendation: move TASK-1+2 to a follow-on TODO (e.g., `TODO-named-constants.md`) or demote them to a single optional TASK-6.5 labeled "polish" with no blocking relationship to TASK-3+4+6.

- **PO-M-2** [Scope / TASK-5 — sentinel `.co-cli/settings.json`]: The `.co-cli/` directory already exists at the project root with live data (`co-cli.db`, `knowledge/`, `memory/`, `session.json`). Adding `settings.json` there means every `uv run co chat` launched from the project root will silently pick up sentinel values (`memory_max_count: 150`, `doom_loop_threshold: 4`, etc.) rather than the developer's real user config. The sentinel is solving a testing concern (detect `CoConfig()` that bypasses `from_settings()`) by mutating a shared, production-adjacent path. A better mechanism: if TASK-6 requires `from_settings(settings)`, any field gap is immediately visible as a test assertion failure on the returned config value — no sentinel needed. Alternatively, place the sentinel in a pytest-only config path (e.g., via a `CO_CLI_PROJECT_CONFIG` env var pointing to a test fixture file) so it cannot affect live sessions. Recommendation: either drop TASK-5 (the `from_settings()` classmethod itself makes field gaps detectable) or replace it with an env-var-based test fixture that does not live in `.co-cli/`.

**Minor issues:**
- **PO-m-1** [TASK-7]: This is not a task — it has no file changes and its `done_when` condition (`uv run pytest`) is already the `done_when` for every preceding task. Recommendation: delete TASK-7 and add its `done_when` condition to TASK-6's `done_when` as a final bullet.

- **PO-m-2** [High-Level Design §2 — `from_settings()` field coverage]: `Settings` contains several fields with no `CoConfig` counterpart (`background_*`, `session_ttl_minutes`, `theme`, `tool_retries`, `model_http_retries`, `max_request_limit`, `knowledge_embedding_*`, `knowledge_hybrid_*`). The plan's field list in §2 implicitly omits them but never says so. A developer implementing `from_settings()` will stall or add incorrect mappings. Recommendation: add a short "Fields in Settings intentionally omitted from `from_settings()`" table in §2.

## Cycle C1 — Core Dev
**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2, CD-M-3
**Summary:** Three blocking issues require plan corrections before implementation: a missed callsite in `evals/_common.py` (also broken today), an under-specified `skills_dir` migration that leaves dead mutation code, and an ambiguous treatment of `knowledge_search_backend` in `from_settings()` that will confuse the implementer.

**Major issues:**
- **CD-M-1** [Scope / TASK-6]: `evals/_common.py` is a Pattern A callsite not listed in scope. It manually copies fields from `Settings` into `CoConfig` using the same anti-pattern the plan exists to eliminate — and it is already broken (references `s.model_roles` and `get_role_head` which do not exist; real field is `role_models`). Excluding it means the plan ships with a known broken caller and a surviving anti-pattern. Recommendation: add `evals/_common.py` to the Pattern A file list in TASK-6 and fix the stale references as part of that task.
- **CD-M-2** [High-Level Design §2 / TASK-4]: `skills_dir` is currently set by direct mutation (`deps.config.skills_dir = Path.cwd() / ".co-cli" / "skills"`) at `main.py` line 292, *outside* `create_deps()` and after it returns. If TASK-4 adds `skills_dir` to the `dataclasses.replace()` inside `create_deps()`, the line-292 mutation becomes dead code that silently overwrites the replace() result with the same value. The plan must explicitly choose: (a) preserve line 292 and remove `skills_dir` from `dataclasses.replace()`, or (b) delete line 292 and move `skills_dir` fully into `create_deps()`. Recommendation: choose option (b).
- **CD-M-3** [High-Level Design §2, `knowledge_search_backend` row]: The plan says `from_settings()` does NOT set this field, leaving the dataclass default `"fts5"`. This is the correct final behavior (runtime resolution overrides it in `main.py`). But the plan never states this explicitly — the implementer will see "not set by `from_settings()`" but not understand that `from_settings()` silently leaves `"fts5"` regardless of `settings.knowledge_search_backend`. Recommendation: add explicit note to the §2 table row.

**Minor issues:**
- **CD-m-1** [TASK-4 `done_when`]: Does not mention removing the line-292 `skills_dir` mutation. Recommendation: add "line-292 mutation removed" to TASK-4's `done_when`.
- **CD-m-2** [TASK-5 / High-Level Design §3]: Env vars (Layer 3) override the sentinel. Recommendation: add one sentence noting this.
- **CD-m-3** [High-Level Design §1 / TASK-1]: The `DEFAULT_*` constant list omits `DEFAULT_SHELL_SAFE_COMMANDS` and background-task literals. Recommendation: explicitly include or exclude these in TASK-1's scope.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | `evals/_common.py` added to TASK-6 scope; stale API references fixed in same task |
| CD-M-2   | adopt (b) | Line-292 mutation removed; `skills_dir` consolidated into `create_deps()` via `dataclasses.replace()` |
| CD-M-3   | adopt    | Explicit note added to §2 table row for `knowledge_search_backend` |
| CD-m-1   | adopt    | "line-292 mutation removed" added to TASK-4 `done_when` |
| CD-m-2   | adopt    | Env-var-precedence note added to §4 |
| CD-m-3   | adopt    | `DEFAULT_SHELL_SAFE_COMMANDS` and background-task constants explicitly included in TASK-1 |
| PO-M-1   | reject   | User explicitly requested "CAP_VALUE vars in config, no magic numbers" — named constants are a stated requirement, not gold-plating. TASK-1+2 stay. |
| PO-M-2   | modify   | User explicitly requested a `settings.json` with custom values. File stays at `.co-cli/settings.json` (the correct project config path for both tests and dev). Values must be operationally valid real model names — content confirmed with user before TASK-5 is implemented. |
| PO-m-1   | adopt    | TASK-7 removed; its `done_when` merged into TASK-6 |
| PO-m-2   | adopt    | "Settings fields intentionally omitted" table added to §2 |
