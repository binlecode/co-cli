---
name: orchestrate-research
description: Reference system tradeoff analysis. TL resolves scope, spawns Researcher subagent to compare co-cli design decisions against convergent patterns in peer systems, then writes a research report. Use before planning a scope that involves architecture decisions. For code/doc accuracy and TODO health, use /orchestrate-review instead.
---

# Research Orchestration Workflow

**TL is the orchestrator.** Researcher reads peer systems and returns tradeoff findings. TL synthesizes to `docs/reference/RESEARCH-<scope>.md`.

**Invocation:** `/orchestrate-research <scope>`

`<scope>` is a feature area, module name, or `all`. Output is permanent — `docs/reference/RESEARCH-<scope>.md` is not temporary scaffolding.

---

## Phase 1 — TL: Scope Resolution

### Step 1a — Resolve DESIGN docs

- If `scope = all`: glob `docs/DESIGN-*.md` — all files in scope.
- Otherwise: match `scope` as prefix or substring against filenames in `docs/DESIGN-*.md`. Multiple docs may match.
- If no filename matches, try a **content fallback**: grep each `docs/DESIGN-*.md` for the scope keyword in h1/h2 headings and Files-section entries. If at least two headings or a Files-section entry match, proceed with those docs. Only stop if content grep also finds nothing:
  ```
  ✗ No DESIGN docs matched scope "<scope>" (filename or content).
  Available: <list of docs/DESIGN-*.md>
  Refine scope and re-run.
  ```

### Step 1b — Select reference repos

Use the scope-to-repo table to identify which repos the Researcher reads:

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

All repos are available locally at `~/workspace_genai/<repo-name>`. Key files per repo are listed in CLAUDE.md's Reference Repos table.

When scope is ambiguous or matches `core`, read `sidekick-cli` as baseline plus the two most relevant primary repos.

### Step 1c — Check for existing docs

- Check if `docs/reference/RESEARCH-<scope>.md` already exists — note its date.
- Also check `docs/reference/REVIEW-<scope>.md` — it may contain already-triaged decisions.
- Researcher uses both to avoid re-raising decisions already analyzed or consciously rejected.

### Step 1d — Announce scope

Before spawning, announce:
```
## Research scope: <scope>

DESIGN docs:       [list]
Reference repos:   [list]
Existing RESEARCH: [found at <path>, dated <date> / none]
Existing REVIEW:   [found at <path>, dated <date> / none]
```

**Spawn Researcher (Phase 2). Wait before Phase 3.**

---

## Phase 2 — Researcher: Tradeoff Analysis

Researcher checks co-cli's major design decisions against convergent patterns in peer systems. **Reports only — does not modify docs or code.**

### Step 2-1 — Read existing docs first

If any `docs/reference/RESEARCH-<scope>.md` or `docs/reference/REVIEW-<scope>.md` exists for this scope, read it. Extract which gaps were already triaged, marked "low priority", or consciously rejected in DESIGN docs ("Not Adopted" sections). Do not re-raise already-triaged gaps as new findings.

### Step 2-2 — Identify design decisions in scope

Read in-scope DESIGN docs. Signals that mark a major decision worth comparing:
- Explicit "Not Adopted" / "Design Decisions" sections — each row is a comparison target
- Architecture diagrams encoding scope boundaries (deliberate shape choices)
- Config section with non-trivial defaults (encode tradeoff decisions)
- "Deferred" blocks — conscious time-bounded decisions
- Single-tier vs multi-tier framing, "MVP" language

Do NOT treat implementation details (which pragma, which Python version) or style conventions as design decisions.

### Step 2-3 — Read reference repos

For each primary repo in scope, read the key files from CLAUDE.md's Reference Repos table. Read secondary repos only at the key-files level. Volume cap: at most 3-4 peer files per repo.

### Step 2-4 — Classify each decision

| Verdict | Meaning |
|---------|---------|
| `aligned` | co-cli matches the convergent pattern — no action needed |
| `divergent` | co-cli differs from 2+ peer systems — divergence needs documented rationale or is a deliberate MVP tradeoff |
| `gap` | co-cli is missing something 2+ systems converge on, not yet tracked or consciously rejected |

**Verdict vocabulary is strict.** Use only `aligned`, `divergent`, or `gap`. If co-cli exceeds the peer pattern on a dimension, classify as `aligned` and note the advantage. Do not invent new labels such as "best_practice" or "comprehensive".

MVP filter: only flag gaps that affect current delivery quality. Do not surface future-facing or speculative gaps.

**A gap is significant** (worth escalating) when it meets 2+ of:
1. 2+ peer systems independently converge on the same solution
2. Creates a known user-facing failure mode
3. Blocks or degrades a feature in an active TODO doc
4. Closable with ≤5 files / 1 task (not a major refactor)

**A gap is minor** when it meets only 1 criterion, is already tracked, was consciously deferred, or is tightly coupled to a peer's architecture co-cli doesn't use.

### Step 2-5 — Verify a gap is untracked before flagging

Before flagging a gap:
1. Does any `docs/TODO-*.md` mention it?
2. Does any `docs/reference/RESEARCH-*.md` or `REVIEW-*.md` mark it "low priority" or "deferred"?
3. Does a DESIGN doc's "Not Adopted" section explain why it was rejected?

If all three checks pass (new, not triaged, not rejected), it is a significant untracked gap.

### Step 2-6 — Return findings to TL

Return a structured report to TL:
```
decisions_compared: [list]
findings:
  - decision: <name>
    co_cli_approach: <what co-cli does>
    peer_pattern: <what 2+ repos do, named>
    verdict: aligned | divergent | gap
    notes: <rationale or gap description>
    already_tracked: <yes — docs/TODO-X.md | no>

significant_untracked_gaps: <count — only gaps that passed the 2+ criteria filter; do not include minor notes here>
minor_notes: [gaps that met fewer than 2 criteria — listed for completeness, do not count toward verdict]
summary: <1-3 sentences>
```

---

## Phase 3 — TL: Synthesis

TL reads the Researcher report and writes the final output file.

### Step 3a — Overall verdict

| Verdict | Criteria |
|---------|---------|
| `ALIGNED` | No significant untracked gaps. co-cli's decisions match peer patterns or diverge with documented rationale. |
| `GAPS_FOUND` | 1+ significant untracked gaps, none directly blocking planned scope. Track before planning. |
| `ACTION_REQUIRED` | Significant untracked gap directly affects planned scope — implementation will hit it. |

### Step 3b — Priority table

| Priority | Criteria | Typical action |
|----------|---------|----------------|
| P0 | Gap directly blocks planned scope | Add to TODO doc / address in plan |
| P1 | Gap degrades quality but doesn't block | Document rationale / add tracking |
| P2 | Minor note; won't affect current plan | Log for future consideration |

Only include rows with actual findings. Recommended next step names P0 actions only.

### Step 3c — Recommended next step (one sentence)

| Verdict | Template |
|---------|---------|
| ALIGNED | "No untracked gaps — proceed to `/orchestrate-plan <slug>` when ready." |
| GAPS_FOUND | "Add tracking for [gap name] to [most relevant TODO doc] before planning [scope]." |
| ACTION_REQUIRED | "Resolve [gap name] gap before planning [scope] — implementation will hit this during [specific phase]." |

### Step 3d — Write `docs/reference/RESEARCH-<scope>.md`

TL authors the complete output file from the Researcher report:

```markdown
# RESEARCH: <scope> — Reference System Tradeoff Analysis
_Date: <ISO 8601>_

## What Was Reviewed

- **DESIGN docs read:** [list]
- **Reference repos read:** [list]
- **Existing RESEARCH/REVIEW docs consulted:** [list or none]

---

## Researcher — Tradeoff Analysis

| Decision | co-cli approach | Peer pattern | Verdict |
|----------|----------------|-------------|---------|
| <decision> | <co-cli> | <2+ repos + pattern> | aligned / divergent / gap |

**Significant untracked gaps:** N
[For each: name, peer evidence, co-cli status, why significant, estimated effort]

**Already tracked / consciously deferred:** [list — no action needed]

---

## TL Verdict

**Overall: ALIGNED / GAPS_FOUND / ACTION_REQUIRED**

| Priority | Action | Source |
|----------|--------|--------|
| P0 | [action] | [gap name] |
| P1 | [action] | [source] |

**Recommended next step:** [one sentence]
```

### Step 3e — Print verdict to terminal

```
## Research complete: <scope>

Verdict: ALIGNED | GAPS_FOUND | ACTION_REQUIRED
Output:  docs/reference/RESEARCH-<scope>.md

<recommended next step sentence>
```

---

## Execution Rules

- **No-fix rule:** Researcher reports only. Does not edit source files, DESIGN docs, or TODO docs.
- **TL authors the output file:** Researcher returns report to TL; TL writes `docs/reference/RESEARCH-<scope>.md` in one structured pass.
- **Local reference repos only:** Researcher reads repos at `~/workspace_genai/`. No web fetches. Key files are listed in CLAUDE.md's Reference Repos table.
- **MVP filter:** Researcher flags only gaps affecting current delivery quality — not theoretical or future-facing gaps.
- **Output file is permanent:** `docs/reference/RESEARCH-<scope>.md` is not temporary scaffolding.
- **Already-tracked gaps don't count toward verdict:** Researcher checks existing TODO and RESEARCH/REVIEW docs before calling something a gap.
