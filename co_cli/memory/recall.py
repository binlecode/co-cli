"""Re-export knowledge-artifact loaders under the ``memory/`` path.

Prompt-assembly and personality injection need ``load_knowledge_artifacts``
but cannot import from ``co_cli.tools`` (cycle). This module re-exports the
canonical loaders from ``co_cli.knowledge._artifact`` to sit outside that cycle.
"""

from co_cli.knowledge._artifact import (
    KnowledgeArtifact,
    load_knowledge_artifacts,
)

__all__ = [
    "KnowledgeArtifact",
    "load_knowledge_artifacts",
]
