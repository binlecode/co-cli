"""User reasoning depth — trait overrides applied at personality compose time.

``reasoning_depth`` is a user-expressed session intent, not a personality property.
It overrides specific trait lookups during behavior file selection so the assembled
``## Soul`` block reflects the user's desired response depth without altering
who co is.

Lives at the prompts layer (not inside ``personalities/``) because it is a prompt
assembly concern — the user's intent modulates the assembled block.
"""

VALID_DEPTHS: list[str] = ["quick", "normal", "deep"]

# Maps user depth intent to trait overrides applied before behavior file loading.
# Each value references an existing behavior file — no new files required.
#
# quick: two overrides
#   thoroughness → minimal   suppress verification, rationale, step-by-step
#   curiosity    → reactive  answer what was asked; stop volunteering follow-ups
#
# normal: no overrides — role defaults apply unchanged
#
# deep: one override
#   thoroughness → comprehensive  verify results, explain reasoning chain, surface edge cases
#
# Traits NOT overridden: communication, relationship, emotional_tone.
# Overriding these would corrupt role identity for no behavioral gain —
# depth modulation should stay within the role, not replace it.
_DEPTH_OVERRIDES: dict[str, dict[str, str]] = {
    "quick": {
        "thoroughness": "minimal",
        "curiosity": "reactive",
    },
    "normal": {},
    "deep": {
        "thoroughness": "comprehensive",
    },
}
