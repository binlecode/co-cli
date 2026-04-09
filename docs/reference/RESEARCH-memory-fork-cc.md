# RESEARCH: fork-cc (Claude Code) Memory Architecture — Deep Scan

Source: `~/workspace_genai/fork-claude-code` (Anthropic Claude Code fork)
Scan date: 2026-04-06

---

## 1. File Format & Schema

**MEMORY.md index** (`memdir/memdir.ts:34–38`):
- `ENTRYPOINT_NAME = 'MEMORY.md'` (line 34)
- `MAX_ENTRYPOINT_LINES = 200` (line 35)
- `MAX_ENTRYPOINT_BYTES = 25_000` (line 38)
- Truncation via `truncateEntrypointContent()` (lines 57–103): line cap first (line 79), then byte cap (lines 82–84), warning appended if either fires (lines 96–97)
- Entry format: `- [Title](file.md) — one-line hook`, ~150 chars max (line 55 of `consolidationPrompt.ts`)

**Per-file frontmatter** (`memdir/memoryTypes.ts:261–271`):
- Required fields: `name`, `description`, `type`
- Parsed via `parseFrontmatter()` (imported in `memdir/memoryScan.ts:9`)

**Memory types** (`memdir/memoryTypes.ts:14–21`):
- Four types: `user`, `feedback`, `project`, `reference` (lines 14–19)
- `MemoryType` type at line 21
- `parseMemoryType()` at lines 28–31 — returns `undefined` for invalid types

**File organization**:
- Semantic by topic, not chronological (`memdir.ts:214, 231`)
- Individual `.md` files per memory (`memdir.ts:209`)
- No per-file size limit; only MEMORY.md index has caps

---

## 2. Scoping & Directory Structure

**Three scopes** (`tools/AgentTool/agentMemory.ts:12–13`):

```
getAgentMemoryDir(agentType, scope)              agentMemory.ts:52–65
  'project' → <cwd>/.claude/agent-memory/<agentType>/        line 59
  'local'   → getLocalAgentMemoryDir(dirName)                line 61
  'user'    → <memoryBase>/agent-memory/<dirName>/            line 63
```

**Local scope override** (`agentMemory.ts:29–44`):
- If `CLAUDE_CODE_REMOTE_MEMORY_DIR` set:
  `${CLAUDE_CODE_REMOTE_MEMORY_DIR}/projects/<sanitized-git-root>/agent-memory-local/<agentType>/` (lines 33–39)
- Fallback: `<cwd>/.claude/agent-memory-local/<dirName>` (line 43)

**Auto-memory directory** (separate hierarchy, `memdir/paths.ts:223–235`):
```
getAutoMemPath()                                  paths.ts:223–235 (memoized)
  1. CLAUDE_COWORK_MEMORY_PATH_OVERRIDE env var   lines 225–226
  2. autoMemoryDirectory in settings.json          lines 225–226
  3. <memoryBase>/projects/<sanitized-git-root>/memory/   line 231
```

**Memory base** (`paths.ts:85–90`):
```
getMemoryBaseDir()                                paths.ts:85–90
  1. CLAUDE_CODE_REMOTE_MEMORY_DIR env var        line 86
  2. fallback: getClaudeConfigHomeDir() (~/.claude/)   line 89
```

**Agent type sanitization** (`agentMemory.ts:20–22`):
- `sanitizeAgentTypeForPath()` replaces colons with dashes

**Path validation** (`paths.ts:109–150`):
- `validateMemoryPath()` rejects relative, root-near, UNC, null-byte paths

**No cross-scope visibility** — each scope has an isolated directory. Agent memory and auto-memory are separate hierarchies.

---

## 3. Memory Read/Write Operations

### Read path

**System prompt injection** (`memdir/memdir.ts:272–305`):
```
session start
 → loadAgentMemoryPrompt()                agentMemory.ts:138–177
     → ensureMemoryDirExists()            line 165 (fire-and-forget)
     → buildMemoryPrompt()               memdir.ts:272–305
         → reads MEMORY.md synchronously  line 296
         → truncateEntrypointContent()    lines 57–103
         → returns prompt with index content
```

**Per-turn relevance loading** (`utils/attachments.ts:2361–2419`):
```
model streaming
 → startRelevantMemoryPrefetch()          attachments.ts:2361–2419
     → gate: tengu_moth_copse flag        line 2367
     → extract last user message          line 2372
     → findRelevantMemories()             findRelevantMemories.ts:39–75
         → scan MEMORY.md headers         line 46
         → selectRelevantMemories()       line 77 (Sonnet selector)
         → returns up to 5 files          line 20
     → wrap as attachments with staleness warning
       memoryFreshnessText()              memoryAge.ts:33–41
```

### Write path

**Direct write** (main agent):
```
agent decides to save
 → FileWrite/FileEdit tool
     → path gated by isAutoMemPath()      paths.ts:274–278
     → writes topic file with frontmatter
     → updates MEMORY.md index
```

**Background extraction** (`services/extractMemories/extractMemories.ts`):
```
end of query loop (no tool calls remain)  lines 160–170
 → skip if main agent already wrote       lines 348–359
 → runForkedAgent()
     → createAutoMemCanUseTool()          lines 171–222
       allowed: FileRead, Grep, Glob unrestricted
       allowed: read-only Bash (ls, find, grep, cat, stat, wc, head, tail)
       allowed: FileEdit/FileWrite only in auto-memory paths
       denied: all other tools
     → manifest pre-injected              prompts.ts:32 ("check list before writing")
     → cursor advanced after success      lines 432–434
```

### Update/Delete

- Direct file edit via FileEditTool (constrained to auto-memory paths)
- No specialized delete tool; deletion via empty content write
- No interactive approval for memory writes

---

## 4. Snapshot System

**Snapshot lifecycle** (`tools/AgentTool/agentMemorySnapshot.ts:31–198`):

```
Snapshot paths:
  snapshot dir:  <cwd>/.claude/agent-memory-snapshots/<agentType>/
  snapshot.json: updatedAt (string)               snapshotMetaSchema line 16
  .snapshot-synced.json: syncedFrom (string)       syncedMetaSchema line 22
```

**Check flow** (`checkAgentMemorySnapshot()` lines 98–144):
```
 → snapshot exists?
     no  → return { action: 'none' }                 line 111
     yes → local memory exists?
         no  → return { action: 'initialize' }       line 125
         yes → snapshot newer than synced?
             no  → return { action: 'none' }
             yes → return { action: 'prompt-update' }  lines 137–139
```

**Operations**:
- `initializeFromSnapshot()` (lines 149–159): copy `.md` files from snapshot → local, save sync metadata
- `replaceFromSnapshot()` (lines 164–186): delete local `.md` files, copy snapshot, save sync
- `markSnapshotSynced()` (lines 191–197): update sync timestamp without modifying local

---

## 5. autoDream Consolidation — Full Pipeline

### Gate sequence

```
autoDream runner                                   autoDream.ts:125–272

 → isGateOpen()                                    lines 95–100
     getKairosActive() (assistant mode → skip)     line 96
     getIsRemoteMode() (remote → skip)             line 97
     isAutoMemoryEnabled()                         line 98
     isAutoDreamEnabled()                          line 99
       → settings.json autoDreamEnabled             config.ts:14–15
       → OR GrowthBook tengu_onyx_plover.enabled   config.ts:16–20

 → Gate 1: time ≥ minHours?                        lines 130–141
     readLastConsolidatedAt() → lock file mtime    consolidationLock.ts:29–36
     minHours default 24, from tengu_onyx_plover   autoDream.ts:63–65, 80–85

 → Gate 2: scan throttle (10min)?                  lines 143–150
     SESSION_SCAN_INTERVAL_MS = 10 * 60 * 1000     line 56

 → Gate 3: sessions ≥ minSessions?                 lines 153–171
     listSessionsTouchedSince(lastAt)              consolidationLock.ts:118–124
     excludes current session                      autoDream.ts:164–165
     minSessions default 5, from tengu_onyx_plover autoDream.ts:63–65, 86–92

 → Gate 4: tryAcquireConsolidationLock()           consolidationLock.ts:46–84
     lock file: .consolidate-lock (PID body)       line 22
     stale if dead PID or mtime > 1h               lines 60–68
     race detection: write PID, re-read to confirm lines 72, 76–81
```

### Consolidation execution

```
 → buildConsolidationPrompt(memRoot, txDir)        consolidationPrompt.ts:10–65
 → runForkedAgent()                                autoDream.ts:224–233
     tool permissions: createAutoMemCanUseTool()   line 227
     skipTranscript: true                          line 230
```

### Post-consolidation

```
 → success:
     complete dream task                           line 235
     if files touched → append memorySavedMessage  lines 238–247
       verb: 'Improved'

 → error:
     if aborted → no-op                           lines 262–264
     otherwise → failDreamTask()                   line 268
              → rollbackConsolidationLock(prior)    line 270
                  prior=0 → unlink lock            consolidationLock.ts:96–98
                  prior>0 → rewind mtime           lines 100–102
```

---

## 6. Consolidation Prompt — Four Phases

Source: `services/autoDream/consolidationPrompt.ts:10–65`

**Phase 1 — Orient** (lines 26–31):
- `ls` memory directory to see existing files
- Read `MEMORY.md` to understand current index
- Skim existing topic files to avoid duplicates
- Review `logs/` or `sessions/` subdirectories if present

**Phase 2 — Gather signal** (lines 33–42):
- Priority 1: daily logs (`logs/YYYY/MM/YYYY-MM-DD.md`)
- Priority 2: existing memories that drifted (contradictions)
- Priority 3: transcript search via grep (narrow terms, not exhaustive)
- Example: `grep -rn "<term>" ${transcriptDir}/ --include="*.jsonl" | tail -50` (line 40)

**Phase 3 — Consolidate** (lines 44–52):
- Write or update memory files at top level of memory directory
- Merge into existing files vs. creating duplicates (line 49)
- Convert relative dates to absolute dates (line 50)
- Delete contradicted facts (line 51)

**Phase 4 — Prune & index** (lines 54–61):
- Update `MEMORY.md` under 200 lines AND 25KB (line 55)
- Each entry: `- [Title](file.md) — one-line hook`, ~150 chars (line 55)
- Remove stale/wrong/superseded pointers (line 57)
- Demote verbose entries >200 chars (line 58)
- Add new important pointers (line 59)
- Resolve contradictions (line 60)

**Subagent context** (`autoDream.ts:211–222`):
- Tool constraints note (lines 216–218): read-only bash only
- Session IDs being reviewed (lines 220–221)
- Memory directory path (line 211)
- Transcript directory path (line 212)

---

## 7. Memory Loading into Context

**buildMemoryLines()** (`memdir.ts:199–266`) produces system prompt guidance:
- H1 heading with display name
- Intro paragraph (lines 239–242)
- Memory types taxonomy (line 245)
- What NOT to save (line 246)
- How to save memories (line 248)
- When to access memories (line 250)
- Trusting recall (line 252)
- Memory vs. other persistence (lines 254–257)
- Extra guidelines if provided (line 259)
- Searching past context (line 263)

**buildMemoryPrompt()** (`memdir.ts:272–305`):
- Reads MEMORY.md synchronously (line 296)
- Appends truncated content after guidance lines

---

## 8. Deduplication

No structural dedup infrastructure. Three model-guidance layers:
1. **autoDream Phase 3** (`consolidationPrompt.ts:49`): "merge into existing topic files rather than creating near-duplicates"
2. **extractMemories prompt** (`services/extractMemories/prompts.ts:32, 64–65, 80–81`): manifest pre-injected, "check list before writing"
3. **System prompt** (`memdir.ts:232–233`): "Do not write duplicate memories. First check if there is an existing memory you can update"

---

## 9. Configuration & Feature Flags

### Environment Variables

| Variable | Purpose | Source |
|----------|---------|--------|
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY` | Kill all memory features | `paths.ts:31–36` |
| `CLAUDE_CODE_SIMPLE` | `--bare` mode, disables memory | `paths.ts:41–42` |
| `CLAUDE_CODE_REMOTE` | Remote execution mode | `paths.ts:44–49` |
| `CLAUDE_CODE_REMOTE_MEMORY_DIR` | Mount path for memory override | `paths.ts:86`, `agentMemory.ts:30` |
| `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` | Full auto-memory path override | `paths.ts:161–166` |
| `CLAUDE_COWORK_MEMORY_EXTRA_GUIDELINES` | Additional memory guidance | `agentMemory.ts:168` |

### GrowthBook Feature Flags

| Flag | Controls | Type |
|------|----------|------|
| `tengu_onyx_plover` | autoDream enabled + config (`minHours`, `minSessions`) | object |
| `tengu_passport_quail` | Extract memories enabled | bool |
| `tengu_moth_copse` | Relevant memory prefetch enabled | bool |
| `tengu_slate_thimble` | Extract in non-interactive sessions | bool |
| `tengu_bramble_lintel` | Extraction turn throttle (default 1 = every turn) | number |

### settings.json Keys

| Key | Type | Purpose |
|-----|------|---------|
| `autoMemoryEnabled` | bool | Global memory on/off |
| `autoDreamEnabled` | bool | Consolidation on/off (overrides GrowthBook) |
| `autoMemoryDirectory` | string | Custom memory root |

### Auto-memory enable chain (`paths.ts:30–55`)

```
isAutoMemoryEnabled()
 1. CLAUDE_CODE_DISABLE_AUTO_MEMORY=1/true → OFF     lines 31–36
 2. CLAUDE_CODE_SIMPLE (--bare) → OFF                 lines 41–42
 3. Remote without CLAUDE_CODE_REMOTE_MEMORY_DIR → OFF lines 44–49
 4. autoMemoryEnabled setting → value                 lines 50–52
 5. default → ON                                      line 54
```

---

## 10. Gap Analysis: fork-cc vs co-cli

### fork-cc has, co-cli does not

| Gap | fork-cc | co-cli status | Severity |
|-----|---------|---------------|----------|
| **Relevance-based memory loading** | Sonnet selector picks up to 5 files per turn based on user message relevance (`findRelevantMemories.ts:39–75`). Prefetched non-blocking during model streaming (`attachments.ts:2361–2419`) | co-cli injects by `always_on` flag (up to 5) + FTS5/BM25 recall (max 3) based on keyword match (`_history.py:525–583`). No LLM-based relevance selection | **Medium** — co-cli's FTS recall is keyword-driven; fork-cc's Sonnet selector is semantic. Trade-off: co-cli avoids extra LLM call per turn |
| **Background memory extraction** | `extractMemories` runs at end of query loop as forked agent when main agent didn't write. Manifest-aware, cursor-tracked, throttle-gated (`extractMemories.ts:160–170, 348–359`) | co-cli's auto-signal extraction (`_extractor.py:141–225`) runs post-turn for high-confidence tag matches, fire-and-forget. No manifest awareness, no cursor tracking | **Low** — co-cli's extraction is simpler but functional. fork-cc's is more sophisticated (manifest dedup, cursor, trailing runs) |
| **Three-tier scoping** | User (cross-project), project (VCS-tracked), local (not VCS). Cross-project knowledge persists in user scope (`agentMemory.ts:52–65`) | Two-tier: project-local memory + user-scope articles. No project-VCS-tracked memory scope | **Low** — co-cli's articles serve the cross-project role. Missing: VCS-tracked project memory that travels with the repo |
| **Snapshot sync** | Snapshot system for team memory sharing: `snapshot.json` + `.snapshot-synced.json`, initialize/prompt-update flow (`agentMemorySnapshot.ts:98–144`) | No snapshot or team-sync mechanism | **Low** — relevant for team workflows; co-cli is single-user |
| **Memory staleness warnings** | Memories >1 day old get freshness caveat in context (`memoryAge.ts:33–41`) | No staleness annotation on recalled memories | **Low** — useful signal for model to weight recent vs stale memories |
| **Feature-gated rollout** | All memory features controlled by GrowthBook flags (5 flags), progressive rollout, per-field validation for stale cache | co-cli features are always-on or config-gated. No feature flag infrastructure | **Not applicable** — co-cli is not a SaaS product; config is sufficient |

### co-cli has, fork-cc does not

| Advantage | co-cli | fork-cc status |
|-----------|--------|----------------|
| **Structured search index** | FTS5/BM25 hybrid index in SQLite (`KnowledgeStore` at `_store.py:213`), temporal decay scoring (0.6×relevance + 0.4×decay, `memory.py:457–462`), one-hop `related` traversal | No search index. Grep-based search during consolidation only. Model reads files directly |
| **Write-time dedup** | Agent-based upsert via `check_and_save()` (`_save.py:64–100`) — compares candidate against manifest of existing memories before write | No write-time dedup. Model-guided only ("check list before writing") |
| **Typed frontmatter with validation** | 14 validated fields (`_frontmatter.py:103–257`) including `provenance`, `certainty`, `auto_category`, `decay_protected`, `always_on`, `related` | 3 fields (`name`, `description`, `type`). No provenance, certainty, decay protection, or relation tracking |
| **Capacity-based retention** | `enforce_retention()` (`_retention.py:16–52`) — automatic pruning when total > `memory_max_count` (default 200). `decay_protected` entries exempt | No retention mechanism. autoDream prunes MEMORY.md index but no automatic file deletion based on capacity |
| **Cross-source knowledge search** | `search_knowledge` (`articles.py:161–309`) searches across memory, articles, Obsidian, Google Drive. `_detect_contradictions()` across sources | Single-source only. No cross-source search or contradiction detection |
| **Explicit memory update tools** | `update_memory` (str_replace, `memory.py:830`) and `append_memory` (`memory.py:943`) with guards (unique match, line-prefix rejection) | No dedicated update tools. Model uses generic FileEdit |
