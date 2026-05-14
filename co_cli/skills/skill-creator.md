---
description: Promote a procedure into a reusable skill — verify reusability, dedup-check, draft to §6 shape, lint-check, and invoke skill_manage(action='create').
argument-hint: <task-type-name>
user-invocable: true
---

# Skill Creator

**Invocation:** `/skill-creator <task-type-name>`

Promote a procedure you just executed into a reusable skill. Walks from decision through authoring, lint check, and write. Output lands in `~/.co-cli/skills/<name>.md` and is immediately dispatchable.

---

## Phase 1 — Decide

Check two conditions before authoring:

1. **Reusability bar:** "Would I run the same sequence of steps for another task of this type?" If no — one-off — stop here. Don't create.
2. **Dedup check:** Call `skill_search('<task-type-name>')`. If a skill for this task type already exists, load it with `skill_view` and patch it instead of creating a duplicate.

If both pass, continue.

## Phase 2 — Shape

Draft the skill body in the §6 format (`skill.md` §6):

1. **Frontmatter** — `description` ≤1024 chars (single sentence: when to use this skill), `argument-hint` if args apply, `user-invocable: true`.
2. **H1 title** — matches the slash name.
3. **`**Invocation:**` line** — `/<name> [args]` in backticks; appears within the first 10 lines of the body.
4. **Opening paragraph** — one paragraph: what the skill does and when.
5. **Horizontal rule** — `---` after the opening paragraph.
6. **Phases** — 2–5 `## Phase N — <name>` sections, each ≤2000 chars. Name describes what the phase accomplishes ("Phase 1 — Validate", not "Phase 1 — First step").
7. **`## Rules` section** — optional; terminal invariants only ("Never X without Y first").

Name by task type, not the specific instance: `review` not `review-pr-123`, `deploy` not `deploy-main-2025`.

## Phase 3 — Lint

Check the draft against the R1–R10 rules before writing:

- R1: opens with `---` frontmatter
- R2: `description` present and non-empty
- R3: description ≤1024 chars
- R4: H1 present after frontmatter
- R5: `**Invocation:**` line in the first 10 body lines
- R6: at least one `## Phase N — <name>` section
- R7: all phase headers match `## Phase N — <name>` (H2, integer N, em-dash separator)
- R8: body total ≤8000 chars
- R9: each phase ≤2000 chars
- R10: no work-in-progress markers in body

Fix any violations before writing. Run `/skills lint <name>` post-create to confirm.

## Phase 4 — Write

Call `skill_manage(action='create', name=<task-type-name>, content=<body>)`.

On success, `refresh_skills` makes the skill immediately dispatchable and searchable — confirm with `skill_view(<name>)`.

## Rules

- Search before creating: `skill_search` first to avoid duplicates.
- Name by task type, not by instance.
- Don't create for one-offs — the bar is repeated use of the same procedure.
- Lint clean is non-negotiable: R1–R10 must pass before and after write.
- One procedure per skill: don't fold multiple workflows into one body.
