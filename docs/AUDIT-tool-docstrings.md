# AUDIT: Tool Docstring Quality

Assessment of all co-cli tool descriptions against the expanded template
(`docs/TODO-tool-docstring-template.md`) and patterns from reference systems
(Gemini CLI, Claude Code, Codex, Aider).

## Evaluation Standard

### 4 Dimensions

| Dim | Name | What it covers |
|-----|------|----------------|
| **D1** | What it does | One action sentence — verb + object + return shape |
| **D2** | What it returns | Key fields, format, how to present to user |
| **D3** | When/how to use | Cross-tool routing, when-to-use vs alternatives, pagination, use-case enumeration, scope boundaries, conditional behavior, fallback guidance |
| **D4** | Caveats | Limits, failure modes, silent failures, what NOT to do |

### Sub-Patterns under D3 (from updated template)

| Code | Sub-pattern | Source |
|------|-------------|--------|
| **D3a** | Cross-tool references | All systems |
| **D3b** | When-to-use vs alternatives | Gemini CLI glob, co-cli gmail |
| **D3c** | Pagination / looping as capability | co-cli drive (post-fix) |
| **D3d** | Use-case enumeration (DO/DO NOT) | Gemini CLI memory |
| **D3e** | Scope boundaries ("Does NOT...") | Claude Code skills |
| **D3f** | Conditional behavior | Gemini CLI edit |
| **D3g** | Fallback guidance | Gemini CLI memory, co-cli web_fetch |

### Anti-Patterns

| AP | Name | Rule |
|----|------|------|
| **AP1** | Passive pagination | Describe as capability, not user-driven |
| **AP2** | Missing cross-tool refs | Bidirectional: if A→B, B should mention A |
| **AP3** | Undocumented limits | Silent caps must be documented |
| **AP4** | `ctx` in Args | Never document framework-injected params |
| **AP5** | Over-documentation | Simple tools stay 1-4 lines, don't bloat |
| **AP6** | Vague scope | If tool creates but doesn't send, say so |

### Additional Checks (from updated template)

| Check | What to look for |
|-------|-----------------|
| **Emphasis** | CAPS reserved for safety-critical constraints only |
| **Param quality** | Type context + example value for every parameter |
| **Param interactions** | Document when one param changes another's behavior |
| **Response-embedded hints** | Paginated/truncated tools embed next-action in return value |

> "Provide extremely detailed descriptions. This is by far the most important
> factor in tool performance. Aim for at least 3-4 sentences, more if complex."
> — Anthropic official guidance

---

## Scorecard

| # | Tool | File | D1 | D2 | D3 | D4 | APs | Params | Grade | Notes |
|---|------|------|----|----|----|----|-----|--------|-------|-------|
| 1 | `run_shell_command` | shell.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | D3a routing to dedicated tools, D3b when-to-use examples |
| 2 | `save_memory` | memory.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | D3d use-case enumeration, D3e scope boundary (dedup note) |
| 3 | `recall_memory` | memory.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | D3a tri-directional routing, D3g fallback (broader keywords) |
| 4 | `list_memories` | memory.py | ✅ | ✅ | ✅ | — | ✅ | ✅ | **A-** | D3c pagination added, response-embedded hints |
| 5 | `search_notes` | obsidian.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | D3a tri-directional, D3e scope disambiguation, D4 whole-word caveat |
| 6 | `list_notes` | obsidian.py | ✅ | ✅ | ✅ | — | ✅ | ✅ | **A-** | D3c pagination added, response-embedded hints |
| 7 | `read_note` | obsidian.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A-** | D3a refs both producers, D4 path traversal + error guidance |
| 8 | `search_drive_files` | google_drive.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | D3c pagination as capability, D3f conditional (name OR fullText) |
| 9 | `read_drive_file` | google_drive.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A-** | D3f conditional (Workspace export vs raw download) |
| 10 | `list_emails` | google_gmail.py | ✅ | ✅ | ✅ | — | ✅ | ✅ | **B+** | Simple list — correct scaling |
| 11 | `search_emails` | google_gmail.py | ✅ | ✅ | ✅ | — | ✅ | ✅ | **A-** | D3d query syntax enumeration. D4 skip acceptable — no silent caps |
| 12 | `create_email_draft` | google_gmail.py | ✅ | ✅ | ✅ | — | ✅ | ✅ | **B+** | D3e scope boundary ("Does NOT send"). Simple write tool |
| 13 | `list_calendar_events` | google_calendar.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A-** | D3b cross-ref, D3f auto-pagination note, D4 primary-only |
| 14 | `search_calendar_events` | google_calendar.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A-** | D3b cross-ref, D4 days_back hint |
| 15 | `web_search` | web.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | D3a→web_fetch, AP3 clear (max 8 documented) |
| 16 | `web_fetch` | web.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | D3f conditional (HTML→md, JSON as-is), D3g curl fallback, 4 caveats |
| 17 | `load_personality` | context.py | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A-** | D3e "internalize, do not show to user", D3f axis conflict rules |

**Summary: 7 A, 8 A-, 2 B+. Zero C or below. All anti-patterns clear.**

---

## Cross-Tool Routing Map

The complete routing graph the agent can follow:

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

**Bidirectional routing verified:**
- recall_memory ↔ search_notes ↔ search_drive_files (tri-directional disambiguation)
- search_notes → read_note ← list_notes (both producers reference consumer)
- search_drive_files → read_drive_file (producer → consumer)
- web_search → web_fetch → run_shell_command (chain with curl fallback)
- list_emails ↔ search_emails (both route to each other)
- list_calendar_events ↔ search_calendar_events (both route to each other)
- run_shell_command → web_fetch, search_notes, search_drive_files (routes away to dedicated tools)

**No orphan tools.** Every tool participates in at least one routing chain.

---

## Template Coverage Matrix

New patterns from the updated template and where they appear:

| Pattern | Tools that use it |
|---------|------------------|
| **D3d Use-case enumeration** | save_memory (When to save / Do NOT), search_emails (query syntax) |
| **D3e Scope boundaries** | create_email_draft ("Does NOT send"), recall_memory (data source), search_notes (vault vs memories vs Drive), save_memory ("safe to call without checking"), load_personality ("internalize, do not show") |
| **D3f Conditional behavior** | read_drive_file (Workspace export vs raw), web_fetch (HTML→md vs JSON as-is), search_drive_files (name OR fullText), list/search_calendar (auto-pagination), load_personality (axis conflict resolution) |
| **D3g Fallback guidance** | web_fetch (curl via shell), recall_memory (try broader keywords), read_note (error lists available notes) |
| **Emphasis (CAPS)** | Not currently used — acceptable since no tools have safety-critical constraints requiring it |
| **Response-embedded hints** | search_drive_files ("More results available — request page N+1"), list_memories, list_notes ("More available — call with offset=N") |
| **Param interactions** | Not currently needed — no tools have params that change each other's behavior |

---

## Remaining Opportunities

These are not gaps — all tools pass. These are potential future improvements:

1. **Response-embedded hints**: Only search_drive_files embeds pagination hints
   in the return value. search_notes (which has has_more) could add the same
   pattern: "More results available — increase limit or narrow with folder/tag."

2. **Emphasis conventions**: No tools currently use CAPS emphasis. If we add an
   edit/write tool in the future (file creation, code modification), that would
   be the first candidate for MUST/NEVER constraints per the Gemini CLI edit
   pattern.

3. **Parameter interaction documentation**: No current tools have parameters
   that change each other's behavior. If we add such parameters, document
   the interaction per template guidance.
