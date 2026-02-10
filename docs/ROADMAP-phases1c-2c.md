# Co Evolution Roadmap: Phases 1c-2c

**Status**: Documentation Complete ✅ | Ready for Implementation ⏳

This document tracks the comprehensive evolution plan from tool executor to personal companion (Phases 1c through 2c).

---

## Phase Status Overview

| Phase | Name | Status | Effort | Documentation | Priority |
|-------|------|--------|--------|---------------|----------|
| **1a** | Model Conditionals | ✅ COMPLETE | - | DESIGN-co-evolution.md | - |
| **1b** | Personality Templates | ✅ COMPLETE | - | DESIGN-co-evolution.md | - |
| **1c** | Internal Knowledge | 📝 DOCUMENTED | 8-10h | TODO-prompt-system-phase1c.md | HIGH |
| **1d** | Prompt Improvements | 📝 DOCUMENTED | 3-4h | TODO-prompt-system-phase1d.md | QUICK WIN |
| **1e** | Portable Identity | 📝 DOCUMENTED | 9h | TODO-prompt-system-phase1e.md | MEDIUM |
| **2a** | MCP Client (stdio) | 📝 DOCUMENTED | 6-8h | TODO-mcp-client-IMPL.md | HIGH |
| **2b** | User Preferences | 📝 DOCUMENTED | 10-12h | TODO-prompt-system-phase2b.md | MEDIUM |
| **2c** | Background Execution | 📝 DOCUMENTED | 10-12h | TODO-background-execution.md | MEDIUM |

**Total Documented Work**: 46-55 hours (6-7 days)

---

## Documentation Summary

### Phase 1c: Internal Knowledge System
**File**: `docs/TODO-prompt-system-phase1c.md` (2,050 lines) ✅

**Goal**: Load co's learned context from `.co-cli/internal/context.json` - facts about user, project patterns, learned preferences that persist across sessions.

**Key Features**:
- Internal knowledge schema with 10KB budget (20KB hard limit)
- Three memory tools: `save_memory`, `recall_memory`, `list_memories`
- Auto-loaded at session start
- Clear boundary: automatic context vs explicit tool calls
- 18 comprehensive tests

**Success Criteria**: 20+ checkboxes across code, tests, behavior, quality

---

### Phase 1d: Prompt Improvements (Peer Learnings)
**File**: `docs/TODO-prompt-system-phase1d.md` (1,500 lines) ✅

**Goal**: Apply 5 high-impact techniques from peer system analysis to improve system.md without adding complexity.

**Techniques**:
1. **System Reminder** (Aider pattern) - Recency bias exploitation
2. **Escape Hatches** (Codex pattern) - Prevent stuck states
3. **Contrast Examples** (Codex pattern) - Good vs bad responses
4. **Model Quirk Counter-Steering** (Aider pattern) - Database-driven
5. **Commentary in Examples** (Claude Code pattern) - Teach principles

**Impact**: +15-25% Inquiry/Directive compliance, -60% stuck states, -70% model-specific issues

**Success Criteria**: 5 new tests, behavioral validation, improvement metrics

---

### Phase 2a: MCP Client Integration
**File**: `docs/TODO-mcp-client-IMPL.md` (1,850 lines) ✅

**Goal**: Integrate MCP servers as external tool sources via stdio transport.

**Key Features**:
- Config schema: `mcp_servers` in settings.json
- Dynamic tool discovery via MCP protocol
- Async lifecycle management (`async with agent`)
- Automatic tool name collision handling (prefixing)
- Approval inheritance (MCP tools = native tools)
- 13+ functional tests

**Success Criteria**: 25 checkboxes across functional, approval, config, status, testing, docs

---

### Phase 2b: User Preferences System
**Files**:
- `docs/RESEARCH-user-preferences.md` (805 lines) ✅
- `docs/TODO-prompt-system-phase2b.md` (2,000 lines) ✅

**Goal**: Workflow preferences that adapt co's behavior to user's work style, separate from personality.

**Research Findings**:
- Analyzed 4 peer systems (Codex, Gemini CLI, Claude Code, Aider)
- 10 MVP preferences identified (approval, sandbox, output, UI, telemetry, updates)
- JSON with comments storage pattern
- Hierarchical precedence model

**Key Features**:
- `UserPreferences` dataclass with 10 core preferences
- Conflict resolution: command > preference > personality > base
- Runtime overrides: `/verbose`, `/terse`, `/explain`, `/cautious`, `/yolo`
- Progressive disclosure (only show non-default)
- 15+ comprehensive tests

**Success Criteria**: 31 checkboxes (code, test, behavioral, quality)

---

### Phase 2c: Background Execution
**File**: `docs/TODO-background-execution.md` (1,900 lines) ✅

**Goal**: Long-running tasks run in background without blocking chat. User can start, check status, cancel, and view results asynchronously.

**Use Cases**:
- Long test runs (5+ minutes)
- Large file processing (hundreds of files)
- Research tasks (codebase analysis)
- Batch operations (mass file updates)

**Key Features**:
- Task lifecycle: pending → running → completed/failed/cancelled
- Storage: `.co-cli/tasks/{task-id}.json` (metadata) + `.co-cli/tasks/{task-id}.log` (output)
- Slash commands: `/background`, `/tasks`, `/status`, `/cancel`
- Three agent tools: `start_background_task`, `check_task_status`, `cancel_task`
- Approval inheritance (pre-execution gate, no mid-execution prompts)
- OTel integration (trace linking)
- 25+ functional tests

**Success Criteria**: Phase-specific completion gates for storage, execution, commands, tools, integration

---

## Implementation Sequence

### Recommended Order

1. **Phase 1d** (3-4 hours) - QUICK WIN
   - Apply peer learnings immediately
   - Low risk, high impact
   - No dependencies

2. **Phase 1c** (8-10 hours) - FOUNDATIONAL
   - Internal knowledge foundational for companion vision
   - Memory tools enable learning
   - Symlink pattern for basic portability
   - No dependencies

3. **Phase 1e** (9 hours) - PORTABILITY ENHANCEMENT
   - Identity separation (portable vs machine-local)
   - Export/import commands
   - Depends on 1c completion
   - Can be deferred if needed

4. **Phase 2a** (6-8 hours) - ECOSYSTEM ENABLER
   - MCP extensibility unlocks tool ecosystem
   - Independent implementation
   - Can run parallel with 1c/1d/1e

5. **Phase 2b** (10-12 hours) - PERSONALIZATION
   - Research complete, ready to implement
   - Depends on 1c for learned preferences
   - Integrates with personality system (1b)

6. **Phase 2c** (10-12 hours) - ADVANCED UX
   - Most complex, goes last
   - Benefits from 2a completion (MCP tools in background)
   - Independent core implementation

### Parallel Workstreams

If multiple implementers available:

- **Stream A**: Phase 1d + 1c (prompt system improvements)
- **Stream B**: Phase 2a (MCP client)
- **Stream C**: Phase 2b research → implementation
- **Stream D**: Phase 2c (after Stream B complete)

---

## Unified Prompt Assembly Order

After all phases complete, system prompt will assemble as:

```
1. Base system.md (identity, principles, tool guidance)
2. Model conditionals ([IF gemini] / [IF ollama])          ← Phase 1a ✓
3. Model quirk counter-steering (if model_name provided)   ← Phase 1d
4. Personality template (if specified)                      ← Phase 1b ✓
5. Internal knowledge (.co-cli/internal/context.json)       ← Phase 1c
6. User preferences (computed from settings)                ← Phase 2b
7. Project instructions (.co-cli/instructions.md)           ← Phase 1a ✓
```

Total context budget: ~15-20KB (manageable within LLM context window)

---

## Success Metrics

### Technical Metrics

- **Test Coverage**: >90% for all new code
- **Performance**: <100ms overhead per phase at session start
- **Memory**: <10KB per phase (internal knowledge, preferences)
- **Reliability**: No regressions in existing test suite

### Behavioral Metrics

- **Companion Behavior**: Co remembers user context across sessions
- **Adaptive Communication**: Personality + preferences work together without conflict
- **Extensible Tooling**: MCP servers integrate seamlessly with native tools
- **Async Capable**: Long tasks run in background without blocking interaction

### Quality Metrics

- **Peer Parity**: System prompt quality matches/exceeds Codex, Gemini CLI, Claude Code, Aider
- **Testability**: All features have functional tests, no mocks
- **Maintainability**: Clear documentation, explicit over implicit, simple over complex

---

## Risk Assessment

### Phase 1c Risks
- **Context size bloat** → Mitigation: Hard 20KB limit, validation
- **Performance impact** → Mitigation: Lazy loading, caching, benchmarks
- **Schema versioning** → Mitigation: Version field, migration path

### Phase 1d Risks
- **System reminder too repetitive** → Mitigation: Keep to 3-4 rules only
- **Model quirk maintenance** → Mitigation: Document process, community contributions

### Phase 2a Risks
- **Zombie MCP processes** → Mitigation: Proper async context manager
- **Tool name collisions** → Mitigation: Automatic prefixing
- **Approval bypass** → Mitigation: Strict inheritance of host approval model

### Phase 2b Risks
- **Preference explosion** → Mitigation: Start with 10 core preferences, grow slowly
- **Personality vs preference conflicts** → Mitigation: Clear precedence rules, tests

### Phase 2c Risks
- **Resource leaks** → Mitigation: Task cleanup policy, monitoring
- **Silent failures** → Mitigation: Status tracking, error logging
- **Approval gaps** → Mitigation: Inherit approval decisions, no mid-execution prompts

---

## Next Steps

### Immediate Actions

1. ✅ **COMPLETE**: Create all 6 implementation guides
2. ⏳ **NEXT**: Begin Phase 1d implementation (quick win)
3. ⏳ Review each TODO document before implementation
4. ⏳ Set up tracking for success criteria

### Phase-Specific Next Steps

**Phase 1d** (Ready to start):
- Modify `co_cli/prompts/system.md` (3 sections)
- Create `co_cli/prompts/model_quirks.py`
- Update `co_cli/prompts/__init__.py`
- Add 5 tests to `tests/test_prompts.py`

**Phase 1c** (After 1d):
- Design internal knowledge schema
- Implement loading logic
- Create memory tools
- Write 18 comprehensive tests

**Phase 2a** (Can run parallel):
- Verify pydantic-ai v1.52+ MCP support
- Implement MCPServerConfig
- Integrate with agent factory
- Write 13+ functional tests

**Phase 2b** (After research integration):
- Implement UserPreferences dataclass
- Create preference loading logic
- Integrate with prompt assembly
- Write 15+ tests

**Phase 2c** (Last):
- Design task storage system
- Implement async runner
- Create slash commands
- Write 25+ tests

---

## References

### Design Documents
- `docs/DESIGN-co-evolution.md` - Strategic vision (Phases 0-3)
- `docs/REVIEW-compare-four.md` - Peer system analysis (prompt techniques)

### Implementation Guides (Created)
- `docs/TODO-prompt-system-phase1c.md` (2,050 lines) - Internal knowledge
- `docs/TODO-prompt-system-phase1d.md` (1,500 lines) - Prompt improvements
- `docs/TODO-mcp-client-IMPL.md` (1,850 lines) - MCP client
- `docs/RESEARCH-user-preferences.md` (805 lines) - Peer system research
- `docs/TODO-prompt-system-phase2b.md` (2,000 lines) - User preferences
- `docs/TODO-background-execution.md` (1,900 lines) - Background execution

### Related Documents
- `docs/DESIGN-00-co-cli.md` - Architecture overview
- `docs/DESIGN-01-agent.md` - Agent factory, CoDeps
- `docs/DESIGN-02-chat-loop.md` - Chat loop, streaming, commands

---

## Version History

- **2026-02-09**: Documentation phase complete (6 guides, ~10,100 lines)
- **2026-02-09**: Phase 1a, 1b complete (model conditionals, personalities)

---

**Status**: Ready for implementation. All design documents complete. Begin with Phase 1d (quick win) or Phase 1c (foundational).
