# Research: CLI Agent Tools Landscape 2026

**Date:** Feb 2026
**Purpose:** Survey the personal AI CLI agent ecosystem and identify top tool candidates for co-cli functional expansion.

---

## 1. Current co-cli Tool Inventory

| Tool | File | Category | Risk | External Service |
|------|------|----------|------|------------------|
| `run_shell_command` | `shell.py` | Execution | High | Docker sandbox |
| `search_notes` | `obsidian.py` | Knowledge | Low | Local filesystem |
| `list_notes` | `obsidian.py` | Knowledge | Low | Local filesystem |
| `read_note` | `obsidian.py` | Knowledge | Low | Local filesystem |
| `search_drive` | `drive.py` | Documents | Low | Google Drive API |
| `read_drive_file` | `drive.py` | Documents | Low | Google Drive API |
| `draft_email` | `gmail.py` | Communication | High | Gmail API |
| `list_calendar_events` | `calendar.py` | Scheduling | Low | Google Calendar API |
| `post_slack_message` | `slack.py` | Communication | High | Slack API |

**Notable gaps vs. the 2026 field:** No web search, no web fetch, no memory/persistence, no file editing, no browser automation, no task management, no scheduling/cron, no GitHub integration.

---

## 2. Competitive Landscape — Tool Comparison Matrix

### 2.1 Tool-by-Tool Comparison Across 10 Major Agents

| Capability | co-cli | Claude Code | Gemini CLI | Codex | OpenClaw | Manus | Warp | Goose | OpenCode | Copilot CLI |
|------------|--------|-------------|------------|-------|----------|-------|------|-------|----------|-------------|
| **Shell/Exec** | Docker sandbox | Direct | Shell | Sandboxed | Shell + sandbox | gVisor (sudo) | Full TTY | Yes | Yes | Yes |
| **File R/W/Edit** | Obsidian only | Read/Edit/Write | ReadFile/WriteFile/Edit | Read/Write/Edit | File R/W | Filesystem | - | Yes | read/write/edit | Yes |
| **Web Search** | **-** | WebSearch | GoogleSearch | --search flag | Browser | Browser | - | Via MCP | Via MCP | - |
| **Web Fetch** | **-** | WebFetch | WebFetch | - | Browser | Browser | - | Via MCP | Via MCP | web_fetch |
| **Memory** | **-** | CLAUDE.md auto | SaveMemory | Thread memory | Local MD store | Event stream | - | Via MCP | SQLite sessions | Persistent |
| **Task/Todo** | **-** | TaskCreate/Update | WriteTodos | - | Cron/webhooks | Planner | /plan | - | - | - |
| **Browser** | - | - | - | - | Chrome control | Playwright | - | Via MCP | - | - |
| **Calendar** | Google Calendar | - | - | - | Via skills | - | - | Via MCP | - | - |
| **Email** | Gmail draft | - | - | - | Gmail Pub/Sub | - | - | Via MCP | - | - |
| **Slack** | Post message | - | - | - | Slack actions | - | Slack/Linear/GH | Via MCP | - | - |
| **Drive/Docs** | Google Drive | - | - | - | - | - | - | Via MCP | - | - |
| **GitHub** | **-** | Via Bash | - | - | Via skills | - | GitHub Actions | Via MCP | GitHub agent | GitHub MCP |
| **Cron/Schedule** | - | - | - | - | First-class | - | - | - | - | - |
| **Code Review** | - | Via subagent | - | Code review agent | - | - | Interactive | - | - | Code-review agent |
| **MCP Support** | **-** | Yes | Yes | Yes + self-as-MCP | AgentSkills | - | - | **Native (ref impl)** | Yes | Yes |
| **Multi-Agent** | - | Subagents | Codebase Investigator | Via Agents SDK | - | Internal multi-agent | Multi-agent parallel | - | - | 4 built-in agents |
| **Planning** | - | Plan mode | - | - | - | Planner module | /plan | - | - | Plan agent |
| **Git Native** | - | Via Bash | - | - | Via skills | - | Via agent | Via MCP | - | Via GitHub MCP |

**Legend:** **Bold dash** = critical gap vs. field. Via MCP = available through MCP extension, not built-in.

### 2.2 System-by-System Deep Dive

#### Claude Code (Anthropic)
- **Tools:** Read, Edit, Write, Bash, Glob, Grep, WebSearch, WebFetch, Task (sub-agents), NotebookEdit
- **Key pattern:** Auto-memory (`~/.claude/projects/*/memory/MEMORY.md`), sub-agent orchestration, `CLAUDE.md` project instructions
- **Unique:** Permission modes (auto-allow vs. confirm), hooks system, MCP server integration, IDE integration
- **Strength:** Deep codebase understanding via multi-tool orchestration

#### Gemini CLI (Google)
- **Tools:** Shell, ReadFile, WriteFile, Edit, FindFiles, SearchText, GoogleSearch, WebFetch, SaveMemory, WriteTodos, Codebase Investigator
- **Key pattern:** `SaveMemory` persists across sessions, `WriteTodos` for task tracking, `--yolo` mode for auto-confirm
- **Unique:** GoogleSearch as first-class tool, Codebase Investigator agent, GEMINI.md project instructions
- **Strength:** Integrated web search + local tools in one coherent package

#### OpenAI Codex
- **Tools:** Shell, file read/write/edit, web search (opt-in via `--search`)
- **Key pattern:** Skills system (`~/.agents/skills/`), MCP configuration (`~/.codex/config.toml`), `codex mcp` CLI
- **Unique:** Remote skill marketplace, AGENTS.md convention, thread memory summaries
- **Strength:** Extensible skills architecture

#### OpenClaw (2026 breakout)
- **Tools:** Browser (Chrome/Chromium), Canvas (A2UI), Nodes (camera/screen/location/notifications), Cron, Sessions, Discord/Slack actions, Shell, File R/W
- **Key pattern:** Persistent long-term memory (local), 100+ community AgentSkills, webhooks, Gmail Pub/Sub
- **Unique:** Goes far beyond coding — full personal automation (inbox management, calendar scheduling, browser automation, smart home, media control)
- **Strength:** Personal life automation, not just developer tasks

#### Manus (autonomous agent)
- **Tools:** Shell (sudo), browser (Playwright), filesystem, Python/Node.js interpreters, 200+ integrated tools
- **Key pattern:** CodeAct (executable Python as action), Planner module with step tracking, event stream memory, multi-agent orchestration
- **Unique:** Full autonomous operation in gVisor-secured containers, planning with status tracking
- **Context engineering:** KV-cache optimization, structured event stream, selective context loading
- **Strength:** Complex multi-step autonomous workflows

#### Warp (agentic development environment)
- **Tools:** Full terminal use (REPLs, debuggers, full-screen apps), /plan command, interactive code review
- **Key pattern:** Slack/Linear/GitHub integrations — tag @warp in an issue and it creates a PR
- **Unique:** Only product with "Full Terminal Use" — agent can interact with interactive programs
- **Strength:** Team workflow integration (Slack → agent → PR)

#### Goose (Block / Linux Foundation)
- **Tools:** All capabilities via MCP extensions — file ops, shell, code execution, debugging, multi-step workflows
- **Key pattern:** MCP-native architecture — reference implementation for Model Context Protocol. Contributed to Linux Foundation's Agentic AI Foundation (Dec 2025) alongside Anthropic's MCP and OpenAI's AGENTS.md
- **Unique:** 26K+ stars, 3,000+ MCP servers available, LLM-agnostic, MCP Apps (interactive experiences rendered in conversation)
- **Strength:** Infinite extensibility via MCP — every new MCP server is a new Goose capability for free

#### OpenCode (SST)
- **Tools:** read, write, edit, search (regex), bash, glob — clean Go implementation with Bubble Tea TUI
- **Key pattern:** 95K+ stars, 2.5M+ monthly users. Model-agnostic (OpenAI, Anthropic, Gemini, Bedrock, Groq, Azure, self-hosted)
- **Unique:** LSP integration for language-aware editing, SQLite session persistence, GitHub agent via GitHub Actions
- **Strength:** Developer experience — vim-like editing, session management, fast Go runtime

#### GitHub Copilot CLI (Jan 2026 enhanced release)
- **Tools:** web_fetch, shell, file R/W/edit, GitHub MCP Server (default)
- **4 Built-in Agents:** Explore (codebase analysis), Task (runs commands), Plan (implementation planning), Code-review (quality review)
- **Key pattern:** Persistent cross-session memory, async task delegation, parallel agent execution
- **Strength:** Tight GitHub ecosystem — ships with GitHub MCP Server by default

#### The Convergence Point

The ecosystem is converging on: **local-first agent + MCP extensibility + persistent memory + human-in-the-loop controls**. This is being standardized by the Linux Foundation's Agentic AI Foundation (Dec 2025) with MCP, Goose, and AGENTS.md under neutral governance.

The spectrum from code-focused to full personal assistant:
```
Code-Only ◄──────────────────────────────────────────────► Personal Agent
Aider → OpenCode → Codex → Gemini CLI → Claude Code → Goose → Warp → OpenClaw
                                                                         ▲
                                                              co-cli sits here
                                                    (Google suite + personal tools)
```

---

## 3. Agentic Patterns Worth Adopting

### 3.1 Context Engineering (from Manus)

The most important architectural pattern of 2025-2026. Context engineering treats the LLM's context window as a first-class system:

1. **Structured event stream** — Instead of dumping raw chat history, construct an optimized context per turn
2. **KV-cache hit rate** — The single most important production metric; structure prompts so prefixes are stable
3. **Selective tool results** — Compress/summarize large tool outputs before injecting into context
4. **Plan injection** — Maintain a "Plan" document that's always in context, showing progress

**co-cli relevance:** Currently we pass raw conversation history. We should consider structured context construction as we add more tools.

### 3.2 Persistent Memory (from Gemini CLI, OpenClaw, Claude Code)

Three tiers of memory emerging:

| Tier | Description | Examples |
|------|-------------|---------|
| **Session memory** | Within one conversation | Current co-cli behavior |
| **Project memory** | Persists per-project across sessions | `CLAUDE.md`, `GEMINI.md`, `SaveMemory` |
| **Long-term memory** | Cross-project, learns preferences over time | OpenClaw local store, Graphiti knowledge graphs |

**co-cli relevance:** We have zero cross-session memory. Even a simple `save_memory` / `recall_memory` tool writing to `~/.local/share/co-cli/memory.json` would be a major upgrade.

### 3.3 Planning & Task Tracking (from Manus, Gemini CLI, Warp)

All leading agents now include some form of task decomposition:

- **Manus:** Explicit Planner module that generates numbered step lists injected into context
- **Gemini CLI:** `WriteTodos` tool for persistent task lists
- **Warp:** `/plan` command for spec-driven development
- **Claude Code:** `TaskCreate`/`TaskUpdate` for structured task tracking

**co-cli relevance:** Adding a `write_todos` or `manage_tasks` tool would let Co break complex requests into trackable steps.

### 3.4 Human-in-the-Loop Patterns (converging across all agents)

The industry has converged on a 3-tier permission model:

| Level | Description | Examples |
|-------|-------------|---------|
| **Auto-allow** | Safe read operations | Search, read, list |
| **Confirm** | Writes and external actions | Shell exec, send email, post message |
| **Block** | Never auto-execute | Delete, force-push, destructive ops |

**co-cli relevance:** Our current `auto_confirm` bool is binary. Moving to `requires_approval=True` per-tool (Batch 6) aligns with industry standard.

### 3.5 MCP (Model Context Protocol) Extensibility

All three major vendor CLIs (Claude Code, Gemini CLI, Codex) now support MCP. The ecosystem has exploded to **7,260+ servers** cataloged, **1,200+ quality-verified**:

- Standard protocol for adding tools without modifying agent code
- STDIO and streaming HTTP transports
- Key curated lists: [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers), [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) (official), [mcp-awesome.com](https://mcp-awesome.com/)

**Top MCP Servers by GitHub Stars:**

| Server | Stars | Category |
|--------|-------|----------|
| Playwright MCP (Microsoft) | ~26K | Browser automation |
| Filesystem MCP (official) | In official repo | Local file access |
| GitHub MCP | ~3.2K | Code management, issues, PRs |
| Brave Search MCP | In official repo | Web search |
| Postgres MCP | In official repo | Database access |
| Google Drive MCP | In official repo | Cloud storage |
| Slack MCP | In official repo | Team communication |
| Mem0 MCP | Growing | Persistent memory |
| Google Sheets MCP | ~620 | Structured data |
| Linear MCP | Growing | Task management |

**Strategic insight:** Goose (Block's open-source agent, 25K+ stars) went all-in on MCP and gets access to 3,000+ community servers for free. pydantic-ai has MCP client support as of late 2025.

**co-cli relevance:** MCP support would let users add arbitrary tools (GitHub, Jira, Notion, etc.) without us building each integration.

### 3.6 Memory Architecture Deep-Dive

Production memory systems in 2026 have matured significantly:

**Mem0 (production-ready memory layer):**
- Auto-extracts "salient facts" from each conversation turn
- Deduplicates against existing memory before storing
- 91% lower latency and 90% token cost savings vs. full-context approaches
- Mem0 paper (April 2025) shows 26% improvement on MemoryBench

**Graphiti (Zep AI — temporal knowledge graphs):**
- Built on Neo4j, bi-temporal model: tracks when events occurred AND when ingested
- Hybrid retrieval: semantic embeddings + BM25 keyword + graph traversal (no LLM at query time)
- P95 latency of 300ms, incremental updates without batch recomputation
- Ships with an MCP server for integration with any agent

**Letta/MemGPT (OS-inspired virtual context management):**
- Treats memory like an operating system: working memory, archival memory, recall memory
- "Heartbeat" self-calls let the agent think between tool results
- Memory blocks are explicit, editable data structures the agent reads/writes

**co-cli phased approach:**
1. Phase 1: SQLite `memories` table (fact, source, timestamp) — keyword search
2. Phase 2: Auto-extract facts from conversations via LLM
3. Phase 3: Inject relevant memories into system prompt via search
4. Phase 4: (Optional) Graduate to Graphiti for relationship-rich knowledge

### 3.7 The "Morning Briefing" Pattern

The single most-requested personal AI agent feature across all community surveys. A single command that aggregates:

- Today's calendar events
- Unread high-priority emails
- Pending tasks / due items
- Slack unreads from important channels
- Weather
- (Optional) Health data (sleep score, HRV) from Whoop/Apple Health

**co-cli relevance:** We already have Calendar, Gmail, and Slack tools. A `co briefing` command that calls all three and formats the output is very high ROI for very low effort. This is a differentiator — none of the coding-focused CLIs (Claude Code, Gemini CLI, Codex) have this.

### 3.8 Multi-Agent Warning: The 17x Error Trap

Research shows adding agents has diminishing returns — coordination overhead can make multi-agent systems **17x more error-prone** than single agents if topology is not deliberately designed. Three topologies exist:

1. **Centralized (Puppeteer):** One orchestrator routes to specialists. Simple, debuggable.
2. **Decentralized (Swarm):** Peer-to-peer handoffs. Resilient but hard to debug.
3. **Hybrid:** Orchestrator plans, specialists execute independently.

**co-cli relevance:** For a personal CLI agent, a single agent with many tools is better than multi-agent until we hit clear scaling limits. Stay single-agent, invest in better tools.

---

## 4. Recommended Tools for co-cli Expansion

### Tier 1: Critical Gaps (every competitor has these)

| # | Tool | Description | Effort | Deps Addition |
|---|------|-------------|--------|---------------|
| **T1.1** | `web_search` | Search the web via Google Custom Search API or SerpAPI | Small | `search_client: Any \| None` |
| **T1.2** | `web_fetch` | Fetch and parse a URL, return markdown content | Small | None (uses httpx + markdownify) |
| **T1.3** | `save_memory` | Save a key-value note to persistent local storage | Small | `memory_path: Path` |
| **T1.4** | `recall_memory` | Retrieve previously saved memories by keyword | Small | Same as above |

**Rationale:** Web search + fetch are the #1 gap. Every single competitor (Claude Code, Gemini CLI, Codex, OpenClaw, Manus) has web access. Without these, Co can't answer questions about current events, look up documentation, or fetch URLs the user references. Memory is the #2 gap — Co forgets everything between sessions.

### Tier 2: High-Value Additions (differentiation + daily utility)

| # | Tool | Description | Effort | Deps Addition |
|---|------|-------------|--------|---------------|
| **T2.1** | `read_file` / `write_file` | Read/write local files (outside Obsidian vault) | Small | None (uses pathlib) |
| **T2.2** | `edit_file` | String-replace editing of local files | Small | None (uses pathlib) |
| **T2.3** | `manage_todos` | Create/update/list persistent task items | Medium | `todos_path: Path` |
| **T2.4** | `search_github` / `create_github_issue` | Search repos, read issues, create issues via GitHub API | Medium | `github_token: str \| None` |
| **T2.5** | `list_gmail` / `search_gmail` | Read/search inbox (complement existing `draft_email`) | Small | Uses existing `google_gmail` |

**Rationale:** File operations let Co work with any file, not just Obsidian notes. Todo management enables multi-step workflows. GitHub tools connect Co to the developer's primary collaboration platform. Gmail read completes the email story (currently write-only).

### Tier 3: Advanced Capabilities (future-looking)

| # | Tool | Description | Effort | Deps Addition |
|---|------|-------------|--------|---------------|
| **T3.1** | `create_calendar_event` | Write to Google Calendar (currently read-only) | Small | Uses existing `google_calendar` |
| **T3.2** | `schedule_task` | Cron-like scheduling for recurring tasks | Large | New scheduler subsystem |
| **T3.3** | `browser_action` | Headless browser automation (Playwright) | Large | `browser: Any \| None` |
| **T3.4** | MCP client support | Load external MCP servers as tool providers | Large | New MCP subsystem |
| **T3.5** | `summarize_url` | Fetch URL + LLM summarization in one tool | Medium | Combines web_fetch + LLM call |

---

## 5. Proposed Implementation Roadmap

### Batch 7: Web Intelligence (highest ROI)

```
co_cli/tools/web.py        → web_search, web_fetch
co_cli/deps.py             → + search_api_key: str | None
```

- `web_search(query: str, num_results: int = 5)` — Google Custom Search JSON API or SerpAPI
- `web_fetch(url: str)` — httpx GET → html2text/markdownify → return markdown
- Both are read-only, low-risk, no confirmation needed
- Config: `search_api_key` + `search_engine_id` in settings.json

### Batch 8: Persistent Memory

```
co_cli/tools/memory.py     → save_memory, recall_memory, list_memories
~/.local/share/co-cli/memory.json  (or SQLite)
```

- `save_memory(key: str, content: str)` — save a named memory to local JSON/SQLite
- `recall_memory(query: str)` — keyword search across saved memories
- `list_memories()` — show all saved memory keys
- Memories stored in XDG data dir, persist across sessions
- Low-risk, no confirmation needed

### Batch 9: Local File Operations

```
co_cli/tools/files.py      → read_file, write_file, edit_file, list_directory
```

- `read_file(path: str)` — read any local file (with path traversal protection relative to CWD)
- `write_file(path: str, content: str)` — write/create file (confirmation required)
- `edit_file(path: str, old: str, new: str)` — string-replace edit (confirmation required)
- `list_directory(path: str)` — list directory contents
- Path safety: resolve paths, reject anything outside CWD or allowed directories

### Batch 10: Todo/Task Management

```
co_cli/tools/todos.py      → add_todo, list_todos, update_todo, delete_todo
~/.local/share/co-cli/todos.json
```

- Simple persistent task list with status tracking (pending/in_progress/done)
- Enables Co to decompose complex requests into trackable steps
- Low-risk reads, confirmation for deletes

### Batch 11: GitHub Integration

```
co_cli/tools/github.py     → search_github, list_issues, create_issue, list_prs
co_cli/deps.py             → + github_token: str | None
```

- Uses PyGithub or plain httpx + GitHub REST API
- Read operations: no confirmation. Write operations (create issue): confirmation required.

### Batch 12: Gmail Read + Calendar Write

```
co_cli/tools/gmail.py      → + list_emails, search_emails, read_email
co_cli/tools/calendar.py   → + create_calendar_event
```

- Complete the Google suite — currently Gmail is write-only and Calendar is read-only
- Uses existing `google_gmail` and `google_calendar` deps

### Future: MCP Client Support

Would require a new subsystem to:
1. Parse MCP server configs from `~/.config/co-cli/mcp.json`
2. Launch STDIO/HTTP MCP servers at startup
3. Dynamically register their tools with the pydantic-ai agent
4. This is a large architectural change but would make co-cli infinitely extensible

---

## 6. Priority Ranking (Impact vs. Effort)

```
              High Impact
                  │
    Batch 7       │     Batch 11
    (Web)         │     (GitHub)
                  │
    Batch 8       │     Batch 12
    (Memory)      │     (Gmail+Cal)
                  │
──────────────────┼──────────────────
                  │     Batch 10
    Batch 9       │     (Todos)
    (Files)       │
                  │     MCP Client
                  │
              Low Impact
   Low Effort         High Effort
```

**Recommended order:** 7 → 8 → 9 → 12 → 10 → 11 → MCP

---

## 7. Key Architectural Decisions

### 7.1 Web Search: Which API?

| Option | Pros | Cons |
|--------|------|------|
| Google Custom Search JSON API | Same Google ecosystem, 100 free queries/day | Requires CSE setup |
| SerpAPI | Simple, reliable, rich results | Paid ($50/mo) |
| DuckDuckGo (ddg library) | Free, no API key | Unreliable, scraping-based |
| Brave Search API | 2000 free queries/mo, good quality | Another API key |

**Recommendation:** Google Custom Search (aligns with existing Google auth) with Brave Search as fallback.

### 7.2 Memory: JSON vs. SQLite

| Option | Pros | Cons |
|--------|------|------|
| JSON file | Simple, human-readable, easy to edit | No search indexing, scales poorly |
| SQLite | Already used for OTel, FTS5 for search | Heavier, less human-readable |
| Graphiti (Neo4j) | Temporal knowledge graph, industry-leading | Massive dependency, overkill for v1 |

**Recommendation:** Start with JSON (Batch 8), migrate to SQLite with FTS5 if search performance becomes an issue. Graphiti is aspirational for a v2 knowledge layer.

### 7.3 File Operations: Scope & Safety

All competitor agents (Claude Code, Gemini CLI, Codex) allow file operations within the project directory. Key safety patterns:

1. **CWD-scoped:** Only allow reads/writes within CWD (like our Obsidian vault_path pattern)
2. **Allowlist directories:** Configure additional allowed paths in settings.json
3. **Path resolution:** Always resolve to absolute path, reject `..` traversal
4. **Write confirmation:** All writes require human confirmation (unless auto_confirm)

---

## 8. Community Demand Signals — What People Actually Want

### Top 10 Most-Wanted Features (from GitHub discussions, Reddit, HN, product surveys)

| Rank | Feature | Status in co-cli |
|------|---------|-----------------|
| 1 | "Do this on the web for me" (browser automation) | Missing |
| 2 | "Remember that I..." (persistent memory) | Missing |
| 3 | "What should I do today?" (task triage + calendar) | Partial (calendar read-only) |
| 4 | "Search the web for..." (web search) | **Missing** |
| 5 | "Summarize this webpage/PDF" (web fetch + parse) | **Missing** |
| 6 | "Draft a response to..." (email/Slack reply) | Have email draft + Slack post |
| 7 | "Track my spending" (finance integration) | Missing |
| 8 | "Schedule this meeting" (calendar write) | Missing (read-only) |
| 9 | "Run this every morning" (cron/scheduled workflows) | Missing |
| 10 | "What did we discuss last time?" (memory recall) | Missing |

### Key Insight

co-cli's Google suite (Drive, Gmail, Calendar, Slack) is a **strength** that coding-focused agents don't have. But the fundamentals that ALL agents share (web search, memory, file ops) are missing. Filling these gaps first maximizes the value of the integrations we already built.

---

## 9. Sources

### Competitive Analysis
- [Claude Code Documentation](https://docs.anthropic.com/en/docs/claude-code)
- [Gemini CLI GitHub](https://github.com/google-gemini/gemini-cli)
- [Gemini CLI Built-in Tools](https://medium.com/google-cloud/gemini-cli-tutorial-series-part-4-built-in-tools-c591befa59ba)
- [OpenAI Codex CLI](https://developers.openai.com/codex/cli/)
- [OpenAI Codex Skills](https://developers.openai.com/codex/skills/)
- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [OpenClaw Tools Documentation](https://docs.openclaw.ai/tools)
- [Manus AI Architecture Analysis](https://gist.github.com/renschni/4fbc70b31bad8dd57f3370239dccd58f)
- [Manus Context Engineering Blog](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [Warp Agents 3.0](https://www.warp.dev/blog/agents-3-full-terminal-use-plan-code-review-integration)
- [Goose GitHub (Block)](https://github.com/block/goose)
- [Linux Foundation Agentic AI Foundation](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)
- [OpenCode GitHub](https://github.com/opencode-ai/opencode)
- [OpenCode Tools Docs](https://opencode.ai/docs/tools/)
- [GitHub Copilot CLI Enhanced (Jan 2026)](https://github.blog/changelog/2026-01-14-github-copilot-cli-enhanced-agents-context-management-and-new-ways-to-install/)
- [Aider](https://aider.chat/)

### Patterns & Architecture
- [Context Engineering Guide 2026](https://codeconductor.ai/blog/context-engineering/)
- [Context Engineering for Developers (Faros AI)](https://www.faros.ai/blog/context-engineering-for-developers)
- [Graphiti Knowledge Graph Memory](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/)
- [Graphiti GitHub](https://github.com/getzep/graphiti)
- [Agentic Terminal (InfoQ)](https://www.infoq.com/articles/agentic-terminal-cli-agents/)
- [Personal AI Infrastructure (Daniel Miessler)](https://danielmiessler.com/blog/personal-ai-infrastructure)

### Memory & Knowledge
- [Mem0 Paper (April 2025)](https://arxiv.org/abs/2504.19413)
- [Letta/MemGPT Memory Blocks](https://www.letta.com/blog/memory-blocks)
- [Graphiti GitHub (Zep AI)](https://github.com/getzep/graphiti)
- [Graphiti Knowledge Graph Memory (Neo4j)](https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/)

### MCP Ecosystem
- [awesome-mcp-servers (punkpeye)](https://github.com/punkpeye/awesome-mcp-servers)
- [awesome-mcp-servers (wong2)](https://github.com/wong2/awesome-mcp-servers)
- [Official MCP Servers](https://github.com/modelcontextprotocol/servers)
- [mcp-awesome.com](https://mcp-awesome.com/)
- [Top MCP Servers (Builder.io)](https://www.builder.io/blog/best-mcp-servers-2026)

### Market & Trends
- [AI Personal Assistants 2026 (Kairntech)](https://kairntech.com/blog/articles/ai-personal-assistants/)
- [AI Personal Assistants 2026 (Reclaim)](https://reclaim.ai/blog/ai-assistant-apps)
- [AI Agent Trends 2026 (Salesmate)](https://www.salesmate.io/blog/future-of-ai-agents/)
- [Top CLI Coding Agents 2025 (DEV)](https://dev.to/forgecode/top-10-open-source-cli-coding-agents-you-should-be-using-in-2025-with-links-244m)
- [Goose AI Agent (Block)](https://github.com/block/goose)
- [17x Error Trap in Multi-Agent Systems](https://towardsdatascience.com/why-your-multi-agent-system-is-failing-escaping-the-17x-error-trap-of-the-bag-of-agents/)
- [Personal AI Infrastructure (Daniel Miessler)](https://danielmiessler.com/blog/personal-ai-infrastructure)
