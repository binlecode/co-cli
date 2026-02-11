---
title: Quickstart
nav_order: 5
---

# Quickstart

These docs cover the system design of **Co CLI** — a personal AI assistant CLI built on Pydantic AI with Ollama (local) and Gemini (cloud) backends.

## How to read

Start with [System Design](DESIGN-00-co-cli.md) for the architecture overview, then dive into component docs grouped by layer:

- **Core** — Agent factory, chat loop, LLM model selection. The runtime skeleton.
- **Infrastructure** — Telemetry, tail viewer, context governance, theming. Supporting subsystems that the core depends on.
- **Tools** — Shell sandbox, Obsidian vault, Google services, Slack. Each tool follows the `RunContext[CoDeps]` pattern.

Every component doc has 4 sections: *What & How* (overview + diagram), *Core Logic* (the meat), *Config* (settings table), *Files* (source paths).

## Install and usage

See the [README](https://github.com/binlecode/co-cli#readme) for installation, configuration, and usage instructions.
