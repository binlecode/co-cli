# TODO: Daemon Utilities & Background Workflows

**Status:** Planned
**Target:** Implement the first set of high-value, explicitly delegated background workflows for the `co daemon`.
**Pydantic-AI Patterns:** Headless `RunContext`, user-initiated scheduling, zero-trust background execution.

## 1. Context & Motivation

When measured strictly against `co-cli`'s core design philosophy—**local-first, approval-first, inspectable, non-over-engineered**—many typical "autonomous agent" background tasks (like silently reading your emails every hour, pulling Google Drive files automatically) are fundamentally flawed. They violate the "approval-first" and "inspectable" mandates by consuming API quotas implicitly and performing speculative work.

However, a core architectural feature of `co-cli` is its heavily local knowledge and memory model (Markdown + SQLite FTS5). Over days of interactive sessions, the agent accumulates a vast amount of fragmented facts, conversational context, and code snippets in `.co-cli/memory/`. 

To maintain the performance and semantic precision of the agent's context window, this data must be periodically synthesized. **Nightly Self-Learning and Knowledge Consolidation** is the premier, foundational use case for a 24x7 local daemon.

---

## 2. Utility 1: Nightly Self-Learning & Knowledge Consolidation

**Problem:** Over time, the `.co-cli/memory/` folder fills with overlapping or fragmented facts ("User prefers pytest", "Theme is dark", "We refactored auth.py yesterday"). The FTS5 index becomes noisy, and injecting all raw memories into the prompt blows up token budgets and confuses the model.
**Goal:** The daemon wakes up nightly (e.g., 3:00 AM) to read all recently added memories, distill them into high-density semantic profiles, and rebuild the SQLite FTS index without slowing down the interactive `co chat` user.

### Implementation Steps
- [ ] Implement `co_cli/daemon/jobs/knowledge_compaction.py`.
- [ ] The daemon runs a headless summarization agent (ideally `reasoning_effort=none` to save cost) over the `kind: memory` files modified in the last 48 hours.
- [ ] **Consolidation:** It detects conflicting facts (e.g., an old memory saying "Node.js v18" and a new one saying "Upgraded to Node.js v22") and outputs a single, updated canonical memory file.
- [ ] **Pruning:** It deletes the redundant, raw markdown files.
- [ ] **Re-indexing:** It natively calls `KnowledgeIndex.rebuild()` or selectively updates the FTS5 tables in `search.db`.
- **Why it aligns:** It is entirely local (no external API calls/quota usage), it dramatically improves the speed and reasoning quality of the next day's interactive session, and it happens completely invisibly to the user.

---

## 3. Utility 2: Deferred Task Execution (The "Remind Me / Follow Up" Pattern)

**Problem:** Users often need `co` to perform an action at a specific time in the future, but they don't want to leave their laptop open and terminal running.
**Goal:** The user can explicitly schedule an action to execute hours or days from now.

### Implementation Steps
- [ ] Add a user-facing tool/slash command: `/daemon schedule "Draft a reply to Alice's email tomorrow at 9am"` or `/daemon schedule "Remind me to update the sprint board in 3 hours"`.
- [ ] Implement `co_cli/daemon/jobs/deferred_task.py`.
- [ ] The task is inserted into the SQLite queue with a specific `scheduled_for` epoch time.
- [ ] When the time arrives, the daemon wakes up, runs the task headlessly, and leaves the drafted email or reminder note in the user's `TODO.md` or next chat session context.
- **Why it aligns:** Highly pragmatic, 100% user-initiated, explicitly inspectable.

---

## 4. Utility 3: Generalized Async Poller & Watchdog

**Problem:** Users often need to wait for long-running processes (a data download, an API rate limit to reset, or a specific email reply) but don't want to poll manually.
**Goal:** An on-demand background watchdog that polls a generic condition and notifies the user upon completion.

### Implementation Steps
- [ ] Add a user-facing tool/slash command: `/daemon watch [condition]`. (e.g., `/daemon watch "until I get an email from Bob"` or `/daemon watch "until localhost:8080 returns 200"`).
- [ ] Implement `co_cli/daemon/jobs/async_poller.py`.
- [ ] The daemon periodically executes the read-only check (using `curl` via bash, or calling the `google_gmail` tool).
- [ ] **If the condition is met:** The daemon marks the job complete and writes a notification to the database, which surfaces as a green alert in `co status` or the next interactive REPL prompt.
- **Why it aligns:** It replaces a tedious human workflow (polling), it only hits APIs when explicitly told to, and it safely delegates a waiting task.

---

## 5. Utility 4: Heavy Batch Processing & Extraction

**Problem:** Asking `co` to summarize 50 PDFs in a Google Drive folder, or to FTS index a massive local Obsidian vault takes several minutes. In `co chat`, this blocks the synchronous REPL and frustrates the user.
**Goal:** The user can kick off massive data extraction/processing jobs to the daemon.

### Implementation Steps
- [ ] Add a tool: `delegate_heavy_task_to_daemon(prompt: str)`.
- [ ] The user requests: "Read all the docs in my 'Project X' Google Drive folder and build a master summary in my Obsidian vault."
- [ ] The interactive agent recognizes this will take >2 minutes, and replies: "This is a heavy task. I have delegated it to the background daemon. You will be notified when it completes."
- [ ] The daemon picks up the job, runs the `google_drive` and `obsidian` tools headlessly at its own pace without blocking the user, and records the result.
- **Why it aligns:** It adheres to the "approval-first" rule (the user explicitly asked for the extraction), and it solves a real UX problem of token-heavy tasks freezing the CLI.

---

## 6. Guardrails & Security

- **Zero-Trust Baseline:** Unless explicitly pre-approved, none of these utilities are permitted to use the `edit`, `write`, or mutating `bash` tools. They are strictly confined to `Read`, `Glob`, `Grep`, Google API reads, and specific read-only shell commands.
- **Artifact Scope:** The daemon may only write to its own `REPORT-*.md` files, append to existing `TODO-*.md` files, or mutate internal `.co-cli/` state (SQLite/memory files).
- **No Implicit Polling:** The daemon must **never** hit external APIs (Google Drive, Gmail, etc.) on an autonomous schedule unless the user explicitly ran `/daemon watch` or `/daemon schedule`. This prevents invisible quota exhaustion.