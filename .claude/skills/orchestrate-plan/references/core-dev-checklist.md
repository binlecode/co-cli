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

## Operational risk

- Schema or data model changes without migration or rollback path
- Irreversible operations (deletes, overwrites, publishes, prunes) without safeguards
- External API integrations or third-party side effects without error handling
- Tools marked `requires_approval=True` missing approval wiring

## Compatibility and safety

- **Backward compatibility:** Do any public API signature changes (tool interfaces, config structures, CLI flags) break existing callers? Consult CLAUDE.md for what constitutes a public interface in this project. Flag if no migration path is provided.
- **Performance:** Unbounded loops, N+1 query patterns, or large in-memory allocations introduced without justification.
- **Concurrency safety:** Shared mutable state accessed from async tools or parallel subagents without coordination.
