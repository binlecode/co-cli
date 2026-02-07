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
│  _THEMES ──▶ Theme() ──▶ Console(theme=...)
│                                 │ ──▶ banner.py  (accent, art selection)
│                                 │ ──▶ main.py    (shared output)
│                                 │ ──▶ _confirm.py (shared output)
│                                 │
│  Indicators: ❯ ▸ ✦ ✖ ◈         │
│                                 │
│  display_status()               │
│  display_error()                │
│  display_info()                 │
└─────────────────────────────────┘
```

Theme selection flows from `settings.theme` (config/env) into a Rich `Theme` object at Console construction time. Semantic style names (e.g. `"accent"`, `"shell"`) are resolved natively by Rich — no wrapper function needed.

---

## Design Decisions

### Rich `Theme` with semantic style names

The console is a single `Console(theme=Theme(...))` instance created at module level. The theme dict is selected from `_THEMES` based on `settings.theme` at import time. Semantic style names (`"status"`, `"accent"`, `"shell"`, etc.) are registered with Rich and resolved natively — both in `style=` parameters and `[markup]` tags.

```python
_THEMES = {
    "dark":  {"status": "yellow",      "accent": "bold cyan",  ...},
    "light": {"status": "dark_orange", "accent": "bold blue",  ...},
}

console = Console(theme=Theme(_THEMES.get(settings.theme, _THEMES["light"])))
```

This is idiomatic Rich. Since `settings` is a module-level singleton resolved once at startup (env > settings.json > defaults), the theme is fixed for the process lifetime — no runtime theme switching needed, so binding at construction is fine. All modules that import `console` share the same themed instance.

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
| Role | Dark | Light | Usage |
|------|------|-------|-------|
| `status` | yellow | dark_orange | Status messages, thinking indicator |
| `info` | cyan | blue | Informational messages |
| `accent` | bold cyan | bold blue | Banner art, panel borders, emphasis |
| `yolo` | bold orange3 | bold orange3 | Auto-confirm mode indicator |
| `shell` | dim | dim | Shell output panel borders |
| `error` | bold red | bold red | Error messages (same both themes) |
| `success` | green | green | Success confirmation (same both themes) |
| `hint` | dim | dim | Secondary text, exit instructions |

Roles with the same value in both themes (error, success, hint, shell) could use inline Rich markup directly, but registering them in the theme keeps all style definitions in one place and allows per-theme customization later.

---

## Module Layout

### `co_cli/display.py`

Central display module. Owns the shared console, color definitions, indicators, and helper functions.

- `_THEMES` — theme-keyed style dict, two variants (dark/light)
- `console` — single `Console(theme=Theme(...))` instance, imported by all modules that produce terminal output
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
