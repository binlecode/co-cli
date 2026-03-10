# REVIEW: delivery/capability-boundary — Delivery Audit
_Date: 2026-03-07_

## What Was Scanned

Scope: "capability-boundary" matched no module filenames — all DESIGN docs used per skill rules.

**Source modules scanned for new features:**
- `co_cli/config.py` — Settings fields and env_map
- `co_cli/agent.py` — `_register()` calls (agent tool registrations)
- `co_cli/tools/capabilities.py` — `check_capabilities` return dict
- `co_cli/_orchestrate.py` — `_check_skill_grant` observability

**DESIGN docs checked:**
All 25 `docs/DESIGN-*.md` files.

---

## Delivery Audit

| Feature | Class | Source | Coverage | Severity | Gap |
|---------|-------|--------|----------|----------|-----|
| `memory_auto_save_tags` | config | `co_cli/config.py:161` | full | — | None — DESIGN-index.md + DESIGN-memory.md both have full rows (setting, env var, default, description) |
| `knowledge_chunk_size` | config | `co_cli/config.py:164` | full | — | None — DESIGN-index.md + DESIGN-knowledge.md both have full rows |
| `knowledge_chunk_overlap` | config | `co_cli/config.py:165` | full | — | None — DESIGN-index.md + DESIGN-knowledge.md both have full rows |
| `skill_grants` field on `check_capabilities` | agent tool (new return field) | `co_cli/tools/capabilities.py` | partial → **fixed** | blocking (fixed) | `skill_grants` absent from DESIGN-tools-execution.md §2 return dict list — fixed in-place during this audit |

**Summary: 0 blocking (1 found, fixed inline), 0 minor**

---

## Verdict

**CLEAN** (after inline fix)

The one blocking finding (`skill_grants` missing from `check_capabilities` return dict in DESIGN-tools-execution.md) was corrected directly during this audit pass. All three new config settings (`memory_auto_save_tags`, `knowledge_chunk_size`, `knowledge_chunk_overlap`) have full documentation coverage across DESIGN-index.md, DESIGN-memory.md, and DESIGN-knowledge.md.

| Priority | Feature | Gap | Action taken |
|----------|---------|-----|--------------|
| P1 | `skill_grants` field on `check_capabilities` | Not listed in return dict in DESIGN-tools-execution.md §2 | Added bullet to return dict description with behavior note |
