# Architecture

`co-cli` is a local-first, approval-first terminal agent built on `pydantic-ai`.

## Dependency Direction (one-way rule)

```
main → bootstrap → agent → tools / context / config / knowledge / memory
```

- `main` owns the CLI entrypoints and REPL lifecycle; it calls into `bootstrap` and `agent`.
- `bootstrap` assembles the `CoDeps` runtime (config, stores, capabilities) at session start.
- `agent` builds the foreground agent and its toolset from `CoDeps`.
- `tools`, `context`, `config`, `knowledge`, `memory` are leaf packages — they do not import from each other or from `agent`, `bootstrap`, or `main`.
- All cross-package communication goes through `CoDeps` (passed via `RunContext[CoDeps]` in tool calls), never through direct imports.

Violations of this rule are import errors waiting to happen. If you find yourself importing upward (e.g. a tool importing from `agent`), the design is wrong — fix the API.

## Full Architecture Detail

See [`docs/DESIGN-system.md`](DESIGN-system.md) for the complete runtime architecture, subsystem responsibilities, `CoDeps` shape, capability surface, and security boundaries.

For component internals, see the relevant `DESIGN-*.md` docs in `docs/`.
