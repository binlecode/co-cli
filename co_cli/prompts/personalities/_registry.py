"""Personality preset registry.

Maps preset names to two orthogonal axes:
- character: WHO — identity, philosophy, behavioral patterns, markers
- style: HOW — format, length, structure, emoji policy

Override precedence: style wins on format, character wins on identity.
"""

from typing import TypedDict


class PersonalityPreset(TypedDict):
    """A personality preset: optional character axis + required style axis.

    Three tiers per preset:
    - Seed (always-on): ``seed/{preset_name}.md`` — voice fingerprint
    - Character (on-demand): ``character/{name}.md`` — identity axis
    - Style (on-demand): ``style/{name}.md`` — format axis
    """

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
