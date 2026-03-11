"""Eval markdown report formatting helpers."""

import re
from typing import Any

from evals._checks import count_sentences


def md_cell(text: str) -> str:
    """Escape markdown table cell metacharacters."""
    return text.replace("|", r"\|").replace("\n", "<br>")


def check_display(check: dict[str, Any]) -> str:
    t = check.get("type", "")
    if t == "max_sentences":
        return f"max_sentences: ≤ {check.get('n')}"
    if t == "min_sentences":
        return f"min_sentences: ≥ {check.get('n')}"
    if t == "forbidden":
        phrases = check.get("phrases", [])
        return f"forbidden: {phrases}"
    if t == "required_any":
        phrases = check.get("phrases", [])
        return f"required_any: {phrases}"
    if t == "no_preamble":
        phrases = check.get("phrases", [])
        return f"no_preamble: {phrases}"
    if t == "has_question":
        return "has_question"
    if t == "llm_judge":
        criteria = check.get("criteria", "")
        return f"llm_judge: {criteria[:80]}{'...' if len(criteria) > 80 else ''}"
    return t


def check_result(check: dict[str, Any], failures: list[str]) -> str:
    check_type = check.get("type", "")
    for f in failures:
        if f.startswith(check_type):
            return f"FAIL — {f}"
    return "PASS"


def check_match_detail(check: dict[str, Any], text: str) -> str:
    """Return what was matched (or not) for a check — used in the Matched column."""
    t = check.get("type", "")
    clean = re.sub(r'[*_]', '', text).lower()
    if t == "required_any":
        for phrase in check.get("phrases", []):
            if phrase.lower() in clean:
                return f'"{phrase}"'
        return "none found"
    if t == "forbidden":
        for phrase in check.get("phrases", []):
            if phrase.lower() in clean:
                return f'"{phrase}"'
        return "—"
    if t in ("max_sentences", "min_sentences"):
        return f"actual={count_sentences(text)}"
    if t == "no_preamble":
        stripped = text.strip().lower()
        for phrase in check.get("phrases", []):
            if stripped.startswith(phrase.lower()):
                return f'"{phrase}"'
        return "—"
    if t == "has_question":
        return '"?" found' if "?" in text else "no ? found"
    if t == "llm_judge":
        return "(LLM evaluated)"
    return "—"
