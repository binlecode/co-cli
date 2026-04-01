"""Eval scoring engine: synchronous checks and JSONL case loading."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
            cases.append(
                EvalCase(
                    id=raw["id"],
                    personality=raw["personality"],
                    turns=raw["turns"],
                    checks_per_turn=raw["checks_per_turn"],
                )
            )
    return cases


# ---------------------------------------------------------------------------
# Synchronous check functions
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")


def count_sentences(text: str) -> int:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
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
    clean = re.sub(r"[*_]", "", text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return f"forbidden: found '{phrase}'"
    return None


def check_required_any(text: str, params: dict[str, Any]) -> str | None:
    # Strip inline markdown emphasis (* and _) before matching so
    # "not *always* wrong" matches the phrase "not always wrong".
    clean = re.sub(r"[*_]", "", text).lower()
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

    Skips unknown check types silently.
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
