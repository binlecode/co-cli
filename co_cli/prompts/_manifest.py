"""Prompt assembly manifest — tracks what was loaded and diagnostics."""

from dataclasses import dataclass, field


@dataclass
class PromptManifest:
    """Audit trail for assembled system prompt.

    Attributes:
        parts_loaded: Names of components loaded (e.g. ["instructions", "identity", ...]).
        total_chars: Character count of the assembled prompt.
        warnings: Any issues encountered during assembly.
    """

    parts_loaded: list[str] = field(default_factory=list)
    total_chars: int = 0
    warnings: list[str] = field(default_factory=list)
