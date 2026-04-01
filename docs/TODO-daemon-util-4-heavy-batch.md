# TODO: Daemon Utility 4 — Heavy Batch Processing & Extraction

**Status:** Planned
**Target:** The user can kick off massive data extraction/processing jobs to the daemon so that `co chat` interactive REPL does not freeze for 5+ minutes while LLM tokens are consumed.
**Pydantic-AI Patterns:** Subagent spawn via headless `Task` tool, long-running agent execution, isolated dependencies.

## 1. Context & Motivation
Asking `co` to summarize 50 PDFs in a Google Drive folder, or to FTS index a massive local Obsidian vault, takes several minutes and tens of thousands of LLM tokens. In `co chat`, this blocks the synchronous REPL, frustrating the user.
The daemon provides a way to delegate token-heavy/time-heavy tasks directly into the background, where they churn at their own pace without blocking the user interface.

## 2. Dev Implementation Details

### 2.1 The Agent Tool
Create an `@agent.tool` in `co_cli/tools/daemon_control.py` named `delegate_heavy_task(ctx: RunContext[CoDeps], prompt: str)`.
- **Logic:** 
  The interactive agent recognizes a prompt will take >2 minutes ("Read all docs in my Google Drive folder X and summarize them"). It replies: "This is a heavy task. I have delegated it to the background daemon. You will be notified when it completes."
- **Database Insert:** 
  The tool inserts a row into `co-cli.db` table `daemon_jobs`:
  ```sql
  INSERT INTO daemon_jobs (id, prompt, scheduled_for_epoch, status, type)
  VALUES (?, ?, ?, 'pending', 'heavy_batch');
  ```
  `scheduled_for_epoch = now()` (run immediately).

### 2.2 The Daemon Worker & `subagent.py` Integration
In the `co daemon` polling loop (`co_cli/daemon/_worker.py`), select rows where `status='pending'` and `type='heavy_batch'`.
- **Pydantic-AI Execution:**
  ```python
  from co_cli.deps import CoDeps

  # Initialize headless runtime dependencies
  deps = CoDeps(...) 
  deps.runtime.is_headless = True
  ```
- **Execution via `subagent.py`:** 
  The `delegate_heavy_task` tool specifically formats the prompt so that the daemon executes it using the `task` tool (which spawns isolated subagents).
  - Prompt: `"Use the Task tool to launch the 'research' subagent. Tell it to read all files in Obsidian folder X and summarize them into a single local Markdown report."`
  - The headless daemon receives this prompt, natively triggers `subagent.py`, spawning the child agent perfectly identically to interactive mode.
  - **Memory Operations:** Because the daemon relies on the standard `RunContext`, the subagent's `save_memory` operations, FTS5 inserts, and API calls to Google Drive/Obsidian happen just like interactive mode, making the results instantly searchable in the user's next `co chat` session.

### 2.3 User Notification
When the heavy batch job completes, the user needs to know.
- **Status Integration:** Update `co_cli/bootstrap/_render_status.py`. In `get_status()`, query `daemon_jobs` for any row where `status='completed'` and `type='heavy_batch'` and `acknowledged=0`.
- **Banner Display:** The interactive terminal banner will display:
  `[Daemon] Heavy batch completed: "Summarize 50 PDFs in Drive"`
  `"Summary output saved to KnowledgeIndex."`
- **Acknowledge Tool:** Use `/daemon clear` to mark `acknowledged=1`.

### 2.4 Guardrails & Security
- **Security Rule:** Even heavy batch tasks are strictly read-only or confined to `.co-cli/memory/`. If `deps.runtime.is_headless == True` and the child subagent attempts to invoke `bash` to `rm -rf` something, `_tool_approvals.py` MUST intercept it and return `ModelRetry("Headless daemon subagents cannot execute mutating tools.")`.
- **Test Mandate:** Write `tests/test_daemon_heavy_batch.py`. Provide a `prompt` that tries to spawn an `explore` subagent headlessly to read 3 test files and summarize them. Execute the daemon polling loop and assert the subagent executes, the FTS index is updated, and the `daemon_jobs` row completes successfully.