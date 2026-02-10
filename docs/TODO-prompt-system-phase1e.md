# Phase 1e: Portable Identity - TODO

**Status:** ⏳ Pending (follows Phase 1c completion)
**Estimated Effort:** 9 hours
**Dependencies:** Phase 1c (internal knowledge system)

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

See `docs/TODO-prompt-system-phase1c-kb-portable-follow-on.md` for complete design.

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

- Design document: `docs/TODO-prompt-system-phase1c-kb-portable-follow-on.md`
- Phase 1c (prerequisite): `docs/TODO-prompt-system-phase1c.md`
