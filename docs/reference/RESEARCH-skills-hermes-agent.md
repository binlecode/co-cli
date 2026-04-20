# RESEARCH: Hermes Agent Skills

Scan basis:

- fetched remote ref `origin/main` at commit `6af04474a393753328734aa622e3edfa62d3c0fb`
- local `git pull` in `~/workspace_genai/hermes-agent` was blocked by an existing modified `uv.lock`, so this note traces the fetched remote ref directly and leaves that worktree unchanged
- code paths read: `tools/skills_sync.py`, `tools/skills_hub.py`, `tools/skills_tool.py`, `agent/skill_utils.py`, `agent/prompt_builder.py`, `agent/skill_commands.py`, `cli.py`, `gateway/run.py`, `cron/scheduler.py`

## 1. Registration Sources

- Bundled skills live under repo `skills/`. `tools/skills_sync.py` is the code path that seeds them into the runtime skills store at `~/.hermes/skills/`, tracked by `~/.hermes/skills/.bundled_manifest`.
- Sync behavior is manifest-based: new bundled skills are copied, unchanged bundled skills may update in place, user-modified installed copies are skipped, and user-deleted installed copies stay deleted.
- Optional skills live under repo `optional-skills/`. `tools/skills_hub.OptionalSkillSource` exposes them to hub search/fetch/inspect but explicitly does not copy them into `~/.hermes/skills/` or make them prompt-visible by default.
- Runtime skill discovery merges the local skills dir with `skills.external_dirs` from config. `agent.skill_utils.get_all_skills_dirs()` returns local first and external dirs after it; local names win on collisions.
- Plugin-provided skills are separate from repo and local tree skills. `skill_view()` resolves them only through qualified names such as `plugin:skill`.

## 2. LLM Context Loading Logic

1. `tools/skills_tool.py` registers `skills_list` and `skill_view` in the `skills` toolset.
2. `agent.prompt_builder.build_skills_system_prompt()` scans installed `SKILL.md` and category `DESCRIPTION.md` files from local and external skill dirs, filters by platform, disabled-skill config, and `metadata.hermes` conditional-visibility fields, then emits the `<available_skills>` block injected into the system prompt.
3. The prompt-builder index is cached in memory and on disk (`~/.hermes/.skills_prompt_snapshot.json`) and rebuilt when the manifest of indexed files changes.
4. `skills_list()` is the compact discovery layer: it returns only `name`, `description`, and `category` for installed, enabled, platform-compatible skills.
5. `skill_view(name)` is the full-load path: it resolves a local skill, external skill, or qualified plugin skill; reads `SKILL.md`; checks platform and disabled state; and returns full content plus linked-file metadata for `references/`, `templates/`, `assets/`, and `scripts/`.
6. On full load, `skill_view()` also computes setup metadata from frontmatter, registers declared env passthrough names for child execution environments, registers declared credential files for remote sandboxes, and returns `setup_needed` / `setup_note` status to the caller.
7. `agent.skill_commands.build_preloaded_skills_prompt()` preloads named skills into the CLI session system prompt before the first turn when `--skills` is used.
8. `agent.skill_commands.build_skill_invocation_message()` loads a skill for `/skill-name` commands by prepending a system activation note and the loaded skill content to the user message.
9. `gateway/run.py` uses the same load/build helpers to auto-load bound skills for new messaging sessions, and `cron/scheduler.py` prepends one or more loaded skills before the cron job prompt runs.

## 3. Inventory Flow

- Built-in shipped inventory: `70` skills under `skills/`.
- Optional shipped inventory: `57` skills under `optional-skills/`.
- The inventory below uses only repo-frontmatter descriptions and repo-visible package structure. "Integration surface" means only these code-visible packaging signals: helper `scripts/`, `references/`, `templates/`, `assets/`, declared required env vars, declared required credential files, or conditional-visibility metadata. It does not summarize tool instructions inside the prose body of `SKILL.md`.

## 4. Built-In Inventory (`skills/`)

Flow: Repo `skills/` -> `tools/skills_sync.py` -> `~/.hermes/skills/` -> `build_skills_system_prompt()` / `skills_list()` / `skill_view()` / slash-command scan.

### `apple` (4)

- `apple-notes` — Manage Apple Notes via the memo CLI on macOS (create, view, search, edit). Integration surface: SKILL.md only.
- `apple-reminders` — Manage Apple Reminders via remindctl CLI (list, add, complete, delete). Integration surface: SKILL.md only.
- `findmy` — Track Apple devices and AirTags via FindMy.app on macOS using AppleScript and screen capture. Integration surface: SKILL.md only.
- `imessage` — Send and receive iMessages/SMS via the imsg CLI on macOS. Integration surface: SKILL.md only.

### `autonomous-ai-agents` (4)

- `claude-code` — Delegate coding tasks to Claude Code (Anthropic's CLI agent). Use for building features, refactoring, PR reviews, and iterative coding. Requires the claude CLI installed. Integration surface: SKILL.md only.
- `codex` — Delegate coding tasks to OpenAI Codex CLI agent. Use for building features, refactoring, PR reviews, and batch issue fixing. Requires the codex CLI and a git repository. Integration surface: SKILL.md only.
- `hermes-agent` — Complete guide to using and extending Hermes Agent — CLI usage, setup, configuration, spawning additional agents, gateway platforms, skills, voice, tools, profiles, and a concise contributor reference. Load this skill when helping users configure Hermes, troubleshoot issues, spawn agent instances, or make code contributions. Integration surface: SKILL.md only.
- `opencode` — Delegate coding tasks to OpenCode CLI agent for feature implementation, refactoring, PR review, and long-running autonomous sessions. Requires the opencode CLI installed and authenticated. Integration surface: SKILL.md only.

### `creative` (10)

- `architecture-diagram` — Generate dark-themed SVG diagrams of software systems and cloud infrastructure as standalone HTML files with inline SVG graphics. Semantic component colors (cyan=frontend, emerald=backend, violet=database, amber=cloud/AWS, rose=security, orange=message bus), JetBrains Mono font, grid background. Best suited for software architecture, cloud/VPC topology, microservice maps, service-mesh diagrams, database + API layer diagrams, security groups, message buses — anything that fits a tech-infra deck with a dark aesthetic. If a more specialized diagramming skill exists for the subject (scientific, educational, hand-drawn, animated, etc.), prefer that — otherwise this skill can also serve as a general-purpose SVG diagram fallback. Based on Cocoon AI's architecture-diagram-generator (MIT). Integration surface: ships templates.
- `ascii-art` — Generate ASCII art using pyfiglet (571 fonts), cowsay, boxes, toilet, image-to-ascii, remote APIs (asciified, ascii.co.uk), and LLM fallback. No API keys required. Integration surface: SKILL.md only.
- `ascii-video` — Production pipeline for ASCII art video — any format. Converts video/audio/images/generative input into colored ASCII character video output (MP4, GIF, image sequence). Covers: video-to-ASCII conversion, audio-reactive music visualizers, generative ASCII art animations, hybrid video+audio reactive, text/lyrics overlays, real-time terminal rendering. Use when users request: ASCII video, text art video, terminal-style video, character art animation, retro text visualization, audio visualizer in ASCII, converting video to ASCII art, matrix-style effects, or any animated ASCII output. Integration surface: ships reference docs.
- `baoyu-infographic` — Generate professional infographics with 21 layout types and 21 visual styles. Analyzes content, recommends layout×style combinations, and generates publication-ready infographics. Use when user asks to create "infographic", "visual summary", "信息图", "可视化", or "高密度信息大图". Integration surface: ships reference docs.
- `excalidraw` — Create hand-drawn style diagrams using Excalidraw JSON format. Generate .excalidraw files for architecture diagrams, flowcharts, sequence diagrams, concept maps, and more. Files can be opened at excalidraw.com or uploaded for shareable links. Integration surface: ships helper scripts, ships reference docs.
- `ideation` — Generate project ideas through creative constraints. Use when the user says 'I want to build something', 'give me a project idea', 'I'm bored', 'what should I make', 'inspire me', or any variant of 'I have tools but no direction'. Works for code, art, hardware, writing, tools, and anything that can be made. Integration surface: ships reference docs.
- `manim-video` — Production pipeline for mathematical and technical animations using Manim Community Edition. Creates 3Blue1Brown-style explainer videos, algorithm visualizations, equation derivations, architecture diagrams, and data stories. Use when users request: animated explanations, math animations, concept visualizations, algorithm walkthroughs, technical explainers, 3Blue1Brown style videos, or any programmatic animation with geometric/mathematical content. Integration surface: ships helper scripts, ships reference docs.
- `p5js` — Production pipeline for interactive and generative visual art using p5.js. Creates browser-based sketches, generative art, data visualizations, interactive experiences, 3D scenes, audio-reactive visuals, and motion graphics — exported as HTML, PNG, GIF, MP4, or SVG. Covers: 2D/3D rendering, noise and particle systems, flow fields, shaders (GLSL), pixel manipulation, kinetic typography, WebGL scenes, audio analysis, mouse/keyboard interaction, and headless high-res export. Use when users request: p5.js sketches, creative coding, generative art, interactive visualizations, canvas animations, browser-based visual art, data viz, shader effects, or any p5.js project. Integration surface: ships helper scripts, ships reference docs, ships templates.
- `popular-web-designs` — 54 production-quality design systems extracted from real websites. Load a template to generate HTML/CSS that matches the visual identity of sites like Stripe, Linear, Vercel, Notion, Airbnb, and more. Each template includes colors, typography, components, layout rules, and ready-to-use CSS values. Integration surface: ships templates.
- `songwriting-and-ai-music` — Songwriting craft, AI music generation prompts (Suno focus), parody/adaptation techniques, phonetic tricks, and lessons learned. These are tools and ideas, not rules. Break any of them when the art calls for it. Integration surface: SKILL.md only.

### `data-science` (1)

- `jupyter-live-kernel` — Use a live Jupyter kernel for stateful, iterative Python execution via hamelnb. Load this skill when the task involves exploration, iteration, or inspecting intermediate results — data science, ML experimentation, API exploration, or building up complex code step-by-step. Uses terminal to run CLI commands against a live Jupyter kernel. No new tools required. Integration surface: SKILL.md only.

### `devops` (1)

- `webhook-subscriptions` — Create and manage webhook subscriptions for event-driven agent activation, or for direct push notifications (zero LLM cost). Use when the user wants external services to trigger agent runs OR push notifications to chats. Integration surface: SKILL.md only.

### `dogfood` (1)

- `dogfood` — Systematic exploratory QA testing of web applications — find bugs, capture evidence, and generate structured reports Integration surface: ships reference docs, ships templates.

### `email` (1)

- `himalaya` — CLI to manage emails via IMAP/SMTP. Use himalaya to list, read, write, reply, forward, search, and organize emails from the terminal. Supports multiple accounts and message composition with MML (MIME Meta Language). Integration surface: ships reference docs.

### `gaming` (2)

- `minecraft-modpack-server` — Set up a modded Minecraft server from a CurseForge/Modrinth server pack zip. Covers NeoForge/Forge install, Java version, JVM tuning, firewall, LAN config, backups, and launch scripts. Integration surface: SKILL.md only.
- `pokemon-player` — Play Pokemon games autonomously via headless emulation. Starts a game server, reads structured game state from RAM, makes strategic decisions, and sends button inputs — all from the terminal. Integration surface: SKILL.md only.

### `github` (6)

- `codebase-inspection` — Inspect and analyze codebases using pygount for LOC counting, language breakdown, and code-vs-comment ratios. Use when asked to check lines of code, repo size, language composition, or codebase stats. Integration surface: SKILL.md only.
- `github-auth` — Set up GitHub authentication for the agent using git (universally available) or the gh CLI. Covers HTTPS tokens, SSH keys, credential helpers, and gh auth — with a detection flow to pick the right method automatically. Integration surface: ships helper scripts.
- `github-code-review` — Review code changes by analyzing git diffs, leaving inline comments on PRs, and performing thorough pre-push review. Works with gh CLI or falls back to git + GitHub REST API via curl. Integration surface: ships reference docs.
- `github-issues` — Create, manage, triage, and close GitHub issues. Search existing issues, add labels, assign people, and link to PRs. Works with gh CLI or falls back to git + GitHub REST API via curl. Integration surface: ships templates.
- `github-pr-workflow` — Full pull request lifecycle — create branches, commit changes, open PRs, monitor CI status, auto-fix failures, and merge. Works with gh CLI or falls back to git + GitHub REST API via curl. Integration surface: ships reference docs, ships templates.
- `github-repo-management` — Clone, create, fork, configure, and manage GitHub repositories. Manage remotes, secrets, releases, and workflows. Works with gh CLI or falls back to git + GitHub REST API via curl. Integration surface: ships reference docs.

### `mcp` (1)

- `native-mcp` — Built-in MCP (Model Context Protocol) client that connects to external MCP servers, discovers their tools, and registers them as native Hermes Agent tools. Supports stdio and HTTP transports with automatic reconnection, security filtering, and zero-config tool injection. Integration surface: SKILL.md only.

### `media` (4)

- `gif-search` — Search and download GIFs from Tenor using curl. No dependencies beyond curl and jq. Useful for finding reaction GIFs, creating visual content, and sending GIFs in chat. Integration surface: SKILL.md only.
- `heartmula` — Set up and run HeartMuLa, the open-source music generation model family (Suno-like). Generates full songs from lyrics + tags with multilingual support. Integration surface: SKILL.md only.
- `songsee` — Generate spectrograms and audio feature visualizations (mel, chroma, MFCC, tempogram, etc.) from audio files via CLI. Useful for audio analysis, music production debugging, and visual documentation. Integration surface: SKILL.md only.
- `youtube-content` — Fetch YouTube video transcripts and transform them into structured content (chapters, summaries, threads, blog posts). Use when the user shares a YouTube URL or video link, asks to summarize a video, requests a transcript, or wants to extract and reformat content from any YouTube video. Integration surface: ships helper scripts, ships reference docs.

### `mlops` (1)

- `huggingface-hub` — Hugging Face Hub CLI (hf) — search, download, and upload models and datasets, manage repos, query datasets with SQL, deploy inference endpoints, manage Spaces and buckets. Integration surface: SKILL.md only.

### `mlops/evaluation` (2)

- `evaluating-llms-harness` — Evaluates LLMs across 60+ academic benchmarks (MMLU, HumanEval, GSM8K, TruthfulQA, HellaSwag). Use when benchmarking model quality, comparing models, reporting academic results, or tracking training progress. Industry standard used by EleutherAI, HuggingFace, and major labs. Supports HuggingFace, vLLM, APIs. Integration surface: ships reference docs.
- `weights-and-biases` — Track ML experiments with automatic logging, visualize training in real-time, optimize hyperparameters with sweeps, and manage model registry with W&B - collaborative MLOps platform Integration surface: ships reference docs.

### `mlops/inference` (4)

- `llama-cpp` — Run LLM inference with llama.cpp on CPU, Apple Silicon, AMD/Intel GPUs, or NVIDIA — plus GGUF model conversion and quantization (2–8 bit with K-quants and imatrix). Covers CLI, Python bindings, OpenAI-compatible server, and Ollama/LM Studio integration. Use for edge deployment, M1/M2/M3/M4 Macs, CUDA-less environments, or flexible local quantization. Integration surface: ships reference docs.
- `obliteratus` — Remove refusal behaviors from open-weight LLMs using OBLITERATUS — mechanistic interpretability techniques (diff-in-means, SVD, whitened SVD, LEACE, SAE decomposition, etc.) to excise guardrails while preserving reasoning. 9 CLI methods, 28 analysis modules, 116 model presets across 5 compute tiers, tournament evaluation, and telemetry-driven recommendations. Use when a user wants to uncensor, abliterate, or remove refusal from an LLM. Integration surface: ships reference docs, ships templates.
- `outlines` — Guarantee valid JSON/XML/code structure during generation, use Pydantic models for type-safe outputs, support local models (Transformers, vLLM), and maximize inference speed with Outlines - dottxt.ai's structured generation library Integration surface: ships reference docs.
- `serving-llms-vllm` — Serves LLMs with high throughput using vLLM's PagedAttention and continuous batching. Use when deploying production LLM APIs, optimizing inference latency/throughput, or serving models with limited GPU memory. Supports OpenAI-compatible endpoints, quantization (GPTQ/AWQ/FP8), and tensor parallelism. Integration surface: ships reference docs.

### `mlops/models` (2)

- `audiocraft-audio-generation` — PyTorch library for audio generation including text-to-music (MusicGen) and text-to-sound (AudioGen). Use when you need to generate music from text descriptions, create sound effects, or perform melody-conditioned music generation. Integration surface: ships reference docs.
- `segment-anything-model` — Foundation model for image segmentation with zero-shot transfer. Use when you need to segment any object in images using points, boxes, or masks as prompts, or automatically generate all object masks in an image. Integration surface: ships reference docs.

### `mlops/research` (1)

- `dspy` — Build complex AI systems with declarative programming, optimize prompts automatically, create modular RAG systems and agents with DSPy - Stanford NLP's framework for systematic LM programming Integration surface: ships reference docs.

### `mlops/training` (3)

- `axolotl` — Expert guidance for fine-tuning LLMs with Axolotl - YAML configs, 100+ models, LoRA/QLoRA, DPO/KTO/ORPO/GRPO, multimodal support Integration surface: ships reference docs.
- `fine-tuning-with-trl` — Fine-tune LLMs using reinforcement learning with TRL - SFT for instruction tuning, DPO for preference alignment, PPO/GRPO for reward optimization, and reward model training. Use when need RLHF, align model with preferences, or train from human feedback. Works with HuggingFace Transformers. Integration surface: ships reference docs, ships templates.
- `unsloth` — Expert guidance for fast fine-tuning with Unsloth - 2-5x faster training, 50-80% less memory, LoRA/QLoRA optimization Integration surface: ships reference docs.

### `note-taking` (1)

- `obsidian` — Read, search, and create notes in the Obsidian vault. Integration surface: SKILL.md only.

### `productivity` (7)

- `google-workspace` — Gmail, Calendar, Drive, Contacts, Sheets, and Docs integration for Hermes. Uses Hermes-managed OAuth2 setup, prefers the Google Workspace CLI (`gws`) when available for broader API coverage, and falls back to the Python client libraries otherwise. Integration surface: ships helper scripts, ships reference docs.
- `linear` — Manage Linear issues, projects, and teams via the GraphQL API. Create, update, search, and organize issues. Uses API key auth (no OAuth needed). All operations via curl — no dependencies. Integration surface: SKILL.md only.
- `maps` — Location intelligence — geocode a place, reverse-geocode coordinates, find nearby places (44 POI categories), driving/walking/cycling distance + time, turn-by-turn directions, timezone lookup, bounding box + area for a named place, and POI search within a rectangle. Uses OpenStreetMap + Overpass + OSRM. Free, no API key. Integration surface: ships helper scripts, uses conditional visibility metadata.
- `nano-pdf` — Edit PDFs with natural-language instructions using the nano-pdf CLI. Modify text, fix typos, update titles, and make content changes to specific pages without manual editing. Integration surface: SKILL.md only.
- `notion` — Notion API for creating and managing pages, databases, and blocks via curl. Search, create, update, and query Notion workspaces directly from the terminal. Integration surface: ships reference docs.
- `ocr-and-documents` — Extract text from PDFs and scanned documents. Use web_extract for remote URLs, pymupdf for local text-based PDFs, marker-pdf for OCR/scanned docs. For DOCX use python-docx, for PPTX see the powerpoint skill. Integration surface: ships helper scripts.
- `powerpoint` — Use this skill any time a .pptx file is involved in any way — as input, output, or both. This includes: creating slide decks, pitch decks, or presentations; reading, parsing, or extracting text from any .pptx file (even if the extracted content will be used elsewhere, like in an email or summary); editing, modifying, or updating existing presentations; combining or splitting slide files; working with templates, layouts, speaker notes, or comments. Trigger whenever the user mentions "deck," "slides," "presentation," or references a .pptx filename, regardless of what they plan to do with the content afterward. If a .pptx file needs to be opened, created, or touched, use this skill. Integration surface: ships helper scripts.

### `red-teaming` (1)

- `godmode` — Jailbreak API-served LLMs using G0DM0D3 techniques — Parseltongue input obfuscation (33 techniques), GODMODE CLASSIC system prompt templates, ULTRAPLINIAN multi-model racing, encoding escalation, and Hermes-native prefill/system prompt integration. Use when a user wants to bypass safety filters, jailbreak a model, or red-team model robustness. Integration surface: ships helper scripts, ships reference docs, ships templates.

### `research` (5)

- `arxiv` — Search and retrieve academic papers from arXiv using their free REST API. No API key needed. Search by keyword, author, category, or ID. Combine with web_extract or the ocr-and-documents skill to read full paper content. Integration surface: ships helper scripts.
- `blogwatcher` — Monitor blogs and RSS/Atom feeds for updates using the blogwatcher-cli tool. Add blogs, scan for new articles, track read status, and filter by category. Integration surface: SKILL.md only.
- `llm-wiki` — Karpathy's LLM Wiki — build and maintain a persistent, interlinked markdown knowledge base. Ingest sources, query compiled knowledge, and lint for consistency. Integration surface: SKILL.md only.
- `polymarket` — Query Polymarket prediction market data — search markets, get prices, orderbooks, and price history. Read-only via public REST APIs, no API key needed. Integration surface: ships helper scripts, ships reference docs.
- `research-paper-writing` — End-to-end pipeline for writing ML/AI research papers — from experiment design through analysis, drafting, revision, and submission. Covers NeurIPS, ICML, ICLR, ACL, AAAI, COLM. Integrates automated experiment monitoring, statistical analysis, iterative writing, and citation verification. Integration surface: ships reference docs, ships templates, uses conditional visibility metadata.

### `smart-home` (1)

- `openhue` — Control Philips Hue lights, rooms, and scenes via the OpenHue CLI. Turn lights on/off, adjust brightness, color, color temperature, and activate scenes. Integration surface: SKILL.md only.

### `social-media` (1)

- `xurl` — Interact with X/Twitter via xurl, the official X API CLI. Use for posting, replying, quoting, searching, timelines, mentions, likes, reposts, bookmarks, follows, DMs, media upload, and raw v2 endpoint access. Integration surface: SKILL.md only.

### `software-development` (6)

- `plan` — Plan mode for Hermes — inspect context, write a markdown plan into the active workspace's `.hermes/plans/` directory, and do not execute the work. Integration surface: SKILL.md only.
- `requesting-code-review` — Pre-commit verification pipeline — static security scan, baseline-aware quality gates, independent reviewer subagent, and auto-fix loop. Use after code changes and before committing, pushing, or opening a PR. Integration surface: SKILL.md only.
- `subagent-driven-development` — Use when executing implementation plans with independent tasks. Dispatches fresh delegate_task per task with two-stage review (spec compliance then code quality). Integration surface: SKILL.md only.
- `systematic-debugging` — Use when encountering any bug, test failure, or unexpected behavior. 4-phase root cause investigation — NO fixes without understanding the problem first. Integration surface: SKILL.md only.
- `test-driven-development` — Use when implementing any feature or bugfix, before writing implementation code. Enforces RED-GREEN-REFACTOR cycle with test-first approach. Integration surface: SKILL.md only.
- `writing-plans` — Use when you have a spec or requirements for a multi-step task. Creates comprehensive implementation plans with bite-sized tasks, exact file paths, and complete code examples. Integration surface: SKILL.md only.

## 5. Optional Inventory (`optional-skills/`)

Flow: Repo `optional-skills/` -> `tools/skills_hub.OptionalSkillSource` -> hub search/install/inspect -> installed copy under `~/.hermes/skills/` -> normal local-skill prompt/index flow.

### `autonomous-ai-agents` (2)

- `blackbox` — Delegate coding tasks to Blackbox AI CLI agent. Multi-model agent with built-in judge that runs tasks through multiple LLMs and picks the best result. Requires the blackbox CLI and a Blackbox AI API key. Integration surface: SKILL.md only.
- `honcho` — Configure and use Honcho memory with Hermes -- cross-session user modeling, multi-profile peer isolation, observation config, dialectic reasoning, session summaries, and context budget enforcement. Use when setting up Honcho, troubleshooting memory, managing profiles with Honcho peers, or tuning observation, recall, and dialectic settings. Integration surface: SKILL.md only.

### `blockchain` (2)

- `base` — Query Base (Ethereum L2) blockchain data with USD pricing — wallet balances, token info, transaction details, gas analysis, contract inspection, whale detection, and live network stats. Uses Base RPC + CoinGecko. No API key required. Integration surface: ships helper scripts.
- `solana` — Query Solana blockchain data with USD pricing — wallet balances, token portfolios with values, transaction details, NFTs, whale detection, and live network stats. Uses Solana RPC + CoinGecko. No API key required. Integration surface: ships helper scripts.

### `communication` (1)

- `one-three-one-rule` — Structured decision-making framework for technical proposals and trade-off analysis. When the user faces a choice between multiple approaches (architecture decisions, tool selection, refactoring strategies, migration paths), this skill produces a 1-3-1 format: one clear problem statement, three distinct options with pros/cons, and one concrete recommendation with definition of done and implementation plan. Use when the user asks for a "1-3-1", says "give me options", or needs help choosing between competing approaches. Integration surface: SKILL.md only.

### `creative` (4)

- `blender-mcp` — Control Blender directly from Hermes via socket connection to the blender-mcp addon. Create 3D objects, materials, animations, and run arbitrary Blender Python (bpy) code. Use when user wants to create or modify anything in Blender. Integration surface: SKILL.md only.
- `concept-diagrams` — Generate flat, minimal light/dark-aware SVG diagrams as standalone HTML files, using a unified educational visual language with 9 semantic color ramps, sentence-case typography, and automatic dark mode. Best suited for educational and non-software visuals — physics setups, chemistry mechanisms, math curves, physical objects (aircraft, turbines, smartphones, mechanical watches), anatomy, floor plans, cross-sections, narrative journeys (lifecycle of X, process of Y), hub-spoke system integrations (smart city, IoT), and exploded layer views. If a more specialized skill exists for the subject (dedicated software/cloud architecture, hand-drawn sketches, animated explainers, etc.), prefer that — otherwise this skill can also serve as a general-purpose SVG diagram fallback with a clean educational look. Ships with 15 example diagrams. Integration surface: ships reference docs, ships templates.
- `meme-generation` — Generate real meme images by picking a template and overlaying text with Pillow. Produces actual .png meme files. Integration surface: ships helper scripts.
- `touchdesigner-mcp` — Control a running TouchDesigner instance via twozero MCP — create operators, set parameters, wire connections, execute Python, build real-time visuals. 36 native tools. Integration surface: ships helper scripts, ships reference docs.

### `devops` (2)

- `docker-management` — Manage Docker containers, images, volumes, networks, and Compose stacks — lifecycle ops, debugging, cleanup, and Dockerfile optimization. Integration surface: uses conditional visibility metadata.
- `inference-sh-cli` — Run 150+ AI apps via inference.sh CLI (infsh) — image generation, video creation, LLMs, search, 3D, social automation. Uses the terminal tool. Triggers: inference.sh, infsh, ai apps, flux, veo, image generation, video generation, seedream, seedance, tavily Integration surface: ships reference docs.

### `email` (1)

- `agentmail` — Give the agent its own dedicated email inbox via AgentMail. Send, receive, and manage email autonomously using agent-owned email addresses (e.g. hermes-agent@agentmail.to). Integration surface: SKILL.md only.

### `health` (2)

- `fitness-nutrition` — Gym workout planner and nutrition tracker. Search 690+ exercises by muscle, equipment, or category via wger. Look up macros and calories for 380,000+ foods via USDA FoodData Central. Compute BMI, TDEE, one-rep max, macro splits, and body fat — pure Python, no pip installs. Built for anyone chasing gains, cutting weight, or just trying to eat better. Integration surface: ships helper scripts, ships reference docs, declares required env vars.
- `neuroskill-bci` — Connect to a running NeuroSkill instance and incorporate the user's real-time cognitive and emotional state (focus, relaxation, mood, cognitive load, drowsiness, heart rate, HRV, sleep staging, and 40+ derived EXG scores) into responses. Requires a BCI wearable (Muse 2/S or OpenBCI) and the NeuroSkill desktop app running locally. Integration surface: ships reference docs.

### `mcp` (2)

- `fastmcp` — Build, test, inspect, install, and deploy MCP servers with FastMCP in Python. Use when creating a new MCP server, wrapping an API or database as MCP tools, exposing resources or prompts, or preparing a FastMCP server for Claude Code, Cursor, or HTTP deployment. Integration surface: ships helper scripts, ships reference docs, ships templates.
- `mcporter` — Use the mcporter CLI to list, configure, auth, and call MCP servers/tools directly (HTTP or stdio), including ad-hoc servers, config edits, and CLI/type generation. Integration surface: SKILL.md only.

### `migration` (1)

- `openclaw-migration` — Migrate a user's OpenClaw customization footprint into Hermes Agent. Imports Hermes-compatible memories, SOUL.md, command allowlists, user skills, and selected workspace assets from ~/.openclaw, then reports exactly what could not be migrated and why. Integration surface: ships helper scripts.

### `mlops` (25)

- `chroma` — Open-source embedding database for AI applications. Store embeddings and metadata, perform vector and full-text search, filter by metadata. Simple 4-function API. Scales from notebooks to production clusters. Use for semantic search, RAG applications, or document retrieval. Best for local development and open-source projects. Integration surface: ships reference docs.
- `clip` — OpenAI's model connecting vision and language. Enables zero-shot image classification, image-text matching, and cross-modal retrieval. Trained on 400M image-text pairs. Use for image search, content moderation, or vision-language tasks without fine-tuning. Best for general-purpose image understanding. Integration surface: ships reference docs.
- `distributed-llm-pretraining-torchtitan` — Provides PyTorch-native distributed LLM pretraining using torchtitan with 4D parallelism (FSDP2, TP, PP, CP). Use when pretraining Llama 3.1, DeepSeek V3, or custom models at scale from 8 to 512+ GPUs with Float8, torch.compile, and distributed checkpointing. Integration surface: ships reference docs.
- `faiss` — Facebook's library for efficient similarity search and clustering of dense vectors. Supports billions of vectors, GPU acceleration, and various index types (Flat, IVF, HNSW). Use for fast k-NN search, large-scale vector retrieval, or when you need pure similarity search without metadata. Best for high-performance applications. Integration surface: ships reference docs.
- `guidance` — Control LLM output with regex and grammars, guarantee valid JSON/XML/code generation, enforce structured formats, and build multi-step workflows with Guidance - Microsoft Research's constrained generation framework Integration surface: ships reference docs.
- `hermes-atropos-environments` — Build, test, and debug Hermes Agent RL environments for Atropos training. Covers the HermesAgentBaseEnv interface, reward functions, agent loop integration, evaluation with tools, wandb logging, and the three CLI modes (serve/process/evaluate). Use when creating, reviewing, or fixing RL environments in the hermes-agent repo. Integration surface: ships reference docs.
- `huggingface-accelerate` — Simplest distributed training API. 4 lines to add distributed support to any PyTorch script. Unified API for DeepSpeed/FSDP/Megatron/DDP. Automatic device placement, mixed precision (FP16/BF16/FP8). Interactive config, single launch command. HuggingFace ecosystem standard. Integration surface: ships reference docs.
- `huggingface-tokenizers` — Fast tokenizers optimized for research and production. Rust-based implementation tokenizes 1GB in <20 seconds. Supports BPE, WordPiece, and Unigram algorithms. Train custom vocabularies, track alignments, handle padding/truncation. Integrates seamlessly with transformers. Use when you need high-performance tokenization or custom tokenizer training. Integration surface: ships reference docs.
- `instructor` — Extract structured data from LLM responses with Pydantic validation, retry failed extractions automatically, parse complex JSON with type safety, and stream partial results with Instructor - battle-tested structured output library Integration surface: ships reference docs.
- `lambda-labs-gpu-cloud` — Reserved and on-demand GPU cloud instances for ML training and inference. Use when you need dedicated GPU instances with simple SSH access, persistent filesystems, or high-performance multi-node clusters for large-scale training. Integration surface: ships reference docs.
- `llava` — Large Language and Vision Assistant. Enables visual instruction tuning and image-based conversations. Combines CLIP vision encoder with Vicuna/LLaMA language models. Supports multi-turn image chat, visual question answering, and instruction following. Use for vision-language chatbots or image understanding tasks. Best for conversational image analysis. Integration surface: ships reference docs.
- `modal-serverless-gpu` — Serverless GPU cloud platform for running ML workloads. Use when you need on-demand GPU access without infrastructure management, deploying ML models as APIs, or running batch jobs with automatic scaling. Integration surface: ships reference docs.
- `nemo-curator` — GPU-accelerated data curation for LLM training. Supports text/image/video/audio. Features fuzzy deduplication (16× faster), quality filtering (30+ heuristics), semantic deduplication, PII redaction, NSFW detection. Scales across GPUs with RAPIDS. Use for preparing high-quality training datasets, cleaning web data, or deduplicating large corpora. Integration surface: ships reference docs.
- `optimizing-attention-flash` — Optimizes transformer attention with Flash Attention for 2-4x speedup and 10-20x memory reduction. Use when training/running transformers with long sequences (>512 tokens), encountering GPU memory issues with attention, or need faster inference. Supports PyTorch native SDPA, flash-attn library, H100 FP8, and sliding window attention. Integration surface: ships reference docs.
- `peft-fine-tuning` — Parameter-efficient fine-tuning for LLMs using LoRA, QLoRA, and 25+ methods. Use when fine-tuning large models (7B-70B) with limited GPU memory, when you need to train <1% of parameters with minimal accuracy loss, or for multi-adapter serving. HuggingFace's official library integrated with transformers ecosystem. Integration surface: ships reference docs.
- `pinecone` — Managed vector database for production AI applications. Fully managed, auto-scaling, with hybrid search (dense + sparse), metadata filtering, and namespaces. Low latency (<100ms p95). Use for production RAG, recommendation systems, or semantic search at scale. Best for serverless, managed infrastructure. Integration surface: ships reference docs.
- `pytorch-fsdp` — Expert guidance for Fully Sharded Data Parallel training with PyTorch FSDP - parameter sharding, mixed precision, CPU offloading, FSDP2 Integration surface: ships reference docs.
- `pytorch-lightning` — High-level PyTorch framework with Trainer class, automatic distributed training (DDP/FSDP/DeepSpeed), callbacks system, and minimal boilerplate. Scales from laptop to supercomputer with same code. Use when you want clean training loops with built-in best practices. Integration surface: ships reference docs.
- `qdrant-vector-search` — High-performance vector similarity search engine for RAG and semantic search. Use when building production RAG systems requiring fast nearest neighbor search, hybrid search with filtering, or scalable vector storage with Rust-powered performance. Integration surface: ships reference docs.
- `simpo-training` — Simple Preference Optimization for LLM alignment. Reference-free alternative to DPO with better performance (+6.4 points on AlpacaEval 2.0). No reference model needed, more efficient than DPO. Use for preference alignment when want simpler, faster training than DPO/PPO. Integration surface: ships reference docs.
- `slime-rl-training` — Provides guidance for LLM post-training with RL using slime, a Megatron+SGLang framework. Use when training GLM models, implementing custom data generation workflows, or needing tight Megatron-LM integration for RL scaling. Integration surface: ships reference docs.
- `sparse-autoencoder-training` — Provides guidance for training and analyzing Sparse Autoencoders (SAEs) using SAELens to decompose neural network activations into interpretable features. Use when discovering interpretable features, analyzing superposition, or studying monosemantic representations in language models. Integration surface: ships reference docs.
- `stable-diffusion-image-generation` — State-of-the-art text-to-image generation with Stable Diffusion models via HuggingFace Diffusers. Use when generating images from text prompts, performing image-to-image translation, inpainting, or building custom diffusion pipelines. Integration surface: ships reference docs.
- `tensorrt-llm` — Optimizes LLM inference with NVIDIA TensorRT for maximum throughput and lowest latency. Use for production deployment on NVIDIA GPUs (A100/H100), when you need 10-100x faster inference than PyTorch, or for serving models with quantization (FP8/INT4), in-flight batching, and multi-GPU scaling. Integration surface: ships reference docs.
- `whisper` — OpenAI's general-purpose speech recognition model. Supports 99 languages, transcription, translation to English, and language identification. Six model sizes from tiny (39M params) to large (1550M params). Use for speech-to-text, podcast transcription, or multilingual audio processing. Best for robust, multilingual ASR. Integration surface: ships reference docs.

### `productivity` (4)

- `canvas` — Canvas LMS integration — fetch enrolled courses and assignments using API token authentication. Integration surface: ships helper scripts.
- `memento-flashcards` — Spaced-repetition flashcard system. Create cards from facts or text, chat with flashcards using free-text answers graded by the agent, generate quizzes from YouTube transcripts, review due cards with adaptive scheduling, and export/import decks as CSV. Integration surface: ships helper scripts, uses conditional visibility metadata.
- `siyuan` — SiYuan Note API for searching, reading, creating, and managing blocks and documents in a self-hosted knowledge base via curl. Integration surface: declares required env vars.
- `telephony` — Give Hermes phone capabilities without core tool changes. Provision and persist a Twilio number, send and receive SMS/MMS, make direct calls, and place AI-driven outbound calls through Bland.ai or Vapi. Integration surface: ships helper scripts.

### `research` (8)

- `bioinformatics` — Gateway to 400+ bioinformatics skills from bioSkills and ClawBio. Covers genomics, transcriptomics, single-cell, variant calling, pharmacogenomics, metagenomics, structural biology, and more. Fetches domain-specific reference material on demand. Integration surface: SKILL.md only.
- `domain-intel` — Passive domain reconnaissance using Python stdlib. Subdomain discovery, SSL certificate inspection, WHOIS lookups, DNS records, domain availability checks, and bulk multi-domain analysis. No API keys required. Integration surface: ships helper scripts.
- `drug-discovery` — Pharmaceutical research assistant for drug discovery workflows. Search bioactive compounds on ChEMBL, calculate drug-likeness (Lipinski Ro5, QED, TPSA, synthetic accessibility), look up drug-drug interactions via OpenFDA, interpret ADMET profiles, and assist with lead optimization. Use for medicinal chemistry questions, molecule property analysis, clinical pharmacology, and open-science drug research. Integration surface: ships helper scripts, ships reference docs.
- `duckduckgo-search` — Free web search via DuckDuckGo — text, news, images, videos. No API key needed. Prefer the `ddgs` CLI when installed; use the Python DDGS library only after verifying that `ddgs` is available in the current runtime. Integration surface: ships helper scripts, uses conditional visibility metadata.
- `gitnexus-explorer` — Index a codebase with GitNexus and serve an interactive knowledge graph via web UI + Cloudflare tunnel. Integration surface: ships helper scripts.
- `parallel-cli` — Optional vendor skill for Parallel CLI — agent-native web search, extraction, deep research, enrichment, FindAll, and monitoring. Prefer JSON output and non-interactive flows. Integration surface: SKILL.md only.
- `qmd` — Search personal knowledge bases, notes, docs, and meeting transcripts locally using qmd — a hybrid retrieval engine with BM25, vector search, and LLM reranking. Supports CLI and MCP integration. Integration surface: SKILL.md only.
- `scrapling` — Web scraping with Scrapling - HTTP fetching, stealth browser automation, Cloudflare bypass, and spider crawling via CLI and Python. Integration surface: SKILL.md only.

### `security` (3)

- `1password` — Set up and use 1Password CLI (op). Use when installing the CLI, enabling desktop app integration, signing in, and reading/injecting secrets for commands. Integration surface: ships reference docs.
- `oss-forensics` — Supply chain investigation, evidence recovery, and forensic analysis for GitHub repositories.
Covers deleted commit recovery, force-push detection, IOC extraction, multi-source evidence
collection, hypothesis formation/validation, and structured forensic reporting.
Inspired by RAPTOR's 1800+ line OSS Forensics system. Integration surface: ships helper scripts, ships reference docs, ships templates.
- `sherlock` — OSINT username search across 400+ social networks. Hunt down social media accounts by username. Integration surface: SKILL.md only.
