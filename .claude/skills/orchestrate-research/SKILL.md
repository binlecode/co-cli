---
name: orchestrate-research
description: Reference system tradeoff analysis. TL resolves scope, spawns Researcher subagent to compare co-cli design decisions against convergent patterns in peer systems, then writes a research report. Use before planning a scope that involves architecture decisions. For code/doc accuracy and TODO health, use /orchestrate-review instead.
---

# Research Orchestration Workflow

**TL is the orchestrator.** Researcher reads peer systems and returns tradeoff findings. TL synthesizes to `docs/RESEARCH-<scope>.md`.

**Invocation:** `/orchestrate-research <scope>`

`<scope>` is a feature area, module name, or `all`. Output is permanent — `docs/RESEARCH-<scope>.md` is not temporary scaffolding.

**Consumes:** DESIGN docs, peer repos, REVIEW-<scope>.md (if exists). **Produces:** docs/RESEARCH-<scope>.md

---

## Phase 1 — TL: Scope Resolution

**1. Resolve DESIGN docs.**
- `scope = all`: glob `docs/DESIGN-*.md`.
- Otherwise: match scope as prefix or substring against filenames. If no filename matches, grep h1/h2 headings and Files-section entries for the scope keyword — proceed if 2+ headings or a Files-section entry match. If still nothing: list available docs and ask to refine.

**2. Select reference repos** using this table:

| Scope keyword | Primary repos | Secondary repos |
|--------------|---------------|----------------|
| `knowledge` / `memory` / `search` / `retrieval` | `openclaw`, `letta`, `mem0` | `gemini-cli` |
| `shell` / `approval` / `execution` / `exec` / `background` | `codex`, `aider`, `claude-code` | `opencode` |
| `skills` | `codex`, `opencode`, `gemini-cli` | `claude-code` |
| `context` / `history` / `compaction` | `letta`, `aider` | `codex`, `gemini-cli` |
| `sub-agents` / `delegation` / `orchestration` | `codex`, `gemini-cli`, `opencode` | `letta` |
| `mcp` / `tools` / `capabilities` | `gemini-cli`, `sidekick-cli` | `opencode` |
| `personality` / `prompt` | `letta`, `sidekick-cli` | `mem0` |
| `all` | All repos in the Reference Repos table in CLAUDE.md | — |

All repos are at `~/workspace_genai/<repo-name>`. Key files per repo are listed in CLAUDE.md's Reference Repos table. When scope is ambiguous or matches `core`, use `sidekick-cli` as baseline plus the two most relevant primary repos.

**Spawn Researcher (Phase 2). Wait before Phase 3.**

---

## Phase 2 — Researcher: Tradeoff Analysis

Researcher checks co-cli's major design decisions against convergent patterns in peer systems. **Reports only — does not modify docs or code.**

**1. Read existing docs.** If any `docs/RESEARCH-<scope>.md` or `REVIEW-<scope>.md` exists, read it. Do not re-raise gaps already triaged, marked "low priority", or consciously rejected in DESIGN docs ("Not Adopted" sections).

**2. Identify design decisions to compare.** Read in-scope DESIGN docs. Look for: "Not Adopted" / "Design Decisions" sections, architecture diagram shape choices, non-trivial config defaults, "Deferred" blocks, single-tier vs multi-tier framing. Skip implementation details and style conventions.

**3. Read reference repos.** For each primary repo, read key files from CLAUDE.md's Reference Repos table. Secondary repos at key-files level only. Cap: 3–4 files per repo.

**4. Classify each decision:**
- `aligned` — co-cli matches the convergent pattern
- `divergent` — co-cli differs from 2+ peers; divergence needs a documented rationale or is a deliberate MVP tradeoff
- `gap` — co-cli is missing something 2+ systems converge on, not yet tracked or consciously rejected

**A gap is significant** (worth escalating) when 2+ of these hold: 2+ peers independently converge on it; creates a known user-facing failure mode; blocks or degrades an active TODO item; closable with ≤5 files / 1 task. Otherwise it's a minor note.

**Before flagging a gap as significant**, verify it isn't already tracked in `docs/TODO-*.md`, `docs/RESEARCH-*.md`, `REVIEW-*.md`, or rejected in a DESIGN doc's "Not Adopted" section.

**5. Return findings to TL** — a plain prose or table summary covering: decisions compared, verdict per decision (aligned/divergent/gap), significant untracked gaps (with peer evidence and estimated effort), minor notes, and a 1–3 sentence overall summary.

---

## Phase 2b — Skeptic (optional, for significant gaps)

When the Researcher found 1+ significant untracked gaps, TL spawns a Skeptic subagent.
Skeptic reads the Researcher's findings (not the peer repos) and challenges each significant
gap: Is the peer pattern actually convergent (2+ systems), or is it one system's quirk?
Is the gap genuinely untracked, or does it appear under a different name in TODO/DESIGN docs?
Skeptic returns a brief rebuttal or confirmation per gap. TL weighs both before writing
the output file. Skip if Researcher found no significant gaps.

---

## Phase 3 — TL: Synthesis

TL reads the Researcher report and writes `docs/RESEARCH-<scope>.md` covering:

- What was reviewed (DESIGN docs, repos, existing docs consulted)
- A decisions table: Decision | co-cli approach | Peer pattern (named) | Verdict
- Significant untracked gaps — for each: peer evidence, co-cli status, why it matters, estimated effort
- Already tracked / consciously deferred items (no action needed)
- Recommended next step: one sentence — if gaps exist, name what to do and where; if none, say proceed to `/orchestrate-plan`

Print a brief terminal summary when done: scope, verdict (aligned / gaps found / action required), output path, recommended next step.

---

## Rules

- **No-fix rule:** Researcher reports only — no edits to source, DESIGN docs, or TODO docs.
- **Local repos only:** No web fetches. Key files listed in CLAUDE.md's Reference Repos table.
- **MVP filter:** Flag only gaps affecting current delivery quality, not theoretical future gaps.
- **Already-tracked gaps don't count:** Check existing docs before calling something a gap.
- **Output is permanent:** `docs/RESEARCH-<scope>.md` is not temporary scaffolding.
