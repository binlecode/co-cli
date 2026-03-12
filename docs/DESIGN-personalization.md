# Design: Personalization

## 1. What & How

This doc is the canonical spec for co-cli's personalization subsystem: the role files, learned-context assets, and invariants that define how co is customized for a given role.

Scope boundary:
- In scope: soul files, critique/examples/mindsets, role discovery and validation, character base memories, personality-context memories, and personalization-specific design invariants
- Out of scope: runtime prompt assembly, per-request instruction layering, history processors, and compaction behavior in [DESIGN-context-engineering.md](DESIGN-context-engineering.md)

### Architecture

| Personalization asset | Source | Purpose |
|-----------------------|--------|---------|
| Soul seed | `souls/{role}/seed.md` | Core identity anchor: identity declaration, trait essence, hard constraints |
| Critique lens | `souls/{role}/critique.md` | Always-on review lens for self-evaluation |
| Examples | `souls/{role}/examples.md` | Optional trigger→response demonstrations |
| Mindsets | `mindsets/{role}/*.md` | Role-specific guidance by task type |
| Character base memories | `.co-cli/memory/` tagged `[role, "character"]` | Stable source-material grounding |
| Personality-context memories | `.co-cli/memory/` tagged `personality-context` | Learned context carried across sessions |

### Design invariants

1. Soul is always loaded; there is no generic fallback identity.
2. Seed is the authority for identity, trait essence, and Never constraints.
3. File structure is the schema; roles and mindsets are discovered from disk, not Python registries.
4. Personality modulates how rules are expressed; it never weakens safety, approvals, or factual standards.
5. Structural personalization files are stable assets; session-to-session adaptation happens through learned context, not by rewriting the role files.

## 2. Core Logic

### Role asset model

Each role is defined by a file bundle under `co_cli/prompts/personalities/`:

```text
souls/{role}/seed.md
  identity declaration
  Core: trait essence activations
  Never: hard constraint list

souls/{role}/critique.md
  always-on self-eval lens

souls/{role}/examples.md
  optional trigger→response patterns

mindsets/{role}/{task_type}.md
  role-specific guidance for one of 6 task types
```

Runtime composition of these assets into the model context is owned by [DESIGN-context-engineering.md](DESIGN-context-engineering.md).

### Role discovery and validation

`VALID_PERSONALITIES` is derived from the `souls/` directory by listing roles that contain `seed.md`; there is no hardcoded role registry.

Startup validation is non-blocking:
- `validate_personality_files(role)` checks for `seed.md` plus all 6 required mindset files
- `_validate_personality(settings.personality)` emits startup warnings when files are missing
- missing mindset files degrade the role but do not block startup

Adding a new role requires only files:
1. `souls/{name}/seed.md`
2. `souls/{name}/critique.md`
3. `mindsets/{name}/*.md` for the 6 required task types
4. optional `souls/{name}/examples.md`
5. optional character base memories tagged `[name, "character"]`

### Task-type mindsets

Six task types are supported per role:

| Token | When used |
|-------|-----------|
| `technical` | implementation, commands, file ops, tool chains |
| `exploration` | research, tradeoffs, open investigation |
| `debugging` | isolate fault, hypothesize, verify |
| `teaching` | explanation and guided understanding |
| `emotional` | frustration, stuckness, celebration |
| `memory` | save, recall, or manage learned context |

Mindsets are personalization assets. The fact that they are loaded statically at agent creation is a context-engineering decision, documented there rather than here.

### Character base memories

Character base memories are pre-planted memory entries that carry the felt layer of each role: source-material scenes, speech patterns, and relationship dynamics. They live in `.co-cli/memory/` but are distinguished from user-derived memories by:

| Field | Value | Purpose |
|-------|-------|---------|
| `provenance` | `planted` | Marks source-material grounding |
| `decay_protected` | `true` | Exempts the entry from normal decay/retention pressure |
| `tags` | `[role, "character", "source-material"]` | Scopes the memory to a specific role |

These memories are stable personalization assets, not mutable user-state.

### Personality-context memories

Personality-context memories are learned-context entries tagged `personality-context`. They carry durable adaptations such as communication preferences or role-specific corrections across sessions.

The memory system owns how these entries are stored and maintained. Context engineering owns when and how they are injected into the model context.

### Compaction guard

`_PERSONALITY_COMPACTION_ADDENDUM` in `co_cli/_history.py` preserves personality-reinforcing moments only for `/new` session checkpointing. This is a personalization-specific rule applied by the summarization path; the runtime compaction mechanics remain documented in [DESIGN-context-engineering.md](DESIGN-context-engineering.md).

### Design decisions

- Expanded seed as identity anchor: the seed is the durable source of identity and Never constraints.
- Examples stay separate from the seed: demonstrations are optional role assets, not identity definition.
- Base vs. experience memory distinction: stable role grounding and user-derived learned context share storage but have different lifecycle expectations.
- No self-modifying persona: adaptation happens through learned context rather than rewriting the role files.
- No fragment registry: role composition is file-driven; adding a role is a content change, not a Python change.

### Personality behavior evals

Personality quality is validated by `evals/eval_personality_behavior.py` against golden cases in `evals/personality_behavior.jsonl` using the real agent and real model.

This doc keeps only the contract-level view:
- pass/fail is computed per case from multi-run outcomes
- multi-turn consistency regressions are tracked as `drift`
- tool-call responses in place of final text are tracked as `tool_leakage`

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `personality` | `CO_CLI_PERSONALITY` | `"finch"` | Active role name; validated against discovered roles at config load time |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/personalities/_composer.py` | Role asset loaders and role discovery/validation |
| `co_cli/prompts/personalities/souls/{role}/seed.md` | Identity anchor: identity declaration, Core, Never |
| `co_cli/prompts/personalities/souls/{role}/critique.md` | Role-specific self-eval lens |
| `co_cli/prompts/personalities/souls/{role}/examples.md` | Optional response-pattern examples |
| `co_cli/prompts/personalities/mindsets/{role}/{task_type}.md` | Role-specific guidance by task type |
| `.co-cli/memory/` | Shared store containing user memories plus role-tagged character base memories |
| `co_cli/tools/personality.py` | Learned-context memory loader |
| `co_cli/_history.py` | Personality checkpoint compaction addendum |
| `co_cli/config.py` | `personality` setting and startup validation |
| `evals/eval_personality_behavior.py` | Personality behavior eval runner |
| `evals/personality_behavior.jsonl` | Golden personality behavior cases |
