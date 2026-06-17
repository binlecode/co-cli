"""Subprocess environment sanitization — the canonical env for every spawn site.

Allowlist-based (not blocklist) to prevent pager/editor hijacking
(CVE-2025-66032 vectors) and shared-library injection. Shared low-level home
imported downward by both the tools layer and the session layer.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Allowlist: only these host env vars propagate to subprocess execution.
# Tight by design — only the minimum needed for basic shell commands.
SAFE_ENV_VARS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TERM",
    "SHELL",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
}


def restricted_env() -> dict[str, str]:
    """Build a sanitized environment for subprocess execution.

    Uses an allowlist (not blocklist) to prevent pager/editor hijacking
    (CVE-2025-66032 vectors) and shared-library injection.
    """
    base = {k: v for k, v in os.environ.items() if k in SAFE_ENV_VARS}
    base["PYTHONUNBUFFERED"] = "1"
    # Force safe pagers to block arbitrary code execution via PAGER/GIT_PAGER
    base["PAGER"] = "cat"
    base["GIT_PAGER"] = "cat"
    return base


def build_subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Canonical subprocess env for every co-cli spawn site.

    Allowlist base + optional skill overlay; refuses keys that would shadow
    the host allowlist (PATH, HOME, etc.) to keep the security boundary intact.
    """
    env = restricted_env()
    if extra_env:
        for k, v in extra_env.items():
            if k in SAFE_ENV_VARS:
                logger.warning("subprocess.env_shadow_refused key=%s", k)
                continue
            env[k] = v
    return env
