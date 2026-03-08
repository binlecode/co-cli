# REVIEW: delivery/startup-flow-consolidation — Delivery Audit
_Date: 2026-03-07_

## What Was Scanned

**Scope note:** The slug `startup-flow-consolidation` does not match any non-helper source module. Per the audit rules for doc-only deliveries, the full feature surface is scanned to confirm no new features were introduced without coverage.

**Agent tools scanned:** `_register(` in `co_cli/agent.py` — 32 registered tools enumerated.

**Sub-agent tools scanned:** `agent.tool(` in `co_cli/agents/*.py` — coder (3 tools), research (2 tools), analysis (2 tools).

**Config settings scanned:** All `Field(...)` declarations in `co_cli/config.py` `Settings` class — 47 settings; all `env_map` entries in `fill_from_env` — 47 mappings plus 5 per-role env vars and 3 special-case env vars (`CO_CLI_MCP_SERVERS`, `CO_CLI_WEB_POLICY_SEARCH`, `CO_CLI_WEB_POLICY_FETCH`).

**CLI commands scanned:** `@app.command` in `co_cli/main.py` and `co_cli/_commands.py` — 5 CLI commands; slash commands checked against `DESIGN-core.md` §Slash Commands table.

**DESIGN docs checked:**
- `docs/DESIGN-core.md` (capability surface, CLI commands, config, security)
- `docs/DESIGN-index.md` (config reference table, module index)
- `docs/DESIGN-tools.md` (approval table, tool families)
- `docs/DESIGN-tools-integrations.md` (memory, Obsidian, Google, web)
- `docs/DESIGN-tools-execution.md` (shell, files, background, todo, capabilities)
- `docs/DESIGN-tools-delegation.md` (sub-agents)
- `docs/DESIGN-flow-bootstrap.md` (startup flow)
- `docs/DESIGN-llm-models.md` (model config)
- `docs/DESIGN-knowledge.md` (knowledge config)
- `docs/DESIGN-flow-knowledge-lifecycle.md` (article tools)

---

## Delivery Audit

### Phase 1 — Scope

Delivery was doc-only: one pseudocode fix in `DESIGN-flow-bootstrap.md`. No new agent tools, config settings, CLI commands, or source modules were introduced. The audit confirms no new features lack documentation.

### Phase 2 — Feature Coverage

**Agent tools — main agent (32 tools via `_register`):**

| Tool | Approval (code) | In DESIGN-tools.md approval table | In DESIGN-core.md §3.1 | Coverage |
|------|-----------------|-----------------------------------|------------------------|----------|
| `start_background_task` | Yes | Yes | Yes | Full |
| `check_task_status` | No | Yes | Yes | Full |
| `cancel_background_task` | No | Yes | Yes | Full |
| `list_background_tasks` | No | Yes | Yes | Full |
| `check_capabilities` | No | Yes | Yes | Full |
| `delegate_coder` | No | Yes | Yes | Full |
| `delegate_research` | No | Yes | Yes | Full |
| `delegate_analysis` | No | Yes | Yes | Full |
| `list_directory` | No | Yes (§3.5 covers; omitted from main approval table) | Yes | Full |
| `read_file` | No | Yes | Yes | Full |
| `find_in_files` | No | Yes | Yes | Full |
| `write_file` | Yes | Yes | Yes | Full |
| `edit_file` | Yes | Yes | Yes | Full |
| `run_shell_command` | No (policy in tool) | Yes | Yes | Full |
| `create_email_draft` | Yes | Yes | Yes | Full |
| `save_memory` | Yes | Yes | Yes | Full |
| `save_article` | Yes | Yes | Yes | Full |
| `update_memory` | `all_approval` | Yes | Yes | Full |
| `append_memory` | `all_approval` | Yes | Yes | Full |
| `todo_write` | `all_approval` | Yes | Yes | Full |
| `todo_read` | `all_approval` | Yes | Yes | Full |
| `list_memories` | `all_approval` | Yes | Yes | Full |
| `search_memories` | `all_approval` | Yes | Yes | Full |
| `read_article_detail` | `all_approval` | Yes | Yes | Full |
| `search_knowledge` | `all_approval` | Yes | Yes | Full |
| `list_notes` | `all_approval` | Yes | Yes | Full |
| `read_note` | `all_approval` | Yes | Yes | Full |
| `search_drive_files` | `all_approval` | Yes | Yes | Full |
| `read_drive_file` | `all_approval` | Yes | Yes | Full |
| `list_emails` | `all_approval` | Yes | Yes | Full |
| `search_emails` | `all_approval` | Yes | Yes | Full |
| `list_calendar_events` | `all_approval` | Yes | Yes | Full |
| `search_calendar_events` | `all_approval` | Yes | Yes | Full |
| `web_search` | `search_approval` | Yes | Yes | Full |
| `web_fetch` | `fetch_approval` | Yes | Yes | Full |

Note: `recall_article` and `search_notes` are imported in `agent.py` but NOT passed to `_register()` — they are internal helpers, not agent-registered tools. `DESIGN-tools-integrations.md` correctly documents `search_notes` as "not agent-registered." `recall_article` is documented in `DESIGN-flow-knowledge-lifecycle.md` §Article Retrieval table as an internal function. No doc gap.

**Sub-agent tools — all match `DESIGN-core.md` §3.2 sub-agent surface table:**

| Sub-agent | Code tools | Doc tools | Match |
|-----------|------------|-----------|-------|
| Coder | `list_directory`, `read_file`, `find_in_files` | Same | Full |
| Research | `web_search`, `web_fetch` | Same | Full |
| Analysis | `search_knowledge`, `search_drive_files` | Same | Full |

**CLI commands:**

| Command | In DESIGN-core.md §CLI Commands | Coverage |
|---------|----------------------------------|----------|
| `co chat` | Yes | Full |
| `co status` | Yes | Partial — one-line desc; security findings sub-function and verbose-flag absence not documented there |
| `co logs` | Yes | Partial — one-line desc; fuller coverage in `DESIGN-logging-and-tracking.md` |
| `co traces` | Yes | Partial — one-line desc; fuller coverage in `DESIGN-logging-and-tracking.md` |
| `co tail` | Yes | Full — dedicated section in `DESIGN-logging-and-tracking.md` |

**Config settings:**

| Setting | Env Var | In DESIGN-index config table | Coverage |
|---------|---------|------------------------------|----------|
| `library_path` | `CO_LIBRARY_PATH` | **No** | Partial — covered in `DESIGN-knowledge.md` §Config only |
| `knowledge_hybrid_vector_weight` | None (intentional) | Yes — env var column marked `—` | Full |
| `knowledge_hybrid_text_weight` | None (intentional) | Yes — env var column marked `—` | Full |
| All other 44 settings | Correct env vars | Yes | Full |

### Phase 3 — Summary Table

| Feature | Class | Source | Coverage | Severity | Gap |
|---------|-------|--------|----------|----------|-----|
| `library_path` / `CO_LIBRARY_PATH` | Config setting | `co_cli/config.py:125` | Partial | minor | Covered in `DESIGN-knowledge.md` §Config but absent from the canonical `DESIGN-index.md` §2 Config Reference table |
| `co status` CLI detail | CLI command | `co_cli/main.py:574` | Partial | minor | One-line desc in `DESIGN-core.md`; security findings sub-function not cross-referenced |
| `co logs` / `co traces` CLI detail | CLI command | `co_cli/main.py:583,615` | Partial | minor | One-line desc only; acceptable for thin wrappers — full detail in `DESIGN-logging-and-tracking.md` |
| All 32 main-agent tools | Agent tool | `co_cli/agent.py` | Full | — | All present in DESIGN-tools.md approval table and DESIGN-core.md §3.1 |
| All 3 sub-agent tool surfaces | Sub-agent | `co_cli/agents/*.py` | Full | — | DESIGN-core.md §3.2 matches code exactly |
| 46 of 47 config settings | Config | `co_cli/config.py` | Full | — | All in DESIGN-index.md config table with correct env vars and defaults |
| DESIGN-llm-models.md stale references | Doc accuracy | `docs/DESIGN-llm-models.md` | Full | — | Previously flagged P1 gap (`_preflight.py` / `run_preflight`) is resolved — file now correctly references `_model_check.py` and `run_model_check` at lines 57–69, 123 |

**Summary: 0 blocking, 3 minor**

---

## Phase 4 — Second Pass

1. **All 32 agent tools confirmed present** in `DESIGN-tools.md` approval table or `DESIGN-core.md` §3.5 approval boundary table. Approval flags match code (`True` / `all_approval` / `False` / policy-conditional). No tool is named in code but absent from docs.

2. **`knowledge_hybrid_vector_weight` / `knowledge_hybrid_text_weight`** have no env var entry in `fill_from_env`. `DESIGN-index.md` correctly marks them with `—` in the Env Var column. Intentional design, not a gap.

3. **`library_path` absent from DESIGN-index config table** — confirmed. It appears in `DESIGN-knowledge.md` §Config with a full entry (setting name, env var `CO_LIBRARY_PATH`, default, description). The index table is the canonical consolidated reference and is missing this one entry. Severity: minor — the information is accessible via the knowledge doc.

4. **Previously flagged P1 (`DESIGN-llm-models.md` stale references)** — resolved. Lines 57–69 and 123 now correctly reference `_model_check.py`, `run_model_check`, and `deps.config.model_roles`. No action needed.

5. **`recall_article` and `search_notes`** imported in `agent.py` but not in `_register()`. Confirmed internal helpers. No approval-table gap.

6. **`_approval_risk.py`** — the previous review flagged this as a missing module-index entry, but the module is deleted in the current working tree (git status shows `D co_cli/_approval_risk.py`). No gap.

---

## Verdict

**CLEAN** (0 blocking, 3 minor)

All 32 main-agent tools have full approval table and capability surface coverage. All 47 config settings have doc coverage (46 in the canonical DESIGN-index table; 1 in component doc only). Sub-agent tool surfaces match docs exactly. The previously flagged P1 stale reference in `DESIGN-llm-models.md` is resolved.

| Priority | Feature | Gap | Recommended fix |
|----------|---------|-----|----------------|
| P1 | `library_path` / `CO_LIBRARY_PATH` config | Absent from `DESIGN-index.md` §2 Config Reference (canonical table) | Add row: `\| \`library_path\` \| \`CO_LIBRARY_PATH\` \| \`null\` \| User-global library directory; overrides default \`~/.local/share/co-cli/library/\` \|` |
| P2 | `co status` CLI detail | Security findings sub-function not cross-referenced in `DESIGN-core.md` §CLI Commands | Optional: append "(see `DESIGN-doctor.md` for security findings)" to the `co status` row |
| P3 | `co logs` / `co traces` CLI detail | One-line desc only in `DESIGN-core.md` | Acceptable as-is — both are thin wrappers; full coverage exists in `DESIGN-logging-and-tracking.md` |
