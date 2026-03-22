---
name: audit-delivery
description: On-demand inverse coverage check. Single agent scans source for agent-registered tools, config settings, and CLI commands, then checks each has a corresponding DESIGN doc section. Reports gaps only — does not fix.
---

# Delivery Audit Workflow

**One agent. Direction: source → doc. Default stance: gaps exist — CLEAN is earned, not assumed.**

Inventory every shipped feature from source. For each, verify it has honest DESIGN doc coverage. A name appearing in passing does not count. The goal is to find what is underdocumented or missing — not to confirm everything is fine.

**Invocation:** `/delivery-audit <scope>`

`<scope>` is a feature area, module name, or `all`. Results appended to `docs/TODO-<scope>.md` — the TODO is the single source of work tracking.

**Consumes:** source files, DESIGN docs, `docs/TODO-<scope>.md`. **Produces:** appends `## Delivery Audit` section to `docs/TODO-<scope>.md`.

---

## Phase 1 — Scope Resolution

**1. Resolve source modules.**

First, identify the project's source root from CLAUDE.md's architecture section.

- `scope = all`: scan all `<source_root>/**/*.py` excluding `_*.py` helpers and `__init__.py`.
- Otherwise: match scope as prefix or substring against module filenames under the source root. If no match: list available modules and stop.

**2. Resolve DESIGN docs.**
- `scope = all`: glob `docs/DESIGN-*.md`.
- Otherwise: same prefix/substring match against `docs/DESIGN-*.md` filenames. If no match: use all DESIGN docs (feature may be documented outside its own module doc).

**3. Locate the TODO file** at `docs/TODO-<scope>.md`. If it does not exist (e.g. `scope = all` or no matching slug), create a standalone section in memory and print to terminal only — do not create an AUDIT file.

**Append a header** to `docs/TODO-<scope>.md` (after existing content):
```
## Delivery Audit — <today>

### What Was Scanned
<list source modules, DESIGN docs checked>
```

---

## Phase 2 — Feature Inventory

Scan in-scope source modules for the project's feature classes. **Be exhaustive — missing an item from the inventory is a worse failure than a false positive.**

Use CLAUDE.md to identify the project's architecture and derive inventory methods. At minimum, inventory:

| Feature class | How to find |
|--------------|-------------|
| **Agent tools** | From CLAUDE.md: identify the tool registration pattern and agent entry points. Grep for registration calls across agent source files. Where sub-agent factories exist, grep those separately. |
| **Config settings** | From CLAUDE.md: identify the config module. Read it in full — every setting field and env var mapping. Settings without env vars are still in scope. |
| **CLI commands** | From CLAUDE.md: identify the CLI framework and entry point. Grep for command registration decorators or dispatch patterns. |

**Zero-result guard:** If any grep returns 0 results, stop immediately and report:
```
✗ Inventory incomplete: grep for <pattern> in <file> returned 0 results.
The registration pattern may have changed. Check CLAUDE.md for current architecture before proceeding.
```
Do not produce a coverage report against an empty inventory — a false-clean result is worse than no result.

For each found item record: name, source file, line number, feature class.

**Do not prune the inventory.** If uncertain whether a feature warrants documentation, include it — the coverage check decides severity.

---

## Phase 3 — Coverage Check

For each item, read the DESIGN docs and make a determination. **Do not accept a name appearing in a list as sufficient — read the surrounding text.**

| Coverage level | Criteria |
|---------------|----------|
| **Full** | Dedicated subsection or table row with: what it does, key behavior or parameters, and (for config) the default value and env var. All three must be present. |
| **Partial** | Name appears with some context but one or more required elements are missing (no default, no behavior description, no env var). |
| **None** | Absent from all in-scope DESIGN docs, or only appears incidentally in an unrelated sentence. |

**Severity — be strict:**
- `blocking` — no coverage, OR partial coverage for an agent tool (tools must be fully documented; partial is not acceptable for agent-facing features)
- `minor` — partial coverage for a config setting or CLI command (name + some context present, but incomplete)

**When in doubt, classify higher.** A `blocking` finding that turns out to be minor is less harmful than a `minor` finding that masks a real gap.

**Append to `docs/TODO-<scope>.md`:**

```markdown
| Feature | Class | Source | Coverage | Severity | Gap |
|---------|-------|--------|----------|----------|-----|
| `tool_name` | agent tool | `co_cli/tools/foo.py:12` | none | blocking | No DESIGN doc section |
| `tool_name2` | agent tool | `co_cli/tools/foo.py:30` | partial | blocking | Named in tools table but behavior not described |
| `MY_SETTING` | config | `co_cli/config.py:34` | partial | minor | Default value not documented |

**Summary: <N> blocking, <N> minor**
```

---

## Phase 4 — Second Pass

Before writing the verdict, re-scan the inventory against your findings:

1. **Any item marked full coverage** — confirm the doc section actually describes behavior, not just names the feature. If a "full" entry only lists the tool name in a table row with no description, downgrade to partial/blocking.
2. **Any item marked partial** — re-read the relevant doc section one more time. If all required elements are now present (behavior, parameters, default for config), upgrade to full and remove the finding. Items marked `none` are not eligible for upgrade here — absent means absent.
3. **Any config setting not in `env_map`** — verify it is explicitly noted as "no env var" or equivalent. Silence is not documentation.
4. **Any agent tool registered but not in the project's tool approval table** (identify from CLAUDE.md which DESIGN doc owns tool approval documentation) — flag as blocking regardless of other doc coverage (approval behavior must be documented for every tool).

Add or revise findings from the second pass — both downgrades and upgrades — before proceeding.

---

## Phase 5 — Verdict

**CLEAN** — every agent tool has full coverage, every config setting has at least partial coverage, every CLI command has at least partial coverage. Second pass found no downgrades. This verdict is rare on a first run.

**GAPS_FOUND** — any item is blocking, or second pass produced downgrades.

```markdown
## Verdict

**CLEAN / GAPS_FOUND**

| Priority | Feature | Gap | Recommended fix |
|----------|---------|-----|----------------|
| P1 | `tool_name` | No doc section | Add to `docs/DESIGN-tools-integrations.md` |
| P2 | `MY_SETTING` | Default not documented | Add default to Config table in `docs/DESIGN-core.md` |
```

Print terminal summary: scope, verdict, blocking count, minor count, appended to `docs/TODO-<scope>.md`.

---

## Rules

- **No-fix rule:** Reports only. Fixes go to `/sync-doc` or manual edits per the verdict.
- **Adversarial default:** Start assuming documentation has gaps. Every CLEAN classification must be positively justified — not inferred from absence of obvious problems.
- **Partial is blocking for agent tools:** A tool that is only named in passing gives a developer no actionable information. It is underdocumented, not partially documented.
- **Scope mismatch stops immediately:** If Phase 1 finds no matching source modules, stop — no output file.
- **Output appended to TODO:** Results go into `docs/TODO-<scope>.md` — the TODO is the single source of work tracking. If no matching TODO exists, output to terminal only.
- **No standalone AUDIT files:** Do not create `docs/AUDIT-*.md` files.
