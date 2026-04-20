# RESEARCH: Codex Skills

Scan basis:

- embedded system-skill installer in `codex-rs/skills/src/lib.rs`
- bundled sample skills under `codex-rs/skills/src/assets/samples/*/SKILL.md`

## 1. Source-of-Truth Runtime Model

Codex ships a small embedded set of system skills. The runtime source of truth is `codex-rs/skills/src/lib.rs`, which installs the embedded directory from:

- `codex-rs/skills/src/assets/samples`

That means the built-in implemented sample skills in this checkout are exactly the directories under `assets/samples/` that contain a `SKILL.md`.

## 2. Complete Implemented Skill Inventory

Implemented embedded sample skills found in source: **5**

| Skill | Functionality | Core implementation | Prompt design | Tool integration |
|------|---------------|---------------------|---------------|------------------|
| `imagegen` | Generate or edit bitmap images for assets, mockups, and image variants | Hybrid skill: built-in image tool first, CLI fallback second | Strong routing rules around bitmap vs vector/code-native work; explicit fallback rules | Primary `image_generation`; supporting `view_image`; optional shell/CLI fallback |
| `openai-docs` | Answer OpenAI-product and API questions from current official docs | Connector-first docs skill with bundled helper references | Prioritizes official docs, citations, and latest-model guidance; constrains fallback browsing | OpenAI docs MCP tools first; `web_search` fallback; shell only for MCP install/setup |
| `plugin-creator` | Scaffold Codex plugins and optional marketplace entries | Script-backed scaffold generator | Treats plugin creation as a structured artifact-generation workflow | Shell/script execution plus follow-up file edits |
| `skill-creator` | Create or update Codex skills with the right structure and validation path | Mixed prompt + script workflow | Encodes the design rubric for good skills and how to package references/scripts/assets | Shell scripts for init/validation; agent tools for forward-testing |
| `skill-installer` | List and install Codex skills from curated or GitHub sources | Script-backed installer workflow | Constrains listing/install flow and restart expectations | Shell/network install scripts plus permission/escalation path |

No additional bundled system skills were found under the embedded `assets/samples/` directory in this checkout.

## 3. Structural Read

### A. Codex skills are "instruction bundle + preferred execution path"

The built-in sample skills are not standalone capabilities. Each one combines:

- a dispatch condition
- a workflow contract
- a preferred tool path
- supporting resources or scripts when the workflow is repetitive

### B. Prompt design and tool design are tightly coupled

The useful research unit is not just the prompt body. It is:

- what user problem the skill routes
- what concrete implementation path it prefers
- what safety or scope rules the prompt imposes
- what tools make the skill operational

### C. Implication for `co-cli`

For skill research, Codex suggests documenting every skill with:

1. exact implemented-skill inventory from source
2. functionality
3. core implementation path
4. prompt-design constraints
5. tool integration and fallback path
