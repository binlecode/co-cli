# RESEARCH: Peer Configuration Systems

Code scan of configuration logic across six peer AI CLI tools, compared with co-cli's current design.

**Systems scanned:** Codex (OpenAI), Claude Code (Anthropic), Aider, Goose (Block), OpenCode, Gemini CLI (Google)

---

## 1. Config File Format

| System | Format | Comments Support |
|--------|--------|-----------------|
| **Codex** | TOML | Yes (native) |
| **Claude Code** | JSON | No (strict JSON) |
| **Aider** | YAML | Yes (native) |
| **Goose** | YAML | Yes (native) |
| **OpenCode** | JSONC | Yes (JSON with comments) |
| **Gemini CLI** | JSON | Yes (stripped at parse time) |
| **co-cli** | JSON | No |

**Convergence:** 4/6 support comments. TOML and YAML natively; JSON systems that added comments use JSONC or strip-json-comments. co-cli's plain JSON is the minority approach.

---

## 2. Config Load Precedence

All six systems use layered loading. The universal pattern is: **defaults → system/managed → user → project → env vars → CLI flags**.

| Layer | Codex | Claude Code | Aider | Goose | OpenCode | Gemini CLI | co-cli |
|-------|-------|-------------|-------|-------|----------|------------|--------|
| Built-in defaults | Yes | Yes (at point of use) | Yes (argparse) | Yes (macro) | Yes (Zod) | Yes (schema) | Yes (Pydantic) |
| System/managed | `/etc/codex/` + MDM | `/etc/claude-code/` + MDM + remote API | No | No | `/etc/opencode/` + remote | `/etc/gemini-cli/` | **No** |
| User config | `~/.codex/config.toml` | `~/.claude/settings.json` | `~/.aider.conf.yml` | `~/.config/goose/config.yaml` | `~/.config/opencode/opencode.json` | `~/.gemini/settings.json` | `~/.co-cli/settings.json` |
| Project config | `.codex/config.toml` (trust-gated) | `.claude/settings.json` | `.aider.conf.yml` | No | `opencode.json` | `.gemini/settings.json` (trust-gated) | `.co-cli/settings.json` |
| Env vars | Yes | Yes | `AIDER_*` prefix | Yes (uppercase key) | `OPENCODE_*` env vars | `$VAR` template syntax | `CO_CLI_*` / `CO_*` |
| CLI flags | `--config key=val` | `--settings` flag | argparse flags | No (GUI configure) | `OPENCODE_CONFIG_CONTENT` | yargs flags | **No** |
| Profiles | Yes (`[profiles.*]`) | No | No | No | No | No | **No** |

**Key findings:**
- **System/managed config** (enterprise): Codex, Claude Code, OpenCode, Gemini CLI all support it. 4/6 have admin lockdown. co-cli has none.
- **CLI flag overrides**: 4/6 support direct CLI flag → config override. co-cli has none.
- **Profiles**: Only Codex supports named config profiles. Novel but not converged.
- **Project trust gating**: Codex and Gemini CLI validate trust before applying project config. co-cli applies project config unconditionally.

---

## 3. Config File Locations

### User Config

| System | Path Pattern | Standard |
|--------|-------------|----------|
| Codex | `~/.codex/config.toml` | Custom |
| Claude Code | `~/.claude/settings.json` | Custom |
| Aider | `~/.aider.conf.yml` | Custom (dotfile) |
| Goose | `~/.config/goose/config.yaml` | XDG |
| OpenCode | Platform-specific (XDG on Linux, `~/Library/Preferences/` on macOS) | XDG + platform |
| Gemini CLI | `~/.gemini/settings.json` | Custom |
| co-cli | `~/.co-cli/settings.json` | Custom dotdir |

**Convergence:** Split. 3/6 use custom dotdirs (`~/.toolname/`), 2/6 use XDG, 1 uses platform-native. co-cli uses a custom dotdir (`~/.co-cli/`) matching the project-level `.co-cli/` convention, aligned with the majority pattern (Codex, Claude Code, Gemini CLI).

### Project Config

| System | Path |
|--------|------|
| Codex | `.codex/config.toml` |
| Claude Code | `.claude/settings.json` |
| Aider | `.aider.conf.yml` (root dotfile) |
| Goose | None |
| OpenCode | `opencode.json` (root file) or `.opencode/` dir |
| Gemini CLI | `.gemini/settings.json` |
| co-cli | `.co-cli/settings.json` |

**Convergence:** 5/6 support project config. The `.<toolname>/settings.json` pattern (dotdir with settings file) is used by Claude Code, Gemini CLI, and co-cli. Aider uses a single dotfile. OpenCode uses both root file and dotdir.

---

## 4. Config Schema & Validation

| System | Schema Tech | Validation Strategy | Unknown Fields |
|--------|-------------|-------------------|----------------|
| Codex | `schemars` (Rust) → JSON Schema | Serde deserialization + constraint system | Reject |
| Claude Code | Zod v4 | Runtime Zod parse, non-blocking warnings | Passthrough (preserve) |
| Aider | None (argparse types) | Type coercion at parse time | Ignore |
| Goose | `serde_yaml` (Rust) | Deserialization + manual validation | Ignore |
| OpenCode | Zod | Strict mode (reject unknown), blocking errors | Reject |
| Gemini CLI | TypeScript const + Zod | Runtime Zod parse, non-blocking warnings | Strip |
| co-cli | Pydantic v2 | Pydantic validation, blocking errors | Ignore (`extra="ignore"`) |

**Key findings:**
- **Strict vs. lenient on unknown fields**: Split. Codex and OpenCode reject; Claude Code preserves; Aider, Goose, co-cli ignore. Preserving unknown fields is the most forward-compatible approach.
- **Non-blocking vs. blocking validation**: Claude Code and Gemini CLI emit warnings but proceed. OpenCode and co-cli abort on invalid config. The warning approach is more user-friendly for config migration.
- **Constraint/requirements system**: Only Codex has admin-enforced constraints (`requirements.toml`) that restrict which values users can set. This is an enterprise feature absent from all others.

---

## 5. Runtime Config Access

| System | Access Pattern | Caching |
|--------|---------------|---------|
| Codex | Struct fields via `Config` singleton with layer stack | Layer-aware, immutable after load |
| Claude Code | `getInitialSettings()` functional getter | Session cache, invalidated on write |
| Aider | `args` namespace (argparse result) | None (loaded once) |
| Goose | `Config::global()` static singleton with typed accessors | In-memory cache with mutex |
| OpenCode | Effect-based DI (`Config.Service`) | TTL-based instance cache |
| Gemini CLI | `LoadedSettings` class with `merged` getter | 10-second TTL cache |
| co-cli | Module-level `get_settings()` singleton | Loaded once, no invalidation |

**Key findings:**
- **Hot reload**: Claude Code, OpenCode, and Gemini CLI support config changes during a session (file watchers + cache invalidation). co-cli loads once at startup.
- **Layer introspection**: Codex and Claude Code can report which layer "won" for each setting (origin tracking). Useful for `co status` diagnostics.
- **Typed access**: Codex (Rust struct), Goose (macro-generated accessors), co-cli (Pydantic model) all provide type-safe field access. Aider's argparse namespace is the weakest.

---

## 6. Default Handling

| System | Strategy |
|--------|----------|
| Codex | `Option<T>` with defaults applied during struct conversion |
| Claude Code | Defaults at point of use (late-bound) |
| Aider | argparse `default=` |
| Goose | Macro-generated defaults (`config_value!` with `DEFAULT` const) |
| OpenCode | Zod `.default()` in schema |
| Gemini CLI | Schema `default` field + `getDefaultsFromSchema()` |
| co-cli | Pydantic `Field(default=...)` with module-level constants |

**Convergence:** All define defaults close to the schema. co-cli's pattern (named constants + Pydantic Field) is the most explicit and self-documenting approach. Claude Code's late-binding is the most flexible but hardest to audit.

---

## 7. Secret Management

| System | Approach |
|--------|----------|
| Codex | Keyring + file-based credential stores |
| Claude Code | `apiKeyHelper` shell command, AWS/GCP refresh hooks |
| Aider | `.env` file + CLI flags + `~/.aider/oauth-keys.env` |
| Goose | System keyring (default) + file fallback (`secrets.yaml`) |
| OpenCode | Provider config with env var references |
| Gemini CLI | `.env` files with trust-gated loading |
| co-cli | Plain env vars + settings.json fields |

**Key finding:** Goose and Codex separate secrets from config (dedicated secret store). Claude Code delegates to external helpers. co-cli stores API keys as plain settings fields — no separation.

---

## 8. Merge Strategies

| System | Dict Merge | Array Merge |
|--------|-----------|-------------|
| Codex | Recursive deep merge | Replace |
| Claude Code | Deep merge | Replace |
| Aider | Last value wins (argparse) | Replace |
| Goose | Replace (YAML-level) | Replace |
| OpenCode | `mergeDeep()` from remeda | Concatenate |
| Gemini CLI | Per-field strategy (`REPLACE`, `CONCAT`, `UNION`, `SHALLOW_MERGE`) | Per-field |
| co-cli | `_deep_merge_settings()` recursive | Replace |

**Key finding:** Gemini CLI's per-field merge strategy is the most flexible — different fields can use different strategies. This matters for arrays like `shell_safe_commands` where users want to extend defaults, not replace them.

---

## 9. Enterprise/Admin Controls

| Feature | Codex | Claude Code | OpenCode | Gemini CLI | co-cli |
|---------|-------|-------------|----------|------------|--------|
| System-managed config | Yes | Yes | Yes | Yes | No |
| MDM (plist/registry) | Yes | Yes | No | No | No |
| Remote managed settings | No | Yes (API) | Yes (`.well-known`) | No | No |
| Admin lockdown (immutable) | Yes (`requirements.toml`) | Yes (first-source-wins) | Yes (managed > all) | Yes (system file) | No |
| Drop-in config dirs | No | Yes (`.d/`) | No | No | No |

**Convergence:** 4/6 have enterprise admin config. Not relevant for co-cli's current scope (personal tool), but worth noting for future extensibility.

---

## 10. Config Migration & Recovery

| System | Migration | Recovery |
|--------|-----------|---------|
| Codex | Legacy config mapping, deprecation warnings | Fingerprint-based conflict detection |
| Claude Code | Schema preprocessors, backward-compat passthrough | Validation errors non-blocking |
| Aider | `deprecated.py` with flag mapping | Graceful fallback to defaults |
| Goose | Extension auto-migration on load | Backup rotation (5 copies), auto-restore from backup |
| OpenCode | No explicit migration | Strict schema rejection |
| Gemini CLI | Deprecation migrations in load path | Non-blocking validation warnings |
| co-cli | None | Fatal error on invalid config |

**Key finding:** Goose's automatic backup rotation (write → backup → restore on corrupt) is production-grade resilience absent from all others. co-cli's fatal error on invalid config is the most brittle.

---

## 11. Workspace Trust

| System | Trust Model |
|--------|-------------|
| Codex | Git ownership + directory permissions + markers |
| Claude Code | Separate trust dialog acceptance per project |
| Gemini CLI | `isWorkspaceTrusted()` check, restricts env vars in untrusted |
| OpenCode | No explicit trust (strict schema provides some safety) |
| Aider | No trust model |
| Goose | No project config → no trust needed |
| co-cli | **No trust model** — project config loaded unconditionally |

**Key finding:** 3/6 gate project config behind trust. co-cli loads `.co-cli/settings.json` without validation — a security consideration for shared repos.

---

## 12. Notable Patterns Worth Adopting

### Converged (2+ top systems agree)

1. ~~**Non-blocking validation warnings**~~ — **Rejected.** Fail-fast at bootstrap is the correct co-cli policy. The peer pattern (warn + proceed) solves hot-reload scenarios where users edit config mid-session; co-cli loads once at startup, so a crash with a clear error message is the right UX. Not a gap.

2. ~~**Config origin tracking**~~ — **Rejected (over-design).** co-cli has 3 layers (user file, project file, env vars). If a user is confused about where a value came from, they can read 2 JSON files. Origin tracking solves a problem that doesn't exist at this scale.

3. **Per-field merge strategies** (Gemini CLI, OpenCode): Arrays are currently replaced wholesale when project config overrides user config. Whether concat or replace is correct depends on the field semantics — needs case-by-case analysis:
   - `shell_safe_commands`: concat likely correct (project extends user defaults)
   - `web_fetch_blocked_domains`: concat likely correct (project adds restrictions)
   - `web_fetch_allowed_domains`: replace may be correct (project narrows scope)
   - `memory_auto_save_tags`: replace is fine (project overrides user preference)

   **Status:** No concrete bug has surfaced. Analyze per-field when a real use case demands it.

4. **Env var template syntax** (Gemini CLI: `$VAR`, OpenCode: `{env:VAR}`): Reference env vars inside config files. Avoids duplicating secrets across files.

5. ~~**Project config trust gating**~~ — **Rejected.** Trust gating contradicts fail-fast: if project config is untrusted, the right response is to refuse to load, not silently ignore it. co-cli is a personal tool — the scenario of cloning an untrusted repo that ships a malicious `.co-cli/settings.json` is not a current threat model.

### Novel but interesting

6. **Backup rotation on write** (Goose): `config.yaml` → `.bak` → `.bak.1` ... `.bak.5`. Cheap insurance against corruption.

7. **Config change detection + hooks** (Claude Code): File watchers trigger hooks on settings change. Enables live config reload without restart.

8. **Typed macro-generated accessors** (Goose `config_value!`): Auto-generates getter/setter with default and type. Reduces boilerplate.

---

## 13. co-cli Current State vs. Peers — Gap Summary

| Capability | Peer Baseline | co-cli Status | Priority |
|-----------|--------------|---------------|----------|
| Layered load (user + project + env) | Universal | **Done** | - |
| Pydantic schema validation | Best in class | **Done** | - |
| Named default constants | Co-cli is ahead | **Done** | - |
| CLI flag overrides | 4/6 have | Missing | Low (Typer can add) |
| Per-field array merge | 2/6 converged | Missing (arrays replace) | Low (no bug yet; analyze per-field when needed) |
| Comments in config | 4/6 support | Missing (plain JSON) | Low |
| Secret separation | 2/6 (keyring) | Missing | Low |
| Env var template syntax | 2/6 converged | Missing | Low |
| Config backup/recovery | 1/6 (Goose) | Missing | Low |
| Non-blocking validation | 2/6 converged | **Rejected** (fail-fast is correct) | - |
| Config origin tracking | 2/6 converged | **Rejected** (over-design) | - |
| Project trust gating | 3/6 converged | **Rejected** (contradicts fail-fast) | - |
| System/managed config | 4/6 (enterprise) | Not needed | - |
| Hot reload | 3/6 | Not needed | - |
| Profiles | 1/6 (Codex only) | Not needed | - |
