# TODO: Openclaw Adoption — Implementation Tasks

Remaining deferred task. TASK-1 through TASK-8 shipped with the openclaw-skills-adoption-review delivery.

P3 items (MMR, embeddings, process registry, skills, cron, config includes) are tracked in
`TODO-gap-openclaw-analysis.md` §8–§14.

---

## TASK-9 — `/new` Slash Command (P2, deferred)

**What:** Add `/new` slash command that checkpoints the current session to a knowledge file
and clears history.

**Why:** No explicit session-close hook. Good exchanges are lost when sessions end.

**Dependency:** `_index_session_summary()` in `co_cli/_history.py` must ship first (currently
deferred — no timeline). Do not start this task until that function exists.

**Files:** `co_cli/_commands.py`

**When unblocked:**
- Add `_cmd_new()`: take last min(15, len(history)) messages; call
  `_run_summarization_with_policy()` + `_index_session_summary()`; if summary succeeds, clear
  history; print confirmation.
- Register as `SlashCommand("new", "Checkpoint session to memory and start fresh", _cmd_new)`.
- Tests: session file written and history cleared on `/new`; no-op on empty history.

**Done when:** `/new` writes a `session-{timestamp}.md` file in `.co-cli/knowledge/` with
`provenance: session` frontmatter and returns `[]` (clears history). `/new` on empty history
prints a no-op message and returns `None`.
