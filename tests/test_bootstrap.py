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
    memory_dir: Path,
    library_dir: Path,
    session_path: Path,
    ttl_minutes: int = 60,
    n_skills: int = 2,
) -> dict:
    return await run_bootstrap(
        deps,
        frontend,
        memory_dir=memory_dir,
        library_dir=library_dir,
        session_path=session_path,
        session_ttl_minutes=ttl_minutes,
        n_skills=n_skills,
    )


def test_bootstrap_syncs_knowledge_and_restores_fresh_session(tmp_path: Path) -> None:
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    library_dir = tmp_path / ".co-cli" / "library"
    memory_file = memory_dir / "001-wakeup-memory.md"
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
            memory_dir=memory_dir,
            library_dir=library_dir,
            session_path=session_path,
        )
    )

    assert out["session_id"] == session_data["session_id"]
    assert deps.session_id == session_data["session_id"]
    results = idx.search("wakeup sync", source="memory", limit=5)
    assert results, "Expected synced knowledge to be searchable in the real index"
    assert any(r.path == str(memory_file) for r in results)
    idx.close()


def test_bootstrap_two_pass_sync_partitions_by_kind(tmp_path: Path) -> None:
    """Bootstrap syncs kind:memory under source='memory' and kind:article under source='library'."""
    import yaml as _yaml

    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    library_dir = tmp_path / ".co-cli" / "library"
    library_dir.mkdir(parents=True)

    mem_file = memory_dir / "001-wakeup-memory.md"
    _write_knowledge_file(mem_file, mem_id=1, body="Memory content for partition test.")

    art_file = library_dir / "002-test-article.md"
    art_fm = {
        "id": 2, "kind": "article", "created": "2026-01-01T00:00:00+00:00",
        "tags": [], "decay_protected": True, "origin_url": "https://example.com/test",
    }
    art_file.write_text(
        f"---\n{_yaml.dump(art_fm, default_flow_style=False)}---\n\nArticle for partition test.\n",
        encoding="utf-8",
    )

    session_path = tmp_path / ".co-cli" / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    from co_cli._session import new_session, save_session
    session_data = new_session()
    save_session(session_path, session_data)

    idx = KnowledgeIndex(tmp_path / "search.db", backend="fts5", reranker_provider="none")
    deps = CoDeps(shell=ShellBackend(), knowledge_index=idx, knowledge_search_backend="fts5")
    frontend = TerminalFrontend()

    asyncio.run(_run(deps, frontend, memory_dir=memory_dir, library_dir=library_dir, session_path=session_path))

    mem_results = idx.search("partition test", source="memory", limit=5)
    assert any(r.path == str(mem_file) for r in mem_results), \
        "Memory file must be searchable under source='memory'"

    art_results = idx.search("partition test", source="library", limit=5)
    assert any(r.path == str(art_file) for r in art_results), \
        "Article file must be searchable under source='library'"
    idx.close()


def test_bootstrap_disables_index_when_sync_fails(tmp_path: Path) -> None:
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    library_dir = tmp_path / ".co-cli" / "library"
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
            memory_dir=memory_dir,
            library_dir=library_dir,
            session_path=session_path,
        )
    )

    assert deps.knowledge_index is None


def test_bootstrap_stale_session_creates_new_session(tmp_path: Path) -> None:
    """Expired session at wake-up should create a fresh session id."""
    memory_dir = tmp_path / ".co-cli" / "memory"
    memory_dir.mkdir(parents=True)
    library_dir = tmp_path / ".co-cli" / "library"
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
            memory_dir=memory_dir,
            library_dir=library_dir,
            session_path=session_path,
            ttl_minutes=60,
        )
    )

    assert out["session_id"] != stale["session_id"]
    assert deps.session_id == out["session_id"]



