# RESEARCH-user-preferences.md

**Research Objective**: Understand how peer CLI agents handle user preferences to inform co's Phase 2b design.

This document analyzes preference patterns across four peer systems (Codex, Gemini CLI, Claude Code, and Aider) to identify common dimensions, storage strategies, and injection approaches for user preferences in AI coding assistants.

---

## 1. Research Objective

User preferences allow agents to adapt behavior to individual workflows without requiring repeated instructions. This research identifies:

- Common preference dimensions across peer systems
- Storage mechanisms (files, precedence, formats)
- Injection strategies (prompt augmentation vs runtime logic)
- Precedence models (global vs project vs session)

The findings inform co's Phase 2b preference system design.

---

## 2. Peer System Analysis

### 2.1 Codex (`~/workspace_genai/codex`)

**Files analyzed**:
- `/codex-rs/core/src/config/types.rs` — Core config structures
- `/codex-rs/core/src/config/profile.rs` — User profile definitions
- `/codex-rs/protocol/src/config_types.rs` — Config enums (Verbosity, Personality, etc.)
- `/codex-rs/protocol/src/protocol.rs` — Approval policy enums
- `/codex-rs/core/src/exec_policy.rs` — Execution policy manager

**Storage**:
- Format: TOML (`config.toml`)
- Location: `~/.codex/` (user), project-local via profiles
- Structure: Strongly typed Rust structs with Serde deserialization

**Key preference dimensions**:

| Dimension | Type | Values | Purpose |
|-----------|------|--------|---------|
| `approval_policy` | Enum | `untrusted`, `on-failure`, `on-request`, `never` | Controls when to prompt for command approval |
| `sandbox_mode` | Enum | `read-only`, `workspace-write`, `danger-full-access` | Filesystem access level |
| `model_verbosity` | Enum | `low`, `medium`, `high` | Output detail level |
| `personality` | Enum | `none`, `friendly`, `pragmatic` | Agent communication style |
| `model` | String | Model name | Default LLM model |
| `model_provider` | String | Provider key | LLM provider selection |
| `web_search` | Enum | `disabled`, `cached`, `live` | Web search behavior |
| `model_reasoning_effort` | Enum | `low`, `medium`, `high` | Reasoning compute budget |
| `model_reasoning_summary` | Enum | `auto`, `concise`, `detailed`, `none` | Reasoning output format |
| `shell_environment_policy` | Object | Inherit/exclude/set env vars | Shell tool environment control |
| `history.persistence` | Enum | `save-all`, `none` | History logging preference |
| `tui.animations` | Boolean | true/false | UI animation toggle |
| `tui.notifications` | Enum | `auto`, `osc9`, `bel` | Desktop notification method |
| `analytics.enabled` | Boolean | true/false | Telemetry opt-in |

**Approval policy details** (from `protocol.rs`):
```rust
pub enum AskForApproval {
    UnlessTrusted,  // Only known-safe read commands auto-approved
    OnFailure,      // Auto-approve in sandbox, escalate on failure
    OnRequest,      // Model decides when to ask (default)
    Never,          // Never prompt, return failures to model
}
```

**Injection strategy**:
- Runtime logic: Approval policy enforced by `ExecPolicyManager` at command execution time
- Prompt augmentation: Personality and verbosity influence system prompt generation
- Hybrid: Config affects both agent behavior (sandbox, approval) and prompt styling

**Precedence**: Profile-based (`ConfigProfile` struct) — profiles can be switched at runtime, allowing workspace-specific overrides.

---

### 2.2 Gemini CLI (`~/workspace_genai/gemini-cli`)

**Files analyzed**:
- `/packages/cli/src/config/settings.ts` — Settings loader, merge logic
- `/packages/cli/src/config/settingsSchema.ts` — 2325-line schema definition

**Storage**:
- Format: JSON with comments (`settings.json`)
- Locations (precedence order):
  1. Schema defaults (built-in)
  2. System defaults (`/etc/gemini-cli/system-defaults.json`, `/Library/Application Support/GeminiCli/settings.json` on macOS)
  3. User settings (`~/.config/gemini-cli/settings.json`)
  4. Workspace settings (`.gemini/settings.json`)
  5. System overrides (`/etc/gemini-cli/settings.json`)
- Trust model: Workspace settings ignored if directory not trusted

**Key preference dimensions** (from `settingsSchema.ts`):

| Dimension | Type | Default | Purpose |
|-----------|------|---------|---------|
| `tools.approvalMode` | Enum | `default` | `default`, `auto_edit`, `plan` — approval behavior |
| `tools.allowed` | String[] | undefined | Tool name prefixes that bypass confirmation |
| `tools.sandbox` | Boolean/String | undefined | Sandbox path or boolean toggle |
| `tools.shell.enableInteractiveShell` | Boolean | true | Use node-pty for interactive shell |
| `tools.shell.showColor` | Boolean | false | Color in shell output |
| `tools.shell.inactivityTimeout` | Number | 300 | Max seconds without shell output |
| `general.vimMode` | Boolean | false | VI keybindings |
| `general.enableAutoUpdate` | Boolean | true | Auto-update toggle |
| `ui.theme` | String | undefined | Color theme name |
| `ui.autoThemeSwitching` | Boolean | true | Auto light/dark based on terminal |
| `output.format` | Enum | `text` | `text` or `json` output format |
| `context.discoveryMaxDirs` | Number | 200 | Max directories for memory search |
| `context.fileFiltering.respectGitIgnore` | Boolean | true | Honor .gitignore files |
| `context.fileFiltering.enableFuzzySearch` | Boolean | true | Fuzzy file search |
| `security.disableYoloMode` | Boolean | false | Hard-disable YOLO mode |
| `security.enablePermanentToolApproval` | Boolean | false | Allow "approve forever" option |
| `security.folderTrust.enabled` | Boolean | true | Folder trust system toggle |
| `model.aliases` | Object | {} | Custom model name aliases |
| `advanced.excludedEnvVars` | String[] | `['DEBUG', 'DEBUG_MODE']` | Env vars to exclude from .env loading |

**Approval mode details** (lines 1064-1081):
```typescript
approvalMode: {
  type: 'enum',
  default: 'default',
  description: `The default approval mode for tool execution.
    'default' prompts for approval,
    'auto_edit' auto-approves edit tools,
    and 'plan' is read-only mode. 'yolo' is not supported yet.`,
  options: [
    { value: 'default', label: 'Default' },
    { value: 'auto_edit', label: 'Auto Edit' },
    { value: 'plan', label: 'Plan' },
  ],
}
```

**Allowed tools pattern** (lines 1095-1108):
```typescript
allowed: {
  type: 'array',
  description: `Tool names that bypass the confirmation dialog.
    Useful for trusted commands (for example
    ["run_shell_command(git)", "run_shell_command(npm test)"]).
    See shell tool command restrictions for matching details.`,
}
```

**Injection strategy**:
- Runtime logic: Approval mode and allowed tools control tool execution flow
- Prompt augmentation: Theme and output format affect display only
- Merge strategy: Custom deep merge with per-field strategies (`REPLACE`, `CONCAT`, `UNION`, `SHALLOW_MERGE`)

**Precedence**: Five-layer stack with trust-aware workspace isolation. Remote admin settings override all file-based settings.

---

### 2.3 Claude Code (`~/workspace_genai/claude-code`)

**Files analyzed**:
- `/examples/settings/settings-strict.json` — Strict security profile
- `/examples/settings/settings-lax.json` — Permissive profile
- `/examples/settings/settings-bash-sandbox.json` — Sandbox config example

**Storage**:
- Format: JSON
- Location: Project-specific (exact paths not available in provided files)
- Structure: Permission-centric with allow/deny lists

**Key preference dimensions** (from example files):

| Dimension | Type | Example Values | Purpose |
|-----------|------|----------------|---------|
| `permissions.disableBypassPermissionsMode` | String | `disable` | Prevent permission bypass mode |
| `permissions.ask` | String[] | `["Bash"]` | Tools requiring approval |
| `permissions.deny` | String[] | `["WebSearch", "WebFetch"]` | Explicitly blocked tools |
| `allowManagedPermissionRulesOnly` | Boolean | true | Restrict to managed rules |
| `allowManagedHooksOnly` | Boolean | true | Restrict to managed hooks |
| `strictKnownMarketplaces` | String[] | [] | Marketplace restrictions |
| `sandbox.autoAllowBashIfSandboxed` | Boolean | false | Auto-approve bash in sandbox |
| `sandbox.excludedCommands` | String[] | [] | Commands excluded from sandbox |
| `sandbox.network.allowUnixSockets` | String[] | [] | Allowed Unix socket paths |
| `sandbox.network.allowAllUnixSockets` | Boolean | false | Blanket Unix socket access |
| `sandbox.network.allowLocalBinding` | Boolean | false | Allow localhost network binding |
| `sandbox.network.allowedDomains` | String[] | [] | Network domain allowlist |
| `sandbox.network.httpProxyPort` | Number/null | null | HTTP proxy port |
| `sandbox.network.socksProxyPort` | Number/null | null | SOCKS proxy port |
| `sandbox.enableWeakerNestedSandbox` | Boolean | false | Weaker nested sandbox escape prevention |

**Injection strategy**:
- Runtime logic: Permission rules enforced at tool invocation time
- Security-first: All preferences framed as security boundaries
- No prompt augmentation: Settings control execution policy, not agent style

**Precedence**: Not documented in available files (likely workspace > user > system).

**Notable pattern**: CVE-2025-66032 post-mortem led to explicit permission model — every example shows permission boundaries as primary configuration surface.

---

### 2.4 Aider (`~/workspace_genai/aider`)

**Files analyzed**:
- `/aider/args.py` — Complete CLI argument parser (848 lines analyzed)

**Storage**:
- Format: YAML (`.aider.conf.yml`)
- Locations: Git root, cwd, or home directory (search order)
- Also supports: `.env` files for API keys, `.aiderignore` for file exclusions

**Key preference dimensions** (from `args.py`):

| Dimension | Type | Default | Purpose |
|-----------|------|---------|---------|
| `--yes-always` | Boolean | None | Always confirm without prompting |
| `--dark-mode` | Boolean | False | Dark terminal color scheme |
| `--light-mode` | Boolean | False | Light terminal color scheme |
| `--pretty` | Boolean | True | Enable colorized output |
| `--stream` | Boolean | True | Enable streaming responses |
| `--user-input-color` | String | `#00cc00` | User input color |
| `--assistant-output-color` | String | `#0088ff` | Assistant output color |
| `--tool-error-color` | String | `#FF2222` | Tool error color |
| `--tool-warning-color` | String | `#FFA500` | Tool warning color |
| `--code-theme` | String | `default` | Markdown code syntax theme |
| `--show-diffs` | Boolean | False | Show diffs when committing |
| `--vim` | Boolean | False | VI editing mode |
| `--fancy-input` | Boolean | True | History and completion in input |
| `--multiline` | Boolean | False | Multi-line input mode |
| `--notifications` | Boolean | False | Terminal bell on response ready |
| `--notifications-command` | String | None | Custom notification command |
| `--verbose` | Boolean | False | Verbose output |
| `--auto-commits` | Boolean | True | Auto-commit LLM changes |
| `--dirty-commits` | Boolean | True | Allow commits when repo dirty |
| `--auto-lint` | Boolean | True | Auto-lint after changes |
| `--auto-test` | Boolean | False | Auto-test after changes |
| `--suggest-shell-commands` | Boolean | True | Suggest shell commands |
| `--detect-urls` | Boolean | True | Detect and offer to add URLs |
| `--check-update` | Boolean | True | Check for updates on launch |
| `--show-release-notes` | Boolean | None | Show release notes (ask if None) |
| `--git` | Boolean | True | Look for git repo |
| `--gitignore` | Boolean | True | Add .aider* to .gitignore |
| `--edit-format` | Enum | None | LLM edit format (model-dependent) |
| `--architect` | Boolean | False | Use architect edit format |
| `--auto-accept-architect` | Boolean | True | Auto-accept architect changes |
| `--map-tokens` | Number | None | Repo map token budget (0=disable) |
| `--map-refresh` | Enum | `auto` | `auto`, `always`, `files`, `manual` |
| `--chat-language` | String | None | Chat language (uses system if None) |
| `--commit-language` | String | None | Commit message language |
| `--encoding` | String | `utf-8` | File encoding |
| `--line-endings` | Enum | `platform` | `platform`, `lf`, `crlf` |
| `--cache-prompts` | Boolean | False | Enable prompt caching |
| `--restore-chat-history` | Boolean | False | Restore previous chat history |
| `--voice-format` | Enum | `wav` | `wav`, `mp3`, `webm` |
| `--voice-language` | String | `en` | Voice input language (ISO 639-1) |

**Approval pattern**:
- No explicit approval mode toggle
- `--yes-always` disables all confirmation prompts (simple boolean)
- Simplest model: either ask for everything, or approve everything

**Injection strategy**:
- Runtime logic: Colors, formatting, git behavior, vim mode affect terminal UI
- Prompt augmentation: `--chat-language` likely influences system prompt
- File behavior: `.aiderignore`, gitignore respect, encoding affect file operations

**Precedence**: YAML config < env vars < CLI args (via `configargparse` library with `auto_env_var_prefix="AIDER_"`).

**Notable pattern**: Aider has the most granular output styling preferences (8 separate color settings), reflecting focus on terminal UX over security boundaries.

---

## 3. Cross-System Patterns

### 3.1 Common Preference Dimensions

| Dimension | Codex | Gemini CLI | Claude Code | Aider | Notes |
|-----------|-------|------------|-------------|-------|-------|
| **Approval control** | `approval_policy` (4 levels) | `approvalMode` (3 modes) | `permissions.ask/deny` | `--yes-always` | Universal concern; granularity varies |
| **Allowed tool/command lists** | Exec policy prefix rules | `tools.allowed` (prefix match) | `permissions.ask/deny` | None | 3/4 systems support allowlists |
| **Sandbox control** | `sandbox_mode` (3 levels) | `tools.sandbox` (bool/path) | `sandbox.*` (detailed) | None | Security-focused systems only |
| **Output verbosity** | `model_verbosity` | Implicit (no explicit setting) | None | `--verbose`, `--stream`, `--pretty` | Mixed: model param vs UX toggle |
| **Color/theme** | None | `ui.theme`, `ui.autoThemeSwitching` | None | 8 color settings | UX-focused systems only |
| **Vim mode** | None | `general.vimMode` | None | `--vim` | 2/4 systems support |
| **Auto-update** | None | `general.enableAutoUpdate` | None | `--check-update` | Maintenance preference |
| **Git integration** | None | None | None | `--git`, `--gitignore`, `--auto-commits` | Aider-specific (git-centric workflow) |
| **Personality** | `personality` (3 styles) | None | None | None | Codex only |
| **Web search mode** | `web_search` (3 modes) | None | None | None | Codex only |
| **Analytics opt-in** | `analytics.enabled` | None | None | `--analytics` (Aider has flag but not in analyzed section) | Privacy preference |
| **History persistence** | `history.persistence` | None | None | Input/chat history files | Data retention preference |
| **Trust management** | None | `security.folderTrust.enabled` | Implicit in permissions | None | Gemini CLI explicit |

**Convergence points** (2+ systems):
1. **Approval control** — All 4 systems (required for safety)
2. **Allowed tool lists** — 3/4 systems (Codex, Gemini CLI, Claude Code)
3. **Sandbox configuration** — 3/4 systems (Codex, Gemini CLI, Claude Code)
4. **Output styling** — 3/4 systems (Gemini CLI, Aider, plus Codex verbosity)
5. **Vim mode** — 2/4 systems (Gemini CLI, Aider)
6. **Auto-update control** — 2/4 systems (Gemini CLI, Aider)

### 3.2 Storage Mechanisms Comparison

| System | Format | Global Location | Project Location | Trust Model | Precedence |
|--------|--------|-----------------|------------------|-------------|------------|
| Codex | TOML | `~/.codex/config.toml` | Profile-based | Trust level enum | Profile selection |
| Gemini CLI | JSON (with comments) | `~/.config/gemini-cli/settings.json` | `.gemini/settings.json` | Workspace trust check | 5-layer merge: schema defaults → system defaults → user → workspace → system overrides |
| Claude Code | JSON | Unknown | Project-level | Permission-based | Likely workspace > user > system |
| Aider | YAML | `~/.aider.conf.yml` or home dir | `.aider.conf.yml` in git root/cwd | None (all trusted) | Config < env vars < CLI args |

**Key patterns**:
- **JSON dominates** (3/4 systems) — human-editable, well-tooled
- **Hierarchical merge** — All systems support global + project overrides
- **Trust-aware loading** — Gemini CLI and Claude Code isolate untrusted workspace settings
- **XDG compliance** — Gemini CLI follows XDG Base Directory spec (`~/.config/`, not home dir clutter)

### 3.3 Injection Strategies

| System | Prompt Augmentation | Runtime Logic | Hybrid |
|--------|---------------------|---------------|--------|
| Codex | `personality`, `model_verbosity` → system prompt | `approval_policy`, `sandbox_mode` → exec policy | ✓ |
| Gemini CLI | Minimal (theme affects display only) | `approvalMode`, `tools.allowed` → tool execution | Mostly runtime |
| Claude Code | None identified | `permissions.*`, `sandbox.*` → execution guards | Pure runtime |
| Aider | `--chat-language` likely → prompt | Colors, git behavior, vim mode → UX | ✓ |

**Pattern**: Security and execution preferences (approval, sandbox) always use runtime logic. Stylistic preferences (personality, verbosity, colors) may augment prompts or affect display.

### 3.4 Precedence Models

**Global → Project → Session hierarchy**:
- All systems support global (user) and project (workspace) scopes
- Session-level overrides via CLI flags: Aider (explicit), others (not documented in analyzed files)

**Trust boundaries**:
- **Gemini CLI**: Workspace settings ignored if folder untrusted
- **Claude Code**: Permission model as trust proxy (stricter rules for untrusted contexts)
- **Codex**: Explicit `TrustLevel` enum, approval policy varies by trust
- **Aider**: No trust model (assumes all projects trusted)

---

## 4. Recommendations for Co

### 4.1 Core Preference Dimensions (10 MVP + 3 post-MVP)

**MVP (Phase 2b)**:

| Preference | Type | Default | Rationale |
|------------|------|---------|-----------|
| `approval.mode` | Enum | `ask` | `ask`, `auto-safe`, `auto-sandbox`, `yolo` — matches Codex/Gemini CLI patterns |
| `approval.allowed_tools` | String[] | `[]` | Tool name prefix allowlist (Codex/Gemini CLI convergence) |
| `approval.allowed_commands` | String[] | `[]` | Shell command prefix allowlist (Codex exec policy pattern) |
| `sandbox.enabled` | Boolean | `true` | Enable Docker sandbox (co's existing pattern) |
| `sandbox.fallback` | Enum | `ask` | `ask`, `deny`, `allow-unsafe` — subprocess fallback policy |
| `output.style` | Enum | `auto` | `auto`, `plain`, `fancy` — matches Aider's `--pretty` intent |
| `output.streaming` | Boolean | `true` | Enable streaming responses (Aider default) |
| `ui.vim_mode` | Boolean | `false` | VI keybindings (Gemini CLI + Aider) |
| `telemetry.enabled` | Boolean | `true` | Opt-in for usage analytics (Codex pattern) |
| `updates.check_on_start` | Boolean | `true` | Check for updates at startup (Gemini CLI + Aider) |

**Post-MVP (Phase 3+)**:

| Preference | Type | Default | Rationale |
|------------|------|---------|-----------|
| `personality` | Enum | `pragmatic` | `none`, `friendly`, `pragmatic` (Codex pattern) |
| `verbosity` | Enum | `medium` | `low`, `medium`, `high` (Codex pattern) |
| `trust.folder_trust_enabled` | Boolean | `true` | Enable folder trust system (Gemini CLI pattern) |

### 4.2 Storage Strategy

**Format**: JSON with comments (like Gemini CLI)
- Human-editable
- Well-tooled (jsonc parsers widely available)
- Supports inline documentation

**Locations** (XDG-compliant):
- **User**: `~/.config/co-cli/settings.json`
- **Project**: `.co-cli/settings.json` (already used for project config)
- **System**: `/etc/co-cli/settings.json` (enterprise deployments)

**Precedence** (same as current):
```
env vars > project settings > user settings > built-in defaults
```

**Trust model** (Phase 3):
- Workspace settings loaded only if directory in `~/.config/co-cli/trusted-folders.json` OR user confirms trust prompt
- Untrusted workspaces: ignore project settings, apply stricter approval policy

### 4.3 Injection Approach

**Runtime logic** (enforcement):
- `approval.*` → `requires_approval` decorator and chat loop approval flow
- `sandbox.*` → Docker backend selection and fallback policy
- `output.streaming` → pydantic-ai stream mode
- `ui.vim_mode` → prompt-toolkit key bindings
- `telemetry.enabled` → SQLite span export toggle
- `updates.check_on_start` → startup version check

**Prompt augmentation** (stylistic, Phase 3+):
- `personality` → Append to system prompt: "You are a {personality} coding assistant..."
- `verbosity` → Prompt instruction: "Provide {verbosity}-detail explanations..."

**No prompt injection for security settings** — approval, sandbox, and trust preferences must be enforced in code, not via prompts (LLMs can ignore prompt instructions).

### 4.4 Implementation Notes

**Schema validation**:
- Use Pydantic models for settings (like `CoDeps` pattern)
- Zod-style validation errors (Gemini CLI pattern) for user-friendly feedback

**Merge strategy**:
- Simple recursive merge for objects
- Array handling: `approval.allowed_*` uses union (Gemini CLI `UNION` strategy)

**Migration path**:
- Phase 2b: Add `settings.json` support alongside existing env vars
- No breaking changes: env vars continue to work, settings file optional
- Phase 3: Add trust model, personality, verbosity

---

## 5. Open Questions

### 5.1 Gaps in Peer Systems

1. **Language preference**: Only Aider has `--chat-language` and `--commit-language`. Should co support non-English system prompts?

2. **Context window management**: None of the analyzed systems expose token budget preferences. Co has `max_history_pairs` — should this be user-configurable?

3. **Multi-model workflows**: Gemini CLI has `model.aliases` and overrides; Aider has weak-model for summarization. Should co support "fast model for X, powerful model for Y" preferences?

4. **Notification preferences**: Codex has 3 notification methods, Aider has custom notification commands. Is this needed for a CLI tool, or only for TUI modes?

5. **Extension/plugin preferences**: Gemini CLI has extensive extension management. Co has no extension system yet — defer until plugins designed.

### 5.2 Co-Specific Considerations

1. **Obsidian integration**: Should `obsidian.*` settings (vault path, search preferences) live in user settings or remain project-level?

2. **Google tool auth**: Should `google.credentials_path` be a user preference (global auth) or project preference (per-workspace auth)?

3. **Slack tool config**: `slack.token` is sensitive — must stay in env var or secrets manager, not settings file.

4. **MCP server config**: Co will eventually support MCP. Codex and Gemini CLI both have detailed MCP server configs — study these when implementing MCP client.

5. **Approval UI**: Should co display "Approve once / Approve for session / Approve forever" like Gemini CLI's `enablePermanentToolApproval`? This requires persistent approval state storage.

---

## 6. Next Steps

1. **Implement Phase 2b settings** (10 core preferences):
   - Add `co_cli/settings.py`: Pydantic models for settings schema
   - Extend `co_cli/config.py`: Load settings from `~/.config/co-cli/settings.json`
   - Update `CoDeps`: Add `settings: Settings` field
   - Wire approval preferences into existing approval flow

2. **Add settings command**:
   - `co settings list` — show current effective settings
   - `co settings edit` — open settings file in $EDITOR
   - `co settings reset` — delete user settings (fall back to defaults)

3. **Document in DESIGN-14-settings.md**:
   - Follow 4-section template (What & How, Core Logic, Config, Files)
   - Include precedence table
   - Link to this research doc for rationale

4. **Write tests** (`tests/test_settings.py`):
   - Precedence: env var > project > user > default
   - Validation: invalid values produce clear errors
   - Approval integration: `approval.mode` affects tool execution

5. **Update CLAUDE.md**:
   - Add settings management guidance
   - Document preference vs configuration distinction (preferences = user workflow; configuration = system setup)

---

**Document Status**: Complete — ready for Phase 2b implementation (2026-02-09)
