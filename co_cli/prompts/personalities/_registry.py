"""Personality preset registry.

Maps preset names to composable aspect combinations (character + style).
"""

from typing import TypedDict


class PersonalityPreset(TypedDict):
    """A personality preset: optional character aspect + required style aspect."""

    character: str | None
    style: str


PRESETS: dict[str, PersonalityPreset] = {
    "finch":       {"character": "finch", "style": "balanced"},
    "jeff":        {"character": "jeff",  "style": "warm"},
    "friendly":    {"character": None,    "style": "warm"},
    "terse":       {"character": None,    "style": "terse"},
    "inquisitive": {"character": None,    "style": "educational"},
}

VALID_PERSONALITIES: list[str] = list(PRESETS.keys())
