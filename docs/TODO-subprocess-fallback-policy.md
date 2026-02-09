# TODO: Subprocess Fallback Policy

**Origin:** RESEARCH-PYDANTIC-AI-CLI-BEST-PRACTICES.md gap analysis (isolation-first shell policy)

---

## Gap Analysis

### Current behavior

`_create_sandbox()` in `main.py:68-88` implements a silent fallback:

```python
if backend in ("docker", "auto"):
    try:
        docker.from_env().ping()
        return DockerSandbox(...)
    except Exception:
        if backend == "docker":
            raise  # explicit docker — don't hide the error

console.print("[yellow]Docker unavailable — running without sandbox[/yellow]")
return SubprocessBackend()
```

When `sandbox_backend="auto"` (the default) and Docker is unavailable, co-cli falls back to `SubprocessBackend` with a single yellow warning. The approval flow correctly gates all commands through the prompt when `isolation_level == "none"` (main.py:179), so security is maintained — but the user may not realize they're running without isolation until they're deep into a session.

### What's missing

1. **No fail-fast option** — Stricter environments (CI, shared machines) have no way to say "Docker or nothing." The only options are `auto` (silent fallback), `docker` (fails on Docker error), and `subprocess` (explicit no-isolation). There's no `auto` variant that refuses to fall back.

2. **No startup-time policy enforcement** — The fallback decision happens inside `_create_sandbox()` and is invisible to tests or CI scripts. There's no config-driven way to enforce isolation requirements.

3. **Warning is easily missed** — The yellow `[yellow]Docker unavailable — running without sandbox[/yellow]` prints once during startup and scrolls away. Users in long sessions may not remember they're unsandboxed.

### What already works

- `sandbox_backend` config field with `auto | docker | subprocess` options
- `isolation_level` property on both backends (`"full"` vs `"none"`)
- Safe-command auto-approval gated on `isolation_level != "none"` (main.py:179)
- All tools requiring approval prompt regardless when isolation is `none`

---

## Design

### New config field

```python
# co_cli/config.py — Settings
sandbox_fallback: Literal["warn", "error"] = Field(default="warn")
```

- `warn` (default) — current behavior: fall back to `SubprocessBackend`, print warning
- `error` — refuse to start: raise `RuntimeError` with a message explaining how to fix (install Docker, switch to `subprocess` explicitly, or set `sandbox_fallback=warn`)

### Config interaction matrix

| `sandbox_backend` | `sandbox_fallback` | Docker available | Result |
|---|---|---|---|
| `auto` | `warn` | Yes | DockerSandbox |
| `auto` | `warn` | No | SubprocessBackend + warning |
| `auto` | `error` | Yes | DockerSandbox |
| `auto` | `error` | No | **Error: refuse to start** |
| `docker` | (ignored) | Yes | DockerSandbox |
| `docker` | (ignored) | No | Error (existing behavior) |
| `subprocess` | (ignored) | (ignored) | SubprocessBackend |

`sandbox_fallback` only applies when `sandbox_backend="auto"`. When the user explicitly picks `docker` or `subprocess`, the fallback setting is irrelevant.

### Updated `_create_sandbox()`

```python
def _create_sandbox(session_id: str) -> SandboxProtocol:
    backend = settings.sandbox_backend

    if backend in ("docker", "auto"):
        try:
            import docker
            docker.from_env().ping()
            return DockerSandbox(
                image=settings.docker_image,
                container_name=f"co-runner-{session_id[:8]}",
                network_mode=settings.sandbox_network,
                mem_limit=settings.sandbox_mem_limit,
                cpus=settings.sandbox_cpus,
            )
        except Exception as exc:
            if backend == "docker":
                raise
            # backend == "auto" — apply fallback policy
            if settings.sandbox_fallback == "error":
                raise RuntimeError(
                    f"Docker unavailable ({exc}). "
                    "Options: install Docker, set sandbox_backend=subprocess, "
                    "or set sandbox_fallback=warn to allow fallback."
                ) from exc

    console.print("[yellow]Docker unavailable — running without sandbox[/yellow]")
    return SubprocessBackend()
```

### Enhanced startup warning

When falling back with `warn`, make the warning persistent in the banner rather than a one-off print that scrolls away. Add `sandbox_fallback_active: bool` to the status dataclass so `display_welcome_banner` can show a persistent indicator.

```python
# co_cli/status.py — StatusInfo
sandbox_fallback_active: bool = False

# co_cli/banner.py — display_welcome_banner
if info.sandbox_fallback_active:
    console.print("[yellow]  Warning: No sandbox — all shell commands require approval[/yellow]")
```

### Env var

```python
# co_cli/config.py — fill_from_env
"sandbox_fallback": "CO_CLI_SANDBOX_FALLBACK",
```

---

## Implementation Plan

### Items

- [ ] Add `sandbox_fallback: Literal["warn", "error"]` to `Settings` with default `"warn"`
- [ ] Add `CO_CLI_SANDBOX_FALLBACK` to `fill_from_env` map
- [ ] Add `sandbox_fallback` to `settings.defaults.json`
- [ ] Update `_create_sandbox()` in `main.py` to check `sandbox_fallback` on auto-detect failure
- [ ] Add `sandbox_fallback_active` flag to `StatusInfo` in `status.py`
- [ ] Pass fallback state from `_create_sandbox()` to banner display
- [ ] Update `display_welcome_banner` to show persistent warning when fallback is active
- [ ] Add functional tests:
  - `sandbox_fallback=error` + no Docker → `RuntimeError`
  - `sandbox_fallback=warn` + no Docker → `SubprocessBackend` (existing behavior)
  - `sandbox_backend=subprocess` ignores `sandbox_fallback`
- [ ] Update `docs/DESIGN-09-tool-shell.md` to document fallback policy

### File changes

| File | Change |
|---|---|
| `co_cli/config.py` | Add `sandbox_fallback` field + env mapping |
| `co_cli/main.py` | Update `_create_sandbox()` with fallback policy check |
| `co_cli/status.py` | Add `sandbox_fallback_active` to `StatusInfo` |
| `co_cli/banner.py` | Show persistent warning when fallback active |
| `settings.defaults.json` | Add `sandbox_fallback` default |
| `tests/test_shell.py` | Add fallback policy tests |
| `docs/DESIGN-09-tool-shell.md` | Document fallback policy matrix |
