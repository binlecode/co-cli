---
title: "08 — Theming & ASCII Art"
parent: Infrastructure
nav_order: 4
---

# Design: Theming & ASCII Art

## 1. What & How

Themed terminal UX for co-cli — light/dark color palettes, theme-aware ASCII art welcome banner, shared console instance, and display helpers. Theme selection flows from `settings.theme` into a Rich `Theme` object at Console construction time. Semantic style names (e.g. `"accent"`, `"shell"`) are resolved natively by Rich.

```
settings.theme ("light" | "dark")
      │
      ▼
┌─────────────────────────────────┐
│  display.py                     │
│                                 │
│  _THEMES ──▶ Theme() ──▶ Console(theme=...)
│                                 │ ──▶ _banner.py  (accent, art selection)
│                                 │ ──▶ main.py    (shared output)
│                                 │ ──▶ _commands.py (shared output)
│                                 │
│  Indicators: ❯ ▸ ✦ ✖ ◈         │
│  display_status(), display_error(), display_info()
└─────────────────────────────────┘
```

## 2. Core Logic

### Rich Theme with Semantic Styles

The console is a single `Console(theme=Theme(...))` instance created at module level. Semantic style names are registered with Rich and resolved natively in `style=` parameters and `[markup]` tags.

```python
_THEMES = {
    "dark":  {"status": "yellow",      "accent": "bold cyan",  ...},
    "light": {"status": "dark_orange", "accent": "bold blue",  ...},
}
console = Console(theme=Theme(_THEMES.get(settings.theme, _THEMES["light"])))
```

`set_theme(name)` calls `console.push_theme(Theme(...))` to switch at runtime before the chat loop starts.

### Color Semantics

| Role | Dark | Light | Usage |
|------|------|-------|-------|
| `status` | yellow | dark_orange | Status messages, thinking indicator |
| `info` | cyan | blue | Informational messages |
| `accent` | bold cyan | bold blue | Banner art, panel borders, emphasis |
| `yolo` | bold orange3 | bold orange3 | Auto-confirm mode indicator |
| `shell` | dim | dim | Shell output panel borders |
| `error` | bold red | bold red | Error messages |
| `success` | green | green | Success confirmation |
| `warning` | orange3 | orange3 | Warnings |
| `hint` | dim | dim | Secondary text, exit instructions |
| `thinking` | dim italic | dim italic | Verbose thinking stream panel/text |

### Theme-Aware ASCII Art Banner

Different character sets per theme: block characters (`█▀▀`) for dark terminals, box-drawing characters (`┌─┐`) for light terminals. Heavy block chars read better on dark backgrounds, lighter line-drawing chars suit light backgrounds.

The banner is a Rich `Panel` with `expand=False` so it hugs the content width. Border color uses the theme's accent.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Single console (no stdout/stderr split) | co-cli is a pure REPL, no piped data flow |
| Fixed indicators (no per-theme glyph variants) | Unicode chars render well on both themes; colors provide the distinction |
| Display layer doesn't touch core loop | `main.py` imports `console`, `PROMPT_CHAR`, `display_welcome_banner()` only |
| TTY detection is free | `Console()` auto-detects terminal, respects `NO_COLOR` |

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `theme` | `CO_CLI_THEME` | `"light"` | `"light"` or `"dark"` |

**Precedence:** CLI flag (`co chat --theme dark`) > env var > settings file > default.

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/display.py` | Shared console, `_THEMES`, `set_theme()`, indicators, display helpers |
| `co_cli/_banner.py` | Welcome banner with theme-aware ASCII art, `display_welcome_banner()` |
| `co_cli/config.py` | `theme` field in Settings |
