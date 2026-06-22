# PO Checklist

- **Right problem?** Does the plan address the actual user need, or a proxy/assumed version of it?
- **Correct scope?** Is the scope the minimum needed to solve the problem — no more, no less?
- **First principles?** Does the design start from fundamentals, or does it layer complexity on top of existing complexity without necessity?
- **Non-over-engineering?** Are any tasks, abstractions, or design choices more elaborate than the problem warrants? Flag gold-plating, premature generalization, and speculative future-proofing.
- **Cost-blind on merit?** When choosing between technically-valid options, is the choice justified by correctness, simplicity, and maintainability — not by which is cheaper or faster to build? Implementation/development cost is not a tiebreaker. Distinct from scope: minimum-scope means don't do unnecessary work; this means among the necessary work, don't pick the cheaper-to-build option on cost grounds alone.
- **Effectiveness?** Will this plan, if fully executed, actually solve the stated problem for the user?
- **Behavioral constraints consistent with product requirements?** Are the stated constraints
  appropriately scoped — not more restrictive than the problem requires, not missing
  constraints that the product clearly needs? A plan with no behavioral constraints is
  a blocking concern unless the TL explicitly notes why none apply.

## Assessment threshold

- **approve** — all checklist questions answered satisfactorily; no blocking scope, value, or first-principles concern. Minor stylistic preferences do not block approval.
- **revise** — at least one question has a concrete, blocking concern: the plan solves the wrong problem, includes work that shouldn't be done, or the design is materially over-engineered for the stated outcome. State each blocking concern as a `PO-M-*` issue with a specific recommendation.
