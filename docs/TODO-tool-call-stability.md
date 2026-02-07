# TODO: Tool-Call Stability — Remaining Items

Core stability gaps (retry budget, error paths, display consistency, loop guard) are resolved.
Design principles merged into respective DESIGN docs — see:
- `DESIGN-co-cli.md` §5.1 (ModelRetry principle, tool retry budget, loop guard)
- `DESIGN-tool-shell-sandbox.md` (shell error → ModelRetry)
- `DESIGN-tool-obsidian.md` (display field consistency, empty result convention)

---

## Sandbox Hardening

**Impact:** Medium
**Files:** `co_cli/sandbox.py`
**Related:** `docs/DESIGN-tool-shell-sandbox.md` (Future Enhancements section)

### Problem

The Docker sandbox provides basic filesystem isolation (only CWD is mounted) but lacks production-grade hardening. The container runs as root with full network access, no resource limits, and no command timeout. For an agentic assistant where the LLM chooses commands, these defaults are too permissive.

### Current Container Configuration (`sandbox.py`)

```python
self.client.containers.run(
    self.image,
    name=self.container_name,
    volumes={self.workspace_dir: {"bind": "/workspace", "mode": "rw"}},
    working_dir="/workspace",
    detach=True,
    tty=True,
    command="sh"
)
```

### Gap Analysis vs 2026 Agentic Sandbox Norms

| Dimension | Current | Target | Industry Reference |
|-----------|---------|--------|-------------------|
| **User** | Root (container default) | Non-root (`--user 1000:1000`) | E2B, Devin — all run as non-root |
| **Network** | Full access | `network_mode="none"` by default | E2B — no network; Devin — isolated VPC |
| **Resource limits** | None | `mem_limit="512m"`, `cpu_quota=50000` | All cloud sandboxes set limits |
| **Command timeout** | ~~None~~ Implemented | ~~`timeout` param on `exec_run()`~~ Done — see `DESIGN-tool-shell-sandbox.md` §Timeout | Standard for any untrusted execution |
| **Mount mode** | `rw` on entire CWD | `rw` is acceptable (agent needs to write files) | Same as Devin, Claude Code |
| **Docker socket** | Not mounted | Not mounted | Correct — already aligned |

### Refactoring Plan

#### Phase 1: Non-root user + network isolation (low risk, high value)

```python
self.client.containers.run(
    self.image,
    name=self.container_name,
    volumes={self.workspace_dir: {"bind": "/workspace", "mode": "rw"}},
    working_dir="/workspace",
    user="1000:1000",                   # non-root
    network_mode="none",                # no network by default
    mem_limit="1g",                     # OOM-kill at 1 GB
    nano_cpus=1_000_000_000,            # 1 CPU core
    pids_limit=256,                     # prevent fork bombs
    cap_drop=["ALL"],                   # drop all Linux capabilities
    security_opt=["no-new-privileges"], # prevent setuid escalation
    detach=True,
    tty=True,
    command="sh"
)
```

**Considerations:**
- `user="1000:1000"` matches typical host UID on Linux/macOS. May need to be configurable for environments where CWD has different ownership.
- `network_mode="none"` breaks commands that need network (e.g., `pip install`, `curl`). Options:
  - Add a `sandbox_network` setting (`"none"` | `"bridge"`) in `config.py`
  - Or let specific commands opt in (more complex, deferred)

#### Phase 2: Resource limits (prevents runaway processes)

```python
self.client.containers.run(
    ...
    mem_limit="1g",                     # OOM-kill at 1 GB (industry norm: 1-2 GB)
    nano_cpus=1_000_000_000,            # 1 CPU core
    pids_limit=256,                     # prevent fork bombs
    cap_drop=["ALL"],                   # drop all Linux capabilities
    security_opt=["no-new-privileges"], # prevent setuid escalation
)
```

**Considerations:**
- Values should be configurable via `config.py` for users with large builds.
- `mem_limit` may need to be higher for heavy workloads (e.g., compiling).
- `cap_drop=["ALL"]` + `no-new-privileges` are zero-cost hardening aligned with Anthropic's reference sandbox and E2B norms.

### Implementation Order

1. ~~**Phase 3** — LLM-controlled command timeout~~ **Done** — see `DESIGN-tool-shell-sandbox.md` §Timeout
2. ~~**Phase 1** — non-root + network isolation~~ **Done** — merged into `DESIGN-tool-shell-sandbox.md` §Container Configuration
3. ~~**Phase 2** — resource limits + privilege hardening~~ **Done** — merged into `DESIGN-tool-shell-sandbox.md` §Container Configuration

---

## Out of Scope

| Topic | Doc |
|-------|-----|
| Conditional shell approval (safe-prefix whitelist) | `docs/TODO-approval-flow.md` |
| Streaming tool output | `docs/TODO-streaming-tool-output.md` |

---

## Summary

| # | Item | File(s) | Priority |
|---|------|---------|----------|
| ~~1~~ | ~~Command timeout — LLM-controlled per-invocation~~ | ~~`shell.py`, `sandbox.py`, `deps.py`, `config.py`, `agent.py`~~ | ~~**High**~~ Done |
| ~~2~~ | ~~Non-root user + network isolation~~ | ~~`sandbox.py`, `config.py`~~ | ~~Medium~~ Done |
| ~~3~~ | ~~Resource limits + privilege hardening (mem, CPU, pids, cap_drop, no-new-privileges)~~ | ~~`sandbox.py`, `config.py`~~ | ~~Medium~~ Done |
