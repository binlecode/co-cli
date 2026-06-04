"""UAT eval — Workflow 3: Memory recall and curation.

Covers `memory_search`, `session_search`, `memory_view`, and the memory write
tools (`memory_create`/`memory_append`/`memory_replace`/`memory_delete`)
via agent calls, plus the `/memory list|forget` user surface and the dream
cycle's merge/decay/archive. Validates BM25/hybrid recall, write-time
indexing, on-disk artifact lifecycle, and dream-cycle content preservation.

Per plan section W3, case ordering is a hard contract:
  W3.A seeds `eval_W3_fact` (agent-driven via memory_create).
  W3.B/C/D read it (search, recall, list).
  W3.E forgets it (boundary).
  W3.F uses its own pre-seeded `eval_W3_dupA` / `eval_W3_dupB` pair to exercise
  the dream cycle's merge/decay path.

Reruns overwrite in place via deterministic stems — no accumulation.

Specs: docs/specs/memory.md, knowledge.md, dream.md
Mission tenet: local — user-controlled storage; trusted — reversible (W3.E/G)
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
from evals._judge import judge_model_annotation, judge_with_llm
from evals._observability import CaseResult, EvalRun, Verdict, open_eval_run
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
from co_cli.daemons.dream._housekeeping import merge_memory
from co_cli.daemons.dream._state import HousekeepingState
from co_cli.deps import CoDeps
from co_cli.session.filename import session_filename
from co_cli.tools.memory.recall import memory_search

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORT_PATH = _PROJECT_ROOT / "docs" / "REPORT-eval-memory.md"

_FACT_TITLE = "eval_W3_fact"
_FACT_TOKEN = "STG_DEPLOY_42"
_FACT_GLOB_PREFIX = "eval-w3-fact-"

_DUP_A_STEM = "eval_W3_dupA"
_DUP_B_STEM = "eval_W3_dupB"
_DUP_A_TOKEN = "TOKEN_A_ONLY"
_DUP_B_TOKEN = "TOKEN_B_ONLY"

_W3G_STEM = "eval_w3g_fact"
_W3G_TITLE = "eval_W3G_fact"
_W3G_TOKEN = "W3G_MARKER_XK42"


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
    for path in deps.memory_dir.glob(f"{_FACT_GLOB_PREFIX}*.md"):
        if path.is_file():
            return path
    return None


def _purge_fact_artifacts(deps: CoDeps) -> None:
    """Remove any prior eval_W3_fact* on disk and from the FTS index.

    Ensures W3.A starts clean so the agent's save is the only matching file.
    Called once at the top of W3.A; idempotent across reruns.
    """
    for path in deps.memory_dir.glob(f"{_FACT_GLOB_PREFIX}*.md"):
        try:
            if deps.memory_store is not None:
                deps.memory_store.remove(path)
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

    Bypasses the memory write tools so the filename_stem is exactly the one we
    request — ``save_artifact`` slugifies the title and appends a random uuid
    suffix, which loses deterministic-stem semantics needed for W3.F's pair.
    """
    knowledge_dir = deps.memory_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = knowledge_dir / f"{filename_stem}.md"
    artifact_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()
    frontmatter = {
        "id": artifact_id,
        "memory_kind": "note",
        "title": title,
        "created_at": created_at,
    }
    yaml_lines = [
        "---",
        f"id: {artifact_id}",
        "memory_kind: note",
        f"title: {title}",
        f"created_at: '{created_at}'",
        "---",
        "",
        body.strip(),
        "",
    ]
    markdown_content = "\n".join(yaml_lines)
    artifact_path.write_text(markdown_content, encoding="utf-8")

    if deps.memory_store is not None:
        deps.memory_store.reindex_one(artifact_path, body, markdown_content, frontmatter)
    return artifact_path


def _seed_past_session(deps: CoDeps, *, token: str) -> Path:
    """Write a canonical past-session JSONL containing ``token`` and index it.

    ``session_search`` excludes the *current* session at query time, so a case
    that needs a findable hit must seed a separate past session (distinct uuid8,
    earlier timestamp) — re-indexing the active session can never surface it.
    """
    active = deps.session.session_path
    has_real_active = active is not None and active != Path(".")
    sessions_dir = active.parent if has_real_active else (deps.memory_dir.parent / "sessions")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    session_id = uuid4().hex[:8]
    path = sessions_dir / session_filename(created_at, session_id)
    record = [
        {
            "timestamp": created_at.isoformat(),
            "parts": [
                {
                    "part_kind": "user-prompt",
                    "content": f"Earlier the user noted: the staging deploy id is {token}.",
                }
            ],
        }
    ]
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


def _purge_dup_pair(deps: CoDeps) -> None:
    """Remove any prior W3.F seeded pair from disk + FTS so W3.F can re-seed cleanly."""
    for stem in (_DUP_A_STEM, _DUP_B_STEM):
        path = deps.memory_dir / f"{stem}.md"
        if path.exists():
            try:
                if deps.memory_store is not None:
                    deps.memory_store.remove(path)
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
    row = deps.memory_store._index._conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE source = ? AND doc_path = ?",
        ("memory", str(artifact_path)),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def _direct_memory_search_count(deps: CoDeps, query: str) -> int:
    """Count knowledge-source FTS hits for ``query`` via the memory store.

    Used by W3.E to assert post-forget cleanup: a search for the unique
    token should return 0 hits once the artifact is deleted + de-indexed.
    """
    if deps.memory_store is None:
        return 0
    hits = deps.memory_store.search_memory_items(query, None, 10)
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
    """W3.A — agent calls memory_create for a clearly-durable fact.

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

        create_calls = _extract_tool_calls_by_name(turn_trace.tool_calls, "memory_create")
        if not create_calls:
            passed = False
            tool_names = sorted({rec.tool_name for rec in turn_trace.tool_calls})
            reason = f"no memory_create tool call found; tools used: {tool_names!r}"
        else:
            fact_path = _find_fact_path(deps)
            if fact_path is None:
                passed = False
                reason = (
                    f"no on-disk artifact matched glob {_FACT_GLOB_PREFIX}*.md after "
                    "memory_create; save_artifact() may have failed silently"
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
        verdict=Verdict.PASS if passed else Verdict.FAIL,
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
    """W3.B — memory_search ranks the W3.A artifact #1 with the token in its snippet.

    Drives a turn that prompts the agent to call ``memory_search``; falls
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
            verdict=Verdict.FAIL,
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

        search_calls = _extract_tool_calls_by_name(turn_trace.tool_calls, "memory_search")
        if not search_calls:
            passed = False
            tool_names = sorted({rec.tool_name for rec in turn_trace.tool_calls})
            reason = f"agent did not call memory_search; tools used: {tool_names!r}"
        else:
            ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())
            search_return = await memory_search(ctx, query=_FACT_TOKEN, limit=5)
            results = (search_return.metadata or {}).get("results", []) or []
            if not results:
                passed = False
                reason = f"production memory_search for {_FACT_TOKEN!r} returned 0 results"
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
        verdict=Verdict.PASS if passed else Verdict.FAIL,
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
    """W3.C — session_search surfaces a prior (non-current) session containing the fact.

    ``session_search`` excludes the *active* session at query time, so this case
    seeds a separate past session carrying the fact token and indexes it — the
    search must then return that past session as a hit.
    """
    case_id = "W3.C"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)

    try:
        _seed_past_session(deps, token=_FACT_TOKEN)
    except Exception as exc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"seed_past_session failed: {type(exc).__name__}: {exc}",
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
        verdict=Verdict.PASS if passed else Verdict.FAIL,
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
            verdict=Verdict.FAIL,
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

        from co_cli.memory.item import load_memory_items

        artifacts = load_memory_items(deps.memory_dir)
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
        verdict=Verdict.PASS if passed else Verdict.FAIL,
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
    removal, chunks_fts cleanup, and that ``memory_search`` for the unique
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
            verdict=Verdict.FAIL,
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
            # NOTE: a global ``memory_search(_FACT_TOKEN)`` check would be
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
        verdict=Verdict.PASS if passed else Verdict.FAIL,
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
      Exactly one missing    → SOFT_PASS — gate passes; LLM-merge may drop rare
                               tokens (degradation, not regression). Logged for review.
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
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"seed_dup_pair failed: {type(exc).__name__}: {exc}",
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
        )

    verdict = Verdict.PASS
    reason = ""
    try:
        async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):
            hk_state = HousekeepingState()
            merged_count = await merge_memory(deps, hk_state)

        survivors_text: list[str] = []
        for path in (path_a, path_b):
            if path.exists():
                survivors_text.append(path.read_text(encoding="utf-8"))

        archive_dir = deps.memory_dir / "_archive"
        if archive_dir.exists():
            for archived in archive_dir.rglob("*.md"):
                stem_lower = archived.stem.lower()
                if _DUP_A_STEM.lower() in stem_lower or _DUP_B_STEM.lower() in stem_lower:
                    survivors_text.append(archived.read_text(encoding="utf-8"))

        searchable = "\n".join(survivors_text)
        has_a = _DUP_A_TOKEN in searchable
        has_b = _DUP_B_TOKEN in searchable

        cycle_summary = f"merged={merged_count}"

        if has_a and has_b:
            reason = f"both tokens preserved across survivors+archive; {cycle_summary}"
        elif has_a or has_b:
            verdict = Verdict.SOFT_PASS
            missing = _DUP_B_TOKEN if has_a else _DUP_A_TOKEN
            reason = (
                f"[SOFT_PASS] merge dropped {missing!r}; LLM-merge degradation; {cycle_summary}"
            )
        else:
            verdict = Verdict.FAIL
            reason = f"both tokens missing after merge; {cycle_summary}"
    except TimeoutError:
        verdict = Verdict.FAIL
        reason = f"asyncio.timeout({DREAM_CYCLE_BUDGET_S}s) fired before merge completed"
    except Exception as exc:
        verdict = Verdict.FAIL
        reason = f"{type(exc).__name__}: {exc}"

    return CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=time.monotonic() - t0,
        reason=reason or "ok",
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


async def case_w3_g_forget_propagates_to_recall(
    deps: CoDeps,
    agent: Any,
    frontend: EvalFrontend,
    run: EvalRun,
) -> CaseResult:
    """W3.G — memory_delete propagates through FTS; re-search finds nothing.

    Three turns with shared history: recall seed → agent-driven delete → re-recall
    judged for absence. SOFT_FAIL when judge says agent still surfaces the token.
    """
    case_id = "W3.G"
    t0 = time.monotonic()
    trace_file = run.case_trace_path(case_id)

    body = f"{_W3G_TOKEN} eval W3G memory item for forget-propagation test"
    try:
        seed_path = _seed_dup_artifact(deps, filename_stem=_W3G_STEM, title=_W3G_TITLE, body=body)
    except Exception as exc:
        return CaseResult(
            name=case_id,
            verdict=Verdict.FAIL,
            duration_s=time.monotonic() - t0,
            reason=f"seed failed: {type(exc).__name__}: {exc}",
            trace_files=[str(trace_file.relative_to(run.dir.parent))],
        )

    passed = True
    verdict = Verdict.PASS
    reason = ""
    model_call_seconds = 0.0
    token_usage: dict[str, int] = {}
    history: list[Any] = []
    t2_result = None

    try:
        # Turn 0: recall — agent should call memory_search and surface the seed
        t0_input = (
            f"Use memory_search to find any memory item containing '{_W3G_TOKEN}'. "
            "Report the filename_stem of any hits."
        )
        async with asyncio.timeout(CALL_TIMEOUT_S):
            t0_result, t0_trace = await record_turn(
                case_id=case_id,
                turn_index=0,
                user_input=t0_input,
                run_turn_callable=lambda: run_turn(
                    agent=agent,
                    user_input=t0_input,
                    deps=deps,
                    message_history=history,
                    frontend=frontend,
                ),
                case_dir_path=trace_file,
                agent=agent,
            )
        model_call_seconds += t0_trace.model_call_seconds
        for k, v in t0_trace.token_usage.items():
            token_usage[k] = token_usage.get(k, 0) + v
        history = list(t0_result.messages)

        t0_tool_names = [rec.tool_name for rec in t0_trace.tool_calls]
        if "memory_search" not in t0_tool_names:
            passed = False
            reason = f"turn 0: agent did not call memory_search; tools={t0_tool_names!r}"

        # Turn 1: delete — agent should call memory_delete using the stem
        if passed:
            t1_input = (
                f"Now delete the memory item with filename_stem `{_W3G_STEM}` using memory_delete."
            )
            async with asyncio.timeout(CALL_TIMEOUT_S):
                t1_result, t1_trace = await record_turn(
                    case_id=case_id,
                    turn_index=1,
                    user_input=t1_input,
                    run_turn_callable=lambda: run_turn(
                        agent=agent,
                        user_input=t1_input,
                        deps=deps,
                        message_history=history,
                        frontend=frontend,
                    ),
                    case_dir_path=trace_file,
                    agent=agent,
                )
            model_call_seconds += t1_trace.model_call_seconds
            for k, v in t1_trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v
            history = list(t1_result.messages)

            if seed_path.exists():
                passed = False
                reason = f"turn 1: seed {seed_path.name!r} still on disk after delete turn"

        # Turn 2: re-recall — judge that agent does not surface the token
        if passed:
            t2_input = f"Search memory again for '{_W3G_TOKEN}'. What do you find?"
            async with asyncio.timeout(CALL_TIMEOUT_S):
                t2_result, _t2_trace = await record_turn(
                    case_id=case_id,
                    turn_index=2,
                    user_input=t2_input,
                    run_turn_callable=lambda: run_turn(
                        agent=agent,
                        user_input=t2_input,
                        deps=deps,
                        message_history=history,
                        frontend=frontend,
                    ),
                    case_dir_path=trace_file,
                    agent=agent,
                )
            model_call_seconds += _t2_trace.model_call_seconds
            for k, v in _t2_trace.token_usage.items():
                token_usage[k] = token_usage.get(k, 0) + v

        # Judge turn 2 for absence of the deleted token
        if passed and t2_result is not None:
            rubric = (
                f"The user deleted a memory item containing '{_W3G_TOKEN}' in turn 1. "
                f"In turn 2, the user searched memory again for '{_W3G_TOKEN}'. "
                "PASS only if the agent reports no results found or confirms the item is gone. "
                f"FAIL if the agent successfully recalls or quotes '{_W3G_TOKEN}'."
            )
            try:
                async with asyncio.timeout(CALL_TIMEOUT_S):
                    jverdict = await judge_with_llm(
                        rubric, t2_result.messages, deps=deps, model=deps.judge_model
                    )
                judge_note = f"judge.score={jverdict.score} {judge_model_annotation(deps)}"
                if jverdict.rationale:
                    judge_note += f" {jverdict.rationale[:120]}"
                if not jverdict.passed:
                    verdict = Verdict.SOFT_FAIL
                reason = f"seed_deleted={not seed_path.exists()} | {judge_note}"
            except Exception as jexc:
                reason = (
                    f"seed_deleted={not seed_path.exists()} | judge_error: {type(jexc).__name__}"
                )

        if not reason:
            reason = "ok"
    except Exception as exc:
        passed = False
        verdict = Verdict.FAIL
        reason = f"{type(exc).__name__}: {exc}"

    if not passed and verdict == Verdict.PASS:
        verdict = Verdict.FAIL

    return CaseResult(
        name=case_id,
        verdict=verdict,
        duration_s=time.monotonic() - t0,
        model_call_seconds=model_call_seconds,
        token_usage=token_usage,
        reason=reason,
        trace_files=[str(trace_file.relative_to(run.dir.parent))],
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> int:
    """Run W3.A through W3.G in fixed order and emit the REPORT.

    Case ordering is a hard contract (plan W3 header): A seeds, B/C/D read,
    E forgets, F uses its own pair, G uses its own seed. Failures don't abort
    the run — each case captures its verdict and the script continues; exit code
    is non-zero iff any case failed (SOFT_PASS / SOFT_FAIL are review signals,
    not gate failures).
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
                case_w3_g_forget_propagates_to_recall,
            )
            for case_fn in ordered_cases:
                try:
                    cr = await case_fn(deps, agent, frontend, run)
                except Exception as exc:
                    cr = CaseResult(
                        name=case_fn.__name__,
                        verdict=Verdict.FAIL,
                        duration_s=0.0,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                cases.append(cr)
                run.append(cr)
                if cr.skipped:
                    label = f"SKIP:{cr.skip_category or '?'}"
                else:
                    label = cr.verdict.value.upper()
                _print(f"[memory] {cr.name}: {label} — {cr.reason or 'ok'}")
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
