# TODO: Daemon Utility 1 — Nightly Knowledge Compaction

**Status:** Planned
**Target:** A scheduled nightly job that consolidates overlapping/redundant facts in local memory, keeping the FTS5 index clean and the interactive prompt context token-efficient.
**Pydantic-AI Patterns:** Headless execution, optimized agent config (`reasoning_effort="none"`), structured output.

## 1. Context & Motivation
As users interact with `co chat` daily, `.co-cli/memory/` accumulates fragmented facts (e.g., "User prefers pytest", "Theme is dark", "We refactored auth.py yesterday"). Injecting all raw memories into the prompt causes token bloat and confuses the model. The daemon must wake up nightly (e.g., 3:00 AM) to read recent memories, distill them into high-density semantic profiles, and rebuild the SQLite FTS index without blocking the user.

## 2. Dev Implementation Details

### 2.1 Agent Configuration
To minimize LLM costs for background housekeeping, the daemon must bypass the standard `co chat` interactive agent and construct a specialized, low-cost summarization agent.
- **File:** `co_cli/daemon/jobs/knowledge_compaction.py`
- **Agent Init:** 
  ```python
  from co_cli.agent import build_agent
  from co_cli.config import CoConfig

  # Force reasoning_effort to none for simple summarization/compaction tasks
  compaction_config = CoConfig(reasoning_effort="none", ...)
  compaction_agent = build_agent(config=compaction_config).agent
  ```

### 2.2 Execution Logic
- **Data Gathering:** 
  Use `co_cli.tools.memory.get_memory_index()` to retrieve all `MemoryEntry` objects where `created_at` or `updated_at` > `(time.time() - 172800)` (last 48 hours).
- **Prompting:**
  Pass the `content` of these entries as the `user_input` to the `compaction_agent`.
  *System Prompt Override:* "You are a headless knowledge consolidation worker. Analyze the following local memories. Detect conflicting facts (e.g., node v18 vs v22) and merge overlapping facts. Output a single, consolidated Markdown payload. Do not add conversational filler."
- **Execution:** 
  ```python
  result = await compaction_agent.run(prompt, deps=deps)
  consolidated_text = result.data
  ```

### 2.3 Mutations & Cleanup
- **Write New State:** Use the existing `co_cli.tools.memory.save_memory(ctx, consolidated_text, tags=["consolidated", "system"])`. This natively handles writing the new Markdown file and updating the SQLite `KnowledgeIndex`.
- **Prune Old State:** Iterate over the old `MemoryEntry.path` objects and use `os.remove()` to delete the raw markdown files.
- **Index Rebuild:** Call `ctx.deps.services.knowledge_index.rebuild()` to ensure the FTS5 database perfectly mirrors the new disk state.

### 2.4 Guardrails & Security
- The job must execute with `ctx.deps.runtime.is_headless = True`.
- **Test Mandate:** Write `tests/test_daemon_compaction.py` using a real SQLite DB. Create 5 conflicting memory files, run the job headlessly, and assert that 5 files are deleted and 1 new consolidated file exists.

### 2.5 Session Data Cleanup
When `.co-cli/sessions/` exceeds a volume threshold (default 500 MB), prune old session files using a combined age + recent-access hotness score. No LLM needed — pure filesystem housekeeping run alongside knowledge compaction.

- **Trigger:** Total size of `sessions/` directory > `SESSION_CLEANUP_THRESHOLD_MB` (default 500). Skip entirely if under threshold.
- **Scoring:** For each session pair (`{id}.json` + `{id}.jsonl`), compute a hotness score: `score = recency_weight * days_since_last_access + size_weight * file_size_mb`. Lower score = hotter (keep). Sessions accessed within the last 7 days are exempt from pruning.
- **Pruning:** Sort by score descending (coldest first). Delete `.jsonl` transcript first (bulk of the size), then `.json` metadata. Stop when total directory size drops below 80% of threshold (400 MB default) — leave headroom to avoid thrashing.
- **Safety:** Never delete the current active session (most recent by mtime). Log each deleted session ID and bytes reclaimed.
- **Config:** `session_cleanup_threshold_mb: int = 500` in daemon job config (not in `CoConfig` — daemon-only concern).