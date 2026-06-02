---
description: Promote a procedure into a reusable skill — verify reusability, dedup-check, draft to §6 shape, lint-check, and invoke skill_create.
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
2. **Dedup check:** Scan the `<available_skills>` manifest for a skill matching this task type. If one exists, load it with `skill_view` and patch it instead of creating a duplicate.

If both pass, continue.

## Phase 2 — Shape

Draft the skill body (`skill.md` §6):

1. **Frontmatter** — `description` ≤1024 chars (single sentence: when to use this skill), `argument-hint` if args apply, `user-invocable: true`.
2. **H1 title** — matches the slash name.
3. **Body** — whatever structure fits the skill. For multi-step procedures, the recommended template is `## Phase N — <Name>` sections with a final `## Rules` section for terminal invariants. Short skills, reference tables, and quick-action skills do not need this template.

Name by task type, not the specific instance: `review` not `review-pr-123`, `deploy` not `deploy-main-2025`.

## Phase 3 — Lint

Check the draft against the R1–R4 advisory rules before writing:

- R1: opens with `---` frontmatter
- R2: `description` present, non-empty, and ≤1024 chars
- R3: H1 present after frontmatter
- R4: body ≤8000 chars (warning — consider splitting if exceeded)

Fix R1/R2/R3 before writing (R1 and R2 are also enforced as hard blocks by `_validate_skill_content`). R4 is advisory; address it if the skill is genuinely overly broad. Run `/skills lint <name>` post-create to confirm.

## Phase 4 — Write

Call `skill_create(name=<task-type-name>, content=<body>)`. Any advisory findings come back as `lint_warnings` in the result.

On success, `refresh_skills` makes the skill immediately dispatchable and searchable — confirm with `skill_view(<name>)`.

## Rules

- Search before creating: scan the `<available_skills>` manifest first to avoid duplicates.
- Name by task type, not by instance.
- Don't create for one-offs — the bar is repeated use of the same procedure.
- R1–R3 must pass before write; R4 is advisory.
- One procedure per skill: don't fold multiple workflows into one body.
