# TODO: Tool Docstring Template

**Purpose:** Standard template and checklist for all co-cli tool docstrings.
Enforces LLM tool-selection reliability — especially important before sub-agent delegation multiplies tool usage.

**Status:** All 17 existing tools audited and pass — routing map and pattern coverage reference below.
Remaining work: three improvement opportunities from the audit, plus applying this template to all new tools added in future TODOs.

---

## Template Standard

### 4 Dimensions

Every tool docstring must address these four dimensions:

| Dim | Name | What it covers |
|-----|------|----------------|
| **D1** | What it does | One action sentence — verb + object + return shape |
| **D2** | What it returns | Key fields, format, how to present to user |
| **D3** | When/how to use | Cross-tool routing, alternatives, pagination, use-case enumeration, scope boundaries, conditional behavior, fallback guidance |
| **D4** | Caveats | Limits, failure modes, silent failures, what NOT to do |

D1 and D2 are required for every tool. D3 and D4 are required unless there is genuinely nothing to say (simple tools with no alternatives, no limits, no failure modes).

### D3 Sub-Patterns

Apply relevant sub-patterns to D3. Not all sub-patterns apply to every tool.

| Code | Sub-pattern | When to apply |
|------|-------------|---------------|
| **D3a** | Cross-tool references | Any tool that routes to or from another tool — reference it by name |
| **D3b** | When-to-use vs alternatives | When two+ tools solve similar problems — disambiguate use cases |
| **D3c** | Pagination / looping as capability | Any tool that returns paginated or truncated results |
| **D3d** | Use-case enumeration | When correct use cases are non-obvious — explicit DO / DO NOT list |
| **D3e** | Scope boundaries | When the tool does NOT do something users might expect |
| **D3f** | Conditional behavior | When behavior changes based on input type or state |
| **D3g** | Fallback guidance | When the tool can fail silently or partially — what to try instead |

### Anti-Patterns

| AP | Name | Rule |
|----|------|------|
| **AP1** | Passive pagination | Describe pagination as agent capability, not user-driven |
| **AP2** | Missing cross-tool refs | Routing is bidirectional: if A mentions B, B should mention A |
| **AP3** | Undocumented limits | Silent result caps must be documented in D3 or D4 |
| **AP4** | `ctx` in Args | Never document framework-injected params (RunContext, CoDeps) |
| **AP5** | Over-documentation | Simple tools stay 1–4 lines; don't add dimensions that don't apply |
| **AP6** | Vague scope | If a tool creates but doesn't send, say so explicitly (D3e) |

### Additional Checks

| Check | Guidance |
|-------|----------|
| **Emphasis** | CAPS reserved for safety-critical constraints only (write/delete tools). No CAPS for routine notes |
| **Param quality** | Each parameter description: type context + example value |
| **Param interactions** | If one param changes another's behavior, document it |
| **Response-embedded hints** | Paginated/truncated tools embed next-action hints in the return `display` field |

> "Provide extremely detailed descriptions. This is by far the most important factor in tool performance.
> Aim for at least 3-4 sentences, more if complex." — Anthropic official guidance

---

## Cross-Tool Routing Map

The complete routing graph the agent can follow — every tool must participate in at least one chain:

```
User says "find X"
  ├── in memories (preferences, decisions)  → recall_memory
  ├── in Obsidian notes (personal notes)    → search_notes → read_note
  ├── in Google Drive (cloud docs)          → search_drive_files → read_drive_file
  ├── in Gmail (emails)                     → search_emails
  ├── in Calendar (events)                  → search_calendar_events
  ├── on the web                            → web_search → web_fetch
  └── on the filesystem / other             → run_shell_command
```

**Bidirectional routing verified (existing tools):**
- recall_memory ↔ search_notes ↔ search_drive_files (tri-directional disambiguation)
- search_notes → read_note ← list_notes (both producers reference consumer)
- search_drive_files → read_drive_file (producer → consumer)
- web_search → web_fetch → run_shell_command (chain with curl fallback)
- list_emails ↔ search_emails
- list_calendar_events ↔ search_calendar_events
- run_shell_command → web_fetch, search_notes, search_drive_files (routes away to dedicated tools)

New tools must be wired into this graph — update bidirectional refs in both directions when adding a tool.

---

## Pattern Coverage Reference

Where each D3 sub-pattern currently appears in existing tools — use as examples when writing new docstrings:

| Pattern | Example tools |
|---------|--------------|
| **D3d Use-case enumeration** | `save_memory` (When to save / Do NOT), `search_emails` (query syntax) |
| **D3e Scope boundaries** | `create_email_draft` ("Does NOT send"), `recall_memory` (data source), `search_notes` (vault vs memories vs Drive), `save_memory` ("safe to call without checking"), `load_personality` ("internalize, do not show") |
| **D3f Conditional behavior** | `read_drive_file` (Workspace export vs raw), `web_fetch` (HTML→md vs JSON as-is), `search_drive_files` (name OR fullText), `list/search_calendar` (auto-pagination), `load_personality` (axis conflict resolution) |
| **D3g Fallback guidance** | `web_fetch` (curl via shell), `recall_memory` (try broader keywords), `read_note` (error lists available notes) |
| **Response-embedded hints** | `search_drive_files` ("More results available — request page N+1"), `list_memories`, `list_notes` ("More available — call with offset=N") |
| **Emphasis (CAPS)** | Not yet used — first candidate: any future file-write or edit tool |
| **Param interactions** | Not yet used — document if a param changes another's behavior |

---

## Remaining Opportunities (from audit)

Not gaps — improvements to make when tools are next touched:

- [ ] **`search_notes` response-embedded hints**: `has_more` is returned but `display` doesn't embed
      "More results available — increase limit or narrow with folder/tag." Add the same pattern as
      `search_drive_files` when this tool is next modified.
- [ ] **Emphasis conventions**: No existing tools use CAPS. First candidate: any future file-write or
      edit tool should add MUST/NEVER constraints per the Gemini CLI edit pattern.
- [ ] **Parameter interaction documentation**: No current tools have params that change each other's
      behavior. Document if added to any tool.

---

## New Tools Requiring Template Application

New tools planned in other TODOs that must follow this template when implemented:

| Tool | TODO | Key dimensions needed |
|------|------|-----------------------|
| `save_article` | FTS TODO Prereq B | D3d (article vs memory — when to use each), D3e (writes only; dedup by origin_url, not content), D4 (URL dedup: same origin_url → consolidated) |
| `recall_article` | FTS TODO Prereq B | D3a (↔ `recall_memory`, → `read_article_detail`), D3b (article vs memory distinction), D3c (summary-only; use `read_article_detail` for full body) |
| `read_article_detail` | FTS TODO Prereq B | D3a (← `recall_article` — always call this after), D3e (full body on demand — does NOT summarize), D3f (slug input: from recall_article result) |
| `list_memories(kind=)` | FTS TODO Prereq B | D3c (pagination), D3f (kind= filter changes result set: "memory", "article", or all) |
| `search_knowledge` | FTS TODO Phase 1 | D3a (cross-source: memories + articles + notes + drive), D3b (vs `recall_memory`/`recall_article` — use when source is unknown), D3g (falls back to grep if FTS unavailable) |

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/*.py` | Apply template to new tools as they are added |
