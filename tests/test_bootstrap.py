"""Functional tests for startup bootstrap flow (real components only)."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from co_cli._bootstrap import run_bootstrap
from co_cli._session import new_session, save_session
from co_cli.deps import CoDeps
from co_cli.display import TerminalFrontend
from co_cli.knowledge_index import KnowledgeIndex
from co_cli.shell_backend import ShellBackend


def _write_knowledge_file(path: Path, *, mem_id: int, body: str) -> None:
    path.write_text(
        (
            "---\n"
            f"id: {mem_id}\n"
            "created: '2026-03-01T00:00:00+00:00'\n"
            "kind: memory\n"
            "tags:\n"
            "- wakeup\n"
            "---\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )


async def _run(
    deps: CoDeps,
    frontend: TerminalFrontend,
    *,
    knowledge_dir: Path,
    session_path: Path,
    ttl_minutes: int = 60,
    n_skills: int = 2,
) -> dict:
    return await run_bootstrap(
        deps,
        frontend,
        knowledge_dir=knowledge_dir,
        session_path=session_path,
        session_ttl_minutes=ttl_minutes,
        n_skills=n_skills,
    )


def test_bootstrap_syncs_knowledge_and_restores_fresh_session(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    memory_file = knowledge_dir / "001-wakeup-memory.md"
    _write_knowledge_file(memory_file, mem_id=1, body="Wakeup sync writes this entry to the index.")
    session_path = tmp_path / ".co-cli" / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_data = new_session()
    save_session(session_path, session_data)

    idx = KnowledgeIndex(tmp_path / "search.db", backend="fts5", reranker_provider="none")
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_index=idx,
        knowledge_search_backend="fts5",
    )
    frontend = TerminalFrontend()

    out = asyncio.run(
        _run(
            deps,
            frontend,
            knowledge_dir=knowledge_dir,
            session_path=session_path,
        )
    )

    assert out["session_id"] == session_data["session_id"]
    assert deps.session_id == session_data["session_id"]
    results = idx.search("wakeup sync", source="memory", limit=5)
    assert results, "Expected synced knowledge to be searchable in the real index"
    assert any(r.path == str(memory_file) for r in results)
    idx.close()


def test_bootstrap_disables_index_when_sync_fails(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    session_path = tmp_path / ".co-cli" / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)

    idx = KnowledgeIndex(tmp_path / "search.db", backend="fts5", reranker_provider="none")
    idx.close()
    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_index=idx,
        knowledge_search_backend="fts5",
    )
    frontend = TerminalFrontend()

    asyncio.run(
        _run(
            deps,
            frontend,
            knowledge_dir=knowledge_dir,
            session_path=session_path,
        )
    )

    assert deps.knowledge_index is None


def test_bootstrap_stale_session_creates_new_session(tmp_path: Path) -> None:
    """Expired session at wake-up should create a fresh session id."""
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    session_path = tmp_path / ".co-cli" / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    stale = new_session()
    stale["last_used_at"] = (datetime.now(timezone.utc) - timedelta(minutes=180)).isoformat()
    save_session(session_path, stale)

    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_index=None,
        knowledge_search_backend="grep",
    )
    frontend = TerminalFrontend()

    out = asyncio.run(
        _run(
            deps,
            frontend,
            knowledge_dir=knowledge_dir,
            session_path=session_path,
            ttl_minutes=60,
        )
    )

    assert out["session_id"] != stale["session_id"]
    assert deps.session_id == out["session_id"]


def test_bootstrap_skips_sync_when_index_unavailable(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / ".co-cli" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    session_path = tmp_path / ".co-cli" / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)

    deps = CoDeps(
        shell=ShellBackend(),
        knowledge_index=None,
        knowledge_search_backend="grep",
    )
    frontend = TerminalFrontend()

    asyncio.run(
        _run(
            deps,
            frontend,
            knowledge_dir=knowledge_dir,
            session_path=session_path,
            n_skills=0,
        )
    )
    assert deps.knowledge_index is None
