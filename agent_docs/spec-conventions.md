# Spec and Doc Conventions

## Specs (`docs/specs/`)

Specs are **living implementation documents** — they track development milestones and stay in sync with the latest code. `/sync-doc` keeps sections 1–4 accurate against code but never touches `## 5. Test Gates`. Specs must never appear as tasks in an exec-plan: spec updates are outputs of delivery, not inputs to it. Any task whose `files:` list includes a `docs/specs/` path is invalid and must be removed.

Every spec follows this structure:
- `## 1. Functional Architecture` / `## 2. Core Logic` / `## 3. Config` / `## 4. Files` / `## 5. Test Gates` / `## 6. Variable Derivation Tree` (optional; required when the spec introduces multiple cached or derived values)

Use pseudocode, never source code. Sequence-owning specs (`bootstrap.md`, `core-loop.md`, flow diagrams in `compaction.md`, `prompt-assembly.md`, `memory.md`) follow execution order strictly — no separate taxonomy sections that duplicate the flow.

### Section Rules

- **`## 1. Functional Architecture`**: opens with a Mermaid block diagram anchored on the subsystem's components and lifecycle position; followed by a mechanism/component table and short prose on shared helpers and entry points.
- **`## 2. Core Logic`**: mechanism descriptions in pseudocode; no source-code paste.
- **`## 3. Config`**: settings and env var table.
- **`## 4. Files`**: one-liner per file — path and role only; no docstring prose.
- **`## 5. Test Gates`**: human-maintained behavioral map — one row per correctness property and the test file that gates it. `/sync-doc` does not touch this section.
- **`## 6. Variable Derivation Tree`** (optional): required when the spec introduces multiple cached or derived values. Contains three subsections: **Roots** (immutable inputs), **Module Constants** (load-bearing hardcoded values), and a derivation diagram (Mermaid or pseudocode) showing dependency chains. Bootstrap order is stated explicitly when a chain exists (e.g., `tool_call_limit → spill_threshold_chars`).

Specs live in `docs/specs/` — one file per subsystem. `docs/reference/` is for research and background material (`RESEARCH-*`) and is not linked from specs.

## Partitioning

When to split a spec:
- The spec exceeds ~500 lines AND covers 3+ clearly separable sub-domains, OR
- A sibling sub-spec already exists with parallel structure (e.g. `memory.md` foundation + `knowledge.md` / `sessions.md` channel sub-specs).

When to merge:
- The spec is <80 lines AND has no realistic growth runway.

Never split for pure topic count. A spec covering ten small mechanisms that share one mental model belongs in one file.

Renames and partitioning changes are immediate and complete: no aliases in code, no "Backward-Compat Notes" sections in specs, no migration ledgers. Co has no installed base; carrying compat surface area is pure overhead. `git log` is the authoritative history (see `feedback_zero_backward_compat` memory).

## Artifact Lifecycle

- `REPORT-*.md` lives directly in `docs/`.
- `REPORT-<scope>.md` is permanent — only eval/benchmark/script runs produce these.
- Exec-plans live at `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md` (creation date). Each plan tracks: plan content, `✓ DONE` marks (never delete mid-delivery), delivery summary, and review verdict. On Gate 2 PASS, run `/ship <slug>` — it handles plan archiving (`git mv` to `completed/`) as part of the commit. Never delete a plan.
