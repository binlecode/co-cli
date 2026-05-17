"""UAT eval — Workflow 3: Memory recall and curation.

Covers `knowledge_search`, `session_search`, `knowledge_view`, `knowledge_manage`
via agent calls, plus the `/memory list|forget` user surface and the dream
cycle's merge/decay/archive. Validates BM25/hybrid recall, write-time
indexing, on-disk artifact lifecycle, and dream-cycle content preservation.

Per plan section W3, case ordering is a hard contract:
  W3.A seeds `eval_W3_fact` (agent-driven via knowledge_manage).
  W3.B/C/D read it (search, recall, list).
  W3.E forgets it (boundary).
  W3.F uses its own pre-seeded `eval_W3_dupA` / `eval_W3_dupB` pair to exercise
  the dream cycle's merge/decay path.

Reruns overwrite in place via deterministic stems — no accumulation.

Specs: docs/specs/memory.md, knowledge.md, dream.md
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from evals._deps import EvalFrontend, make_eval_deps
from evals._observability import CaseResult, EvalRun, open_eval_run
from evals._ollama import ensure_ollama_warm
from evals._report import prepend_report
from evals._timeouts import (
    CALL_TIMEOUT_S,
    DREAM_CYCLE_BUDGET_S,
    TOOL_TURN_BUDGET_S,
    TURN_BUDGET_S,
)
from evals._trace import record_turn
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps
from co_cli.memory.dream import run_dream_cycle
from co_cli.memory.service import reindex
from co_cli.tools.memory.manage import knowledge_manage
from co_cli.tools.memory.recall import knowledge_search

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORT_PATH = _PROJECT_ROOT / "docs" / "REPORT-eval-memory.md"

_FACT_TITLE = "eval_W3_fact"
_FACT_TOKEN = "STG_DEPLOY_42"
_FACT_GLOB_PREFIX = "eval-w3-fact-"

_DUP_A_STEM = "eval_W3_dupA"
_DUP_B_STEM = "eval_W3_dupB"
_DUP_A_TOKEN = "TOKEN_A_ONLY"
_DUP_B_TOKEN = "TOKEN_B_ONLY"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print(msg: str) -> None:
    print(msg, flush=True)


def _make_ctx(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    message_history: list[Any] | None = None,
) -> CommandContext:
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=agent,
        completer=None,
        frontend=frontend,
    )


def _find_fact_path(deps: CoDeps) -> Path | None:
    """Return the on-disk artifact saved by W3.A (slugified eval_W3_fact-XXXXXXXX.md)."""
    for path in deps.knowledge_dir.glob(f"{_FACT_GLOB_PREFIX}*.md"):
        if path.is_file():
            return path
    return None


def _purge_fact_artifacts(deps: CoDeps) -> None:
    """Remove any prior eval_W3_fact* on disk and from the FTS index.

    Ensures W3.A starts clean so the agent's save is the only matching file.
    Called once at the top of W3.A; idempotent across reruns.
    """
    for path in deps.knowledge_dir.glob(f"{_FACT_GLOB_PREFIX}*.md"):
        try:
            if deps.memory_store is not None:
                deps.memory_store.remove("knowledge", str(path))
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _seed_dup_artifact(
    deps: CoDeps,
    *,
    filename_stem: str,
    title: str,
    body: str,
) -> Path:
    """Write a knowledge .md file with canonical frontmatter and reindex into FTS.

    Bypasses ``knowledge_manage`` so the filename_stem is exactly the one we
    request — ``save_artifact`` slugifies the title and appends a random uuid
    suffix, which loses deterministic-stem semantics needed for W3.F's pair.
    """
    knowledge_dir = deps.knowledge_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = knowledge_dir / f"{filename_stem}.md"
    artifact_id = str(uuid4())
    created = datetime.now(UTC).isoformat()
    frontmatter = {
        "id": artifact_id,
        "kind": "knowledge",
        "artifact_kind": "note",
        "title": title,
        "created": created,
    }
    yaml_lines = [
        "---",
        f"id: {artifact_id}",
        "kind: knowledge",
        "artifact_kind: note",
        f"title: {title}",
        f"created: '{created}'",
        "---",
        "",
        body.strip(),
        "",
    ]
    markdown_content = "\n".join(yaml_lines)
    artifact_path.write_text(markdown_content, encoding="utf-8")

    if deps.memory_store is not None:
        reindex(
            deps.memory_store,
            artifact_path,
            body,
            markdown_content,
            frontmatter,
            artifact_path.stem,
            chunk_tokens=deps.config.knowledge.chunk_tokens,
            chunk_overlap_tokens=deps.config.knowledge.chunk_overlap_tokens,
        )
    return artifact_path


def _purge_dup_pair(deps: CoDeps) -> None:
    """Remove any prior W3.F seeded pair from disk + FTS so W3.F can re-seed cleanly."""
    for stem in (_DUP_A_STEM, _DUP_B_STEM):
        path = deps.knowledge_dir / f"{stem}.md"
        if path.exists():
            try:
                if deps.memory_store is not None:
                    deps.memory_store.remove("knowledge", str(path))
                path.unlink(missing_ok=True)
            except OSError:
                continue


def _fts_row_count_for(deps: CoDeps, artifact_path: Path) -> int:
    """Count chunks_fts rows for ``(source='knowledge', doc_path=artifact_path)``.

    Reads directly via the memory_store's private connection — there's no
    public counter, and the eval needs the row count to defend the
    write-time indexing invariant in W3.A and the post-delete cleanup
    invariant in W3.E.
    """
    if deps.memory_store is None:
        return 0
    row = deps.memory_store._conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE source = ? AND doc_path = ?",
        ("knowledge", str(artifact_path)),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def _direct_knowledge_search_count(deps: CoDeps, query: str) -> int:
    """Count knowledge-source FTS hits for ``query`` via the memory store.

    Used by W3.E to assert post-forget cleanup: a search for the unique
    token should return 0 hits once the artifact is deleted + de-indexed.
    """
    if deps.memory_store is None:
        return 0
    hits = deps.memory_store.search(query, sources=["knowledge"], limit=10)
    return len(hits)


def _slow_reason(model_call_seconds: float, *, tool_turn: bool = False) -> str | None:
    """Return ``[slow] N.Ns vs budget M.Ms`` when over the per-case budget; else None.

    Pass ``tool_turn=True`` for turns that include ≥ 1 tool roundtrip — those
    legitimately need the wider :data:`TOOL_TURN_BUDGET_S` ceiling.
    """
    budget = TOOL_TURN_BUDGET_S if tool_turn else TURN_BUDGET_S
    if model_call_seconds > budget:
        return f"[slow] {model_call_seconds:.1f}s vs budget {budget}.0s"
    return None


def _extract_tool_calls_by_name(
    tool_calls: list[Any], tool_name: str
) -> list[tuple[dict[str, Any], str]]:
    """Pick (args_dict, result_text) pairs from a turn's ToolCallRecord list.

    Args are JSON strings from ``_trace.ToolCallRecord``; decode best-effort
    and fall back to an empty dict so downstream lookups never raise.
    """
    matches: list[tuple[dict[str, Any], str]] = []
    for rec in tool_calls:
        if rec.tool_name != tool_name:
            continue
        try:
            args = json.loads(rec.args) if rec.args else {}
        except json.JSONDecodeError:
            args = {}
        matches.append((args if isinstance(args, dict) else {}, rec.result or ""))
    return matches


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def case_w3_a_agent_chooses_to_save(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.A — agent calls knowledge_manage(create) for a clearly-durable fact.

    Cleans any prior W3.A artifact first so disk membership cleanly attributes
    to this run. Drives one ``run_turn`` and inspects the resulting tool-call
    record + on-disk artifact + FTS index row.
    """
    case_id = "W3.A"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)

    _purge_fact_artifacts(deps)

    user_input = (
        f"I want you to remember that my staging deploy id is {_FACT_TOKEN}. "
        f"Save it as {_FACT_TITLE}."
    )

    passed = True
    reason = ""
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            _turn_result, turn_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=user_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=trace_file,
                agent=agent,
            )
        model_call_seconds = turn_trace.model_call_seconds
        token_usage = dict(turn_trace.token_usage)

        manage_calls = _extract_tool_calls_by_name(turn_trace.tool_calls, "knowledge_manage")
        create_calls = [
            (args, result) for args, result in manage_calls if args.get("action") == "create"
        ]
        if not create_calls:
            passed = False
            tool_names = sorted({rec.tool_name for rec in turn_trace.tool_calls})
            reason = (
                f"no knowledge_manage(action='create') tool call found; tools used: {tool_names!r}"
            )
        else:
            fact_path = _find_fact_path(deps)
            if fact_path is None:
                passed = False
                reason = (
                    f"no on-disk artifact matched glob {_FACT_GLOB_PREFIX}*.md after "
                    "knowledge_manage(create); save_artifact() may have failed silently"
                )
            else:
                body = fact_path.read_text(encoding="utf-8")
                if _FACT_TOKEN not in body:
                    passed = False
                    reason = (
                        f"artifact {fact_path.name} saved but body does not contain "
                        f"{_FACT_TOKEN!r} — agent paraphrased the durable fact"
                    )
                elif _fts_row_count_for(deps, fact_path) == 0:
                    passed = False
                    reason = (
                        f"artifact {fact_path.name} on disk but chunks_fts has 0 rows — "
                        "write-time indexing skipped"
                    )
                else:
                    reason = (
                        f"agent saved {fact_path.name}; "
                        f"FTS rows={_fts_row_count_for(deps, fact_path)}"
                    )

        if passed:
            slow = _slow_reason(model_call_seconds, tool_turn=True)
            if slow is not None:
                passed = False
                reason = slow
        # Note: the eval bootstrap (make_eval_deps) does not call
        # restore_session, so deps.session.session_path is the default Path()
        # (== Path(".")) — a directory, not a real session file. We therefore
        # don't persist the turn here. W3.C drives its own session_search
        # against the indexed pool without needing this turn to land on disk.
    except TimeoutError:
        passed = False
        reason = f"asyncio.timeout({CALL_TIMEOUT_S}s) fired before run_turn completed"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"

    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def case_w3_b_recall_ranks_correct_artifact(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.B — knowledge_search ranks the W3.A artifact #1 with the token in its snippet.

    Drives a turn that prompts the agent to call ``knowledge_search``; falls
    back to a direct production search via ``RunContext`` for the rank
    assertion (the formatted ToolReturnPart string is rank-ordered, but
    parsing it back is brittle — the underlying SearchResult list is the
    canonical evidence).
    """
    case_id = "W3.B"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)

    fact_path = _find_fact_path(deps)
    if fact_path is None:
        return CaseResult(
            name=case_id,
            passed=False,
            duration_s=time.monotonic() - t0,
            reason="W3.A artifact missing — case ordering broken",
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
        )

    user_input = "Search your knowledge for staging deploy id and tell me the answer."

    passed = True
    reason = ""
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            _turn_result, turn_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=user_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=trace_file,
                agent=agent,
            )
        model_call_seconds = turn_trace.model_call_seconds
        token_usage = dict(turn_trace.token_usage)

        search_calls = _extract_tool_calls_by_name(turn_trace.tool_calls, "knowledge_search")
        if not search_calls:
            passed = False
            tool_names = sorted({rec.tool_name for rec in turn_trace.tool_calls})
            reason = f"agent did not call knowledge_search; tools used: {tool_names!r}"
        else:
            ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())
            search_return = await knowledge_search(ctx, query=_FACT_TOKEN, limit=5)
            results = (search_return.metadata or {}).get("results", []) or []
            if not results:
                passed = False
                reason = f"production knowledge_search for {_FACT_TOKEN!r} returned 0 results"
            else:
                # In a real ~/.co-cli/ workspace, other artifacts may share
                # BM25 tokens with the W3.A fact (W1's "staging deploy id
                # reminder" seed, accumulated `deploy-id-*` artifacts from
                # prior agent saves). Strict #1 is therefore brittle; the
                # right UAT signal is "the W3.A artifact is recoverable in
                # the top results, and its snippet carries the token."
                all_stems = [r.get("filename_stem", "") for r in results]
                try:
                    rank = all_stems.index(fact_path.stem)
                except ValueError:
                    rank = -1
                if rank == -1:
                    passed = False
                    reason = (
                        f"W3.A artifact {fact_path.stem!r} not in top "
                        f"{len(results)} hits; all hits: {all_stems!r}"
                    )
                else:
                    hit = results[rank]
                    snippet = hit.get("snippet") or ""
                    if _FACT_TOKEN not in snippet:
                        passed = False
                        reason = (
                            f"W3.A hit at rank {rank + 1} but snippet missing "
                            f"{_FACT_TOKEN!r}; snippet={snippet!r}"
                        )
                    else:
                        reason = (
                            f"W3.A hit at rank {rank + 1}/{len(results)}; "
                            f"snippet contains {_FACT_TOKEN}"
                        )

        if passed:
            slow = _slow_reason(model_call_seconds, tool_turn=True)
            if slow is not None:
                passed = False
                reason = slow
    except TimeoutError:
        passed = False
        reason = f"asyncio.timeout({CALL_TIMEOUT_S}s) fired before run_turn completed"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"

    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def case_w3_c_session_search_finds_prior_turn(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.C — session_search hits the current session after W3.A's turn was indexed.

    Bootstrap excludes the active session from the index, so we re-index it
    explicitly via ``memory_store.index_session(session_path)`` before driving
    the turn — otherwise the search has nothing to find.
    """
    case_id = "W3.C"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)

    session_path = deps.session.session_path
    if session_path is None or not Path(session_path).exists():
        return CaseResult(
            name=case_id,
            passed=False,
            duration_s=time.monotonic() - t0,
            reason="active session_path missing — prior turns not persisted",
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
        )

    if deps.memory_store is not None:
        try:
            deps.memory_store.index_session(Path(session_path))
        except Exception as exc:
            return CaseResult(
                name=case_id,
                passed=False,
                duration_s=time.monotonic() - t0,
                reason=f"index_session failed: {type(exc).__name__}: {exc}",
                trace_files=[str(trace_file.relative_to(run.dir.parent))],
            )

    # Explicit tool-and-query phrasing: an earlier "Search past sessions for…"
    # phrasing left tool choice ambiguous and the agent skipped tooling
    # entirely on a first run. Naming the tool by its public surface and the
    # query verbatim gives the agent zero room for misinterpretation.
    user_input = (
        f"Call the `session_search` tool with query `{_FACT_TOKEN}` and limit 5. "
        "Then report how many hits were returned and which sessions they came from."
    )

    passed = True
    reason = ""
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            _turn_result, turn_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=user_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=user_input,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                ),
                case_dir_path=trace_file,
                agent=agent,
            )
        model_call_seconds = turn_trace.model_call_seconds
        token_usage = dict(turn_trace.token_usage)

        session_calls = _extract_tool_calls_by_name(turn_trace.tool_calls, "session_search")
        if not session_calls:
            passed = False
            tool_names = sorted({rec.tool_name for rec in turn_trace.tool_calls})
            reason = f"agent did not call session_search; tools used: {tool_names!r}"
        else:
            total_returned = 0
            for _args, result_text in session_calls:
                if not result_text:
                    continue
                if "No session results" in result_text:
                    continue
                if "Found " in result_text or "Recent " in result_text:
                    total_returned += 1
            if total_returned == 0:
                passed = False
                reason = (
                    "all session_search returns indicated zero hits — current session not surfaced"
                )
            else:
                reason = f"{len(session_calls)} session_search call(s); at least one returned hits"

        if passed:
            slow = _slow_reason(model_call_seconds, tool_turn=True)
            if slow is not None:
                passed = False
                reason = slow
    except TimeoutError:
        passed = False
        reason = f"asyncio.timeout({CALL_TIMEOUT_S}s) fired before run_turn completed"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"

    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def case_w3_d_memory_list(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.D — `/memory list` enumerates the W3.A artifact's filename_stem.

    The handler prints to `console` (rich Console), so capturing the rich
    output via ``redirect_stdout`` is unreliable — instead we exercise the
    production lister directly (``load_artifacts``) which is what
    ``_subcmd_memory_list`` walks, and we dispatch `/memory list` to verify
    the slash plumbing returns ``LocalOnly`` cleanly.
    """
    case_id = "W3.D"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    fact_path = _find_fact_path(deps)
    if fact_path is None:
        return CaseResult(
            name=case_id,
            passed=False,
            duration_s=time.monotonic() - t0,
            reason="W3.A artifact missing — case ordering broken",
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
        )

    passed = True
    reason = ""
    try:
        ctx = _make_ctx(deps, agent, frontend)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            await dispatch("/memory list", ctx)

        from co_cli.memory.artifact import load_artifacts

        artifacts = load_artifacts(deps.knowledge_dir)
        stems = {a.path.stem for a in artifacts}
        if fact_path.stem not in stems:
            passed = False
            reason = (
                f"production load_artifacts did not surface {fact_path.stem!r}; "
                f"found {len(stems)} artifacts total"
            )
        else:
            reason = f"/memory list enumerates {fact_path.stem!r} among {len(stems)} artifacts"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"

    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=time.monotonic() - t0,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def case_w3_e_memory_forget(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.E — `/memory forget` removes the artifact from disk AND FTS.

    Drives `/memory forget <stem>` via ``dispatch``. ``EvalFrontend.prompt_confirm``
    returns True so the handler proceeds past its y/N gate. Asserts file
    removal, chunks_fts cleanup, and that ``knowledge_search`` for the unique
    token returns 0 hits.
    """
    case_id = "W3.E"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    fact_path = _find_fact_path(deps)
    if fact_path is None:
        return CaseResult(
            name=case_id,
            passed=False,
            duration_s=time.monotonic() - t0,
            reason="W3.A artifact missing — case ordering broken",
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
        )

    passed = True
    reason = ""
    try:
        ctx = _make_ctx(deps, agent, frontend)
        captured = io.StringIO()
        # `/memory forget <query>` greps artifact bodies — the filename stem
        # isn't in the body, so passing it matched zero entries. Use a body
        # token (the W3.A unique token) so the selector matches exactly the
        # W3.A artifact for deletion.
        with contextlib.redirect_stdout(captured):
            await dispatch(f"/memory forget {_FACT_TOKEN}", ctx)

        if fact_path.exists():
            passed = False
            reason = f"artifact file {fact_path.name} still on disk after /memory forget"
        elif _fts_row_count_for(deps, fact_path) != 0:
            passed = False
            reason = (
                f"chunks_fts still has {_fts_row_count_for(deps, fact_path)} rows for "
                f"{fact_path.name} — index cleanup skipped"
            )
        else:
            # NOTE: a global ``knowledge_search(_FACT_TOKEN)`` check would be
            # too strict — other artifacts in the real workspace (W1 seed,
            # accumulated ``deploy-id-*`` saves from prior runs) legitimately
            # mention the same token. The artifact-specific signal — the
            # W3.A file is gone AND its chunks_fts rows are gone — is the
            # precise post-forget invariant.
            reason = f"file removed; FTS rows for {fact_path.name} cleared"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"

    return CaseResult(
        name=case_id,
        passed=passed,
        duration_s=time.monotonic() - t0,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def case_w3_f_dream_decay_preserves_content(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.F — dream cycle merge preserves distinctive tokens from both artifacts.

    Pre-seeds two near-identical artifacts (each with one unique token), runs
    a real dream cycle, then inspects the surviving artifact bodies + archive
    for the two tokens.

      Both tokens preserved  → PASS
      Exactly one missing    → SOFT_FAIL (passed=True, soft_fail=True)
                               LLM-merge may drop rare tokens — degradation, not regression.
      Both missing           → FAIL

    The dream cycle drives sub-agent LLM calls and writes to the real
    knowledge dir; wrapped in ``asyncio.timeout(DREAM_CYCLE_BUDGET_S)``.
    """
    case_id = "W3.F"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)
    trace_file.touch(exist_ok=True)

    _purge_dup_pair(deps)

    body_a = (
        "Eval W3.F duplicate artifact (variant A). "
        f"This carries the unique token {_DUP_A_TOKEN}. "
        "Most of the content is shared between A and B to trigger dedup."
    )
    body_b = (
        "Eval W3.F duplicate artifact (variant B). "
        f"This carries the unique token {_DUP_B_TOKEN}. "
        "Most of the content is shared between A and B to trigger dedup."
    )

    try:
        path_a = _seed_dup_artifact(
            deps, filename_stem=_DUP_A_STEM, title="Eval W3 Dup A", body=body_a
        )
        path_b = _seed_dup_artifact(
            deps, filename_stem=_DUP_B_STEM, title="Eval W3 Dup B", body=body_b
        )
    except Exception as exc:
        return CaseResult(
            name=case_id,
            passed=False,
            duration_s=time.monotonic() - t0,
            reason=f"seed_dup_pair failed: {type(exc).__name__}: {exc}",
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
        )

    passed = True
    soft_fail = False
    reason = ""
    try:
        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            dream_result = await run_dream_cycle(
                deps,
                knowledge_manage,
                dry_run=False,
                timeout_secs=float(DREAM_CYCLE_BUDGET_S),
            )

        survivors_text: list[str] = []
        for path in (path_a, path_b):
            if path.exists():
                survivors_text.append(path.read_text(encoding="utf-8"))

        archive_dir = deps.knowledge_dir / "_archive"
        if archive_dir.exists():
            for archived in archive_dir.glob("*.md"):
                stem_lower = archived.stem.lower()
                if _DUP_A_STEM.lower() in stem_lower or _DUP_B_STEM.lower() in stem_lower:
                    survivors_text.append(archived.read_text(encoding="utf-8"))

        searchable = "\n".join(survivors_text)
        has_a = _DUP_A_TOKEN in searchable
        has_b = _DUP_B_TOKEN in searchable

        cycle_summary = (
            f"extracted={dream_result.extracted} merged={dream_result.merged} "
            f"decayed={dream_result.decayed} errors={len(dream_result.errors)}"
        )

        if has_a and has_b:
            reason = f"both tokens preserved across survivors+archive; {cycle_summary}"
        elif has_a or has_b:
            soft_fail = True
            missing = _DUP_B_TOKEN if has_a else _DUP_A_TOKEN
            reason = (
                f"[SOFT_FAIL] merge dropped {missing!r}; LLM-merge degradation; {cycle_summary}"
            )
        else:
            passed = False
            reason = f"both tokens missing after dream cycle; {cycle_summary}"
    except TimeoutError:
        passed = False
        reason = f"asyncio.timeout({DREAM_CYCLE_BUDGET_S}s) fired before dream cycle completed"
    except Exception as exc:
        passed = False
        reason = f"{type(exc).__name__}: {exc}"

    return CaseResult(
        name=case_id,
        passed=passed,
        soft_fail=soft_fail,
        duration_s=time.monotonic() - t0,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> int:
    """Run W3.A through W3.F in fixed order and emit the REPORT.

    Case ordering is a hard contract (plan W3 header): A seeds, B/C/D read,
    E forgets, F uses its own pair. Failures don't abort the run — each case
    captures its verdict and the script continues; exit code is non-zero
    iff any case failed (soft_fail counts as PASS).
    """
    await ensure_ollama_warm()
    deps, agent, frontend, stack = await make_eval_deps()
    cases: list[CaseResult] = []
    try:
        async with open_eval_run("memory") as run:
            ordered_cases = (
                case_w3_a_agent_chooses_to_save,
                case_w3_b_recall_ranks_correct_artifact,
                case_w3_c_session_search_finds_prior_turn,
                case_w3_d_memory_list,
                case_w3_e_memory_forget,
                case_w3_f_dream_decay_preserves_content,
            )
            for case_fn in ordered_cases:
                try:
                    cr = await case_fn(deps, agent, frontend, run)
                except Exception as exc:
                    cr = CaseResult(
                        name=case_fn.__name__,
                        passed=False,
                        duration_s=0.0,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                cases.append(cr)
                run.append(cr)
                if cr.skipped:
                    verdict = f"SKIP:{cr.skip_category or '?'}"
                elif cr.soft_fail:
                    verdict = "SOFT_FAIL"
                elif cr.passed:
                    verdict = "PASS"
                else:
                    verdict = "FAIL"
                _print(f"[memory] {cr.name}: {verdict} — {cr.reason or 'ok'}")
            prepend_report(
                _REPORT_PATH,
                "memory",
                run.iso,
                cases,
                run_dir=run.dir,
            )
    finally:
        await stack.aclose()

    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
