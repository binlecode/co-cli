# DESIGN: Co Evolution (OpenClaw-Informed, Practical Scope)

## 1. Direction

Co should evolve from a tool executor into a personal operator with:

1. Strong perception (`eyes` via web search + web fetch).
2. Layered memory (session + project + personal).
3. Human-centered interaction (text first, voice optional).
4. Safe automation boundaries (approval-first for side effects).

This keeps Co useful for daily personal workflows while staying aligned with co-cli's local-first safety model.

## 2. OpenClaw Designs Worth Adopting

Only adopt patterns that fit co-cli's architecture and risk posture.

### 2.1 Eyes-first agent behavior

OpenClaw signal:

1. Web and browser capabilities are first-class.
2. Agent can gather current external context before acting.

Applicable to Co:

1. Add `web_search` and `web_fetch` as baseline tools.
2. Default Co behavior: search/fetch first when user asks about external facts, docs, or links.
3. Keep browser automation out of MVP.

### 2.2 Memory as product capability

OpenClaw signal:

1. Persistent memory is central to long-term usefulness.

Applicable to Co:

1. Add explicit memory tools: `save_memory`, `recall_memory`, `list_memories`.
2. Keep memory local (XDG data path), no hidden cloud sync.
3. Start with simple schema and explicit writes.

### 2.3 Multi-surface personal workflows

OpenClaw signal:

1. Integrates communication/scheduling surfaces as one workflow.

Applicable to Co:

1. Preserve and extend Google + Slack integrations.
2. Add missing write operations incrementally (for example calendar create, Slack thread reply).
3. Prefer composable tools over hardcoded mega-commands in MVP.

### 2.4 Automation with guardrails

OpenClaw signal:

1. Supports scheduled and automated execution paths.

Applicable to Co:

1. Keep approval gates for side-effect tools.
2. Defer cron/scheduled jobs until core tools are stable and observable.
3. Require explicit opt-in for unattended operations.

## 3. Co Evolution Plan (MVP-first)

### Phase 1 (MVP)

1. Web intelligence: `web_search`, `web_fetch`.
2. Memory v1: explicit local memory tools.
3. File operations beyond Obsidian (`read_file`, `write_file`, `edit_file`, `list_directory`).

### Phase 2

1. Planning/task tools (`todo` primitives).
2. Integration completeness (Calendar write, richer Slack writes).
3. MCP client pilot for extensibility.

### Phase 3 (Post-MVP)

1. Voice input (STT) and voice output (TTS).
2. Optional browser automation for high-value flows.
3. Optional scheduling engine with strict approval policy.

## 4. Boundaries and Non-Goals (for now)

1. No full autonomous personal-agent mode in MVP.
2. No broad background automation without explicit user controls.
3. No memory auto-ingestion that stores sensitive data implicitly.

## 5. Principle

Adopt OpenClaw's strength (external perception + memory + multi-surface workflows) while keeping co-cli's identity:

1. Local-first.
2. Approval-first.
3. Incremental, testable MVP delivery.
