# Skill Porting — Mission-First, Convergence-Prioritized

Task type: docs + skill content (no new code)

## Context

co-cli is "The Production-Grade **Personal Assistant** CLI" (README) — privacy-
first, wired to the user's notes (Obsidian), mail/calendar/files (Google), shell,
and memory. Peer agent CLIs (hermes, openclaw, codex) ship large bundled skill
catalogs. This plan brings the *proven* peer workflow patterns into co — but
ranked by **co's mission**, not by what's easiest to copy.

Two earlier framings were wrong and are discarded:
- A **bulk channel-aware importer** (`plan4`, deleted) — phantom dependencies,
  over-engineered; the loader ignores unknown frontmatter so no importer is needed.
- A **coding-discipline-first tiering** (TDD/debugging/spike at Tier 1) — that was
  the hermes worldview, not co's. co is an assistant, not a coding agent.

### What's already shipped (don't re-port)

Per the refreshed `docs/reference/RESEARCH-skills-peers-tiers.md` (2026-05-27):
co has 6 bundled skills (`doctor`, `plan`, `refactor`, `review`, `triage`,
`skill-creator`), model-callable authoring + patch (`skill_manage`), lint
(`lint.py` R1–R4/B1), the `<available_skills>` awareness layer, and an autonomous
dream-daemon skill reviewer. The skill *system* is mature. Engineering-workflow
and skill-lifecycle capabilities are covered. What co lacks is **assistant
workflow skills over its existing personal-data tools**.

### Prioritization model

A candidate's priority = **mission-fit × convergence × substrate × marginal-value**:

1. **Mission-fit** — does it serve co's personal-assistant purpose (notes, mail,
   calendar, files, documents)? Coding skills score low here by definition.
2. **Convergence** — do ≥2 surveyed peers ship the capability? (proven pattern,
   not a one-off). From the convergence matrix in the research doc.
3. **Substrate** — does co already have the tool/bin the skill drives? A skill is
   a prompt overlay; it cannot supply a tool co lacks. **Hard gate.**
4. **Marginal-value** — does the skill encode a *multi-step workflow* the raw
   tools don't already give? A CRUD wrapper over an existing tool is redundant
   (the research doc marks Obsidian and Google Workspace as "tools cover").
   Only **workflow overlays** clear this gate.

### Port the pattern, author the body

Peer assistant skills target *their* tool APIs (himalaya mail, peer Obsidian
plugins). They cannot be copied verbatim. What transfers is the **converged
workflow pattern** (e.g. "inbox triage → categorize → draft"); the **body is
authored native** against co's actual tools. This is "porting" in the sense that
matters — importing validated best practice — implemented correctly for co.

### Verified co substrate (gates the candidates)

| Tool group | Functions | Capability |
|---|---|---|
| Obsidian | `obsidian_search`, `obsidian_list`, `obsidian_read` | **read-only** vault retrieval/synthesis (no note write) |
| Gmail | `google_gmail_list`, `google_gmail_search`, `google_gmail_draft` | read + **draft** (no send) — approval-safe |
| Calendar | `google_calendar_list`, `google_calendar_search` | read schedule |
| Drive | `google_drive_search`, `google_drive_read` | read files |

No PDF/OCR tool, no audio/Whisper tool, no MCP-server tool, no Notion/Apple/Maps
tool exist — capabilities needing those are substrate-blocked (see Deferred).

## Problem & Outcome

**Problem.** co has the personal-data *tools* but no *workflows* over them. A user
must hand-orchestrate "what's on my plate today" across calendar + mail + drive,
or "triage my inbox," every time. Peers proved these patterns; co should ship them.

**Outcome.**
1. **Tier-1 assistant workflow skills landed** — `briefing`, `inbox-triage` —
   native over co's Google tools, multi-step, additive over raw CRUD.
2. **Tier-2 landed** — `vault-research` (Obsidian read-only synthesis).
3. **Substrate-blocked converged capabilities documented** as tool-gap
   recommendations (OCR/PDF, audio), not attempted as overlays.
4. All ports land **bundled** (`co_cli/skills/*.md`), version-controlled, passing
   the bundled-library test gate (`tests/test_flow_skill_bundled_library.py`).
5. **Incremental shipping** — each skill ships independently once it passes lint
   + the bundled gate.

## Prioritized list

| Tier | Skill | Mission | Convergence | Substrate | Marginal value (workflow) |
|------|-------|---------|-------------|-----------|---------------------------|
| **1** | `briefing` | core (assistant) | productivity cluster, 2+ peers | calendar+gmail+drive ✓ | Synthesize today: schedule + priority mail + relevant docs. 3-tool orchestration the raw tools don't give. |
| **1** | `inbox-triage` | core | email himalaya (2) + openclaw `taskflow-inbox-triage` | gmail list/search/**draft** ✓ | Search → categorize → draft replies for the user to approve. Clear multi-step procedure. |
| **2** | `vault-research` | core (notes) | Obsidian, 2 peers (hermes+openclaw) | obsidian search/read ✓ (read-only) | Search vault → read top hits → synthesize a cited answer. Retrieval+synthesis, not CRUD. |
| **→ tool + skill plans** | document handling (OCR/PDF) | core (documents) | **2 peers** (`ocr-and-documents`, `nano-pdf`) | ❌ no PDF/OCR tool | Converged + mission-core but **needs a tool first**. Tool in `2026-05-27-172717-toolgap-b2-document-extract.md`; skill in `2026-05-27-171910-documents-skill.md`. |
| **below line** | TDD, `systematic-debugging`, `spike` | low (builder, not mission) | TDD 1 peer; debugging redundant w/ `triage`; spike 1 peer | shell ✓ | Deprioritized: low mission-fit; `systematic-debugging` overlaps `triage`/`doctor`. Park. |

## Scope

### In scope
- `co_cli/skills/briefing.md` (new, native workflow over Google tools).
- `co_cli/skills/inbox-triage.md` (new).
- `co_cli/skills/vault-research.md` (new).
- Document-handling tool-gap note appended to this plan (recommendation, not impl).
- Verification via existing gates: `/skills lint`, `/skills check`,
  `tests/test_flow_skill_bundled_library.py`.

### Out of scope
- **Any new code or tool.** No importer, no new tool. If a candidate needs a tool
  co lacks, it's deferred to a tool plan — not forced into a skill body.
- **Verbatim peer ports.** Bodies are authored native against co's tools; peer
  SKILL.md files are pattern references only.
- **Substrate-absent capabilities.** OCR/PDF, Whisper, MCP-server, Notion, Apple,
  Maps, Spotify/Slack — no co tool → not in scope (see Deferred).
- **Write-side note capture.** Obsidian tools are read-only; no vault-write skill.
- **Coding-discipline skills.** Deprioritized under the mission lens (below the line).
- **Install-from-source (T1-2 gap).** Real gap, but it's lifecycle infra, not a
  skill port — separate plan.

## Behavioural Constraints
1. **Mission gate.** Every Tier-1/2 skill serves co's personal-assistant purpose.
2. **Substrate gate (hard).** Each skill names the exact co tools it drives; if a
   referenced tool doesn't exist, the skill is not authored.
3. **Marginal-value gate.** Each skill is a multi-step workflow (§6 Phase
   structure), not a single-tool CRUD wrapper. If the raw tool already does it,
   don't ship a skill.
4. **Native body.** No peer-specific tool names (`himalaya`, peer plugins) in any
   body. co tool names only.
5. **Approval-safe.** Skills draft/propose; they never auto-send mail or mutate
   without the user in the loop (gmail is draft-only; obsidian read-only — aligns).
6. **Bundled, test-gated.** Each lands in `co_cli/skills/`, passes lint R1–R4 + B1
   + security scan + the bundled-library gate before ship.
7. **One skill at a time.** Independently authored, linted, verified, shipped.

## Per-skill design (Tier 1 + 2)

**`briefing`** — "What's on my plate." Phases: (1) pull today/upcoming via
`google_calendar_list`; (2) scan recent/unread priority mail via
`google_gmail_list`/`search`; (3) surface relevant `google_drive_search` hits if
a meeting/thread references a doc; (4) synthesize a concise digest. Read-only.

**`inbox-triage`** — Phases: (1) `google_gmail_search`/`list` the target window;
(2) categorize (action-needed / FYI / waiting / ignore); (3) for action-needed,
`google_gmail_draft` a reply for user approval; (4) summarize the triage. Never sends.

**`vault-research`** — Phases: (1) `obsidian_search` the question; (2)
`obsidian_read` the top hits; (3) synthesize an answer with note-name citations;
(4) note gaps where the vault is silent. Read-only synthesis.

## Tasks

### TODO — TASK-1 — Confirm prioritization + substrate at Gate 1
Files: this plan. Acceptance: PO/TL confirm the mission+convergence ranking, the
three Tier-1/2 skills, and the substrate gate. Re-verify each named tool exists.

### TODO — TASK-2 — Tier 1: `briefing` + `inbox-triage`
Files: `co_cli/skills/briefing.md`, `co_cli/skills/inbox-triage.md` (new).
Acceptance (each): authored per design + §6 (`description`, H1, `## Phase N`);
co tool names only; body < 8000 chars; `/skills lint <name>` clean (R1–R4); B1
clean; security scan empty; loads via `/skills reload`; in `/skills list` +
manifest; `tests/test_flow_skill_bundled_library.py` green (manifest count bumped).

### TODO — TASK-3 — Tier 2: `vault-research`
Files: `co_cli/skills/vault-research.md` (new). Acceptance: same per-skill gate as
TASK-2; explicitly read-only (no obsidian write tool exists).

### TODO — TASK-4 — (moved) Document handling → separate tool + skill plans
The document-handling capability (OCR/PDF) is substrate-blocked — co has no
extraction tool — so it is sequenced **tool first, skill second**. The tool
(`document_extract`) is the batch-2 tool-gap plan
`2026-05-27-172717-toolgap-b2-document-extract.md`; the `documents` skill has its
own plan `2026-05-27-171910-documents-skill.md`, hard-gated on the tool. No work
in this plan; this task is a pointer only.

### TODO — TASK-5 — Bundled gate + manifest verification
Files: none (verification). Acceptance: `scripts/quality-gate.sh lint` clean;
`tests/test_flow_skill_bundled_library.py` green (manifest count reflects new set);
`/skills lint --all` clean; new skills surface in manifest + `/skills list`.

## Testing
No new test files. Ports are covered by the existing bundled-library gate
(`tests/test_flow_skill_bundled_library.py`: load + R1–R4 + B1 + manifest count).
Manual: `/skills reload`, `/skills check`, `/skills lint <name>`, `skill_view`.

## Open Questions
1. **Q:** Bundled vs user-tier? **A (rec):** Bundled — universal assistant
   workflows, version-controlled, test-gated. Same call as the prior plan.
2. **Q:** `briefing` and `inbox-triage` overlap on Gmail — merge? **A (tentative):**
   Keep separate. Briefing is read-only synthesis; triage drafts. Distinct intents.
3. **Q:** Should `vault-research` also pull `memory_search`/`session_search`?
   **A (tentative):** No — keep it vault-scoped; cross-store synthesis is a
   separate, larger skill if a need surfaces.

## Deferred items
- **Document handling (OCR/PDF)** — tool absorbed into
  `2026-05-27-172717-toolgap-b2-document-extract.md`; skill in
  `2026-05-27-171910-documents-skill.md`.
- **Audio/Whisper, MCP-server skills** — converged but substrate-blocked; need a
  tool first (future tool plans).
- **Notion / Apple / Maps / Spotify / Slack** — no co tool; substrate-absent.
- **Obsidian write/capture skill** — needs a vault-write tool (none today).
- **Install-from-source (T1-2)** — lifecycle gap; separate plan.
- **Coding-discipline skills (TDD etc.)** — below the line under the mission lens;
  revisit only if co's purpose shifts toward a coding agent.

## Shipping order
Incremental, per skill (not all-or-nothing):
1. TASK-1 (confirm) → Gate 1.
2. TASK-2 (`briefing`, then `inbox-triage`) → lint + bundled gate → ship.
3. TASK-3 (`vault-research`) → lint + bundled gate → ship.
4. TASK-4 (tool-gap note) → ship with TASK-3 or standalone.
5. TASK-5 gates each ship.

**Hard dependencies:** none. Skill system is mature; all named tools exist today.
