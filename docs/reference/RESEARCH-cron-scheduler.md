# RESEARCH: Cron Scheduler — Peer Review & Adoption Design

Status: rejected
Aspect: asynchronous execution and scheduling
Pydantic-AI patterns: durable workflows, bounded background execution, resumed runs

**Date:** 2026-03-31

**Scope:** OpenClaw's cron system reviewed as primary reference; gap analysis against `co-cli` architecture resulting in rejection of internal scheduler proposal.

---

## 1. Executive Summary & Gap Analysis

Previous drafts of this document proposed building a robust, SQLite-backed persistent cron scheduler inside `co-cli`, heavily modeled after OpenClaw. Following a deep gap analysis against peer system best practices and `co-cli`'s actual architecture, **this proposal is rejected.**

### 1.1 Architectural Mismatch (Daemon vs. Ephemeral CLI)
- **Peer Systems:** Systems like OpenClaw, Letta, or Nanobot that implement internal job scheduling rely on long-running daemon/server processes.
- **co-cli Reality:** `co-cli` is an interactive, ephemeral REPL (`uv run co chat`). The process exits when the user ends the session. An internal polling loop (`asyncio.sleep(10)`) would only execute jobs if the user happened to leave their terminal window open and running. Jobs scheduled for "tomorrow" or "every day at 9am" would silently miss their window if the CLI was closed.
- **Conclusion:** Building daemon-style persistent scheduling into an ephemeral CLI is fundamentally broken architecture. 

### 1.2 Over-engineering and Speculative Scope
- **CLAUDE.md Policy:** "Add abstractions only when a concrete need exists in the current scope — never speculatively. Design from first principles: non-over-engineered, MVP-first."
- **The Gap:** There is no concrete product requirement for `co` to duplicate the host operating system's `cron`, `launchd`, or CI/CD capabilities. If a user needs `co` to run a daily digest, the converged best practice for CLI tools (like `aider`, `claude-code`, `gemini-cli`) is for the user to configure system `cron` to invoke the CLI (e.g., `co run "generate daily digest"`).

### 1.3 Misrepresentation of Current Code
- **Previous Claim:** The earlier research claimed `TaskStorage (filesystem, metadata.json, output.log)` was already complete in `co_cli/tools/_background.py`.
- **Code Reality:** `co_cli/tools/_background.py` and `task_control.py` implement a lightweight, purely in-memory, session-scoped background task runner (`"""Session-scoped background task execution — no file I/O."""`). Tasks live in `CoSessionState.background_tasks` and die cleanly when the session ends. This is the correct, intended behavior for an ephemeral CLI tool.

---

## 2. Updated Design Decision for co-cli

`co-cli` will **not** implement an internal cron scheduler, SQLite job queue, or persistent background execution engine.

### 2.1 Background Tasks (Session-Scoped)
- `co-cli` will continue to use the existing `tools/task_control.py` and `_background.py` for session-scoped background execution. 
- These tools are intended for long-running shell processes (e.g., `uv run pytest`, `npm run build`) that the user wants to run without blocking the active chat REPL.
- When the chat session ends, all background tasks are terminated. No disk I/O or persistent state is used.

### 2.2 Scheduled Execution (Host-Delegated)
- For tasks that need to run on a persistent schedule (`at`, `every`, `cron`), `co-cli` delegates to the host OS.
- Users should use `cron`, `systemd` timers, `launchd`, or GitHub Actions to execute `co` in headless mode.
- Future improvements should focus on ensuring `co` has a robust, scriptable entrypoint (e.g., `co run "<prompt>" --headless`) rather than building an internal cron engine.

## 3. Guardrails (Updated)

- **No internal cron scheduler:** Do not build persistent job queues or interval polling loops inside the `co` runtime.
- **Background tasks remain ephemeral:** Do not add file-backed state or SQLite persistence to `co_cli/tools/_background.py`. Session scope is a hard boundary.
