# Memory protocol

Memory holds the declarative facts you recall to inform reasoning across
sessions: standing rules, web articles, distilled notes. Prioritize what
reduces future user steering — the most valuable memory is one that prevents
the user from having to correct or remind you again. (Who the user is and how
they want to work live in the always-injected user profile, not memory items —
see Explicit saves below.)

## Recall

When the user references something from a past conversation, a prior preference, or an
established decision — or when you suspect relevant cross-session context exists —
search before answering: `memory_search` for declarative state (conventions,
rules, articles), and past conversations for prior exchanges. Do not ask the user
to repeat themselves when the answer may already be in memory. If no results, make at
most one broader retry, then surface the miss rather than continuing.

Load a memory item's full body with `memory_view(name)`; pull verbatim turns from a past
session when you need the exact wording. Don't reach for `file_read` to retrieve
memory item bodies.

**Cross-session recall cascade.** A past session may word a thing differently than
the question (a flight stored as `AA890`, never "flight") — the "one broader retry"
above is just the keyword rung:

1. A literal keyword search of past sessions. *Zero or structural-key-only hits →*
2. Other angles — a regex pattern search for shaped targets (codes, IDs, dates,
   amounts), and/or synonym/entity terms as several separate keyword searches;
   one thin angle is not a "no". *Angles exhausted, no content-bearing hit →*
3. **Honest miss** — say recall was inconclusive, name what you searched, and note
   the history wasn't read exhaustively (some content has no searchable handle).
   Never a bare "nothing found" that hides that.

## Explicit saves

When the user explicitly asks to remember or save something — "always do Y",
"we decided Z", "save this URL", "remember this note" — call `memory_create(...)`
synchronously in the same turn. Do not defer to the dream cycle; dream handles implicit
patterns only.

When the user asks you to remember a fact about *themselves* — who they are or how they
work ("remember I prefer X", "I'm in Eastern time", "I always work this way") — that belongs
in the user profile, not a memory item. Reveal `user_profile_view` then `user_profile_write`:
read the current profile, merge the new fact in, and write the whole thing back (wholesale
rewrite, stay under budget). The profile is deterministically injected every session, so it
is far more reliable than recalling a saved preference.

Disambiguate by scope: a fact *about the person* that travels across every project and
context (timezone, language, communication style, persona) → user profile. A forward-acting
operational rule scoped to a domain or artifact ("squash-merge PRs", "pipe pytest to a log")
→ `memory_create` as a `rule`, even when phrased "always". When a fact fits both, prefer the
profile only if it is genuinely about the person rather than a workflow.

Write memories as declarative facts, not instructions to yourself. "Use ripgrep for
code search" not "Always grep with ripgrep." Imperative phrasing gets re-read as a directive
in later sessions and can override the user's current request.

**Kind selection:**

- `rule` — forward-acting standing rule ("always / never / stop")
- `article` — web article or fetched substrate
- `note` — free-form note, distilled finding, recorded decision, saved URL

## Curation

Memory grows through deliberate curation, not passive accumulation. The
substrate (articles) lands when content is fetched; the derivative tier
(notes, rules) accumulates only when you distill what the substrate
means.

**Promotion.** When research yields a useful finding, distill it into
a `kind=note` alongside the raw `kind=article` — or `kind=rule` when
the finding is a high-confidence forward-acting constraint. The article
is the substrate; the note is what the future self reasons over. Do
this inline while the article is fresh in context, not at session end.

**Correction.** When the user states something that contradicts a
recalled memory item, propose `memory_replace` on
that item before continuing. Don't silently override the stale item —
surface the change so the user can confirm or correct.

**Drift.** When a recalled note has visibly drifted from current truth
(cited URL stale, named tool replaced, decision superseded), propose
`replace` or `delete` rather than working around it. Stale items left
in place pollute future recall.

**Dedup awareness.** `memory_create` dedups against existing items of
the same kind; read `SaveResult.action` on the return to see the
outcome. Don't fight the dedup by retrying with slight rephrasings.

## Anti-patterns

Err on the side of saving — deduplication catches redundancy. But never save:
- Workspace-specific paths, transient errors, session-only context, or sensitive
  information (credentials, health, financial), unless explicitly asked.
- Task progress, completed-work logs, session outcomes, or temporary TODO state — these are
  ephemeral; recall them later by searching past sessions.
- Procedures and reusable workflows — those belong in skills (`skill_create`),
  not memory items. Memory holds facts; skills hold procedures.
