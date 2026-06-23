"""Render-fidelity regression: /status echoes input and renders styled ANSI cleanly.

Drives the real REPL Application through the ``_tui_harness`` (pipe-fed keys,
``Vt100_Output`` byte capture, production-equivalent ``patch_stdout(raw=True)``)
and asserts on the actual terminal byte stream — the layer that ``console.capture()``
flow tests and the ``PlainTextOutput`` smoke test cannot see.

Guards two regressions that shipped undetected:
- ESC-sanitization garble: ``patch_stdout(raw=False)`` made ``Vt100_Output.write``
  rewrite every ``\\x1b`` to ``?`` for styled mid-app output (``?[1m...`` instead of
  ``\\x1b[1m...``). The fix is ``patch_stdout(raw=True)``.
- No input echo: the inline ``TextArea`` never committed accepted input to
  scrollback. The fix is an echo ``console.print`` in ``_handle_one_input``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration._tui_harness import drive_repl, forced_tty_console, make_repl_deps

_SECTION_TITLES = (
    "Session",
    "Model & context",
    "Dream",
    "Work in flight",
    "Capabilities",
    "Degraded",
)


@pytest.mark.asyncio
async def test_status_echoes_input_and_renders_clean_ansi(tmp_path: Path) -> None:
    deps = make_repl_deps(tmp_path)

    with forced_tty_console():
        out = await drive_repl(deps, "/status", sentinel="Capabilities")

    # (a) The submitted input is echoed to scrollback. The echo source is
    # `[dim]{glyphs().prompt}[/dim] {user_input}` — `[dim]` renders as SGR 2, so the
    # dim-wrapped prompt marker is specific to the echo line (the TextArea's own
    # prompt char is not rich-dim-styled). The prompt glyph is theme-dependent, so
    # derive it from `glyphs()` rather than hardcoding. Do NOT assert literal
    # `/status`: rich highlight splits the leading `/` into its own SGR run, so
    # `/status` is never a contiguous substring once SGR is on. Pair marker + word.
    from co_cli.display.core import glyphs

    assert f"\x1b[2m{glyphs().prompt}" in out
    assert "status" in out

    # (b) No ESC-sanitization garble anywhere (the raw=False symptom).
    assert "?[" not in out

    # (c) At least one ESC sequence survives in the stream. Weak corroborator
    # only: prompt_toolkit's own renderer emits cursor/mode control sequences
    # regardless of console SGR, so this alone does not prove styling survived —
    # the SGR-fidelity weight is carried by (a) (the dim SGR echo run) and (b)
    # (no sanitized garble). Kept as a cheap floor.
    assert "\x1b[" in out

    # (d) All six section titles rendered.
    for title in _SECTION_TITLES:
        assert title in out, f"missing section title: {title!r}"
