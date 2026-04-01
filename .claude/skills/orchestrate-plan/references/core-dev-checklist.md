# Core Dev Checklist

## Implementation quality

- Tasks listing `docs/DESIGN-*.md` in `files:` — these are invalid; flag as blocking
- Missing or ambiguous steps
- Hidden coupling / migration gotchas
- Tasks too large for a single agent session or missing `done_when`
- "Hallucinated" success (outcomes assumed without validation steps)
- Test coverage gaps
- All `done_when:` criteria are machine-verifiable. Acceptable: `grep/test/file/doc-match`.
  Not acceptable: subjective phrases like "code is clean", "developer is satisfied",
  "feature works as expected" with no concrete check command.
- **User-facing tasks (non-N/A `success_signal`):** `done_when` must include a behavioral
  assertion that exercises the feature at its integration boundary — a test run
  (`uv run pytest tests/test_<feature>.py`), a CLI command (`uv run co <cmd>`), or a
  Python `-c` snippet that invokes the runtime path. Grep-only or file-exists-only criteria
  on user-facing tasks are **minor** issues — they prove structure but not function.
- **Integration boundary, not module boundary:** `done_when` should verify that the feature
  is wired into its consumer (e.g. tool appears in the agent's toolset, config field is
  read by the loader), not just that the module imports cleanly. A passing import does not
  confirm the feature is reachable at runtime.
- **Behavioral Constraints section:** present and non-empty; each constraint is specific
  enough to test or enforce without interpretation (not "should not fail" — must be
  "must never do X in condition Y"). Absence or vague constraints are blocking.

## Operational risk

- Schema or data model changes without migration or rollback path
- Irreversible operations (deletes, overwrites, publishes, prunes) without safeguards
- External API integrations or third-party side effects without error handling
- Tools marked `requires_approval=True` missing approval wiring

## Compatibility and safety

- **Backward compatibility:** Do any public API signature changes (tool interfaces, config structures, CLI flags) break existing callers? Consult CLAUDE.md for what constitutes a public interface in this project. Flag if no migration path is provided.
- **Performance:** Unbounded loops, N+1 query patterns, or large in-memory allocations introduced without justification.
- **Concurrency safety:** Shared mutable state accessed from async tools or parallel subagents without coordination.
