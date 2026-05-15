# Plan 4 of 4 — Skill Migration Importer (Channel-Aware)

Task type: code + docs

## Overall Map — Skill Self-Evolution Replan

This plan is one of four sequential plans porting hermes's self-evolving skill capability to co-cli, reframed around the **four-tier surface model**. The map below appears verbatim at the top of each plan to prevent drift.

| # | Plan | File | Scope |
|---|---|---|---|
| **1 (shipped)** | Four-tier surface decomposition | `2026-05-11-120000-plan1-four-tier-surface-decomposition.md` | Eject skills and canon channels from `memory_search`; create `skill_search`; manifest injection; spec restructure. Foundation. |
| **1.5 (shipped)** | Surface tool naming convergence | `2026-05-12-100000-plan1.5-surface-tool-naming-convergence.md` | Drop `memory_search(channel=...)`; split into `session_search` + `knowledge_search`; add `knowledge_view` + `session_view`; hermes-pattern convergence across all three tiers. |
| **2** | Skill authoring contract + bundled library | `2026-05-11-120100-plan2-skill-authoring-contract-and-bundled-library.md` | `skill.md` §6 + §7; `_lint.py` + `/skills lint`; bundled library. |
| **3** | Skill protocol + lifecycle workflow bodies | `2026-05-11-120200-plan3-skill-protocol-and-workflow-bodies.md` | `06_skill_protocol.md` (five reflexes); bundled `skill-creator.md` + `skill-installer.md`. |
| **4 (this plan)** | Migration importer (channel-aware) | `2026-05-11-120300-plan4-skill-migration-importer.md` | `/skills import {claude\|hermes\|openclaw}` — read peer source dir, normalize frontmatter against §6/§7, lint-gate, write to `~/.co-cli/skills/`. Channel-aware adapter shape opens later artifact-import extension. |

**Order:** 1 → 1.5 → 2 → 3 → 4. Plan 4 can ship before or after Plan 3 (both depend on Plans 1.5 + 2).

**Reference:** `docs/reference/RESEARCH-skills-peers-tiers.md` Part 5, Step 5.

**What ships before this plan:**
- Plan 1 — four-tier surface, `skill_search`, manifest injection.
- Plan 1.5 — surface tool naming convergence (`session_search`, `knowledge_search`, `knowledge_view`, `session_view`; `memory_search` removed).
- Plan 2 — `skill.md` §6 + §7, `_lint.py` validator. **Critical**: importer gates writes on lint clean.
- Sibling shipped plans — `skill_view`, `skill_manage(install)`.

## Context

Peer agent ecosystems (claude-code, hermes-agent, openclaw) ship their own bundled skill libraries. Importing them into co-cli is a recurring need:
- **claude-code**: bundled skills under `.claude/skills/<name>/SKILL.md`.
- **hermes-agent**: bundled skills under `~/.hermes/skills/<name>/SKILL.md`.
- **openclaw**: bundled skills under `<workspace>/skills/<name>/SKILL.md` (and other tiers).

Each peer's format is slightly different (folder structure, frontmatter fields, body conventions). Without an importer, users hand-port skill by skill, hitting:
- Frontmatter incompatibilities (peer fields not recognized; required fields missing).
- §6/§7 violations (peer bodies don't match co-cli's authoring contract).
- Folder-vs-flat-file mismatches (peers use `SKILL.md` inside a folder; co-cli is flat `<name>.md`).

This plan ships `/skills import {claude|hermes|openclaw}` — a CLI command that reads a peer source directory, normalizes each skill, lint-gates against §6/§7, and writes the survivors to `~/.co-cli/skills/`.

### Why channel-aware adapters

The importer is structured as **per-channel adapters**, even though only the skill channel is in scope today. The reason: artifact import (e.g. importing obsidian notes or peer-agent memory entries) is a foreseeable future plan. The adapter shape — *"read peer source → normalize → validate → write to channel store"* — is general; making it skill-only would force a rewrite when artifact import lands.

### Current-state validation (inline)

Verified against the codebase (post-Plan-1.5; Plan-2 not yet shipped):

- ✓ `co_cli/skills/installer.py:fetch_skill_content` — single-file install path used by `skill_manage(action='install')`. Doesn't handle bulk dirs.
- ✓ `co_cli/skills/installer.py:_atomic_write_skill` — write helper. Reusable.
- ✓ `co_cli/skills/loader.py:scan_skill_content` — security scan. Reused.
- ✓ `co_cli/skills/_lint.py` (Plan 2) — lint validator. Reused.
- ✓ `co_cli/commands/skills.py` — `/skills` command family. New `import` subcommand lands here.
- ✓ `~/workspace_genai/fork-claude-code/`, `~/workspace_genai/hermes-agent/`, `~/workspace_genai/openclaw/` — peer source repos exist on disk for adapter development and testing.

### Why a CLI command, not a model-callable tool

Skill import is a bulk operation that mutates a security-sensitive directory (`~/.co-cli/skills/`). The user must explicitly opt in to importing from a specific source (path or peer-name). Exposing this as a model-callable tool risks the model autonomously importing skills the user didn't authorize. Single-skill install (via `skill_manage(action='install')`) is the model-callable path; bulk import is user-only.

## Problem & Outcome

**Problem.** Co-cli has no path for bulk-importing peer agent skill libraries. Users who want to leverage e.g. hermes's `tdd` or `architect` skills must hand-port them: read the peer SKILL.md, rewrite frontmatter, reshape body per co-cli's §6, manually validate against §7, then copy to `~/.co-cli/skills/`. Tedious and error-prone.

**Outcome.**

1. **`/skills import <peer-name>`** — CLI command. Reads the peer's bundled skill dir, normalizes each skill, lint-gates, writes survivors to `~/.co-cli/skills/`. Reports counts (imported / rejected / lint-failed).
2. **Three adapters**: `claude`, `hermes`, `openclaw`. Each adapter knows its peer's source layout, frontmatter dialect, and body conventions.
3. **Channel-aware adapter shape**: `Adapter` protocol with `discover(source_dir) → list[PeerEntry]`, `normalize(PeerEntry) → CoSkill | RejectionReason`. Reusable for future artifact-channel adapters.
4. **Lint-gated writes**: any skill that fails `_lint.py` is reported but not written. User sees the findings and chooses whether to hand-fix and re-import.
5. **Dry-run mode**: `/skills import <peer> --dry-run` validates without writing.
6. **Source-path override**: `/skills import <peer> --source <path>` allows pointing the adapter at a non-default location (e.g. a copy of the peer repo under inspection).
7. **Behavioral tests**: each adapter has a per-peer fixture covering normalization, rejection cases, and lint gating.

## Scope

### In scope

- `co_cli/skills/_migration/__init__.py` (new) — docstring-only.
- `co_cli/skills/_migration/_adapter.py` (new) — `Adapter` protocol + `PeerEntry`, `CoSkill`, `RejectionReason` dataclasses.
- `co_cli/skills/_migration/_claude.py` (new) — claude-code adapter.
- `co_cli/skills/_migration/_hermes.py` (new) — hermes-agent adapter.
- `co_cli/skills/_migration/_openclaw.py` (new) — openclaw adapter.
- `co_cli/skills/_migration/_importer.py` (new) — orchestrator: `run_import(peer: str, source_dir: Path, dry_run: bool) -> ImportReport`.
- `co_cli/commands/skills.py` — `/skills import <peer> [--source <path>] [--dry-run]` subcommand.
- `docs/specs/skill.md` — add §9 "Migration importer" documenting the adapter shape and the three supported peers.
- Behavioral tests:
  - `tests/test_flow_skill_import_claude.py` (new) — claude adapter fixture coverage.
  - `tests/test_flow_skill_import_hermes.py` (new) — hermes adapter fixture coverage.
  - `tests/test_flow_skill_import_openclaw.py` (new) — openclaw adapter fixture coverage.
  - `tests/test_flow_skill_importer_cli.py` (new) — CLI command integration.
- Fixtures: `tests/fixtures/peer_skills/{claude,hermes,openclaw}/<sample-skill>/SKILL.md` — small representative skill files for each peer.

### Out of scope

- **Artifact import.** Channel-aware adapter shape is built to support it, but no artifact adapter ships in this plan. Separate future plan.
- **Bidirectional sync.** Importer is one-way (peer → co). Exporting co skills back to peers is not supported.
- **Automatic upgrades.** Importer doesn't track upstream changes or auto-pull updates. Users re-run `/skills import` manually when they want to refresh.
- **In-place rewrite.** Importer never modifies peer source dirs; only writes to `~/.co-cli/skills/`.
- **Per-skill conflict resolution UI.** On name collision, the importer skips and reports; no interactive merge. User edits the collision via `/skills edit` after import.
- **Networked import.** Importer reads local peer source dirs. Pulling from a git URL is a future enhancement (would extend `_importer.py`'s source-resolution path).
- **Multi-peer import in one command.** `/skills import` takes one peer name per invocation. Users can run sequentially.
- **Roll-back.** Imports are non-transactional; a partial run leaves successfully-imported skills in place. Users can `/skills delete` individuals.

## Behavioural Constraints

1. **CLI-only, never model-callable.** Bulk mutation of `~/.co-cli/skills/` requires explicit user invocation. `skill_manage(action='install')` is the model-callable single-file path.
2. **Lint-gated.** Every imported skill is run through `_lint.py` before write. Lint failures are reported but the file is not written.
3. **Security-scanned.** Every imported body runs through `scan_skill_content` before write. Security flags trigger rejection (same as `skill_manage(action='install')`).
4. **Atomic per-skill.** Each skill is either fully written or not at all. Partial files never appear.
5. **No overwrites.** Name collisions with existing `~/.co-cli/skills/` entries are rejected with a directing message ("use `/skills edit` to update").
6. **Channel-aware shape, skill-only implementation.** Adapter protocol is designed for future artifact reuse; only `Channel.SKILL` adapters ship.
7. **Dry-run mode is read-only.** No writes, no file creates; reports same findings as a real run.
8. **Adapter ordering is alphabetical** in the CLI help output (claude, hermes, openclaw) — predictable surface, no priority signaling.

## High-Level Design

### Adapter protocol (`_adapter.py`)

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class Channel(Enum):
    SKILL = "skill"
    # ARTIFACT = "artifact"  # future


@dataclass(frozen=True)
class PeerEntry:
    """Raw discovered entry from a peer source dir."""
    name: str
    source_path: Path
    raw_content: str


@dataclass(frozen=True)
class CoSkill:
    """Normalized skill ready for co-cli (passes §6 shape; may fail §7 lint)."""
    name: str
    content: str  # frontmatter + body in co-cli format


@dataclass(frozen=True)
class RejectionReason:
    """Why a peer entry was rejected pre-write."""
    name: str
    reason: str  # "missing-description", "lint-fail", "security-flag", "name-collision"
    detail: str


class Adapter(Protocol):
    channel: Channel
    peer_name: str

    def default_source_dir(self) -> Path:
        """Where this peer typically stores bundled skills."""

    def discover(self, source_dir: Path) -> list[PeerEntry]:
        """Scan source_dir, return discovered peer entries."""

    def normalize(self, entry: PeerEntry) -> CoSkill | RejectionReason:
        """Convert peer entry to co-cli skill format, or reject."""
```

### `_importer.py` orchestrator

```python
@dataclass
class ImportReport:
    peer: str
    source_dir: Path
    discovered: int
    imported: int
    rejected: list[RejectionReason]
    written_paths: list[Path]


def run_import(
    peer: str,
    source_dir: Path,
    target_dir: Path,
    *,
    dry_run: bool = False,
) -> ImportReport:
    adapter = _resolve_adapter(peer)
    entries = adapter.discover(source_dir)
    rejected: list[RejectionReason] = []
    written: list[Path] = []

    for entry in entries:
        result = adapter.normalize(entry)
        if isinstance(result, RejectionReason):
            rejected.append(result)
            continue

        # Lint gate (Plan 2)
        from co_cli.skills._lint import lint_skill
        findings = lint_skill(result.content)
        if findings:
            rejected.append(RejectionReason(
                name=result.name,
                reason="lint-fail",
                detail=", ".join(f.rule for f in findings),
            ))
            continue

        # Security scan
        from co_cli.skills.loader import scan_skill_content
        flags = scan_skill_content(result.content)
        if flags:
            rejected.append(RejectionReason(
                name=result.name,
                reason="security-flag",
                detail=", ".join(flags),
            ))
            continue

        # Name collision
        target_path = target_dir / f"{result.name}.md"
        if target_path.exists():
            rejected.append(RejectionReason(
                name=result.name,
                reason="name-collision",
                detail=f"already exists at {target_path}; use /skills edit",
            ))
            continue

        if not dry_run:
            from co_cli.tools.system.skills import _atomic_write_skill
            _atomic_write_skill(target_path, result.content)
        written.append(target_path)

    return ImportReport(
        peer=peer,
        source_dir=source_dir,
        discovered=len(entries),
        imported=len(written),
        rejected=rejected,
        written_paths=written,
    )
```

### Per-peer adapters

**`_claude.py`** — claude-code skills:
- Layout: `<source>/<name>/SKILL.md`.
- Frontmatter: claude-code uses `description` (compatible), `allowed-tools` (drop), `argument-hint` (compatible).
- Body: usually H1 + phases or steps. Reshape to §6 if needed.

**`_hermes.py`** — hermes-agent skills:
- Layout: `<source>/<name>/SKILL.md` + linked files in `references/`, `templates/`.
- Frontmatter: `description`, `requires` (compatible), `metadata.hermes.*` (drop), `allowed-tools` (drop).
- Body: hermes bodies are typically well-shaped; minimal reshape.
- **Linked files**: drop. Co-cli's flat-file model doesn't support them today; importer notes them in the rejection detail if the skill relies on them heavily.

**`_openclaw.py`** — openclaw skills:
- Layout: `<source>/<name>/SKILL.md` (folder-per-skill).
- Frontmatter: `description`, `metadata.openclaw.*` (drop), `command-dispatch` (drop).
- Body: openclaw skills are typically procedural — should map cleanly to §6.

Each adapter implements:
- `default_source_dir()` — best-guess location (e.g. `~/.hermes/skills/`, `~/workspace_genai/openclaw/.agents/skills/`). User overrides via `--source`.
- `discover(source_dir)` — walks the source layout, returns `PeerEntry` per skill file.
- `normalize(entry)` — strips peer-specific frontmatter, reshapes body to §6 if needed, returns `CoSkill` or `RejectionReason`.

### CLI surface

```bash
# Default source dir
$ co-cli skills import hermes
Discovering hermes skills at /Users/binle/.hermes/skills/...
Discovered 12. Imported 9. Rejected 3:
  - tdd: lint-fail (R7, R8)
  - architect: security-flag (destructive-shell)
  - refactor: name-collision (already in ~/.co-cli/skills/)
9 skills written to ~/.co-cli/skills/.

# Custom source dir
$ co-cli skills import hermes --source ~/workspace_genai/hermes-agent/bundled/

# Dry-run
$ co-cli skills import hermes --dry-run
... (same output, no files written)
```

### `skill.md` §9 — Migration importer

Append to `skill.md` after Plan 3's §8:

```markdown
## 9. Migration importer

Bulk import skills from peer agents via `/skills import <peer>`. The
importer normalizes peer frontmatter, lint-gates per §7, security-scans,
and writes survivors to `~/.co-cli/skills/`.

Supported peers: `claude`, `hermes`, `openclaw`.

CLI-only (never model-callable). Bulk mutation of skill storage requires
explicit user invocation; `skill_manage(action='install')` is the
model-callable single-file path.

See `co_cli/skills/_migration/` for adapter implementations.
```

## Tasks

### TODO — TASK-1 — Adapter protocol and orchestrator

Files:
- `co_cli/skills/_migration/__init__.py` (new, docstring-only).
- `co_cli/skills/_migration/_adapter.py` (new) — protocol + dataclasses.
- `co_cli/skills/_migration/_importer.py` (new) — `run_import` orchestrator + `ImportReport`.

Acceptance:
- `Adapter` protocol with `discover` and `normalize` methods.
- `PeerEntry`, `CoSkill`, `RejectionReason`, `ImportReport` dataclasses.
- `run_import` orchestrates discovery → normalize → lint → security → collision → write.
- Each gate (lint/security/collision) emits the right `RejectionReason.reason` value.
- `dry_run=True` skips writes but still reports.

### TODO — TASK-2 — Claude adapter

Files:
- `co_cli/skills/_migration/_claude.py` (new).
- `tests/fixtures/peer_skills/claude/<sample>/SKILL.md` (new, ≥2 representative samples).

Acceptance:
- `default_source_dir()` returns the typical claude-code skills path.
- `discover(source_dir)` walks `<source>/<name>/SKILL.md` and returns `PeerEntry` per file.
- `normalize(entry)`:
  - Strips `allowed-tools` from frontmatter.
  - Preserves `description`, `argument-hint`.
  - Reshapes body to §6 if needed (adds `**Invocation:**` line if absent).
  - Returns `RejectionReason("missing-description")` when description is absent.

### TODO — TASK-3 — Hermes adapter

Files:
- `co_cli/skills/_migration/_hermes.py` (new).
- `tests/fixtures/peer_skills/hermes/<sample>/SKILL.md` (new, ≥2 representative samples; one with linked-files for rejection coverage).

Acceptance:
- `default_source_dir()` returns `~/.hermes/skills/`.
- `discover(source_dir)` walks the hermes layout.
- `normalize(entry)`:
  - Strips `metadata.hermes.*` and `allowed-tools` from frontmatter.
  - Preserves `description`, `requires`, `argument-hint`.
  - Body: minimal reshape (hermes bodies usually §6-shaped).
  - **Linked files**: detects references to `references/` or `templates/` in body; emits warning in `RejectionReason.detail` but does not reject (skill is imported with the references inert).

### TODO — TASK-4 — Openclaw adapter

Files:
- `co_cli/skills/_migration/_openclaw.py` (new).
- `tests/fixtures/peer_skills/openclaw/<sample>/SKILL.md` (new, ≥2 representative samples).

Acceptance:
- `default_source_dir()` returns a sensible openclaw skills path (e.g. `~/.openclaw/skills/`).
- `discover(source_dir)` walks the openclaw layout.
- `normalize(entry)`:
  - Strips `metadata.openclaw.*` and `command-dispatch` from frontmatter.
  - Preserves `description`, `user-invocable`, `disable-model-invocation`.
  - Reshapes body to §6 if needed.

### TODO — TASK-5 — `/skills import` CLI command

Files:
- `co_cli/commands/skills.py` — add `import <peer>` subcommand with `--source`, `--dry-run` flags.

Acceptance:
- `/skills import <peer>` calls `run_import` with the adapter's default source dir.
- `--source <path>` overrides the default.
- `--dry-run` passes `dry_run=True`.
- Output format matches §High-Level Design example (Discovered N, Imported M, Rejected list).
- Exit code 0 if all imports succeeded; exit code 1 if any rejected.
- Unknown peer name → error message listing supported peers.
- After successful import, `refresh_skills(deps)` is called (so newly-imported skills are immediately available without restart).

### TODO — TASK-6 — `skill.md` §9 documentation

Files:
- `docs/specs/skill.md` (append §9).

Acceptance:
- §9 documents the CLI command, supported peers, gates (lint, security, collision).
- Cross-link to `co_cli/skills/_migration/`.
- ≤25 lines.

### TODO — TASK-7 — Behavioral tests

Files:
- `tests/test_flow_skill_import_claude.py` (new).
- `tests/test_flow_skill_import_hermes.py` (new).
- `tests/test_flow_skill_import_openclaw.py` (new).
- `tests/test_flow_skill_importer_cli.py` (new) — CLI integration.

Test surface (per adapter):

| # | Assertion |
|---|---|
| 1 | `discover()` on a fixture dir returns the expected `PeerEntry` count. |
| 2 | `normalize()` on a valid peer skill returns `CoSkill` with §6-compliant content. |
| 3 | `normalize()` strips peer-specific frontmatter fields. |
| 4 | `normalize()` returns `RejectionReason("missing-description")` when description is absent. |
| 5 | `default_source_dir()` returns a `Path` (no I/O check). |

Test surface (`_importer.py` orchestrator):

| # | Assertion |
|---|---|
| 1 | `run_import` runs all gates in order: normalize → lint → security → collision → write. |
| 2 | `dry_run=True` produces same report but no files written. |
| 3 | Lint failure rejects with `reason="lint-fail"`. |
| 4 | Security flag rejects with `reason="security-flag"`. |
| 5 | Name collision rejects with `reason="name-collision"`. |
| 6 | Successful imports land at `target_dir/<name>.md` with atomic-write semantics. |

Test surface (CLI):

| # | Assertion |
|---|---|
| 1 | `co-cli skills import claude --source <fixture-dir> --dry-run` returns exit 0 and reports correct counts. |
| 2 | Without `--dry-run`, the same command writes to a tmp target dir. |
| 3 | Unknown peer name returns exit code != 0 with a clear error message. |
| 4 | After successful import, `refresh_skills` is invoked (verified via `deps.skill_registry` containing imported names). |

### TODO — TASK-8 — Cross-plan integration check

Files: none (verification step).

Acceptance:
- `scripts/quality-gate.sh full` clean.
- `/skills import claude --source tests/fixtures/peer_skills/claude/ --dry-run` exits 0.
- Same for hermes and openclaw fixtures.
- Newly-imported skills surface via `skill_search` (regression guard from Plan 1).
- Imported skills appear in `/skills list` after a real (non-dry-run) import.

## Testing

### Test files

- `tests/test_flow_skill_import_claude.py` (new)
- `tests/test_flow_skill_import_hermes.py` (new)
- `tests/test_flow_skill_import_openclaw.py` (new)
- `tests/test_flow_skill_importer_cli.py` (new)
- Fixtures: `tests/fixtures/peer_skills/{claude,hermes,openclaw}/<sample-name>/SKILL.md`

### Test pattern

Real `_importer.py`, real adapters, real `_lint.py`, real `scan_skill_content`. Fixtures are small, representative peer skill files committed to the repo (not pulled from external repos at test time).

CLI tests use the real `co-cli` entrypoint via `subprocess.run` or the typer test runner.

### Lint / quality gate

- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before considering ready to ship.
- `/skills lint --all` on imported skills (manual verification of fixture-import outputs).

## Open Questions

1. **Q:** Should the importer recurse into nested directories in the peer source, or only the top-level?
   **Tentative answer:** Only top-level per peer. Each peer's layout is well-known and shallow (one folder per skill).

2. **Q:** Should `--source` accept a URL (git or HTTP) or only a local path?
   **Tentative answer:** Local only for v1. Networked import is a future enhancement; keeps the security surface tight.

3. **Q:** What's the default target dir? `~/.co-cli/skills/` (user-global)?
   **Tentative answer:** Yes — same as `skill_manage(action='install')`. No per-project target for v1.

4. **Q:** Should the importer be idempotent (re-importing the same source dir produces no churn)?
   **Tentative answer:** Yes — collision rejection makes it idempotent by design. Re-importing fails on existing names; user uses `/skills edit` for updates.

5. **Q:** Should we ship adapter for the legacy `~/.co-cli/skills/` format itself (e.g. importing from an older co-cli install)?
   **Tentative answer:** No — that's a copy operation, not normalization. `cp -r` is sufficient.

6. **Q:** Should the importer expose a Python API for programmatic use (in addition to CLI)?
   **Tentative answer:** The internal API (`run_import`) is callable. Public-API status is deferred until there's a documented use case (e.g. eval harness importing peer skills as a setup step).

## Deferred items

- **Artifact-channel adapters.** Channel-aware shape supports them; no implementation in this plan.
- **Networked import (git/HTTPS source URLs).** Future enhancement; extends `_importer.py`'s source-resolution.
- **Bidirectional sync.** Export from co → peer not supported.
- **Conflict resolution UI.** Name collisions are rejected with directing message; no interactive merge.
- **Auto-update tracking.** Importer doesn't track upstream changes; users re-run manually.
- **Per-project target dirs.** Default target is user-global; project-local skill dirs deferred.
- **Linked-file import (`references/`, `templates/`).** Co-cli's flat-file model doesn't support linked files; importer drops references with a warning.

## Shipping order

Single commit — all eight TASKs. Adapter protocol + three adapters + CLI command + tests + spec ship together. Partial ship leaves the importer with incomplete peer coverage.

**Hard dependencies:**
- Plan 1.5 (surface tool naming convergence, shipped) — converged surface; provides `skill_search`, `~/.co-cli/skills/` as target, `_atomic_write_skill` in `co_cli/tools/system/skills.py`.
- Plan 2 (authoring contract + lint) — provides `_lint.py` for the lint gate.

**Soft dependencies:** Plan 3 — independent. This plan can ship before or after Plan 3.

**Initial-state caveat:** none. The importer is opt-in; users invoke `/skills import` when they want bulk import. Default state is unchanged.

## Post-ship — research-doc resync

After this plan ships, update `docs/reference/RESEARCH-skills-peers-tiers.md`:

- Step 5 (Migration importer) → **shipped** (`/skills import` with three adapters).
- Part 5 (Build order) → all five steps shipped.
- Architecture comparison: co-cli's importer is the first instance of channel-aware adapter shape. Future artifact-channel adapters reuse the protocol.
