# Review: Approval Flow

## Verdict

**Short answer**

- The current approval process is **somewhat overdesigned**.
- It is **partially aligned** with converged patterns from strong reference systems, but not fully.
- The strongest part of the design is the **single deferred approval loop** plus the shell-specific `DENY / ALLOW / REQUIRE_APPROVAL` split.
- The weakest part is that the system adds multiple approval layers and trust caches **without a correspondingly strong execution boundary** such as a first-class sandbox or tightly-scoped managed permission rules.

**Bottom line**

The design has the shape of a mature approval system, but not yet the discipline of one. It is carrying more policy surface area than the current enforcement model justifies. In practice, it behaves closer to a Sidekick-style or Aider-style confirmation system with added tiers than to the tighter sandbox-first designs seen in Codex and Claude Code.


## Scope Reviewed

This review compares:

- The design doc: [docs/DESIGN-flow-approval.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-flow-approval.md)
- The current implementation in:
  - [co_cli/_orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/_orchestrate.py)
  - [co_cli/tools/shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)
  - [co_cli/_shell_policy.py](/Users/binle/workspace_genai/co-cli/co_cli/_shell_policy.py)
  - [co_cli/_approval.py](/Users/binle/workspace_genai/co-cli/co_cli/_approval.py)
  - [co_cli/_exec_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/_exec_approvals.py)
  - [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
  - [co_cli/_commands.py](/Users/binle/workspace_genai/co-cli/co_cli/_commands.py)
- Local peer references already present in the workspace:
  - `codex`
  - `claude-code`
  - `sidekick-cli`
  - `aider`

This is a static design/code review. I did not run runtime validation for this writeup.


## Executive Assessment

The approval flow is not fundamentally wrong. It has several strong design decisions:

- Shell execution is not treated the same as all other tools.
- Hard-deny shell patterns are blocked before user prompting.
- Auto-allowed shell commands are separated from approval-required shell commands.
- All deferred approvals eventually pass through one orchestration loop.
- MCP approval inheritance reuses the native deferred approval path rather than inventing a separate pipeline.

Those are good choices.

The overdesign comes from what sits on top of that core:

- turn-scoped skill grants
- session-wide auto-approvals
- optional risk classification
- cross-session shell wildcard persistence
- different persistence semantics by tool class

That stack creates a lot of conceptual machinery, but the actual trust model remains fairly blunt:

- many non-shell write tools are approved at the **tool-name level**
- shell persistence is generalized by a **derived wildcard pattern**
- the risk classifier mostly affects prompting, not the real security boundary

So the system is more elaborate than a minimal confirmation model, but still less disciplined than the top sandbox-first systems.


## What The Current Design Gets Right

### 1. One shared deferred-approval loop is the right shape

The central orchestration loop in [co_cli/_orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/_orchestrate.py#L393) is structurally sound.

The important properties are:

- deferred calls are resumed in the same turn
- approval hops share the same token budget
- a resumed run can trigger further deferred approvals
- MCP tools and native tools can reuse the same resume path

That is good design. It avoids fragmented approval handling and makes the system easier to reason about operationally.

This part is aligned with what stronger systems converge on:

- one event loop
- one approval resume mechanism
- no separate approval subsystem per tool family

### 2. The shell-specific split is materially better than blanket confirmation

The shell flow in [co_cli/tools/shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py#L40) and [co_cli/_shell_policy.py](/Users/binle/workspace_genai/co-cli/co_cli/_shell_policy.py#L22) is one of the strongest parts of the current implementation.

It distinguishes:

- `DENY`: commands that should not run even if the model asks
- `ALLOW`: commands that match a safe prefix plus constrained args
- `REQUIRE_APPROVAL`: everything else

That separation is important because it means approvals are not the only control surface. Some shell behavior is categorically blocked, and some is categorically cheap to allow.

That is materially better than a naive "prompt for every shell command" model.

### 3. Shell safety is not bypassed by orchestration grants

The design doc explicitly states that shell policy remains inside the tool and is not bypassed by orchestration-level grants. The implementation supports that model because `run_shell_command` evaluates policy before raising `ApprovalRequired` and before executing the subprocess. See [co_cli/tools/shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py#L40).

This is the correct layering:

- local tool policy decides whether shell execution is even eligible
- orchestration handles only deferred approvals

That separation is healthy.

### 4. MCP approval inheritance is simpler than a separate MCP security model

In [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py#L187), MCP servers configured with `approval="auto"` are wrapped into the same approval-required mechanism used elsewhere.

That is a good decision.

It avoids:

- a second UX for MCP approvals
- a second set of semantics for yes/no/always
- a second resumption protocol

This is aligned with converged practice. Good systems reuse the primary permission path whenever possible.


## Where The Design Is Overdesigned

### 1. There are too many approval tiers for the actual enforcement strength

The doc presents a four-tier decision chain:

1. skill grants
2. session tool approvals
3. risk classifier
4. user prompt

See [docs/DESIGN-flow-approval.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-flow-approval.md#L100).

The problem is not that four tiers are impossible to justify. The problem is that these tiers do not correspond to equally strong trust distinctions.

In the current code:

- skill grants are just a set membership check during the active skill turn
- session approvals are just a set membership check
- risk classification is mostly label logic
- only the shell tool has a more nuanced internal boundary

So the system looks layered, but most of the layers are only different ways to arrive at `True` for execution.

That is classic policy overgrowth:

- more states
- more terms
- more edge semantics
- limited improvement in actual safety or clarity

### 2. `"Always allow"` is too coarse for mutable non-shell tools

This is the clearest design weakness.

When the user selects `"a"` in [_handle_approvals](/Users/binle/workspace_genai/co-cli/co_cli/_orchestrate.py#L433), non-shell tools are added to `deps.session.session_tool_approvals` by bare tool name. See [co_cli/_orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/_orchestrate.py#L439).

That means one approval of:

- `write_file`
- `edit_file`
- `create_email_draft`
- `save_memory`
- `save_article`

approves every later call to that tool for the session, regardless of:

- target path
- content
- size of edit
- destination
- side-effect scope

The problem is strongest for tools with external or broad write effects, such as:

- `write_file`
- `edit_file`
- `create_email_draft`

It is still relevant for internal persistence tools like `save_memory` and `save_article`, but those are not identical in risk profile and should not be treated as if they were equivalent to shell execution or arbitrary file mutation.

This is both:

- too broad for a serious write-side permission model
- too blunt to justify the surrounding complexity

If a system wants tool-name-level grants, it should usually stay simpler overall and present itself honestly as a convenience-oriented confirmation UX. If it wants a more serious approval architecture, it needs tighter scoping:

- path-scoped grants
- operation-class grants
- server-scoped grants
- write-target constraints
- stronger runtime boundary

Right now it has the complexity language of the latter and the granularity of the former.

### 3. Cross-session shell persistence generalizes trust too aggressively

The shell persistence mechanism in [co_cli/_exec_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/_exec_approvals.py#L31) derives a pattern from the first one to three non-flag tokens and appends ` *`.

Examples:

- `ls` -> `ls *`
- `git status --short` -> `git status *`
- `git commit -m "msg"` -> `git commit *`

This is convenient, but it is also a trust expansion:

- the user approved one concrete command
- the system stores a broader class of future commands

That is a real policy decision, not just a UX shortcut.

The stronger policy-driven systems in the local peer set do not appear to center their model on this kind of inferred wildcard expansion from a single approval; they lean more on explicit policy and sandbox configuration. Simpler systems do use convenience-oriented “don’t ask again” behavior, but usually without presenting it as part of a more layered permission architecture.

Here, the wildcard rule becomes a cross-session trust artifact in `.co-cli/exec-approvals.json`, matched by `fnmatch`. See [co_cli/_exec_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/_exec_approvals.py#L51).

This is not inherently unacceptable, but it is more permissive than the design doc’s layered presentation suggests.

### 4. The risk classifier adds policy surface area without being the real boundary

The risk classifier in [co_cli/_approval_risk.py](/Users/binle/workspace_genai/co-cli/co_cli/_approval_risk.py) is optional and off by default via [co_cli/deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py#L67).

When enabled, it does three things:

- low-risk may auto-approve
- high-risk may be annotated in the prompt
- medium-risk falls through to the prompt

That means it is not doing hard enforcement. It is only influencing how the user gets asked.

That can be useful, but in the current system it feels like an extra policy tier whose operational value is modest:

- shell hard denials do not depend on it
- write tools requiring approval do not depend on it
- MCP permission inheritance does not depend on it
- cross-session shell persistence does not depend on it

So from a design-effort perspective, it is expensive in explanation relative to its leverage.

### 5. The design doc spends a lot of complexity budget on internal semantics the user does not benefit from much

[docs/DESIGN-flow-approval.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-flow-approval.md) is internally coherent, but it documents a lot of distinctions that mostly matter because the system has accumulated multiple trust caches and branches.

Examples:

- separate persistence semantics for shell vs non-shell `"a"`
- shell internal tiers plus orchestration tiers
- optional risk-classifier behavior
- re-entry budget accounting
- approval inheritance wording for MCP

Some of that is necessary and worth documenting. But overall, the doc reflects a system whose mental model is larger than the user-facing value delivered.


## Where The Design Is Underpowered Relative To Stronger Systems

This is the key point: the current design is not just overdesigned. It is overdesigned in some places while still underpowered in the places that matter most.

### 1. The real trust boundary is not sandbox-first

The strongest reference systems in this repo’s own peer set lean heavily on sandbox/policy modes.

Codex exposes explicit sandbox modes such as:

- read-only
- workspace-write
- danger-full-access

See [codex README](/Users/binle/workspace_genai/codex/codex-rs/README.md#L75).

Claude Code’s strict settings expose permissions and sandbox settings as first-class config, including explicit `ask` and `deny` rules and sandbox network restrictions. See [Claude strict settings](/Users/binle/workspace_genai/claude-code/examples/settings/settings-strict.json#L1).

Among the stronger policy-oriented systems in the local peer set, the recurring pattern is:

- put a strong execution boundary underneath the model
- let approvals operate as escalation or override, not as the only meaningful defense

co-cli does some of this for shell, but only heuristically:

- regex deny patterns
- safe-prefix allowlist
- approval fallback

That is useful, but it is not the same as a real sandbox policy layer.

### 2. Tool-name grants are too weak to support a multi-tier approval architecture

Once a system introduces:

- skill grants
- session grants
- persistent grants
- risk tiers

it needs those grants to be precise enough to be trustworthy.

Today they are often not.

For non-shell tools, the trusted unit is frequently just the tool name. That is too coarse for a more serious approval framework. If the system stays at that granularity, the better design is to simplify the model rather than simulate fine-grained safety through multiple tiers.

### 3. Peer patterns split into two credible camps

The local references do not all converge on one model. They split in a fairly clear way:

- Sidekick has a straightforward `yes / always / no` tool confirmation loop and a session disable set. See [sidekick agent](/Users/binle/workspace_genai/sidekick-cli/src/sidekick/agent.py#L75).
- Aider has a relatively simple confirmation flow with optional “don’t ask again” behavior. See [aider io.py](/Users/binle/workspace_genai/aider/aider/io.py#L807).
- Codex and Claude Code put more emphasis on sandbox and permission policy modes than on stacking multiple internal approval heuristics.

So the picture is not "many approval tiers plus a light execution boundary."

It is more like one of these two models:

- **Simple confirmation model**: minimal approval state, no strong sandbox, honest about being lightweight.
- **Sandbox-first policy model**: strong execution boundary, approvals as escalation and user visibility.

co-cli currently sits between them. That middle position is where the overdesign concern is most justified.


## Detailed Alignment With Reference Systems

### Codex

Alignment:

- clear distinction between safer and less-safe execution paths
- approvals are not the only mechanism
- emphasis on execution policy

Misalignment:

- Codex centers safety on sandbox modes and execution policy, not on accumulating many approval subtiers
- co-cli has more approval choreography and less hard runtime isolation

Conclusion:

co-cli is **directionally aligned** with Codex on the idea that not all commands are equal, but **not aligned** on where the primary safety boundary should live.

### Claude Code

Alignment:

- explicit ask/deny semantics
- permission policy is a first-class concern
- shell is treated differently from read-only operations

Misalignment:

- Claude Code’s stricter model is more declarative and policy-driven
- co-cli relies more on per-session emergent trust state and heuristics
- turn-scoped skill grants plus session grants plus pattern-derived shell approvals are looser than managed permissions

Conclusion:

co-cli is **partially aligned** in overall intent, but weaker and more ad hoc in policy realization.

### Sidekick

Alignment:

- yes/always/no UX
- session-level confirmation skipping
- straightforward interactive approval handling

Misalignment:

- co-cli adds more tiers and more explanation than Sidekick
- but the actual trust granularity is often still close to Sidekick-level

Conclusion:

This is the closest behavioral match. co-cli currently feels like a more elaborate Sidekick-style approval model rather than a Codex-style policy model.

### Aider

Alignment:

- approval as a simple user-facing safety valve
- convenience features such as “don’t ask again”

Misalignment:

- co-cli’s design language implies more formal policy sophistication than Aider’s simple confirmation flow
- but many decisions still collapse to broad user-trust shortcuts

Conclusion:

co-cli is more structured than Aider, but some of its broad grants resemble a dressed-up version of the same convenience-first pattern.


## Specific Areas That Feel Well-Designed vs Overbuilt

### Well-designed

- Shared deferred approval loop
- Shared approval resume semantics across native and MCP tools
- Shell-specific pre-deferral policy
- Budget sharing across approval hops
- Explicit denial path that returns useful model-visible errors

### Overbuilt or weakly justified

- Risk-classifier tier as a distinct architectural layer
- Separate session-vs-cross-session semantics that are not equally principled
- Turn-scoped skill auto-grants without stronger scoping
- Tool-name-only session allowlisting for state-changing tools
- Pattern-derived shell approvals from one-off user consent


## Recommended product direction

Pick one of these models and simplify toward it.

### Option A: Simpler approval-first model

Keep the current no-sandbox environment assumptions and simplify the approval model to match.

That would mean:

- preserve shell `DENY / ALLOW / ASK`
- preserve one deferred approval loop
- keep `yes / no / always`
- remove or de-emphasize the risk-classifier tier
- be explicit that session `"always"` is convenience, not a fine-grained trust policy
- consider narrowing `"always"` for mutable tools or dropping it for the highest-risk ones

This would be the most pragmatic MVP line.

### Option B: Stronger policy-first model

If the goal is to align with Codex / Claude Code style convergence, move the real boundary downward.

That would mean:

- introduce stronger sandboxing or policy-backed execution constraints
- make persistent shell approvals more explicit and less inferred
- scope grants to resources or operation classes, not just tool names
- use the risk classifier only as annotation, not as a core approval tier
- make skill grants declarative and bounded, not broad turn-level auto-allow

This is a better long-term architecture, but it is more work.


## Concrete Verdict By Question

### Is this approval process over designed?

**Yes, somewhat.**

More precisely:

- the core shell + deferred-approval structure is reasonable
- the extra layers above it are heavier than their enforcement precision justifies
- the system spends too much complexity budget on approval-state distinctions relative to the actual trust granularity

### Is this aligned with converged top ref systems?

**Partially, but not fully.**

More precisely:

- aligned on:
  - separating shell from ordinary tools
  - having explicit ask/deny behavior
  - using one approval resume loop
  - treating approval as part of orchestration, not ad hoc UI glue
- not aligned on:
  - sandbox-first boundary
  - tightly-scoped managed permissions
  - keeping the approval model simple when grant precision is coarse

The current implementation is best described as:

**a hybrid between a lightweight confirmation system and a more formal permission architecture, without fully committing to either**

That is why it feels overdesigned.


## Final Judgment

The approval flow should not be thrown away. Its foundation is decent.

But if the goal is alignment with converged best practice, the next step is not to add more approval tiers. The next step is to reduce policy surface area or strengthen the execution boundary.

Right now, the system has:

- more approval machinery than a simple confirmation tool needs
- less hard isolation than a serious policy-driven tool should have

That is the central design tension, and the current concern about overdesign is justified.
