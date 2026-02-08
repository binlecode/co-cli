# Research: CLI Agent Tools Landscape (Refresh)

**Date:** 2026-02-08
**Purpose:** Re-benchmark the CLI agent landscape using primary sources and update co-cli tool strategy.
**Method:** Official docs/changelogs/repos only (no secondary summaries).

---

## 1. Current co-cli Baseline (from code)

### 1.1 Registered tools

| Tool | Category | Side effects | Approval |
|------|----------|--------------|----------|
| `run_shell_command` | Execution | Yes | Required |
| `search_notes` / `list_notes` / `read_note` | Local knowledge (Obsidian) | No | No |
| `search_drive_files` / `read_drive_file` | Google Drive | No | No |
| `list_emails` / `search_emails` | Gmail read | No | No |
| `create_email_draft` | Gmail write (draft) | Yes | Required |
| `list_calendar_events` / `search_calendar_events` | Calendar read | No | No |
| `list_slack_channels` / `list_slack_messages` / `list_slack_replies` / `list_slack_users` | Slack read | No | No |
| `send_slack_message` | Slack write | Yes | Required |
| `web_search` / `web_fetch` | Web intelligence | No | No |

Source: `co_cli/agent.py`, `co_cli/tools/*`.

### 1.2 Architecture snapshot

- Shell is Docker-sandboxed with configurable network/memory/CPU limits.
- Approval model is explicit per tool call (`requires_approval=True` for side-effect tools).
- Built-in web search/fetch tools are shipped (`web_search` via Brave API, `web_fetch` via HTTP fetch + HTML->markdown).
- Known limitation: `web_fetch` currently has no private-network/SSRF guard.
- No first-class persistent memory tool yet.
- No MCP client integration yet.

Source: `co_cli/main.py`, `co_cli/config.py`, `co_cli/agent.py`.

---

## 2. Benchmark Selection (refined)

### 2.1 Selection criteria

Systems are included as primary benchmarks only if they satisfy all of:

1. Terminal-first coding workflow is a core product use case.
2. Public documentation clearly describes tool/permission behavior.
3. Feature set is directly comparable to co-cli decisions (tools, approvals, memory, extensibility).

### 2.2 Primary benchmark set (direct comparators)

1. Claude Code
2. Gemini CLI
3. OpenAI Codex CLI
4. GitHub Copilot CLI
5. Aider
6. OpenCode
7. Goose

### 2.3 Secondary set (adjacent references, not parity targets)

1. Warp (Ambient Agents platform + CLI)
2. OpenClaw (personal automation agent platform)

### 2.4 Why this split

- Core set is most comparable to co-cli as a terminal coding/ops assistant.
- Secondary set is useful for roadmap ideas (browser, cron, orchestration), but less apples-to-apples.

---

## 3. Capability Matrix (2026-02-08 snapshot)

### 3.1 Core coding CLIs

| Capability | co-cli | Claude Code | Gemini CLI | Codex CLI | Copilot CLI | Aider | OpenCode | Goose |
|------------|--------|-------------|------------|-----------|-------------|-------|----------|-------|
| Shell execution | Yes (Docker sandbox) | Yes (`Bash`) | Yes (`run_shell_command`) | Yes | Yes | Yes (`/run`) | Yes (`bash`) | Yes (Developer extension) |
| File read/write/edit | Partial (Obsidian + shell) | Yes (`Read/Edit/Write/MultiEdit`) | Yes (file tools) | Yes | Yes (file ops documented) | Yes (primary workflow) | Yes (`read/edit/write/patch`) | Yes (Developer extension) |
| Web search | Yes (`web_search`, Brave API) | Yes (`WebSearch`) | Yes (`google_web_search`) | Yes (default cached mode since 2026-01-28) | Not as a dedicated search tool in reviewed docs | No first-class search tool in reviewed docs | Yes (`websearch`) | Via MCP/extensions |
| Web fetch / URL content | Yes (`web_fetch`) | Yes (`WebFetch`) | Yes (`web_fetch`) | Via web search workflow (no separate web_fetch doc tool) | Yes (`web_fetch`) | Yes (`/web` URL scrape) | Yes (`webfetch`) | Via MCP/extensions |
| Persistent memory | No first-class memory tool | Yes (`CLAUDE.md` memory system) | Yes (`save_memory` to `~/.gemini/GEMINI.md`) | Not documented as a dedicated memory tool in reviewed docs | Yes (Copilot Memory, public preview) | Not documented as a first-class memory layer | Not documented as a first-class memory layer | Yes (Memory extension) |
| MCP support | No | Yes | Yes | Yes | Yes (GitHub MCP preconfigured + add more) | Not documented in reviewed docs | Yes | Yes (extension model accepts MCP servers) |
| Planning/task decomposition | Partial (model-driven; no dedicated todo tool yet) | Yes (`Task`, `TodoWrite`, subagents) | Partial (tooling + workflows; no explicit `write_todos` doc found in this refresh) | Yes (plan mode default in recent releases) | Yes (built-in agents: Explore/Task/Plan/Code-review) | Yes (`architect` mode) | Yes (Plan agent + todo tools) | Yes (`/plan`) |
| Granular permission model | Yes (tool approvals + sandbox limits; web tools currently ungated as read-only) | Yes (allow/ask/deny + modes) | Yes (confirmations + trusted folders) | Yes (approval modes + sandbox modes) | Yes (path/url permissions + trust prompts) | Partial (strong git safety; less policy-centric) | Yes (`allow/ask/deny` per tool) | Yes (mode + per-tool permissions) |

### 3.2 Secondary platforms (signal only)

| Platform | Strong signal from docs | Relevance to co-cli |
|----------|--------------------------|---------------------|
| Warp | Full Terminal Use, interactive code review, MCP-capable agent platform, Slack/Linear triggers, containerized remote envs | High for remote team automation patterns, medium for local-first CLI parity |
| OpenClaw | First-class browser/web/cron/memory tooling and multi-channel automation | High for personal assistant patterns, lower for pure coding CLI parity |

---

## 4. Notable System Findings (source-backed)

### 4.1 Claude Code

- Strong built-in coding toolset with explicit permissions (`allow` / `ask` / `deny`) and multiple modes including `plan` and `bypassPermissions`.
- Built-in web tools (`WebSearch`, `WebFetch`) and MCP support are first-class, not bolt-ons.
- Memory model is markdown-native (`CLAUDE.md`, recursive lookup rules).

### 4.2 Gemini CLI

- Built-in shell/file/web-search/web-fetch tooling and explicit `save_memory` implementation to local markdown.
- Trusted Folders adds an explicit workspace trust boundary.
- MCP support is deep (stdio/SSE/HTTP, discovery, CLI management, OAuth for remote servers).

### 4.3 OpenAI Codex CLI

- Web search behavior changed recently: since **2026-01-28**, web search is enabled by default for local tasks in cached mode.
- Clear approval and sandbox controls; MCP and skills are first-class extension mechanisms.
- Plan mode and review workflows are actively evolving in current releases.

### 4.4 GitHub Copilot CLI

- Strong control plane: path/url permissions, trust prompts, MCP (GitHub server preconfigured), custom agents.
- `web_fetch` is built-in with URL allow/deny controls.
- Built-in specialized agents (Explore/Task/Plan/Code-review) are a notable UX pattern.

### 4.5 Aider

- Remains strong on git-native editing workflow, repo-map context strategy, and architect/code/ask modes.
- Supports URL ingestion via `/web`; scripting/headless mode is mature.
- In reviewed docs, MCP and persistent memory are not positioned as first-class core capabilities.

### 4.6 OpenCode

- Very complete built-in tool surface (`bash`, file edit stack, todo tools, `webfetch`, `websearch`).
- Fine-grained permission model is explicit and ergonomic.
- MCP and specialized agents/subagents are integrated into the main product model.

### 4.7 Goose

- MCP-native extension strategy is the central scaling mechanism.
- Has explicit permission modes and per-tool permission controls.
- Includes planning (`/plan`) and memory extension patterns relevant to co-cli evolution.

---

## 5. Updated Gaps for co-cli

### 5.1 Critical gaps (high impact, small-to-medium effort)

1. Web safety hardening for `web_fetch` (SSRF/private-network blocking + redirect revalidation)
2. URL/domain permission policy for web tools (allow/ask/deny + allowlist/denylist)
3. Local file R/W/edit tools outside Obsidian-only scope
4. First-class persistent memory primitives (`save_memory`, `recall_memory`)

### 5.2 Strategic gaps (higher effort)

1. MCP client support (tool extensibility without native integrations for each service)
2. Built-in planning/todo primitives (explicit decomposition state)
3. Calendar write operations and richer Slack write actions (thread reply, reactions)
4. Richer web retrieval controls (`domains`, `recency`, pagination, cached/live mode)

### 5.3 Already strong (preserve)

1. Docker sandbox boundary with resource controls
2. Per-tool approval model for side-effect actions
3. Practical Google + Slack integration baseline
4. Built-in web search + web fetch baseline

---

## 6. Revised Roadmap Recommendation

### Phase A: Web intelligence hardening

- Add private-network/SSRF protections for `web_fetch` (including redirect target checks)
- Add explicit web permission policy (`allow|ask|deny`) and URL/domain allow/deny rules
- Add safer retrieval controls (`cached|live|disabled` search mode, domains/recency filters)

### Phase B: General local file toolset

- Add `read_file`, `write_file`, `edit_file`, `list_directory`
- Enforce strict path policies (cwd-scoped + optional allowlist)

### Phase C: Memory v1 (simple and explicit)

- Add `save_memory`, `recall_memory`, `list_memories`
- Start with local JSON or SQLite in XDG data dir

### Phase D: MCP pilot

- Support project/user-scoped MCP config
- Start with stdio transport first, then remote transports
- Reuse existing approval model for risky MCP tool calls

### Phase E: Planning/task UX

- Add todo/task primitives (session-local first, persistent optional)
- Keep single-agent core; avoid premature multi-agent complexity

---

## 7. Source Quality Notes

- This refresh intentionally removed unstable claims (stars/users/market-share counts) unless needed.
- Claims are anchored to official product docs/changelogs as of 2026-02-08.
- For secondary platforms (OpenClaw, Warp), feature scope is broader than coding CLI; treat as design inspiration, not parity targets.

---

## 8. Primary Sources

### co-cli codebase (local)

- `co_cli/agent.py`
- `co_cli/main.py`
- `co_cli/config.py`
- `co_cli/deps.py`
- `co_cli/tools/*`

### Claude Code (Anthropic)

- https://docs.anthropic.com/en/docs/claude-code/settings
- https://docs.anthropic.com/en/docs/claude-code/mcp
- https://docs.anthropic.com/en/docs/claude-code/memory
- https://docs.anthropic.com/en/docs/claude-code/tutorials

### Gemini CLI (Google)

- https://google-gemini.github.io/gemini-cli/
- https://google-gemini.github.io/gemini-cli/docs/tools/
- https://google-gemini.github.io/gemini-cli/docs/tools/web-fetch.html
- https://google-gemini.github.io/gemini-cli/docs/tools/memory.html
- https://google-gemini.github.io/gemini-cli/docs/tools/shell.html
- https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html
- https://google-gemini.github.io/gemini-cli/docs/cli/trusted-folders.html

### OpenAI Codex CLI

- https://developers.openai.com/codex/cli
- https://developers.openai.com/codex/cli/features
- https://developers.openai.com/codex/changelog
- https://developers.openai.com/codex/config-basic
- https://developers.openai.com/codex/mcp/
- https://developers.openai.com/codex/skills

### GitHub Copilot CLI

- https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli
- https://github.blog/changelog/2026-01-14-github-copilot-cli-enhanced-agents-context-management-and-new-ways-to-install
- https://docs.github.com/en/copilot/how-tos/use-copilot-agents/copilot-memory
- https://docs.github.com/en/copilot/concepts/coding-agent/mcp-and-coding-agent

### Aider

- https://aider.chat/docs/
- https://aider.chat/docs/usage/commands.html
- https://aider.chat/docs/usage/modes.html
- https://aider.chat/docs/usage/images-urls.html
- https://aider.chat/docs/git.html
- https://aider.chat/docs/repomap.html

### OpenCode

- https://opencode.ai/docs/tools/
- https://opencode.ai/docs/permissions
- https://opencode.ai/docs/agents/
- https://opencode.ai/docs/mcp-servers/

### Goose

- https://block.github.io/goose/docs/guides/goose-permissions/
- https://block.github.io/goose/docs/guides/managing-tools/tool-permissions/
- https://block.github.io/goose/docs/getting-started/using-extensions/
- https://block.github.io/goose/docs/guides/creating-plans/
- https://block.github.io/goose/docs/mcp/memory-mcp

### Warp

- https://docs.warp.dev/platform/cli
- https://docs.warp.dev/platform/cli/integrations-and-environments
- https://docs.warp.dev/agents/full-terminal-use
- https://docs.warp.dev/code/code-review
- https://docs.warp.dev/code/code-review/interactive-code-review
- https://docs.warp.dev/ambient-agents/mcp-servers-for-agents

### OpenClaw

- https://docs.openclaw.ai/index
- https://docs.openclaw.ai/tools
- https://docs.openclaw.ai/tools/web
- https://docs.openclaw.ai/tools/browser
- https://docs.openclaw.ai/automation/cron-jobs
- https://docs.openclaw.ai/concepts/memory
