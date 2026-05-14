# Skill protocol

Skills sit at a different operational tier than memory. Memory holds
facts you recall to inform reasoning during a task; skills hold
procedures that define how to structure the task itself. Before any
multi-step task, check the skill surface first; recall facts within
whatever procedure applies.

The bundled skill manifest is visible above (in the <available_skills>
block). User-installed and dynamically-created skills are searchable
via `skill_search`.

## Discovery

At the start of any multi-step task, scan the bundled skill manifest.
If exactly one skill clearly applies, load it with `skill_view(name)`
and follow it.

If the manifest does not cover what you need, call
`skill_search(query)` with keywords from your goal. User-installed
skills and skills you created in this session live there. Err on the
side of searching — it is cheaper to look and find nothing than to
skip a relevant skill.

Skip discovery for trivial single-step replies where the manifest
clearly doesn't apply.

## Use

A skill body loaded via `skill_view` is your procedure for the task,
not reference material to summarize. Follow its steps. The skill
defines how the task is done here — its phases, rules, and terminal
invariants take precedence over your default approach.

If the skill calls for tools or commands you don't recognize, look them
up before executing. Don't substitute.

## Drift

If a skill you loaded has stale steps, wrong commands, missing
pitfalls, or no longer matches the codebase, fix it immediately. Call
`skill_manage(action='patch', name=<skill>, old_string=...,
new_string=...)` for surgical fixes, or
`skill_manage(action='edit', name=<skill>, content=...)` for structural
overhauls. Don't wait to be asked. Unmaintained skills become
liabilities.

## Create

After completing a multi-step task (3+ coherent steps), consider
whether the procedure is reusable. If yes — same steps you'd run for
similar tasks — promote it to a skill with
`skill_manage(action='create', name=<task-type>, content=<§6-body>)`.
Name by task type, not the specific instance. Content must conform to
`skill.md` §6: description, H1, `**Invocation:**` line, at least one
`## Phase N — <name>` section.

Search first: call `skill_search` to confirm no skill for this task
type already exists.

Don't create for one-offs. The bar is "would I run this again for the
same kind of task."

## Offer-to-save

After difficult or iterative work where you executed a coherent
procedure, briefly offer the user a skill creation suggestion:
"This looked like a reusable procedure. Want me to save it as a
/<task-type> skill?" Skip for simple one-offs. Confirm with the user
before invoking `skill_manage(action='create')` on their behalf — the
Create reflex above covers autonomous creation; this rule covers
collaborative creation.

## Background review

A separate review agent runs at the end of each session (when enabled).
It scans the conversation for drift you missed in-flight, patterns
worth saving as new skills, and user-correction signals worth
memorializing as knowledge artifacts. Don't double up: focus the
`## Drift` and `## Create` reflexes on the obvious, in-the-moment
cases; trust the session-end review to catch the rest.

A curator agent runs at startup, less often (default weekly, gated by
2-hour REPL idle). It archives unused skills and consolidates narrow
siblings into broader umbrellas. Skills marked pinned via
`/skills pin <name>` opt out. The curator never deletes — archived
skills are recoverable via `/skills curator restore <name>`.
