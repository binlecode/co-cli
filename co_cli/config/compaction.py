"""Compaction settings sub-model."""

from pydantic import BaseModel, ConfigDict, Field

COMPACTION_ENV_MAP: dict[str, str] = {
    "compaction_ratio": "CO_COMPACTION_RATIO",
    "tail_fraction": "CO_COMPACTION_TAIL_FRACTION",
    "min_proactive_savings": "CO_COMPACTION_MIN_PROACTIVE_SAVINGS",
    "proactive_thrash_window": "CO_COMPACTION_PROACTIVE_THRASH_WINDOW",
}


class CompactionSettings(BaseModel):
    """Tuning knobs for the context compaction system.

    All ratios apply to the raw context_window budget returned by resolve_compaction_budget().
    Threshold knobs are integers (token counts). All fields are overridable via settings.json.
    """

    model_config = ConfigDict(extra="forbid")

    compaction_ratio: float = Field(
        default=0.80,
        description="Fraction of budget above which compaction fires (M0 pre-turn and M3 proactive).",
    )
    tail_fraction: float = Field(
        default=0.40,
        description="Fraction of budget targeted for the preserved tail in plan_compaction_boundaries.",
    )
    min_proactive_savings: float = Field(
        default=0.10,
        description="Minimum token savings fraction to count a proactive compaction as effective.",
    )
    proactive_thrash_window: int = Field(
        default=2,
        description="Number of consecutive low-yield proactive compactions before the anti-thrashing gate activates.",
    )
