# Review Discipline and Code Change Principles

## Review Discipline

- **Deep pass on first round**: read every function body, trace call paths, check for stale imports and dead code. Do not skim signatures or assume correctness from names.
- **Evidence-based verdicts**: do not declare "ready" unless you can cite `file:line` references. If zero issues found, list every file read and what was checked. If scope is unclear, ask rather than rubber-stamp.
- Always check `docs/reference/` for research/best-practice docs before reviews or design proposals.
- **Design philosophy**: design from first principles — MVP-first but production-grade. Add abstractions only when a concrete need exists, and simplify any implementation or abstraction that is hard to explain in one short paragraph unless the complexity is forced by an external contract. When researching peers, focus on convergent best practices, not volume.
- **Peer research verification**: when comparing against peer tools, always confirm the correct repo/source before reading. Do a deep code scan (grep/read) to verify claimed gaps exist — do not report features as missing without evidence.

## Code Change Principles

- Prefer fail-fast over redundant fallbacks. Clean up dead code during implementation, not as a separate pass.
- **Subagents**: each subagent cleans up dead code before returning. After all finish, do an integration review for stale imports and orphaned references.
- Keep plans concise and actionable — resist over-engineering. If the user pushes back on complexity, simplify immediately rather than defending the design.
- Do not swallow foreground or user-visible errors with broad `except`, empty handlers, or log-and-continue paths. Let unexpected errors propagate. Convert expected non-fatal conditions into typed project-standard results or exceptions with actionable context; for tool failures, use `tool_error()`. Background cleanup, shutdown, and best-effort degradation paths may log and continue only when failing the main operation would be worse than losing the auxiliary work.
- **Verifiable criteria before implementing**: before starting a multi-step task, restate it as a testable outcome ("tests pass for X", "command Y produces Z"). If the success condition is vague ("make it work"), name it explicitly before writing code — weak criteria cause mid-task clarification loops.
- When ambiguity affects behavior, persistence, security, approval, or public API shape, stop and surface the assumption. For low-risk local implementation details, make the smallest coherent assumption and state it in the delivery summary.
- After renames or file moves: (1) grep for ALL remaining references to the old name across the whole repo, (2) check test imports specifically — they are the most common miss, (3) run the full test suite. Done only when grep finds zero stale references AND tests pass.

## Code Regulation Model (how rules get enforced)

A rule's enforcement medium must match how often it is violated. Choosing the wrong medium is why documented rules keep being re-litigated.

| Tier | Medium | Right when |
|------|--------|-----------|
| 0 — Judgment | Stated once in a plan/spec, trusted | Genuinely one-off decisions |
| 1 — Prose rule | `.agent_docs` + reviewer attention | Violations are rare, **or** the rule cannot be mechanized |
| 2 — Fitness function | A test / gate step that fails the build | The rule recurs **and** is mechanically checkable |

**Escalation trigger (standing rule):** when a Tier-1 prose rule is re-litigated **≥3 times** — or surfaces as a recurring violation *class* when mining git history (e.g. repeated "remove dead code", "fix private leak", "dedup" commits) — it must **graduate to Tier 2 or be explicitly retired**. Repeating a prose rule that keeps being violated is itself the failure mode: the rule either becomes an executable invariant or is redesigned/dropped. A Tier-1 rule that recurs but genuinely cannot be mechanized is a candidate for retirement or a design change, not for being restated again.

**Graduation is not complete until the Tier-1 source is retired.** When a rule moves to Tier 2, reduce or remove the prose/detection that owned it at Tier 1 — otherwise two layers enforce the same rule and drift against each other (the "one owner per fact" violation). The Tier-1 artifact keeps only what the gate cannot do (judgment-based variants, driving the burn-down of baselined debt) and points to the gate as the authoritative enforcer for new violations. Adding the gate while leaving the full Tier-1 detection in place is a half-graduation.

**Why:** prose + reviewer attention is the weakest enforcement medium — local, context-limited, interpreted — so global invariants defended only at Tier 1 erode as fast as they are cleaned. The proven counter-pattern in this repo is `test_instruction_floor_coupling.py`, which caught a live `skill_create` leak the moment it appeared. Tests don't erode; prose does.

**Two review scopes, both needed:**
- **Diff-scoped review** (`/review-impl`): catches *new* violations introduced by a change. Structurally blind to slow whole-surface accretion.
- **Whole-surface audit** (periodic): runs the Tier-2 fitness functions, then judgment-scans for recurring classes not yet automated, and proposes which to graduate. This is the feedback loop that grows Tier 2 — review feeds regulation. When authoring a Tier-2 check: baseline-and-ratchet (land green over current debt via an itemized, shrinking allowlist — never a blanket ignore), write it as `pytest` so it runs under `scripts/quality-gate.sh full`, and include a negative case proving a real violation fails.

## Clarity by Subtraction (proven refactor rules)

These are the corrections that have most consistently improved co's clarity, simplicity, and maintainability — verified across dozens of `no behavior change` refactors. The recurring failure mode they all prevent is **drift**: two copies of one truth diverging. A concept should have exactly one home, one name, one writer, and one moment it becomes true. This is the canonical source for these rules; `/review-impl` loads it by value.

- **Delete one-sided members.** A field, parameter, or flag with only a producer or only a consumer is dead — delete it, do not document it. When reviewing a new field/flag, grep for both a write site and a read site; if one is missing, it's a blocking finding.
- **Collapse redundant same-lifecycle state.** Two flags or code paths that are written together and cleared together are one concept wearing two names — collapse to one. Multiple code paths for one concept are a drift hazard; unify behind a shared primitive.
- **Flatten wrapper/bundle bags.** A class or dataclass that exists only as a return-value bag, or only to give evals/tests a one-liner, should be flattened onto its caller. Never shape a production API for eval/test convenience — convenience lives in the eval/test layer.
- **Module home = owning domain.** A module belongs in the package that owns its concern, not where it was first written. Domain logic placed in an unrelated package (e.g. domain code under a generic `context/` layer) is a modular-structure finding — relocate it.
- **Underscore is the visibility contract, both directions.** A leading-underscore module or symbol imported across a package boundary must drop the underscore — no fake-private facade. A symbol that stays package-internal keeps it. `__init__.py` stays docstring-only regardless.
- **No import-time side effects.** Module load must only *define*. Config reads, IO, console/tracer construction, instrumentation, or singleton coupling at module scope is a finding — defer it to a call-time function reached from the entrypoint. Deep-copy shared singletons (e.g. settings) before mutation so bootstrap changes don't leak.
- **Set state flags only after the operation succeeds.** A `state.x = True` that precedes the operation it asserts is a latent race — order writes so the flag-set is the last step after the work commits. State reflects committed reality, not intent.
- **Renames are hard and total.** No backward-compat aliases in code, no compat tables in specs, no migration shims. Converge vocabulary on the canonical upstream concept (e.g. align to the SDK's term) rather than inventing a synonym. `git log` is the history.
