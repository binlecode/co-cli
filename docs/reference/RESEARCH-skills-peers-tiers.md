# RESEARCH: Skill Surface Tiering Across Peers — hermes · openclaw · codex

Cross-peer synthesis of three general AI-agent CLIs that ship bundled skill
catalogs, plus `RESEARCH-skills-prompt-gaps.md`. Establishes a three-tier
classification of **skill capabilities** (the workflows skills encode, not
individual skill names) based on convergence across peers, with full
per-peer inventories, runtime architecture comparison, co-cli coverage
mapping, and prioritized gap analysis.

**Peers reviewed:** hermes-agent, openclaw, codex.
**Excluded from this survey:**
- **fork-claude-code** — Claude Code is the harness, not a general AI-agent CLI peer; its skill set is harness-specific (keybindings, settings.json, etc.) and not representative of agent-CLI convergence.
- **opencode** — runtime-only skill system (0 production bundled skills, 2 test fixtures); contributes nothing to catalog convergence.

**Reference system:** co-cli (6 bundled skills as of v0.8.x — `doctor`, `plan`,
`refactor`, `review`, `skill-creator`, `triage`; the `.claude/skills/*` files are
Claude Code harness skills bundled in the repo for dev workflow but live outside
co-cli's own skill loader and are out of scope for this comparison).

> **Status refresh (2026-05-27).** This survey was first written when co-cli had
> 1 bundled skill (`doctor`) and no skill lifecycle. co-cli has since shipped
> most of the Part-5 build order. Current state, reflected in the cells below:
> - **6 bundled skills** (`doctor`, `plan`, `refactor`, `review`, `triage`,
>   `skill-creator`) — Step 3 done.
> - **Authoring + patch** via model-callable `skill_manage`
>   (create/edit/patch/delete) — Steps 2 (T1-1, T1-3) done; **install (T1-2)
>   still absent** (no URL/repo install path — `co_cli/skills/usage.py:67`).
> - **Lint** `co_cli/skills/lint.py` (R1–R4 + B1) + `/skills lint` — Step 1 done.
> - **Awareness layer** `render_skill_manifest()` → `<available_skills>` block
>   injected into the static prompt; model loads bodies via `skill_view` (Path 2),
>   not a `skill_run` tool — Step 4 done (different mechanism than the doc proposed).
> - **Self-improvement loop**: dream-daemon skill reviewer
>   (`co_cli/daemons/dream/_reviewer.py`) + merge/decay housekeeping + usage
>   sidecars + `/skills pin` — beyond what any surveyed peer ships.
> - **Migration importer (Step 5)** — NOT built; the plan was withdrawn.
> - **Linked files** — still unsupported (`skill_manage` `write_file` returns error).
> See `docs/specs/skills.md` for the authoritative current surface.

**Sources (code-grounded scan basis for each peer):**
- **hermes**: repo `skills/` + `optional-skills/`; `tools/skills_sync.py`, `tools/skills_hub.py`, `tools/skills_tool.py`, `agent/prompt_builder.py`, `agent/skill_utils.py`, `agent/skill_registry.py`
- **openclaw** (HEAD `bafe49f062`, local pulled fresh from `origin/main`): repo `skills/` + `.agents/skills/`; `src/agents/skills.ts`, `src/agents/skills/{workspace,skill-contract,refresh,source,plugin-skills,frontmatter}.ts`, `src/agents/skills-clawhub.ts`, `extensions/{skill-workshop,migrate-claude,migrate-hermes}/`
- **codex**: `codex-rs/skills/src/lib.rs`, `codex-rs/skills/src/assets/samples/*/SKILL.md`
- **co-cli gap analysis**: `RESEARCH-skills-prompt-gaps.md` (sibling doc — authoring/discovery gaps vs hermes)

**Tiering method:** Each skill *capability* (the workflow domain) is scored by
how many of the three peers ship at least one bundled skill in that domain.
**Tier 1 = 3 peers** (universal), **Tier 2 = 2 peers** (converged),
**Tier 3 = 1 peer** (specialized / differentiated).

---

## Part 1: Per-Peer Skill Inventories

### 1.1 hermes-agent

Source: repo `skills/` + `optional-skills/`; `tools/skills_sync.py`,
`tools/skills_hub.py`, `tools/skills_tool.py`, `agent/prompt_builder.py`,
`agent/skill_utils.py`, `agent/skill_registry.py`.

**Built-in shipped: 70 skills under `skills/` (sync-installed to `~/.hermes/skills/`).**
**Optional shipped: 57 skills under `optional-skills/` (hub-fetch on demand, not auto-installed).**

#### Built-in by category (counts from source)

| Category | Count | Notable skills |
|---|---|---|
| `apple` | 4 | apple-notes, apple-reminders, findmy, imessage |
| `autonomous-ai-agents` | 4 | claude-code, codex, hermes-agent, opencode (delegation skills) |
| `creative` | 10 | architecture-diagram, ascii-art, ascii-video, baoyu-infographic, excalidraw, ideation, manim-video, p5js, popular-web-designs, songwriting-and-ai-music |
| `data-science` | 1 | jupyter-live-kernel |
| `devops` | 1 | webhook-subscriptions |
| `dogfood` | 1 | dogfood (exploratory QA) |
| `email` | 1 | himalaya |
| `gaming` | 2 | minecraft-modpack-server, pokemon-player |
| `github` | 6 | github-auth, github-code-review, github-issues, github-pr-workflow, github-repo-management, codebase-inspection |
| `mcp` | 1 | native-mcp |
| `media` | 4 | gif-search, heartmula, songsee, youtube-content |
| `mlops` | 1 | huggingface-hub |
| `mlops/evaluation` | 2 | evaluating-llms-harness, weights-and-biases |
| `mlops/inference` | 4 | llama-cpp, obliteratus, outlines, serving-llms-vllm |
| `mlops/models` | 2 | audiocraft-audio-generation, segment-anything-model |
| `mlops/research` | 1 | dspy |
| `mlops/training` | 3 | axolotl, fine-tuning-with-trl, unsloth |
| `note-taking` | 1 | obsidian |
| `productivity` | 7 | google-workspace, linear, maps, nano-pdf, notion, ocr-and-documents, powerpoint |
| `red-teaming` | 1 | godmode |
| `research` | 5 | arxiv, blogwatcher, llm-wiki, polymarket, research-paper-writing |
| `smart-home` | 1 | openhue |
| `social-media` | 1 | xurl |
| `software-development` | 6 | plan, requesting-code-review, subagent-driven-development, systematic-debugging, test-driven-development, writing-plans |

#### Optional categories (top of `optional-skills/`)

`autonomous-ai-agents` (2), `blockchain` (2), `communication` (1), `creative` (4),
`devops` (2), `email` (1), `health` (2), `mcp` (2), `migration` (1), `mlops` (25),
`productivity` (4), `research` (8), `security` (3).

#### hermes Skill Runtime

| Aspect | Detail |
|---|---|
| Registration | Manifest-based sync (`tools/skills_sync.py`) — bundled `skills/` → `~/.hermes/skills/`, tracked by `.bundled_manifest`; user-modified copies skipped, user-deleted copies stay deleted |
| Optional source | `OptionalSkillSource` exposes `optional-skills/` for hub `search`/`fetch`/`inspect`; not auto-installed, not prompt-visible |
| External dirs | `agent.skill_utils.get_all_skills_dirs()` merges local + `skills.external_dirs` from config; local wins on collision |
| Plugin skills | Resolved only via qualified `plugin:skill` names |
| Index in prompt | `agent.prompt_builder.build_skills_system_prompt()` injects `<available_skills>` block (cached at `~/.hermes/.skills_prompt_snapshot.json`, rebuilt on manifest change) with mandatory-scan instruction |
| Discovery tool | `skills_list()` — returns `name`, `description`, `category` for installed, enabled, platform-compatible skills |
| Load tool | `skill_view(name)` — loads `SKILL.md` body + linked-file metadata for `references/`, `templates/`, `assets/`, `scripts/`; computes `setup_needed`/`setup_note`; registers env passthrough + credential files |
| Authoring tool | `skill_manage` — model-invokable create/edit/patch/delete/write_file/remove_file |
| Filtering | Platform check + `disabled_skills` config + `metadata.hermes` conditional-visibility frontmatter fields |
| Preload | `--skills` CLI flag uses `build_preloaded_skills_prompt()`; cron jobs prepend bound skills via `cron/scheduler.py`; messaging gateway via `gateway/run.py` |
| Slash invocation | `/skill-name` → `build_skill_invocation_message()` prepends activation note + skill body to user message |

---

### 1.2 openclaw

Source: repo `skills/` + `.agents/skills/`; `src/agents/skills.ts` and
`src/agents/skills/{workspace,skill-contract,refresh,source,plugin-skills,
frontmatter}.ts`; hub at `src/agents/skills-clawhub.ts`; authoring/migration
at `extensions/{skill-workshop,migrate-claude,migrate-hermes}/`.

**User skills: 52 under `skills/` (workspace-synced).**
**Agent routines: 19 under `.agents/skills/` (maintainer/automation skills, separate dispatch surface).**

#### User skills by category (52 total)

| Category | Count | Skills |
|---|---|---|
| Apple ecosystem | 5 | apple-notes, apple-reminders, bear-notes, imsg, things-mac |
| Communication | 4 | discord, slack, voice-call, wacli |
| Productivity / SaaS | 8 | canvas, goplaces, notion, ordercli, taskflow, taskflow-inbox-triage, trello, weather |
| Media (audio/video/image) | 8 | camsnap, gifgrep, openai-whisper, openai-whisper-api, sherpa-onnx-tts, songsee, spotify-player, video-frames |
| GitHub workflow | 2 | gh-issues, github |
| Notes / research | 3 | blogwatcher, obsidian, summarize |
| Smart home | 2 | openhue, sonoscli |
| Diagnostic | 3 | healthcheck, model-usage, session-logs |
| Skill management | 2 | skill-creator, clawhub |
| Peer-CLI delegation | 2 | coding-agent, gemini |
| Dev / system | 10 | tmux, eightctl, oracle, peekaboo, sag, blucli, gog, mcporter, nano-pdf, node-connect |
| Security | 1 | 1password |
| Email | 1 | himalaya |
| Social | 1 | xurl |

#### Agent routines (`.agents/skills/`, 19 total)

| Subcategory | Skills |
|---|---|
| Testing automation | blacksmith-testbox, crabbox, openclaw-parallels-smoke, openclaw-pre-release-plugin-testing, openclaw-qa-testing, openclaw-test-heap-leaks, openclaw-test-performance, openclaw-testing, optimizetests, parallels-discord-roundtrip |
| Maintenance bots | clawsweeper, openclaw-pr-maintainer, openclaw-release-maintainer, openclaw-small-bugfix-sweep, gitcrawl, tag-duplicate-prs-issues |
| Security automation | openclaw-ghsa-maintainer, openclaw-secret-scanning-maintainer, security-triage |
| Communication bot | discord-clawd |

These are openclaw-specific maintainer/automation routines (closer to hermes
cron jobs than user-facing skills).

#### openclaw Skill Runtime

| Aspect | Detail |
|---|---|
| Registration | `loadWorkspaceSkillEntries` reads `skills/` + bundled allowlist + plugin sources; syncs into workspace via `syncSkillsToWorkspace`; manifest-tracked refresh in `refresh.ts` |
| Index in prompt | `formatSkillsForPrompt(skills)` (`src/agents/skills/skill-contract.ts:46`) emits `<available_skills>` XML block — `<skill><name>…</name><description>…</description><location>…</location></skill>` per skill |
| Compact-mode fallback | `formatSkillsCompact()` + `applySkillsPromptLimits()` (`workspace.ts:830+`) — when full format exceeds `maxSkillsPromptChars`, drops descriptions then count-truncates rather than dropping skills silently |
| Model load mechanism | **Generic Read tool against `<location>` path** — no dedicated `skill_view` tool; the index includes `location` so the model can `read` the SKILL.md directly |
| Plugin skills | First-class via `src/agents/skills/plugin-skills.ts` — plugins contribute skills through declared sources |
| Skill hub | `clawhub` skill + `src/agents/skills-clawhub.ts` runtime — search/install from a curated registry |
| Authoring tool | `extensions/skill-workshop/` — separate extension with `tool.ts`, `prompt.ts`, `reviewer.ts` (creator + reviewer pair); plus `skills/skill-creator/` skill (forked from codex's skill-creator per body content) |
| Migration extensions | `extensions/migrate-claude/`, `extensions/migrate-hermes/` — import skills from peer systems |
| Eligibility filter | `agent-filter.ts` — `SkillEligibilityContext` (platform, bins, env, config) gates which skills are visible per-run |
| Env injection | `env-overrides.ts` + `env-overrides.runtime.ts` — apply skill-declared env vars (with snapshot + restore semantics) |
| Workspace target | `syncSkillsToWorkspace` writes filtered/synced skills into the active workspace — model reads from workspace path, not user-global |
| Compaction-aware | `compact-skill-paths.test.ts` + `compact-format.test.ts` — explicit budget tests for prompt compaction paths |
| Routine dispatch | `.agents/skills/` are loaded through a separate routine surface, not the per-turn skill index |

---

### 1.3 codex

Source: `codex-rs/skills/src/lib.rs` (embedded installer) +
`codex-rs/skills/src/assets/samples/*/SKILL.md`.

**Embedded sample skills: 5.**

| Skill | Domain |
|---|---|
| `imagegen` | Bitmap image generation/edit; built-in image tool first, CLI fallback |
| `openai-docs` | OpenAI product/API answers; MCP-connector first, web-search fallback |
| `plugin-creator` | Scaffold Codex plugins (and optional marketplace entries) |
| `skill-creator` | Create or update Codex skills with validation |
| `skill-installer` | List + install Codex skills from curated/GitHub sources |

#### codex Skill Runtime

| Aspect | Detail |
|---|---|
| Registration | Embedded installer in `codex-rs/skills/src/lib.rs`; assets compiled into binary at `assets/samples/` |
| Install target | User skills directory at startup |
| Linked files | Each sample is a SKILL.md + supporting scripts/references in its asset directory |
| Authoring | `skill-creator` skill encodes the design rubric + packaging rules |
| Installer | `skill-installer` is itself a skill; manages curated/GitHub sources with permission/escalation path |

---

### 1.4 co-cli (reference)

Source: `co_cli/skills/`, `co_cli/commands/skills.py`, `co_cli/tools/system/skills.py`, `docs/specs/skills.md`.

**Bundled skills: 6 — `doctor`, `plan`, `refactor`, `review`, `triage`, `skill-creator`.**
User-global directory: `~/.co-cli/skills/*.md` (security-scanned at load).

#### co-cli Skill Runtime

| Aspect | Detail |
|---|---|
| Registration | `load_skills()` per-file at `create_deps()` startup; bundled (`scan=False`) → user-global (`scan=True`) |
| Override | User-global wins on name collision |
| Dispatch model | Slash command (`/skill-name` → `delegated_input`, new turn) OR model inline (`skill_view` loads body inline within the turn) |
| Index in prompt | `render_skill_manifest()` → `<available_skills>` block in the static system prompt; opt-in (not hermes mandatory-scan) |
| Load tool | `skill_view(name)` — full body inline (`spill_threshold=inf`) |
| Authoring tool | `skill_manage` — model-callable create/edit/patch/delete; security-scanned, rollback-on-flag, auto-reload. **No install action** (no URL/repo path) |
| Lint | `co_cli/skills/lint.py` — R1–R4 advisory + B1 (bundled no-marker gate); surfaced via `/skills lint` and on `skill_manage` success |
| Self-improvement | Dream-daemon skill reviewer (`_reviewer.py`) patches/creates from transcripts; merge/decay housekeeping; usage sidecars; `/skills pin` exemption |
| Linked files | Frontmatter only; `write_file`/`remove_file` reserved but return "not yet supported" |
| Distinctive features | Turn-scoped `skill-env` injection with rollback (no peer equivalent); skill body as one-shot `delegated_input`; self-evolution daemon (beyond all surveyed peers) |

---

## Part 2: Cross-Peer Convergence Matrix

Score = number of peers (hermes, openclaw, codex) shipping at least one
bundled skill in this domain.

### 2.1 Engineering Workflow Skills

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Session diagnosis (debug logs / frozen sessions) | `systematic-debugging` | `healthcheck`, `session-logs`, `model-usage` | — | 2 | `doctor` + `triage` ✓ (native) |
| Subagent-driven implementation | `subagent-driven-development` | `coding-agent` (peer-CLI) | — | 2 | harness-only (`.claude/skills/orchestrate-dev`) |
| Code review / critique | `requesting-code-review`, `github-code-review` | (`.agents/skills/openclaw-pr-maintainer` for PR-side automation) | — | 1–2 | `review` ✓ (native bundled) |
| Plan drafting | `plan`, `writing-plans` | — | — | 1 | `plan` ✓ (native bundled) |
| Guided refactor | (—) | — | — | 0 | `refactor` ✓ (native bundled; co-unique) |
| TDD discipline | `test-driven-development` | — | — | 1 | ✗ gap |
| Test automation routines | — | `.agents/`: 10 testing skills | — | 1 (caveat: agent routines, not user skills) | ✗ gap |

### 2.2 Skill Authoring & Maintenance

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Author new skill from session | `skill_manage` tool | `skill-creator` skill + `skill-workshop` extension | `skill-creator` | **3** | ✓ `skill_manage(action='create')` + `skill-creator` bundled skill |
| Install skill from URL/repo | `skills_hub` (`OptionalSkillSource`) | `clawhub` skill + `skills-clawhub.ts` runtime | `skill-installer` | **3** | ✗ gap (no URL/repo install path) |
| Patch / self-improve skill | `skill_manage(action='patch')` | `skill-workshop` reviewer + creator pair | `skill-creator` update path | **3** | ✓ `skill_manage(action='patch')` + dream-daemon skill reviewer |
| Migration from peer system | `openclaw-migration` (opt) | `migrate-claude` + `migrate-hermes` extensions | — | 2 | ✗ gap (importer plan withdrawn) |
| Plugin scaffolding | — | (separate plugin system) | `plugin-creator` | 1 | ✗ gap |

### 2.3 Scheduling & Maintainer Routines

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Cron / scheduled remote agents | `cronjob` tool + `webhook-subscriptions` skill | `.agents/skills/*` (clawsweeper, gitcrawl, release-maintainer routines) | — | 2 | ✗ gap |
| Webhook subscription / event activation | `webhook-subscriptions` | — | — | 1 | ✗ gap |
| PR / release maintainer automation | — | `.agents/`: openclaw-pr-maintainer, openclaw-release-maintainer, clawsweeper, openclaw-small-bugfix-sweep | — | 1 | ✗ gap |

### 2.4 Documentation Grounding

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Vendor-API docs skill | — | — | `openai-docs` | 1 | ✗ gap |
| MCP server build / inspect | `native-mcp`, `fastmcp` (opt), `mcporter` (opt) | `mcporter` | — | 2 | ✗ gap |

### 2.5 Image & Media

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Bitmap image generation | (via `image_generate` tool, not skill) | — | `imagegen` | 1 (skill-form) | ✗ gap |
| ASCII art / video | `ascii-art`, `ascii-video` | — | — | 1 | ✗ gap |
| Diagrams (architecture / SVG) | `architecture-diagram`, `concept-diagrams` (opt), `excalidraw` | — | — | 1 | ✗ gap |
| Animation pipeline | `manim-video`, `p5js` | — | — | 1 | ✗ gap |
| Infographic / web design | `baoyu-infographic`, `popular-web-designs` | — | — | 1 | ✗ gap |
| OCR / PDF / PPTX extraction | `ocr-and-documents`, `nano-pdf`, `powerpoint` | `nano-pdf` | — | 2 | ✗ gap |
| TTS (text-to-speech) | (`text_to_speech` tool, no skill) | `sherpa-onnx-tts`, `voice-call` | — | 1 (skill-form) | ✗ gap |
| Audio transcription (Whisper) | `whisper` (opt) | `openai-whisper`, `openai-whisper-api` | — | 2 | ✗ gap |
| Music / spectrogram | `heartmula`, `audiocraft-audio-generation`, `songsee` | `songsee`, `spotify-player` | — | 2 | ✗ gap |
| GIF search | `gif-search` | `gifgrep` | — | 2 | ✗ gap |
| Camera / video frame capture | — | `camsnap`, `video-frames` | — | 1 | ✗ gap |
| YouTube transcript / restructure | `youtube-content` | — | — | 1 | ✗ gap |

### 2.6 Productivity / SaaS Integrations

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Notion | `notion` | `notion` | — | 2 | ✗ gap |
| Apple ecosystem (notes/reminders/imessage/findmy) | `apple-notes`, `apple-reminders`, `imessage`, `findmy` | `apple-notes`, `apple-reminders`, `imsg`, `bear-notes`, `things-mac` | — | 2 | ✗ gap |
| Obsidian | `obsidian` | `obsidian` | — | 2 | (`obsidian_*` tools cover) |
| Email (himalaya / agentmail) | `himalaya`, `agentmail` (opt) | `himalaya` | — | 2 | ✗ gap |
| Maps / location | `maps` | `goplaces` | — | 2 | ✗ gap |
| Social (X / Twitter, xurl) | `xurl` | `xurl` | — | 2 | ✗ gap |
| Canvas LMS | `canvas` (opt) | `canvas` | — | 2 | ✗ gap |
| Linear / Trello | `linear` | `trello` | — | 2 (distinct tools) | ✗ gap |
| Voice call / phone | `telephony` (opt) | `voice-call` | — | 2 | ✗ gap |
| iMessage / SMS | `imessage` | `imsg` | — | 2 | ✗ gap |
| Google Workspace | `google-workspace` | — | — | 1 | (`drive_*`/`gmail_*`/`calendar_*` tools cover) |
| Weather | — | `weather` | — | 1 | ✗ gap |
| Task management (taskflow / things) | `linear` | `taskflow`, `taskflow-inbox-triage`, `things-mac` | — | 1 (distinct tools) | ✗ gap |

### 2.7 Communication / Messaging

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Discord (skill-form) | (gateway integration, no skill) | `discord` | — | 1 (skill-form) | ✗ gap |
| Slack (skill-form) | (gateway integration, no skill) | `slack` | — | 1 (skill-form) | ✗ gap |
| WhatsApp | — | `wacli` | — | 1 | ✗ gap |

### 2.8 GitHub & Repo Workflow

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| GitHub auth / PR / issues / repo management | `github-auth`, `github-pr-workflow`, `github-issues`, `github-repo-management`, `github-code-review` | `github`, `gh-issues` | — | 2 | ✗ gap |
| Codebase inspection (LOC / language) | `codebase-inspection` | — | — | 1 | ✗ gap |
| Exploratory QA / smoke testing | `dogfood` | `.agents/openclaw-qa-testing`, `openclaw-parallels-smoke` | — | 2 | ✗ gap |
| OSS forensics (deleted commit recovery) | `oss-forensics` (opt) | — | — | 1 | ✗ gap |
| GHSA / security maintainer | — | `.agents/`: openclaw-ghsa-maintainer, openclaw-secret-scanning-maintainer, security-triage | — | 1 (routine) | ✗ gap |

### 2.9 Smart Home

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Philips Hue | `openhue` | `openhue` | — | 2 | ✗ gap |
| Sonos | — | `sonoscli` | — | 1 | ✗ gap |

### 2.10 Peer-CLI Delegation

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Generic coding-agent delegation | (cluster) | `coding-agent` | — | 2 | ✗ gap |
| Delegate to claude-code CLI | `claude-code` | (covered by `coding-agent`) | — | 1 specific | ✗ gap |
| Delegate to codex CLI | `codex` | — | — | 1 | ✗ gap |
| Delegate to opencode CLI | `opencode` | — | — | 1 | ✗ gap |
| Delegate to blackbox CLI | `blackbox` (opt) | — | — | 1 | ✗ gap |
| Delegate to Gemini CLI | — | `gemini` | — | 1 | ✗ gap |

### 2.11 Security

| Capability | hermes | openclaw | codex | Score | co-cli |
|---|---|---|---|---|---|
| Password manager (1Password) | `1password` (opt) | `1password` | — | 2 | ✗ gap |
| Red-teaming / jailbreak | `godmode` | — | — | 1 | ✗ gap |
| OSINT (sherlock) | `sherlock` (opt) | — | — | 1 | ✗ gap |

### 2.12 ML / Research Domain (hermes-only)

hermes ships a large mlops cluster (~50 skills): training, inference,
evaluation, models, vector DBs, GPU clouds, research frameworks, +400-skill
bioinformatics gateway. **Score: 1 (hermes only). co-cli gap: full domain.**
Out of scope unless co-cli pivots to ML research.

### 2.13 Domain-Niche (single-peer)

| Capability | Peer | co-cli |
|---|---|---|
| Gaming (Minecraft, Pokemon) | hermes (2) | ✗ gap |
| Drug discovery / bioinformatics / health | hermes (opt) | ✗ gap |
| Blockchain (Solana / Base) | hermes (opt) | ✗ gap |
| BCI / neuroscience | hermes (opt) | ✗ gap |
| Telephony (Twilio, Bland.ai) | hermes (opt) | ✗ gap |
| Decision framework (1-3-1) | hermes (opt) | ✗ gap |
| Spotify / music player | openclaw `spotify-player` | ✗ gap |
| Tmux session orchestration | openclaw `tmux` | ✗ gap |
| Bluetooth / audio devices | openclaw `blucli` | ✗ gap |

---

## Part 3: Three-Tier Classification

### Tier 1 — Universal Skill Capabilities (3 peers)

Every peer that ships a skill catalog also ships ways to **manage that
catalog**. The entire Tier 1 is the skill lifecycle.

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T1-1 | Skill authoring (create new SKILL.md from session workflow) | hermes `skill_manage`, openclaw `skill-creator`+`skill-workshop`, codex `skill-creator` (3) | ✓ `skill_manage(create)` + `skill-creator` bundled skill |
| T1-2 | Skill installation from external source | hermes `skills_hub`/`OptionalSkillSource`, openclaw `clawhub`+`skills-clawhub.ts`, codex `skill-installer` (3) | ✗ gap — no URL/repo install path |
| T1-3 | Skill patch / self-improvement | hermes `skill_manage(action='patch')`, openclaw `skill-workshop` reviewer/creator pair, codex `skill-creator` update path (3) | ✓ `skill_manage(patch)` + dream-daemon skill reviewer (self-evolving) |

**Reading (refreshed).** The strongest cross-peer signal is that a skill catalog
without a maintenance loop becomes a liability — every catalog-shipping system
shipped the create / install / patch trio. co-cli has since shipped **authoring
(T1-1) and patch (T1-3)** via `skill_manage`, and goes further with an
autonomous dream-daemon skill reviewer that no surveyed peer matches. The one
remaining Tier-1 gap is **install-from-source (T1-2)** — co-cli has no URL/repo
install path; user skills arrive only via hand-authoring or `skill_manage`.

---

### Tier 2 — Converged Skill Capabilities (2 peers)

#### T2-A: Engineering Workflow

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-A1 | Session diagnosis (logs, frozen sessions, debugging) | hermes `systematic-debugging`, openclaw `healthcheck`+`session-logs`+`model-usage` (2) | `doctor` + `triage` ✓ (debugging capability already covered) |
| T2-A2 | Subagent-driven / coding-agent delegation | hermes `subagent-driven-development`, openclaw `coding-agent` (2) | harness-only (`.claude/skills/orchestrate-dev`) |

#### T2-B: Skill Maintenance

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-B1 | Migration from peer system | hermes `openclaw-migration` (opt, one-way), openclaw `migrate-claude`+`migrate-hermes` extensions (2) | ✗ gap |

#### T2-C: Scheduling & Maintainer Routines

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-C1 | Cron / scheduled remote agents | hermes `webhook-subscriptions`+`cronjob`, openclaw `.agents/skills/*` (2) | ✗ gap |

#### T2-D: Documentation Grounding

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-D1 | MCP server skill | hermes `native-mcp`+`fastmcp`+`mcporter`, openclaw `mcporter` (2) | ✗ gap |

#### T2-E: Image / Media

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-E1 | Audio transcription (Whisper) | hermes `whisper` (opt), openclaw `openai-whisper`+`openai-whisper-api` (2) | ✗ gap |
| T2-E2 | Music / spectrogram | hermes `heartmula`+`audiocraft-audio-generation`+`songsee`, openclaw `songsee`+`spotify-player` (2) | ✗ gap |
| T2-E3 | GIF search | hermes `gif-search`, openclaw `gifgrep` (2) | ✗ gap |
| T2-E4 | OCR / PDF | hermes `ocr-and-documents`+`nano-pdf`, openclaw `nano-pdf` (2) | ✗ gap |

#### T2-F: SaaS Productivity (heavy hermes/openclaw overlap)

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-F1 | Notion | hermes + openclaw (2) | ✗ gap |
| T2-F2 | Apple ecosystem (notes / reminders / imessage / findmy) | hermes (4 skills) + openclaw (5 skills) (2) | ✗ gap |
| T2-F3 | Obsidian | hermes + openclaw (2) | (`obsidian_*` tools cover) |
| T2-F4 | Email (himalaya) | hermes + openclaw (2) | ✗ gap |
| T2-F5 | xurl / X / Twitter | hermes + openclaw (2) | ✗ gap |
| T2-F6 | Maps / location | hermes `maps` + openclaw `goplaces` (2) | ✗ gap |
| T2-F7 | Canvas LMS | hermes (opt) + openclaw (2) | ✗ gap |
| T2-F8 | Linear / Trello | hermes `linear` + openclaw `trello` (2; distinct tools) | ✗ gap |
| T2-F9 | iMessage / SMS | hermes `imessage` + openclaw `imsg` (2) | ✗ gap |
| T2-F10 | Voice call / phone | hermes `telephony` (opt) + openclaw `voice-call` (2) | ✗ gap |

#### T2-G: GitHub Workflow

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-G1 | GitHub PR / issues / auth / repo mgmt | hermes (5 skills) + openclaw `github`+`gh-issues` (2) | ✗ gap |
| T2-G2 | Exploratory QA / smoke testing | hermes `dogfood` + openclaw `.agents/openclaw-qa-testing`+`openclaw-parallels-smoke` (2) | ✗ gap |

#### T2-H: Smart Home

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-H1 | Philips Hue | hermes `openhue` + openclaw `openhue` (2) | ✗ gap |

#### T2-I: Security / Auth

| # | Capability | Peers | co-cli status |
|---|---|---|---|
| T2-I1 | 1Password | hermes (opt) + openclaw (2) | ✗ gap |

**co-cli Tier 2 coverage (refreshed): T2-A1 via `doctor`+`triage` ✓; engineering
workflow also covered natively by `plan`/`review`/`refactor`. The ~22 remaining
Tier-2 capabilities are SaaS/media/integration domains — most either already
covered by co-cli *tools* (Obsidian, Google Workspace) or blocked on a missing
tool (OCR/PDF, Whisper, MCP-server skill).**

---

### Tier 3 — Specialized / Single-Peer

#### T3-A: hermes-only

10 creative skills (architecture-diagram, ascii-art/video, baoyu-infographic,
excalidraw, ideation, manim-video, p5js, popular-web-designs, songwriting),
full ML cluster (~25 built-in + 25 optional), gaming (Minecraft, Pokemon),
red-teaming (`godmode`), bioinformatics gateway (opt), OSINT/forensics (opt),
BCI (opt), 1-3-1 decision framework (opt), `youtube-content`, `arxiv`,
`research-paper-writing`, `polymarket`, `google-workspace`,
`heartmula`+`audiocraft-audio-generation`, `findmy`, `plan`+`writing-plans`,
`test-driven-development`, `requesting-code-review`, `webhook-subscriptions`,
`codebase-inspection`, `oss-forensics` (opt), full delegation cluster
(claude-code/codex/opencode/blackbox).

#### T3-B: openclaw-only

| Skill | Notes |
|---|---|
| `coding-agent` | Generic coding-agent delegation umbrella (caller decides target CLI) |
| `gemini` | Delegate to Gemini CLI |
| `tmux` | Tmux session orchestration |
| `sonoscli` | Sonos smart-home control |
| `spotify-player` | Spotify control |
| `weather` | Weather lookup |
| `bear-notes` | Bear notes |
| `things-mac` | Things task manager |
| `taskflow`, `taskflow-inbox-triage` | Task management |
| `camsnap` | Camera capture |
| `video-frames` | Video frame extraction |
| `voice-call` | Voice calling |
| `wacli` | WhatsApp |
| `discord`, `slack` | Skill-form messaging (hermes uses gateway integration instead) |
| `blogwatcher` | RSS/Atom monitoring |
| `summarize` | Summarization workflow |
| `peekaboo`, `eightctl`, `oracle`, `sag`, `blucli`, `gog`, `ordercli`, `node-connect`, `model-usage` | openclaw-domain-specific tools |
| `.agents/skills/` (19 maintainer routines) | clawsweeper, openclaw-pr-maintainer, openclaw-release-maintainer, gitcrawl, openclaw-ghsa-maintainer, openclaw-secret-scanning-maintainer, security-triage, blacksmith-testbox, crabbox, openclaw-test-heap-leaks, openclaw-test-performance, optimizetests, openclaw-pre-release-plugin-testing, openclaw-small-bugfix-sweep, tag-duplicate-prs-issues, parallels-discord-roundtrip, openclaw-testing, discord-clawd |
| `extensions/skill-workshop` | Authoring + reviewer pair (separate from skill-creator) |
| Compact-mode prompt fallback | `formatSkillsCompact()` — drops description before count-truncating; keeps awareness of all skills |

#### T3-C: codex-only

| Skill | Notes |
|---|---|
| `imagegen` | Bitmap image gen with hybrid tool-first/CLI-fallback routing |
| `openai-docs` | OpenAI MCP-connector first, web-search fallback |
| `plugin-creator` | Codex plugin scaffolding |

#### T3-D: co-cli-Unique

| Capability | co-cli surface |
|---|---|
| Turn-scoped `skill-env` injection with rollback | `co_cli/skills/loader.py` + `_SKILL_ENV_BLOCKED` filter — no peer equivalent (closest is openclaw's snapshot-restore env-overrides, but openclaw's is session-scoped, not turn-scoped) |
| Skill body as one-shot `delegated_input` | `commands/_commands.py` dispatch — peer-distinct architecture (see §4) |

---

## Part 4: Skill Runtime Architecture Comparison

| Aspect | hermes | openclaw | codex | co-cli |
|---|---|---|---|---|
| **Catalog model** | File-tree sync (`skills/` → `~/.hermes/skills/`) + optional hub | Workspace sync (`skills/` → workspace dir) + plugin sources + clawhub | Embedded assets installed at startup | Per-file load at `create_deps()` |
| **User catalog directory** | `~/.hermes/skills/` + `external_dirs` config | Active workspace skills dir + plugin contributions | User skills dir under home | `~/.co-cli/skills/*.md` |
| **Override semantics** | Local wins on collision | Workspace-synced; bundled allowlist + plugin merge | Sample reinstall on startup | User-global wins over bundled |
| **Visibility filtering** | Platform check + `disabled_skills` + `metadata.hermes` conditional fields | `SkillEligibilityContext` (platform, bins, env, config) via `agent-filter.ts` | (Sample-set only) | `requires` block: bins, anyBins, env, os, settings |
| **Model discovery surface** | `<available_skills>` system-prompt block (cached snapshot, mandatory-scan) | `<available_skills>` XML block via `formatSkillsForPrompt()`; **compact-mode fallback** drops descriptions before truncating | Skill names visible via skill tool | `<available_skills>` block via `render_skill_manifest()` — opt-in (not mandatory-scan) |
| **Prompt budget handling** | Index cached on disk; rebuilt on manifest change | `applySkillsPromptLimits()` — full → compact → count-truncate | — | Rides the static-prompt cache; no compact-mode fallback yet |
| **Model load mechanism** | `skill_view(name)` tool — body + linked-file metadata | **Generic Read tool against `<location>` path** — no dedicated load tool | Skill name → load | `skill_view(name)` tool — full body inline (`spill_threshold=inf`) |
| **Slash invocation** | `/skill-name` → activation note + body prepended | (via slash + tool) | (via tool) | `/skill-name` → body becomes `delegated_input` |
| **Authoring contract** | Strong: rich frontmatter, standardized sections, code examples | `skill-creator` skill + `skill-workshop` extension (reviewer + creator) — explicit rubric (forked from codex) | `skill-creator` skill encodes rubric | `docs/specs/skills.md` authoring contract + `lint.py` R1–R4/B1 + `skill-creator` bundled skill |
| **Linked-file convention** | `references/`, `templates/`, `assets/`, `scripts/` | Per-skill scripts/refs (e.g. `skill-creator/scripts/*.py`) | Per-skill asset dir | Frontmatter only |
| **Self-improvement loop** | `skill_manage(action='patch')` + prompt invariant | `skill-workshop` reviewer + creator pair | `skill-creator` update path | `skill_manage(patch)` + prompt invariant + **autonomous dream-daemon skill reviewer** (no peer equivalent) |
| **Body-as-prompt vs reference** | Loaded into context as reference; model may revisit | Loaded into context as reference (model reads from `<location>`) | Loaded as reference | One-shot prompt that drives a single turn |
| **Env injection** | Env passthrough names registered for child execution | `env-overrides.ts` + `env-overrides.runtime.ts` — apply skill-declared vars with **snapshot + restore** | None | Turn-scoped `skill-env` with rollback; `_SKILL_ENV_BLOCKED` filter — **finer scope than openclaw's session-level snapshot** |
| **Plugin skills** | Qualified `plugin:skill` namespace | First-class via `plugin-skills.ts` | — | None |
| **Migration extensions** | `openclaw-migration` opt skill (one-way) | `migrate-claude` + `migrate-hermes` extensions (both directions) | — | None |
| **Skill hub / install runtime** | `skills_hub.py` `OptionalSkillSource` | `clawhub` skill + `skills-clawhub.ts` runtime | `skill-installer` | None — no URL/repo install path |
| **Index caching** | On-disk snapshot (`.skills_prompt_snapshot.json`) | `refresh-state.ts` + `snapshot-hydration.ts` | — | Manifest rendered into static prompt; `refresh_skills()` rebuilds on write |
| **Preload mechanisms** | `--skills` CLI flag, cron-job prepend, gateway auto-load | Workspace sync at session start | — | None |
| **Routine vs user skills** | (cron jobs are tool-side) | `.agents/skills/` separate from `skills/` — different dispatch surface | — | — |

### Architectural axis: Prompt overlay vs Loaded reference

| Axis | hermes / openclaw / codex | co-cli |
|---|---|---|
| Body role | Reference loaded into context | One-shot prompt that drives a single turn |
| Discovery driver | Model decides from indexed list | User decides via slash command |
| Multi-turn | Skill body persists; model may revisit | One turn, then skill body is gone |
| Cost of large catalog | +N lines of system prompt + attention contention (openclaw mitigates with compact-mode) | Zero ongoing prompt overhead |
| Discoverability without docs | High — hermes/openclaw index in prompt; codex embeds samples | Low — invisible until correct slash typed |

**openclaw's compact-mode fallback is the most sophisticated answer** to the
catalog-vs-budget tradeoff: it drops descriptions before count-truncating, so
the model retains *awareness of every skill name* even when budget is tight.
Hermes accepts the budget cost; co-cli pays nothing because it has no index.

**co-cli's architecture is leanest** but **least discoverable**. The
prompt-gaps doc recommends an *opt-in / aspirational* awareness layer
(not hermes's mandatory-scan). openclaw's compact-mode pattern is a useful
reference if co-cli ever adds an index — it bounds the prompt cost.

---

## Part 5: co-cli Gap Priority — Build Order

> **STATUS (2026-05-27 refresh).** Steps 1–4 are SHIPPED. Step 5 was withdrawn.
> | Step | Status |
> |---|---|
> | 1 — Lifecycle spec + lint | ✅ shipped (`skills.md` contract, `lint.py` R1–R4/B1) |
> | 2 — Lifecycle trio | ⚠️ partial: create ✅ + patch ✅ (`skill_manage`); **install ❌ still absent** |
> | 3 — Bundled library | ✅ shipped (`doctor`,`plan`,`refactor`,`review`,`triage`,`skill-creator`) |
> | 4 — Awareness layer | ✅ shipped (`render_skill_manifest` / `<available_skills>`, opt-in) |
> | 5 — Migration importer | ❌ withdrawn (over-engineered; loader ignores unknown frontmatter so porting needs no importer) |
> The implementation-plan prose below is preserved as the historical roadmap;
> read it as "what was proposed," not "what's pending." Remaining real gaps:
> **install-from-source (T1-2)** and opportunistic tool-backed Tier-2 skills.

Derived from `RESEARCH-skills-prompt-gaps.md` "Concrete Gaps" § and the
convergence matrix above. Ordering is by **dependency**, not signal strength —
the lifecycle trio is the strongest 3-peer convergence but it sits on top of
the lifecycle spec.

```text
[1] Lifecycle spec  ──►  [2] Lifecycle trio  ──►  [3] Bundled library
   (foundation)            (create/install/patch)     (canonical examples)
                                                              │
                                                              ▼
                                                    [4] Awareness layer
                                                              │
                                                              ▼
                                                    [5] Migration importer
```

Below the line: opportunistic Tier-2 single-domain skills (no fixed order).

---

### Step 1 — Lifecycle spec (Gap 2; foundation)

**Why first.** Every later step encodes or enforces parts of the skill
lifecycle. The lifecycle trio (#2) implements create/install/patch against
this spec; the bundled library (#3) sets precedent for user-installed skills;
the migration importer (#5) normalizes against this spec. Skipping it means
each downstream tool reinvents an implicit, inconsistent contract.

**Scope.** A single spec covering the full lifecycle, not just body shape:

1. **Authoring contract** — body structure (required sections, frontmatter, length budget, style rules).
2. **Install contract** — sources, security scan + lint validation, name-collision behavior, idempotency.
3. **Patch contract** — what counts as a valid edit, what re-validation runs, when reload is automatic vs explicit.
4. **Lint rules** — the canonical list the validator enforces (each rule numbered for citation in error output).
5. **Lifecycle states** — bundled vs user-installed vs imported; which gates apply to each.

**Gap detail.**
- `co_cli/skills/doctor.md` is 23 lines with ad-hoc structure (preamble + "respond with this exact structure" block) — no template, no required sections.
- `docs/specs/skills.md` documents frontmatter parsing and load semantics but says nothing about *body* shape, length, style, or the create/install/patch contracts.
- `/skills install <url>` runs a security scan but no structural-quality scan (per spec §112-127).
- Peer baselines are explicit: openclaw `skills/skill-creator/SKILL.md` ships a "Concise is Key" rubric with token-budget reasoning; hermes's `subagent-driven-development/SKILL.md` follows a strict `## Overview` / `## When to Use` / `## The Process` / numbered-steps / inline-code-examples shape; codex's `skill-creator` packages a design rubric in its asset dir.

**Implementation plan.**
- New spec file: `docs/specs/skill-lifecycle.md`. Sections in order:
  - **Authoring contract**: required body sections (`## When to use`, `## Steps` or `## The Process`, `## Output contract`); required frontmatter (`description`, `argument-hint`, `user-invocable`, `disable-model-invocation`); recommended (`requires.bins`, `requires.env`, `version`); length budget (≤150 lines for bundled, soft-warn at 250 for user-installed); style rules (imperative mood, concrete tool names in code fences, no narration of what code "tries" to do).
  - **Install contract**: source taxonomy (URL / local path / hub), security-scan rules, lint gate, name-collision resolution, post-install reload trigger.
  - **Patch contract**: edit scope (frontmatter vs body vs sections), required re-validation, reload semantics, who can patch bundled vs user-installed.
  - **Lint rules**: numbered rule list (R1: required frontmatter present; R2: required sections present; R3: length within budget; R4: no banned patterns like wildcard imports in code fences; …).
  - **Lifecycle states**: bundled (version-controlled, no scan) → user-installed (security scan + lint at load) → imported (Step 5; lint at write time).
- Validator: `co_cli/skills/_lint.py` — parse frontmatter via existing `parse_frontmatter()` (`co_cli/memory/frontmatter.py`), check required sections via heading regex, enforce length budget, emit findings keyed by rule number. Surface via `/skills lint` slash command and as part of `_load_skill_file(scan=True)` for user-installed skills.
- Migrate `co_cli/skills/doctor.md` to conform — proves the spec on the only existing example.
- Update `docs/specs/skills.md` to delegate body/lifecycle rules to `skill-lifecycle.md` (keep load-order, frontmatter parsing, and skill-env injection in the existing spec; cross-reference).

**Effort.** 1–2 days. Spec writing is most of the work; the lint script is shallow.

---

### Step 2 — Lifecycle trio (T1-1 / T1-2 / T1-3; 3-peer universal)

**Why second.** Strongest convergence signal in the survey — every catalog-shipping
peer ships create + install + patch. Blocked on Step 1 because each tool needs
its lifecycle contract (authoring / install / patch) to enforce.

**Gap detail per leg.**

**T1-1 Skill authoring** — no model-callable path to create a new skill from
session workflow. Peer baselines: hermes `skill_manage(action=create)`, openclaw
`skills/skill-creator/SKILL.md` + `extensions/skill-workshop/src/{tool,prompt,reviewer}.ts`,
codex `assets/samples/skill-creator/SKILL.md`. All three exist as discoverable
skills *and* one of (tool / extension) for actual file write.

**T1-2 Skill installation** — partial coverage today. `/skills install <url>` is
a CLI slash command (per `docs/specs/skills.md:190-200`) but not exposed as a
skill-form workflow that the model can invoke. Peer baselines: hermes
`tools/skills_hub.py` (`OptionalSkillSource` exposes search/fetch/inspect),
openclaw `skills/clawhub/SKILL.md` + `src/agents/skills-clawhub.ts` runtime,
codex `assets/samples/skill-installer/SKILL.md`.

**T1-3 Skill patch** — no model-callable surgical-edit path. Peer baselines:
hermes `skill_manage(action='patch')` paired with the prompt invariant *"when
using a skill and finding it outdated, incomplete, or wrong, patch it
immediately"* (`agent/prompt_builder.py:164-171`); openclaw
`extensions/skill-workshop/src/reviewer.ts` reviewer + creator pair; codex
`skill-creator` update path.

**Implementation plan.**
- T1-1 — bundled skill `co_cli/skills/skill-creator.md` that walks the user through domain → template → validate-via-lint → save. Body steers toward existing tools (`file_write`, `file_read`); no new tool needed because the user is in the loop.
- T1-2 — bundled skill `co_cli/skills/skill-installer.md` whose body invokes the existing `/skills install <url>` CLI under the hood (model produces the slash invocation; user confirms). For full model-invocable form, add a thin `skill_install(url)` tool in `co_cli/tools/skills/` that wraps the existing installer in `co_cli/skills/installer.py` and runs the new lint validator before writing.
- T1-3 — new tool `co_cli/tools/skills/skill_patch.py`: takes `name`, `old_string`, `new_string`, `reason` — locates the skill file via `deps.skill_registry` registry, applies patch via existing `file_patch` mechanics (`co_cli/tools/file/file_patch.py`), re-runs lint, reloads via `co_cli/skills/lifecycle.py`. Deferred-approval (writes to bundled skills require explicit confirmation; user-installed are session-approvable). Pair with prompt rule in `co_cli/prompts/rules/04_tool_protocol.md`: *"when invoking a skill and finding its steps outdated, note the drift and propose a patch."*

**Effort.** 3–5 days total. T1-1 and T1-2 skill bodies: ~half a day each. T1-3 tool + lint integration: 2–3 days. Skill-form `skill_install` upgrade adds ~1 day.

---

### Step 3 — Bundled library fill-in (Gap 4)

**Why third.** Blocked on Step 1 because bundled skills set the de-facto
example for every user-installed one. Best authored after #2 so each new
bundled skill can be created with the `skill-creator` skill (dogfooding).

**Gap detail.** `co_cli/skills/` has exactly one file (`doctor.md`). Peers ship
hermes 70, openclaw 52, codex 5. The harness `.claude/skills/*` (`deliver`,
`orchestrate-dev`, `orchestrate-plan`, `review-impl`, `ship`, `sync-doc`,
`test-hygiene`) are Claude Code harness skills, not co-cli loader skills —
they don't help when co-cli runs outside the Claude Code harness.

**Implementation plan.** Port 4–5 high-value bundled skills as
co-cli-native bodies (not harness-coupled). Each conforms to Step 1's
authoring contract. Candidate set, all from T2-A or harness-equivalent:

| Skill | Source signal | What it encodes |
|---|---|---|
| `co_cli/skills/review.md` | T2-A3 (hermes `requesting-code-review`); harness `review-impl` | Structured self-review of pending changes — diff scan, behavior verification, test gate |
| `co_cli/skills/plan.md` | T3-A hermes `plan`+`writing-plans`; harness `orchestrate-plan` | Plan drafting — bite-sized tasks, file paths, code examples |
| `co_cli/skills/triage.md` | T2-A1 hermes `systematic-debugging`; doctor's neighbor | Diagnose failing test/error — 4-phase root-cause workflow |
| `co_cli/skills/refactor.md` | (no direct peer; high local value) | Apply a named refactor pattern with safety gates |
| (existing) `doctor.md` | T2-A1 already shipped | (migrate to contract in Step 1) |

Skip skills that depend on tools co-cli doesn't have (image gen, browser,
audio). Each new skill: ~1 day to draft, lint-pass, and validate end-to-end.

**Effort.** 4–6 days for 4 new skills.

---

### Step 4 — Opt-in awareness layer (Gap 1)

**Why fourth.** Surfaces the catalog to the model. Blocked on #3 because
without 4–5 bundled skills there's nothing worth surfacing. Blocked on #1 +
#2 because surfaced skills must be lint-passing and have a model-invokable
dispatch path.

**Gap detail.**
- `co_cli/prompts/_assembly.py:87-160` builds the static system prompt with no skill index.
- `co_cli/agent/_instructions.py` has no `add_skill_awareness_prompt` equivalent (compare `add_category_awareness_prompt` at lines 21-24 — same shape, different domain).
- `deps.skill_registry` and `get_skill_registry()` exist but the agent never sees them through any prompt channel.
- Peer baselines: hermes `<available_skills>` block via `agent/prompt_builder.build_skills_system_prompt()` (mandatory-scan); openclaw `formatSkillsForPrompt()` at `src/agents/skills/skill-contract.ts:46` with **compact-mode fallback** at `formatSkillsCompact()` (drops descriptions before count-truncating). Codex relies on skill-tool listing.
- co-cli explicitly should not adopt hermes's mandatory-scan framing — the prompt-gaps doc is direct: *"do not adopt hermes's mandatory-scan skill index. The 'you MUST load any even partially relevant skill' framing is too aggressive and would nullify co-cli's prompt-mass advantage."*

**Implementation plan.**
- New runtime instructions callback in `co_cli/agent/_instructions.py` (`add_skill_awareness_prompt`). Iterates `deps.skill_registry`, filters `disable_model_invocation=True`, emits compact `name — description` lines under an aspirational header: *"The following skills are available — invoke them with `skill_run(name)` when directly relevant."* Not mandatory-scan.
- New tool `co_cli/tools/skills/skill_run.py`: takes `name` and optional `args`, dispatches via existing `commands/_commands.py:dispatch()` path (the same one slash commands use). Returns a sentinel that triggers `delegated_input` in `main.py` — preserves the one-shot-prompt architecture instead of loading skill body into context.
- **Compact-mode fallback (port openclaw's pattern):** in the awareness callback, measure rendered length. If above budget (configurable, default ~2KB), drop the description column and emit `name` + one-line summary only. If still above budget, count-truncate with a footer noting the count. Code lives in `co_cli/skills/_index_format.py`; tests mirror openclaw's `compact-format.test.ts`.
- Cache the rendered index; invalidate on `lifecycle.refresh()`.

**Effort.** 3–5 days. Awareness callback is ~½ day; `skill_run` tool is 1 day; compact-mode + tests is the bulk.

---

### Step 5 — Migration importer (T2-B1)

**Why fifth.** Onboarding nicety, not load-bearing. Can come anytime after
Step 1 (the importer needs the authoring contract to normalize against), but
most useful after Step 4 so imported skills appear in the awareness layer.

**Gap detail.** No path to import skills from peer CLIs. Peer baselines:
hermes `optional-skills/migration/openclaw-migration` (one-way, openclaw →
hermes); openclaw `extensions/migrate-claude/skills.ts` and
`extensions/migrate-hermes/skills.ts` (both directions). Users coming from
those CLIs face an empty `co-cli/skills/` directory.

**Implementation plan.**
- New CLI: `/skills import <source>` where `source ∈ {claude, hermes, openclaw}`. Optional `<name>` to import a single skill by name; default imports all.
- Source roots:
  - claude: `~/.claude/skills/*` (and `.claude/skills/*` in cwd)
  - hermes: `~/.hermes/skills/*`
  - openclaw: workspace skills dir (resolved from openclaw config) + `~/.openclaw/skills/*` if present
- Per skill: parse SKILL.md frontmatter, normalize to co-cli's frontmatter (rename fields per a static map), run Step 1's lint, write to `~/.co-cli/skills/<name>.md` (security-scanned same as `/skills install`). Skip on lint failure with a structured warning, not a hard fail. Idempotent — name collisions prompt for overwrite.
- Read-only on the source side. No write-back.
- Implementation: `co_cli/skills/_migration.py` per-source adapter; `co_cli/commands/_skill_migration.py` CLI dispatcher. Reuse existing `_load_skill_file()` for re-validation of the written copy.

**Effort.** 2–3 days for all three source adapters. Each adapter is shallow once the frontmatter map is defined.

---

### Below the line — opportunistic Tier-2 (no fixed order)

Single-domain Tier-2 skills with 2-peer convergence. Port when the underlying
tool/integration exists or when a specific user need surfaces. None gate the
five steps above.

| Capability | Convergence | Notes |
|---|---|---|
| MCP server build / inspect skill (T2-D1) | hermes + openclaw `mcporter` | Small — bundled body steering toward existing MCP tool surface |
| Audio transcription / Whisper (T2-E1) | hermes `whisper` (opt) + openclaw `openai-whisper` | Needs audio-transcription tool first |
| OCR / PDF (T2-E4) | hermes `ocr-and-documents`+`nano-pdf` + openclaw `nano-pdf` | Small if a PDF-extract tool exists |
| GIF search (T2-E3) | hermes `gif-search` + openclaw `gifgrep` | Niche; depends on Tenor or similar API |
| Cron / scheduled-remote-agent (T2-C1) | hermes + openclaw `.agents/skills/*` | Blocked on tool-level scheduling (separate gap, see `RESEARCH-tools-peers-tiers.md` T2-D) |
| SaaS productivity cluster (T2-F1–F10) | hermes + openclaw — 10 capabilities | Notion, Apple ecosystem, Linear/Trello, Canvas, email, maps, xurl, voice-call, iMessage. Small per skill, large in aggregate |
| GitHub workflow skill bundle (T2-G1) | hermes (5 skills) + openclaw `github`+`gh-issues` | `gh` CLI covers most paths today |
| Exploratory QA (T2-G2) | hermes `dogfood` + openclaw `.agents/openclaw-qa-testing` | Useful pre-ship; small body skill |
| Smart home Hue (T2-H1) | hermes + openclaw `openhue` | Domain-bound |
| 1Password (T2-I1) | hermes (opt) + openclaw | Domain-bound |

### Out of Scope

| Capability | Reason |
|---|---|
| hermes ML cluster (~50 skills) | co-cli is not an ML research agent |
| hermes domain niches (gaming, smart home, blockchain, BCI, drug discovery, telephony) | Not in scope |
| openclaw maintainer routines (`.agents/skills/*`) | openclaw-product-specific (its own PR maintainer, secret scanner, release bot, etc.) |
| Mandatory-scan skill index (hermes-style) | Use openclaw's compact mode + opt-in framing instead (see Step 4) |
| openclaw plugin-skills system | Larger architectural change; co-cli has no plugin surface |
| Codex `plugin-creator` | co-cli has no plugin system |

---

## Appendix: Skill Name Mapping Across Peers

Quick lookup — capability → bundled skill name per peer.

| Capability | co-cli | hermes | openclaw | codex |
|---|---|---|---|---|
| Skill authoring | — | (`skill_manage`) | `skill-creator` + `skill-workshop` | `skill-creator` |
| Skill install | (CLI) | (hub source) | `clawhub` | `skill-installer` |
| Skill patch | — | (`skill_manage(action=patch)`) | `skill-workshop` reviewer | `skill-creator` update |
| Migration from peer | — | `openclaw-migration` (opt) | `migrate-claude` / `migrate-hermes` | — |
| Plugin scaffold | — | — | (plugin system) | `plugin-creator` |
| Session diagnosis | `doctor` | `systematic-debugging` | `healthcheck` / `session-logs` / `model-usage` | — |
| Subagent-driven impl | (harness) | `subagent-driven-development` | `coding-agent` | — |
| Code review | (harness) | `requesting-code-review` / `github-code-review` | (`.agents/openclaw-pr-maintainer`) | — |
| Plan drafting | (harness) | `plan` / `writing-plans` | — | — |
| TDD | — | `test-driven-development` | — | — |
| Cron / remote schedule | — | `webhook-subscriptions` (+`cronjob` tool) | `.agents/skills/*` (clawsweeper, gitcrawl, …) | — |
| Vendor-SDK docs | — | — | — | `openai-docs` |
| MCP server | — | `native-mcp` / `fastmcp` (opt) / `mcporter` (opt) | `mcporter` | — |
| Bitmap image gen | — | (`image_generate` tool) | — | `imagegen` |
| ASCII / diagrams | — | `ascii-art` / `architecture-diagram` / `excalidraw` / `concept-diagrams` (opt) | — | — |
| Audio transcription | — | `whisper` (opt) | `openai-whisper` / `openai-whisper-api` | — |
| TTS | — | (`text_to_speech` tool) | `sherpa-onnx-tts` / `voice-call` | — |
| Music / spectrogram | — | `heartmula` / `audiocraft-audio-generation` / `songsee` | `songsee` / `spotify-player` | — |
| GIF search | — | `gif-search` | `gifgrep` | — |
| OCR / PDF | — | `ocr-and-documents` / `nano-pdf` | `nano-pdf` | — |
| GitHub workflow | — | `github-pr-workflow` / `github-issues` / `github-auth` / `github-repo-management` / `github-code-review` / `codebase-inspection` | `github` / `gh-issues` | — |
| Exploratory QA | — | `dogfood` | `.agents/openclaw-qa-testing` / `openclaw-parallels-smoke` | — |
| Apple ecosystem | — | `apple-notes` / `apple-reminders` / `imessage` / `findmy` | `apple-notes` / `apple-reminders` / `imsg` / `bear-notes` / `things-mac` | — |
| Notion | — | `notion` | `notion` | — |
| Linear / Trello | — | `linear` | `trello` | — |
| Canvas LMS | — | `canvas` (opt) | `canvas` | — |
| Email | — | `himalaya` / `agentmail` (opt) | `himalaya` | — |
| Maps | — | `maps` | `goplaces` | — |
| Obsidian | (`obsidian_*` tools) | `obsidian` | `obsidian` | — |
| Google Workspace | (`drive_*`/`gmail_*`/`calendar_*` tools) | `google-workspace` | — | — |
| xurl / X | — | `xurl` | `xurl` | — |
| Smart home (Hue) | — | `openhue` | `openhue` | — |
| Sonos | — | — | `sonoscli` | — |
| Spotify | — | — | `spotify-player` | — |
| Discord / Slack (skill-form) | — | (gateway integration) | `discord` / `slack` | — |
| Voice call / phone | — | `telephony` (opt) | `voice-call` | — |
| iMessage / SMS | — | `imessage` | `imsg` | — |
| Delegate to peer CLI | — | `claude-code` / `codex` / `opencode` / `blackbox` (opt) | `coding-agent` / `gemini` | — |
| Tmux | — | — | `tmux` | — |
| 1Password | — | `1password` (opt) | `1password` | — |
