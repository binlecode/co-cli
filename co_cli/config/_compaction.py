"""Compaction settings sub-model."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompactionSettings(BaseModel):
    """Tuning knobs for the context compaction system.

    All ratios apply to the raw context_window budget returned by resolve_compaction_budget().
    Threshold knobs are integers (token counts). All fields are overridable via settings.json.
    """

    model_config = ConfigDict(extra="forbid")

    proactive_ratio: float = Field(
        default=0.75,
        description="Fraction of budget above which proactive compaction fires.",
    )
    hygiene_ratio: float = Field(
        default=0.88,
        description="Fraction of budget above which pre-turn hygiene compaction fires.",
    )
    tail_fraction: float = Field(
        default=0.40,
        description="Fraction of budget targeted for the preserved tail in plan_compaction_boundaries.",
    )
    min_context_length_tokens: int = Field(
        default=64_000,
        description="Absolute floor on the proactive trigger threshold. Compaction never fires until token_count exceeds this value, regardless of the budget-ratio result.",
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
    def _check_ratio_ordering(self) -> "CompactionSettings":
        if self.proactive_ratio >= self.hygiene_ratio:
            raise ValueError(
                "compaction.proactive_ratio must be strictly less than compaction.hygiene_ratio "
                f"(got proactive_ratio={self.proactive_ratio}, hygiene_ratio={self.hygiene_ratio}). "
                "The hygiene ratio is the safety net at run_turn entry; placing it at or below "
                "the proactive ratio inverts the trigger order and breaks the safety-net semantics."
            )
        return self
