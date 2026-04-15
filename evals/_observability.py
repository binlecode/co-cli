"""Eval observability bootstrap — file logging + OTel tracing.

Mirrors the dual-write setup in ``co_cli/main.py`` for standalone eval
runners that do not import ``co_cli.main``.  Call ``init_eval_observability()``
once at the top of ``main()`` in each eval runner; it is idempotent.

``service.name`` is set to ``"co-cli-eval"`` so eval spans are distinguishable
from interactive CLI spans in the shared ``co-cli-logs.db``.
"""

import tomllib
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.agent import InstrumentationSettings

from co_cli.config._core import LOGS_DIR, settings
from co_cli.observability._file_logging import setup_file_logging
from co_cli.observability._telemetry import setup_tracer_provider

_VERSION = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())[
    "project"
]["version"]


def init_eval_observability() -> None:
    """Set up file logging and OTel tracing for eval runners.

    Idempotent — safe to call multiple times and safe to call after
    ``co_cli.main`` has already installed a provider (``skip_if_installed``
    prevents a second provider from overwriting the first).
    """
    # Python logging → co-cli.log + errors.log (rotating, idempotent)
    setup_file_logging(
        log_dir=LOGS_DIR,
        level=settings.observability.log_level,
        max_size_mb=settings.observability.log_max_size_mb,
        backup_count=settings.observability.log_backup_count,
    )

    # OTel spans → co-cli-logs.db + spans.log
    # SimpleSpanProcessor (synchronous) flushes before the short-lived eval
    # process exits; BatchSpanProcessor would drop spans without force_flush.
    provider = setup_tracer_provider(
        service_name="co-cli-eval",
        service_version=_VERSION,
        log_dir=LOGS_DIR,
        max_size_mb=settings.observability.log_max_size_mb,
        backup_count=settings.observability.log_backup_count,
        skip_if_installed=True,
    )
    Agent.instrument_all(InstrumentationSettings(tracer_provider=provider, version=3))
