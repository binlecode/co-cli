# DESIGN: Theming & ASCII Art

Themed terminal UX for co-cli — light/dark color palettes, theme-aware ASCII art welcome banner, shared console instance, and display helpers. Adapted from BERA CLI's display patterns using Rich-native idioms.

---

## Architecture

```
settings.theme ("light" | "dark")
      │
      ▼
┌─────────────────────────────────┐
│  display.py                     │
│                                 │
│  _COLORS ─── _c(role) ──────── │ ──▶ banner.py  (accent, art selection)
│                                 │
│  console = Console()            │ ──▶ main.py    (shared output)
│                                 │ ──▶ _confirm.py (shared output)
│                                 │
│  Indicators: ❯ ▸ ✦ ✖ ◈         │
│                                 │
│  display_status()               │
│  display_error()                │
│  display_info()                 │
└─────────────────────────────────┘
```

Theme selection flows from `settings.theme` (config/env) through `_c()` at call time. No Console replacement, no global mutation, no import-order dependencies.

---

## Design Decisions

### Plain `Console()` with call-time color resolution

The console is a single `Console()` instance created once at module level. Theme colors are resolved at call time via `_c(role)`, which looks up `settings.theme` against the `_COLORS` dict. This avoids the stale-reference problem that arises when replacing a module-level `Console` object — other modules that imported the original reference would keep using the old one.

```python
_COLORS = {
    "dark":  {"status": "yellow",      "accent": "bold cyan",  ...},
    "light": {"status": "dark_orange", "accent": "bold blue",  ...},
}

console = Console()  # single instance, never replaced

def _c(role: str) -> str:
    return _COLORS.get(settings.theme, _COLORS["light"]).get(role, "")
```

**Why not `rich.theme.Theme`:** `Theme` objects are bound to a `Console` at construction. Switching themes means creating a new `Console`, which breaks any module that imported the original via `from display import console`. The a-cli pattern of a plain dict + call-time lookup is simpler and has no import-order hazards.

### Theme-aware ASCII art banner

The banner uses different character sets per theme following a-cli's pattern: block characters (`█▀▀`) for dark terminals, box-drawing characters (`┌─┐`) for light terminals. This is a visual weight choice — heavy block chars read better on dark backgrounds, lighter line-drawing chars suit light backgrounds.

```python
ASCII_ART = {
    "dark": [
        "    █▀▀ █▀█   █▀▀ █   █",
        "    █▄▄ █▄█   █▄▄ █▄▄ █",
    ],
    "light": [
        "    ┌─┐ ┌─┐   ┌─┐ ┬   ┬",
        "    │   │ │   │   │   │",
        "    └─┘ └─┘   └─┘ └─┘ ┴",
    ],
}
```

The banner is a Rich `Panel` with `expand=False` so it hugs the content width. Border color uses the theme's accent.

### Fixed indicators (no light/dark variants)

Unlike a-cli which has separate indicator sets per theme (`●`/`▸`, `✗`/`✖`, etc.), co-cli uses a single indicator set: `❯` (prompt), `▸` (bullet), `✦` (success), `✖` (error), `◈` (info). These characters render well on both light and dark terminals. The meaningful visual distinction between themes comes from colors, not glyph shape.

### Single console (no stdout/stderr split)

a-cli routes system messages to stderr (`console_err`) and agent output to stdout to support piped workflows. co-cli is a pure REPL with no piped data flow, so a single `Console()` on stdout is sufficient. All output — banner, status, errors, agent responses — goes to the same stream.

### Display layer doesn't touch core loop

The theme system is an overlay. `main.py` imports three things from the display layer:

- `console` — shared Rich Console (replaces the old local `Console()`)
- `PROMPT_CHAR` — the `❯` character for the input prompt
- `display_welcome_banner()` — replaces the old two-line banner

All other `console.print()` calls in the chat loop remain as inline Rich markup (`[dim]`, `[bold red]`, etc.). The display helpers (`display_status`, `display_error`, `display_info`) exist for use by tools and future callsites but are not forced into the core loop.

### TTY detection is free

`Console()` auto-detects whether stdout is a terminal. When piped or in CI, Rich strips ANSI codes and disables interactive features. The `NO_COLOR` env var is also respected automatically. No custom detection code needed.

---

## Color Semantics

| Role | Dark | Light | Usage |
|------|------|-------|-------|
| `status` | yellow | dark_orange | Status messages, thinking indicator |
| `info` | cyan | blue | Informational messages |
| `accent` | bold cyan | bold blue | Banner art, panel borders, emphasis |
| `yolo` | bold orange3 | bold orange3 | Auto-confirm mode indicator |
| `error` | bold red | bold red | Error messages (same both themes) |
| `success` | green | green | Success confirmation (same both themes) |
| `hint` | dim | dim | Secondary text, exit instructions |

Colors that are the same in both themes (error, success, hint) use inline Rich markup directly rather than going through `_c()`.

---

## Module Layout

### `co_cli/display.py`

Central display module. Owns the shared console, color definitions, indicators, and helper functions.

- `_COLORS` — theme-keyed color dict, two variants (dark/light)
- `_c(role)` — resolves a semantic color name for the active theme at call time
- `console` — single `Console()` instance, imported by all modules that produce terminal output
- `PROMPT_CHAR`, `BULLET`, `SUCCESS`, `ERROR`, `INFO` — Unicode indicator constants
- `display_status(message, style?)` — themed bullet + message
- `display_error(message, hint?)` — red-bordered Panel with error icon and optional recovery hint
- `display_info(message)` — themed info icon + message

### `co_cli/banner.py`

Welcome banner with theme-aware ASCII art. Separate from `display.py` because banner layout logic (art selection, Panel construction, version formatting) is distinct from general display utilities.

- `ASCII_ART` — theme-keyed dict with dark (block chars, 2 lines) and light (box-drawing, 3 lines) variants
- `display_welcome_banner(model_info, version?)` — renders a Rich Panel with ASCII art, version, model info, and exit hint

### `co_cli/config.py`

Settings model extended with `theme` field.

- `theme: str = Field(default="light")` — `"light"` or `"dark"`
- Env var: `CO_CLI_THEME`
- CLI override: `co chat --theme dark` / `-t dark` (sets `settings.theme` before the chat loop)

---

## Integration Points

### `main.py`

```python
from co_cli.display import console, PROMPT_CHAR
from co_cli.banner import display_welcome_banner
```

- `console` replaces the old module-level `Console()`
- `display_welcome_banner(model_info)` replaces the old two `console.print()` banner lines
- `f"Co {PROMPT_CHAR} "` replaces `"Co > "` as the REPL prompt
- `--theme` / `-t` flag on the `chat` command overrides `settings.theme` at runtime
- Core loop `console.print()` calls are unchanged — inline markup preserved

### `tools/_confirm.py`

```python
from co_cli.display import console
```

Replaces the old private `_console = Console()`. The shared console ensures confirm prompts respect any future console configuration (width, force_terminal, etc.) set in one place.

---

## Terminal UX

### Dark theme (`--theme dark`)

```
╭────────────────────────────────────────╮
│                                        │
│     █▀▀ █▀█   █▀▀ █   █                │
│     █▄▄ █▄█   █▄▄ █▄▄ █                │
│                                        │
│     v0.2.2 — CLI Assistant             │
│                                        │
│     Model: Gemini (gemini-2.0-flash)   │
│                                        │
│     Type 'exit' to quit                │
│                                        │
╰────────────────────────────────────────╯

Co ❯ run ls -la
Execute command: ls -la? [y/n/a(yolo)]
```

Panel border and ASCII art in **bold cyan**. Status text in **yellow**.

### Light theme (`--theme light`)

```
╭────────────────────────────────────────╮
│                                        │
│     ┌─┐ ┌─┐   ┌─┐ ┬   ┬                │
│     │   │ │   │   │   │                │
│     └─┘ └─┘   └─┘ └─┘ ┴                │
│                                        │
│     v0.2.2 — CLI Assistant             │
│                                        │
│     Model: Gemini (gemini-2.0-flash)   │
│                                        │
│     Type 'exit' to quit                │
│                                        │
╰────────────────────────────────────────╯

Co ❯ run ls -la
Execute command: ls -la? [y/n/a(yolo)]
```

Panel border and ASCII art in **bold blue**. Status text in **dark orange**.

---

## Configuration Precedence

Theme follows the standard co-cli config precedence:

1. CLI flag: `co chat --theme dark`
2. Env var: `CO_CLI_THEME=dark`
3. Settings file: `~/.config/co-cli/settings.json` → `{"theme": "dark"}`
4. Built-in default: `"light"`

---

## Reference

Adapted from BERA CLI (`/Users/binle/workspace_bera/a-cli`):

- `cli/config.py` — `THEME_INDICATORS` dict with per-theme Unicode indicators
- `cli/display.py` — `_current_theme` global, `set_theme()`, display helpers resolving indicators at call time, plain `Console()`
- `cli/qa_chat.py` — `_display_welcome_banner()` with per-theme ASCII art selection

Key differences from a-cli: co-cli uses a single indicator set (no per-theme glyph variants), reads theme from `settings.theme` directly (no separate `_current_theme` global), and keeps the core chat loop's inline Rich markup unchanged.
