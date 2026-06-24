"""Shared image-intake primitives: byte-read core + lone-path detector.

Two callers share this module so the accepted-suffix set, size cap, and byte-read
never drift:
  - ``image_view`` (view.py) — the agent-initiated tool. It enforces the read boundary
    itself, then hands an already-resolved path to ``read_image``.
  - the REPL turn preprocessor (main.py) — detects a user-dragged lone image path with
    ``detect_lone_image_path`` (user-gesture read allowance: no boundary, see the
    user-image-intake plan) and reads it with the same ``read_image`` core.

``read_image`` takes an ALREADY-RESOLVED path and does exists/dir/MIME/size/read_bytes
only — boundary resolution stays caller-side so the agent path keeps its full
``enforce_read_boundary`` check while the user-gesture path can opt out.
"""

import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.messages import BinaryContent

# Cap at ~20 MB — a bounded, deliberate read; an oversize image is rejected with a clear
# error rather than downscaled (no image-processing dependency on this path).
_MAX_IMAGE_BYTES = 20 * 1024 * 1024

# Accepted image media types, keyed by file suffix. PDFs are out of scope — they route
# to the pdf skill (text) or the scanned-PDF tier-2 path (which renders pages to
# PNGs and feeds them back through image_view).
_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass(frozen=True)
class ImageRejection:
    """A non-fatal reason an image could not be attached (caller surfaces ``message``)."""

    message: str


def read_image(resolved: Path) -> BinaryContent | ImageRejection:
    """Read an already-resolved image path into BinaryContent, or reject it.

    Does exists/dir/MIME/size/read_bytes only. The caller is responsible for any read
    boundary (image_view enforces it; the user-gesture preprocessor deliberately does not).
    """
    if not resolved.exists():
        return ImageRejection(f"Image not found: {resolved}")
    if resolved.is_dir():
        return ImageRejection(f"Path is a directory: {resolved}")

    media_type = _MEDIA_TYPES.get(resolved.suffix.lower())
    if media_type is None:
        accepted = ", ".join(sorted(_MEDIA_TYPES))
        return ImageRejection(
            f"Unsupported image type: {resolved.suffix or '(no suffix)'}. "
            f"image_view accepts {accepted}. For a PDF, use the pdf skill."
        )

    size = resolved.stat().st_size
    if size > _MAX_IMAGE_BYTES:
        return ImageRejection(
            f"Image too large ({size / (1024 * 1024):.1f} MB; cap is "
            f"{_MAX_IMAGE_BYTES // (1024 * 1024)} MB). Resize it and retry."
        )

    return BinaryContent(data=resolved.read_bytes(), media_type=media_type)


def detect_lone_image_path(text: str, workspace_dir: Path) -> Path | None:
    """Return a resolved Path iff the ENTIRE submitted text is one existing image path.

    The lone-path trigger: the whole input (after trimming, stripping a single pair of
    surrounding quotes, stripping a leading ``file://`` URI with URL-decoding, unescaping
    ``\\<space>``, and ``~`` expansion) must be a single path with a supported image suffix
    that resolves to an existing file. Anything more than a bare path — trailing text, a
    question, a mid-sentence mention — yields ``None``. This is the false-positive guard
    and never tokenizes trailing words: the whole string is tested as one path.

    A relative path resolves against ``workspace_dir``. No read boundary is applied — this
    is the user-gesture allowance (preprocessor-only; the model never reaches here).
    """
    candidate = text.strip()
    if not candidate:
        return None

    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in ("'", '"'):
        candidate = candidate[1:-1]

    if candidate.startswith("file://"):
        candidate = urllib.parse.unquote(candidate[len("file://") :])

    candidate = candidate.replace("\\ ", " ")
    if not candidate:
        return None

    # Suffix gate first — cheap and syscall-free. This runs on every REPL input (intake is
    # checked before slash dispatch), so non-image text short-circuits before any filesystem
    # resolve. It is also the collision guard: no slash command ends in an image suffix, so a
    # real command can never be mistaken for a lone image path.
    raw = Path(candidate).expanduser()
    if raw.suffix.lower() not in _MEDIA_TYPES:
        return None

    path = raw if raw.is_absolute() else workspace_dir / raw
    path = path.resolve()
    if not path.exists() or path.is_dir():
        return None
    return path
