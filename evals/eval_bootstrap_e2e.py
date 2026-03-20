#!/usr/bin/env python3
"""Eval: end-to-end bootstrap sequence validation.

Exercises the full startup pipeline against real components:
  create_deps() -> sync_knowledge() -> restore_session() -> display_welcome_banner()

Also validates the knowledge backend degradation path:
  resolve_knowledge_backend() hybrid -> fts5 when sqlite-vec dims are invalid.

Case groups:
  bootstrap-create-deps          -- create_deps() returns CoDeps without raising
  bootstrap-startup-statuses     -- deps.runtime.startup_statuses is present and a list
  bootstrap-sync-ok              -- sync_knowledge() keeps index active after successful sync
  bootstrap-sync-disable         -- sync_knowledge() sets knowledge_index=None on failure
  bootstrap-restore-session      -- restore_session() populates a non-empty session_id
  bootstrap-banner               -- display_welcome_banner() produces non-empty rich output
  bootstrap-degrade-hybrid       -- resolve_knowledge_backend() hybrid->fts5 on vec failure
  bootstrap-degrade-hybrid-sync  -- full path: hybrid->fts5 degradation then sync_knowledge()

Prerequisites:
  LLM provider configured (bootstrap-create-deps/startup-statuses skip gracefully
  when credentials are absent).

Usage:
    uv run python evals/eval_bootstrap_e2e.py
"""

import sys
import tempfile
import time
import traceback
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from co_cli.bootstrap._banner import display_welcome_banner
from co_cli.bootstrap._bootstrap import resolve_knowledge_backend, restore_session, sync_knowledge
from co_cli.bootstrap._check import check_agent_llm
from co_cli.context._history import OpeningContextState, SafetyState
from co_cli.context._session import new_session, save_session
from co_cli.deps import CoDeps, CoConfig, CoRuntimeState, CoServices, CoSessionState
from co_cli.display import console
from co_cli.knowledge._index_store import KnowledgeIndex
from co_cli.tools._shell_backend import ShellBackend

from evals._frontend import SilentFrontend


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    id: str
    passed: bool
    detail: str
    skipped: bool = False
    elapsed: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deps(
    tmp_path: Path,
    *,
    knowledge_index: KnowledgeIndex | None = None,
    memory_dir: Path | None = None,
    library_dir: Path | None = None,
) -> CoDeps:
    config = CoConfig(
        session_path=tmp_path / "session.json",
        session_ttl_minutes=60,
        memory_dir=memory_dir or tmp_path / "memory",
        library_dir=library_dir or tmp_path / "library",
    )
    services = CoServices(shell=ShellBackend(), knowledge_index=knowledge_index)
    runtime = CoRuntimeState(opening_ctx_state=OpeningContextState(), safety_state=SafetyState())
    return CoDeps(services=services, config=config, session=CoSessionState(), runtime=runtime)


def _write_memory_file(path: Path, *, body: str) -> None:
    path.write_text(
        "---\nid: 1\ncreated: '2026-01-01T00:00:00+00:00'\nkind: memory\ntags:\n- test\n---\n\n"
        + body + "\n",
        encoding="utf-8",
    )


def _write_article_file(path: Path, *, body: str) -> None:
    fm = {
        "id": 2,
        "kind": "article",
        "created": "2026-01-01T00:00:00+00:00",
        "tags": [],
        "decay_protected": True,
        "origin_url": "https://example.com/eval-test",
    }
    path.write_text(
        f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{body}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def case_create_deps_success() -> CaseResult:
    """create_deps() must return CoDeps without raising on a configured system."""
    from co_cli.config import settings
    from co_cli.bootstrap._bootstrap import create_deps

    # Check prerequisite: LLM must be configured
    config_probe = CoConfig(
        llm_provider=settings.llm_provider,
        llm_api_key=getattr(settings, "llm_api_key", None),
    )
    check = check_agent_llm(config_probe)
    if check.status == "error":
        return CaseResult(
            id="bootstrap-create-deps",
            passed=True,
            detail=f"SKIPPED — LLM not configured ({check.detail})",
            skipped=True,
        )

    t0 = time.monotonic()
    try:
        deps = create_deps()
        elapsed = time.monotonic() - t0
        if not isinstance(deps, CoDeps):
            return CaseResult(
                id="bootstrap-create-deps",
                passed=False,
                detail=f"create_deps() returned {type(deps).__name__}, expected CoDeps",
                elapsed=elapsed,
            )
        return CaseResult(
            id="bootstrap-create-deps",
            passed=True,
            detail="create_deps() returned CoDeps without raising",
            elapsed=elapsed,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return CaseResult(
            id="bootstrap-create-deps",
            passed=False,
            detail=f"create_deps() raised: {exc}\n{traceback.format_exc()}",
            elapsed=elapsed,
        )


def case_startup_statuses_is_list() -> CaseResult:
    """deps.runtime.startup_statuses must be a list after create_deps()."""
    from co_cli.config import settings
    from co_cli.bootstrap._bootstrap import create_deps

    config_probe = CoConfig(
        llm_provider=settings.llm_provider,
        llm_api_key=getattr(settings, "llm_api_key", None),
    )
    check = check_agent_llm(config_probe)
    if check.status == "error":
        return CaseResult(
            id="bootstrap-startup-statuses",
            passed=True,
            detail=f"SKIPPED — LLM not configured ({check.detail})",
            skipped=True,
        )

    t0 = time.monotonic()
    try:
        deps = create_deps()
        elapsed = time.monotonic() - t0
        statuses = deps.runtime.startup_statuses
        if not isinstance(statuses, list):
            return CaseResult(
                id="bootstrap-startup-statuses",
                passed=False,
                detail=f"startup_statuses is {type(statuses).__name__}, expected list",
                elapsed=elapsed,
            )
        return CaseResult(
            id="bootstrap-startup-statuses",
            passed=True,
            detail=f"startup_statuses is list with {len(statuses)} item(s)",
            elapsed=elapsed,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return CaseResult(
            id="bootstrap-startup-statuses",
            passed=False,
            detail=f"create_deps() raised: {exc}",
            elapsed=elapsed,
        )


def case_sync_knowledge_keeps_index() -> CaseResult:
    """sync_knowledge() with a healthy index must not disable it."""
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        memory_dir = tmp / "memory"
        memory_dir.mkdir()
        library_dir = tmp / "library"
        library_dir.mkdir()
        _write_memory_file(memory_dir / "001-eval-mem.md", body="Bootstrap eval memory.")
        _write_article_file(library_dir / "002-eval-art.md", body="Bootstrap eval article.")

        idx = KnowledgeIndex(config=CoConfig(
            knowledge_db_path=tmp / "search.db",
            knowledge_search_backend="fts5",
            knowledge_cross_encoder_reranker_url=None,
        ))
        try:
            deps = _make_deps(tmp, knowledge_index=idx, memory_dir=memory_dir, library_dir=library_dir)
            frontend = SilentFrontend()
            sync_knowledge(deps, frontend)
            elapsed = time.monotonic() - t0

            if deps.services.knowledge_index is None:
                return CaseResult(
                    id="bootstrap-sync-ok",
                    passed=False,
                    detail="sync_knowledge() disabled the index unexpectedly",
                    elapsed=elapsed,
                )
            statuses = frontend.statuses
            if not any("Knowledge synced" in s for s in statuses):
                return CaseResult(
                    id="bootstrap-sync-ok",
                    passed=False,
                    detail=f"Expected 'Knowledge synced' status; got: {statuses}",
                    elapsed=elapsed,
                )
            return CaseResult(
                id="bootstrap-sync-ok",
                passed=True,
                detail="sync_knowledge() kept index active and reported sync status",
                elapsed=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return CaseResult(
                id="bootstrap-sync-ok",
                passed=False,
                detail=f"Unexpected exception: {exc}",
                elapsed=elapsed,
            )
        finally:
            try:
                idx.close()
            except Exception:
                pass


def case_sync_knowledge_disables_on_failure() -> CaseResult:
    """sync_knowledge() must set knowledge_index=None when sync raises."""
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        memory_dir = tmp / "memory"
        memory_dir.mkdir()
        _write_memory_file(memory_dir / "001-eval-mem.md", body="Will fail.")

        idx = KnowledgeIndex(config=CoConfig(
            knowledge_db_path=tmp / "search.db",
            knowledge_search_backend="fts5",
            knowledge_cross_encoder_reranker_url=None,
        ))
        idx.close()  # close before sync so sync_dir raises

        try:
            deps = _make_deps(tmp, knowledge_index=idx, memory_dir=memory_dir)
            frontend = SilentFrontend()
            sync_knowledge(deps, frontend)
            elapsed = time.monotonic() - t0

            if deps.services.knowledge_index is not None:
                return CaseResult(
                    id="bootstrap-sync-disable",
                    passed=False,
                    detail="sync_knowledge() did not disable index after failure",
                    elapsed=elapsed,
                )
            return CaseResult(
                id="bootstrap-sync-disable",
                passed=True,
                detail="sync_knowledge() correctly set knowledge_index=None on failure",
                elapsed=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return CaseResult(
                id="bootstrap-sync-disable",
                passed=False,
                detail=f"Unexpected exception: {exc}",
                elapsed=elapsed,
            )


def case_restore_session_sets_id() -> CaseResult:
    """restore_session() must populate a non-empty session_id on deps."""
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        try:
            deps = _make_deps(tmp)
            frontend = SilentFrontend()
            restore_session(deps, frontend)
            elapsed = time.monotonic() - t0

            session_id = deps.session.session_id
            if not session_id:
                return CaseResult(
                    id="bootstrap-restore-session",
                    passed=False,
                    detail="restore_session() left session_id empty",
                    elapsed=elapsed,
                )
            statuses = frontend.statuses
            has_status = any("Session" in s for s in statuses)
            if not has_status:
                return CaseResult(
                    id="bootstrap-restore-session",
                    passed=False,
                    detail=f"Expected 'Session' status; got: {statuses}",
                    elapsed=elapsed,
                )
            return CaseResult(
                id="bootstrap-restore-session",
                passed=True,
                detail=f"restore_session() set session_id={session_id[:8]}..., reported status",
                elapsed=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return CaseResult(
                id="bootstrap-restore-session",
                passed=False,
                detail=f"Unexpected exception: {exc}",
                elapsed=elapsed,
            )


def case_banner_produces_output() -> CaseResult:
    """display_welcome_banner() must render non-empty rich output."""
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        try:
            deps = _make_deps(tmp)
            deps.session.tool_names = ["tool_a", "tool_b"]
            deps.session.skill_registry = [{"name": "skill_x"}]
            deps.session.slash_command_count = 1

            with console.capture() as cap:
                display_welcome_banner(deps)
            output = cap.get()
            elapsed = time.monotonic() - t0

            if not output.strip():
                return CaseResult(
                    id="bootstrap-banner",
                    passed=False,
                    detail="display_welcome_banner() produced empty output",
                    elapsed=elapsed,
                )
            if "Ready" not in output:
                return CaseResult(
                    id="bootstrap-banner",
                    passed=False,
                    detail=f"Banner missing 'Ready' marker; got: {output[:200]}",
                    elapsed=elapsed,
                )
            return CaseResult(
                id="bootstrap-banner",
                passed=True,
                detail=f"Banner rendered {len(output)} chars, contains 'Ready'",
                elapsed=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return CaseResult(
                id="bootstrap-banner",
                passed=False,
                detail=f"Unexpected exception: {exc}",
                elapsed=elapsed,
            )


def case_degrade_hybrid_to_fts5() -> CaseResult:
    """resolve_knowledge_backend() must degrade hybrid->fts5 on vec setup failure."""
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        config = CoConfig(
            knowledge_db_path=tmp / "search.db",
            knowledge_search_backend="hybrid",
            knowledge_embedding_provider="tei",
            knowledge_embed_api_url="http://127.0.0.1:1/embed",
            knowledge_cross_encoder_reranker_url=None,
        )
        try:
            resolved_config, knowledge_index, statuses = resolve_knowledge_backend(config)
            elapsed = time.monotonic() - t0

            failures = []
            if resolved_config.knowledge_search_backend != "fts5":
                failures.append(
                    f"backend={resolved_config.knowledge_search_backend!r}, expected 'fts5'"
                )
            if knowledge_index is None:
                failures.append("knowledge_index is None — FTS should remain available after hybrid failure")
            if not any("using fts5" in s for s in statuses):
                failures.append(f"No 'using fts5' status message; got: {statuses}")

            if knowledge_index is not None:
                try:
                    tables = {
                        row[0]
                        for row in knowledge_index._conn.execute(
                            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
                        ).fetchall()
                    }
                    if "docs_fts" not in tables:
                        failures.append("docs_fts table missing after fallback to fts5")
                    if any(t.startswith("docs_vec_") for t in tables):
                        failures.append("docs_vec_{dims} table unexpectedly present after fallback to fts5")
                finally:
                    knowledge_index.close()

            if failures:
                return CaseResult(
                    id="bootstrap-degrade-hybrid",
                    passed=False,
                    detail="; ".join(failures),
                    elapsed=elapsed,
                )
            return CaseResult(
                id="bootstrap-degrade-hybrid",
                passed=True,
                detail=f"hybrid->fts5 degraded cleanly; status: {statuses[0] if statuses else '(none)'}",
                elapsed=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return CaseResult(
                id="bootstrap-degrade-hybrid",
                passed=False,
                detail=f"Unexpected exception: {exc}\n{traceback.format_exc()}",
                elapsed=elapsed,
            )


def case_degrade_hybrid_then_sync() -> CaseResult:
    """Full path: hybrid config -> fts5 degradation -> sync_knowledge() on degraded index.

    This validates that the fts5 index returned by resolve_knowledge_backend()
    is actually usable for sync — not just that degradation resolves correctly.
    """
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        memory_dir = tmp / "memory"
        memory_dir.mkdir()
        _write_memory_file(memory_dir / "001-eval-degrade.md", body="Degraded hybrid bootstrap memory.")

        config = CoConfig(
            knowledge_db_path=tmp / "search.db",
            knowledge_search_backend="hybrid",
            knowledge_embedding_provider="tei",
            knowledge_embed_api_url="http://127.0.0.1:1/embed",
            knowledge_cross_encoder_reranker_url=None,
            session_path=tmp / "session.json",
            memory_dir=memory_dir,
            library_dir=tmp / "library",
        )
        try:
            resolved_config, knowledge_index, statuses = resolve_knowledge_backend(config)
            if resolved_config.knowledge_search_backend != "fts5" or knowledge_index is None:
                elapsed = time.monotonic() - t0
                return CaseResult(
                    id="bootstrap-degrade-hybrid-sync",
                    passed=False,
                    detail=f"Precondition failed: backend={resolved_config.knowledge_search_backend!r}, index={knowledge_index}",
                    elapsed=elapsed,
                )

            services = CoServices(shell=ShellBackend(), knowledge_index=knowledge_index)
            runtime = CoRuntimeState(
                startup_statuses=list(statuses),
                opening_ctx_state=OpeningContextState(),
                safety_state=SafetyState(),
            )
            deps = CoDeps(
                services=services,
                config=resolved_config,
                session=CoSessionState(),
                runtime=runtime,
            )

            frontend = SilentFrontend()
            sync_knowledge(deps, frontend)
            elapsed = time.monotonic() - t0

            failures = []
            if deps.services.knowledge_index is None:
                failures.append("sync_knowledge() disabled the index on the degraded fts5 backend")
            if not any("Knowledge synced" in s for s in frontend.statuses):
                failures.append(f"Expected 'Knowledge synced' status; got: {frontend.statuses}")

            if not failures and deps.services.knowledge_index is not None:
                results_found = deps.services.knowledge_index.search(
                    "Degraded hybrid bootstrap", source="memory", limit=5
                )
                if not any("001-eval-degrade.md" in r.path for r in results_found):
                    failures.append("Memory file not findable after sync on degraded fts5 index")

            try:
                if deps.services.knowledge_index is not None:
                    deps.services.knowledge_index.close()
            except Exception:
                pass

            if failures:
                return CaseResult(
                    id="bootstrap-degrade-hybrid-sync",
                    passed=False,
                    detail="; ".join(failures),
                    elapsed=elapsed,
                )
            return CaseResult(
                id="bootstrap-degrade-hybrid-sync",
                passed=True,
                detail="hybrid->fts5 degraded, sync_knowledge() indexed memory and kept index active",
                elapsed=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return CaseResult(
                id="bootstrap-degrade-hybrid-sync",
                passed=False,
                detail=f"Unexpected exception: {exc}\n{traceback.format_exc()}",
                elapsed=elapsed,
            )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all() -> list[CaseResult]:
    cases = [
        case_create_deps_success,
        case_startup_statuses_is_list,
        case_sync_knowledge_keeps_index,
        case_sync_knowledge_disables_on_failure,
        case_restore_session_sets_id,
        case_banner_produces_output,
        case_degrade_hybrid_to_fts5,
        case_degrade_hybrid_then_sync,
    ]
    results = []
    for fn in cases:
        print(f"  running {fn.__name__.replace('case_', '')} ...", flush=True)
        results.append(fn())
    return results


def print_report(results: list[CaseResult]) -> None:
    passed = sum(1 for r in results if r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    print()
    print("=" * 70)
    print("BOOTSTRAP E2E EVAL REPORT")
    print("=" * 70)
    print(f"{'Case':<35} {'Result':<8} {'Time':>6}  Detail")
    print("-" * 70)
    for r in results:
        if r.skipped:
            status = "SKIP"
        elif r.passed:
            status = "PASS"
        else:
            status = "FAIL"
        elapsed_str = f"{r.elapsed:.2f}s"
        # Truncate detail for table row
        detail = r.detail.split("\n")[0][:60]
        print(f"{r.id:<35} {status:<8} {elapsed_str:>6}  {detail}")
    print("-" * 70)
    print(f"Total: {total}  Passed: {passed}  Skipped: {skipped}  Failed: {failed}")
    print("=" * 70)

    if failed:
        print()
        print("FAILURES:")
        for r in results:
            if not r.passed:
                print(f"\n  [{r.id}]")
                for line in r.detail.splitlines():
                    print(f"    {line}")


def main() -> None:
    print("Bootstrap E2E Eval")
    print("-" * 40)
    results = run_all()
    print_report(results)
    failed = sum(1 for r in results if not r.passed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
