"""CoDeps factory for the dream daemon process."""

from __future__ import annotations

from pathlib import Path

from co_cli.config.core import MEMORY_DIR, SESSIONS_DIR, load_config
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend


def build_codeps_for_daemon(co_home: Path) -> CoDeps:
    """Build a minimal CoDeps for the daemon process.

    Loads settings, creates a shell backend, wires memory/session paths from
    config constants, and builds the LLM model from config. Does not set any
    frontend or REPL state.
    """
    from co_cli.llm.factory import build_model

    config = load_config()
    shell = ShellBackend(workspace_dir=str(co_home))

    memory_dir = Path(config.memory_path) if config.memory_path else MEMORY_DIR
    sessions_dir = SESSIONS_DIR

    return CoDeps(
        shell=shell,
        config=config,
        model=build_model(config.llm),
        memory_dir=memory_dir,
        sessions_dir=sessions_dir,
    )
