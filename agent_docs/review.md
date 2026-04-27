# Review Discipline and Code Change Principles

## Review Discipline

- **Deep pass on first round**: read every function body, trace call paths, check for stale imports and dead code. Do not skim signatures or assume correctness from names.
- **Evidence-based verdicts**: do not declare "ready" unless you can cite `file:line` references. If zero issues found, list every file read and what was checked. If scope is unclear, ask rather than rubber-stamp.
- Always check `docs/reference/` for research/best-practice docs before reviews or design proposals.
- **Design philosophy**: design from first principles — MVP-first but production-grade. Add abstractions only when a concrete need exists, and simplify any implementation or abstraction that is hard to explain in one short paragraph unless the complexity is forced by an external contract. When researching peers, focus on convergent best practices, not volume.
- **Peer research verification**: when comparing against peer tools, always confirm the correct repo/source before reading. Do a deep code scan (grep/read) to verify claimed gaps exist — do not report features as missing without evidence.

## Code Change Principles

- Prefer fail-fast over redundant fallbacks. Clean up dead code during implementation, not as a separate pass.
- Do not swallow foreground or user-visible errors with broad `except`, empty handlers, or log-and-continue paths. Let unexpected errors propagate. Convert expected non-fatal conditions into typed project-standard results or exceptions with actionable context; for tool failures, use `tool_error()`. Background cleanup, shutdown, and best-effort degradation paths may log and continue only when failing the main operation would be worse than losing the auxiliary work.
- When ambiguity affects behavior, persistence, security, approval, or public API shape, stop and surface the assumption. For low-risk local implementation details, make the smallest coherent assumption and state it in the delivery summary.
- After renames or file moves: (1) grep for ALL remaining references to the old name across the whole repo, (2) check test imports specifically — they are the most common miss, (3) run the full test suite. Done only when grep finds zero stale references AND tests pass.
