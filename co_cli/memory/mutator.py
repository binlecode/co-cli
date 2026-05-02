"""Knowledge artifact mutation helpers — atomic file write."""

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
    os.replace(tmp.name, path)
