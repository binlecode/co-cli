"""LLM rubric judge for eval cases that can't be checked structurally.

Used by W1.A (response on-topic + voice) and W4.A (skill body adherence).
All other eval cases assert on observable outcomes (file presence, FTS hits,
session state mutations) — the judge is reserved for prose-quality
assertions where deterministic checking would be brittle.

Returns :class:`JudgeVerdict` with ``passed: bool`` (not ``pass`` — Python
keyword), ``score: int`` (0-10), and a one-line ``rationale``.

Judge model isolation: pass ``model=deps.judge_model`` to pin a distinct model
than the agent under test (recommended for phase-2 behavioral evals — see
``settings.llm.judge_model``). When ``model`` is None (no pinned judge
configured), the call falls back to ``deps.model`` and callers should emit
``[judge_model_same_as_agent]`` in ``CaseResult.reason``.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from evals._timeouts import LLM_REASONING_TIMEOUT_SECS

from co_cli.deps import CoDeps
from co_cli.llm.call import llm_call
from co_cli.llm.factory import LlmModel


def judge_model_annotation(deps: CoDeps) -> str:
    """Return the judge-model annotation chip for ``CaseResult.reason``.

    Returns ``[judge_model=<name>]`` when ``deps.judge_model`` is pinned and
    ``[judge_model_same_as_agent]`` when the judge falls back to the agent
    model — a single-model regression risk the reviewer should be aware of.
    """
    if deps.judge_model is None:
        return "[judge_model_same_as_agent]"
    name = deps.config.llm.judge_model or "?"
    return f"[judge_model={name}]"


_JUDGE_SYSTEM_PROMPT = """You are a rubric-based judge for an AI agent eval.
Read the rubric, read the transcript, and return ONE compact JSON object on a
single line. Schema:

    {"passed": true|false, "score": <0-10 integer>, "rationale": "<one sentence>"}

Rules:
- Be strict. "passed" is true ONLY if the rubric's PASS criteria are met.
- The rationale must be a single sentence under 200 chars.
- Output nothing else — no preamble, no markdown, no commentary.
"""

_JSON_OBJ_RE = re.compile(r"\{[^{}]*\"passed\"[^{}]*\}", re.DOTALL)

_PAIRWISE_SYSTEM_PROMPT = """You are a pairwise judge for an AI agent eval.
You are given a TARGET STANCE describing the behavioral stance a good response
should exhibit, and two candidate responses, A and B. Decide which response
better exhibits the target stance. Return ONE compact JSON object on a single
line. Schema:

    {"winner": "A"|"B"|"tie", "rationale": "<one sentence>"}

Rules:
- Judge only against the target stance — not length, politeness, or formatting.
- Choose "tie" only when neither response is clearly better on the stance.
- The rationale must be a single sentence under 200 chars.
- Output nothing else — no preamble, no markdown, no commentary.
"""

_PAIRWISE_OBJ_RE = re.compile(r"\{[^{}]*\"winner\"[^{}]*\}", re.DOTALL)


@dataclass(frozen=True)
class JudgeVerdict:
    """Single judge verdict — field is ``passed`` (not ``pass`` — keyword)."""

    passed: bool
    score: int
    rationale: str


@dataclass(frozen=True)
class PairwiseVerdict:
    """Single pairwise comparison — ``winner`` ∈ {"A", "B", "tie"}."""

    winner: str
    rationale: str


def _stringify_transcript(transcript: list[Any]) -> str:
    """Render a heterogeneous transcript list into a compact judge-readable string.

    Accepts pydantic-ai ``ModelMessage`` objects (walks .parts) and plain
    ``{"role": "...", "content": "..."}`` dicts interchangeably.
    """
    lines: list[str] = []
    for item in transcript:
        if isinstance(item, dict):
            role = item.get("role", "user")
            content = item.get("content", "")
            lines.append(f"[{role}] {content}")
            continue
        parts = getattr(item, "parts", None)
        if not parts:
            lines.append(f"[{type(item).__name__}] {item!r}")
            continue
        for part in parts:
            cls_name = type(part).__name__
            if cls_name in {"UserPromptPart", "SystemPromptPart", "TextPart"}:
                content = getattr(part, "content", "")
                role = (
                    "user"
                    if cls_name == "UserPromptPart"
                    else ("system" if cls_name == "SystemPromptPart" else "assistant")
                )
                lines.append(f"[{role}] {content}")
            elif cls_name == "ToolCallPart":
                name = getattr(part, "tool_name", "")
                args = getattr(part, "args", "")
                lines.append(f"[tool_call] {name}({args})")
            elif cls_name == "ToolReturnPart":
                name = getattr(part, "tool_name", "")
                content = getattr(part, "content", "")
                content_str = (
                    content if isinstance(content, str) else json.dumps(content, default=str)
                )
                lines.append(f"[tool_return] {name} -> {content_str[:500]}")
    return "\n".join(lines)


def _parse_verdict(raw: str) -> JudgeVerdict:
    """Pull a single ``{passed, score, rationale}`` JSON object from raw text."""
    raw = raw.strip()
    match = _JSON_OBJ_RE.search(raw)
    if match:
        try:
            data = json.loads(match.group(0))
            return JudgeVerdict(
                passed=bool(data.get("passed", False)),
                score=int(data.get("score", 0)),
                rationale=str(data.get("rationale", ""))[:300],
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return JudgeVerdict(
        passed=False,
        score=0,
        rationale=f"Judge returned unparseable output: {raw[:200]}",
    )


async def judge_with_llm(
    rubric_md: str,
    transcript: list[Any],
    *,
    deps: CoDeps,
    model: LlmModel | None = None,
) -> JudgeVerdict:
    """Score a transcript against a rubric using the configured judge model.

    When ``model`` is passed (typically ``deps.judge_model``), the judge runs
    on that pinned handle instead of ``deps.model``. Falls back to ``deps.model``
    when ``model`` is None — the caller is responsible for flagging
    ``[judge_model_same_as_agent]`` in the resulting ``CaseResult.reason``.

    Wrapped in ``asyncio.timeout(LLM_REASONING_TIMEOUT_SECS)`` to bound a
    stalled judge call without hiding a regression. Returns a ``JudgeVerdict``
    even on parse failure (with ``passed=False`` and the raw text as
    rationale) so a malformed judge response doesn't crash the eval.
    """
    transcript_text = _stringify_transcript(transcript)
    prompt = f"RUBRIC:\n{rubric_md.strip()}\n\nTRANSCRIPT:\n{transcript_text}\n\nReturn JSON now."
    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        raw = await llm_call(deps, prompt, instructions=_JUDGE_SYSTEM_PROMPT, model=model)
    return _parse_verdict(raw)


def _parse_pairwise(raw: str) -> PairwiseVerdict:
    """Pull a single ``{winner, rationale}`` object; coerce winner to A/B/tie."""
    raw = raw.strip()
    match = _PAIRWISE_OBJ_RE.search(raw)
    if match:
        try:
            data = json.loads(match.group(0))
            winner = str(data.get("winner", "tie")).strip().upper()
            if winner not in {"A", "B"}:
                winner = "tie"
            return PairwiseVerdict(
                winner=winner,
                rationale=str(data.get("rationale", ""))[:300],
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return PairwiseVerdict(
        winner="tie", rationale=f"Judge returned unparseable output: {raw[:200]}"
    )


async def judge_pairwise(
    target_stance: str,
    response_a: str,
    response_b: str,
    *,
    deps: CoDeps,
    model: LlmModel | None = None,
) -> PairwiseVerdict:
    """One oriented pairwise comparison: which of A/B better exhibits ``target_stance``.

    This is the single-comparison primitive. To cancel position bias, callers
    must run each comparison in **both orders** ((A,B) and (B,A)) and treat a
    disagreement between the two runs as a tie — see the ablation eval's
    per-case reconciliation. The absolute :func:`judge_with_llm` is untouched.

    Pass ``model=deps.judge_model`` to pin a judge distinct from the agent under
    test (recommended). Wrapped in ``asyncio.timeout`` like the absolute judge;
    returns ``winner="tie"`` on parse failure so a malformed response never
    crashes the eval.
    """
    prompt = (
        f"TARGET STANCE:\n{target_stance.strip()}\n\n"
        f"RESPONSE A:\n{response_a.strip()}\n\n"
        f"RESPONSE B:\n{response_b.strip()}\n\n"
        "Return JSON now."
    )
    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        raw = await llm_call(deps, prompt, instructions=_PAIRWISE_SYSTEM_PROMPT, model=model)
    return _parse_pairwise(raw)
