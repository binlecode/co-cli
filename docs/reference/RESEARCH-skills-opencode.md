# RESEARCH: OpenCode Skills

Scan basis:

- skill runtime in `packages/opencode/src/skill/index.ts`
- skill tool in `packages/opencode/src/tool/skill.ts`
- local fixture skills under `packages/opencode/test/fixture/skills`

## 1. Source-of-Truth Runtime Model

OpenCode's skill system is runtime-driven rather than bundled-skill-driven.

What the source clearly implements:

- discovery of `SKILL.md` files from global/project/configured paths
- loading those skills through the `skill` tool
- permission-gated skill invocation

Patterns from source:

- external roots such as `.claude` and `.agents`
- configured skill paths and remote URLs
- dynamic availability filtered through permission evaluation

So for OpenCode, the most important implemented capability is the **skill runtime**, not a large built-in catalog.

## 2. Complete Implemented Skill Inventory In This Checkout

### Runtime support

- `skill` runtime and loader: implemented in production source
- bundled production skills: **none found in `packages/opencode/src` in this checkout**

### Fixture/example skills present in source

Fixture skills found under `packages/opencode/test/fixture/skills`: **2**

| Skill | Status | Functionality | Core implementation | Prompt design | Tool integration |
|------|--------|---------------|---------------------|---------------|------------------|
| `agents-sdk` | test fixture | Build stateful AI agents on Cloudflare Workers using the Agents SDK | Example `SKILL.md` loaded through the same runtime as real skills | Strong retrieval-first guidance because SDK knowledge may be stale | steers toward docs retrieval, code reading, and implementation tools |
| `cloudflare` | test fixture | General Cloudflare platform development across Workers, Pages, D1, Durable Objects, AI, and related products | Example `SKILL.md` with reference-driven navigation | Uses decision trees to route the model to the right platform/product | steers toward references, docs lookup, and normal code/web tools |

These two skills are implemented as fixtures/examples, not as a bundled production catalog.

## 3. Structural Read

### A. OpenCode is the clearest "skills as runtime-loaded overlays" peer

Compared with Hermes or Codex, OpenCode emphasizes:

- dynamic discovery
- markdown-defined skill instructions
- tool steering rather than skill-specific executables or scripts

### B. Tool integration still matters even when skills are prompt-first

Even when a skill is "just a `SKILL.md`", the runtime behavior still depends on:

- which tools the skill pushes the model toward
- what workflows it encodes
- what supporting files and references it exposes

### C. Implication for `co-cli`

OpenCode is a good reference for documenting skills that are primarily:

- instruction bundles
- runtime-discovered overlays
- tool-steering, not tool-implementing

But research docs should still record:

1. runtime support model
2. complete implemented fixture/bundled inventory in the checked-out source
3. functionality
4. core implementation
5. prompt design
6. tool integration
