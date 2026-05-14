---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
argument-hint: "[<slug>] [<focus>]"
---

# grill-me

Interview relentlessly about every aspect of a plan or design until reaching shared understanding. Walk each branch of the decision tree, resolving dependencies one-by-one. Ask one question at a time. If a question can be answered from the codebase, read the codebase instead of asking.

**Invocation:** `/grill-me <slug>` · `/grill-me <slug> <focus>` · `/grill-me` (inline)

- With slug: reads `docs/exec-plans/active/*-<slug>.md` and grills against it.
- With slug + focus: narrows to branches matching the focus phrase.
- Inline: user describes the design space; skill creates a plan stub and grills against it.

---

## Phase 1 — Locate or create the target plan

If slug given: glob `docs/exec-plans/active/*-<slug>.md`. If no match, stop: `✗ No plan found for slug <slug>`. Read the plan in full; note any existing `## Open Decisions Resolved` block.

If inline: ask for a slug (kebab-case, ≤ 4 words). Create `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-<slug>.md`:
```
# <slug>

## Problem
<one paragraph from the user's prompt>

## Status
Draft — open decisions to be resolved via /grill-me.
```
Announce the target plan path.

---

## Phase 2 — Enumerate the decision tree

Read the plan and any referenced `docs/reference/RESEARCH-<scope>.md`. List every decision branch:

```
[<status>] D<n>. <branch label>
```

- `[RESOLVED]` — already settled in the plan. Skip.
- `[DERIVED]` — answerable from the codebase or config. Resolve in Phase 3 without asking.
- `[OPEN]` — needs the user. Goes to Phase 4.

A focus argument limits enumeration to matching branches; others are `[SKIP]`.

If no `[OPEN]` and no `[DERIVED]` branches exist, write the block with `_No open decisions detected._` and exit.

---

## Phase 3 — Resolve `[DERIVED]` branches from codebase

For each `[DERIVED]` branch: identify the source-of-truth file, read it, record the answer with `path/file.py:LINE`. If the codebase is ambiguous, reclassify as `[OPEN]`.

---

## Phase 4 — Interview

Ask each `[OPEN]` branch one at a time. Every question must include:
- The decision space in one sentence.
- A recommended default with a one-line rationale.
- One or two alternatives.

One decision per question. No batching. Move to the next question immediately after each answer — don't wait for permission to continue.

If a branch turns out to be answerable from code mid-interview, drop it and resolve via Phase 3.

After each answer, record:
```
D<n>. <branch label>
- Question: <what was asked>
- Recommended: <default + one-line tradeoff>
- Chosen: <user's answer>
- Why: <one-line rationale>
- Constraint: <optional — load-bearing downstream fact>
```

If the user defers a branch, note it in `### Deferred` with any re-grill trigger they gave.

---

## Phase 5 — Write output block

Append `## Open Decisions Resolved — <date>` to the plan file. If a block already exists, number new decisions starting from `D<n+1>` where `n` is the highest existing entry.

In inline mode: update `## Status` to `Open decisions resolved — ready for /orchestrate-plan <slug>.`

Announce: `✓ Wrote ## Open Decisions Resolved to <path>  N open → R resolved · D deferred · C from codebase`

---

## Exit

Exit when all `[OPEN]` branches are answered or deferred, or the user says stop.

Do not invent questions outside the enumerated tree.

---

## Output schema

```markdown
## Open Decisions Resolved — <date>

### D1. <branch label>
- Question: ...
- Recommended: ...
- Chosen: ...
- Why: ...
- Constraint: <optional>

### Resolved from codebase (no interview)
- D7. <branch> → <answer> (source: path/file.py:142)

### Deferred
- D5. <branch> — <reason; optional re-grill trigger>

---
Summary: N open → R resolved · D deferred · C from codebase
```
