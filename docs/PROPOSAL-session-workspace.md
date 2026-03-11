# PROPOSAL: Session Workspace & File Exchange Pattern
_Date: 2026-03-10_
_Status: Proposed (Pending PO & TL Review)_

## Executive Summary

Following the review of peer systems (specifically the hyper-minimalist `TinyClaw` architecture), we identified a critical gap in `co-cli`'s execution model: **File-System Sandboxing and Transparency**. 

While `co` enforces strict *tool* boundaries (e.g., read-only sub-agents), it executes all agents within the same global working directory. This proposal introduces the **Session Workspace Pattern**, adapting TinyClaw's isolated directory approach to fit `co`'s engineering rigor.

This proposal advocates for three distinct architectural additions under the `.co-cli/` directory:
1. **Sub-agent Sandboxing** (`.co-cli/workspaces/`): Ephemeral scratchpads for delegated tasks.
2. **File Exchange** (`.co-cli/exchange/`): A standard ingress/egress directory for future multimodal and cross-surface file sharing.
3. **Markdown-Backed Background Tasks** (`.co-cli/schedules/`): Pairing our upcoming SQLite scheduler with legible, user-editable markdown files for job instructions.

### Why Adopt This? (Alignment with `co` Principles)
The `co` architecture explicitly rejects "magic prompt hacking" and "black-box execution." We believe in inspectability, local-first control, and structured tools. 

Adopting a strict File Exchange and Session Workspace pattern provides three massive adoption benefits for `co`:
1. **It preserves CLI simplicity**: We don't need to build complex WebUI upload widgets or Base64 API payload handlers for future multimodal features (images, PDFs). A simple directory watch on `.co-cli/exchange/` keeps the terminal UX clean.
2. **It hardens the trust boundary**: `co`'s primary value proposition is being a *trusted* local operator. Sandboxing sub-agents physically (via the file system) before they ever gain write access proves we take execution safety seriously, matching Codex's rigor.
3. **It makes autonomy legible**: Users hate opaque cron jobs hidden in SQLite tables. Storing background task instructions as standard `.md` files means users can manage their agent's recurring behavior using their own IDE and Git, which perfectly aligns with our existing `skills/*.md` and `memory/*.md` paradigms.

---

## 1. The Problem

Currently, `co-cli` operates directly in the project root. This creates several risks and limitations as the system scales:

1. **Sub-agent Pollution**: When `delegate_coder` runs, it reads the actual project files. If we ever grant sub-agents write capabilities (e.g., to generate test scaffolds), they risk polluting or breaking the user's working tree while "thinking" or iterating.
2. **Multimodal Friction**: As `co` moves toward cross-surface continuity (uploading PDFs, images, or audio), handling these files via base64 API payloads or complex schema attachments is brittle and bloats the context window.
3. **Opaque Background Tasks**: The planned `TaskScheduler` (from `RESEARCH-cron-scheduler.md`) stores the shell command in SQLite. This makes complex, multi-step instructions for a background task invisible to the user's IDE, hiding the "what" behind a database query.

---

## 2. Product Owner (PO) Perspective: UX & Safety

From a product standpoint, this proposal doubles down on `co`'s core tenets: **Trust through explicitness and inspectability.**

### Feature 1: The "Thinking" Scratchpad
Users currently have to trust that sub-agents won't mess up their files. By explicitly routing sub-agents to a `.co-cli/workspaces/task_123/` directory, users can physically open that folder in VSCode and watch the agent work in real-time, safely separated from their actual source code. If the agent writes a great script, the user can manually copy it over.

### Feature 2: Drop-and-Chat (File Exchange & Input Queuing)
Instead of building complex UI for file uploads or wrestling with terminal blocking while the LLM generates long responses, we establish a `.co-cli/exchange/` directory as an asynchronous context queue. 
- **User workflow**: The user drops `Q3_Report.pdf` or writes a complex `new_instructions.md` into `.co-cli/exchange/` while the terminal is busy rendering output.
- **Agent workflow**: On the very next turn boundary (before the UI queue pops the next text message), the system scans the directory. The agent is notified `[System: New files detected in .co-cli/exchange/: Q3_Report.pdf]` and can immediately use its standard `read_file` tool to interact with it. This natively solves the "how do I give the agent bulk context while it's thinking" problem without complicating the CLI's read loop.

### Feature 3: Legible Cron Jobs
Users don't want to use `/schedule update <id> --command "..."` to edit a complex daily summary prompt. By putting the instructions in `.co-cli/schedules/daily-digest.md`, the user manages the prompt like any other file in their project.

---

## 3. Tech Lead (TL) Perspective: Architecture & Implementation

From an engineering perspective, this requires surgical changes to `CoDeps`, the `ShellBackend`, and the `tools/files.py` implementations, rather than a massive rewrite.

### 3.1 Directory Topology

```text
<project-root>/.co-cli/
├── workspaces/
│   └── task_<uuid>/        # Ephemeral. Sub-agent cwd. Cleaned up on success.
├── exchange/               # Ingress/egress for files. Kept clean by a TTL policy.
└── schedules/
    └── <job_name>.md       # Markdown payloads for the SQLite scheduler.
```

### 3.2 Refactoring `CoDeps` and Context Isolation

Currently, `make_subagent_deps(base_deps)` just copies the existing `CoDeps`. We need to introduce a `workspace_dir` to `CoDeps` that enforces a chroot-like boundary.

**Changes to `deps.py`:**
```python
@dataclass
class CoDeps:
    ...
    workspace_dir: Path | None = None  # If set, tools must treat this as the root/cwd
```

**Changes to `tools/files.py` & `_shell_backend.py`:**
File tools (`read_file`, `write_file`, `list_directory`) and the `ShellBackend` must intercept requests and resolve them against `ctx.deps.workspace_dir` (if present) instead of `os.getcwd()`. Attempting to use `../` to break out of the `workspace_dir` must raise a `SecurityError`.

### 3.3 Modifying `tools/delegation.py`

When `delegate_coder` or `delegate_research` is invoked:
1. Generate a unique task ID.
2. `workspace_path = create_ephemeral_workspace(task_id)`
3. Optionally copy required context files into the workspace.
4. Pass the new `workspace_path` into `make_subagent_deps()`.
5. Run the agent.
6. On success, extract the `CoderResult` and optionally delete the workspace. On failure, leave the workspace intact for debugging.

### 3.4 Modifying the Upcoming Scheduler (`_scheduler.py`)

Instead of storing a raw `command` string in the SQLite `scheduled_jobs` table, the table stores a reference to the payload file.

**SQLite Schema Adjustment:**
```sql
-- Before
command TEXT NOT NULL

-- After
payload_file TEXT NOT NULL -- e.g., ".co-cli/schedules/standup.md"
```

**Execution Flow:**
When the timer ticks, the `TaskScheduler` reads the markdown file. The file can use frontmatter to define whether it is a raw shell script or an LLM prompt (e.g., `kind: agent_turn`).

### 3.5 Integrating with the Upcoming Input Queue

The `REVIEW-input-queue.md` proposes an explicit UI text queue. The File Exchange directory acts as the **bulk data twin** to that text queue. 

**Wait, Should the Text Queue also be a sequence file?**
We considered whether the text FIFO queue itself should be backed by a sequence file (e.g., `.co-cli/exchange/queue.jsonl`) rather than an in-memory queue inside `main.py`. 

*Why an in-memory `QueueManager` is better for text inputs:*
1. **Interrupts:** If the user hits `Ctrl+C` while the agent is busy, we want to clear or pause the in-memory queue immediately. If the queue were a file, we'd have to deal with race conditions and file-locking upon abrupt exits.
2. **Ephemeral Nature:** User chat inputs ("stop", "actually fix the other function") are highly contextual to the exact second they are typed. If the process crashes and restarts 10 minutes later, replaying a stale "fix the other function" command from a JSONL file without context is dangerous.
3. **The File Exchange is for Bulk Data:** Text commands are control signals; files are data. We should keep control signals in memory where they can be explicitly canceled, and keep data on disk where it can be asynchronously dropped.

**Changes to `main.py` (chat loop):**
Between `run_turn()` invocations, the loop must execute a deterministic join:
1. Turn completes.
2. `chat_loop` scans `.co-cli/exchange/` for newly modified/added files (tracking `mtime`).
3. If new files exist, it synthesizes an automatic prompt: `[System: New files detected in workspace exchange: X, Y. Please review if relevant to current task.]`
4. Only after processing the bulk-exchange notification does the chat loop pop the next explicit text string from the UI `QueueManager`.

This guarantees that if a user drops a massive log file into the exchange folder while the model is typing, and then types "read the log" into the UI queue, the file exists in the context *before* the text command executes.

---

## 4. Phased Rollout Plan

### Phase 1: Core Scaffolding & File Exchange
1. Update `config.py` to generate `.co-cli/workspaces/` and `.co-cli/exchange/` on startup.
2. Implement `CoDeps.workspace_dir` and enforce the boundary checks in `tools/files.py` (preventing directory traversal breakouts).

### Phase 2: Sub-agent Sandboxing
1. Update `tools/delegation.py` to generate ephemeral workspaces.
2. Update the `make_*_agent` factories to bind the isolated `CoDeps`.
3. Add a background cleanup task to wipe `.co-cli/workspaces/` directories older than 24 hours to prevent disk bloat.

### Phase 3: Scheduler Integration (Dependent on Scheduler Rollout)
1. Ensure the implementation of `RESEARCH-cron-scheduler.md` Phase 1 incorporates `.co-cli/schedules/<file>.md` as the execution payload source of truth.
2. Update `/schedule add` to automatically generate the markdown template file and open it in the user's default `$EDITOR`.

---

## 5. Security & Risk Assessment

* **Directory Traversal (High Risk):** An LLM might try to `read_file("../../src/secrets.env")`. 
  * *Mitigation:* `tools/files.py` must use `Path.resolve()` and explicitly check `resolved_path.is_relative_to(deps.workspace_dir)` before performing *any* I/O operations.
* **Disk Bloat (Low Risk):** Workspaces pile up over time.
  * *Mitigation:* Apply the same TTL (Time-To-Live) cleanup logic we use for session persistence (`session_ttl_minutes`).

## 6. Request for Comments
- **PO:** Does the `exchange/` directory feel like the right UX for future multimodal file drops, or should we just track files wherever they are on disk?
- **TL:** Should the `workspace_dir` boundary apply strictly to *all* tools (including shell execution), or just native file tools? (Recommendation: Both, to prevent `cat ../../main.py` via shell).
