# RESEARCH: Peer Configuration Systems

Code scan of configuration logic across six peer AI CLI tools, compared with co-cli's current design.

**Systems scanned (ordered by architectural relevance to co-cli):**
1. **Hermes (Local Peer):** Highest relevance. A direct architectural sibling to `co-cli` (Python CLI, local agent loop). Its configuration model (layered YAML, separate `.env` secrets) provides the most direct comparison for immediate improvements.
2. **Claude Code (Anthropic):** High relevance. Represents the industry standard for CLI agent UX. Demonstrates modern best practices for hot-reloading and project trust gating.
3. **OpenCode:** Medium relevance. Shows the limits of strict schema validation (Zod) and environment variable templating, but is structurally different (TypeScript/Effect).
4. **Letta:** Medium relevance. A Python-based agent OS (MemGPT style) that demonstrates a unique approach to configuration by converting hierarchical YAML directly into flat environment variables for consumption.
5. **Codex (OpenAI):** Lower relevance. Highly enterprise-focused (MDM, locked profiles) and Rust-based, offering patterns that `co-cli` does not currently need.

---

## 1. Config File Format

| System | Format | Comments Support |
|--------|--------|-----------------|
| **Codex** | TOML | Yes (native) |
| **Claude Code** | JSON | No (strict JSON) |
| **Letta** | YAML | Yes (native) |
| **OpenCode** | JSONC | Yes (JSON with comments) |
| **Hermes** | YAML | Yes (native) |
| **co-cli** | JSON | No |

**Convergence:** 4/5 support comments. TOML and YAML natively; JSON systems that added comments use JSONC. co-cli's plain JSON is the minority approach.

---

## 2. Config Load Precedence

All peer systems use layered loading. The common pattern is: **defaults → system/managed → user → project → env vars → CLI flags**. Hermes has a unique XOR logic for user vs project config.

| Layer | Codex | Claude Code | Letta | OpenCode | Hermes | co-cli |
|-------|-------|-------------|-------|----------|--------|--------|
| Built-in defaults | Yes | Yes (at point of use) | No (relies on env fallbacks) | Yes (Zod) | Yes (dict) | Yes (Pydantic) |
| System/managed | `/etc/codex/` + MDM | `/etc/claude-code/` + MDM + remote API | No | `/etc/opencode/` + remote | No (`.managed` for updates) | **No** |
| User config | `~/.codex/config.toml` | `~/.claude/settings.json` | `~/.letta/conf.yaml` | `~/.config/opencode/opencode.json` | `~/.hermes/config.yaml` | `~/.co-cli/settings.json` |
| Project config | `.codex/config.toml` (trust-gated) | `.claude/settings.json` | `conf.yaml` | `opencode.json` | `cli-config.yaml` (fallback if no user config) | `.co-cli/settings.json` |
| Env vars | Yes | Yes | Maps YAML to Env Vars | `OPENCODE_*` env vars | `${VAR}` template syntax | `CO_CLI_*` / `CO_*` |
| CLI flags | `--config key=val` | `--settings` flag | No | `OPENCODE_CONFIG_CONTENT` | No | **No** |
| Profiles | Yes (`[profiles.*]`) | No | No | No | No | **No** |

**Key findings:**
- **System/managed config** (enterprise): Codex, Claude Code, OpenCode support it. 3/5 have admin lockdown. co-cli has none.
- **CLI flag overrides**: 2/5 support direct CLI flag → config override. co-cli has none.
- **Profiles**: Only Codex supports named config profiles. Novel but not converged.
- **Project trust gating**: Codex validates trust before applying project config. Hermes only loads project config if user config is absent. co-cli applies project config unconditionally and merges.

---

## 3. Config File Locations

### User Config

| System | Path Pattern | Standard |
|--------|-------------|----------|
| Codex | `~/.codex/config.toml` | Custom |
| Claude Code | `~/.claude/settings.json` | Custom |
| Letta | `~/.letta/conf.yaml` | Custom dotdir |
| OpenCode | Platform-specific (XDG on Linux, `~/Library/Preferences/` on macOS) | XDG + platform |
| Hermes | `~/.hermes/config.yaml` | Custom |
| co-cli | `~/.co-cli/settings.json` | Custom dotdir |

**Convergence:** Split. 3/5 use custom dotdirs (`~/.toolname/`), 1 uses XDG, 1 uses platform-native. co-cli uses a custom dotdir (`~/.co-cli/`) matching the project-level `.co-cli/` convention, aligned with the majority pattern (Codex, Claude Code, Hermes).

### Project Config

| System | Path |
|--------|------|
| Codex | `.codex/config.toml` |
| Claude Code | `.claude/settings.json` |
| Letta | `conf.yaml` (root file) |
| OpenCode | `opencode.json` (root file) or `.opencode/` dir |
| Hermes | `cli-config.yaml` |
| co-cli | `.co-cli/settings.json` |

**Convergence:** 4/5 support project config. The `.<toolname>/settings.json` pattern (dotdir with settings file) is used by Claude Code and co-cli. OpenCode uses both root file and dotdir. Hermes uses a root file `cli-config.yaml`.

---

## 4. Config Schema & Validation

| System | Schema Tech | Validation Strategy | Unknown Fields |
|--------|-------------|-------------------|----------------|
| Codex | `schemars` (Rust) → JSON Schema | Serde deserialization + constraint system | Reject |
| Claude Code | Zod v4 | Runtime Zod parse, non-blocking warnings | Passthrough (preserve) |
| Letta | None (maps to ENV) | Ad-hoc ENV mapping | Ignore |
| OpenCode | Zod | Strict mode (reject unknown), blocking errors | Reject |
| Hermes | None (ad-hoc) | Structural validation warnings (non-blocking) | Ignore |
| co-cli | Pydantic v2 | Pydantic validation, blocking errors | Ignore (`extra="ignore"`) |

**Key findings:**
- **Strict vs. lenient on unknown fields**: Split. Codex and OpenCode reject; Claude Code preserves; Letta, co-cli ignore. Preserving unknown fields is the most forward-compatible approach.
- **Non-blocking vs. blocking validation**: Claude Code and Hermes emit warnings but proceed. OpenCode and co-cli abort on invalid config. The warning approach is more user-friendly for config migration.
- **Constraint/requirements system**: Only Codex has admin-enforced constraints (`requirements.toml`) that restrict which values users can set. This is an enterprise feature absent from all others.

---

## 5. Runtime Config Access

| System | Access Pattern | Caching |
|--------|---------------|---------|
| Codex | Struct fields via `Config` singleton with layer stack | Layer-aware, immutable after load |
| Claude Code | `getInitialSettings()` functional getter | Session cache, invalidated on write |
| Letta | Standard `os.environ` | Native OS env var |
| OpenCode | Effect-based DI (`Config.Service`) | TTL-based instance cache |
| Hermes | Module-level `load_config()` | None (loaded once at startup) |
| co-cli | Module-level `get_settings()` singleton | Loaded once, no invalidation |

**Key findings:**
- **Hot reload**: Claude Code and OpenCode support config changes during a session (file watchers + cache invalidation). co-cli and Hermes load once at startup.
- **Layer introspection**: Codex and Claude Code can report which layer "won" for each setting (origin tracking). Useful for `co status` diagnostics.
- **Typed access**: Codex (Rust struct), Letta (os.environ), co-cli (Pydantic model) all provide type-safe field access. Hermes uses raw dictionary access.

---

## 6. Default Handling

| System | Strategy |
|--------|----------|
| Codex | `Option<T>` with defaults applied during struct conversion |
| Claude Code | Defaults at point of use (late-bound) |
| Letta | Handled downstream where ENV vars are read |
| OpenCode | Zod `.default()` in schema |
| Hermes | Deep copy of `DEFAULT_CONFIG` dict in code |
| co-cli | Pydantic `Field(default=...)` with module-level constants |

**Convergence:** All define defaults close to the schema. co-cli's pattern (named constants + Pydantic Field) is the most explicit and self-documenting approach. Claude Code's late-binding is the most flexible but hardest to audit.

---

## 7. Secret Management

| System | Approach |
|--------|----------|
| Codex | Keyring + file-based credential stores |
| Claude Code | `apiKeyHelper` shell command, AWS/GCP refresh hooks |
| Letta | YAML fields mapped to ENV (mixed with config) |
| OpenCode | Provider config with env var references |
| Hermes | `~/.hermes/.env` file + template replacement |
| co-cli | Plain env vars + settings.json fields |

**Key finding:** Codex separates secrets from config (dedicated secret store). Claude Code delegates to external helpers. Letta and co-cli store API keys as plain config fields — no separation.

---

## 8. Merge Strategies

| System | Dict Merge | Array Merge |
|--------|-----------|-------------|
| Codex | Recursive deep merge | Replace |
| Claude Code | Deep merge | Replace |
| Letta | Recursive deep merge | Replace |
| OpenCode | `mergeDeep()` from remeda | Concatenate |
| Hermes | Recursive deep merge (`_deep_merge`) | Replace |
| co-cli | `_deep_merge_settings()` recursive | Replace |

**Key finding:** OpenCode concatenates arrays, while others replace them. co-cli's array replacement means users must redefine entire lists if they want to append one item.

---

## 9. Enterprise/Admin Controls

| Feature | Codex | Claude Code | Letta | OpenCode | Hermes | co-cli |
|---------|-------|-------------|-------|----------|--------|--------|
| System-managed config | Yes | Yes | No | Yes | Yes (for updates) | No |
| MDM (plist/registry) | Yes | Yes | No | No | No | No |
| Remote managed settings | No | Yes (API) | No | Yes (`.well-known`) | No | No |
| Admin lockdown (immutable) | Yes (`requirements.toml`) | Yes (first-source-wins) | No | Yes (managed > all) | No | No |
| Drop-in config dirs | No | Yes (`.d/`) | No | No | No | No |

**Convergence:** 3/6 have enterprise admin config. Not relevant for co-cli's current scope (personal tool), but worth noting for future extensibility.

---

## 10. Config Migration & Recovery

| System | Migration | Recovery |
|--------|-----------|---------|
| Codex | Legacy config mapping, deprecation warnings | Fingerprint-based conflict detection |
| Claude Code | Schema preprocessors, backward-compat passthrough | Validation errors non-blocking |
| Letta | None | None |
| OpenCode | No explicit migration | Strict schema rejection |
| Hermes | Fallback keys in dict merge | Non-blocking structure warnings |
| co-cli | None | Fatal error on invalid config |

**Key finding:** Most systems lack rigorous config recovery besides dropping to defaults or hard-crashing. co-cli's fatal error on invalid config is the most brittle.

---

## 11. Workspace Trust

| System | Trust Model |
|--------|-------------|
| Codex | Git ownership + directory permissions + markers |
| Claude Code | Separate trust dialog acceptance per project |
| OpenCode | No explicit trust (strict schema provides some safety) |
| Hermes | No explicit trust |
| Letta | **No trust model** — project config loaded unconditionally |
| co-cli | **No trust model** — project config loaded unconditionally |

**Key finding:** 2/5 gate project config behind trust. co-cli loads `.co-cli/settings.json` without validation — a security consideration for shared repos.

---

## 12. Notable Patterns Worth Adopting

### Converged (2+ top systems agree)

1. ~~**Non-blocking validation warnings**~~ — **Rejected.** Fail-fast at bootstrap is the correct co-cli policy. The peer pattern (warn + proceed) solves hot-reload scenarios where users edit config mid-session; co-cli loads once at startup, so a crash with a clear error message is the right UX. Not a gap.

2. ~~**Config origin tracking**~~ — **Rejected (over-design).** co-cli has 3 layers (user file, project file, env vars). If a user is confused about where a value came from, they can read 2 JSON files. Origin tracking solves a problem that doesn't exist at this scale.

3. **Per-field merge strategies** (OpenCode): Arrays are currently replaced wholesale when project config overrides user config. Whether concat or replace is correct depends on the field semantics — needs case-by-case analysis:
   - `shell_safe_commands`: concat likely correct (project extends user defaults)
   - `web_fetch_blocked_domains`: concat likely correct (project adds restrictions)
   - `web_fetch_allowed_domains`: replace may be correct (project narrows scope)
   - `memory_auto_save_tags`: replace is fine (project overrides user preference)

   **Status:** No concrete bug has surfaced. Analyze per-field when a real use case demands it.

4. **Env var template syntax** (Hermes: `${VAR}`, OpenCode: `{env:VAR}`): Reference env vars inside config files. Avoids duplicating secrets across files.

5. ~~**Project config trust gating**~~ — **Rejected.** Trust gating contradicts fail-fast: if project config is untrusted, the right response is to refuse to load, not silently ignore it. co-cli is a personal tool — the scenario of cloning an untrusted repo that ships a malicious `.co-cli/settings.json` is not a current threat model.

### Novel but interesting

6. **Config to ENV flattening** (Letta): Parsing hierarchical YAML and translating it into flat uppercase ENV vars. An interesting bridge between user-friendly structured files and legacy 12-factor ENV dependencies.

7. **Config change detection + hooks** (Claude Code): File watchers trigger hooks on settings change. Enables live config reload without restart.



---

## 13. co-cli Current State vs. Peers — Gap Summary

| Capability | Peer Baseline | co-cli Status | Priority |
|-----------|--------------|---------------|----------|
| Layered load (user + project + env) | Universal | **Done** | - |
| Pydantic schema validation | Best in class | **Done** | - |
| Named default constants | Co-cli is ahead | **Done** | - |
| CLI flag overrides | 2/5 have | Missing | Low (Typer can add) |
| Per-field array merge | 1/5 have | Missing (arrays replace) | Low (no bug yet; analyze per-field when needed) |
| Comments in config | 4/5 support | Missing (plain JSON) | Low |
| Secret separation | 2/5 (keyring) | Missing | Low |
| Env var template syntax | 2/5 converged | Missing | Low |
| Config backup/recovery | 0/5 | Missing | Low |
| Non-blocking validation | 2/5 converged | **Rejected** (fail-fast is correct) | - |
| Config origin tracking | 2/5 converged | **Rejected** (over-design) | - |
| Project trust gating | 2/5 converged | **Rejected** (contradicts fail-fast) | - |
| System/managed config | 3/5 (enterprise) | Not needed | - |
| Hot reload | 2/5 | Not needed | - |
| Profiles | 1/5 (Codex only) | Not needed | - |

---

## 14. ROI-Ranked Adoption Recommendations for co-cli

Based on the gap analysis, here is the prioritized list of features to adopt, ranked by Return on Investment (ROI):

### High/Medium ROI (Worth adopting)

1. **Comments in Config (JSONC or TOML)**
   - **Gap:** `co-cli` uses strict JSON; 4/5 peers support comments.
   - **ROI: High.** Users frequently want to comment out settings or document why a project override exists. Adding a JSON-comment-stripper before Pydantic parsing is low effort for a massive UX win.
2. **Env Var Template Syntax (`${VAR}`)**
   - **Gap:** `co-cli` requires hardcoding values or relying entirely on system env vars; Hermes and OpenCode allow referencing env vars inside the config file.
   - **ROI: Medium.** High value for security and team collaboration. Allows committing a project `.co-cli/settings.json` that references `${TEAM_API_KEY}` without leaking actual secrets.

### Negative/Zero ROI (Do not adopt)

5. **Soft / Non-blocking Validation**
   - **Gap:** Hermes and Claude Code emit warnings for bad config but try to proceed; `co-cli` aborts immediately.
   - **ROI: Negative.** `co-cli`'s fail-fast Pydantic validation is a strength. Silent fallback behavior causes unpredictable runtime states.

---

## 15. Anti-Patterns in co-cli's Current Design

While some of `co-cli`'s design choices are intentional tradeoffs, the following represent recognized configuration anti-patterns or risks compared to peer standards:

### 1. Unconditional Project Config Loading (Security)
- **The Anti-Pattern:** Automatically merging `.co-cli/settings.json` from the current working directory without verifying workspace trust.
- **The Risk:** Enables drive-by attacks. Cloning a malicious repository could silently override `shell_safe_commands` to allow destructive scripts or change the `base_url` to steal API keys.
- **Peer Solution:** Claude Code and Codex require explicit user consent ("Do you trust this workspace?") before parsing local config overrides.

### 2. Mixing Secrets with Standard Config (Security/UX)
- **The Anti-Pattern:** Storing API keys and sensitive tokens in the same `~/.co-cli/settings.json` file as general preferences.
- **The Risk:** Breaks dotfile portability. Users who back up their development configurations via public GitHub dotfile repositories are highly likely to accidentally commit their API keys.
- **Peer Solution (Bindvars/Templating):** Hermes supports `${VAR}` template syntax directly inside its YAML config. This acts as a "bindvar," allowing users to write `api_key: ${MY_CUSTOM_ENV_KEY}` in the config file, which Hermes expands at runtime. This bridges the YAML config with the OS environment gracefully without hardcoding secrets. Letta uses the inverse approach, parsing the YAML and flattening it *out* into OS environment variables.

### 3. Strict JSON for Human-Edited Configs (Maintenance)
- **The Anti-Pattern:** Using standard `JSON` without comment support for user-facing configuration files.
- **The Risk:** Users cannot document *why* a specific setting exists (e.g., dropping context window size, overriding a provider endpoint). This leads to "config rot" where users are afraid to modify or clean up their settings later.
- **Peer Solution:** JSONC, TOML, or YAML formats that natively support `#` or `//` comments.

### 4. Destructive Array Overrides (Usability)
- **The Anti-Pattern:** Array settings in the project config completely overwrite the user's global arrays instead of merging.
- **The Risk:** If a user globally whitelists `"npm run test"` and a specific project config defines `shell_safe_commands: ["pytest"]`, the user's global allowance is silently dropped in that directory.
- **Peer Solution:** OpenCode concatenates arrays; other systems use explicit per-field merge strategies.

