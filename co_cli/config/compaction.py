"""Compaction settings sub-model."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    Shape invariant: tail_fraction < compaction_ratio. Post-compact state
    (head ~3% + marker ~3% + tail) must leave headroom before the trigger
    re-fires. Inverting the order causes immediate re-trigger on every pass.
    """

    model_config = ConfigDict(extra="forbid")

    compaction_ratio: float = Field(
        default=0.50,
        description=(
            "Fraction of budget above which the proactive mid-turn trigger fires. "
            "Post-compact state ≈ tail_fraction + head (~3%) + marker (~3%); "
            "headroom per pass ≈ compaction_ratio - post_compact_state. "
            "At 0.50 with a 32k context, trigger fires at ~16k tokens; "
            "tail budget = 20% × 32k ≈ 6.5k tokens; headroom per pass ≈ 24%."
        ),
    )
    tail_fraction: float = Field(
        default=0.20,
        description=(
            "Fraction of budget targeted for the preserved tail in plan_compaction_boundaries. "
            "Must be < compaction_ratio. Cross-compaction memory lives in the iterative "
            "summary marker, so the tail carries only the recent reasoning chain."
        ),
    )
    min_proactive_savings: float = Field(
        default=0.10,
        description="Minimum token savings fraction to count a proactive compaction as effective.",
    )
    proactive_thrash_window: int = Field(
        default=2,
        description="Number of consecutive low-yield proactive compactions before the anti-thrashing gate activates.",
    )

    @model_validator(mode="after")
    def _validate_shape(self) -> "CompactionSettings":
        if not self.tail_fraction > 0:
            raise ValueError(f"tail_fraction must be > 0, got {self.tail_fraction}")
        if not 0 < self.compaction_ratio < 1.0:
            raise ValueError(f"compaction_ratio must be in (0, 1.0), got {self.compaction_ratio}")
        if not self.tail_fraction < self.compaction_ratio:
            raise ValueError(
                f"tail_fraction ({self.tail_fraction}) must be < compaction_ratio "
                f"({self.compaction_ratio}): post-compact state must leave headroom "
                "before the trigger re-fires"
            )
        return self
