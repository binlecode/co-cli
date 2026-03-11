"""Eval scoring engine: synchronous checks, LLM-as-judge, and JSONL case loading."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai.settings import ModelSettings

from co_cli.deps import CoDeps


# ---------------------------------------------------------------------------
# Eval case schema (shared by runner and trace report)
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    id: str
    personality: str
    turns: list[str]
    checks_per_turn: list[list[dict[str, Any]]]


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            cases.append(EvalCase(
                id=raw["id"],
                personality=raw["personality"],
                turns=raw["turns"],
                checks_per_turn=raw["checks_per_turn"],
            ))
    return cases


# ---------------------------------------------------------------------------
# Synchronous check functions
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r'[.!?]+(?:\s|$)')


def count_sentences(text: str) -> int:
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    if _SENTENCE_END.search(text):
        parts = _SENTENCE_END.split(text)
        return sum(1 for p in parts if p.strip())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return max(len(lines), 1) if lines else 0


def check_max_sentences(text: str, params: dict[str, Any]) -> str | None:
    n = params["n"]
    actual = count_sentences(text)
    if actual <= n:
        return None
    return f"max_sentences: got {actual}, expected <= {n}"


def check_min_sentences(text: str, params: dict[str, Any]) -> str | None:
    n = params["n"]
    actual = count_sentences(text)
    if actual >= n:
        return None
    return f"min_sentences: got {actual}, expected >= {n}"


def check_forbidden(text: str, params: dict[str, Any]) -> str | None:
    # Strip inline markdown emphasis (* and _) so formatted text like
    # "not *always* wrong" doesn't bypass forbidden checks on "always".
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return f"forbidden: found '{phrase}'"
    return None


def check_required_any(text: str, params: dict[str, Any]) -> str | None:
    # Strip inline markdown emphasis (* and _) before matching so
    # "not *always* wrong" matches the phrase "not always wrong".
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return None
    return f"required_any: none of {params['phrases']} found"


def check_no_preamble(text: str, params: dict[str, Any]) -> str | None:
    stripped = text.strip().lower()
    for phrase in params["phrases"]:
        if stripped.startswith(phrase.lower()):
            return f"no_preamble: starts with '{phrase}'"
    return None


def check_has_question(text: str, params: dict[str, Any]) -> str | None:
    if "?" in text:
        return None
    return "has_question: no '?' found"


_CHECK_DISPATCH: dict[str, Any] = {
    "max_sentences": check_max_sentences,
    "min_sentences": check_min_sentences,
    "forbidden": check_forbidden,
    "required_any": check_required_any,
    "no_preamble": check_no_preamble,
    "has_question": check_has_question,
}


def score_response(text: str, checks: list[dict[str, Any]]) -> list[str]:
    """Run synchronous checks. Returns list of failure descriptions.

    Skips ``llm_judge`` and other async-only check types silently.
    Use ``score_turn`` for full evaluation including LLM judge checks.
    """
    failures: list[str] = []
    for check in checks:
        check_type = check["type"]
        fn = _CHECK_DISPATCH.get(check_type)
        if fn is None:
            # Unknown or async-only type (e.g. llm_judge) — skip silently
            continue
        result = fn(text, check)
        if result is not None:
            failures.append(result)
    return failures


# ---------------------------------------------------------------------------
# LLM-as-judge (async)
# ---------------------------------------------------------------------------


class JudgeResult(BaseModel):
    passed: bool
    reasoning: str


_JUDGES_DIR = Path(__file__).parent / "judges"


def _load_character_judge(role: str) -> str:
    """Load character-specific judgment rules from ``evals/judges/{role}.md``.

    Returns empty string if no judge file exists for the role — the judge
    prompt will omit the character rules section gracefully.
    """
    if not role:
        return ""
    path = _JUDGES_DIR / f"{role}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


_JUDGE_PROMPT = (
    "You are evaluating whether this AI response is in character for {personality}.\n\n"
    "CHARACTER JUDGMENT RULES:\n{character_rules}\n\n"
    "SPECIFIC CRITERION FOR THIS CHECK:\n{criteria}\n\n"
    "RESPONSE TO EVALUATE:\n{response}\n\n"
    "Return JSON with exactly two fields:\n"
    '- "passed": true only if the response clearly satisfies the criterion with confidence\n'
    '- "reasoning": one sentence explaining your judgment\n\n'
    "When in doubt, fail. High bar — only pass when the criterion is clearly and unambiguously met."
)


def _make_judge_settings(base: ModelSettings | None) -> ModelSettings:
    """Build model settings for the LLM judge from base eval settings.

    Reduces temperature to 70% of the base value for more stable binary
    judgments while keeping it above 0.3 (thinking models loop at very low
    temperatures). Preserves ``extra_body`` (e.g. ``enable_thinking``) so
    the thinking budget is still available for reasoning through criteria.
    Max tokens is intentionally not capped — thinking models consume output
    tokens for chain-of-thought before emitting the JSON object.
    """
    if base is None:
        return ModelSettings(temperature=0.7)
    base_temp = base.get("temperature") or 1.0
    # Floor at 0.3 — thinking models produce degenerate loops at temperature=0
    judge_temp = max(0.3, base_temp * 0.7)
    kwargs: dict[str, Any] = {"temperature": judge_temp}
    extra_body = base.get("extra_body")
    if extra_body:
        kwargs["extra_body"] = extra_body
    return ModelSettings(**kwargs)


async def _llm_judge(
    text: str,
    criteria: str,
    agent: Any,
    deps: CoDeps,
    model_settings: ModelSettings | None,
) -> JudgeResult:
    """Run one LLM judge check. Returns the full JudgeResult (passed + reasoning).

    Loads the character-specific judgment rules from ``evals/judges/{role}.md``
    so the judge applies consistent behavioral standards across all cases for
    that personality. The JSONL criterion is the per-check assertion; the judge
    file is the shared character evaluation rubric.
    """
    from dataclasses import replace as dataclass_replace
    role = deps.config.personality or ""
    character_rules = _load_character_judge(role)
    personality_label = role.capitalize() if role else "this character"
    judge_deps = dataclass_replace(deps)
    judge_ms = _make_judge_settings(model_settings)
    prompt = _JUDGE_PROMPT.format(
        personality=personality_label,
        character_rules=character_rules or "(no character rules file found)",
        criteria=criteria,
        response=text,
    )
    result = await agent.run(
        prompt,
        output_type=JudgeResult,
        message_history=[],
        deps=judge_deps,
        model_settings=judge_ms,
    )
    return result.output


async def score_turn(
    text: str,
    checks: list[dict[str, Any]],
    agent: Any,
    deps: CoDeps,
    model_settings: ModelSettings | None,
) -> tuple[list[str], dict[int, str]]:
    """Run all checks for one turn.

    Returns ``(failures, judge_details)`` where:
    - ``failures``: list of failure description strings (empty = all pass)
    - ``judge_details``: dict of check_index → "PASS: reasoning" or "FAIL: reasoning"
      for every ``llm_judge`` check, so reasoning is visible for both outcomes

    Handles async ``llm_judge`` checks via LLM call and falls back to
    synchronous dispatch for all other check types (``forbidden``, etc.).
    """
    failures: list[str] = []
    judge_details: dict[int, str] = {}
    for i, check in enumerate(checks):
        check_type = check["type"]
        if check_type == "llm_judge":
            jr = await _llm_judge(
                text, check["criteria"], agent, deps, model_settings
            )
            prefix = "PASS" if jr.passed else "FAIL"
            judge_details[i] = f"{prefix}: {jr.reasoning}"
            if not jr.passed:
                failures.append(f"llm_judge: {jr.reasoning}")
        else:
            fn = _CHECK_DISPATCH.get(check_type)
            if fn is None:
                continue
            result = fn(text, check)
            if result is not None:
                failures.append(result)
    return failures, judge_details
