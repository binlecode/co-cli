# Spec and Doc Conventions

## Specs (`docs/specs/`)

Specs are **living requirements and progress-tracking documents** — they define intent, track development milestones, and stay in sync with the latest code. Every spec has a human-maintained `## Product Intent` section (Goal, Functional areas, Non-goals, Success criteria, Status, `### Deferred`) followed by five implementation sections. `/sync-doc` keeps sections 1–4 accurate against code but never touches `## Product Intent` or `## 5. Test Gates`. Specs must never appear as tasks in an exec-plan: spec updates are outputs of delivery, not inputs to it. Any task whose `files:` list includes a `docs/specs/` path is invalid and must be removed.

Every spec follows this structure:
- `## Product Intent` / `## 1. Functional Architecture` / `## 2. Core Logic` / `## 3. Config` / `## 4. Files` / `## 5. Test Gates`

Use pseudocode, never source code. Sequence-owning specs (`bootstrap.md`, `core-loop.md`, flow diagrams in `compaction.md`, `prompt-assembly.md`, `memory-knowledge.md`) follow execution order strictly — no separate taxonomy sections that duplicate the flow.

### Section Rules

- **`## Product Intent`**: human-maintained; `### Deferred` subsection consolidates all known gaps and future work — never touched by `/sync-doc`.
- **`## 1. Functional Architecture`**: opens with a Mermaid block diagram anchored on the subsystem's components and lifecycle position; followed by a mechanism/component table and short prose on shared helpers and entry points.
- **`## 2. Core Logic`**: mechanism descriptions in pseudocode; no source-code paste.
- **`## 3. Config`**: settings and env var table.
- **`## 4. Files`**: one-liner per file — path and role only; no docstring prose.
- **`## 5. Test Gates`**: human-maintained behavioral map — one row per correctness property and the test file that gates it. `/sync-doc` does not touch this section.

Specs live in `docs/specs/` — one file per subsystem. `docs/reference/` is for research and background material (`RESEARCH-*`) and is not linked from specs.

## Artifact Lifecycle

- `REPORT-*.md` lives directly in `docs/`.
- `REPORT-<scope>.md` is permanent — only eval/benchmark/script runs produce these.
- Exec-plans live at `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` (creation date). Each plan tracks: plan content, `✓ DONE` marks (never delete mid-delivery), delivery summary, and review verdict. On Gate 2 PASS, run `/ship <slug>` — it handles plan archiving (`git mv` to `completed/`) as part of the commit. Never delete a plan.
