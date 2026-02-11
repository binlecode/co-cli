# Phase 1e-FOLLOW-ON: Portable Identity - TODO

**Status:** 📅 **DEFERRED** (Non-Core Feature)
**Estimated Effort:** 9 hours
**Dependencies:** Phase 1c (internal knowledge system) - Already Complete ✅

---

## 🔴 DEFERRAL NOTICE (2026-02-10)

**This phase has been deferred to follow-on status** based on architecture review and roadmap prioritization.

### Why Deferred?

**Phase 1e is portability polish, not core functionality.**

**Rationale**:
1. **Let Phase 1c stabilize first** - Knowledge system just shipped, needs production validation
2. **Co should have a soul before making it portable** - First *use* knowledge in production, then worry about export/import/sync
3. **Symlinks work today** - Users can already achieve portability: `ln -s ~/Dropbox/co-knowledge ~/.config/co-cli/knowledge`
4. **Higher priorities waiting** - Phase 2a (MCP Client), 2b (User Preferences), 2c (Background Execution) deliver more immediate user value

### When Will This Execute?

**Phase 1e-FOLLOW-ON**: After Phase 1c knowledge system stabilizes in production usage
- No earlier than Phase 3+ timeframe
- When users explicitly request export/import/sync features
- After Phase 2a-2c complete and stabilize

### What Changed in Roadmap?

**Original plan**: Phase 1e after Phase 1c (portable identity next)

**Revised sequence**:
```
Phase 2a (MCP Client, 6-8h) ← NEXT
  ↓
Phase 2b (User Preferences, 10-12h)
  ↓
Phase 2c (Background Execution, 10-12h)
  ↓
Phase 2.5 (Shell Security S0+S1, 6-9 days)
  ↓
Phase 2d (File Tools C1, 3-4h)
  ↓
Phase 1e-FOLLOW-ON (Portable Identity, 9h) ← THIS DOCUMENT
```

**Reference**: Architecture review and roadmap in `docs/ROADMAP-co-evolution.md`

---

## Overview

**Goal:** Make co's identity portable across machines via directory separation and export/import commands.

**Scope:**
- Separate `identity/` (portable) from `local/` (machine-specific)
- Export/import commands: `co identity export/import`
- Auto-migration from Phase 1c structure
- Config path override (`identity_path` setting)

**Why Phase 1e, not Phase 1c:**
- Phase 1c focuses on core: memory tools + persistent context (8-10h)
- Portability is enhancement, not core functionality
- Symlink pattern provides zero-code portability TODAY (document in Phase 1c)
- Validate users want knowledge system before investing in portability

---

## What Phase 1c Ships (without portability)

**Directory structure (Phase 1c):**
```
~/.config/co-cli/
└── knowledge/
    ├── context.md              # Always-loaded
    └── memories/*.md           # Explicit memories
```

**Portability in Phase 1c:**
- ✅ Users can symlink: `ln -s ~/Dropbox/co-knowledge ~/.config/co-cli/knowledge`
- ✅ Documented pattern in README
- ✅ Zero code changes needed

---

## What Phase 1e Adds

**New directory structure:**
```
~/.config/co-cli/
├── settings.json               # Machine-local (API keys)
├── identity/                   # PORTABLE (NEW)
│   ├── profile.md
│   ├── personality.md
│   ├── knowledge/
│   │   ├── context.md
│   │   └── memories/*.md
│   └── traits/
│       ├── communication-style.md
│       └── learned-patterns.md
└── local/                      # Machine-local (NEW)
    └── knowledge.db            # SQLite index
```

**New commands:**
```bash
co identity export [path]       # Create portable archive
co identity import <path>       # Import identity
co identity import --merge      # Merge with existing
```

**Auto-migration:**
- Detect old `knowledge/` structure on first run
- Move to `identity/knowledge/`
- One-time, automatic, transparent

---

## Implementation Plan

See `docs/TODO-co-evolution-phase1c-kb-portable-follow-on.md` for complete design.

**High-level phases:**
1. Create `identity/` directory structure (1h)
2. Auto-migration from Phase 1c structure (1h)
3. Split personality into `identity/personality.md` (1h)
4. Add `identity_path` config override (30m)
5. Implement `co identity export` (2h)
6. Implement `co identity import` with merge (2h)
7. Documentation updates (30m)
8. Testing (1h)

**Total:** ~9 hours

---

## Success Criteria

- [ ] Auto-migration works (Phase 1c → Phase 1e structure)
- [ ] Export creates portable archive
- [ ] Import restores identity on fresh machine
- [ ] Merge strategy deduplicates memories
- [ ] Config path override works
- [ ] Documentation covers symlink + export/import patterns
- [ ] No API keys in `identity/` directory
- [ ] SQLite index rebuilds automatically

---

## Relationship to Phase 1c

**Phase 1c delivers:**
- ✅ Memory tools working
- ✅ Always-loaded context
- ✅ Markdown lakehouse pattern
- ✅ **Basic portability via symlink** (documented)

**Phase 1e enhances:**
- ⏳ Cleaner directory structure (identity separation)
- ⏳ Export/import convenience commands
- ⏳ Merge strategies for multi-machine sync
- ⏳ Config-driven identity path

**Key insight:** Phase 1c is fully functional. Phase 1e is polish.

---

## References

- Design document: `docs/TODO-co-evolution-phase1c-kb-portable-follow-on.md`
- Phase 1c (prerequisite): `docs/TODO-co-evolution-phase1c.md`
