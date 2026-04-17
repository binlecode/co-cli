# Plan: Security Fixes for Tool Approval and Execution

Task type: code-feature

## Context
A review of the `RESEARCH-peer-approval-policy.md` against the current `co-cli` implementation revealed two critical security gaps in the tool approval and execution layer:
1.  **SSRF via DNS Rebinding in `web_fetch`:** The `is_url_safe()` check validates the IP address of the given URL to block private/internal network access. However, it passes the original URL string to `httpx`, opening a Time-of-Check to Time-of-Use (TOCTOU) vulnerability where an attacker's DNS server could return a safe IP during validation but a private IP (e.g., cloud metadata) during the actual fetch.
2.  **Missing Shell Policy in `start_background_task`:** While foreground shell tools (`run_shell_command`, `execute_code`) route commands through `evaluate_shell_command()` in `_shell_policy.py` to block dangerous inputs (like heredocs or env-injection), `start_background_task` entirely bypasses this policy and executes the raw command string if the tool call is approved.

## Problem & Outcome
**Problem:** The agent is vulnerable to SSRF attacks if tricked into fetching malicious URLs, and vulnerable to shell injection or destructive commands if tricked into running background tasks, because these paths lack the robust security checks applied elsewhere.
**Failure cost:** A compromised or confused agent could exfiltrate cloud metadata (e.g., AWS IAM credentials at `169.254.169.254`) or execute destructive commands (e.g., `rm -rf /`) in the background, compromising the host system or cloud environment.

## Scope
-   Fix `is_url_safe()` and `web_fetch` in `co_cli/tools/web.py` to enforce connection to the validated IP or use an `httpx` transport that strictly validates IPs during connection. Given `httpx` constraints, the simplest robust fix is for `is_url_safe` to also return the resolved safe IP, and for `web_fetch` to force `httpx` to connect to that specific IP while keeping the original `Host` header. Alternatively, implement a custom `httpx.AsyncHTTPTransport` with a custom `local_address` or DNS resolver that fails on private IPs. We will go with the custom `httpx` transport approach as it's cleaner and more robust for HTTPS.
-   Update `start_background_task` in `co_cli/tools/task_control.py` to evaluate commands against `evaluate_shell_command()` and reject `DENY` results before proceeding with execution or approval.

## Behavioral Constraints
-   `web_fetch` must continue to support standard public URLs and follow redirects safely.
-   `start_background_task` must fail closed (return a tool error) if the command matches a `DENY` policy, exactly like `run_shell_command`.

## High-Level Design
1.  **`web_fetch` SSRF Fix:** We will create a custom `httpx.AsyncHTTPTransport` subclass or a custom DNS resolver hook for `httpx` in `co_cli/tools/web.py`. `httpx` allows passing a custom transport. However, `httpx`'s `AsyncHTTPTransport` doesn't easily let us hook just DNS. A simpler, robust approach is to perform the DNS resolution in Python, validate the IP, and then pass that specific IP to `httpx` using the `transport` or by modifying the request, but HTTPS requires the correct `Host` header and SNI. 
    Actually, `httpx` supports `transport = httpx.AsyncHTTPTransport(local_address=...)` but that's the source.
    The most robust way in `httpx` to prevent DNS rebinding is to resolve the IP ourselves, check it, and then instruct `httpx` to connect to that IP, setting the `Host` header for TLS SNI.
    Wait, `httpx` doesn't easily support "connect to this IP but use this Host".
    Alternative: We keep `is_url_safe` as an initial check, but to truly prevent TOCTOU, we must ensure the actual connection goes to a safe IP. We can use `httpx`'s `AsyncHTTPTransport` with a custom `Proxy` or monkeypatch/hook the connection.
    Actually, let's use a simpler approach: `is_url_safe` is an initial fast check. To prevent the actual connection from hitting a private IP, we can limit the `httpx` fetch. Wait, how do we prevent TOCTOU without a custom transport? We might need to write a custom `AsyncHTTPTransport` or `AsyncClient` that hooks the connection phase.
    Let's refine: `httpx` doesn't provide an easy DNS hook. `urllib3` does, but we use `httpx` for async. 
    Let's look at `httpx` docs. We can't easily hook DNS.
    We will modify `web.py` to resolve the IP, validate it, and if safe, replace the hostname in the URL with the IP address, and pass the original hostname in the `Host` header. This prevents DNS rebinding because we bypass DNS for the actual request.
2.  **`start_background_task` Policy Fix:** Import `evaluate_shell_command` and `ShellDecisionEnum` into `co_cli/tools/task_control.py`. Call `evaluate_shell_command` with the command and `ctx.deps.config.shell.safe_commands`. If `DENY`, return `tool_error(policy.reason)`.

## Implementation Plan

### TASK-1: Fix SSRF Vulnerability in `web_fetch`
Modify `co_cli/tools/web.py` to prevent DNS rebinding.
- Modify `is_url_safe` to return a `tuple[bool, str | None]` (is_safe, resolved_ip).
- In `web_fetch`, call `is_url_safe`. If safe, construct a new URL using the `resolved_ip` but preserve the original `Host` header for the request.
- Ensure redirects are also validated. Since `httpx` handles redirects automatically, if we want to protect redirects against DNS rebinding, we might need to disable automatic redirects (`follow_redirects=False`), handle them manually in a loop (up to 5 times), resolve the new URL's IP, validate it, and fetch again.
- **files:**
  - `co_cli/tools/web.py`
  - `tests/test_web.py` (add tests for SSRF protection and redirects)
- **done_when:** `uv run pytest tests/test_web.py` passes, and a test explicitly verifies that a URL resolving to `169.254.169.254` is blocked.
- **success_signal:** N/A (Security fix)

### TASK-2: Add Shell Policy to `start_background_task`
Modify `co_cli/tools/task_control.py` to enforce shell policy.
- Import `evaluate_shell_command` and `ShellDecisionEnum`.
- In `start_background_task`, before creating the span and task, evaluate the command.
- If `DENY`, return `tool_error`.
- **files:**
  - `co_cli/tools/task_control.py`
  - `tests/test_task_control.py` (add test for DENY policy)
- **done_when:** `uv run pytest tests/test_task_control.py` passes, and a test verifies that `start_background_task` with a `DENY` command (like `rm -rf /`) returns a tool error.
- **success_signal:** N/A (Security fix)

## Testing
- Unit tests for `web_fetch` with mocked DNS resolution to simulate rebinding and private IPs.
- Unit tests for `start_background_task` with dangerous commands to ensure they are blocked.

## Open Questions
- Is modifying the URL to use an IP and setting the `Host` header sufficient for HTTPS requests with SNI in `httpx`? If `httpx` checks SNI against the URL, it might fail. If so, manual redirect handling and a custom transport or socket might be needed. We will verify this during implementation.

---
## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev security-fixes-approval`
